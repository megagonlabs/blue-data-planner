"""SQL failures must give the fragment repair what ops still reports, and the catalog target."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

import pytest
from blue_data.source.sql.sqlite.sqlite_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteDBSource,
)
from blue_data_ops.llm import LLMClient as OpsLLMClient
from blue_data_ops.llm.errors import LLMError

from blue_data_planner.data import DataCatalog, DataRegistry
from blue_data_planner.fragment_runner import FragmentRunner
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.types import (
    Conversation,
    ErrorKind,
    ExecutionStatus,
    OperatorUnavailableError,
    OutputColumn,
    PhysicalPlanFragment,
    RecoverableOperatorError,
    RunCounters,
    RunLimits,
    RunStatus,
)
from tests.doubles.llm import ScriptedLLMClient
from tests.unit.operators.adapters.blue.test_read_only_sql import PARAMETERS, nl2sql

SCAN_SOURCE = cast("str", PARAMETERS["scan_source"])


class DriverError(Exception):
    """A driver error exposing a structured identifier alongside a private raw message."""

    def __init__(self, pgcode: str | None = None, errno: int | None = None) -> None:
        super().__init__("postgres://alice:private-password@example.invalid/database")
        self.pgcode = pgcode
        self.errno = errno


@pytest.fixture
def sql_replies(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Script the provider while running the real Blue operation and SQLite source."""
    import litellm

    monkeypatch.setenv("BLUE_DATA_LLM_PROVIDER", "openai")
    monkeypatch.delenv("BLUE_DATA_LLM_ALLOWED_MODELS", raising=False)
    replies: list[Any] = []

    def complete(**kwargs: Any) -> dict[str, Any]:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(litellm, "completion", complete)
    return replies


SCHEMA_FAILURE = "generated SQL failed against the approved schema"


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("SELECT absent_column FROM stores", SCHEMA_FAILURE),
        ("SELECT * FROM absent_table", SCHEMA_FAILURE),
        ("SELECT FROM stores", SCHEMA_FAILURE),
        ("SELECT 1; CREATE TABLE injected (id INTEGER)", "single read-only SELECT"),
        # Ops' guard admits this one, and blue-data's delimiter check refuses it.
        ("SELECT ';' FROM stores", "single read-only SELECT"),
    ],
)
def test_sql_a_rewrite_can_fix_stays_repairable_and_names_the_catalog_target(
    sqlite_registry: DataRegistry, sql_replies: list[Any], query: str, reason: str
) -> None:
    """Ops withholds the driver's words, so the repair gets its fixed reason and the target."""
    sql_replies.append({"query": query})

    with pytest.raises(RecoverableOperatorError) as caught:
        nl2sql(sqlite_registry).call([], PARAMETERS, [])

    message = str(caught.value)
    assert "blue.nl2sql failed at EXECUTE" in message
    assert reason in message
    assert SCAN_SOURCE in message


