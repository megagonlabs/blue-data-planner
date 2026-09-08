from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from blue_data.source.sql.sqlite.sqlite_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteDBSource,  # blue-data ships no py.typed
)

from blue_data_planner.data import DataRegistry

# The `llm` extra is intentionally absent from the development dependencies; putting the stub
# directory on the path lets the client's lazy `import litellm` resolve offline.
sys.path.insert(0, str(Path(__file__).parent / "stubs"))

# Shared fixtures for the blue-data-planner test suite belong here; stage-specific
# fixtures stay beside their tests.


@pytest.fixture
def sqlite_registry(tmp_path: Path) -> DataRegistry:
    """Register one SQLite source over a directory holding retail.db and its two entities."""
    databases_dir = tmp_path / "databases"
    databases_dir.mkdir()
    connection = sqlite3.connect(databases_dir / "retail.db")
    connection.execute("CREATE TABLE stores (store_id INTEGER, city TEXT)")
    connection.executemany("INSERT INTO stores VALUES (?, ?)", [(1, "Reno"), (2, "Oakland")])
    # A second entity whose names are not bare identifiers, which is ordinary in TAG-Bench:
    # it inherits BIRD's column names, such as "Percent (%) Eligible Free (K-12)".
    connection.execute('CREATE TABLE "store notes" ("note text" TEXT, city TEXT)')
    connection.execute("""INSERT INTO "store notes" VALUES ('quiet', 'Reno')""")
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
