"""How a planner is assembled, and how one request is scheduled, repaired, and ended."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from blue_data_planner import data_planner as data_planner_module
from blue_data_planner.data import DataRegistry
from blue_data_planner.data_planner import DataPlanner, build_data_planner
from blue_data_planner.llm import LLMError
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.types import (
    ControlOperator,
    Conversation,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    LogicalOperator,
    LogicalPlan,
    LogicalPlanResult,
    OutputColumn,
    PhysicalOperator,
    PhysicalPlan,
    PhysicalPlanFragment,
    PhysicalPlanResult,
    PipelineNode,
    PlanValidationError,
    RunLimits,
    RunStatus,
)
from tests.doubles.components import (
    FakeCatalogSearcher,
    FakeFragmentRunner,
    FakeLogicalPlanner,
    FakePhysicalPlanner,
)
from tests.doubles.llm import ScriptedLLMClient


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


def a_scan_reaching_no_return(plan_id: str) -> LogicalPlan:
    """n1 SCAN with nothing projecting it, and no output node named."""
    return LogicalPlan(
        id=plan_id,
        parent_id=None,
        output_node_id=None,
        nodes=[_a_scan_node()],
    )


def a_scan_named_as_the_output(plan_id: str) -> LogicalPlan:
    """n1 SCAN named as the output node, where a RETURN node belongs."""
    return LogicalPlan(
        id=plan_id,
        parent_id=None,
        output_node_id="n1",
        nodes=[_a_scan_node()],
    )


def _a_scan_node() -> PipelineNode:
    return PipelineNode(
        id="n1",
        kind=LogicalOperator.SCAN,
        input_node_ids=[],
        attributes={"instruction": "read the postings"},
        required_output_columns=[OutputColumn(name="title", dtype="str")],
    )


def an_ending_naming_a_column_the_rows_lack(plan_id: str) -> LogicalPlan:
    """n1 SCAN declaring title, ended by n2 RETURN projecting a column nothing produced."""
    plan = a_scan_and_ending(plan_id)
    plan.node("n2").attributes["columns"] = ["posting_count"]
    return plan


def an_ending_reading_a_node_a_fragment_absorbed(plan_id: str) -> LogicalPlan:
    """n1 SCAN and n2 FILTER fused into one call, ended by n3 RETURN reading both."""
    return LogicalPlan(
        id=plan_id,
        parent_id=None,
        output_node_id="n3",
        nodes=[
            _a_scan_node(),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the open postings"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n2", "n1"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_fused_cover(cover_id: str, logical_plan_id: str) -> PhysicalPlan:
    """One fragment over n1 and n2, rooted at n2, so n1 produces no table of its own."""
    return PhysicalPlan(
        id=cover_id,
        parent_id=None,
        logical_plan_id=logical_plan_id,
        fragments=[
            PhysicalPlanFragment(
                id="f1",
                logical_node_ids=["n1", "n2"],
                root_logical_node_id="n2",
                operator_id="builtin.nl2table",
                scan_sources={},
                instruction="read the postings and keep the open ones",
                input_data=[],
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            )
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
    return FragmentResult(status=RunStatus.OK, result=result, failure_reason=None)


def an_escalating_fragment() -> FragmentResult:
    """One fragment whose parameter attempts ran out."""
    return FragmentResult(
        status=RunStatus.NEEDS_REPAIR,
        result=None,
        failure_reason="the predicate matched nothing",
    )


def a_planner(
    logical: FakeLogicalPlanner,
    physical: FakePhysicalPlanner,
    runner: FakeFragmentRunner,
    searcher: FakeCatalogSearcher,
    limits: RunLimits | None = None,
) -> DataPlanner:
    """A run loop over four fakes."""
    # The four fakes stand in for the real components structurally; pyright's strict
    # nominal check does not see them as related.
    return DataPlanner(
        logical,  # pyright: ignore[reportArgumentType]
        physical,  # pyright: ignore[reportArgumentType]
        runner,  # pyright: ignore[reportArgumentType]
        searcher,  # pyright: ignore[reportArgumentType]
        limits or RunLimits(),
    )


def ok(plan: LogicalPlan) -> LogicalPlanResult:
    return LogicalPlanResult(status=RunStatus.OK, plan=plan, failure_reason=None)


def invalid_plan(failure_reason: str = "the plan was turned down") -> LogicalPlanResult:
    return LogicalPlanResult(
        status=RunStatus.INVALID_PLAN, plan=None, failure_reason=failure_reason
    )


def covered(cover: PhysicalPlan) -> PhysicalPlanResult:
    return PhysicalPlanResult(plan=cover, failure_reason=None)


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


@pytest.mark.parametrize(
    "plan",
    [a_scan_reaching_no_return, a_scan_named_as_the_output],
    ids=["no output node", "output node is not a RETURN"],
)
def test_a_run_with_nothing_left_to_run_and_no_answer_to_project_ends_as_planner_failed(
    plan: Callable[[str], LogicalPlan],
) -> None:
    """A finished run whose plan names no RETURN has no answer, so ok would misreport it."""
    result = a_planner(
        FakeLogicalPlanner([ok(plan("p1"))]),
        FakePhysicalPlanner([covered(a_cover("c1", "p1"))]),
        FakeFragmentRunner([an_accepted_fragment()]),
        FakeCatalogSearcher(),
    ).run("list the job titles")

    assert result.status is RunStatus.PLANNER_FAILED
    assert result.answer is None
    assert result.failure_reason is not None


@pytest.mark.parametrize(
    ("plan", "cover"),
    [
        (an_ending_naming_a_column_the_rows_lack, a_cover),
        (an_ending_reading_a_node_a_fragment_absorbed, a_fused_cover),
    ],
    ids=["a column the rows lack", "a node a fragment absorbed"],
)
def test_an_ending_the_finished_rows_cannot_answer_is_written_again_rather_than_ending_the_run(
    plan: Callable[[str], LogicalPlan],
    cover: Callable[[str, str], PhysicalPlan],
) -> None:
    """Which columns hold the answer is the logical planner's, so a replan is what corrects it."""
    logical = FakeLogicalPlanner([ok(plan("p1")), ok(a_scan_and_ending("p2"))])
    result = a_planner(
        logical,
        FakePhysicalPlanner([covered(cover("c1", "p1")), covered(a_cover("c2", "p2"))]),
        FakeFragmentRunner([an_accepted_fragment(), an_accepted_fragment()]),
        FakeCatalogSearcher(),
    ).run("list the job titles")

    assert result.status is RunStatus.OK
    assert result.answer == [[{"title": "SWE"}]]
    assert logical.reasons[-1] is not None


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
    assert result.counters.control_replans == 1
    assert result.status is RunStatus.OK


