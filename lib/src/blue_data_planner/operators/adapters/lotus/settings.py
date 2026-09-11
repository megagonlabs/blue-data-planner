"""Importing LOTUS and configuring the model it runs on."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from blue_data_planner.types import BlueDataPlannerError

# LOTUS keeps one model per process in its own global settings, so this module remembers
# what it was configured with and refuses a second, rather than letting the last writer win.
_configured_model: str | None = None


def lotus_is_installed() -> bool:
    """Answer whether LOTUS can be imported, without importing it."""
    return importlib.util.find_spec("lotus") is not None


def configured_lotus(model: str) -> Any:
    """Import LOTUS on first use, configure its model once, and return the module."""
    global _configured_model
    lotus: Any = importlib.import_module("lotus")
    if _configured_model is None:
        lotus.settings.configure(lm=lotus.models.LM(model=model))
        _configured_model = model
    elif _configured_model != model:
        raise BlueDataPlannerError(
            f"LOTUS is configured with model {_configured_model!r}, and its settings are "
            f"process-wide, so {model!r} cannot be configured beside it"
        )
    return lotus
