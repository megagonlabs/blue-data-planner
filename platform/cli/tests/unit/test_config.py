"""What a profile supplies, and the planner those settings assemble."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs]
    ConfigResolver,
    ProfileStore,
)

from blue_data_planner_cli import config
from blue_data_planner_cli.config import (
    CliError,
    PlannerSettings,
    assemble_planner,
    build_resolver,
    cli_home,
    config_spec,
    export_secrets,
    resolve_settings,
)


def test_the_spec_declares_the_planner_parameters_beside_the_family_wide_ones() -> None:
    """One spec answers for both, so a CLI parameter and a family parameter resolve alike."""
    spec = config_spec()

    assert spec["BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS"].default == 30
    assert spec["BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS"].cast == "int"
    assert spec["BLUE_DATA_PLANNER_LOTUS_MODEL"].inherits == ("BLUE_DATA_PLANNER_MODEL",)
    assert spec["BLUE_DATA_PLANNER_REASONING_EFFORT"].default == "low"
    assert spec["BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT"].default == "none"
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


def a_captured_llm_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace LLMClient with a double that records its constructor arguments, and return them."""
    captured: dict[str, Any] = {}

    class _CapturingLLMClient:
        def __init__(self, name: str, properties: dict[str, Any]) -> None:
            captured["name"] = name
            captured["properties"] = properties

    monkeypatch.setattr(config, "LLMClient", _CapturingLLMClient)
    return captured


def test_the_bounds_a_profile_carries_reach_run_limits(tmp_path: Path) -> None:
    """A profile is where a deployment raises a bound a run reports back, so each has to arrive
    intact, and a bound no parameter names keeps its RunLimits default."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS="7")

    settings: PlannerSettings = resolve_settings(resolver)

    assert settings.limits.operator_calls == 7
    assert settings.limits.plan_corrections == 5  # no parameter names it, so the default stands


def test_the_reasoning_effort_parameter_resolves_from_the_profile(tmp_path: Path) -> None:
    """A deployment sets this the same way it sets any other bound, without being asked for it."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_REASONING_EFFORT="low")

    settings: PlannerSettings = resolve_settings(resolver)

    assert settings.reasoning_effort == "low"


def test_the_lotus_reasoning_effort_parameter_resolves_from_the_profile(tmp_path: Path) -> None:
    """LOTUS's own reasoning effort is set through a profile the same way the main one is."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT="low")

    settings: PlannerSettings = resolve_settings(resolver)

    assert settings.lotus_reasoning_effort == "low"


def test_an_unset_lotus_reasoning_effort_resolves_to_none_regardless_of_the_main_setting(
    tmp_path: Path,
) -> None:
    """The default stands on its own: it does not inherit BLUE_DATA_PLANNER_REASONING_EFFORT."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_REASONING_EFFORT="high")

    settings: PlannerSettings = resolve_settings(resolver)

    assert settings.lotus_reasoning_effort == "none"


def test_a_command_option_outranks_the_profile(tmp_path: Path) -> None:
    """The documented order is option, environment, profile, default."""
    other = tmp_path / "other.json"
    other.write_text("[]")
    resolver = a_resolver_over(tmp_path)

    settings: PlannerSettings = resolve_settings(resolver, registry=str(other))

    assert settings.registry_path == other


def test_the_model_option_reaches_the_lotus_suite_as_well_as_the_planner(tmp_path: Path) -> None:
    """--model names the model every component uses, and BLUE_DATA_PLANNER_LOTUS_MODEL inherits
    BLUE_DATA_PLANNER_MODEL, so its own resolution never sees the option."""
    resolver = a_resolver_over(tmp_path)

    settings: PlannerSettings = resolve_settings(resolver, model="openai/gpt-4o-mini")

    assert settings.model == "openai/gpt-4o-mini"
    assert settings.lotus_model == "openai/gpt-4o-mini"


def test_a_profile_set_lotus_model_stands_apart_from_the_planners_own(tmp_path: Path) -> None:
    """Setting LOTUS away from the planner's model is what the parameter is for."""
    resolver = a_resolver_over(
        tmp_path,
        BLUE_DATA_PLANNER_MODEL="openai/gpt-4o",
        BLUE_DATA_PLANNER_LOTUS_MODEL="openai/gpt-4o-mini",
    )

    settings: PlannerSettings = resolve_settings(resolver)

    assert settings.model == "openai/gpt-4o"
    assert settings.lotus_model == "openai/gpt-4o-mini"


def test_an_unknown_operator_suite_is_a_configuration_error(tmp_path: Path) -> None:
    """A name outside the three would otherwise register nothing and fail much later."""
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_OPERATORS="builtin,sqlmagic")

    with pytest.raises(CliError) as raised:
        resolve_settings(resolver)

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


def test_the_assembled_planners_llm_client_names_its_model_under_the_clients_own_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare "model" key never matches api_properties' <prefix>.* filter, so every LLM call
    the assembled planner makes would fail with "no model configured"."""
    captured = a_captured_llm_client(monkeypatch)
    resolver = a_resolver_over(
        tmp_path, BLUE_DATA_PLANNER_OPERATORS="builtin", BLUE_DATA_PLANNER_MODEL="gpt-4o"
    )
    settings = resolve_settings(resolver)

    assemble_planner(settings)

    assert captured["properties"][f"{captured['name']}.model"] == "gpt-4o"


def test_the_assembled_planners_llm_client_carries_a_set_reasoning_effort_under_the_same_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reasoning_effort reaches litellm.completion() only through the <prefix>.* extraction, the
    same route settings.model already takes."""
    captured = a_captured_llm_client(monkeypatch)
    resolver = a_resolver_over(
        tmp_path,
        BLUE_DATA_PLANNER_OPERATORS="builtin",
        BLUE_DATA_PLANNER_MODEL="gpt-4o",
        BLUE_DATA_PLANNER_REASONING_EFFORT="low",
    )
    settings = resolve_settings(resolver)

    assemble_planner(settings)

    assert captured["properties"][f"{captured['name']}.reasoning_effort"] == "low"


def test_the_off_sentinel_set_through_a_profile_omits_reasoning_effort_from_the_llm_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user switching to a model that rejects the field sets "off" through `profile config`,
    and that value, unlike an empty string, survives ConfigResolver and reaches assemble_planner."""
    captured = a_captured_llm_client(monkeypatch)
    resolver = a_resolver_over(
        tmp_path, BLUE_DATA_PLANNER_OPERATORS="builtin", BLUE_DATA_PLANNER_REASONING_EFFORT="off"
    )
    settings = resolve_settings(resolver)

    assemble_planner(settings)

    assert settings.reasoning_effort == "off"
    assert f"{captured['name']}.reasoning_effort" not in captured["properties"]


def test_a_blank_reasoning_effort_is_left_out_of_the_llm_clients_properties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit-level check of assemble_planner's own conditional: an empty string, which no real
    profile or environment value can produce, still has to drop the key rather than forward it."""
    captured = a_captured_llm_client(monkeypatch)
    resolver = a_resolver_over(tmp_path, BLUE_DATA_PLANNER_OPERATORS="builtin")
    settings = dataclasses.replace(resolve_settings(resolver), reasoning_effort="")

    assemble_planner(settings)

    assert f"{captured['name']}.reasoning_effort" not in captured["properties"]


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
        export_secrets(resolver)

    assert raised.value.code == "CONFIG_INVALID"
    assert "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS" in raised.value.description
