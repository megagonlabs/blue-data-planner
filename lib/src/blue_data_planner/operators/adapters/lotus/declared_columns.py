"""The columns a LOTUS operation creates, read off the node's declaration."""

from __future__ import annotations

from blue_data_planner.types import OutputColumn, RecoverableOperatorError


def new_columns(required_output_columns: list[OutputColumn], present: list[str]) -> list[str]:
    """Return the declared columns the input does not already carry, in declared order."""
    carried = set(present)
    return [column.name for column in required_output_columns if column.name not in carried]


def one_new_column(
    operator_id: str,
    required_output_columns: list[OutputColumn],
    present: list[str],
    fallback: str | None,
) -> str:
    """Return the single column this operation creates, from the declaration or the parameter."""
    if not required_output_columns:
        if fallback is None:
            raise RecoverableOperatorError(
                f"{operator_id} creates one column and the node declared none, so the "
                "output_column parameter has to name it"
            )
        return fallback
    names = new_columns(required_output_columns, present)
    if len(names) != 1:
        named = ", ".join(names) if names else "none"
        raise RecoverableOperatorError(
            f"{operator_id} creates exactly one column, and the node declares {len(names)} its "
            f"input does not carry ({named})"
        )
    return names[0]


def check_named_columns(
    operator_id: str,
    required_output_columns: list[OutputColumn],
    present: list[str],
    named: list[str],
) -> None:
    """Raise when the columns a parameter names are not the ones the node declared."""
    if not required_output_columns:
        return
    declared = new_columns(required_output_columns, present)
    if sorted(declared) != sorted(named):
        raise RecoverableOperatorError(
            f"{operator_id} was asked for {sorted(named)} while the node declares "
            f"{sorted(declared)} that its input does not carry"
        )
