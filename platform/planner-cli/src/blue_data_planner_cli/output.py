"""What the CLI writes: the answer, the plan, a run's JSON document, a table, an error."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

import click
from blue_data_planner import RunResult
from rich.console import Console
from rich.table import Column, Table
from rich.text import Text

from blue_data_planner_cli.config import OUTPUT_FORMATS


def format_option(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add --format to a command that prints a result."""
    return click.option(
        "--format",
        "output_format",
        type=click.Choice(OUTPUT_FORMATS),
        default=None,
        callback=_record_given_format,
        help="How the result is written. Default: BLUE_DATA_CLI_FORMAT, else table.",
    )(command)


def record_output_format(ctx: click.Context, output_format: str) -> None:
    """Record the format in force, so an error raised after this point is written in it."""
    ctx.obj["output_format"] = output_format


def error_format(state: dict[str, Any]) -> str:
    """Return the format an error is written in: the one a command recorded, else table."""
    # A command without --format records nothing, so its errors stay the plain line.
    return state.get("output_format", "table")


def _record_given_format(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    # An error can come before the profile is read, or be that it cannot be, so until the
    # command resolves the format, --format and then the environment variable stand in for it.
    record_output_format(ctx, value or ctx.obj["environ"].get("BLUE_DATA_CLI_FORMAT", "table"))
    return value


def print_answer(
    result: RunResult, output_format: str, console: Console, *, log: bool = False
) -> None:
    """Print the answer as a table, or the whole run as one JSON document."""
    if output_format == "json":
        print_json(run_json(result, explain=False, log=log), console)
        return
    for position, table_rows in enumerate(result.answer or [], start=1):
        if len(result.answer or []) > 1:
            console.print(f"table {position} of {len(result.answer or [])}")
        console.print(_as_table(table_rows))


def print_plan(
    result: RunResult, output_format: str, console: Console, *, log: bool = False
) -> None:
    """Print the logical plan and the cover a planning run produced."""
    if output_format == "json":
        print_json(run_json(result, explain=True, log=log), console)
        return
    for plan in result.pipeline.logical_plans.values():
        nodes = Table("Node", "Kind", "Instruction")
        for node in plan.nodes:
            # Every cell goes through Text like the ones in _as_table: the instruction is
            # model-authored, and a plain string with square brackets would read as markup.
            nodes.add_row(Text(node.id), Text(str(node.kind)), Text(node.instruction))
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


def run_json(result: RunResult, *, explain: bool, log: bool) -> dict[str, Any]:
    """Return the JSON document standard output carries."""
    payload = result.to_dict()
    # Every plan version the request produced sits under `pipeline`, so it grows with what the
    # request tried. It travels where it is the result asked for, or where the log was.
    if not (explain or log):
        del payload["pipeline"]
    return payload


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


def error_document(code: str, description: str) -> dict[str, str]:
    """Return the document a failure is written as under --format json."""
    # The MCP server's payload with the code added, so a reader of either reads one shape.
    return {"error": description, "code": code}


def report_error(code: str, description: str, output_format: str) -> None:
    """Print one failure as the error line, and as a document on standard output under json."""
    if output_format == "json":
        print_json(error_document(code, description), Console())
    print_error(Console(stderr=True), code, description)


def print_error(console: Console, code: str, description: str) -> None:
    """Print one failure in that shape."""
    # A description carries model-authored text and source paths, so Rich must neither read a
    # square bracket in one as markup nor fold a long one into the terminal's width.
    console.print(error_line(code, description), markup=False, highlight=False, soft_wrap=True)


def print_table(headers: Sequence[str], rows: Iterable[Sequence[str]], console: Console) -> None:
    """Print rows under their headers, the way every table this CLI writes is drawn."""
    table = Table(*headers)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_json(payload: Any, console: Console) -> None:
    """Print one JSON document, the way every JSON this CLI writes is written."""
    console.print_json(data=payload)


def _as_table(rows: list[dict[str, Any]]) -> Table:
    # Every header and cell is a Text rather than a string: Rich reads square brackets in a
    # string as markup, which drops "[red, blue]" and raises on "[/red]".
    columns = list(rows[0]) if rows else []
    table = Table(*(Column(header=Text(column)) for column in columns))
    for row in rows:
        table.add_row(*(Text(str(row.get(column, ""))) for column in columns))
    return table
