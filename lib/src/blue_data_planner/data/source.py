"""Reading a registered source: a live source object, and the rows a query returns."""

from __future__ import annotations

from typing import Any, cast

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.types.errors import SourceQueryError, SourceUnreachableError
from blue_data_planner.types.plan import Data


def connect_source(registry: DataRegistry, source: str) -> Any:
    """Return the live source object registered under one name."""
    # The one untyped-library boundary (blue-data has no py.typed): delegate through Any so
    # unknown member types stop here, not in every caller.
    registry_calls: Any = registry
    connected: Any = registry_calls.connect_source(source)
    if connected is None:
        raise SourceUnreachableError(
            f"Source {source!r} could not be connected to: the registry holds no connectable "
            f"source under that name"
        )
    return connected


def execute_query(
    registry: DataRegistry,
    source: str,
    query: str,
    database: str,
    collection: str | None = None,
) -> Data:
    """Run one query against a registered source and return its rows."""
    source_object: Any = connect_source(registry, source)
    try:
        rows: Any = source_object.execute_query(
            query=query, database=database, collection=collection
        )
    # blue-data reaches several backends and the DB-API defines no shared error base, so no
    # narrower catch is available here.
    except Exception as error:
        raise SourceQueryError(
            f"Source {source!r} refused the query against database {database!r}: "
            f"{type(error).__name__}: {error}"
        ) from error
    return _as_table(rows, source)


def _as_table(rows: Any, source: str) -> Data:
    if not isinstance(rows, list):
        raise ValueError(
            f"Source {source!r} returned {type(rows).__name__}, expected a list of rows"
        )
    table = cast("list[Any]", rows)
    for index, row in enumerate(table):
        if not isinstance(row, dict):
            raise ValueError(
                f"Source {source!r} returned {type(row).__name__} at row {index}, "
                f"expected a mapping"
            )
    return cast("Data", table)
