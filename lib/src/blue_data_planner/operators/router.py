"""The lookup that answers which operators can implement a logical operator."""

from __future__ import annotations

from blue_data_planner.types import LogicalOperator, OperatorRoutingError, PhysicalOperator


class OperatorRouter:
    """Maps a logical operator to the physical operators that can implement it."""

    def __init__(self) -> None:
        self._operators: dict[LogicalOperator, list[PhysicalOperator]] = {}
        self._by_id: dict[str, PhysicalOperator] = {}

    def register(self, operator: PhysicalOperator) -> None:
        """Add one operator, refusing an operator id that is already registered."""
        if operator.operator_id in self._by_id:
            raise OperatorRoutingError(
                f"operator id {operator.operator_id!r} is already registered; an id names "
                "one implementation and cannot be reused"
            )
        self._by_id[operator.operator_id] = operator
        # A compound operator answers for every kind it can absorb, so it is indexed under
        # each one rather than under a single kind.
        for logical_op in operator.logical_operators:
            self._operators.setdefault(logical_op, []).append(operator)

    def operators_for(self, logical_op: LogicalOperator) -> list[PhysicalOperator]:
        """Return every operator declaring one logical operator, in registration order."""
        return list(self._operators.get(logical_op, []))

    def resolve(self, operator_id: str) -> PhysicalOperator:
        """Return the operator with this id, raising when nothing registered it."""
        operator = self._by_id.get(operator_id)
        if operator is None:
            raise OperatorRoutingError(f"operator id {operator_id!r} names no registered operator")
        return operator
