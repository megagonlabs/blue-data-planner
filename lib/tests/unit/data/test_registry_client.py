"""Integration contract for DataRegistryClient against blue-data.

Covers the two reads a caller makes of an already-populated registry, the live source
`connect_source` hands back, the query and schema argument translations, and the delegate
signatures those translations depend on.
"""

# blue-data ships no py.typed:
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast

import pytest

blue_data = pytest.importorskip("blue_data")

from blue_data.registry.data_registry import DataRegistry  # noqa: E402
from blue_data.source.sql.sqlite.sqlite_sql_source import SQLiteDBSource  # noqa: E402

from blue_data_planner.data import DataRegistryClient  # noqa: E402


def _memory_sql_stub(name: str) -> Any:
    # A stateful stub source: register_source syncs the registry against fetch_databases(),
    # so the source must answer the whole fetch surface.
    class _Stub:
        def __init__(self, stub_name: str) -> None:
            self.name = stub_name
            self.properties: dict[str, Any] = {"connection": {"protocol": "memory"}}
            self.databases: list[str] = []

        def create_database(self, database: str, **_kwargs: Any) -> dict[str, str]:
            self.databases.append(database)
            return {"status": "success"}

        def fetch_databases(self) -> list[str]:
            return list(self.databases)

        def fetch_metadata(self) -> dict[str, Any]:
            return {}

        def fetch_database_metadata(self, database: str) -> dict[str, Any]:
            return {}

        def fetch_database_collections(self, database: str) -> list[str]:
            return []

    return _Stub(name)


def _a_registry_holding(source: Any) -> DataRegistryClient:
    """A populated registry, wrapped — the state every caller of this client starts from."""
    registry = DataRegistry(name="populated", rebuild=True)
    registry.register_source(source)
    return DataRegistryClient(registry)


# --- Reading an already-populated registry ------------------------------------------ #
def test_a_source_registered_before_wrapping_is_readable_through_the_client() -> None:
    client = _a_registry_holding(_memory_sql_stub("src"))
    assert client.get_source_properties("src")["connection"]["protocol"] == "memory"


def test_connect_source_returns_the_live_source_a_caller_writes_through(tmp_path: Path) -> None:
    source = SQLiteDBSource(
        "sqlite_src",
        properties={"connection": {"protocol": "sqlite", "database_directory": str(tmp_path)}},
    )
    client = _a_registry_holding(source)

    connected = client.connect_source("sqlite_src")
    assert connected is source  # registered sources are returned directly

    db_connection = connected._db_connect("smoke")
    cursor = db_connection.cursor()
    cursor.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    cursor.executemany("INSERT INTO t VALUES (?, ?)", [(1, "x"), (2, "y")])
    db_connection.commit()
    connected._db_disconnect(db_connection)
    assert (tmp_path / "smoke.db").exists()


# --- Query and schema argument translation ------------------------------------------ #
class _Recorder:
    """A delegate that records the keywords it was called with and returns a fixed value."""

    def __init__(self, result: Any) -> None:
        self.kwargs: dict[str, Any] = {}
        self._result = result

    def execute_query(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self._result

    def get_data_source_schema(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self._result


def test_execute_query_delegates_the_swapped_source_and_query_order() -> None:
    # Distinct values make an argument-order regression visible: the client receives
    # (query, source) and delegates blue-data's (source=..., query=...) keywords.
    recorder = _Recorder("rows")
    client = DataRegistryClient(cast(Any, recorder))

    out = client.execute_query("SELECT 1", "my_source", database="db", collection="c")

    assert out == "rows"
    assert recorder.kwargs == {
        "source": "my_source",
        "query": "SELECT 1",
        "database": "db",
        "collection": "c",
        "optional_properties": None,
    }


@pytest.mark.parametrize("optional_properties", [{}, None])
def test_execute_query_passes_optional_properties_through_unchanged(
    optional_properties: dict[str, Any] | None,
) -> None:
    # blue-data normalizes optional_properties with `x or {}`, so both {} and None are
    # safe pass-through values.
    recorder = _Recorder([])
    client = DataRegistryClient(cast(Any, recorder))

    client.execute_query("q", "s", optional_properties=optional_properties)

    assert recorder.kwargs["optional_properties"] == optional_properties


def test_get_data_source_schema_delegates_every_argument_by_keyword() -> None:
    recorder = _Recorder({"entities": [], "relations": []})
    client = DataRegistryClient(cast(Any, recorder))

    client.get_data_source_schema("my_source", "db", "c", format="dict")

    assert recorder.kwargs == {
        "source": "my_source",
        "database": "db",
        "collection": "c",
        "format": "dict",
    }


# --- Schema shapes a caller has to handle ------------------------------------------- #
def test_an_unknown_source_has_an_empty_schema() -> None:
    client = DataRegistryClient(DataRegistry(name="schema_probe", rebuild=True))
    assert client.get_data_source_schema("absent", "db", "c") == {"entities": [], "relations": []}


def test_a_bn_source_reports_graph_structure_at_the_top_level() -> None:
    # Not under "metadata", which is the shape the other protocols report.
    bn_source = _memory_sql_stub("bn_src")
    bn_source.properties = {"connection": {"protocol": "bn"}}
    client = _a_registry_holding(bn_source)

    schema = client.get_data_source_schema("bn_src", "db", "c")

    assert "graph_structure" in schema
    assert "metadata" not in schema


# --- The delegate signatures these translations depend on ---------------------------- #
# Full parameter lists, so an upstream reorder fails as loudly as an upstream rename.
EXPECTED_DELEGATE_PARAMS = {
    "connect_source": ["source"],
    "execute_query": ["source", "query", "database", "collection", "optional_properties"],
    "get_data_source_schema": ["source", "database", "collection", "format"],
    "get_source_properties": ["source"],
}


@pytest.mark.parametrize("method_name", sorted(EXPECTED_DELEGATE_PARAMS))
def test_blue_data_still_takes_the_arguments_this_client_forwards(method_name: str) -> None:
    method: Any = getattr(DataRegistry, method_name)
    parameters = [p for p in inspect.signature(method).parameters if p != "self"]
    assert parameters == EXPECTED_DELEGATE_PARAMS[method_name]
