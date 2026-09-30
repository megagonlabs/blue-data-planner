"""The parameter schemas a fragment runner writes a LOTUS call against."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from blue_data_planner.operators.adapters.lotus.schemas import (
    SemAggParameters,
    SemExtractParameters,
    SemFilterParameters,
    SemJoinParameters,
    SemMapParameters,
    SemTopKParameters,
)


def objects_in(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object a JSON schema describes: the top level and each definition it refers to."""
    definitions: dict[str, Any] = schema.get("$defs", {})
    return [schema, *definitions.values()]


@pytest.mark.parametrize(
    "parameters",
    [
        SemFilterParameters,
        SemMapParameters,
        SemExtractParameters,
        SemAggParameters,
        SemTopKParameters,
        SemJoinParameters,
    ],
)
def test_every_object_in_a_parameter_schema_refuses_keys_it_does_not_name(
    parameters: type[BaseModel],
) -> None:
    """A provider enforcing the schema strictly can only write keys the operation takes."""
    for described in objects_in(parameters.model_json_schema()):
        assert described.get("additionalProperties") is False, described.get("title")
