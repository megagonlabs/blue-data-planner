"""Writing a plan from the request and a preview of the catalog, and rewriting a rejected one."""

from __future__ import annotations

import uuid
from typing import Any

from blue_data_planner.data import DataCatalog
from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import render_prompt
from blue_data_planner.plan import logical_plan_schema, validate_logical_plan
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import (
    ControlOperator,
    DiscoveryResult,
    ExecutionResult,
    LogicalOperator,
    LogicalPlan,
    LogicalPlanResult,
    OutputColumn,
    PipelineNode,
    PlanValidationError,
    RunCounters,
    RunStatus,
    summarize_table,
)

# How many source records one prompt carries. It decides how much of a registry a prompt
# holds rather than how much work a run may do, so it is not a RunLimits field.
CATALOG_PREVIEW_LIMIT = 20
# How many rows of a carried node's table one prompt shows. A continuation is written
# against these values, since a REPLAN hands the planner nothing of its own.
CARRIED_SAMPLE_ROWS = 3


class LogicalPlanner:
    """Writes a plan, and a continuation for every pass after the first."""

    def __init__(self, llm_client: LLMClient, catalog: DataCatalog) -> None:
        self._llm_client = llm_client
        self._catalog = catalog

    def generate(
        self,
        request: str,
        conversation: Conversation,
        counters: RunCounters,
        parent: LogicalPlan | None = None,
        carried_node_ids: frozenset[str] = frozenset(),
        results: dict[str, ExecutionResult | DiscoveryResult] | None = None,
        reason: str | None = None,
    ) -> LogicalPlanResult:
        """Write a plan, or a continuation of one, correcting it until validation accepts."""
        held = results or {}
        entity_paths = _entity_paths(held)
        if not conversation.turns:
            conversation.turns.append(
                Turn(role="system", content=prompt_text("logical_planner.md"))
            )
        conversation.turns.append(
            Turn(role="user", content=self._ask(request, carried_node_ids, held, reason, parent))
        )
        failure_reason: str | None = None
        for _attempt in range(counters.limits.plan_corrections + 1):
            written = self._llm_client.complete_structured(conversation, logical_plan_schema())
            try:
                plan = _assemble(written.model_dump(), parent, carried_node_ids)
                validate_logical_plan(plan, entity_paths, carried_node_ids)
            except PlanValidationError as error:
                failure_reason = str(error)
                conversation.turns.append(
                    Turn(
                        role="user",
                        content=(
                            "That plan was turned down. Write it again, fixing this and "
                            f"nothing else.\n\n{error}"
                        ),
                    )
                )
                continue
            return LogicalPlanResult(status=RunStatus.OK, plan=plan, failure_reason=None)
        return LogicalPlanResult(
            status=RunStatus.INVALID_PLAN, plan=None, failure_reason=failure_reason
        )

    def _ask(
        self,
        request: str,
        carried_node_ids: frozenset[str],
        results: dict[str, ExecutionResult | DiscoveryResult],
        reason: str | None,
        parent: LogicalPlan | None,
    ) -> str:
        first_pass = parent is None
        return render_prompt(
            prompt_text("logical_planner_turn.md"),
            request=request if first_pass else "",
            catalog_preview=(
                _render_preview(self._catalog.preview(request, CATALOG_PREVIEW_LIMIT))
                if first_pass
                else ""
            ),
            catalog_context=_render_context(results),
            carried_nodes=_render_carried(parent, carried_node_ids, results),
            reason=reason or "",
        )


def _assemble(
    written: dict[str, Any], parent: LogicalPlan | None, carried_node_ids: frozenset[str]
) -> LogicalPlan:
    nodes = [_node(payload) for payload in written["nodes"]]
    if parent is not None:
        written_ids = {node.id for node in nodes}
        # A continuation names an existing id only to read a node it carried over (design/10, the
        # logical half). Letting a written node take one would attach a carried node's name, and
        # the result filed under it, to work nobody planned.
        collisions = sorted(written_ids & carried_node_ids)
        if collisions:
            raise PlanValidationError(
                f"the continuation writes node(s) {collisions}, whose id(s) name nodes it "
                "carried over; name a carried node only to read it, and give new work new ids"
            )
        nodes = [node for node in parent.nodes if node.id in carried_node_ids] + nodes
    return LogicalPlan(
        id=str(uuid.uuid4()),
        parent_id=parent.id if parent is not None else None,
        nodes=nodes,
        output_node_id=written["output_node_id"],
    )


