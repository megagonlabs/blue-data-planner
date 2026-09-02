from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from blue_data_planner.llm.client import LLMClient


class LLMClientProvider(ABC):
    """Constructs (or reuses) the LLM client an operator should use."""

    @abstractmethod
    def get_client(self, properties: dict[str, Any]) -> LLMClient: ...


def resolve_llm_client(
    properties: dict[str, Any] | None, provider: LLMClientProvider | None = None
) -> LLMClient:
    """Resolve an injected or provider-owned client, otherwise construct a fresh one.

    The default lifetime is one client per invocation, never a module-level
    singleton. An invalid injected value falls through to the next resolution path.
    """
    if properties is None:
        properties = {}
    injected = properties.get("llm_client")
    if isinstance(injected, LLMClient):
        return injected
    if provider is not None:
        return provider.get_client(properties)
    return LLMClient(name="llm_client", properties=properties)


class CachingLLMClientProvider(LLMClientProvider):
    """Opt-in per-config client reuse; the cache's lifetime belongs to its owner."""

    def __init__(self) -> None:
        self._cache: dict[str, LLMClient] = {}

    def get_client(self, properties: dict[str, Any]) -> LLMClient:
        relevant = {
            key: value
            for key, value in properties.items()
            if key == "service_prefix"
            or key.startswith("llm.")
            or self._is_prefixed(key, properties)
        }
        try:
            cache_key = json.dumps(relevant, sort_keys=True)
        except (TypeError, ValueError):
            # non-serializable config: construct uncached (never an error)
            return LLMClient(name="llm_client", properties=properties)
        client = self._cache.get(cache_key)
        if client is None:
            client = LLMClient(name="llm_client", properties=properties)
            self._cache[cache_key] = client
        return client

    @staticmethod
    def _is_prefixed(key: str, properties: dict[str, Any]) -> bool:
        prefix = properties.get("service_prefix")
        return bool(prefix) and key.startswith(str(prefix) + ".")
