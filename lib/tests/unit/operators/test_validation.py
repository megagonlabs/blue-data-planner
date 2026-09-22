"""The checks every operator suite makes before it runs one call."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from blue_data_planner.operators.validation import check_table_count, validated_parameters
from blue_data_planner.types import RecoverableOperatorError


class Parameters(BaseModel):
    """A schema naming one required key and refusing every other."""

    model_config = ConfigDict(extra="forbid")

    column: str


def test_parameters_matching_the_schema_come_back_as_the_model() -> None:
    """Parameters a schema accepts are returned read as that schema."""
    assert validated_parameters(Parameters, {"column": "city"}, "blue.select").column == "city"


@pytest.mark.parametrize(
    "parameters",
    [{}, {"column": 1}, {"column": "city", "unknown": True}],
    ids=["missing", "mistyped", "unknown key"],
)
def test_parameters_a_schema_refuses_are_recoverable(parameters: dict[str, object]) -> None:
    """Parameters a schema cannot read raise recoverably, so the parameter repair answers."""
    with pytest.raises(RecoverableOperatorError, match="blue.select"):
        validated_parameters(Parameters, parameters, "blue.select")


def test_a_bundle_carrying_the_tables_an_operation_reads_is_accepted() -> None:
    """A bundle of the declared length passes without raising."""
    check_table_count("lotus.sem_join", [[{"n": 1}], [{"n": 2}]], 2)


@pytest.mark.parametrize("inputs", [[], [[{"n": 1}], [{"n": 2}]]], ids=["too few", "too many"])
def test_a_bundle_of_the_wrong_length_is_refused_with_both_counts(
    inputs: list[list[dict[str, object]]],
) -> None:
    """A bundle that is not the declared length raises recoverably, naming what was expected."""
    with pytest.raises(RecoverableOperatorError, match="1 table"):
        check_table_count("lotus.sem_filter", inputs, 1)
