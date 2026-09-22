"""Which node sets an operator declaring a set of logical operators could cover."""

from __future__ import annotations

from blue_data_planner.plan import candidate_fragments, fragment_frontier
from blue_data_planner.types import ControlOperator, LogicalOperator, LogicalPlan, PipelineNode

ALL_OPERATOR_KINDS = frozenset(
    kind for kind in LogicalOperator if kind is not LogicalOperator.RETURN
)


def a_node(
    node_id: str, kind: LogicalOperator | ControlOperator, inputs: list[str]
) -> PipelineNode:
    """One node carrying the attributes its kind takes."""
    attributes = (
        {"columns": ["title"]} if kind is LogicalOperator.RETURN else {"instruction": "do it"}
    )
    return PipelineNode(id=node_id, kind=kind, input_node_ids=inputs, attributes=attributes)


def a_chain() -> LogicalPlan:
    """n1 SCAN -> n2 FILTER -> n3 AGGREGATE -> n4 RETURN."""
    return LogicalPlan(
        id="p1",
        parent_id=None,
        output_node_id="n4",
        nodes=[
            a_node("n1", LogicalOperator.SCAN, []),
            a_node("n2", LogicalOperator.FILTER, ["n1"]),
            a_node("n3", LogicalOperator.AGGREGATE, ["n2"]),
            a_node("n4", LogicalOperator.RETURN, ["n3"]),
        ],
    )


def a_plan_whose_filter_two_nodes_read() -> LogicalPlan:
    """n1 SCAN -> n2 FILTER, read by n3 AGGREGATE and n4 DISTINCT, joined at n5, ended at n6."""
    return LogicalPlan(
        id="p2",
        parent_id=None,
        output_node_id="n6",
        nodes=[
            a_node("n1", LogicalOperator.SCAN, []),
            a_node("n2", LogicalOperator.FILTER, ["n1"]),
            a_node("n3", LogicalOperator.AGGREGATE, ["n2"]),
            a_node("n4", LogicalOperator.DISTINCT, ["n2"]),
            a_node("n5", LogicalOperator.JOIN, ["n3", "n4"]),
            a_node("n6", LogicalOperator.RETURN, ["n5"]),
        ],
    )


def a_join_reading_its_inputs_out_of_order() -> LogicalPlan:
    """n1 and n2 SCAN, joined at n3 whose inputs read n2 first, ended at n4."""
    return LogicalPlan(
        id="p3",
        parent_id=None,
        output_node_id="n4",
        nodes=[
            a_node("n1", LogicalOperator.SCAN, []),
            a_node("n2", LogicalOperator.SCAN, []),
            a_node("n3", LogicalOperator.JOIN, ["n2", "n1"]),
            a_node("n4", LogicalOperator.RETURN, ["n3"]),
        ],
    )


def test_the_root_alone_is_a_candidate_when_an_operator_declares_its_kind() -> None:
    """An atomic operator matching the root is offered exactly the one-node set."""
    assert candidate_fragments(a_chain(), "n3", frozenset({LogicalOperator.AGGREGATE})) == [
        frozenset({"n3"})
    ]


def test_a_chain_yields_one_candidate_per_prefix_largest_first() -> None:
    """A compound operator can take the root, the root and its input, and so on down."""
    assert candidate_fragments(a_chain(), "n3", ALL_OPERATOR_KINDS) == [
        frozenset({"n1", "n2", "n3"}),
        frozenset({"n2", "n3"}),
        frozenset({"n3"}),
    ]


def test_growth_stops_at_a_node_whose_kind_the_operator_does_not_declare() -> None:
    """An operator declaring FILTER and AGGREGATE cannot reach past the SCAN below them."""
    assert candidate_fragments(
        a_chain(), "n3", frozenset({LogicalOperator.AGGREGATE, LogicalOperator.FILTER})
    ) == [frozenset({"n2", "n3"}), frozenset({"n3"})]


def test_a_node_read_from_outside_is_never_admitted_below_the_root() -> None:
    """Grouping n2 while n4 still reads it would leave n4 waiting for a table nobody makes."""
    assert candidate_fragments(a_plan_whose_filter_two_nodes_read(), "n3", ALL_OPERATOR_KINDS) == [
        frozenset({"n3"})
    ]


def test_a_node_both_its_readers_join_is_admitted() -> None:
    """Once n3 and n4 are inside, n2 has no consumer left outside and can be grouped."""
    sets = candidate_fragments(a_plan_whose_filter_two_nodes_read(), "n5", ALL_OPERATOR_KINDS)
    assert frozenset({"n2", "n3", "n4", "n5"}) in sets
    assert frozenset({"n2", "n3", "n5"}) not in sets


def test_the_same_plan_enumerated_twice_returns_the_same_list_in_the_same_order() -> None:
    """A cover written twice sees one order, so two covers of one plan are comparable."""
    plan = a_plan_whose_filter_two_nodes_read()
    assert candidate_fragments(plan, "n5", ALL_OPERATOR_KINDS) == candidate_fragments(
        plan, "n5", ALL_OPERATOR_KINDS
    )


def test_the_frontier_is_every_node_outside_the_set_that_feeds_it() -> None:
    """A fragment reads the nodes feeding it from outside, and a closed set reads nothing."""
    assert fragment_frontier(a_chain(), frozenset({"n2", "n3"})) == ["n1"]
    assert fragment_frontier(a_chain(), frozenset({"n1", "n2", "n3"})) == []
    assert fragment_frontier(a_plan_whose_filter_two_nodes_read(), frozenset({"n5"})) == [
        "n3",
        "n4",
    ]


def test_the_frontier_follows_each_nodes_own_input_order() -> None:
    """An operator takes its two tables positionally, so a sorted frontier would swap them."""
    assert fragment_frontier(a_join_reading_its_inputs_out_of_order(), frozenset({"n3"})) == [
        "n2",
        "n1",
    ]
