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
from blue_data_planner import settings as settings_module
from click.testing import CliRunner

from blue_data_planner_cli import commands
from blue_data_planner_cli.commands import cli
from blue_data_planner_cli.config import ResolvedSettings
from tests.doubles.components import (
    FakeDataPlanner,
    a_planning_run,
    a_run_over_one_cover_of_two_fragments,
    a_run_result,
)
from tests.doubles.environment import a_configured_environment, a_profile_holding


def a_planner_answering(monkeypatch: pytest.MonkeyPatch, result: RunResult) -> FakeDataPlanner:
    """Replace the assembled planner with a double returning `result`, so no test reaches a
    model or assembles a planner over a real registry."""
    double = FakeDataPlanner(result)

    def _assemble(settings: ResolvedSettings) -> FakeDataPlanner:
        return double

    monkeypatch.setattr(commands, "assemble_planner", _assemble)
    return double


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> FakeDataPlanner:
    """Replace the assembled planner with a double, so no command reaches a model."""
    return a_planner_answering(monkeypatch, a_run_result(answer=[[{"school": "Lowell"}]]))


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


@pytest.mark.parametrize("profile", [False, True])
def test_a_query_opens_the_shared_blue_data_store_from_environment_or_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool
) -> None:
    from blue_data.config import build_store  # pyright: ignore[reportMissingTypeStubs]
    from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
        DataRegistry,
    )

    path = tmp_path / "shared.json"
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "file")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_FILE", str(path))
    writer: Any = DataRegistry(store=build_store())
    writer.register_record("shared", "source")
    monkeypatch.delenv("BLUE_DATA_REGISTRY_STORE")
    monkeypatch.delenv("BLUE_DATA_REGISTRY_FILE")
    captured: list[str] = []

    def build(registry: Any, **options: Any) -> FakeDataPlanner:
        captured.extend(record["name"] for record in registry.list_records(record_type="source"))
        return FakeDataPlanner(a_run_result(answer=[[{"source": "shared"}]]))

    monkeypatch.setattr(settings_module, "build_data_planner", build)
    home = str(tmp_path / "home")
    shared = {"BLUE_DATA_REGISTRY_STORE": "file", "BLUE_DATA_REGISTRY_FILE": str(path)}
    env = {"BLUE_DATA_PLANNER_HOME": home, "BLUE_DATA_PLANNER_OPERATORS": "builtin"}
    if profile:
        a_profile_holding(home, **shared)
    else:
        env.update(shared)
    result = CliRunner().invoke(cli, ["data", "query", "q"], env=env)
    assert result.exit_code == 0, result.output
    assert captured == ["shared"]


def test_json_output_holds_nothing_but_the_result(tmp_path: Path, planner: FakeDataPlanner) -> None:
    """A script parsing stdout must not meet a progress line or a summary."""
    result = CliRunner().invoke(
        cli,
        ["data", "query", "which cities have stores", "--format", "json"],
        env=a_configured_environment(tmp_path),
    )

    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["status"] == "ok"


