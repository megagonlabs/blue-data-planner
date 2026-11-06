"""Model-written SQL must read the source without changing its schema or rows."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from blue_data.source.json.file_json_source import (  # pyright: ignore[reportMissingTypeStubs]
    FileJSONSource,
)
from blue_data.source.json.memory_json_source import (  # pyright: ignore[reportMissingTypeStubs]
    MemoryJSONSource,
)
from blue_data.source.sql.sqlite.sqlite_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteDBSource,
)
from blue_data_ops.llm.sql_execution import ReadOnlySQLExecutor
from blue_data_ops.operators import NL2SQLOutcomeOperator, OperatorRegistry

from blue_data_planner.data import DataRegistry, execute_query, open_registry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.adapters.blue.operators import OPERATIONS, blue_operators
from blue_data_planner.types import (
    OperatorUnavailableError,
    PhysicalOperator,
    RecoverableOperatorError,
)
from tests.doubles.blue_operations import scripted_operation

PARAMETERS = {
    "question": "How many stores are there?",
    "scan_source": "/source/tag_bench/database/retail/collection/public/entity/stores",
    "attr_names": ["n"],
}


@pytest.fixture
def model_reply(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Exercise the real ops client with a scripted provider reply and no network calls."""
    import litellm

    monkeypatch.setenv("BLUE_DATA_LLM_PROVIDER", "openai")
    monkeypatch.delenv("BLUE_DATA_LLM_ALLOWED_MODELS", raising=False)
    reply: dict[str, Any] = {"query": "SELECT COUNT(*) AS n FROM stores"}

    def complete(**kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": json.dumps(reply)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(litellm, "completion", complete)
    return reply


def nl2sql(registry: DataRegistry) -> PhysicalOperator:
    """Use the public suite's real, pinned NL2SQL operation."""
    return next(
        operator
        for operator in BlueSuite().operators(
            data_registry=registry,
            llm_client=LLMClient(
                name="read_only_sql", properties={"llm.model": "openai/gpt-6-luna"}
            ),
            model="openai/gpt-6-luna",
        )
        if operator.operator_id == "blue.nl2sql"
    )


def a_shop_database(directory: Path) -> None:
    """Write the issue's three orders, two of them shipped to France."""
    connection = sqlite3.connect(directory / "shop.db")
    connection.execute("CREATE TABLE orders (order_id INTEGER, ship_country TEXT)")
    connection.executemany(
        "INSERT INTO orders VALUES (?, ?)", [(1, "France"), (2, "France"), (3, "Spain")]
    )
    connection.commit()
    connection.close()


SHOP_PARAMETERS = {
    "question": "How many orders were shipped to France?",
    "scan_source": "/source/shop/database/shop/collection/public/entity/orders",
    "context": "",
    "attr_names": ["order_count"],
}
SHOP_QUERY = "SELECT COUNT(*) AS order_count FROM orders WHERE ship_country = 'France'"


def test_the_suite_returns_rows_from_a_registered_sqlite_source(
    tmp_path: Path, model_reply: dict[str, Any]
) -> None:
    """#67: ops refused every call the suite made, so the operation never returned a row."""
    a_shop_database(tmp_path)
    registry: Any = DataRegistry(name="DEMO", store=MemoryJSONSource())
    registry.register_source(
        SQLiteDBSource(
            name="shop", properties={"connection": {"database_directory": str(tmp_path)}}
        )
    )
    registry.sync_source("shop", recursive=True)
    model_reply["query"] = SHOP_QUERY

    assert nl2sql(registry).call([], SHOP_PARAMETERS, []).data == [[{"order_count": 2}]]


def test_a_reopened_registry_returns_rows_too(tmp_path: Path, model_reply: dict[str, Any]) -> None:
    """A registry read back from its store holds no live source until one is connected."""
    a_shop_database(tmp_path)
    store = tmp_path / "registry.json"
    store.write_text("{}", encoding="utf-8")
    writer: Any = DataRegistry(
        name="registry", store=FileJSONSource(properties={"connection": {"path": str(store)}})
    )
    writer.register_source(
        SQLiteDBSource(
            name="shop", properties={"connection": {"database_directory": str(tmp_path)}}
        )
    )
    writer.sync_source("shop", recursive=True)
    model_reply["query"] = SHOP_QUERY
    reopened: Any = open_registry(store)
    assert reopened.get_source("shop") is None

    assert nl2sql(reopened).call([], SHOP_PARAMETERS, []).data == [[{"order_count": 2}]]


def test_generated_sql_runs_only_through_ops_executor_on_the_called_target(
    sqlite_registry: DataRegistry,
    model_reply: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ops' executor owns the read policy, so nothing the model adds to its reply can reach it."""
    execute = ReadOnlySQLExecutor.execute
    calls: list[tuple[str, tuple[str, str, str]]] = []

    def record_query(self: ReadOnlySQLExecutor, query: str, target: Any) -> Any:
        calls.append((query, target))
        return execute(self, query, target)

    monkeypatch.setattr(ReadOnlySQLExecutor, "execute", record_query)
    # Extra model output cannot change the adapter's execution policy.
    model_reply.update(execute_query=False, validate_query_prefixes=["CREATE"], commit=True)

    result = nl2sql(sqlite_registry).call([], PARAMETERS, [])

    assert result.data == [[{"n": 2}]]
    assert calls == [("SELECT COUNT(*) AS n FROM stores", ("tag_bench", "retail", "public"))]


def test_a_source_ops_will_not_bind_leaves_the_operation_unavailable_before_any_model_call(
    sqlite_registry: DataRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ops vouches only for blue-data's own source classes, so another operator must read it."""
    import litellm

    class Subclassed(SQLiteDBSource):
        pass

    def no_model_call(**kwargs: Any) -> Any:
        raise AssertionError("the model was called for a source ops refuses")

    monkeypatch.setattr(litellm, "completion", no_model_call)
    registry: Any = sqlite_registry
    live = registry.get_source("tag_bench")
    registry.sources["tag_bench"] = Subclassed(name="tag_bench", properties=live.properties)

    with pytest.raises(OperatorUnavailableError) as caught:
        nl2sql(sqlite_registry).call([], PARAMETERS, [])

    assert "read-only SQL execution is not configured" in str(caught.value)
    assert str(PARAMETERS["scan_source"]) in str(caught.value)


def test_a_host_bound_operation_keeps_the_targets_its_host_approved(
    sqlite_registry: DataRegistry, model_reply: dict[str, Any]
) -> None:
    """Rebinding a host's operation to the called target would widen what that host approved."""
    approved = ReadOnlySQLExecutor(sqlite_registry, frozenset({("tag_bench", "retail", "other")}))
    registry = OperatorRegistry()
    for operation in OPERATIONS:
        name, _, version = operation.registry_operator_id.partition("@")
        registry.register(
            NL2SQLOutcomeOperator(read_only_executor=approved)
            if name == "nl2sql"
            else scripted_operation(name),
            version,
        )
    built = blue_operators(registry, sqlite_registry, model="openai/gpt-6-luna")
    operator = next(one for one in built if one.operator_id == "blue.nl2sql")

    with pytest.raises(OperatorUnavailableError, match="outside the configured execution scope"):
        operator.call([], PARAMETERS, [])


@pytest.mark.parametrize(
    "query",
    [
        "CREATE TABLE xr_marker (id INTEGER)",
        "SELECT 1; CREATE TABLE xr_marker (id INTEGER); COMMIT; SELECT 1 AS done",
        "SELECT 1; ALTER TABLE stores ADD COLUMN injected INTEGER; SELECT 1 AS done",
        "SELECT 1; DROP TABLE stores; SELECT 1 AS done",
        "SELECT 1; DELETE FROM stores; COMMIT; SELECT 1 AS done",
        "SELECT * INTO xr_marker FROM stores",
    ],
    ids=["create", "xr06", "alter", "drop", "delete", "select-into"],
)
def test_model_written_sql_cannot_change_the_database(
    sqlite_registry: DataRegistry,
    model_reply: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    """A Blue-suite call must report failure and leave both the schema and data intact."""
    schema_query = "SELECT name, sql FROM sqlite_master ORDER BY name"
    before = execute_query(sqlite_registry, "tag_bench", schema_query, "retail")
    model_reply.update(query=query, commit=True, validate_query_prefixes=["SELECT", "CREATE"])
    source_class: Any = SQLiteDBSource
    connect = source_class._db_connect
    opened: list[str] = []

    def record_connection(self: Any, database: str, create: bool = False) -> Any:
        opened.append(database)
        return connect(self, database, create)

    # Ops refuses a source whose guarded methods are patched on the instance.
    monkeypatch.setattr(SQLiteDBSource, "_db_connect", record_connection)

    with pytest.raises(RecoverableOperatorError, match="blue.nl2sql failed"):
        nl2sql(sqlite_registry).call([], PARAMETERS, [])

    assert opened == [], "a refused query reached the database"
    monkeypatch.undo()
    assert execute_query(sqlite_registry, "tag_bench", schema_query, "retail") == before
    assert execute_query(sqlite_registry, "tag_bench", "SELECT * FROM stores", "retail") == [
        {"store_id": 1, "city": "Reno"},
        {"store_id": 2, "city": "Oakland"},
    ]


@pytest.mark.parametrize("query", ["SELECT city FROM stores", "SELECT city FROM stores;"])
def test_single_selects_still_return_rows(
    sqlite_registry: DataRegistry, model_reply: dict[str, Any], query: str
) -> None:
    """A normal read works with or without its optional terminating semicolon."""
    model_reply["query"] = query

    result = nl2sql(sqlite_registry).call([], PARAMETERS, [])

    assert result.data == [[{"city": "Reno"}, {"city": "Oakland"}]]


def test_the_source_connection_itself_is_read_only(
    sqlite_registry: DataRegistry,
    model_reply: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SELECT prefix alone cannot stop a write: the database connection must refuse it."""
    source_class: Any = SQLiteDBSource
    connect = source_class._db_connect
    read_only: list[bool] = []

    def observe_connection(self: Any, database: str, create: bool = False) -> Any:
        connection = connect(self, database, create)

        def observe_statement(statement: str) -> None:
            if statement.upper().startswith("SELECT COUNT"):
                read_only.append(bool(connection.execute("PRAGMA query_only").fetchone()[0]))

        connection.set_trace_callback(observe_statement)
        return connection

    # Ops refuses a source whose guarded methods are patched on the instance.
    monkeypatch.setattr(SQLiteDBSource, "_db_connect", observe_connection)

    result = nl2sql(sqlite_registry).call([], PARAMETERS, [])

    assert result.data == [[{"n": 2}]]
    assert read_only == [True]


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("execute_query", False),
        ("validate_query_prefixes", ["CREATE"]),
        ("commit", True),
        ("optional_properties", {"commit": True}),
    ],
)
def test_request_parameters_cannot_change_the_sql_execution_policy(
    sqlite_registry: DataRegistry, setting: str, value: Any
) -> None:
    """The model-facing parameter schema must not expose execution-policy controls."""
    with pytest.raises(RecoverableOperatorError, match="parameters it cannot take"):
        nl2sql(sqlite_registry).call([], {**PARAMETERS, setting: value}, [])
