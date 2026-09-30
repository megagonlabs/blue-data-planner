"""The resolver contract matrix: injected -> provider -> fresh construction;
construct-per-call default (no module cache); owner-scoped opt-in caching."""

from __future__ import annotations

from typing import Any

import pytest

from blue_data_planner.llm import (
    CachingLLMClientProvider,
    LLMClient,
    LLMClientProvider,
    resolve_llm_client,
)
from blue_data_planner.types import InvalidSettingError

PROPS: dict[str, Any] = {"service_prefix": "openai", "openai.model": "gpt-6-luna"}


def test_an_injected_client_is_returned_as_is() -> None:
    """An llm_client property holding an LLMClient is the client resolved."""
    client = LLMClient("mine", PROPS)
    assert resolve_llm_client({**PROPS, "llm_client": client}) is client


def test_an_injected_llm_client_that_is_not_a_client_is_ignored() -> None:
    """A non-client llm_client value is skipped and a client is built from the properties."""
    resolved = resolve_llm_client({**PROPS, "llm_client": "not-a-client"})
    assert isinstance(resolved, LLMClient)


def test_resolving_twice_without_a_provider_builds_two_clients() -> None:
    """Default resolution keeps no module-level cache, so each call builds a new client."""
    # Default resolution constructs per invocation; it does not use a module cache.
    first = resolve_llm_client(dict(PROPS))
    second = resolve_llm_client(dict(PROPS))
    assert first is not second


def test_none_properties_are_refused_for_naming_no_model() -> None:
    """Resolving with no properties raises a setting error because no model is named."""
    with pytest.raises(InvalidSettingError, match="no model"):
        resolve_llm_client(None)


def test_a_custom_provider_supplies_the_client_and_receives_the_properties() -> None:
    """A given provider is asked for the client and sees the properties passed to resolve."""

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


def test_a_caching_provider_returns_one_client_for_equal_properties() -> None:
    """Two requests with equal properties get the same client object."""
    provider = CachingLLMClientProvider()
    first = provider.get_client(dict(PROPS))
    second = provider.get_client(dict(PROPS))
    assert first is second


def test_a_caching_provider_builds_a_new_client_for_different_properties() -> None:
    """Changing the model between requests yields a different client."""
    provider = CachingLLMClientProvider()
    first = provider.get_client(dict(PROPS))
    second = provider.get_client({**PROPS, "openai.model": "gpt-4.1"})
    assert first is not second


def test_a_caching_provider_builds_an_uncached_client_when_properties_cannot_be_serialized() -> (
    None
):
    """Properties that cannot form a cache key give a new client each time, without an error."""
    provider = CachingLLMClientProvider()
    props = {**PROPS, "llm.max_parallel": object()}
    first = provider.get_client(props)
    second = provider.get_client(props)
    assert first is not second  # constructed uncached, never an error


def test_concurrent_requests_to_a_caching_provider_build_at_most_one_duplicate_client() -> None:
    """Fifty threads asking for the same properties end up with at most two distinct clients."""
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


def test_two_caching_providers_do_not_share_clients() -> None:
    """Each caching provider holds its own clients, so equal properties give different clients."""
    a, b = CachingLLMClientProvider(), CachingLLMClientProvider()
    assert a.get_client(dict(PROPS)) is not b.get_client(dict(PROPS))
