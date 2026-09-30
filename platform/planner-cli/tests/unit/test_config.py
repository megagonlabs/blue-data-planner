"""What a profile supplies, and the planner those settings assemble."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs]
    ConfigResolver,
)
from blue_data_planner import PlannerSettings, RunLimits

from blue_data_planner_cli import config
from blue_data_planner_cli.config import (
    CliError,
    ResolvedSettings,
    assemble_planner,
    build_resolver,
    cli_home,
    config_spec,
    resolve_settings,
    resolved_environment,
)
from tests.doubles.environment import a_cli_home, a_profile_holding, a_registry_file


def test_the_profile_spec_declares_the_library_defaults() -> None:
    """Two files name the same defaults, so a test is what keeps them from drifting apart."""
    spec = json.loads(Path(config.__file__).with_name("profile_spec.json").read_text())
    defaults = PlannerSettings(registry_path=Path("registry.json"))
    declared = {
        "BLUE_DATA_PLANNER_MODEL": defaults.model,
        "BLUE_DATA_PLANNER_REASONING_EFFORT": defaults.reasoning_effort,
        "BLUE_DATA_PLANNER_OPERATORS": ",".join(defaults.operator_suites),
        "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT": defaults.lotus_reasoning_effort,
        "BLUE_DATA_PLANNER_LOTUS_MAX_ROWS": defaults.lotus_max_rows,
        "BLUE_DATA_PLANNER_ENABLE_NL2TABLE": defaults.enable_nl2table,
        "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS": defaults.limits.operator_calls,
        "BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS": defaults.limits.failure_replans,
        "BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS": defaults.limits.control_replans,
    }
    assert {name: spec[name]["default"] for name in declared} == declared


def test_the_spec_declares_the_planner_parameters_beside_the_family_wide_ones() -> None:
    """One spec answers for both, so a CLI parameter and a family parameter resolve alike."""
    spec = config_spec()

    assert spec["BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS"].cast == "int"
    assert spec["BLUE_DATA_PLANNER_LOTUS_MODEL"].inherits == ()
    assert spec["BLUE_DATA_PLANNER_REASONING_EFFORT"].default == "low"
    assert spec["BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT"].default == "none"
    assert spec["OPENAI_API_KEY"].is_secret
    assert spec["BLUE_DATA_CLI_FORMAT"].default == "table"


def test_every_bound_a_profile_carries_defaults_to_the_one_the_library_holds() -> None:
    """A profile and `RunLimits` name the same bounds, so a default changed in one and not the
    other leaves the CLI and a library caller disagreeing about the same run."""
    limits = RunLimits()

    declared = {
        one.name.removeprefix("BLUE_DATA_PLANNER_MAX_").lower(): one.default
        for one in config_spec()
        if one.name.startswith("BLUE_DATA_PLANNER_MAX_")
    }

    # Three bounds a run reports back, so an empty reading would pass for the wrong reason.
    assert len(declared) == 3
    assert declared == {field: getattr(limits, field) for field in declared}


def test_the_home_directory_follows_the_environment_when_it_names_one() -> None:
    """A test run, and anyone keeping profiles elsewhere, relocate the directory this way."""
    assert cli_home({}) == "~/.blue-planner"
    assert cli_home({"BLUE_DATA_PLANNER_HOME": "/tmp/planner"}) == "/tmp/planner"


def a_resolver_over(tmp_path: Path, **values: str) -> ConfigResolver:
    """Return a resolver whose profile holds `values` and whose home is under tmp_path."""
    home = a_cli_home(tmp_path)
    a_profile_holding(home, BLUE_DATA_REGISTRY_PATH=str(a_registry_file(tmp_path)), **values)
    return build_resolver(profile=None, environ={"BLUE_DATA_PLANNER_HOME": home})


def a_captured_build(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace build_data_planner_from_settings with a double recording what the CLI handed it."""
    captured: dict[str, Any] = {}

    def _build(settings: Any, **arguments: Any) -> Any:
        captured["settings"] = settings
        captured.update(arguments)
        return None

    monkeypatch.setattr(config, "build_data_planner_from_settings", _build)
    return captured


