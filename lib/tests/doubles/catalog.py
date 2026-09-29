"""A catalog double whose attribute views are fixed per entity path, and a view to fill it with."""

from __future__ import annotations

from typing import Any

from blue_data_planner.types import AttributeRef, AttributeView, ValueCount


class FakeCatalog:
    """Answers each reading from a fixed map, and nothing for an unlisted path."""

    def __init__(
        self,
        views: dict[str, list[AttributeView]] | None = None,
        row_counts: dict[str, int] | None = None,
    ) -> None:
        self.views = views or {}
        self.row_counts = row_counts or {}

    def attribute_views(self, entity_path: str) -> list[AttributeView]:
        return self.views.get(entity_path, [])

    def entity_row_count(self, entity_path: str) -> int | None:
        return self.row_counts.get(entity_path)


def an_attribute_view(
    attribute: str,
    entity: str = "postings",
    dtype: str | None = "str",
    description: str | None = None,
    frequent_values: list[tuple[Any, int]] | None = None,
    sample_values: list[Any] | None = None,
    distinct_count: int | None = None,
    null_count: int | None = None,
    empty_count: int | None = None,
    counted_rows: int | None = None,
) -> AttributeView:
    """One attribute of an entity in a jobs database, recording only what is given."""
    return AttributeView(
        ref=AttributeRef(
            source="jobs",
            database="jobs",
            collection="public",
            entity=entity,
            attribute=attribute,
        ),
        dtype=dtype,
        description=description,
        frequent_values=[
            ValueCount(value=value, rows=rows) for value, rows in frequent_values or []
        ],
        sample_values=sample_values or [],
        distinct_count=distinct_count,
        null_count=null_count,
        empty_count=empty_count,
        counted_rows=counted_rows,
        min=None,
        max=None,
        semantic_type=None,
    )
