"""The deterministic operators this repository owns, one per operator node kind but SCAN."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators.builtin import table_transformations
from blue_data_planner.operators.builtin.schemas import (
    AggregateParameters,
    DistinctParameters,
    ExpandParameters,
    FilterParameters,
    JoinParameters,
    ProjectParameters,
    SortSuperlativeParameters,
    UnionParameters,
)
from blue_data_planner.types import (
    Data,
    DataBundle,
    LogicalOperator,
    OperatorOutput,
    PhysicalOperator,
    RecoverableOperatorError,
)

ParametersT = TypeVar("ParametersT", bound=BaseModel)

# Every operator in this suite carries the same sentence, because the fixed vocabulary is
# what makes it true: nothing these parameters can express reads a value for its meaning.
_LIMITATIONS = (
    "Compares values as they are stored and cannot judge meaning, so a predicate that needs "
    "reading text belongs to a semantic operator."
)


def builtin_operators(data_registry: DataRegistry, llm_client: LLMClient) -> list[PhysicalOperator]:
    """Return one physical operator per logical operator this suite implements."""
    # Neither argument is read yet: builtin.scan is what reads a registry, and
    # builtin.nl2table is what calls a model. Both arrive as their own operators.
    return [
        PhysicalOperator(
            operator_id=entry.operator_id,
            logical_operators=frozenset({entry.logical_operator}),
            description=entry.description,
            parameter_schema=entry.parameter_schema,
            call=entry.call,
            source_kinds=None,
            accepts_bound_inputs=False,
            limitations=_LIMITATIONS,
        )
        for entry in _ENTRIES
    ]


def _filter(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    filtering = _validated(FilterParameters, parameters, "builtin.filter")
    table = _one_table(inputs, "builtin.filter")
    return OperatorOutput(data=[table_transformations.filter_rows(table, filtering.predicate)])


def _project(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    projection = _validated(ProjectParameters, parameters, "builtin.project")
    table = _one_table(inputs, "builtin.project")
    return OperatorOutput(data=[table_transformations.project_columns(table, projection.columns)])


def _expand(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    expansion = _validated(ExpandParameters, parameters, "builtin.expand")
    table = _one_table(inputs, "builtin.expand")
    return OperatorOutput(
        data=[
            table_transformations.expand_rows(
                table,
                expansion.column,
                expansion.into or expansion.column,
                expansion.separator,
            )
        ]
    )


def _aggregate(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    aggregation = _validated(AggregateParameters, parameters, "builtin.aggregate")
    table = _one_table(inputs, "builtin.aggregate")
    return OperatorOutput(
        data=[
            table_transformations.aggregate_rows(
                table, aggregation.group_by, aggregation.aggregations
            )
        ]
    )


def _distinct(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    distinct = _validated(DistinctParameters, parameters, "builtin.distinct")
    table = _one_table(inputs, "builtin.distinct")
    return OperatorOutput(data=[table_transformations.distinct_rows(table, distinct.columns)])


def _sort_superlative(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    ordering = _validated(SortSuperlativeParameters, parameters, "builtin.sort_superlative")
    table = _one_table(inputs, "builtin.sort_superlative")
    return OperatorOutput(
        data=[table_transformations.sort_rows(table, ordering.keys, ordering.limit)]
    )


def _join(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    join = _validated(JoinParameters, parameters, "builtin.join")
    left, right = _two_tables(inputs, "builtin.join")
    return OperatorOutput(data=[table_transformations.join_tables(left, right, join.on, join.how)])


def _union(inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
    _validated(UnionParameters, parameters, "builtin.union")
    left, right = _two_tables(inputs, "builtin.union")
    return OperatorOutput(data=[table_transformations.union_tables(left, right)])


def _validated(
    schema: type[ParametersT], parameters: dict[str, Any], operator_id: str
) -> ParametersT:
    try:
        return schema.model_validate(parameters)
    except ValidationError as error:
        raise RecoverableOperatorError(
            f"{operator_id} was given parameters it cannot take: {error}"
        ) from error


def _one_table(inputs: DataBundle, operator_id: str) -> Data:
    if len(inputs) != 1:
        raise RecoverableOperatorError(
            f"{operator_id} transforms one table and was handed {len(inputs)}"
        )
    return inputs[0]


def _two_tables(inputs: DataBundle, operator_id: str) -> tuple[Data, Data]:
    if len(inputs) != 2:
        raise RecoverableOperatorError(
            f"{operator_id} combines two tables and was handed {len(inputs)}"
        )
    return inputs[0], inputs[1]


@dataclass(frozen=True)
class _Entry:
    operator_id: str
    logical_operator: LogicalOperator
    description: str
    parameter_schema: type[BaseModel]
    call: Callable[[DataBundle, dict[str, Any]], OperatorOutput]


_ENTRIES: tuple[_Entry, ...] = (
    _Entry(
        "builtin.filter",
        LogicalOperator.FILTER,
        "Keeps the rows a predicate of comparisons, combined with and, or, and not, accepts.",
        FilterParameters,
        _filter,
    ),
    _Entry(
        "builtin.project",
        LogicalOperator.PROJECT,
        "Narrows each row to named columns, renaming any of them.",
        ProjectParameters,
        _project,
    ),
    _Entry(
        "builtin.expand",
        LogicalOperator.EXPAND,
        "Writes one row per element of a list column, or per piece of a delimited string.",
        ExpandParameters,
        _expand,
    ),
    _Entry(
        "builtin.aggregate",
        LogicalOperator.AGGREGATE,
        "Groups rows by named columns and counts, sums, averages, minimizes, or maximizes them.",
        AggregateParameters,
        _aggregate,
    ),
    _Entry(
        "builtin.distinct",
        LogicalOperator.DISTINCT,
        "Keeps the first row of each distinct combination of values.",
        DistinctParameters,
        _distinct,
    ),
    _Entry(
        "builtin.sort_superlative",
        LogicalOperator.SORT_SUPERLATIVE,
        "Orders rows by named columns and keeps the first of them.",
        SortSuperlativeParameters,
        _sort_superlative,
    ),
    _Entry(
        "builtin.join",
        LogicalOperator.JOIN,
        "Matches the rows of two tables on equal values in named columns.",
        JoinParameters,
        _join,
    ),
    _Entry(
        "builtin.union",
        LogicalOperator.UNION,
        "Concatenates the rows of two tables.",
        UnionParameters,
        _union,
    ),
)
