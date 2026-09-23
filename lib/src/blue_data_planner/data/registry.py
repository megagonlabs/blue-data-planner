"""Opening a serialized blue-data registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.types.errors import RegistryUnreadableError


def open_registry(registry_path: Path) -> DataRegistry:
    """Read a serialized blue-data registry into memory."""
    # blue-data's load returns quietly when the file is absent, which would hand back an
    # empty registry indistinguishable from one holding nothing.
    if not registry_path.is_file():
        raise RegistryUnreadableError(f"no registry file at {registry_path}")
    registry: Any = DataRegistry(name=registry_path.stem, rebuild=True)
    try:
        registry.load(str(registry_path))
    except Exception as error:
        # A registry read reaches backends whose failures share no common type. This is the
        # boundary the coding style permits a broad catch at.
        raise RegistryUnreadableError(
            f"cannot read the registry at {registry_path}: {error}"
        ) from error
    return registry
