"""LOTUS operations as physical operators."""

from __future__ import annotations

import sys
from typing import Any

import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed
import pytest

from blue_data_planner.execution import execute_physical_operator
from blue_data_planner.operators.adapters.lotus import lotus_operators, settings
from blue_data_planner.types import (
    DataBundle,
    ExecutionResult,
    LogicalOperator,
    OutputColumn,
    PhysicalOperator,
    RunCounters,
    RunLimits,
)

MODEL = "openai/gpt-4o"


@pytest.fixture(autouse=True)
def lotus_stub() -> Any:
    """Return the stub with its script emptied and its configuration forgotten."""
    settings._configured_model = None  # pyright: ignore[reportPrivateUsage]
    import lotus

    lotus.settings.lm = lotus.models.LM()
    lotus.reset()
    return lotus


def operator(operator_id: str, max_rows: int = 5000) -> PhysicalOperator:
    """Return one operator of the suite by id."""
    found = [one for one in lotus_operators(MODEL, max_rows) if one.operator_id == operator_id]
    assert found, f"{operator_id} is not registered"
    return found[0]


def declared(*names: str) -> list[OutputColumn]:
    """Return one declaration per name, all of them strings."""
    return [OutputColumn(name=name, dtype="str") for name in names]


def run(
    one: PhysicalOperator,
    inputs: DataBundle,
    parameters: dict[str, Any],
    columns: list[OutputColumn],
) -> ExecutionResult:
    """Run one operator the way the fragment runner does, so a refusal is a result."""
    return execute_physical_operator(
        one, parameters, inputs, columns, RunCounters(RunLimits(operator_calls=10))
    )


def test_the_suite_declares_one_logical_operator_per_operation() -> None:
    """Each operator covers one node, so the physical planner never groups a LOTUS call."""
    for one in lotus_operators(MODEL):
        assert len(one.logical_operators) == 1
        assert one.source_kinds is None
        assert one.accepts_bound_inputs is False
        assert one.description
        assert one.limitations is not None and str(5000) in one.limitations


def test_the_single_table_operations_are_registered_for_their_kinds() -> None:
    """The four row operations answer for the logical operators they implement."""
    registered = {one.operator_id: one.logical_operators for one in lotus_operators(MODEL)}

    assert registered["lotus.sem_filter"] == frozenset({LogicalOperator.FILTER})
    assert registered["lotus.sem_map"] == frozenset({LogicalOperator.PROJECT})
    assert registered["lotus.sem_extract"] == frozenset({LogicalOperator.PROJECT})
    assert registered["lotus.sem_topk"] == frozenset({LogicalOperator.SORT_SUPERLATIVE})


def test_no_operator_is_offered_where_lotus_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the package uninstalled the router gains nothing, rather than failing to import."""
    monkeypatch.setattr(settings, "lotus_is_installed", lambda: False)

    assert lotus_operators(MODEL) == []


def test_building_the_suite_imports_no_lotus(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite is described without loading a package a deployment may not have (ADR-004)."""
    monkeypatch.delitem(sys.modules, "lotus", raising=False)

    lotus_operators(MODEL)

    assert "lotus" not in sys.modules


def test_a_filter_hands_lotus_the_instruction_it_was_given(lotus_stub: Any) -> None:
    """The parameters reach the operation as the arguments the suite takes."""
    lotus_stub.frames.append(pd.DataFrame([{"city": "Reno"}]))

    result = operator("lotus.sem_filter").call(
        [[{"city": "Reno"}, {"city": "Oakland"}]], {"instruction": "Is {city} inland?"}, []
    )

    assert lotus_stub.calls[0]["operation"] == "sem_filter"
    assert lotus_stub.calls[0]["args"] == ("Is {city} inland?",)
    assert result.data == [[{"city": "Reno"}]]


def test_a_created_column_takes_its_name_from_the_nodes_declaration(lotus_stub: Any) -> None:
    """The plan's declared column is what the operation writes, not a name the model chose."""
    lotus_stub.frames.append(pd.DataFrame([{"city": "Reno", "mood": "dry"}]))

    operator("lotus.sem_map").call(
        [[{"city": "Reno"}]], {"instruction": "How does {city} feel?"}, declared("city", "mood")
    )

    assert lotus_stub.calls[0]["kwargs"]["suffix"] == "mood"


