"""What the two checks accept and what they turn down."""

from __future__ import annotations

import pytest

from blue_data_planner.plan import validate_logical_plan, validate_physical_plan
from blue_data_planner.types import (
    ControlOperator,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    PlanValidationError,
)


def a_node(
    node_id: str,
    kind: LogicalOperator | ControlOperator,
    inputs: list[str],
) -> PipelineNode:
    """One node with the attributes its kind takes, declaring one column where its kind can."""
    attributes = (
        {"columns": ["title"]} if kind is LogicalOperator.RETURN else {"instruction": "do it"}
    )
    declares = isinstance(kind, LogicalOperator) and kind is not LogicalOperator.RETURN
    return PipelineNode(
        id=node_id,
        kind=kind,
        input_node_ids=inputs,
        attributes=attributes,
        required_output_columns=[OutputColumn(name="title", dtype="str")] if declares else [],
    )


def a_plan(nodes: list[PipelineNode], output_node_id: str | None) -> LogicalPlan:
    """A single-version plan over the nodes given."""
    return LogicalPlan(id="p1", parent_id=None, nodes=nodes, output_node_id=output_node_id)


def a_scan_filter_return_plan() -> LogicalPlan:
    """Read a table, filter it, return two columns: the plan every cover test covers."""
    return a_plan(
        [
            a_node("n1", LogicalOperator.SCAN, []),
            a_node("n2", LogicalOperator.FILTER, ["n1"]),
            a_node("n3", LogicalOperator.RETURN, ["n2"]),
        ],
        output_node_id="n3",
    )


def a_fragment(fragment_id: str, node_ids: list[str], root: str) -> PhysicalPlanFragment:
    """One fragment over the node ids given, rooted at the last one that runs."""
    return PhysicalPlanFragment(
        id=fragment_id,
        logical_node_ids=node_ids,
        root_logical_node_id=root,
        operator_id="blue.nl2sql",
        scan_sources={},
        instruction="run it",
        input_data=[],
        required_output_columns=[OutputColumn(name="title", dtype="str")],
    )


def a_cover(fragments: list[PhysicalPlanFragment]) -> PhysicalPlan:
    """A first cover of the scan-filter-return plan."""
    return PhysicalPlan(id="c1", parent_id=None, logical_plan_id="p1", fragments=fragments)


@pytest.mark.parametrize(
    "plan",
    [
        a_scan_filter_return_plan(),
        a_plan(
            [
                a_node("n1", LogicalOperator.SCAN, []),
                a_node("n2", ControlOperator.DISCOVER, ["n1"]),
            ],
            output_node_id=None,
        ),
        a_plan(
            [
                a_node("n1", LogicalOperator.SCAN, []),
                a_node("n2", LogicalOperator.SCAN, []),
                # n2 is a reference input: the filter reads its rows without transforming them.
                a_node("n3", LogicalOperator.FILTER, ["n1", "n2"]),
                a_node("n4", LogicalOperator.RETURN, ["n3"]),
            ],
            output_node_id="n4",
        ),
    ],
    ids=["ends-in-return", "ends-in-a-control-node", "carries-a-reference-input"],
)
def test_a_sound_plan_passes(plan: LogicalPlan) -> None:
    """A plan with one ending, no cycle, and enough inputs per node runs."""
    validate_logical_plan(plan)


