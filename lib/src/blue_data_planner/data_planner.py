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
from blue_data_planner.llm import LLMClient
from blue_data_planner.logical_planner import LogicalPlanner
from blue_data_planner.operators import OperatorRouter, OperatorSuite
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.run_state import NodeResult, RunState
from blue_data_planner.types import (
    BlueDataPlannerError,
    ControlOperator,
    DataBundle,
    DiscoveryResult,
    FailedFragment,
    LLMError,
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
    # Why the loop ended, which is not always a failure: a resolved DISCOVER ends a pass too.
    reason: str | None = None
    answer: DataBundle | None = None
    next_step: _Next | None = None
    # Set on a cover repair alone; its result carries the failure reason.
    failed_fragment: FailedFragment | None = None


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
        return self._run_request(request, execute=True)

    def plan(self, request: str) -> RunResult:
        """Plan, search the catalog, and cover as run would, stopping before any operator call."""
        return self._run_request(request, execute=False)

    def _run_request(self, request: str, *, execute: bool) -> RunResult:
        state = RunState(request=request, counters=RunCounters(self._limits))
        try:
            status, failure_reason, answer = self._run_plans(state, execute)
        except LLMError as error:
            # The one try in the library, so no component handles a provider failure. It precedes
            # the clause below, which would otherwise end a provider outage as planner_failed.
            status, failure_reason, answer = RunStatus.LLM_FAILED, str(error), None
        except BlueDataPlannerError as error:
            # A defect rather than an outcome a repair could answer: every recoverable failure is
            # already a status by the time it reaches here. The run ends keeping what it built and
            # what broke, rather than throwing at whoever called the library.
            status, failure_reason, answer = RunStatus.PLANNER_FAILED, str(error), None
        return state.to_result(status, failure_reason, answer)

    def _run_plans(
        self, state: RunState, execute: bool
    ) -> tuple[RunStatus, str | None, DataBundle | None]:
        """The replan loop: write a plan, run it, and write another while a replan is allowed."""
        replan_reason: str | None = None
        shown_discovery_node_ids: frozenset[str] = frozenset()
        while True:
            has_cover = state.pipeline.current_physical_plan_id is not None
            # Read before the new version is installed: the signatures the results sit under
            # belong to the plan and the cover that are still current.
            held = self._results_by_node_id(state)
            carried = state.carried_node_ids() if has_cover else frozenset[str]()
            parent = state.pipeline.logical_plan if has_cover else None
            discovery_node_ids = frozenset(
                node_id for node_id, result in held.items() if isinstance(result, DiscoveryResult)
            )
            written = self._write_plan(
                state,
                held,
                carried,
                parent,
                replan_reason,
                discovery_node_ids - shown_discovery_node_ids,
            )
            shown_discovery_node_ids = discovery_node_ids
            if written.plan is None:
                return written.status, written.failure_reason, None
            ending = self._run_covers(state, written.plan, held, execute)
            if ending.status is not None:
                return ending.status, ending.reason, ending.answer
            replan_reason = ending.reason
            spent = (
                state.counters.spend_control_replan()
                if ending.next_step is _Next.CONTROL_REPLAN
                else state.counters.spend_failure_replan()
            )
            if not spent:
                return RunStatus.REPLAN_EXHAUSTED, replan_reason, None

    def _run_covers(
        self, state: RunState, plan: LogicalPlan, held: dict[str, NodeResult], execute: bool
    ) -> _Ending:
        """The cover loop: cover this plan, run the cover, and cover it again while allowed."""
        failed_fragment: FailedFragment | None = None
        for _repair in range(self._limits.cover_repairs + 1):
            written = self._write_cover(state, plan, held, failed_fragment)
            if written.plan is None:
                # A plan no operator can cover goes back to the logical planner, with no operator
                # call spent, and never enters the pipeline.
                return _Ending(reason=written.failure_reason, next_step=_Next.FAILURE_REPLAN)
            # The version and its cover are installed together, so the two pointers always name a
            # matching pair and every signature describes the plan and cover that are current.
            state.install_logical_plan(plan)
            state.install_cover(written.plan)
            ending = self._run_passes(state, execute)
            if ending.next_step is not _Next.COVER_REPAIR:
                return ending
            failed_fragment = ending.failed_fragment
            held = self._results_by_node_id(state)
        if failed_fragment is None:
            raise BlueDataPlannerError(
                "the cover repairs ran out with no failed fragment recorded; every cover repair "
                "is asked for by a fragment that failed"
            )
        return _Ending(reason=failed_fragment.result.failure_reason, next_step=_Next.FAILURE_REPLAN)

    def _run_passes(self, state: RunState, execute: bool) -> _Ending:
        """Run everything the current cover makes ready, one fragment or one pass at a time."""
        while True:
            ready = state.next_ready()
            if not execute and (ready is None or isinstance(ready, PhysicalPlanFragment)):
                # A ready fragment is the first operator call, and the planning-only operation
                # ends just before it; it never reaches an answer, so it never projects one.
                return _Ending(status=RunStatus.PLANNED)
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
            return _Ending(reason=result.failure_reason, next_step=_Next.FAILURE_REPLAN)
        if result.status is RunStatus.NEEDS_REPAIR:
            if result.failure_reason is None:
                raise BlueDataPlannerError(
                    f"fragment {fragment.id!r} ended needing a repair with no failure reason; "
                    "the fragment runner always writes one"
                )
            return _Ending(
                next_step=_Next.COVER_REPAIR,
                failed_fragment=FailedFragment(fragment=fragment, result=result),
            )
        return _Ending(status=result.status, reason=result.failure_reason)

    def _resolve_control_nodes(self, state: RunState) -> _Ending:
        """Resolve every control node ready at once, then return to the logical planner."""
        reasons: list[str] = []
        for node in state.ready_control_nodes():
            reasons.append(node.instruction)
            if node.kind is not ControlOperator.DISCOVER:
                continue
            found = self._catalog_searcher.search(
                node,
                state.conversations.for_catalog_searcher(state.signature_of(node.id)),
                state.inputs_for_node(node),
            )
            state.record_discovery(node, found)
        return _Ending(reason="\n".join(reasons), next_step=_Next.CONTROL_REPLAN)

    def _write_plan(
        self,
        state: RunState,
        held: dict[str, NodeResult],
        carried_node_ids: frozenset[str],
        parent: LogicalPlan | None,
        replan_reason: str | None,
        new_discovery_node_ids: frozenset[str],
    ) -> LogicalPlanResult:
        return self._logical_planner.generate(
            state.request,
            state.conversations.logical_planner,
            state.counters,
            parent=parent,
            carried_node_ids=carried_node_ids,
            results=held,
            replan_reason=replan_reason,
            new_discovery_node_ids=new_discovery_node_ids,
        )

    def _write_cover(
        self,
        state: RunState,
        plan: LogicalPlan,
        held: dict[str, NodeResult],
        failed_fragment: FailedFragment | None,
    ) -> PhysicalPlanResult:
        current = state.pipeline.current_physical_plan_id
        return self._physical_planner.cover(
            state.request,
            plan,
            state.conversations.for_physical_planner(plan.id),
            state.pipeline.physical_plans.get(current) if current else None,
            held,
            failed_fragment,
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
                reason=(
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
            return _Ending(reason=str(error), next_step=_Next.FAILURE_REPLAN)
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
