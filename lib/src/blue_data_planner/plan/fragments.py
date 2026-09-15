"""Every node set rooted at one node that a given set of logical operators could cover."""

from __future__ import annotations

from blue_data_planner.types import LogicalOperator, LogicalPlan


def candidate_fragments(
    plan: LogicalPlan, root_node_id: str, logical_operators: frozenset[LogicalOperator]
) -> list[frozenset[str]]:
    """Return every node set rooted here that one operator declaring these kinds could cover."""
    root = plan.node(root_node_id)
    if not root.is_operator_node or root.kind not in logical_operators:
        return []
    consumer_ids = {
        node.id: {consumer.id for consumer in plan.consumers(node.id)} for node in plan.nodes
    }
    found: set[frozenset[str]] = set()
    pending = [frozenset({root_node_id})]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        for node_id in current:
            for input_id in plan.node(node_id).input_node_ids:
                if input_id in current or not _admissible(
                    plan, input_id, current, consumer_ids, logical_operators
                ):
                    continue
                pending.append(current | {input_id})
    # Largest first, then by the sorted ids, so one plan enumerates the same way every time.
    return sorted(found, key=lambda node_ids: (-len(node_ids), sorted(node_ids)))


def fragment_frontier(plan: LogicalPlan, node_ids: frozenset[str]) -> list[str]:
    """Return the nodes outside this set that feed it, in the order their tables arrive."""
    # Each node's own input order is kept rather than sorted, because an operator reading
    # two tables takes them positionally: a JOIN handed its inputs the other way round
    # joins the other way round.
    frontier: list[str] = []
    for node in plan.nodes_in_dependency_order():
        if node.id not in node_ids:
            continue
        for input_id in node.input_node_ids:
            if input_id not in node_ids and input_id not in frontier:
                frontier.append(input_id)
    return frontier


def _admissible(
    plan: LogicalPlan,
    node_id: str,
    current: frozenset[str],
    consumer_ids: dict[str, set[str]],
    logical_operators: frozenset[LogicalOperator],
) -> bool:
    node = plan.node(node_id)
    if not node.is_operator_node or node.kind not in logical_operators:
        return False
    # Admitting a node only once every consumer it has is already inside is the single-exit
    # rule, and it is what makes convexity follow rather than needing its own check.
    return consumer_ids[node_id] <= current
