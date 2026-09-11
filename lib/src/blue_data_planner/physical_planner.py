"""Cutting a plan into fragments and binding each one to an operator and an entity."""

from __future__ import annotations

import uuid
from typing import Any

from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import safe_substitute
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.plan import candidate_fragments, cover_choice_schema, validate_physical_plan
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import (
    DiscoveryResult,
    ExecutionResult,
    InputData,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PhysicalOperator,
    PhysicalPlan,
    PhysicalPlanFragment,
    PhysicalPlanResult,
    PipelineNode,
)

Candidate = tuple[frozenset[str], PhysicalOperator]
NodeResult = ExecutionResult | DiscoveryResult


class PhysicalPlanner:
    """Covers a plan's operator nodes with fragments and binds each fragment to an operator."""

    def __init__(self, llm_client: LLMClient, router: OperatorRouter) -> None:
        self._llm_client = llm_client
        self._router = router

    def cover(
        self,
        plan: LogicalPlan,
        results: dict[str, NodeResult],
        reason: str | None = None,
    ) -> PhysicalPlanResult:
        """Write a whole cover for this plan, which is both the first cover and the repair."""
        return self._walk(plan, results, reason, _coverable(plan), carried=[], parent_id=None)

    def recover_fragment(
        self,
        plan: LogicalPlan,
        previous: PhysicalPlan,
        node_ids: frozenset[str],
        results: dict[str, NodeResult],
        reason: str,
    ) -> PhysicalPlanResult:
        """Cut one node set again, carrying every other fragment across unchanged."""
        carried = [
            fragment
            for fragment in previous.fragments
            if not (set(fragment.logical_node_ids) & node_ids)
        ]
        return self._walk(plan, results, reason, node_ids, carried, parent_id=previous.id)

    def _walk(
        self,
        plan: LogicalPlan,
        results: dict[str, NodeResult],
        reason: str | None,
        confined_to: frozenset[str],
        carried: list[PhysicalPlanFragment],
        parent_id: str | None,
    ) -> PhysicalPlanResult:
        entity_source_kinds = _entity_source_kinds(results)
        conversation = Conversation(
            turns=[Turn(role="system", content=self._prompt(results, reason))]
        )
        fragments = list(carried)
        covered: set[str] = {
            node_id for fragment in carried for node_id in fragment.logical_node_ids
        }
        for node in _reverse_dependency_order(plan):
            if node.id not in confined_to or node.id in covered:
                continue
            candidates = self._candidates(plan, node.id, confined_to, entity_source_kinds)
            if not candidates:
                return PhysicalPlanResult(
                    plan=None,
                    failure_reason=(
                        f"no operator can run node {node.id!r} of kind {node.kind.value}, "
                        "alone or in any fragment containing it"
                    ),
                    usage=list(conversation.usage),
                )
            fragment = self._bind(plan, node, candidates, entity_source_kinds, conversation)
            fragments.append(fragment)
            covered.update(fragment.logical_node_ids)
        cover = PhysicalPlan(
            id=str(uuid.uuid4()),
            parent_id=parent_id,
            logical_plan_id=plan.id,
            fragments=fragments,
        )
        validate_physical_plan(plan, cover, frozenset(entity_source_kinds))
        return PhysicalPlanResult(plan=cover, failure_reason=None, usage=list(conversation.usage))

    def _candidates(
        self,
        plan: LogicalPlan,
        root_node_id: str,
        confined_to: frozenset[str],
        entity_source_kinds: dict[str, str],
    ) -> list[Candidate]:
        kind = plan.node(root_node_id).kind
        if not isinstance(kind, LogicalOperator):
            return []
        candidates: list[Candidate] = []
        for operator in self._router.operators_for(kind):
            for node_ids in candidate_fragments(plan, root_node_id, operator.logical_operators):
                if node_ids <= confined_to and _bindable(
                    plan, node_ids, operator, entity_source_kinds
                ):
                    candidates.append((node_ids, operator))
        # Largest first, so the widest pushdown and the narrowest sit next to each other.
        return sorted(candidates, key=lambda pair: (-len(pair[0]), sorted(pair[0])))

    def _bind(
        self,
        plan: LogicalPlan,
        node: PipelineNode,
        candidates: list[Candidate],
        entity_source_kinds: dict[str, str],
        conversation: Conversation,
    ) -> PhysicalPlanFragment:
        conversation.turns.append(
            Turn(role="user", content=_render_candidates(plan, node, candidates))
        )
        schema = cover_choice_schema(plan, candidates, entity_source_kinds)
        written = self._llm_client.complete_structured(conversation, schema)
        choice: dict[str, Any] = written.model_dump()["choice"]
        # An explicit int() resolves an otherwise ambiguous overload: list.__getitem__ has
        # both an index and a slice form, and indexing with an untyped (Any) value leaves
        # pyright unable to pick one, which taints node_ids and operator as Unknown below.
        node_ids, operator = candidates[int(choice["candidate_index"])]
        declared = choice.get("required_output_columns")
        return PhysicalPlanFragment(
            id=f"f{uuid.uuid4().hex[:8]}",
            logical_node_ids=sorted(node_ids),
            root_logical_node_id=node.id,
            operator_id=operator.operator_id,
            scan_sources={
                key.removeprefix("scan_source_"): value
                for key, value in choice.items()
                if key.startswith("scan_source_")
            },
            instruction=choice["instruction"],
            input_data=[
                InputData(
                    logical_node_id=entry["logical_node_id"],
                    name=entry["name"],
                    description=entry["description"],
                )
                for entry in choice["input_data"]
            ],
            required_output_columns=(
                [OutputColumn(name=c["name"], dtype=c["dtype"]) for c in declared]
                if declared
                else list(node.required_output_columns)
            ),
        )

    def _prompt(self, results: dict[str, NodeResult], reason: str | None) -> str:
        return safe_substitute(
            prompt_text("physical_planner.md"),
            catalog_context=_render_context(results),
            node_results=_render_results(results),
            reason=reason or "",
        )


