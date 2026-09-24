"""What a node's signature covers, and what leaves it unchanged."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from blue_data_planner.plan import signatures
from blue_data_planner.types import (
    BlueDataPlannerError,
    ControlOperator,
    InputData,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
)


def a_scan_filter_and_discovery() -> LogicalPlan:
    """n1 SCAN feeding n2 FILTER, which n3 DISCOVER in turn reads."""
    return LogicalPlan(
        id="p1",
        parent_id=None,
        output_node_id=None,
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                attributes={"instruction": "read the postings", "source": None},
                required_output_columns=[],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the Python ones"},
            ),
            PipelineNode(
                id="n3",
                kind=ControlOperator.DISCOVER,
                # n2 rather than n1: whichever cover a test builds, n2 always roots a
                # fragment (n1 does not, once a cover groups it into n2's), so n3 always has
                # a signed node to read. Reading a node no fragment exposes is exactly the
                # defect the last test in this file checks for, and only that test means to
                # construct it.
                input_node_ids=["n2"],
                attributes={"instruction": "find the salary tables"},
            ),
        ],
    )


def a_fragment(
    fragment_id: str, node_ids: list[str], root: str, **overrides: Any
) -> PhysicalPlanFragment:
    """One fragment binding a node set to builtin.scan, with the given fields overridden."""
    fragment = PhysicalPlanFragment(
        id=fragment_id,
        logical_node_ids=node_ids,
        root_logical_node_id=root,
        operator_id="builtin.scan",
        scan_sources={},
        instruction="do it",
        input_data=[],
        required_output_columns=[],
    )
    return dataclasses.replace(fragment, **overrides)


def reading_n1() -> list[InputData]:
    """The frontier of a fragment fed by n1."""
    return [InputData(logical_node_id="n1", name="postings", description="rows")]


def a_cover(*fragments: PhysicalPlanFragment) -> PhysicalPlan:
    """One cover of p1 over the fragments given."""
    return PhysicalPlan(id="c1", parent_id=None, logical_plan_id="p1", fragments=list(fragments))


def an_atomic_cover() -> PhysicalPlan:
    """f1 over n1 alone, f2 over n2 alone reading n1."""
    return a_cover(
        a_fragment("f1", ["n1"], "n1"),
        a_fragment("f2", ["n2"], "n2", input_data=reading_n1()),
    )


def test_every_fragment_root_and_every_control_node_is_signed_and_nothing_else() -> None:
    """A signature says what a node produces, so a node producing nothing has none."""
    signed = signatures(a_scan_filter_and_discovery(), an_atomic_cover())

    assert set(signed) == {"n1", "n2", "n3"}


def test_a_fresh_set_of_fragment_ids_leaves_every_signature_unchanged() -> None:
    """Ids would stop every result being reused, so they are the one thing excluded."""
    plan = a_scan_filter_and_discovery()
    renamed = a_cover(
        a_fragment("f7", ["n1"], "n1"),
        a_fragment("f8", ["n2"], "n2", input_data=reading_n1()),
    )

    assert signatures(plan, renamed) == signatures(plan, an_atomic_cover())


@pytest.mark.parametrize(
    "overrides",
    [
        {"operator_id": "builtin.nl2table"},
        {"instruction": "read the postings, quickly"},
        {"scan_sources": {"n1": "tag_bench/retail/stores"}},
        {"required_output_columns": [OutputColumn(name="title", dtype="str")]},
        {"logical_node_ids": ["n1", "n2"]},
    ],
    ids=["operator", "instruction", "sources", "columns", "nodes"],
)
def test_anything_deciding_what_a_fragment_produces_changes_its_signature(
    overrides: dict[str, Any],
) -> None:
    """A field a model wrote is a field a repair can change, and a stale result is the cost."""
    plan = a_scan_filter_and_discovery()
    changed = a_cover(
        a_fragment("f1", ["n1"], "n1", **overrides),
        a_fragment("f2", ["n2"], "n2", input_data=reading_n1()),
    )

    assert signatures(plan, changed)["n1"] != signatures(plan, an_atomic_cover())["n1"]


def test_a_change_upstream_changes_every_signature_below_it() -> None:
    """Folding the inputs' signatures in is what replaces a walk down the graph."""
    plan = a_scan_filter_and_discovery()
    changed = a_cover(
        a_fragment("f1", ["n1"], "n1", instruction="read the other postings"),
        a_fragment("f2", ["n2"], "n2", input_data=reading_n1()),
    )
    original = signatures(plan, an_atomic_cover())

    assert signatures(plan, changed)["n2"] != original["n2"]
    assert signatures(plan, changed)["n3"] != original["n3"]


def test_the_same_nodes_grouped_into_one_fragment_sign_differently_from_two() -> None:
    """A fragment call is different work, so it finds none of the results the atomic cuts left."""
    plan = a_scan_filter_and_discovery()
    grouped = a_cover(a_fragment("f1", ["n1", "n2"], "n2"))

    assert signatures(plan, grouped)["n2"] != signatures(plan, an_atomic_cover())["n2"]


def test_a_node_read_from_outside_every_fragment_raises_rather_than_signing_nothing() -> None:
    """A cover that leaves a reader waiting is a defect, and this is where it is named."""
    plan = a_scan_filter_and_discovery()
    missing_upstream = a_cover(a_fragment("f2", ["n2"], "n2", input_data=reading_n1()))

    with pytest.raises(BlueDataPlannerError, match="n1"):
        signatures(plan, missing_upstream)
