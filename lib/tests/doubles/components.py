"""One fake per injected component, so the run loop is driven from returned results alone."""

from __future__ import annotations

from typing import Any

from blue_data_planner.types import (
    CallBudget,
    CatalogSearchResult,
    DataBundle,
    DiscoveryResult,
    FragmentResult,
    LogicalPlan,
    LogicalPlanResult,
    PhysicalPlan,
    PhysicalPlanFragment,
    PhysicalPlanResult,
    PipelineNode,
)


class FakeLogicalPlanner:
    """Answers each generate call with the next scripted LogicalPlanResult."""

    def __init__(self, results: list[LogicalPlanResult]) -> None:
        self.results = list(results)
        self.reasons: list[str | None] = []

    def generate(
        self,
        request: str,
        parent: LogicalPlan | None = None,
        carried_node_ids: frozenset[str] = frozenset(),
        results: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> LogicalPlanResult:
        self.reasons.append(reason)
        if not self.results:
            raise AssertionError("the fake logical planner ran out of results")
        return self.results.pop(0)


class FakePhysicalPlanner:
    """Answers each cover and each repair with the next scripted PhysicalPlanResult."""

    def __init__(
        self,
        covers: list[PhysicalPlanResult],
        repairs: list[PhysicalPlanResult] | None = None,
    ) -> None:
        self.covers = list(covers)
        self.repairs = list(repairs or [])
        self.repaired_node_sets: list[frozenset[str]] = []
        self.covered_plan_ids: list[str] = []

    def cover(
        self, plan: LogicalPlan, results: dict[str, Any], reason: str | None = None
    ) -> PhysicalPlanResult:
        self.covered_plan_ids.append(plan.id)
        if not self.covers:
            raise AssertionError("the fake physical planner ran out of covers")
        return self.covers.pop(0)

    def recover_fragment(
        self,
        plan: LogicalPlan,
        previous: PhysicalPlan,
        node_ids: frozenset[str],
        results: dict[str, Any],
        reason: str,
    ) -> PhysicalPlanResult:
        self.repaired_node_sets.append(node_ids)
        if not self.repairs:
            raise AssertionError("the fake physical planner ran out of repairs")
        return self.repairs.pop(0)


class FakeFragmentRunner:
    """Answers each run with the next scripted FragmentResult."""

    def __init__(self, results: list[FragmentResult]) -> None:
        self.results = list(results)
        self.ran: list[str] = []

    def run(
        self, fragment: PhysicalPlanFragment, inputs: DataBundle, budget: CallBudget
    ) -> FragmentResult:
        self.ran.append(fragment.id)
        if not self.results:
            raise AssertionError("the fake fragment runner ran out of results")
        return self.results.pop(0)


class FakeCatalogSearcher:
    """Answers each search with an empty discovery, recording which node asked."""

    def __init__(self) -> None:
        self.searched: list[str] = []

    def search(self, node: PipelineNode, inputs: DataBundle) -> CatalogSearchResult:
        self.searched.append(node.id)
        return CatalogSearchResult(
            discovery=DiscoveryResult(search_terms=["a term"], matches=[], dropped_terms=0),
            usage=[],
        )
