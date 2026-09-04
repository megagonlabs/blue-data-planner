"""What the run record promises whoever reads a PlanResult."""

from __future__ import annotations

import pytest

from blue_data_planner.types import (
    BlueDataPlannerError,
    BudgetExhaustedError,
    CallBudget,
    PlanLimits,
)


def test_a_budget_counts_what_it_spends_and_refuses_the_call_that_would_pass_its_cap() -> None:
    """The operator call budget is the one ceiling on a request's total work."""
    budget = CallBudget(cap=2)

    budget.spend()
    budget.spend()

    assert budget.spent == 2
    with pytest.raises(BudgetExhaustedError):
        budget.spend()


def test_a_budget_refuses_a_cap_it_could_never_honor() -> None:
    """A negative cap is malformed configuration, so it raises rather than clamping."""
    with pytest.raises(BlueDataPlannerError):
        CallBudget(cap=-1)


def test_the_limits_carry_the_defaults_a_deployment_documents() -> None:
    """A caller who sets no bound gets the numbers design/12-deployment-ops.md publishes."""
    limits = PlanLimits()

    assert limits == PlanLimits(
        operator_calls=30,
        plan_corrections=5,
        parameter_attempts=3,
        fragment_attempts=2,
        cover_attempts=2,
        failure_replans=2,
        control_replans=2,
        search_terms=8,
    )
