"""Offline transport, retry, ledger, cache, async, and response-shape tests for LLMClient."""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

import litellm  # the tests/stubs deterministic fake (conftest inserts the stub path)
import pytest
from pydantic import BaseModel

from blue_data_planner.llm import Conversation, LLMClient, LLMError, Turn
from blue_data_planner.types import BlueDataPlannerError, InvalidSettingError

OP_PROPS: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.api": "ChatCompletion",
    "openai.model": "gpt-6-luna",
    "openai.temperature": 0,
    "service_url": "ws://localhost:8001",
    "openai.service_url": "ws://elsewhere:9",
    "input_json": '[{"role": "user"}]',
    "input_context": "$[0]",
    "input_context_field": "content",
    "input_field": "messages",
    "output_path": "$.choices[0].message.content",
}


PROPS: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.model": "gpt-6-luna",
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
    litellm.replies.clear()
    litellm.inflight["now"] = 0
    litellm.inflight["max"] = 0


def test_operator_defaults_reach_the_provider() -> None:
    """The operator's model and temperature settings reach the provider call."""
    client = LLMClient("test", OP_PROPS)
    out = client.execute_api_call("hi")
    assert out == "echo:hi"
    call = litellm.calls[-1]
    assert call["model"] == "gpt-6-luna"  # the op default survives
    assert call["temperature"] == 0
    assert call["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize(
    ("timeout_property", "expected"),
    [({}, 120.0), ({"openai.timeout": 30}, 30)],
    ids=["unset", "set"],
)
def test_every_provider_call_carries_the_timeout_its_properties_set_or_the_default(
    timeout_property: dict[str, Any], expected: float
) -> None:
    """A call never waits on litellm's own 6000-second default (ADR-061)."""
    client = LLMClient("test", PROPS | timeout_property)
    client.execute_api_call("hi")
    assert litellm.calls[-1]["timeout"] == expected


def test_reserved_fields_never_reach_the_provider() -> None:
    """Routing fields such as api and service_url are dropped from the provider call."""
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call("hi")
    call = litellm.calls[-1]
    assert "api" not in call
    assert "service_url" not in call  # root + prefixed spellings both ignored


@pytest.mark.parametrize("key", ["llm.api", "llm.service_url", "llm.bogus"])
def test_an_unknown_llm_override_is_refused_when_the_client_is_built(key: str) -> None:
    """A key outside the accepted llm.* set is a setting error, not a provider failure."""
    with pytest.raises(InvalidSettingError, match="unknown adapter override"):
        LLMClient("test", {**OP_PROPS, key: "x"})


def test_an_unknown_llm_override_passed_with_one_call_is_refused_before_the_provider() -> None:
    """Per-call properties are checked the way the client's own properties are."""
    client = LLMClient("test", OP_PROPS)
    with pytest.raises(InvalidSettingError, match="unknown adapter override"):
        client.execute_api_call("hi", properties={"llm.bogus": "x"})
    assert litellm.calls == []


def test_an_llm_model_override_replaces_the_operator_model() -> None:
    """An accepted llm.model setting replaces the operator's model in the provider call."""
    client = LLMClient("test", {**OP_PROPS, "llm.model": "gpt-4.1"})
    client.execute_api_call("hi")
    assert litellm.calls[-1]["model"] == "gpt-4.1"


def test_a_client_with_no_model_is_refused_when_it_is_built() -> None:
    """Neither <prefix>.model nor llm.model names a model."""
    props = {k: v for k, v in OP_PROPS.items() if k != "openai.model"}
    with pytest.raises(InvalidSettingError, match="no model"):
        LLMClient("test", props)


def test_properties_passed_with_one_call_override_the_clients_own() -> None:
    """A model named in a call's properties replaces the client's model for that call."""
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call("hi", properties={**OP_PROPS, "openai.model": "gpt-4.1"})
    assert litellm.calls[-1]["model"] == "gpt-4.1"


def test_a_call_that_fails_twice_succeeds_on_the_third_attempt() -> None:
    """Two provider errors are retried, and the third attempt's answer is returned."""
    client = LLMClient("test", OP_PROPS)
    litellm.fail_next.extend([RuntimeError("blip"), RuntimeError("blip2")])
    out = client.execute_api_call("hi")  # third attempt succeeds
    assert out.startswith("echo:")
    assert len(litellm.calls) == 3


def test_a_call_that_fails_on_every_attempt_raises_llm_error() -> None:
    """Three provider errors in a row exhaust the retries and raise LLMError."""
    client = LLMClient("test", OP_PROPS)
    litellm.fail_next.extend([RuntimeError("x")] * 3)
    with pytest.raises(LLMError, match="x"):
        client.execute_api_call("hi")


