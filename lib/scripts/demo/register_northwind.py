"""Build a blue-data registry over the Northwind demo database into data/demo/registry.json."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)
from blue_data.source.json.memory_json_source import (  # pyright: ignore[reportMissingTypeStubs]
    MemoryJSONSource,
)
from blue_data.source.sql.sqlite.sqlite_sql_source import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteDBSource,
)

SOURCE_NAME = "northwind"
DATABASE_NAME = "northwind"
SEED_FILE_NAME = "northwind_sqlite.sql"
DESCRIPTIONS_FILE_NAME = "northwind_descriptions.json"
DEFAULT_SAMPLE_LIMIT = 10

# SQLite has no schemas, so blue-data reports one collection called "public" and a catalog
# path reads source -> database -> public -> entity -> attribute.
COLLECTION_NAME = "public"

# SQLite bookkeeping that AUTOINCREMENT creates, not part of the Northwind schema.
INTERNAL_ENTITY_NAMES = ("sqlite_sequence",)

# Nothing here calls a model. Syncing and statistics are deterministic, and the attribute
# descriptions are the ones blue-data generated once and committed alongside the seed.


def build_sqlite_database(seed_path: Path, database_path: Path) -> None:
    """Run the Northwind seed script into a fresh SQLite file at database_path.

    Raises:
        FileNotFoundError: If seed_path does not exist.
    """
    if not seed_path.is_file():
        raise FileNotFoundError(f"Missing {seed_path}. Run fetch_northwind.py first to fetch it.")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.unlink(missing_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(seed_path.read_text())
        connection.commit()
    finally:
        connection.close()


def build_registry(
    database_dir: Path,
    source_name: str = SOURCE_NAME,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> Any:
    """Register database_dir as one SQLite source, sync it, and collect its statistics."""
    # An absolute directory: blue-data resolves the stored path against the working
    # directory, and the registry is reopened from lib/ by examples and from the
    # repository root by scripts. The registry file is built locally, so it may carry a
    # machine-specific path.
    registry: Any = DataRegistry(name=source_name.upper(), store=MemoryJSONSource())
    source = SQLiteDBSource(
        name=source_name,
        properties={"connection": {"database_directory": str(database_dir.resolve())}},
    )
    registry.register_source(source)
    registry.sync_source(source_name, recursive=True)
    registry.collect_source_stats(source_name, recursive=True, sample_limit=sample_limit)
    deregister_internal_entities(registry, source_name)
    return registry


def deregister_internal_entities(
    registry: Any, source_name: str = SOURCE_NAME, database_name: str = DATABASE_NAME
) -> list[str]:
    """Drop SQLite's own bookkeeping tables from the registry, returning what was dropped."""
    entity_names = {
        str(record["name"])
        for record in registry.list_collection_entities(source_name, database_name, COLLECTION_NAME)
    }
    dropped: list[str] = []
    for entity in INTERNAL_ENTITY_NAMES:
        if entity not in entity_names:
            continue
        # Deregistering an entity leaves its attribute records behind, so they go first.
        for attribute_record in registry.list_entity_attributes(
            source_name, database_name, COLLECTION_NAME, entity
        ):
            registry.deregister_source_database_collection_entity_attribute(
                source_name,
                database_name,
                COLLECTION_NAME,
                entity,
                str(attribute_record["name"]),
            )
        registry.deregister_source_database_collection_entity(
            source_name, database_name, COLLECTION_NAME, entity
        )
        dropped.append(entity)
    return dropped


def apply_descriptions(
    registry: Any,
    descriptions_path: Path,
    source_name: str = SOURCE_NAME,
    database_name: str = DATABASE_NAME,
) -> int:
    """Write the committed attribute descriptions onto the registry, returning how many.

    Raises:
        FileNotFoundError: If descriptions_path does not exist.
        ValueError: If a key is not entity.attribute, or names a record the registry lacks.
    """
    if not descriptions_path.is_file():
        raise FileNotFoundError(
            f"Missing {descriptions_path}. Run fetch_northwind.py first to fetch it."
        )
    document: dict[str, Any] = json.loads(descriptions_path.read_text())
    descriptions: Any = document["descriptions"]
    for key, description in sorted(descriptions.items()):
        entity, separator, attribute = str(key).partition(".")
        if not separator or not attribute:
            raise ValueError(f"Description key {key!r} is not of the form entity.attribute")
        registry.set_source_database_collection_entity_attribute_description(
            source_name, database_name, COLLECTION_NAME, entity, attribute, str(description)
        )
    return len(descriptions)


def attribute_counts_by_entity(
    registry: Any, source_name: str = SOURCE_NAME, database_name: str = DATABASE_NAME
) -> dict[str, int]:
    """Return how many attribute records each of the database's entities holds."""
    counts: dict[str, int] = {}
    for entity_record in registry.list_collection_entities(
        source_name, database_name, COLLECTION_NAME
    ):
        entity = str(entity_record["name"])
        counts[entity] = len(
            registry.list_entity_attributes(source_name, database_name, COLLECTION_NAME, entity)
        )
    return counts


def check_database_registered(
    registry: Any, source_name: str = SOURCE_NAME, database_name: str = DATABASE_NAME
) -> None:
    """Raise unless the database synced with at least one entity.

    Raises:
        ValueError: If the database is absent from the registry or holds no entity record.
    """
    database_names = [str(record["name"]) for record in registry.list_source_databases(source_name)]
    if database_name not in database_names:
        raise ValueError(
            f"Database {database_name!r} is on disk but absent from the registry after syncing; "
            f"the registry holds {database_names}"
        )
    if not attribute_counts_by_entity(registry, source_name, database_name):
        raise ValueError(f"Database {database_name!r} synced with no entity records")


def write_registry(registry: Any, registry_path: Path) -> None:
    """Serialize a registry to registry_path as one JSON document."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry.dump(str(registry_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a blue-data registry over the Northwind demo database"
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/demo"))
    parser.add_argument("--source-name", default=SOURCE_NAME)
    parser.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    registry_path = data_dir / "registry.json"
    if registry_path.exists() and not args.force:
        print(f"skip {registry_path} (exists; use --force to rebuild)")
        return

    database_path = data_dir / f"{DATABASE_NAME}.db"
    print(f"building {database_path} from {data_dir / SEED_FILE_NAME}")
    build_sqlite_database(data_dir / SEED_FILE_NAME, database_path)

    print(f"registering {database_path.name} as source {args.source_name}")
    registry = build_registry(
        data_dir, source_name=args.source_name, sample_limit=args.sample_limit
    )
    check_database_registered(registry, source_name=args.source_name)

    described = apply_descriptions(
        registry, data_dir / DESCRIPTIONS_FILE_NAME, source_name=args.source_name
    )
    print(f"applied {described} committed attribute descriptions")

    write_registry(registry, registry_path)
    counts = attribute_counts_by_entity(registry, args.source_name)
    for entity, attribute_count in sorted(counts.items()):
        print(f"  {entity}: {attribute_count} attributes")
    size_kb = registry_path.stat().st_size / 1_000
    print(f"Wrote {registry_path} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
