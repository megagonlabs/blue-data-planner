"""One fake per injected component, so the run loop is driven from returned results alone."""

from __future__ import annotations

from typing import Any

from blue_data_planner.types import (
    Conversation,
    DataBundle,
    DiscoveryResult,
    FailedFragment,
    FragmentResult,
    LogicalPlan,
    LogicalPlanResult,
    PhysicalPlan,
    PhysicalPlanFragment,
    PhysicalPlanResult,
    PipelineNode,
    RunCounters,
)


class FakeLogicalPlanner:
    """Answers each generate call with the next scripted LogicalPlanResult."""

    def __init__(self, results: list[LogicalPlanResult]) -> None:
        self.results = list(results)
        self.replan_reasons: list[str | None] = []
        self.conversations: list[Conversation] = []

    def generate(
        self,
        request: str,
        conversation: Conversation,
        counters: RunCounters,
        parent: LogicalPlan | None = None,
        carried_node_ids: frozenset[str] = frozenset(),
        results: dict[str, Any] | None = None,
        replan_reason: str | None = None,
    ) -> LogicalPlanResult:
        self.replan_reasons.append(replan_reason)
        self.conversations.append(conversation)
        if not self.results:
            raise AssertionError("the fake logical planner ran out of results")
        return self.results.pop(0)


class FakePhysicalPlanner:
    """Answers each cover with the next scripted PhysicalPlanResult."""

    def __init__(self, covers: list[PhysicalPlanResult]) -> None:
        self.covers = list(covers)
        self.covered_plan_ids: list[str] = []
        self.conversations: list[Conversation] = []
        self.failed_fragments: list[FailedFragment | None] = []

    def cover(
        self,
        request: str,
        plan: LogicalPlan,
        conversation: Conversation,
        previous: PhysicalPlan | None,
        results: dict[str, Any],
        failed_fragment: FailedFragment | None = None,
    ) -> PhysicalPlanResult:
        self.covered_plan_ids.append(plan.id)
        self.conversations.append(conversation)
        self.failed_fragments.append(failed_fragment)
        if not self.covers:
            raise AssertionError("the fake physical planner ran out of covers")
        return self.covers.pop(0)


class FakeFragmentRunner:
    """Answers each run with the next scripted FragmentResult."""

    def __init__(self, results: list[FragmentResult]) -> None:
        self.results = list(results)
        self.ran: list[str] = []

    def run(
        self,
        fragment: PhysicalPlanFragment,
        conversation: Conversation,
        inputs: DataBundle,
        counters: RunCounters,
    ) -> FragmentResult:
        self.ran.append(fragment.id)
        if not self.results:
            raise AssertionError("the fake fragment runner ran out of results")
        return self.results.pop(0)


class FakeCatalogSearcher:
    """Answers each search with an empty discovery, recording which node asked."""

    def __init__(self) -> None:
        self.searched: list[str] = []

    def search(
        self, node: PipelineNode, conversation: Conversation, inputs: DataBundle
    ) -> DiscoveryResult:
        self.searched.append(node.id)
        return DiscoveryResult(search_terms=["a term"], matches=[], dropped_terms=0)