@pytest.mark.parametrize(
    ("plan", "expected_message"),
    [
        (
            a_plan(
                [a_node("n1", LogicalOperator.SCAN, []), a_node("n1", LogicalOperator.RETURN, [])],
                output_node_id="n1",
            ),
            "more than once",
        ),
        (
            a_plan(
                [
                    a_node("n1", LogicalOperator.FILTER, ["n9"]),
                    a_node("n2", LogicalOperator.RETURN, ["n1"]),
                ],
                output_node_id="n2",
            ),
            "n9",
        ),
        (
            a_plan(
                [
                    a_node("n1", LogicalOperator.FILTER, ["n2"]),
                    a_node("n2", LogicalOperator.FILTER, ["n1"]),
                    a_node("n3", LogicalOperator.RETURN, ["n2"]),
                ],
                output_node_id="n3",
            ),
            "cycle",
        ),
        (
            a_plan([a_node("n1", LogicalOperator.SCAN, [])], output_node_id=None),
            "RETURN",
        ),
        (
            a_plan(
                [
                    a_node("n1", LogicalOperator.SCAN, []),
                    a_node("n2", LogicalOperator.RETURN, ["n1"]),
                    a_node("n3", LogicalOperator.RETURN, ["n1"]),
                ],
                output_node_id="n2",
            ),
            "RETURN",
        ),
        (
            a_plan(
                [
                    a_node("n1", LogicalOperator.SCAN, []),
                    a_node("n2", LogicalOperator.RETURN, ["n1"]),
                    a_node("n3", ControlOperator.REPLAN, ["n1"]),
                ],
                output_node_id="n2",
            ),
            "RETURN",
        ),
        (
            a_plan(
                [
                    a_node("n1", LogicalOperator.SCAN, []),
                    a_node("n2", LogicalOperator.RETURN, ["n1"]),
                ],
                output_node_id="n1",
            ),
            "output_node_id",
        ),
        (
            a_plan(
                [
                    a_node("n1", LogicalOperator.SCAN, []),
                    a_node("n2", LogicalOperator.SCAN, []),
                    a_node("n3", LogicalOperator.RETURN, ["n1"]),
                ],
                output_node_id="n3",
            ),
            "nothing reads",
        ),
        (
            a_plan(
                [
                    a_node("n1", LogicalOperator.SCAN, []),
                    a_node("n2", LogicalOperator.JOIN, ["n1"]),
                    a_node("n3", LogicalOperator.RETURN, ["n2"]),
                ],
                output_node_id="n3",
            ),
            "JOIN",
        ),
        (
            a_plan(
                [
                    a_node("n1", ControlOperator.DISCOVER, []),
                    a_node("n2", LogicalOperator.SCAN, ["n1"]),
                    a_node("n3", LogicalOperator.RETURN, ["n2"]),
                ],
                output_node_id="n3",
            ),
            "produces no rows",
        ),
    ],
    ids=[
        "repeated-node-id",
        "input-naming-no-node",
        "cycle",
        "no-ending",
        "two-returns",
        "a-return-beside-a-terminal-control-node",
        "output-node-id-naming-the-wrong-node",
        "a-node-nobody-reads",
        "a-join-given-one-input",
        "a-node-reading-a-control-node",
    ],
)
def test_an_unsound_plan_is_rejected_and_the_error_names_what_was_wrong(
    plan: LogicalPlan, expected_message: str
) -> None:
    """Whatever makes a plan invalid, the check raises and says which node and which rule."""
    with pytest.raises(PlanValidationError, match=expected_message):
        validate_logical_plan(plan)


def test_a_carried_node_is_exempt_from_the_shape_and_reachability_checks() -> None:
    """A continuation carries the nodes the run holds results for; their objections do not apply."""
    plan = a_plan(
        [
            a_node("n1", LogicalOperator.SCAN, []),
            a_node("n2", ControlOperator.REPLAN, ["n1"]),
            a_node("n3", LogicalOperator.SCAN, []),
            a_node("n4", LogicalOperator.RETURN, ["n3"]),
        ],
        output_node_id="n4",
    )

    validate_logical_plan(plan, carried_node_ids=frozenset({"n2"}))


def test_a_continuation_reading_a_carried_discover_node_is_rejected() -> None:
    """Carrying a resolved search over exempts it from the ending, not from being unreadable."""
    plan = a_plan(
        [
            a_node("n1", ControlOperator.DISCOVER, []),
            a_node("n2", LogicalOperator.SCAN, ["n1"]),
            a_node("n3", LogicalOperator.RETURN, ["n2"]),
        ],
        output_node_id="n3",
    )

    with pytest.raises(PlanValidationError, match="produces no rows"):
        validate_logical_plan(plan, carried_node_ids=frozenset({"n1"}))


def test_a_legal_cover_passes() -> None:
    """Every operator node in one fragment, each fragment connected with a single exit."""
    cover = a_cover([a_fragment("f1", ["n1", "n2"], "n2")])

    validate_physical_plan(a_scan_filter_return_plan(), cover, frozenset())


