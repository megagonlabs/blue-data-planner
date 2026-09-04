"""The transformations the built-in operators run on the tables a bundle carries."""

from __future__ import annotations

from collections.abc import Callable
from operator import ge, gt, le, lt
from typing import Any, cast

from blue_data_planner.operators.builtin.schemas import (
    Aggregation,
    AndPredicate,
    Comparison,
    JoinKey,
    JoinKind,
    NotPredicate,
    OrPredicate,
    Predicate,
    ProjectedColumn,
    SortKey,
)
from blue_data_planner.types import Data, Record, RecoverableOperatorError

_ORDERINGS: dict[str, Callable[[Any, Any], bool]] = {"lt": lt, "lte": le, "gt": gt, "gte": ge}


def filter_rows(table: Data, predicate: Predicate) -> Data:
    """Return the rows the predicate accepts."""
    _require_columns(table, _predicate_columns(predicate))
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
    """Return the rows matching on every key, renaming a right column whose name collides."""
    _require_columns(left, [key.left_column for key in on])
    _require_columns(right, [key.right_column for key in on])
    renames = _right_names(right, _column_names(left))
    matches: dict[tuple[Any, ...], Data] = {}
    for row in right:
        key = _hashable(tuple(row.get(pair.right_column) for pair in on), ["a join key"])
        matches.setdefault(key, []).append(row)
    joined: Data = []
    for row in left:
        key = _hashable(tuple(row.get(pair.left_column) for pair in on), ["a join key"])
        found = matches.get(key, [])
        if not found and how == "left":
            joined.append(dict(row))
        for match in found:
            joined.append({**row, **{renames[name]: value for name, value in match.items()}})
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
    if comparison.operator == "is_null":
        return value is None
    # A missing value satisfies no comparison; is_null is how a plan asks about one.
    if value is None:
        return False
    if comparison.operator == "eq":
        return bool(value == comparison.value)
    if comparison.operator == "ne":
        return bool(value != comparison.value)
    if comparison.operator == "in":
        return value in cast(list[Any], comparison.value)
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


def _predicate_columns(predicate: Predicate) -> list[str]:
    if isinstance(predicate, AndPredicate | OrPredicate):
        return [
            column for condition in predicate.conditions for column in _predicate_columns(condition)
        ]
    if isinstance(predicate, NotPredicate):
        return _predicate_columns(predicate.condition)
    return [predicate.column]


def _elements(value: Any, column: str, separator: str | None) -> list[Any]:
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


def _right_names(right: Data, left_names: set[str]) -> dict[str, str]:
    right_names = _column_names(right)
    renames: dict[str, str] = {}
    for name in sorted(right_names):
        if name not in left_names:
            renames[name] = name
            continue
        renamed = f"{name}_right"
        if renamed in left_names or renamed in right_names:
            raise RecoverableOperatorError(
                f"a join cannot keep the shared column {name!r} apart: {renamed!r} is taken"
            )
        renames[name] = renamed
    return renames


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
