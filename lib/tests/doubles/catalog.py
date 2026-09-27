"""A catalog double whose attribute views are fixed per entity path."""

from __future__ import annotations

from blue_data_planner.types import AttributeView


class FakeCatalog:
    """Answers attribute_views from a fixed map, and nothing for an unlisted path."""

    def __init__(self, views: dict[str, list[AttributeView]] | None = None) -> None:
        self.views = views or {}

    def attribute_views(self, entity_path: str) -> list[AttributeView]:
        return self.views.get(entity_path, [])
