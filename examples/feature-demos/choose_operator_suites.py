"""List what each operator suite offers, then build a planner restricted to one of them.

The one example here that reaches no model and needs no API key.

Run, from the repository root, after building the demo registry (examples/README.md):
    python examples/feature-demos/choose_operator_suites.py
"""

from __future__ import annotations

from pathlib import Path

import dotenv

from blue_data_planner import build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.data_planner import DEFAULT_LLM_CLIENT_NAME
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators import (
    OPERATOR_SUITE_NAMES,
    OperatorRouter,
    OperatorSuite,
    operator_suite,
)
from blue_data_planner.operators.adapters.lotus import lotus_is_installed
from blue_data_planner.operators.builtin import BuiltinSuite
from blue_data_planner.types import LogicalOperator

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "demo" / "registry.json"
MODEL = "openai/gpt-5.6-luna"


def main() -> None:
    dotenv.load_dotenv(override=True)
    if not REGISTRY.is_file():
        raise SystemExit(f"{REGISTRY} is missing; build it as examples/README.md describes")

    registry = open_registry(REGISTRY)
    # The same client build_data_planner assembles: the name prefixes every property it reads.
    client = LLMClient(
        name=DEFAULT_LLM_CLIENT_NAME,
        properties={f"{DEFAULT_LLM_CLIENT_NAME}.model": MODEL},
    )
    router = OperatorRouter()

    print(f"LOTUS installed: {lotus_is_installed()}\n")

    for name in OPERATOR_SUITE_NAMES:
        try:
            operators = operator_suite(name).operators(
                data_registry=registry, llm_client=client, model=MODEL
            )
        except Exception as error:  # a suite builds against its own dependency, or does not
            print(f"{name}: unavailable ({type(error).__name__}: {error})\n")
            continue
        print(f"{name}: {len(operators)} operators")
        for operator in sorted(operators, key=lambda held: held.operator_id):
            implements = ", ".join(sorted(str(op) for op in operator.logical_operators))
            print(f"  {operator.operator_id:<26} {implements}")
            router.register(operator)
        print()

    print("which suites can implement each logical operator")
    for logical_op in LogicalOperator:
        candidates = router.operators_for(logical_op)
        ids = ", ".join(operator.operator_id for operator in candidates) or "-"
        print(f"  {logical_op:<18} {ids}")

    # Restricting the suites: a planner registers what the suites it was built with return,
    # and binds nothing else. Assembling one reaches no model.
    suites: list[OperatorSuite] = [BuiltinSuite()]
    planner = build_data_planner(registry, suites=suites, model=MODEL)
    chosen = [
        operator
        for suite in suites
        for operator in suite.operators(data_registry=registry, llm_client=client, model=MODEL)
    ]
    print(f"\na {type(planner).__name__} built with suites=[BuiltinSuite()]")
    print(f"  {len(chosen)} operators: {', '.join(sorted(op.operator_id for op in chosen))}")


if __name__ == "__main__":
    main()
