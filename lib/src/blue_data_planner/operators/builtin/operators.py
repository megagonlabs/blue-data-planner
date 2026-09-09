"""The deterministic operators this repository owns, one per operator node kind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators.builtin import source_scan, table_transformations
from blue_data_planner.operators.builtin.nl2table import nl2table_operator
from blue_data_planner.operators.builtin.schemas import (
    AggregateParameters,
    DistinctParameters,
    ExpandParameters,
    FilterParameters,
    JoinParameters,
    ProjectParameters,
    ScanParameters,
    SortSuperlativeParameters,
    UnionParameters,
)
from blue_data_planner.types import (
    Data,
    DataBundle,
    LogicalOperator,
    OperatorOutput,
    OutputColumn,
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

# What separates this entry from an operator that owns a query engine: it reads a whole entity
# and pushes nothing but the values a reference input carried.
_SCAN_LIMITATIONS = (
    "Reads a SQL source only, returns the entity whole, and narrows the read by nothing but the "
    "values a reference input carries, so any other narrowing is a separate FILTER or PROJECT."
)


def builtin_operators(
    data_registry: DataRegistry,
    llm_client: LLMClient,
    with_nl2table: bool = False,
) -> list[PhysicalOperator]:
    """Return the suite's operators, with the compound entry only where it was asked for."""
    entries = (*_ENTRIES, _scan_entry(data_registry))
    operators = [
        PhysicalOperator(
            operator_id=entry.operator_id,
            logical_operators=frozenset({entry.logical_operator}),
            description=entry.description,
            parameter_schema=entry.parameter_schema,
            call=entry.call,
            source_kinds=entry.source_kinds,
            accepts_bound_inputs=entry.accepts_bound_inputs,
            limitations=entry.limitations,
        )
        for entry in entries
    ]
    # The compound entry runs model-written code, so a deployment enables it on purpose
    # (ADR-020). It is built here rather than as an _Entry because it declares every kind.
    if with_nl2table:
        operators.append(nl2table_operator(data_registry, llm_client))
    return operators


def _filter(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
    filtering = _validated(FilterParameters, parameters, "builtin.filter")
    table = _one_table(inputs, "builtin.filter")
    return OperatorOutput(data=[table_transformations.filter_rows(table, filtering.predicate)])


def _project(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
    projection = _validated(ProjectParameters, parameters, "builtin.project")
    table = _one_table(inputs, "builtin.project")
    return OperatorOutput(data=[table_transformations.project_columns(table, projection.columns)])


def _expand(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
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


def _aggregate(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
    aggregation = _validated(AggregateParameters, parameters, "builtin.aggregate")
    table = _one_table(inputs, "builtin.aggregate")
    return OperatorOutput(
        data=[
            table_transformations.aggregate_rows(
                table, aggregation.group_by, aggregation.aggregations
            )
        ]
    )


def _distinct(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
    distinct = _validated(DistinctParameters, parameters, "builtin.distinct")
    table = _one_table(inputs, "builtin.distinct")
    return OperatorOutput(data=[table_transformations.distinct_rows(table, distinct.columns)])


def _sort_superlative(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
    ordering = _validated(SortSuperlativeParameters, parameters, "builtin.sort_superlative")
    table = _one_table(inputs, "builtin.sort_superlative")
    return OperatorOutput(
        data=[table_transformations.sort_rows(table, ordering.keys, ordering.limit)]
    )


def _join(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
    join = _validated(JoinParameters, parameters, "builtin.join")
    left, right = _two_tables(inputs, "builtin.join")
    return OperatorOutput(data=[table_transformations.join_tables(left, right, join.on, join.how)])


def _union(
    inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
) -> OperatorOutput:
    _validated(UnionParameters, parameters, "builtin.union")
    left, right = _two_tables(inputs, "builtin.union")
    return OperatorOutput(data=[table_transformations.union_tables(left, right)])


def _scan_entry(data_registry: DataRegistry) -> _Entry:
    def scan(
        inputs: DataBundle, parameters: dict[str, Any], required_output_columns: list[OutputColumn]
    ) -> OperatorOutput:
        return _scan(data_registry, inputs, parameters)

    return _Entry(
        "builtin.scan",
        LogicalOperator.SCAN,
        "Reads every row of one registered entity, narrowed to values a reference input carries.",
        ScanParameters,
        scan,
        source_kinds=frozenset({"sql/sqlite"}),
        accepts_bound_inputs=True,
        limitations=_SCAN_LIMITATIONS,
    )


def _scan(
    data_registry: DataRegistry, inputs: DataBundle, parameters: dict[str, Any]
) -> OperatorOutput:
    scan = _validated(ScanParameters, parameters, "builtin.scan")
    if scan.bound_values is None:
        if inputs:
            raise RecoverableOperatorError(
                f"builtin.scan was handed {len(inputs)} input tables and no bound_values "
                "naming what they bind"
            )
        table, report = source_scan.scan_entity(data_registry, scan.scan_source)
    else:
        if len(inputs) != 1:
            raise RecoverableOperatorError(
                f"builtin.scan binds one reference input and was handed {len(inputs)}"
            )
        table, report = source_scan.scan_entity_bound_to(
            data_registry,
            scan.scan_source,
            scan.bound_values.attribute,
            inputs[0],
            scan.bound_values.input_column,
        )
    return OperatorOutput(data=[table], report=report)


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
    call: Callable[[DataBundle, dict[str, Any], list[OutputColumn]], OperatorOutput]
    source_kinds: frozenset[str] | None = None
    accepts_bound_inputs: bool = False
    limitations: str | None = _LIMITATIONS


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
        "Matches the rows of two tables on equal values in named columns, renaming a "
        "colliding right-hand column with a '_right' suffix; an unmatched left row in a left "
        "join carries none of the right table's columns.",
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
