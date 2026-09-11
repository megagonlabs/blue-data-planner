"""What the row cap accepts and what it refuses."""

from __future__ import annotations

import pytest

from blue_data_planner.operators.adapters.lotus.row_cap import check_row_cap
from blue_data_planner.types import Data, RecoverableOperatorError


def rows(count: int) -> Data:
    """Return a table of `count` rows, whose values do not matter to the cap."""
    return [{"n": index} for index in range(count)]


@pytest.mark.parametrize("count", [0, 1, 10])
def test_a_table_within_the_cap_is_accepted(count: int) -> None:
    """A table at or below the cap passes without raising, empty included."""
    check_row_cap("lotus.sem_filter", [rows(count)], max_rows=10)


def test_a_table_over_the_cap_is_refused_with_both_numbers() -> None:
    """A table above the cap raises recoverably, naming the rows that arrived and the cap."""
    with pytest.raises(RecoverableOperatorError) as refusal:
        check_row_cap("lotus.sem_filter", [rows(11)], max_rows=10)

    assert "11" in str(refusal.value)
    assert "10" in str(refusal.value)


def test_a_join_is_counted_as_the_two_tables_multiplied() -> None:
    """Two tables are judged on their product, since that is how many pairs are compared."""
    # 4 x 3 = 12 pairs, over a cap of 10, while neither table is over it on its own.
    with pytest.raises(RecoverableOperatorError):
        check_row_cap("lotus.sem_join", [rows(4), rows(3)], max_rows=10)
