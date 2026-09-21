"""What each format writes on standard output, and what it leaves off it."""

from __future__ import annotations

import io
import json

from blue_data_planner import RunStatus
from rich.console import Console

from blue_data_planner_cli.output import (
    answer_payload,
    error_line,
    plan_payload,
    print_answer,
    summary_line,
)
from tests.doubles.components import a_run_over_one_cover_of_two_fragments, a_run_result


def a_console() -> tuple[Console, io.StringIO]:
    """Return a console writing into a buffer, and the buffer."""
    buffer = io.StringIO()
    return Console(file=buffer, width=200), buffer


def test_a_table_carries_the_column_names_the_rows_came_with() -> None:
    """A reader identifies a column by its own name, not by a position."""
    result = a_run_result(answer=[[{"school": "Lowell", "takers": 44}]])
    console, buffer = a_console()

    print_answer(result, "table", console)

    printed = buffer.getvalue()
    assert "school" in printed and "takers" in printed and "Lowell" in printed


def test_an_answer_value_holding_square_brackets_reaches_the_table_whole() -> None:
    """An answer is data a model wrote, so Rich must not read a bracket in one as markup."""
    result = a_run_result(answer=[[{"co[/red]lumn": "cols [red, blue]"}]])
    console, buffer = a_console()

    print_answer(result, "table", console)

    printed = buffer.getvalue()
    assert "co[/red]lumn" in printed
    assert "cols [red, blue]" in printed


def test_the_json_format_prints_the_payload_rather_than_a_table() -> None:
    """--format json chooses how the result is written, and the result is still the answer."""
    console, buffer = a_console()

    print_answer(a_run_result(answer=[[{"school": "Lowell"}]]), "json", console)

    assert json.loads(buffer.getvalue())["answer"] == [[{"school": "Lowell"}]]


def test_the_answer_payload_wraps_the_answer_in_how_the_run_ended_and_nothing_it_read() -> None:
    """A JSON document is read with no terminal attached, so it carries its own account."""
    payload = answer_payload(a_run_over_one_cover_of_two_fragments())

    assert payload["status"] == "ok"
    assert payload["counters"]["limits"]["operator_calls"] == 30  # the default RunLimits ceiling
    assert set(payload) == {"status", "answer", "failure_reason", "counters", "conversations"}
    json.dumps(payload)  # the payload has to survive encoding


def test_the_plan_payload_carries_the_plan_where_the_answer_payload_carries_rows() -> None:
    """A plan is what --explain was asked for, so it is what standard output carries."""
    payload = plan_payload(a_run_over_one_cover_of_two_fragments())

    assert payload["pipeline"]["current_logical_plan_id"] == "lp1"
    assert set(payload) == {"status", "pipeline", "failure_reason", "counters", "conversations"}
    json.dumps(payload)  # the payload has to survive encoding


def test_a_failed_run_carries_its_status_and_reason_rather_than_an_empty_answer() -> None:
    """A caller who ignores the status must not read a failure as an empty result."""
    result = a_run_result(status=RunStatus.BUDGET_EXHAUSTED, failure_reason="30 calls spent")

    payload = answer_payload(result)

    assert payload["status"] == "budget_exhausted"
    assert payload["answer"] is None
    assert payload["failure_reason"] == "30 calls spent"


def test_an_error_line_names_its_code_first() -> None:
    """Every Blue CLI reports a failure in this one shape."""
    assert error_line("CONFIG_MISSING", "BLUE_DATA_REGISTRY_PATH is not configured").startswith(
        "ERROR: CONFIG_MISSING "
    )


def test_the_summary_names_the_status_and_what_the_run_spent() -> None:
    """The closing line is what a demonstration reads out loud."""
    assert "ok" in summary_line(a_run_result())
