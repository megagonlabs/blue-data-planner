"""How run data is rendered as prompt text."""

from __future__ import annotations

import pytest

from blue_data_planner.prompts.rendering import render_attribute_views, render_sample_rows
from blue_data_planner.types import AttributeRef, AttributeView, summarize_table


def a_view(distinct_count: int | None, null_count: int | None) -> AttributeView:
    """One attribute of a postings entity, with the counts given."""
    return AttributeView(
        ref=AttributeRef(
            source="jobs",
            database="jobs",
            collection="public",
            entity="postings",
            attribute="remote",
        ),
        dtype="bool",
        description="whether the role is remote",
        sample_values=[True, False],
        distinct_count=distinct_count,
        null_count=null_count,
        min=None,
        max=None,
        semantic_type=None,
    )


def test_sample_rows_count_the_rows_left_out() -> None:
    """A sample shorter than the table ends with how many rows it does not show."""
    table = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    lines = render_sample_rows(summarize_table(table, 2), "  ")

    assert lines == ["  {'title': 'a'}", "  {'title': 'b'}", "  (+1 more row(s))"]


@pytest.mark.parametrize(
    ("distinct_count", "null_count", "values_line"),
    [
        (2, 0, "    values look like: True, False; 2 distinct, 0 null"),
        (None, 5, "    values look like: True, False; 5 null"),
        (None, None, "    values look like: True, False"),
    ],
)
def test_attribute_views_show_a_count_only_when_the_catalog_recorded_it(
    distinct_count: int | None, null_count: int | None, values_line: str
) -> None:
    """An unrecorded distinct or null count is left out rather than rendered empty."""
    lines = render_attribute_views([a_view(distinct_count, null_count)], "")

    assert lines == ["- postings.remote [bool]: whether the role is remote", values_line]
