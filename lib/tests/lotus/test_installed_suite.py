"""The adapter against an installed LOTUS."""

from __future__ import annotations

import os
from typing import Any

from blue_data_planner.execution import execute_physical_operator
from blue_data_planner.operators.adapters.lotus import lotus_operators
from blue_data_planner.types import (
    DataBundle,
    ExecutionResult,
    OutputColumn,
    PhysicalOperator,
    RunCounters,
    RunLimits,
)

MODEL = os.environ.get("BLUE_DATA_PLANNER_LOTUS_MODEL", "openai/gpt-4o-mini")

CITIES = [
    {"city": "Reno", "note": "high desert, dry all year"},
    {"city": "Honolulu", "note": "island city on the Pacific"},
]


def operator(operator_id: str) -> PhysicalOperator:
    """Return one operator of the suite by id."""
    found = [one for one in lotus_operators(MODEL) if one.operator_id == operator_id]
    assert found, f"{operator_id} is not registered"
    return found[0]


def run(
    one: PhysicalOperator,
    inputs: DataBundle,
    parameters: dict[str, Any],
    columns: list[OutputColumn],
) -> ExecutionResult:
    """Run one operator the way the fragment runner does."""
    return execute_physical_operator(
        one, parameters, inputs, columns, RunCounters(RunLimits(operator_calls=10))
    )


def test_an_instruction_naming_an_absent_column_is_refused_by_the_installed_suite() -> None:
    """The check before the call agrees with what LOTUS itself would refuse, and costs nothing."""
    result = run(operator("lotus.sem_filter"), [CITIES], {"instruction": "Is {town} coastal?"}, [])

    assert result.status.value == "failed"


def test_a_filter_runs_against_the_installed_suite(provider: None) -> None:
    """The operation LOTUS registers takes the arguments the adapter hands it, and returns rows."""
    result = run(
        operator("lotus.sem_filter"),
        [CITIES],
        {"instruction": "Is {city} on the coast?"},
        [],
    )

    assert result.status.value == "ok"
    assert result.output is not None
    assert [row["city"] for row in result.output[0]] == ["Honolulu"]


def test_an_extraction_writes_the_column_the_node_declared(provider: None) -> None:
    """A created column reaches the next node under the name the plan declared for it."""
    result = run(
        operator("lotus.sem_extract"),
        [CITIES],
        {"input_columns": ["note"], "output_columns": {"climate": "the climate in one word"}},
        [OutputColumn(name="city", dtype="str"), OutputColumn(name="climate", dtype="str")],
    )

    assert result.status.value == "ok"
    assert result.output is not None
    assert all("climate" in row for row in result.output[0])


def test_a_map_writes_the_column_the_node_declared(provider: None) -> None:
    """LOTUS's suffix parameter names the whole column, so the declared name is what comes back."""
    result = run(
        operator("lotus.sem_map"),
        [CITIES],
        {"instruction": "In one word, the climate of {city}"},
        [OutputColumn(name="city", dtype="str"), OutputColumn(name="climate", dtype="str")],
    )

    assert result.status.value == "ok"
    assert result.output is not None
    assert all("climate" in row for row in result.output[0])


def test_an_aggregation_summarizes_each_group(provider: None) -> None:
    """A grouped summary comes back as one row per group, carrying the group and the summary."""
    result = run(
        operator("lotus.sem_agg"),
        [CITIES],
        {"instruction": "Summarize {note}", "group_by": ["city"]},
        [OutputColumn(name="city", dtype="str"), OutputColumn(name="summary", dtype="str")],
    )

    assert result.status.value == "ok"
    assert result.output is not None
    assert len(result.output[0]) == len(CITIES)


def test_a_top_k_keeps_the_rows_it_ranks_highest(provider: None) -> None:
    """The K the parameters ask for is how many rows the operation returns."""
    result = run(
        operator("lotus.sem_topk"),
        [CITIES],
        {"instruction": "Which {city} is warmer?", "k": 1},
        [],
    )

    assert result.status.value == "ok"
    assert result.output is not None
    assert len(result.output[0]) == 1


def test_a_join_matches_the_two_tables_and_renames_what_they_shared(provider: None) -> None:
    """A column both tables carried arrives under the naming a JOIN node expects."""
    airports = [{"city": "Honolulu", "code": "HNL"}, {"city": "Reno", "code": "RNO"}]

    result = run(
        operator("lotus.sem_join"),
        [CITIES, airports],
        {"instruction": "Is {city:left} the same place as {city:right}?"},
        [],
    )

    assert result.status.value == "ok"
    assert result.output is not None
    assert result.output[0]
    assert "city_right" in result.output[0][0]
