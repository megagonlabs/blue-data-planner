"""What the router answers, and what it refuses."""

from __future__ import annotations

import pytest

from blue_data_planner.operators import OperatorRouter
from blue_data_planner.types import LogicalOperator, OperatorRoutingError
from tests.doubles.operators import recording_operator


def test_an_operator_is_found_by_each_logical_operator_it_declares_in_registration_order() -> None:
    """A compound operator answers for every kind it can absorb, and the order is stable."""
    router = OperatorRouter()
    nl2sql, _ = recording_operator(
        "blue.nl2sql", frozenset({LogicalOperator.SCAN, LogicalOperator.FILTER}), []
    )
    select, _ = recording_operator("blue.select", frozenset({LogicalOperator.FILTER}), [])
    router.register(nl2sql)
    router.register(select)

    assert [operator.operator_id for operator in router.operators_for(LogicalOperator.FILTER)] == [
        "blue.nl2sql",
        "blue.select",
    ]
    assert [operator.operator_id for operator in router.operators_for(LogicalOperator.SCAN)] == [
        "blue.nl2sql"
    ]
    assert router.operators_for(LogicalOperator.UNION) == []


def test_a_repeated_operator_id_is_refused() -> None:
    """An id names one implementation, so registering it twice is a wiring bug."""
    router = OperatorRouter()
    first, _ = recording_operator("blue.select", frozenset({LogicalOperator.FILTER}), [])
    second, _ = recording_operator("blue.select", frozenset({LogicalOperator.PROJECT}), [])
    router.register(first)

    with pytest.raises(OperatorRoutingError, match="blue.select"):
        router.register(second)


def test_resolving_an_id_nothing_registered_raises() -> None:
    """A cover records an operator id, and the runner turns it back into the operator."""
    router = OperatorRouter()
    select, _ = recording_operator("blue.select", frozenset({LogicalOperator.FILTER}), [])
    router.register(select)

    assert router.resolve("blue.select") is select
    with pytest.raises(OperatorRoutingError, match="blue.missing"):
        router.resolve("blue.missing")
