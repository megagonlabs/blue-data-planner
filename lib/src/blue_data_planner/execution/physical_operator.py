"""The single place any operator is invoked."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from blue_data_planner.execution.output_columns import check_output_columns
from blue_data_planner.types import (
    BlueDataPlannerError,
    CallBudget,
    DataBundle,
    ErrorInfo,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    OutputColumn,
    PhysicalOperator,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)


def execute_physical_operator(
    operator: PhysicalOperator,
    parameters: dict[str, Any],
    inputs: DataBundle,
    required_output_columns: list[OutputColumn],
    budget: CallBudget,
) -> ExecutionResult:
    """Check the parameters, bill the budget, run one operator, and check what it returned."""
    # Two orderings are deliberate. The parameter check runs before the billing, because the
    # runner wrote them under this same schema, so a mismatch is a bug rather than a call
    # worth charging for. The billing runs before the operator, so a call that really
    # happened still counts when the column check rejects its output.
    try:
        operator.parameter_schema.model_validate(parameters)
    except ValidationError as error:
        raise BlueDataPlannerError(
            f"operator {operator.operator_id!r} was given parameters it cannot take: {error}"
        ) from error
    budget.spend()
    try:
        output = operator.call(inputs, parameters)
    except RecoverableOperatorError as error:
        return _failed(ErrorKind.RECOVERABLE, str(error), operator.operator_id, error.report)
    except UnrecoverableOperatorError as error:
        return _failed(ErrorKind.UNRECOVERABLE, str(error), operator.operator_id, error.report)
    result = ExecutionResult(
        status=ExecutionStatus.OK,
        output=output.data,
        operator_id=operator.operator_id,
        row_count=_row_count(output.data),
        report=output.report,
    )
    # Only the columns the call was handed are checked, because a fused call produces no
    # intermediate tables to check anything else against.
    return check_output_columns(result, required_output_columns, operator.operator_id)


def _row_count(output: DataBundle) -> int:
    # Rows across the whole bundle, not the number of tables in it.
    return sum(len(table) for table in output)


def _failed(
    kind: ErrorKind, message: str, operator_id: str, report: dict[str, Any] | None
) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.FAILED,
        operator_id=operator_id,
        error=ErrorInfo(kind=kind, message=message),
        report=report,
    )
