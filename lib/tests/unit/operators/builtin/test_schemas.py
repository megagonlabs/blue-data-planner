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
    DistinctParameters,
    ExpandParameters,
    FilterParameters,
    InComparison,
    IndexedBoundValues,
    JoinParameters,
    Nl2TableParameters,
    NotPredicate,
    OrPredicate,
    Predicate,
    ProjectParameters,
    ScanParameters,
    SortSuperlativeParameters,
    UnionParameters,
    ValueComparison,
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
    survives a plain model_dump() and back, by default and not because a caller remembered
    an option."""
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

    # And/Or/Not share their field names ("conditions", "condition") and differ only by alias,
    # so comparing dumps was never enough to prove a round trip: an Or dumped without its
    # alias reads back as an And, and the two dumps still match because both hold
    # "conditions". Only the reconstructed tree's own types show that.
    round_tripped = FilterParameters.model_validate(parameters.model_dump())
    assert _predicate_shape(round_tripped.predicate) == _predicate_shape(parameters.predicate)


def _predicate_shape(predicate: Predicate) -> tuple[type, object]:
    """Return a predicate tree's node types and leaf comparisons, recursively."""
    if isinstance(predicate, AndPredicate | OrPredicate):
        return (
            type(predicate),
            [_predicate_shape(condition) for condition in predicate.conditions],
        )
    if isinstance(predicate, NotPredicate):
        return (type(predicate), _predicate_shape(predicate.condition))
    return (type(predicate), predicate.model_dump())


def _elements(value: Any) -> list[Any]:
    """Return a list value's own elements, or a scalar value as its own one-element list."""
    return cast(list[Any], value) if isinstance(value, list) else [value]


@pytest.mark.parametrize(
    ("model", "operator", "value"),
    [
        (ValueComparison, "eq", "CA"),
        (ValueComparison, "eq", 4),
        (ValueComparison, "eq", 4.0),
        (ValueComparison, "eq", True),
        (ValueComparison, "eq", None),
        (InComparison, "in", ["CA", "NY"]),
        (InComparison, "in", [1, 2.5]),
    ],
)
def test_a_comparison_keeps_the_type_of_the_value_it_was_written_with(
    model: type[ValueComparison] | type[InComparison], operator: str, value: Any
) -> None:
    """A value, scalar or a list, is not coerced into a neighboring type on the way in, checked
    element by element so an int written into a list does not read back as a float."""
    written = model.model_validate({"column": "state", "operator": operator, "value": value})

    assert written.value == value
    assert [type(element) for element in _elements(written.value)] == [
        type(element) for element in _elements(value)
    ]


@pytest.mark.parametrize(
    ("schema", "parameters"),
    [
        (FilterParameters, {"predicate": {"column": "closed", "operator": "is_null", "value": 1}}),
        (FilterParameters, {"predicate": {"column": "state", "operator": "in", "value": "CA"}}),
        (
            FilterParameters,
            {"predicate": {"column": "state", "operator": "matches", "value": "CA"}},
        ),
        (
            FilterParameters,
            {"predicate": {"column": "state", "operator": "re.search", "value": 4}},
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


def _literal_values(property_schema: dict[str, Any]) -> set[Any]:
    """Return the literal values a JSON Schema property allows, written as either a const or an
    enum."""
    if "const" in property_schema:
        return {property_schema["const"]}
    return set(property_schema["enum"])


def test_each_comparison_variant_declares_only_the_operators_its_value_shape_supports() -> None:
    """ "is_null" names no "value" property and forbids one, every other operator requires one,
    "in" requires it as a list and "re.search" as a string — and each operator belongs to exactly
    one variant's enum, so a provider's schema can never pair "is_null" with a value or route "in"
    to a variant whose value is not a list."""
    comparisons = FilterParameters.model_json_schema()["$defs"]
    null_comparison = comparisons["NullComparison"]
    value_comparison = comparisons["ValueComparison"]
    in_comparison = comparisons["InComparison"]
    pattern_comparison = comparisons["PatternComparison"]

    assert _literal_values(value_comparison["properties"]["operator"]) == {
        "eq",
        "ne",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
    }
    assert _literal_values(in_comparison["properties"]["operator"]) == {"in"}
    assert _literal_values(null_comparison["properties"]["operator"]) == {"is_null"}
    assert _literal_values(pattern_comparison["properties"]["operator"]) == {"re.search"}

    assert "value" not in null_comparison["properties"]
    assert null_comparison["additionalProperties"] is False
    assert "value" in value_comparison["required"]
    assert "value" in in_comparison["required"]
    assert in_comparison["properties"]["value"]["type"] == "array"
    assert "value" in pattern_comparison["required"]
    assert pattern_comparison["properties"]["value"]["type"] == "string"


def test_each_aggregation_variant_declares_a_column_only_where_its_function_needs_one() -> None:
    """count has an answer for a whole group and leaves its column optional; sum, avg, min, and
    max each need one column to compute over — and each function belongs to exactly one
    variant's enum, so a provider's schema can never route sum, avg, min, or max to the variant
    whose column is optional."""
    aggregations = AggregateParameters.model_json_schema()["$defs"]
    count_aggregation = aggregations["CountAggregation"]
    column_aggregation = aggregations["ColumnAggregation"]

    assert _literal_values(count_aggregation["properties"]["function"]) == {"count"}
    assert _literal_values(column_aggregation["properties"]["function"]) == {
        "sum",
        "avg",
        "min",
        "max",
    }

    assert "column" not in count_aggregation["required"]
    assert "column" in column_aggregation["required"]
