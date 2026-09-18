"""What a profile supplies, and the planner those settings assemble."""

from __future__ import annotations

from pathlib import Path

import pytest
from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs]
    ConfigResolver,
    ProfileStore,
)

from blue_data_planner_cli.config import (
    CliError,
    PlannerSettings,
    assemble_planner,
    build_resolver,
    cli_home,
    config_spec,
    resolve_settings,
)


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


def a_resolver_over(tmp_path: Path, **values: str) -> ConfigResolver:
    """Return a resolver whose profile holds `values` and whose home is under tmp_path."""
    home = tmp_path / "home"
    store = ProfileStore(home)
    store.add("default", {"BLUE_DATA_REGISTRY_PATH": str(a_registry_file(tmp_path)), **values})
    store.select("default")
    return build_resolver(profile=None, environ={"BLUE_DATA_PLANNER_HOME": str(home)})


def a_registry_file(tmp_path: Path) -> Path:
    """Return the path of an empty but readable registry file."""
    path = tmp_path / "registry.json"
    path.write_text("[]")
    return path


def test_the_six_bounds_reach_run_limits(tmp_path: Path) -> None:
    """A profile is where a deployment raises a bound, so each one has to arrive intact."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS="7")

    settings: PlannerSettings = resolve_settings(resolver)

    assert settings.limits.operator_calls == 7
    assert settings.limits.plan_corrections == 5  # the unset bounds keep the RunLimits default


def test_a_command_option_outranks_the_profile(tmp_path: Path) -> None:
    """The documented order is option, environment, profile, default."""
    other = tmp_path / "other.json"
    other.write_text("[]")
    resolver = a_resolver_over(tmp_path)

    settings: PlannerSettings = resolve_settings(resolver, registry=str(other))

    assert settings.registry_path == other


def test_an_unknown_operator_suite_is_a_configuration_error(tmp_path: Path) -> None:
    """A name outside the three would otherwise register nothing and fail much later."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_OPERATORS="builtin,sqlmagic")

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

    assert raised.value.code == "CONFIG_INVALID"
    assert "sqlmagic" in raised.value.description


def test_a_registry_path_naming_no_file_is_reported_before_a_run_starts(tmp_path: Path) -> None:
    """An unreadable registry is a configuration failure, not a run that ends badly."""
    resolver = a_resolver_over(tmp_path)

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver, registry=str(tmp_path / "missing.json"))

    assert raised.value.code == "REGISTRY_UNREADABLE"


def test_a_registry_that_does_not_parse_fails_before_a_planner_is_built(tmp_path: Path) -> None:
    """A path that names a file is not yet a file the registry can read."""
    unparsable = tmp_path / "broken.json"
    unparsable.write_text("not json at all")
    settings = resolve_settings(a_resolver_over(tmp_path), registry=str(unparsable))

    with pytest.raises(CliError) as raised:
        assemble_planner(settings)

    assert raised.value.code == "REGISTRY_UNREADABLE"


def test_a_missing_required_parameter_names_what_to_set(tmp_path: Path) -> None:
    """The one failure a first-time user meets has to say how to fix itself."""
    home = tmp_path / "empty-home"
    resolver = build_resolver(profile=None, environ={"BLUE_DATA_PLANNER_HOME": str(home)})

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

    assert raised.value.code == "CONFIG_MISSING"
    assert "BLUE_DATA_REGISTRY_PATH" in raised.value.description
