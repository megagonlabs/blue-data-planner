"""Keep a SQL call's repair information before ops contains its exceptions."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from blue_data_ops.llm import LLMClient
from blue_data_ops.llm.errors import LLMError


def _parameter_repairable(phase: str, error: Exception) -> bool:
    # Ops collapses query mistakes and broken providers/sources into EXECUTION_FAILED.
    # Preserve #37's repair path only where the observed exception identifies a bad query.
    if phase == "SQL generation":
        return isinstance(error, json.JSONDecodeError)
    if phase != "SQL execution":
        return False
    if isinstance(error, sqlite3.OperationalError):
        # SQLITE_ERROR also covers unrelated runtime faults. Only known syntax/name errors
        # are evidence for parameter repair; locks, I/O, and unknown failures need fallback.
        message = str(error)
        return getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_ERROR and (
            re.fullmatch(
                r"(?:no such table|no such column|ambiguous column name): [\w. -]+", message
            )
            is not None
            or re.fullmatch(r'near ".+": syntax error', message) is not None
            or message == "incomplete input"
        )
    return isinstance(error, ValueError) and str(error) in {
        "blue.nl2sql requires a SELECT statement",
        "blue.nl2sql requires a single SQL statement",
    }


def _safe_cause(error: Exception) -> str:
    # Driver/provider exceptions can include credentials, connection URLs, request bodies,
    # or SQL literals. Only known repair information is copied into the model conversation.
    if isinstance(error, json.JSONDecodeError):
        return "the model response is not valid JSON"
    if isinstance(error, sqlite3.Error):
        message = str(error)
        if re.fullmatch(
            r"(?:no such table|no such column|ambiguous column name): [\w. -]+", message
        ) or message in {
            "database is locked",
            "database table is locked",
            "unable to open database file",
            "attempt to write a readonly database",
        }:
            return f"{type(error).__name__}: {message[:400]}"
        return f"{type(error).__name__}: check SQL syntax and the catalog's tables and columns"
    if isinstance(error, LLMError):
        code = error.code if error.code in {"provider_error", "unsupported_parameter"} else "error"
        return f"LLMError ({code}); check the model/provider configuration"
    if isinstance(error, ValueError) and str(error) in {
        "blue.nl2sql requires a SELECT statement",
        "blue.nl2sql requires a single SQL statement",
    }:
        return str(error)
    # Other SQL drivers expose structured identifiers without needing to copy their raw
    # messages. A SQLSTATE or numeric driver code helps distinguish routing and SQL failures.
    for attribute in ("sqlstate", "pgcode"):
        code = getattr(error, attribute, None)
        if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{5}", code):
            return f"{type(error).__name__} (SQLSTATE {code}); check the catalog target and SQL"
    errno = getattr(error, "errno", None)
    if isinstance(errno, int) and 1000 <= errno <= 9999:
        return f"{type(error).__name__} (driver error {errno}); check the catalog target and SQL"
    return f"{type(error).__name__}; check the registered source, database, and collection"


@dataclass
class SQLDiagnostics:
    """One invocation's diagnostic; never shared between calls, retries, or threads."""

    scan_source: str
    detail: str | None = None
    parameter_repairable: bool = False

    def record(self, phase: str, cause: str, *, parameter_repairable: bool = False) -> None:
        """Name the catalog target the model can correct, alongside a safe cause."""
        self.detail = f"{phase} for {self.scan_source}: {cause}"
        self.parameter_repairable = parameter_repairable

    @contextmanager
    def capture(self, phase: str) -> Generator[None, None, None]:
        """Observe the cause and repairability before ops contains the exception."""
        try:
            yield
        except Exception as error:
            try:
                cause = _safe_cause(error)
                repairable = _parameter_repairable(phase, error)
            except Exception:
                # A driver's unusual exception implementation must not break diagnostics.
                cause = type(error).__name__
                repairable = False
            self.record(phase, cause, parameter_repairable=repairable)
            raise


class DiagnosticSQLClient(LLMClient):
    """An ops client that retains generation diagnostics for its one SQL invocation."""

    def __init__(self, diagnostics: SQLDiagnostics) -> None:
        # Ops supplies the full merged defaults and call properties to execute_api_call.
        super().__init__(name="llm_client")
        self._diagnostics = diagnostics

    def execute_api_call(
        self,
        input: Any,
        properties: dict[str, Any] | None = None,
        additional_data: dict[str, Any] | None = None,
    ) -> Any:
        with self._diagnostics.capture("SQL generation"):
            result = super().execute_api_call(input, properties, additional_data)
            # These checks only retain a reason. The original result still reaches ops,
            # which validates it and owns the execution outcome as before.
            parsed = json.loads(result) if isinstance(result, str) else result
            query = (
                cast("dict[str, Any]", parsed).get("query") if isinstance(parsed, dict) else None
            )
            if not isinstance(query, str):
                self._diagnostics.record(
                    "SQL generation",
                    "the model must return a JSON object with a query string",
                    parameter_repairable=True,
                )
            elif not query:
                self._diagnostics.record(
                    "SQL generation", "the generated query is empty", parameter_repairable=True
                )
            elif not query.upper().startswith("SELECT"):
                self._diagnostics.record(
                    "SQL generation",
                    "the generated query must begin with SELECT",
                    parameter_repairable=True,
                )
            return result
