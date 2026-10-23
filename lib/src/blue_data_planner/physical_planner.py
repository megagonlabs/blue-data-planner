"""Cutting a plan into fragments and binding each one to an operator that reads its scans."""

from __future__ import annotations

import json
import uuid
from typing import Any

from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import render_prompt
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.plan import (
    NONE,
    REUSE,
    candidate_fragments,
    candidate_label,
    cover_choice_schema,
    fragment_frontier,
    validate_physical_plan,
)
from blue_data_planner.plan.fragments import is_single_exit
from blue_data_planner.prompts import prompt_text
from blue_data_planner.prompts.rendering import render_attribute_views, render_sample_rows
from blue_data_planner.types import (
    INPUT_ARITY,
    BlueDataPlannerError,
    DiscoveryResult,
    ExecutionResult,
    FailedFragment,
    InputData,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    ParameterAttempt,
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
        request: str,
        plan: LogicalPlan,
        conversation: Conversation,
        previous: PhysicalPlan | None,
        results: dict[str, NodeResult],
        failed_fragment: FailedFragment | None = None,
    ) -> PhysicalPlanResult:
        """Write a whole cover for this plan, appending to the conversation this cover continues."""
        carried = _carried_cover(plan, previous, results)
        if carried is not None:
            return PhysicalPlanResult(plan=carried, failure_reason=None)
        return self._walk(request, plan, conversation, previous, results, failed_fragment)

    def _walk(
        self,
        request: str,
        plan: LogicalPlan,
        conversation: Conversation,
        previous: PhysicalPlan | None,
        results: dict[str, NodeResult],
        failed_fragment: FailedFragment | None,
    ) -> PhysicalPlanResult:
        confined_to = _coverable(plan)
        entity_source_kinds = _entity_source_kinds(results)
        _check_scan_sources_discovered(plan, entity_source_kinds)
        opening = not conversation.turns
        if opening:
            conversation.turns.append(
                Turn(role="system", content=prompt_text("physical_planner.md"))
            )
        # Every call sends the run's state with its first candidate turn, since a repair follows
        # fragments that ran after the conversation opened.
        preamble = {**_EMPTY_PREAMBLE, "node_results": _render_results(results)}
        if opening:
            preamble["request"] = request
            preamble["plan"] = _render_plan(plan)
            preamble["scanned_entities"] = _render_scanned_entities(plan, results)
        if failed_fragment is not None:
            preamble["failed_fragment"] = _render_failed_fragment(failed_fragment)
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
                    failure_reason=self._no_candidate_reason(plan, node, entity_source_kinds),
                )
            reusable = _reusable_fragment(plan, previous, results, node.id)
            bound = self._bind(
                plan,
                node,
                candidates,
                reusable,
                results,
                conversation,
                preamble,
                bound_so_far,
            )
            # Only the first candidate turn of this call carries the preamble.
            preamble = dict(_EMPTY_PREAMBLE)
            if isinstance(bound, str):
                # A binding turning down every candidate (ADR-040) ends the cover the same way
                # a node with no candidate at all does: no plan, no operator call spent.
                return PhysicalPlanResult(
                    plan=None,
                    failure_reason=f"the binding for node {node.id!r} took none of the "
                    f"candidates offered: {bound}",
                )
            fragments.append(bound)
            covered.update(bound.logical_node_ids)
            for covered_node_id in bound.logical_node_ids:
                bound_so_far[covered_node_id] = bound
        cover = PhysicalPlan(
            id=str(uuid.uuid4()),
            parent_id=_parent_id(plan, previous),
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
    ) -> PhysicalPlanFragment | str:
        conversation.turns.append(
            Turn(
                role="user",
                content=render_prompt(
                    prompt_text("physical_planner_turn.md"),
                    candidates=_render_candidates(plan, node, candidates, reusable, bound_so_far),
                    **preamble,
                ),
            )
        )
        schema = cover_choice_schema(plan, node.id, candidates, reusable)
        written = self._llm_client.complete_structured(conversation, schema)
        choice: dict[str, Any] = written.model_dump()["choice"]
        label = str(choice["candidate"])
        if label == NONE:
            return str(choice["reason"])
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

    def _no_candidate_reason(
        self, plan: LogicalPlan, node: PipelineNode, entity_source_kinds: dict[str, str]
    ) -> str:
        operators = (
            self._router.operators_for(node.kind) if isinstance(node.kind, LogicalOperator) else []
        )
        if not operators:
            return (
                f"no operator can run node {node.id!r} of kind {node.kind.value}, "
                "alone or in any fragment containing it"
            )
        # An operator declaring the kind always yields the candidate holding the root alone, so
        # when every candidate was dropped, every operator turned down the root on its own: for
        # the source a SCAN reads, or for a table it would be handed and cannot take.
        root_alone = frozenset({node.id})
        if node.kind is LogicalOperator.SCAN and not any(
            _reads_its_scans(plan, root_alone, operator, entity_source_kinds)
            for operator in operators
        ):
            return _no_source_reason(node, operators, entity_source_kinds)
        bound_inputs = _bound_inputs(plan, root_alone)
        if not bound_inputs:
            frontier = fragment_frontier(plan, root_alone)
            if frontier and all(
                operator.source_kinds is not None and not operator.accepts_bound_inputs
                for operator in operators
            ):
                return (
                    f"node {node.id!r} of kind {node.kind.value} receives {frontier} from other "
                    "fragments, but its registered source-reading operators cannot take input "
                    "tables. Group it with its source scan or use an operator that reads tables."
                )
            raise BlueDataPlannerError(
                f"node {node.id!r} reads a source its operators read and takes no bound input, "
                "yet none of its operators was offered as a candidate"
            )
        return (
            f"node {node.id!r} of kind {node.kind.value} lists {bound_inputs} as reference "
            f"inputs, and no registered {node.kind.value} operator can read a reference input. "
            f"Get what {node.id!r} needs from them another way."
        )


