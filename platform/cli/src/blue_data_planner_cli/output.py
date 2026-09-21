"""The answer, the plan, the JSON a run prints, and the one line an error prints as."""

from __future__ import annotations

from typing import Any

from blue_data_planner import RunResult
from rich.console import Console
from rich.table import Column, Table
from rich.text import Text


def print_answer(result: RunResult, output_format: str, console: Console) -> None:
    """Print the answer as a table, or the run's answer payload as JSON."""
    if output_format == "json":
        console.print_json(data=answer_payload(result))
        return
    for position, table_rows in enumerate(result.answer or [], start=1):
        if len(result.answer or []) > 1:
            console.print(f"table {position} of {len(result.answer or [])}")
        console.print(_as_table(table_rows))


def print_plan(result: RunResult, output_format: str, console: Console) -> None:
    """Print the logical plan and the cover a planning run produced."""
    if output_format == "json":
        console.print_json(data=plan_payload(result))
        return
    for plan in result.pipeline.logical_plans.values():
        nodes = Table("Node", "Kind", "Instruction")
        for node in plan.nodes:
            # The instruction is model-authored, so it goes through Text like every other
            # cell in _as_table: a plain string with square brackets would be read as markup.
            instruction = str(node.attributes.get("instruction", ""))
            nodes.add_row(Text(node.id), Text(str(node.kind)), Text(instruction))
        console.print(nodes)
    for cover in result.pipeline.physical_plans.values():
        fragments = Table("Fragment", "Nodes", "Operator")
        for fragment in cover.fragments:
            fragments.add_row(
                Text(fragment.id),
                Text(",".join(fragment.logical_node_ids)),
                Text(fragment.operator_id),
            )
        console.print(fragments)


def answer_payload(result: RunResult) -> dict[str, Any]:
    """Return the JSON form of a run asked for an answer."""
    return _without(result.to_dict(), "pipeline", "results")


def plan_payload(result: RunResult) -> dict[str, Any]:
    """Return the JSON form of a run asked for a plan."""
    return _without(result.to_dict(), "answer", "results")


def summary_line(result: RunResult) -> str:
    """Return the closing line: how the run ended and what it spent."""
    spend = result.conversations.to_dict()
    return (
        f"{result.status}  operator calls {result.counters.operator_calls}  "
        f"model calls {spend['calls']}  ${spend['cost']:.2f}"
    )


def error_line(code: str, description: str) -> str:
    """Return the one shape every Blue CLI reports a failure in."""
    return f"ERROR: {code} {description}"


def _without(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in keys}


def _as_table(rows: list[dict[str, Any]]) -> Table:
    # Every header and cell is a Text rather than a string: Rich reads square brackets in a
    # string as markup, which drops "[red, blue]" and raises on "[/red]".
    columns = list(rows[0]) if rows else []
    table = Table(*(Column(header=Text(column)) for column in columns))
    for row in rows:
        table.add_row(*(Text(str(row.get(column, ""))) for column in columns))
    return table
