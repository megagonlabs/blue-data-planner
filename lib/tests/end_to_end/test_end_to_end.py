"""One real request, planned twice through a DISCOVER node and answered.

Excluded from testpaths, so it runs only when named, and it calls a real provider:

    uv run pytest tests/end_to_end/test_end_to_end.py
"""

from __future__ import annotations

import os

from blue_data_planner import RunStatus, build_data_planner
from blue_data_planner.data import DataRegistry
from blue_data_planner.operators.builtin import BuiltinSuite

MODEL = os.environ.get("BLUE_DATA_PLANNER_MODEL", "openai/gpt-4o-mini")


def test_a_request_over_a_registered_source_plans_twice_and_answers(
    sqlite_registry: DataRegistry,
) -> None:
    """The first pass only asks the catalog; the second is written against what it found."""
    # with_nl2table stays off: the atomic entries answer this request, and the compound one
    # runs a generated program and is experimental (ADR-020).
    planner = build_data_planner(sqlite_registry, suites=[BuiltinSuite()], model=MODEL)

    result = planner.run("which cities have stores")

    assert result.status is RunStatus.OK
    assert result.answer is not None
    assert len(result.pipeline.logical_plans) >= 2
    assert result.counters.control_replans >= 1