def _render_scanned_entities(plan: LogicalPlan, results: dict[str, NodeResult]) -> str:
    matches = {
        match.path: match
        for result in results.values()
        if isinstance(result, DiscoveryResult)
        for match in result.matches
    }
    lines: list[str] = []
    for path in _scanned_paths(plan):
        # _check_scan_sources_discovered has already raised on a path no search found.
        match = matches[path]
        described = match.description or "no description recorded"
        rows = (
            f"{match.row_count} row(s)" if match.row_count is not None else "row count not recorded"
        )
        lines.append(f"- {match.path} ({match.source_kind}, {rows}): {described}")
        lines.extend(render_attribute_views(match.attributes, "    ", ATTRIBUTE_VALUES))
    return "\n".join(lines)


def _scanned_paths(plan: LogicalPlan) -> list[str]:
    paths: list[str] = []
    for node in plan.nodes_in_dependency_order():
        source = node.attributes.get("source") if node.kind is LogicalOperator.SCAN else None
        if source is not None and source not in paths:
            paths.append(source)
    return paths


# What a candidate turn substitutes when its section has nothing to show. The opening turn
# overrides the sections it fills, so every turn's preamble has the same keys.
_EMPTY_PREAMBLE: dict[str, str] = {
    "request": "",
    "plan": "",
    "scanned_entities": "",
    "node_results": "",
    "failed_fragment": "",
}

# How many rows of a result one prompt shows, the same as the logical planner's sample.
SAMPLE_ROWS = 3
# How many of an attribute's values one prompt shows: more than the logical planner's, since
# only the entities the plan scans are shown (design/13-decision-log.md, ADR-049).
ATTRIBUTE_VALUES = 5

_FAILED_ROWS_NOTE = (
    "No rows above can be read or reused; they are shown only to explain the failures."
)


