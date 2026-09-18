"""What an argument becomes, what each format writes, and where it writes it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from blue_data_planner import RunResult, RunStatus
from click.testing import CliRunner

from blue_data_planner_cli import commands
from blue_data_planner_cli.commands import cli
from tests.doubles.components import FakeDataPlanner, a_run_result


def a_planner_answering(monkeypatch: pytest.MonkeyPatch, result: RunResult) -> FakeDataPlanner:
    """Replace the assembled planner with a double returning `result`."""
    double = FakeDataPlanner(result)
    monkeypatch.setattr(commands, "PLANNER_FACTORY", lambda settings: double)
    return double


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> FakeDataPlanner:
    """Replace the assembled planner with a double, so no command reaches a model."""
    return a_planner_answering(monkeypatch, a_run_result(answer=[[{"school": "Lowell"}]]))


def a_configured_environment(tmp_path: Path) -> dict[str, str]:
    """Return an environment with a home directory and a readable registry."""
    registry = tmp_path / "registry.json"
    registry.write_text("[]")
    return {
        "BLUE_DATA_PLANNER_HOME": str(tmp_path / "home"),
        "BLUE_DATA_REGISTRY_PATH": str(registry),
        "OPENAI_API_KEY": "sk-not-used",
    }


def test_the_question_reaches_the_planner_and_the_answer_reaches_standard_output(
    tmp_path: Path, planner: FakeDataPlanner
) -> None:
    """This is the one command a demonstration runs, so its path has to be exact."""
    result = CliRunner().invoke(
        cli, ["data", "query", "which cities have stores"], env=a_configured_environment(tmp_path)
    )

    assert result.exit_code == 0
    assert planner.requests == ["which cities have stores"]
    assert planner.operations == ["run"]
    assert "Lowell" in result.stdout


def test_json_output_holds_nothing_but_the_result(tmp_path: Path, planner: FakeDataPlanner) -> None:
    """A script parsing stdout must not meet a progress line or a summary."""
    result = CliRunner().invoke(
        cli,
        ["data", "query", "which cities have stores", "--format", "json"],
        env=a_configured_environment(tmp_path),
    )

    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["status"] == "ok"


def test_the_trace_prints_only_when_it_is_asked_for_and_never_on_standard_output(
    tmp_path: Path, planner: FakeDataPlanner
) -> None:
    """Diagnostics are opt-in, and they stay out of what a pipe carries."""
    env = a_configured_environment(tmp_path)
    runner = CliRunner()

    quiet = runner.invoke(cli, ["data", "query", "q"], env=env)
    logged = runner.invoke(cli, ["data", "query", "q", "--log"], env=env)

    assert "counters" not in quiet.stderr
    assert "counters" in logged.stderr
    assert "counters" not in logged.stdout


def test_an_option_the_command_does_not_define_is_a_usage_error(tmp_path: Path) -> None:
    """Exit code 2 means the command was typed wrong, in every Blue CLI."""
    result = CliRunner().invoke(
        cli, ["data", "query", "q", "--nope"], env=a_configured_environment(tmp_path)
    )

    assert result.exit_code == 2
    # The usage line names the command, so this fails if `data query` stops parsing its own options.
    assert "Usage: cli data query" in result.stderr


def test_a_run_that_did_not_reach_an_answer_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A script branches on the exit code, so any application failure has to be one."""
    a_planner_answering(monkeypatch, a_run_result(status=RunStatus.BUDGET_EXHAUSTED))

    result = CliRunner().invoke(cli, ["data", "query", "q"], env=a_configured_environment(tmp_path))

    assert result.exit_code == 1


def test_a_failure_reason_holding_square_brackets_is_traced_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model writes the reason, so a bracket in it must neither be read nor eaten as markup."""
    a_planner_answering(
        monkeypatch,
        a_run_result(status=RunStatus.OPERATOR_FAILED, failure_reason="nope [/red] x"),
    )

    result = CliRunner().invoke(
        cli, ["data", "query", "q", "--log"], env=a_configured_environment(tmp_path)
    )

    assert "nope [/red] x" in result.stderr
    assert "operator_failed" in result.stderr  # the summary still follows the trace
