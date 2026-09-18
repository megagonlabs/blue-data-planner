"""Importing LOTUS and configuring the model it runs on."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from blue_data_planner.types import BlueDataPlannerError

# LOTUS settings are process-wide, so this module remembers the model and the reasoning effort it
# was configured with and refuses a second, rather than letting the last writer win.
_configured_model: str | None = None
_configured_reasoning_effort: str | None = None


def lotus_is_installed() -> bool:
    """Answer whether LOTUS can be imported, without importing it."""
    return importlib.util.find_spec("lotus") is not None


def configured_lotus(model: str, reasoning_effort: str | None = None) -> Any:
    """Import LOTUS on first use, configure its model once, and return the module."""
    global _configured_model, _configured_reasoning_effort
    lotus: Any = importlib.import_module("lotus")
    if _configured_model is None:
        kwargs: dict[str, Any] = (
            {} if reasoning_effort is None else {"reasoning_effort": reasoning_effort}
        )
        lotus.settings.configure(lm=lotus.models.LM(model=model, **kwargs))
        _configured_model = model
        _configured_reasoning_effort = reasoning_effort
    elif _configured_model != model or _configured_reasoning_effort != reasoning_effort:
        raise BlueDataPlannerError(
            f"LOTUS is configured with model {_configured_model!r} and reasoning effort "
            f"{_configured_reasoning_effort!r}, and its settings are process-wide, so "
            f"{model!r} with reasoning effort {reasoning_effort!r} cannot be configured beside it"
        )
    return lotus
