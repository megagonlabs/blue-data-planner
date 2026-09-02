"""The resolver contract matrix: injected -> provider -> fresh construction;
construct-per-call default (no module cache); owner-scoped opt-in caching."""

from __future__ import annotations

from typing import Any

from blue_data_planner.llm import (
    CachingLLMClientProvider,
    LLMClient,
    LLMClientProvider,
    resolve_llm_client,
)

PROPS: dict[str, Any] = {"service_prefix": "openai", "openai.model": "gpt-4o"}


def test_injected_client_identity() -> None:
    client = LLMClient("mine", PROPS)
    assert resolve_llm_client({**PROPS, "llm_client": client}) is client


def test_injected_junk_falls_through() -> None:
    resolved = resolve_llm_client({**PROPS, "llm_client": "not-a-client"})
    assert isinstance(resolved, LLMClient)


def test_default_constructs_fresh_per_call() -> None:
    # Default resolution constructs per invocation; it does not use a module cache.
    first = resolve_llm_client(dict(PROPS))
    second = resolve_llm_client(dict(PROPS))
    assert first is not second


def test_none_properties_construct() -> None:
    assert isinstance(resolve_llm_client(None), LLMClient)


def test_custom_provider_is_dispatched() -> None:
    class _Custom(LLMClientProvider):
        def __init__(self) -> None:
            self.seen: list[dict[str, Any]] = []
            self.client = LLMClient("custom", PROPS)

        def get_client(self, properties: dict[str, Any]) -> LLMClient:
            self.seen.append(properties)
            return self.client

    provider = _Custom()
    resolved = resolve_llm_client(dict(PROPS), provider=provider)
    assert resolved is provider.client
    assert provider.seen == [PROPS]


def test_caching_provider_same_config_identity() -> None:
    provider = CachingLLMClientProvider()
    first = provider.get_client(dict(PROPS))
    second = provider.get_client(dict(PROPS))
    assert first is second


def test_caching_provider_different_config_miss() -> None:
    provider = CachingLLMClientProvider()
    first = provider.get_client(dict(PROPS))
    second = provider.get_client({**PROPS, "openai.model": "gpt-4o-mini"})
    assert first is not second


def test_caching_provider_non_serializable_uncached() -> None:
    provider = CachingLLMClientProvider()
    props = {**PROPS, "llm.max_parallel": object()}
    first = provider.get_client(props)
    second = provider.get_client(props)
    assert first is not second  # constructed uncached, never an error


def test_concurrent_resolution_smoke() -> None:
    import threading

    provider = CachingLLMClientProvider()
    results: list[LLMClient] = []

    def resolve() -> None:
        results.append(provider.get_client(dict(PROPS)))

    threads = [threading.Thread(target=resolve) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(c) for c in results}) <= 2  # benign race: at most a duplicate construct


def test_two_owners_do_not_share_clients() -> None:
    a, b = CachingLLMClientProvider(), CachingLLMClientProvider()
    assert a.get_client(dict(PROPS)) is not b.get_client(dict(PROPS))
