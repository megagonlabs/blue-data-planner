"""The Blue operations a plan can be bound to, and what each call does with what it is handed."""

from __future__ import annotations

import pytest
from blue_data_ops.operators.registry import versioned_registry

from blue_data_planner.data import DataRegistry
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.adapters.blue import blue_operators
from blue_data_planner.operators.adapters.blue.operators import OPERATIONS
from blue_data_planner.types import (
    INPUT_ARITY,
    LogicalOperator,
    PhysicalOperator,
    RecoverableOperatorError,
)
from tests.doubles.blue_operations import (
    OPERATION_NAMES,
    ScriptedOperation,
    a_registry_answering_every_operation,
    scripted_operation,
)
from tests.unit.operators.adapters.blue.test_schemas import A_CALL_THE_MODEL_ACCEPTS


def test_the_table_offers_the_eighteen_operations_the_suite_is_made_of() -> None:
    """A row dropped from the table leaves a node kind one candidate short, and quietly."""
    assert len(OPERATIONS) == 18


def test_the_scripted_registry_names_the_same_operations_the_table_does() -> None:
    """The two lists are written apart, and a name in only one leaves a test scripting nothing."""
    assert OPERATION_NAMES == tuple(
        operation.registry_operator_id.partition("@")[0] for operation in OPERATIONS
    )


def test_every_operation_resolves_the_registry_id_it_names() -> None:
    """An id the registry does not carry has to fail while the suite is built, not mid-run."""
    registry = versioned_registry()

    for operation in OPERATIONS:
        assert registry.resolve(operation.registry_operator_id) is not None


def test_each_operation_binds_the_newest_card_the_registry_carries_for_it() -> None:
    """A newer card refuses more bad input in the operation's own validator, so it is the one
    bound."""
    registry = versioned_registry()

    behind: list[str] = []
    for operation in OPERATIONS:
        name, _, version = operation.registry_operator_id.partition("@")
        latest = registry.find_latest(name)
        if latest is not None and latest.version != version:
            behind.append(f"{operation.operator_id} binds {version}, and {latest.version} exists")

    assert not behind, behind


def a_suite(*scripted: ScriptedOperation) -> dict[str, PhysicalOperator]:
    """Return the suite built over scripted operations, keyed by operator id."""
    registry = a_registry_answering_every_operation(*scripted)
    built = blue_operators(registry, DataRegistry(name="test", rebuild=True))
    return {one.operator_id: one for one in built}


def test_parameters_an_operation_cannot_take_are_refused_before_the_bundle_is_counted() -> None:
    """The parameter repair answers a parameter error, so it is the failure a bad call reports."""
    scripted = scripted_operation("select")
    one = a_suite(scripted)["blue.select"]

    with pytest.raises(RecoverableOperatorError, match="parameters it cannot take"):
        one.call([[{"city": "Austin"}], [{"city": "Dallas"}]], {"operand_key": "city"}, [])

    assert scripted.seen_inputs is None


def test_a_bundle_of_the_wrong_length_is_refused_before_the_operation_runs() -> None:
    """An operation handed the wrong number of tables answers whatever arrived first, quietly."""
    for operation in OPERATIONS:
        if len(operation.logical_operators) != 1:
            continue
        scripted = scripted_operation(operation.registry_operator_id.partition("@")[0])
        one = a_suite(scripted)[operation.operator_id]
        arity = INPUT_ARITY[next(iter(operation.logical_operators))]
        too_many = [[{"city": "Austin"}] for _ in range(arity + 1)]

        try:
            one.call(too_many, A_CALL_THE_MODEL_ACCEPTS[operation.operator_id], [])
        except RecoverableOperatorError:
            pass
        else:
            raise AssertionError(f"{operation.operator_id} accepted {arity + 1} tables")

        assert scripted.seen_inputs is None, f"{operation.operator_id} ran anyway"


def test_the_compound_operation_refuses_a_fragment_whose_rows_it_would_ignore() -> None:
    """It writes its query from a source's schema, so bound tables would be left out in silence."""
    scripted = scripted_operation("nl2sql")
    one = a_suite(scripted)["blue.nl2sql"]

    with pytest.raises(RecoverableOperatorError, match="blue.nl2sql"):
        one.call([[{"city": "Austin"}]], A_CALL_THE_MODEL_ACCEPTS["blue.nl2sql"], [])

    assert scripted.seen_inputs is None


def test_the_compound_operation_runs_on_a_fragment_that_brought_no_rows() -> None:
    """An empty frontier binds nothing, so the query it writes leaves nothing out."""
    scripted = scripted_operation("nl2sql", output=[[{"drivers": 20}]])
    one = a_suite(scripted)["blue.nl2sql"]

    assert one.call([], A_CALL_THE_MODEL_ACCEPTS["blue.nl2sql"], []).data == [[{"drivers": 20}]]


