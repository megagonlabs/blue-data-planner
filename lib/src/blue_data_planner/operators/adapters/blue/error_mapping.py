"""Mapping what a Blue operation reported onto the errors a run loop knows how to answer."""

from __future__ import annotations

from typing import Any

from blue_data_ops.operators.types import ErrorCode, ExecMeta

from blue_data_planner.types import (
    OperatorUnavailableError,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)

# A different set of parameters, or a different operator, might avoid either of these.
_RECOVERABLE_ERROR_CODES = frozenset({ErrorCode.INVALID_INPUT, ErrorCode.EXECUTION_FAILED})
# Every operator gets the same rows and the same time, so retrying only pays the timeout
# again.
_UNRECOVERABLE_ERROR_CODES = frozenset({ErrorCode.OPERATOR_TIMEOUT})


def raise_for_errors(
    meta: ExecMeta,
    operator_id: str,
    report: dict[str, Any] | None = None,
    *,
    execution_detail: str | None = None,
) -> None:
    """Raise the failure a call's first reported error names, or return when it reported none."""
    if not meta.errors:
        return
    error = meta.errors[0]
    detail = error.message if error.suggestion is None else f"{error.message}: {error.suggestion}"
    if error.code is ErrorCode.EXECUTION_FAILED and execution_detail:
        detail = f"{detail}; {execution_detail}"
    message = f"{operator_id} failed at {error.stage.value}: {detail}"
    if error.code in _RECOVERABLE_ERROR_CODES:
        raise RecoverableOperatorError(message, report=report)
    if error.code in _UNRECOVERABLE_ERROR_CODES:
        raise UnrecoverableOperatorError(message, report=report)
    # Defaults, validator failures, and malformed results belong to this implementation.
    # Another operator can still serve the node, so repair the cover without spending more
    # parameter attempts or reporting the operation's defect as a planner defect (#54).
    raise OperatorUnavailableError(message, report=report)
