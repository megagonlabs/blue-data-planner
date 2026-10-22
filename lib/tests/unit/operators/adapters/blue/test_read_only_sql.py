"""Model-written SQL must read the source without changing its schema or rows."""

from __future__ import annotations

import json
from typing import Any

import pytest
from blue_data_ops.operators.nl2sql_operator import NL2SQLOperator

from blue_data_planner.data import DataRegistry, connect_source, execute_query
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.adapters.blue.read_only_sql import ReadOnlySQLRegistry
from blue_data_planner.types import PhysicalOperator, RecoverableOperatorError

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


def test_sql_reads_with_explicit_policy_even_if_ops_defaults_change(
    sqlite_registry: DataRegistry,
    model_reply: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt into rows and SELECT validation, and explicitly disable commit at the registry."""
    monkeypatch.setattr(
        NL2SQLOperator,
        "DEFAULT_PROPERTIES",
        {
            **NL2SQLOperator.DEFAULT_PROPERTIES,
            "execute_query": False,
            "validate_query_prefixes": ["CREATE"],
        },
    )
    registry: Any = sqlite_registry
    execute = registry.execute_query
    calls: list[dict[str, Any]] = []

    def record_query(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return execute(**kwargs)

    monkeypatch.setattr(registry, "execute_query", record_query)
    # Extra model output cannot change the adapter's execution policy.
    model_reply.update(execute_query=False, validate_query_prefixes=["CREATE"], commit=True)

    result = nl2sql(sqlite_registry).call([], PARAMETERS, [])

    assert result.data == [[{"n": 2}]]
    assert len(calls) == 1
    assert calls[0]["optional_properties"] == {"commit": False}


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
    registry: Any = sqlite_registry
    execute = registry.execute_query
    delegated_queries: list[str] = []

    def record_query(**kwargs: Any) -> Any:
        delegated_queries.append(kwargs["query"])
        return execute(**kwargs)

    monkeypatch.setattr(registry, "execute_query", record_query)

    with pytest.raises(RecoverableOperatorError, match="blue.nl2sql failed"):
        nl2sql(sqlite_registry).call([], PARAMETERS, [])

    if ";" in query:
        assert delegated_queries == [], "multi-statement SQL reached the database registry"
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


@pytest.mark.parametrize("commit", [False, True])
def test_the_source_connection_itself_is_read_only_even_if_ops_requests_commit(
    sqlite_registry: DataRegistry,
    monkeypatch: pytest.MonkeyPatch,
    commit: bool,
) -> None:
    """A SELECT prefix alone cannot stop a write: the database connection must refuse it."""
    source: Any = connect_source(sqlite_registry, "tag_bench")
    connect = source._db_connect
    read_only: list[bool] = []

    def observe_connection(database: str) -> Any:
        connection = connect(database)

        def observe_statement(statement: str) -> None:
            if statement.upper().startswith("SELECT COUNT"):
                read_only.append(bool(connection.execute("PRAGMA query_only").fetchone()[0]))

        connection.set_trace_callback(observe_statement)
        return connection

    monkeypatch.setattr(source, "_db_connect", observe_connection)

    rows = ReadOnlySQLRegistry(sqlite_registry).execute_query(
        "SELECT COUNT(*) AS n FROM stores",
        "tag_bench",
        "retail",
        "public",
        optional_properties={"commit": commit},
    )

    assert rows == [{"n": 2}]
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