def test_a_provider_failure_is_caught_by_the_package_base_error() -> None:
    """A caller driving the client directly needs one except clause for the whole package."""
    client = LLMClient("test", OP_PROPS)
    litellm.fail_next.extend([RuntimeError("down")] * 3)
    with pytest.raises(BlueDataPlannerError):
        client.execute_api_call("hi")


def test_each_call_adds_one_ledger_record_with_its_model_and_cost() -> None:
    """Two calls leave two ledger records, the last one naming the model and a latency."""
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call("hi")
    client.execute_api_call("hi")
    assert len(client.ledger.records) == 2
    assert client.last_usage is not None
    assert client.last_usage.model == "gpt-6-luna"
    assert client.ledger.total_cost == pytest.approx(0.000246)
    assert client.last_usage.latency_seconds >= 0


def test_a_repeated_prompt_is_served_from_the_cache(tmp_path: Any) -> None:
    """With the cache on, a second identical call returns the first answer with no provider call."""
    props = {**OP_PROPS, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c")}
    client = LLMClient("test", props)
    first = client.execute_api_call("hi")
    second = client.execute_api_call("hi")
    assert first == second
    assert len(litellm.calls) == 1  # the second call was served from the cache


def test_a_cache_requested_without_diskcache_is_refused_when_the_client_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The missing package is reported as a setting error before any call."""
    import builtins

    real_import = builtins.__import__

    def no_diskcache(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "diskcache":
            raise ImportError("gone")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_diskcache)
    with pytest.raises(InvalidSettingError, match="diskcache is not installed"):
        LLMClient("test", {**OP_PROPS, "llm.cache": True})


STRUCTURED_PROPS: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.model": "gpt-6-luna",
    "output_path": "$.choices[0].message.content",
}


class _OneField(BaseModel):
    code: str


def test_a_conversation_keeps_its_turns_and_one_usage_entry_per_call() -> None:
    """Two structured calls on one conversation leave four turns and two usage entries."""
    litellm.replies.extend(['{"code": "result = []"}', '{"code": "result = [{}]"}'])
    client = LLMClient("test", STRUCTURED_PROPS)
    conversation = Conversation(turns=[Turn(role="user", content="write a program")])

    first = client.complete_structured(conversation, _OneField)
    conversation.turns.append(Turn(role="user", content="try again"))
    second = client.complete_structured(conversation, _OneField)

    assert (first.code, second.code) == ("result = []", "result = [{}]")
    assert [turn.role for turn in conversation.turns] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert len(conversation.usage) == 2
    assert conversation.usage[0].model == "gpt-6-luna"


def test_every_turn_so_far_reaches_the_provider() -> None:
    """The second call sends the first call's answer back as context."""
    litellm.replies.extend(['{"code": "a"}', '{"code": "b"}'])
    client = LLMClient("test", STRUCTURED_PROPS)
    conversation = Conversation(turns=[Turn(role="user", content="first")])

    client.complete_structured(conversation, _OneField)
    conversation.turns.append(Turn(role="user", content="second"))
    client.complete_structured(conversation, _OneField)

    assert litellm.calls[-1]["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": '{"code": "a"}'},
        {"role": "user", "content": "second"},
    ]


def test_a_reply_the_response_model_refuses_raises() -> None:
    """A provider answering with something the schema cannot read is a provider failure."""
    litellm.replies.append("not json at all")
    client = LLMClient("test", STRUCTURED_PROPS)
    conversation = Conversation(turns=[Turn(role="user", content="write a program")])

    with pytest.raises(LLMError, match="did not answer in the shape"):
        client.complete_structured(conversation, _OneField)


def test_a_reply_the_response_model_refuses_still_counts_toward_the_conversations_usage() -> None:
    """The provider was paid for the call, so its usage is recorded; the refused reply is not
    added as a turn."""
    litellm.replies.append("not json at all")
    client = LLMClient("test", STRUCTURED_PROPS)
    conversation = Conversation(turns=[Turn(role="user", content="write a program")])

    with pytest.raises(LLMError):
        client.complete_structured(conversation, _OneField)

    assert len(conversation.usage) == 1
    assert [turn.role for turn in conversation.turns] == ["user"]


# --- async surface ---
def test_an_async_call_returns_the_provider_answer() -> None:
    """The async call returns the same answer the synchronous call would."""
    client = LLMClient("test", PROPS)
    out = asyncio.run(client.aexecute_api_call("hi"))
    assert out == "echo:hi"


