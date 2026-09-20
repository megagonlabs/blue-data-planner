"""What a table holds, for a prompt or a trace that shows the table rather than passing it on."""

from __future__ import annotations

from blue_data_planner.types import Data, TableSummary


def summarize_table(table: Data, max_rows: int) -> TableSummary:
    """Return a table's row count, its column names, and its first `max_rows` rows."""
    if max_rows < 0:
        raise ValueError(f"max_rows is {max_rows}; expected zero or more")
    columns = sorted({column for row in table for column in row})
    return TableSummary(row_count=len(table), columns=columns, sample_rows=table[:max_rows])
