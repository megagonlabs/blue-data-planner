"""What reading a registered source gives back."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

from blue_data.source.sql.sqlite.sqlite_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteDBSource,  # blue-data ships no py.typed
)

from blue_data_planner.data import DataRegistry, connect_source, execute_query


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
