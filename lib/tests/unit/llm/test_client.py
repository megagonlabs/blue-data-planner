"""Offline transport, retry, usage-ledger, and cache tests for LLMClient."""

from __future__ import annotations

from typing import Any

import litellm  # the tests/stubs deterministic fake (conftest inserts the stub path)
import pytest

from blue_data_planner.llm import LLMClient, LLMError

OP_PROPS: dict[str, Any] = {
    "service_prefix": "openai",
    "openai.api": "ChatCompletion",
    "openai.model": "gpt-4o",
    "openai.temperature": 0,
    "service_url": "ws://localhost:8001",
    "openai.service_url": "ws://elsewhere:9",
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


def test_operator_defaults_reach_the_provider() -> None:
    client = LLMClient("test", OP_PROPS)
    out = client.execute_api_call({}, additional_data={})
    assert out == "echo:Say {}"
    call = litellm.calls[-1]
    assert call["model"] == "gpt-4o"  # the op default survives
    assert call["temperature"] == 0
    assert call["messages"] == [{"role": "user", "content": "Say {}"}]


def test_reserved_fields_never_reach_the_provider() -> None:
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call({}, additional_data={})
    call = litellm.calls[-1]
    assert "api" not in call
    assert "service_url" not in call  # root + prefixed spellings both ignored


@pytest.mark.parametrize("key", ["llm.api", "llm.service_url", "llm.bogus"])
def test_unknown_llm_overrides_rejected(key: str) -> None:
    client = LLMClient("test", {**OP_PROPS, key: "x"})
    with pytest.raises(LLMError, match="unknown adapter override"):
        client.execute_api_call({}, additional_data={})
    assert litellm.calls == []  # rejected before any provider call


def test_accepted_llm_override_wins() -> None:
    client = LLMClient("test", {**OP_PROPS, "llm.model": "gpt-4o-mini"})
    client.execute_api_call({}, additional_data={})
    assert litellm.calls[-1]["model"] == "gpt-4o-mini"


def test_no_model_is_an_error() -> None:
    props = {k: v for k, v in OP_PROPS.items() if k != "openai.model"}
    client = LLMClient("test", props)
    with pytest.raises(LLMError, match="no model"):
        client.execute_api_call({}, additional_data={})


def test_per_call_properties_override_instance() -> None:
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call(
        {}, properties={**OP_PROPS, "openai.model": "gpt-x"}, additional_data={}
    )
    assert litellm.calls[-1]["model"] == "gpt-x"


def test_provider_error_wrapped_and_retried() -> None:
    client = LLMClient("test", OP_PROPS)
    litellm.fail_next.extend([RuntimeError("blip"), RuntimeError("blip2")])
    out = client.execute_api_call({}, additional_data={})  # third attempt succeeds
    assert out.startswith("echo:")
    assert len(litellm.calls) == 3


def test_provider_error_exhausts_retries() -> None:
    client = LLMClient("test", OP_PROPS)
    litellm.fail_next.extend([RuntimeError("x")] * 3)
    with pytest.raises(LLMError, match="x"):
        client.execute_api_call({}, additional_data={})


def test_usage_ledger_accumulates() -> None:
    client = LLMClient("test", OP_PROPS)
    client.execute_api_call({}, additional_data={})
    client.execute_api_call({}, additional_data={})
    assert len(client.ledger.records) == 2
    assert client.last_usage is not None
    assert client.last_usage.model == "gpt-4o"
    assert client.ledger.total_cost == pytest.approx(0.000246)
    assert client.last_usage.latency_seconds >= 0


def test_cache_roundtrip(tmp_path: Any) -> None:
    props = {**OP_PROPS, "llm.cache": True, "llm.cache_dir": str(tmp_path / "c")}
    client = LLMClient("test", props)
    first = client.execute_api_call({}, additional_data={})
    second = client.execute_api_call({}, additional_data={})
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
        client.execute_api_call({}, additional_data={})
