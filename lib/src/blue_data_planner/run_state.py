"""The one mutable object per request: the plans, what the work produced, and the counts."""

from __future__ import annotations

from dataclasses import dataclass, field

from blue_data_planner.plan import signatures
from blue_data_planner.types import (
    BlueDataPlannerError,
    ControlOperator,
    DataBundle,
    DataPipeline,
    DiscoveryResult,
    ExecutionResult,
    FragmentResult,
    LogicalPlan,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    RunConversations,
    RunCounters,
    RunResult,
    RunStatus,
)

# What a node's work produced, for whoever reads it next.
NodeResult = ExecutionResult | DiscoveryResult
# What the run files under a signature: how a fragment ended, or what a search found.
FiledResult = FragmentResult | DiscoveryResult


def _empty_pipeline() -> DataPipeline:
    return DataPipeline(
        logical_plans={},
        physical_plans={},
        current_logical_plan_id=None,
        current_physical_plan_id=None,
    )


@dataclass
class RunState:
    """Everything one request has produced so far, written by the run loop alone."""

    request: str
    counters: RunCounters
    conversations: RunConversations = field(default_factory=RunConversations)
    pipeline: DataPipeline = field(default_factory=_empty_pipeline)
    results: dict[str, FiledResult] = field(default_factory=dict[str, "FiledResult"])
    # Derived from the current plan and the current cover, and rewritten with every cover.
    _signatures: dict[str, str] = field(default_factory=dict[str, str], repr=False)

    def next_ready(self) -> PhysicalPlanFragment | PipelineNode | None:
        """Return the next ready fragment, else a ready control node, else nothing."""
        for fragment in self.pipeline.cover.fragments:
            # A fragment the review turned down is filed too, so what makes one done is an
            # accepted result rather than an entry.
            if self.result_for(fragment.root_logical_node_id) is not None:
                continue
            if all(
                self.result_for(entry.logical_node_id) is not None for entry in fragment.input_data
            ):
                return fragment
        ready = self.ready_control_nodes()
        return ready[0] if ready else None

    def ready_control_nodes(self) -> list[PipelineNode]:
        """Return every control node whose inputs all have results, in plan order."""
        return [
            node
            for node in self.pipeline.logical_plan.nodes
            if isinstance(node.kind, ControlOperator)
            and self.result_for(node.id) is None
            and all(self.result_for(input_id) is not None for input_id in node.input_node_ids)
        ]

    def result_for(self, node_id: str) -> NodeResult | None:
        """Return what this node produced for whoever reads it next, or nothing."""
        # A node a fragment grouped has no signature, because a fragment call produces no
        # intermediate table, so it answers None the same way an unrun node does.
        signature = self._signatures.get(node_id)
        filed = None if signature is None else self.results.get(signature)
        if isinstance(filed, FragmentResult):
            # A rejected fragment is filed with its result, so the status is what says whether
            # anything downstream may read those rows.
            return filed.result if filed.status is RunStatus.OK else None
        return filed

    def signature_of(self, node_id: str) -> str:
        """Return the signature the current cover gives this node's work."""
        signature = self._signatures.get(node_id)
        if signature is None:
            raise BlueDataPlannerError(
                f"node {node_id!r} has no signature under the current cover, so the run cannot "
                "say what work it stands for"
            )
        return signature

    def inputs_for(self, fragment: PhysicalPlanFragment) -> DataBundle:
        """Return the frontier nodes' outputs, in the order the fragment lists them."""
        return self._outputs([entry.logical_node_id for entry in fragment.input_data])

    def inputs_for_node(self, node: PipelineNode) -> DataBundle:
        """Return the outputs of the nodes this control node lists as inputs."""
        return self._outputs(node.input_node_ids)

    def carried_node_ids(self) -> frozenset[str]:
        """Return every node a new logical version keeps, grouped nodes included."""
        carried: set[str] = set()
        for fragment in self.pipeline.cover.fragments:
            if self.result_for(fragment.root_logical_node_id) is not None:
                carried.update(fragment.logical_node_ids)
        for node in self.pipeline.logical_plan.nodes:
            # A REPLAN node never has a result, so it never carries and never fires twice.
            if isinstance(node.kind, ControlOperator) and self.result_for(node.id) is not None:
                carried.add(node.id)
        return frozenset(carried)

    def install_logical_plan(self, plan: LogicalPlan) -> None:
        """Add the version and move the pointer."""
        self.pipeline.logical_plans[plan.id] = plan
        self.pipeline.current_logical_plan_id = plan.id

    def install_cover(self, cover: PhysicalPlan) -> None:
        """Add the cover, move the pointer, and sign the work it asks for."""
        if cover.logical_plan_id != self.pipeline.current_logical_plan_id:
            # signatures() below signs the cover against the current logical plan, so a cover
            # built for a different one would be signed against the wrong graph rather than
            # failing loudly.
            raise BlueDataPlannerError(
                f"cover {cover.id!r} was built for logical plan {cover.logical_plan_id!r}, but "
                f"the pipeline's current logical plan is "
                f"{self.pipeline.current_logical_plan_id!r}"
            )
        # Nothing is compared and nothing is deleted: a fragment whose work changed carries a
        # signature no earlier result sits under, and one whose work did not finds its own.
        self.pipeline.physical_plans[cover.id] = cover
        self.pipeline.current_physical_plan_id = cover.id
        self._signatures = signatures(self.pipeline.logical_plan, cover)

    def record_fragment(self, fragment: PhysicalPlanFragment, result: FragmentResult) -> None:
        """Keep how the fragment ended under the signature of the work that produced it."""
        self.results[self.signature_of(fragment.root_logical_node_id)] = result

    def record_discovery(self, node: PipelineNode, result: DiscoveryResult) -> None:
        """Keep what one DISCOVER node's search found under that node's signature."""
        self.results[self.signature_of(node.id)] = result

    def to_result(
        self, status: RunStatus, failure_reason: str | None, answer: DataBundle | None = None
    ) -> RunResult:
        """Freeze the run into the result a caller receives."""
        return RunResult(
            status=status,
            answer=answer if status is RunStatus.OK else None,
            failure_reason=None if status is RunStatus.OK else failure_reason,
            pipeline=self.pipeline,
            results=self.results,
            counters=self.counters,
            conversations=self.conversations,
        )

    def _outputs(self, node_ids: list[str]) -> DataBundle:
        bundle: DataBundle = []
        for node_id in node_ids:
            result = self.result_for(node_id)
            if not isinstance(result, ExecutionResult) or result.output is None:
                raise BlueDataPlannerError(
                    f"node {node_id!r} was read as an input but the run holds no rows for it"
                )
            bundle.extend(result.output)
        return bundle
