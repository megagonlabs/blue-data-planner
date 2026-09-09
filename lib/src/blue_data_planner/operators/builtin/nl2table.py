"""The compound entry: reading a fragment's entities, writing a program, and running it."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm.templating import safe_substitute
from blue_data_planner.operators.builtin import source_scan
from blue_data_planner.operators.builtin.schemas import IndexedScanSource, Nl2TableParameters
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import Data, DataBundle, OutputColumn, RecoverableOperatorError

_ENTITY_SEGMENT = re.compile(r"/entity/(?P<entity>[^/]+)$")

# Enough rows for a model to see how a column spells its values, and few enough to leave room
# for the instruction (design/04: the values are what the catalog alone cannot give).
SAMPLE_ROWS = 5


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


class WrittenProgram(BaseModel):
    """The program one model call produced."""

    code: str


def program_prompt(
    instruction: str, tables: dict[str, Data], required_output_columns: list[OutputColumn]
) -> str:
    """Return the prompt one program is written from."""
    return safe_substitute(
        prompt_text("nl2table.md"),
        instruction=instruction,
        output_columns=_rendered_columns(required_output_columns),
        tables=_rendered_tables(tables),
    )


def retry_prompt(code: str, failure: str) -> str:
    """Return the prompt the next program is written from after one did not run."""
    return (
        f"This program did not produce a table:\n\n```python\n{code}\n```\n\n"
        f"What happened: {failure}\n\nWrite a program that does, under the same rules."
    )


def _rendered_columns(required_output_columns: list[OutputColumn]) -> str:
    if not required_output_columns:
        return "Any columns the request calls for."
    return "\n".join(f"- `{column.name}` ({column.dtype})" for column in required_output_columns)


def _rendered_tables(tables: dict[str, Data]) -> str:
    if not tables:
        return "None. Everything the request needs is in the instruction."
    return "\n\n".join(_rendered_table(name, rows) for name, rows in tables.items())


def _rendered_table(name: str, rows: Data) -> str:
    columns = sorted({column for row in rows for column in row})
    sample = rows[:SAMPLE_ROWS]
    lines = [f"### `{name}` — {len(rows)} rows", "", f"Columns: {', '.join(columns) or 'none'}", ""]
    lines.extend(f"- {row}" for row in sample)
    return "\n".join(lines)