def test_concurrent_async_calls_never_exceed_max_parallel() -> None:
    """Six concurrent async calls with max_parallel 2 never have more than two in flight."""
    client = LLMClient("test", {**PROPS, "llm.max_parallel": 2})

    async def burst() -> None:
        await asyncio.gather(*(client.aexecute_api_call("hi") for _ in range(6)))

    # All six calls share one prompt; the concurrency cap is what's under test.
    asyncio.run(burst())
    assert len(litellm.calls) == 6
    assert litellm.inflight["max"] <= 2  # the semaphore held the cap


def test_async_calls_run_concurrently_when_max_parallel_is_unset() -> None:
    """Six concurrent async calls with the default setting overlap beyond two in flight."""
    client = LLMClient("test", PROPS)

    async def burst() -> None:
        await asyncio.gather(*(client.aexecute_api_call("hi") for _ in range(6)))

    asyncio.run(burst())
    assert litellm.inflight["max"] > 2  # no artificial serialization by default


# --- ledger semantics (per API call, cache-aware, end-to-end latency) ---
def test_a_cache_hit_is_recorded_as_a_free_zero_token_ledger_record(tmp_path: Any) -> None:
    """The second call is a separate ledger record marked as a cache hit with no cost or tokens."""
    props = {**PROPS, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c")}
    client = LLMClient("test", props)
    client.execute_api_call("hi")
    client.execute_api_call("hi")
    assert len(client.ledger.records) == 2
    first, second = client.ledger.records
    assert first.cache_hit is False and second.cache_hit is True
    assert second.cost == 0.0 and second.prompt_tokens == 0
    assert len(litellm.calls) == 1


def test_a_retried_call_records_latency_including_the_backoff_waits() -> None:
    """The ledger latency of a call that needed two retries includes the waits between attempts."""
    client = LLMClient("test", PROPS)
    litellm.fail_next.extend([RuntimeError("a"), RuntimeError("b")])
    client.execute_api_call("hi")
    record = client.ledger.records[-1]
    assert record.latency_seconds >= 0.1  # includes the two backoff waits


# --- per-call cache_dir isolation ---
def test_calls_naming_different_cache_dirs_do_not_share_cached_answers(tmp_path: Any) -> None:
    """A second call with another cache_dir misses the first call's entry."""
    client = LLMClient("test", {**PROPS, "llm.cache": True})
    dir_a, dir_b = str(tmp_path / "a"), str(tmp_path / "b")
    client.execute_api_call("hi", properties={**PROPS, "llm.cache": True, "llm.cache_dir": dir_a})
    client.execute_api_call("hi", properties={**PROPS, "llm.cache": True, "llm.cache_dir": dir_b})
    assert len(litellm.calls) == 2  # B did NOT read A's entry
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
def test_a_response_not_shaped_like_openai_raises_llm_error(
    raw: Any, match: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider response with no choices list or that is not a dict raises LLMError."""
    client = LLMClient("test", PROPS)

    def fake(**kw: Any) -> Any:
        return raw

    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(LLMError, match=match):
        client.execute_api_call("hi")


def test_a_response_that_cannot_be_materialized_raises_llm_error_from_the_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response whose model_dump raises becomes an LLMError chained to that error."""
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


def test_a_response_of_the_wrong_shape_raises_llm_error_with_no_underlying_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shape failure is plain validation, so the LLMError has no chained exception."""

    def fake(**kw: Any) -> Any:
        return {}

    monkeypatch.setattr(litellm, "completion", fake)
    client = LLMClient("test", PROPS)
    with pytest.raises(LLMError) as excinfo:
        client.execute_api_call("hi")
    assert excinfo.value.__cause__ is None  # pure validation: no underlying exception


# --- prefix fallback (client name) ---
def test_the_client_name_supplies_the_prefix_when_none_is_set() -> None:
    """With no service_prefix, the lowercase client name selects the provider settings."""
    props = {k: v for k, v in PROPS.items() if k != "service_prefix"}
    client = LLMClient("openai", props)  # prefix falls back to the lowercase client name
    out = client.execute_api_call("hi")
    assert out == "echo:hi"
    assert litellm.calls[-1]["model"] == "gpt-6-luna"


def test_an_explicit_service_prefix_wins_over_the_client_name() -> None:
    """A service_prefix setting is used even when the client name differs."""
    props = {**PROPS, "service_prefix": "openai"}
    client = LLMClient("unrelated_name", props)
    client.execute_api_call("hi")
    assert litellm.calls[-1]["model"] == "gpt-6-luna"
