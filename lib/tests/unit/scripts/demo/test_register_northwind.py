"""Tests for lib/scripts/demo/register_northwind.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.demo.register_northwind import (
    apply_descriptions,
    check_database_registered,
    deregister_internal_entities,
)

SOURCE = "northwind"
DATABASE = "northwind"
COLLECTION = "public"


class FakeRegistry:
    """A registry of entity names and their attributes, recording what is written to it."""

    def __init__(self, entities: dict[str, list[str]], databases: list[str] | None = None) -> None:
        self.entities = {name: list(attributes) for name, attributes in entities.items()}
        self.databases = [DATABASE] if databases is None else list(databases)
        self.descriptions: dict[str, str] = {}

    def list_source_databases(self, source: str) -> list[dict[str, Any]]:
        assert source == SOURCE
        return [{"name": name} for name in self.databases]

    def list_collection_entities(
        self, source: str, database: str, collection: str
    ) -> list[dict[str, Any]]:
        assert (source, database, collection) == (SOURCE, DATABASE, COLLECTION)
        return [{"name": name} for name in self.entities]

    def list_entity_attributes(
        self, source: str, database: str, collection: str, entity: str
    ) -> list[dict[str, Any]]:
        assert (source, database, collection) == (SOURCE, DATABASE, COLLECTION)
        return [{"name": name} for name in self.entities[entity]]

    def set_source_database_collection_entity_attribute_description(
        self,
        source: str,
        database: str,
        collection: str,
        entity: str,
        attribute: str,
        description: str,
    ) -> None:
        assert (source, database, collection) == (SOURCE, DATABASE, COLLECTION)
        self.descriptions[f"{entity}.{attribute}"] = description

    def deregister_source_database_collection_entity(
        self, source: str, database: str, collection: str, entity: str
    ) -> None:
        assert (source, database, collection) == (SOURCE, DATABASE, COLLECTION)
        del self.entities[entity]

    def deregister_source_database_collection_entity_attribute(
        self, source: str, database: str, collection: str, entity: str, attribute: str
    ) -> None:
        assert (source, database, collection) == (SOURCE, DATABASE, COLLECTION)
        self.entities[entity].remove(attribute)


def a_northwind_registry(**entities: list[str]) -> FakeRegistry:
    """A registry holding the named entities, each with the attributes given."""
    return FakeRegistry(entities)


def a_descriptions_file(tmp_path: Path, descriptions: dict[str, str]) -> Path:
    """Write the descriptions document blue-data commits beside its seed, returning its path."""
    path = tmp_path / "northwind_descriptions.json"
    path.write_text(json.dumps({"descriptions": descriptions}))
    return path


def test_apply_descriptions_writes_every_description_and_returns_how_many(
    tmp_path: Path,
) -> None:
    """Each entity.attribute key reaches the registry as that attribute's description."""
    registry = a_northwind_registry(products=["product_name"], suppliers=["company_name"])
    descriptions = {
        "products.product_name": "The name of the product.",
        "suppliers.company_name": "The supplier's company name.",
    }

    written = apply_descriptions(registry, a_descriptions_file(tmp_path, descriptions))

    assert written == 2
    assert registry.descriptions == descriptions


def test_apply_descriptions_raises_and_names_fetch_northwind_when_the_file_is_absent(
    tmp_path: Path,
) -> None:
    """A missing descriptions file raises, pointing at the script that fetches it."""
    with pytest.raises(FileNotFoundError, match="fetch_northwind.py"):
        apply_descriptions(a_northwind_registry(), tmp_path / "northwind_descriptions.json")


def test_apply_descriptions_rejects_a_key_that_does_not_name_an_attribute(
    tmp_path: Path,
) -> None:
    """A key carrying no entity.attribute pair raises before anything is written."""
    registry = a_northwind_registry(products=["product_name"])
    path = a_descriptions_file(tmp_path, {"products": "Every product."})

    with pytest.raises(ValueError, match="entity.attribute"):
        apply_descriptions(registry, path)
    assert registry.descriptions == {}


def test_check_database_registered_accepts_a_database_that_synced_with_entities() -> None:
    """A registry holding the database and at least one entity record passes."""
    check_database_registered(a_northwind_registry(products=["product_name"]))


@pytest.mark.parametrize(
    ("registry", "message"),
    [
        (FakeRegistry({}, databases=["other"]), "absent from the registry"),
        (FakeRegistry({}), "no entity records"),
    ],
    ids=["database absent", "database empty"],
)
def test_check_database_registered_refuses_a_database_it_cannot_plan_against(
    registry: FakeRegistry, message: str
) -> None:
    """A database missing from the registry, or synced with no entity, raises."""
    with pytest.raises(ValueError, match=message):
        check_database_registered(registry)


def test_deregister_internal_entities_drops_sqlite_bookkeeping_with_its_attributes() -> None:
    """sqlite_sequence and its attributes leave the registry; the real tables stay."""
    registry = a_northwind_registry(products=["product_name"], sqlite_sequence=["name", "seq"])

    dropped = deregister_internal_entities(registry)

    assert dropped == ["sqlite_sequence"]
    assert registry.entities == {"products": ["product_name"]}
