"""Plan a request without running it, and print what a planning-only pass produces."""

from __future__ import annotations

from pathlib import Path

import dotenv

from blue_data_planner import build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite

REGISTRY = Path(__file__).resolve().parents[1] / "data" / "demo" / "registry.json"
REQUEST = (
    "From the products and suppliers tables, list the supplier company name "
    "for every product whose units_in_stock is zero."
)


def main() -> None:
    dotenv.load_dotenv(override=True)

    planner = build_data_planner(
        open_registry(REGISTRY),
        suites=[BuiltinSuite()],
        model="openai/gpt-5.6-luna",
    )

    result = planner.plan(REQUEST)

    print(f"request: {REQUEST}")
    print(f"status:  {result.status}")
    print(f"answer:  {result.answer}")

    plan = result.pipeline.logical_plan
    print("\nlogical plan")
    for node in plan.nodes:
        reads = ", ".join(node.input_node_ids) or "-"
        print(f"  {node.id:<4} {node.kind:<10} reads {reads}")

    cover = result.pipeline.cover
    print("\ncover")
    for fragment in cover.fragments:
        covered = ", ".join(fragment.logical_node_ids)
        print(f"  {fragment.id:<4} {fragment.operator_id:<20} covers {covered}")
        for bound in fragment.input_data:
            print(f"       reads {bound.logical_node_id} as {bound.name}")

    print(f"\noperator calls: {result.counters.operator_calls}")
    print(f"failure reason: {result.failure_reason}")


if __name__ == "__main__":
    main()
