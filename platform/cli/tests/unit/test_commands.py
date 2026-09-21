"""What an argument becomes, what each format writes, and where it writes it."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs]
    ProfileStore,
    encode,
)
from blue_data_planner import RunResult, RunStatus
from blue_data_planner.plan import signatures
from blue_data_planner.types import (
    DataPipeline,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    LogicalOperator,
    LogicalPlan,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
)
from click.testing import CliRunner

from blue_data_planner_cli import commands
from blue_data_planner_cli.commands import cli
from blue_data_planner_cli.config import PlannerSettings
from tests.doubles.components import FakeDataPlanner, a_run_result


def a_planner_answering(monkeypatch: pytest.MonkeyPatch, result: RunResult) -> FakeDataPlanner:
    """Replace the assembled planner with a double returning `result`."""
    double = FakeDataPlanner(result)

    def _factory(settings: PlannerSettings) -> FakeDataPlanner:
        return double

    monkeypatch.setattr(commands, "PLANNER_FACTORY", _factory)
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
    logged = runner.invoke(cli, ["data", "query", "q", "--trace"], env=env)

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
        cli, ["data", "query", "q", "--trace"], env=a_configured_environment(tmp_path)
    )

    assert "nope [/red] x" in result.stderr
    assert "operator_failed" in result.stderr  # the summary still follows the trace


def test_a_run_that_did_not_finish_reports_its_status_as_the_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status is what failed, so it is also the code a caller matches on."""
    a_planner_answering(
        monkeypatch,
        a_run_result(status=RunStatus.BUDGET_EXHAUSTED, failure_reason="30 calls spent"),
    )

    result = CliRunner().invoke(cli, ["data", "query", "q"], env=a_configured_environment(tmp_path))

    assert result.exit_code == 1
    assert "ERROR: BUDGET_EXHAUSTED 30 calls spent" in result.stderr


