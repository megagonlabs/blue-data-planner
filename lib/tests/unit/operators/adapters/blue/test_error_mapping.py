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
    OperatorUnavailableError,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)


def a_call_that_failed(
    code: ErrorCode,
    suggestion: str | None = None,
    *,
    category: ErrorCategory = ErrorCategory.FAILED,
    retryable: bool = False,
) -> ExecMeta:
    """Return the metadata of one failed call, carrying one error with this code."""
    record = ErrorRecord(
        category=category,
        code=code,
        operator_id="select@2.0.0",
        stage=ErrorStage.EXECUTE if code is ErrorCode.EXECUTION_FAILED else ErrorStage.VALIDATE,
        message="something went wrong",
        retryable=retryable,
        suggestion=suggestion,
    )
    return ExecMeta(is_empty=False, errors=[record], logs=[], traces={})


def test_a_call_that_reported_no_error_raises_nothing() -> None:
    """A successful call returns, so the callable goes on to read what it produced."""
    raise_for_errors(ExecMeta(is_empty=False, errors=[], logs=[], traces={}), "blue.select")


def test_invalid_input_can_be_repaired_even_when_retrying_the_same_call_cannot_help() -> None:
    """Ops marks invalid input non-retryable, but changing the parameters can fix it."""
    with pytest.raises(RecoverableOperatorError):
        raise_for_errors(
            a_call_that_failed(ErrorCode.INVALID_INPUT, category=ErrorCategory.RECOVERABLE),
            "blue.select",
        )


@pytest.mark.parametrize(
    ("category", "retryable", "expected"),
    [
        (ErrorCategory.FAILED, False, OperatorUnavailableError),
        (ErrorCategory.FAILED, True, RecoverableOperatorError),
        (ErrorCategory.RECOVERABLE, False, RecoverableOperatorError),
        (ErrorCategory.RECOVERABLE, True, RecoverableOperatorError),
    ],
)
def test_execution_failures_respect_the_operations_recovery_metadata(
    category: ErrorCategory,
    retryable: bool,
    expected: type[OperatorUnavailableError] | type[RecoverableOperatorError],
) -> None:
    """A non-retryable FAILED execution needs another operator, not new parameters (#55)."""
    with pytest.raises(expected) as caught:
        raise_for_errors(
            a_call_that_failed(
                ErrorCode.EXECUTION_FAILED,
                "check the operation's configuration",
                category=category,
                retryable=retryable,
            ),
            "blue.select",
            {"rows_read": 3},
        )

    assert "blue.select" in str(caught.value)
    assert "check the operation's configuration" in str(caught.value)
    assert caught.value.report == {"rows_read": 3}


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
        ErrorCode.REFINEMENT_FAILED,
        ErrorCode.REFINEMENT_RESULT_INVALID,
    ],
)
def test_a_broken_operation_requests_another_operator(code: ErrorCode) -> None:
    """The implementation failed; another operator may work, but new parameters cannot fix it."""
    with pytest.raises(OperatorUnavailableError) as caught:
        raise_for_errors(
            a_call_that_failed(code), "blue.select", {"rows_read": 3}, parameter_repairable=True
        )

    assert "blue.select" in str(caught.value)
    assert caught.value.report == {"rows_read": 3}


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
        (ErrorCode.DEFAULTS_FAILED, OperatorUnavailableError),
    ],
)
def test_what_a_failed_call_reported_travels_with_the_failure(
    code: ErrorCode,
    expected: type[RecoverableOperatorError]
    | type[UnrecoverableOperatorError]
    | type[OperatorUnavailableError],
) -> None:
    """An operation that failed partway through is the one with the most to say about its run."""
    with pytest.raises(expected) as caught:
        raise_for_errors(a_call_that_failed(code), "blue.select", {"rows_read": 3})

    assert caught.value.report == {"rows_read": 3}


def test_sql_execution_detail_preserves_a_non_retryable_failure_and_its_report() -> None:
    """A diagnostic alone must not send a broken model or source back to parameter repair."""
    with pytest.raises(OperatorUnavailableError) as caught:
        raise_for_errors(
            a_call_that_failed(ErrorCode.EXECUTION_FAILED, "check the source"),
            "blue.nl2sql",
            {"rows_read": 3},
            execution_detail="SQL generation: LLMError (unsupported_parameter)",
        )

    assert "check the source" in str(caught.value)
    assert "unsupported_parameter" in str(caught.value)
    assert caught.value.report == {"rows_read": 3}


def test_execution_detail_is_not_attached_to_a_timeout() -> None:
    """A late worker diagnostic must not override the timeout the operator reported."""
    with pytest.raises(UnrecoverableOperatorError) as caught:
        raise_for_errors(
            a_call_that_failed(ErrorCode.OPERATOR_TIMEOUT),
            "blue.nl2sql",
            execution_detail="a late SQL failure",
            parameter_repairable=True,
        )

    assert "late SQL failure" not in str(caught.value)
