"""Cutting a plan into fragments and binding each one to an operator that reads its scans."""

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
    ControlOperator,
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
    summarize_table,
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
        carried = _carried_cover(plan, previous, results)
        if carried is not None:
            return PhysicalPlanResult(plan=carried, failure_reason=None)
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
        _check_scan_sources_discovered(plan, entity_source_kinds)
        opening = not conversation.turns
        if opening:
            conversation.turns.append(
                Turn(role="system", content=prompt_text("physical_planner.md"))
            )
        # What changed since the last call rides on this call's first candidate turn, so no two
        # user turns ever sit next to each other.
        preamble = {
            "plan": _render_plan(plan) if opening else "",
            "catalog_context": _render_context(results) if opening else "",
            "node_results": _render_results(results) if opening else "",
            "reason": reason or "",
        }
        fragments: list[PhysicalPlanFragment] = []
        covered: set[str] = set()
        # Every node id a fragment bound so far covers, root or grouped, so a later root's
        # readers can be rendered from what is already bound.
        bound_so_far: dict[str, PhysicalPlanFragment] = {}
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
                conversation,
                preamble,
                bound_so_far,
            )
            # The preamble rides on the first candidate turn of this call only.
            preamble = {"plan": "", "catalog_context": "", "node_results": "", "reason": ""}
            fragments.append(fragment)
            covered.update(fragment.logical_node_ids)
            for covered_node_id in fragment.logical_node_ids:
                bound_so_far[covered_node_id] = fragment
        cover = PhysicalPlan(
            id=str(uuid.uuid4()),
            parent_id=previous.id if previous is not None else None,
            logical_plan_id=plan.id,
            fragments=fragments,
        )
        validate_physical_plan(plan, cover)
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
        conversation: Conversation,
        preamble: dict[str, str],
        bound_so_far: dict[str, PhysicalPlanFragment],
    ) -> PhysicalPlanFragment:
        conversation.turns.append(
            Turn(
                role="user",
                content=safe_substitute(
                    prompt_text("physical_planner_turn.md"),
                    candidates=_render_candidates(
                        plan, node, candidates, reusable, results, bound_so_far
                    ),
                    **preamble,
                ),
            )
        )
        schema = cover_choice_schema(plan, node.id, candidates, reusable)
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
        declared = choice["required_output_columns"]
        return PhysicalPlanFragment(
            id=f"f{uuid.uuid4().hex[:8]}",
            logical_node_ids=sorted(node_ids),
            root_logical_node_id=node.id,
            operator_id=operator.operator_id,
            scan_sources=_scan_sources(plan, node_ids),
            instruction=choice["instruction"],
            input_data=[
                InputData(
                    logical_node_id=frontier_id,
                    name=choice[f"input_name_{frontier_id}"],
                    description=choice[f"input_description_{frontier_id}"],
                )
                for frontier_id in fragment_frontier(plan, node_ids)
            ],
            required_output_columns=[
                OutputColumn(name=column["name"], dtype=column["dtype"]) for column in declared
            ],
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
        # An operator declaring the kind always yields the candidate holding the root alone, and a
        # candidate with no SCAN is always bindable, so when every candidate was dropped the root
        # is necessarily the SCAN itself.
        source = node.attributes["source"]
        reads = (
            f"{source!r} ({entity_source_kinds[source]})"
            if source is not None
            else "world knowledge"
        )
        wanted = {
            kind for operator in operators for kind in operator.source_kinds or frozenset[str]()
        }
        if any(not operator.source_kinds for operator in operators):
            wanted.add("world knowledge")
        return (
            f"SCAN node {node.id!r} reads {reads}, and the operators for SCAN read {sorted(wanted)}"
        )


def _carried_cover(
    plan: LogicalPlan, previous: PhysicalPlan | None, results: dict[str, NodeResult]
) -> PhysicalPlan | None:
    """Return the previous cover under this plan's id, when every node it covers has already run."""
    # A continuation rewriting only the ending asks for no work at all, and offering each node
    # whose rows the run already holds back to the model costs one call per fragment to be told
    # what the results already say.
    if previous is None:
        return None
    finished = [
        fragment
        for fragment in previous.fragments
        if isinstance(results.get(fragment.root_logical_node_id), ExecutionResult)
    ]
    covered = frozenset(node_id for fragment in finished for node_id in fragment.logical_node_ids)
    if covered != _coverable(plan):
        return None
    # Nothing is validated again: these fragments passed on the cover that ran them, and a
    # continuation may not rewrite a node it carried over, so what they cover is unchanged.
    return PhysicalPlan(
        id=str(uuid.uuid4()),
        parent_id=previous.id,
        logical_plan_id=plan.id,
        fragments=finished,
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


def _check_scan_sources_discovered(plan: LogicalPlan, entity_source_kinds: dict[str, str]) -> None:
    # The logical planner validates every source against the same results, so a miss here
    # means the caller passed results that do not belong to this plan.
    for node in plan.nodes:
        if node.kind is not LogicalOperator.SCAN:
            continue
        source = node.attributes["source"]
        if source is not None and source not in entity_source_kinds:
            raise BlueDataPlannerError(
                f"SCAN node {node.id!r} names source {source!r}, which no discovery result "
                "passed to the physical planner found"
            )


def _bindable(
    plan: LogicalPlan,
    node_ids: frozenset[str],
    operator: PhysicalOperator,
    entity_source_kinds: dict[str, str],
) -> bool:
    # Every SCAN in the candidate names the source it reads, and the operator's declared
    # source kinds decide whether it can (10-data-model.md, the Sources rule): an operator
    # declaring source kinds reads every scan only when each names a source of one of them,
    # and an operator declaring none reads every scan only when each names no source at all.
    scans = [
        plan.node(node_id)
        for node_id in node_ids
        if plan.node(node_id).kind is LogicalOperator.SCAN
    ]
    if not scans:
        return True
    if operator.source_kinds:
        return all(
            scan.attributes["source"] is not None
            and entity_source_kinds[scan.attributes["source"]] in operator.source_kinds
            for scan in scans
        )
    return all(scan.attributes["source"] is None for scan in scans)


def _scan_sources(plan: LogicalPlan, node_ids: frozenset[str]) -> dict[str, str]:
    # A world-knowledge scan reads no registered source, so it has no entry.
    return {
        node_id: source
        for node_id in sorted(node_ids)
        if plan.node(node_id).kind is LogicalOperator.SCAN
        and (source := plan.node(node_id).attributes["source"]) is not None
    }


def _render_candidates(
    plan: LogicalPlan,
    node: PipelineNode,
    candidates: list[Candidate],
    reusable: PhysicalPlanFragment | None,
    results: dict[str, NodeResult],
    bound_so_far: dict[str, PhysicalPlanFragment],
) -> str:
    lines = [
        f"Node {node.id} is uncovered. Its instruction: {node.attributes['instruction']}",
        _render_root_and_readers(plan, node, bound_so_far),
        "",
    ]
    if reusable is not None:
        lines.append(_render_reuse_candidate(node, reusable, results[node.id]))
    for node_ids, operator in candidates:
        label = candidate_label(node.id, operator.operator_id, node_ids)
        steps = "; ".join(_render_step(plan.node(inner)) for inner in sorted(node_ids))
        limits = f" Cannot: {operator.limitations}" if operator.limitations else ""
        lines.append(
            f"{label}. {operator.operator_id} over {sorted(node_ids)} — "
            f"{operator.description}{limits}\n   Steps: {steps}"
        )
    return "\n".join(lines)


def _render_root_and_readers(
    plan: LogicalPlan, node: PipelineNode, bound_so_far: dict[str, PhysicalPlanFragment]
) -> str:
    # A fragment rooted here has to produce what the root declares plus what every reader of it
    # uses (design/04-detailed-architecture.md, "Choosing among candidates"). A SCAN declares no
    # columns of its own, so for one the readers are the whole of it.
    lines: list[str] = []
    if node.kind is not LogicalOperator.SCAN:
        lines.append(f"Declared output columns: {_render_columns(node.required_output_columns)}")
    readers = plan.consumers(node.id)
    if not readers:
        lines.append(
            "Nothing in this plan reads this node yet; write the columns the next pass will need."
        )
        return "\n".join(lines)
    lines.append(f"Nodes reading {node.id}:")
    # Operator-node readers are grouped by the fragment covering them, so a fragment holding
    # several readers of this root is described once.
    fragments: dict[str, PhysicalPlanFragment] = {}
    readers_by_fragment: dict[str, list[PipelineNode]] = {}
    for reader in readers:
        if not reader.is_operator_node:
            continue
        fragment = bound_so_far.get(reader.id)
        if fragment is None:
            raise BlueDataPlannerError(
                f"node {reader.id!r} reads uncovered root {node.id!r}, but no fragment covers "
                f"{reader.id!r} yet; binding runs readers first, so every one is already bound"
            )
        fragments[fragment.id] = fragment
        readers_by_fragment.setdefault(fragment.id, []).append(reader)
    lines.extend(
        _render_fragment_reader(node.id, fragments[fragment_id], grouped)
        for fragment_id, grouped in readers_by_fragment.items()
    )
    lines.extend(
        _render_non_operator_reader(reader) for reader in readers if not reader.is_operator_node
    )
    return "\n".join(lines)


def _render_non_operator_reader(reader: PipelineNode) -> str:
    if reader.kind is LogicalOperator.RETURN:
        return f"- {reader.id} RETURN: the answer carries {', '.join(reader.attributes['columns'])}"
    lead = ", searching for" if reader.kind is ControlOperator.DISCOVER else ""
    return f"- {reader.id} {reader.kind.value}{lead}: {reader.instruction}"


def _render_fragment_reader(
    root_node_id: str, fragment: PhysicalPlanFragment, readers: list[PipelineNode]
) -> str:
    received = [entry for entry in fragment.input_data if entry.logical_node_id == root_node_id]
    if len(received) != 1:
        raise BlueDataPlannerError(
            f"fragment {fragment.id!r} covers a node reading {root_node_id!r}, but its input_data "
            f"holds {len(received)} entries for {root_node_id!r}, where exactly one was expected"
        )
    return (
        f"- {_join_words([reader.id for reader in readers])}, in fragment {fragment.id} rooted at "
        f'{fragment.root_logical_node_id}. Its instruction: "{fragment.instruction}" '
        f"It produces: {_render_columns(fragment.required_output_columns)}. "
        f"It receives {root_node_id} as: {received[0].name} ({received[0].description})"
    )


def _join_words(words: list[str]) -> str:
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _render_columns(columns: list[OutputColumn]) -> str:
    return ", ".join(f"{column.name} ({column.dtype})" for column in columns) or "none"


def _render_step(node: PipelineNode) -> str:
    step = f"{node.id} ({node.kind.value}) {node.attributes['instruction']}"
    if node.kind is not LogicalOperator.SCAN:
        return step
    source = node.attributes["source"]
    return f"{step} (reads {source if source is not None else 'no registered source'})"


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
    tables = result.output or []
    columns = summarize_table(tables[0], 0).columns if tables else []
    return f"{result.row_count} row(s) from {result.operator_id}, columns {columns}"


def _render_plan(plan: LogicalPlan) -> str:
    # The opening turn shows the whole plan, so each binding is made with every node in view
    # (design/04-detailed-architecture.md, "Choosing among candidates").
    return "\n".join(_render_plan_node(node) for node in plan.nodes_in_dependency_order())


def _render_plan_node(node: PipelineNode) -> str:
    if node.kind is LogicalOperator.SCAN:
        source = node.attributes["source"]
        reads = f" of {source if source is not None else 'no registered source'}"
    elif node.input_node_ids:
        reads = f" of {', '.join(node.input_node_ids)}"
    else:
        reads = ""
    head = f"- {node.id} {node.kind.value}{reads}"
    if node.kind is LogicalOperator.RETURN:
        return f"{head}: the answer carries {', '.join(node.attributes['columns'])}"
    if not node.is_operator_node or node.kind is LogicalOperator.SCAN:
        return f"{head}: {node.instruction}"
    columns = _render_columns(node.required_output_columns)
    return f"{head}: {node.instruction.rstrip('.')}. Outputs: {columns}"
