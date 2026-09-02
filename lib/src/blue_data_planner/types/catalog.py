"""What the three planning stages read off the catalog. Filled by data/, never stored."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ColumnRef:
    """One column's full ancestry, which is how blue-data keys a catalog record."""

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
class CatalogMatch:
    """One catalog record a search term found."""

    path: str
    level: str  # source, database, collection, entity, or attribute
    name: str
    description: str | None
    score: float
    search_term: str


@dataclass(frozen=True)
class ColumnView:
    """What only the catalog knows about one column, over the whole column."""

    ref: ColumnRef
    dtype: str
    description: str | None
    sample_values: list[Any]
    distinct_count: int | None
    null_count: int | None
    min: Any
    max: Any
    # The inferred meaning, such as ENUM_CATEGORY, CODE, or POSTAL_CODE. It is what tells
    # the runner a column holds short codes instead of readable words.
    semantic_type: str | None
