"""Writing a plan from the request and a preview of the catalog, and rewriting a rejected one."""

from __future__ import annotations

import uuid
from typing import Any

from blue_data_planner.data import DataCatalog
from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import safe_substitute
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
    RejectedPlan,
    RunCounters,
    RunStatus,
)

# How many source records one prompt carries. It decides how much of a registry a prompt
# holds rather than how much work a run may do, so it is not a RunLimits field.
CATALOG_PREVIEW_LIMIT = 20


class LogicalPlanner:
    """Writes a plan, and a continuation for every pass after the first."""

    def __init__(self, llm_client: LLMClient, catalog: DataCatalog) -> None:
        self._llm_client = llm_client
        self._catalog = catalog

    def generate(
        self,
        request: str,
        counters: RunCounters,
        parent: LogicalPlan | None = None,
        carried_node_ids: frozenset[str] = frozenset(),
        results: dict[str, ExecutionResult | DiscoveryResult] | None = None,
        reason: str | None = None,
    ) -> LogicalPlanResult:
        """Write a plan, or a continuation of one, correcting it until validation accepts."""
        held = results or {}
        conversation = Conversation(
            turns=[
                Turn(
                    role="system",
                    content=self._prompt(request, carried_node_ids, held, reason, parent),
                )
            ]
        )
        rejected: list[RejectedPlan] = []
        for _attempt in range(counters.limits.plan_corrections + 1):
            written = self._llm_client.complete_structured(conversation, logical_plan_schema())
            plan = _assemble(written.model_dump(), parent, carried_node_ids)
            try:
                validate_logical_plan(plan, carried_node_ids)
            except PlanValidationError as error:
                rejected.append(RejectedPlan(plan=plan, error=str(error)))
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
            return LogicalPlanResult(
                status=RunStatus.OK,
                plan=plan,
                rejected=rejected,
                usage=list(conversation.usage),
            )
        return LogicalPlanResult(
            status=RunStatus.INVALID_PLAN,
            plan=None,
            rejected=rejected,
            usage=list(conversation.usage),
        )

    def _prompt(
        self,
        request: str,
        carried_node_ids: frozenset[str],
        results: dict[str, ExecutionResult | DiscoveryResult],
        reason: str | None,
        parent: LogicalPlan | None,
    ) -> str:
        preview = self._catalog.preview(request, CATALOG_PREVIEW_LIMIT)
        return safe_substitute(
            prompt_text("logical_planner.md"),
            request=request,
            catalog_preview=_render_preview(preview),
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
        carried = [
            node
            for node in parent.nodes
            if node.id in carried_node_ids and node.id not in written_ids
        ]
        nodes = carried + nodes
    return LogicalPlan(
        id=str(uuid.uuid4()),
        parent_id=parent.id if parent is not None else None,
        nodes=nodes,
        output_node_id=written["output_node_id"],
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
        input_node_ids=list(payload["input_node_ids"]),
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
    for result in results.values():
        if not isinstance(result, DiscoveryResult):
            continue
        for match in result.matches:
            described = match.description or "no description recorded"
            lines.append(f"- {match.path} ({match.source_kind}): {described}")
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
            columns = sorted(held.output[0][0]) if held.output and held.output[0] else []
            lines.append(f"- {node.id} ({node.kind.value}): {held.row_count} row(s), {columns}")
        else:
            lines.append(f"- {node.id} ({node.kind.value})")
    return "\n".join(lines)
