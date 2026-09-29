"""The transformations the built-in operators run on the tables a bundle carries."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from operator import eq, ge, gt, le, lt, ne
from typing import Any, cast

from blue_data_planner.operators.builtin.schemas import (
    Aggregation,
    AndPredicate,
    BinaryComparison,
    ColumnOperand,
    Comparison,
    JoinKey,
    JoinKind,
    MembershipComparison,
    NotPredicate,
    NullComparison,
    Operand,
    OrPredicate,
    PatternComparison,
    Predicate,
    ProjectedColumn,
    SortKey,
    ValueOperand,
)
from blue_data_planner.types import Data, Record, RecoverableOperatorError

_BINARY_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": eq,
    "ne": ne,
    "lt": lt,
    "lte": le,
    "gt": gt,
    "gte": ge,
}
_ORDERING_OPERATORS: frozenset[str] = frozenset({"lt", "lte", "gt", "gte"})


def filter_rows(table: Data, predicate: Predicate) -> Data:
    """Return the rows the predicate accepts."""
    _require_columns(table, _predicate_columns(predicate))
    _require_comparable_predicate(table, predicate)
    return [row for row in table if _holds(row, predicate) is True]


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


def _holds(row: Record, predicate: Predicate) -> bool | None:
    # SQL's three-valued logic, with None as unknown: a plan written as SQL then keeps the rows
    # SQL would, and a comparison and its negation agree on a stray or missing cell.
    if isinstance(predicate, AndPredicate):
        outcomes = [_holds(row, condition) for condition in predicate.conditions]
        if False in outcomes:
            return False
        return None if None in outcomes else True
    if isinstance(predicate, OrPredicate):
        outcomes = [_holds(row, condition) for condition in predicate.conditions]
        if True in outcomes:
            return True
        return None if None in outcomes else False
    if isinstance(predicate, NotPredicate):
        outcome = _holds(row, predicate.condition)
        return None if outcome is None else not outcome
    return _compares(row, predicate)


def _compares(row: Record, comparison: Comparison) -> bool | None:
    if isinstance(comparison, NullComparison):
        return _operand_value(row, comparison.operand) is None
    left = _operand_value(row, comparison.left)
    # A missing value makes a comparison unknown; is_null is how a plan asks about one.
    if left is None:
        return None
    if isinstance(comparison, MembershipComparison):
        return _is_member(left, comparison.values)
    if isinstance(comparison, PatternComparison):
        if not isinstance(left, str):
            return None
        return re.search(comparison.pattern, left) is not None
    right = _operand_value(row, comparison.right)
    # A stray cell of another category makes the comparison unknown; a comparison the whole
    # table contradicts was already refused before filtering (_require_comparable_predicate).
    if right is None or _category(left) != _category(right):
        return None
    if comparison.operator == "contains":
        if not isinstance(left, str):
            return None
        return right in left
    left, right = _aligned(left, right)
    # Python answers false for every ordering with a NaN, so not (v lt 5) would keep a NaN row
    # that v gte 5 drops; unknown makes a negated ordering and its opposite keep the same rows.
    if comparison.operator in _ORDERING_OPERATORS and (_is_nan(left) or _is_nan(right)):
        return None
    try:
        return bool(_BINARY_OPERATORS[comparison.operator](left, right))
    except TypeError:
        # Only two values of one category Python has no order for, such as two dicts, reach
        # here; strings, numbers, and bools otherwise compare.
        raise RecoverableOperatorError(
            f"{_described(comparison.left)} holds {type(left).__name__}, which cannot be "
            f"ordered with '{comparison.operator}'"
        ) from None


def _is_member(value: Any, options: list[Any]) -> bool | None:
    # A value against an empty list is false, as SQL's IN () is; a value of a category the
    # list lacks is a stray cell, which makes the test unknown.
    if not options:
        return False
    if _membership_category(value) not in {_membership_category(option) for option in options}:
        return None
    return any(eq(*_aligned(value, option)) for option in options)


def _operand_value(row: Record, operand: Operand) -> Any:
    if isinstance(operand, ValueOperand):
        return operand.value
    return row.get(operand.column)


def _operand_values(table: Data, operand: Operand) -> list[Any]:
    if isinstance(operand, ValueOperand):
        return [operand.value]
    return [row[operand.column] for row in table if row.get(operand.column) is not None]


def _aligned(left: Any, right: Any) -> tuple[Any, Any]:
    # Keeps every comparison involving a SQL NUMERIC column (a Decimal) exact. Python already
    # compares a Decimal with an int exactly, but a float carries binary noise (Decimal("4.20")
    # != 4.2), so a float meeting a Decimal is read as the shortest decimal that prints it. A NaN
    # of either kind becomes a float NaN on both sides, since Decimal raises on an ordering with a
    # NaN (always with a signaling one); a float NaN equals nothing, and _compares makes an
    # ordering with it unknown.
    if not isinstance(left, Decimal) and not isinstance(right, Decimal):
        return left, right
    if _is_nan(left) or _is_nan(right):
        return float("nan"), float("nan")
    return _as_decimal_if_float(left), _as_decimal_if_float(right)


def _is_nan(value: Any) -> bool:
    return value.is_nan() if isinstance(value, Decimal) else value != value


def _as_decimal_if_float(value: Any) -> Any:
    return Decimal(repr(value)) if isinstance(value, float) else value


def _described(operand: Operand) -> str:
    if isinstance(operand, ColumnOperand):
        return f"column {operand.column!r}"
    return f"the value {operand.value!r}"


def _category(value: Any) -> type | None:
    # A category groups the shapes a plan should never treat as interchangeable: a string, a
    # number, or a bool, the same split _numbers makes. A Decimal is a number. A missing value
    # has no category; the checks skip it.
    if value is None:
        return None
    if isinstance(value, bool):
        return bool
    if isinstance(value, str):
        return str
    if isinstance(value, int | float | Decimal):
        return float
    return type(value)  # pyright: ignore[reportUnknownVariableType]


def _membership_category(value: Any) -> type | None:
    # MembershipComparison's schema (list[str | int | float]) has no bool option: pydantic already
    # turned True/False into 1/0 before this runs, so a row's bool value has to be folded into
    # the number category here to match the membership Python itself would find (True == 1),
    # rather than refusing a check the comparison would actually answer.
    if isinstance(value, bool):
        return float
    return _category(value)


def _require_comparable_predicate(table: Data, predicate: Predicate) -> None:
    # Checked once over the whole table before filtering starts, and refused only when the table
    # as a whole contradicts a comparison: a per-row refusal would abort a filter that a column
    # mostly of one category was answering correctly, on one dirty cell (an occasional "N/A" in
    # an otherwise numeric column, say); _compares leaves that row unknown. What each kind of
    # comparison is checked for is in _require_comparable_comparison.
    if isinstance(predicate, AndPredicate | OrPredicate):
        for condition in predicate.conditions:
            _require_comparable_predicate(table, condition)
        return
    if isinstance(predicate, NotPredicate):
        _require_comparable_predicate(table, predicate.condition)
        return
    _require_comparable_comparison(table, predicate)


def _require_comparable_comparison(table: Data, comparison: Comparison) -> None:
    # Refuses what the whole table contradicts, which means the parameters are wrong; a row
    # whose values merely differ in category is left to _compares, which makes it unknown.
    if isinstance(comparison, NullComparison):
        return
    left = _operand_values(table, comparison.left)
    if isinstance(comparison, PatternComparison):
        # Compiled here, before any row, so a bad pattern is refused even when no row would
        # reach it.
        _require_valid_pattern(comparison.pattern)
        _require_strings(left, comparison.left, comparison.operator)
        return
    if isinstance(comparison, MembershipComparison):
        _require_comparable_membership(left, comparison.values, comparison.left)
        return
    right = _operand_values(table, comparison.right)
    if comparison.operator == "contains":
        if _lacks_strings(left) or _lacks_strings(right):
            raise RecoverableOperatorError(
                f"{_described(comparison.left)} holds {_type_names(left)} and "
                f"{_described(comparison.right)} holds {_type_names(right)}, and 'contains' "
                "compares one string against another"
            )
        return
    _require_shared_category(left, right, comparison)


def _require_strings(values: list[Any], operand: Operand, operator: str) -> None:
    if _lacks_strings(values):
        raise RecoverableOperatorError(
            f"{_described(operand)} holds {_type_names(values)}, and {operator!r} searches one "
            "string"
        )


def _lacks_strings(values: list[Any]) -> bool:
    # An operand with no values (an all-missing column) carries no evidence either way.
    return bool(values) and not any(isinstance(value, str) for value in values)


def _require_valid_pattern(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as error:
        raise RecoverableOperatorError(f"pattern {pattern!r} does not compile: {error}") from None


def _require_shared_category(
    left: list[Any], right: list[Any], comparison: BinaryComparison
) -> None:
    # An operand with no values (an all-missing column) carries no evidence against the other.
    if not left or not right:
        return
    left_categories = {_category(value) for value in left}
    if not any(_category(value) in left_categories for value in right):
        raise RecoverableOperatorError(
            f"{_described(comparison.left)} holds {_type_names(left)} and "
            f"{_described(comparison.right)} holds {_type_names(right)}, which cannot be "
            f"compared with '{comparison.operator}'"
        )


def _require_comparable_membership(
    present: list[Any], options: list[Any], operand: Operand
) -> None:
    if not present or not options:
        return
    option_categories = {_membership_category(option) for option in options}
    if not any(_membership_category(value) in option_categories for value in present):
        raise RecoverableOperatorError(
            f"{_described(operand)} holds {_type_names(present)}, which cannot be compared for "
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
    return [
        operand.column for operand in _operands(predicate) if isinstance(operand, ColumnOperand)
    ]


def _operands(comparison: Comparison) -> list[Operand]:
    if isinstance(comparison, NullComparison):
        return [comparison.operand]
    if isinstance(comparison, BinaryComparison):
        return [comparison.left, comparison.right]
    return [comparison.left]


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
        # than an arithmetic error. A Decimal from a SQL NUMERIC column is a number, converted
        # to float below so a sum never mixes the two kinds of arithmetic.
        if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
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