def test_an_extraction_naming_undeclared_columns_is_refused(lotus_stub: Any) -> None:
    """A parameter that does not match the declaration fails before a model is reached."""
    result = run(
        operator("lotus.sem_extract"),
        [[{"bio": "born 1815"}]],
        {"input_columns": ["bio"], "output_columns": {"born_year": "year of birth"}},
        declared("bio", "born"),
    )

    assert result.status.value == "failed"
    assert lotus_stub.calls == []


@pytest.mark.parametrize(
    "operator_id, inputs",
    [("lotus.sem_filter", []), ("lotus.sem_filter", [[{"a": 1}], [{"a": 2}]])],
    ids=["none", "two"],
)
def test_a_bundle_of_the_wrong_size_is_refused(operator_id: str, inputs: list[Any]) -> None:
    """A one-table operation refuses a bundle it cannot serve, so another operator can be tried."""
    result = run(operator(operator_id), inputs, {"instruction": "Is {a} odd?"}, [])

    assert result.status.value == "failed"


def test_a_table_over_the_cap_is_refused_before_a_model_is_reached(lotus_stub: Any) -> None:
    """The cap is what keeps a wide table from being paid for one row at a time."""
    result = run(
        operator("lotus.sem_filter", max_rows=1),
        [[{"city": "Reno"}, {"city": "Oakland"}]],
        {"instruction": "Is {city} inland?"},
        [],
    )

    assert result.status.value == "failed"
    assert lotus_stub.calls == []


def test_an_empty_input_comes_back_empty_with_no_call_made(lotus_stub: Any) -> None:
    """An empty result upstream is an answer, and a model call over no rows returns nothing."""
    result = operator("lotus.sem_filter").call([[]], {"instruction": "Is {city} inland?"}, [])

    assert result.data == [[]]
    assert lotus_stub.calls == []


@pytest.mark.parametrize(
    "raised",
    [ValueError("column city not found in DataFrame"), AssertionError("Column not found")],
    ids=["value error", "assertion"],
)
def test_a_refusal_from_lotus_comes_back_recoverable(lotus_stub: Any, raised: Exception) -> None:
    """LOTUS reports a bad column two ways, and a planner answers both the same way."""
    lotus_stub.failures.append(raised)

    result = run(
        operator("lotus.sem_filter"), [[{"city": "Reno"}]], {"instruction": "Is {city} inland?"}, []
    )

    assert result.status.value == "failed"
    assert result.error is not None and result.error.kind.value == "recoverable"


def test_an_instruction_naming_an_absent_column_never_reaches_lotus(lotus_stub: Any) -> None:
    """The check before the call is what turns a bare assert into a failure a repair can read."""
    result = run(
        operator("lotus.sem_filter"), [[{"city": "Reno"}]], {"instruction": "Is {town} inland?"}, []
    )

    assert result.status.value == "failed"
    assert lotus_stub.calls == []


def test_a_blank_cell_in_a_created_column_arrives_as_none(lotus_stub: Any) -> None:
    """A row the model had no answer for says so, rather than failing the column's type check."""
    lotus_stub.frames.append(pd.DataFrame([{"city": "Reno", "mood": "   "}]))

    result = operator("lotus.sem_map").call(
        [[{"city": "Reno"}]], {"instruction": "How does {city} feel?"}, declared("city", "mood")
    )

    assert result.data == [[{"city": "Reno", "mood": None}]]


def test_a_column_lotus_did_not_produce_is_reported_by_name(lotus_stub: Any) -> None:
    """An extraction whose answers failed to parse names the column that is missing."""
    lotus_stub.frames.append(pd.DataFrame([{"bio": "born 1815"}]))

    result = run(
        operator("lotus.sem_extract"),
        [[{"bio": "born 1815"}]],
        {"input_columns": ["bio"], "output_columns": {"born": "year of birth"}},
        declared("bio", "born"),
    )

    assert result.status.value == "failed"
    assert result.error is not None and "born" in result.error.message


def test_the_report_carries_what_this_call_spent_and_not_what_ran_before(lotus_stub: Any) -> None:
    """LOTUS counts tokens per process, so a call reports the difference it made, not the total."""
    lotus_stub.frames.append(pd.DataFrame([{"city": "Reno"}]))
    # Configure LOTUS first, so the LM object below is the one the call under test actually reads.
    operator("lotus.sem_filter").call(
        [[{"city": "Reno"}]], {"instruction": "Is {city} inland?"}, []
    )
    # A call the run already made, which the next one must not be billed for.
    spent_earlier = lotus_stub.settings.lm.stats.physical_usage
    spent_earlier.prompt_tokens, spent_earlier.total_tokens = 30, 40
    spent_earlier.total_cost = 0.02

    lotus_stub.frames.append(pd.DataFrame([{"city": "Reno"}]))
    result = run(
        operator("lotus.sem_filter"), [[{"city": "Reno"}]], {"instruction": "Is {city} inland?"}, []
    )

    # What the stub spends on one call: 7 prompt, 3 completion, 10 total, 0.001 in cost.
    assert result.report == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
        "cost": pytest.approx(0.001),
    }


