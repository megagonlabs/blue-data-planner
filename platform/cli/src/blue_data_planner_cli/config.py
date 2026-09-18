"""What a profile supplies, and the planner those settings assemble."""

from __future__ import annotations

from pathlib import Path

from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs]
    ConfigSpec,  # blue-data ships no py.typed
    base_spec,
    load_spec,
)

CLI_HOME_VARIABLE: str = "BLUE_DATA_PLANNER_HOME"
DEFAULT_CLI_HOME: str = "~/.blue-planner"

_SPEC_PATH: Path = Path(__file__).with_name("profile_spec.json")


def config_spec() -> ConfigSpec:
    """Return the family-wide parameters merged with this CLI's own."""
    return base_spec().merged_with(load_spec(_SPEC_PATH))


def cli_home(environ: dict[str, str]) -> str:
    """Return the directory holding this CLI's profiles."""
    return environ.get(CLI_HOME_VARIABLE, DEFAULT_CLI_HOME)
