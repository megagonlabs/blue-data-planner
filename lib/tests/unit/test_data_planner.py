"""How one request is scheduled, repaired, and ended."""

from __future__ import annotations

from typing import Any

from blue_data_planner.data_planner import DataPlanner
from blue_data_planner.llm import LLMError
from blue_data_planner.types import (
    ControlOperator,
    ExecutionResult,
    ExecutionStatus,
    FragmentAttempt,
    FragmentResult,
    FragmentReviewDecision,
    LogicalOperator,
    LogicalPlan,
    LogicalPlanResult,
    OutputColumn,
    PhysicalPlan,
    PhysicalPlanFragment,
    PhysicalPlanResult,
    PipelineNode,
    PlanLimits,
    PlanValidationError,
    RunDecision,
    RunStatus,
)
from tests.doubles.components import (
    FakeCatalogSearcher,
    FakeFragmentRunner,
    FakeLogicalPlanner,
    FakePhysicalPlanner,
)


def a_scan_and_ending(plan_id: str) -> LogicalPlan:
    """n1 SCAN declaring one column, ended by n2 RETURN projecting it."""
    return LogicalPlan(
        id=plan_id,
        parent_id=None,
        output_node_id="n2",
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "read the postings"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n1"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def two_discovery_nodes(plan_id: str) -> LogicalPlan:
    """A first pass over registered data: two DISCOVER nodes, ready at once."""
    return LogicalPlan(
        id=plan_id,
        parent_id=None,
        output_node_id=None,
        nodes=[
            PipelineNode(
                id="d1",
                kind=ControlOperator.DISCOVER,
                input_node_ids=[],
                attributes={"instruction": "find the postings"},
            ),
            PipelineNode(
                id="d2",
                kind=ControlOperator.DISCOVER,
                input_node_ids=[],
                attributes={"instruction": "find the companies"},
            ),
        ],
    )


def a_cover(cover_id: str, logical_plan_id: str) -> PhysicalPlan:
    """One fragment over n1."""
    return PhysicalPlan(
        id=cover_id,
        parent_id=None,
        logical_plan_id=logical_plan_id,
        fragments=[
            PhysicalPlanFragment(
                id="f1",
                logical_node_ids=["n1"],
                root_logical_node_id="n1",
                operator_id="builtin.scan",
                scan_sources={},
                instruction="read the postings",
                input_data=[],
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            )
        ],
    )


def an_empty_cover(cover_id: str, logical_plan_id: str) -> PhysicalPlan:
    """A cover with nothing to run, which is what a plan of control nodes gets."""
    return PhysicalPlan(id=cover_id, parent_id=None, logical_plan_id=logical_plan_id, fragments=[])


def an_accepted_fragment() -> FragmentResult:
    """One fragment that ran and whose review kept it."""
    result = ExecutionResult(
        status=ExecutionStatus.OK,
        output=[[{"title": "SWE"}]],
        operator_id="builtin.scan",
        row_count=1,
    )
    return FragmentResult(
        status=RunStatus.OK,
        decision=RunDecision.CONTINUE,
        attempts=[
            FragmentAttempt(
                physical_plan_id="",
                fragment_id="f1",
                logical_node_ids=["n1"],
                operator_id="builtin.scan",
                operator_attributes={},
                result=result,
                assessment="looks right",
                decision=FragmentReviewDecision.ACCEPT,
            )
        ],
        failure_reason=None,
        usage=[],
    )


def an_escalating_fragment() -> FragmentResult:
    """One fragment whose parameter attempts ran out."""
    return FragmentResult(
        status=RunStatus.OK,
        decision=RunDecision.ESCALATE,
        attempts=[
            FragmentAttempt(
                physical_plan_id="",
                fragment_id="f1",
                logical_node_ids=["n1"],
                operator_id="builtin.scan",
                operator_attributes={},
                result=None,
                assessment="the predicate matched nothing",
                decision=FragmentReviewDecision.REJECT,
            )
        ],
        failure_reason="the predicate matched nothing",
        usage=[],
    )


def a_planner(
    logical: FakeLogicalPlanner,
    physical: FakePhysicalPlanner,
    runner: FakeFragmentRunner,
    searcher: FakeCatalogSearcher,
    limits: PlanLimits | None = None,
) -> DataPlanner:
    """A run loop over four fakes."""
    # The four fakes stand in for the real components structurally; pyright's strict
    # nominal check does not see them as related.
    return DataPlanner(
        logical,  # pyright: ignore[reportArgumentType]
        physical,  # pyright: ignore[reportArgumentType]
        runner,  # pyright: ignore[reportArgumentType]
        searcher,  # pyright: ignore[reportArgumentType]
        limits or PlanLimits(),
    )


def ok(plan: LogicalPlan) -> LogicalPlanResult:
    return LogicalPlanResult(status=RunStatus.OK, plan=plan, rejected=[], usage=[])


def covered(cover: PhysicalPlan) -> PhysicalPlanResult:
    return PhysicalPlanResult(plan=cover, failure_reason=None, usage=[])


def test_a_plan_that_runs_clean_ends_ok_with_the_columns_the_return_names() -> None:
    """The answer is the RETURN node's columns projected off the node it reads."""
    result = a_planner(
        FakeLogicalPlanner([ok(a_scan_and_ending("p1"))]),
        FakePhysicalPlanner([covered(a_cover("c1", "p1"))]),
        FakeFragmentRunner([an_accepted_fragment()]),
        FakeCatalogSearcher(),
    ).run("list the job titles")
    assert result.status is RunStatus.OK
    assert result.answer == [[{"title": "SWE"}]]


def test_every_control_node_ready_at_once_is_resolved_before_the_run_plans_again() -> None:
    """Two searches that do not narrow each other cost one control replan between them."""
    searcher = FakeCatalogSearcher()
    result = a_planner(
        FakeLogicalPlanner([ok(two_discovery_nodes("p1")), ok(a_scan_and_ending("p2"))]),
        FakePhysicalPlanner([covered(an_empty_cover("c1", "p1")), covered(a_cover("c2", "p2"))]),
        FakeFragmentRunner([an_accepted_fragment()]),
        searcher,
    ).run("Python jobs at Fortune 500")
    assert searcher.searched == ["d1", "d2"]
    assert result.counters["control_replans"] == 1
    assert result.status is RunStatus.OK


def test_a_rejected_fragment_reaches_the_repairs_in_order_narrowest_first() -> None:
    """The fragment repair runs while its node set has an attempt, then the cover repair."""
    physical = FakePhysicalPlanner(
        covers=[covered(a_cover("c1", "p1")), covered(a_cover("c3", "p1"))],
        repairs=[covered(a_cover("c2", "p1")), covered(a_cover("c2b", "p1"))],
    )
    result = a_planner(
        FakeLogicalPlanner([ok(a_scan_and_ending("p1")), ok(a_scan_and_ending("p2"))]),
        physical,
        FakeFragmentRunner(
            [
                an_escalating_fragment(),
                an_escalating_fragment(),
                an_escalating_fragment(),
                an_accepted_fragment(),
            ]
        ),
        FakeCatalogSearcher(),
        PlanLimits(fragment_attempts=2, cover_attempts=1, failure_replans=1),
    ).run("list the job titles")
    assert physical.repaired_node_sets == [frozenset({"n1"}), frozenset({"n1"})]
    assert result.counters["cover_attempts"] == 1


def test_a_plan_no_operator_can_cover_is_replanned_with_the_reason_in_hand() -> None:
    """Nothing ran, so the repair is a fresh plan written against why the cover failed."""
    logical = FakeLogicalPlanner([ok(a_scan_and_ending("p1")), ok(a_scan_and_ending("p2"))])
    uncoverable = PhysicalPlanResult(
        plan=None,
        failure_reason="node 'n1' scans a source of a kind no operator reads",
        usage=[],
    )
    result = a_planner(
        logical,
        FakePhysicalPlanner([uncoverable, covered(a_cover("c1", "p2"))]),
        FakeFragmentRunner([an_accepted_fragment()]),
        FakeCatalogSearcher(),
    ).run("list the job titles")

    assert result.status is RunStatus.OK
    assert logical.reasons == [None, uncoverable.failure_reason]


def test_a_replan_limit_ends_the_request_as_replan_exhausted() -> None:
    """Each replan path counts separately and either ends the run on its own bound."""
    result = a_planner(
        FakeLogicalPlanner([ok(a_scan_and_ending("p1"))]),
        FakePhysicalPlanner([covered(a_cover("c1", "p1"))]),
        FakeFragmentRunner([an_escalating_fragment()]),
        FakeCatalogSearcher(),
        PlanLimits(fragment_attempts=0, cover_attempts=0, failure_replans=0),
    ).run("list the job titles")
    assert result.status is RunStatus.REPLAN_EXHAUSTED
    assert result.failure_reason == "the predicate matched nothing"


def test_a_defect_in_a_component_ends_the_request_instead_of_raising_at_the_caller() -> None:
    """Every repairable failure is a status by the time it reaches here, so a raise is a bug."""

    class PhysicalPlannerWithADefect(FakePhysicalPlanner):
        def cover(
            self, plan: LogicalPlan, results: dict[str, Any], reason: str | None = None
        ) -> PhysicalPlanResult:
            raise PlanValidationError("fragment 'f1' reads ['n9'], the nodes feeding it are []")

    result = a_planner(
        FakeLogicalPlanner([ok(a_scan_and_ending("p1"))]),
        PhysicalPlannerWithADefect([]),
        FakeFragmentRunner([]),
        FakeCatalogSearcher(),
    ).run("list the job titles")

    assert result.status is RunStatus.PLANNER_FAILED
    assert result.failure_reason is not None
    assert "n9" in result.failure_reason
    # The plan the run had written before the defect is still readable.
    assert "p1" in result.pipeline.logical_plans


def test_the_planning_only_operation_stops_after_the_first_cover() -> None:
    """It spends no operator call, and returns planned with no answer."""
    runner = FakeFragmentRunner([])
    result = a_planner(
        FakeLogicalPlanner([ok(a_scan_and_ending("p1"))]),
        FakePhysicalPlanner([covered(a_cover("c1", "p1"))]),
        runner,
        FakeCatalogSearcher(),
    ).plan("list the job titles")
    assert result.status is RunStatus.PLANNED
    assert result.answer is None
    assert runner.ran == []


def test_the_planning_only_operation_stops_even_when_nothing_covers_the_plan() -> None:
    """An uncoverable plan is not a failure plan reaches; it stops with what it wrote."""
    physical = FakePhysicalPlanner(
        [PhysicalPlanResult(plan=None, failure_reason="no operator can run n1", usage=[])]
    )
    result = a_planner(
        FakeLogicalPlanner([ok(a_scan_and_ending("p1")), ok(a_scan_and_ending("p2"))]),
        physical,
        FakeFragmentRunner([]),
        FakeCatalogSearcher(),
    ).plan("list the job titles")
    assert result.status is RunStatus.PLANNED
    assert physical.covered_plan_ids == ["p1"]


def test_a_provider_that_fails_after_its_retries_ends_the_request_as_llm_failed() -> None:
    """The run returns every plan and attempt it had already accumulated."""

    class FailingLogicalPlanner(FakeLogicalPlanner):
        def generate(self, *args: object, **kwargs: object) -> LogicalPlanResult:
            raise LLMError("the provider failed after its retries", code="provider_error")

    result = a_planner(
        FailingLogicalPlanner([]),
        FakePhysicalPlanner([]),
        FakeFragmentRunner([]),
        FakeCatalogSearcher(),
    ).run("list the job titles")
    assert result.status is RunStatus.LLM_FAILED
    assert result.answer is None


def test_a_plan_the_planner_could_not_write_ends_as_invalid_plan() -> None:
    """Plan generation ran out of corrections, and every rejected plan travels out."""
    result = a_planner(
        FakeLogicalPlanner(
            [LogicalPlanResult(status=RunStatus.INVALID_PLAN, plan=None, rejected=[], usage=[])]
        ),
        FakePhysicalPlanner([]),
        FakeFragmentRunner([]),
        FakeCatalogSearcher(),
    ).run("list the job titles")
    assert result.status is RunStatus.INVALID_PLAN
