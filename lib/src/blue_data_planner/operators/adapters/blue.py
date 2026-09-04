"""Blue operators as physical operators, validating parameters and mapping failures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from blue_data_ops.operators.operator import Operator
from blue_data_ops.operators.registry import OperatorRegistry
from blue_data_ops.operators.types import ErrorCode, ExecMeta, Properties
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from blue_data_planner.data import DataRegistryClient
from blue_data_planner.types import (
    BlueDataPlannerError,
    DataBundle,
    LogicalOperator,
    PhysicalOperator,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)

# A different set of parameters, or a different operator, might avoid either of these.
_RECOVERABLE_ERROR_CODES = frozenset({ErrorCode.INVALID_INPUT, ErrorCode.EXECUTION_FAILED})
# Every operator gets the same rows and the same time, so retrying only pays the timeout
# again.
_UNRECOVERABLE_ERROR_CODES = frozenset({ErrorCode.OPERATOR_TIMEOUT})


def _raise_for_errors(meta: ExecMeta, operator_id: str) -> None:
    if not meta.errors:
        return
    error = meta.errors[0]
    detail = error.message if error.suggestion is None else f"{error.message}: {error.suggestion}"
    message = f"{operator_id} failed at {error.stage.value}: {detail}"
    if error.code in _RECOVERABLE_ERROR_CODES:
        raise RecoverableOperatorError(message)
    if error.code in _UNRECOVERABLE_ERROR_CODES:
        raise UnrecoverableOperatorError(message)
    # The remaining codes (DEFAULTS_FAILED, VALIDATION_FAILED, VALIDATION_RESULT_INVALID,
    # EXECUTION_RESULT_INVALID) all mean the operator's own implementation is broken, not
    # that a different set of parameters could have avoided the failure. Raising a bare
    # BlueDataPlannerError, which nothing here catches, lets it reach the interface boundary
    # with its traceback instead of being recorded as an ordinary fragment failure.
    raise BlueDataPlannerError(message)


def _build_call(
    operator_id: str,
    operator: Operator,
    parameters: type[BaseModel],
    properties: Properties | None,
    ignores_input_bundle: bool = False,
) -> Callable[[DataBundle, dict[str, Any]], DataBundle]:
    def call(inputs: DataBundle, attributes: dict[str, Any]) -> DataBundle:
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
        _raise_for_errors(meta, operator_id)
        return output

    return call


# Unknown keys are refused so a runner cannot reach a tuning parameter a declaration
# deliberately leaves out. Everything omitted keeps the operator's own default.
_STRICT = ConfigDict(extra="forbid")


class _SelectParameters(BaseModel):
    """Filter records by one type-aware comparison against a key."""

    model_config = _STRICT

    operand_key: str = Field(description="The key to check in each record")
    operand: Literal["=", "!=", ">", ">=", "<", "<="] = Field(description="Comparison operator")
    # The operator declares this Any, which no strict schema can express. Naming the
    # types a plan actually compares against is what makes the schema fillable.
    operand_val: bool | int | float | str = Field(description="Value to compare with")


class _ProjectParameters(BaseModel):
    """Keep a named subset of each record's keys, optionally renaming them."""

    model_config = _STRICT

    kept_keys: list[str] = Field(description="List of keys to keep in each record")
    key_mapping: dict[str, str] = Field(
        default_factory=dict[str, str],
        description="Dictionary mapping old key names to new key names",
    )


class _SumParameters(BaseModel):
    """Add up one numeric key across every record."""

    model_config = _STRICT

    sum_key: str = Field(description="The key to sum the values for")
    output_key: str = Field(default="sum", description="The key name for the result sum")


class _AvgParameters(BaseModel):
    """Average one numeric key across every record."""

    model_config = _STRICT

    avg_key: str = Field(description="The key to average the values for")
    output_key: str = Field(default="avg", description="The key name for the result average")


