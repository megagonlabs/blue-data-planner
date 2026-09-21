"""Writing one fragment's parameters, running it, and reviewing what came back."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from blue_data_planner.data import DataCatalog
from blue_data_planner.execution import execute_physical_operator
from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import safe_substitute
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import (
    AttributeView,
    BudgetExhaustedError,
    DataBundle,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    FragmentReviewDecision,
    PhysicalOperator,
    PhysicalPlanFragment,
    RunCounters,
    RunStatus,
    summarize_table,
)

SAMPLE_ROWS = 5


class Review(BaseModel):
    """What one review turn decided about one result."""

    model_config = ConfigDict(extra="forbid")

    decision: FragmentReviewDecision = Field(
        description=(
            "Whether the rows do what the fragment's instruction asked: accept, reject when a "
            "different attempt could fix it, replan when no arrangement of operators will do it."
        )
    )
    # A rejection's assessment is read again by the cover repair, which has none of these turns
    # in front of it, so it has to stand on its own.
    assessment: str = Field(
        description=(
            "For accept, one short sentence saying the result answers the instruction. For "
            "reject and replan, what is wrong and what a different attempt has to do, written "
            "so it reads on its own."
        )
    )


class FragmentRunner:
    """Runs one ready fragment, writing its parameters again while a review rejects them."""

    def __init__(
        self,
        llm_client: LLMClient,
        catalog: DataCatalog,
        router: OperatorRouter,
    ) -> None:
        self._llm_client = llm_client
        self._catalog = catalog
        self._router = router

    def run(
        self,
        fragment: PhysicalPlanFragment,
        conversation: Conversation,
        inputs: DataBundle,
        counters: RunCounters,
    ) -> FragmentResult:
        """Run this fragment until a review accepts a result or the repairs run out."""
        operator = self._router.resolve(fragment.operator_id)
        if not conversation.turns:
            conversation.turns.append(
                Turn(role="system", content=prompt_text("fragment_runner.md"))
            )
        conversation.turns.append(Turn(role="user", content=self._ask(fragment, operator, inputs)))
        try:
            return self._attempt_loop(fragment, operator, inputs, counters, conversation)
        except BudgetExhaustedError:
            # Catching it is what lets what the runner was doing stay readable, in the
            # conversation the run state holds, rather than leaving with the exception.
            return _ended(RunStatus.BUDGET_EXHAUSTED, None, "the operator call budget ran out")

    def _attempt_loop(
        self,
        fragment: PhysicalPlanFragment,
        operator: PhysicalOperator,
        inputs: DataBundle,
        counters: RunCounters,
        conversation: Conversation,
    ) -> FragmentResult:
        last_assessment: str | None = None
        last_error: str | None = None
        last_result: ExecutionResult | None = None
        for _spent in range(counters.limits.parameter_repairs + 1):
            # by_alias=True: belt-and-braces alongside schemas.py's serialize_by_alias, which
            # is what actually makes a predicate's and/or/not survive this dump.
            parameters = self._llm_client.complete_structured(
                conversation, operator.parameter_schema
            ).model_dump(by_alias=True)
            result = execute_physical_operator(
                operator, parameters, inputs, fragment.required_output_columns, counters
            )
            last_result = result
            if (
                result.status is ExecutionStatus.FAILED
                and result.error is not None
                and result.error.kind is ErrorKind.UNRECOVERABLE
            ):
                return _ended(RunStatus.OPERATOR_FAILED, result, result.error.message)
            if result.status is ExecutionStatus.FAILED and result.error is not None:
                last_assessment = None
                last_error = result.error.message
                conversation.turns.append(
                    Turn(
                        role="user",
                        content=(
                            "That call failed. Write the parameters again, fixing this and "
                            f"nothing else.\n\n{result.error.message}"
                        ),
                    )
                )
                continue
            review = self._review(conversation, fragment, result)
            last_assessment = review.assessment
            last_error = None
            if review.decision is FragmentReviewDecision.ACCEPT:
                return _ended(RunStatus.OK, result, None)
            if review.decision is FragmentReviewDecision.REPLAN:
                return _ended(RunStatus.NEEDS_REPLAN, result, review.assessment)
            conversation.turns.append(
                Turn(
                    role="user",
                    content=(
                        "Write the parameters again, answering what the review said and "
                        "nothing else."
                    ),
                )
            )
        return _ended(
            RunStatus.NEEDS_REPAIR, last_result, _escalation_reason(last_assessment, last_error)
        )

    def _review(
        self,
        conversation: Conversation,
        fragment: PhysicalPlanFragment,
        result: ExecutionResult,
    ) -> Review:
        conversation.turns.append(Turn(role="user", content=_render_result(fragment, result)))
        return self._llm_client.complete_structured(conversation, Review)

    def _ask(
        self, fragment: PhysicalPlanFragment, operator: PhysicalOperator, inputs: DataBundle
    ) -> str:
        views: list[AttributeView] = []
        for entity_path in fragment.scan_sources.values():
            views.extend(self._catalog.attribute_views(entity_path))
        return safe_substitute(
            prompt_text("fragment_runner_turn.md"),
            instruction=fragment.instruction,
            operator_description=operator.description,
            operator_limitations=operator.limitations or "",
            scan_sources=_render_scan_sources(fragment),
            attribute_views=_render_views(views),
            input_tables=_render_inputs(fragment, inputs),
        )


def _escalation_reason(assessment: str | None, error: str | None) -> str:
    # This is passed straight into the cover repair's non-optional reason, so it must never come
    # back None. A trailing recoverable failure left no review turn to quote.
    return assessment or error or "no attempt produced a result"


def _ended(
    status: RunStatus, result: ExecutionResult | None, failure_reason: str | None
) -> FragmentResult:
    return FragmentResult(status=status, result=result, failure_reason=failure_reason)


def _render_scan_sources(fragment: PhysicalPlanFragment) -> str:
    # An operator that reads a registered source takes its catalog path as a parameter, so
    # the path has to be in front of the model that writes that parameter.
    return "\n".join(
        f"- step {node_id} reads {path}" for node_id, path in sorted(fragment.scan_sources.items())
    )


def _render_views(views: list[AttributeView]) -> str:
    lines: list[str] = []
    for view in views:
        dtype = view.dtype or "type not recorded"
        described = view.description or "no description recorded"
        samples = ", ".join(repr(value) for value in view.sample_values)
        lines.append(f"- {view.ref.entity}.{view.ref.attribute} [{dtype}]: {described}")
        if samples:
            lines.append(f"    values look like: {samples}")
    return "\n".join(lines)


def _render_inputs(fragment: PhysicalPlanFragment, inputs: DataBundle) -> str:
    lines: list[str] = []
    for index, table in enumerate(inputs):
        entry = fragment.input_data[index] if index < len(fragment.input_data) else None
        name = entry.name if entry is not None else f"table {index}"
        described = entry.description if entry is not None else "no description recorded"
        summary = summarize_table(table, SAMPLE_ROWS)
        lines.append(
            f"- {name}: {described}. {summary.row_count} row(s), columns {summary.columns}"
        )
        for row in summary.sample_rows:
            lines.append(f"    {row}")
    return "\n".join(lines)


def _render_result(fragment: PhysicalPlanFragment, result: ExecutionResult) -> str:
    tables = result.output or []
    summary = summarize_table(tables[0], SAMPLE_ROWS) if tables else None
    lines = [
        "The call returned. Review it.",
        f"Instruction: {fragment.instruction}",
        f"Rows: {result.row_count}",
    ]
    if summary is not None:
        lines.extend(f"  {row}" for row in summary.sample_rows)
    return "\n".join(lines)
