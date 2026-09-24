"""The parameters the built-in operators take."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Unknown keys are refused so a runner cannot reach a parameter a declaration leaves out.
# populate_by_name lets a predicate field also be set by its own name (rather than only by
# its and/or/not alias) when something builds one of these models directly.
# serialize_by_alias makes model_dump() write "and"/"or"/"not" by default: AndPredicate and
# OrPredicate share the field name "conditions" and differ only by alias, so a plain dump
# without it loses which one this was, and the untagged union resolves the wrong type back in.
_STRICT = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

SortDirection = Literal["asc", "desc"]
JoinKind = Literal["inner", "left"]


class ValueComparison(BaseModel):
    """One column checked against a single value."""

    model_config = _STRICT

    column: str = Field(min_length=1)
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "contains"]
    value: str | int | float | bool | None


class InComparison(BaseModel):
    """One column checked against a list of values."""

    model_config = _STRICT

    column: str = Field(min_length=1)
    operator: Literal["in"]
    value: list[str | int | float]


class NullComparison(BaseModel):
    """One column checked for a missing value."""

    model_config = _STRICT

    column: str = Field(min_length=1)
    operator: Literal["is_null"]


class PatternComparison(BaseModel):
    """One string column searched for a regular expression, as Python's re.search does."""

    model_config = _STRICT

    column: str = Field(min_length=1)
    operator: Literal["re.search"]
    value: str = Field(min_length=1)


# Four shapes rather than one model with cross-field rules a JSON Schema cannot state: whether
# "value" is required, absent, a list, or a pattern follows from which variant a call picked, not
# from a validator pydantic can only run after a provider has already accepted the object.
Comparison = ValueComparison | InComparison | NullComparison | PatternComparison


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
# cut differently to fit what an operator's parameters can say (design/06-library.md). Comparison
# is itself a union, and Python flattens a union of unions, so this is the seven-variant flat union
# a provider's schema needs, not a union nested inside another.
Predicate = Comparison | AndPredicate | OrPredicate | NotPredicate

AndPredicate.model_rebuild()
OrPredicate.model_rebuild()
NotPredicate.model_rebuild()


class ProjectedColumn(BaseModel):
    """One column kept, under its own name or a new one."""

    model_config = _STRICT

    column: str = Field(min_length=1)
    into: str | None = Field(default=None, min_length=1)


class CountAggregation(BaseModel):
    """Rows counted, either all of them or those with a value in one column."""

    model_config = _STRICT

    function: Literal["count"]
    column: str | None = Field(default=None, min_length=1)
    into: str = Field(min_length=1)


class ColumnAggregation(BaseModel):
    """One value computed over one column."""

    model_config = _STRICT

    function: Literal["sum", "avg", "min", "max"]
    column: str = Field(min_length=1)
    into: str = Field(min_length=1)


# count is the one function with an answer for a group rather than for a column, so it is the
# one variant whose column stays optional; the split carries that rule in the shape instead of a
# validator run after the fact.
Aggregation = CountAggregation | ColumnAggregation


class SortKey(BaseModel):
    """One column the rows are ordered by."""

    model_config = _STRICT

    column: str = Field(min_length=1)
    direction: SortDirection = "asc"


class JoinKey(BaseModel):
    """One pair of columns two tables are matched on."""

    model_config = _STRICT

    left_column: str = Field(min_length=1)
    right_column: str = Field(min_length=1)


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

    column: str = Field(min_length=1)
    into: str | None = Field(default=None, min_length=1)
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


class BoundValues(BaseModel):
    """The values a reference input carries into a read, and the attribute they match."""

    model_config = _STRICT

    input_column: str = Field(min_length=1, description="Column of that table carrying the values")
    attribute: str = Field(
        min_length=1, description="Entity attribute those values are matched against"
    )


class ScanSource(BaseModel):
    """One registered entity to read, and the values a reference input narrows it to."""

    model_config = _STRICT

    # The catalog path the cover bound one SCAN to, which is what
    # PhysicalPlanFragment.scan_sources holds for the node.
    scan_source: str = Field(
        min_length=1,
        description=(
            "Catalog path of the entity to read, in the form "
            "/source/{source}/database/{database}/collection/{collection}/entity/{entity}"
        ),
    )
    bound_values: BoundValues | None = None


class ScanParameters(ScanSource):
    """Which registered entity is read, and the values a reference input narrows it to."""


class IndexedBoundValues(BoundValues):
    """The same, naming which input table of a multi-input read carries the values."""

    input_index: int = Field(
        default=0, ge=0, description="Which table of the input bundle carries the values"
    )


class IndexedScanSource(ScanSource):
    """A `ScanSource` whose bound values may point at any of several input tables."""

    # Narrows the field pydantic still validates as IndexedBoundValues at runtime; pyright
    # treats a mutable attribute as invariant and flags the narrower annotation on its own.
    bound_values: IndexedBoundValues | None = None  # pyright: ignore[reportIncompatibleVariableOverride]


class Nl2TableParameters(BaseModel):
    """What the fragment asks for, and the entities a program needs read before it runs."""

    model_config = _STRICT

    instruction: str = Field(
        min_length=1, description="What this fragment has to produce, in plain language"
    )
    scan_sources: list[IndexedScanSource] = Field(
        default_factory=list["IndexedScanSource"],
        description="Every entity this fragment reads, one entry per SCAN node it covers",
    )
