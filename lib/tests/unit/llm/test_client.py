"""Offline transport, retry, usage-ledger, and cache tests for LLMClient."""

from __future__ import annotations

from typing import Any

import litellm  # the tests/stubs deterministic fake (conftest inserts the stub path)
import pytest
from pydantic import BaseModel

from blue_data_planner.llm import Conversation, LLMClient, LLMError, Turn

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
def test_unknown_llm_overrides_rejected(key: str) -> None:
    client = LLMClient("test", {**OP_PROPS, key: "x"})
    with pytest.raises(LLMError, match="unknown adapter override"):
        client.execute_api_call("hi")
    assert litellm.calls == []  # rejected before any provider call


def test_accepted_llm_override_wins() -> None:
    client = LLMClient("test", {**OP_PROPS, "llm.model": "gpt-4o-mini"})
    client.execute_api_call("hi")
    assert litellm.calls[-1]["model"] == "gpt-4o-mini"


def test_no_model_is_an_error() -> None:
    props = {k: v for k, v in OP_PROPS.items() if k != "openai.model"}
    client = LLMClient("test", props)
    with pytest.raises(LLMError, match="no model"):
        client.execute_api_call("hi")


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


def test_cache_requested_without_diskcache_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def no_diskcache(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "diskcache":
            raise ImportError("gone")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_diskcache)
    client = LLMClient("test", {**OP_PROPS, "llm.cache": True})
    with pytest.raises(LLMError, match="diskcache is not installed"):
        client.execute_api_call("hi")


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
