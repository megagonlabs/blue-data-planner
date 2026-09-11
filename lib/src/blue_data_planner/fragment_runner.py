"""Writing one fragment's parameters, running it, and reviewing what came back."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from blue_data_planner.data import DataCatalog
from blue_data_planner.execution import execute_physical_operator
from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import safe_substitute
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import (
    AttributeView,
    BudgetExhaustedError,
    CallBudget,
    DataBundle,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    FragmentAttempt,
    FragmentResult,
    FragmentReviewDecision,
    PhysicalOperator,
    PhysicalPlanFragment,
    RunDecision,
    RunStatus,
)

SAMPLE_ROWS = 5


class Review(BaseModel):
    """What one review turn decided about one result."""

    model_config = ConfigDict(extra="forbid")

    decision: FragmentReviewDecision
    assessment: str


class FragmentRunner:
    """Runs one ready fragment, writing its parameters again while a review rejects them."""

    def __init__(
        self,
        llm_client: LLMClient,
        catalog: DataCatalog,
        router: OperatorRouter,
        max_parameter_attempts: int,
    ) -> None:
        self._llm_client = llm_client
        self._catalog = catalog
        self._router = router
        self._max_parameter_attempts = max_parameter_attempts

    def run(
        self, fragment: PhysicalPlanFragment, inputs: DataBundle, budget: CallBudget
    ) -> FragmentResult:
        """Run this fragment until a review accepts a result or the attempts run out."""
        operator = self._router.resolve(fragment.operator_id)
        conversation = Conversation(
            turns=[Turn(role="system", content=self._prompt(fragment, operator, inputs))]
        )
        attempts: list[FragmentAttempt] = []
        try:
            return self._attempt_loop(fragment, operator, inputs, budget, conversation, attempts)
        except BudgetExhaustedError:
            # Catching it is what lets the attempts already made, and their usage, be
            # returned rather than lost with the exception.
            return _ended(
                RunStatus.BUDGET_EXHAUSTED,
                RunDecision.STOP,
                attempts,
                "the operator call budget ran out",
                conversation,
            )

    def _attempt_loop(
        self,
        fragment: PhysicalPlanFragment,
        operator: PhysicalOperator,
        inputs: DataBundle,
        budget: CallBudget,
        conversation: Conversation,
        attempts: list[FragmentAttempt],
    ) -> FragmentResult:
        for _spent in range(self._max_parameter_attempts):
            parameters = self._llm_client.complete_structured(
                conversation, operator.parameter_schema
            ).model_dump()
            result = execute_physical_operator(
                operator, parameters, inputs, fragment.required_output_columns, budget
            )
            if (
                result.status is ExecutionStatus.FAILED
                and result.error is not None
                and result.error.kind is ErrorKind.UNRECOVERABLE
            ):
                attempts.append(
                    _attempt(fragment, parameters, result, None, FragmentReviewDecision.REJECT)
                )
                return _ended(
                    RunStatus.OPERATOR_FAILED,
                    RunDecision.STOP,
                    attempts,
                    result.error.message,
                    conversation,
                )
            if result.status is ExecutionStatus.FAILED and result.error is not None:
                attempts.append(
                    _attempt(fragment, parameters, result, None, FragmentReviewDecision.REJECT)
                )
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
            attempts.append(
                _attempt(fragment, parameters, result, review.assessment, review.decision)
            )
            if review.decision is FragmentReviewDecision.ACCEPT:
                return _ended(RunStatus.OK, RunDecision.CONTINUE, attempts, None, conversation)
            if review.decision is FragmentReviewDecision.REPLAN:
                return _ended(
                    RunStatus.OK, RunDecision.REPLAN, attempts, review.assessment, conversation
                )
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
            RunStatus.OK,
            RunDecision.ESCALATE,
            attempts,
            _escalation_reason(attempts),
            conversation,
        )

    def _review(
        self,
        conversation: Conversation,
        fragment: PhysicalPlanFragment,
        result: ExecutionResult,
    ) -> Review:
        conversation.turns.append(Turn(role="user", content=_render_result(fragment, result)))
        return self._llm_client.complete_structured(conversation, Review)

    def _prompt(
        self, fragment: PhysicalPlanFragment, operator: PhysicalOperator, inputs: DataBundle
    ) -> str:
        views: list[AttributeView] = []
        for entity_path in fragment.scan_sources.values():
            views.extend(self._catalog.attribute_views(entity_path))
        return safe_substitute(
            prompt_text("fragment_runner.md"),
            instruction=fragment.instruction,
            operator_description=operator.description,
            operator_limitations=operator.limitations or "",
            scan_sources=_render_scan_sources(fragment),
            attribute_views=_render_views(views),
            input_tables=_render_inputs(fragment, inputs),
        )


def _attempt(
    fragment: PhysicalPlanFragment,
    parameters: dict[str, Any],
    result: ExecutionResult | None,
    assessment: str | None,
    decision: FragmentReviewDecision,
) -> FragmentAttempt:
    return FragmentAttempt(
        # A fragment arrives without the cover it was cut from, so RunState.record_fragment
        # stamps the cover id on every attempt it takes.
        physical_plan_id="",
        fragment_id=fragment.id,
        logical_node_ids=list(fragment.logical_node_ids),
        operator_id=fragment.operator_id,
        operator_attributes=parameters,
        result=result,
        assessment=assessment,
        decision=decision,
    )


def _escalation_reason(attempts: list[FragmentAttempt]) -> str:
    # The last attempt has no assessment when it ended in a recoverable operator failure
    # rather than a review; its error message is the only account of what went wrong, and
    # Task 6 passes this straight into recover_fragment's non-optional reason, so it must
    # never come back None.
    if not attempts:
        return "no attempt produced a result"
    last = attempts[-1]
    if last.assessment is not None:
        return last.assessment
    if last.result is not None and last.result.error is not None:
        return last.result.error.message
    return "no attempt produced a result"


def _ended(
    status: RunStatus,
    decision: RunDecision,
    attempts: list[FragmentAttempt],
    failure_reason: str | None,
    conversation: Conversation,
) -> FragmentResult:
    return FragmentResult(
        status=status,
        decision=decision,
        attempts=attempts,
        failure_reason=failure_reason,
        usage=list(conversation.usage),
    )


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
        columns = sorted(table[0]) if table else []
        lines.append(f"- {name}: {described}. {len(table)} row(s), columns {columns}")
        for row in table[:SAMPLE_ROWS]:
            lines.append(f"    {row}")
    return "\n".join(lines)


def _render_result(fragment: PhysicalPlanFragment, result: ExecutionResult) -> str:
    tables = result.output or []
    rows = tables[0][:SAMPLE_ROWS] if tables else []
    lines = [
        "The call returned. Review it.",
        f"Instruction: {fragment.instruction}",
        f"Steps this call absorbed: {sorted(fragment.logical_node_ids)}",
        f"Rows: {result.row_count}",
    ]
    lines.extend(f"  {row}" for row in rows)
    return "\n".join(lines)
