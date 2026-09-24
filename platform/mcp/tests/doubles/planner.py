"""A fake planner and the run it answers with, so no test reaches a model or a registry."""

from __future__ import annotations

from blue_data_planner import RunResult, RunStatus
from blue_data_planner.types import (
    DataPipeline,
    LogicalPlan,
    PhysicalPlan,
    RunConversations,
    RunCounters,
    RunLimits,
)


def a_run_that_answered() -> RunResult:
    """One finished run: two rows, one logical plan, one physical plan."""
    logical = LogicalPlan(id="8c4a71d0", parent_id=None, nodes=[], output_node_id=None)
    physical = PhysicalPlan(id="a6e02f77", parent_id=None, logical_plan_id="8c4a71d0", fragments=[])
    return RunResult(
        status=RunStatus.OK,
        answer=[[{"title": "Senior Data Scientist", "company": "Corvus Health"}]],
        failure_reason=None,
        pipeline=DataPipeline(
            logical_plans={"8c4a71d0": logical},
            physical_plans={"a6e02f77": physical},
            current_logical_plan_id="8c4a71d0",
            current_physical_plan_id="a6e02f77",
        ),
        results={},
        counters=RunCounters(limits=RunLimits()),
        conversations=RunConversations(),
    )


def a_run_that_ran_out_of_budget() -> RunResult:
    """One run that did not finish, so the payload carries a status and no answer."""
    answered = a_run_that_answered()
    return RunResult(
        status=RunStatus.BUDGET_EXHAUSTED,
        answer=None,
        failure_reason="the operator call budget reached its cap",
        pipeline=answered.pipeline,
        results={},
        counters=answered.counters,
        conversations=answered.conversations,
    )


class FakePlanner:
    """Answers run and plan with a prepared result, recording what it was asked and where."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.requests: list[str] = []
        self.operations: list[str] = []
        self.thread_ids: list[int] = []

    def run(self, request: str) -> RunResult:
        return self._record("run", request)

    def plan(self, request: str) -> RunResult:
        return self._record("plan", request)

    def _record(self, operation: str, request: str) -> RunResult:
        import threading

        self.operations.append(operation)
        self.requests.append(request)
        self.thread_ids.append(threading.get_ident())
        return self.result
