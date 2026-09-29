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


def render_attribute_views(views: list[AttributeView], indent: str, max_values: int) -> list[str]:
    """Return each attribute's type and description, then its values and counts."""
    lines: list[str] = []
    for view in views:
        dtype = view.dtype or "type not recorded"
        described = view.description or "no description recorded"
        lines.append(f"{indent}- {view.ref.entity}.{view.ref.attribute} [{dtype}]: {described}")
        values = _values_line(view, max_values)
        if values:
            lines.append(f"{indent}    {values}")
        counts = _counts_line(view)
        if counts:
            lines.append(f"{indent}    {counts}")
    return lines


def _values_line(view: AttributeView, max_values: int) -> str:
    # Every list says what it leaves out, so a model does not take a few values for the column.
    if view.frequent_values:
        shown = view.frequent_values[:max_values]
        listed = [
            f"{one.value!r} ({one.rows} {'row' if one.rows == 1 else 'rows'})" for one in shown
        ]
        return "values, most frequent first: " + _with_rest(listed, view, len(view.frequent_values))
    if view.sample_values:
        shown_samples = view.sample_values[:max_values]
        listed = [repr(one) for one in shown_samples]
        return "sample values: " + _with_rest(listed, view, len(view.sample_values))
    return ""


def _with_rest(listed: list[str], view: AttributeView, held: int) -> str:
    if view.distinct_count is not None:
        rest = view.distinct_count - len(listed)
        if rest > 0:
            listed.append(f"and {rest} more")
    elif held > len(listed):
        listed.append("and more")
    return ", ".join(listed)


def _counts_line(view: AttributeView) -> str:
    missing: list[str] = []
    if view.null_count is not None:
        missing.append(f"{view.null_count} null")
    if view.empty_count is not None:
        missing.append(f"{view.empty_count} empty")
    parts: list[str] = []
    if view.distinct_count is not None:
        parts.append(f"{view.distinct_count} distinct")
    if missing:
        # The catalog may count over part of a table, so a count is stated against its own rows.
        if view.counted_rows is not None:
            parts.append(f"of {view.counted_rows} rows counted, " + ", ".join(missing))
        else:
            parts.append(", ".join(missing))
    return "; ".join(parts)
