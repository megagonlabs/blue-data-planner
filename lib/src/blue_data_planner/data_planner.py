"""The public entry point: the loop from a request to an answer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from blue_data_planner.catalog_searcher import CatalogSearcher
from blue_data_planner.data import DataCatalog, DataRegistry
from blue_data_planner.execution import project_answer
from blue_data_planner.fragment_runner import FragmentRunner
from blue_data_planner.llm import LLMClient, LLMError
from blue_data_planner.logical_planner import LogicalPlanner
from blue_data_planner.operators import OperatorRouter, OperatorSuite
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.run_state import NodeResult, RunState
from blue_data_planner.types import (
    BlueDataPlannerError,
    ControlOperator,
    DataBundle,
    LogicalOperator,
    LogicalPlan,
    LogicalPlanResult,
    PhysicalPlanFragment,
    PhysicalPlanResult,
    ReturnAnswerError,
    RunCounters,
    RunLimits,
    RunResult,
    RunStatus,
)

# What an assembled planner calls its model client, which prefixes every property the
# client reads. A deployment naming its own keeps one process's clients apart in a log.
DEFAULT_LLM_CLIENT_NAME: str = "blue_data_planner"


class _Next(StrEnum):
    """What the loop outside has to do when the loop inside ran out of repairs."""

    COVER_REPAIR = "cover_repair"
    FAILURE_REPLAN = "failure_replan"
    CONTROL_REPLAN = "control_replan"


@dataclass(frozen=True)
class _Ending:
    """How one loop ended: with the request's status, or with work for the loop outside it."""

    status: RunStatus | None = None
    failure_reason: str | None = None
    answer: DataBundle | None = None
    next_step: _Next | None = None


