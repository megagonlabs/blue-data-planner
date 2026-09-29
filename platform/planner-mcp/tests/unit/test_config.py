"""Configuration comes from the environment, and only from the environment.

The server shares parameter names with the `blue-planner` CLI but none of its machinery: it must
not read `~/.blue-planner/.profiles`, so a developer's CLI profile cannot silently reconfigure a
running server.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from blue_data_planner import PlannerSettings
from blue_data_planner.types import InvalidSettingError, RunLimits

import blue_data_planner_mcp.config as config

# Read as a file rather than imported, so the check needs nothing from the CLI package.
_CLI_PROFILE_SPEC_PATH: Path = (
    Path(__file__).parents[3]
    / "planner-cli"
    / "src"
    / "blue_data_planner_cli"
    / "profile_spec.json"
)
_A_REGISTRY_ONLY_ENVIRONMENT: dict[str, str] = {"BLUE_DATA_REGISTRY_PATH": "/srv/registry.json"}


def _cli_profile_parameters() -> dict[str, dict[str, Any]]:
    return json.loads(_CLI_PROFILE_SPEC_PATH.read_text())


def a_value_other_than_the_default(parameter: dict[str, Any]) -> str:
    """A value the server accepts for this parameter and that differs from its default."""
    cast = parameter["cast"]
    if cast == "file":
        return "/elsewhere/registry.json"
    if cast == "int":
        return str(parameter["default"] + 1)
    if cast == "bool":
        return str(not parameter["default"]).lower()
    # A reasoning effort, and also a string no str parameter defaults to.
    return "high"


def test_an_environment_naming_only_a_registry_leaves_every_other_value_at_its_default():
    """The server declares no defaults of its own, so it cannot drift from the library's."""
    settings = config.planner_settings({"BLUE_DATA_REGISTRY_PATH": "/srv/registry.json"})
    assert settings == PlannerSettings(registry_path=Path("/srv/registry.json"))


def test_every_setting_the_environment_names_reaches_the_planner():
    settings = config.planner_settings(
        {
            "BLUE_DATA_REGISTRY_PATH": "/srv/registry.json",
            "BLUE_DATA_PLANNER_MODEL": "openai/gpt-4o",
            "BLUE_DATA_PLANNER_REASONING_EFFORT": "high",
            "BLUE_DATA_PLANNER_OPERATORS": "builtin, blue",
            "BLUE_DATA_PLANNER_LOTUS_MODEL": "openai/gpt-4o-mini",
            "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT": "minimal",
            "BLUE_DATA_PLANNER_LOTUS_MAX_ROWS": "120",
            "BLUE_DATA_PLANNER_ENABLE_NL2TABLE": "yes",
            "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS": "7",
            "BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS": "1",
            "BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS": "3",
        }
    )
    assert settings == PlannerSettings(
        registry_path=Path("/srv/registry.json"),
        model="openai/gpt-4o",
        reasoning_effort="high",
        operator_suites=("builtin", "blue"),
        lotus_model="openai/gpt-4o-mini",
        lotus_reasoning_effort="minimal",
        lotus_max_rows=120,
        enable_nl2table=True,
        limits=RunLimits(operator_calls=7, failure_replans=1, control_replans=3),
    )


@pytest.mark.parametrize("name", sorted(_cli_profile_parameters()))
def test_every_parameter_the_cli_profile_names_configures_the_server(name: str):
    """The server promises the CLI's variable names, so a rename on either side fails here."""
    value = a_value_other_than_the_default(_cli_profile_parameters()[name])
    settings = config.planner_settings(_A_REGISTRY_ONLY_ENVIRONMENT | {name: value})
    assert settings != config.planner_settings(_A_REGISTRY_ONLY_ENVIRONMENT)


def test_the_family_wide_model_is_read_where_the_planner_names_none():
    settings = config.planner_settings(
        {"BLUE_DATA_REGISTRY_PATH": "/srv/registry.json", "BLUE_DATA_LLM_MODEL": "openai/gpt-4o"}
    )
    assert settings.model == "openai/gpt-4o"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BLUE_DATA_PLANNER_LOTUS_MAX_ROWS", "many"),
        ("BLUE_DATA_PLANNER_ENABLE_NL2TABLE", "perhaps"),
    ],
)
def test_a_value_the_server_cannot_read_is_refused_by_name(name: str, value: str):
    """A server that guessed would plan under settings nobody chose."""
    with pytest.raises(InvalidSettingError, match=name):
        config.planner_settings({"BLUE_DATA_REGISTRY_PATH": "/srv/registry.json", name: value})


def test_an_unset_registry_path_is_refused():
    with pytest.raises(InvalidSettingError, match="BLUE_DATA_REGISTRY_PATH"):
        config.planner_settings({})


def test_a_cli_profile_does_not_configure_the_server(tmp_path: Path):
    """A .profiles file on disk is inert here — only the environment counts."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profiles").write_text(
        "[default]\nBLUE_DATA_PLANNER_MODEL = openai/from-the-profile\n"
    )
    (home / ".selected.profile").write_text("default")
    settings = config.planner_settings(
        {
            "BLUE_DATA_REGISTRY_PATH": "/srv/registry.json",
            "BLUE_DATA_PLANNER_HOME": str(home),
        }
    )
    assert settings.model == PlannerSettings(registry_path=Path("x")).model


def test_the_server_does_not_import_the_cli():
    """The package depends only on blue-data-planner, so it builds and ships on its own.

    Checked on the import statements rather than the file text, since a comment naming the CLI
    is exactly the point.
    """
    imported: set[str] = set()
    for path in Path(config.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not [name for name in imported if name.startswith("blue_data_planner_cli")]
