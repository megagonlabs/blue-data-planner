"""What one operator call comes back as, and what it costs."""

from __future__ import annotations

import pytest

from blue_data_planner.execution import execute_physical_operator
from blue_data_planner.types import (
    BlueDataPlannerError,
    BudgetExhaustedError,
    ErrorKind,
    ExecutionStatus,
    LogicalOperator,
    OperatorOutput,
    OutputColumn,
    RecoverableOperatorError,
    RunCounters,
    RunLimits,
    UnrecoverableOperatorError,
)
from tests.doubles.operators import recording_operator


def test_a_call_comes_back_as_the_rows_it_produced_with_the_row_count_across_every_table() -> None:
    """A successful call carries its bundle, its operator id, and rows across the whole bundle."""
    operator, recorder = recording_operator(
        "blue.select",
        frozenset({LogicalOperator.FILTER}),
        [OperatorOutput(data=[[{"a": 1}], [{"a": 2}, {"a": 3}]])],
    )
    counters = RunCounters(RunLimits(operator_calls=5))

    result = execute_physical_operator(operator, {"key": "a"}, [[{"a": 1}]], [], counters)

    assert result.status is ExecutionStatus.OK
    assert result.output == [[{"a": 1}], [{"a": 2}, {"a": 3}]]
    assert result.row_count == 3
    assert result.operator_id == "blue.select"
    assert recorder.calls == [([[{"a": 1}]], {"key": "a"}, [])]
    assert counters.operator_calls == 1


@pytest.mark.parametrize(
    ("raised", "expected_kind"),
    [
        (RecoverableOperatorError("bad key"), ErrorKind.RECOVERABLE),
        (UnrecoverableOperatorError("timed out"), ErrorKind.UNRECOVERABLE),
    ],
    ids=["recoverable", "unrecoverable"],
)
def test_an_operator_that_refuses_its_input_comes_back_as_a_failure_of_its_kind(
    raised: Exception, expected_kind: ErrorKind
) -> None:
    """The kind is what decides whether another operator is worth trying."""
    operator, _ = recording_operator("blue.select", frozenset({LogicalOperator.FILTER}), [raised])

    result = execute_physical_operator(
        operator, {}, [], [], RunCounters(RunLimits(operator_calls=5))
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.kind is expected_kind


def test_any_other_exception_reaches_the_caller_as_the_bug_it_is() -> None:
    """Only the two operator errors are converted; everything else propagates."""
    operator, _ = recording_operator(
        "blue.select", frozenset({LogicalOperator.FILTER}), [ValueError("boom")]
    )

    with pytest.raises(ValueError):
        execute_physical_operator(operator, {}, [], [], RunCounters(RunLimits(operator_calls=5)))


def test_a_result_missing_a_declared_column_fails_recoverably_and_still_bills() -> None:
    """A call that really happened counts even when its output is turned down."""
    operator, _ = recording_operator(
        "blue.select", frozenset({LogicalOperator.FILTER}), [OperatorOutput(data=[[{"a": 1}]])]
    )
    counters = RunCounters(RunLimits(operator_calls=5))

    result = execute_physical_operator(
        operator, {}, [], [OutputColumn(name="title", dtype="str")], counters
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.kind is ErrorKind.RECOVERABLE
    assert counters.operator_calls == 1


def test_parameters_the_operator_cannot_take_raise_before_the_budget_is_touched() -> None:
    """The runner wrote them under this schema, so a mismatch is a bug, not a spent call."""
    operator, _ = recording_operator(
        "blue.select", frozenset({LogicalOperator.FILTER}), [OperatorOutput(data=[[{"a": 1}]])]
    )
    counters = RunCounters(RunLimits(operator_calls=5))

    with pytest.raises(BlueDataPlannerError):
        execute_physical_operator(operator, {"unknown": 1}, [], [], counters)

    assert counters.operator_calls == 0


def test_an_exhausted_budget_raises_before_the_operator_runs() -> None:
    """The cap ends the request, and the runner catches it with the attempts it already made."""
    operator, recorder = recording_operator(
        "blue.select", frozenset({LogicalOperator.FILTER}), [OperatorOutput(data=[[{"a": 1}]])]
    )

    with pytest.raises(BudgetExhaustedError):
        execute_physical_operator(operator, {}, [], [], RunCounters(RunLimits(operator_calls=0)))

    assert recorder.calls == []


def test_a_successful_call_carries_the_operator_report_to_the_result() -> None:
    """An operator that reports what it did has that report on the result."""
    operator, _ = recording_operator(
        "blue.select",
        frozenset({LogicalOperator.FILTER}),
        [OperatorOutput(data=[[{"id": 1}]], report={"query": "SELECT 1"})],
    )

    result = execute_physical_operator(
        operator, {}, [], [], RunCounters(RunLimits(operator_calls=1))
    )

    assert result.status is ExecutionStatus.OK
    assert result.report == {"query": "SELECT 1"}


def test_a_failed_call_carries_the_report_the_operator_raised_with() -> None:
    """An operator that failed partway through still reports what it managed to do."""
    raised = RecoverableOperatorError("no column 'city'", report={"steps_run": 2})
    operator, _ = recording_operator("blue.select", frozenset({LogicalOperator.FILTER}), [raised])

    result = execute_physical_operator(
        operator, {}, [], [], RunCounters(RunLimits(operator_calls=1))
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.report == {"steps_run": 2}


def test_a_callable_is_handed_the_columns_its_fragment_declares() -> None:
    """The declared columns reach the operation as well as the check that runs after it."""
    operator, recorder = recording_operator(
        "blue.select",
        frozenset({LogicalOperator.FILTER}),
        [OperatorOutput(data=[[{"a": 1}]])],
    )
    columns = [OutputColumn(name="a", dtype="int")]

    execute_physical_operator(
        operator, {"key": "a"}, [], columns, RunCounters(RunLimits(operator_calls=5))
    )

    assert recorder.calls == [([], {"key": "a"}, columns)]
