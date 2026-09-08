"""Scanning one entity of a SQL source, including the table bound values sometimes travel in."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from blue_data_planner.data import DataRegistry, execute_query
from blue_data_planner.types import (
    Data,
    RecoverableOperatorError,
    SourceQueryError,
    SourceUnreachableError,
    UnrecoverableOperatorError,
)

_SCAN_SOURCE = re.compile(
    r"^/source/(?P<source>[^/]+)/database/(?P<database>[^/]+)"
    r"/collection/(?P<collection>[^/]+)/entity/(?P<entity>[^/]+)$"
)


@dataclass(frozen=True)
class _EntityRef:
    source: str
    database: str
    collection: str
    entity: str


def scan_entity(registry: DataRegistry, scan_source: str) -> tuple[Data, dict[str, Any]]:
    """Read every row of one registered entity, with the report of what ran."""
    entity = _entity_ref(scan_source)
    query = _select(entity, where=None)
    return _read(registry, entity, query), {"query": query, "bind_join": None}


def _entity_ref(scan_source: str) -> _EntityRef:
    match = _SCAN_SOURCE.match(scan_source)
    if match is None:
        raise RecoverableOperatorError(
            f"builtin.scan was given a scan_source it cannot read as a catalog path: "
            f"{scan_source!r}"
        )
    return _EntityRef(
        source=match["source"],
        database=match["database"],
        collection=match["collection"],
        entity=match["entity"],
    )


def _select(entity: _EntityRef, where: str | None) -> str:
    query = f"SELECT * FROM {_quoted(entity.entity)}"
    return query if where is None else f"{query} WHERE {where}"


def _read(registry: DataRegistry, entity: _EntityRef, query: str) -> Data:
    try:
        return execute_query(registry, entity.source, query, entity.database, entity.collection)
    except SourceUnreachableError as error:
        raise UnrecoverableOperatorError(str(error), report={"query": query}) from error
    except SourceQueryError as error:
        raise RecoverableOperatorError(str(error), report={"query": query}) from error


def _quoted(name: str) -> str:
    # Quoted rather than refused: TAG-Bench inherits BIRD's column names, which include
    # "Percent (%) Eligible Free (K-12)".
    if "\x00" in name:
        raise RecoverableOperatorError(f"{name!r} is not a name a source can be asked for")
    escaped = name.replace('"', '""')
    return f'"{escaped}"'