def test_the_bounds_a_profile_carries_reach_run_limits(tmp_path: Path) -> None:
    """A profile is where a deployment raises a bound a run reports back, so each has to arrive
    intact, and a bound no parameter names keeps its RunLimits default."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS="7")

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.planner.limits.operator_calls == 7
    # no parameter names it, so the default stands
    assert settings.planner.limits.plan_corrections == 5


@pytest.mark.parametrize("configured", ["low", "none", "off"])
def test_the_configured_reasoning_effort_reaches_planner_settings_unchanged(
    configured: str, tmp_path: Path
) -> None:
    """Translating "off" into the omitted parameter is the library's job, done when the planner
    is built; resolve_settings passes every documented spelling through as-is."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_REASONING_EFFORT=configured)

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.planner.reasoning_effort == configured


@pytest.mark.parametrize(
    "parameter",
    ["BLUE_DATA_PLANNER_REASONING_EFFORT", "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT"],
)
def test_a_reasoning_effort_outside_the_documented_values_names_the_parameter_that_set_it(
    parameter: str, tmp_path: Path
) -> None:
    """A value the provider refuses fails every model call the run makes, so it is caught where
    it is read, reported under the parameter name a profile sets rather than the library's own
    field name."""
    resolver = a_resolver_over(tmp_path, **{parameter: "maximum"})

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

    assert raised.value.code == "CONFIG_INVALID"
    assert raised.value.description.startswith(parameter)
    assert "maximum" in raised.value.description


def test_an_unset_lotus_reasoning_effort_resolves_to_none_regardless_of_the_main_setting(
    tmp_path: Path,
) -> None:
    """The default stands on its own: it does not inherit BLUE_DATA_PLANNER_REASONING_EFFORT."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_REASONING_EFFORT="high")

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.planner.lotus_reasoning_effort == "none"


def test_a_command_option_outranks_the_profile(tmp_path: Path) -> None:
    """The documented order is option, environment, profile, default."""
    other = tmp_path / "other.json"
    other.write_text("[]")
    resolver = a_resolver_over(tmp_path)

    settings: ResolvedSettings = resolve_settings(
        resolver, registry=str(other), output_format="json"
    )

    assert settings.planner.registry_path == other
    assert settings.output_format == "json"


def test_an_unset_lotus_model_is_left_to_follow_whatever_model_the_planner_resolves(
    tmp_path: Path,
) -> None:
    """With no LOTUS model of its own, the setting stays empty, so LOTUS runs on the planner's
    model wherever that came from: --model here, BLUE_DATA_LLM_MODEL below."""
    resolver = a_resolver_over(tmp_path)

    settings: ResolvedSettings = resolve_settings(resolver, model="openai/gpt-4.1")

    assert settings.planner.model == "openai/gpt-4.1"
    assert settings.planner.lotus_model is None


def test_the_family_model_reaches_the_planner_when_no_planner_model_is_set(
    tmp_path: Path,
) -> None:
    """BLUE_DATA_PLANNER_MODEL falls back to BLUE_DATA_LLM_MODEL, and LOTUS follows the planner."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_LLM_MODEL="openai/gpt-6-luna")

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.planner.model == "openai/gpt-6-luna"
    assert settings.planner.lotus_model is None


