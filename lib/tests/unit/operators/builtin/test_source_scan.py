"""What builtin.scan reads, what it refuses, and what it leaves behind."""

from __future__ import annotations

import math
from typing import Any, cast

import pytest

from blue_data_planner.data import DataRegistry, execute_query
from blue_data_planner.operators.builtin import builtin_operators, source_scan
from blue_data_planner.types import (
    DataBundle,
    OperatorOutput,
    PhysicalOperator,
    RecoverableOperatorError,
    SourceQueryError,
    SourceUnreachableError,
    UnrecoverableOperatorError,
)

STORES = "/source/tag_bench/database/retail/collection/public/entity/stores"


def the_scan_operator(registry: DataRegistry) -> PhysicalOperator:
    """Return the one operator the built-in suite offers for SCAN."""
    operators = builtin_operators(registry)
    return next(operator for operator in operators if operator.operator_id == "builtin.scan")


def scanning(
    registry: DataRegistry, parameters: dict[str, Any], inputs: DataBundle | None = None
) -> OperatorOutput:
    """Run builtin.scan the way execution would, on the bundle a fragment would hand it."""
    return the_scan_operator(registry).call(inputs or [], parameters, [])


def test_a_scan_returns_every_row_and_every_column_the_entity_holds(
    sqlite_registry: DataRegistry,
) -> None:
    """A scan reads the entity whole: narrowing to named columns is PROJECT's job."""
    output = scanning(sqlite_registry, {"scan_source": STORES})

    assert output.data == [[{"store_id": 1, "city": "Reno"}, {"store_id": 2, "city": "Oakland"}]]


def test_the_scan_operator_declares_the_one_source_kind_it_reads_and_accepts_bound_inputs(
    sqlite_registry: DataRegistry,
) -> None:
    """The declaration is what a cover reads to decide whether this operator can take a fragment."""
    operator = the_scan_operator(sqlite_registry)

    assert operator.source_kinds == frozenset({"sql/sqlite"})
    assert operator.accepts_bound_inputs is True


@pytest.mark.parametrize(
    "scan_source",
    [
        "retail.stores",
        "/source/tag_bench/entity/stores",
        "/source//database/d/collection/c/entity/e",
    ],
    ids=["not_a_path", "missing_levels", "empty_level"],
)
def test_a_scan_source_that_is_not_a_catalog_path_refuses_the_call(
    sqlite_registry: DataRegistry, scan_source: str
) -> None:
    """A path a repair can rewrite comes back recoverable, not as a crash."""
    with pytest.raises(RecoverableOperatorError, match="scan_source"):
        scanning(sqlite_registry, {"scan_source": scan_source})


def test_an_entity_the_source_does_not_hold_fails_the_call_recoverably(
    sqlite_registry: DataRegistry,
) -> None:
    """A refused query is what the parameter repair exists to rewrite."""
    absent = "/source/tag_bench/database/retail/collection/public/entity/absent"

    with pytest.raises(RecoverableOperatorError, match="no such table"):
        scanning(sqlite_registry, {"scan_source": absent})


def test_a_source_the_registry_cannot_reach_fails_the_call_unrecoverably(
    sqlite_registry: DataRegistry,
) -> None:
    """No other operator would have reached it either, so the run stops rather than repairs."""
    unknown = "/source/absent/database/retail/collection/public/entity/stores"

    with pytest.raises(UnrecoverableOperatorError, match="absent"):
        scanning(sqlite_registry, {"scan_source": unknown})


def test_a_name_that_is_not_a_bare_identifier_reaches_the_source_intact(
    sqlite_registry: DataRegistry,
) -> None:
    """An entity or column name holding a space is quoted into the query, never refused."""
    notes = "/source/tag_bench/database/retail/collection/public/entity/store notes"

    output = scanning(sqlite_registry, {"scan_source": notes})

    assert output.data == [[{"note text": "quiet", "city": "Reno"}]]


def test_the_report_names_the_query_that_ran(sqlite_registry: DataRegistry) -> None:
    """A finished run is readable, so the SQL the operator wrote travels with the result."""
    output = scanning(sqlite_registry, {"scan_source": STORES})

    assert output.report is not None
    assert output.report["query"] == 'SELECT * FROM "stores"'
    assert output.report["bind_join"] is None
    assert output.report["bound_value_count"] == 0


def bound_to_city(*cities: str) -> tuple[DataBundle, dict[str, Any]]:
    """A one-table reference input of cities, and the parameters that bind a scan to them."""
    inputs: DataBundle = [[{"city": city} for city in cities]]
    parameters = {
        "scan_source": STORES,
        "bound_values": {"input_column": "city", "attribute": "city"},
    }
    return inputs, parameters


