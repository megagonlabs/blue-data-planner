"""Reading a registered source: a live source object, and the rows a query returns."""

from __future__ import annotations

from typing import Any, cast

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.types.plan import Data


def connect_source(registry: DataRegistry, source: str) -> Any:
    """Return the live source object registered under one name."""
    # The one untyped-library boundary (blue-data has no py.typed): delegate through Any so
    # unknown member types stop here, not in every caller.
    registry_calls: Any = registry
    return registry_calls.connect_source(source)


def execute_query(
    registry: DataRegistry,
    source: str,
    query: str,
    database: str,
    collection: str | None = None,
) -> Data:
    """Run one query against a registered source and return its rows."""
    source_object: Any = connect_source(registry, source)
    rows: Any = source_object.execute_query(query=query, database=database, collection=collection)
    return cast("Data", rows)
