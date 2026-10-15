"""The parameter models, checked against the declarations and the validator of each operation."""

from __future__ import annotations

from typing import Any

import pytest
from blue_data_ops.operators.registry import versioned_registry
from pydantic import ValidationError

from blue_data_planner.operators.adapters.blue.operators import OPERATIONS, Operation
from blue_data_planner.operators.adapters.blue.schemas import (
    BlueParameters,
    NL2SQLParameters,
    ProjectParameters,
    SemanticFilterParameters,
    SemanticTransformParameters,
)

# One call per operation, filling what its model requires and nothing else, so a model whose
# own default shape the operation refuses is caught here rather than on a benchmark run.
A_CALL_THE_MODEL_ACCEPTS: dict[str, dict[str, Any]] = {
    "blue.select": {"operand_key": "population", "operand": ">", "operand_val": 5},
    "blue.project": {"kept_keys": ["city"]},
    "blue.join": {"join_on": [["city"], ["city"]]},
    "blue.intersect": {},
    "blue.union": {},
    "blue.sum": {"sum_key": "population"},
    "blue.avg": {"avg_key": "population"},
    "blue.min": {"min_key": "population"},
    "blue.max": {"max_key": "population"},
    "blue.argmax": {"argmax_key": "population"},
    "blue.argmin": {"argmin_key": "population"},
    "blue.semantic_filter": {"filter_conditions": [{"name": "city", "condition": "is in Texas"}]},
    "blue.semantic_project": {"projection_instructions": "keep the city column"},
    # No extract_on_fields: reading every field is the entity's default shape.
    "blue.semantic_extract": {"entities": [{"name": "state"}]},
    "blue.semantic_transform": {
        "output_desc": [
            {"name": "state", "source_fields": ["city"], "description": "the state of the city"}
        ]
    },
    "blue.semantic_join": {"left_fields": ["city"], "right_fields": ["city"]},
    "blue.nl2llm": {"query": "how many states are there"},
    "blue.nl2sql": {
        "question": "how many drivers are there",
        "scan_source": "/source/tag_bench/database/formula_1/collection/main/entity/drivers",
    },
}

_ROWS: list[list[dict[str, Any]]] = [
    [{"city": "Austin", "population": 2}],
    [{"city": "Austin", "state": "TX"}],
]


def attributes_the_adapter_sends(operation: Operation) -> dict[str, Any]:
    """Return what the call in A_CALL_THE_MODEL_ACCEPTS hands the operation."""
    parameters = operation.parameters.model_validate(
        A_CALL_THE_MODEL_ACCEPTS[operation.operator_id]
    )
    attributes = parameters.operation_attributes()
    if operation.operator_id == "blue.nl2sql":
        # The adapter reads the protocol off the source's registry record at call time.
        attributes["protocol"] = "sqlite"
    return attributes


def test_a_call_every_model_accepts_is_one_its_operation_accepts_too() -> None:
    """The model and the operation's validator are written separately and can disagree."""
    registry = versioned_registry()

    refused: list[str] = []
    for operation in OPERATIONS:
        attributes = attributes_the_adapter_sends(operation)
        if not registry.resolve(operation.registry_operator_id).validate(_ROWS, attributes):
            refused.append(f"{operation.operator_id} refused {attributes}")

    assert not refused, refused


def test_every_attribute_the_adapter_sends_is_one_its_operation_declares() -> None:
    """An attribute the operation does not declare would be dropped without a word."""
    registry = versioned_registry()

    unknown: list[str] = []
    for operation in OPERATIONS:
        declared = set(registry.resolve(operation.registry_operator_id).attributes)
        extra = set(attributes_the_adapter_sends(operation)) - declared
        if extra:
            unknown.append(f"{operation.operator_id} names {sorted(extra)}")

    assert not unknown, unknown


def test_no_operation_is_left_without_an_attribute_it_requires() -> None:
    """An attribute the operation requires comes from the adapter or from the operation's
    default."""
    registry = versioned_registry()

    missing: list[str] = []
    for operation in OPERATIONS:
        sent = set(attributes_the_adapter_sends(operation))
        for name, attribute in registry.resolve(operation.registry_operator_id).attributes.items():
            if attribute.required and name not in sent and not attribute.has_default:
                missing.append(f"{operation.operator_id} leaves {name} unset")

    assert not missing, missing


def test_every_model_field_says_what_it_holds() -> None:
    """A field with no description gives the fragment runner nothing to write a value from."""
    undescribed: list[str] = []
    for operation in OPERATIONS:
        for name, field in operation.parameters.model_fields.items():
            if not field.description:
                undescribed.append(f"{operation.operator_id}.{name}")

    assert not undescribed, undescribed