def test_the_log_prints_only_when_it_is_asked_for_and_never_on_standard_output(
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


def test_a_failure_reason_holding_square_brackets_is_logged_whole(
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
    assert "operator_failed" in result.stderr  # the summary still follows the log


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
    assert result.stderr.count("ERROR:") == 1


def test_a_configuration_failure_names_the_parameter_and_exits_one(tmp_path: Path) -> None:
    """The first failure a new user meets has to say what to set."""
    result = CliRunner().invoke(
        cli, ["data", "query", "q"], env={"BLUE_DATA_PLANNER_HOME": str(tmp_path / "home")}
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("ERROR: CONFIG_INVALID ")
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


def an_environment_seen_by_the_planner(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Return the mapping the process environment held when the planner was assembled."""
    seen: dict[str, str] = {}

    def _assemble(settings: ResolvedSettings) -> FakeDataPlanner:
        seen.update(os.environ)
        return FakeDataPlanner(a_run_result())

    monkeypatch.setattr(commands, "assemble_planner", _assemble)
    return seen


def test_a_named_profile_that_does_not_exist_stops_the_query_as_not_found(
    tmp_path: Path, planner: FakeDataPlanner
) -> None:
    """A typo in --profile names a profile nobody added, so the query stops rather than running
    on no profile at all."""
    result = CliRunner().invoke(
        cli, ["--profile", "ghost", "data", "query", "q"], env=a_configured_environment(tmp_path)
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("ERROR: PROFILE_NOT_FOUND ")
    assert "profile add ghost" in result.stderr
    assert planner.requests == []


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


def test_the_log_limit_on_the_command_line_outranks_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every setting follows one order — option, environment, profile, default — and the cap on
    the log's own lines is a setting like any other."""
    a_planner_answering(monkeypatch, a_run_over_one_cover_of_two_fragments())
    env = a_configured_environment(tmp_path)
    a_profile_holding(env["BLUE_DATA_PLANNER_HOME"], BLUE_DATA_CLI_LOG_LIMIT="200")
    runner = CliRunner()

    profiled = runner.invoke(cli, ["data", "query", "q", "--log"], env=env)
    overridden = runner.invoke(cli, ["data", "query", "q", "--log", "--log-limit", "20"], env=env)

    assert "physical plan 1" in profiled.stderr  # the log itself still prints
    assert "Alameda" in profiled.stderr
    assert "Alameda" not in overridden.stderr


class RaisingDataPlanner(FakeDataPlanner):
    """A planner whose run fails with an exception from outside the library's tree."""

    def run(self, request: str) -> RunResult:
        raise ValueError("boom")


def a_planner_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the assembled planner with one whose run raises a ValueError."""
    double = RaisingDataPlanner(a_run_result())

    def _assemble(settings: ResolvedSettings) -> RaisingDataPlanner:
        return double

    monkeypatch.setattr(commands, "assemble_planner", _assemble)


def test_an_unexpected_exception_in_a_query_is_one_error_line_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defect reaches the user as the family's error line, not as a stack trace."""
    a_planner_raising(monkeypatch)

    result = CliRunner().invoke(cli, ["data", "query", "q"], env=a_configured_environment(tmp_path))

    assert result.exit_code == 1
    assert "ERROR: INTERNAL_ERROR ValueError: boom" in result.stderr
    assert "Traceback" not in result.stderr


def test_the_log_flag_adds_the_traceback_of_an_unexpected_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--log is the switch that asks for diagnostics, so it is what shows where a defect was."""
    a_planner_raising(monkeypatch)

    result = CliRunner().invoke(
        cli, ["data", "query", "q", "--log"], env=a_configured_environment(tmp_path)
    )

    assert result.exit_code == 1
    assert "ERROR: INTERNAL_ERROR ValueError: boom" in result.stderr
    assert "Traceback" in result.stderr


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


def test_main_reports_an_unexpected_exception_in_any_command_as_an_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command without --log still reports a defect as one error line and exit code 1."""
    monkeypatch.setattr(sys, "argv", ["blue-planner", "profile", "list"])
    a_process_environment(monkeypatch, {"BLUE_DATA_PLANNER_HOME": str(tmp_path / "home")})

    def _fail(self: ProfileStore) -> list[str]:
        raise RuntimeError("disk gone")

    monkeypatch.setattr(ProfileStore, "list_profiles", _fail)

    with pytest.raises(SystemExit) as excinfo:
        commands.main()

    assert excinfo.value.code == 1
    stderr = capsys.readouterr().err
    assert "ERROR: INTERNAL_ERROR RuntimeError: disk gone" in stderr
    assert "Traceback" not in stderr


def a_query_missing_its_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], dict[str, str]]:
    """Return a query whose environment names no registry, so it fails as CONFIG_INVALID."""
    return ["data", "query", "q"], {"BLUE_DATA_PLANNER_HOME": str(tmp_path / "home")}


def a_query_naming_a_missing_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], dict[str, str]]:
    """Return a query naming a profile nobody added, so it fails as PROFILE_NOT_FOUND."""
    return ["--profile", "ghost", "data", "query", "q"], a_configured_environment(tmp_path)


