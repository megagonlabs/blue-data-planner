"""Answer one request end to end, and print the rows it produced."""

from __future__ import annotations

from pathlib import Path

import dotenv

from blue_data_planner import RunStatus, build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite

REGISTRY = Path(__file__).resolve().parents[1] / "data" / "demo" / "registry.json"
REQUEST = "Which three countries do the most customers come from?"


def main() -> None:
    dotenv.load_dotenv(override=True)

    planner = build_data_planner(
        open_registry(REGISTRY),
        suites=[BuiltinSuite()],
        model="openai/gpt-5.6-luna",
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
    print(f"\noperator calls: {result.counters.operator_calls}")
    print(f"model calls:    {len(usage)}")


if __name__ == "__main__":
    main()
