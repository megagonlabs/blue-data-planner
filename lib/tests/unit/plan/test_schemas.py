"""What a model may and may not answer with at each planning stage."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from blue_data_planner.plan import cover_choice_schema, logical_plan_schema
from blue_data_planner.types import (
    BlueDataPlannerError,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PhysicalOperator,
    PhysicalPlanFragment,
    PipelineNode,
)

POSTINGS = "/source/jobs/database/jobs/collection/public/entity/postings"
COMPANIES = "/source/companies/database/co/collection/public/entity/fortune500"


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

    def scan(node_id: str, source: str | None) -> PipelineNode:
        return PipelineNode(
            id=node_id,
            kind=LogicalOperator.SCAN,
            input_node_ids=[],
            attributes={"instruction": "do it", "source": source},
        )

    def node(node_id: str, kind: LogicalOperator, inputs: list[str]) -> PipelineNode:
        return PipelineNode(
            id=node_id, kind=kind, input_node_ids=inputs, attributes={"instruction": "do it"}
        )

    return LogicalPlan(
        id="p1",
        parent_id=None,
        output_node_id="n5",
        nodes=[
            scan("n1", POSTINGS),
            scan("n2", COMPANIES),
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


def a_scan_and_filter() -> LogicalPlan:
    """n1 SCAN -> n2 FILTER, the smallest plan a candidate can be rooted in the middle of."""
    scan = PipelineNode(
        id="n1",
        kind=LogicalOperator.SCAN,
        input_node_ids=[],
        attributes={"instruction": "do it", "source": None},
    )
    filter_node = PipelineNode(
        id="n2",
        kind=LogicalOperator.FILTER,
        input_node_ids=["n1"],
        attributes={"instruction": "do it"},
    )
    return LogicalPlan(id="p2", parent_id=None, output_node_id=None, nodes=[scan, filter_node])


def two_candidates() -> list[tuple[frozenset[str], PhysicalOperator]]:
    """`op.wide`, fusing the scan and the filter, next to `op.narrow`, taking the filter alone."""
    return [
        (frozenset({"n1", "n2"}), an_operator("op.wide", source_kinds=None)),
        (frozenset({"n2"}), an_operator("op.narrow")),
    ]


def a_fragment_over_n1_and_n2() -> PhysicalPlanFragment:
    """A fragment a previous cover already ran over n1 and n2, offered back as reusable."""
    return PhysicalPlanFragment(
        id="f1",
        logical_node_ids=["n1", "n2"],
        root_logical_node_id="n2",
        operator_id="op.wide",
        scan_sources={},
        instruction="scan and filter",
        input_data=[],
        required_output_columns=[OutputColumn(name="title", dtype="str")],
    )


def a_binding(candidate: str, inputs: tuple[str, ...] = (), **overrides: Any) -> dict[str, Any]:
    """One binding decision's payload."""
    payload: dict[str, Any] = {
        "candidate": candidate,
        "instruction": "count the postings per title",
        "required_output_columns": [{"name": "title", "dtype": "str"}],
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


@pytest.mark.parametrize(
    "node",
    [
        {"id": "n1", "kind": "RETURN", "input_node_ids": [], "instruction": "give the answer"},
        {"id": "n1", "kind": "REPLAN", "input_node_ids": [], "instruction": "wait for the rows"},
        {"id": "n1", "kind": "SCAN", "input_node_ids": [], "instruction": "read the postings"},
        {
            "id": "n1",
            "kind": "SCAN",
            "input_node_ids": [],
            "instruction": "read the postings",
            "source": None,
            "required_output_columns": [],
        },
    ],
    ids=[
        "a-return-carrying-an-instruction",
        "a-replan-listing-inputs",
        "a-scan-naming-no-source",
        "a-scan-carrying-columns",
    ],
)
def test_each_node_kind_is_held_to_the_fields_its_kind_carries(node: dict[str, Any]) -> None:
    """A RETURN names columns, a REPLAN lists no node, and a SCAN names a source and no columns."""
    with pytest.raises(ValidationError):
        logical_plan_schema().model_validate({"output_node_id": "n1", "nodes": [node]})


@pytest.mark.parametrize(
    "source",
    [POSTINGS, None],
    ids=["a-catalog-path", "world-knowledge"],
)
def test_a_scan_naming_a_path_or_null_reads_back_with_its_source(source: str | None) -> None:
    """The ordinary shape: one scan naming its source, or none, and one ending."""
    written = logical_plan_schema().model_validate(
        {
            "output_node_id": "n2",
            "nodes": [
                {
                    "id": "n1",
                    "kind": "SCAN",
                    "input_node_ids": [],
                    "instruction": "read the postings",
                    "source": source,
                },
                {"id": "n2", "kind": "RETURN", "input_node_ids": ["n1"], "columns": ["title"]},
            ],
        }
    )
    assert written.model_dump()["nodes"][0]["source"] == source


def test_a_binding_names_one_input_per_node_on_the_candidates_frontier() -> None:
    """The nodes feeding a fragment are the plan's to decide, so the model only names them."""
    schema = cover_choice_schema(
        a_two_scan_plan(),
        "n4",
        [(frozenset({"n4"}), an_operator("builtin.aggregate"))],
        None,
    )
    label = "n4:builtin.aggregate:[n4]"
    accepted = schema.model_validate(a_binding(label, inputs=("n3",))).model_dump()
    assert accepted["choice"]["input_name_n3"] == "the n3 table"
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding(label, inputs=("n3", "n1")))


