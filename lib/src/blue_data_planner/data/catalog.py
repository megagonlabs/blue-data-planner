"""The three readings each planning component takes off the data catalog."""

from __future__ import annotations

from typing import Any, cast

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.types.catalog import (
    AttributeRecord,
    AttributeRef,
    AttributeView,
    CatalogPreview,
    EntityMatch,
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

# A wide entity's cost is almost all long text in a handful of attributes, so capping the
# samples is what bounds a view; nothing else about it is capped.
_MAX_SAMPLE_VALUES = 3


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


def _entity_scope(ref: AttributeRef) -> str:
    return (
        f"/source/{ref.source}/database/{ref.database}"
        f"/collection/{ref.collection}/entity/{ref.entity}"
    )


def _parse_entity_path(entity_path: str) -> AttributeRef:
    """Split an entity's catalog path into its levels, with the attribute left empty."""
    parts = entity_path.strip("/").split("/")
    levels = parts[::2]
    names = parts[1::2]
    if levels != ["source", "database", "collection", "entity"] or len(names) != 4:
        raise ValueError(f"{entity_path!r} is not an entity path")
    return AttributeRef(
        source=names[0],
        database=names[1],
        collection=names[2],
        entity=names[3],
        attribute="",
    )


def _row_count(properties: dict[str, Any], path: str) -> int | None:
    stats = properties.get("stats", {})
    if not isinstance(stats, dict):
        raise ValueError(
            f"Entity record {path}: stats is {type(stats).__name__}, expected a mapping"
        )
    stats = cast("dict[str, Any]", stats)
    row_count = stats.get("row_count")
    if row_count is None:
        return None
    if isinstance(row_count, bool) or not isinstance(row_count, int):
        raise ValueError(
            f"Entity record {path}: row_count is {type(row_count).__name__}, expected an int"
        )
    return row_count


def _dtype(properties: dict[str, Any], path: str) -> str | None:
    info = properties.get("info")
    if not isinstance(info, dict):
        raise ValueError(f"Attribute record {path}: properties.info missing or not a mapping")
    declared = cast("dict[str, Any]", info).get("type")
    # A source with dynamic typing registers a column with no type at all, so an absent one
    # is absence rather than corruption, the way an absent description is.
    return str(declared) if declared else None


class DataCatalog:
    """Reads the data catalog for the three planning components."""

    def __init__(self, registry: DataRegistry, max_search_results: int = 3) -> None:
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

    def search_records(self, term: str) -> list[EntityMatch]:
        """Return the entities one term found, each with every attribute it holds."""
        return [
            self._match(raw, term)
            for raw in self._search(term, self._max_search_results, record_type="entity")
        ]

    def attribute_views(self, entity_path: str) -> list[AttributeView]:
        """Return one view per attribute the given entity holds."""
        entity = _parse_entity_path(entity_path)
        return [self._view(entity, record) for record in self._attribute_records(entity_path)]

    def _view(self, entity: AttributeRef, record: dict[str, Any]) -> AttributeView:
        name = str(record["name"])
        path = f"{_entity_scope(entity)}/attribute/{name}"
        properties = _properties(record)
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
        samples = cast("list[Any]", stats.get("sample_values", []))
        return AttributeView(
            ref=AttributeRef(
                source=entity.source,
                database=entity.database,
                collection=entity.collection,
                entity=entity.entity,
                attribute=name,
            ),
            dtype=_dtype(properties, path),
            description=_description(record),
            sample_values=list(samples[:_MAX_SAMPLE_VALUES]),
            distinct_count=stats.get("distinct_count"),
            null_count=stats.get("null_count"),
            min=stats.get("min"),
            max=stats.get("max"),
            semantic_type=semantics.get("semantic_type"),
        )

    def _match(self, raw: dict[str, Any], search_term: str) -> EntityMatch:
        scope = str(raw["scope"]).rstrip("/")
        name = str(raw["name"])
        path = f"{scope}/entity/{name}"
        entity = _parse_entity_path(path)
        return EntityMatch(
            path=path,
            name=name,
            description=_description(raw),
            source_kind=self._source_kind(entity.source, path),
            attributes=[
                self._attribute_record(record, path) for record in self._attribute_records(path)
            ],
            score=float(raw["_score"]),
            search_term=search_term,
            row_count=_row_count(_properties(raw), path),
        )

    def _attribute_record(self, record: dict[str, Any], entity_path: str) -> AttributeRecord:
        name = str(record["name"])
        properties = _properties(record)
        return AttributeRecord(
            name=name,
            dtype=_dtype(properties, f"{entity_path}/attribute/{name}"),
            description=_description(record),
        )

    def _attribute_records(self, entity_path: str) -> list[dict[str, Any]]:
        return cast(
            "list[dict[str, Any]]",
            self._registry.list_records(record_type="attribute", scope=entity_path),
        )

    def _source_kind(self, source: str, entity_path: str) -> str:
        record = self._registry.get_record(source, "source", "/")
        if record is None:
            raise ValueError(f"Entity record {entity_path}: no source record named {source!r}")
        return self._protocol(cast("dict[str, Any]", record))

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
        return SourceRecord(
            path=f"/source/{raw['name']}",
            name=str(raw["name"]),
            kind=self._protocol(raw),
            description=_description(raw),
        )

    def _protocol(self, raw: dict[str, Any]) -> str:
        connection = _properties(raw).get("connection", {})
        if not isinstance(connection, dict):
            raise ValueError(
                f"Source record {raw.get('name')}: properties.connection missing or not a mapping"
            )
        protocol = cast("dict[str, Any]", connection).get("protocol")
        if not protocol:
            raise ValueError(
                f"Source record {raw.get('name')}: properties.connection has no protocol field"
            )
        return str(protocol)
