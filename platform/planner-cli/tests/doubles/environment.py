"""The temporary home, registry, and console one CLI test runs against."""

from __future__ import annotations

import io
from pathlib import Path

from blue_data.utils.config import ProfileStore  # pyright: ignore[reportMissingTypeStubs]
from rich.console import Console


def a_cli_home(tmp_path: Path) -> str:
    """Return the directory this CLI's profiles live in for one test."""
    return str(tmp_path / "home")


def a_registry_file(tmp_path: Path) -> Path:
    """Return the path of an empty but readable registry file."""
    path = tmp_path / "registry.json"
    path.write_text("[]")
    return path


def a_configured_environment(tmp_path: Path) -> dict[str, str]:
    """Return an environment with a home directory, a readable registry, and an API key."""
    return {
        "BLUE_DATA_PLANNER_HOME": a_cli_home(tmp_path),
        "BLUE_DATA_REGISTRY_PATH": str(a_registry_file(tmp_path)),
        "OPENAI_API_KEY": "sk-not-used",
    }


def a_profile_holding(home: str, **values: str) -> None:
    """Write `values` into the default profile under `home`, the way `profile config` does."""
    store = ProfileStore(home)
    store.add("default", dict(values))
    store.select("default")


def a_console(width: int = 200) -> tuple[Console, io.StringIO]:
    """Return a console writing into a buffer, wide enough that nothing wraps unless asked."""
    buffer = io.StringIO()
    return Console(file=buffer, width=width), buffer
