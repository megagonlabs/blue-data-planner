"""What the answer carries once the last fragment has run."""

from __future__ import annotations

import pytest

from blue_data_planner.execution import project_answer
from blue_data_planner.types import BlueDataPlannerError


def test_the_answer_holds_the_named_columns_in_the_order_the_return_gives_them() -> None:
    """A RETURN names the columns that hold the answer, and the rest of the table is dropped."""
    rows = [[{"title": "SWE", "posting_count": 3, "company": "Acme"}]]
    assert project_answer(rows, ["posting_count", "title"]) == [
        [{"posting_count": 3, "title": "SWE"}]
    ]


def test_a_column_the_result_does_not_carry_raises() -> None:
    """Validation rules this out, so reaching it means the plan or the check is broken."""
    with pytest.raises(BlueDataPlannerError, match="posting_count"):
        project_answer([[{"title": "SWE"}]], ["title", "posting_count"])
