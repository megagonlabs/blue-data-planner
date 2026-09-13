"""The Blue operations a plan can be bound to, and what each call does with what it is handed."""

from __future__ import annotations

import pytest
from blue_data_ops.operators.registry import versioned_registry

from blue_data_planner.data import DataRegistry
from blue_data_planner.operators.adapters.blue import blue_operators
from blue_data_planner.operators.adapters.blue.operators import OPERATIONS
from blue_data_planner.types import INPUT_ARITY, PhysicalOperator, RecoverableOperatorError
from tests.doubles.blue_operations import (
    ScriptedOperation,
    a_registry_answering_every_operation,
    scripted_operation,
)
from tests.unit.operators.adapters.blue.test_schemas import A_CALL_THE_MODEL_ACCEPTS


def test_every_operation_resolves_the_registry_id_it_names() -> None:
    """An id the registry does not carry has to fail while the suite is built, not mid-run."""
    registry = versioned_registry()

    for operation in OPERATIONS:
        assert registry.resolve(operation.registry_operator_id) is not None


def test_each_operation_binds_the_newest_card_the_registry_carries_for_it() -> None:
    """A newer card refuses more bad input in the operation's own validator, so it is the one
    bound."""
    registry = versioned_registry()

    behind: list[str] = []
    for operation in OPERATIONS:
        name, _, version = operation.registry_operator_id.partition("@")
        latest = registry.find_latest(name)
        if latest is not None and latest.version != version:
            behind.append(f"{operation.operator_id} binds {version}, and {latest.version} exists")

    assert not behind, behind


def a_suite(*scripted: ScriptedOperation) -> dict[str, PhysicalOperator]:
    """Return the suite built over scripted operations, keyed by operator id."""
    registry = a_registry_answering_every_operation(*scripted)
    built = blue_operators(registry, DataRegistry(name="test", rebuild=True))
    return {one.operator_id: one for one in built}


def test_a_bundle_of_the_wrong_length_is_refused_before_the_operation_runs() -> None:
    """An operation handed the wrong number of tables answers whatever arrived first, quietly."""
    for operation in OPERATIONS:
        if len(operation.logical_operators) != 1:
            continue
        scripted = scripted_operation(operation.registry_operator_id.partition("@")[0])
        one = a_suite(scripted)[operation.operator_id]
        arity = INPUT_ARITY[next(iter(operation.logical_operators))]
        too_many = [[{"city": "Austin"}] for _ in range(arity + 1)]

        try:
            one.call(too_many, A_CALL_THE_MODEL_ACCEPTS[operation.operator_id], [])
        except RecoverableOperatorError:
            pass
        else:
            raise AssertionError(f"{operation.operator_id} accepted {arity + 1} tables")

        assert scripted.seen_inputs is None, f"{operation.operator_id} ran anyway"


def test_the_compound_operation_refuses_a_fragment_whose_rows_it_would_ignore() -> None:
    """It writes its query from a source's schema, so bound tables would be left out in silence."""
    scripted = scripted_operation("nl2sql")
    one = a_suite(scripted)["blue.nl2sql"]

    with pytest.raises(RecoverableOperatorError, match="blue.nl2sql"):
        one.call([[{"city": "Austin"}]], A_CALL_THE_MODEL_ACCEPTS["blue.nl2sql"], [])

    assert scripted.seen_inputs is None


def test_the_compound_operation_runs_on_a_fragment_that_brought_no_rows() -> None:
    """An empty frontier binds nothing, so the query it writes leaves nothing out."""
    scripted = scripted_operation("nl2sql", output=[[{"drivers": 20}]])
    one = a_suite(scripted)["blue.nl2sql"]

    assert one.call([], A_CALL_THE_MODEL_ACCEPTS["blue.nl2sql"], []).data == [[{"drivers": 20}]]