def objects_in(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object a JSON schema describes: the top level and each definition it refers to."""
    definitions: dict[str, Any] = schema.get("$defs", {})
    return [schema, *definitions.values()]


def test_every_object_in_a_parameter_schema_refuses_keys_it_does_not_name() -> None:
    """A provider enforcing the schema strictly refuses an object whose keys the schema does not
    fix, and a model left free to add keys could reach a tuning parameter it was not offered."""
    permissive: list[str] = []
    for operation in OPERATIONS:
        for described in objects_in(operation.parameters.model_json_schema()):
            if described.get("additionalProperties") is not False:
                permissive.append(f"{operation.operator_id}: {described.get('title')}")

    assert not permissive, permissive


# Each schema holding a map, an entry list a model writes for it, and the map the operation gets.
_LISTS_AND_THE_MAPS_THEY_SPELL: list[
    tuple[type[BlueParameters], dict[str, Any], dict[str, Any]]
] = [
    (
        ProjectParameters,
        {"kept_keys": ["city"], "key_mapping": [{"old_key": "city", "new_key": "town"}]},
        {"kept_keys": ["city"], "key_mapping": {"city": "town"}},
    ),
    (
        SemanticFilterParameters,
        {"filter_conditions": [{"name": "city", "condition": "is in Texas"}]},
        {"filter_conditions": {"city": "is in Texas"}, "context": ""},
    ),
    (
        SemanticTransformParameters,
        {
            "output_desc": [{"name": "state", "description": "its state", "source_fields": []}],
            "input_meta": [{"name": "city", "description": "a US city"}],
        },
        {
            "output_desc": {
                "state": {
                    "description": "its state",
                    "source_fields": [],
                    "transformation_type": "field_mapping_with_value_change",
                }
            },
            "input_meta": {"city": {"description": "a US city"}},
        },
    ),
]


@pytest.mark.parametrize(("schema", "written", "attributes"), _LISTS_AND_THE_MAPS_THEY_SPELL)
def test_a_list_the_model_writes_reaches_the_operation_as_the_map_it_takes(
    schema: type[BlueParameters], written: dict[str, Any], attributes: dict[str, Any]
) -> None:
    """A schema can fix no map's keys, so the model writes named entries and the operation is
    handed the map they spell."""
    assert schema.model_validate(written).operation_attributes() == attributes


# Each schema holding a map, and an entry list a model writes for it that names one key twice.
_LISTS_NAMING_ONE_KEY_TWICE: list[tuple[type[BlueParameters], dict[str, Any]]] = [
    (
        ProjectParameters,
        {
            "kept_keys": ["city"],
            "key_mapping": [
                {"old_key": "city", "new_key": "town"},
                {"old_key": "city", "new_key": "place"},
            ],
        },
    ),
    (
        SemanticFilterParameters,
        {
            "filter_conditions": [
                {"name": "city", "condition": "is in Texas"},
                {"name": "city", "condition": "is a capital"},
            ]
        },
    ),
    (
        SemanticTransformParameters,
        {
            "output_desc": [
                {"name": "state", "description": "its state", "source_fields": []},
                {"name": "state", "description": "its region", "source_fields": []},
            ]
        },
    ),
]


@pytest.mark.parametrize(("schema", "written"), _LISTS_NAMING_ONE_KEY_TWICE)
def test_a_name_written_twice_is_refused_rather_than_one_entry_dropped(
    schema: type[BlueParameters], written: dict[str, Any]
) -> None:
    """The map an operation takes holds one entry per name, so a second one would be lost."""
    with pytest.raises(ValidationError, match="may appear once"):
        schema.model_validate(written)


def test_a_scan_source_that_is_not_an_entity_path_is_refused() -> None:
    """The operation is called with the source, database, and collection the path names, so a
    value that names no entity has nothing to split."""
    with pytest.raises(ValidationError, match="is not an entity path"):
        NL2SQLParameters.model_validate(
            {"question": "how many orders are there", "scan_source": "northwind"}
        )


def test_a_scan_source_reaches_the_operation_as_the_source_database_and_collection_it_names() -> (
    None
):
    """The model copies one path the fragment shows, and the operation takes three names."""
    parameters = NL2SQLParameters.model_validate(A_CALL_THE_MODEL_ACCEPTS["blue.nl2sql"])

    attributes = parameters.operation_attributes()

    assert (attributes["source"], attributes["database"], attributes["collection"]) == (
        "tag_bench",
        "formula_1",
        "main",
    )
    assert "scan_source" not in attributes
