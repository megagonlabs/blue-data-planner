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
    JoinParameters,
    MembershipComparison,
    NotPredicate,
    OrPredicate,
    Predicate,
    ProjectParameters,
    ScanParameters,
    SortSuperlativeParameters,
    UnionParameters,
    ValueOperand,
)

# Every schema a model fills for a built-in operator, which is what an operator declares.
BUILTIN_PARAMETER_SCHEMAS = [
    AggregateParameters,
    DistinctParameters,
    ExpandParameters,
    FilterParameters,
    JoinParameters,
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
                            {
                                "left": {"column": "state"},
                                "operator": "eq",
                                "right": {"value": "CA"},
                            },
                            {
                                "left": {"column": "rating"},
                                "operator": "gte",
                                "right": {"column": "threshold"},
                            },
                        ]
                    },
                    {"not": {"operand": {"column": "closed"}, "operator": "is_null"}},
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


@pytest.mark.parametrize("value", ["CA", 4, 4.0, True])
def test_a_literal_operand_keeps_the_type_it_was_written_with(value: Any) -> None:
    """A literal is not coerced into a neighboring type on the way in."""
    written = ValueOperand.model_validate({"value": value})

    assert type(written.value) is type(value)
    assert written.value == value


@pytest.mark.parametrize("values", [["CA", "NY"], [1, 2.5]])
def test_a_membership_list_keeps_the_type_of_each_element(values: list[Any]) -> None:
    """Each element of a membership list reads back as the type it was written with, so an int
    is not turned into a float."""
    written = MembershipComparison.model_validate(
        {"left": {"column": "state"}, "operator": "in", "values": values}
    )

    assert [type(element) for element in written.values] == [type(v) for v in values]


@pytest.mark.parametrize(
    ("schema", "parameters"),
    [
        (
            FilterParameters,
            {"predicate": {"operand": {"column": "closed"}, "operator": "is_null", "right": 1}},
        ),
        (
            FilterParameters,
            {"predicate": {"left": {"column": "state"}, "operator": "in", "values": "CA"}},
        ),
        (
            FilterParameters,
            {
                "predicate": {
                    "left": {"column": "state"},
                    "operator": "matches",
                    "right": {"value": "CA"},
                }
            },
        ),
        (
            FilterParameters,
            {"predicate": {"left": {"column": "state"}, "operator": "re.search", "pattern": 4}},
        ),
        (FilterParameters, {"predicate": {"left": {"column": "closed"}, "operator": "eq"}}),
        (
            FilterParameters,
            {
                "predicate": {
                    "left": {"column": "tags"},
                    "operator": "eq",
                    "right": {"value": {"a": 1}},
                }
            },
        ),
        pytest.param(
            FilterParameters,
            {
                "predicate": {
                    "left": {"column": "closed"},
                    "operator": "eq",
                    "right": {"value": None},
                }
            },
            id="a_null_literal",
        ),
        (
            FilterParameters,
            {
                "predicate": {
                    "left": {"column": "a", "value": 1},
                    "operator": "eq",
                    "right": {"value": 1},
                }
            },
        ),
        (FilterParameters, {"predicate": {"column": "state", "operator": "eq", "value": "CA"}}),
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


def test_each_comparison_variant_declares_only_the_operators_its_right_side_supports() -> None:
    """Each operator belongs to exactly one variant's enum, and each variant requires the right
    side its operators take: a second operand, a list of literals, a pattern string, or
    nothing, so a provider's schema can never pair "is_null" with a right side or route "in" to
    a variant whose right side is not a list."""
    comparisons = FilterParameters.model_json_schema()["$defs"]
    binary = comparisons["BinaryComparison"]
    membership = comparisons["MembershipComparison"]
    pattern = comparisons["PatternComparison"]
    null = comparisons["NullComparison"]

    assert _literal_values(binary["properties"]["operator"]) == {
        "eq",
        "ne",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
    }
    assert _literal_values(membership["properties"]["operator"]) == {"in"}
    assert _literal_values(pattern["properties"]["operator"]) == {"re.search"}
    assert _literal_values(null["properties"]["operator"]) == {"is_null"}

    assert {"left", "right"} <= set(binary["required"])
    assert membership["properties"]["values"]["type"] == "array"
    assert "values" in membership["required"]
    assert pattern["properties"]["pattern"]["type"] == "string"
    assert "pattern" in pattern["required"]
    assert set(null["properties"]) == {"operand", "operator"}
    assert null["additionalProperties"] is False


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
