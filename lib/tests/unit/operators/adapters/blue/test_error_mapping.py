"""Turning what a Blue operation reported into the exception its error code names."""

from __future__ import annotations

import pytest
from blue_data_ops.operators.types import (
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    ErrorStage,
    ExecMeta,
)

from blue_data_planner.operators.adapters.blue.error_mapping import raise_for_errors
from blue_data_planner.types import (
    BlueDataPlannerError,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)


def a_call_that_failed(code: ErrorCode, suggestion: str | None = None) -> ExecMeta:
    """Return the metadata of one failed call, carrying one error with this code."""
    record = ErrorRecord(
        category=ErrorCategory.FAILED,
        code=code,
        operator_id="select@2.0.0",
        stage=ErrorStage.VALIDATE,
        message="something went wrong",
        retryable=False,
        suggestion=suggestion,
    )
    return ExecMeta(is_empty=False, errors=[record], logs=[], traces={})


def test_a_call_that_reported_no_error_raises_nothing() -> None:
    """A successful call returns, so the callable goes on to read what it produced."""
    raise_for_errors(ExecMeta(is_empty=False, errors=[], logs=[], traces={}), "blue.select")


@pytest.mark.parametrize("code", [ErrorCode.INVALID_INPUT, ErrorCode.EXECUTION_FAILED])
def test_a_failure_another_operation_might_avoid_is_recoverable(code: ErrorCode) -> None:
    """Refused parameters and input it could not handle both leave another operator to try."""
    with pytest.raises(RecoverableOperatorError):
        raise_for_errors(a_call_that_failed(code), "blue.select")


def test_a_timed_out_call_is_unrecoverable() -> None:
    """Every operator gets the same rows and the same time, so retrying only pays the wait again."""
    with pytest.raises(UnrecoverableOperatorError):
        raise_for_errors(a_call_that_failed(ErrorCode.OPERATOR_TIMEOUT), "blue.select")


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.DEFAULTS_FAILED,
        ErrorCode.VALIDATION_FAILED,
        ErrorCode.VALIDATION_RESULT_INVALID,
        ErrorCode.EXECUTION_RESULT_INVALID,
    ],
)
def test_a_broken_operation_raises_past_the_run_loop(code: ErrorCode) -> None:
    """These four name a broken operation, which no other parameters would have avoided."""
    with pytest.raises(BlueDataPlannerError) as caught:
        raise_for_errors(a_call_that_failed(code), "blue.select")

    # execute_physical_operator catches the two operator error types and nothing else, so
    # raising either of them here would record a bug as an ordinary fragment failure.
    assert not isinstance(caught.value, (RecoverableOperatorError, UnrecoverableOperatorError))


def test_the_message_names_the_operator_the_stage_and_what_to_try() -> None:
    """A review turn writes its next attempt from this message and nothing else."""
    with pytest.raises(RecoverableOperatorError) as caught:
        raise_for_errors(
            a_call_that_failed(ErrorCode.INVALID_INPUT, "supply a supported operand"),
            "blue.select",
        )

    message = str(caught.value)
    assert "blue.select" in message
    assert "VALIDATE" in message
    assert "supply a supported operand" in message


def test_the_first_error_decides_when_a_call_reported_several() -> None:
    """The list is read from the front, so one call raises one exception."""
    meta = ExecMeta(
        is_empty=False,
        errors=[
            a_call_that_failed(ErrorCode.INVALID_INPUT).errors[0],
            a_call_that_failed(ErrorCode.VALIDATION_FAILED).errors[0],
        ],
        logs=[],
        traces={},
    )

    with pytest.raises(RecoverableOperatorError):
        raise_for_errors(meta, "blue.select")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ErrorCode.INVALID_INPUT, RecoverableOperatorError),
        (ErrorCode.OPERATOR_TIMEOUT, UnrecoverableOperatorError),
    ],
)
def test_what_a_failed_call_reported_travels_with_the_failure(
    code: ErrorCode,
    expected: type[RecoverableOperatorError] | type[UnrecoverableOperatorError],
) -> None:
    """An operation that failed partway through is the one with the most to say about its run."""
    with pytest.raises(expected) as caught:
        raise_for_errors(a_call_that_failed(code), "blue.select", {"rows_read": 3})

    assert caught.value.report == {"rows_read": 3}


def test_sql_execution_detail_preserves_the_upstream_suggestion_and_report() -> None:
    """The observed cause supplements the outcome, without replacing its classification."""
    with pytest.raises(RecoverableOperatorError) as caught:
        raise_for_errors(
            a_call_that_failed(ErrorCode.EXECUTION_FAILED, "check the source"),
            "blue.nl2sql",
            {"rows_read": 3},
            execution_detail="SQL execution: no such column: absent_column",
        )

    assert "check the source" in str(caught.value)
    assert "no such column: absent_column" in str(caught.value)
    assert caught.value.report == {"rows_read": 3}


def test_execution_detail_is_not_attached_to_a_timeout() -> None:
    """A late worker diagnostic must not override the timeout the operator reported."""
    with pytest.raises(UnrecoverableOperatorError) as caught:
        raise_for_errors(
            a_call_that_failed(ErrorCode.OPERATOR_TIMEOUT),
            "blue.nl2sql",
            execution_detail="a late SQL failure",
        )

    assert "late SQL failure" not in str(caught.value)
