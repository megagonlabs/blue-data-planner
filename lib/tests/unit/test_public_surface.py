"""What the package root exports, and nothing more."""

from __future__ import annotations

import blue_data_planner

_RUN_PATH = frozenset(
    {
        "BlueDataPlannerError",
        "DataPlanner",
        "PlannerSettings",
        "RunLimits",
        "RunResult",
        "RunStatus",
        "build_data_planner",
        "build_data_planner_from_settings",
    }
)


def test_the_root_exports_exactly_the_run_path() -> None:
    """A caller who runs a planner and reads its result writes one import from the root."""
    assert set(blue_data_planner.__all__) == _RUN_PATH


def test_every_name_the_root_advertises_is_reachable() -> None:
    """A name listed in __all__ but not on the module is a broken export."""
    for name in blue_data_planner.__all__:
        assert hasattr(blue_data_planner, name)
