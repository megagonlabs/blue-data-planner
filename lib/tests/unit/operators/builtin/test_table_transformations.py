"""What each built-in transformation does to a table, and what it refuses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from blue_data_planner.operators.builtin.schemas import (
    Aggregation,
    FilterParameters,
    JoinKey,
    ProjectedColumn,
    SortDirection,
    SortKey,
)
from blue_data_planner.operators.builtin.table_transformations import (
    aggregate_rows,
    distinct_rows,
    expand_rows,
    filter_rows,
    join_tables,
    project_columns,
    sort_rows,
    union_tables,
)
from blue_data_planner.types import Data, RecoverableOperatorError


def a_table_of_restaurants() -> Data:
    """Four restaurants, one of them missing a rating."""
    return [
        {"id": 1, "name": "Ada", "state": "CA", "rating": 4.5, "tags": ["thai", "cheap"]},
        {"id": 2, "name": "Borges", "state": "NY", "rating": 3.0, "tags": ["thai"]},
        {"id": 3, "name": "Curie", "state": "CA", "rating": 4.0, "tags": []},
        {"id": 4, "name": "Ada", "state": "CA", "rating": None, "tags": ["sushi"]},
    ]


def a_predicate(payload: dict[str, Any]) -> Any:
    """Return the predicate a filter's parameters carry, built from its JSON form."""
    return FilterParameters.model_validate({"predicate": payload}).predicate


@pytest.mark.parametrize(
    ("payload", "expected_ids"),
    [
        ({"column": "state", "operator": "eq", "value": "CA"}, [1, 3, 4]),
        ({"column": "rating", "operator": "is_null"}, [4]),
        ({"column": "name", "operator": "contains", "value": "da"}, [1, 4]),
        ({"column": "name", "operator": "contains", "value": "DA"}, []),
        (
            {
                "or": [
                    {
                        "and": [
                            {"column": "state", "operator": "eq", "value": "CA"},
                            {"column": "rating", "operator": "gte", "value": 4.5},
                        ]
                    },
                    {"not": {"column": "state", "operator": "eq", "value": "CA"}},
                ]
            },
            [1, 2],
        ),
    ],
)
def test_a_filter_keeps_the_rows_its_predicate_accepts(
    payload: dict[str, Any], expected_ids: list[int]
) -> None:
    """A predicate selects rows, nested or not, and a missing value satisfies nothing but
    is_null."""
    kept = filter_rows(a_table_of_restaurants(), a_predicate(payload))

    assert [row["id"] for row in kept] == expected_ids


def test_a_projection_keeps_the_columns_it_names_under_the_names_it_asks_for() -> None:
    """A projection narrows each row and renames what it was told to rename."""
    projected = project_columns(
        a_table_of_restaurants(),
        [ProjectedColumn(column="name"), ProjectedColumn(column="rating", into="score")],
    )

    assert projected[0] == {"name": "Ada", "score": 4.5}


def test_an_expansion_writes_one_row_per_element_of_its_column() -> None:
    """A list column becomes one row per element, and an empty list produces none."""
    expanded = expand_rows(a_table_of_restaurants(), "tags", "tag", None)

    assert [(row["id"], row["tag"]) for row in expanded] == [
        (1, "thai"),
        (1, "cheap"),
        (2, "thai"),
        (4, "sushi"),
    ]


def test_an_expansion_splits_a_delimited_string_when_it_is_given_a_separator() -> None:
    """A string column expands on its separator, with the surrounding spaces dropped."""
    expanded = expand_rows([{"id": 1, "tags": "thai, cheap"}], "tags", "tag", ",")

    assert [row["tag"] for row in expanded] == ["thai", "cheap"]


def test_an_aggregation_writes_one_row_per_group() -> None:
    """Rows group by the named columns, and each aggregation lands under the name it asked for."""
    aggregated = aggregate_rows(
        a_table_of_restaurants(),
        ["state"],
        [
            Aggregation(function="count", into="restaurants"),
            Aggregation(function="avg", column="rating", into="mean_rating"),
        ],
    )

    assert aggregated == [
        {"state": "CA", "restaurants": 3, "mean_rating": 4.25},
        {"state": "NY", "restaurants": 1, "mean_rating": 3.0},
    ]


