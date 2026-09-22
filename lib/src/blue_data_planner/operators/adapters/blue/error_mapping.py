"""Mapping what a Blue operation reported onto the errors a run loop knows how to answer."""

from __future__ import annotations

from typing import Any

from blue_data_ops.operators.types import ErrorCode, ExecMeta

from blue_data_planner.types import (
    BlueDataPlannerError,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)

# A different set of parameters, or a different operator, might avoid either of these.
_RECOVERABLE_ERROR_CODES = frozenset({ErrorCode.INVALID_INPUT, ErrorCode.EXECUTION_FAILED})
# Every operator gets the same rows and the same time, so retrying only pays the timeout
# again.
_UNRECOVERABLE_ERROR_CODES = frozenset({ErrorCode.OPERATOR_TIMEOUT})


def raise_for_errors(
    meta: ExecMeta, operator_id: str, report: dict[str, Any] | None = None
) -> None:
    """Raise the failure a call's first reported error names, or return when it reported none."""
    if not meta.errors:
        return
    error = meta.errors[0]
    detail = error.message if error.suggestion is None else f"{error.message}: {error.suggestion}"
    message = f"{operator_id} failed at {error.stage.value}: {detail}"
    if error.code in _RECOVERABLE_ERROR_CODES:
        raise RecoverableOperatorError(message, report=report)
    if error.code in _UNRECOVERABLE_ERROR_CODES:
        raise UnrecoverableOperatorError(message, report=report)
    # The remaining codes all mean the operation's own implementation is broken, not that a
    # different set of parameters could have avoided the failure. A bare BlueDataPlannerError,
    # which nothing here catches, reaches the interface boundary with its traceback instead of
    # being recorded as an ordinary fragment failure.
    raise BlueDataPlannerError(message)
