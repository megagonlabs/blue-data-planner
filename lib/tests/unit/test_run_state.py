"""What the run holds between passes, and what it hands back when the request ends."""

from __future__ import annotations

from blue_data_planner.run_state import RunState
from blue_data_planner.types import (
    ControlOperator,
    DiscoveryResult,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    InputData,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    ParameterAttempt,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    RunCounters,
    RunLimits,
    RunStatus,
    Turn,
)
from blue_data_planner.types import (
    PipelineNode as Node,
)


def a_state() -> RunState:
    """A run over a two-scan plan cut into three fragments, with nothing run yet."""
    state = RunState(
        request="how many Python postings per title", counters=RunCounters(RunLimits())
    )
    state.install_logical_plan(a_plan())
    state.install_cover(a_cover())
    return state


def a_plan() -> LogicalPlan:
    """n1 SCAN and n2 SCAN joined at n3, counted at n4, ended at n5."""

    def node(node_id: str, kind: LogicalOperator, inputs: list[str]) -> PipelineNode:
        attributes: dict[str, object] = {"instruction": "do it"}
        if kind is LogicalOperator.SCAN:
            attributes["source"] = None
        return PipelineNode(
            id=node_id,
            kind=kind,
            input_node_ids=inputs,
            attributes=attributes,
            required_output_columns=(
                [] if kind is LogicalOperator.SCAN else [OutputColumn(name="title", dtype="str")]
            ),
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
    fragment_id: str,
    node_ids: list[str],
    root: str,
    operator_id: str,
    inputs: list[str] | None = None,
) -> PhysicalPlanFragment:
    """One fragment binding a node set to an operator, naming the nodes feeding it."""
    return PhysicalPlanFragment(
        id=fragment_id,
        logical_node_ids=node_ids,
        root_logical_node_id=root,
        operator_id=operator_id,
        scan_sources={},
        instruction="do it",
        input_data=[
            InputData(logical_node_id=input_id, name=input_id, description="rows")
            for input_id in inputs or []
        ],
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
            a_fragment("f3", ["n3", "n4"], "n4", "builtin.nl2table", inputs=["n1", "n2"]),
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


def an_accepted_fragment(rows: list[dict[str, object]] | None = None) -> FragmentResult:
    """One fragment run whose result the review kept."""
    return FragmentResult(
        status=RunStatus.OK,
        attempts=[
            ParameterAttempt(
                parameters={},
                result=an_ok_result(rows if rows is not None else [{"title": "SWE"}]),
                assessment=None,
            )
        ],
        failure_reason=None,
    )


def a_rejected_fragment() -> FragmentResult:
    """One fragment run the review turned down, carrying the result it turned down."""
    return FragmentResult(
        status=RunStatus.NEEDS_REPAIR,
        attempts=[
            ParameterAttempt(
                parameters={}, result=an_ok_result([{"title": "SWE"}]), assessment=None
            )
        ],
        failure_reason="the review said the predicate dropped the wrong rows",
    )


def a_cover_with_fresh_fragment_ids() -> PhysicalPlan:
    """The same three fragments over the same nodes, under a new cover and new fragment ids."""
    return PhysicalPlan(
        id="c2",
        parent_id="c1",
        logical_plan_id="p1",
        fragments=[
            a_fragment("f7", ["n1"], "n1", "builtin.scan"),
            a_fragment("f8", ["n2"], "n2", "builtin.scan"),
            a_fragment("f9", ["n3", "n4"], "n4", "builtin.nl2table", inputs=["n1", "n2"]),
        ],
    )


def a_cover_binding_n1_to_another_operator() -> PhysicalPlan:
    """The same cut, with n1 bound to a different operator."""
    return PhysicalPlan(
        id="c3",
        parent_id="c1",
        logical_plan_id="p1",
        fragments=[
            a_fragment("f7", ["n1"], "n1", "builtin.nl2table"),
            a_fragment("f8", ["n2"], "n2", "builtin.scan"),
            a_fragment("f9", ["n3", "n4"], "n4", "builtin.nl2table", inputs=["n1", "n2"]),
        ],
    )


def a_second_version_of_the_plan() -> LogicalPlan:
    """p2, carrying n1 unchanged and writing the rest of the plan fresh."""
    plan = a_plan()
    return LogicalPlan(
        id="p2",
        parent_id="p1",
        output_node_id="n8",
        nodes=[
            plan.node("n1"),
            PipelineNode(
                id="n6",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "do it differently", "source": None},
                required_output_columns=[],
            ),
            PipelineNode(
                id="n7",
                kind=LogicalOperator.JOIN,
                input_node_ids=["n1", "n6"],
                attributes={"instruction": "do it"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n8",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n7"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_cover_of_the_second_version() -> PhysicalPlan:
    """A cover of p2 whose fragment over n1 matches a_cover's f1 in every field but its id."""
    return PhysicalPlan(
        id="c4",
        parent_id=None,
        logical_plan_id="p2",
        fragments=[
            a_fragment("f10", ["n1"], "n1", "builtin.scan"),
            a_fragment("f11", ["n6"], "n6", "builtin.scan"),
            a_fragment("f12", ["n7"], "n7", "builtin.nl2table", inputs=["n1", "n6"]),
        ],
    )


def a_state_over_two_control_nodes() -> RunState:
    """A plan ending in n6 DISCOVER, reading n1, and n7 REPLAN, covered over n1 alone."""
    state = RunState(request="find the salary tables", counters=RunCounters(RunLimits()))
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
                    attributes={"instruction": "read the postings", "source": None},
                    required_output_columns=[],
                ),
                PipelineNode(
                    id="n6",
                    kind=ControlOperator.DISCOVER,
                    input_node_ids=["n1"],
                    attributes={"instruction": "find the salary tables"},
                ),
                PipelineNode(
                    id="n7",
                    kind=ControlOperator.REPLAN,
                    input_node_ids=[],
                    attributes={"instruction": "write the rest"},
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
    return state


def a_discovery() -> DiscoveryResult:
    """One search that found nothing."""
    return DiscoveryResult(search_terms=["salary"], matches=[], dropped_terms=0)


def test_a_fragment_becomes_ready_once_every_node_on_its_frontier_has_a_result() -> None:
    """f3 reads n1 and n2, so it waits for both and then comes up."""
    state = a_state()
    state.record_fragment(state.pipeline.cover.fragment("f1"), an_accepted_fragment())
    assert getattr(state.next_ready(), "id", None) == "f2"
    state.record_fragment(state.pipeline.cover.fragment("f2"), an_accepted_fragment())
    assert getattr(state.next_ready(), "id", None) == "f3"


def test_a_fragment_whose_root_already_holds_a_result_is_not_offered_again() -> None:
    """A cover that left most fragments alone resumes on top of what already ran."""
    state = a_state()
    state.record_fragment(state.pipeline.cover.fragment("f1"), an_accepted_fragment())
    state.record_fragment(state.pipeline.cover.fragment("f2"), an_accepted_fragment())
    state.record_fragment(state.pipeline.cover.fragment("f3"), an_accepted_fragment())
    assert state.next_ready() is None


def test_a_fragment_the_review_turned_down_is_filed_without_becoming_something_to_read() -> None:
    """The rejection is part of the record, and nothing downstream may run on top of it."""
    state = a_state()
    state.record_fragment(state.pipeline.cover.fragment("f1"), a_rejected_fragment())

    filed = state.results[state.signature_of("n1")]
    assert getattr(filed, "status", None) is RunStatus.NEEDS_REPAIR
    assert state.result_for("n1") is None
    assert getattr(state.next_ready(), "id", None) == "f1"  # f1 is offered again, f2 is not ready


def test_a_ready_fragment_is_offered_before_a_ready_control_node() -> None:
    """A pass runs everything it can before it plans again."""
    state = RunState(request="find them", counters=RunCounters(RunLimits()))
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
                    attributes={"instruction": "read them", "source": None},
                    required_output_columns=[],
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
    state.record_fragment(state.pipeline.cover.fragment("f1"), an_accepted_fragment())
    assert [node.id for node in state.ready_control_nodes()] == ["n2", "n3"]


def test_a_result_survives_a_cover_that_asks_for_the_same_work_again() -> None:
    """A signature names the work, so an unchanged fragment finds what it produced before."""
    state = a_state()
    state.record_fragment(state.pipeline.cover.fragment("f1"), an_accepted_fragment())

    state.install_cover(a_cover_with_fresh_fragment_ids())

    assert state.result_for("n1") is not None


def test_a_result_is_unreadable_once_its_fragment_asks_for_other_work() -> None:
    """A rebound fragment is different work, so nothing in the new cover reaches the old result."""
    state = a_state()
    state.record_fragment(state.pipeline.cover.fragment("f1"), an_accepted_fragment())

    state.install_cover(a_cover_binding_n1_to_another_operator())

    assert state.result_for("n1") is None


def test_a_result_is_unreadable_once_the_work_above_it_changed() -> None:
    """The inputs' signatures are part of a node's own, so nothing walks down the graph."""
    state = a_state()
    for fragment_id in ("f1", "f2", "f3"):
        state.record_fragment(state.pipeline.cover.fragment(fragment_id), an_accepted_fragment())

    state.install_cover(a_cover_binding_n1_to_another_operator())

    assert state.result_for("n4") is None


def test_a_node_a_fragment_grouped_has_no_result_of_its_own() -> None:
    """A fragment call produces no intermediate table, so n3 answers the way an unrun node does."""
    state = a_state()
    state.record_fragment(state.pipeline.cover.fragment("f3"), an_accepted_fragment())

    assert state.result_for("n4") is not None
    assert state.result_for("n3") is None


def test_a_new_logical_version_keeps_what_the_nodes_it_carried_produced() -> None:
    """Identical work is identical whichever plan version asks for it."""
    state = a_state()
    state.record_fragment(state.pipeline.cover.fragment("f1"), an_accepted_fragment())
    carried = state.carried_node_ids()

    state.install_logical_plan(a_second_version_of_the_plan())
    state.install_cover(a_cover_of_the_second_version())

    assert carried == frozenset({"n1"})
    assert state.result_for("n1") is not None


def test_a_resolved_discovery_node_carries_over_and_a_replan_node_does_not() -> None:
    """A DISCOVER node never searches twice, and a REPLAN node that fired never fires again."""
    state = a_state_over_two_control_nodes()
    state.record_discovery(state.pipeline.logical_plan.node("n6"), a_discovery())

    assert state.carried_node_ids() == frozenset({"n6"})


def test_the_counters_a_result_reports_are_the_ones_the_run_spent() -> None:
    """RunCounters travels to the caller carrying the bounds it was counted against."""
    state = a_state()
    state.counters.spend_operator_call()

    result = state.to_result(RunStatus.OK, None, answer=[[{"title": "SWE"}]])

    assert result.counters.operator_calls == 1
    assert result.counters.limits.operator_calls == 30


def test_the_result_a_caller_receives_carries_the_conversations_the_run_held() -> None:
    """The conversations are where a rejected plan and a rejected set of parameters are read."""
    state = a_state()
    state.conversations.logical_planner.turns.append(Turn(role="system", content="fixed"))

    result = state.to_result(RunStatus.OK, None, answer=[[{"title": "SWE"}]])

    assert result.conversations is state.conversations
    assert not hasattr(result, "planner_usage")
