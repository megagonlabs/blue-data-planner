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
class EntityMatch:
    """One entity a search term found, with a view of every attribute it holds."""

    path: str
    name: str
    description: str | None
    # The connection protocol of the source above the entity, which is what decides
    # which operators may run a fragment scanning it.
    source_kind: str
    attributes: list[AttributeView]
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
class ValueCount:
    """One stored value, and how many of the counted rows hold it."""

    value: Any
    rows: int

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {"value": self.value, "rows": self.rows}


@dataclass(frozen=True)
class AttributeView:
    """One attribute's values, how often each occurs, and how much of it is missing."""

    ref: AttributeRef
    dtype: str | None
    description: str | None
    # Most frequent first. Empty where the catalog records none, and sample_values stands in.
    frequent_values: list[ValueCount]
    sample_values: list[Any]
    # Whether the registry recorded more values than the list above keeps, which is what says a
    # list is a cut rather than the whole column where no distinct count is recorded.
    more_values_recorded: bool
    distinct_count: int | None
    null_count: int | None
    # Rows holding the empty string, read off the frequent values. None where they do not list
    # it, which says the count is unknown rather than zero.
    empty_count: int | None
    # The rows every count above was taken over. A registry may take its statistics over part
    # of a table, so the entity's row count is not their denominator.
    counted_rows: int | None
    min: Any
    max: Any
    # The inferred meaning, such as ENUM_CATEGORY, CODE, or POSTAL_CODE. It is what tells
    # the runner an attribute holds short codes instead of readable words.
    semantic_type: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "ref": {
                "source": self.ref.source,
                "database": self.ref.database,
                "collection": self.ref.collection,
                "entity": self.ref.entity,
                "attribute": self.ref.attribute,
            },
            "dtype": self.dtype,
            "description": self.description,
            "frequent_values": [one.to_dict() for one in self.frequent_values],
            "sample_values": list(self.sample_values),
            "more_values_recorded": self.more_values_recorded,
            "distinct_count": self.distinct_count,
            "null_count": self.null_count,
            "empty_count": self.empty_count,
            "counted_rows": self.counted_rows,
            "min": self.min,
            "max": self.max,
            "semantic_type": self.semantic_type,
        }
