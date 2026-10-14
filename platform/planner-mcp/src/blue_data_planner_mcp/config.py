"""What the environment supplies, and the planner it builds.

The server reads the environment and nothing else. There is no configuration file: a client
launches this as a subprocess with a working directory it did not choose, and a file read from
that directory would take precedence over the `env` block the client passed deliberately.

The variable names are the `blue-planner` CLI's, so a value means the same thing in both. This
package imports nothing from `blue_data_planner_cli` and reads no profile: it depends only on
blue-data-planner, so it builds, versions, and deploys on its own.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from blue_data_planner import DataPlanner, PlannerSettings
from blue_data_planner.settings import build_data_planner_from_settings
from blue_data_planner.types import InvalidSettingError, RunLimits

LLM_CLIENT_NAME: str = "blue_planner_mcp"

# Every variable that carries a value straight through, by the field it fills.
_TEXT_FIELDS: dict[str, str] = {
    "BLUE_DATA_PLANNER_REASONING_EFFORT": "reasoning_effort",
    "BLUE_DATA_PLANNER_LOTUS_MODEL": "lotus_model",
    "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT": "lotus_reasoning_effort",
}
_WHOLE_NUMBER_FIELDS: dict[str, str] = {
    "BLUE_DATA_PLANNER_LOTUS_MAX_ROWS": "lotus_max_rows",
}
_LIMIT_FIELDS: dict[str, str] = {
    "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS": "operator_calls",
    "BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS": "failure_replans",
    "BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS": "control_replans",
}

_planner: DataPlanner | None = None


def get_planner() -> DataPlanner:
    """Return the one planner this process runs, building it on the first call."""
    global _planner
    if _planner is None:
        _planner = build_data_planner_from_settings(
            planner_settings(os.environ), llm_client_name=LLM_CLIENT_NAME
        )
    return _planner


def planner_settings(environ: Mapping[str, str]) -> PlannerSettings:
    """Read what this environment names, leaving every other value to PlannerSettings."""
    registry_path = environ.get("BLUE_DATA_REGISTRY_PATH", "").strip()
    if not registry_path:
        raise InvalidSettingError(
            "BLUE_DATA_REGISTRY_PATH is not set. A client sets it in the env block it launches "
            "this server with."
        )
    given: dict[str, Any] = {"registry_path": Path(registry_path).expanduser()}

    model = environ.get("BLUE_DATA_PLANNER_MODEL") or environ.get("BLUE_DATA_LLM_MODEL")
    if model:
        given["model"] = model
    for name, field in _TEXT_FIELDS.items():
        if environ.get(name):
            given[field] = environ[name]
    for name, field in _WHOLE_NUMBER_FIELDS.items():
        if environ.get(name):
            given[field] = _whole_number(name, environ[name])
    if environ.get("BLUE_DATA_PLANNER_OPERATORS"):
        given["operator_suites"] = tuple(
            suite.strip()
            for suite in environ["BLUE_DATA_PLANNER_OPERATORS"].split(",")
            if suite.strip()
        )

    bounds = {
        field: _whole_number(name, environ[name])
        for name, field in _LIMIT_FIELDS.items()
        if environ.get(name)
    }
    if bounds:
        given["limits"] = RunLimits(**bounds)
    return PlannerSettings(**given)


def _whole_number(name: str, value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as error:
        raise InvalidSettingError(f"{name} is {value!r}; expected a whole number") from error
