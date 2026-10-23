"""The response cache must not deserialize objects or trust caller-selected storage."""

from __future__ import annotations

import asyncio
import json
import os
import pickle
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import litellm
import pytest

from blue_data_planner.llm import LLMClient
from blue_data_planner.llm import cache as cache_module
from blue_data_planner.llm.cache import JSONResponseCache
from blue_data_planner.types import InvalidSettingError
from tests.unit.llm.test_client import PROPS


@pytest.fixture(autouse=True)
def reset_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """All model responses are local stubs; no user cache or provider is contacted."""
    monkeypatch.delenv("BLUE_DATA_PLANNER_LLM_CACHE_DIR", raising=False)
    litellm.calls.clear()
    litellm.fail_next.clear()
    litellm.replies.clear()


def a_client(directory: Path) -> LLMClient:
    return LLMClient("test", {**PROPS, "llm.cache": True}, cache_dir=directory)


def test_json_cache_is_private_and_a_new_client_can_use_its_response(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    assert a_client(directory).execute_api_call("hi") == "echo:hi"
    (entry,) = directory.glob("*.json")
    document = json.loads(entry.read_text())
    assert document["format"] == "blue-data-planner.llm-response.v1"
    assert isinstance(document["response"], dict)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(entry.stat().st_mode) == 0o600
    assert a_client(directory).execute_api_call("hi") == "echo:hi"
    assert len(litellm.calls) == 1
    assert not (directory / "cache.db").exists()


def test_async_calls_keep_cache_hits_and_zero_cost_accounting(tmp_path: Path) -> None:
    client = a_client(tmp_path / "cache")
    assert asyncio.run(client.aexecute_api_call("hi")) == "echo:hi"
    assert asyncio.run(client.aexecute_api_call("hi")) == "echo:hi"
    assert len(litellm.calls) == 1
    assert client.ledger.records[-1].cache_hit
    assert client.ledger.records[-1].cost == client.ledger.records[-1].prompt_tokens == 0


def test_non_json_provider_metadata_does_not_break_a_successful_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def complete(**kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "metadata": object(),
        }

    monkeypatch.setattr(litellm, "completion", complete)
    client = a_client(tmp_path / "cache")
    assert client.execute_api_call("hi") == "answer"
    assert list((tmp_path / "cache").iterdir()) == []
    assert not client.ledger.records[-1].cache_hit


@pytest.mark.parametrize("asynchronous", [False, True])
def test_call_properties_cannot_redirect_storage(tmp_path: Path, asynchronous: bool) -> None:
    client = a_client(tmp_path / "deployment")
    override = {"llm.cache_dir": str(tmp_path / "caller")}
    with pytest.raises(InvalidSettingError, match="llm.cache_dir"):
        if asynchronous:
            asyncio.run(client.aexecute_api_call("hi", properties=override))
        else:
            client.execute_api_call("hi", properties=override)
    assert litellm.calls == []
    assert not (tmp_path / "caller").exists()


def test_old_directory_property_is_also_refused_at_construction(tmp_path: Path) -> None:
    with pytest.raises(InvalidSettingError, match="llm.cache_dir"):
        LLMClient("test", {**PROPS, "llm.cache_dir": str(tmp_path / "old")})
    assert not (tmp_path / "old").exists()


def test_default_cache_is_planner_specific_and_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / ".llm_cache"
    legacy.mkdir()
    (legacy / "cache.db").write_bytes(b"legacy pickle-backed cache; do not read")
    client = LLMClient("test", {**PROPS, "llm.cache": True})
    assert client.execute_api_call("hi") == "echo:hi"
    assert list((tmp_path / ".cache" / "blue-data-planner" / "llm-v1").glob("*.json"))
    assert (legacy / "cache.db").read_bytes() == b"legacy pickle-backed cache; do not read"


def test_deployment_location_is_fixed_when_the_client_is_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLUE_DATA_PLANNER_LLM_CACHE_DIR", str(tmp_path / "first"))
    client = LLMClient("test", PROPS)
    monkeypatch.setenv("BLUE_DATA_PLANNER_LLM_CACHE_DIR", str(tmp_path / "second"))
    client.execute_api_call("hi", properties={"llm.cache": True})
    assert (tmp_path / "first").is_dir()
    assert not (tmp_path / "second").exists()


def test_constructor_location_overrides_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLUE_DATA_PLANNER_LLM_CACHE_DIR", str(tmp_path / "environment"))
    a_client(tmp_path / "constructor").execute_api_call("hi")
    assert not (tmp_path / "environment").exists()


def test_a_disabled_cache_creates_no_directory(tmp_path: Path) -> None:
    client = LLMClient("test", PROPS, cache_dir=tmp_path / "unused")
    client.execute_api_call("hi")
    assert not (tmp_path / "unused").exists()


def test_a_relative_deployment_directory_is_refused() -> None:
    with pytest.raises(InvalidSettingError, match="absolute"):
        a_client(Path(".llm_cache"))


@pytest.mark.parametrize("permissions", [0o755, 0o770, 0o777])
def test_an_existing_shared_directory_is_refused(tmp_path: Path, permissions: int) -> None:
    directory = tmp_path / "shared"
    directory.mkdir()
    directory.chmod(permissions)
    with pytest.raises(InvalidSettingError, match="0700"):
        a_client(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == permissions
    assert litellm.calls == []


def test_a_directory_with_another_owner_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "other_owner"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(os, "geteuid", lambda: directory.stat().st_uid + 1)
    with pytest.raises(InvalidSettingError, match="owned by the current user"):
        a_client(directory)


def test_a_symlink_directory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(InvalidSettingError, match="cache directory"):
        a_client(link)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("operation", ["get", "set"])
def test_directory_permissions_are_rechecked_for_every_operation(
    tmp_path: Path, operation: str
) -> None:
    cache = JSONResponseCache(tmp_path / "cache")
    cache.directory.chmod(0o755)
    with pytest.raises(InvalidSettingError, match="0700"):
        if operation == "get":
            cache.get("key")
        else:
            cache.set("key", {"answer": "value"})


class PickleMarker:
    """A harmless local marker proving whether a cache ever executes pickle instructions."""

    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self) -> tuple[Callable[..., Any], tuple[Path]]:
        return Path.touch, (self.marker,)


def test_pickle_bytes_are_a_cache_miss_and_never_execute(tmp_path: Path) -> None:
    directory = tmp_path / "cache"
    client = a_client(directory)
    client.execute_api_call("hi")
    (entry,) = directory.glob("*.json")
    marker = tmp_path / "pickle_executed"
    entry.write_bytes(pickle.dumps(PickleMarker(marker)))
    assert client.execute_api_call("hi") == "echo:hi"
    assert not marker.exists()
    assert len(litellm.calls) == 2
    assert isinstance(json.loads(entry.read_text()), dict)


@pytest.mark.parametrize(
    "corruption",
    ["invalid-json", "wrong-format", "invalid-payload", "invalid-usage"],
)
def test_corrupt_or_foreign_entries_are_replaced_by_fresh_responses(
    tmp_path: Path, corruption: str
) -> None:
    directory = tmp_path / "cache"
    client = a_client(directory)
    client.execute_api_call("hi")
    (entry,) = directory.glob("*.json")
    document = json.loads(entry.read_text())
    if corruption == "invalid-json":
        entry.write_text("not JSON")
    else:
        if corruption == "wrong-format":
            document["format"] = "another-client.llm-response.v1"
        elif corruption == "invalid-payload":
            document["response"] = {"content": "wrong"}
        else:
            document["response"]["usage"] = {"prompt_tokens": "wrong"}
        entry.write_text(json.dumps(document))
    assert client.execute_api_call("hi") == "echo:hi"
    assert len(litellm.calls) == 2


@pytest.mark.parametrize("linked", ["symlink", "hardlink", "fifo", "shared-file"])
def test_unsafe_entries_are_not_read(tmp_path: Path, linked: str) -> None:
    cache = JSONResponseCache(tmp_path / "cache")
    cache.set("key", {"value": "private"})
    (entry,) = cache.directory.glob("*.json")
    original = entry.read_bytes()
    outside = tmp_path / "outside"
    outside.write_bytes(original)
    if linked == "shared-file":
        entry.chmod(0o666)
    else:
        entry.unlink()
        if linked == "symlink":
            entry.symlink_to(outside)
        elif linked == "hardlink":
            os.link(outside, entry)
        else:
            os.mkfifo(entry, 0o600)
    assert cache.get("key") is None
    cache.set("key", {"value": "fresh"})
    assert cache.get("key") == {"value": "fresh"}
    assert outside.read_bytes() == original


def test_concurrent_writers_publish_complete_json_files(tmp_path: Path) -> None:
    cache = JSONResponseCache(tmp_path / "cache")
    cache.set("key", {"value": 0})

    def write_and_read(value: int) -> None:
        cache.set("key", {"value": value, "rows": [value] * 1000})
        response = cache.get("key")
        assert response is not None and isinstance(response["value"], int)

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(write_and_read, range(32)))
    assert len(list(cache.directory.iterdir())) == 1


def test_cache_growth_retires_older_entries_without_removing_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, "_MAX_CACHE_BYTES", 256)
    cache = JSONResponseCache(tmp_path / "cache")
    unrelated = cache.directory / "deployment-note.txt"
    unrelated.write_text("keep")
    foreign = cache.directory / f"{'f' * 64}.json"
    foreign.write_text("foreign")
    cache.set("older", {"value": "a" * 100})
    cache.set("newer", {"value": "b" * 100})

    assert cache.get("older") is None
    assert cache.get("newer") == {"value": "b" * 100}
    assert sum(entry.stat().st_size for entry in cache.directory.glob("*.json")) <= 256
    assert unrelated.read_text() == "keep"
    assert foreign.read_text() == "foreign"


def test_oversized_responses_are_returned_without_being_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, "_MAX_CACHE_BYTES", 64)
    client = a_client(tmp_path / "cache")
    assert client.execute_api_call("hi") == "echo:hi"
    assert list((tmp_path / "cache").iterdir()) == []
