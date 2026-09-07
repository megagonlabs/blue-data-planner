"""Every exception this package raises."""

from __future__ import annotations

from typing import Any


class BlueDataPlannerError(Exception):
    """Base class for every error this package raises."""


class PlanFormatError(BlueDataPlannerError):
    """A serialized plan is missing a key, or carries a value its node kind does not take."""


class UnknownNodeError(BlueDataPlannerError):
    """A node id names no node in the plan."""


class UnknownFragmentError(BlueDataPlannerError):
    """A fragment id names no fragment in the cover, or a node belongs to none."""


class PlanValidationError(BlueDataPlannerError):
    """A plan or a cover failed a structural check and must not run."""


class OperatorRoutingError(BlueDataPlannerError):
    """An operator id is registered twice, or names no registered operator."""


class BudgetExhaustedError(BlueDataPlannerError):
    """A budget reached its cap."""


class RecoverableOperatorError(BlueDataPlannerError):
    """An operator refused its input in a way a different operator might not."""

    report: dict[str, Any] | None

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report


class UnrecoverableOperatorError(BlueDataPlannerError):
    """An operator failed for a reason no other operator would avoid."""

    report: dict[str, Any] | None

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report


class SourceQueryError(BlueDataPlannerError):
    """A source was reached and refused the query."""


class SourceUnreachableError(BlueDataPlannerError):
    """A registered source could not be connected to."""
