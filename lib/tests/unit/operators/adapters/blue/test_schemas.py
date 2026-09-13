"""The parameter models, checked against the declarations and the validator of each operation."""

from __future__ import annotations

from typing import Any

from blue_data_ops.operators.registry import versioned_registry

from blue_data_planner.operators.adapters.blue.operators import OPERATIONS

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
    "blue.semantic_filter": {"filter_conditions": {"city": "is in Texas"}},
    "blue.semantic_project": {"projection_instructions": "keep the city column"},
    # No extract_on_fields: reading every field is the entity's default shape.
    "blue.semantic_extract": {"entities": [{"name": "state"}]},
    "blue.semantic_transform": {
        "output_desc": {
            "state": {"source_fields": ["city"], "description": "the state of the city"}
        }
    },
    "blue.semantic_join": {"left_fields": ["city"], "right_fields": ["city"]},
    "blue.nl2llm": {"query": "how many states are there"},
    "blue.nl2sql": {
        "question": "how many drivers are there",
        "source": "tag_bench",
        "database": "formula_1",
        "collection": "main",
        "protocol": "sqlite",
    },
}

_ROWS: list[list[dict[str, Any]]] = [
    [{"city": "Austin", "population": 2}],
    [{"city": "Austin", "state": "TX"}],
]


def test_a_call_every_model_accepts_is_one_its_operation_accepts_too() -> None:
    """The model and the operation's validator are written separately and can disagree."""
    registry = versioned_registry()

    refused: list[str] = []
    for operation in OPERATIONS:
        parameters = operation.parameters.model_validate(
            A_CALL_THE_MODEL_ACCEPTS[operation.operator_id]
        )
        attributes = parameters.model_dump(exclude_none=True)
        if not registry.resolve(operation.registry_operator_id).validate(_ROWS, attributes):
            refused.append(f"{operation.operator_id} refused {attributes}")

    assert not refused, refused


def test_every_model_field_names_an_attribute_its_operation_declares() -> None:
    """A field the operation does not declare would be dropped without a word."""
    registry = versioned_registry()

    unknown: list[str] = []
    for operation in OPERATIONS:
        declared = set(registry.resolve(operation.registry_operator_id).attributes)
        extra = set(operation.parameters.model_fields) - declared
        if extra:
            unknown.append(f"{operation.operator_id} names {sorted(extra)}")

    assert not unknown, unknown


def test_no_operation_is_left_without_an_attribute_it_requires() -> None:
    """An attribute the operation requires comes from the model or from the operation's default."""
    registry = versioned_registry()

    missing: list[str] = []
    for operation in OPERATIONS:
        fields = set(operation.parameters.model_fields)
        for name, attribute in registry.resolve(operation.registry_operator_id).attributes.items():
            if attribute.required and name not in fields and not attribute.has_default:
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


def test_every_model_refuses_a_key_it_does_not_name() -> None:
    """A model accepting extra keys would let a runner reach a tuning parameter it was not
    offered."""
    permissive = [
        operation.operator_id
        for operation in OPERATIONS
        if operation.parameters.model_config.get("extra") != "forbid"
    ]

    assert not permissive, permissive
