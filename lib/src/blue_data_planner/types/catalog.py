"""What the three planning stages read off the catalog. Filled by data/, never stored."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AttributeRef:
    """One attribute's full ancestry, which is how blue-data keys a catalog record."""

    source: str
    database: str
    collection: str
    entity: str
    attribute: str


@dataclass(frozen=True)
class SourceRecord:
    """One registered source, as the logical planner sees it."""

    path: str
    name: str
    # The connection protocol blue-data recorded at registration, such as "sql/sqlite", so it
    # is available without enrichment and without a search.
    kind: str
    description: str | None


@dataclass(frozen=True)
class CatalogPreview:
    """Which sources exist, ranked against the request and capped."""

    sources: list[SourceRecord]
    # How many are registered, which may exceed len(sources) when the list is a sample.
    total_sources: int


@dataclass(frozen=True)
class AttributeRecord:
    """One attribute of a matched entity, named and typed but not profiled."""

    name: str
    # None where the registry recorded no type, which a source with dynamic typing produces.
    dtype: str | None
    description: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {"name": self.name, "dtype": self.dtype, "description": self.description}


@dataclass(frozen=True)
class EntityMatch:
    """One entity a search term found, with every attribute it holds."""

    path: str
    name: str
    description: str | None
    # The connection protocol of the source above the entity, which is what decides
    # which operators may run a fragment scanning it.
    source_kind: str
    attributes: list[AttributeRecord]
    score: float
    search_term: str
    # The registry's recorded row count for the entity. None means the registry records no
    # row count for it, which a registry built without stats legitimately produces.
    row_count: int | None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "path": self.path,
            "name": self.name,
            "description": self.description,
            "source_kind": self.source_kind,
            "attributes": [one.to_dict() for one in self.attributes],
            "score": self.score,
            "search_term": self.search_term,
            "row_count": self.row_count,
        }


@dataclass(frozen=True)
class AttributeView:
    """What only the catalog knows about one attribute, over all of its values."""

    ref: AttributeRef
    dtype: str | None
    description: str | None
    sample_values: list[Any]
    distinct_count: int | None
    null_count: int | None
    min: Any
    max: Any
    # The inferred meaning, such as ENUM_CATEGORY, CODE, or POSTAL_CODE. It is what tells
    # the runner an attribute holds short codes instead of readable words.
    semantic_type: str | None
