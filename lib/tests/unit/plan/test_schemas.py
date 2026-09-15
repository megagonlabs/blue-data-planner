"""What a model may and may not answer with at each planning stage."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from blue_data_planner.plan import cover_choice_schema, logical_plan_schema
from blue_data_planner.types import (
    BlueDataPlannerError,
    LogicalOperator,
    LogicalPlan,
    PhysicalOperator,
    PipelineNode,
)

POSTINGS = "/source/jobs/database/jobs/collection/public/entity/postings"
COMPANIES = "/source/companies/database/co/collection/public/entity/fortune500"
NOTES = "/source/notes/database/notes/collection/public/entity/memos"
SOURCE_KINDS = {POSTINGS: "sql/sqlite", COMPANIES: "sql/sqlite", NOTES: "vector/chroma"}


class NoParameters(BaseModel):
    """A parameter schema for an operator a schema test never calls."""


def an_operator(
    operator_id: str, source_kinds: frozenset[str] | None = frozenset({"sql/sqlite"})
) -> PhysicalOperator:
    """One registered operator, declaring every operator kind and the source kinds given."""
    return PhysicalOperator(
        operator_id=operator_id,
        logical_operators=frozenset(
            kind for kind in LogicalOperator if kind is not LogicalOperator.RETURN
        ),
        description="Does whatever the instruction says.",
        parameter_schema=NoParameters,
        call=lambda inputs, parameters, columns: None,  # pyright: ignore[reportArgumentType]
        source_kinds=source_kinds,
    )


def a_two_scan_plan() -> LogicalPlan:
    """n1 SCAN and n2 SCAN joined at n3, counted at n4, ended at n5."""

    def node(node_id: str, kind: LogicalOperator, inputs: list[str]) -> PipelineNode:
        return PipelineNode(
            id=node_id, kind=kind, input_node_ids=inputs, attributes={"instruction": "do it"}
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
            PipelineNode(
                id="n5",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n4"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_binding(inputs: tuple[str, ...] = (), **overrides: Any) -> dict[str, Any]:
    """One binding decision's payload, before the fields a candidate's scans add."""
    payload: dict[str, Any] = {
        "candidate_index": 0,
        "instruction": "count the postings per title",
        "required_output_columns": None,
    }
    for node_id in inputs:
        payload[f"input_name_{node_id}"] = f"the {node_id} table"
        payload[f"input_description_{node_id}"] = f"the rows {node_id} produced"
    payload.update(overrides)
    return {"choice": payload}


def test_a_plan_naming_an_operation_the_vocabulary_lacks_is_refused() -> None:
    """The kind is a closed set, so a provider enforcing the schema cannot invent one."""
    with pytest.raises(ValidationError):
        logical_plan_schema().model_validate(
            {
                "output_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "kind": "SUMMARIZE",
                        "input_node_ids": [],
                        "instruction": "sum it up",
                        "required_output_columns": [],
                    }
                ],
            }
        )


def test_each_node_kind_is_held_to_the_attributes_its_kind_carries() -> None:
    """A RETURN names columns and carries no instruction; an operator node is the reverse."""
    with pytest.raises(ValidationError):
        logical_plan_schema().model_validate(
            {
                "output_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "kind": "RETURN",
                        "input_node_ids": [],
                        "instruction": "give the answer",
                    }
                ],
            }
        )


def test_a_plan_the_vocabulary_admits_reads_back_with_its_nodes() -> None:
    """The ordinary shape: one scan, one ending, and the columns each declares."""
    written = logical_plan_schema().model_validate(
        {
            "output_node_id": "n2",
            "nodes": [
                {
                    "id": "n1",
                    "kind": "SCAN",
                    "input_node_ids": [],
                    "instruction": "read the postings",
                    "required_output_columns": [{"name": "title", "dtype": "str"}],
                },
                {"id": "n2", "kind": "RETURN", "input_node_ids": ["n1"], "columns": ["title"]},
            ],
        }
    )
    assert [node["kind"] for node in written.model_dump()["nodes"]] == ["SCAN", "RETURN"]


def test_a_candidate_holding_no_scan_offers_nowhere_to_name_a_source() -> None:
    """A binding over a scanless candidate has no field an entity path could go in."""
    schema = cover_choice_schema(
        a_two_scan_plan(), [(frozenset({"n4"}), an_operator("builtin.aggregate"))], SOURCE_KINDS
    )
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding(inputs=("n3",), scan_source_n1=POSTINGS))


def test_a_binding_names_one_entity_path_per_scan_the_candidate_holds() -> None:
    """A candidate holding two scans has two required fields, one per scan node."""
    schema = cover_choice_schema(
        a_two_scan_plan(),
        [(frozenset({"n1", "n2", "n3", "n4"}), an_operator("builtin.nl2table"))],
        SOURCE_KINDS,
    )
    accepted = schema.model_validate(
        a_binding(scan_source_n1=POSTINGS, scan_source_n2=COMPANIES)
    ).model_dump()
    assert accepted["choice"]["scan_source_n1"] == POSTINGS
    assert accepted["choice"]["scan_source_n2"] == COMPANIES


def test_a_path_no_discover_node_found_is_refused() -> None:
    """The allowed values are the entity paths the run searched for, and nothing else."""
    schema = cover_choice_schema(
        a_two_scan_plan(),
        [(frozenset({"n1", "n2", "n3", "n4"}), an_operator("builtin.nl2table"))],
        SOURCE_KINDS,
    )
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding(scan_source_n1=POSTINGS, scan_source_n2="/source/invented"))


def test_a_scan_source_of_a_kind_the_operator_cannot_read_is_refused() -> None:
    """The allowed values are narrowed again to the kinds the operator's engine speaks."""
    schema = cover_choice_schema(
        a_two_scan_plan(),
        [(frozenset({"n1", "n2", "n3", "n4"}), an_operator("builtin.nl2table"))],
        SOURCE_KINDS,
    )
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding(scan_source_n1=POSTINGS, scan_source_n2=NOTES))


