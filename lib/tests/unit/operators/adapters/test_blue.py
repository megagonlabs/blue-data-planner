"""The Blue operator adapter: failure translation, the callable, and the operator table."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from blue_data_ops.operators.join_operator import JoinOperator
from blue_data_ops.operators.operator import Operator
from blue_data_ops.operators.registry import ResolveError, default_registry, versioned_registry
from blue_data_ops.operators.select_operator import SelectOperator
from blue_data_ops.operators.types import (
    Attributes,
    AttributeSpec,
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    ErrorStage,
    ExecMeta,
    Properties,
)
from pydantic import BaseModel, ConfigDict

from blue_data_planner.data import DataRegistryClient
from blue_data_planner.operators.adapters.blue import (
    _OPERATOR_SPECS,  # pyright: ignore[reportPrivateUsage]
    _build_call,  # pyright: ignore[reportPrivateUsage]
    _raise_for_errors,  # pyright: ignore[reportPrivateUsage]
    blue_operators,
)
from blue_data_planner.operators.router import OperatorRouter
from blue_data_planner.types import (
    BlueDataPlannerError,
    Data,
    DataBundle,
    LogicalOperator,
    PhysicalOperator,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)
from tests.doubles.data import DataRegistryClientDouble


def _meta(code: ErrorCode, stage: ErrorStage, suggestion: str | None = None) -> ExecMeta:
    record = ErrorRecord(
        category=ErrorCategory.FAILED,
        code=code,
        operator_id="select@2.0.0",
        stage=stage,
        message="something went wrong",
        retryable=False,
        suggestion=suggestion,
    )
    return ExecMeta(is_empty=False, errors=[record], logs=[], traces={})


def test_no_errors_raises_nothing() -> None:
    """An ExecMeta with an empty error list returns without raising."""
    _raise_for_errors(ExecMeta(is_empty=False, errors=[], logs=[], traces={}), "blue.select")


@pytest.mark.parametrize(
    ("code", "stage"),
    [
        (ErrorCode.INVALID_INPUT, ErrorStage.VALIDATE),
        (ErrorCode.EXECUTION_FAILED, ErrorStage.EXECUTE),
    ],
)
def test_a_failure_another_operator_might_avoid_is_recoverable(
    code: ErrorCode, stage: ErrorStage
) -> None:
    """Rejected attributes and input the operator could not handle both allow another try."""
    with pytest.raises(RecoverableOperatorError):
        _raise_for_errors(_meta(code, stage), "blue.select")


def test_a_timeout_is_unrecoverable() -> None:
    """A timed-out call ends the run rather than sending the node back for another attempt."""
    with pytest.raises(UnrecoverableOperatorError):
        _raise_for_errors(_meta(ErrorCode.OPERATOR_TIMEOUT, ErrorStage.EXECUTE), "blue.select")


@pytest.mark.parametrize(
    ("code", "stage"),
    [
        (ErrorCode.DEFAULTS_FAILED, ErrorStage.DEFAULTS),
        (ErrorCode.VALIDATION_FAILED, ErrorStage.VALIDATE),
        (ErrorCode.VALIDATION_RESULT_INVALID, ErrorStage.VALIDATE),
        (ErrorCode.EXECUTION_RESULT_INVALID, ErrorStage.NORMALIZE),
    ],
)
def test_a_broken_operator_raises_something_nothing_catches(
    code: ErrorCode, stage: ErrorStage
) -> None:
    """The four codes naming a broken operator raise a bare BlueDataPlannerError."""
    with pytest.raises(BlueDataPlannerError) as caught:
        _raise_for_errors(_meta(code, stage), "blue.select")

    # execute_physical_operator catches only the two operator error types. Raising either of
    # them for a bug would turn it into a failed call instead of letting it reach the caller.
    assert not isinstance(caught.value, (RecoverableOperatorError, UnrecoverableOperatorError))


def test_the_message_names_the_operator_the_stage_and_the_suggestion() -> None:
    """A raised message carries enough for a review turn to say what to try next."""
    with pytest.raises(RecoverableOperatorError) as caught:
        _raise_for_errors(
            _meta(ErrorCode.INVALID_INPUT, ErrorStage.VALIDATE, "supply a supported operand"),
            "blue.select",
        )

    message = str(caught.value)
    assert "blue.select" in message
    assert "VALIDATE" in message
    assert "supply a supported operand" in message


def test_only_the_first_error_decides() -> None:
    """A record list is read from the front, so the first failure picks the exception."""
    first = _meta(ErrorCode.INVALID_INPUT, ErrorStage.VALIDATE).errors[0]
    second = _meta(ErrorCode.VALIDATION_FAILED, ErrorStage.VALIDATE).errors[0]
    meta = ExecMeta(is_empty=False, errors=[first, second], logs=[], traces={})

    with pytest.raises(RecoverableOperatorError):
        _raise_for_errors(meta, "blue.select")


class _LooseSelectParameters(BaseModel):
    """A select schema that lets a bad operand through, so the operator's own check runs."""

    model_config = ConfigDict(extra="forbid")

    operand_key: str
    operand: str
    operand_val: str | float


