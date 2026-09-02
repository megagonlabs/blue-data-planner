"""Tests for lib/scripts/tag_bench/register_databases.py."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)
from blue_data.source.json.memory_json_source import (  # pyright: ignore[reportMissingTypeStubs]
    MemoryJSONSource,
)

from scripts.tag_bench.register_databases import (
    build_registry,
    check_every_database_registered,
    entity_counts_by_database,
    sqlite_database_names,
    write_registry,
)

_SCHEMAS: dict[str, str] = {
    "retail": """
        CREATE TABLE stores (store_id INTEGER, name TEXT, city TEXT);
        INSERT INTO stores VALUES (1, 'Bay Market', 'Oakland'), (2, 'Hill Foods', 'Reno');
        CREATE TABLE reviews (store_id INTEGER, body TEXT);
        INSERT INTO reviews VALUES (1, 'great'), (2, 'fine');
    """,
    "racing": """
        CREATE TABLE drivers (driver_id INTEGER, surname TEXT);
        INSERT INTO drivers VALUES (1, 'Hamilton'), (2, 'Verstappen');
    """,
}


def two_staged_databases(tmp_path: Path) -> Path:
    """Stage retail.db (stores, reviews) and racing.db (drivers), returning the directory."""
    databases_dir = tmp_path / "databases"
    databases_dir.mkdir(parents=True, exist_ok=True)
    for db_name, schema in _SCHEMAS.items():
        connection = sqlite3.connect(databases_dir / f"{db_name}.db")
        connection.executescript(schema)
        connection.commit()
        connection.close()
    return databases_dir


def a_scripted_enricher(prompt: str) -> str:
    """Answer an enrichment prompt the way a model would: JSON where the prompt asks for it."""
    # Naming an attribute the table does not have makes blue-data raise, so the
    # descriptions are written for the attributes the prompt itself lists.
    if "table_description" not in prompt:
        return "Records from one collection."
    attributes = re.findall(r"^- (\w+) \(", prompt, flags=re.MULTILINE)
    return json.dumps(
        {
            "table_description": "One row per record.",
            "attributes": {name: f"The {name} column." for name in attributes},
        }
    )


def attribute_record(registry: Any, database: str, entity: str, attribute: str) -> dict[str, Any]:
    return registry.get_source_database_collection_entity_attribute(
        "tag_bench", database, "public", entity, attribute
    )


def test_sqlite_database_names_lists_the_staged_databases_sorted(tmp_path: Path) -> None:
    """The staged .db files are reported by name, sorted, without the extension."""
    assert sqlite_database_names(two_staged_databases(tmp_path)) == ["racing", "retail"]


def test_sqlite_database_names_raises_and_names_the_fetch_step_when_none_is_staged(
    tmp_path: Path,
) -> None:
    """A missing directory and an empty one both raise, pointing at fetch_databases.py."""
    with pytest.raises(FileNotFoundError, match="fetch_databases.py"):
        sqlite_database_names(tmp_path / "absent")

    empty_dir = tmp_path / "databases"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="fetch_databases.py"):
        sqlite_database_names(empty_dir)


def test_build_registry_records_every_table_and_column_of_every_database(tmp_path: Path) -> None:
    """Each staged database arrives as a database record holding its tables and columns."""
    registry = build_registry(two_staged_databases(tmp_path))

    assert entity_counts_by_database(registry) == {"racing": 1, "retail": 2}
    stores_columns = {
        record["name"]
        for record in registry.list_entity_attributes("tag_bench", "retail", "public", "stores")
    }
    assert stores_columns == {"store_id", "name", "city"}


def test_build_registry_stores_stats_and_a_semantic_type_on_every_attribute(
    tmp_path: Path,
) -> None:
    """An attribute record carries the values, counts, and semantic type a planner reads."""
    registry = build_registry(two_staged_databases(tmp_path))

    properties = attribute_record(registry, "retail", "stores", "city")["properties"]
    assert sorted(properties["stats"]["sample_values"]) == ["Oakland", "Reno"]
    assert properties["stats"]["distinct_count"] == 2
    assert properties["value_semantics"]["semantic_type"] == "ENUM_CATEGORY"


def test_build_registry_writes_descriptions_when_given_an_llm_fn(tmp_path: Path) -> None:
    """A supplied llm_fn fills in the entity and attribute descriptions enrichment adds."""
    registry = build_registry(two_staged_databases(tmp_path), llm_fn=a_scripted_enricher)

    entity = registry.get_source_database_collection_entity(
        "tag_bench", "retail", "public", "stores"
    )
    assert entity["description"] == "One row per record."
    assert attribute_record(registry, "retail", "stores", "city")["description"] == (
        "The city column."
    )


def test_a_written_registry_reopens_from_another_directory_and_queries_the_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A serialized registry reconnects to its SQLite files whatever the working directory."""
    databases_dir = two_staged_databases(tmp_path)
    registry_path = tmp_path / "registry.json"
    write_registry(build_registry(databases_dir), registry_path)

    working_dir = tmp_path / "elsewhere"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    reopened: Any = DataRegistry(store=MemoryJSONSource())
    reopened.load(str(registry_path))
    rows = reopened.execute_query(
        "tag_bench", "SELECT city FROM stores ORDER BY city", database="retail"
    )

    assert [row["city"] for row in rows] == ["Oakland", "Reno"]


def test_check_every_database_registered_raises_for_a_database_without_entities(
    tmp_path: Path,
) -> None:
    """A database missing from the registry, or synced with no tables, raises."""
    databases_dir = two_staged_databases(tmp_path)
    # A zero-byte file is a valid empty SQLite database, which is what a truncated
    # download leaves behind.
    (databases_dir / "truncated.db").touch()
    registry = build_registry(databases_dir)

    with pytest.raises(ValueError, match="absent from the registry"):
        check_every_database_registered(registry, ["retail", "never_staged"])
    with pytest.raises(ValueError, match="no entity records"):
        check_every_database_registered(registry, ["retail", "truncated"])