def test_a_call_the_operation_reported_a_failure_for_leaves_another_operator_to_try() -> None:
    """A failed call is a fragment failure the run loop answers, not an exception past it."""
    scripted = scripted_operation("select", failure="population is not a key of these rows")
    one = a_suite(scripted)["blue.select"]

    with pytest.raises(RecoverableOperatorError, match="blue.select failed at EXECUTE"):
        one.call([[{"city": "Austin"}]], A_CALL_THE_MODEL_ACCEPTS["blue.select"], [])


def test_the_compound_operation_is_handed_the_data_registry_it_reads_a_source_through() -> None:
    """It writes its query from a source's schema, which it has no way to read without one."""
    scripted = scripted_operation("nl2sql")
    data_registry = DataRegistry(name="test", rebuild=True)
    built = blue_operators(a_registry_answering_every_operation(scripted), data_registry)
    one = {each.operator_id: each for each in built}["blue.nl2sql"]

    one.call([], A_CALL_THE_MODEL_ACCEPTS["blue.nl2sql"], [])

    assert scripted.seen_properties == {"data_registry": data_registry}


def test_what_an_operation_reported_about_its_run_reaches_the_result() -> None:
    """An average says nothing about how many values it could use, and the operation does."""
    scripted = scripted_operation(
        "avg", output=[[{"avg": 5.5}]], output_attributes={"num_values_used": 2}
    )
    one = a_suite(scripted)["blue.avg"]

    result = one.call([[{"population": 2}, {"population": 9}]], {"avg_key": "population"}, [])

    assert result.report == {"num_values_used": 2}


def test_an_operation_that_reported_nothing_leaves_the_report_unset() -> None:
    """Most operations have nothing to say, and an empty dict would read as a report of none."""
    scripted = scripted_operation("select", output=[[{"city": "Dallas"}]])
    one = a_suite(scripted)["blue.select"]

    result = one.call([[{"city": "Dallas"}]], A_CALL_THE_MODEL_ACCEPTS["blue.select"], [])

    assert result.report is None


def test_every_operation_the_suite_offers_reaches_the_router_as_the_table_declares_it() -> None:
    """Planning binds against the declaration and never looks inside the operation."""
    built = a_suite()

    for operation in OPERATIONS:
        one = built[operation.operator_id]
        assert one.logical_operators == operation.logical_operators
        assert one.source_kinds == operation.source_kinds
        assert one.accepts_bound_inputs == operation.accepts_bound_inputs
        assert one.limitations == operation.limitations
        assert one.description


def test_a_bundle_crosses_the_call_in_both_directions_unchanged() -> None:
    """A fragment and a Blue operation speak the same bundle, so nothing is converted at the
    edge."""
    left = [{"city": "Austin"}]
    right = [{"city": "Austin", "state": "TX"}]
    scripted = scripted_operation("join", output=[left, right])
    one = a_suite(scripted)["blue.join"]

    result = one.call([left, right], {"join_on": [["city"], ["city"]]}, [])

    assert scripted.seen_inputs == [left, right]
    assert result.data == [left, right]


def test_the_operation_does_not_get_the_bundle_the_caller_owns() -> None:
    """An operation may write into the bundle it is handed, and the caller reuses one across
    tries."""
    inputs = [[{"city": "Austin"}]]
    scripted = scripted_operation("select")
    one = a_suite(scripted)["blue.select"]

    one.call(inputs, A_CALL_THE_MODEL_ACCEPTS["blue.select"], [])

    assert scripted.seen_inputs == inputs
    assert scripted.seen_inputs is not inputs


def test_a_parameter_the_runner_left_unset_does_not_reach_the_operation() -> None:
    """The operations branch on whether a key is present, so a null would read as a value set."""
    scripted = scripted_operation("semantic_extract")
    one = a_suite(scripted)["blue.semantic_extract"]

    one.call([[{"city": "Austin"}]], {"entities": [{"name": "state"}]}, [])

    assert scripted.seen_attributes is not None
    entity = scripted.seen_attributes["entities"][0]
    # Every optional field of the entity defaults to None, and none of them may be dumped:
    # semantic_extract refuses a present-but-empty extract_on_fields and writes its own
    # description for an entity that carries none.
    assert entity == {"name": "state"}


def test_the_suite_answers_for_every_logical_operator_but_expand_and_return() -> None:
    """A kind no operation declares is answered as no_operator and sent back to the logical
    planner."""
    router = OperatorRouter()
    for one in a_suite().values():
        router.register(one)

    unanswered = {kind for kind in LogicalOperator if not router.operators_for(kind)}

    # RETURN names the columns an answer carries rather than a step, so no operator runs one.
    # EXPAND opens one row into many, which no Blue operation does.
    assert unanswered == {LogicalOperator.EXPAND, LogicalOperator.RETURN}
