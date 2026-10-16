"""What a run did or would do: the records behind the log and the plan, and the table they are
drawn in."""

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
    RunStatus,
    TableSummary,
    summarize_table,
    total_cost,
)
from rich.console import Console
from rich.table import Table
from rich.text import Text

from blue_data_planner_cli.output import format_cost, print_json, run_json

# Every string a user reads says "physical plan", the library's own word for a cover.
NOT_REACHED = "not reached under this physical plan"


@dataclass(frozen=True)
class TracedRead:
    """One node another node reads, and the fragment producing it where that is another one."""

    node_id: str
    # None where the node is read inside the same fragment, or is not an operator node.
    fragment_id: str | None


@dataclass(frozen=True)
class TracedNode:
    """One logical node, the nodes it reads, and the source it reads where it scans one."""

    id: str
    kind: str
    instruction: str
    reads: list[TracedRead]
    scan_source: str | None


@dataclass(frozen=True)
class TracedFragment:
    """One fragment of a cover: the nodes it covered, what fed it, and how its call ended."""

    id: str
    operator_id: str
    nodes: list[TracedNode]
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
        heading = f"physical plan {cover.position}   logical plan {cover.logical_plan_id}"
        print_cover(cover, console, heading=heading, max_output_chars=max_output_chars)
        console.print("")
    for line in spend_lines(result):
        console.print(Text(line))


def print_plan(
    result: RunResult, output_format: str, console: Console, *, log: bool = False
) -> None:
    """Print the cover a run of this request would start executing, drawn as the log draws one."""
    if output_format == "json":
        print_json(run_json(result, explain=True, log=log), console)
        return
    current = result.pipeline.current_physical_plan_id
    # A pass that installed no plan has nothing to show; the summary line names how it ended.
    if current is None:
        return
    # The covers written before it are the log's to show, under --log.
    cover = traced_covers(result)[list(result.pipeline.physical_plans).index(current)]
    heading = f"physical plan   logical plan {cover.logical_plan_id}"
    print_cover(cover, console, heading=heading, max_output_chars=None)


def print_cover(
    cover: TracedCover, console: Console, *, heading: str, max_output_chars: int | None
) -> None:
    """Print one cover's table and what its columns cannot hold.

    With no character cap, the cover is one nothing has run under: the results column and the
    rows are left out.
    """
    console.print(heading)
    console.print(_cover_table(cover, show_results=max_output_chars is not None))
    for discovery in cover.discoveries:
        matched = [f"      matched  {one}" for one in discovery.matches]
        _print_block(console, discovery.node.id, matched)
    for fragment in cover.fragments:
        _print_block(console, fragment.id, _fragment_detail(fragment, max_output_chars))


