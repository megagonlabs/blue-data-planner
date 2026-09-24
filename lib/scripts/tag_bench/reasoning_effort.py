"""The reasoning-effort convention shared by register_databases.py and benchmark/planner.py."""

from __future__ import annotations

# The one value that means "omit this parameter," for a model that rejects it. Distinct from
# "none", a real litellm reasoning_effort value that still has to reach the provider. Mirrors
# platform/cli/src/blue_data_planner_cli/config.py's _REASONING_EFFORT_OFF: lib/ does not depend
# on platform/cli, so the check is duplicated here rather than imported.
REASONING_EFFORT_OFF: str = "off"


def effective_reasoning_effort(value: str | None) -> str | None:
    """Return the value litellm should receive, or None to omit the parameter."""
    return value if value and value != REASONING_EFFORT_OFF else None
