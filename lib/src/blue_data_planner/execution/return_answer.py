"""The columns a RETURN node names, taken off the result of the node it reads."""

from __future__ import annotations

from blue_data_planner.types import BlueDataPlannerError, Data, DataBundle


def project_answer(inputs: DataBundle, columns: list[str]) -> DataBundle:
    """Return each table's named columns, in the order the RETURN node gives them."""
    return [_project(table, columns) for table in inputs]


def _project(table: Data, columns: list[str]) -> Data:
    projected: Data = []
    for row in table:
        missing = [name for name in columns if name not in row]
        if missing:
            raise BlueDataPlannerError(
                f"the answer projects {missing}, which the result does not carry; the row "
                f"holds {sorted(row)}"
            )
        projected.append({name: row[name] for name in columns})
    return projected