def test_the_model_option_leaves_a_set_lotus_model_alone(tmp_path: Path) -> None:
    """--model overrides the planner's model only; a LOTUS model set on its own is kept."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_LOTUS_MODEL="openai/gpt-4.1")

    settings: ResolvedSettings = resolve_settings(resolver, model="openai/gpt-6-luna")

    assert settings.planner.model == "openai/gpt-6-luna"
    assert settings.planner.lotus_model == "openai/gpt-4.1"


def test_a_profile_set_lotus_model_resolves_apart_from_the_planners_own(tmp_path: Path) -> None:
    """Setting LOTUS away from the planner's model is what the parameter is for, so the two
    settings resolve side by side."""
    resolver = a_resolver_over(
        tmp_path,
        BLUE_DATA_PLANNER_MODEL="openai/gpt-6-luna",
        BLUE_DATA_PLANNER_LOTUS_MODEL="openai/gpt-4.1",
    )

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.planner.model == "openai/gpt-6-luna"
    assert settings.planner.lotus_model == "openai/gpt-4.1"


def test_an_unknown_operator_suite_is_a_configuration_error(tmp_path: Path) -> None:
    """The library refuses a name no suite registers, and a configured name that no suite
    answers to is the user's mistake to be told about rather than a planner defect."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_OPERATORS="builtin,sqlmagic")
    settings = resolve_settings(resolver)

    with pytest.raises(CliError) as raised:
        assemble_planner(settings)

    assert raised.value.code == "CONFIG_INVALID"
    assert "sqlmagic" in raised.value.description


def test_an_output_format_outside_the_two_is_a_configuration_error(tmp_path: Path) -> None:
    """A name the printer does not know would otherwise print a table and report nothing."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_CLI_FORMAT="yaml")

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

    assert raised.value.code == "CONFIG_INVALID"
    assert "yaml" in raised.value.description
    assert "table" in raised.value.description and "json" in raised.value.description


def test_a_registry_path_naming_no_file_is_reported_before_a_run_starts(tmp_path: Path) -> None:
    """An unreadable registry is a configuration failure, not a run that ends badly."""
    resolver = a_resolver_over(tmp_path)
    settings = resolve_settings(resolver, registry=str(tmp_path / "missing.json"))

    with pytest.raises(CliError) as raised:
        assemble_planner(settings)

    assert raised.value.code == "REGISTRY_UNREADABLE"


def test_a_registry_that_does_not_parse_fails_before_a_planner_is_built(tmp_path: Path) -> None:
    """A path that names a file is not yet a file the registry can read."""
    unparsable = tmp_path / "broken.json"
    unparsable.write_text("not json at all")
    settings = resolve_settings(a_resolver_over(tmp_path), registry=str(unparsable))

    with pytest.raises(CliError) as raised:
        assemble_planner(settings)

    assert raised.value.code == "REGISTRY_UNREADABLE"


def test_the_settings_a_profile_resolved_reach_the_library_under_the_cli_client_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter resolves and hands off: what a profile set arrives as the PlannerSettings the
    library builds from, under the CLI's own LLM client name. Building the planner from those
    settings, suites and all, is the library's job."""
    captured = a_captured_build(monkeypatch)
    resolver = a_resolver_over(
        tmp_path,
        BLUE_DATA_PLANNER_ENABLE_NL2TABLE="true",
        BLUE_DATA_PLANNER_LOTUS_MAX_ROWS="10",
    )

    assemble_planner(resolve_settings(resolver))

    assert captured["settings"].enable_nl2table is True
    assert captured["settings"].lotus_max_rows == 10
    assert captured["llm_client_name"] == "blue_planner"


def test_a_missing_required_parameter_names_what_to_set(tmp_path: Path) -> None:
    """The one failure a first-time user meets has to say how to fix itself."""
    home = tmp_path / "empty-home"
    resolver = build_resolver(profile=None, environ={"BLUE_DATA_PLANNER_HOME": str(home)})

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

    assert raised.value.code == "CONFIG_INVALID"
    assert "BLUE_DATA_REGISTRY_PATH" in raised.value.description


def test_a_stored_value_that_fails_its_cast_is_a_configuration_error_when_the_profile_is_applied(
    tmp_path: Path,
) -> None:
    """export resolves every parameter, so one bad stored value has to reach the ERROR line
    rather than the underlying ConfigError reaching the user as a traceback."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS="many")

    with pytest.raises(CliError) as raised:
        resolved_environment(resolver)

    assert raised.value.code == "CONFIG_INVALID"
    assert "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS" in raised.value.description