def _render_failed_fragment(failed: FailedFragment) -> str:
    fragment = failed.fragment
    reason = failed.result.failure_reason
    if reason is None:
        raise BlueDataPlannerError(
            f"fragment {fragment.id!r} is handed to a cover repair with no failure reason; "
            "a fragment ending needs_repair always carries one"
        )
    lines = [f"Fragment {fragment.id}, bound to {fragment.operator_id}, failed. {reason}"]
    attempts = failed.result.attempts
    for number, attempt in enumerate(attempts, start=1):
        lines.extend(["", f"Attempt {number} (failed)"])
        lines.extend(_render_attempt(attempt, attempts[: number - 1]))
    if any(attempt.result is not None and attempt.result.row_count for attempt in attempts):
        lines.extend(["", _FAILED_ROWS_NOTE])
    return "\n".join(lines)


def _render_attempt(attempt: ParameterAttempt, earlier: list[ParameterAttempt]) -> list[str]:
    # Worded without the fragment runner's review vocabulary: the physical planner never saw
    # a review, and "rejected" or "accepted" could be read as something the data says.
    call = attempt.result
    if call is None:
        return [f"  Not run: its parameters repeat attempt {_repeated(attempt, earlier)}."]
    lines = [f"  Parameters: {json.dumps(attempt.parameters, ensure_ascii=False)}"]
    if call.error is not None:
        lines.append(f"  The call failed: {call.error.message}")
        return lines
    wrong = "" if attempt.assessment is None else f": {attempt.assessment}"
    if not call.row_count or not call.output:
        lines.append(f"  It returned no rows, which does not do what the fragment asked{wrong}")
        return lines
    summary = summarize_table(call.output[0], SAMPLE_ROWS)
    lines.append(
        f"  It returned {call.row_count} row(s), columns {summary.columns}, but they do not do "
        f"what the fragment asked{wrong}"
    )
    lines.extend(render_sample_rows(summary, "    "))
    return lines


def _repeated(attempt: ParameterAttempt, earlier: list[ParameterAttempt]) -> int:
    for number, one in enumerate(earlier, start=1):
        if one.result is not None and one.parameters == attempt.parameters:
            return number
    raise BlueDataPlannerError(
        "an attempt that never ran has parameters no earlier attempt ran with; the fragment "
        "runner skips a call only when its parameters repeat an earlier one's"
    )


def _carried_cover(
    plan: LogicalPlan, previous: PhysicalPlan | None, results: dict[str, NodeResult]
) -> PhysicalPlan | None:
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
    # A continuation cannot rewrite a node it carried over, so each fragment stays connected.
    # It can add a reader of a grouped node, a DISCOVER node included, and the cut then has to
    # be made again.
    if not all(_still_single_exit(plan, fragment) for fragment in finished):
        return None
    return PhysicalPlan(
        id=str(uuid.uuid4()),
        parent_id=_parent_id(plan, previous),
        logical_plan_id=plan.id,
        fragments=finished,
    )


def _reusable_fragment(
    plan: LogicalPlan,
    previous: PhysicalPlan | None,
    results: dict[str, NodeResult],
    node_id: str,
) -> PhysicalPlanFragment | None:
    # A node id reaches results only because a fragment rooted there already produced
    # something, so no signature knowledge is needed here to offer it back.
    if previous is None or node_id not in results:
        return None
    fragment = previous.fragment_for_root(node_id)
    if fragment is None or not _still_single_exit(plan, fragment):
        return None
    return fragment


def _still_single_exit(plan: LogicalPlan, fragment: PhysicalPlanFragment) -> bool:
    return is_single_exit(plan, frozenset(fragment.logical_node_ids), fragment.root_logical_node_id)


def _parent_id(plan: LogicalPlan, previous: PhysicalPlan | None) -> str | None:
    # A cover replaces only an earlier cover of the same logical plan. A new plan version still
    # reads the previous cover, for the fragments it can carry over, but starts a lineage of its
    # own.
    if previous is None or previous.logical_plan_id != plan.id:
        return None
    return previous.id


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
    if not _reads_its_scans(plan, node_ids, operator, entity_source_kinds):
        return False
    # A source-reading operator that declines input tables cannot handle a frontier even
    # when this cut contains no SCAN and those rows are ordinary transformed inputs. Checking
    # only _bound_inputs would still offer blue.nl2sql table-only FILTER/AGGREGATE cuts (#52).
    if operator.source_kinds is not None and not operator.accepts_bound_inputs:
        return not fragment_frontier(plan, node_ids)
    return operator.accepts_bound_inputs or not _bound_inputs(plan, node_ids)