class _StrictSelectParameters(BaseModel):
    """The shape of a real operator schema: unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")

    operand_key: str
    operand: str
    operand_val: str | float


class _NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _OptionalParameters(BaseModel):
    """A schema whose optional field has no stand-in default, as the nested models do."""

    model_config = ConfigDict(extra="forbid")

    named: str
    unnamed: str | None = None


class _EchoingOperator(Operator):
    """Returns whatever bundle a test tells it to, and records what it was passed."""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "returns a scripted bundle"
    attributes: ClassVar[dict[str, AttributeSpec]] = {}

    def __init__(self, output: DataBundle) -> None:
        super().__init__()
        self.output = output
        self.seen_inputs: DataBundle | None = None
        self.seen_properties: Properties | None = None
        self.seen_attributes: Attributes | None = None

    def apply(
        self,
        input_data: DataBundle,
        attributes: Attributes,
        properties: Properties | None = None,
    ) -> DataBundle:
        self.seen_inputs = input_data
        self.seen_attributes = attributes
        self.seen_properties = properties
        return self.output


_ROWS: Data = [{"city": "Austin", "population": 2}, {"city": "Dallas", "population": 9}]


def test_parameters_the_schema_refuses_are_recoverable() -> None:
    """A model rejection is what the fragment runner hits most often, and it allows another try."""
    call = _build_call("blue.select", SelectOperator(), _StrictSelectParameters, None)

    with pytest.raises(RecoverableOperatorError, match="blue.select"):
        call([_ROWS], {"operand_key": "population", "operand": ">", "eps": 0.1})


def test_parameters_the_operator_refuses_are_recoverable() -> None:
    """A schema-valid operand the operator rejects reaches its validator and still retries."""
    call = _build_call("blue.select", SelectOperator(), _LooseSelectParameters, None)

    with pytest.raises(RecoverableOperatorError, match="blue.select"):
        call([_ROWS], {"operand_key": "population", "operand": "~=", "operand_val": 3})


def test_one_input_reaches_the_operator_as_one_record_group() -> None:
    """A single-input node passes its rows through as a bundle of one group."""
    operator = _EchoingOperator([[]])
    call = _build_call("blue.echo", operator, _NoParameters, None)

    call([_ROWS], {})

    assert operator.seen_inputs == [_ROWS]


def test_two_inputs_reach_the_operator_as_two_record_groups() -> None:
    """A JOIN node's two inputs arrive as two groups, in the order the node lists them."""
    right: Data = [{"city": "Austin", "state": "TX"}]
    operator = _EchoingOperator([[]])
    call = _build_call("blue.echo", operator, _NoParameters, None)

    call([_ROWS, right], {})

    assert operator.seen_inputs == [_ROWS, right]


def test_zero_inputs_reach_the_operator_as_an_empty_bundle() -> None:
    """A SCAN node reads a source rather than another node, so it passes no groups."""
    operator = _EchoingOperator([[]])
    call = _build_call("blue.echo", operator, _NoParameters, None)

    call([], {})

    assert operator.seen_inputs == []


def test_properties_reach_the_operator() -> None:
    """An operator that closed over properties passes them on every call."""
    operator = _EchoingOperator([[]])
    call = _build_call("blue.echo", operator, _NoParameters, {"data_registry": "sentinel"})

    call([], {})

    assert operator.seen_properties == {"data_registry": "sentinel"}


