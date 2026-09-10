"""The parameters a plan fills for one LOTUS operation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Unknown keys are refused so a runner cannot reach a tuning parameter a declaration
# deliberately leaves out.
_STRICT = ConfigDict(extra="forbid")

_COLUMN_NOTATION = (
    "Name each column to read in braces, spelled exactly as the input table spells it, "
    "as in 'Is {city} on the coast?'"
)


class SemFilterParameters(BaseModel):
    """Keep the rows a model judges to satisfy a condition."""

    model_config = _STRICT

    instruction: str = Field(
        description=f"What a row has to satisfy to be kept. {_COLUMN_NOTATION}"
    )


class SemMapParameters(BaseModel):
    """Write one new column from a model's reading of existing ones."""

    model_config = _STRICT

    instruction: str = Field(description=f"What the new column should hold. {_COLUMN_NOTATION}")
    output_column: str | None = Field(
        default=None,
        description=(
            "The name of the column to create. Needed only where the node declares no output "
            "columns; otherwise the declared column is used."
        ),
    )


class SemExtractParameters(BaseModel):
    """Pull named values out of text columns into new columns."""

    model_config = _STRICT

    input_columns: list[str] = Field(description="The columns to read the values out of")
    output_columns: dict[str, str] = Field(
        description=(
            "Each column to create, mapped to what to extract into it. The names have to be "
            "the columns the node declares that its input does not already carry."
        )
    )


class SemAggParameters(BaseModel):
    """Summarize the rows of each group into one value."""

    model_config = _STRICT

    instruction: str = Field(
        description=f"What the summary of a group should say. {_COLUMN_NOTATION}"
    )
    group_by: list[str] = Field(
        default_factory=list[str],
        description="The columns to group by; empty summarizes the whole table into one row",
    )
    output_column: str | None = Field(
        default=None,
        description=(
            "The name of the summary column. Needed only where the node declares no output "
            "columns; otherwise the declared column is used."
        ),
    )


class SemTopKParameters(BaseModel):
    """Keep the rows a model ranks highest."""

    model_config = _STRICT

    instruction: str = Field(description=f"What makes a row rank higher. {_COLUMN_NOTATION}")
    k: int = Field(ge=1, description="How many rows to keep")


class SemJoinParameters(BaseModel):
    """Match rows of two tables on a model's judgment."""

    model_config = _STRICT

    instruction: str = Field(
        description=(
            "When a row of the left table matches a row of the right one. Name one column from "
            "each in braces; where both tables carry the name, write {column:left} and "
            "{column:right}."
        )
    )
