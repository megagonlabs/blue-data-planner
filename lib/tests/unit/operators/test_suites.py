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
from tests.doubles.blue_operations import a_registry_answering_every_operation

PLANNER_MODEL = "openai/gpt-4o"


@pytest.fixture
def lotus_stub() -> Any:
    """Return the stub with its model forgotten, so a suite's own choice is what it records."""
    settings._configured_model = None  # pyright: ignore[reportPrivateUsage]
    settings._configured_reasoning_effort = None  # pyright: ignore[reportPrivateUsage]
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
        (LotusSuite(model="openai/gpt-4o-mini"), "openai/gpt-4o-mini"),
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


def test_a_blue_suite_resolves_its_operations_against_the_registry_it_was_given() -> None:
    """A caller holding its own operator registry hands that one in rather than the default."""
    operators = built(BlueSuite(operator_registry=a_registry_answering_every_operation()))

    assert [one.operator_id for one in operators if one.operator_id == "blue.select"]
