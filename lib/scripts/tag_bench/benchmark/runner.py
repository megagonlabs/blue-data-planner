"""Running one benchmark query and recording everything its run produced."""

from __future__ import annotations

import dataclasses
import traceback
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from blue_data_planner import DataPlanner, RunResult
from blue_data_planner.types import (
    Conversation,
    DiscoveryResult,
    ExecutionResult,
    RunConversations,
)
from scripts.tag_bench.benchmark.scoring import score_answer
from scripts.tag_bench.fetch_queries import QueryRecord

# A run that raised is not a planner outcome: every outcome a repair could answer is already a
# RunStatus by the time a run returns. This names the other case in the same field.
HARNESS_ERROR_STATUS: str = "harness_error"


@dataclass(frozen=True)
class QueryOutcome:
    """One query's record, and the transcripts that travel beside it."""

    record: dict[str, Any]
    conversations: dict[str, Any]


def run_query(planner: DataPlanner, query: QueryRecord) -> QueryOutcome:
    """Run one query, score its answer, and return the record and the transcripts."""
    started = perf_counter()
    try:
        result = planner.run(query["question"])
    except Exception:
        # Deliberately broad, and the one place the harness departs from failing fast: a defect
        # in one query must leave the rest of a run scorable.
        return QueryOutcome(
            record=_failed_record(query, traceback.format_exc(), perf_counter() - started),
            conversations={},
        )
    return QueryOutcome(
        record=_record(query, result, perf_counter() - started),
        conversations=_conversations_payload(result.conversations),
    )


def _record(query: QueryRecord, result: RunResult, elapsed: float) -> dict[str, Any]:
    scored = score_answer(result.answer, query["gold_answer"])
    return {
        **query,
        "prediction": scored.prediction,
        "correct": scored.correct,
        "note": scored.note,
        "status": str(result.status),
        "failure_reason": result.failure_reason,
        "pipeline": result.pipeline.to_dict(),
        "fragment_results": {
            signature: _outcome_payload(outcome) for signature, outcome in result.results.items()
        },
        "counters": {
            "operator_calls": result.counters.operator_calls,
            "failure_replans": result.counters.failure_replans,
            "control_replans": result.counters.control_replans,
            "limits": dataclasses.asdict(result.counters.limits),
        },
        "usage": _usage_payload(result),
        "elapsed_seconds": round(elapsed, 3),
        "error": None,
    }


def _failed_record(query: QueryRecord, formatted: str, elapsed: float) -> dict[str, Any]:
    return {
        **query,
        "prediction": None,
        "correct": False,
        "note": "the run raised before it produced an answer",
        "status": HARNESS_ERROR_STATUS,
        "failure_reason": None,
        "pipeline": None,
        "fragment_results": {},
        "counters": None,
        "usage": {"calls": 0, "cost": 0.0, "by_component": {}},
        "elapsed_seconds": round(elapsed, 3),
        "error": formatted,
    }


def _outcome_payload(outcome: ExecutionResult | DiscoveryResult) -> dict[str, Any]:
    if isinstance(outcome, ExecutionResult):
        return {
            "status": str(outcome.status),
            "operator_id": outcome.operator_id,
            "row_count": outcome.row_count,
            "error": None
            if outcome.error is None
            else {"kind": str(outcome.error.kind), "message": outcome.error.message},
            "report": outcome.report,
        }
    return {
        "search_terms": outcome.search_terms,
        "matches": [dataclasses.asdict(match) for match in outcome.matches],
        "dropped_terms": outcome.dropped_terms,
    }


def _usage_payload(result: RunResult) -> dict[str, Any]:
    conversations = result.conversations
    by_component: dict[str, dict[str, Any]] = {
        "logical_planner": _spend([conversations.logical_planner]),
        "physical_planner": _spend(list(conversations.physical_planner.values())),
        "fragment_runner": _spend(list(conversations.fragment_runner.values())),
        "catalog_searcher": _spend(list(conversations.catalog_searcher.values())),
    }
    usage = conversations.usage
    return {
        "calls": len(usage),
        "cost": round(sum(one.cost for one in usage), 6),
        "by_component": by_component,
    }


def _spend(conversations: list[Conversation]) -> dict[str, Any]:
    usage = [one for conversation in conversations for one in conversation.usage]
    return {"calls": len(usage), "cost": round(sum(one.cost for one in usage), 6)}


def _conversations_payload(conversations: RunConversations) -> dict[str, Any]:
    return {
        "logical_planner": _conversation_payload(conversations.logical_planner),
        "physical_planner": {
            plan_id: _conversation_payload(conversation)
            for plan_id, conversation in conversations.physical_planner.items()
        },
        "fragment_runner": {
            fragment_id: _conversation_payload(conversation)
            for fragment_id, conversation in conversations.fragment_runner.items()
        },
        "catalog_searcher": {
            signature: _conversation_payload(conversation)
            for signature, conversation in conversations.catalog_searcher.items()
        },
    }


def _conversation_payload(conversation: Conversation) -> dict[str, Any]:
    return {
        "turns": [{"role": turn.role, "content": turn.content} for turn in conversation.turns],
        "usage": [dataclasses.asdict(one) for one in conversation.usage],
    }