class DataPlanner:
    """Plans a request, runs it, repairs what fails, and assembles the result."""

    def __init__(
        self,
        logical_planner: LogicalPlanner,
        physical_planner: PhysicalPlanner,
        fragment_runner: FragmentRunner,
        catalog_searcher: CatalogSearcher,
        limits: RunLimits,
    ) -> None:
        self._logical_planner = logical_planner
        self._physical_planner = physical_planner
        self._fragment_runner = fragment_runner
        self._catalog_searcher = catalog_searcher
        self._limits = limits

    def run(self, request: str) -> RunResult:
        """Plan, cover, execute, and repair until the request ends."""
        state = RunState(request=request, counters=RunCounters(self._limits))
        try:
            status, failure_reason, answer = self._run_plans(state)
        except LLMError as error:
            # The one try in the library, so no component handles a provider failure.
            status, failure_reason, answer = RunStatus.LLM_FAILED, str(error), None
        except BlueDataPlannerError as error:
            # A defect rather than an outcome a repair could answer: every recoverable failure is
            # already a status by the time it reaches here. The run ends keeping what it built and
            # what broke, rather than throwing at whoever called the library.
            status, failure_reason, answer = RunStatus.PLANNER_FAILED, str(error), None
        return state.to_result(status, failure_reason, answer)

    def plan(self, request: str) -> RunResult:
        """Write one logical plan and one cover, then stop."""
        state = RunState(request=request, counters=RunCounters(self._limits))
        try:
            written = self._write_plan(state, {}, frozenset[str](), None, None)
            if written.plan is None:
                return state.to_result(written.status, written.failure_reason)
            state.install_logical_plan(written.plan)
            cover = self._write_cover(state, written.plan, {}, None)
        except LLMError as error:
            return state.to_result(RunStatus.LLM_FAILED, str(error))
        except BlueDataPlannerError as error:
            return state.to_result(RunStatus.PLANNER_FAILED, str(error))
        if cover.plan is not None:
            state.install_cover(cover.plan)
        # Whatever the one cover attempt came back with, the planning-only operation stops here.
        return state.to_result(RunStatus.PLANNED, cover.failure_reason)

    def _run_plans(self, state: RunState) -> tuple[RunStatus, str | None, DataBundle | None]:
        """The replan loop: write a plan, run it, and write another while a replan is allowed."""
        reason: str | None = None
        while True:
            has_cover = state.pipeline.current_physical_plan_id is not None
            # Read before the new version is installed: the signatures the results sit under
            # belong to the plan and the cover that are still current.
            held = self._results_by_node_id(state)
            carried = state.carried_node_ids() if has_cover else frozenset[str]()
            parent = state.pipeline.logical_plan if has_cover else None
            written = self._write_plan(state, held, carried, parent, reason)
            if written.plan is None:
                return written.status, written.failure_reason, None
            ending = self._run_covers(state, written.plan, held, reason)
            if ending.status is not None:
                return ending.status, ending.failure_reason, ending.answer
            reason = ending.failure_reason
            spent = (
                state.counters.spend_control_replan()
                if ending.next_step is _Next.CONTROL_REPLAN
                else state.counters.spend_failure_replan()
            )
            if not spent:
                return RunStatus.REPLAN_EXHAUSTED, reason, None

    def _run_covers(
        self, state: RunState, plan: LogicalPlan, held: dict[str, NodeResult], reason: str | None
    ) -> _Ending:
        """The cover loop: cover this plan, run the cover, and cover it again while allowed."""
        for _repair in range(self._limits.cover_repairs + 1):
            written = self._write_cover(state, plan, held, reason)
            if written.plan is None:
                # A plan no operator can cover goes back to the logical planner, with no operator
                # call spent, and never enters the pipeline.
                return _Ending(
                    failure_reason=written.failure_reason, next_step=_Next.FAILURE_REPLAN
                )
            # The version and its cover are installed together, so the two pointers always name a
            # matching pair and every signature describes the plan and cover that are current.
            state.install_logical_plan(plan)
            state.install_cover(written.plan)
            ending = self._run_passes(state)
            if ending.next_step is not _Next.COVER_REPAIR:
                return ending
            reason = ending.failure_reason
            held = self._results_by_node_id(state)
        return _Ending(failure_reason=reason, next_step=_Next.FAILURE_REPLAN)

    def _run_passes(self, state: RunState) -> _Ending:
        """Run everything the current cover makes ready, one fragment or one pass at a time."""
        while True:
            ready = state.next_ready()
            if ready is None:
                return self._answer(state)
            if not isinstance(ready, PhysicalPlanFragment):
                return self._resolve_control_nodes(state)
            ending = self._run_fragment(state, ready)
            if ending is not None:
                return ending

    def _run_fragment(self, state: RunState, fragment: PhysicalPlanFragment) -> _Ending | None:
        """Run one fragment, and say which loop outside takes over when it fails."""
        result = self._fragment_runner.run(
            fragment,
            state.conversations.for_fragment_runner(fragment.id),
            state.inputs_for(fragment),
            state.counters,
        )
        state.record_fragment(fragment, result)
        if result.status is RunStatus.OK:
            return None
        if result.status is RunStatus.NEEDS_REPLAN:
            return _Ending(failure_reason=result.failure_reason, next_step=_Next.FAILURE_REPLAN)
        if result.status is RunStatus.NEEDS_REPAIR:
            return _Ending(
                failure_reason=result.failure_reason or "the fragment was rejected",
                next_step=_Next.COVER_REPAIR,
            )
        return _Ending(status=result.status, failure_reason=result.failure_reason)

    def _resolve_control_nodes(self, state: RunState) -> _Ending:
        """Resolve every control node ready at once, then return to the logical planner."""
        reasons: list[str] = []
        for node in state.ready_control_nodes():
            reasons.append(str(node.attributes.get("instruction", "")))
            if node.kind is not ControlOperator.DISCOVER:
                continue
            found = self._catalog_searcher.search(
                node,
                state.conversations.for_catalog_searcher(state.signature_of(node.id)),
                state.inputs_for_node(node),
            )
            state.record_discovery(node, found)
        return _Ending(failure_reason="\n".join(reasons), next_step=_Next.CONTROL_REPLAN)

    def _write_plan(
        self,
        state: RunState,
        held: dict[str, NodeResult],
        carried_node_ids: frozenset[str],
        parent: LogicalPlan | None,
        reason: str | None,
    ) -> LogicalPlanResult:
        return self._logical_planner.generate(
            state.request,
            state.conversations.logical_planner,
            state.counters,
            parent=parent,
            carried_node_ids=carried_node_ids,
            results=held,
            reason=reason,
        )

    def _write_cover(
        self,
        state: RunState,
        plan: LogicalPlan,
        held: dict[str, NodeResult],
        reason: str | None,
    ) -> PhysicalPlanResult:
        current = state.pipeline.current_physical_plan_id
        return self._physical_planner.cover(
            plan,
            state.conversations.for_physical_planner(plan.id),
            state.pipeline.physical_plans.get(current) if current else None,
            held,
            reason,
        )

    def _results_by_node_id(self, state: RunState) -> dict[str, NodeResult]:
        """Return what the run holds for each node of the current plan, keyed by node id."""
        # The components read a node's result by the id the plan gives it, and the run state files
        # results by the signature of the work that produced them.
        if state.pipeline.current_physical_plan_id is None:
            return {}
        held: dict[str, NodeResult] = {}
        for node in state.pipeline.logical_plan.nodes:
            result = state.result_for(node.id)
            if result is not None:
                held[node.id] = result
        return held

    def _answer(self, state: RunState) -> _Ending:
        """End the run on the answer the RETURN names, or on a replan that names it again."""
        # A run that reaches here has nothing left to run, so the plan it ends on has to be
        # one that answers. Returning None instead would hand a caller status ok with no
        # answer, which is the one thing RunResult.answer promises cannot happen.
        output_node_id = state.pipeline.logical_plan.output_node_id
        if output_node_id is None:
            raise BlueDataPlannerError(
                f"plan {state.pipeline.logical_plan.id!r} has nothing left to run but names no "
                "output node, so the run ended on a plan that answers nothing"
            )
        ending = state.pipeline.logical_plan.node(output_node_id)
        if ending.kind is not LogicalOperator.RETURN:
            raise BlueDataPlannerError(
                f"plan {state.pipeline.logical_plan.id!r} names node {output_node_id!r} as its "
                f"output, but it is of kind {ending.kind.value} where RETURN was expected"
            )
        unfinished = [
            node_id for node_id in ending.input_node_ids if state.result_for(node_id) is None
        ]
        if unfinished:
            return _Ending(
                failure_reason=(
                    f"the answer reads node(s) {unfinished}, which produced no rows the run may "
                    "read; write an ending over a node that finished"
                ),
                next_step=_Next.FAILURE_REPLAN,
            )
        columns = [str(name) for name in ending.attributes.get("columns", [])]
        try:
            answer = project_answer(state.inputs_for_node(ending), columns)
        except ReturnAnswerError as error:
            # The columns holding the answer are the logical planner's to name, so a name the
            # rows do not carry is answered by writing the ending again rather than by failing.
            return _Ending(failure_reason=str(error), next_step=_Next.FAILURE_REPLAN)
        return _Ending(status=RunStatus.OK, answer=answer)


