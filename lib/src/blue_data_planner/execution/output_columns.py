"""The check a result has to pass against the output columns declared for the call."""

from __future__ import annotations

import math
from typing import Any

from blue_data_planner.types import (
    BlueDataPlannerError,
    ColumnDataType,
    Data,
    DataBundle,
    ErrorInfo,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    OutputColumn,
)

# Accepted only because the column was declared boolean. Inference from content has
# to refuse "y" and "1", which are ordinary values in plenty of real columns.
BOOLEAN_SPELLINGS: dict[str, bool] = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
    "y": True,
    "n": False,
    "1": True,
    "0": False,
}


class DeclaredTypeMismatch(BlueDataPlannerError):
    """A value does not spell the type declared for its column."""

    def __init__(
        self, value: Any, declared_type: ColumnDataType, row_index: int | None = None
    ) -> None:
        location = "" if row_index is None else f" in row {row_index}"
        super().__init__(f"holds {value!r}{location}, which is not a valid {declared_type}")
        self.value = value
        self.declared_type = declared_type
        self.row_index = row_index


def coerce_value(value: Any, declared_type: ColumnDataType) -> Any:
    """Return one value as its declared type, raising when it spells no such type."""
    if value is None or declared_type == "str":
        return value

    if declared_type == "bool":
        if isinstance(value, bool):
            return value
        # A bare 0 or 1 reaches here from a source that stores booleans as integers.
        # Every other integer is a real mismatch.
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in BOOLEAN_SPELLINGS:
            return BOOLEAN_SPELLINGS[value.strip().lower()]
        raise DeclaredTypeMismatch(value, declared_type)

    # bool subclasses int, so True would pass every numeric check below. Reading it as 1
    # is the exact mistake this check exists to catch, so it is refused first.
    if isinstance(value, bool):
        raise DeclaredTypeMismatch(value, declared_type)
    number: float
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise DeclaredTypeMismatch(value, declared_type) from None
    elif isinstance(value, int | float):
        number = value
    else:
        raise DeclaredTypeMismatch(value, declared_type)
    # float() reads "nan" and "inf"; no declared column holds either, and int() raises
    # on both.
    if not math.isfinite(number):
        raise DeclaredTypeMismatch(value, declared_type)

    if declared_type == "float":
        return float(number)
    # A fractional value is refused rather than truncated. Turning 48.7 into 48 changes
    # the answer, which is worse than a rejection a retry can read.
    if number != int(number):
        raise DeclaredTypeMismatch(value, declared_type)
    return int(number)


def coerce_column(values: list[Any], declared_type: ColumnDataType) -> list[Any]:
    """Return one column's values as its declared type, naming the row that refuses."""
    coerced: list[Any] = []
    for row_index, value in enumerate(values):
        try:
            coerced.append(coerce_value(value, declared_type))
        except DeclaredTypeMismatch as mismatch:
            raise DeclaredTypeMismatch(mismatch.value, declared_type, row_index) from None
    return coerced


def check_output_columns(
    result: ExecutionResult,
    required_output_columns: list[OutputColumn],
    operator_id: str,
) -> ExecutionResult:
    """Return the result with every table's declared columns coerced, or a recoverable failure."""
    if result.status is not ExecutionStatus.OK or not required_output_columns:
        return result
    if result.output is None:
        raise BlueDataPlannerError(
            f"operator {operator_id!r} returned an accepted result with no output"
        )

    # The declared columns describe the result the call promised, and every table in the
    # bundle reaches the next node, so each one has to carry them.
    output: DataBundle = []
    for table_index, records in enumerate(result.output):
        checked = _check_one_table(
            records, required_output_columns, operator_id, table_index, len(result.output)
        )
        if isinstance(checked, ExecutionResult):
            return checked
        output.append(checked)
    return ExecutionResult(
        status=ExecutionStatus.OK,
        output=output,
        operator_id=operator_id,
        row_count=sum(len(table) for table in output),
    )


def _check_one_table(
    records: Data,
    required_output_columns: list[OutputColumn],
    operator_id: str,
    table_index: int,
    table_count: int,
) -> Data | ExecutionResult:
    """Return one table with its declared columns coerced, or the failure that rejects it."""
    # A zero-row table carries no column evidence either way, and an empty result is an
    # answer rather than a failure.
    if not records:
        return records
    # With one table there is nothing to tell apart, and "table 0" would only clutter
    # the message a review turn reads.
    where = "" if table_count == 1 else f" in table {table_index}"

    missing = [
        column.name
        for column in required_output_columns
        if any(column.name not in record for record in records)
    ]
    if missing:
        return _recoverable(
            f"required output column(s) {', '.join(repr(name) for name in missing)} are "
            f"absent{where}; the result holds {', '.join(repr(name) for name in records[0])}",
            operator_id,
        )

    coerced: dict[str, list[Any]] = {}
    for column in required_output_columns:
        try:
            coerced[column.name] = coerce_column(
                [record[column.name] for record in records], column.dtype
            )
        except DeclaredTypeMismatch as mismatch:
            return _recoverable(
                f"required output column {column.name!r} is declared {column.dtype} but "
                f"{mismatch}{where}",
                operator_id,
            )

    # The coerced values land in copies. A passthrough operator such as a filter hands
    # back the very dicts its input node committed, so writing in place would retype a
    # finished node's output from underneath it, and a row-reducing operator would
    # retype only the surviving rows, leaving the upstream node holding mixed types.
    table = [dict(record) for record in records]
    for name, values in coerced.items():
        for record, value in zip(table, values):
            record[name] = value
    return table


def _recoverable(message: str, operator_id: str) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.FAILED,
        operator_id=operator_id,
        error=ErrorInfo(kind=ErrorKind.RECOVERABLE, message=message),
    )
