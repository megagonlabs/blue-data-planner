"""What a run did: the records behind the log, and the table the CLI draws them in."""

from __future__ import annotations

from dataclasses import dataclass

from blue_data_planner import RunResult
from blue_data_planner.plan import signatures
from blue_data_planner.types import (
    SUMMARY_SAMPLE_ROWS,
    ControlOperator,
    DiscoveryResult,
    FragmentResult,
    LogicalPlan,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    RunStatus,
    TableSummary,
    summarize_table,
)
from rich.console import Console
from rich.table import Table
from rich.text import Text

NOT_REACHED = "not reached under this cover"


@dataclass(frozen=True)
class TracedNode:
    """One logical node, and the source it reads where it scans one."""

    id: str
    kind: str
    instruction: str
    reads: str | None


@dataclass(frozen=True)
class TracedFragment:
    """One fragment of a cover: the nodes it covered, what fed it, and how its call ended."""

    id: str
    operator_id: str
    nodes: list[TracedNode]
    input_fragment_ids: list[str]
    outcome: str
    reason: str | None
    rows: TableSummary | None


@dataclass(frozen=True)
class TracedDiscovery:
    """One DISCOVER node the cover's plan carries, and what its search matched."""

    node: TracedNode
    outcome: str
    matches: list[str]


@dataclass(frozen=True)
class TracedCover:
    """One cover: the searches its plan resolved, then its fragments in the order a run runs."""

    position: int
    logical_plan_id: str
    discoveries: list[TracedDiscovery]
    fragments: list[TracedFragment]


def traced_covers(result: RunResult) -> list[TracedCover]:
    """Return one record per cover the request produced."""
    return _Walk(result).covers()


def print_log(result: RunResult, console: Console, *, max_output_chars: int) -> None:
    """Print the log as one table per cover, with what a table cannot hold beneath it."""
    for cover in traced_covers(result):
        console.print(f"cover {cover.position}   logical plan {cover.logical_plan_id}")
        console.print(_cover_table(cover))
        for discovery in cover.discoveries:
            matched = [f"      matched  {one}" for one in discovery.matches]
            _print_block(console, discovery.node.id, matched)
        for fragment in cover.fragments:
            _print_block(console, fragment.id, _fragment_detail(fragment, max_output_chars))
        console.print("")
    for line in spend_lines(result):
        console.print(Text(line))


def spend_lines(result: RunResult) -> list[str]:
    """Return what the run spent against its bounds, and why it ended where it did."""
    counters = result.counters
    usage = result.conversations.usage
    lines = [
        f"counters  operator calls {counters.operator_calls}/{counters.limits.operator_calls}   "
        f"failure replans {counters.failure_replans}/{counters.limits.failure_replans}   "
        f"control replans {counters.control_replans}/{counters.limits.control_replans}",
        f"model     {len(usage)} calls   ${sum(one.cost for one in usage):.2f}",
    ]
    if result.failure_reason:
        lines.append(f"ended     {result.failure_reason}")
    return lines


class _Walk:
    """One pass over the covers, remembering which work an earlier cover already traced."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.shown: dict[str, int] = {}

    def covers(self) -> list[TracedCover]:
        traced: list[TracedCover] = []
        for position, cover in enumerate(self.result.pipeline.physical_plans.values(), start=1):
            plan = self.result.pipeline.logical_plans.get(cover.logical_plan_id)
            traced.append(
                TracedCover(
                    position=position,
                    logical_plan_id=cover.logical_plan_id,
                    discoveries=[] if plan is None else self._discoveries(plan, cover, position),
                    fragments=[] if plan is None else self._fragments(plan, cover, position),
                )
            )
        return traced

    def _discoveries(
        self, plan: LogicalPlan, cover: PhysicalPlan, position: int
    ) -> list[TracedDiscovery]:
        # A search resolves before the plan reading it is written, so discovery leads the cover.
        signed = signatures(plan, cover)
        return [
            self._discovery(node, signed.get(node.id, ""), position)
            for node in plan.nodes
            if node.kind is ControlOperator.DISCOVER
        ]

    def _fragments(
        self, plan: LogicalPlan, cover: PhysicalPlan, position: int
    ) -> list[TracedFragment]:
        signed = signatures(plan, cover)
        nodes = {node.id: node for node in plan.nodes}
        roots = {one.root_logical_node_id: one.id for one in cover.fragments}
        return [
            self._fragment(
                fragment, nodes, roots, signed.get(fragment.root_logical_node_id, ""), position
            )
            for fragment in _in_run_order(plan, cover)
        ]

    def _discovery(self, node: PipelineNode, signature: str, position: int) -> TracedDiscovery:
        traced = TracedNode(node.id, str(node.kind), _instruction(node), None)
        found = self.result.results.get(signature)
        if not isinstance(found, DiscoveryResult):
            return TracedDiscovery(traced, NOT_REACHED, [])
        reused = self._reused(signature, position)
        dropped = f"  ({found.dropped_terms} terms dropped)" if found.dropped_terms else ""
        matches = [] if reused else [match.path for match in found.matches]
        outcome = f"ok  {len(found.matches)} matches{dropped}{reused}"
        return TracedDiscovery(traced, outcome, matches)

    def _fragment(
        self,
        fragment: PhysicalPlanFragment,
        nodes: dict[str, PipelineNode],
        roots: dict[str, str],
        signature: str,
        position: int,
    ) -> TracedFragment:
        traced = [_traced_node(node_id, nodes, fragment) for node_id in fragment.logical_node_ids]
        inputs = [
            roots.get(one.logical_node_id, one.logical_node_id) for one in fragment.input_data
        ]
        found = self._filed(signature)
        if found is None:
            return TracedFragment(
                fragment.id, fragment.operator_id, traced, inputs, NOT_REACHED, None, None
            )
        call = found.result
        # A call that failed reports no rows, so the outcome names the ending alone.
        counted = "" if call is None or call.row_count is None else f"  {call.row_count} rows"
        tables = [] if call is None else call.output or []
        extra = f"  (+{len(tables) - 1} more tables)" if len(tables) > 1 else ""
        if found.status is not RunStatus.OK:
            return TracedFragment(
                fragment.id,
                fragment.operator_id,
                traced,
                inputs,
                f"{found.status}{counted}{extra}",
                found.failure_reason,
                None,
            )
        # Only an accepted result is reused: a rejected one is offered again, so naming it as
        # reuse would say a call was saved that was not.
        reused = self._reused(signature, position)
        # A bundle almost always holds one table, so the trace shows the first and says how
        # many others there were rather than nesting a block per table.
        rows = None if reused or not tables else summarize_table(tables[0], SUMMARY_SAMPLE_ROWS)
        return TracedFragment(
            fragment.id,
            fragment.operator_id,
            traced,
            inputs,
            f"ok{counted}{extra}{reused}",
            None,
            rows,
        )

    def _filed(self, signature: str) -> FragmentResult | None:
        found = self.result.results.get(signature)
        return found if isinstance(found, FragmentResult) else None

    def _reused(self, signature: str, position: int) -> str:
        first = self.shown.setdefault(signature, position)
        return f"  (reused from cover {first})" if first != position else ""


def _in_run_order(plan: LogicalPlan, cover: PhysicalPlan) -> list[PhysicalPlanFragment]:
    """Return the cover's fragments in the order the run could have reached them."""
    reached = {node.id: place for place, node in enumerate(plan.nodes_in_dependency_order())}
    return sorted(
        cover.fragments, key=lambda one: reached.get(one.root_logical_node_id, len(reached))
    )


