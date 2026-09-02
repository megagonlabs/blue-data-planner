"""In-memory double for the boundary in `data/`.

`DataRegistryClientDouble` implements the client's surface and records every call so a test
can assert its boundary interactions offline.
"""

from __future__ import annotations

from typing import Any


class DataRegistryClientDouble:
    """Call-recording double for the DataRegistryClient surface."""

    def __init__(self, source_properties: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # A registry reaches this package already populated, so a test seeds this directly.
        self.sources: dict[str, Any] = {}
        self._source_properties = source_properties or {}
        # Configurable returns for the query/schema surface (set by tests as needed).
        self.query_result: Any = None
        self.schema_result: Any = {"entities": [], "relations": []}
        # Per-page search results (consumer shape); missing pages return [].
        self.search_pages: dict[int, list[dict[str, Any]]] = {}

    def _log(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    def calls_to(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]

    def get_source_properties(self, source: str) -> dict[str, Any]:
        self._log("get_source_properties", source=source)
        return self._source_properties

    def connect_source(self, source: str) -> Any:
        self._log("connect_source", source=source)
        return self.sources.get(source)

    def execute_query(
        self,
        query: Any,
        source: str,
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict[str, Any] | None = None,
    ) -> Any:
        self._log(
            "execute_query",
            query=query,
            source=source,
            database=database,
            collection=collection,
            optional_properties=optional_properties,
        )
        return self.query_result

    def get_data_source_schema(
        self,
        source: str,
        database: str,
        collection: str,
        format: str = "dict",  # noqa: A002 — mirrors the client surface
    ) -> Any:
        self._log(
            "get_data_source_schema",
            source=source,
            database=database,
            collection=collection,
            format=format,
        )
        return self.schema_result

    def search_records(
        self,
        input_query: Any,
        type: str | None = None,
        scope: str | None = None,
        page: int = 0,
        page_size: int = 5,
    ) -> list[dict[str, Any]]:
        self._log(
            "search_records",
            input_query=input_query,
            type=type,
            scope=scope,
            page=page,
            page_size=page_size,
        )
        return self.search_pages.get(page, [])