def a_query_whose_planner_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], dict[str, str]]:
    """Return a query whose planner raises a ValueError, so it fails as INTERNAL_ERROR."""
    a_planner_raising(monkeypatch)
    return ["data", "query", "q"], a_configured_environment(tmp_path)


@pytest.mark.parametrize(
    ("a_failing_query", "code"),
    [
        (a_query_missing_its_registry, "CONFIG_INVALID"),
        (a_query_naming_a_missing_profile, "PROFILE_NOT_FOUND"),
        (a_query_whose_planner_raises, "INTERNAL_ERROR"),
    ],
)
def test_under_json_an_error_before_any_run_is_a_document_on_standard_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a_failing_query: Any, code: str
) -> None:
    """A caller that asked for JSON parses standard output, so every failure leaves one
    document there, while the error line stays on standard error."""
    arguments, env = a_failing_query(tmp_path, monkeypatch)

    result = CliRunner().invoke(cli, [*arguments, "--format", "json"], env=env)

    document = json.loads(result.stdout)
    assert document["code"] == code
    assert f"ERROR: {code} {document['error']}" in result.stderr
    assert result.exit_code == 1


@pytest.mark.parametrize("where", ["environment", "profile"])
def test_a_format_set_outside_the_command_line_decides_how_an_error_is_written(
    tmp_path: Path, where: str
) -> None:
    """A script that sets its format once, in the environment or a profile, still reads its
    errors as documents."""
    home = str(tmp_path / "home")
    env = {"BLUE_DATA_PLANNER_HOME": home}
    if where == "environment":
        env["BLUE_DATA_CLI_FORMAT"] = "json"
    else:
        a_profile_holding(home, BLUE_DATA_CLI_FORMAT="json")

    result = CliRunner().invoke(cli, ["data", "query", "q"], env=env)

    assert json.loads(result.stdout)["code"] == "CONFIG_INVALID"


def test_main_writes_an_error_in_the_format_the_failing_command_was_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command other than data query reports through main, which has to know the format."""
    monkeypatch.setattr(sys, "argv", ["blue-planner", "profile", "list", "--format", "json"])
    a_process_environment(monkeypatch, {"BLUE_DATA_PLANNER_HOME": str(tmp_path / "home")})

    def _fail(self: ProfileStore) -> list[str]:
        raise RuntimeError("disk gone")

    monkeypatch.setattr(ProfileStore, "list_profiles", _fail)

    with pytest.raises(SystemExit):
        commands.main()

    assert json.loads(capsys.readouterr().out) == {
        "error": "RuntimeError: disk gone",
        "code": "INTERNAL_ERROR",
    }


def test_a_command_without_format_writes_its_error_as_the_plain_line_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The environment's format belongs to commands that print a result, so an interactive
    command ignores it."""
    monkeypatch.setattr(sys, "argv", ["blue-planner", "profile", "select", "ghost"])
    a_process_environment(
        monkeypatch,
        {"BLUE_DATA_PLANNER_HOME": str(tmp_path / "home"), "BLUE_DATA_CLI_FORMAT": "json"},
    )

    with pytest.raises(SystemExit):
        commands.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR: PROFILE_NOT_FOUND ")


def test_explaining_prints_the_cover_and_spends_no_operator_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of --explain is seeing the boundary before paying for it."""
    double = a_planner_answering(monkeypatch, a_planning_run())

    result = CliRunner().invoke(
        cli,
        ["data", "query", "which cities have stores", "--explain"],
        env=a_configured_environment(tmp_path),
    )

    assert result.exit_code == 0
    assert double.operations == ["plan"]
    assert "n1" in result.stdout
    assert "builtin.source_scan" in result.stdout
