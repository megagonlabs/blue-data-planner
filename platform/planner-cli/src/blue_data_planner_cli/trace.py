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

# Every string a user reads says "physical plan", the library's own word for a cover.
NOT_REACHED = "not reached under this physical plan"


@dataclass(frozen=True)
class TracedNode:
    """One logical node, and the source it reads where it scans one."""

    id: str
    kind: str
    instruction: str
    scan_source: str | None


@dataclass(frozen=True)
class TracedFragment:
    """One fragment of a cover: the nodes it covered, what fed it, and how its call ended."""

    id: str
    operator_id: str
    nodes: list[TracedNode]
    input_fragment_ids: list[str]
    # None where the pass never reached this fragment under this cover.
    status: RunStatus | None
    row_count: int | None
    # Tables past the first, which the log counts rather than summarizing.
    other_tables: int
    # The cover that first ran this work, where a later cover asked for it again.
    reused_from: int | None
    failure_reason: str | None
    rows: TableSummary | None


@dataclass(frozen=True)
class TracedDiscovery:
    """One DISCOVER node the cover's plan carries, and what its search matched."""

    node: TracedNode
    # None where the search was not run under this cover.
    match_count: int | None
    dropped_terms: int
    reused_from: int | None
    # Empty where the result was reused, since the cover that ran it already listed them.
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
    return _CoverWalk(result).covers()


def print_log(result: RunResult, console: Console, *, max_output_chars: int) -> None:
    """Print the log as one table per cover, with what a table cannot hold beneath it."""
    for cover in traced_covers(result):
        console.print(f"physical plan {cover.position}   logical plan {cover.logical_plan_id}")
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


def fragment_ending(fragment: TracedFragment) -> str:
    """Return how one fragment ended, as its row of the table reads."""
    if fragment.status is None:
        return NOT_REACHED
    parts = [str(fragment.status)]
    if fragment.row_count is not None:
        parts.append(f"{fragment.row_count} rows")
    return "  ".join([*parts, *_notes(fragment.other_tables, fragment.reused_from)])


def discovery_ending(discovery: TracedDiscovery) -> str:
    """Return how one search ended, as its row of the table reads."""
    if discovery.match_count is None:
        return NOT_REACHED
    dropped = f"({discovery.dropped_terms} terms dropped)" if discovery.dropped_terms else ""
    parts = ["ok", f"{discovery.match_count} matches", dropped]
    return "  ".join([one for one in parts if one] + _notes(0, discovery.reused_from))


def _notes(other_tables: int, reused_from: int | None) -> list[str]:
    notes: list[str] = []
    if other_tables:
        notes.append(f"(+{other_tables} more tables)")
    if reused_from is not None:
        notes.append(f"(reused from physical plan {reused_from})")
    return notes


