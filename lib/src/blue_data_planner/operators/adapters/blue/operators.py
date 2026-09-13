"""Blue operators as physical operators, validating parameters and mapping failures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from blue_data_ops.operators.operator import Operator
from blue_data_ops.operators.registry import OperatorRegistry
from blue_data_ops.operators.types import Properties
from pydantic import BaseModel, ValidationError

from blue_data_planner.data import DataRegistry
from blue_data_planner.operators.adapters.blue import failures
from blue_data_planner.operators.adapters.blue.schemas import (
    ArgmaxParameters,
    ArgminParameters,
    AvgParameters,
    JoinParameters,
    MatchOptionParameters,
    MaxParameters,
    MinParameters,
    NL2LLMParameters,
    NL2SQLParameters,
    ProjectParameters,
    SelectParameters,
    SemanticExtractParameters,
    SemanticFilterParameters,
    SemanticJoinParameters,
    SemanticProjectParameters,
    SemanticTransformParameters,
    SumParameters,
)
from blue_data_planner.types import (
    DataBundle,
    LogicalOperator,
    OperatorOutput,
    OutputColumn,
    PhysicalOperator,
    RecoverableOperatorError,
)


def _build_call(
    operator_id: str,
    operator: Operator,
    parameters: type[BaseModel],
    properties: Properties | None,
    ignores_input_bundle: bool = False,
) -> Callable[[DataBundle, dict[str, Any], list[OutputColumn]], OperatorOutput]:
    def call(
        inputs: DataBundle,
        attributes: dict[str, Any],
        required_output_columns: list[OutputColumn],
    ) -> OperatorOutput:
        try:
            validated = parameters.model_validate(attributes)
        except ValidationError as error:
            raise RecoverableOperatorError(
                f"{operator_id} was given parameters it cannot take: {error}"
            ) from error
        # Checked after validation, so a parameter error stays the recoverable failure a
        # fragment runner can answer rather than being masked by a placeholder.
        if ignores_input_bundle and any(inputs):
            raise NotImplementedError(
                f"{operator_id} declares accepts_bound_inputs but the ported operator drops "
                "its input bundle, so a fragment with a non-empty frontier would be answered "
                "from its source alone"
            )
        # A fragment and operator.run speak the same bundle, so nothing is converted here.
        # The list is copied because an operator may write into the bundle it is handed
        # and the caller reuses one bundle across retries.
        #
        # exclude_none because the operators branch on a key's presence: semantic_extract
        # reads entity.get("description", <generated>) and semantic_transform tests
        # "type" in meta_info. A field dumped as None is present to both.
        output, _, meta = operator.run(
            list(inputs), validated.model_dump(exclude_none=True), properties
        )
        failures.raise_for_errors(meta, operator_id)
        # The Blue operators say nothing about how they ran, so the report stays unset.
        return OperatorOutput(data=output)

    return call


@dataclass(frozen=True)
class Operation:
    """One Blue operation: the id it is offered under, what it covers, and how it is resolved."""

    operator_id: str
    logical_operators: frozenset[LogicalOperator]
    # The version is where an operation's validator and its result shape are fixed, so each row
    # names one rather than the suite naming one for all eighteen.
    registry_operator_id: str
    parameters: type[BaseModel]
    needs_data_registry: bool = False
    source_kinds: frozenset[str] | None = None
    accepts_bound_inputs: bool = False
    limitations: str | None = None
    ignores_input_bundle: bool = False


# AGGREGATE and SORT_SUPERLATIVE take several operators each, because summing and
# averaging are different operations sharing a node kind rather than two
# implementations of one. A single AGGREGATE operator would leave a node asking for an
# average with no candidate at all.
#
# The order is what operators_for returns and what the physical planner reads: relational,
# then aggregate, then semantic, then nl2x, so deterministic operators come before
# model-backed ones, and single-node operators come before the compound blue.nl2sql, which
# is last for the same reason.
OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "blue.select",
        frozenset({LogicalOperator.FILTER}),
        "select@2.0.0",
        SelectParameters,
        limitations=(
            "One comparison against one key. A conjunction of predicates needs one node per "
            "comparison."
        ),
    ),
    Operation(
        "blue.project", frozenset({LogicalOperator.PROJECT}), "project@3.0.0", ProjectParameters
    ),
    Operation("blue.join", frozenset({LogicalOperator.JOIN}), "join@3.0.0", JoinParameters),
    Operation(
        "blue.intersect",
        frozenset({LogicalOperator.JOIN}),
        "intersect@3.0.0",
        MatchOptionParameters,
        limitations=(
            "Matches whole tuples, so it joins only where both tables share their column names "
            "and their values."
        ),
    ),
    Operation(
        "blue.union", frozenset({LogicalOperator.UNION}), "union@2.0.0", MatchOptionParameters
    ),
    Operation(
        "blue.sum",
        frozenset({LogicalOperator.AGGREGATE}),
        "sum@2.0.0",
        SumParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    Operation(
        "blue.avg",
        frozenset({LogicalOperator.AGGREGATE}),
        "avg@3.0.0",
        AvgParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    Operation(
        "blue.min",
        frozenset({LogicalOperator.AGGREGATE}),
        "min@3.0.0",
        MinParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    Operation(
        "blue.max",
        frozenset({LogicalOperator.AGGREGATE}),
        "max@3.0.0",
        MaxParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    Operation(
        "blue.argmax",
        frozenset({LogicalOperator.SORT_SUPERLATIVE}),
        "argmax@2.0.0",
        ArgmaxParameters,
        limitations=(
            "Returns the one extreme record over the whole table; it does not group by a column."
        ),
    ),
    Operation(
        "blue.argmin",
        frozenset({LogicalOperator.SORT_SUPERLATIVE}),
        "argmin@2.0.0",
        ArgminParameters,
        limitations=(
            "Returns the one extreme record over the whole table; it does not group by a column."
        ),
    ),
    Operation(
        "blue.semantic_filter",
        frozenset({LogicalOperator.FILTER}),
        "semantic_filter@3.0.0",
        SemanticFilterParameters,
    ),
    Operation(
        "blue.semantic_project",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_project@3.0.0",
        SemanticProjectParameters,
    ),
    Operation(
        "blue.semantic_extract",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_extract@3.0.0",
        SemanticExtractParameters,
    ),
    Operation(
        "blue.semantic_transform",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_transform@3.0.0",
        SemanticTransformParameters,
    ),
    Operation(
        "blue.semantic_join",
        frozenset({LogicalOperator.JOIN}),
        "semantic_join@3.0.0",
        SemanticJoinParameters,
    ),
    Operation(
        "blue.nl2llm",
        frozenset({LogicalOperator.SCAN}),
        "nl2llm@3.0.0",
        NL2LLMParameters,
        limitations=(
            "Answers from the model's own world knowledge, so it reads no registered source and "
            "its rows cannot be held to one."
        ),
    ),
    Operation(
        "blue.nl2sql",
        frozenset(
            {
                LogicalOperator.SCAN,
                LogicalOperator.FILTER,
                LogicalOperator.PROJECT,
                LogicalOperator.JOIN,
                LogicalOperator.AGGREGATE,
                LogicalOperator.DISTINCT,
                LogicalOperator.SORT_SUPERLATIVE,
            }
        ),
        "nl2sql@3.0.0",
        NL2SQLParameters,
        needs_data_registry=True,
        source_kinds=frozenset({"sql/sqlite", "sql/mysql", "sql/postgres"}),
        accepts_bound_inputs=True,
        # True to what the operation is for; the port drops its bundle, which is what
        # ignores_input_bundle says and what the raise reports.
        ignores_input_bundle=True,
        limitations=(
            "Cannot express a semantic predicate, and cannot bind tables from other fragments "
            "into the query it writes."
        ),
    ),
)


def blue_operators(
    registry: OperatorRegistry, data_registry: DataRegistry
) -> list[PhysicalOperator]:
    """Return one physical operator per Blue operation a fragment can be bound to."""
    # The operators are returned rather than registered so this module never imports
    # planner/, which would make operators/ depend on the component that routes them.
    operators: list[PhysicalOperator] = []
    for operation in OPERATIONS:
        operator = registry.resolve(operation.registry_operator_id)
        properties: Properties | None = (
            {"data_registry": data_registry} if operation.needs_data_registry else None
        )
        operators.append(
            PhysicalOperator(
                operator_id=operation.operator_id,
                logical_operators=operation.logical_operators,
                description=operator.description,
                parameter_schema=operation.parameters,
                call=_build_call(
                    operation.operator_id,
                    operator,
                    operation.parameters,
                    properties,
                    operation.ignores_input_bundle,
                ),
                source_kinds=operation.source_kinds,
                accepts_bound_inputs=operation.accepts_bound_inputs,
                limitations=operation.limitations,
            )
        )
    return operators
