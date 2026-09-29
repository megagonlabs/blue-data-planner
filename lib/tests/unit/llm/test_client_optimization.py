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
    out = asyncio.run(client.aexecute_api_call("hi"))
    assert out == "echo:hi"


def test_max_parallel_caps_concurrency() -> None:
    client = LLMClient("test", {**PROPS, "llm.max_parallel": 2})

    async def burst() -> None:
        await asyncio.gather(*(client.aexecute_api_call("hi") for _ in range(6)))

    # All six calls share one prompt; the concurrency cap is what's under test.
    asyncio.run(burst())
    assert len(litellm.calls) == 6
    assert litellm.inflight["max"] <= 2  # the semaphore held the cap


def test_default_parallelism_exceeds_two() -> None:
    client = LLMClient("test", PROPS)

    async def burst() -> None:
        await asyncio.gather(*(client.aexecute_api_call("hi") for _ in range(6)))

    asyncio.run(burst())
    assert litellm.inflight["max"] > 2  # no artificial serialization by default


# --- rate limit ---
def test_rate_limit_spaces_calls() -> None:
    client = LLMClient("test", {**PROPS, "llm.rate_limit": 20})  # >= 50ms apart
    started = time.monotonic()
    client.execute_api_call("hi")
    client.execute_api_call("hi")
    client.execute_api_call("hi")
    elapsed = time.monotonic() - started
    assert elapsed >= 0.1  # two enforced gaps of ~50ms


# --- ledger semantics (per API call, cache-aware, end-to-end latency) ---
def test_cache_hit_gets_its_own_record(tmp_path: Any) -> None:
    props = {**PROPS, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c")}
    client = LLMClient("test", props)
    client.execute_api_call("hi")
    client.execute_api_call("hi")
    assert len(client.ledger.records) == 2
    first, second = client.ledger.records
    assert first.cache_hit is False and second.cache_hit is True
    assert second.cost == 0.0 and second.prompt_tokens == 0
    assert len(litellm.calls) == 1


def test_retry_latency_is_end_to_end() -> None:
    client = LLMClient("test", PROPS)
    litellm.fail_next.extend([RuntimeError("a"), RuntimeError("b")])
    client.execute_api_call("hi")
    record = client.ledger.records[-1]
    assert record.latency_seconds >= 0.1  # includes the two backoff waits


# --- per-call cache_dir isolation ---
def test_cache_dir_per_call_isolation(tmp_path: Any) -> None:
    client = LLMClient("test", {**PROPS, "llm.cache": True})
    dir_a, dir_b = str(tmp_path / "a"), str(tmp_path / "b")
    client.execute_api_call("hi", properties={**PROPS, "llm.cache": True, "llm.cache_dir": dir_a})
    client.execute_api_call("hi", properties={**PROPS, "llm.cache": True, "llm.cache_dir": dir_b})
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
        client.execute_api_call("hi")


def test_model_dump_failure_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient("test", PROPS)

    def fake(**kw: Any) -> Any:
        return _BoomDump()

    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(LLMError, match="could not be materialized") as excinfo:
        client.execute_api_call("hi")
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def a_client_answered_with(monkeypatch: pytest.MonkeyPatch, usage: Any) -> LLMClient:
    """A client whose provider answers "x" with the usage block given, or none for _MISSING."""
    payload: dict[str, Any] = {"choices": [{"message": {"content": "x"}}]}
    if usage is not _MISSING:
        payload["usage"] = usage

    def fake(**kw: Any) -> Any:
        return payload

    monkeypatch.setattr(litellm, "completion", fake)
    return LLMClient("test", PROPS)


_MISSING = object()


@pytest.mark.parametrize(
    "usage",
    [
        _MISSING,
        "bad",
        {"prompt_tokens": "junk"},
        {"prompt_tokens": float("inf")},
        {"prompt_tokens": float("nan")},
        {"prompt_tokens": object()},
        {"prompt_tokens": [1]},
        {"prompt_tokens": -1},
        {"prompt_tokens": True},
    ],
    ids=["missing", "not-a-mapping", "str", "inf", "nan", "obj", "list", "negative", "bool"],
)
def test_a_response_whose_usage_is_no_token_count_fails_the_call(
    usage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A usage block that is absent or malformed is a malformed response, not zero tokens."""
    client = a_client_answered_with(monkeypatch, usage)

    with pytest.raises(LLMError, match="usage|token count"):
        client.execute_api_call("hi")


def test_a_token_count_the_provider_leaves_out_is_recorded_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A count the provider did not send is unknown, so it is recorded as None rather than 0."""
    client = a_client_answered_with(monkeypatch, {"completion_tokens": 3})

    assert client.execute_api_call("hi") == "x"
    assert client.ledger.records[-1].prompt_tokens is None
    assert client.ledger.total_tokens is None


def test_a_model_with_no_known_price_has_an_unknown_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call succeeds, and its cost, and so the ledger's total, are recorded as unknown."""

    def no_price(completion_response: Any) -> float:
        raise Exception("This model isn't mapped yet.")  # litellm raises a bare Exception

    monkeypatch.setattr(litellm, "completion_cost", no_price)
    client = LLMClient("test", PROPS)

    assert client.execute_api_call("hi") == "echo:hi"
    assert client.ledger.records[-1].cost is None
    assert client.ledger.total_cost is None


def test_shape_failures_have_no_cause_by_design(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(**kw: Any) -> Any:
        return {}

    monkeypatch.setattr(litellm, "completion", fake)
    client = LLMClient("test", PROPS)
    with pytest.raises(LLMError) as excinfo:
        client.execute_api_call("hi")
    assert excinfo.value.__cause__ is None  # pure validation: no underlying exception


# --- prefix fallback (client name) ---
def test_client_name_prefix_fallback() -> None:
    props = {k: v for k, v in PROPS.items() if k != "service_prefix"}
    client = LLMClient("openai", props)  # prefix falls back to the lowercase client name
    out = client.execute_api_call("hi")
    assert out == "echo:hi"
    assert litellm.calls[-1]["model"] == "gpt-4o"


def test_explicit_prefix_wins_over_name() -> None:
    props = {**PROPS, "service_prefix": "openai"}
    client = LLMClient("unrelated_name", props)
    client.execute_api_call("hi")
    assert litellm.calls[-1]["model"] == "gpt-4o"