def test_a_single_table_result_passes_through_as_a_bundle() -> None:
    """A node passes a bundle, so a one-table result arrives as a one-table bundle."""
    call = _build_call("blue.echo", _EchoingOperator([_ROWS]), _NoParameters, None)

    assert call([], {}) == [_ROWS]


def test_a_zero_row_result_is_not_a_failure() -> None:
    """An operator that matched nothing returns an empty table rather than raising."""
    call = _build_call("blue.echo", _EchoingOperator([[]]), _NoParameters, None)

    assert call([], {}) == [[]]


def test_the_callable_reshapes_nothing_the_operator_returned() -> None:
    """An empty result arrives exactly as `Operator.run` hands it over."""
    call = _build_call("blue.echo", _EchoingOperator([]), _NoParameters, None)

    assert call([], {}) == [[]]


def test_more_than_one_output_table_passes_through() -> None:
    """A multi-output operator's tables all arrive, in the order it returned them."""
    right: Data = [{"city": "Dallas", "state": "TX"}]
    call = _build_call("blue.echo", _EchoingOperator([_ROWS, right]), _NoParameters, None)

    assert call([], {}) == [_ROWS, right]


def test_a_real_operator_runs_end_to_end() -> None:
    """SelectOperator filters real rows through the callable and returns a one-table bundle."""
    call = _build_call("blue.select", SelectOperator(), _StrictSelectParameters, None)

    result = call([_ROWS], {"operand_key": "population", "operand": ">", "operand_val": 5})

    assert result == [[{"city": "Dallas", "population": 9}]]


def test_a_real_join_runs_end_to_end() -> None:
    """JoinOperator reads its two inputs as two tables of one bundle and joins them."""

    class _JoinTestParameters(BaseModel):
        model_config = ConfigDict(extra="forbid")

        join_on: list[list[str]]

    right: Data = [{"city": "Dallas", "state": "TX"}]
    call = _build_call("blue.join", JoinOperator(), _JoinTestParameters, None)

    result = call([_ROWS, right], {"join_on": [["city"], ["city"]]})

    assert [row["city"] for row in result[0]] == ["Dallas"]


def test_a_real_union_runs_end_to_end() -> None:
    """Union merges two tables and drops the duplicate, so the wiring holds for a set operation."""
    operators = {operator.operator_id: operator for operator in _operators()}

    result = operators["blue.union"].call(
        [[{"city": "Austin"}], [{"city": "Austin"}, {"city": "Dallas"}]], {}
    )

    assert result == [[{"city": "Austin"}, {"city": "Dallas"}]]


def test_a_real_intersect_runs_end_to_end() -> None:
    """Intersect keeps the record both tables hold, matched on the whole tuple."""
    operators = {operator.operator_id: operator for operator in _operators()}

    result = operators["blue.intersect"].call(
        [[{"city": "Austin"}, {"city": "Dallas"}], [{"city": "Dallas"}]], {}
    )

    assert result == [[{"city": "Dallas"}]]


def test_the_bundle_the_caller_owns_is_not_the_one_the_operator_gets() -> None:
    """An operator may write into the bundle it gets, so it must not get the caller's own."""
    operator = _EchoingOperator([_ROWS])
    call = _build_call("blue.echo", operator, _NoParameters, None)
    inputs: DataBundle = [_ROWS]

    call(inputs, {})

    assert operator.seen_inputs == inputs
    assert operator.seen_inputs is not inputs


def test_a_parameter_the_runner_left_unset_does_not_reach_the_operator() -> None:
    """The operators branch on a key's presence, so a null would read as a supplied value."""
    operator = _EchoingOperator([[]])
    call = _build_call("blue.echo", operator, _OptionalParameters, None)

    call([_ROWS], {"named": "x"})

    assert operator.seen_attributes == {"named": "x"}


def test_an_operator_that_ignores_its_bundle_refuses_tables_it_would_drop() -> None:
    """Answering from a source alone while holding frontier rows is a silent wrong answer."""
    call = _build_call(
        "blue.echo", _EchoingOperator([[]]), _NoParameters, None, ignores_input_bundle=True
    )

    # An empty table binds nothing, so a fragment arriving with one still runs.
    assert call([[]], {}) == [[]]

    with pytest.raises(NotImplementedError, match="blue.echo"):
        call([_ROWS], {})


