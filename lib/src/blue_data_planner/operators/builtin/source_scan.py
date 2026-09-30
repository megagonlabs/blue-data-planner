"""Scanning one entity of a SQL source, including the table bound values sometimes travel in."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from blue_data_planner.data import DataRegistry, execute_query, execute_write
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

# The values are rendered into the query text, so the source parses each one, and the cap bounds
# how long that text grows.
MAX_EMBEDDED_VALUES = 100


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
    return _read(registry, entity, query), _report(query, None, 0)


def scan_entity_bound_to(
    registry: DataRegistry,
    scan_source: str,
    attribute: str,
    reference_input: Data,
    input_column: str,
) -> tuple[Data, dict[str, Any]]:
    """Read the rows of one entity whose attribute matches a value the reference input carries."""
    entity = _entity_ref(scan_source)
    values = _distinct_values(reference_input, input_column)
    if not values:
        # No key can match, so the empty result is the answer rather than a query worth running.
        return [], _report(None, None, 0)
    if len(values) <= MAX_EMBEDDED_VALUES:
        rendered = ", ".join(_literal(value) for value in values)
        query = _select(entity, where=f"{_quoted(attribute)} IN ({rendered})")
        return _read(registry, entity, query), _report(query, "in_list", len(values))
    return _read_through_a_table(registry, entity, attribute, values)


def _report(query: str | None, bind_join: str | None, value_count: int) -> dict[str, Any]:
    return {"query": query, "bind_join": bind_join, "bound_value_count": value_count}


def _distinct_values(reference_input: Data, input_column: str) -> list[Any]:
    values: list[Any] = []
    seen: set[Any] = set()
    for index, row in enumerate(reference_input):
        if input_column not in row:
            raise RecoverableOperatorError(
                f"builtin.scan was told to bind on {input_column!r}, which row {index} of its "
                "reference input does not carry"
            )
        value = row[input_column]
        # A null key matches nothing a source could return, so it is not worth carrying.
        if value is None:
            continue
        if not isinstance(value, str | int | float):
            raise RecoverableOperatorError(
                f"builtin.scan cannot match a column against the {type(value).__name__} that "
                f"row {index} of its reference input carries in {input_column!r}"
            )
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _literal(value: Any) -> str:
    # blue-data's source interface takes a query string and offers no parameter binding, so
    # the values are rendered rather than bound.
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecoverableOperatorError(f"{value!r} is not a value a source can compare against")
        return repr(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


# SQLite bounds how many rows one INSERT ... VALUES may carry, so the values go in batches.
_INSERT_BATCH_SIZE = 500

_BOUND_VALUE_COLUMN = "value"


def _read_through_a_table(
    registry: DataRegistry, entity: _EntityRef, attribute: str, values: list[Any]
) -> tuple[Data, dict[str, Any]]:
    # blue-data's SQL sources open a connection per call and close it again, so a TEMP table
    # would be gone before the read that needs it. The table is ordinary and dropped by hand.
    table = f"blue_bind_{uuid4().hex}"
    query = _select(
        entity,
        where=f"{_quoted(attribute)} IN "
        f"(SELECT {_quoted(_BOUND_VALUE_COLUMN)} FROM {_quoted(table)})",
    )
    try:
        _write_bound_values(registry, entity, table, values)
        rows = _read(registry, entity, query)
    finally:
        # A request leaves nothing behind in a source a caller owns, on the failing path as
        # much as on the succeeding one (ADR-023).
        _drop_bound_values(registry, entity, table)
    return rows, _report(query, "temporary_table", len(values))


def _write_bound_values(
    registry: DataRegistry, entity: _EntityRef, table: str, values: list[Any]
) -> None:
    # The column is declared without a type so each value keeps its storage class and the
    # comparison takes the scanned column's affinity.
    _write(registry, entity, f"CREATE TABLE {_quoted(table)} ({_quoted(_BOUND_VALUE_COLUMN)})")
    for start in range(0, len(values), _INSERT_BATCH_SIZE):
        batch = values[start : start + _INSERT_BATCH_SIZE]
        rendered = ", ".join(f"({_literal(value)})" for value in batch)
        _write(registry, entity, f"INSERT INTO {_quoted(table)} VALUES {rendered}")


def _drop_bound_values(registry: DataRegistry, entity: _EntityRef, table: str) -> None:
    try:
        _write(registry, entity, f"DROP TABLE IF EXISTS {_quoted(table)}")
    except (RecoverableOperatorError, UnrecoverableOperatorError) as error:
        # A repair would write a second table rather than remove the first (ADR-023). This
        # catches both the source refusing the DROP and the source going unreachable during
        # it, so the "left the table" diagnostic survives either way.
        raise UnrecoverableOperatorError(
            f"builtin.scan left the table {table!r} in source {entity.source!r}: {error}",
            report={"table": table, "source": entity.source},
        ) from error


def _write(registry: DataRegistry, entity: _EntityRef, statement: str) -> None:
    try:
        execute_write(registry, entity.source, statement, entity.database, entity.collection)
    except SourceUnreachableError as error:
        raise UnrecoverableOperatorError(str(error), report={"statement": statement}) from error
    except SourceQueryError as error:
        raise RecoverableOperatorError(str(error), report={"statement": statement}) from error


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
