"""Tests for lib/scripts/tag_bench/fetch_databases.py."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts.tag_bench.fetch_databases import (
    _INNER_ZIP_ENTRY,
    extract_bird_databases,
    load_required_db_names,
)


def _write_queries_json(path: Path, db_names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    queries = [{"query_id": i, "db_name": name} for i, name in enumerate(db_names)]
    path.write_text(json.dumps({"queries": queries}))


def test_load_required_db_names_returns_distinct_sorted_names(tmp_path: Path) -> None:
    """The distinct db_name values are returned sorted, with duplicates collapsed."""
    queries_path = tmp_path / "queries.json"
    _write_queries_json(queries_path, ["formula_1", "california_schools", "formula_1"])
    assert load_required_db_names(queries_path) == ["california_schools", "formula_1"]


def test_load_required_db_names_raises_when_queries_json_is_missing(tmp_path: Path) -> None:
    """A missing queries.json raises and names fetch_queries.py."""
    with pytest.raises(FileNotFoundError, match="fetch_queries.py"):
        load_required_db_names(tmp_path / "queries.json")


def _build_bird_zip(db_name_to_bytes: dict[str, bytes]) -> bytes:
    inner_buffer = io.BytesIO()
    with zipfile.ZipFile(inner_buffer, "w") as inner:
        for db_name, data in db_name_to_bytes.items():
            inner.writestr(f"dev_databases/{db_name}/{db_name}.sqlite", data)

    outer_buffer = io.BytesIO()
    with zipfile.ZipFile(outer_buffer, "w") as outer:
        outer.writestr(_INNER_ZIP_ENTRY, inner_buffer.getvalue())
    return outer_buffer.getvalue()


def test_extract_bird_databases_writes_each_requested_db(tmp_path: Path) -> None:
    """Each requested db_name's .sqlite bytes land at output_dir/<db_name>.db."""
    zip_path = tmp_path / "dev.zip"
    zip_path.write_bytes(_build_bird_zip({"formula_1": b"fake-sqlite-bytes"}))
    output_dir = tmp_path / "databases"

    extract_bird_databases(zip_path, ["formula_1"], output_dir)

    assert (output_dir / "formula_1.db").read_bytes() == b"fake-sqlite-bytes"


def test_extract_bird_databases_raises_on_missing_entry(tmp_path: Path) -> None:
    """A db_name missing from the inner zip raises, naming the missing entry."""
    zip_path = tmp_path / "dev.zip"
    zip_path.write_bytes(_build_bird_zip({"formula_1": b"fake-sqlite-bytes"}))
    output_dir = tmp_path / "databases"

    with pytest.raises(ValueError, match="california_schools"):
        extract_bird_databases(zip_path, ["california_schools"], output_dir)
