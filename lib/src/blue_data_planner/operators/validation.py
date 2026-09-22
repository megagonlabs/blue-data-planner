"""The checks every operator suite makes before it runs one call."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from blue_data_planner.types import DataBundle, RecoverableOperatorError

ParametersT = TypeVar("ParametersT", bound=BaseModel)


def validated_parameters(
    schema: type[ParametersT], parameters: dict[str, Any], operator_id: str
) -> ParametersT:
    """Return the parameters read as an operator's schema, or raise for the parameter repair."""
    try:
        return schema.model_validate(parameters)
    except ValidationError as error:
        raise RecoverableOperatorError(
            f"{operator_id} was given parameters it cannot take: {error}"
        ) from error


def check_table_count(operator_id: str, inputs: DataBundle, table_count: int) -> None:
    """Raise when a bundle does not carry the tables an operation reads."""
    if len(inputs) != table_count:
        raise RecoverableOperatorError(
            f"{operator_id} reads {table_count} table(s) and was handed {len(inputs)}"
        )
