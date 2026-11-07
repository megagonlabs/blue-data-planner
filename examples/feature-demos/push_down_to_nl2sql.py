"""Run requests over the Blue and built-in suites, and show how much of each plan blue.nl2sql takes.

blue.nl2sql is a compound operator: one call can cover a scan, a join, a filter, an aggregate,
and a sort, as long as every scan reads one database and no step needs a semantic judgment.
Each case below is a request chosen for one of those two situations. For each plan version the
run wrote, the script prints the logical plan's nodes grouped by the fragment that covers them,
what each fragment's operator was sent and returned, then the SQL blue.nl2sql wrote and whether
the answer matches the expected rows.

Run, from the repository root, after building the demo registry (examples/README.md):
    export OPENAI_API_KEY=sk-...
    python examples/feature-demos/push_down_to_nl2sql.py            # every case
    python examples/feature-demos/push_down_to_nl2sql.py --case semantic_split
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dotenv

from blue_data_planner import RunResult, build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.builtin import BuiltinSuite
from blue_data_planner.plan import signatures
from blue_data_planner.types import (
    DiscoveryResult,
    FragmentResult,
    LogicalOperator,
    LogicalPlan,
    PhysicalPlan,
    PipelineNode,
)

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "demo" / "registry.json"
MODEL = "openai/gpt-6-luna"
# blue.nl2sql returns the rows its query produced and reports nothing, so the query it wrote
# reaches no result object. The operation logs it, and this script reads it from there.
NL2SQL_LOGGER = "blue_data_ops.operators.nl2sql_operator"
NL2SQL_LOG_PREFIX = "Generated Query: "


@dataclass(frozen=True)
class Case:
    """One request, what it is meant to show, and the rows a correct answer holds."""

    name: str
    shows: str
    request: str
    # Matched by value, since the planner names the answer's columns. Row order and extra
    # columns in the answer do not count against it.
    expected_rows: list[tuple[Any, ...]]


CASES = [
    Case(
        name="whole_plan",
        shows="every step is SQL over one database, so one blue.nl2sql call can take the plan",
        request=(
            "For each supplier country, how many products that are not discontinued have a "
            "unit price above 20? Sort the countries by that count, highest first."
        ),
        expected_rows=[("USA", 4), ("Spain", 2), ("Japan", 2), ("UK", 1), ("Australia", 1)],
    ),
    Case(
        name="semantic_split",
        shows=(
            "picking the products takes knowing what they are made of, which no column says, "
            "so blue.nl2sql can take only the steps SQL can write, and the filter goes elsewhere"
        ),
        request=(
            "Which products are made from soybeans? List each product's name and the company "
            "name of its supplier."
        ),
        # Tofu is filed under Produce and Genen Shouyu (soy sauce) under Condiments, and
        # neither name contains "soy", so no category or LIKE pattern finds both.
        expected_rows=[("Tofu", "Mayumis"), ("Genen Shouyu", "Mayumis")],
    ),
]


class QueryCollector(logging.Handler):
    """Keeps every query blue.nl2sql logs, in the order it wrote them."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.queries: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(NL2SQL_LOG_PREFIX):
            self.queries.append(message.removeprefix(NL2SQL_LOG_PREFIX))


def main() -> None:
    dotenv.load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Show how much of each plan blue.nl2sql takes.")
    parser.add_argument(
        "--case", choices=[case.name for case in CASES], action="append", help="Run this case only"
    )
    arguments = parser.parse_args()
    if not REGISTRY.is_file():
        raise SystemExit(f"{REGISTRY} is missing; build it as examples/README.md describes")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; every planning stage calls a model")

    collector = QueryCollector()
    nl2sql_logger = logging.getLogger(NL2SQL_LOGGER)
    nl2sql_logger.setLevel(logging.INFO)
    nl2sql_logger.addHandler(collector)

    planner = build_data_planner(
        open_registry(REGISTRY),
        suites=[BuiltinSuite(), BlueSuite()],
        model=MODEL,
    )
    selected = [case for case in CASES if arguments.case is None or case.name in arguments.case]
    for case in selected:
        collector.queries.clear()
        result = planner.run(case.request)
        print_case(case, result, collector.queries)