def build_data_planner(
    data_registry: DataRegistry,
    *,
    suites: Sequence[OperatorSuite],
    model: str,
    reasoning_effort: str | None = None,
    limits: RunLimits | None = None,
    llm_client_name: str = DEFAULT_LLM_CLIENT_NAME,
) -> DataPlanner:
    """Assemble a planner over these suites, on one model, reading one registry."""
    llm_client = _llm_client(llm_client_name, model, reasoning_effort)
    router = OperatorRouter()
    for suite in suites:
        for operator in suite.operators(
            data_registry=data_registry, llm_client=llm_client, model=model
        ):
            router.register(operator)
    catalog = DataCatalog(data_registry)
    return DataPlanner(
        logical_planner=LogicalPlanner(llm_client, catalog),
        physical_planner=PhysicalPlanner(llm_client, router),
        fragment_runner=FragmentRunner(llm_client, catalog, router),
        catalog_searcher=CatalogSearcher(llm_client, catalog),
        limits=limits or RunLimits(),
    )


def _llm_client(name: str, model: str, reasoning_effort: str | None) -> LLMClient:
    # The name prefixes every property the client reads, which is how blue-data keys them.
    properties: dict[str, Any] = {f"{name}.model": model}
    if reasoning_effort is not None:
        properties[f"{name}.reasoning_effort"] = reasoning_effort
    return LLMClient(name=name, properties=properties)