class _MinParameters(BaseModel):
    """Return the smallest value of one key across every record."""

    model_config = _STRICT

    min_key: str = Field(description="The key to find the minimum value for")
    output_key: str = Field(default="min", description="The key name for the result min value")


class _MaxParameters(BaseModel):
    """Return the largest value of one key across every record."""

    model_config = _STRICT

    max_key: str = Field(description="The key to find the maximum value for")
    output_key: str = Field(default="max", description="The key name for the result max value")


class _ArgmaxParameters(BaseModel):
    """Return the record holding the largest value of one key."""

    model_config = _STRICT

    argmax_key: str = Field(description="The key to find the maximum value for")


class _ArgminParameters(BaseModel):
    """Return the record holding the smallest value of one key."""

    model_config = _STRICT

    argmin_key: str = Field(description="The key to find the minimum value for")


class _JoinParameters(BaseModel):
    """Join two tables on one key list per table."""

    model_config = _STRICT

    join_on: list[list[str]] = Field(description="List of join key lists for each data source")
    join_type: Literal["inner", "left", "right", "outer"] = Field(
        default="inner", description="Type of join"
    )
    keep_keys: Literal["left", "both"] = Field(
        default="left", description="'left' to keep left keys only, 'both' to keep both"
    )


class _MatchOptionParameters(BaseModel):
    """How two records are compared, shared by the two set operations."""

    model_config = _STRICT

    match_option: Literal["key_match", "seq_match"] = Field(
        default="key_match",
        description=(
            "'key_match' compares field names and values; 'seq_match' compares by position"
        ),
    )


class _SemanticFilterParameters(BaseModel):
    """Keep the records a model judges to satisfy a condition per field."""

    model_config = _STRICT

    filter_conditions: dict[str, str] = Field(
        description="Field name to the natural-language condition its value has to satisfy"
    )
    context: str = Field(
        default="", description="Optional domain knowledge to apply to every condition"
    )


class _SemanticProjectParameters(BaseModel):
    """Keep and rename columns from a description of what the result should hold."""

    model_config = _STRICT

    projection_instructions: str = Field(
        description="Which columns to keep and how to rename them, in plain language"
    )


class _EntityToExtract(BaseModel):
    """One entity a semantic extraction pulls out of each record."""

    model_config = _STRICT

    name: str = Field(description="The output field name for this entity")
    description: str | None = Field(
        default=None, description="What to extract; omit to let the operator write one"
    )
    extract_on_fields: list[str] = Field(
        default_factory=list[str],
        description="Fields to read; empty reads every field in the record",
    )
    type: str | None = Field(default=None, description="Expected type of the extracted value")


class _SemanticExtractParameters(BaseModel):
    """Pull named entities out of each record's text into new columns."""

    model_config = _STRICT

    entities: list[_EntityToExtract] = Field(
        description="The entities to extract, one per output column"
    )
    context: str = Field(
        default="", description="Optional domain knowledge to apply to every extraction"
    )


class _TransformField(BaseModel):
    """One target column a semantic transform produces."""

    model_config = _STRICT

    source_fields: list[str] = Field(description="Input fields this column is computed from")
    description: str | None = Field(
        default=None, description="What the column should hold, in plain language"
    )
    type: str | None = Field(default=None, description="Expected type of the column's values")
    transformation_type: Literal[
        "field_mapping_without_value_change", "field_mapping_with_value_change"
    ] = Field(
        default="field_mapping_with_value_change",
        description="Whether the value is copied unchanged or computed from its sources",
    )
    required: bool | None = Field(
        default=None, description="Whether every record has to produce this column"
    )
    hints: str | None = Field(default=None, description="Extra guidance for computing the column")


class _InputFieldMeta(BaseModel):
    """What an input column holds, where the rows alone do not say."""

    model_config = _STRICT

    description: str | None = Field(default=None, description="What the input field holds")
    type: str | None = Field(default=None, description="The input field's type")
    hints: str | None = Field(default=None, description="Extra guidance for reading the field")


