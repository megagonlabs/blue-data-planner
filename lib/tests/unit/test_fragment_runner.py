"""What one fragment's run comes to, and what the run loop is told to do next."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from blue_data_planner.fragment_runner import FragmentRunner
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.types import (
    AttributeView,
    DataBundle,
    ExecutionStatus,
    OperatorOutput,
    OutputColumn,
    PhysicalOperator,
    PhysicalPlanFragment,
    RecoverableOperatorError,
    RunCounters,
    RunLimits,
    RunStatus,
    UnrecoverableOperatorError,
)
from tests.doubles.llm import ScriptedLLMClient


class Predicate(BaseModel):
    """The one parameter the operator under test takes."""

    predicate: str


class FakeCatalog:
    """A catalog whose attribute views are fixed."""

    def attribute_views(self, entity_path: str) -> list[AttributeView]:
        return []


POSTINGS = "/source/jobs/database/jobs/collection/public/entity/postings"


def a_fragment(scan_sources: dict[str, str] | None = None) -> PhysicalPlanFragment:
    """One FILTER node bound to an operator, reading one input table."""
    return PhysicalPlanFragment(
        id="f1",
        logical_node_ids=["n2"],
        root_logical_node_id="n2",
        operator_id="builtin.filter",
        scan_sources=scan_sources or {},
        instruction="keep the Python postings",
        input_data=[],
        required_output_columns=[OutputColumn(name="title", dtype="str")],
    )


def an_operator(behavior: Any) -> PhysicalOperator:
    """One registered operator whose callable is the behavior given."""
    return PhysicalOperator(
        operator_id="builtin.filter",
        logical_operators=frozenset(),
        description="Keeps the rows a predicate holds for.",
        parameter_schema=Predicate,
        call=behavior,
    )


def a_runner(answers: list[Any], behavior: Any) -> FragmentRunner:
    """A runner over one operator, whose model answers with these values in order."""
    return a_runner_over(ScriptedLLMClient(answers), behavior)


def a_runner_over(client: ScriptedLLMClient, behavior: Any) -> FragmentRunner:
    """A runner over one operator, driven by a client the test keeps a handle on."""
    router = OperatorRouter()
    router.register(an_operator(behavior))
    # ScriptedLLMClient and FakeCatalog stand in for LLMClient and DataCatalog
    # structurally; pyright's strict nominal check does not see them as related.
    return FragmentRunner(
        client,  # pyright: ignore[reportArgumentType]
        FakeCatalog(),  # pyright: ignore[reportArgumentType]
        router,
    )


def counters(repairs: int = 2, operator_calls: int = 10) -> RunCounters:
    """Counters bounding one run() call, standing in for the CallBudget a test used to pass."""
    return RunCounters(RunLimits(parameter_repairs=repairs, operator_calls=operator_calls))


def keeps_everything(
    inputs: DataBundle, parameters: dict[str, Any], columns: list[OutputColumn]
) -> OperatorOutput:
    return OperatorOutput(data=list(inputs))


def raises_recoverably(
    inputs: DataBundle, parameters: dict[str, Any], columns: list[OutputColumn]
) -> OperatorOutput:
    raise RecoverableOperatorError("the predicate names no column the table carries")


def raises_unrecoverably(
    inputs: DataBundle, parameters: dict[str, Any], columns: list[OutputColumn]
) -> OperatorOutput:
    raise UnrecoverableOperatorError("the source refused the connection")


ROWS: DataBundle = [[{"title": "SWE"}]]


def test_an_accepted_result_ends_the_fragment_with_the_run_continuing() -> None:
    """A review that accepts is the ordinary ending, and the result is the one kept."""
    runner = a_runner(
        [{"predicate": "title is not null"}, {"decision": "accept", "assessment": "looks right"}],
        keeps_everything,
    )
    result = runner.run(a_fragment(), ROWS, counters())
    assert result.status is RunStatus.OK
    assert len(result.attempts) == 1
    assert result.result is not None
    assert result.result.status is ExecutionStatus.OK
    assert result.result.output == ROWS


def test_a_rejected_result_is_answered_by_writing_the_parameters_again() -> None:
    """Both attempts run against the same input, so no reduction compounds into the next."""
    runner = a_runner(
        [
            {"predicate": "title = 'nothing'"},
            {"decision": "reject", "assessment": "the predicate matched nothing"},
            {"predicate": "title is not null"},
            {"decision": "accept", "assessment": "looks right"},
        ],
        keeps_everything,
    )
    result = runner.run(a_fragment(), ROWS, counters())
    assert result.status is RunStatus.OK
    assert len(result.attempts) == 2


def test_running_out_of_parameter_attempts_escalates() -> None:
    """The runner spends its own bound before the run loop reaches a wider repair."""
    runner = a_runner(
        [
            {"predicate": "a"},
            {"decision": "reject", "assessment": "no"},
            {"predicate": "b"},
            {"decision": "reject", "assessment": "still no"},
        ],
        keeps_everything,
    )
    result = runner.run(a_fragment(), ROWS, counters(repairs=1))
    assert result.status is RunStatus.NEEDS_REPAIR
    assert len(result.attempts) == 2


def test_a_review_asking_for_a_replan_comes_back_with_its_assessment() -> None:
    """No arrangement of operators will do this, so the reason is what the planner reads."""
    runner = a_runner(
        [
            {"predicate": "a"},
            {"decision": "replan", "assessment": "the plan asks for data nobody registered"},
        ],
        keeps_everything,
    )
    result = runner.run(a_fragment(), ROWS, counters())
    assert result.status is RunStatus.NEEDS_REPLAN
    assert result.failure_reason == "the plan asks for data nobody registered"


def test_a_recoverable_operator_failure_is_answered_by_writing_the_parameters_again() -> None:
    """Bad parameters are frequent and cheap to fix, so the narrowest repair runs first."""
    calls: list[int] = []

    def fails_once(
        inputs: DataBundle, parameters: dict[str, Any], columns: list[OutputColumn]
    ) -> OperatorOutput:
        calls.append(1)
        if len(calls) == 1:
            raise RecoverableOperatorError("no such column")
        return OperatorOutput(data=list(inputs))

    runner = a_runner(
        [
            {"predicate": "a"},
            {"predicate": "title is not null"},
            {"decision": "accept", "assessment": "looks right"},
        ],
        fails_once,
    )
    result = runner.run(a_fragment(), ROWS, counters())
    assert result.status is RunStatus.OK
    assert len(result.attempts) == 2


def test_an_escalation_after_a_trailing_operator_failure_reports_the_operators_error() -> None:
    """The last attempt has no review assessment; its error message is what a repair reads."""
    runner = a_runner([{"predicate": "a"}], raises_recoverably)
    result = runner.run(a_fragment(), ROWS, counters(repairs=0))
    assert result.status is RunStatus.NEEDS_REPAIR
    assert result.failure_reason == "the predicate names no column the table carries"


def test_an_unrecoverable_operator_failure_stops_the_run_without_a_review_turn() -> None:
    """No other operator would avoid the same failure, so there is nothing to review."""
    runner = a_runner([{"predicate": "a"}], raises_unrecoverably)
    result = runner.run(a_fragment(), ROWS, counters())
    assert result.status is RunStatus.OPERATOR_FAILED


def test_a_budget_that_runs_out_mid_fragment_returns_the_attempts_already_made() -> None:
    """Those attempts are the record of what the runner was doing when the request ended."""
    runner = a_runner(
        [
            {"predicate": "a"},
            {"decision": "reject", "assessment": "no"},
            {"predicate": "b"},
        ],
        keeps_everything,
    )
    result = runner.run(a_fragment(), ROWS, counters(operator_calls=1))
    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert len(result.attempts) == 1


def test_the_prompt_names_the_entity_path_each_scan_is_bound_to() -> None:
    """A scan operator takes that path back as a parameter, so the model has to be shown it."""
    client = ScriptedLLMClient(
        [{"predicate": "title is not null"}, {"decision": "accept", "assessment": "looks right"}]
    )
    a_runner_over(client, keeps_everything).run(
        a_fragment(scan_sources={"n1": POSTINGS}), ROWS, counters()
    )
    _role, content = client.conversations[0][0]
    assert POSTINGS in content