def _reads_its_scans(
    plan: LogicalPlan,
    node_ids: frozenset[str],
    operator: PhysicalOperator,
    entity_source_kinds: dict[str, str],
) -> bool:
    # Every SCAN in the candidate names the source it reads, and the operator's declared
    # source kinds decide whether it can (10-data-model.md, the Sources rule): an operator
    # declaring source kinds reads every scan only when each names a source of one of them,
    # and an operator declaring none reads every scan only when each names no source at all.
    scans = _scans(plan, node_ids)
    if not scans:
        return True
    if operator.source_kinds:
        return all(
            scan.attributes["source"] is not None
            and entity_source_kinds[scan.attributes["source"]] in operator.source_kinds
            for scan in scans
        )
    return all(scan.attributes["source"] is None for scan in scans)


def _bound_inputs(plan: LogicalPlan, node_ids: frozenset[str]) -> list[str]:
    # A frontier table is bound when it arrives alongside a source the fragment reads, or when
    # no node of the fragment transforms it, as with a reference input (ADR-059).
    frontier = fragment_frontier(plan, node_ids)
    if any(scan.attributes["source"] is not None for scan in _scans(plan, node_ids)):
        return frontier
    transformed = {
        input_id for node_id in node_ids for input_id in _transformed_inputs(plan.node(node_id))
    }
    return [frontier_id for frontier_id in frontier if frontier_id not in transformed]


def _transformed_inputs(node: PipelineNode) -> list[str]:
    if not isinstance(node.kind, LogicalOperator):
        raise BlueDataPlannerError(f"node {node.id!r} is a control node inside a fragment")
    return node.input_node_ids[: INPUT_ARITY[node.kind]]


def _scans(plan: LogicalPlan, node_ids: frozenset[str]) -> list[PipelineNode]:
    return [
        plan.node(node_id)
        for node_id in node_ids
        if plan.node(node_id).kind is LogicalOperator.SCAN
    ]


def _no_source_reason(
    node: PipelineNode, operators: list[PhysicalOperator], entity_source_kinds: dict[str, str]
) -> str:
    source = node.attributes["source"]
    reads = (
        f"{source!r} ({entity_source_kinds[source]})" if source is not None else "world knowledge"
    )
    wanted = {kind for operator in operators for kind in operator.source_kinds or frozenset[str]()}
    if any(not operator.source_kinds for operator in operators):
        wanted.add("world knowledge")
    return f"SCAN node {node.id!r} reads {reads}, and the operators for SCAN read {sorted(wanted)}"


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
    bound_so_far: dict[str, PhysicalPlanFragment],
) -> str:
    lines = [
        f"Node {node.id} is uncovered.",
        "",
        "Read by:",
        "",
        _render_readers(plan, node, bound_so_far),
        "",
        "Candidates:",
        "",
    ]
    if reusable is not None:
        lines.append(_render_reuse_candidate(node, reusable))
    for node_ids, operator in candidates:
        label = candidate_label(node.id, operator.operator_id, node_ids)
        limits = f" Cannot: {operator.limitations}" if operator.limitations else ""
        lines.append(f"- {label} — {operator.operator_id}: {operator.description}{limits}")
    return "\n".join(lines)


