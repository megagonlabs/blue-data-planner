"""Tables, the result payload, the trace, and the one line an error prints as."""

from __future__ import annotations

import dataclasses
from typing import Any

from blue_data_planner import RunResult
from blue_data_planner.plan import signatures
from blue_data_planner.types import ErrorInfo, ExecutionResult
from rich.console import Console
from rich.table import Table


def print_answer(result: RunResult, output_format: str, console: Console) -> None:
    """Print the answer as a table, or the whole result as JSON."""
    if output_format == "json":
        console.print_json(data=result_payload(result))
        return
    for position, table_rows in enumerate(result.answer or [], start=1):
        if len(result.answer or []) > 1:
            console.print(f"table {position} of {len(result.answer or [])}")
        console.print(_as_table(table_rows))


def result_payload(result: RunResult) -> dict[str, Any]:
    """Return the JSON form of a run: everything but the model transcripts."""
    return {
        "status": str(result.status),
        "answer": result.answer,
        "failure_reason": result.failure_reason,
        "pipeline": result.pipeline.to_dict(),
        "results": {key: _result_payload(value) for key, value in result.results.items()},
        "counters": {
            "operator_calls": result.counters.operator_calls,
            "failure_replans": result.counters.failure_replans,
            "control_replans": result.counters.control_replans,
            "limits": dataclasses.asdict(result.counters.limits),
        },
        "usage": _usage_payload(result),
    }


def trace_lines(result: RunResult, log_limit: int) -> list[str]:
    """Return the run's trace: one line per fragment of each cover, then what it spent."""
    lines: list[str] = []
    for position, cover in enumerate(result.pipeline.physical_plans.values(), start=1):
        lines.append(f"cover {position}")
        plan = result.pipeline.logical_plans.get(cover.logical_plan_id)
        signed = signatures(plan, cover) if plan is not None else {}
        for fragment in cover.fragments:
            outcome = result.results.get(signed.get(fragment.root_logical_node_id, ""))
            lines.append(
                f"  {fragment.id}  {','.join(fragment.logical_node_ids)}  "
                f"{fragment.operator_id}  {_outcome(outcome)}"
            )
    counters = result.counters
    lines.append(
        f"counters  operator calls {counters.operator_calls}/{counters.limits.operator_calls}   "
        f"failure replans {counters.failure_replans}/{counters.limits.failure_replans}   "
        f"control replans {counters.control_replans}/{counters.limits.control_replans}"
    )
    usage = _usage_payload(result)
    lines.append(f"model     {usage['calls']} calls   ${usage['cost']:.2f}")
    if result.failure_reason:
        lines.append(f"ended     {result.failure_reason}")
    return [line[:log_limit] for line in lines]


def trace_payload(result: RunResult, log_limit: int) -> dict[str, Any]:
    """Return the trace as one object, for a run printing JSON on standard output."""
    return {"trace": trace_lines(result, log_limit)}


def summary_line(result: RunResult) -> str:
    """Return the closing line: how the run ended and what it spent."""
    usage = _usage_payload(result)
    return (
        f"{result.status}  operator calls {result.counters.operator_calls}  "
        f"model calls {usage['calls']}  ${usage['cost']:.2f}"
    )


def error_line(code: str, description: str) -> str:
    """Return the one shape every Blue CLI reports a failure in."""
    return f"ERROR: {code} {description}"


def _as_table(rows: list[dict[str, Any]]) -> Table:
    columns = list(rows[0]) if rows else []
    table = Table(*columns)
    for row in rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    return table


def _outcome(outcome: Any) -> str:
    if isinstance(outcome, ExecutionResult):
        return f"ok ({outcome.row_count} rows)"
    if outcome is not None:
        return "ok (discovery)"
    return "no result under this cover"


def _result_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, ExecutionResult):
        return {
            "status": str(value.status),
            "operator_id": value.operator_id,
            "row_count": value.row_count,
            "error": _error_payload(value.error),
            "report": value.report,
        }
    return {
        "search_terms": value.search_terms,
        "matches": [dataclasses.asdict(match) for match in value.matches],
        "dropped_terms": value.dropped_terms,
    }


def _error_payload(error: ErrorInfo | None) -> dict[str, str] | None:
    if error is None:
        return None
    return {"kind": str(error.kind), "message": error.message}


def _usage_payload(result: RunResult) -> dict[str, Any]:
    usage = result.conversations.usage
    return {"calls": len(usage), "cost": sum(one.cost for one in usage)}
