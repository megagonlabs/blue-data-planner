"""The three readings each planning component takes off the data catalog."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.types.catalog import (
    CatalogMatch,
    CatalogPreview,
    ColumnRef,
    ColumnView,
    SourceRecord,
)

# blue-data's search defaults filter by score, and with no vector index every vector score
# is zero, so a search over a populated registry returns almost nothing. The readings rank
# and cap instead (ADR-021).
_NO_THRESHOLDS: dict[str, float] = {
    "bm25_threshold": 0.0,
    "vector_threshold": 0.0,
    "value_threshold": 0.0,
    "combined_threshold": 0.0,
}

# blue-data scores at most page_limit * page_size candidates, truncating the rest before
# ranking (its default page_limit is 10).
_SEARCH_PAGE_LIMIT = 10_000


def _properties(record: dict[str, Any]) -> dict[str, Any]:
    properties = record.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(
            f"Record {record.get('type')}/{record.get('name')}: "
            f"properties is {type(properties).__name__}, expected a mapping"
        )
    return cast("dict[str, Any]", properties)


def _description(record: dict[str, Any]) -> str | None:
    description = record.get("description")
    if description is None or description == "":
        return None
    if not isinstance(description, str):
        raise ValueError(
            f"Record {record.get('type')}/{record.get('name')}: "
            f"description is {type(description).__name__}, expected a string"
        )
    return description


def _attribute_scope(ref: ColumnRef) -> str:
    return (
        f"/source/{ref.source}/database/{ref.database}"
        f"/collection/{ref.collection}/entity/{ref.entity}"
    )


class DataCatalog:
    """Reads the data catalog for the three planning components."""

    def __init__(self, registry: DataRegistry, max_search_results: int = 10) -> None:
        # The one untyped-library boundary (blue-data has no py.typed): delegate through
        # Any so unknown member types stop here, not in every caller.
        self._registry: Any = registry
        self._max_search_results = max_search_results

    def preview(self, request: str, limit: int) -> CatalogPreview:
        """Return the registered sources, ranked against the request and capped."""
        sources = [
            self._source_record(raw) for raw in self._search(request, limit, record_type="source")
        ]
        return CatalogPreview(
            sources=sources,
            total_sources=len(self._registry.list_records(record_type="source")),
        )

    def search_records(self, term: str) -> list[CatalogMatch]:
        """Return the catalog records one term found, ranked and capped."""
        return [self._match(raw, term) for raw in self._search(term, self._max_search_results)]

    def column_views(self, columns: Sequence[ColumnRef]) -> list[ColumnView]:
        """Return one view per column that resolves to a catalog record."""
        views: list[ColumnView] = []
        for ref in columns:
            record = self._registry.get_record(ref.attribute, "attribute", _attribute_scope(ref))
            if record is None:
                continue
            views.append(self._view(ref, cast("dict[str, Any]", record)))
        return views

    def _view(self, ref: ColumnRef, record: dict[str, Any]) -> ColumnView:
        path = f"{_attribute_scope(ref)}/attribute/{ref.attribute}"
        properties = _properties(record)
        info = properties.get("info")
        if not isinstance(info, dict):
            raise ValueError(f"Attribute record {path}: no properties.info.type")
        info = cast("dict[str, Any]", info)
        if not info.get("type"):
            raise ValueError(f"Attribute record {path}: no properties.info.type")
        stats = properties.get("stats", {})
        if not isinstance(stats, dict):
            raise ValueError(
                f"Attribute record {path}: stats is {type(stats).__name__}, expected a mapping"
            )
        stats = cast("dict[str, Any]", stats)
        semantics = properties.get("value_semantics", {})
        if not isinstance(semantics, dict):
            raise ValueError(
                f"Attribute record {path}: value_semantics is "
                f"{type(semantics).__name__}, expected a mapping"
            )
        semantics = cast("dict[str, Any]", semantics)
        return ColumnView(
            ref=ref,
            dtype=str(info["type"]),
            description=_description(record),
            sample_values=list(cast("list[Any]", stats.get("sample_values", []))),
            distinct_count=stats.get("distinct_count"),
            null_count=stats.get("null_count"),
            min=stats.get("min"),
            max=stats.get("max"),
            semantic_type=semantics.get("semantic_type"),
        )

    def _match(self, raw: dict[str, Any], search_term: str) -> CatalogMatch:
        scope = str(raw["scope"])
        level = str(raw["type"])
        name = str(raw["name"])
        return CatalogMatch(
            path=f"{scope.rstrip('/')}/{level}/{name}",
            level=level,
            name=name,
            description=_description(raw),
            score=float(raw["_score"]),
            search_term=search_term,
        )

    def _search(
        self, query: str, page_size: int, record_type: str | None = None
    ) -> list[dict[str, Any]]:
        results = self._registry.search_records(
            query,
            type=record_type,
            page_size=page_size,
            page_limit=_SEARCH_PAGE_LIMIT,
            **_NO_THRESHOLDS,
        )
        return cast("list[dict[str, Any]]", results)

    def _source_record(self, raw: dict[str, Any]) -> SourceRecord:
        connection = _properties(raw).get("connection", {})
        if not isinstance(connection, dict):
            raise ValueError(f"Source record {raw.get('name')}: no properties.connection.protocol")
        protocol = cast("dict[str, Any]", connection).get("protocol")
        if not protocol:
            raise ValueError(f"Source record {raw.get('name')}: no properties.connection.protocol")
        return SourceRecord(
            path=f"/source/{raw['name']}",
            name=str(raw["name"]),
            kind=str(protocol),
            description=_description(raw),
        )
