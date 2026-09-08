"""What builtin.scan reads, what it refuses, and what it leaves behind."""

from __future__ import annotations

from typing import Any, cast

import pytest

from blue_data_planner.data import DataRegistry
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.types import (
    DataBundle,
    OperatorOutput,
    PhysicalOperator,
    RecoverableOperatorError,
    UnrecoverableOperatorError,
)

STORES = "/source/tag_bench/database/retail/collection/public/entity/stores"


def the_scan_operator(registry: DataRegistry) -> PhysicalOperator:
    """Return the one operator the built-in suite offers for SCAN."""
    operators = builtin_operators(registry, cast(Any, None))
    return next(operator for operator in operators if operator.operator_id == "builtin.scan")


def scanning(
    registry: DataRegistry, parameters: dict[str, Any], inputs: DataBundle | None = None
) -> OperatorOutput:
    """Run builtin.scan the way execution would, on the bundle a fragment would hand it."""
    return the_scan_operator(registry).call(inputs or [], parameters)


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