def test_a_configuration_failure_names_the_parameter_and_exits_one(tmp_path: Path) -> None:
    """The first failure a new user meets has to say what to set."""
    result = CliRunner().invoke(
        cli, ["data", "query", "q"], env={"BLUE_DATA_PLANNER_HOME": str(tmp_path / "home")}
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("ERROR: CONFIG_MISSING ")
    assert "BLUE_DATA_REGISTRY_PATH" in result.stderr
    assert result.stdout == ""


def test_a_failed_run_still_prints_its_result_under_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed run stays machine-readable, which is what keeps the status useful."""
    a_planner_answering(
        monkeypatch, a_run_result(status=RunStatus.LLM_FAILED, failure_reason="provider down")
    )

    result = CliRunner().invoke(
        cli, ["data", "query", "q", "--format", "json"], env=a_configured_environment(tmp_path)
    )

    assert json.loads(result.stdout)["status"] == "llm_failed"
    assert result.exit_code == 1


def test_a_long_failure_reason_prints_as_one_unbroken_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rich wraps stderr at 80 columns when it is not a terminal, which would break the
    ERROR line the global constraints require to stay whole."""
    reason = "the operator refused every column it was offered, one at a time, " * 3
    a_planner_answering(
        monkeypatch, a_run_result(status=RunStatus.BUDGET_EXHAUSTED, failure_reason=reason)
    )

    result = CliRunner().invoke(cli, ["data", "query", "q"], env=a_configured_environment(tmp_path))

    assert f"ERROR: BUDGET_EXHAUSTED {reason}" in result.stderr


def a_profile_holding(home: str, **values: str) -> None:
    """Write `values` into the default profile under `home`, the way `profile config` does."""
    store = ProfileStore(home)
    store.add("default", dict(values))
    store.select("default")


def an_environment_seen_by_the_planner(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Return the mapping the process environment held when the planner was assembled."""
    seen: dict[str, str] = {}

    def _factory(settings: PlannerSettings) -> FakeDataPlanner:
        seen.update(os.environ)
        return FakeDataPlanner(a_run_result())

    monkeypatch.setattr(commands, "PLANNER_FACTORY", _factory)
    return seen


def test_a_key_held_only_in_the_profile_reaches_the_environment_a_model_call_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """litellm reads OPENAI_API_KEY from os.environ, so a key stored through `profile config`
    and never exported would leave every run of every profile unauthenticated."""
    env = a_configured_environment(tmp_path)
    del env["OPENAI_API_KEY"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    a_profile_holding(env["BLUE_DATA_PLANNER_HOME"], OPENAI_API_KEY=encode("sk-stored"))
    seen = an_environment_seen_by_the_planner(monkeypatch)

    result = CliRunner().invoke(cli, ["data", "query", "q"], env=env)

    assert result.exit_code == 0
    assert seen["OPENAI_API_KEY"] == "sk-stored"


@pytest.mark.parametrize(("cap", "shows_the_row"), [("0", False), ("5", True)])
def test_the_row_cap_the_command_was_given_decides_the_sample_rows_the_trace_shows(
    cap: str, shows_the_row: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero is a cap a user can ask for, and the profile's own cap must not stand in for it."""
    pipeline = a_planned_pipeline()
    a_planner_answering(
        monkeypatch, a_run_result(pipeline=pipeline, results=a_scan_of_one_row(pipeline))
    )

    result = CliRunner().invoke(
        cli,
        ["data", "query", "q", "--trace", "--trace-max-fragment-rows", cap],
        env=a_configured_environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "blue.select" in result.stderr  # the trace itself still prints
    assert ("Lowell" in result.stderr) is shows_the_row


def a_process_environment(monkeypatch: pytest.MonkeyPatch, variables: dict[str, str]) -> None:
    """Set each variable in the real process environment `main()` reads through `os.environ`."""
    for key, value in variables.items():
        monkeypatch.setenv(key, value)


def test_main_exits_one_on_a_configuration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click's non-standalone main() returns an exit code rather than calling sys.exit itself;
    main() has to propagate it or the process exits 0 no matter what failed."""
    monkeypatch.setattr(sys, "argv", ["blue-planner", "data", "query", "q"])
    a_process_environment(monkeypatch, {"BLUE_DATA_PLANNER_HOME": str(tmp_path / "home")})

    with pytest.raises(SystemExit) as excinfo:
        commands.main()

    assert excinfo.value.code == 1


def test_main_exits_zero_after_a_successful_run(
    tmp_path: Path, planner: FakeDataPlanner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click's non-standalone main() returns None on a clean run, and that has to become
    exit code 0 rather than a bare fall-through that leaves the process's own exit status."""
    monkeypatch.setattr(sys, "argv", ["blue-planner", "data", "query", "which cities have stores"])
    a_process_environment(monkeypatch, a_configured_environment(tmp_path))

    with pytest.raises(SystemExit) as excinfo:
        commands.main()

    assert excinfo.value.code == 0


def a_planned_pipeline() -> DataPipeline:
    """Return a pipeline with one logical plan and one matching cover, each holding one step."""
    logical_plan = LogicalPlan(
        id="l1",
        parent_id=None,
        nodes=[PipelineNode(id="n1", kind=LogicalOperator.SCAN)],
        output_node_id="n1",
    )
    physical_plan = PhysicalPlan(
        id="p1",
        parent_id=None,
        logical_plan_id="l1",
        fragments=[
            PhysicalPlanFragment(
                id="f1",
                logical_node_ids=["n1"],
                root_logical_node_id="n1",
                operator_id="blue.select",
                scan_sources={},
                instruction="scan the stores table",
                input_data=[],
                required_output_columns=[],
            )
        ],
    )
    return DataPipeline(
        logical_plans={"l1": logical_plan},
        physical_plans={"p1": physical_plan},
        current_logical_plan_id="l1",
        current_physical_plan_id="p1",
    )


def a_scan_of_one_row(pipeline: DataPipeline) -> dict[str, FragmentResult]:
    """Return how the one fragment of `a_planned_pipeline` ended, and the row it returned."""
    signed = signatures(pipeline.logical_plans["l1"], pipeline.physical_plans["p1"])
    return {
        signed["n1"]: FragmentResult(
            status=RunStatus.OK,
            result=ExecutionResult(
                status=ExecutionStatus.OK,
                operator_id="blue.select",
                output=[[{"school": "Lowell"}]],
                row_count=1,
            ),
            failure_reason=None,
        )
    }


def test_explaining_prints_the_cover_and_spends_no_operator_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of --explain is seeing the boundary before paying for it."""
    double = a_planner_answering(
        monkeypatch, a_run_result(status=RunStatus.PLANNED, pipeline=a_planned_pipeline())
    )

    result = CliRunner().invoke(
        cli,
        ["data", "query", "which cities have stores", "--explain"],
        env=a_configured_environment(tmp_path),
    )

    assert result.exit_code == 0
    assert double.operations == ["plan"]
    assert "n1" in result.stdout
    assert "blue.select" in result.stdout