class _CoverWalk:
    """One pass over the covers, remembering which work an earlier cover already showed."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        # Signature -> the position of the cover that first ran that work.
        self.first_seen: dict[str, int] = {}

    def covers(self) -> list[TracedCover]:
        traced: list[TracedCover] = []
        for position, cover in enumerate(self.result.pipeline.physical_plans.values(), start=1):
            plan = self.result.pipeline.logical_plans.get(cover.logical_plan_id)
            if plan is None:
                traced.append(TracedCover(position, cover.logical_plan_id, [], []))
                continue
            # Signing a cover walks the whole plan, so it is done once and read twice.
            signature_by_node_id = signatures(plan, cover)
            traced.append(
                TracedCover(
                    position=position,
                    logical_plan_id=cover.logical_plan_id,
                    discoveries=self._discoveries(plan, signature_by_node_id, position),
                    fragments=self._fragments(plan, cover, signature_by_node_id, position),
                )
            )
        return traced

    def _discoveries(
        self, plan: LogicalPlan, signature_by_node_id: dict[str, str], position: int
    ) -> list[TracedDiscovery]:
        # A search resolves before the plan reading it is written, so discovery leads the cover.
        return [
            self._discovery(node, signature_by_node_id.get(node.id, ""), position)
            for node in plan.nodes
            if node.kind is ControlOperator.DISCOVER
        ]

    def _fragments(
        self,
        plan: LogicalPlan,
        cover: PhysicalPlan,
        signature_by_node_id: dict[str, str],
        position: int,
    ) -> list[TracedFragment]:
        roots = {one.root_logical_node_id: one.id for one in cover.fragments}
        return [
            self._fragment(
                plan,
                fragment,
                roots,
                signature_by_node_id.get(fragment.root_logical_node_id, ""),
                position,
            )
            for fragment in _in_run_order(plan, cover)
        ]

    def _discovery(self, node: PipelineNode, signature: str, position: int) -> TracedDiscovery:
        traced = _traced_discover_node(node)
        found = self.result.results.get(signature)
        if not isinstance(found, DiscoveryResult):
            return TracedDiscovery(traced, None, 0, None, [])
        reused_from = self._first_seen_in(signature, position)
        return TracedDiscovery(
            node=traced,
            match_count=len(found.matches),
            dropped_terms=found.dropped_terms,
            reused_from=reused_from,
            matches=[] if reused_from is not None else [match.path for match in found.matches],
        )

    def _fragment(
        self,
        plan: LogicalPlan,
        fragment: PhysicalPlanFragment,
        roots: dict[str, str],
        signature: str,
        position: int,
    ) -> TracedFragment:
        traced = [_traced_node(plan, node_id, fragment) for node_id in fragment.logical_node_ids]
        inputs = [
            roots.get(one.logical_node_id, one.logical_node_id) for one in fragment.input_data
        ]
        found = self._fragment_result(signature)
        if found is None:
            return TracedFragment(
                fragment.id, fragment.operator_id, traced, inputs, None, None, 0, None, None, None
            )
        call = found.result
        tables = [] if call is None else call.output or []
        # Only an accepted result is reused: a rejected one is offered again, so naming it as
        # reuse would say a call was saved that was not.
        reused_from = (
            self._first_seen_in(signature, position) if found.status is RunStatus.OK else None
        )
        # A bundle almost always holds one table, so the log shows the first and counts the
        # others rather than nesting a block per table.
        shows_rows = found.status is RunStatus.OK and reused_from is None and bool(tables)
        return TracedFragment(
            id=fragment.id,
            operator_id=fragment.operator_id,
            nodes=traced,
            input_fragment_ids=inputs,
            status=found.status,
            row_count=None if call is None else call.row_count,
            other_tables=max(len(tables) - 1, 0),
            reused_from=reused_from,
            failure_reason=found.failure_reason,
            rows=summarize_table(tables[0], SUMMARY_SAMPLE_ROWS) if shows_rows else None,
        )

    def _fragment_result(self, signature: str) -> FragmentResult | None:
        found = self.result.results.get(signature)
        return found if isinstance(found, FragmentResult) else None

    def _first_seen_in(self, signature: str, position: int) -> int | None:
        """Return the cover that first ran this work, or None where this cover is that one."""
        first = self.first_seen.setdefault(signature, position)
        return None if first == position else first


def _in_run_order(plan: LogicalPlan, cover: PhysicalPlan) -> list[PhysicalPlanFragment]:
    """Return the cover's fragments in the order the run could have reached them."""
    reached = {node.id: place for place, node in enumerate(plan.nodes_in_dependency_order())}
    return sorted(
        cover.fragments, key=lambda one: reached.get(one.root_logical_node_id, len(reached))
    )


def _traced_node(plan: LogicalPlan, node_id: str, fragment: PhysicalPlanFragment) -> TracedNode:
    node = plan.node(node_id)
    return TracedNode(
        id=node_id,
        kind=str(node.kind),
        instruction=node.instruction,
        scan_source=fragment.scan_sources.get(node_id),
    )


def _traced_discover_node(node: PipelineNode) -> TracedNode:
    return TracedNode(
        id=node.id, kind=str(node.kind), instruction=node.instruction, scan_source=None
    )


def _fragment_detail(fragment: TracedFragment, max_output_chars: int) -> list[str]:
    """Return the lines a narrow column cannot hold: a scan's source, then the output."""
    lines = [
        f"      reads  {node.id}  {node.scan_source}"
        for node in fragment.nodes
        if node.scan_source is not None
    ]
    if fragment.failure_reason is not None:
        lines.append(f"      reason  {fragment.failure_reason}")
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
    table = Table("Fragment", "Node", "Instruction", "Input", "Result")
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
            Text(discovery_ending(discovery)),
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
    ending = Text(fragment_ending(fragment))
    for place, node in enumerate(fragment.nodes):
        table.add_row(
            name if place == 0 else Text(""),
            Text(f"{node.id}\n{node.kind}"),
            Text(node.instruction),
            inputs if place == 0 else Text(""),
            ending if place == 0 else Text(""),
        )
