"""Opening blue-data's shared registry stores and legacy snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from blue_data.config import build_store  # pyright: ignore[reportMissingTypeStubs]
from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)
from blue_data.source.json.file_json_source import (  # pyright: ignore[reportMissingTypeStubs]
    FileJSONSource,
)

from blue_data_planner.types.errors import InvalidSettingError, RegistryUnreadableError


def open_registry(registry_path: Path | None = None) -> DataRegistry:
    """Open an explicit file, or the store configured for blue-data's other tools.

    An explicit path accepts a standard file store or a legacy list snapshot. Without
    one, BLUE_DATA_REGISTRY_STORE selects blue-data's file/Redis store; the old
    BLUE_DATA_REGISTRY_PATH is consulted only when no shared store is configured.
    """
    if registry_path is not None:
        return _open_file(registry_path.expanduser(), shared=False)
    store_type = os.environ.get("BLUE_DATA_REGISTRY_STORE", "").lower()
    if store_type == "file":
        return _open_file(
            Path(os.environ.get("BLUE_DATA_REGISTRY_FILE", "blue_data_store.json")), shared=True
        )
    if store_type == "redis":
        try:
            registry: Any = DataRegistry(store=build_store(), rebuild=True)
            registry.build_index()
            return registry
        except Exception as error:
            raise RegistryUnreadableError(f"cannot read the Redis registry: {error}") from error
    if store_type:
        raise InvalidSettingError(
            f"BLUE_DATA_REGISTRY_STORE is {store_type!r}; expected file or redis. "
            "Build an in-memory registry directly when using the library."
        )
    legacy_path = os.environ.get("BLUE_DATA_REGISTRY_PATH", "")
    if legacy_path:
        return _open_file(Path(legacy_path).expanduser(), shared=False)
    raise InvalidSettingError(
        "Set BLUE_DATA_REGISTRY_STORE=file and BLUE_DATA_REGISTRY_FILE, or "
        "BLUE_DATA_REGISTRY_STORE=redis; BLUE_DATA_REGISTRY_PATH remains a legacy fallback."
    )


def _open_file(registry_path: Path, *, shared: bool) -> DataRegistry:
    try:
        # FileJSONSource creates a missing file; planning must instead report a typo.
        if not registry_path.is_file():
            raise FileNotFoundError(f"no registry file at {registry_path}")
        with registry_path.open(encoding="utf-8") as stream:
            document = json.load(stream)
        registry: Any
        if isinstance(document, dict):
            store: Any = (
                build_store()
                if shared
                else FileJSONSource(properties={"connection": {"path": str(registry_path)}})
            )
            registry = DataRegistry(name=registry_path.stem, store=store, rebuild=True)
            registry.build_index()
        elif isinstance(document, list) and not shared:
            registry = DataRegistry(name=registry_path.stem, rebuild=True)
            registry.loads(json.dumps(document))
        else:
            raise ValueError(
                "expected a blue-data dictionary store; open a legacy list snapshot with "
                "an explicit path or BLUE_DATA_REGISTRY_PATH"
            )
        return registry
    except Exception as error:
        # A registry read reaches backends whose failures share no common type. This is the
        # boundary the coding style permits a broad catch at.
        raise RegistryUnreadableError(
            f"cannot read the registry at {registry_path}: {error}"
        ) from error