def a_planner_whose_every_repair_is_refused() -> tuple[
    DataPlanner, FakePhysicalPlanner, FakeLogicalPlanner
]:
    """A fragment always rejected, so the run spends every cover repair before the plan gives up."""
    logical = FakeLogicalPlanner([ok(a_scan_and_ending("p1")), invalid_plan()])
    physical = FakePhysicalPlanner(
        [
            covered(a_cover("c1", "p1")),
            covered(a_cover("c2", "p1")),
            covered(a_cover("c3", "p1")),
        ]
    )
    runner = FakeFragmentRunner(
        [an_escalating_fragment(), an_escalating_fragment(), an_escalating_fragment()]
    )
    return a_planner(logical, physical, runner, FakeCatalogSearcher()), physical, logical


def test_a_rejected_fragment_reaches_the_repairs_in_order_narrowest_first() -> None:
    """Three repairs remain: the parameters inside the runner, the cover, then the plan."""
    planner, physical, logical = a_planner_whose_every_repair_is_refused()

    result = planner.run("a request")

    assert len(physical.covered_plan_ids) == 3  # one cover plus cover_repairs=2
    assert len(logical.reasons) == 2
    assert result.status is RunStatus.INVALID_PLAN


def a_planner_over_two_plans_each_needing_every_cover() -> tuple[DataPlanner, FakePhysicalPlanner]:
    """Two logical plans, each covered twice and rejected every time cover_repairs=1 allows."""
    logical = FakeLogicalPlanner([ok(a_scan_and_ending("p1")), ok(a_scan_and_ending("p2"))])
    physical = FakePhysicalPlanner(
        [
            covered(a_cover("c1", "p1")),
            covered(a_cover("c2", "p1")),
            covered(a_cover("c3", "p2")),
            covered(a_cover("c4", "p2")),
        ]
    )
    runner = FakeFragmentRunner([an_escalating_fragment() for _ in range(4)])
    planner = a_planner(
        logical,
        physical,
        runner,
        FakeCatalogSearcher(),
        RunLimits(cover_repairs=1, failure_replans=1),
    )
    return planner, physical


