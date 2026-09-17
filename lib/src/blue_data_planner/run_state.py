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

NodeResult = ExecutionResult | DiscoveryResult


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
    results: dict[str, NodeResult] = field(default_factory=dict[str, "NodeResult"])
    # Derived from the current plan and the current cover, and rewritten with every cover.
    _signatures: dict[str, str] = field(default_factory=dict[str, str], repr=False)
    # Repair-loop bookkeeping the run loop reads and writes across passes. RunCounters already
    # bounds the replan counts (spend_failure_replan, spend_control_replan); a cover repair has
    # no counterpart there yet, so it stays here for now. Task 10 revisits how a failure reason
    # travels; until then it is written here and read back into to_result.
    cover_attempts: int = 0
    failure_reason: str | None = None

    def next_ready(self) -> PhysicalPlanFragment | PipelineNode | None:
        """Return the next ready fragment, else a ready control node, else nothing."""
        for fragment in self.pipeline.cover.fragments:
            if self.signature_of(fragment.root_logical_node_id) in self.results:
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
            and self.signature_of(node.id) not in self.results
            and all(self.result_for(input_id) is not None for input_id in node.input_node_ids)
        ]

    def result_for(self, node_id: str) -> NodeResult | None:
        """Return what the current cover's work at this node produced, or nothing."""
        # A node a fragment absorbed has no signature, because a fused call produces no
        # intermediate table, so it answers None the same way an unrun node does.
        signature = self._signatures.get(node_id)
        return None if signature is None else self.results.get(signature)

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
        """Return every node a new logical version keeps, absorbed nodes included."""
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
        # Nothing is compared and nothing is deleted: a fragment whose work changed carries a
        # signature no earlier result sits under, and one whose work did not finds its own.
        self.pipeline.physical_plans[cover.id] = cover
        self.pipeline.current_physical_plan_id = cover.id
        self._signatures = signatures(self.pipeline.logical_plan, cover)

    def record_fragment(self, fragment: PhysicalPlanFragment, result: FragmentResult) -> None:
        """Keep what the review accepted under the signature of the work that produced it."""
        if result.result is not None:
            self.results[self.signature_of(fragment.root_logical_node_id)] = result.result

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
            attempts=[],
            counters=self.counters,
            planner_usage=[],
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
