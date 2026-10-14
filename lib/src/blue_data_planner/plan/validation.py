"""The structural checks a plan and a cover have to pass before anything runs them."""

from __future__ import annotations

from blue_data_planner.plan.fragments import fragment_frontier
from blue_data_planner.types import (
    INPUT_ARITY,
    BlueDataPlannerError,
    ControlOperator,
    LogicalOperator,
    LogicalPlan,
    PhysicalPlan,
    PhysicalPlanFragment,
    PlanValidationError,
)


def validate_logical_plan(
    plan: LogicalPlan,
    entity_paths: frozenset[str],
    carried_node_ids: frozenset[str] = frozenset(),
    *,
    max_nodes: int,
) -> None:
    """Raise unless the plan is a sound DAG with one ending, enough inputs, and known scans."""
    _check_structure(plan)
    _check_arity(plan)
    _check_replan_count(plan)
    _check_size(plan, max_nodes)
    _check_replan_waits_for_work(plan, carried_node_ids)
    _check_controls_are_terminal(plan)
    terminal_ids = _check_ending(plan, carried_node_ids)
    _check_reachability(plan, terminal_ids, carried_node_ids)
    _check_answer_columns(plan)
    _check_scan_sources(plan, entity_paths)


def validate_physical_plan(plan: LogicalPlan, physical_plan: PhysicalPlan) -> None:
    """Raise unless every operator node is covered exactly once by a legal fragment."""
    _check_coverage(plan, physical_plan)
    for fragment in physical_plan.fragments:
        _check_fragment_shape(plan, fragment)
        _check_input_data(plan, fragment)
        _check_fragment_scan_sources(plan, fragment)


def _check_structure(plan: LogicalPlan) -> None:
    known_ids: set[str] = set()
    for node in plan.nodes:
        if node.id in known_ids:
            raise PlanValidationError(f"node id {node.id!r} appears more than once")
        known_ids.add(node.id)
    for node in plan.nodes:
        for input_id in node.input_node_ids:
            if input_id not in known_ids:
                raise PlanValidationError(
                    f"node {node.id!r} reads {input_id!r}, which the plan does not hold"
                )
    _check_acyclic(plan)


def _check_acyclic(plan: LogicalPlan) -> None:
    # A plan that can be put in dependency order has no cycle, so the ordering the plan
    # already computes for serialization is the check rather than a second graph walk.
    # Ordering a cycle is the only way it fails, and the logical planner regenerates on a
    # PlanValidationError, so the error is restated as one rather than propagated.
    try:
        plan.nodes_in_dependency_order()
    except BlueDataPlannerError as cycle:
        raise PlanValidationError(str(cycle)) from None


def _check_arity(plan: LogicalPlan) -> None:
    for node in plan.nodes:
        if not isinstance(node.kind, LogicalOperator):
            continue
        required = INPUT_ARITY[node.kind]
        given = len(node.input_node_ids)
        # The arity is a floor rather than an equality: a node listing more inputs than it
        # transforms carries reference inputs, whose rows its instruction mentions.
        if given < required:
            raise PlanValidationError(
                f"node {node.id!r} of kind {node.kind.value} takes at least {required} "
                f"input(s), got {given}"
            )


def _check_replan_count(plan: LogicalPlan) -> None:
    replans = [node for node in plan.nodes if node.kind is ControlOperator.REPLAN]
    if len(replans) > 1:
        raise PlanValidationError(
            "a plan takes at most one REPLAN node, since resolving one runs nothing and a "
            f"second asks for the continuation the first already asked for; got {len(replans)}"
        )


def _check_size(plan: LogicalPlan, max_nodes: int) -> None:
    # When each fragment runs one node, a plan with more of these nodes than the operator call
    # budget cannot run to its end (ADR-060).
    counted = [
        node
        for node in plan.nodes
        if node.kind not in (LogicalOperator.RETURN, ControlOperator.REPLAN)
    ]
    if len(counted) > max_nodes:
        raise PlanValidationError(
            f"the plan has {len(counted)} operator and DISCOVER nodes, more than the operator "
            f"call budget of {max_nodes}; write a plan with at most {max_nodes}"
        )


