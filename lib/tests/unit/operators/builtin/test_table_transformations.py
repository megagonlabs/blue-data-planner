"""What each built-in transformation does to a table, and what it refuses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from blue_data_planner.operators.builtin.schemas import (
    ColumnAggregation,
    CountAggregation,
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
        pytest.param(
            {"column": "name", "operator": "re.search", "value": r"^(Ada|Curie)$"},
            [1, 3, 4],
            id="a_pattern_keeps_the_rows_whose_string_it_finds",
        ),
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


def test_an_expansion_contributes_no_rows_for_a_missing_value() -> None:
    """A None value expands to zero rows, like SQL's UNNEST(NULL), rather than refusing
    the whole table."""
    expanded = expand_rows(
        [{"id": 1, "tags": None}, {"id": 2, "tags": ["sushi"]}], "tags", "tag", None
    )

    assert [(row["id"], row["tag"]) for row in expanded] == [(2, "sushi")]


def test_an_aggregation_writes_one_row_per_group() -> None:
    """Rows group by the named columns, and each aggregation lands under the name it asked for."""
    aggregated = aggregate_rows(
        a_table_of_restaurants(),
        ["state"],
        [
            CountAggregation(function="count", into="restaurants"),
            ColumnAggregation(function="avg", column="rating", into="mean_rating"),
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


def test_a_join_matches_rows_on_their_keys_and_keeps_both_copies_of_a_shared_column() -> None:
    """Matched rows merge, and a non-key name both tables carry is suffixed on each side."""
    joined = join_tables(
        [{"id": 1, "name": "Ada"}],
        [{"restaurant_id": 1, "name": "sushi night"}],
        [JoinKey(left_column="id", right_column="restaurant_id")],
        "inner",
    )

    assert joined == [
        {"id": 1, "name_left": "Ada", "restaurant_id": 1, "name_right": "sushi night"}
    ]


def test_a_join_keeps_one_copy_of_a_key_both_tables_name_alike() -> None:
    """A key both tables name alike holds equal values, so it reaches the result once."""
    joined = join_tables(
        [{"raceId": 7, "year": 2014}],
        [{"raceId": 7, "points": 25}],
        [JoinKey(left_column="raceId", right_column="raceId")],
        "inner",
    )

    assert joined == [{"raceId": 7, "year": 2014, "points": 25}]


def test_a_join_over_already_suffixed_columns_takes_the_first_free_name() -> None:
    """A suffixed name an earlier join produced is not reused, and the join still runs."""
    joined = join_tables(
        [{"id": 1, "name": "Ada", "name_left": "x", "name_right": "y"}],
        [{"id": 1, "name": "sushi night"}],
        [JoinKey(left_column="id", right_column="id")],
        "inner",
    )

    assert joined == [
        {
            "id": 1,
            "name_left_2": "Ada",
            "name_left": "x",
            "name_right": "y",
            "name_right_2": "sushi night",
        }
    ]


def test_a_left_join_keeps_a_row_that_matched_nothing() -> None:
    """An unmatched left row survives a left join and gains none of the right columns."""
    joined = join_tables(
        [{"id": 1}, {"id": 2}],
        [{"id": 1, "visits": 3}],
        [JoinKey(left_column="id", right_column="id")],
        "left",
    )

    assert joined == [{"id": 1, "visits": 3}, {"id": 2}]


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
    lambda table: aggregate_rows(table, ["city"], [CountAggregation(function="count", into="n")]),
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


@pytest.mark.parametrize(
    ("table", "predicate_value", "match"),
    [
        ([{"name": None}], "(unclosed", "does not compile"),
        ([{"name": 1234}], r"^Adam\b", "name"),
    ],
)
def test_a_pattern_comparison_that_cannot_run_refuses_the_table(
    table: Data, predicate_value: str, match: str
) -> None:
    """A pattern that does not compile is refused even when no row would reach it, and a pattern
    run against a non-string column is refused, so the parameter repair can rewrite either."""
    with pytest.raises(RecoverableOperatorError, match=match):
        filter_rows(
            table,
            a_predicate({"column": "name", "operator": "re.search", "value": predicate_value}),
        )


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


@pytest.mark.parametrize(
    ("operator", "column_value", "predicate_value"),
    [
        ("eq", "1234", 1234),
        ("ne", "1234", 1234),
        ("in", "1234", [1234]),
        ("eq", 1234, "1234"),
        ("eq", True, 1),
        ("eq", 1, True),
        ("eq", True, "true"),
    ],
)
def test_a_cross_category_comparison_refuses_the_table(
    operator: str, column_value: Any, predicate_value: Any
) -> None:
    """A string, a number, and a bool are separate categories, so eq, ne, and in refuse a
    comparison the whole column crosses them on, rather than silently answering that no rows
    match."""
    with pytest.raises(RecoverableOperatorError, match="code"):
        filter_rows(
            [{"code": column_value}],
            a_predicate({"column": "code", "operator": operator, "value": predicate_value}),
        )


def test_an_equality_comparison_between_two_numbers_answers_no_rows_rather_than_refusing() -> None:
    """An int compared against a float, both numbers, is an ordinary unequal comparison, not
    the category mismatch a string compared against a number is."""
    kept = filter_rows(
        [{"rating": 182.88}], a_predicate({"column": "rating", "operator": "eq", "value": 182})
    )

    assert kept == []


@pytest.mark.parametrize(
    "table",
    [[{"id": 5}, {"id": "5"}], [{"id": "N/A"}, {"id": 5}]],
)
def test_a_stray_dirty_cell_does_not_abort_a_comparison_the_column_otherwise_answers(
    table: Data,
) -> None:
    """A column that is a number except for one stray string still answers eq against a
    number literal, since some row shares its category; the refusal is for a column with no
    row left to compare against, not for one dirty cell in an otherwise numeric column."""
    kept = filter_rows(table, a_predicate({"column": "id", "operator": "eq", "value": 5}))

    assert [row["id"] for row in kept] == [5]


def test_an_in_comparison_matches_a_bool_column_against_a_bool_option() -> None:
    """A bool column checked with 'in' against a list holding that same bool matches, the way
    an ordinary equality check would, rather than being refused as a category mismatch."""
    kept = filter_rows(
        [{"active": True}], a_predicate({"column": "active", "operator": "in", "value": [True]})
    )

    assert kept == [{"active": True}]


def _sorted_by_n(table: Data) -> Data:
    return sort_rows(table, [SortKey(column="n", direction="desc")], None)


def _max_of_n(table: Data) -> Data:
    return aggregate_rows(table, [], [ColumnAggregation(function="max", column="n", into="top")])


@pytest.mark.parametrize("transformation", [_sorted_by_n, _max_of_n])
def test_ordering_a_column_of_numeric_strings_is_refused_rather_than_read_as_text(
    transformation: Callable[[Data], Data],
) -> None:
    """A column whose values are all strings that parse as numbers, of differing lengths,
    cannot be ordered lexicographically, whether sort_rows or an aggregation's min/max does
    the ordering."""
    with pytest.raises(RecoverableOperatorError, match="numeric strings"):
        transformation([{"n": "9"}, {"n": "10"}, {"n": "100"}])


@pytest.mark.parametrize(
    ("transformation", "expected"),
    [(_sorted_by_n, [{"n": "42"}]), (_max_of_n, [{"top": "42"}])],
)
def test_ordering_a_single_value_is_a_no_op_even_if_it_looks_numeric(
    transformation: Callable[[Data], Data], expected: Data
) -> None:
    """A column with one present value has nothing to misorder, so a lone numeric string is
    not refused, and the one row passes through unchanged."""
    assert transformation([{"n": "42"}]) == expected


def test_sorting_equal_width_digit_strings_is_not_refused_because_order_coincides() -> None:
    """Zip codes or four-digit years, all the same length and made of digits alone, sort the
    same way whether compared as text or as numbers, so the numeric-string refusal would only
    cost a repair loop and does not fire."""
    sorted_rows = sort_rows([{"zip": "94104"}, {"zip": "94103"}], [SortKey(column="zip")], None)

    assert [row["zip"] for row in sorted_rows] == ["94103", "94104"]


@pytest.mark.parametrize(
    "values",
    [["10.5", "9.25"], ["-9", "-8"], ["+5", "-5"], ["1e3", "999"]],
)
def test_sorting_equal_width_numeric_strings_with_a_sign_or_point_is_still_refused(
    values: list[str],
) -> None:
    """Equal width alone does not make lexicographic and numeric order agree: a decimal point,
    a sign, or exponent notation breaks the digit-by-digit correspondence plain zip codes or
    years have, so the refusal still fires."""
    with pytest.raises(RecoverableOperatorError, match="numeric strings"):
        sort_rows([{"n": value} for value in values], [SortKey(column="n")], None)


def test_sorting_a_column_mixing_numeric_and_non_numeric_strings_sorts_as_text() -> None:
    """A column where not every value parses as a number is plain text, even when some values
    happen to look numeric, so it sorts lexicographically rather than being refused."""
    sorted_rows = sort_rows(
        [{"tag": "banana"}, {"tag": "9"}, {"tag": "apple"}], [SortKey(column="tag")], None
    )

    assert [row["tag"] for row in sorted_rows] == ["9", "apple", "banana"]


def test_a_groups_max_still_refuses_a_group_whose_own_values_are_all_numeric_strings() -> None:
    """A group-by max is judged per group, not over the whole column: a sentinel like "N/A" in
    a different group must not disable the refusal for a group whose own values are all
    numeric strings, since that group's max would otherwise read lexicographically ("9" over
    the numeric maximum "10") with nothing to say so."""
    with pytest.raises(RecoverableOperatorError, match="numeric strings"):
        aggregate_rows(
            [{"g": "a", "n": "9"}, {"g": "a", "n": "10"}, {"g": "b", "n": "N/A"}],
            ["g"],
            [ColumnAggregation(function="max", column="n", into="top")],
        )
