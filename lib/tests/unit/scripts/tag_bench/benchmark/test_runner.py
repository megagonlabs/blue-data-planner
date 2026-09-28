"""Tests for lib/scripts/tag_bench/benchmark/runner.py."""

from __future__ import annotations

from typing import Any

from blue_data_planner import RunResult, RunStatus
from blue_data_planner.types import (
    Conversation,
    DataPipeline,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    LLMUsage,
    LogicalPlan,
    ParameterAttempt,
    RunConversations,
    RunCounters,
    RunLimits,
    Turn,
)
from scripts.tag_bench.benchmark.runner import run_query
from scripts.tag_bench.fetch_queries import QueryRecord

A_QUERY = QueryRecord(
    query_id=7,
    question="how many stores are in Reno",
    query_type="Match",
    db_name="retail",
    gold_answer=2,
    split="dev",
)


class PlannerAnsweringOnce:
    """A planner double returning one prepared result, or raising one prepared error."""

    def __init__(self, result: RunResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def run(self, request: str) -> RunResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def a_finished_run(answer: Any) -> RunResult:
    """One ok RunResult over a one-node plan, one fragment result, and one model call."""
    plan = LogicalPlan(id="lp1", parent_id=None, nodes=[], output_node_id=None)
    conversations = RunConversations(
        logical_planner=Conversation(
            turns=[Turn(role="user", content="write a plan")],
            usage=[
                LLMUsage(
                    model="openai/gpt-5.6-luna",
                    prompt_tokens=10,
                    completion_tokens=2,
                    cost=0.5,
                    latency_seconds=1.0,
                )
            ],
        )
    )
    counters = RunCounters(limits=RunLimits())
    counters.operator_calls = 3
    return RunResult(
        status=RunStatus.OK,
        answer=answer,
        failure_reason=None,
        pipeline=DataPipeline(
            logical_plans={"lp1": plan},
            physical_plans={},
            current_logical_plan_id="lp1",
            current_physical_plan_id=None,
        ),
        results={
            "f1:SCAN": FragmentResult(
                status=RunStatus.OK,
                attempts=[
                    ParameterAttempt(
                        parameters={},
                        result=ExecutionResult(
                            status=ExecutionStatus.OK,
                            operator_id="builtin.scan",
                            row_count=2,
                            report={"rows": 2},
                        ),
                        assessment=None,
                    )
                ],
                failure_reason=None,
            )
        },
        counters=counters,
        conversations=conversations,
    )


def test_a_query_the_planner_answers_becomes_a_record_carrying_its_score_and_its_plans() -> None:
    """The record embeds the query, then states the scored answer, the status, and the plans."""
    planner = PlannerAnsweringOnce(a_finished_run([[{"count": 2}]]))

    outcome = run_query(planner, A_QUERY)  # pyright: ignore[reportArgumentType]

    assert (outcome.record["query_id"], outcome.record["gold_answer"]) == (7, 2)
    assert (outcome.record["prediction"], outcome.record["correct"]) == (2, True)
    assert outcome.record["status"] == "ok"
    assert list(outcome.record["pipeline"]["logical_plans"]) == ["lp1"]
    assert outcome.record["error"] is None


def test_a_record_states_what_its_query_spent_and_how_each_fragment_ended() -> None:
    """Operator calls, model calls, cost, and each fragment's result reach the record."""
    planner = PlannerAnsweringOnce(a_finished_run([[{"count": 2}]]))

    record = run_query(planner, A_QUERY).record  # pyright: ignore[reportArgumentType]

    assert record["counters"]["operator_calls"] == 3
    assert record["usage"]["calls"] == 1
    assert record["usage"]["cost"] == 0.5
    assert record["usage"]["by_component"]["logical_planner"]["calls"] == 1
    assert record["fragment_results"]["f1:SCAN"]["operator_id"] == "builtin.scan"


def test_a_rejected_fragment_reaches_the_record_with_the_result_its_review_turned_down() -> None:
    """A run's record has to say where a cover stopped, so a rejection keeps both readings."""
    run = a_finished_run([[{"count": 2}]])
    run.results["f1:SCAN"] = FragmentResult(
        status=RunStatus.NEEDS_REPAIR,
        attempts=[
            ParameterAttempt(
                parameters={},
                result=ExecutionResult(
                    status=ExecutionStatus.OK,
                    operator_id="builtin.scan",
                    row_count=2,
                ),
                assessment=None,
            )
        ],
        failure_reason="the rows are of the wrong city",
    )
    planner = PlannerAnsweringOnce(run)

    filed = run_query(planner, A_QUERY).record["fragment_results"]["f1:SCAN"]  # pyright: ignore[reportArgumentType]

    assert filed["status"] == "needs_repair"
    assert filed["failure_reason"] == "the rows are of the wrong city"
    assert (filed["operator_id"], filed["row_count"]) == ("builtin.scan", 2)


def test_a_query_whose_run_raised_becomes_a_record_carrying_the_traceback() -> None:
    """One defect scores its own query wrong and leaves the rest of a run scorable."""
    planner = PlannerAnsweringOnce(error=RuntimeError("the registry vanished"))

    record = run_query(planner, A_QUERY).record  # pyright: ignore[reportArgumentType]

    assert (record["correct"], record["prediction"]) == (False, None)
    assert record["status"] == "harness_error"
    assert "the registry vanished" in record["error"]


def test_a_query_keeps_its_transcripts_apart_from_its_record() -> None:
    """The turns travel beside the record, since triage starts with the record."""
    planner = PlannerAnsweringOnce(a_finished_run([[{"count": 2}]]))

    outcome = run_query(planner, A_QUERY)  # pyright: ignore[reportArgumentType]

    assert outcome.conversations["logical_planner"]["turns"][0]["content"] == "write a plan"
    assert "turns" not in outcome.record
