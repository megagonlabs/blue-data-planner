"""The settings one benchmark run resolved, and the planner a worker process holds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blue_data_planner import DataPlanner, RunLimits, build_data_planner
from blue_data_planner.data import DataCatalog, open_registry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.adapters.lotus import lotus_operators
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.types import PhysicalOperator

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
    """Open the registry, register the builtin and LOTUS suites, and build the planner."""
    data_registry = open_registry(settings.registry_path)
    llm_properties: dict[str, Any] = {f"{LLM_CLIENT_NAME}.model": settings.model}
    if settings.reasoning_effort is not None:
        llm_properties[f"{LLM_CLIENT_NAME}.reasoning_effort"] = settings.reasoning_effort
    llm_client = LLMClient(name=LLM_CLIENT_NAME, properties=llm_properties)
    # builtin.nl2table runs a generated program, and open questions 49 and 50 have not settled
    # its boundary, so the benchmark leaves it unregistered.
    operators: list[PhysicalOperator] = builtin_operators(
        data_registry, llm_client, with_nl2table=False
    )
    from_lotus = lotus_operators(
        settings.model,
        settings.lotus_max_rows,
        reasoning_effort=LOTUS_REASONING_EFFORT,
    )
    if not from_lotus:
        raise ValueError(
            "LOTUS is not installed, and the benchmark is solved with its operators; sync the "
            "lotus extra as docs/development.md describes"
        )
    operators.extend(from_lotus)
    router = OperatorRouter()
    for operator in operators:
        router.register(operator)
    return build_data_planner(router, DataCatalog(data_registry), llm_client, settings.limits)