def test_a_candidate_reading_no_registered_source_offers_nowhere_to_name_one() -> None:
    """An operator declaring no source kinds scans what the model knows, not a source."""
    schema = cover_choice_schema(
        a_two_scan_plan(),
        [
            (
                frozenset({"n1", "n2", "n3", "n4"}),
                an_operator("builtin.nl2table", source_kinds=None),
            )
        ],
        SOURCE_KINDS,
    )
    assert "scan_source_n1" not in schema.model_validate(a_binding()).model_dump()["choice"]
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding(scan_source_n1=POSTINGS))


def test_a_candidate_whose_scans_have_no_readable_source_raises() -> None:
    """Nothing sound could constrain the field, and the planner drops such a candidate first."""
    with pytest.raises(BlueDataPlannerError, match="no discovered entity has a source kind"):
        cover_choice_schema(
            a_two_scan_plan(),
            [(frozenset({"n1", "n2", "n3", "n4"}), an_operator("builtin.nl2table"))],
            {NOTES: "vector/chroma"},
        )


def test_a_binding_picks_one_candidate_by_its_index() -> None:
    """Several candidates are several variants, and the index says which one was taken."""
    schema = cover_choice_schema(
        a_two_scan_plan(),
        [
            (frozenset({"n1", "n2", "n3", "n4"}), an_operator("builtin.nl2table")),
            (frozenset({"n4"}), an_operator("builtin.aggregate")),
        ],
        SOURCE_KINDS,
    )
    taken = schema.model_validate(a_binding(candidate_index=1, inputs=("n3",))).model_dump()
    assert taken["choice"]["candidate_index"] == 1
    # Variant 1 is the scanless one, so naming a source under it is not representable.
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding(candidate_index=1, inputs=("n3",), scan_source_n1=POSTINGS))


def test_a_binding_decision_with_no_candidate_at_all_raises() -> None:
    """A node with no candidate is the physical planner's no_operator case, not a schema."""
    with pytest.raises(BlueDataPlannerError, match="at least one candidate"):
        cover_choice_schema(a_two_scan_plan(), [], SOURCE_KINDS)


def test_a_binding_names_one_input_per_node_on_the_candidates_frontier() -> None:
    """The nodes feeding a fragment are the plan's to decide, so the model only names them."""
    schema = cover_choice_schema(
        a_two_scan_plan(), [(frozenset({"n4"}), an_operator("builtin.aggregate"))], SOURCE_KINDS
    )
    accepted = schema.model_validate(a_binding(inputs=("n3",))).model_dump()
    assert accepted["choice"]["input_name_n3"] == "the n3 table"
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding(inputs=("n3", "n1")))
