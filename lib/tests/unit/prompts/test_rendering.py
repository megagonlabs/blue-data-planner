"""How run data is rendered as prompt text."""

from __future__ import annotations

from typing import Any

import pytest

from blue_data_planner.prompts.rendering import render_attribute_views, render_sample_rows
from blue_data_planner.types import summarize_table
from tests.doubles.catalog import an_attribute_view


def test_sample_rows_count_the_rows_left_out() -> None:
    """A sample shorter than the table ends with how many rows it does not show."""
    table = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    lines = render_sample_rows(summarize_table(table, 2), "  ")

    assert lines == ["  {'title': 'a'}", "  {'title': 'b'}", "  (+1 more row(s))"]


@pytest.mark.parametrize(
    ("recorded", "values_line"),
    [
        pytest.param(
            {"frequent_values": [("SME", 1390), ("LAM", 423), ("KAM", 187)], "distinct_count": 3},
            "    values, most frequent first: 'SME' (1390 rows), 'LAM' (423 rows), and 1 more",
            id="frequent_values_and_a_distinct_count",
        ),
        pytest.param(
            {"frequent_values": [("SME", 1390), ("LAM", 423), ("KAM", 187)]},
            "    values, most frequent first: 'SME' (1390 rows), 'LAM' (423 rows), and more",
            id="frequent_values_and_no_distinct_count",
        ),
        pytest.param(
            {"frequent_values": [("SME", 1390), ("LAM", 423)], "more_values_recorded": True},
            "    values, most frequent first: 'SME' (1390 rows), 'LAM' (423 rows), and more",
            id="every_value_kept_shown_but_more_recorded",
        ),
        pytest.param(
            {"frequent_values": [("SME", 1390), ("LAM", 423)]},
            "    values, most frequent first: 'SME' (1390 rows), 'LAM' (423 rows)",
            id="the_whole_recorded_list_shown",
        ),
        pytest.param(
            {"sample_values": ["SME", "LAM", "SME"], "distinct_count": 3},
            "    sample values: 'SME', 'LAM', and 1 more",
            id="sample_values_where_no_frequent_values_are_recorded",
        ),
    ],
)
def test_a_list_of_values_says_how_many_it_leaves_out(
    recorded: dict[str, Any], values_line: str
) -> None:
    """Two values are shown and the rest counted, or marked when the catalog has no count."""
    lines = render_attribute_views([an_attribute_view("segment", **recorded)], "", 2)

    assert lines[1] == values_line


@pytest.mark.parametrize(
    ("recorded", "counts_line"),
    [
        pytest.param(
            {"distinct_count": 22, "null_count": 0, "empty_count": 1893, "counted_rows": 2000},
            "    of 2000 rows counted: 22 distinct, 0 null, 1893 empty",
            id="every_count_recorded",
        ),
        pytest.param(
            {"distinct_count": 22, "null_count": 5},
            "    22 distinct, 5 null",
            id="no_rows_counted",
        ),
        pytest.param({}, None, id="nothing_recorded"),
    ],
)
def test_counts_are_stated_against_the_rows_counted_and_only_when_recorded(
    recorded: dict[str, Any], counts_line: str | None
) -> None:
    """An unrecorded count is left out rather than rendered empty or as zero."""
    view = an_attribute_view("user_name", description="who wrote it", **recorded)

    lines = render_attribute_views([view], "", 3)

    assert lines[0] == "- postings.user_name [str]: who wrote it"
    assert lines[1:] == ([counts_line] if counts_line is not None else [])