def _coverable(plan: LogicalPlan) -> frozenset[str]:
    return frozenset(node.id for node in plan.nodes if node.is_operator_node)


def _reverse_dependency_order(plan: LogicalPlan) -> list[PipelineNode]:
    # Consumers before the nodes they read, which is what keeps a candidate rooted at an
    # uncovered node from ever containing a node an earlier fragment already took.
    return [node for node in reversed(plan.nodes_in_dependency_order()) if node.is_operator_node]


def _entity_source_kinds(results: dict[str, NodeResult]) -> dict[str, str]:
    return {
        match.path: match.source_kind
        for result in results.values()
        if isinstance(result, DiscoveryResult)
        for match in result.matches
    }


def _bindable(
    plan: LogicalPlan,
    node_ids: frozenset[str],
    operator: PhysicalOperator,
    entity_source_kinds: dict[str, str],
) -> bool:
    # An operator declaring source kinds needs every scan in its fragment bound to a source
    # of one of them (10-data-model.md, the Sources rule), so a candidate whose scans have
    # nothing to bind to is not a candidate. An operator declaring none reads no registered
    # source and can take any scan.
    if not operator.source_kinds:
        return True
    if not any(plan.node(node_id).kind is LogicalOperator.SCAN for node_id in node_ids):
        return True
    return any(source_kind in operator.source_kinds for source_kind in entity_source_kinds.values())


def _render_candidates(plan: LogicalPlan, node: PipelineNode, candidates: list[Candidate]) -> str:
    lines = [f"Node {node.id} is uncovered. Its instruction: {node.attributes['instruction']}", ""]
    for index, (node_ids, operator) in enumerate(candidates):
        steps = "; ".join(
            f"{inner} ({plan.node(inner).kind.value}) {plan.node(inner).attributes['instruction']}"
            for inner in sorted(node_ids)
        )
        limits = f" Cannot: {operator.limitations}" if operator.limitations else ""
        lines.append(
            f"{index}. {operator.operator_id} over {sorted(node_ids)} — "
            f"{operator.description}{limits}\n   Steps: {steps}"
        )
    return "\n".join(lines)


def _render_context(results: dict[str, NodeResult]) -> str:
    lines: list[str] = []
    for result in results.values():
        if not isinstance(result, DiscoveryResult):
            continue
        for match in result.matches:
            described = match.description or "no description recorded"
            names = ", ".join(attribute.name for attribute in match.attributes)
            lines.append(f"- {match.path} ({match.source_kind}): {described}. Holds: {names}")
    return "\n".join(lines)


def _render_results(results: dict[str, NodeResult]) -> str:
    lines: list[str] = []
    for node_id, result in results.items():
        if not isinstance(result, ExecutionResult):
            continue
        columns = sorted(result.output[0][0]) if result.output and result.output[0] else []
        lines.append(
            f"- {node_id}: {result.row_count} row(s) from {result.operator_id}, columns {columns}"
        )
    return "\n".join(lines)