class _SemanticTransformParameters(BaseModel):
    """Compute new columns from existing ones with a model."""

    model_config = _STRICT

    output_desc: dict[str, _TransformField] = Field(
        description="Target column name to how that column should be produced"
    )
    input_meta: dict[str, _InputFieldMeta] = Field(
        default_factory=dict[str, _InputFieldMeta],
        description="Input column name to what it holds, where the rows alone do not say",
    )


class _SemanticJoinParameters(BaseModel):
    """Join two tables on a model's judgment of when two records match."""

    model_config = _STRICT

    left_fields: list[str] = Field(description="Fields of the left table to compare on")
    right_fields: list[str] = Field(description="Fields of the right table to compare on")
    join_predicate: str = Field(
        default="", description="When two records should be joined, in plain language"
    )
    join_type: Literal["inner", "left", "right", "outer"] = Field(
        default="inner", description="Type of join"
    )
    keep_keys: Literal["left", "both"] = Field(
        default="left", description="'left' to keep left keys only, 'both' to keep both"
    )
    context: str = Field(default="", description="Optional domain knowledge for the comparison")


class _QueryAttribute(BaseModel):
    """One output field a model-answered scan should produce."""

    model_config = _STRICT

    name: str = Field(description="The output field name")
    type: str | None = Field(default=None, description="Expected type of the field's values")


class _NL2LLMParameters(BaseModel):
    """Answer a question from the model's own world knowledge."""

    model_config = _STRICT

    query: str = Field(description="The question to answer")
    context: str = Field(default="", description="Optional domain knowledge for the answer")
    attrs: list[_QueryAttribute] = Field(
        default_factory=list[_QueryAttribute],
        description="The fields each returned record should carry",
    )


class _NL2SQLParameters(BaseModel):
    """Write one SQL query against a registered relational source and run it."""

    model_config = _STRICT

    question: str = Field(description="The question to translate into SQL")
    source: str = Field(
        description="Registered source name, from the fragment's scan_sources catalog path"
    )
    database: str = Field(description="Database name within the source")
    collection: str = Field(description="Collection or schema name within the database")
    protocol: Literal["sqlite", "mysql", "postgres"] = Field(
        description="The source's database protocol"
    )
    context: str = Field(default="", description="Optional domain knowledge for the translation")
    attr_names: list[str] = Field(
        default_factory=list[str],
        description="The field names each returned record should carry",
    )


@dataclass(frozen=True)
class _OperatorSpec:
    operator_id: str
    logical_operators: frozenset[LogicalOperator]
    operator_name: str
    parameters: type[BaseModel]
    needs_data_registry: bool = False
    source_kinds: frozenset[str] | None = None
    accepts_bound_inputs: bool = False
    limitations: str | None = None
    ignores_input_bundle: bool = False


# The registry pins a class by its `name` attribute, so an id reads select@2.0.0.
_OPERATOR_VERSION = "2.0.0"