def spend_lines(result: RunResult) -> list[str]:
    """Return what the run spent against its bounds, and why it ended where it did."""
    counters = result.counters
    usage = result.conversations.usage
    lines = [
        f"counters  operator calls {counters.operator_calls}/{counters.limits.operator_calls}   "
        f"failure replans {counters.failure_replans}/{counters.limits.failure_replans}   "
        f"control replans {counters.control_replans}/{counters.limits.control_replans}",
        f"planner   {len(usage)} model calls   {format_cost(total_cost(usage))}",
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
            fragment_by_node_id = {
                node_id: fragment.id
                for fragment in cover.fragments
                for node_id in fragment.logical_node_ids
            }
            traced.append(
                TracedCover(
                    position=position,
                    logical_plan_id=cover.logical_plan_id,
                    discoveries=self._discoveries(
                        plan, signature_by_node_id, fragment_by_node_id, position
                    ),
                    fragments=self._fragments(
                        plan, cover, signature_by_node_id, fragment_by_node_id, position
                    ),
                )
            )
        return traced

    def _discoveries(
        self,
        plan: LogicalPlan,
        signature_by_node_id: dict[str, str],
        fragment_by_node_id: dict[str, str],
        position: int,
    ) -> list[TracedDiscovery]:
        # A search resolves before the plan reading it is written, so discovery leads the cover.
        return [
            self._discovery(
                _traced_node(plan, node.id, fragment_by_node_id, None),
                signature_by_node_id.get(node.id, ""),
                position,
            )
            for node in plan.nodes
            if node.kind is ControlOperator.DISCOVER
        ]

    def _fragments(
        self,
        plan: LogicalPlan,
        cover: PhysicalPlan,
        signature_by_node_id: dict[str, str],
        fragment_by_node_id: dict[str, str],
        position: int,
    ) -> list[TracedFragment]:
        return [
            self._fragment(
                [
                    _traced_node(plan, node_id, fragment_by_node_id, fragment)
                    for node_id in fragment.logical_node_ids
                ],
                fragment,
                signature_by_node_id.get(fragment.root_logical_node_id, ""),
                position,
            )
            for fragment in _in_run_order(plan, cover)
        ]

    def _discovery(self, traced: TracedNode, signature: str, position: int) -> TracedDiscovery:
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
        traced: list[TracedNode],
        fragment: PhysicalPlanFragment,
        signature: str,
        position: int,
    ) -> TracedFragment:
        found = self._fragment_result(signature)
        if found is None:
            return TracedFragment(
                fragment.id, fragment.operator_id, traced, None, None, 0, None, None, None
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
        first = self.first_seen.setdefault(signature, position)
        return None if first == position else first


def _in_run_order(plan: LogicalPlan, cover: PhysicalPlan) -> list[PhysicalPlanFragment]:
    reached = {node.id: place for place, node in enumerate(plan.nodes_in_dependency_order())}
    return sorted(
        cover.fragments, key=lambda one: reached.get(one.root_logical_node_id, len(reached))
    )


def _traced_node(
    plan: LogicalPlan,
    node_id: str,
    fragment_by_node_id: dict[str, str],
    fragment: PhysicalPlanFragment | None,
) -> TracedNode:
    node = plan.node(node_id)
    own_fragment_id = None if fragment is None else fragment.id
    reads: list[TracedRead] = []
    for read_id in node.input_node_ids:
        producer = fragment_by_node_id.get(read_id)
        reads.append(TracedRead(read_id, None if producer == own_fragment_id else producer))
    return TracedNode(
        id=node_id,
        kind=str(node.kind),
        instruction=node.instruction,
        reads=reads,
        scan_source=None if fragment is None else fragment.scan_sources.get(node_id),
    )


def _fragment_detail(fragment: TracedFragment, max_output_chars: int | None) -> list[str]:
    lines = [
        f"      source  {node.id}  {node.scan_source}"
        for node in fragment.nodes
        if node.scan_source is not None
    ]
    if fragment.failure_reason is not None:
        lines.append(f"      reason  {fragment.failure_reason}")
    if max_output_chars is None:
        return lines
    return [*lines, *_output_lines(fragment, max_output_chars, "      ")]


def _output_lines(fragment: TracedFragment, max_output_chars: int, indent: str) -> list[str]:
    if fragment.rows is None:
        return []
    lines = [f"{indent}columns  {', '.join(fragment.rows.columns)}"]
    lines.extend(f"{indent}{row}" for row in fragment.rows.sample_rows)
    return [_capped(line, max_output_chars) for line in lines]


def _capped(line: str, max_output_chars: int) -> str:
    return line if len(line) <= max_output_chars else f"{line[: max_output_chars - 1]}…"


def _print_block(console: Console, name: str, lines: list[str]) -> None:
    if not lines:
        return
    console.print(Text(f"  {name}"))
    for line in lines:
        # Left to the terminal to wrap: Rich breaks at a space, which moves a catalog path to a
        # line of its own and splits it from the node it belongs to when copied.
        console.print(Text(line), soft_wrap=True)


def _cover_table(cover: TracedCover, *, show_results: bool) -> Table:
    table = Table("Fragment", "Node", "Reads", "Instruction", *(["Result"] if show_results else []))
    sections = 0
    for discovery in cover.discoveries:
        if sections:
            table.add_section()
        sections += 1
        ending = [Text(discovery_ending(discovery))] if show_results else []
        table.add_row(Text(discovery.node.id), *_node_cells(discovery.node), *ending)
    for fragment in cover.fragments:
        if sections:
            table.add_section()
        sections += 1
        _add_fragment(table, fragment, show_results=show_results)
    return table


def _add_fragment(table: Table, fragment: TracedFragment, *, show_results: bool) -> None:
    name = Text(f"{fragment.id}\n{fragment.operator_id}")
    ending = Text(fragment_ending(fragment))
    for place, node in enumerate(fragment.nodes):
        first = place == 0
        results = [ending if first else Text("")] if show_results else []
        table.add_row(name if first else Text(""), *_node_cells(node), *results)


def _node_cells(node: TracedNode) -> list[Text]:
    # Every cell is a Text: the instructions and the ids are model-authored, and Rich would
    # read a square bracket in one as markup.
    reads = "\n".join(
        read.node_id if read.fragment_id is None else f"{read.node_id} ({read.fragment_id})"
        for read in node.reads
    )
    return [Text(f"{node.id}\n{node.kind}"), Text(reads), Text(node.instruction)]