def test_a_binding_naming_a_scans_source_is_refused() -> None:
    """The plan's scans already name their sources, so a binding has no field for one."""
    schema = cover_choice_schema(
        a_two_scan_plan(),
        "n4",
        [(frozenset({"n1", "n2", "n3", "n4"}), an_operator("builtin.nl2table"))],
        None,
    )
    with pytest.raises(ValidationError):
        schema.model_validate(a_binding("n4:builtin.nl2table:[n1,n2,n3,n4]", source=POSTINGS))


def test_a_binding_picks_one_candidate_by_its_label() -> None:
    """Several candidates are several variants, and the label says which one was taken."""
    narrow_label = "n4:builtin.aggregate:[n4]"
    schema = cover_choice_schema(
        a_two_scan_plan(),
        "n4",
        [
            (frozenset({"n1", "n2", "n3", "n4"}), an_operator("builtin.nl2table")),
            (frozenset({"n4"}), an_operator("builtin.aggregate")),
        ],
        None,
    )
    taken = schema.model_validate(a_binding(narrow_label, inputs=("n3",))).model_dump()
    assert taken["choice"]["candidate"] == narrow_label


def test_a_binding_decision_with_no_candidate_at_all_raises() -> None:
    """A node with no candidate is the physical planner's no_operator case, not a schema."""
    with pytest.raises(BlueDataPlannerError, match="at least one candidate"):
        cover_choice_schema(a_two_scan_plan(), "n4", [], None)


def test_a_candidate_is_named_by_its_operator_and_its_node_set() -> None:
    """A name that says what it is reads the same wherever it appears in a list."""
    schema = cover_choice_schema(a_scan_and_filter(), "n2", two_candidates(), None)

    written = schema.model_validate(
        {
            "choice": {
                "candidate": "n2:op.wide:[n1,n2]",
                "instruction": "do it",
                "required_output_columns": [{"name": "title", "dtype": "str"}],
            }
        }
    )

    assert written.model_dump()["choice"]["candidate"] == "n2:op.wide:[n1,n2]"


@pytest.mark.parametrize(
    "required_output_columns",
    [None, []],
    ids=["missing", "empty"],
)
def test_a_binding_naming_no_output_columns_is_refused(required_output_columns: Any) -> None:
    """The physical planner writes at least one column for every fragment it binds."""
    schema = cover_choice_schema(a_scan_and_filter(), "n2", two_candidates(), None)
    choice: dict[str, Any] = {"candidate": "n2:op.wide:[n1,n2]", "instruction": "do it"}
    if required_output_columns is not None:
        choice["required_output_columns"] = required_output_columns

    with pytest.raises(ValidationError):
        schema.model_validate({"choice": choice})


def test_a_label_naming_no_candidate_of_this_call_is_refused() -> None:
    """A label the call did not offer must not resolve to whatever sits at that position."""
    schema = cover_choice_schema(a_scan_and_filter(), "n2", two_candidates(), None)

    with pytest.raises(ValidationError):
        schema.model_validate({"choice": {"candidate": "n2:op.gone:[n2]", "instruction": "do it"}})


def test_reusing_a_fragment_is_a_choice_and_carries_nothing_else() -> None:
    """Everything about a reused fragment is already written down, so there is nothing to write."""
    schema = cover_choice_schema(
        a_scan_and_filter(), "n2", two_candidates(), a_fragment_over_n1_and_n2()
    )

    written = schema.model_validate({"choice": {"candidate": "n2:reuse:[n1,n2]"}})

    assert written.model_dump()["choice"] == {"candidate": "n2:reuse:[n1,n2]"}


@pytest.mark.parametrize(
    "candidates",
    [two_candidates(), two_candidates()[1:]],
    ids=["two_candidates", "one_candidate"],
)
def test_turning_down_every_candidate_is_a_choice_that_carries_a_reason(
    candidates: list[tuple[frozenset[str], PhysicalOperator]],
) -> None:
    """A binding may take none of the candidates, however many there are, so long as it says why."""
    schema = cover_choice_schema(a_scan_and_filter(), "n2", candidates, None)

    written = schema.model_validate(
        {"choice": {"candidate": "none", "reason": "no candidate reads images"}}
    )

    assert written.model_dump()["choice"] == {
        "candidate": "none",
        "reason": "no candidate reads images",
    }


def test_turning_down_every_candidate_with_no_reason_is_refused() -> None:
    """A binding that turns every candidate down without a reason is refused."""
    schema = cover_choice_schema(a_scan_and_filter(), "n2", two_candidates(), None)

    with pytest.raises(ValidationError):
        schema.model_validate({"choice": {"candidate": "none"}})


@pytest.mark.parametrize(
    ("name", "schema"),
    [
        ("logical plan", logical_plan_schema()),
        (
            "cover choice",
            cover_choice_schema(
                a_scan_and_filter(), "n2", two_candidates(), a_fragment_over_n1_and_n2()
            ),
        ),
    ],
)
def test_a_schema_offering_a_choice_stays_within_what_a_strict_provider_accepts(
    name: str, schema: type[BaseModel]
) -> None:
    """A provider enforcing a schema during decoding rejects oneOf and discriminator outright."""
    written = json.dumps(schema.model_json_schema())

    assert "oneOf" not in written, name
    assert "discriminator" not in written, name