def many_cities(count: int) -> list[str]:
    """`count` distinct city names, the last of which the retail database holds."""
    return [*(f"City {index}" for index in range(count - 1)), "Reno"]


def tables_in_the_retail_database(registry: DataRegistry) -> set[str]:
    """Every table name the retail database holds, which is how a leftover table is spotted."""
    rows = execute_query(
        registry,
        "tag_bench",
        "SELECT name FROM sqlite_master WHERE type = 'table'",
        "retail",
    )
    return {str(row["name"]) for row in rows}


@pytest.mark.parametrize(
    "cities, expected_realization",
    [
        (["Reno", "Fresno"], "in_list"),
        # One more than the cap, which is the smallest input that cannot be embedded.
        (many_cities(source_scan.MAX_EMBEDDED_VALUES + 1), "temporary_table"),
    ],
    ids=["in_list", "temporary_table"],
)
def test_bound_values_narrow_the_read_to_the_rows_that_match_them(
    sqlite_registry: DataRegistry, cities: list[str], expected_realization: str
) -> None:
    """However many values travel, the source does the narrowing and the report says how."""
    inputs, parameters = bound_to_city(*cities)

    output = scanning(sqlite_registry, parameters, inputs)

    assert output.data == [[{"store_id": 1, "city": "Reno"}]]
    assert output.report is not None
    assert output.report["bind_join"] == expected_realization
    # Every city in the fixture is distinct, so the count is exactly how many travelled.
    assert output.report["bound_value_count"] == len(cities)


def test_a_repeated_value_travels_once_and_a_null_value_not_at_all(
    sqlite_registry: DataRegistry,
) -> None:
    """A null key matches nothing and a repeat asks for nothing new, so neither travels."""
    inputs: DataBundle = [[{"city": "Reno"}, {"city": "Reno"}, {"city": None}]]
    _, parameters = bound_to_city()

    output = scanning(sqlite_registry, parameters, inputs)

    assert output.data == [[{"store_id": 1, "city": "Reno"}]]
    assert output.report is not None
    assert output.report["bound_value_count"] == 1


def test_a_reference_input_carrying_no_usable_value_returns_no_rows(
    sqlite_registry: DataRegistry,
) -> None:
    """No key can match, so an empty result is the answer rather than a swallowed failure."""
    inputs, parameters = bound_to_city()

    output = scanning(sqlite_registry, parameters, inputs)

    assert output.data == [[]]
    assert output.report is not None
    assert output.report["query"] is None


def test_a_value_holding_a_quote_reaches_the_source_intact(
    sqlite_registry: DataRegistry,
) -> None:
    """A value is escaped into the query text rather than breaking out of it."""
    inputs, parameters = bound_to_city("O'Reno")

    output = scanning(sqlite_registry, parameters, inputs)

    assert output.data == [[]]
    assert output.report is not None
    assert output.report["query"] == "SELECT * FROM \"stores\" WHERE \"city\" IN ('O''Reno')"


def bound_to_store_id(value: Any) -> tuple[DataBundle, dict[str, Any]]:
    """A one-table reference input binding on `store_id`, for values that are not strings."""
    inputs: DataBundle = [[{"store_id": value}]]
    parameters = {
        "scan_source": STORES,
        "bound_values": {"input_column": "store_id", "attribute": "store_id"},
    }
    return inputs, parameters


@pytest.mark.parametrize(
    "value, literal",
    [(1, "1"), (True, "1"), (False, "0"), (2.5, "2.5")],
    ids=["int", "true", "false", "float"],
)
def test_a_numeric_value_renders_unquoted_into_the_query(
    sqlite_registry: DataRegistry, value: int | float | bool, literal: str
) -> None:
    """An int, a bool, and a float all render bare, unlike a string value."""
    inputs, parameters = bound_to_store_id(value)

    output = scanning(sqlite_registry, parameters, inputs)

    assert output.report is not None
    assert output.report["query"] == f'SELECT * FROM "stores" WHERE "store_id" IN ({literal})'


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "neg_inf"])
def test_a_non_finite_float_value_refuses_the_call(
    sqlite_registry: DataRegistry, value: float
) -> None:
    """A NaN or an infinite value is not something a source can compare a column against."""
    inputs, parameters = bound_to_store_id(value)

    with pytest.raises(RecoverableOperatorError, match="not a value a source can compare"):
        scanning(sqlite_registry, parameters, inputs)