def _check_replan_waits_for_work(plan: LogicalPlan, carried_node_ids: frozenset[str]) -> None:
    replans = [node for node in plan.nodes if node.kind is ControlOperator.REPLAN]
    if not replans:
        return
    written_work = [
        node for node in plan.nodes if node.is_operator_node and node.id not in carried_node_ids
    ]
    if not written_work:
        raise PlanValidationError(
            f"node {replans[0].id!r} is a REPLAN in a plan that writes no step for it to wait "
            "for, so it asks for the continuation it was already asked for; write the step(s) "
            "whose rows the continuation needs, or a DISCOVER node for what the catalog is "
            "missing, or the whole plan from what is already known"
        )


def _check_controls_are_terminal(plan: LogicalPlan) -> None:
    control_ids = {node.id for node in plan.nodes if isinstance(node.kind, ControlOperator)}
    for node in plan.nodes:
        for input_id in node.input_node_ids:
            if input_id in control_ids:
                raise PlanValidationError(
                    f"node {node.id!r} reads control node {input_id!r}, which produces no rows; "
                    "a control node ends the pass and what it produces reaches the next plan"
                )


def _check_answer_columns(plan: LogicalPlan) -> None:
    for node in plan.nodes:
        if node.kind is not LogicalOperator.RETURN:
            continue
        source = plan.node(node.input_node_ids[0])
        if source.kind is LogicalOperator.SCAN:
            raise PlanValidationError(
                f"the RETURN node {node.id!r} reads SCAN node {source.id!r} directly, and a "
                "SCAN declares no columns; add a PROJECT node that declares the answer's columns"
            )
        declared = {column.name for column in source.required_output_columns}
        if not declared:
            raise PlanValidationError(
                f"node {source.id!r} feeds the RETURN node and declares no output columns, "
                "so nothing can check what the answer projects"
            )
        projected = node.attributes.get("columns", [])
        missing = [name for name in projected if name not in declared]
        if missing:
            raise PlanValidationError(
                f"the RETURN node projects {missing}, which node {source.id!r} does not "
                f"declare; it declares {sorted(declared)}"
            )


def _check_ending(plan: LogicalPlan, carried_node_ids: frozenset[str]) -> set[str]:
    returns = [node for node in plan.nodes if node.kind is LogicalOperator.RETURN]
    # Every control node this version wrote is terminal, since _check_controls_are_terminal
    # has already turned down a plan where anything reads one. A carried one fired in an
    # earlier version, so it ends nothing here.
    terminal_controls = [
        node
        for node in plan.nodes
        if isinstance(node.kind, ControlOperator) and node.id not in carried_node_ids
    ]
    if len(returns) == 1 and not terminal_controls:
        if plan.output_node_id != returns[0].id:
            raise PlanValidationError(
                f"output_node_id is {plan.output_node_id!r} but the RETURN node "
                f"is {returns[0].id!r}"
            )
        return {returns[0].id}
    if not returns and terminal_controls:
        if plan.output_node_id is not None:
            raise PlanValidationError(
                "a plan ending in a control node has no answer yet, so output_node_id must "
                f"be null, got {plan.output_node_id!r}"
            )
        return {node.id for node in terminal_controls}
    raise PlanValidationError(
        "a plan takes exactly one RETURN, or one or more terminal control nodes and no "
        f"RETURN; got {len(returns)} RETURN node(s) and "
        f"{len(terminal_controls)} terminal control node(s)"
    )


def _check_reachability(
    plan: LogicalPlan, terminal_ids: set[str], carried_node_ids: frozenset[str]
) -> None:
    if plan.output_node_id is None:
        # A plan ending at control nodes stops before its work is consumed, since a REPLAN reads
        # nothing: the rows its last steps produce are carried into the next version, which is
        # where something reads them.
        return
    read_ids = _read_node_ids(plan)
    for node in plan.nodes:
        if node.id in terminal_ids or node.id in carried_node_ids:
            continue
        if node.id not in read_ids:
            raise PlanValidationError(f"nothing reads the output of node {node.id!r}")


def _read_node_ids(plan: LogicalPlan) -> set[str]:
    return {input_id for node in plan.nodes for input_id in node.input_node_ids}


