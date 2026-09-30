"""Importing LOTUS and configuring the model it runs on."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

from blue_data_planner.types import InvalidSettingError

# LOTUS's own default for a reasoning model (8192) is spent on hidden reasoning tokens as well as
# the visible answer, so a call can exhaust it and return an empty answer that operators silently
# coerce to their default output. Raising the ceiling costs nothing on calls that stay under it.
_MAX_TOKENS: int = 15000

# LOTUS settings are process-wide, so this module remembers the model and the reasoning effort the
# first suite built claimed and refuses a second, rather than letting the last writer win.
_configured_model: str | None = None
_configured_reasoning_effort: str | None = None
_lotus_configured: bool = False


def lotus_is_installed() -> bool:
    """Answer whether LOTUS can be imported, without importing it."""
    return importlib.util.find_spec("lotus") is not None


def claim_lotus_settings(model: str, reasoning_effort: str | None = None) -> None:
    """Record the model and reasoning effort LOTUS runs on, refusing a second, different pair."""
    global _configured_model, _configured_reasoning_effort
    if _configured_model is None:
        _configured_model = model
        _configured_reasoning_effort = reasoning_effort
        return
    if _configured_model != model or _configured_reasoning_effort != reasoning_effort:
        raise InvalidSettingError(
            f"LOTUS is configured with model {_configured_model!r} and reasoning effort "
            f"{_configured_reasoning_effort!r}, and its settings are process-wide, so "
            f"{model!r} with reasoning effort {reasoning_effort!r} cannot be configured beside it"
        )


def configured_lotus(model: str, reasoning_effort: str | None = None) -> Any:
    """Import LOTUS on first use, configure its model once, and return the module."""
    global _lotus_configured
    claim_lotus_settings(model, reasoning_effort)
    lotus: Any = importlib.import_module("lotus")
    if not _lotus_configured:
        kwargs: dict[str, Any] = (
            {} if reasoning_effort is None else {"reasoning_effort": reasoning_effort}
        )
        lotus.settings.configure(lm=lotus.models.LM(model=model, max_tokens=_MAX_TOKENS, **kwargs))
        _lotus_configured = True
    return lotus
