"""What opening a serialized blue-data registry gives back."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from blue_data.config import build_store  # pyright: ignore[reportMissingTypeStubs]
from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,
)

from blue_data_planner.data import DataCatalog, open_registry
from blue_data_planner.types import InvalidSettingError, RegistryUnreadableError


def test_a_reopened_registry_holds_the_records_it_was_written_with(tmp_path: Path) -> None:
    """A registry written to disk reads back with its records addressable by path."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "name": "jobs",
                    "type": "source",
                    "scope": "/",
                    "description": "job postings",
                    "properties": {"connection": {"protocol": "sql/postgres"}},
                    "contents": {},
                }
            ]
        )
    )

    registry = open_registry(registry_path)

    # blue-data ships no py.typed, so get_record's return stays partially unknown here,
    # the one place a test reads the raw registry `open_registry` hands back.
    assert (
        registry.get_record("jobs", "source", "/")[  # pyright: ignore[reportUnknownMemberType]
            "description"
        ]
        == "job postings"
    )


def test_opening_a_registry_that_is_not_there_raises(tmp_path: Path) -> None:
    """A path naming no file is a caller error, not an empty registry."""
    with pytest.raises(RegistryUnreadableError):
        open_registry(tmp_path / "absent.json")


@pytest.mark.parametrize("explicit", [False, True])
def test_blue_data_store_records_can_be_searched_and_queried_without_rewriting_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sqlite_registry: Any, explicit: bool
) -> None:
    path = tmp_path / "store.json"
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "file")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_FILE", str(path))
    # The same build_store and DataRegistry writes used by blue-registry/API/MCP.
    writer: Any = DataRegistry(store=build_store())
    writer.register_source(sqlite_registry.get_source("tag_bench"))
    writer.sync_source("tag_bench", recursive=True)
    before = path.read_bytes()

    reopened: Any = open_registry(path if explicit else None)

    assert [r["name"] for r in reopened.list_records(record_type="source")] == ["tag_bench"]
    assert DataCatalog(reopened).search_records("stores")
    assert reopened.execute_query(
        "tag_bench", "SELECT city FROM stores ORDER BY city", database="retail"
    ) == [{"city": "Oakland"}, {"city": "Reno"}]
    assert path.read_bytes() == before


def test_shared_store_outranks_the_legacy_path_but_an_explicit_path_outranks_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "store.json"
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "file")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_FILE", str(path))
    writer: Any = DataRegistry(store=build_store())
    writer.register_record("shared", "source")
    legacy = tmp_path / "snapshot.json"
    legacy.write_text("[]")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_PATH", str(legacy))

    shared: Any = open_registry()
    explicit: Any = open_registry(legacy)
    assert shared.get_record("shared", "source") is not None
    assert explicit.list_records() == []
    monkeypatch.delenv("BLUE_DATA_REGISTRY_STORE")
    fallback: Any = open_registry()
    assert fallback.list_records() == []


def test_a_missing_shared_file_is_not_created_or_replaced_by_the_legacy_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "missing.json"
    legacy = tmp_path / "legacy.json"
    legacy.write_text("[]")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "file")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_FILE", str(path))
    monkeypatch.setenv("BLUE_DATA_REGISTRY_PATH", str(legacy))
    with pytest.raises(RegistryUnreadableError, match="no registry file"):
        open_registry()
    assert not path.exists()


@pytest.mark.parametrize("document", ['"wrong"', "null", "42", "not json"])
def test_unreadable_files_fail_at_open_without_being_changed(tmp_path: Path, document: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(document)
    with pytest.raises(RegistryUnreadableError):
        open_registry(path)
    assert path.read_text() == document


def test_a_shared_file_must_be_a_dictionary_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("[]")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "file")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_FILE", str(path))
    with pytest.raises(RegistryUnreadableError, match="legacy list snapshot"):
        open_registry()
    assert path.read_text() == "[]"


@pytest.mark.parametrize("backend", ["", "memory", "typo"])
def test_missing_or_unsupported_store_settings_fail_clearly(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", backend)
    monkeypatch.delenv("BLUE_DATA_REGISTRY_PATH", raising=False)
    with pytest.raises(InvalidSettingError, match="BLUE_DATA_REGISTRY_STORE"):
        open_registry()


def test_shared_file_uses_blue_datas_default_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "file")
    monkeypatch.delenv("BLUE_DATA_REGISTRY_FILE", raising=False)
    writer: Any = DataRegistry(store=build_store())
    writer.register_record("shared", "source")
    reopened: Any = open_registry()
    assert reopened.get_record("shared", "source") is not None


def test_redis_uses_the_standard_store_url_and_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = pytest.importorskip("redis")

    entries: dict[str, Any] = {}
    connections: list[dict[str, Any]] = []

    class Transport:
        """Only the Redis transport is replaced; build_store and RedisJSONSource are real."""

        def __init__(self, **connection: Any) -> None:
            connections.append(connection)

        def json(self) -> Transport:
            return self

        def get(self, key: str, path: str = "$") -> Any:
            assert path == "$"
            return [entries[key]] if key in entries else None

        def set(self, key: str, path: str, value: Any) -> None:
            assert path == "$"
            entries[key] = value

        def scan(self, **arguments: Any) -> tuple[int, list[str]]:
            prefix = arguments["match"].removesuffix("*")
            return 0, [key for key in entries if key.startswith(prefix)]

        def close(self) -> None:
            pass

    monkeypatch.setattr(redis, "Redis", Transport)
    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "redis")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_REDIS_URL", "redis://registry.example:6380/4")
    monkeypatch.setenv("BLUE_DATA_REGISTRY_REDIS_NAMESPACE", "planner_test")
    writer: Any = DataRegistry(store=build_store())
    writer.register_record("shared", "source", description="Shared Redis source")
    reopened: Any = open_registry()
    assert reopened.get_record("shared", "source")["description"] == "Shared Redis source"
    assert reopened.list_records(record_type="source")[0]["name"] == "shared"
    assert all(key.startswith("planner_test:") for key in entries)
    assert len(connections) == 2
    assert all(
        c["host"] == "registry.example" and c["port"] == 6380 and c["db"] == 4 for c in connections
    )


def test_redis_backend_failures_are_reported_before_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from blue_data_planner.data import registry as registry_module

    def unavailable() -> Any:
        raise ConnectionError("registry unavailable")

    monkeypatch.setenv("BLUE_DATA_REGISTRY_STORE", "redis")
    monkeypatch.setattr(registry_module, "build_store", unavailable)
    with pytest.raises(RegistryUnreadableError, match="Redis registry"):
        open_registry()