@pytest.mark.parametrize(
    ("cover", "expected_message"),
    [
        (
            a_cover([a_fragment("f1", ["n1", "n2"], "n2"), a_fragment("f2", ["n2"], "n2")]),
            "more than one fragment",
        ),
        (a_cover([a_fragment("f1", ["n1"], "n1")]), "no fragment covers node 'n2'"),
        (
            a_cover([a_fragment("f1", ["n1", "n2", "n3"], "n3")]),
            "node 'n3' of kind RETURN, which no fragment may hold",
        ),
        (
            a_cover([a_fragment("f1", ["n1", "n2", "n9"], "n2")]),
            "names node 'n9', which the plan does not hold",
        ),
        (a_cover([a_fragment("f1", ["n1", "n2"], "n1")]), "single exit"),
    ],
    ids=[
        "a-node-covered-twice",
        "a-node-covered-by-nothing",
        "a-terminal-node-inside-a-fragment",
        "a-fragment-naming-a-node-the-plan-does-not-hold",
        "a-non-root-node-read-from-outside",
    ],
)
def test_an_illegal_cover_is_rejected_and_the_error_names_what_was_wrong(
    cover: PhysicalPlan, expected_message: str
) -> None:
    """A broken cover fails loudly here rather than as a run that stops making progress."""
    with pytest.raises(PlanValidationError, match=expected_message):
        validate_physical_plan(a_scan_filter_return_plan(), cover, frozenset())


def test_a_disconnected_fragment_is_rejected() -> None:
    """Two nodes with no edge between them do not describe one computation."""
    plan = a_plan(
        [
            a_node("n1", LogicalOperator.SCAN, []),
            a_node("n2", LogicalOperator.SCAN, []),
            a_node("n3", LogicalOperator.UNION, ["n1", "n2"]),
            a_node("n4", LogicalOperator.RETURN, ["n3"]),
        ],
        output_node_id="n4",
    )
    cover = a_cover([a_fragment("f1", ["n1", "n2"], "n1"), a_fragment("f2", ["n3"], "n3")])

    with pytest.raises(PlanValidationError, match="connected"):
        validate_physical_plan(plan, cover, frozenset())


def test_a_plan_ending_on_several_terminal_control_nodes_is_accepted() -> None:
    """Two things needing to be found are two DISCOVER nodes ending one pass."""
    plan = a_plan(
        [
            a_node("n1", ControlOperator.DISCOVER, []),
            a_node("n2", ControlOperator.DISCOVER, []),
        ],
        output_node_id=None,
    )
    validate_logical_plan(plan)


def test_a_plan_holding_two_replan_nodes_is_rejected() -> None:
    """Resolving a REPLAN runs nothing, so a second asks for the continuation the first did."""
    plan = a_plan(
        [
            a_node("n1", ControlOperator.REPLAN, []),
            a_node("n2", ControlOperator.REPLAN, []),
        ],
        output_node_id=None,
    )
    with pytest.raises(PlanValidationError, match="at most one REPLAN"):
        validate_logical_plan(plan)


def test_a_return_naming_a_column_its_input_does_not_declare_is_rejected() -> None:
    """The answer is projected after the last fragment runs, so nothing downstream catches it."""
    ending = PipelineNode(
        id="n2",
        kind=LogicalOperator.RETURN,
        input_node_ids=["n1"],
        attributes={"columns": ["posting_count"]},
    )
    plan = a_plan([a_node("n1", LogicalOperator.SCAN, []), ending], output_node_id="n2")
    with pytest.raises(PlanValidationError, match="posting_count"):
        validate_logical_plan(plan)


def test_the_node_a_return_reads_has_to_declare_its_output_columns() -> None:
    """An empty declaration leaves the ending with nothing to be checked against."""
    scan = PipelineNode(
        id="n1",
        kind=LogicalOperator.SCAN,
        input_node_ids=[],
        attributes={"instruction": "read them"},
        required_output_columns=[],
    )
    ending = PipelineNode(
        id="n2",
        kind=LogicalOperator.RETURN,
        input_node_ids=["n1"],
        attributes={"columns": ["title"]},
    )
    with pytest.raises(PlanValidationError, match="declares no output columns"):
        validate_logical_plan(a_plan([scan, ending], output_node_id="n2"))


def test_a_cover_binding_a_scan_to_a_path_the_run_never_searched_for_is_rejected() -> None:
    """A path no DISCOVER node found is a path a model wrote from nothing."""
    plan = a_plan(
        [a_node("n1", LogicalOperator.SCAN, []), a_node("n2", LogicalOperator.RETURN, ["n1"])],
        output_node_id="n2",
    )
    cover = PhysicalPlan(
        id="c1",
        parent_id=None,
        logical_plan_id="p1",
        fragments=[
            PhysicalPlanFragment(
                id="f1",
                logical_node_ids=["n1"],
                root_logical_node_id="n1",
                operator_id="builtin.scan",
                scan_sources={"n1": "/source/invented"},
                instruction="read them",
                input_data=[],
                required_output_columns=[],
            )
        ],
    )
    with pytest.raises(PlanValidationError, match="/source/invented"):
        validate_physical_plan(plan, cover, frozenset({"/source/jobs/entity/postings"}))
