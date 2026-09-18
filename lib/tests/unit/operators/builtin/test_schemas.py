"""What the built-in operators accept as parameters."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from blue_data_planner.operators.builtin.schemas import (
    AggregateParameters,
    AndPredicate,
    BoundValues,
    Comparison,
    ComparisonValue,
    DistinctParameters,
    ExpandParameters,
    FilterParameters,
    IndexedBoundValues,
    JoinParameters,
    Nl2TableParameters,
    NotPredicate,
    OrPredicate,
    ProjectParameters,
    ScanParameters,
    SortSuperlativeParameters,
    UnionParameters,
)

# Every schema a model fills for a built-in operator, which is what an operator declares.
BUILTIN_PARAMETER_SCHEMAS = [
    AggregateParameters,
    DistinctParameters,
    ExpandParameters,
    FilterParameters,
    JoinParameters,
    Nl2TableParameters,
    ProjectParameters,
    ScanParameters,
    SortSuperlativeParameters,
    UnionParameters,
]


def test_a_predicate_reads_back_as_the_tree_it_was_written_as() -> None:
    """A filter's predicate nests and, or, and not to whatever depth a plan asks for, and
    survives a round trip through model_dump() and back."""
    parameters = FilterParameters.model_validate(
        {
            "predicate": {
                "or": [
                    {
                        "and": [
                            {"column": "state", "operator": "eq", "value": "CA"},
                            {"column": "rating", "operator": "gte", "value": 4.0},
                        ]
                    },
                    {"not": {"column": "closed", "operator": "is_null"}},
                ]
            }
        }
    )

    predicate = parameters.predicate
    assert isinstance(predicate, OrPredicate)
    conjunction, negation = predicate.conditions
    assert isinstance(conjunction, AndPredicate)
    assert isinstance(negation, NotPredicate)
    assert isinstance(negation.condition, Comparison)

    # model_dump() writes each field's own name ("conditions", "condition"), not its and/or/not
    # alias, so the tree has to accept its own field names back to survive the round trip.
    round_tripped = FilterParameters.model_validate(parameters.model_dump())
    assert round_tripped.model_dump() == parameters.model_dump()


@pytest.mark.parametrize(
    "value",
    ["CA", 4, 4.0, True, None, ["CA", "NY"], [1, 2.5]],
)
def test_a_comparison_keeps_the_kind_of_value_it_was_written_with(value: ComparisonValue) -> None:
    """A value compares as a string, a number, a boolean, null, or a list, and is not coerced
    into a neighboring type on the way in."""
    operator = "in" if isinstance(value, list) else "is_null" if value is None else "eq"

    written = Comparison.model_validate({"column": "state", "operator": operator, "value": value})

    assert written.value == value
    assert type(written.value) is type(value)


@pytest.mark.parametrize(
    ("schema", "parameters"),
    [
        (FilterParameters, {"predicate": {"column": "closed", "operator": "is_null", "value": 1}}),
        (FilterParameters, {"predicate": {"column": "state", "operator": "in", "value": "CA"}}),
        (
            FilterParameters,
            {"predicate": {"column": "state", "operator": "matches", "value": "CA"}},
        ),
        (FilterParameters, {"predicate": {"column": "closed", "operator": "eq"}}),
        (FilterParameters, {"predicate": {"column": "tags", "operator": "eq", "value": {"a": 1}}}),
        (AggregateParameters, {"aggregations": [{"function": "avg", "into": "mean"}]}),
        (ProjectParameters, {"columns": []}),
        (ProjectParameters, {"columns": [{"column": "id"}], "unknown": 1}),
        (ProjectParameters, {"columns": [{"column": "id", "into": ""}]}),
        (ProjectParameters, {"columns": [{"column": ""}]}),
    ],
)
def test_parameters_an_operator_could_not_run_are_refused(
    schema: type[BaseModel], parameters: dict[str, Any]
) -> None:
    """A schema refuses parameters whose shape the operator has no way to carry out."""
    with pytest.raises(ValidationError):
        schema.model_validate(parameters)


def test_a_scan_takes_a_catalog_path_and_optionally_the_values_bound_into_it() -> None:
    """The path is required; bound values are not, since most scans read a source outright."""
    plain = ScanParameters.model_validate(
        {"scan_source": "/source/s/database/d/collection/c/entity/e"}
    )
    bound = ScanParameters.model_validate(
        {
            "scan_source": "/source/s/database/d/collection/c/entity/e",
            "bound_values": {"input_column": "city", "attribute": "city"},
        }
    )

    assert plain.bound_values is None
    assert bound.bound_values == BoundValues(input_column="city", attribute="city")


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"scan_source": ""},
        {"scan_source": "/source/s", "columns": ["city"]},
        {"scan_source": "/source/s", "bound_values": {"input_column": "city"}},
        {"scan_source": "/source/s", "bound_values": {"input_column": "", "attribute": "city"}},
    ],
    ids=["no_path", "empty_path", "unknown_key", "half_a_binding", "empty_column"],
)
def test_a_scan_refuses_parameters_it_cannot_act_on(parameters: dict[str, Any]) -> None:
    """A missing path, an unknown key, or half a binding is refused rather than defaulted."""
    with pytest.raises(ValidationError):
        ScanParameters.model_validate(parameters)


def test_an_indexed_bound_value_names_the_first_input_unless_told_otherwise() -> None:
    """input_index defaults to the first input of a multi-input read and can name a later one."""
    default = IndexedBoundValues.model_validate({"input_column": "id", "attribute": "store_id"})
    named = IndexedBoundValues.model_validate(
        {"input_index": 2, "input_column": "id", "attribute": "store_id"}
    )

    assert (default.input_index, named.input_index) == (0, 2)


def test_a_negative_input_index_is_not_a_parameter_the_operator_takes() -> None:
    """No table sits before the first, so the schema refuses the index rather than the operator."""
    with pytest.raises(ValidationError):
        IndexedBoundValues.model_validate(
            {"input_index": -1, "input_column": "id", "attribute": "store_id"}
        )


def test_builtin_scans_bound_values_cannot_name_an_input_index() -> None:
    """builtin.scan takes exactly one input, so its own schema has no index to set at all."""
    with pytest.raises(ValidationError):
        BoundValues.model_validate({"input_index": 1, "input_column": "city", "attribute": "city"})


def test_nl2table_takes_an_instruction_and_the_entities_to_read() -> None:
    """A fragment scanning nothing is a valid parameter set: the entity list may be empty."""
    parameters = Nl2TableParameters.model_validate(
        {
            "instruction": "count the stores in each city",
            "scan_sources": [
                {"scan_source": "/source/tag_bench/database/retail/collection/public/entity/stores"}
            ],
        }
    )
    empty = Nl2TableParameters.model_validate({"instruction": "join what you were given"})

    assert parameters.scan_sources[0].bound_values is None
    assert empty.scan_sources == []


def test_nl2tables_scan_source_keeps_the_input_index_its_bound_values_were_given() -> None:
    """A non-default input_index set on one scan source's bound values survives model_validate()
    through Nl2TableParameters and IndexedScanSource, not just through IndexedBoundValues built
    on its own."""
    parameters = Nl2TableParameters.model_validate(
        {
            "instruction": "join stores with their sales",
            "scan_sources": [
                {
                    "scan_source": "/source/tag_bench/database/retail/collection/public/entity/s",
                    "bound_values": {
                        "input_index": 1,
                        "input_column": "store_id",
                        "attribute": "id",
                    },
                }
            ],
        }
    )

    bound_values = parameters.scan_sources[0].bound_values
    assert bound_values is not None
    assert bound_values.input_index == 1


def typeless_subschemas(schema: dict[str, Any]) -> list[str]:
    """Return the path of every field of a JSON schema that declares no type of its own."""
    declares_a_type = {"type", "anyOf", "allOf", "$ref", "enum", "const"}
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        for name, field in cast(dict[str, Any], node).get("properties", {}).items():
            if not declares_a_type & cast(dict[str, Any], field).keys():
                found.append(f"{path}.{name}")
        for key, child in cast(dict[str, Any], node).items():
            if isinstance(child, dict):
                walk(child, f"{path}.{key}")

    walk(schema, "")
    return found


@pytest.mark.parametrize("schema", BUILTIN_PARAMETER_SCHEMAS)
def test_a_parameter_schema_declares_a_type_for_every_value_it_takes(
    schema: type[BaseModel],
) -> None:
    """A provider enforcing a schema during decoding refuses a field that names no type."""
    assert typeless_subschemas(schema.model_json_schema()) == []