def test_a_refused_parameter_is_reported_before_a_bundle_it_cannot_bind() -> None:
    """A recoverable parameter error must not be masked by a placeholder nothing retries."""
    call = _build_call(
        "blue.echo", _EchoingOperator([[]]), _NoParameters, None, ignores_input_bundle=True
    )

    with pytest.raises(RecoverableOperatorError):
        call([_ROWS], {"not_a_parameter": 1})


_EXPECTED_OPERATORS: list[tuple[str, frozenset[LogicalOperator]]] = [
    ("blue.select", frozenset({LogicalOperator.FILTER})),
    ("blue.project", frozenset({LogicalOperator.PROJECT})),
    ("blue.join", frozenset({LogicalOperator.JOIN})),
    ("blue.intersect", frozenset({LogicalOperator.JOIN})),
    ("blue.union", frozenset({LogicalOperator.UNION})),
    ("blue.sum", frozenset({LogicalOperator.AGGREGATE})),
    ("blue.avg", frozenset({LogicalOperator.AGGREGATE})),
    ("blue.min", frozenset({LogicalOperator.AGGREGATE})),
    ("blue.max", frozenset({LogicalOperator.AGGREGATE})),
    ("blue.argmax", frozenset({LogicalOperator.SORT_SUPERLATIVE})),
    ("blue.argmin", frozenset({LogicalOperator.SORT_SUPERLATIVE})),
    ("blue.semantic_filter", frozenset({LogicalOperator.FILTER})),
    ("blue.semantic_project", frozenset({LogicalOperator.PROJECT})),
    ("blue.semantic_extract", frozenset({LogicalOperator.PROJECT})),
    ("blue.semantic_transform", frozenset({LogicalOperator.PROJECT})),
    ("blue.semantic_join", frozenset({LogicalOperator.JOIN})),
    ("blue.nl2llm", frozenset({LogicalOperator.SCAN})),
    (
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
    ),
]

# The two logical operators no registered operator answers for, for different reasons.
# RETURN is structural: it names the columns the answer carries rather than a step, and a
# RETURN node belongs to no fragment, so no operator ever runs it. EXPAND opens one row
# into many, which neither the Blue operators nor LOTUS does.
_KNOWN_GAPS = frozenset({LogicalOperator.EXPAND, LogicalOperator.RETURN})


class _DataRegistryClientSeam(DataRegistryClientDouble, DataRegistryClient):
    """The recording double, typed as a DataRegistryClient so injection resolves it."""


def _operators() -> list[PhysicalOperator]:
    return blue_operators(versioned_registry(), _DataRegistryClientSeam())


def _router_from_operators() -> OperatorRouter:
    router = OperatorRouter()
    for operator in _operators():
        router.register(operator)
    return router


def test_the_operator_table_is_exactly_what_the_design_names() -> None:
    """The eighteen operators design/06 declares and the adapter registers, in order."""
    operators = _operators()

    assert [
        (operator.operator_id, operator.logical_operators) for operator in operators
    ] == _EXPECTED_OPERATORS


def test_every_operator_carries_a_description() -> None:
    """Descriptions are non-empty, since they are what the physical planner picks an operator on."""
    for operator in _operators():
        assert operator.description


def test_a_registry_without_2_0_0_cards_is_refused() -> None:
    """Only versioned_registry() carries the 2.0.0 cards the operators resolve."""
    with pytest.raises(ResolveError):
        blue_operators(default_registry(), _DataRegistryClientSeam())


def test_the_operators_leave_only_the_known_gaps_unanswered() -> None:
    """A router filled from the adapter answers for every logical operator outside the gaps."""
    router = _router_from_operators()

    unanswered = {
        logical_op for logical_op in LogicalOperator if not router.operators_for(logical_op)
    }

    assert unanswered == _KNOWN_GAPS


def test_a_compound_operator_competes_for_every_kind_it_declares() -> None:
    """Running several nodes in one call starts with one operator answering for all of them."""
    router = _router_from_operators()

    answering = {
        logical_op
        for logical_op in LogicalOperator
        if any(op.operator_id == "blue.nl2sql" for op in router.operators_for(logical_op))
    }

    assert answering == frozenset(
        {
            LogicalOperator.SCAN,
            LogicalOperator.FILTER,
            LogicalOperator.PROJECT,
            LogicalOperator.JOIN,
            LogicalOperator.AGGREGATE,
            LogicalOperator.DISTINCT,
            LogicalOperator.SORT_SUPERLATIVE,
        }
    )