@pytest.mark.parametrize(
    "inputs, bound_values",
    [
        ([[{"city": "Reno"}]], None),
        ([], {"input_column": "city", "attribute": "city"}),
        (
            [[{"city": "Reno"}], [{"city": "Oakland"}]],
            {"input_column": "city", "attribute": "city"},
        ),
    ],
    ids=["input_without_binding", "binding_without_input", "two_inputs"],
)
def test_an_input_and_a_binding_that_do_not_agree_refuse_the_call(
    sqlite_registry: DataRegistry, inputs: DataBundle, bound_values: dict[str, Any] | None
) -> None:
    """The operator binds exactly one reference input, and says so rather than guessing."""
    parameters: dict[str, Any] = {"scan_source": STORES, "bound_values": bound_values}

    with pytest.raises(RecoverableOperatorError, match="builtin.scan"):
        scanning(sqlite_registry, parameters, inputs)


def test_a_reference_input_missing_the_column_it_is_bound_on_refuses_the_call(
    sqlite_registry: DataRegistry,
) -> None:
    """A required column that is not there is a refusal, never a row silently skipped."""
    inputs: DataBundle = [[{"town": "Reno"}]]
    _, parameters = bound_to_city()

    with pytest.raises(RecoverableOperatorError, match="city"):
        scanning(sqlite_registry, parameters, inputs)


def test_a_value_the_operator_cannot_compare_against_a_column_refuses_the_call(
    sqlite_registry: DataRegistry,
) -> None:
    """A list is not something a source can match a column against, so it is refused."""
    inputs: DataBundle = [[{"city": ["Reno"]}]]
    _, parameters = bound_to_city()

    with pytest.raises(RecoverableOperatorError, match="list"):
        scanning(sqlite_registry, parameters, inputs)


@pytest.mark.parametrize(
    "scan_source, expected_error",
    [
        (STORES, None),
        (
            "/source/tag_bench/database/retail/collection/public/entity/absent",
            RecoverableOperatorError,
        ),
    ],
    ids=["read_succeeds", "read_fails"],
)
def test_the_table_a_bind_join_wrote_is_gone_once_the_call_returns(
    sqlite_registry: DataRegistry,
    scan_source: str,
    expected_error: type[Exception] | None,
) -> None:
    """A request leaves nothing behind in a source a caller owns, failing path included."""
    inputs: DataBundle = [
        [{"city": city} for city in many_cities(source_scan.MAX_EMBEDDED_VALUES + 1)]
    ]
    parameters = {
        "scan_source": scan_source,
        "bound_values": {"input_column": "city", "attribute": "city"},
    }

    if expected_error is None:
        scanning(sqlite_registry, parameters, inputs)
    else:
        with pytest.raises(expected_error):
            scanning(sqlite_registry, parameters, inputs)

    assert tables_in_the_retail_database(sqlite_registry) == {"stores", "store notes"}


def bound_join_that_reads_through_a_table() -> tuple[DataBundle, dict[str, Any]]:
    """Inputs and parameters guaranteed to bind through a temporary table, not an IN list."""
    inputs: DataBundle = [
        [{"city": city} for city in many_cities(source_scan.MAX_EMBEDDED_VALUES + 1)]
    ]
    parameters = {
        "scan_source": STORES,
        "bound_values": {"input_column": "city", "attribute": "city"},
    }
    return inputs, parameters


def failing_the_drop_with(monkeypatch: pytest.MonkeyPatch, drop_error: Exception) -> None:
    """Make the DROP statement `_read_through_a_table` issues fail, every other statement not."""
    real_execute_write = source_scan.execute_write

    def execute_write_unless_dropping(
        registry: DataRegistry,
        source: str,
        statement: str,
        database: str,
        collection: str | None = None,
    ) -> None:
        if statement.startswith("DROP TABLE"):
            raise drop_error
        real_execute_write(registry, source, statement, database, collection)

    monkeypatch.setattr(source_scan, "execute_write", execute_write_unless_dropping)


def test_a_drop_that_fails_fails_the_call_unrecoverably(
    sqlite_registry: DataRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repair would write a second orphan table rather than remove the first (ADR-023)."""
    failing_the_drop_with(monkeypatch, SourceQueryError("simulated drop failure"))
    inputs, parameters = bound_join_that_reads_through_a_table()

    with pytest.raises(UnrecoverableOperatorError, match="left the table") as excinfo:
        scanning(sqlite_registry, parameters, inputs)

    # The failed DROP is the operator's most reportable failure, so it carries a report too.
    assert excinfo.value.report is not None
    assert excinfo.value.report["source"] == "tag_bench"
    assert cast(str, excinfo.value.report["table"]).startswith("blue_bind_")


def test_a_drop_that_fails_because_the_source_is_unreachable_still_names_the_left_over_table(
    sqlite_registry: DataRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source going unreachable during the DROP must not lose the orphan-table diagnostic."""
    failing_the_drop_with(monkeypatch, SourceUnreachableError("simulated source outage"))
    inputs, parameters = bound_join_that_reads_through_a_table()

    with pytest.raises(UnrecoverableOperatorError, match="left the table"):
        scanning(sqlite_registry, parameters, inputs)
