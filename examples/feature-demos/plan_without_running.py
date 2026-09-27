"""Plan a request without running it, and print what a planning-only pass produces.

Run, from the repository root, after building the demo registry (examples/README.md):
    export OPENAI_API_KEY=sk-...
    python examples/feature-demos/plan_without_running.py
"""

from __future__ import annotations

import os
from pathlib import Path

import dotenv

from blue_data_planner import build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite
from blue_data_planner.types import DiscoveryResult

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "demo" / "registry.json"
REQUEST = (
    "From the products and suppliers tables, list the supplier company name "
    "for every product whose units_in_stock is zero."
)


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

    result = planner.plan(REQUEST)

    print(f"request: {REQUEST}")
    print(f"status:  {result.status}")
    print(f"answer:  {result.answer}")
    # A pass that ended before a plan and its cover were installed has nothing more to show.
    if result.pipeline.current_logical_plan_id is None:
        print(f"reason:  {result.failure_reason}")
        return

    print(f"\nplan versions written: {len(result.pipeline.logical_plans)}")
    for found in result.results.values():
        if isinstance(found, DiscoveryResult):
            paths = ", ".join(match.path for match in found.matches) or "-"
            print(f"  searched {', '.join(found.search_terms)}: found {paths}")

    plan = result.pipeline.logical_plan
    print("\nlogical plan the operators would run against")
    for node in plan.nodes:
        reads = ", ".join(node.input_node_ids) or "-"
        print(f"  {node.id:<28} {node.kind:<10} reads {reads}")

    cover = result.pipeline.cover
    print("\ncover")
    for fragment in cover.fragments:
        covered = ", ".join(fragment.logical_node_ids)
        print(f"  {fragment.id:<10} {fragment.operator_id:<16} covers {covered}")
        for bound in fragment.input_data:
            print(f"{'':<13}reads {bound.logical_node_id} as {bound.name}")

    print(f"\noperator calls: {result.counters.operator_calls}")


if __name__ == "__main__":
    main()