def _entity_paths(results: dict[str, ExecutionResult | DiscoveryResult]) -> frozenset[str]:
    return frozenset(
        match.path
        for result in results.values()
        if isinstance(result, DiscoveryResult)
        for match in result.matches
    )


def _node(payload: dict[str, Any]) -> PipelineNode:
    kind_name: str = payload["kind"]
    if kind_name in {member.value for member in ControlOperator}:
        kind: LogicalOperator | ControlOperator = ControlOperator(kind_name)
        attributes: dict[str, Any] = {"instruction": payload["instruction"]}
        columns: list[OutputColumn] = []
    elif kind_name == LogicalOperator.RETURN.value:
        kind = LogicalOperator.RETURN
        attributes = {"columns": payload["columns"]}
        columns = []
    elif kind_name == LogicalOperator.SCAN.value:
        kind = LogicalOperator.SCAN
        attributes = {"instruction": payload["instruction"], "source": payload["source"]}
        # A SCAN declares no columns of its own; the nodes reading it declare the entity's
        # columns they need (design/13-decision-log.md, ADR-008).
        columns = []
    else:
        kind = LogicalOperator(kind_name)
        attributes = {"instruction": payload["instruction"]}
        columns = [
            OutputColumn(name=column["name"], dtype=column["dtype"])
            for column in payload["required_output_columns"]
        ]
    return PipelineNode(
        id=payload["id"],
        kind=kind,
        # A REPLAN reads nothing, so ReplanNodeModel carries no input_node_ids field at all.
        input_node_ids=[] if kind is ControlOperator.REPLAN else list(payload["input_node_ids"]),
        attributes=attributes,
        required_output_columns=columns,
    )


def _render_preview(preview: Any) -> str:
    lines = [f"{len(preview.sources)} of {preview.total_sources} registered source(s)."]
    for source in preview.sources:
        described = source.description or "no description recorded"
        lines.append(f"- {source.path} ({source.kind}): {described}")
    return "\n".join(lines)


def _render_context(results: dict[str, ExecutionResult | DiscoveryResult]) -> str:
    lines: list[str] = []
    for node_id, result in results.items():
        if not isinstance(result, DiscoveryResult):
            continue
        terms = ", ".join(f'"{term}"' for term in result.search_terms)
        if not result.matches:
            lines.append(f"{node_id} searched for {terms} and found no entity.")
            continue
        lines.append(f"{node_id} searched for {terms} and found:")
        for match in result.matches:
            described = match.description or "no description recorded"
            rows = (
                f"{match.row_count} row(s)"
                if match.row_count is not None
                else "row count not recorded"
            )
            lines.append(f"- {match.path} ({match.source_kind}, {rows}): {described}")
            for attribute in match.attributes:
                dtype = attribute.dtype or "type not recorded"
                note = attribute.description or "no description recorded"
                lines.append(f"    {attribute.name} [{dtype}]: {note}")
    return "\n".join(lines)


def _render_carried(
    parent: LogicalPlan | None,
    carried_node_ids: frozenset[str],
    results: dict[str, ExecutionResult | DiscoveryResult],
) -> str:
    if parent is None or not carried_node_ids:
        return ""
    lines: list[str] = []
    for node in parent.nodes:
        if node.id not in carried_node_ids:
            continue
        held = results.get(node.id)
        if isinstance(held, ExecutionResult):
            lines.extend(_describe_rows(node, held))
        else:
            lines.append(f"- {node.id} ({node.kind.value})")
    return "\n".join(lines)


def _describe_rows(node: PipelineNode, held: ExecutionResult) -> list[str]:
    """Describe one node's rows: how many, which columns, and the first few of them."""
    tables = held.output or []
    # A few rows rather than the table: enough to show how the values vary, short of what a
    # column of long text would cost the prompt.
    summary = summarize_table(tables[0], CARRIED_SAMPLE_ROWS) if tables else None
    if summary is None:
        return [f"- {node.id} ({node.kind.value}): {held.row_count} row(s)"]
    lines = [f"- {node.id} ({node.kind.value}): {held.row_count} row(s), {summary.columns}"]
    lines.extend(f"    {row}" for row in summary.sample_rows)
    remaining = summary.row_count - len(summary.sample_rows)
    if remaining:
        lines.append(f"    (+{remaining} more row(s))")
    return lines
