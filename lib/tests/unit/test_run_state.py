"""What the run holds between passes, and what it hands back when the request ends."""

from __future__ import annotations

from blue_data_planner.run_state import RunState
from blue_data_planner.types import (
    CallBudget,
    ControlOperator,
    DiscoveryResult,
    ExecutionResult,
    ExecutionStatus,
    FragmentAttempt,
    FragmentResult,
    FragmentReviewDecision,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    RunDecision,
    RunStatus,
)
from blue_data_planner.types import (
    PipelineNode as Node,
)


def a_state() -> RunState:
    """A run over a two-scan plan cut into three fragments, with nothing run yet."""
    state = RunState(request="how many Python postings per title", budget=CallBudget(30))
    state.install_logical_plan(a_plan())
    state.install_cover(a_cover())
    return state


def a_plan() -> LogicalPlan:
    """n1 SCAN and n2 SCAN joined at n3, counted at n4, ended at n5."""

    def node(node_id: str, kind: LogicalOperator, inputs: list[str]) -> PipelineNode:
        return PipelineNode(
            id=node_id,
            kind=kind,
            input_node_ids=inputs,
            attributes={"instruction": "do it"},
            required_output_columns=[OutputColumn(name="title", dtype="str")],
        )

    return LogicalPlan(
        id="p1",
        parent_id=None,
        output_node_id="n5",
        nodes=[
            node("n1", LogicalOperator.SCAN, []),
            node("n2", LogicalOperator.SCAN, []),
            node("n3", LogicalOperator.JOIN, ["n1", "n2"]),
            node("n4", LogicalOperator.AGGREGATE, ["n3"]),
            Node(
                id="n5",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n4"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_fragment(
    fragment_id: str, node_ids: list[str], root: str, operator_id: str
) -> PhysicalPlanFragment:
    """One fragment binding a node set to an operator, with no scan sources named."""
    return PhysicalPlanFragment(
        id=fragment_id,
        logical_node_ids=node_ids,
        root_logical_node_id=root,
        operator_id=operator_id,
        scan_sources={},
        instruction="do it",
        input_data=[],
        required_output_columns=[],
    )


def a_cover() -> PhysicalPlan:
    """f1 over n1, f2 over n2, f3 over n3 and n4 rooted at n4."""
    return PhysicalPlan(
        id="c1",
        parent_id=None,
        logical_plan_id="p1",
        fragments=[
            a_fragment("f1", ["n1"], "n1", "builtin.scan"),
            a_fragment("f2", ["n2"], "n2", "builtin.scan"),
            a_fragment("f3", ["n3", "n4"], "n4", "builtin.nl2table"),
        ],
    )


def an_ok_result(rows: list[dict[str, object]]) -> ExecutionResult:
    """One accepted operator call returning a single table."""
    return ExecutionResult(
        status=ExecutionStatus.OK,
        output=[rows],
        operator_id="builtin.scan",
        row_count=len(rows),
    )


def an_accepted_fragment() -> FragmentResult:
    """One run of f1 whose single attempt the review kept."""
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
                result=an_ok_result([{"title": "SWE"}]),
                assessment="looks right",
                decision=FragmentReviewDecision.ACCEPT,
            )
        ],
        failure_reason=None,
        usage=[],
    )


def test_a_fragment_becomes_ready_once_every_node_on_its_frontier_has_a_result() -> None:
    """f3 reads n1 and n2, so it waits for both and then comes up."""
    state = a_state()
    state.results["p1"]["n1"] = an_ok_result([{"title": "SWE"}])
    assert getattr(state.next_ready(), "id", None) == "f2"
    state.results["p1"]["n2"] = an_ok_result([{"title": "SWE"}])
    assert getattr(state.next_ready(), "id", None) == "f3"


def test_a_fragment_whose_root_already_holds_a_result_is_not_offered_again() -> None:
    """A cover that left most fragments alone resumes on top of what already ran."""
    state = a_state()
    state.results["p1"]["n1"] = an_ok_result([{"title": "SWE"}])
    state.results["p1"]["n2"] = an_ok_result([{"title": "SWE"}])
    state.results["p1"]["n4"] = an_ok_result([{"title": "SWE"}])
    assert state.next_ready() is None


