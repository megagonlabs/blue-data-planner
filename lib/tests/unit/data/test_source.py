"""What reading a registered source gives back."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from blue_data_planner.data import DataRegistry, connect_source, execute_query, execute_write
from blue_data_planner.types.errors import SourceQueryError, SourceUnreachableError


def test_a_query_returns_the_rows_the_source_holds(sqlite_registry: DataRegistry) -> None:
    """A query against a registered source comes back as one row dict per row."""
    rows = execute_query(
        sqlite_registry, "tag_bench", "SELECT city FROM stores ORDER BY city", "retail"
    )

    assert rows == [{"city": "Oakland"}, {"city": "Reno"}]


def test_a_statement_that_changes_a_source_outlives_the_connection_that_ran_it(
    sqlite_registry: DataRegistry,
) -> None:
    """A write is committed, so the next call — on its own connection — sees it."""
    execute_write(sqlite_registry, "tag_bench", "CREATE TABLE bind_values (city)", "retail")
    execute_write(sqlite_registry, "tag_bench", "INSERT INTO bind_values VALUES ('Reno')", "retail")

    rows = execute_query(
        sqlite_registry,
        "tag_bench",
        'SELECT s.city FROM stores s JOIN bind_values b ON s.city = b."city"',
        "retail",
    )

    assert rows == [{"city": "Reno"}]


def reading_a_source_by_connecting(registry: DataRegistry) -> object:
    return connect_source(registry, "absent")


def reading_a_source_by_querying(registry: DataRegistry) -> object:
    return execute_query(registry, "absent", "SELECT 1 AS one", "retail")


def reading_a_source_by_writing(registry: DataRegistry) -> object:
    return execute_write(registry, "absent", "CREATE TABLE placeholder (c)", "retail")


@pytest.mark.parametrize(
    "read_the_source",
    [reading_a_source_by_connecting, reading_a_source_by_querying, reading_a_source_by_writing],
    ids=["connect_source", "execute_query", "execute_write"],
)
def test_reading_a_source_the_registry_cannot_connect_to_raises_a_source_unreachable_error(
    sqlite_registry: DataRegistry, read_the_source: Callable[[DataRegistry], object]
) -> None:
    """A name the registry holds no connectable source for is an error, not an empty result."""
    with pytest.raises(SourceUnreachableError, match="absent"):
        read_the_source(sqlite_registry)


def a_source_returning(value: Any) -> Callable[..., Any]:
    """Stand in for a source's own execute_query, handing back one fixed value."""

    def execute_query_returning_the_value(**parameters: Any) -> Any:
        return value

    return execute_query_returning_the_value


def refusing_a_statement_by_writing(registry: DataRegistry) -> object:
    return execute_write(registry, "tag_bench", "DROP TABLE absent_table", "retail")


def refusing_a_statement_by_querying(registry: DataRegistry) -> object:
    return execute_query(registry, "tag_bench", "SELECT nope FROM stores", "retail")


@pytest.mark.parametrize(
    "refuse_the_statement, expected_message",
    [
        (refusing_a_statement_by_writing, "no such table"),
        (refusing_a_statement_by_querying, "no such column"),
    ],
    ids=["execute_write", "execute_query"],
)
def test_a_source_that_refuses_a_statement_raises_a_source_query_error(
    sqlite_registry: DataRegistry,
    refuse_the_statement: Callable[[DataRegistry], object],
    expected_message: str,
) -> None:
    """A reached source that refuses a statement carries what it said, so a repair can read it."""
    with pytest.raises(SourceQueryError, match=expected_message):
        refuse_the_statement(sqlite_registry)


@pytest.mark.parametrize(
    "malformed_result, expected_message",
    [
        ("not a table", "expected a list of rows"),
        ([{"city": "Reno"}, "not a row"], "expected a mapping"),
    ],
    ids=["not_a_list", "row_not_a_mapping"],
)
def test_a_malformed_result_raises_rather_than_being_returned_as_a_table(
    sqlite_registry: DataRegistry,
    monkeypatch: pytest.MonkeyPatch,
    malformed_result: Any,
    expected_message: str,
) -> None:
    """The Data annotation is checked, not assumed, because blue-data ships no types."""
    source: Any = connect_source(sqlite_registry, "tag_bench")
    monkeypatch.setattr(source, "execute_query", a_source_returning(malformed_result))

    with pytest.raises(ValueError, match=expected_message):
        execute_query(sqlite_registry, "tag_bench", "SELECT city FROM stores", "retail")
