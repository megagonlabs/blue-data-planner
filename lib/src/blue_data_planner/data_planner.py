"""The public entry point: the loop from a request to an answer."""

from __future__ import annotations

from blue_data_planner.catalog_searcher import CatalogSearcher
from blue_data_planner.data import DataCatalog
from blue_data_planner.execution import project_answer
from blue_data_planner.fragment_runner import FragmentRunner
from blue_data_planner.llm import LLMClient, LLMError
from blue_data_planner.logical_planner import LogicalPlanner
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.run_state import NodeResult, RunState
from blue_data_planner.types import (
    BlueDataPlannerError,
    ControlOperator,
    DataBundle,
    LogicalOperator,
    PhysicalPlanFragment,
    RunCounters,
    RunLimits,
    RunResult,
    RunStatus,
)


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
        answer: DataBundle | None = None
        try:
            status = self._plan_and_cover(state, reason=None)
            while status is None:
                status = self._run_pass(state)
            if status is RunStatus.OK:
                answer = self._answer(state)
        except LLMError as error:
            # The one try in the library, so no component handles a provider failure.
            status = RunStatus.LLM_FAILED
            state.failure_reason = str(error)
        except BlueDataPlannerError as error:
            # A defect rather than an outcome a repair could answer: every recoverable
            # failure is already a status by the time it reaches here. The run ends, keeping
            # what it built and what broke, rather than throwing at whoever called the
            # library.
            status = RunStatus.PLANNER_FAILED
            state.failure_reason = str(error)
        return state.to_result(status, state.failure_reason, answer)

    def plan(self, request: str) -> RunResult:
        """Write one logical plan and one cover, then stop."""
        state = RunState(request=request, counters=RunCounters(self._limits))
        try:
            status = self._plan_and_cover(state, reason=None, retry_uncoverable_plan=False)
        except LLMError as error:
            state.failure_reason = str(error)
            return state.to_result(RunStatus.LLM_FAILED, state.failure_reason)
        except BlueDataPlannerError as error:
            state.failure_reason = str(error)
            return state.to_result(RunStatus.PLANNER_FAILED, state.failure_reason)
        return state.to_result(
            status if status is not None else RunStatus.PLANNED, state.failure_reason
        )

    def _run_pass(self, state: RunState) -> RunStatus | None:
        ready = state.next_ready()
        if ready is None:
            return RunStatus.OK
        if isinstance(ready, PhysicalPlanFragment):
            return self._run_fragment(state, ready)
        return self._resolve_control_nodes(state)

    def _run_fragment(self, state: RunState, fragment: PhysicalPlanFragment) -> RunStatus | None:
        result = self._fragment_runner.run(fragment, state.inputs_for(fragment), state.counters)
        state.record_fragment(fragment, result)
        if result.status is RunStatus.OK:
            return None
        if result.status is RunStatus.NEEDS_REPLAN:
            return self._replan(state, result.failure_reason)
        if result.status is RunStatus.NEEDS_REPAIR:
            return self._repair(
                state, fragment, result.failure_reason or "the fragment was rejected"
            )
        state.failure_reason = result.failure_reason
        return result.status

    def _repair(
        self, state: RunState, fragment: PhysicalPlanFragment, reason: str
    ) -> RunStatus | None:
        node_ids = frozenset(fragment.logical_node_ids)
        spent = state.fragment_attempts.get(node_ids, 0)
        if spent < self._limits.fragment_repairs:
            state.fragment_attempts[node_ids] = spent + 1
            written = self._physical_planner.recover_fragment(
                state.pipeline.logical_plan,
                state.pipeline.cover,
                node_ids,
                self._results_by_node_id(state),
                reason,
            )
            if written.plan is not None:
                state.install_cover(written.plan)
                return None
            return self._replan(state, written.failure_reason)
        if state.cover_attempts < self._limits.cover_repairs:
            state.cover_attempts += 1
            written = self._physical_planner.cover(
                state.pipeline.logical_plan,
                state.pipeline.physical_plans.get(state.pipeline.current_physical_plan_id or ""),
                self._results_by_node_id(state),
                reason,
            )
            if written.plan is not None:
                state.install_cover(written.plan)
                return None
            return self._replan(state, written.failure_reason)
        return self._replan(state, reason)

    def _replan(self, state: RunState, reason: str | None) -> RunStatus | None:
        if not state.counters.spend_failure_replan():
            state.failure_reason = reason
            return RunStatus.REPLAN_EXHAUSTED
        return self._plan_and_cover(state, reason)

    def _resolve_control_nodes(self, state: RunState) -> RunStatus | None:
        reasons: list[str] = []
        for node in state.ready_control_nodes():
            reasons.append(str(node.attributes.get("instruction", "")))
            if node.kind is not ControlOperator.DISCOVER:
                continue
            found = self._catalog_searcher.search(node, state.inputs_for_node(node))
            state.record_discovery(node, found.discovery)
        if not state.counters.spend_control_replan():
            state.failure_reason = "\n".join(reasons)
            return RunStatus.REPLAN_EXHAUSTED
        return self._plan_and_cover(state, "\n".join(reasons))

    def _plan_and_cover(
        self, state: RunState, reason: str | None, retry_uncoverable_plan: bool = True
    ) -> RunStatus | None:
        # carried and held are read off the plan and cover that are still current, before the
        # new logical plan is installed: the signatures they rest on belong to that pairing,
        # and reading them after the install would resolve node ids against a graph those
        # signatures do not describe.
        has_cover = state.pipeline.current_physical_plan_id is not None
        parent = state.pipeline.logical_plan if has_cover else None
        carried = state.carried_node_ids() if has_cover else frozenset[str]()
        held = self._results_by_node_id(state)
        written = self._logical_planner.generate(
            state.request,
            state.counters,
            parent=parent,
            carried_node_ids=carried,
            results=held,
            reason=reason,
        )
        if written.plan is None:
            state.failure_reason = written.rejected[-1].error if written.rejected else None
            return written.status
        state.install_logical_plan(written.plan)
        cover = self._physical_planner.cover(
            written.plan,
            state.pipeline.physical_plans.get(state.pipeline.current_physical_plan_id or ""),
            held,
            reason,
        )
        if cover.plan is None:
            if not retry_uncoverable_plan:
                # plan writes one logical plan and attempts one cover, then stops, whatever
                # that attempt came back with.
                state.failure_reason = cover.failure_reason
                return RunStatus.PLANNED
            # A plan no operator can cover goes straight back to the logical planner, with
            # no operator call spent.
            return self._replan(state, cover.failure_reason)
        state.install_cover(cover.plan)
        return None

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

    def _answer(self, state: RunState) -> DataBundle:
        """Project the answer the ending names, raising unless the run really produced one."""
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
        columns = [str(name) for name in ending.attributes.get("columns", [])]
        return project_answer(state.inputs_for_node(ending), columns)


def build_data_planner(
    router: OperatorRouter,
    catalog: DataCatalog,
    llm_client: LLMClient,
    limits: RunLimits | None = None,
) -> DataPlanner:
    """Assemble the four components over one router, one catalog, and one model client."""
    return DataPlanner(
        logical_planner=LogicalPlanner(llm_client, catalog),
        physical_planner=PhysicalPlanner(llm_client, router),
        fragment_runner=FragmentRunner(llm_client, catalog, router),
        catalog_searcher=CatalogSearcher(llm_client, catalog),
        limits=limits or RunLimits(),
    )
