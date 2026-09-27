"""The profile component: what it stores, what it resolves, and what it hides."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from blue_data_planner_cli.commands import cli
from blue_data_planner_cli.config import CliError
from tests.doubles.environment import a_cli_home


def a_runner(tmp_path: Path) -> tuple[CliRunner, dict[str, str]]:
    """Return a runner and the environment that points this CLI's home at tmp_path."""
    return CliRunner(), {"BLUE_DATA_PLANNER_HOME": a_cli_home(tmp_path)}


def test_a_profile_is_added_listed_and_selected(tmp_path: Path) -> None:
    """The three actions that make a second environment usable have to work together."""
    runner, env = a_runner(tmp_path)

    runner.invoke(cli, ["profile", "add", "staging"], env=env)
    runner.invoke(cli, ["profile", "select", "staging"], env=env)
    listed = runner.invoke(cli, ["profile", "list"], env=env)

    assert listed.exit_code == 0
    assert "staging" in listed.output


def test_config_sets_one_parameter_and_inspect_reports_where_it_came_from(
    tmp_path: Path,
) -> None:
    """A surprising value has to be traceable to the tier that set it."""
    runner, env = a_runner(tmp_path)

    runner.invoke(
        cli, ["profile", "config", "BLUE_DATA_PLANNER_MODEL", "openai/gpt-4o-mini"], env=env
    )
    inspected = runner.invoke(cli, ["profile", "inspect"], env=env)

    assert "openai/gpt-4o-mini" in inspected.output
    assert "profile:default" in inspected.output


def test_a_secret_is_prompted_for_and_never_printed_back(tmp_path: Path) -> None:
    """A key in the shell history or on the screen is a leaked key."""
    runner, env = a_runner(tmp_path)
    env["OPENAI_API_KEY"] = ""  # so the profile, not an exported key, is the tier under test

    stored = runner.invoke(
        cli, ["profile", "config", "OPENAI_API_KEY"], input="sk-secret\n", env=env
    )
    inspected = runner.invoke(cli, ["profile", "inspect"], env=env)

    assert stored.exit_code == 0
    assert "***" in inspected.output  # the mask, so a value did reach the profile
    assert "sk-secret" not in inspected.output
    assert "sk-secret" not in (tmp_path / "home" / ".profiles").read_text()


def test_removing_a_profile_asks_first_and_confirm_answers_ahead_of_time(tmp_path: Path) -> None:
    """Removal is the one destructive action here, so it never runs on a typo."""
    runner, env = a_runner(tmp_path)
    runner.invoke(cli, ["profile", "add", "staging"], env=env)

    declined = runner.invoke(cli, ["profile", "remove", "staging"], input="n\n", env=env)
    confirmed = runner.invoke(cli, ["profile", "remove", "staging", "--confirm"], env=env)

    assert declined.exit_code != 0
    assert confirmed.exit_code == 0
    assert "staging" not in runner.invoke(cli, ["profile", "list"], env=env).output


def test_the_version_is_the_package_version(tmp_path: Path) -> None:
    """Every Blue CLI answers --version, and the answer is the release it came from."""
    runner, env = a_runner(tmp_path)

    result = runner.invoke(cli, ["--version"], env=env)

    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_selecting_a_profile_that_does_not_exist_reports_it_as_not_found(tmp_path: Path) -> None:
    """A typo at `profile select` names a profile nobody added, and has to say so."""
    runner, env = a_runner(tmp_path)

    result = runner.invoke(cli, ["profile", "select", "ghost"], env=env)

    assert isinstance(result.exception, CliError)
    assert result.exception.code == "PROFILE_NOT_FOUND"


def test_inspecting_a_profile_that_does_not_exist_reports_it_as_not_found(tmp_path: Path) -> None:
    """`profile inspect` under a name nobody added would otherwise show defaults as if they were
    that profile's values."""
    runner, env = a_runner(tmp_path)

    result = runner.invoke(cli, ["--profile", "ghost", "profile", "inspect"], env=env)

    assert isinstance(result.exception, CliError)
    assert result.exception.code == "PROFILE_NOT_FOUND"


def test_configuring_a_profile_that_does_not_exist_reports_it_as_not_found(tmp_path: Path) -> None:
    """`profile config` writes, so a name nobody added is the typo `profile select` already
    refuses, and a profile the user removed has to stay removed."""
    runner, env = a_runner(tmp_path)

    result = runner.invoke(cli, ["--profile", "ghost", "profile", "config"], env=env, input="")

    assert isinstance(result.exception, CliError)
    assert result.exception.code == "PROFILE_NOT_FOUND"
    assert "profile add ghost" in result.exception.description


def test_a_configured_format_reaches_every_command_that_prints_a_result(tmp_path: Path) -> None:
    """`--format` carries one meaning across the family, so a profile setting it has to reach
    the profile actions as well as the query that made the family declare it."""
    runner, env = a_runner(tmp_path)
    runner.invoke(cli, ["profile", "config", "BLUE_DATA_CLI_FORMAT", "json"], env=env)

    listed = runner.invoke(cli, ["profile", "list"], env=env)

    assert json.loads(listed.output)["selected"] == "default"


def test_a_value_the_cast_refuses_reports_it_as_invalid(tmp_path: Path) -> None:
    """A parameter is declared with a cast, and a value that fails it is never stored."""
    runner, env = a_runner(tmp_path)

    result = runner.invoke(
        cli, ["profile", "config", "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS", "lots"], env=env
    )

    assert isinstance(result.exception, CliError)
    assert result.exception.code == "CONFIG_INVALID"
