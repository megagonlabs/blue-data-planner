"""What each format writes on standard output, and what it leaves off it."""

from __future__ import annotations

import io
import json

import pytest
from blue_data_planner import RunStatus
from rich.console import Console

from blue_data_planner_cli.output import (
    error_line,
    print_answer,
    run_json,
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


def test_the_json_document_carries_how_the_run_ended_and_how_each_fragment_did() -> None:
    """A JSON document is read with no terminal attached, so it carries its own account of the
    run as well as the answer, and a reader asking nothing more gets no plan versions."""
    document = run_json(a_run_over_one_cover_of_two_fragments(), explain=False, log=False)

    assert document["status"] == "ok"
    assert document["counters"]["limits"]["operator_calls"] == 30  # the default RunLimits ceiling
    assert [one["status"] for one in document["results"].values()] == ["ok"]
    assert set(document) == {
        "status",
        "answer",
        "failure_reason",
        "results",
        "counters",
        "conversations",
    }
    json.dumps(document)  # a document that will not encode is not a document


@pytest.mark.parametrize(("explain", "log"), [(True, False), (False, True)])
def test_the_plans_travel_where_they_are_asked_for(explain: bool, log: bool) -> None:
    """Every plan version the request produced sits under one key, so it travels where it is the
    result --explain asked for, or where --log asked for the run behind the answer."""
    document = run_json(a_run_over_one_cover_of_two_fragments(), explain=explain, log=log)

    assert document["pipeline"]["current_logical_plan_id"] == "lp1"


def test_a_failed_run_carries_its_status_and_reason_rather_than_an_empty_answer() -> None:
    """A caller who ignores the status must not read a failure as an empty result."""
    result = a_run_result(status=RunStatus.BUDGET_EXHAUSTED, failure_reason="30 calls spent")

    document = run_json(result, explain=False, log=False)

    assert document["status"] == "budget_exhausted"
    assert document["answer"] is None
    assert document["failure_reason"] == "30 calls spent"


def test_an_error_line_names_its_code_first() -> None:
    """Every Blue CLI reports a failure in this one shape."""
    assert error_line("CONFIG_MISSING", "BLUE_DATA_REGISTRY_PATH is not configured").startswith(
        "ERROR: CONFIG_MISSING "
    )


def test_the_summary_names_the_status_and_what_the_run_spent() -> None:
    """The closing line is what a demonstration reads out loud."""
    assert "ok" in summary_line(a_run_result())
