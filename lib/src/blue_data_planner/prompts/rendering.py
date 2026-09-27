"""Run data rendered as prompt text, shared by the components that write prompts."""

from __future__ import annotations

from blue_data_planner.types import AttributeView, TableSummary


def render_sample_rows(summary: TableSummary, indent: str) -> list[str]:
    """Return a summary's sample rows, then a count of the rows it leaves out."""
    # The rows not shown are counted, so a model does not take the sample for the whole table.
    lines = [f"{indent}{row}" for row in summary.sample_rows]
    remaining = summary.row_count - len(summary.sample_rows)
    if remaining:
        lines.append(f"{indent}(+{remaining} more row(s))")
    return lines


def render_attribute_views(views: list[AttributeView], indent: str) -> list[str]:
    """Return each attribute's type and description, then its sample values and counts."""
    lines: list[str] = []
    for view in views:
        dtype = view.dtype or "type not recorded"
        described = view.description or "no description recorded"
        lines.append(f"{indent}- {view.ref.entity}.{view.ref.attribute} [{dtype}]: {described}")
        values = _values_line(view)
        if values:
            lines.append(f"{indent}    {values}")
    return lines


def _values_line(view: AttributeView) -> str:
    parts: list[str] = []
    if view.sample_values:
        parts.append("values look like: " + ", ".join(repr(one) for one in view.sample_values))
    counts: list[str] = []
    if view.distinct_count is not None:
        counts.append(f"{view.distinct_count} distinct")
    if view.null_count is not None:
        counts.append(f"{view.null_count} null")
    if counts:
        parts.append(", ".join(counts))
    return "; ".join(parts)
