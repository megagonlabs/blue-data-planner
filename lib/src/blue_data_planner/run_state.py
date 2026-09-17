"""The one mutable object per request: plans, results, attempts, and the counters."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from blue_data_planner.types import (
    BlueDataPlannerError,
    ControlOperator,
    DataBundle,
    DataPipeline,
    DiscoveryResult,
    ExecutionResult,
    FragmentAttempt,
    FragmentResult,
    FragmentReviewDecision,
    LLMUsage,
    LogicalPlan,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    PlanResult,
    RejectedPlan,
    RunCounters,
    RunStatus,
)

NodeResult = ExecutionResult | DiscoveryResult


@dataclass
class RunState:
    """Everything one request has produced so far, written by the run loop alone."""

    request: str
    counters: RunCounters
    pipeline: DataPipeline = field(
        default_factory=lambda: DataPipeline(
            logical_plans={},
            physical_plans={},
            current_logical_plan_id=None,
            current_physical_plan_id=None,
        )
    )
    results: dict[str, dict[str, NodeResult]] = field(
        default_factory=dict[str, dict[str, "NodeResult"]]
    )
    attempts: list[FragmentAttempt] = field(default_factory=list[FragmentAttempt])
    rejected_plans: list[RejectedPlan] = field(default_factory=list[RejectedPlan])
    usage: list[LLMUsage] = field(default_factory=list[LLMUsage])
    fragment_attempts: dict[frozenset[str], int] = field(default_factory=dict[frozenset[str], int])
    cover_attempts: int = 0
    failure_reason: str | None = None
    failure_replans: int = 0
    control_replans: int = 0

    @property
    def logical_plan(self) -> LogicalPlan:
        return self.pipeline.logical_plan

    @property
    def cover(self) -> PhysicalPlan:
        return self.pipeline.cover

    @property
    def current_results(self) -> dict[str, NodeResult]:
        return self.results[self.logical_plan.id]

    def next_ready(self) -> PhysicalPlanFragment | PipelineNode | None:
        """Return the next ready fragment, else a ready control node, else nothing."""
        held = self.current_results
        for fragment in self.cover.fragments:
            if fragment.root_logical_node_id in held:
                continue
            if all(node_id in held for node_id in self._frontier(fragment)):
                return fragment
        ready = self.ready_control_nodes()
        return ready[0] if ready else None

    def ready_control_nodes(self) -> list[PipelineNode]:
        """Return every control node whose inputs all have results, in plan order."""
        held = self.current_results
        return [
            node
            for node in self.logical_plan.nodes
            if isinstance(node.kind, ControlOperator)
            and node.id not in held
            and all(input_id in held for input_id in node.input_node_ids)
        ]

    def inputs_for(self, fragment: PhysicalPlanFragment) -> DataBundle:
        """Return the frontier nodes' outputs, in the order the fragment lists them."""
        return self._outputs([entry.logical_node_id for entry in fragment.input_data])

    def inputs_for_node(self, node: PipelineNode) -> DataBundle:
        """Return the outputs of the nodes this control node lists as inputs."""
        return self._outputs(node.input_node_ids)

    def carried_node_ids(self) -> frozenset[str]:
        """Return every node a new logical version keeps, absorbed nodes included."""
        held = self.current_results
        carried: set[str] = set()
        for node_id, result in held.items():
            if isinstance(result, DiscoveryResult):
                carried.add(node_id)
                continue
            carried.update(self.cover.fragment_for_node(node_id).logical_node_ids)
        return frozenset(carried)

    def install_logical_plan(self, plan: LogicalPlan) -> None:
        """Add the version, move the pointer, and seed its slot with the carried results."""
        carried = (
            self.carried_node_ids() if self.pipeline.current_logical_plan_id else frozenset[str]()
        )
        parent_results = self.current_results if self.pipeline.current_logical_plan_id else {}
        self.pipeline.logical_plans[plan.id] = plan
        self.pipeline.current_logical_plan_id = plan.id
        # A map copy rather than a data copy: the values are shared references.
        self.results[plan.id] = {
            node_id: result for node_id, result in parent_results.items() if node_id in carried
        }

    def install_cover(self, cover: PhysicalPlan) -> None:
        """Add the cover and drop the results of every fragment whose binding changed."""
        previous = (
            self.pipeline.physical_plans.get(self.pipeline.current_physical_plan_id or "")
            if self.pipeline.current_physical_plan_id
            else None
        )
        self.pipeline.physical_plans[cover.id] = cover
        self.pipeline.current_physical_plan_id = cover.id
        # A fresh logical plan version's first cover has nothing to diff against: whatever
        # should carry over was already decided by install_logical_plan's seeding of the
        # results slot from carried_node_ids(). Comparing node sets across two plan versions
        # would compare ids that belong to unrelated graphs.
        if previous is None or previous.logical_plan_id != cover.logical_plan_id:
            return
        # A fragment's id is fresh in every cover (10-data-model.md), so identity is not
        # what tells an unchanged fragment apart from a new one: a fragment is unchanged
        # when its node set and its operator both match one from the previous cover.
        unchanged = {
            (frozenset(fragment.logical_node_ids), fragment.operator_id)
            for fragment in previous.fragments
        } & {
            (frozenset(fragment.logical_node_ids), fragment.operator_id)
            for fragment in cover.fragments
        }
        dropped: set[str] = set()
        for fragment in cover.fragments:
            if (frozenset(fragment.logical_node_ids), fragment.operator_id) not in unchanged:
                dropped.update(fragment.logical_node_ids)
        held = self.current_results
        for node_id in list(held):
            if node_id in dropped or self._downstream_of(node_id, dropped):
                del held[node_id]

    def record_fragment(self, result: FragmentResult) -> None:
        """Append this fragment's attempts, its usage, and the result its last attempt kept."""
        # A fragment id is fresh in every cover, so an attempt names its cover as well
        # (10-data-model.md, the Identity rule). FragmentRunner is handed a fragment and
        # not the cover it came from, so the id is stamped here, where it is known.
        stamped = [replace(attempt, physical_plan_id=self.cover.id) for attempt in result.attempts]
        self.attempts.extend(stamped)
        self.usage.extend(result.usage)
        kept = stamped[-1] if stamped else None
        if (
            kept is not None
            and kept.result is not None
            and kept.decision is FragmentReviewDecision.ACCEPT
        ):
            root_id = self.cover.fragment(kept.fragment_id).root_logical_node_id
            self.current_results[root_id] = kept.result

    def record_discovery(self, node_id: str, result: DiscoveryResult) -> None:
        """Keep what one DISCOVER node's search found under that node's id."""
        self.current_results[node_id] = result

    def record_rejected_plans(self, rejected: list[RejectedPlan]) -> None:
        """Append every plan validation turned down, with the error that turned it down."""
        self.rejected_plans.extend(rejected)

    def record_usage(self, usage: list[LLMUsage]) -> None:
        """Append what one component's model calls cost, in the order they ran."""
        self.usage.extend(usage)

    def to_result(self, status: RunStatus, answer: DataBundle | None = None) -> PlanResult:
        """Freeze the run into the result a caller receives."""
        return PlanResult(
            status=status,
            answer=answer if status is RunStatus.OK else None,
            failure_reason=None if status is RunStatus.OK else self.failure_reason,
            pipeline=self.pipeline,
            results=self.results,
            attempts=self.attempts,
            rejected_plans=self.rejected_plans,
            counters=self._counters(),
            planner_usage=self.usage,
        )

    def _counters(self) -> dict[str, int]:
        return {
            "operator_calls": self.counters.operator_calls,
            "plan_corrections": len(self.rejected_plans),
            "parameter_attempts": self._most_attempts_at_one_fragment(),
            "fragment_attempts": max(self.fragment_attempts.values(), default=0),
            "cover_attempts": self.cover_attempts,
            "failure_replans": self.failure_replans,
            "control_replans": self.control_replans,
            "search_terms": sum(
                len(result.search_terms)
                for slot in self.results.values()
                for result in slot.values()
                if isinstance(result, DiscoveryResult)
            ),
        }

    def _most_attempts_at_one_fragment(self) -> int:
        counted: dict[str, int] = {}
        for attempt in self.attempts:
            counted[attempt.fragment_id] = counted.get(attempt.fragment_id, 0) + 1
        return max(counted.values(), default=0)

    def _frontier(self, fragment: PhysicalPlanFragment) -> set[str]:
        inside = set(fragment.logical_node_ids)
        return {
            input_id
            for node_id in inside
            for input_id in self.logical_plan.node(node_id).input_node_ids
            if input_id not in inside
        }

    def _outputs(self, node_ids: list[str]) -> DataBundle:
        bundle: DataBundle = []
        for node_id in node_ids:
            result = self.current_results.get(node_id)
            if not isinstance(result, ExecutionResult) or result.output is None:
                raise BlueDataPlannerError(
                    f"node {node_id!r} was read as an input but the run holds no rows for it"
                )
            bundle.extend(result.output)
        return bundle

    def _downstream_of(self, node_id: str, dropped: set[str]) -> bool:
        seen: set[str] = set()
        pending = [node_id]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            for input_id in self.logical_plan.node(current).input_node_ids:
                if input_id in dropped:
                    return True
                pending.append(input_id)
        return False
