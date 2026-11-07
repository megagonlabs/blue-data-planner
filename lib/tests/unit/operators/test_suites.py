"""A configured suite name, and the options the suite it names is built with."""

from __future__ import annotations

from typing import Any

import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed
import pytest

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators import OPERATOR_SUITE_NAMES, operator_suite
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.adapters.lotus import LotusSuite, settings
from blue_data_planner.types import UnknownOperatorSuiteError
from tests.doubles.blue_operations import (
    OPERATION_NAMES,
    a_registry_answering_every_operation,
    scripted_operation,
)

PLANNER_MODEL = "openai/gpt-6-luna"


@pytest.fixture
def lotus_stub() -> Any:
    """Return the stub with its model forgotten, so a suite's own choice is what it records."""
    settings._configured_model = None  # pyright: ignore[reportPrivateUsage]
    settings._configured_reasoning_effort = None  # pyright: ignore[reportPrivateUsage]
    settings._lotus_configured = False  # pyright: ignore[reportPrivateUsage]
    import lotus

    lotus.settings.lm = lotus.models.LM()
    lotus.reset()
    return lotus


def an_empty_registry() -> DataRegistry:
    """A registry holding no records, for a suite that reads none."""
    registry: Any = DataRegistry(name="suite_test", rebuild=True)
    registry.loads("[]")
    return registry


def built(suite: Any) -> list[Any]:
    """The operators one suite registers, over an empty registry and one planner model."""
    return suite.operators(
        data_registry=an_empty_registry(),
        llm_client=LLMClient(name="suite_test", properties={"suite_test.model": PLANNER_MODEL}),
        model=PLANNER_MODEL,
    )


@pytest.mark.parametrize("name", OPERATOR_SUITE_NAMES)
def test_a_configured_suite_name_reaches_the_suite_that_registers_it(name: str) -> None:
    """A deployment names its suites in configuration, so a name is what has to resolve."""
    assert operator_suite(name).name == name


def test_a_name_no_suite_registers_is_refused_rather_than_left_out() -> None:
    """A typo in a suite list would otherwise run with fewer operators than it named."""
    with pytest.raises(UnknownOperatorSuiteError):
        operator_suite("lotos")


@pytest.mark.parametrize(
    ("suite", "runs_on"),
    [
        (LotusSuite(), PLANNER_MODEL),
        (LotusSuite(model="openai/gpt-4.1"), "openai/gpt-4.1"),
    ],
)
def test_a_lotus_suite_runs_on_its_own_model_or_on_the_planners(
    suite: LotusSuite, runs_on: str, lotus_stub: Any
) -> None:
    """A deployment sets LOTUS's model away from the planner's, or leaves it to follow."""
    operators = [one for one in built(suite) if one.operator_id == "lotus.sem_filter"]
    assert operators, "lotus.sem_filter is not registered"
    lotus_stub.frames.append(pd.DataFrame([{"city": "Reno"}]))

    operators[0].call([[{"city": "Reno"}]], {"instruction": "Is {city} inland?"}, [])

    assert lotus_stub.settings.lm.model == runs_on


@pytest.mark.parametrize(
    "suite", [BlueSuite(), operator_suite("blue")], ids=["direct", "configured"]
)
def test_a_blue_suite_without_a_registry_builds_and_runs_its_pinned_operations(suite: Any) -> None:
    """Both public defaults must resolve the real pinned operations before a request starts."""
    operators = {one.operator_id: one for one in built(suite)}

    assert set(operators) == {f"blue.{name}" for name in OPERATION_NAMES}
    result = operators["blue.select"].call(
        [[{"city": "Reno", "population": 2}, {"city": "Dallas", "population": 9}]],
        {"operand_key": "population", "operand": ">", "operand_val": 5},
        [],
    )

    assert result.data == [[{"city": "Dallas", "population": 9}]]


def test_a_blue_suite_resolves_its_operations_against_the_registry_it_was_given() -> None:
    """A caller holding its own operator registry hands that one in rather than the default."""
    select = scripted_operation("select", output=[[{"city": "from the custom registry"}]])
    operators = built(BlueSuite(operator_registry=a_registry_answering_every_operation(select)))
    operator = next(one for one in operators if one.operator_id == "blue.select")

    result = operator.call(
        [[{"city": "Reno"}]],
        {"operand_key": "city", "operand": "=", "operand_val": "Reno"},
        [],
    )

    assert result.data == [[{"city": "from the custom registry"}]]


@pytest.mark.parametrize(
    ("own_model", "runs_on"),
    [(None, "gpt-6-luna"), ("openai/gpt-4.1", "gpt-4.1")],
)
def test_a_blue_suite_runs_on_its_own_model_or_on_the_planners(
    own_model: str | None, runs_on: str
) -> None:
    """A deployment sets the Blue suite's model away from the planner's, or leaves it to follow.
    The provider route is left off, since blue-data-ops takes its provider from the
    environment."""
    scripted = scripted_operation("nl2llm")
    suite = BlueSuite(
        operator_registry=a_registry_answering_every_operation(scripted), model=own_model
    )
    operators = [one for one in built(suite) if one.operator_id == "blue.nl2llm"]

    operators[0].call([], {"query": "how many states are there"}, [])

    assert scripted.seen_properties == {"llm.model": runs_on, "llm.temperature": None}


@pytest.mark.parametrize("reasoning_effort", [None, "low"])
def test_a_blue_model_call_uses_the_provider_temperature_default(
    reasoning_effort: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Blue model call through the real ops client sends no fixed temperature."""
    import litellm

    monkeypatch.setenv("BLUE_DATA_LLM_PROVIDER", "openai")
    monkeypatch.delenv("BLUE_DATA_LLM_ALLOWED_MODELS", raising=False)
    seen: list[dict[str, Any]] = []

    def complete(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        if kwargs.get("temperature") is not None:
            raise ValueError("this model refuses a fixed temperature")
        return {
            "choices": [{"message": {"content": '[{"city": "Oslo"}]'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(litellm, "completion", complete)
    operators = built(BlueSuite(reasoning_effort=reasoning_effort))
    operator = next(one for one in operators if one.operator_id == "blue.nl2llm")

    result = operator.call([], {"query": "Name a Nordic capital."}, [])

    assert result.data == [[{"city": "Oslo"}]]
    assert len(seen) == 1
    assert seen[0]["model"] == "gpt-6-luna"
    assert seen[0].get("temperature") is None
