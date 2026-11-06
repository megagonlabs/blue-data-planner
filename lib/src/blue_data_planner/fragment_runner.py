"""Writing one fragment's parameters, running it, and reviewing what came back."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from blue_data_planner.data import DataCatalog
from blue_data_planner.execution import execute_physical_operator
from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import render_prompt
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.prompts import prompt_text
from blue_data_planner.prompts.rendering import render_attribute_views, render_sample_rows
from blue_data_planner.types import (
    AttributeView,
    BudgetExhaustedError,
    DataBundle,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    FragmentReviewDecision,
    ParameterAttempt,
    PhysicalOperator,
    PhysicalPlanFragment,
    RunCounters,
    RunStatus,
    summarize_table,
)

SAMPLE_ROWS = 5
# How many of an attribute's values one prompt shows (design/13-decision-log.md, ADR-049).
ATTRIBUTE_VALUES = 5

_REPEAT_MESSAGE = (
    "Those parameters repeat an earlier attempt for this fragment. The operator was not "
    "called: a repeat is not worth spending one of the run's scarce operator calls on the "
    "chance the operator answers differently this time. Write a set that differs from every "
    "attempt tried so far."
)


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
        # Held here rather than inside the loop, so the attempts a budget cut short stay on
        # the result instead of leaving with the exception.
        attempts: list[ParameterAttempt] = []
        try:
            return self._attempt_loop(fragment, operator, inputs, counters, conversation, attempts)
        except BudgetExhaustedError:
            # Catching it is what lets what the runner was doing stay readable, in the
            # conversation the run state holds, rather than leaving with the exception.
            return FragmentResult(
                status=RunStatus.BUDGET_EXHAUSTED,
                attempts=attempts,
                failure_reason="the operator call budget ran out",
            )

    def _attempt_loop(
        self,
        fragment: PhysicalPlanFragment,
        operator: PhysicalOperator,
        inputs: DataBundle,
        counters: RunCounters,
        conversation: Conversation,
        attempts: list[ParameterAttempt],
    ) -> FragmentResult:
        for _spent in range(counters.limits.parameter_repairs + 1):
            # by_alias=True: belt-and-braces alongside schemas.py's serialize_by_alias, which
            # is what actually makes a predicate's and/or/not survive this dump.
            parameters = self._llm_client.complete_structured(
                conversation, operator.parameter_schema
            ).model_dump(by_alias=True)
            # Unhashable (a predicate's and/or/not nests further dicts), so `in` over the
            # parameters already tried, rather than a set, is the check.
            if parameters in [attempt.parameters for attempt in attempts]:
                attempts.append(
                    ParameterAttempt(parameters=parameters, result=None, assessment=None)
                )
                conversation.turns.append(Turn(role="user", content=_REPEAT_MESSAGE))
                continue
            result = execute_physical_operator(
                operator, parameters, inputs, fragment.required_output_columns, counters
            )
            if result.status is ExecutionStatus.FAILED and result.error is not None:
                attempts.append(
                    ParameterAttempt(parameters=parameters, result=result, assessment=None)
                )
                if result.error.kind is ErrorKind.UNRECOVERABLE:
                    return FragmentResult(
                        status=RunStatus.OPERATOR_FAILED,
                        attempts=attempts,
                        failure_reason=result.error.message,
                    )
                if result.error.kind is ErrorKind.OPERATOR_UNAVAILABLE:
                    # A broken implementation cannot be repaired by changing parameters.
                    # Keep the failed attempt and let the cover choose another operator.
                    return FragmentResult(
                        status=RunStatus.NEEDS_REPAIR,
                        attempts=attempts,
                        failure_reason=_exhausted_reason(fragment, attempts),
                    )
                conversation.turns.append(
                    Turn(
                        role="user",
                        content=(
                            "That call failed. Write different parameters that fix this "
                            "problem, changing nothing beyond what fixing it requires.\n\n"
                            f"{result.error.message}"
                        ),
                    )
                )
                continue
            review = self._review(conversation, fragment, result)
            attempts.append(
                ParameterAttempt(parameters=parameters, result=result, assessment=review.assessment)
            )
            if review.decision is FragmentReviewDecision.ACCEPT:
                return FragmentResult(status=RunStatus.OK, attempts=attempts, failure_reason=None)
            if review.decision is FragmentReviewDecision.REPLAN:
                return FragmentResult(
                    status=RunStatus.NEEDS_REPLAN,
                    attempts=attempts,
                    failure_reason=f"{_steps(fragment)} cannot be done: {review.assessment}",
                )
            conversation.turns.append(
                Turn(
                    role="user",
                    content=(
                        "Write different parameters that answer what the review said, "
                        "changing nothing beyond what answering it requires."
                    ),
                )
            )
        return FragmentResult(
            status=RunStatus.NEEDS_REPAIR,
            attempts=attempts,
            failure_reason=_exhausted_reason(fragment, attempts),
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
        return render_prompt(
            prompt_text("fragment_runner_turn.md"),
            instruction=fragment.instruction,
            operator_description=operator.description,
            operator_limitations=operator.limitations or "",
            scan_sources=self._render_scan_sources(fragment),
            attribute_views="\n".join(render_attribute_views(views, "", ATTRIBUTE_VALUES)),
            input_tables=_render_inputs(fragment, inputs),
        )

    def _render_scan_sources(self, fragment: PhysicalPlanFragment) -> str:
        # An operator that reads a registered source takes its catalog path as a parameter, so
        # the path has to be in front of the model that writes that parameter. The row count
        # goes beside it because the attribute counts may be taken over a sample, and a model
        # shown only those reads the sample's size as the entity's.
        lines: list[str] = []
        for node_id, path in sorted(fragment.scan_sources.items()):
            row_count = self._catalog.entity_row_count(path)
            size = "row count not recorded" if row_count is None else f"{row_count} row(s)"
            lines.append(f"- step {node_id} reads {path} ({size})")
        return "\n".join(lines)


def _steps(fragment: PhysicalPlanFragment) -> str:
    # The failure reason is also a replan's reason, and the logical planner knows these nodes
    # by its own instructions, not by the one the physical planner rewrote for the fragment.
    node_ids = sorted(fragment.logical_node_ids)
    label = "Step" if len(node_ids) == 1 else "Steps"
    return f'{label} {", ".join(node_ids)} (executed as "{fragment.instruction}")'


def _exhausted_reason(fragment: PhysicalPlanFragment, attempts: list[ParameterAttempt]) -> str:
    # Both planners read this, so it names no operator and quotes no operator error: the
    # physical planner is shown each attempt beside it, and the logical planner picks no
    # operator. The review words (accept, reject) mean nothing to either of them.
    tried = "its only attempt" if len(attempts) == 1 else f"all {len(attempts)} attempts"
    assessments = [attempt.assessment for attempt in attempts if attempt.assessment is not None]
    if not assessments:
        return f"{_steps(fragment)} failed in {tried}; no attempt returned a result."
    return (
        f"{_steps(fragment)} failed in {tried}. The most recent result that came back was "
        f"wrong: {assessments[-1]}"
    )


def _render_inputs(fragment: PhysicalPlanFragment, inputs: DataBundle) -> str:
    lines: list[str] = []
    for index, table in enumerate(inputs):
        entry = fragment.input_data[index] if index < len(fragment.input_data) else None
        name = entry.name if entry is not None else f"table {index}"
        described = entry.description if entry is not None else "no description recorded"
        summary = summarize_table(table, SAMPLE_ROWS)
        lines.append(
            f"- {name} ({summary.row_count} row(s), columns {summary.columns}): {described}"
        )
        lines.extend(render_sample_rows(summary, "    "))
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
        lines.extend(render_sample_rows(summary, "  "))
    return "\n".join(lines)
