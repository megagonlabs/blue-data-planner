"""What a table's summary says about the table it stands in for."""

from __future__ import annotations

import pytest

from blue_data_planner.types import summarize_table


def test_a_summary_names_every_column_any_row_carries() -> None:
    """A row need not carry every key another row carries, so the columns are the union."""
    rows = [{"title": "SWE"}, {"company": "Acme", "title": "SRE"}]
    assert summarize_table(rows, 5).columns == ["company", "title"]


def test_a_summary_counts_the_whole_table_and_samples_the_front_of_it() -> None:
    """The count says how large the table is; the sample says what a row in it looks like."""
    summary = summarize_table([{"n": index} for index in range(40)], 3)
    assert summary.row_count == 40
    assert summary.sample_rows == [{"n": 0}, {"n": 1}, {"n": 2}]


@pytest.mark.parametrize("max_rows", [0, 5])
def test_an_empty_table_summarizes_to_no_columns_and_no_rows(max_rows: int) -> None:
    """An empty result is not a failure, so summarizing one is not either."""
    summary = summarize_table([], max_rows)
    assert (summary.row_count, summary.columns, summary.sample_rows) == (0, [], [])


def test_a_negative_row_cap_raises_rather_than_being_read_as_a_slice() -> None:
    """Python would read -1 as "all but the last row," which is not a cap."""
    with pytest.raises(ValueError, match="max_rows"):
        summarize_table([{"n": 1}], -1)