def test_distinct_keeps_the_first_row_of_each_repeated_key() -> None:
    """Repeated values collapse to their first row, and the input order survives."""
    distinct = distinct_rows(a_table_of_restaurants(), ["name"])

    assert [row["id"] for row in distinct] == [1, 2, 3]


def test_a_sort_orders_rows_by_each_key_in_turn() -> None:
    """A tie on the first key is broken by the second, each key applied in its own direction."""
    sorted_rows = sort_rows(
        a_table_of_restaurants(),
        [SortKey(column="state", direction="asc"), SortKey(column="rating", direction="desc")],
        None,
    )

    # CA groups before NY (the ascending first key); within CA, rating breaks the tie in
    # descending order, and id 4's missing rating still lands last in that group.
    assert [row["id"] for row in sorted_rows] == [1, 3, 4, 2]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_a_sort_puts_a_missing_value_last_regardless_of_direction(
    direction: SortDirection,
) -> None:
    """A row missing the sort key's value sorts last whichever direction was asked for."""
    sorted_rows = sort_rows(
        a_table_of_restaurants(), [SortKey(column="rating", direction=direction)], None
    )

    assert sorted_rows[-1]["id"] == 4


def test_a_sort_cuts_the_rows_to_the_limit_it_was_given() -> None:
    """A limit keeps the first rows of the order and drops the rest."""
    sorted_rows = sort_rows(
        a_table_of_restaurants(), [SortKey(column="rating", direction="desc")], 2
    )

    assert [row["id"] for row in sorted_rows] == [1, 3]


def test_a_join_matches_rows_on_their_keys_and_renames_a_column_that_collides() -> None:
    """Matched rows merge, and a name both tables carry is kept apart with a suffix."""
    joined = join_tables(
        [{"id": 1, "name": "Ada"}],
        [{"restaurant_id": 1, "name": "sushi night"}],
        [JoinKey(left_column="id", right_column="restaurant_id")],
        "inner",
    )

    assert joined == [{"id": 1, "name": "Ada", "restaurant_id": 1, "name_right": "sushi night"}]


def test_a_left_join_keeps_a_row_that_matched_nothing() -> None:
    """An unmatched left row survives a left join and gains none of the right columns."""
    joined = join_tables(
        [{"id": 1}, {"id": 2}],
        [{"id": 1, "visits": 3}],
        [JoinKey(left_column="id", right_column="id")],
        "left",
    )

    assert joined == [{"id": 1, "id_right": 1, "visits": 3}, {"id": 2}]


def test_a_union_concatenates_the_two_tables_in_order() -> None:
    """Both tables reach the result, the first table's rows first."""
    united = union_tables([{"id": 1}], [{"id": 2}])

    assert united == [{"id": 1}, {"id": 2}]


_TRANSFORMATIONS_NAMING_A_COLUMN: list[Callable[[Data], Data]] = [
    lambda table: filter_rows(
        table, a_predicate({"column": "city", "operator": "eq", "value": "LA"})
    ),
    lambda table: project_columns(table, [ProjectedColumn(column="city")]),
    lambda table: expand_rows(table, "city", "city", None),
    lambda table: aggregate_rows(table, ["city"], [Aggregation(function="count", into="n")]),
    lambda table: distinct_rows(table, ["city"]),
    lambda table: sort_rows(table, [SortKey(column="city")], None),
]


@pytest.mark.parametrize("transformation", _TRANSFORMATIONS_NAMING_A_COLUMN)
def test_a_transformation_refuses_a_table_that_lacks_the_column_it_names(
    transformation: Callable[[Data], Data],
) -> None:
    """A column no row carries is a refusal, not an empty result."""
    with pytest.raises(RecoverableOperatorError, match="city"):
        transformation(a_table_of_restaurants())


def test_a_comparison_between_values_that_cannot_be_ordered_refuses_the_table() -> None:
    """Ordering a number against a string is refused rather than answered."""
    with pytest.raises(RecoverableOperatorError):
        filter_rows(
            a_table_of_restaurants(),
            a_predicate({"column": "rating", "operator": "lt", "value": "4"}),
        )


def test_an_expansion_refuses_a_column_it_cannot_take_apart() -> None:
    """A value that is neither a list nor a string with a separator is refused."""
    with pytest.raises(RecoverableOperatorError, match="rating"):
        expand_rows(a_table_of_restaurants(), "rating", "rating", None)
