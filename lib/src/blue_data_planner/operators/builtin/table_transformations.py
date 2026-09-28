"""The transformations the built-in operators run on the tables a bundle carries."""

from __future__ import annotations

import re
from collections.abc import Callable
from operator import ge, gt, le, lt
from typing import Any, cast

from blue_data_planner.operators.builtin.schemas import (
    Aggregation,
    AndPredicate,
    Comparison,
    InComparison,
    JoinKey,
    JoinKind,
    NotPredicate,
    NullComparison,
    OrPredicate,
    PatternComparison,
    Predicate,
    ProjectedColumn,
    SortKey,
)
from blue_data_planner.types import Data, Record, RecoverableOperatorError

_ORDERINGS: dict[str, Callable[[Any, Any], bool]] = {"lt": lt, "lte": le, "gt": gt, "gte": ge}


def filter_rows(table: Data, predicate: Predicate) -> Data:
    """Return the rows the predicate accepts."""
    _require_columns(table, _predicate_columns(predicate))
    _require_comparable_predicate(table, predicate)
    return [row for row in table if _holds(row, predicate)]


def project_columns(table: Data, columns: list[ProjectedColumn]) -> Data:
    """Return each row narrowed to the named columns, under the names asked for."""
    _require_columns(table, [column.column for column in columns])
    names = [column.into or column.column for column in columns]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise RecoverableOperatorError(f"a projection cannot write two columns named {repeated}")
    return [
        {column.into or column.column: row.get(column.column) for column in columns}
        for row in table
    ]


def expand_rows(table: Data, column: str, into: str, separator: str | None) -> Data:
    """Return one row per element of a list column, or per piece of a delimited string."""
    _require_columns(table, [column])
    expanded: Data = []
    for row in table:
        for element in _elements(row.get(column), column, separator):
            expanded.append({**row, into: element})
    return expanded


def aggregate_rows(table: Data, group_by: list[str], aggregations: list[Aggregation]) -> Data:
    """Return one row per group, carrying the value each aggregation computed."""
    named = group_by + [
        aggregation.column for aggregation in aggregations if aggregation.column is not None
    ]
    _require_columns(table, named)
    groups: dict[tuple[Any, ...], Data] = {}
    for row in table:
        groups.setdefault(_group_key(row, group_by), []).append(row)
    # A count over a table with no groups still has one answer, which is zero.
    if not groups and not group_by:
        groups[()] = []
    aggregated: Data = []
    for key, rows in groups.items():
        result: Record = dict(zip(group_by, key))
        for aggregation in aggregations:
            result[aggregation.into] = _aggregate(rows, aggregation)
        aggregated.append(result)
    return aggregated


def distinct_rows(table: Data, columns: list[str] | None) -> Data:
    """Return the first row of every distinct key, in the order the rows arrived."""
    if columns is not None:
        _require_columns(table, columns)
    seen: set[tuple[Any, ...]] = set()
    kept: Data = []
    for row in table:
        names = columns if columns is not None else sorted(row)
        key = _hashable(tuple((name, row.get(name)) for name in names), names)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def sort_rows(table: Data, keys: list[SortKey], limit: int | None) -> Data:
    """Return the rows ordered by each key in turn, missing values last, cut to the limit."""
    _require_columns(table, [key.column for key in keys])
    ordered = list(table)
    # Sorted once per key, last key first, because Python's sort is stable and the first key
    # has to win.
    for key in reversed(keys):
        _require_not_numeric_strings(
            [row[key.column] for row in table if row.get(key.column) is not None], key.column
        )
        descending = key.direction == "desc"
        try:
            ordered.sort(
                key=lambda row: _sort_key(row.get(key.column), descending), reverse=descending
            )
        except TypeError:
            raise RecoverableOperatorError(
                f"column {key.column!r} holds values that cannot be ordered against each other"
            ) from None
    return ordered if limit is None else ordered[:limit]


