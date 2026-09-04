"""What a caller can catch through the package's own base class."""

from __future__ import annotations

from blue_data_planner.types import BlueDataPlannerError, OperatorRoutingError


def test_a_declared_exception_is_catchable_as_the_package_base() -> None:
    """Every exception this package raises can be caught as BlueDataPlannerError."""
    assert issubclass(OperatorRoutingError, BlueDataPlannerError)