def test_a_schema_lookup_failure_names_the_target_without_exposing_connection_details(
    sqlite_registry: DataRegistry, sql_replies: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A driver exception can contain credentials; an arbitrary message must not be echoed."""
    registry: Any = sqlite_registry

    def refuse_schema(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("postgres://alice:private-password@example.invalid/database")

    monkeypatch.setattr(registry, "get_data_source_schema", refuse_schema)

    with pytest.raises(OperatorUnavailableError) as caught:
        nl2sql(sqlite_registry).call([], PARAMETERS, [])

    message = str(caught.value)
    assert "read-only SQL execution failed" in message
    assert SCAN_SOURCE in message
    assert "private-password" not in message
    assert "example.invalid" not in message
    assert sql_replies == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            ConnectionError("postgres://alice:private-password@example.invalid/database"),
            "ConnectionError",
        ),
        (DriverError(pgcode="3F000"), "SQLSTATE 3F000"),
        (DriverError(errno=1049), "driver error 1049"),
    ],
)
def test_a_source_that_cannot_connect_names_the_target_without_exposing_connection_details(
    sqlite_registry: DataRegistry,
    sql_replies: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: str,
) -> None:
    """Connecting happens before ops is called, so this cause is still the adapter's to report."""
    registry: Any = sqlite_registry

    def refuse_connection(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(registry, "connect_source", refuse_connection)

    with pytest.raises(OperatorUnavailableError) as caught:
        nl2sql(sqlite_registry).call([], PARAMETERS, [])

    message = str(caught.value)
    assert "source binding" in message
    assert expected in message
    assert SCAN_SOURCE in message
    assert "private-password" not in message
    assert "example.invalid" not in message
    assert caught.value.__cause__ is None
    assert sql_replies == []


@pytest.mark.parametrize(
    "reply",
    ["not valid JSON", {}, {"query": ""}, {"query": 17}, {"query": "DROP TABLE stores"}],
    ids=["invalid-json", "missing-query", "empty-query", "non-string-query", "write-query"],
)
def test_invalid_sql_generation_has_an_actionable_diagnostic(
    sqlite_registry: DataRegistry, sql_replies: list[Any], reply: Any
) -> None:
    """Generation failures happen before execute_query, so the model client must observe them."""
    sql_replies.append(reply)

    with pytest.raises(RecoverableOperatorError) as caught:
        nl2sql(sqlite_registry).call([], PARAMETERS, [])

    message = str(caught.value)
    assert "SQL generation" in message
    assert SCAN_SOURCE in message
    assert any(word in message for word in ("JSON", "query", "SELECT"))


def test_diagnostics_are_cleared_between_calls_to_the_same_physical_operator(
    sqlite_registry: DataRegistry, sql_replies: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed query's detail must not be attached to a later failure or a successful call."""
    operator = nl2sql(sqlite_registry)
    sql_replies.extend(
        [
            {"query": "SELECT absent_column FROM stores"},
            {"query": "SELECT COUNT(*) AS n FROM stores"},
        ]
    )

    with pytest.raises(RecoverableOperatorError, match=SCHEMA_FAILURE):
        operator.call([], PARAMETERS, [])

    def refuse_model(*args: Any, **kwargs: Any) -> Any:
        raise LLMError("Bearer private-api-key", code="provider_error")

    with monkeypatch.context() as patch:
        patch.setattr(OpsLLMClient, "execute_api_call", refuse_model)
        with pytest.raises(OperatorUnavailableError) as caught:
            operator.call([], PARAMETERS, [])

    message = str(caught.value)
    assert "SQL generation" in message
    assert "provider_error" in message
    assert SCHEMA_FAILURE not in message
    assert "private-api-key" not in message
    assert operator.call([], PARAMETERS, []).data == [[{"n": 2}]]


def test_the_fragment_repair_receives_the_failure_and_can_finish(
    sqlite_registry: DataRegistry, sql_replies: list[Any]
) -> None:
    """The message must survive operator error mapping and reach the next parameter turn."""
    sql_replies.extend(
        [
            {"query": "SELECT absent_column FROM stores"},
            {"query": "SELECT COUNT(*) AS n FROM stores"},
        ]
    )
    client = ScriptedLLMClient(
        [
            PARAMETERS,
            {**PARAMETERS, "question": "Count stores using COUNT(*), without absent_column."},
            {"decision": "accept", "assessment": "There are two stores."},
        ]
    )
    router = OperatorRouter()
    router.register(nl2sql(sqlite_registry))
    runner = FragmentRunner(
        client,  # pyright: ignore[reportArgumentType]
        DataCatalog(sqlite_registry),
        router,
    )
    fragment = PhysicalPlanFragment(
        id="count-stores",
        logical_node_ids=["scan", "count"],
        root_logical_node_id="count",
        operator_id="blue.nl2sql",
        scan_sources={"scan": SCAN_SOURCE},
        instruction="Count the stores.",
        input_data=[],
        required_output_columns=[OutputColumn(name="n", dtype="int")],
    )
    counters = RunCounters(RunLimits(parameter_repairs=1, operator_calls=2))

    result = runner.run(fragment, Conversation(), [], counters)

    assert result.status is RunStatus.OK
    assert len(result.attempts) == 2
    first = result.attempts[0].result
    assert first is not None and first.status is ExecutionStatus.FAILED
    assert first.error is not None and SCHEMA_FAILURE in first.error.message
    assert any(
        SCHEMA_FAILURE in text and SCAN_SOURCE in text for _, text in client.conversations[1]
    )
    last = result.attempts[-1].result
    assert last is not None and last.output == [[{"n": 2}]]
    assert counters.operator_calls == 2


@pytest.mark.parametrize(
    ("boundary", "failure"),
    [
        ("model", LLMError("private provider detail", code="unsupported_parameter")),
        ("model", LLMError("private provider detail", code="provider_error")),
        ("schema", ConnectionError("private connection detail")),
        ("query", sqlite3.OperationalError("database is locked")),
        ("query", RuntimeError("private runtime detail")),
    ],
    ids=["model-setting", "provider", "connection", "locked-database", "unexpected-query-error"],
)
def test_non_repairable_sql_failures_skip_parameter_retries(
    sqlite_registry: DataRegistry,
    sql_replies: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    failure: Exception,
) -> None:
    """Having a SQL diagnostic must not hide a non-retryable provider or source failure."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise failure

    registry: Any = sqlite_registry
    if boundary == "model":
        monkeypatch.setattr(OpsLLMClient, "execute_api_call", refuse)
    elif boundary == "schema":
        monkeypatch.setattr(registry, "get_data_source_schema", refuse)
    else:
        sql_replies.append({"query": "SELECT COUNT(*) AS n FROM stores"})
        # Ops refuses a source whose guarded methods are patched on the instance.
        monkeypatch.setattr(SQLiteDBSource, "_db_connect", refuse)
    client = ScriptedLLMClient([PARAMETERS])
    router = OperatorRouter()
    router.register(nl2sql(sqlite_registry))
    runner = FragmentRunner(
        client,  # pyright: ignore[reportArgumentType]
        DataCatalog(sqlite_registry),
        router,
    )
    fragment = PhysicalPlanFragment(
        id="count-stores",
        logical_node_ids=["scan"],
        root_logical_node_id="scan",
        operator_id="blue.nl2sql",
        scan_sources={"scan": SCAN_SOURCE},
        instruction="Count the stores.",
        input_data=[],
        required_output_columns=[OutputColumn(name="n", dtype="int")],
    )
    counters = RunCounters(RunLimits(parameter_repairs=4, operator_calls=5))

    result = runner.run(fragment, Conversation(), [], counters)

    assert result.status is RunStatus.NEEDS_REPAIR
    assert len(result.attempts) == counters.operator_calls == 1
    error = result.attempts[0].result
    assert error is not None and error.error is not None
    assert error.error.kind is ErrorKind.OPERATOR_UNAVAILABLE
    assert SCAN_SOURCE in error.error.message
    assert "private" not in error.error.message
    assert client.answers == []
    assert sql_replies == []