def join_tables(left: Data, right: Data, on: list[JoinKey], how: JoinKind) -> Data:
    """Return the rows matching on every key, with a column both tables hold kept apart."""
    _require_columns(left, [key.left_column for key in on])
    _require_columns(right, [key.right_column for key in on])
    left_renames, right_renames = _joined_names(_column_names(left), _column_names(right), on)
    matches: dict[tuple[Any, ...], Data] = {}
    for row in right:
        key = _hashable(tuple(row.get(pair.right_column) for pair in on), ["a join key"])
        matches.setdefault(key, []).append(row)
    joined: Data = []
    for row in left:
        key = _hashable(tuple(row.get(pair.left_column) for pair in on), ["a join key"])
        renamed = {left_renames[name]: value for name, value in row.items()}
        found = matches.get(key, [])
        if not found and how == "left":
            joined.append(renamed)
        for match in found:
            joined.append(
                {
                    **renamed,
                    **{
                        right_renames[name]: value
                        for name, value in match.items()
                        if name in right_renames
                    },
                }
            )
    return joined


def union_tables(left: Data, right: Data) -> Data:
    """Return the rows of both tables, the first table's rows first."""
    return list(left) + list(right)


def _holds(row: Record, predicate: Predicate) -> bool:
    if isinstance(predicate, AndPredicate):
        return all(_holds(row, condition) for condition in predicate.conditions)
    if isinstance(predicate, OrPredicate):
        return any(_holds(row, condition) for condition in predicate.conditions)
    if isinstance(predicate, NotPredicate):
        return not _holds(row, predicate.condition)
    return _compares(row.get(predicate.column), predicate)


def _compares(value: Any, comparison: Comparison) -> bool:
    if isinstance(comparison, NullComparison):
        return value is None
    # A missing value satisfies no comparison; is_null is how a plan asks about one.
    if value is None:
        return False
    # eq, ne, and in are checked once per column, over every row, before filtering starts
    # (_require_comparable_predicate): a per-row check here would abort a filter that a
    # column mostly of one category was answering correctly, on one dirty cell.
    if isinstance(comparison, InComparison):
        return value in comparison.value
    if isinstance(comparison, PatternComparison):
        if not isinstance(value, str):
            raise RecoverableOperatorError(
                f"column {comparison.column!r} holds {type(value).__name__}, and 're.search' "
                "searches one string for a pattern"
            )
        return re.search(comparison.value, value) is not None
    if comparison.operator == "eq":
        return bool(value == comparison.value)
    if comparison.operator == "ne":
        return bool(value != comparison.value)
    if comparison.operator == "contains":
        if not isinstance(value, str) or not isinstance(comparison.value, str):
            raise RecoverableOperatorError(
                f"column {comparison.column!r} holds {type(value).__name__}, and 'contains' "
                "compares one string against another"
            )
        return comparison.value in value
    try:
        return _ORDERINGS[comparison.operator](value, comparison.value)
    except TypeError:
        raise RecoverableOperatorError(
            f"column {comparison.column!r} holds {type(value).__name__}, which cannot be "
            f"ordered against the {type(comparison.value).__name__} the predicate compares it with"
        ) from None


def _category(value: Any) -> type | None:
    # A category groups the shapes a plan should never treat as interchangeable: a string, a
    # number, or a bool, matching _numbers, which already refuses a bool where a number is
    # expected. None is excluded rather than its own category: comparing against a literal
    # None is what "eq value=None" means, not a category mismatch is_null already covers.
    if value is None:
        return None
    if isinstance(value, bool):
        return bool
    if isinstance(value, str):
        return str
    if isinstance(value, int | float):
        return float
    return type(value)  # pyright: ignore[reportUnknownVariableType]


def _membership_category(value: Any) -> type | None:
    # InComparison's schema (list[str | int | float]) has no bool option: pydantic already
    # turned True/False into 1/0 before this runs, so a row's bool value has to be folded into
    # the number category here to match the membership Python itself would find (True == 1),
    # rather than refusing a check the comparison would actually answer.
    if isinstance(value, bool):
        return float
    return _category(value)


