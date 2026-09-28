"""The bounds a run obeys, the counts it spends against them, and the form it leaves in."""

from __future__ import annotations

import json

import pytest

from blue_data_planner.types import (
    SUMMARY_SAMPLE_ROWS,
    BudgetExhaustedError,
    DataPipeline,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    LLMUsage,
    ParameterAttempt,
    RunConversations,
    RunCounters,
    RunLimits,
    RunResult,
    RunStatus,
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
    assert limits.control_replans == 5
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


def a_run_that_scanned(rows: int) -> RunResult:
    """A finished run whose one fragment returned `rows` rows, after one model call."""
    conversations = RunConversations()
    conversations.logical_planner.turns.append(Turn(role="system", content="a whole prompt"))
    conversations.logical_planner.usage.append(a_usage_record(cost=0.5))
    return RunResult(
        status=RunStatus.OK,
        answer=[[{"school": "Lowell"}]],
        failure_reason=None,
        pipeline=DataPipeline(
            logical_plans={},
            physical_plans={},
            current_logical_plan_id=None,
            current_physical_plan_id=None,
        ),
        results={
            "a1": FragmentResult(
                status=RunStatus.OK,
                attempts=[
                    ParameterAttempt(
                        parameters={},
                        result=ExecutionResult(
                            status=ExecutionStatus.OK,
                            operator_id="builtin.scan",
                            output=[[{"n": index} for index in range(rows)]],
                            row_count=rows,
                        ),
                        assessment=None,
                    )
                ],
                failure_reason=None,
            )
        },
        counters=RunCounters(limits=RunLimits()),
        conversations=conversations,
    )


def test_the_json_form_of_a_run_reduces_its_rows_and_its_transcript_to_a_measure() -> None:
    """A document leaves the process, so whatever grows with the data a run read travels as a
    measure of itself rather than whole."""
    payload = a_run_that_scanned(rows=140).to_dict()

    scanned = payload["results"]["a1"]["output"][0]
    assert scanned["row_count"] == 140
    assert len(scanned["sample_rows"]) == SUMMARY_SAMPLE_ROWS
    assert payload["conversations"] == {"calls": 1, "cost": 0.5}
    json.dumps(payload)  # a document that will not encode is not a document
