"""What the built-in suite offers the router, and how each operator answers a bundle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.types import (
    DataBundle,
    LogicalOperator,
    PhysicalOperator,
    RecoverableOperatorError,
)

# Every operator node kind, each answered by one entry of this suite.
DETERMINISTIC_KINDS = [
    LogicalOperator.FILTER,
    LogicalOperator.PROJECT,
    LogicalOperator.EXPAND,
    LogicalOperator.AGGREGATE,
    LogicalOperator.DISTINCT,
    LogicalOperator.SORT_SUPERLATIVE,
    LogicalOperator.JOIN,
    LogicalOperator.UNION,
    LogicalOperator.SCAN,
]

TWO_TABLE_IDS = ["builtin.join", "builtin.union"]


def the_built_in_operators() -> dict[str, PhysicalOperator]:
    """Return every built-in operator by id; only builtin.scan's call reads either argument."""
    operators = builtin_operators(cast(Any, None), cast(Any, None))
    return {operator.operator_id: operator for operator in operators}


def a_bundle_of_restaurants() -> DataBundle:
    """One table of three restaurants, two of them in California."""
    return [
        [
            {"id": 1, "name": "Ada", "state": "CA", "rating": 4.5},
            {"id": 2, "name": "Borges", "state": "NY", "rating": 3.0},
            {"id": 3, "name": "Curie", "state": "CA", "rating": 4.0},
        ]
    ]


def a_bundle_of_taggable_restaurants() -> DataBundle:
    """One table of two restaurants, each carrying a list column to expand."""
    return [[{"id": 1, "tags": ["thai", "cheap"]}, {"id": 2, "tags": ["sushi"]}]]


def a_bundle_of_two_id_tables() -> DataBundle:
    """Two one-row tables, each holding a single id, for a two-table operator."""
    return [[{"id": 1}], [{"id": 2}]]


def test_every_deterministic_node_kind_has_a_built_in_operator() -> None:
    """A router holding the suite answers every operator node kind."""
    router = OperatorRouter()
    for operator in builtin_operators(cast(Any, None), cast(Any, None)):
        router.register(operator)

    for kind in DETERMINISTIC_KINDS:
        assert [operator.operator_id for operator in router.operators_for(kind)]


def test_every_bundle_only_operator_declares_that_it_reads_no_source() -> None:
    """Every operator but builtin.scan works on the bundle alone and says what it cannot do."""
    for operator_id, operator in the_built_in_operators().items():
        if operator_id != "builtin.scan":
            assert operator.source_kinds is None
            assert operator.accepts_bound_inputs is False
        assert operator.limitations


@pytest.mark.parametrize(
    ("operator_id", "parameters", "bundle_factory", "column", "expected"),
    [
        (
            "builtin.filter",
            {"predicate": {"column": "state", "operator": "eq", "value": "CA"}},
            a_bundle_of_restaurants,
            "id",
            [1, 3],
        ),
        (
            "builtin.project",
            {"columns": [{"column": "id"}, {"column": "state"}]},
            a_bundle_of_restaurants,
            "id",
            [1, 2, 3],
        ),
        (
            "builtin.expand",
            {"column": "tags", "into": "tag"},
            a_bundle_of_taggable_restaurants,
            "tag",
            ["thai", "cheap", "sushi"],
        ),
        (
            "builtin.aggregate",
            {"group_by": ["state"], "aggregations": [{"function": "count", "into": "n"}]},
            a_bundle_of_restaurants,
            "n",
            # CA's two rows come first in the input, so its group is written first.
            [2, 1],
        ),
        ("builtin.distinct", {"columns": ["state"]}, a_bundle_of_restaurants, "id", [1, 2]),
        (
            "builtin.distinct",
            {},
            a_bundle_of_restaurants,
            "id",
            # No column named: every value distinguishes a row, and every row's full.
            [1, 2, 3],
        ),
        (
            "builtin.sort_superlative",
            {"keys": [{"column": "rating", "direction": "desc"}], "limit": 2},
            a_bundle_of_restaurants,
            "id",
            [1, 3],
        ),
        ("builtin.union", {}, a_bundle_of_two_id_tables, "id", [1, 2]),
    ],
)
def test_an_operator_transforms_its_bundle_as_its_parameters_say(
    operator_id: str,
    parameters: dict[str, Any],
    bundle_factory: Callable[[], DataBundle],
    column: str,
    expected: list[Any],
) -> None:
    """An operator runs its transformation over the table(s) its bundle carries."""
    output = the_built_in_operators()[operator_id].call(bundle_factory(), parameters)

    assert [row[column] for row in output.data[0]] == expected
    # A deterministic operation has nothing to say about how it ran.
    assert output.report is None


def test_a_two_table_operator_combines_the_tables_its_bundle_carries() -> None:
    """A join reads both tables of its bundle and matches them on the keys it was given."""
    bundle: DataBundle = [[{"id": 1, "name": "Ada"}], [{"restaurant_id": 1, "visits": 3}]]

    output = the_built_in_operators()["builtin.join"].call(
        bundle, {"on": [{"left_column": "id", "right_column": "restaurant_id"}]}
    )

    assert output.data == [[{"id": 1, "name": "Ada", "restaurant_id": 1, "visits": 3}]]


@pytest.mark.parametrize(
    ("operator_id", "expected_message"),
    [("builtin.filter", "transforms one table"), ("builtin.join", "combines two tables")],
)
def test_an_operator_refuses_a_bundle_holding_the_wrong_number_of_tables(
    operator_id: str, expected_message: str
) -> None:
    """An operator handed a bundle it cannot serve refuses it instead of guessing."""
    wrong_count: DataBundle = [] if operator_id in TWO_TABLE_IDS else [[], []]
    parameters: dict[str, Any] = (
        {"on": [{"left_column": "id", "right_column": "id"}]}
        if operator_id in TWO_TABLE_IDS
        else {"predicate": {"column": "state", "operator": "is_null"}}
    )

    with pytest.raises(RecoverableOperatorError, match=expected_message):
        the_built_in_operators()[operator_id].call(wrong_count, parameters)


def test_an_operator_refuses_parameters_its_schema_does_not_take() -> None:
    """Parameters the schema rejects come back as a refusal a fragment runner can answer."""
    with pytest.raises(RecoverableOperatorError, match="parameters"):
        the_built_in_operators()["builtin.filter"].call(
            a_bundle_of_restaurants(), {"predicate": {"column": "state"}}
        )
