"""How many rows one LOTUS operation may be handed."""

from __future__ import annotations

from blue_data_planner.types import Data, RecoverableOperatorError

DEFAULT_MAX_ROWS = 5000
# sem_topk is quickselect over model-judged pairwise comparisons, so its model calls grow with
# the comparison count rather than the row count — measured at roughly 8x the rows in one
# partitioning. A lower cap here keeps its worst case in the same order of magnitude as what
# DEFAULT_MAX_ROWS already bounds for the other operators.
SEM_TOPK_MAX_ROWS = 500


def check_row_cap(operator_id: str, tables: list[Data], max_rows: int) -> None:
    """Raise when an operation would read more rows than the cap allows."""
    # A join judges every pair, so the two tables multiply rather than add. One table
    # multiplies against the empty product, which is its own length.
    counted = 1
    for table in tables:
        counted *= len(table)
    if counted <= max_rows:
        return
    raise RecoverableOperatorError(
        f"{operator_id} was handed {counted} rows to judge, above the {max_rows}-row cap. A "
        "built-in operation can narrow the table before a semantic one reads it."
    )
