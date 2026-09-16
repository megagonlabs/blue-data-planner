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
from blue_data_planner.run_state import RunState
from blue_data_planner.types import (
    BlueDataPlannerError,
    CallBudget,
    ControlOperator,
    DataBundle,
    LogicalOperator,
    PhysicalPlanFragment,
    PlanLimits,
    PlanResult,
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
        limits: PlanLimits,
    ) -> None:
        self._logical_planner = logical_planner
        self._physical_planner = physical_planner
        self._fragment_runner = fragment_runner
        self._catalog_searcher = catalog_searcher
        self._limits = limits

    def run(self, request: str) -> PlanResult:
        """Plan, cover, execute, and repair until the request ends."""
        state = RunState(request=request, budget=CallBudget(self._limits.operator_calls))
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
        return state.to_result(status, answer)

    def plan(self, request: str) -> PlanResult:
        """Write one logical plan and one cover, then stop."""
        state = RunState(request=request, budget=CallBudget(self._limits.operator_calls))
        try:
            status = self._plan_and_cover(state, reason=None, retry_uncoverable_plan=False)
        except LLMError as error:
            state.failure_reason = str(error)
            return state.to_result(RunStatus.LLM_FAILED)
        except BlueDataPlannerError as error:
            state.failure_reason = str(error)
            return state.to_result(RunStatus.PLANNER_FAILED)
        return state.to_result(status if status is not None else RunStatus.PLANNED)

    def _run_pass(self, state: RunState) -> RunStatus | None:
        ready = state.next_ready()
        if ready is None:
            return RunStatus.OK
        if isinstance(ready, PhysicalPlanFragment):
            return self._run_fragment(state, ready)
        return self._resolve_control_nodes(state)

    def _run_fragment(self, state: RunState, fragment: PhysicalPlanFragment) -> RunStatus | None:
        result = self._fragment_runner.run(fragment, state.inputs_for(fragment), state.budget)
        state.record_fragment(result)
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
        if spent < self._limits.fragment_attempts:
            state.fragment_attempts[node_ids] = spent + 1
            written = self._physical_planner.recover_fragment(
                state.logical_plan, state.cover, node_ids, state.current_results, reason
            )
            state.record_usage(written.usage)
            if written.plan is not None:
                state.install_cover(written.plan)
                return None
            return self._replan(state, written.failure_reason)
        if state.cover_attempts < self._limits.cover_attempts:
            state.cover_attempts += 1
            written = self._physical_planner.cover(
                state.logical_plan, state.current_results, reason
            )
            state.record_usage(written.usage)
            if written.plan is not None:
                state.install_cover(written.plan)
                return None
            return self._replan(state, written.failure_reason)
        return self._replan(state, reason)

    def _replan(self, state: RunState, reason: str | None) -> RunStatus | None:
        if state.failure_replans >= self._limits.failure_replans:
            state.failure_reason = reason
            return RunStatus.REPLAN_EXHAUSTED
        state.failure_replans += 1
        return self._plan_and_cover(state, reason)

    def _resolve_control_nodes(self, state: RunState) -> RunStatus | None:
        reasons: list[str] = []
        for node in state.ready_control_nodes():
            reasons.append(str(node.attributes.get("instruction", "")))
            if node.kind is not ControlOperator.DISCOVER:
                continue
            found = self._catalog_searcher.search(node, state.inputs_for_node(node))
            state.record_usage(found.usage)
            state.record_discovery(node.id, found.discovery)
        if state.control_replans >= self._limits.control_replans:
            state.failure_reason = "\n".join(reasons)
            return RunStatus.REPLAN_EXHAUSTED
        state.control_replans += 1
        return self._plan_and_cover(state, "\n".join(reasons))

    def _plan_and_cover(
        self, state: RunState, reason: str | None, retry_uncoverable_plan: bool = True
    ) -> RunStatus | None:
        parent = state.logical_plan if state.pipeline.current_logical_plan_id is not None else None
        carried = state.carried_node_ids() if parent is not None else frozenset[str]()
        results = state.current_results if parent is not None else {}
        written = self._logical_planner.generate(
            state.request,
            parent=parent,
            carried_node_ids=carried,
            results=results,
            reason=reason,
        )
        state.record_rejected_plans(written.rejected)
        state.record_usage(written.usage)
        if written.plan is None:
            state.failure_reason = written.rejected[-1].error if written.rejected else None
            return written.status
        state.install_logical_plan(written.plan)
        cover = self._physical_planner.cover(written.plan, state.current_results, reason)
        state.record_usage(cover.usage)
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

    def _answer(self, state: RunState) -> DataBundle:
        """Project the answer the ending names, raising unless the run really produced one."""
        # A run that reaches here has nothing left to run, so the plan it ends on has to be
        # one that answers. Returning None instead would hand a caller status ok with no
        # answer, which is the one thing PlanResult.answer promises cannot happen.
        output_node_id = state.logical_plan.output_node_id
        if output_node_id is None:
            raise BlueDataPlannerError(
                f"plan {state.logical_plan.id!r} has nothing left to run but names no output "
                "node, so the run ended on a plan that answers nothing"
            )
        ending = state.logical_plan.node(output_node_id)
        if ending.kind is not LogicalOperator.RETURN:
            raise BlueDataPlannerError(
                f"plan {state.logical_plan.id!r} names node {output_node_id!r} as its output, "
                f"but it is of kind {ending.kind.value} where RETURN was expected"
            )
        columns = [str(name) for name in ending.attributes.get("columns", [])]
        return project_answer(state.inputs_for_node(ending), columns)


def build_data_planner(
    router: OperatorRouter,
    catalog: DataCatalog,
    llm_client: LLMClient,
    limits: PlanLimits | None = None,
) -> DataPlanner:
    """Assemble the four components over one router, one catalog, and one model client."""
    bounds = limits or PlanLimits()
    return DataPlanner(
        logical_planner=LogicalPlanner(llm_client, catalog, bounds.plan_corrections),
        physical_planner=PhysicalPlanner(llm_client, router),
        fragment_runner=FragmentRunner(llm_client, catalog, router, bounds.parameter_attempts),
        catalog_searcher=CatalogSearcher(llm_client, catalog, bounds.search_terms),
        limits=bounds,
    )
