"""Answer one request end to end, and print the rows it produced.

Run, from the repository root, after building the demo registry (examples/README.md):
    export OPENAI_API_KEY=sk-...
    python examples/feature-demos/run_a_request.py
"""

from __future__ import annotations

import os
from pathlib import Path

import dotenv

from blue_data_planner import RunStatus, build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "demo" / "registry.json"
REQUEST = "Which country has the most customers, and how many does it have?"


def main() -> None:
    dotenv.load_dotenv(override=True)
    if not REGISTRY.is_file():
        raise SystemExit(f"{REGISTRY} is missing; build it as examples/README.md describes")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; every planning stage calls a model")

    planner = build_data_planner(
        open_registry(REGISTRY),
        suites=[BuiltinSuite()],
        model="openai/gpt-6-luna",
    )

    result = planner.run(REQUEST)

    print(f"request: {REQUEST}")
    print(f"status:  {result.status}")
    if result.status is not RunStatus.OK:
        print(f"reason:  {result.failure_reason}")
        return

    # The answer is a list of tables, each a list of dict rows.
    assert result.answer is not None
    for table in result.answer:
        for row in table:
            print(row)

    usage = result.conversations.usage
    print(f"\noperator calls:      {result.counters.operator_calls}")
    print(f"planner model calls: {len(usage)}")


if __name__ == "__main__":
    main()
