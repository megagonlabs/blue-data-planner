"""One operator call and the check on what it returned. No model, no registry, no planner."""

from __future__ import annotations

from blue_data_planner.execution.output_columns import check_output_columns
from blue_data_planner.execution.physical_operator import execute_physical_operator
from blue_data_planner.execution.return_answer import project_answer

__all__ = [
    "check_output_columns",
    "execute_physical_operator",
    "project_answer",
]
