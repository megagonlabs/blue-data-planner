"""Async, concurrency, rate-limit, ledger, and provider-response tests."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import litellm  # the deterministic stub
import pytest

from blue_data_planner.llm import LLMClient, LLMError

PROPS: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.model": "gpt-4o",
    "input_template": "Say $input",
    "input_json": '[{"role": "user"}]',
    "input_context": "$[0]",
    "input_context_field": "content",
    "input_field": "messages",
    "output_path": "$.choices[0].message.content",
}


@pytest.fixture(autouse=True)
def _reset_fake() -> None:  # pyright: ignore[reportUnusedFunction] — autouse
    litellm.calls.clear()
    litellm.fail_next.clear()
    litellm.inflight["now"] = 0
    litellm.inflight["max"] = 0


# --- async surface ---
def test_aexecute_api_call_works() -> None:
    client = LLMClient("test", PROPS)
    out = asyncio.run(client.aexecute_api_call({}, additional_data={}))
    assert out == "echo:Say {}"


def test_max_parallel_caps_concurrency() -> None:
    client = LLMClient("test", {**PROPS, "llm.max_parallel": 2})

    async def burst() -> None:
        await asyncio.gather(
            *(client.aexecute_api_call({}, additional_data={"i": str(i)}) for i in range(6))
        )

    # All six calls share one prompt; the concurrency cap is what's under test.
    asyncio.run(burst())
    assert len(litellm.calls) == 6
    assert litellm.inflight["max"] <= 2  # the semaphore held the cap


def test_default_parallelism_exceeds_two() -> None:
    client = LLMClient("test", PROPS)

    async def burst() -> None:
        await asyncio.gather(*(client.aexecute_api_call({}, additional_data={}) for _ in range(6)))

    asyncio.run(burst())
    assert litellm.inflight["max"] > 2  # no artificial serialization by default


# --- rate limit ---
def test_rate_limit_spaces_calls() -> None:
    client = LLMClient("test", {**PROPS, "llm.rate_limit": 20})  # >= 50ms apart
    started = time.monotonic()
    client.execute_api_call({}, additional_data={})
    client.execute_api_call({}, additional_data={})
    client.execute_api_call({}, additional_data={})
    elapsed = time.monotonic() - started
    assert elapsed >= 0.1  # two enforced gaps of ~50ms


# --- ledger semantics (per API call, cache-aware, end-to-end latency) ---
def test_cache_hit_gets_its_own_record(tmp_path: Any) -> None:
    props = {**PROPS, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c")}
    client = LLMClient("test", props)
    client.execute_api_call({}, additional_data={})
    client.execute_api_call({}, additional_data={})
    assert len(client.ledger.records) == 2
    first, second = client.ledger.records
    assert first.cache_hit is False and second.cache_hit is True
    assert second.cost == 0.0 and second.prompt_tokens == 0
    assert len(litellm.calls) == 1


def test_retry_latency_is_end_to_end() -> None:
    client = LLMClient("test", PROPS)
    litellm.fail_next.extend([RuntimeError("a"), RuntimeError("b")])
    client.execute_api_call({}, additional_data={})
    record = client.ledger.records[-1]
    assert record.latency_seconds >= 0.1  # includes the two backoff waits


# --- per-call cache_dir isolation ---
def test_cache_dir_per_call_isolation(tmp_path: Any) -> None:
    client = LLMClient("test", {**PROPS, "llm.cache": True})
    dir_a, dir_b = str(tmp_path / "a"), str(tmp_path / "b")
    client.execute_api_call(
        {}, properties={**PROPS, "llm.cache": True, "llm.cache_dir": dir_a}, additional_data={}
    )
    client.execute_api_call(
        {}, properties={**PROPS, "llm.cache": True, "llm.cache_dir": dir_b}, additional_data={}
    )
    assert len(litellm.calls) == 2  # B did NOT read A's entry
    import pathlib

    assert pathlib.Path(dir_a).exists() and pathlib.Path(dir_b).exists()


# --- provider-response boundary (wrapped, chained) ---
class _NoDump:
    pass


class _BoomDump:
    def model_dump(self) -> dict[str, Any]:
        raise RuntimeError("dump broke")


@pytest.mark.parametrize(
    "raw,match",
    [
        ({}, "non-OpenAI-shaped"),  # empty dict: no choices list
        ({"choices": "not-a-list"}, "non-OpenAI-shaped"),
        (_NoDump(), "non-OpenAI-shaped"),  # non-dict materialization
    ],
    ids=["empty-dict", "choices-not-list", "non-dict"],
)
def test_malformed_responses_wrapped(raw: Any, match: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient("test", PROPS)

    def fake(**kw: Any) -> Any:
        return raw

    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(LLMError, match=match):
        client.execute_api_call({}, additional_data={})


def test_model_dump_failure_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient("test", PROPS)

    def fake(**kw: Any) -> Any:
        return _BoomDump()

    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(LLMError, match="could not be materialized") as excinfo:
        client.execute_api_call({}, additional_data={})
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_dict_shaped_junk_usage_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    # Dict-shaped usage with junk field values must count as zero, never raise.
    payload = {"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": "junk"}}

    def fake(**kw: Any) -> Any:
        return payload

    monkeypatch.setattr(litellm, "completion", fake)
    client = LLMClient("test", PROPS)
    assert client.execute_api_call({}, additional_data={}) == "x"
    assert client.ledger.records[-1].prompt_tokens == 0


@pytest.mark.parametrize(
    "junk",
    ["junk", float("inf"), float("nan"), object(), [1]],
    ids=["str", "inf", "nan", "obj", "list"],
)
def test_any_junk_usage_value_counts_as_zero(junk: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": junk}}

    def fake(**kw: Any) -> Any:
        return payload

    monkeypatch.setattr(litellm, "completion", fake)
    client = LLMClient("test", PROPS)
    assert client.execute_api_call({}, additional_data={}) == "x"
    assert client.ledger.records[-1].prompt_tokens == 0


def test_shape_failures_have_no_cause_by_design(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(**kw: Any) -> Any:
        return {}

    monkeypatch.setattr(litellm, "completion", fake)
    client = LLMClient("test", PROPS)
    with pytest.raises(LLMError) as excinfo:
        client.execute_api_call({}, additional_data={})
    assert excinfo.value.__cause__ is None  # pure validation: no underlying exception


def test_malformed_usage_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"choices": [{"message": {"content": "x"}}], "usage": "bad"}

    def fake(**kw: Any) -> Any:
        return payload

    monkeypatch.setattr(litellm, "completion", fake)
    client = LLMClient("test", PROPS)
    out = client.execute_api_call({}, additional_data={})  # non-dict usage: zeros, no raise
    assert out == "x"
    assert client.ledger.records[-1].prompt_tokens == 0


# --- prefix fallback (client name) ---
def test_client_name_prefix_fallback() -> None:
    props = {k: v for k, v in PROPS.items() if k != "service_prefix"}
    client = LLMClient("openai", props)  # prefix falls back to the lowercase client name
    out = client.execute_api_call({}, additional_data={})
    assert out == "echo:Say {}"
    assert litellm.calls[-1]["model"] == "gpt-4o"


def test_explicit_prefix_wins_over_name() -> None:
    props = {**PROPS, "service_prefix": "openai"}
    client = LLMClient("unrelated_name", props)
    client.execute_api_call({}, additional_data={})
    assert litellm.calls[-1]["model"] == "gpt-4o"