def _require_comparable_predicate(table: Data, predicate: Predicate) -> None:
    # Checked once per column, over every row, before filtering starts: a per-row check would
    # abort a filter that a column mostly of one category was answering correctly, on one
    # dirty cell (an occasional "N/A" in an otherwise numeric column, say).
    if isinstance(predicate, AndPredicate | OrPredicate):
        for condition in predicate.conditions:
            _require_comparable_predicate(table, condition)
        return
    if isinstance(predicate, NotPredicate):
        _require_comparable_predicate(table, predicate.condition)
        return
    _require_comparable_comparison(table, predicate)


def _require_comparable_comparison(table: Data, comparison: Comparison) -> None:
    if isinstance(comparison, NullComparison):
        return
    if isinstance(comparison, PatternComparison):
        # Compiled here, before any row, so a bad pattern is refused even when no row would
        # reach it.
        _require_valid_pattern(comparison.value)
        return
    present = [row[comparison.column] for row in table if row.get(comparison.column) is not None]
    if isinstance(comparison, InComparison):
        _require_comparable_membership(present, comparison.value, comparison.column)
        return
    if comparison.operator in ("eq", "ne"):
        _require_comparable_category(present, comparison.value, comparison.column)


def _require_valid_pattern(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as error:
        raise RecoverableOperatorError(f"pattern {pattern!r} does not compile: {error}") from None


def _require_comparable_category(present: list[Any], other: Any, column: str) -> None:
    other_category = _category(other)
    if other_category is None or not present:
        return
    if not any(_category(value) == other_category for value in present):
        raise RecoverableOperatorError(
            f"column {column!r} holds {_type_names(present)}, which cannot be compared for "
            f"equality against the {type(other).__name__} the predicate compares it with"
        )


def _require_comparable_membership(present: list[Any], options: list[Any], column: str) -> None:
    if not present or not options:
        return
    option_categories = {_membership_category(option) for option in options}
    if not any(_membership_category(value) in option_categories for value in present):
        raise RecoverableOperatorError(
            f"column {column!r} holds {_type_names(present)}, which cannot be compared for "
            f"membership against a list holding {_type_names(options)}"
        )


def _type_names(values: list[Any]) -> list[str]:
    return sorted({type(value).__name__ for value in values})


def _predicate_columns(predicate: Predicate) -> list[str]:
    if isinstance(predicate, AndPredicate | OrPredicate):
        return [
            column for condition in predicate.conditions for column in _predicate_columns(condition)
        ]
    if isinstance(predicate, NotPredicate):
        return _predicate_columns(predicate.condition)
    return [predicate.column]


def _elements(value: Any, column: str, separator: str | None) -> list[Any]:
    # A missing value has no elements to expand, the way SQL's UNNEST(NULL) yields no rows.
    if value is None:
        return []
    if isinstance(value, list):
        return cast(list[Any], value)
    if isinstance(value, str) and separator is not None:
        return [piece.strip() for piece in value.split(separator)]
    raise RecoverableOperatorError(
        f"column {column!r} holds {type(value).__name__}, which cannot be expanded; a list, "
        "or a string with a separator, can be"
    )


def _group_key(row: Record, group_by: list[str]) -> tuple[Any, ...]:
    return _hashable(tuple(row.get(name) for name in group_by), group_by)


def _aggregate(rows: Data, aggregation: Aggregation) -> Any:
    if aggregation.column is None:
        return len(rows)
    column = aggregation.column
    values = [row.get(column) for row in rows]
    present = [value for value in values if value is not None]
    if aggregation.function == "count":
        return len(present)
    if aggregation.function in ("sum", "avg"):
        numbers = _numbers(present, column)
        if aggregation.function == "sum":
            return sum(numbers)
        return sum(numbers) / len(numbers) if numbers else None
    if not present:
        return None
    # Judged per group, not over the whole column: a column that also holds a sentinel like
    # "N/A" in another group must not disable this check for a group whose own values are all
    # numeric strings, or a group-by max reads lexicographically ("9" over "10") with nothing
    # to say so.
    _require_not_numeric_strings(present, column)
    try:
        return min(present) if aggregation.function == "min" else max(present)
    except TypeError:
        raise RecoverableOperatorError(
            f"column {column!r} holds values that cannot be ordered against each other"
        ) from None


def _numbers(values: list[Any], column: str) -> list[float]:
    for value in values:
        # bool is an int in Python, and summing True with a rating is a wrong answer rather
        # than an arithmetic error.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise RecoverableOperatorError(
                f"column {column!r} holds {type(value).__name__}, which cannot be summed"
            )
    return [float(value) for value in values]


