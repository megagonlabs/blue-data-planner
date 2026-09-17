"""The bounds a run obeys and the counts it spends against them."""

from __future__ import annotations

import pytest

from blue_data_planner.types import BudgetExhaustedError, RunCounters, RunLimits


def test_an_operator_call_past_the_cap_raises_rather_than_returning() -> None:
    """Operator calls are billed where no caller is positioned to read a return value."""
    counters = RunCounters(RunLimits(operator_calls=2))

    counters.spend_operator_call()
    counters.spend_operator_call()

    assert counters.operator_calls == 2
    with pytest.raises(BudgetExhaustedError):
        counters.spend_operator_call()


@pytest.mark.parametrize("bound", ["failure_replans", "control_replans"])
def test_a_replan_past_its_cap_comes_back_as_no_room_rather_than_raising(bound: str) -> None:
    """The run loop reads the answer, so a spent replan allowance is a False and not a raise."""
    counters = RunCounters(RunLimits(**{bound: 1}))
    spend = getattr(counters, f"spend_{bound[:-1]}")

    assert spend() is True
    assert spend() is False
    assert getattr(counters, bound) == 1


def test_the_limits_carry_the_defaults_a_deployment_documents() -> None:
    """design/12 section 5 is written against these values."""
    limits = RunLimits()

    assert limits.operator_calls == 30
    assert limits.plan_corrections == 5
    assert limits.parameter_repairs == 3
    assert limits.cover_repairs == 2
    assert limits.failure_replans == 2
    assert limits.control_replans == 2
