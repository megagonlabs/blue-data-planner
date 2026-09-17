"""The bounds a run obeys and the counts it spends against them."""

from __future__ import annotations

import pytest

from blue_data_planner.types import (
    BudgetExhaustedError,
    LLMUsage,
    RunConversations,
    RunCounters,
    RunLimits,
    Turn,
)


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
    assert not hasattr(limits, "fragment_repairs")


def a_usage_record(cost: float) -> LLMUsage:
    """One model call costing what the caller named."""
    return LLMUsage(
        model="scripted", prompt_tokens=0, completion_tokens=0, cost=cost, latency_seconds=0.0
    )


def test_a_run_reads_its_whole_model_spend_off_the_conversations_it_holds() -> None:
    """No component reports what its calls cost: a total is a sum over the records they hold."""
    conversations = RunConversations()
    conversations.logical_planner.usage.append(a_usage_record(cost=1.0))
    conversations.for_physical_planner("p1").usage.append(a_usage_record(cost=2.0))
    conversations.for_fragment_runner("f1").usage.append(a_usage_record(cost=4.0))
    conversations.for_catalog_searcher("a3f1").usage.append(a_usage_record(cost=8.0))

    assert sum(record.cost for record in conversations.usage) == 15.0


@pytest.mark.parametrize(
    "accessor", ["for_physical_planner", "for_fragment_runner", "for_catalog_searcher"]
)
def test_asking_twice_for_one_scope_returns_the_exchange_already_open(accessor: str) -> None:
    """A repair continues what it repairs, so the second call answers on top of the first."""
    conversations = RunConversations()
    opened = getattr(conversations, accessor)("k1")
    opened.turns.append(Turn(role="system", content="fixed"))

    assert getattr(conversations, accessor)("k1").turns == opened.turns
    assert getattr(conversations, accessor)("k2").turns == []