def print_case(case: Case, result: RunResult, queries: list[str]) -> None:
    """Print one case: each plan version with its cover, the queries, and the answer."""
    print("=" * 72)
    print(f"case:    {case.name}")
    print(f"shows:   {case.shows}")
    print(f"request: {case.request}")
    print(f"status:  {result.status}")
    if result.failure_reason is not None:
        print(f"reason:  {result.failure_reason}")

    # Signature -> the physical plan that first ran that work, so a later physical plan
    # carrying it over is not shown running it again.
    first_run: dict[str, int] = {}
    # Physical plans are kept in the order the run wrote them, so a repaired one follows the
    # one it replaced, and a new logical plan version follows the last physical plan of the
    # version before it.
    for position, cover in enumerate(result.pipeline.physical_plans.values(), start=1):
        plan = result.pipeline.logical_plans[cover.logical_plan_id]
        print(f"\nphysical plan {position} (logical plan {plan.id[:8]})")
        print_cover(plan, cover, result, position, first_run)

    print("\nqueries blue.nl2sql wrote")
    for query in queries or ["-"]:
        print("  " + query.replace("\n", "\n  "))

    print("\nanswer")
    if result.answer is None:
        print("  none")
        return
    for table in result.answer:
        for row in table:
            print(f"  {row}")
    print(f"matches the expected rows: {matches(case, result.answer)}")
    print(f"operator calls: {result.counters.operator_calls}")


def print_cover(
    plan: LogicalPlan,
    cover: PhysicalPlan,
    result: RunResult,
    position: int,
    first_run: dict[str, int],
) -> None:
    """Print a plan's nodes in the order a run reaches them, each fragment with the nodes it
    covers, and how each fragment or search ended."""
    signed = signatures(plan, cover)
    fragments_by_root = {fragment.root_logical_node_id: fragment for fragment in cover.fragments}
    for node in plan.nodes_in_dependency_order():
        fragment = fragments_by_root.get(node.id)
        if fragment is not None:
            print(f"  fragment {fragment.id}  {fragment.operator_id}")
            for covered in plan.nodes_in_dependency_order():
                if covered.id in fragment.logical_node_ids:
                    print_node(covered, indent=4)
        elif not node.is_operator_node:
            print_node(node, indent=2)
            # RETURN names the answer's columns, and no call or search produces a result for it.
            if node.kind is LogicalOperator.RETURN:
                continue
        else:
            continue
        signature = signed.get(node.id)
        if signature is None or signature not in result.results:
            print(f"{'':6}not run")
        elif first_run.setdefault(signature, position) != position:
            print(f"{'':6}reused from physical plan {first_run[signature]}")
        else:
            print_node_result(result.results[signature])


def print_node(node: PipelineNode, indent: int) -> None:
    """Print one logical node: its id, kind, what it reads, and what it asks for."""
    reads = f"  reads {', '.join(node.input_node_ids)}" if node.input_node_ids else ""
    print(f"{'':{indent}}{node.id}  {node.kind}{reads}")
    if node.kind is LogicalOperator.RETURN:
        print(f"{'':{indent + 2}}columns: {', '.join(node.attributes['columns'])}")
        return
    print(f"{'':{indent + 2}}{node.instruction}")
    if node.kind is LogicalOperator.SCAN and node.attributes.get("source") is not None:
        print(f"{'':{indent + 2}}source: {node.attributes['source']}")


def print_node_result(found: FragmentResult | DiscoveryResult) -> None:
    """Print what a catalog search matched, or what each of a fragment's attempts sent and
    got back."""
    if isinstance(found, DiscoveryResult):
        print(f"{'':6}-> searched {', '.join(found.search_terms)}")
        for match in found.matches:
            print(f"{'':9}matched {match.path}")
        return
    print(f"{'':6}-> {found.status}, {len(found.attempts)} attempt(s)")
    for number, attempt in enumerate(found.attempts, start=1):
        print(f"{'':6}attempt {number}: {attempt.parameters}")
        call = attempt.result
        if call is None:
            print(f"{'':8}repeated an earlier attempt, not run")
            continue
        print(f"{'':8}{call.status}, {call.row_count} row(s)")
        if call.error is not None:
            print(f"{'':8}error: {call.error.message}")
        if attempt.assessment is not None:
            print(f"{'':8}review: {attempt.assessment}")
    if found.failure_reason is not None:
        print(f"{'':6}reason: {found.failure_reason}")


def matches(case: Case, answer: list[list[dict[str, Any]]]) -> bool:
    """Return whether each row of the answer's first table holds one expected row's values."""
    if not answer or len(answer[0]) != len(case.expected_rows):
        return False
    unmatched = list(case.expected_rows)
    for row in answer[0]:
        values = list(row.values())
        found = next((one for one in unmatched if all(value in values for value in one)), None)
        if found is None:
            return False
        unmatched.remove(found)
    return True


if __name__ == "__main__":
    main()