def test_a_replan_gives_the_next_plan_its_own_covers() -> None:
    """The cover allowance is a loop variable, so a new logical plan enters the loop fresh."""
    planner, physical = a_planner_over_two_plans_each_needing_every_cover()

    result = planner.run("a request")

    # cover_repairs=1 means two covers per logical plan, and there are two plans.
    assert len(physical.covered_plan_ids) == 4
    assert result.status is RunStatus.REPLAN_EXHAUSTED


def a_scan_and_a_filter(plan_id: str) -> LogicalPlan:
    """n1 SCAN -> n2 FILTER -> n3 RETURN, two nodes an operator cannot fuse into one fragment."""
    return LogicalPlan(
        id=plan_id,
        parent_id=None,
        output_node_id="n3",
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
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the Python ones"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n2"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


class _NoParameters(BaseModel):
    """A parameter schema for an operator this test never calls."""


def a_single_kind_operator(operator_id: str, kind: LogicalOperator) -> PhysicalOperator:
    """One registered operator declaring only this kind, reading no registered source."""
    return PhysicalOperator(
        operator_id=operator_id,
        logical_operators=frozenset({kind}),
        description=f"{operator_id} does its one job.",
        parameter_schema=_NoParameters,
        call=lambda inputs, parameters, columns: None,  # pyright: ignore[reportArgumentType]
        source_kinds=None,
    )


def a_binding(label: str, inputs: tuple[str, ...] = ()) -> dict[str, Any]:
    """One binding decision's payload, naming each node on the chosen candidate's frontier."""
    payload: dict[str, Any] = {
        "candidate": label,
        "instruction": "do it",
        "required_output_columns": None,
    }
    for node_id in inputs:
        payload[f"input_name_{node_id}"] = f"the {node_id} table"
        payload[f"input_description_{node_id}"] = f"the rows {node_id} produced"
    return {"choice": payload}


def test_a_cover_repair_carries_an_unchanged_fragment_forward_without_rerunning_it() -> None:
    """ADR-026 holds only if reuse survives a real repair loop, not just each link in isolation."""
    router = OperatorRouter()
    router.register(a_single_kind_operator("builtin.scan", LogicalOperator.SCAN))
    router.register(a_single_kind_operator("builtin.filter", LogicalOperator.FILTER))
    client = ScriptedLLMClient(
        [
            # First cover: n2 (the consumer) is bound before n1, per the physical planner's
            # reverse dependency order.
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]"),
            # Repair cover, same logical plan: n2 is rebound, n1 is offered back as reuse.
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            {"choice": {"candidate": "n1:reuse:[n1]"}},
        ]
    )
    physical = PhysicalPlanner(client, router)  # pyright: ignore[reportArgumentType]
    runner = FakeFragmentRunner(
        [
            an_accepted_fragment(),  # n1
            an_escalating_fragment(),  # n2, first attempt: triggers the cover repair
            an_accepted_fragment(),  # n2, after the repair
        ]
    )

    result = DataPlanner(
        FakeLogicalPlanner([ok(a_scan_and_a_filter("p1"))]),  # pyright: ignore[reportArgumentType]
        physical,
        runner,  # pyright: ignore[reportArgumentType]
        FakeCatalogSearcher(),  # pyright: ignore[reportArgumentType]
        RunLimits(),
    ).run("list the Python job titles")

    assert result.status is RunStatus.OK
    covers = list(result.pipeline.physical_plans.values())
    assert len(covers) == 2  # the opening cover, plus the one cover repair
    n1_fragment_ids = {
        fragment.id for cover in covers if (fragment := cover.fragment_for_root("n1"))
    }
    # The reused fragment is the same object across both covers, not a rebuilt copy of it.
    assert len(n1_fragment_ids) == 1
    assert runner.ran.count(next(iter(n1_fragment_ids))) == 1