# AGGREGATE and SORT_SUPERLATIVE take several operators each, because summing and
# averaging are different operations sharing a node kind rather than two
# implementations of one. A single AGGREGATE operator would leave a node asking for an
# average with no candidate at all.
#
# The order is what operators_for returns and what the physical planner reads: relational,
# then aggregate, then semantic, then nl2x, so deterministic operators come before
# model-backed ones, and single-node operators come before the compound blue.nl2sql, which
# is last for the same reason.
_OPERATOR_SPECS: tuple[_OperatorSpec, ...] = (
    _OperatorSpec(
        "blue.select",
        frozenset({LogicalOperator.FILTER}),
        "select",
        _SelectParameters,
        limitations=(
            "One comparison against one key. A conjunction of predicates needs one node per "
            "comparison."
        ),
    ),
    _OperatorSpec(
        "blue.project", frozenset({LogicalOperator.PROJECT}), "project", _ProjectParameters
    ),
    _OperatorSpec("blue.join", frozenset({LogicalOperator.JOIN}), "join", _JoinParameters),
    _OperatorSpec(
        "blue.intersect",
        frozenset({LogicalOperator.JOIN}),
        "intersect",
        _MatchOptionParameters,
        limitations=(
            "Matches whole tuples, so it joins only where both tables share their column names "
            "and their values."
        ),
    ),
    _OperatorSpec(
        "blue.union", frozenset({LogicalOperator.UNION}), "union", _MatchOptionParameters
    ),
    _OperatorSpec(
        "blue.sum",
        frozenset({LogicalOperator.AGGREGATE}),
        "sum",
        _SumParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    _OperatorSpec(
        "blue.avg",
        frozenset({LogicalOperator.AGGREGATE}),
        "avg",
        _AvgParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    _OperatorSpec(
        "blue.min",
        frozenset({LogicalOperator.AGGREGATE}),
        "min",
        _MinParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    _OperatorSpec(
        "blue.max",
        frozenset({LogicalOperator.AGGREGATE}),
        "max",
        _MaxParameters,
        limitations="Aggregates the whole table; it does not group by a column.",
    ),
    _OperatorSpec(
        "blue.argmax",
        frozenset({LogicalOperator.SORT_SUPERLATIVE}),
        "argmax",
        _ArgmaxParameters,
        limitations=(
            "Returns the one extreme record over the whole table; it does not group by a column."
        ),
    ),
    _OperatorSpec(
        "blue.argmin",
        frozenset({LogicalOperator.SORT_SUPERLATIVE}),
        "argmin",
        _ArgminParameters,
        limitations=(
            "Returns the one extreme record over the whole table; it does not group by a column."
        ),
    ),
    _OperatorSpec(
        "blue.semantic_filter",
        frozenset({LogicalOperator.FILTER}),
        "semantic_filter",
        _SemanticFilterParameters,
    ),
    _OperatorSpec(
        "blue.semantic_project",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_project",
        _SemanticProjectParameters,
    ),
    _OperatorSpec(
        "blue.semantic_extract",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_extract",
        _SemanticExtractParameters,
    ),
    _OperatorSpec(
        "blue.semantic_transform",
        frozenset({LogicalOperator.PROJECT}),
        "semantic_transform",
        _SemanticTransformParameters,
    ),
    _OperatorSpec(
        "blue.semantic_join",
        frozenset({LogicalOperator.JOIN}),
        "semantic_join",
        _SemanticJoinParameters,
    ),
    _OperatorSpec(
        "blue.nl2llm",
        frozenset({LogicalOperator.SCAN}),
        "nl2llm",
        _NL2LLMParameters,
        limitations=(
            "Answers from the model's own world knowledge, so it reads no registered source and "
            "its rows cannot be held to one."
        ),
    ),
    _OperatorSpec(
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
        "nl2sql",
        _NL2SQLParameters,
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
    registry: OperatorRegistry, data_registry: DataRegistryClient
) -> list[PhysicalOperator]:
    """Return one physical operator per Blue operation a fragment can be bound to."""
    # The operators are returned rather than registered so this module never imports
    # planner/, which would make operators/ depend on the component that routes them.
    operators: list[PhysicalOperator] = []
    for spec in _OPERATOR_SPECS:
        operator = registry.resolve(f"{spec.operator_name}@{_OPERATOR_VERSION}")
        properties: Properties | None = (
            {"data_registry": data_registry} if spec.needs_data_registry else None
        )
        operators.append(
            PhysicalOperator(
                operator_id=spec.operator_id,
                logical_operators=spec.logical_operators,
                description=operator.description,
                parameter_schema=spec.parameters,
                call=_build_call(
                    spec.operator_id,
                    operator,
                    spec.parameters,
                    properties,
                    spec.ignores_input_bundle,
                ),
                source_kinds=spec.source_kinds,
                accepts_bound_inputs=spec.accepts_bound_inputs,
                limitations=spec.limitations,
            )
        )
    return operators
