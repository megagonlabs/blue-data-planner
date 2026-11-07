"""Keep what a SQL call can still say about a failure once ops has contained its exceptions.

Ops reports fixed text for every schema, driver, and execution failure and refuses a wrapped
registry or executor, so the adapter sees only the model's reply and the target it asked for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from blue_data_ops.llm import LLMClient
from blue_data_ops.llm.errors import LLMError
from blue_data_ops.llm.sql_guard import is_read_query


def _safe_cause(error: Exception) -> str:
    # Driver/provider exceptions can include credentials, connection URLs, request bodies,
    # or SQL literals. Only known repair information is copied into the model conversation.
    if isinstance(error, json.JSONDecodeError):
        return "the model response is not valid JSON"
    if isinstance(error, LLMError):
        if (error.code or "").startswith("sql_"):
            # Ops writes these messages itself, with no SQL or driver text in them.
            return error.message
        code = error.code if error.code in {"provider_error", "unsupported_parameter"} else "error"
        return f"LLMError ({code}); check the model/provider configuration"
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
    detail: str = ""
    parameter_repairable: bool = False
    # The query the model wrote, which ops runs unchanged and does not return.
    query: str | None = None

    def __post_init__(self) -> None:
        # Ops' fixed failures cannot name the catalog target, which is what the model corrects.
        self.detail = f"reading {self.scan_source}"

    def record(self, phase: str, cause: str, *, parameter_repairable: bool = False) -> None:
        """Name the catalog target the model can correct, alongside a safe cause."""
        self.detail = f"{phase} for {self.scan_source}: {cause}"
        self.parameter_repairable = parameter_repairable

    @contextmanager
    def capture(self, phase: str) -> Generator[None, None, None]:
        """Keep the cause and repairability of what ops would otherwise contain."""
        try:
            yield
        except Exception as error:
            try:
                cause = _safe_cause(error)
                repairable = phase == "SQL generation" and isinstance(error, json.JSONDecodeError)
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
            if isinstance(query, str) and query:
                self._diagnostics.query = query
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
            # Ops' guard, and blue-data's stricter delimiter check, refuse these with a failure
            # that says nothing about repair, though a rewritten query can pass both.
            elif not is_read_query(query) or ";" in query.strip().rstrip(";").rstrip():
                self._diagnostics.record(
                    "SQL generation",
                    "the generated query must be a single read-only SELECT statement",
                    parameter_repairable=True,
                )
            return result
