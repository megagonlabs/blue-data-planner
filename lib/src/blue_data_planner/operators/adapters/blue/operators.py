"""Blue operators as physical operators, validating parameters and mapping errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from blue_data_ops.operators.operator import Operator
from blue_data_ops.operators.registry import OperatorRegistry, default_registry
from blue_data_ops.operators.types import Properties
from pydantic import BaseModel

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators.adapters.blue import error_mapping
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
from blue_data_planner.operators.validation import check_table_count, validated_parameters
from blue_data_planner.types import (
    INPUT_ARITY,
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
    table_count: int | None,
) -> Callable[[DataBundle, dict[str, Any], list[OutputColumn]], OperatorOutput]:
    def call(
        inputs: DataBundle,
        attributes: dict[str, Any],
        required_output_columns: list[OutputColumn],
    ) -> OperatorOutput:
        validated = validated_parameters(parameters, attributes, operator_id)
        # Checked after the parameters, so a parameter error stays the recoverable failure the
        # parameter repair answers rather than being hidden behind a refusal about the bundle.
        if table_count is None:
            # blue.nl2sql is the only operation this branch reaches. It writes its query from
            # a source's schema, with no way to bind another fragment's rows into that query,
            # so a non-empty bundle would be answered from the source alone and the bundle's
            # rows would be silently left out. See design/13-decision-log.md, open question 51.
            if inputs:
                raise RecoverableOperatorError(
                    f"{operator_id} writes its query from the schema of the source it reads, so "
                    f"the {len(inputs)} table(s) this fragment carries would not reach the query"
                )
        else:
            check_table_count(operator_id, inputs, table_count)
        # A fragment and operator.run speak the same bundle, so nothing is converted here.
        # The list is copied because an operator may write into the bundle it is handed
        # and the caller reuses one bundle across retries.
        #
        # exclude_none because the operators branch on a key's presence: semantic_extract
        # reads entity.get("description", <generated>) and semantic_transform tests
        # "type" in meta_info. A field dumped as None is present to both.
        output, reported, meta = operator.run(
            list(inputs), validated.model_dump(exclude_none=True), properties
        )
        error_mapping.raise_for_errors(meta, operator_id, reported or None)
        # ExecMeta.traces stays out: the base class attaches timings and shapes to every call,
        # which is instrumentation rather than something the operation chose to say, and open
        # question 42 is where instrumentation is decided.
        return OperatorOutput(data=output, report=reported or None)

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


# Four aggregates and two superlatives each carry one of these two sentences, because what
# limits them is the operation they share rather than the key each one reads.
_WHOLE_TABLE_AGGREGATE = "group by a column, since it aggregates the whole table"
_WHOLE_TABLE_SUPERLATIVE = (
    "group by a column, since it returns the one extreme record over the whole table"
)

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
            "compare more than one key per call, so a conjunction of predicates needs one node "
            "per comparison"
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
            "join tables unless they share their column names and their values, since it "
            "matches whole tuples"
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
        limitations=_WHOLE_TABLE_AGGREGATE,
    ),
    Operation(
        "blue.avg",
        frozenset({LogicalOperator.AGGREGATE}),
        "avg@3.0.0",
        AvgParameters,
        limitations=_WHOLE_TABLE_AGGREGATE,
    ),
    Operation(
        "blue.min",
        frozenset({LogicalOperator.AGGREGATE}),
        "min@3.0.0",
        MinParameters,
        limitations=_WHOLE_TABLE_AGGREGATE,
    ),
    Operation(
        "blue.max",
        frozenset({LogicalOperator.AGGREGATE}),
        "max@3.0.0",
        MaxParameters,
        limitations=_WHOLE_TABLE_AGGREGATE,
    ),
    Operation(
        "blue.argmax",
        frozenset({LogicalOperator.SORT_SUPERLATIVE}),
        "argmax@2.0.0",
        ArgmaxParameters,
        limitations=_WHOLE_TABLE_SUPERLATIVE,
    ),
    Operation(
        "blue.argmin",
        frozenset({LogicalOperator.SORT_SUPERLATIVE}),
        "argmin@2.0.0",
        ArgminParameters,
        limitations=_WHOLE_TABLE_SUPERLATIVE,
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
            "read a registered source, since it answers from the model's own world knowledge, so "
            "its rows cannot be checked against one"
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
        # A declaration has no vocabulary for "every scan reads one database", so the
        # constraint lives here, where the physical planner reads it (open question 44).
        limitations=(
            "express a semantic predicate, or read more than one database in one query, so a "
            "fragment whose scans name two sources is not its to take"
        ),
    ),
)


def _table_count(operation: Operation) -> int | None:
    # A compound operation covers a group of nodes, and how many tables that group is handed
    # depends on the cut, so only a single-node operation has a count to hold it to.
    if len(operation.logical_operators) != 1:
        return None
    return INPUT_ARITY[next(iter(operation.logical_operators))]


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
                    _table_count(operation),
                ),
                source_kinds=operation.source_kinds,
                accepts_bound_inputs=operation.accepts_bound_inputs,
                limitations=operation.limitations,
            )
        )
    return operators


@dataclass(frozen=True)
class BlueSuite:
    """The Blue operator library's operations, over the registry that resolves them."""

    name: ClassVar[str] = "blue"
    # None resolves each operation against the registry blue-data-ops builds by default.
    operator_registry: OperatorRegistry | None = None

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]:
        """Return one physical operator per Blue operation a fragment can be bound to."""
        return blue_operators(self.operator_registry or default_registry(), data_registry)
