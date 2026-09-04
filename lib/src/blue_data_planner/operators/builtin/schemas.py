"""The parameters the built-in operators take."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Unknown keys are refused so a runner cannot reach a parameter a declaration leaves out.
_STRICT = ConfigDict(extra="forbid")

ComparisonOperator = Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "is_null"]
AggregationFunction = Literal["count", "sum", "avg", "min", "max"]
SortDirection = Literal["asc", "desc"]
JoinKind = Literal["inner", "left"]


class Comparison(BaseModel):
    """One column checked against one value."""

    model_config = _STRICT

    column: str
    operator: ComparisonOperator
    value: Any = None

    @model_validator(mode="after")
    def _value_fits_the_operator(self) -> Comparison:
        if self.operator == "is_null" and self.value is not None:
            raise ValueError("'is_null' takes no value")
        if self.operator == "in" and not isinstance(self.value, list):
            raise ValueError("'in' takes a list of values")
        return self


class AndPredicate(BaseModel):
    """Every condition holds."""

    model_config = _STRICT

    conditions: list[Predicate] = Field(alias="and")


class OrPredicate(BaseModel):
    """At least one condition holds."""

    model_config = _STRICT

    conditions: list[Predicate] = Field(alias="or")


class NotPredicate(BaseModel):
    """The condition does not hold."""

    model_config = _STRICT

    condition: Predicate = Field(alias="not")


# A tree rather than a flat list, so a filter node reaches one operator whole and no plan is
# cut differently to fit what an operator's parameters can say (design/06-library.md).
Predicate = Comparison | AndPredicate | OrPredicate | NotPredicate

AndPredicate.model_rebuild()
OrPredicate.model_rebuild()
NotPredicate.model_rebuild()


class ProjectedColumn(BaseModel):
    """One column kept, under its own name or a new one."""

    model_config = _STRICT

    column: str
    into: str | None = None


class Aggregation(BaseModel):
    """One value computed over a group of rows."""

    model_config = _STRICT

    function: AggregationFunction
    column: str | None = None
    into: str

    @model_validator(mode="after")
    def _column_is_given_where_it_is_needed(self) -> Aggregation:
        # count is the one function with an answer for a group rather than for a column.
        if self.function != "count" and self.column is None:
            raise ValueError(f"'{self.function}' needs a column to aggregate")
        return self


class SortKey(BaseModel):
    """One column the rows are ordered by."""

    model_config = _STRICT

    column: str
    direction: SortDirection = "asc"


class JoinKey(BaseModel):
    """One pair of columns two tables are matched on."""

    model_config = _STRICT

    left_column: str
    right_column: str


class FilterParameters(BaseModel):
    """The predicate the surviving rows satisfy."""

    model_config = _STRICT

    predicate: Predicate


class ProjectParameters(BaseModel):
    """The columns each row is narrowed to."""

    model_config = _STRICT

    columns: list[ProjectedColumn] = Field(min_length=1)


class ExpandParameters(BaseModel):
    """The column whose elements become rows of their own."""

    model_config = _STRICT

    column: str
    into: str | None = None
    separator: str | None = None


class AggregateParameters(BaseModel):
    """The groups the rows fall into, and the values computed over each group."""

    model_config = _STRICT

    group_by: list[str] = Field(default_factory=list[str])
    aggregations: list[Aggregation] = Field(min_length=1)


class DistinctParameters(BaseModel):
    """The columns whose repeated values collapse to one row, or every column."""

    model_config = _STRICT

    columns: list[str] | None = None


class SortSuperlativeParameters(BaseModel):
    """The order the rows are put in, and how many of them survive."""

    model_config = _STRICT

    keys: list[SortKey] = Field(min_length=1)
    limit: int | None = Field(default=None, ge=1)


class JoinParameters(BaseModel):
    """The columns two tables are matched on, and what becomes of an unmatched row."""

    model_config = _STRICT

    on: list[JoinKey] = Field(min_length=1)
    how: JoinKind = "inner"


class UnionParameters(BaseModel):
    """Nothing: the two tables are concatenated as they are."""

    model_config = _STRICT