def test_a_ready_fragment_is_offered_before_a_ready_control_node() -> None:
    """A pass runs everything it can before it plans again."""
    state = RunState(request="find them", budget=CallBudget(30))
    state.install_logical_plan(
        LogicalPlan(
            id="p1",
            parent_id=None,
            output_node_id=None,
            nodes=[
                PipelineNode(
                    id="n1",
                    kind=LogicalOperator.SCAN,
                    input_node_ids=[],
                    attributes={"instruction": "read them"},
                    required_output_columns=[OutputColumn(name="title", dtype="str")],
                ),
                PipelineNode(
                    id="n2",
                    kind=ControlOperator.DISCOVER,
                    input_node_ids=["n1"],
                    attributes={"instruction": "find a source"},
                ),
                PipelineNode(
                    id="n3",
                    kind=ControlOperator.DISCOVER,
                    input_node_ids=[],
                    attributes={"instruction": "find another"},
                ),
            ],
        )
    )
    state.install_cover(
        PhysicalPlan(
            id="c1",
            parent_id=None,
            logical_plan_id="p1",
            fragments=[a_fragment("f1", ["n1"], "n1", "builtin.scan")],
        )
    )
    assert getattr(state.next_ready(), "id", None) == "f1"
    state.results["p1"]["n1"] = an_ok_result([{"title": "SWE"}])
    assert [node.id for node in state.ready_control_nodes()] == ["n2", "n3"]


def test_installing_a_cover_keeps_an_unchanged_fragment_and_drops_a_changed_one() -> None:
    """The changed fragment's nodes and everything executed downstream lose their results."""
    state = a_state()
    state.results["p1"]["n1"] = an_ok_result([{"title": "SWE"}])
    state.results["p1"]["n2"] = an_ok_result([{"title": "SWE"}])
    state.results["p1"]["n4"] = an_ok_result([{"title": "SWE"}])
    state.install_cover(
        PhysicalPlan(
            id="c2",
            parent_id="c1",
            logical_plan_id="p1",
            fragments=[
                a_fragment("f1", ["n1"], "n1", "builtin.scan"),
                a_fragment("g2", ["n2"], "n2", "builtin.nl2table"),
                a_fragment("f3", ["n3", "n4"], "n4", "builtin.nl2table"),
            ],
        )
    )
    assert set(state.results["p1"]) == {"n1"}


def test_a_new_logical_version_starts_with_the_carried_nodes_results_and_nothing_else() -> None:
    """A continuation keeps what the run has something for and writes the rest again."""
    state = a_state()
    state.results["p1"]["n1"] = an_ok_result([{"title": "SWE"}])
    carried = state.carried_node_ids()
    assert carried == frozenset({"n1"})
    state.install_logical_plan(
        LogicalPlan(id="p2", parent_id="p1", output_node_id=None, nodes=a_plan().nodes)
    )
    assert set(state.results["p2"]) == {"n1"}


def test_a_resolved_discovery_node_carries_over_and_a_replan_node_does_not() -> None:
    """A search never runs twice, and a REPLAN node that fired must not fire again."""
    state = RunState(request="find them", budget=CallBudget(30))
    state.install_logical_plan(
        LogicalPlan(
            id="p1",
            parent_id=None,
            output_node_id=None,
            nodes=[
                PipelineNode(
                    id="n1",
                    kind=ControlOperator.DISCOVER,
                    input_node_ids=[],
                    attributes={"instruction": "find a source"},
                ),
                PipelineNode(
                    id="n2",
                    kind=ControlOperator.REPLAN,
                    input_node_ids=[],
                    attributes={"instruction": "write the rest"},
                ),
            ],
        )
    )
    state.install_cover(PhysicalPlan(id="c1", parent_id=None, logical_plan_id="p1", fragments=[]))
    state.record_discovery(
        "n1", DiscoveryResult(search_terms=["jobs"], matches=[], dropped_terms=0)
    )
    assert state.carried_node_ids() == frozenset({"n1"})


def test_the_counters_a_result_reports_are_derived_from_what_the_run_holds() -> None:
    """One number, one place it comes from, so nothing is tracked twice."""
    state = a_state()
    state.budget.spend()
    state.budget.spend()
    result = state.to_result(RunStatus.BUDGET_EXHAUSTED)
    assert result.counters["operator_calls"] == 2
    assert result.counters["plan_corrections"] == 0
    assert result.answer is None


def test_a_recorded_attempt_names_the_cover_it_ran_under() -> None:
    """A fragment id is fresh in every cover, so the cover id is what places an attempt."""
    state = a_state()
    state.record_fragment(an_accepted_fragment())
    assert [attempt.physical_plan_id for attempt in state.attempts] == [state.cover.id]
