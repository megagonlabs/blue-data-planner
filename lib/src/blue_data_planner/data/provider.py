from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.data.registry_client import DataRegistryClient

# Local alias rather than operators.types.Properties: the data package must not
# import the operators package (operators import the client — one-way dependency).
Properties = dict[str, Any]


class DataRegistryProvider(ABC):
    """Resolves and constructs the data registry an operator should use."""

    @abstractmethod
    def get_registry(
        self, platform_name: str, registry_name: str, properties: Properties
    ) -> DataRegistryClient: ...


class BlueDataRegistryProvider(DataRegistryProvider):
    """Default provider: a per-name cached blue-data DataRegistry factory."""

    # The default provider uses an in-memory registry with eager index rebuilds.
    # A registry reaches this package already populated, so a caller with data and no
    # registry builds one with blue-data first, then injects it.

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], DataRegistryClient] = {}

    def get_registry(
        self, platform_name: str, registry_name: str, properties: Properties
    ) -> DataRegistryClient:
        key = (platform_name, registry_name)
        client = self._cache.get(key)
        if client is None:
            client = DataRegistryClient(DataRegistry(name=registry_name, rebuild=True))
            self._cache[key] = client
        return client


_default_provider = BlueDataRegistryProvider()


def resolve_registry(
    properties: Properties | None, provider: DataRegistryProvider | None = None
) -> DataRegistryClient | None:
    """Resolve an injected registry or construct one from the two naming keys."""
    # An invalid injected value falls through to the construction-key branch. A raw
    # blue-data registry is wrapped so no blue-data class escapes this package.
    if properties is None:
        properties = {}
    injected = properties.get("data_registry")
    if isinstance(injected, DataRegistryClient):
        return injected
    if isinstance(injected, DataRegistry):
        return DataRegistryClient(injected)

    platform_name = properties.get("platform.name")
    registry_name = properties.get("data_registry.name")
    if platform_name and registry_name:
        active_provider = provider if provider is not None else _default_provider
        return active_provider.get_registry(str(platform_name), str(registry_name), properties)
    return None
