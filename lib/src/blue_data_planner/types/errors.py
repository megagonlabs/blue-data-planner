"""Every exception this package raises."""

from __future__ import annotations


class BlueDataOpsError(Exception):
    """Base class for every error this package raises."""


class PlanFormatError(BlueDataOpsError):
    """A serialized plan is missing a key, or carries a value its node kind does not take."""


class UnknownNodeError(BlueDataOpsError):
    """A node id names no node in the plan."""


class UnknownFragmentError(BlueDataOpsError):
    """A fragment id names no fragment in the cover, or a node belongs to none."""


class OperatorRoutingError(BlueDataOpsError):
    """An operator id is registered twice, or names no registered operator."""


class BudgetExhaustedError(BlueDataOpsError):
    """A budget reached its cap."""


class RecoverableOperatorError(BlueDataOpsError):
    """An operator refused its input in a way a different operator might not."""


class UnrecoverableOperatorError(BlueDataOpsError):
    """An operator failed for a reason no other operator would avoid."""