def _traced_node(
    node_id: str, nodes: dict[str, PipelineNode], fragment: PhysicalPlanFragment
) -> TracedNode:
    node = nodes.get(node_id)
    return TracedNode(
        id=node_id,
        kind=str(node.kind) if node is not None else "?",
        instruction=_instruction(node) if node is not None else "",
        reads=fragment.scan_sources.get(node_id),
    )


def _instruction(node: PipelineNode) -> str:
    return str(node.attributes.get("instruction", ""))


def _fragment_detail(fragment: TracedFragment, max_output_chars: int) -> list[str]:
    """Return the lines a narrow column cannot hold: a scan's source, then the output."""
    lines = [
        f"      reads  {node.id}  {node.reads}" for node in fragment.nodes if node.reads is not None
    ]
    if fragment.reason is not None:
        lines.append(f"      reason  {fragment.reason}")
    return [*lines, *_output_lines(fragment, max_output_chars, "      ")]


def _output_lines(fragment: TracedFragment, max_output_chars: int, indent: str) -> list[str]:
    """Return a fragment's columns and sample rows, each cut to the cap."""
    if fragment.rows is None:
        return []
    lines = [f"{indent}columns  {', '.join(fragment.rows.columns)}"]
    lines.extend(f"{indent}{row}" for row in fragment.rows.sample_rows)
    return [_capped(line, max_output_chars) for line in lines]


def _capped(line: str, max_output_chars: int) -> str:
    return line if len(line) <= max_output_chars else f"{line[: max_output_chars - 1]}…"


def _print_block(console: Console, name: str, lines: list[str]) -> None:
    """Print what a table cannot hold, under the id of the work it belongs to."""
    if not lines:
        return
    console.print(Text(f"  {name}"))
    for line in lines:
        console.print(Text(line))


def _cover_table(cover: TracedCover) -> Table:
    table = Table("Fragment", "Step", "Instruction", "Input", "Result")
    sections = 0
    for discovery in cover.discoveries:
        if sections:
            table.add_section()
        sections += 1
        node = discovery.node
        table.add_row(
            Text(node.id),
            Text(f"{node.id}\n{node.kind}"),
            Text(node.instruction),
            Text(""),
            Text(discovery.outcome),
        )
    for fragment in cover.fragments:
        if sections:
            table.add_section()
        sections += 1
        _add_fragment(table, fragment)
    return table


def _add_fragment(table: Table, fragment: TracedFragment) -> None:
    # Every cell is a Text: the instructions and the ids are model-authored, and Rich would
    # read a square bracket in one as markup.
    name = Text(f"{fragment.id}\n{fragment.operator_id}")
    inputs = Text("\n".join(f"from {one}" for one in fragment.input_fragment_ids))
    outcome = Text(fragment.outcome)
    for place, node in enumerate(fragment.nodes):
        table.add_row(
            name if place == 0 else Text(""),
            Text(f"{node.id}\n{node.kind}"),
            Text(node.instruction),
            inputs if place == 0 else Text(""),
            outcome if place == 0 else Text(""),
        )
