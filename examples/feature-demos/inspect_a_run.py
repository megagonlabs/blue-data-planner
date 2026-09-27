"""Run a request, then read back the plan it settled on and what each fragment did.

Run, from the repository root, after building the demo registry (examples/README.md):
    export OPENAI_API_KEY=sk-...
    python examples/feature-demos/inspect_a_run.py
"""

from __future__ import annotations

import os
from pathlib import Path

import dotenv

from blue_data_planner import build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite
from blue_data_planner.plan import signatures
from blue_data_planner.types import DiscoveryResult, FragmentResult

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "demo" / "registry.json"
REQUEST = "Which product categories have the most products?"


def main() -> None:
    dotenv.load_dotenv(override=True)
    if not REGISTRY.is_file():
        raise SystemExit(f"{REGISTRY} is missing; build it as examples/README.md describes")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; every planning stage calls a model")

    planner = build_data_planner(
        open_registry(REGISTRY),
        suites=[BuiltinSuite()],
        model="openai/gpt-5.6-luna",
    )

    result = planner.run(REQUEST)
    print(f"request: {REQUEST}")
    print(f"status:  {result.status}\n")

    for found in result.results.values():
        if isinstance(found, DiscoveryResult):
            print("discovery")
            print(f"  searched for: {found.search_terms}")
            for match in found.matches:
                print(f"  matched:      {match.path}")

    # A run that ended before it had a plan and a cover to run has nothing more to show.
    if result.pipeline.current_physical_plan_id is None:
        print(f"reason: {result.failure_reason}")
        return

    plan = result.pipeline.logical_plan
    cover = result.pipeline.cover
    signed = signatures(plan, cover)

    print("\nthe plan that ran")
    for node in plan.nodes:
        reads = ", ".join(node.input_node_ids) or "-"
        print(f"  {node.id:<28} {node.kind:<10} reads {reads}")

    print("\nper fragment")
    for fragment in cover.fragments:
        found = result.results.get(signed[fragment.root_logical_node_id])
        if not isinstance(found, FragmentResult):
            continue
        print(f"  {fragment.id} {fragment.operator_id} -> {found.status}")
        for bound in fragment.input_data:
            print(f"    reads {bound.logical_node_id} as {bound.name}")
        if found.failure_reason is not None:
            print(f"    reason: {found.failure_reason}")
        if found.result is not None:
            print(f"    rows: {found.result.row_count}")

    print(f"\nlogical plans written: {len(result.pipeline.logical_plans)}")
    print(f"covers written:        {len(result.pipeline.physical_plans)}")
    print(f"operator calls:        {result.counters.operator_calls}")


if __name__ == "__main__":
    main()