def test_the_two_shaped_operations_are_registered_for_their_kinds() -> None:
    """The aggregation and the join answer for the logical operators they implement."""
    registered = {one.operator_id: one.logical_operators for one in lotus_operators(MODEL)}

    assert registered["lotus.sem_agg"] == frozenset({LogicalOperator.AGGREGATE})
    assert registered["lotus.sem_join"] == frozenset({LogicalOperator.JOIN})


def test_an_aggregation_names_its_column_against_the_group_by(lotus_stub: Any) -> None:
    """sem_agg keeps only the columns it groups by, so the rest of the declaration is its column."""
    # "note" is both what the input carries and what the node declares as output: sem_agg drops
    # the input's "note" along with every other non-grouped column and writes its summary under
    # that same declared name. Deriving the new column against the input's columns instead of
    # group_by would see "note" as already present and find nothing left to create.
    lotus_stub.frames.append(pd.DataFrame([{"city": "Reno", "note": "quiet"}]))

    operator("lotus.sem_agg").call(
        [[{"city": "Reno", "note": "loud"}]],
        {"instruction": "Summarize {note}", "group_by": ["city"]},
        declared("city", "note"),
    )

    assert lotus_stub.calls[0]["kwargs"]["suffix"] == "note"
    assert lotus_stub.calls[0]["kwargs"]["group_by"] == ["city"]


def test_an_ungrouped_aggregation_asks_lotus_for_no_grouping(lotus_stub: Any) -> None:
    """An empty group_by summarizes the whole table, which LOTUS spells as no grouping at all."""
    lotus_stub.frames.append(pd.DataFrame([{"summary": "quiet"}]))

    operator("lotus.sem_agg").call(
        [[{"note": "quiet"}]], {"instruction": "Summarize {note}"}, declared("summary")
    )

    assert lotus_stub.calls[0]["kwargs"]["group_by"] is None


def test_a_grouping_column_the_input_lacks_never_reaches_lotus(lotus_stub: Any) -> None:
    """A group_by naming an absent column is a refusal a repair can read, not a bare KeyError."""
    result = run(
        operator("lotus.sem_agg"),
        [[{"note": "quiet"}]],
        {"instruction": "Summarize {note}", "group_by": ["city"]},
        declared("city", "summary"),
    )

    assert result.status.value == "failed"
    assert result.error is not None and result.error.kind.value == "recoverable"
    assert lotus_stub.calls == []


def test_a_join_reads_both_tables_and_renames_what_they_shared(lotus_stub: Any) -> None:
    """A column both tables carried comes back in the naming a JOIN node expects."""
    lotus_stub.frames.append(
        pd.DataFrame([{"city:left": "Reno", "city:right": "Reno", "sales": 10}])
    )

    result = operator("lotus.sem_join").call(
        [[{"city": "Reno"}], [{"city": "Reno", "sales": 10}]],
        {"instruction": "Is {city:left} the same place as {city:right}?"},
        [],
    )

    assert result.data == [[{"city": "Reno", "city_right": "Reno", "sales": 10}]]


def test_a_join_is_refused_when_its_two_tables_multiply_past_the_cap(lotus_stub: Any) -> None:
    """The pairs a join compares are what it is billed for, so the cap counts the product."""
    left = [{"city": f"c{index}"} for index in range(3)]
    right = [{"town": f"t{index}"} for index in range(3)]

    result = run(
        operator("lotus.sem_join", max_rows=8),
        [left, right],
        {"instruction": "Is {city} near {town}?"},
        [],
    )

    assert result.status.value == "failed"
    assert lotus_stub.calls == []


def test_a_join_over_an_empty_side_returns_no_rows(lotus_stub: Any) -> None:
    """An inner join against nothing is empty, and no model call can change that."""
    result = operator("lotus.sem_join").call(
        [[{"city": "Reno"}], []], {"instruction": "Is {city} near {town}?"}, []
    )

    assert result.data == [[]]
    assert lotus_stub.calls == []
