"""Cutting a plan into fragments and binding each one to an operator and an entity."""

from __future__ import annotations

import uuid
from typing import Any

from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import safe_substitute
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.plan import (
    REUSE,
    candidate_fragments,
    candidate_label,
    cover_choice_schema,
    fragment_frontier,
    validate_physical_plan,
)
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import (
    BlueDataPlannerError,
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
        conversation: Conversation,
        previous: PhysicalPlan | None,
        results: dict[str, NodeResult],
        reason: str | None = None,
    ) -> PhysicalPlanResult:
        """Write a whole cover for this plan, appending to the conversation this cover continues."""
        return self._walk(plan, conversation, previous, results, reason)

    def _walk(
        self,
        plan: LogicalPlan,
        conversation: Conversation,
        previous: PhysicalPlan | None,
        results: dict[str, NodeResult],
        reason: str | None,
    ) -> PhysicalPlanResult:
        confined_to = _coverable(plan)
        entity_source_kinds = _entity_source_kinds(results)
        opening = not conversation.turns
        if opening:
            conversation.turns.append(
                Turn(role="system", content=prompt_text("physical_planner.md"))
            )
        # What changed since the last call rides on this call's first candidate turn, so no two
        # user turns ever sit next to each other.
        preamble = {
            "catalog_context": _render_context(results) if opening else "",
            "node_results": _render_results(results) if opening else "",
            "reason": reason or "",
        }
        fragments: list[PhysicalPlanFragment] = []
        covered: set[str] = set()
        for node in _reverse_dependency_order(plan):
            if node.id not in confined_to or node.id in covered:
                continue
            candidates = self._candidates(plan, node.id, confined_to, entity_source_kinds)
            if not candidates:
                return PhysicalPlanResult(
                    plan=None,
                    failure_reason=self._no_candidate_reason(node, entity_source_kinds),
                )
            # A node id reaches results only because a fragment rooted there already produced
            # something, so no signature knowledge is needed here to offer it back.
            reusable = (
                previous.fragment_for_root(node.id)
                if previous is not None and node.id in results
                else None
            )
            fragment = self._bind(
                plan,
                node,
                candidates,
                reusable,
                results,
                entity_source_kinds,
                conversation,
                preamble,
            )
            # The preamble rides on the first candidate turn of this call only.
            preamble = {"catalog_context": "", "node_results": "", "reason": ""}
            fragments.append(fragment)
            covered.update(fragment.logical_node_ids)
        cover = PhysicalPlan(
            id=str(uuid.uuid4()),
            parent_id=previous.id if previous is not None else None,
            logical_plan_id=plan.id,
            fragments=fragments,
        )
        validate_physical_plan(plan, cover, frozenset(entity_source_kinds))
        return PhysicalPlanResult(plan=cover, failure_reason=None)

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
        reusable: PhysicalPlanFragment | None,
        results: dict[str, NodeResult],
        entity_source_kinds: dict[str, str],
        conversation: Conversation,
        preamble: dict[str, str],
    ) -> PhysicalPlanFragment:
        conversation.turns.append(
            Turn(
                role="user",
                content=safe_substitute(
                    prompt_text("physical_planner_turn.md"),
                    candidates=_render_candidates(plan, node, candidates, reusable, results),
                    **preamble,
                ),
            )
        )
        schema = cover_choice_schema(plan, node.id, candidates, reusable, entity_source_kinds)
        written = self._llm_client.complete_structured(conversation, schema)
        choice: dict[str, Any] = written.model_dump()["choice"]
        label = str(choice["candidate"])
        if reusable is not None and label == candidate_label(
            node.id, REUSE, frozenset(reusable.logical_node_ids)
        ):
            return reusable
        by_label = {
            candidate_label(node.id, operator.operator_id, node_ids): (node_ids, operator)
            for node_ids, operator in candidates
        }
        chosen = by_label.get(label)
        if chosen is None:
            raise BlueDataPlannerError(
                f"the binding for node {node.id!r} named candidate {label!r}, which is not one "
                "of the candidates it was offered"
            )
        node_ids, operator = chosen
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
                    logical_node_id=frontier_id,
                    name=choice[f"input_name_{frontier_id}"],
                    description=choice[f"input_description_{frontier_id}"],
                )
                for frontier_id in fragment_frontier(plan, node_ids)
            ],
            required_output_columns=(
                [OutputColumn(name=c["name"], dtype=c["dtype"]) for c in declared]
                if declared
                else list(node.required_output_columns)
            ),
        )

    def _no_candidate_reason(self, node: PipelineNode, entity_source_kinds: dict[str, str]) -> str:
        operators = (
            self._router.operators_for(node.kind) if isinstance(node.kind, LogicalOperator) else []
        )
        if not operators:
            return (
                f"no operator can run node {node.id!r} of kind {node.kind.value}, "
                "alone or in any fragment containing it"
            )
        # An operator declaring the kind is dropped only by the source-kind filter, and that
        # filter drops a candidate only when it holds a SCAN. So the kind is not what left
        # this node uncovered, the discovered sources are, and saying which kinds were wanted
        # is what lets the logical planner search for a different one.
        readable = sorted(
            {kind for operator in operators for kind in operator.source_kinds or frozenset[str]()}
        )
        return (
            f"node {node.id!r} scans a source of a kind no operator reads: the operators for "
            f"{node.kind.value} read {readable}, and discovery found "
            f"{sorted(set(entity_source_kinds.values()))}"
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


def _render_candidates(
    plan: LogicalPlan,
    node: PipelineNode,
    candidates: list[Candidate],
    reusable: PhysicalPlanFragment | None,
    results: dict[str, NodeResult],
) -> str:
    lines = [f"Node {node.id} is uncovered. Its instruction: {node.attributes['instruction']}", ""]
    if reusable is not None:
        lines.append(_render_reuse_candidate(node, reusable, results[node.id]))
    for node_ids, operator in candidates:
        label = candidate_label(node.id, operator.operator_id, node_ids)
        steps = "; ".join(
            f"{inner} ({plan.node(inner).kind.value}) {plan.node(inner).attributes['instruction']}"
            for inner in sorted(node_ids)
        )
        limits = f" Cannot: {operator.limitations}" if operator.limitations else ""
        lines.append(
            f"{label}. {operator.operator_id} over {sorted(node_ids)} — "
            f"{operator.description}{limits}\n   Steps: {steps}"
        )
    return "\n".join(lines)


def _render_reuse_candidate(
    node: PipelineNode, reusable: PhysicalPlanFragment, result: NodeResult
) -> str:
    if not isinstance(result, ExecutionResult):
        raise BlueDataPlannerError(
            f"node {node.id!r} offers a reusable fragment, but the result recorded for it is a "
            f"{type(result).__name__}, not an ExecutionResult"
        )
    label = candidate_label(node.id, REUSE, frozenset(reusable.logical_node_ids))
    return (
        f"{label}. Reuse the fragment already run, {reusable.operator_id} over "
        f"{sorted(reusable.logical_node_ids)} — costs nothing.\n"
        f"   Instruction: {reusable.instruction}\n"
        f"   Already produced: {_describe_execution(result)}"
    )


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
    return "\n".join(
        f"- {node_id}: {_describe_execution(result)}"
        for node_id, result in results.items()
        if isinstance(result, ExecutionResult)
    )


def _describe_execution(result: ExecutionResult) -> str:
    columns = sorted(result.output[0][0]) if result.output and result.output[0] else []
    return f"{result.row_count} row(s) from {result.operator_id}, columns {columns}"