def test_only_an_operator_reading_a_registered_source_declares_source_kinds() -> None:
    """A declaration constrains a scan's binding only where the operator does the scanning."""
    declaring = {
        operator.operator_id: operator.source_kinds
        for operator in _operators()
        if operator.source_kinds is not None
    }

    assert declaring == {"blue.nl2sql": frozenset({"sql/sqlite", "sql/mysql", "sql/postgres"})}


def test_nl2sql_refuses_a_fragment_whose_rows_it_would_ignore() -> None:
    """It writes SQL from a schema alone, so bound tables would be silently left out."""
    operators = {operator.operator_id: operator for operator in _operators()}
    parameters = {
        "question": "how many drivers are there",
        "source": "tag_bench",
        "database": "formula_1",
        "collection": "main",
        "protocol": "sqlite",
    }

    with pytest.raises(NotImplementedError, match="blue.nl2sql"):
        operators["blue.nl2sql"].call([_ROWS], parameters)


def test_each_operator_id_is_unique() -> None:
    """Registering all eighteen into one router does not trip the duplicate check."""
    assert len(_router_from_operators().operators_for(LogicalOperator.AGGREGATE)) == 5


@pytest.mark.parametrize("spec", _OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_a_declaration_reaches_the_operator_the_planner_reads(spec: Any) -> None:
    """Whatever a spec declares is what the router hands the physical planner."""
    operator = {op.operator_id: op for op in _operators()}[spec.operator_id]

    assert operator.source_kinds == spec.source_kinds
    assert operator.accepts_bound_inputs == spec.accepts_bound_inputs
    assert operator.limitations == spec.limitations


def test_a_built_operator_runs() -> None:
    """The select operator's callable filters real rows, so the wiring holds end to end."""
    operators = {operator.operator_id: operator for operator in _operators()}

    result = operators["blue.select"].call(
        [_ROWS], {"operand_key": "population", "operand": ">", "operand_val": 5}
    )

    assert result == [[{"city": "Dallas", "population": 9}]]


def _operator_for(operator_name: str) -> Operator:
    return versioned_registry().resolve(f"{operator_name}@2.0.0")


@pytest.mark.parametrize("spec", _OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_every_schema_field_names_a_real_attribute(spec: Any) -> None:
    """A model field the operator does not declare would be dropped without a word."""
    declared = set(_operator_for(spec.operator_name).attributes)

    unknown = set(spec.parameters.model_fields) - declared

    assert not unknown, f"{spec.operator_id} names attributes {sorted(unknown)} the operator lacks"


@pytest.mark.parametrize("spec", _OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_every_required_attribute_is_supplied_or_defaulted(spec: Any) -> None:
    """An attribute the operator requires must come from the model or from its own default."""
    fields = set(spec.parameters.model_fields)

    missing = [
        name
        for name, attribute in _operator_for(spec.operator_name).attributes.items()
        if attribute.required and name not in fields and not attribute.has_default
    ]

    assert not missing, f"{spec.operator_id} leaves required attributes {missing} unset"


@pytest.mark.parametrize("spec", _OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_every_schema_field_is_described(spec: Any) -> None:
    """A field with no description gives the fragment runner nothing to write a value from."""
    undescribed = [
        name for name, field in spec.parameters.model_fields.items() if not field.description
    ]

    assert not undescribed, f"{spec.operator_id} leaves {undescribed} undescribed"


@pytest.mark.parametrize("spec", _OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_every_schema_refuses_unknown_parameters(spec: Any) -> None:
    """An operator that accepted extra keys would let the fragment runner set a tuning parameter."""
    assert spec.parameters.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("spec", _OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_unknown_parameters_are_a_recoverable_failure(spec: Any) -> None:
    """Every operator turns a parameter it does not take into a retry rather than a crash."""
    operators = {operator.operator_id: operator for operator in _operators()}

    with pytest.raises(RecoverableOperatorError):
        operators[spec.operator_id].call([_ROWS], {"not_a_parameter": 1})
