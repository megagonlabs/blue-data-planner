"""A fake planner and a run-result builder, so the CLI's own tests run without a model."""

from __future__ import annotations

from typing import Any

from blue_data_planner import RunResult, RunStatus
from blue_data_planner.types import (
    DataBundle,
    DataPipeline,
    RunConversations,
    RunCounters,
    RunLimits,
)


class FakeDataPlanner:
    """Answers run and plan with prepared results, so an adapter is tested without a model."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.requests: list[str] = []
        self.operations: list[str] = []

    def run(self, request: str) -> RunResult:
        self.requests.append(request)
        self.operations.append("run")
        return self.result

    def plan(self, request: str) -> RunResult:
        self.requests.append(request)
        self.operations.append("plan")
        return self.result


def a_run_result(
    status: RunStatus = RunStatus.OK,
    answer: DataBundle | None = None,
    failure_reason: str | None = None,
    pipeline: DataPipeline | None = None,
    results: dict[str, Any] | None = None,
    counters: RunCounters | None = None,
    conversations: RunConversations | None = None,
) -> RunResult:
    """Return a RunResult whose unset parts are the empty shapes a finished run would carry."""
    return RunResult(
        status=status,
        answer=answer,
        failure_reason=failure_reason,
        pipeline=pipeline
        or DataPipeline(
            logical_plans={},
            physical_plans={},
            current_logical_plan_id=None,
            current_physical_plan_id=None,
        ),
        results=results or {},
        counters=counters or RunCounters(limits=RunLimits()),
        conversations=conversations or RunConversations(),
    )
