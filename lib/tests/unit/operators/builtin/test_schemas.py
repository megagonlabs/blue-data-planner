"""What the built-in operators accept as parameters."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from blue_data_planner.operators.builtin.schemas import (
    AggregateParameters,
    AndPredicate,
    Comparison,
    FilterParameters,
    NotPredicate,
    OrPredicate,
    ProjectParameters,
)


def test_a_predicate_reads_back_as_the_tree_it_was_written_as() -> None:
    """A filter's predicate nests and, or, and not to whatever depth a plan asks for, and
    survives a round trip through model_dump() and back."""
    parameters = FilterParameters.model_validate(
        {
            "predicate": {
                "or": [
                    {
                        "and": [
                            {"column": "state", "operator": "eq", "value": "CA"},
                            {"column": "rating", "operator": "gte", "value": 4.0},
                        ]
                    },
                    {"not": {"column": "closed", "operator": "is_null"}},
                ]
            }
        }
    )

    predicate = parameters.predicate
    assert isinstance(predicate, OrPredicate)
    conjunction, negation = predicate.conditions
    assert isinstance(conjunction, AndPredicate)
    assert isinstance(negation, NotPredicate)
    assert isinstance(negation.condition, Comparison)

    # model_dump() writes each field's own name ("conditions", "condition"), not its and/or/not
    # alias, so the tree has to accept its own field names back to survive the round trip.
    round_tripped = FilterParameters.model_validate(parameters.model_dump())
    assert round_tripped.model_dump() == parameters.model_dump()


@pytest.mark.parametrize(
    ("schema", "parameters"),
    [
        (FilterParameters, {"predicate": {"column": "closed", "operator": "is_null", "value": 1}}),
        (FilterParameters, {"predicate": {"column": "state", "operator": "in", "value": "CA"}}),
        (
            FilterParameters,
            {"predicate": {"column": "state", "operator": "matches", "value": "CA"}},
        ),
        (FilterParameters, {"predicate": {"column": "closed", "operator": "eq"}}),
        (AggregateParameters, {"aggregations": [{"function": "avg", "into": "mean"}]}),
        (ProjectParameters, {"columns": []}),
        (ProjectParameters, {"columns": [{"column": "id"}], "unknown": 1}),
        (ProjectParameters, {"columns": [{"column": "id", "into": ""}]}),
        (ProjectParameters, {"columns": [{"column": ""}]}),
    ],
)
def test_parameters_an_operator_could_not_run_are_refused(
    schema: type[BaseModel], parameters: dict[str, Any]
) -> None:
    """A schema refuses parameters whose shape the operator has no way to carry out."""
    with pytest.raises(ValidationError):
        schema.model_validate(parameters)
