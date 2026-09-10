"""The structural checks a plan and a cover have to pass before anything runs them."""

from __future__ import annotations

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
    plan: LogicalPlan, carried_node_ids: frozenset[str] = frozenset()
) -> None:
    """Raise unless the plan is a sound DAG with one ending and enough inputs per node."""
    _check_structure(plan)
    _check_arity(plan)
    _check_replan_count(plan)
    terminal_ids = _check_ending(plan, carried_node_ids)
    _check_reachability(plan, terminal_ids, carried_node_ids)
    _check_answer_columns(plan)


def validate_physical_plan(
    plan: LogicalPlan, physical_plan: PhysicalPlan, entity_paths: frozenset[str]
) -> None:
    """Raise unless every operator node is covered once by a legal fragment over a known entity."""
    _check_coverage(plan, physical_plan)
    for fragment in physical_plan.fragments:
        _check_fragment_shape(plan, fragment)
        _check_scan_sources(fragment, entity_paths)


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


def _check_answer_columns(plan: LogicalPlan) -> None:
    for node in plan.nodes:
        if node.kind is not LogicalOperator.RETURN:
            continue
        source = plan.node(node.input_node_ids[0])
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
    read_ids = _read_node_ids(plan)
    returns = [node for node in plan.nodes if node.kind is LogicalOperator.RETURN]
    terminal_controls = [
        node
        for node in plan.nodes
        if isinstance(node.kind, ControlOperator)
        and node.id not in read_ids
        and node.id not in carried_node_ids
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


def _check_scan_sources(fragment: PhysicalPlanFragment, entity_paths: frozenset[str]) -> None:
    for node_id, path in fragment.scan_sources.items():
        if path not in entity_paths:
            raise PlanValidationError(
                f"fragment {fragment.id!r} binds node {node_id!r} to {path!r}, which no "
                "DISCOVER node found; a scan reads only an entity the run searched for"
            )
