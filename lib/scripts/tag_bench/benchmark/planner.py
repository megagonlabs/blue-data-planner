"""The settings one benchmark run resolved, and the planner a worker process holds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blue_data_planner import DataPlanner, RunLimits, build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.adapters.lotus import LotusSuite, lotus_is_installed
from blue_data_planner.operators.builtin import BuiltinSuite
from scripts.tag_bench.reasoning_effort import effective_reasoning_effort

LLM_CLIENT_NAME: str = "tag_bench_benchmark"
# LOTUS runs the planner's own model. "none" is a real litellm reasoning_effort value rather
# than an omission, and it is what the CLI configures LOTUS with.
LOTUS_REASONING_EFFORT: str = "none"


@dataclass(frozen=True)
class BenchmarkSettings:
    """Every value one run resolved, before a planner is built from them."""

    registry_path: Path
    model: str
    reasoning_effort: str | None
    lotus_max_rows: int
    limits: RunLimits


def build_benchmark_planner(settings: BenchmarkSettings) -> DataPlanner:
    """Open the registry and build the planner over the built-in and LOTUS suites."""
    if not lotus_is_installed():
        raise ValueError(
            "LOTUS is not installed, and the benchmark is solved with its operators; sync the "
            "lotus extra as lib/README.md#the-lotus-environment describes"
        )
    return build_data_planner(
        open_registry(settings.registry_path),
        # builtin.nl2table runs a generated program and is experimental, registered only where a
        # deployment chooses to (ADR-020), so the benchmark leaves it unregistered.
        suites=[
            BuiltinSuite(),
            LotusSuite(max_rows=settings.lotus_max_rows, reasoning_effort=LOTUS_REASONING_EFFORT),
        ],
        model=settings.model,
        reasoning_effort=effective_reasoning_effort(settings.reasoning_effort),
        limits=settings.limits,
        llm_client_name=LLM_CLIENT_NAME,
    )
