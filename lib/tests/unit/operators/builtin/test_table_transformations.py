"""What each built-in transformation does to a table, and what it refuses."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, Literal

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


def a_column(name: str) -> dict[str, Any]:
    """Return an operand reading one column of the row."""
    return {"column": name}


def a_literal(value: Any) -> dict[str, Any]:
    """Return an operand holding one literal value."""
    return {"value": value}


def a_column_aggregation(
    function: Literal["count", "sum", "avg", "min", "max"], column: str, into: str
) -> CountAggregation | ColumnAggregation:
    if function == "count":
        return CountAggregation(function=function, column=column, into=into)
    return ColumnAggregation(function=function, column=column, into=into)


def a_table_of_cities() -> Data:
    """Three cities with their school counts; Fresno is missing its virtual count."""
    return [
        {"city": "Oakland", "virtual_school_count": 2, "school_count": 2},
        {"city": "Reno", "virtual_school_count": 1, "school_count": 4},
        {"city": "Fresno", "virtual_school_count": None, "school_count": 3},
    ]


@pytest.mark.parametrize(
    ("payload", "expected_ids"),
    [
        ({"left": a_column("state"), "operator": "eq", "right": a_literal("CA")}, [1, 3, 4]),
        ({"operand": a_column("rating"), "operator": "is_null"}, [4]),
        ({"left": a_column("name"), "operator": "contains", "right": a_literal("da")}, [1, 4]),
        ({"left": a_column("name"), "operator": "contains", "right": a_literal("DA")}, []),
        pytest.param(
            {"left": a_column("name"), "operator": "re.search", "pattern": r"^(Ada|Curie)$"},
            [1, 3, 4],
            id="a_pattern_keeps_the_rows_whose_string_it_finds",
        ),
        pytest.param(
            {"left": a_literal(4.0), "operator": "lte", "right": a_column("rating")},
            [1, 3],
            id="a_literal_on_the_left",
        ),
        (
            {
                "or": [
                    {
                        "and": [
                            {"left": a_column("state"), "operator": "eq", "right": a_literal("CA")},
                            {
                                "left": a_column("rating"),
                                "operator": "gte",
                                "right": a_literal(4.5),
                            },
                        ]
                    },
                    {
                        "not": {
                            "left": a_column("state"),
                            "operator": "eq",
                            "right": a_literal("CA"),
                        }
                    },
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


@pytest.mark.parametrize(
    ("operator", "expected_cities"),
    [("eq", ["Oakland"]), ("lt", ["Reno"])],
)
def test_a_comparison_between_two_columns_keeps_the_rows_where_it_holds(
    operator: str, expected_cities: list[str]
) -> None:
    """Two columns of one row are compared with each other, and a row missing either value
    matches nothing."""
    kept = filter_rows(
        a_table_of_cities(),
        a_predicate(
            {
                "left": a_column("virtual_school_count"),
                "operator": operator,
                "right": a_column("school_count"),
            }
        ),
    )

    assert [row["city"] for row in kept] == expected_cities


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


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            a_table_of_restaurants(),
            [
                {"state": "CA", "restaurants": 3, "mean_rating": 4.25},
                {"state": "NY", "restaurants": 1, "mean_rating": 3.0},
            ],
        ),
        (
            [
                {"state": "CA", "rating": Decimal("4.5")},
                {"state": "CA", "rating": Decimal("4.0")},
            ],
            [{"state": "CA", "restaurants": 2, "mean_rating": 4.25}],
        ),
    ],
)
def test_an_aggregation_writes_one_row_per_group(table: Data, expected: Data) -> None:
    """Rows group by the named columns, and each aggregation lands under the name it asked for;
    a Decimal column averages to a float like any other number."""
    aggregated = aggregate_rows(
        table,
        ["state"],
        [
            CountAggregation(function="count", into="restaurants"),
            ColumnAggregation(function="avg", column="rating", into="mean_rating"),
        ],
    )

    assert aggregated == expected


@pytest.mark.parametrize("missing", [None, float("nan"), Decimal("NaN")])
@pytest.mark.parametrize("function", ["sum", "avg", "min", "max"])
def test_a_column_aggregation_over_a_group_with_no_values_is_none(
    function: Literal["sum", "avg", "min", "max"], missing: Any
) -> None:
    """A group whose rows all miss the aggregated column, or hold only NaN there, gets None, as
    SQL gives NULL, rather than a value no row recorded."""
    aggregated = aggregate_rows(
        [{"state": "CA", "rating": missing}, {"state": "CA", "rating": missing}],
        ["state"],
        [ColumnAggregation(function=function, column="rating", into="result")],
    )

    assert aggregated == [{"state": "CA", "result": None}]


@pytest.mark.parametrize("nan", [float("nan"), Decimal("NaN")])
@pytest.mark.parametrize(
    ("function", "expected"), [("count", 2), ("sum", 6), ("avg", 3), ("min", 1), ("max", 5)]
)
def test_a_column_aggregation_skips_a_nan_as_it_skips_a_missing_value(
    nan: Any, function: Literal["count", "sum", "avg", "min", "max"], expected: float
) -> None:
    """A NaN in the aggregated column leaves the result to the other values, wherever the NaN
    row falls in the group."""
    aggregated = aggregate_rows(
        [{"n": nan}, {"n": 5}, {"n": 1}],
        [],
        [a_column_aggregation(function, "n", "result")],
    )

    assert aggregated == [{"result": expected}]


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


@pytest.mark.parametrize("nan", [float("nan"), Decimal("NaN")])
@pytest.mark.parametrize(("direction", "expected"), [("asc", [1, 2, 3]), ("desc", [3, 2, 1])])
def test_a_sort_puts_a_nan_last_and_orders_the_other_rows_regardless_of_direction(
    nan: Any, direction: SortDirection, expected: list[int]
) -> None:
    """A NaN sort key lands last, as a missing value does, and the other rows keep their order."""
    sorted_rows = sort_rows(
        [{"n": 2}, {"n": nan}, {"n": 3}, {"n": 1}], [SortKey(column="n", direction=direction)], None
    )

    assert [row["n"] for row in sorted_rows[:-1]] == expected
    assert sorted_rows[-1]["n"] is nan


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
        table, a_predicate({"left": a_column("city"), "operator": "eq", "right": a_literal("LA")})
    ),
    lambda table: filter_rows(
        table, a_predicate({"left": a_literal("LA"), "operator": "eq", "right": a_column("city")})
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
        # Alternation under repetition backtracks exponentially on a run of a's ending in a miss.
        ([{"name": "a" * 40 + "!"}], r"(a|aa)+$", "time limit"),
    ],
    ids=["does_not_compile", "non_string_column", "past_the_time_limit"],
)
def test_a_pattern_comparison_that_cannot_run_refuses_the_table(
    table: Data, predicate_value: str, match: str
) -> None:
    """A pattern that does not compile, one run against a non-string column, and one whose search
    runs past the time limit are each refused, so the parameter repair can rewrite the pattern."""
    with pytest.raises(RecoverableOperatorError, match=match):
        filter_rows(
            table,
            a_predicate(
                {"left": a_column("name"), "operator": "re.search", "pattern": predicate_value}
            ),
        )


def test_an_expansion_refuses_a_column_it_cannot_take_apart() -> None:
    """A value that is neither a list nor a string with a separator is refused."""
    with pytest.raises(RecoverableOperatorError, match="rating"):
        expand_rows(a_table_of_restaurants(), "rating", "rating", None)


@pytest.mark.parametrize(
    ("table", "predicate"),
    [
        (
            [{"code": "1234"}],
            {"left": a_column("code"), "operator": "eq", "right": a_literal(1234)},
        ),
        (
            [{"code": "1234"}],
            {"left": a_column("code"), "operator": "ne", "right": a_literal(1234)},
        ),
        (
            [{"code": 1234}],
            {"left": a_column("code"), "operator": "eq", "right": a_literal("1234")},
        ),
        ([{"code": True}], {"left": a_column("code"), "operator": "eq", "right": a_literal(1)}),
        ([{"code": 1}], {"left": a_column("code"), "operator": "eq", "right": a_literal(True)}),
        (
            [{"code": True}],
            {"left": a_column("code"), "operator": "eq", "right": a_literal("true")},
        ),
        ([{"code": 4.5}], {"left": a_column("code"), "operator": "lt", "right": a_literal("4")}),
        (
            [{"code": 4.5}],
            {"left": a_column("code"), "operator": "contains", "right": a_literal("4")},
        ),
        (
            [{"code": "a"}],
            {"left": a_column("code"), "operator": "contains", "right": a_literal(4)},
        ),
        ([{"code": "1234"}], {"left": a_column("code"), "operator": "in", "values": [1234]}),
        (
            [{"code": "1234", "other": 1234}],
            {"left": a_column("code"), "operator": "gte", "right": a_column("other")},
        ),
    ],
)
def test_a_comparison_whose_sides_share_no_category_refuses_the_table(
    table: Data, predicate: dict[str, Any]
) -> None:
    """A string, a number, and a bool are separate categories, so a comparison whose two sides
    cross them over the whole table is refused before any row is filtered, rather than
    answering that no rows match."""
    with pytest.raises(RecoverableOperatorError, match="code"):
        filter_rows(table, a_predicate(predicate))


_UNORDERABLE = {"left": a_column("code"), "operator": "lt", "right": a_column("code")}


@pytest.mark.parametrize(
    "predicate",
    [
        _UNORDERABLE,
        {"and": [{"left": a_literal(1), "operator": "eq", "right": a_literal(2)}, _UNORDERABLE]},
        {"or": [{"left": a_literal(1), "operator": "eq", "right": a_literal(1)}, _UNORDERABLE]},
    ],
    ids=["bare", "after_a_false_and_condition", "after_a_true_or_condition"],
)
def test_ordering_two_values_that_have_no_order_refuses_the_table_wherever_it_is_written(
    predicate: dict[str, Any],
) -> None:
    """Two dicts are one category but cannot be ordered, so an ordering comparison over them
    is refused for the parameter repair to rewrite, even when an earlier condition already
    decides the and/or."""
    with pytest.raises(RecoverableOperatorError, match="cannot be ordered"):
        filter_rows([{"code": {"a": 1}}], a_predicate(predicate))


def test_an_equality_comparison_between_two_numbers_answers_no_rows_rather_than_refusing() -> None:
    """An int compared against a float, both numbers, is an ordinary unequal comparison, not
    the category mismatch a string compared against a number is."""
    kept = filter_rows(
        [{"rating": 182.88}],
        a_predicate({"left": a_column("rating"), "operator": "eq", "right": a_literal(182)}),
    )

    assert kept == []


@pytest.mark.parametrize(
    ("table", "predicate", "expected"),
    [
        (
            [{"v": 5}, {"v": "5"}],
            {"left": a_column("v"), "operator": "eq", "right": a_literal(5)},
            [5],
        ),
        (
            [{"v": "N/A"}, {"v": 5}, {"v": 9}],
            {"left": a_column("v"), "operator": "lt", "right": a_literal(6)},
            [5],
        ),
        (
            [{"v": "abc"}, {"v": 5}],
            {"left": a_column("v"), "operator": "contains", "right": a_literal("b")},
            ["abc"],
        ),
        (
            [{"v": 5}, {"v": "abc"}],
            {"left": a_column("v"), "operator": "re.search", "pattern": "^a"},
            ["abc"],
        ),
        (
            [{"v": 5}, {"v": "5"}],
            {"left": a_column("v"), "operator": "in", "values": [5]},
            [5],
        ),
        (
            [{"v": Decimal("4.2")}, {"v": "N/A"}, {"v": Decimal("5.0")}],
            {"left": a_column("v"), "operator": "lt", "right": a_literal(4.5)},
            [Decimal("4.2")],
        ),
        (
            [{"v": 1, "w": 1}, {"v": "x", "w": 1}],
            {"left": a_column("v"), "operator": "eq", "right": a_column("w")},
            [1],
        ),
    ],
)
def test_a_stray_cell_of_another_category_matches_nothing_and_the_rest_are_answered(
    table: Data, predicate: dict[str, Any], expected: list[Any]
) -> None:
    """A column of one category except for a stray cell still answers every comparison, since
    some row shares a category with the other side; the stray row does not match."""
    kept = filter_rows(table, a_predicate(predicate))

    assert [row["v"] for row in kept] == expected


def a_comparison_of_v(operator: str, **operand: Any) -> dict[str, Any]:
    """Return a comparison of column v, given the right-hand side its operator needs."""
    return {"left": a_column("v"), "operator": operator, **operand}


@pytest.mark.parametrize(
    ("column", "comparison", "expected"),
    [
        pytest.param(
            ["N/A", 5, 9], a_comparison_of_v("eq", right=a_literal(5)), [9], id="eq_over_a_stray"
        ),
        pytest.param(
            [None, 5, 9], a_comparison_of_v("eq", right=a_literal(5)), [9], id="eq_over_a_missing"
        ),
        pytest.param(
            ["N/A", 5, 9], a_comparison_of_v("lt", right=a_literal(6)), [9], id="lt_over_a_stray"
        ),
        pytest.param(
            [None, 5, 9], a_comparison_of_v("lt", right=a_literal(6)), [9], id="lt_over_a_missing"
        ),
        pytest.param(["N/A", 5, 9], a_comparison_of_v("in", values=[5]), [9], id="in_over_a_stray"),
        pytest.param(
            [None, 5, 9], a_comparison_of_v("in", values=[5]), [9], id="in_over_a_missing"
        ),
        pytest.param(
            [None, 5], a_comparison_of_v("in", values=[]), [5], id="in_an_empty_list_over_a_missing"
        ),
        pytest.param(
            [5, "abc", "xyz"],
            a_comparison_of_v("contains", right=a_literal("b")),
            ["xyz"],
            id="contains_over_a_stray",
        ),
        pytest.param(
            [None, "abc", "xyz"],
            a_comparison_of_v("contains", right=a_literal("b")),
            ["xyz"],
            id="contains_over_a_missing",
        ),
        pytest.param(
            [5, "abc", "xyz"],
            a_comparison_of_v("re.search", pattern="^a"),
            ["xyz"],
            id="re_search_over_a_stray",
        ),
        pytest.param(
            [None, "abc", "xyz"],
            a_comparison_of_v("re.search", pattern="^a"),
            ["xyz"],
            id="re_search_over_a_missing",
        ),
        pytest.param(
            [float("nan"), 5.0, 9.0],
            a_comparison_of_v("lt", right=a_literal(6)),
            [9.0],  # what gte 6 keeps
            id="lt_over_a_float_nan_keeps_what_gte_keeps",
        ),
        pytest.param(
            [Decimal("NaN"), Decimal("5"), Decimal("9")],
            a_comparison_of_v("lt", right=a_literal(6)),
            [Decimal("9")],  # what gte 6 keeps
            id="lt_over_a_decimal_nan_keeps_what_gte_keeps",
        ),
    ],
)
def test_a_negated_comparison_keeps_only_the_rows_where_it_is_false_never_where_unknown(
    column: list[Any], comparison: dict[str, Any], expected: list[Any]
) -> None:
    """A stray or missing cell, or a NaN under an ordering operator, makes a comparison unknown,
    and not of unknown is unknown, so only the rows where the comparison is false are kept: not
    lt keeps what gte keeps."""
    kept = filter_rows([{"v": value} for value in column], a_predicate({"not": comparison}))

    assert [row["v"] for row in kept] == expected


def test_a_not_equal_comparison_and_a_negated_equal_comparison_keep_the_same_rows() -> None:
    """Over a stray cell and a missing value, ne and not eq agree, as they do in SQL."""
    table: Data = [{"v": "N/A"}, {"v": None}, {"v": 5}, {"v": 9}]
    comparison = a_comparison_of_v("eq", right=a_literal(5))

    negated = filter_rows(table, a_predicate({"not": comparison}))
    not_equal = filter_rows(table, a_predicate({**comparison, "operator": "ne"}))

    assert negated == not_equal == [{"v": 9}]


def an_unknown() -> dict[str, Any]:
    """Return a comparison that is unknown for the stray row below, whose u is a string."""
    return {"left": a_column("u"), "operator": "eq", "right": a_literal(5)}


def a_true() -> dict[str, Any]:
    """Return a comparison that is true for the rows below."""
    return {"left": a_column("t"), "operator": "eq", "right": a_literal(1)}


def a_false() -> dict[str, Any]:
    """Return a comparison that is false for the rows below."""
    return {"left": a_column("t"), "operator": "eq", "right": a_literal(2)}


@pytest.mark.parametrize(
    ("predicate", "is_kept"),
    [
        pytest.param({"or": [an_unknown(), a_true()]}, True, id="or_of_unknown_and_true"),
        pytest.param({"or": [an_unknown(), a_false()]}, False, id="or_of_unknown_and_false"),
        pytest.param({"and": [an_unknown(), a_true()]}, False, id="and_of_unknown_and_true"),
        pytest.param({"and": [an_unknown(), a_false()]}, False, id="and_of_unknown_and_false"),
        pytest.param(
            {"not": {"and": [an_unknown(), a_false()]}}, True, id="not_of_and_of_unknown_and_false"
        ),
        pytest.param(
            {"not": {"or": [an_unknown(), a_false()]}}, False, id="not_of_or_of_unknown_and_false"
        ),
        pytest.param(
            {"not": {"and": [an_unknown(), a_true()]}}, False, id="not_of_and_of_unknown_and_true"
        ),
        pytest.param(
            {"not": {"or": [an_unknown(), a_true()]}}, False, id="not_of_or_of_unknown_and_true"
        ),
    ],
)
def test_and_and_or_combine_an_unknown_comparison_as_sql_does(
    predicate: dict[str, Any], is_kept: bool
) -> None:
    """And is false when any condition is false and unknown otherwise unless all are true; or is
    true when any is true; only a true predicate keeps the row."""
    stray = {"u": "N/A", "t": 1}
    # A second row with a number in u keeps the whole-table check from refusing the comparison.
    table: Data = [stray, {"u": 6, "t": 1}]

    kept = filter_rows(table, a_predicate(predicate))

    assert (stray in kept) is is_kept


def a_column_of_decimals() -> Data:
    """Four rows of a SQL NUMERIC column, one of them NaN."""
    return [
        {"v": Decimal("4.20")},
        {"v": Decimal("0.10")},
        {"v": Decimal("5.00")},
        {"v": Decimal("NaN")},
    ]


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        (
            {"left": a_column("v"), "operator": "eq", "right": a_literal(4.2)},
            [Decimal("4.20")],
        ),
        (
            {"left": a_column("v"), "operator": "in", "values": [4.2, 0.1]},
            [Decimal("4.20"), Decimal("0.10")],
        ),
        (
            {"left": a_column("v"), "operator": "gte", "right": a_literal(4.2)},
            [Decimal("4.20"), Decimal("5.00")],
        ),
        (
            {"left": a_column("v"), "operator": "lt", "right": a_literal(4.5)},
            [Decimal("4.20"), Decimal("0.10")],
        ),
    ],
)
def test_a_decimal_column_is_compared_exactly_against_a_float_literal_written_as_text(
    predicate: dict[str, Any], expected: list[Any]
) -> None:
    """A float literal meets a Decimal cell as the decimal text it is written with, so 4.20 equals
    4.2, and a Decimal NaN matches nothing, as a float NaN does."""
    kept = filter_rows(a_column_of_decimals(), a_predicate(predicate))

    assert [row["v"] for row in kept] == expected


@pytest.mark.parametrize(
    ("table", "predicate", "expected"),
    [
        pytest.param(
            [{"v": Decimal(2**53 + 1)}, {"v": Decimal(2**53)}],
            a_comparison_of_v("eq", right=a_literal(2**53 + 1)),
            [Decimal(2**53 + 1)],
            id="a_whole_number_above_2_to_the_53_equals_the_same_int",
        ),
        pytest.param(
            [{"v": Decimal(2**53 + 1)}, {"v": Decimal(2**53)}],
            a_comparison_of_v("in", values=[2**53 + 1]),
            [Decimal(2**53 + 1)],
            id="a_whole_number_above_2_to_the_53_is_in_a_list_holding_the_same_int",
        ),
        pytest.param(
            [
                {"v": Decimal("1.00000000000000000002"), "w": Decimal("1.00000000000000000001")},
                {"v": Decimal("1.00000000000000000001"), "w": Decimal("1.00000000000000000001")},
            ],
            {"left": a_column("v"), "operator": "gt", "right": a_column("w")},
            [Decimal("1.00000000000000000002")],
            id="two_decimal_columns_differing_beyond_float_precision",
        ),
        pytest.param(
            [{"v": Decimal("sNaN")}, {"v": Decimal("1")}],
            a_comparison_of_v("lt", right=a_literal(2.0)),
            [Decimal("1")],
            id="a_signaling_nan_matches_nothing_and_raises_nothing",
        ),
        pytest.param(
            [{"v": Decimal("sNaN")}, {"v": Decimal("1")}],
            a_comparison_of_v("eq", right=a_literal(1)),
            [Decimal("1")],
            id="a_signaling_nan_matches_no_int",
        ),
    ],
)
def test_a_decimal_comparison_is_exact_and_never_raises_on_a_nan(
    table: Data, predicate: dict[str, Any], expected: list[Any]
) -> None:
    """Decimals compare exactly with ints, floats written as text, and each other; a NaN cell
    of either kind matches nothing under eq or an ordering operator."""
    kept = filter_rows(table, a_predicate(predicate))

    assert [row["v"] for row in kept] == expected


@pytest.mark.parametrize(
    "predicate",
    [
        {"left": a_column("v"), "operator": "eq", "right": a_literal(5)},
        {"left": a_column("v"), "operator": "contains", "right": a_literal("a")},
        {"left": a_column("v"), "operator": "re.search", "pattern": "^a"},
        {"left": a_column("v"), "operator": "in", "values": [5]},
    ],
)
def test_a_column_missing_from_every_row_matches_nothing_rather_than_refusing(
    predicate: dict[str, Any],
) -> None:
    """A column whose every cell is missing gives the check before filtering nothing to object
    to, so the comparison is answered, and no row matches."""
    kept = filter_rows([{"v": None}, {"v": None}], a_predicate(predicate))

    assert kept == []


def test_an_in_comparison_matches_a_bool_column_against_a_bool_option() -> None:
    """A bool column checked with 'in' against a list holding that same bool matches, the way
    an ordinary equality check would, rather than being refused as a category mismatch."""
    kept = filter_rows(
        [{"active": True}],
        a_predicate({"left": a_column("active"), "operator": "in", "values": [True]}),
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