def _render_readers(
    plan: LogicalPlan, node: PipelineNode, bound_so_far: dict[str, PhysicalPlanFragment]
) -> str:
    # Every reader of an uncovered root is already known: a cover binds a node's readers before
    # the node itself, and the root is its own fragment's only exit
    # (design/04-detailed-architecture.md, "Choosing among candidates").
    readers = plan.consumers(node.id)
    if not readers:
        return "[]"
    # Operator-node readers are keyed by the fragment covering them, so a fragment holding
    # several readers of this root is named once.
    fragments: dict[str, PhysicalPlanFragment] = {}
    non_operator_entries: list[dict[str, Any]] = []
    for reader in readers:
        if not reader.is_operator_node:
            non_operator_entries.append(_render_non_operator_reader(reader))
            continue
        fragment = bound_so_far.get(reader.id)
        if fragment is None:
            raise BlueDataPlannerError(
                f"node {reader.id!r} reads uncovered root {node.id!r}, and no fragment covers "
                f"{reader.id!r} yet; a cover binds every reader of a root before the root"
            )
        fragments[fragment.id] = fragment
    fragment_entries = [
        _render_fragment_reader(node.id, fragment) for fragment in fragments.values()
    ]
    return _render_json_array(fragment_entries + non_operator_entries)


def _render_non_operator_reader(reader: PipelineNode) -> dict[str, Any]:
    if reader.kind is LogicalOperator.RETURN:
        return {
            "id": reader.id,
            "kind": reader.kind.value,
            "columns": reader.attributes["columns"],
        }
    return {"id": reader.id, "kind": reader.kind.value, "instruction": reader.instruction}


def _render_fragment_reader(root_node_id: str, fragment: PhysicalPlanFragment) -> dict[str, Any]:
    received = [entry for entry in fragment.input_data if entry.logical_node_id == root_node_id]
    if len(received) != 1:
        raise BlueDataPlannerError(
            f"fragment {fragment.id!r} covers a node reading {root_node_id!r}, but its input_data "
            f"holds {len(received)} entries for {root_node_id!r}, where exactly one was expected"
        )
    return {
        "fragment": fragment.id,
        "logical_node_ids": sorted(fragment.logical_node_ids),
        "instruction": fragment.instruction,
        "required_output_columns": _columns_as_strings(fragment.required_output_columns),
        "input": {"name": received[0].name, "description": received[0].description},
    }


def _columns_as_strings(columns: list[OutputColumn]) -> list[str]:
    return [f"{column.name}: {column.dtype}" for column in columns]


def _render_json_array(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "[]"
    lines = [json.dumps(entry, ensure_ascii=False) for entry in entries]
    return "[\n  " + ",\n  ".join(lines) + "\n]"


def _render_reuse_candidate(node: PipelineNode, reusable: PhysicalPlanFragment) -> str:
    label = candidate_label(node.id, REUSE, frozenset(reusable.logical_node_ids))
    return (
        f"- {label} — {reusable.operator_id}: reuse the fragment already run, which costs "
        f"nothing. Instruction: {json.dumps(reusable.instruction, ensure_ascii=False)}"
    )


def _render_results(results: dict[str, NodeResult]) -> str:
    lines: list[str] = []
    for node_id, result in results.items():
        if not isinstance(result, ExecutionResult):
            continue
        tables = result.output or []
        summary = summarize_table(tables[0], SAMPLE_ROWS) if tables else None
        columns = summary.columns if summary is not None else []
        lines.append(
            f"- {node_id}: {result.row_count} row(s) from {result.operator_id}, columns {columns}"
        )
        if summary is not None:
            lines.extend(render_sample_rows(summary, "    "))
    return "\n".join(lines)


def _render_plan(plan: LogicalPlan) -> str:
    # The opening turn shows the whole plan, so each binding is made with every node in view
    # (design/04-detailed-architecture.md, "Choosing among candidates").
    nodes = [_plan_node_payload(node) for node in plan.nodes_in_dependency_order()]
    return _render_json_array(nodes)


def _plan_node_payload(node: PipelineNode) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": node.id,
        "kind": node.kind.value,
        "input_node_ids": node.input_node_ids,
    }
    if node.kind is LogicalOperator.SCAN:
        payload["instruction"] = node.instruction
        payload["source"] = node.attributes["source"]
    elif node.kind is LogicalOperator.RETURN:
        payload["columns"] = node.attributes["columns"]
    else:
        # An operator node other than SCAN, or a control node (DISCOVER, REPLAN).
        payload["instruction"] = node.instruction
        if node.is_operator_node:
            payload["required_output_columns"] = _columns_as_strings(node.required_output_columns)
    return payload
