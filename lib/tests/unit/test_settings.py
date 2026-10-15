"""What a planner is built from, and what a bad value does."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blue_data_planner import data_planner as data_planner_module
from blue_data_planner import settings as settings_module
from blue_data_planner.operators.adapters.lotus import LotusSuite
from blue_data_planner.operators.builtin import BuiltinSuite
from blue_data_planner.settings import PlannerSettings, build_data_planner_from_settings
from blue_data_planner.types import (
    InvalidSettingError,
    RegistryUnreadableError,
    RunLimits,
    UnknownOperatorSuiteError,
)


def a_settings(**overrides: object) -> PlannerSettings:
    """Settings naming a registry that does not have to exist."""
    values: dict[str, object] = {"registry_path": Path("registry.json")}
    values.update(overrides)
    return PlannerSettings(**values)  # pyright: ignore[reportArgumentType]


def a_readable_registry(tmp_path: Path) -> Path:
    """A registry path holding an empty, but readable, registry file."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("[]")
    return registry_path


def a_captured_llm_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the client an assembled planner builds with a double recording its properties."""
    captured: dict[str, Any] = {}

    class _Capturing:
        def __init__(self, name: str, properties: dict[str, Any]) -> None:
            captured["properties"] = properties

    monkeypatch.setattr(data_planner_module, "LLMClient", _Capturing)
    return captured


def a_captured_build_data_planner(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the builder the settings call delegates to with a double recording its options."""
    captured: dict[str, Any] = {}

    def _capturing(data_registry: Any, **options: Any) -> Any:
        captured.update(options)

    monkeypatch.setattr(settings_module, "build_data_planner", _capturing)
    return captured


def test_a_caller_who_names_only_a_registry_gets_the_documented_defaults():
    """Every default lives here, and profile_spec.json is checked against these values."""
    settings = a_settings()
    assert settings.model == "openai/gpt-6-luna"
    assert settings.reasoning_effort == "low"
    assert settings.operator_suites == ("builtin", "lotus")
    assert settings.lotus_model is None
    assert settings.lotus_reasoning_effort == "none"
    assert settings.lotus_max_rows == 5000
    assert settings.provider_timeout_seconds == 120
    assert settings.limits.operator_calls == 30
    assert settings.limits.failure_replans == 2
    assert settings.limits.control_replans == 5


@pytest.mark.parametrize("field", ["reasoning_effort", "lotus_reasoning_effort"])
def test_a_reasoning_effort_no_model_accepts_is_refused(field: str):
    """A typo reaches the provider as a call that fails, so it is caught where it is written."""
    with pytest.raises(InvalidSettingError, match=field):
        a_settings(**{field: "maximum"})


@pytest.mark.parametrize("seconds", [0, -5])
def test_a_provider_timeout_that_is_not_above_zero_is_refused(seconds: int):
    """A timeout of zero or less would fail every provider call, so it is caught here."""
    with pytest.raises(InvalidSettingError, match="provider_timeout_seconds"):
        a_settings(provider_timeout_seconds=seconds)


def test_the_provider_timeout_reaches_the_model_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The client the planner builds carries the timeout the settings name."""
    captured = a_captured_llm_client(monkeypatch)

    build_data_planner_from_settings(
        a_settings(
            registry_path=a_readable_registry(tmp_path),
            provider_timeout_seconds=45,
            operator_suites=(),
        )
    )

    assert captured["properties"]["blue_data_planner.timeout"] == 45


def test_a_registry_path_naming_no_file_is_refused_when_the_planner_is_built():
    """blue-data's load returns quietly on a missing file, which would plan over nothing."""
    with pytest.raises(RegistryUnreadableError, match="no registry file"):
        build_data_planner_from_settings(a_settings(registry_path=Path("/nowhere/registry.json")))


def test_an_unknown_operator_suite_name_is_refused_when_the_planner_is_built(
    tmp_path: Path,
) -> None:
    """The registry opens fine; the typo is in the suite name, caught assembling the router."""
    with pytest.raises(UnknownOperatorSuiteError, match="nope"):
        build_data_planner_from_settings(
            a_settings(registry_path=a_readable_registry(tmp_path), operator_suites=("nope",))
        )


@pytest.mark.parametrize(("configured", "expected"), [("off", None), ("low", "low")])
def test_off_leaves_the_reasoning_effort_parameter_out_of_the_model_call(
    configured: str, expected: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The word "off" omits the parameter from the model call; every other word reaches it."""
    captured = a_captured_llm_client(monkeypatch)

    build_data_planner_from_settings(
        a_settings(
            registry_path=a_readable_registry(tmp_path),
            reasoning_effort=configured,
            operator_suites=(),
        )
    )

    assert captured["properties"].get("blue_data_planner.reasoning_effort") == expected


def test_the_suites_named_are_the_suites_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The planner is built over the suites operator_suites names, in the order it names them."""
    captured = a_captured_build_data_planner(monkeypatch)

    build_data_planner_from_settings(
        a_settings(
            registry_path=a_readable_registry(tmp_path), operator_suites=("lotus", "builtin")
        )
    )

    assert [suite.name for suite in captured["suites"]] == ["lotus", "builtin"]


def test_the_bounds_a_caller_sets_reach_the_planner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A raised bound is what the run obeys, so it has to survive the build rather than resetting
    to the RunLimits default."""
    captured = a_captured_build_data_planner(monkeypatch)
    limits = RunLimits(operator_calls=7)

    build_data_planner_from_settings(
        a_settings(registry_path=a_readable_registry(tmp_path), limits=limits)
    )

    assert captured["limits"] == limits


@pytest.mark.parametrize(
    ("lotus_model", "runs_lotus_on"),
    [(None, "openai/gpt-6-luna"), ("openai/gpt-4.1", "openai/gpt-4.1")],
)
def test_each_suite_option_reaches_the_suite_it_configures(
    lotus_model: str | None,
    runs_lotus_on: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A suite is built from the options naming it, and a lotus_model left unset runs the LOTUS
    suite on the model every other component uses."""
    captured = a_captured_build_data_planner(monkeypatch)

    build_data_planner_from_settings(
        a_settings(
            registry_path=a_readable_registry(tmp_path),
            model="openai/gpt-6-luna",
            lotus_model=lotus_model,
            lotus_reasoning_effort="high",
            lotus_max_rows=10,
        )
    )

    assert captured["suites"] == [
        BuiltinSuite(),
        LotusSuite(model=runs_lotus_on, max_rows=10, reasoning_effort="high"),
    ]
