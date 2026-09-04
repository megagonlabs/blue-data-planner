"""Opening a serialized blue-data registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)


def open_registry(registry_path: Path) -> DataRegistry:
    """Read a serialized blue-data registry into memory."""
    # blue-data's load returns quietly when the file is absent, which would hand back an
    # empty registry indistinguishable from one holding nothing.
    if not registry_path.is_file():
        raise FileNotFoundError(f"No registry file at {registry_path}")
    registry: Any = DataRegistry(name=registry_path.stem, rebuild=True)
    registry.load(str(registry_path))
    return registry
