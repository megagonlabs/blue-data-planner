"""Offline transport, retry, usage-ledger, and cache tests for LLMClient."""

from __future__ import annotations

from typing import Any

import litellm  # the tests/stubs deterministic fake (conftest inserts the stub path)
import pytest
from pydantic import BaseModel

from blue_data_planner.llm import Conversation, LLMClient, LLMError, Turn
from blue_data_planner.types import BlueDataPlannerError, InvalidSettingError

OP_PROPS: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.api": "ChatCompletion",
    "openai.model": "gpt-4o",
    "openai.temperature": 0,
    "service_url": "ws://localhost:8001",
    "openai.service_url": "ws://elsewhere:9",
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


def test_operator_defaults_reach_the_provider() -> None:
    client = LLMClient("test", OP_PROPS)
    out = client.execute_api_call("hi")
    assert out == "echo:hi"
    call = litellm.calls[-1]
    assert call["model"] == "gpt-4o"  # the op default survives
    assert call["temperature"] == 0
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_reserved_fields_never_reach_the_provider() -> None:
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


def test_accepted_llm_override_wins() -> None:
    client = LLMClient("test", {**OP_PROPS, "llm.model": "gpt-4o-mini"})
    client.execute_api_call("hi")
    assert litellm.calls[-1]["model"] == "gpt-4o-mini"


def test_a_client_with_no_model_is_refused_when_it_is_built() -> None:
    """Neither <prefix>.model nor llm.model names a model."""
    props = {k: v for k, v in OP_PROPS.items() if k != "openai.model"}
    with pytest.raises(InvalidSettingError, match="no model"):
        LLMClient("test", props)


def test_per_call_properties_override_instance() -> None:
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call("hi", properties={**OP_PROPS, "openai.model": "gpt-x"})
    assert litellm.calls[-1]["model"] == "gpt-x"


def test_provider_error_wrapped_and_retried() -> None:
    client = LLMClient("test", OP_PROPS)
    litellm.fail_next.extend([RuntimeError("blip"), RuntimeError("blip2")])
    out = client.execute_api_call("hi")  # third attempt succeeds
    assert out.startswith("echo:")
    assert len(litellm.calls) == 3


def test_provider_error_exhausts_retries() -> None:
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


def test_usage_ledger_accumulates() -> None:
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call("hi")
    client.execute_api_call("hi")
    assert len(client.ledger.records) == 2
    assert client.last_usage is not None
    assert client.last_usage.model == "gpt-4o"
    assert client.ledger.total_cost == pytest.approx(0.000246)
    assert client.last_usage.latency_seconds >= 0


def test_cache_roundtrip(tmp_path: Any) -> None:
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
    "openai.model": "gpt-4o",
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
    assert conversation.usage[0].model == "gpt-4o"


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