def _check_coverage(plan: LogicalPlan, physical_plan: PhysicalPlan) -> None:
    nodes_by_id = {node.id: node for node in plan.nodes}
    covering_fragment_ids: dict[str, list[str]] = {}
    for fragment in physical_plan.fragments:
        for node_id in fragment.logical_node_ids:
            covering_fragment_ids.setdefault(node_id, []).append(fragment.id)
    for node_id, fragment_ids in covering_fragment_ids.items():
        distinct_ids = sorted(set(fragment_ids))
        if len(distinct_ids) > 1:
            raise PlanValidationError(
                f"node {node_id!r} is covered by more than one fragment: {distinct_ids}"
            )
        if len(fragment_ids) > 1:
            raise PlanValidationError(
                f"fragment {distinct_ids[0]!r} lists node {node_id!r} more than once"
            )
    for fragment in physical_plan.fragments:
        for node_id in fragment.logical_node_ids:
            node = nodes_by_id.get(node_id)
            if node is None:
                raise PlanValidationError(
                    f"fragment {fragment.id!r} names node {node_id!r}, which the plan does not hold"
                )
            if not node.is_operator_node:
                raise PlanValidationError(
                    f"fragment {fragment.id!r} holds node {node_id!r} of kind "
                    f"{node.kind.value}, which no fragment may hold"
                )
    for node in plan.nodes:
        if node.is_operator_node and node.id not in covering_fragment_ids:
            raise PlanValidationError(f"no fragment covers node {node.id!r}")


def _check_fragment_shape(plan: LogicalPlan, fragment: PhysicalPlanFragment) -> None:
    # The order is what a doubly broken fragment is told about: a fragment that is neither
    # connected nor single exit is disconnected first, which is the more useful reading.
    node_ids = set(fragment.logical_node_ids)
    if fragment.root_logical_node_id not in node_ids:
        raise PlanValidationError(
            f"fragment {fragment.id!r} is rooted at node {fragment.root_logical_node_id!r}, "
            "which it does not hold"
        )
    _check_fragment_connected(plan, fragment, node_ids)
    _check_fragment_single_exit(plan, fragment, node_ids)


def _check_fragment_connected(
    plan: LogicalPlan, fragment: PhysicalPlanFragment, node_ids: set[str]
) -> None:
    reached = {fragment.root_logical_node_id}
    frontier = [fragment.root_logical_node_id]
    while frontier:
        node_id = frontier.pop()
        for neighbor_id in _neighbors_inside(plan, node_id, node_ids):
            if neighbor_id not in reached:
                reached.add(neighbor_id)
                frontier.append(neighbor_id)
    unreached = node_ids - reached
    if unreached:
        raise PlanValidationError(
            f"fragment {fragment.id!r} is not connected: {sorted(reached)} and {sorted(unreached)}"
        )


def _neighbors_inside(plan: LogicalPlan, node_id: str, node_ids: set[str]) -> list[str]:
    inputs = [input_id for input_id in plan.node(node_id).input_node_ids if input_id in node_ids]
    consumers = [node.id for node in plan.consumers(node_id) if node.id in node_ids]
    return inputs + consumers


def _check_fragment_single_exit(
    plan: LogicalPlan, fragment: PhysicalPlanFragment, node_ids: set[str]
) -> None:
    for node_id in fragment.logical_node_ids:
        if node_id == fragment.root_logical_node_id:
            continue
        for consumer in plan.consumers(node_id):
            if consumer.id not in node_ids:
                raise PlanValidationError(
                    f"fragment {fragment.id!r} is not single exit: node {node_id!r} is read "
                    f"from outside by {consumer.id!r}"
                )


def _check_input_data(plan: LogicalPlan, fragment: PhysicalPlanFragment) -> None:
    listed = [entry.logical_node_id for entry in fragment.input_data]
    frontier = fragment_frontier(plan, frozenset(fragment.logical_node_ids))
    if listed != frontier:
        raise PlanValidationError(
            f"fragment {fragment.id!r} reads {listed}, but the nodes feeding it are "
            f"{frontier}; a fragment takes one input per frontier node, in the order "
            "its nodes read them"
        )


def _check_scan_sources(plan: LogicalPlan, entity_paths: frozenset[str]) -> None:
    for node in plan.nodes:
        if node.kind is not LogicalOperator.SCAN:
            continue
        source = node.attributes["source"]
        if source is not None and source not in entity_paths:
            raise PlanValidationError(
                f"node {node.id!r} names source {source!r}, which no DISCOVER node found; "
                "a scan reads only an entity the run searched for"
            )


def _check_fragment_scan_sources(plan: LogicalPlan, fragment: PhysicalPlanFragment) -> None:
    expected = {
        node_id: source
        for node_id in fragment.logical_node_ids
        if plan.node(node_id).kind is LogicalOperator.SCAN
        and (source := plan.node(node_id).attributes["source"]) is not None
    }
    if fragment.scan_sources != expected:
        raise PlanValidationError(
            f"fragment {fragment.id!r} lists scan sources {fragment.scan_sources}, but its SCAN "
            f"nodes name {expected}"
        )
