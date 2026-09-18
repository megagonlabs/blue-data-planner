"""What a profile supplies, and the planner those settings assemble."""

from __future__ import annotations

from blue_data_planner_cli.config import cli_home, config_spec


def test_the_spec_declares_the_planner_parameters_beside_the_family_wide_ones() -> None:
    """One spec answers for both, so a CLI parameter and a family parameter resolve alike."""
    spec = config_spec()

    assert spec["BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS"].default == 30
    assert spec["BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS"].cast == "int"
    assert spec["BLUE_DATA_PLANNER_LOTUS_MODEL"].inherits == ("BLUE_DATA_PLANNER_MODEL",)
    assert spec["OPENAI_API_KEY"].is_secret
    assert spec["BLUE_DATA_CLI_FORMAT"].default == "table"


def test_the_home_directory_follows_the_environment_when_it_names_one() -> None:
    """A test run, and anyone keeping profiles elsewhere, relocate the directory this way."""
    assert cli_home({}) == "~/.blue-planner"
    assert cli_home({"BLUE_DATA_PLANNER_HOME": "/tmp/planner"}) == "/tmp/planner"
