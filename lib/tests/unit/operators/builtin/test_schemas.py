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
    """A filter's predicate nests and, or, and not to whatever depth a plan asks for."""
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


@pytest.mark.parametrize(
    ("schema", "parameters"),
    [
        (FilterParameters, {"predicate": {"column": "closed", "operator": "is_null", "value": 1}}),
        (FilterParameters, {"predicate": {"column": "state", "operator": "in", "value": "CA"}}),
        (
            FilterParameters,
            {"predicate": {"column": "state", "operator": "matches", "value": "CA"}},
        ),
        (AggregateParameters, {"aggregations": [{"function": "avg", "into": "mean"}]}),
        (ProjectParameters, {"columns": []}),
        (ProjectParameters, {"columns": [{"column": "id"}], "unknown": 1}),
    ],
)
def test_parameters_an_operator_could_not_run_are_refused(
    schema: type[BaseModel], parameters: dict[str, Any]
) -> None:
    """A schema refuses parameters whose shape the operator has no way to carry out."""
    with pytest.raises(ValidationError):
        schema.model_validate(parameters)