def test_a_plan_no_operator_can_cover_is_replanned_with_the_reason_in_hand() -> None:
    """Nothing ran, so the repair is a fresh plan written against why the cover failed."""
    logical = FakeLogicalPlanner([ok(a_scan_and_ending("p1")), ok(a_scan_and_ending("p2"))])
    uncoverable = PhysicalPlanResult(
        plan=None,
        failure_reason="node 'n1' scans a source of a kind no operator reads",
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
        RunLimits(cover_repairs=0, failure_replans=0),
    ).run("list the job titles")
    assert result.status is RunStatus.REPLAN_EXHAUSTED
    assert result.failure_reason == "the predicate matched nothing"


def test_a_defect_in_a_component_ends_the_request_instead_of_raising_at_the_caller() -> None:
    """Every repairable failure is a status by the time it reaches here, so a raise is a bug."""

    class PhysicalPlannerWithADefect(FakePhysicalPlanner):
        def cover(
            self,
            plan: LogicalPlan,
            conversation: Conversation,
            previous: PhysicalPlan | None,
            results: dict[str, Any],
            reason: str | None = None,
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
    # A logical plan is installed only once it is paired with a cover that covers it, so the
    # defective attempt never enters the pipeline.
    assert result.pipeline.logical_plans == {}


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
        [PhysicalPlanResult(plan=None, failure_reason="no operator can run n1")]
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
    """Plan generation ran out of corrections, so the run ends without a plan to cover."""
    result = a_planner(
        FakeLogicalPlanner([invalid_plan()]),
        FakePhysicalPlanner([]),
        FakeFragmentRunner([]),
        FakeCatalogSearcher(),
    ).run("list the job titles")
    assert result.status is RunStatus.INVALID_PLAN


@dataclass(frozen=True)
class _RegistersOneOperator:
    """A suite standing in for a real one, so assembly is tested without an operator library."""

    name: ClassVar[str] = "one"
    seen: dict[str, Any] | None = None

    def operators(
        self, *, data_registry: DataRegistry, llm_client: Any, model: str
    ) -> list[PhysicalOperator]:
        if self.seen is not None:
            self.seen.update({"data_registry": data_registry, "model": model})
        return [a_single_kind_operator("suite.filter", LogicalOperator.FILTER)]


def an_empty_registry() -> DataRegistry:
    """A registry holding no records, for an assembly that reads none."""
    registry: Any = DataRegistry(name="assembly_test", rebuild=True)
    registry.loads("[]")
    return registry


def a_captured_llm_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the client an assembled planner builds with a double recording its arguments."""
    captured: dict[str, Any] = {}

    class _Capturing:
        def __init__(self, name: str, properties: dict[str, Any]) -> None:
            captured["name"] = name
            captured["properties"] = properties

    monkeypatch.setattr(data_planner_module, "LLMClient", _Capturing)
    return captured


@pytest.mark.parametrize("reasoning_effort", ["low", None])
def test_an_assembled_planners_model_client_names_its_values_under_its_own_prefix(
    reasoning_effort: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare "model" key never matches the client's own <prefix>.* filter, so every call the
    planner made would fail with "no model configured"; None leaves the effort out entirely."""
    captured = a_captured_llm_client(monkeypatch)

    build_data_planner(
        an_empty_registry(),
        suites=[_RegistersOneOperator()],
        model="gpt-4o",
        reasoning_effort=reasoning_effort,
        llm_client_name="a_planner",
    )

    assert captured["name"] == "a_planner"
    assert captured["properties"]["a_planner.model"] == "gpt-4o"
    assert captured["properties"].get("a_planner.reasoning_effort") == reasoning_effort


def test_each_suite_is_built_over_the_registry_and_the_model_the_planner_was_given() -> None:
    """A suite reads the registry it will scan and follows the planner's model unless it holds
    one of its own, so both have to reach it."""
    seen: dict[str, Any] = {}
    registry = an_empty_registry()

    build_data_planner(
        registry, suites=[_RegistersOneOperator(seen=seen)], model="gpt-4o", limits=RunLimits()
    )

    assert seen == {"data_registry": registry, "model": "gpt-4o"}
