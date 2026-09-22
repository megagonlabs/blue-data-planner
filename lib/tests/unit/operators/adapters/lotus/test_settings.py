"""Importing LOTUS and configuring the model it runs on."""

from __future__ import annotations

import sys

import pytest

from blue_data_planner.operators.adapters.lotus import settings
from blue_data_planner.types import BlueDataPlannerError


@pytest.fixture(autouse=True)
def unconfigured() -> None:
    """Forget the model a previous test configured, since LOTUS holds one per process."""
    settings._configured_model = None  # pyright: ignore[reportPrivateUsage]
    settings._configured_reasoning_effort = None  # pyright: ignore[reportPrivateUsage]


def test_lotus_is_reported_installed_without_being_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check that decides whether to offer the suite loads no module."""
    monkeypatch.delitem(sys.modules, "lotus", raising=False)

    assert settings.lotus_is_installed() is True
    assert "lotus" not in sys.modules


def test_the_first_call_configures_the_model_it_was_given() -> None:
    """Asking for LOTUS imports it and hands its own settings the model string."""
    lotus = settings.configured_lotus("openai/gpt-4o")

    assert lotus.settings.lm.model == "openai/gpt-4o"


def test_a_second_model_is_refused_rather_than_overwriting_the_first() -> None:
    """LOTUS holds one model per process, so a second string is a failure, not a silent swap."""
    settings.configured_lotus("openai/gpt-4o")

    with pytest.raises(BlueDataPlannerError):
        settings.configured_lotus("openai/gpt-4o-mini")


def test_asking_twice_for_the_same_model_configures_once() -> None:
    """Every call reaches for LOTUS, and only the first one configures it."""
    first = settings.configured_lotus("openai/gpt-4o")
    configured = first.settings.lm

    assert settings.configured_lotus("openai/gpt-4o").settings.lm is configured


def test_the_first_call_forwards_the_reasoning_effort_it_was_given() -> None:
    """A reasoning effort reaches LOTUS's model the same way litellm receives it elsewhere."""
    lotus = settings.configured_lotus("openai/gpt-4o", "none")

    assert lotus.settings.lm.kwargs["reasoning_effort"] == "none"


def test_a_call_with_no_reasoning_effort_omits_the_parameter_entirely() -> None:
    """No reasoning effort means the kwarg is left out, not passed through as None."""
    lotus = settings.configured_lotus("openai/gpt-4o")

    assert "reasoning_effort" not in lotus.settings.lm.kwargs


def test_the_first_call_configures_the_model_with_the_token_budget() -> None:
    """The token budget reaches LOTUS's model on every call, not only a caller-supplied one."""
    lotus = settings.configured_lotus("openai/gpt-4o")

    assert lotus.settings.lm.kwargs["max_tokens"] == 15000


def test_a_second_reasoning_effort_is_refused_rather_than_overwriting_the_first() -> None:
    """Reasoning effort is process-wide too, so a second value is a failure, not a silent swap."""
    settings.configured_lotus("openai/gpt-4o", "none")

    with pytest.raises(BlueDataPlannerError):
        settings.configured_lotus("openai/gpt-4o", "low")
