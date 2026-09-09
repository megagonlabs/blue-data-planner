"""The compound entry: reading a fragment's entities, writing a program, and running it."""

from __future__ import annotations

import re
from typing import Any

from blue_data_planner.data import DataRegistry
from blue_data_planner.operators.builtin import source_scan
from blue_data_planner.operators.builtin.schemas import IndexedScanSource, Nl2TableParameters
from blue_data_planner.types import Data, DataBundle, RecoverableOperatorError

_ENTITY_SEGMENT = re.compile(r"/entity/(?P<entity>[^/]+)$")


def read_tables(
    data_registry: DataRegistry, parameters: Nl2TableParameters, inputs: DataBundle
) -> tuple[dict[str, Data], list[dict[str, Any]]]:
    """Return the named tables a program works over, and one report per entity read."""
    tables: dict[str, Data] = {f"input_{index}": table for index, table in enumerate(inputs)}
    reports: list[dict[str, Any]] = []
    for scan in parameters.scan_sources:
        rows, report = _read_one(data_registry, scan, inputs)
        name = _unique_name(_entity_name(scan.scan_source), tables)
        tables[name] = rows
        reports.append({"table": name, **report})
    return tables, reports


def _read_one(
    data_registry: DataRegistry, scan: IndexedScanSource, inputs: DataBundle
) -> tuple[Data, dict[str, Any]]:
    if scan.bound_values is None:
        return source_scan.scan_entity(data_registry, scan.scan_source)
    index = scan.bound_values.input_index
    if index >= len(inputs):
        raise RecoverableOperatorError(
            f"builtin.nl2table was given an input_index of {index} and {len(inputs)} input tables"
        )
    return source_scan.scan_entity_bound_to(
        data_registry,
        scan.scan_source,
        scan.bound_values.attribute,
        inputs[index],
        scan.bound_values.input_column,
    )


def _entity_name(scan_source: str) -> str:
    match = _ENTITY_SEGMENT.search(scan_source)
    if match is None:
        raise RecoverableOperatorError(
            f"builtin.nl2table was given a scan_source it cannot read as a catalog path: "
            f"{scan_source!r}"
        )
    # A program refers to a table by name, so a name Python cannot spell is replaced rather
    # than refused: TAG-Bench inherits BIRD's entity names, which include "store notes".
    return re.sub(r"\W|^(?=\d)", "_", match["entity"])


def _unique_name(name: str, taken: dict[str, Data]) -> str:
    if name not in taken:
        return name
    suffix = 2
    while f"{name}_{suffix}" in taken:
        suffix += 1
    return f"{name}_{suffix}"