def _require_not_numeric_strings(values: list[Any], column: str) -> None:
    # A column that came out of a LOTUS sem_map or sem_extract call is always text, so a column
    # of numeric strings shows up whenever such a column is sorted or ranked before anything
    # declares it a number. Python orders those lexicographically ("10" before "9"), which is
    # never what an order over numbers means; a column mixing numeric and non-numeric strings
    # still sorts as text, since that is a plain text column doing what it should.
    #
    # Below two values, there is nothing to misorder. And when every value is the same length
    # of digits only (zip codes, four-digit years), lexicographic and numeric order agree, so
    # refusing would only cost a repair loop without preventing a wrong answer. Equal width
    # alone is not enough: "10.5"/"9.25", "-9"/"-8", and "1e3"/"999" are the same length and
    # still sort wrong as text, since a decimal point, a sign, or exponent notation breaks the
    # digit-by-digit correspondence equal width otherwise guarantees.
    if len(values) < 2:
        return
    if not all(isinstance(value, str) and _parses_as_number(value) for value in values):
        return
    if len({len(value) for value in values}) == 1 and all(value.isdigit() for value in values):
        return
    raise RecoverableOperatorError(
        f"column {column!r} holds numeric strings, which sort lexicographically as text; "
        "declare the column numeric before ordering it"
    )


def _parses_as_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _column_names(table: Data) -> set[str]:
    names: set[str] = set()
    for row in table:
        names.update(row)
    return names


def _require_columns(table: Data, columns: list[str]) -> None:
    # An empty table carries no evidence about its columns, so nothing can be missing from it.
    if not table:
        return
    present = _column_names(table)
    missing = sorted(set(columns) - present)
    if missing:
        raise RecoverableOperatorError(
            f"the table does not hold the column(s) {missing}; it holds {sorted(present)}"
        )


def _joined_names(
    left_names: set[str], right_names: set[str], on: list[JoinKey]
) -> tuple[dict[str, str], dict[str, str]]:
    # A key both tables name alike holds equal values in every matched row, so the right copy
    # is dropped. Any other shared name is renamed on both sides: keeping the left one bare
    # would let a declaration meaning the right table's column pass the column check silently.
    shared_keys = {key.right_column for key in on if key.left_column == key.right_column}
    shared = sorted((left_names & right_names) - shared_keys)
    left_renames = {name: name for name in left_names}
    right_renames = {name: name for name in right_names - shared_keys}
    taken = left_names | right_names
    for name in shared:
        left_renames[name] = _free_name(f"{name}_left", taken)
        taken.add(left_renames[name])
        right_renames[name] = _free_name(f"{name}_right", taken)
        taken.add(right_renames[name])
    return left_renames, right_renames


def _free_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    suffix = 2
    while f"{name}_{suffix}" in taken:
        suffix += 1
    return f"{name}_{suffix}"


def _sort_key(value: Any, descending: bool) -> tuple[bool, Any]:
    # The first element decides where a missing value lands, and it flips with the direction
    # so that a missing value sorts last whichever way the rows are ordered.
    if value is None:
        return (not descending, 0)
    return (descending, value)


def _hashable(key: tuple[Any, ...], columns: list[str]) -> tuple[Any, ...]:
    try:
        hash(key)
    except TypeError:
        raise RecoverableOperatorError(
            f"the values of {columns} cannot be compared for equality"
        ) from None
    return key
