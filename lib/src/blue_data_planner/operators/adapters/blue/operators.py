"""Blue operators as physical operators, validating parameters and mapping errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from blue_data_ops.operators.operator import Operator
from blue_data_ops.operators.registry import OperatorRegistry, ResolveError, versioned_registry
from blue_data_ops.operators.types import Properties

from blue_data_planner.data import DataRegistry
from blue_data_planner.data.catalog import source_kind
from blue_data_planner.llm import LLMClient
from blue_data_planner.llm.model_names import model_without_provider
from blue_data_planner.operators.adapters.blue import error_mapping
from blue_data_planner.operators.adapters.blue.read_only_sql import ReadOnlySQLRegistry
from blue_data_planner.operators.adapters.blue.schemas import (
    ArgmaxParameters,
    ArgminParameters,
    AvgParameters,
    BlueParameters,
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
from blue_data_planner.operators.adapters.blue.sql_diagnostics import (
    DiagnosticSQLClient,
    SQLDiagnostics,
)
from blue_data_planner.operators.validation import check_table_count, validated_parameters
from blue_data_planner.types import (
    INPUT_ARITY,
    DataBundle,
    LogicalOperator,
    OperatorOutput,
    OperatorRoutingError,
    OutputColumn,
    PhysicalOperator,
    RecoverableOperatorError,
    SourceUnreachableError,
)

# The source kinds blue.nl2sql reads, and the protocol name it takes for each.
_NL2SQL_PROTOCOLS: dict[str, str] = {
    "sql/sqlite": "sqlite",
    "sql/mysql": "mysql",
    "sql/postgres": "postgres",
}


def _nl2sql_protocol(data_registry: DataRegistry, source: str, operator_id: str) -> str:
    try:
        kind = source_kind(data_registry, source)
    except SourceUnreachableError as error:
        raise RecoverableOperatorError(f"{operator_id} cannot read {source!r}: {error}") from error
    if kind not in _NL2SQL_PROTOCOLS:
        raise RecoverableOperatorError(
            f"{operator_id} reads only {', '.join(_NL2SQL_PROTOCOLS)} sources, and {source!r} "
            f"is a {kind} source"
        )
    return _NL2SQL_PROTOCOLS[kind]


def _build_call(
    operator_id: str,
    operator: Operator,
    parameters: type[BlueParameters],
    properties: Properties | None,
    table_count: int | None,
    data_registry: DataRegistry,
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
        attributes_sent = validated.operation_attributes()
        call_properties = properties
        diagnostics: SQLDiagnostics | None = None
        if isinstance(validated, NL2SQLParameters):
            # The protocol is the source's, so it is read off the registry rather than written
            # by the model.
            attributes_sent["protocol"] = _nl2sql_protocol(
                data_registry, attributes_sent["source"], operator_id
            )
            # Ops contains its exceptions before returning ExecMeta. Observe the registry and
            # model boundaries before that detail is lost, using fresh objects for every call.
            diagnostics = SQLDiagnostics(validated.scan_source)
            call_properties = {
                **(properties or {}),
                "data_registry": ReadOnlySQLRegistry(data_registry, diagnostics),
                "llm_client": DiagnosticSQLClient(diagnostics),
            }
        output, reported, meta = operator.run(list(inputs), attributes_sent, call_properties)
        error_mapping.raise_for_errors(
            meta,
            operator_id,
            reported or None,
            execution_detail=diagnostics.detail if diagnostics else None,
        )
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
    parameters: type[BlueParameters]
    source_kinds: frozenset[str] | None = None
    accepts_bound_inputs: bool = False
    limitations: str | None = None
    # What the adapter hands the operation as call properties: the data registry it reads a
    # source through, and the model and reasoning effort its LLM calls run on.
    uses_data_registry: bool = False
    uses_llm: bool = False


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
        uses_llm=True,
    ),
    Operation(
        "blue.semantic_project",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_project@3.0.0",
        SemanticProjectParameters,
        uses_llm=True,
    ),
    Operation(
        "blue.semantic_extract",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_extract@3.0.0",
        SemanticExtractParameters,
        uses_llm=True,
    ),
    Operation(
        "blue.semantic_transform",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_transform@3.0.0",
        SemanticTransformParameters,
        uses_llm=True,
    ),
    Operation(
        "blue.semantic_join",
        frozenset({LogicalOperator.JOIN}),
        "semantic_join@3.0.0",
        SemanticJoinParameters,
        uses_llm=True,
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
        uses_llm=True,
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
        source_kinds=frozenset(_NL2SQL_PROTOCOLS),
        accepts_bound_inputs=True,
        # A declaration has no vocabulary for "every scan reads one database", so the
        # constraint lives here, where the physical planner reads it (open question 44).
        limitations=(
            "express a semantic predicate, or read more than one database in one query, so a "
            "fragment whose scans name two sources is not its to take"
        ),
        uses_data_registry=True,
        uses_llm=True,
    ),
)


def _table_count(operation: Operation) -> int | None:
    # A compound operation covers a group of nodes, and how many tables that group is handed
    # depends on the cut, so only a single-node operation has a count to hold it to.
    if len(operation.logical_operators) != 1:
        return None
    return INPUT_ARITY[next(iter(operation.logical_operators))]


def _model_properties(model: str, reasoning_effort: str | None) -> Properties:
    # blue-data-ops refuses a model string that names a provider: the provider every call is sent
    # to is the one BLUE_DATA_LLM_PROVIDER names in the deployment's environment.
    # The operations default temperature to 0, which reasoning models can reject. None leaves
    # the temperature at the provider's default instead of inheriting that fixed value.
    properties: Properties = {
        "llm.model": model_without_provider(model),
        "llm.temperature": None,
    }
    if reasoning_effort is not None:
        properties["llm.reasoning_effort"] = reasoning_effort
    return properties


def blue_operators(
    registry: OperatorRegistry,
    data_registry: DataRegistry,
    *,
    model: str,
    reasoning_effort: str | None = None,
) -> list[PhysicalOperator]:
    """Return one physical operator per Blue operation a fragment can be bound to."""
    # The operators are returned rather than registered so this module never imports
    # planner/, which would make operators/ depend on the component that routes them.
    operators: list[PhysicalOperator] = []
    for operation in OPERATIONS:
        try:
            operator = registry.resolve(operation.registry_operator_id)
        except ResolveError as error:
            raise OperatorRoutingError(
                f"{operation.operator_id} is bound to {operation.registry_operator_id}, which "
                "the operator registry does not carry"
            ) from error
        properties: Properties = {}
        if operation.uses_data_registry:
            # nl2sql needs rows, but its SQL is model output. Explicitly opt into execution
            # and SELECT validation; the registry enforces single-statement, non-commit SQL
            # even though ops currently exposes no read-only execution switch (#62).
            properties.update(
                {
                    "execute_query": True,
                    "validate_query_prefixes": ["SELECT"],
                }
            )
        if operation.uses_llm:
            properties.update(_model_properties(model, reasoning_effort))
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
                    properties or None,
                    _table_count(operation),
                    data_registry,
                ),
                source_kinds=operation.source_kinds,
                accepts_bound_inputs=operation.accepts_bound_inputs,
                limitations=operation.limitations,
            )
        )
    return operators


@dataclass(frozen=True)
class BlueSuite:
    """The Blue operator library's operations, the registry that resolves them, and the model
    they run on."""

    name: ClassVar[str] = "blue"
    # None resolves the operation table's exact pins from blue-data-ops' retained versions.
    operator_registry: OperatorRegistry | None = None
    # None runs the operations on the model every other component uses.
    model: str | None = None
    # None omits the parameter, for a model that does not accept one.
    reasoning_effort: str | None = None

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]:
        """Return one physical operator per Blue operation a fragment can be bound to."""
        registry = self.operator_registry
        if registry is None:
            registry = versioned_registry()
        return blue_operators(
            registry,
            data_registry,
            model=self.model or model,
            reasoning_effort=self.reasoning_effort,
        )
