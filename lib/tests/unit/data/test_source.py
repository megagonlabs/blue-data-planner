"""What reading a registered source gives back."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from blue_data.source.sql.sqlite.sqlite_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteDBSource,  # blue-data ships no py.typed
)

from blue_data_planner.data import DataRegistry, connect_source, execute_query
from blue_data_planner.types.errors import SourceQueryError, SourceUnreachableError


def a_registry_holding_a_retail_database(tmp_path: Path) -> DataRegistry:
    """Register one SQLite source over a directory holding retail.db and its two stores."""
    databases_dir = tmp_path / "databases"
    databases_dir.mkdir()
    connection = sqlite3.connect(databases_dir / "retail.db")
    connection.execute("CREATE TABLE stores (store_id INTEGER, city TEXT)")
    connection.executemany("INSERT INTO stores VALUES (?, ?)", [(1, "Reno"), (2, "Oakland")])
    connection.commit()
    connection.close()

    registry: Any = DataRegistry(name="test", rebuild=True)
    registry.register_source(
        SQLiteDBSource(
            name="tag_bench",
            properties={
                "connection": {
                    "protocol": "sql/sqlite",
                    "database_directory": str(databases_dir),
                }
            },
        )
    )
    return cast("DataRegistry", registry)


def test_a_query_returns_the_rows_the_source_holds(tmp_path: Path) -> None:
    """A query against a registered source comes back as one row dict per row."""
    registry = a_registry_holding_a_retail_database(tmp_path)

    rows = execute_query(registry, "tag_bench", "SELECT city FROM stores ORDER BY city", "retail")

    assert rows == [{"city": "Oakland"}, {"city": "Reno"}]


def test_a_connected_source_accepts_a_table_written_through_it(tmp_path: Path) -> None:
    """The object connect_source returns can be written through, which is what a bind join needs."""
    registry = a_registry_holding_a_retail_database(tmp_path)

    source: Any = connect_source(registry, "tag_bench")
    # blue-data's SQL sources persist a write only when told to commit.
    source.execute_query(
        "CREATE TABLE bind_values (city TEXT)",
        database="retail",
        optional_properties={"commit": True},
    )
    source.execute_query(
        "INSERT INTO bind_values VALUES ('Reno')",
        database="retail",
        optional_properties={"commit": True},
    )

    rows = execute_query(
        registry,
        "tag_bench",
        "SELECT s.city FROM stores s JOIN bind_values b ON s.city = b.city",
        "retail",
    )

    assert rows == [{"city": "Reno"}]


def reading_a_source_by_connecting(registry: DataRegistry) -> object:
    return connect_source(registry, "absent")


def reading_a_source_by_querying(registry: DataRegistry) -> object:
    return execute_query(registry, "absent", "SELECT 1 AS one", "retail")


@pytest.mark.parametrize(
    "read_the_source",
    [reading_a_source_by_connecting, reading_a_source_by_querying],
    ids=["connect_source", "execute_query"],
)
def test_reading_a_source_the_registry_cannot_connect_to_raises_a_source_unreachable_error(
    tmp_path: Path, read_the_source: Callable[[DataRegistry], object]
) -> None:
    """A name the registry holds no connectable source for is an error, not an empty result."""
    registry = a_registry_holding_a_retail_database(tmp_path)

    with pytest.raises(SourceUnreachableError, match="absent"):
        read_the_source(registry)


def a_source_returning(value: Any) -> Callable[..., Any]:
    """Stand in for a source's own execute_query, handing back one fixed value."""

    def execute_query_returning_the_value(**parameters: Any) -> Any:
        return value

    return execute_query_returning_the_value


def test_a_query_naming_a_column_the_source_does_not_have_raises_a_source_query_error(
    tmp_path: Path,
) -> None:
    """A query the source refuses carries what the source said, so a repair can read it."""
    registry = a_registry_holding_a_retail_database(tmp_path)

    with pytest.raises(SourceQueryError, match="no such column"):
        execute_query(registry, "tag_bench", "SELECT nope FROM stores", "retail")


@pytest.mark.parametrize(
    "malformed_result, expected_message",
    [
        ("not a table", "expected a list of rows"),
        ([{"city": "Reno"}, "not a row"], "expected a mapping"),
    ],
    ids=["not_a_list", "row_not_a_mapping"],
)
def test_a_malformed_result_raises_rather_than_being_returned_as_a_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_result: Any,
    expected_message: str,
) -> None:
    """The Data annotation is checked, not assumed, because blue-data ships no types."""
    registry = a_registry_holding_a_retail_database(tmp_path)
    source: Any = connect_source(registry, "tag_bench")
    monkeypatch.setattr(source, "execute_query", a_source_returning(malformed_result))

    with pytest.raises(ValueError, match=expected_message):
        execute_query(registry, "tag_bench", "SELECT city FROM stores", "retail")
