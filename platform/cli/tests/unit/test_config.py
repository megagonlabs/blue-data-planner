"""What a profile supplies, and the planner those settings assemble."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs]
    ConfigResolver,
)
from blue_data_planner import RunLimits
from blue_data_planner.operators.adapters.lotus import LotusSuite
from blue_data_planner.operators.builtin import BuiltinSuite

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


def test_the_spec_declares_the_planner_parameters_beside_the_family_wide_ones() -> None:
    """One spec answers for both, so a CLI parameter and a family parameter resolve alike."""
    spec = config_spec()

    assert spec["BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS"].cast == "int"
    assert spec["BLUE_DATA_PLANNER_LOTUS_MODEL"].inherits == ("BLUE_DATA_PLANNER_MODEL",)
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
    """Replace build_data_planner with a double recording what the CLI handed it."""
    captured: dict[str, Any] = {}

    def _build(data_registry: Any, **arguments: Any) -> Any:
        captured.update(arguments)
        return None

    monkeypatch.setattr(config, "build_data_planner", _build)
    return captured


def test_the_bounds_a_profile_carries_reach_run_limits(tmp_path: Path) -> None:
    """A profile is where a deployment raises a bound a run reports back, so each has to arrive
    intact, and a bound no parameter names keeps its RunLimits default."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS="7")

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.limits.operator_calls == 7
    assert settings.limits.plan_corrections == 5  # no parameter names it, so the default stands


@pytest.mark.parametrize(
    ("configured", "resolved"), [("low", "low"), ("none", "none"), ("off", None)]
)
def test_off_is_the_one_spelling_that_leaves_the_reasoning_effort_out(
    configured: str, resolved: str | None, tmp_path: Path
) -> None:
    """ "none" is a real litellm value the provider still receives; "off" is how a user on a model
    that rejects the field says it must not be sent, and None is how the library reads that."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_REASONING_EFFORT=configured)

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.reasoning_effort == resolved


@pytest.mark.parametrize(
    "parameter",
    ["BLUE_DATA_PLANNER_REASONING_EFFORT", "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT"],
)
def test_a_reasoning_effort_outside_the_documented_values_is_a_configuration_error(
    parameter: str, tmp_path: Path
) -> None:
    """A value the provider refuses fails every model call the run makes, so it is caught where
    it is read rather than partway into a request."""
    resolver = a_resolver_over(tmp_path, **{parameter: "maximum"})

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

    assert raised.value.code == "CONFIG_INVALID"
    assert "maximum" in raised.value.description


def test_an_unset_lotus_reasoning_effort_resolves_to_none_regardless_of_the_main_setting(
    tmp_path: Path,
) -> None:
    """The default stands on its own: it does not inherit BLUE_DATA_PLANNER_REASONING_EFFORT."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_REASONING_EFFORT="high")

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.lotus_reasoning_effort == "none"


def test_a_command_option_outranks_the_profile(tmp_path: Path) -> None:
    """The documented order is option, environment, profile, default."""
    other = tmp_path / "other.json"
    other.write_text("[]")
    resolver = a_resolver_over(tmp_path)

    settings: ResolvedSettings = resolve_settings(
        resolver, registry=str(other), output_format="json"
    )

    assert settings.registry_path == other
    assert settings.output_format == "json"


def test_the_model_option_reaches_the_lotus_suite_as_well_as_the_planner(tmp_path: Path) -> None:
    """--model names the model every component uses, and BLUE_DATA_PLANNER_LOTUS_MODEL inherits
    BLUE_DATA_PLANNER_MODEL, so its own resolution never sees the option."""
    resolver = a_resolver_over(tmp_path)

    settings: ResolvedSettings = resolve_settings(resolver, model="openai/gpt-4o-mini")

    assert settings.model == "openai/gpt-4o-mini"
    assert settings.lotus_model == "openai/gpt-4o-mini"


def test_a_profile_set_lotus_model_stands_apart_from_the_planners_own(tmp_path: Path) -> None:
    """Setting LOTUS away from the planner's model is what the parameter is for."""
    resolver = a_resolver_over(
        tmp_path,
        BLUE_DATA_PLANNER_MODEL="openai/gpt-4o",
        BLUE_DATA_PLANNER_LOTUS_MODEL="openai/gpt-4o-mini",
    )

    settings: ResolvedSettings = resolve_settings(resolver)

    assert settings.model == "openai/gpt-4o"
    assert settings.lotus_model == "openai/gpt-4o-mini"


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


def test_what_a_profile_resolved_is_what_the_library_is_handed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter resolves and translates, so every value a run is built on has to arrive as the
    profile left it, the suites and their own options included."""
    captured = a_captured_build(monkeypatch)
    resolver = a_resolver_over(
        tmp_path,
        BLUE_DATA_PLANNER_OPERATORS="builtin,lotus",
        BLUE_DATA_PLANNER_MODEL="openai/gpt-4o",
        BLUE_DATA_PLANNER_REASONING_EFFORT="off",
        BLUE_DATA_PLANNER_ENABLE_NL2TABLE="true",
        BLUE_DATA_PLANNER_LOTUS_MAX_ROWS="10",
    )
    settings = resolve_settings(resolver)

    assemble_planner(settings)

    assert captured["model"] == "openai/gpt-4o"
    assert captured["reasoning_effort"] is None
    assert captured["limits"] == settings.limits
    assert captured["suites"] == [
        BuiltinSuite(with_nl2table=True),
        LotusSuite(model="openai/gpt-4o", max_rows=10, reasoning_effort="none"),
    ]


def test_a_missing_required_parameter_names_what_to_set(tmp_path: Path) -> None:
    """The one failure a first-time user meets has to say how to fix itself."""
    home = tmp_path / "empty-home"
    resolver = build_resolver(profile=None, environ={"BLUE_DATA_PLANNER_HOME": str(home)})

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

    assert raised.value.code == "CONFIG_MISSING"
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
