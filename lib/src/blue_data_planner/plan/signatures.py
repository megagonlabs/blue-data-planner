"""What one node's work is, as a value that changes whenever its result would."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from blue_data_planner.types import (
    BlueDataPlannerError,
    ControlOperator,
    LogicalPlan,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
)

# Only the exclusions are listed, so a field added to PhysicalPlanFragment later is covered without
# anyone remembering to add it. A fragment id would leave no signature ever matching an earlier
# cover's, since a fragment the model did not reuse gets a fresh one.
_EXCLUDED_FROM_SIGNATURE = {"id"}


def signatures(plan: LogicalPlan, cover: PhysicalPlan) -> dict[str, str]:
    """Return one signature per node producing a result: every fragment root, every control node."""
    root_fragments = {fragment.root_logical_node_id: fragment for fragment in cover.fragments}
    signed: dict[str, str] = {}
    # Dependency order is what lets a node's inputs already be signed when it is reached.
    for node in plan.nodes_in_dependency_order():
        fragment = root_fragments.get(node.id)
        if fragment is not None:
            input_ids = [entry.logical_node_id for entry in fragment.input_data]
            signed[node.id] = _sign(_fragment_work(fragment), _upstream(input_ids, signed, node.id))
        elif isinstance(node.kind, ControlOperator):
            signed[node.id] = _sign(
                _control_work(node), _upstream(node.input_node_ids, signed, node.id)
            )
    return signed


def _fragment_work(fragment: PhysicalPlanFragment) -> dict[str, Any]:
    work = {
        field.name: getattr(fragment, field.name)
        for field in dataclasses.fields(fragment)
        if field.name not in _EXCLUDED_FROM_SIGNATURE
    }
    # Two covers may list one fragment's nodes in different orders without the work differing, so
    # what is signed is the set.
    work["logical_node_ids"] = sorted(work["logical_node_ids"])
    return work


def _control_work(node: PipelineNode) -> dict[str, Any]:
    return {"id": node.id, "kind": node.kind.value, "attributes": node.attributes}


def _upstream(node_ids: list[str], signed: dict[str, str], of_node_id: str) -> list[str]:
    upstream: list[str] = []
    for node_id in node_ids:
        held = signed.get(node_id)
        if held is None:
            raise BlueDataPlannerError(
                f"node {of_node_id!r} reads node {node_id!r}, which roots no fragment of the "
                "cover, so the work behind it cannot be signed"
            )
        upstream.append(held)
    return upstream


def _sign(work: dict[str, Any], upstream: list[str]) -> str:
    # The upstream signatures go into the value, so a change anywhere above a node changes the
    # node's own signature and nothing has to walk down the graph to invalidate a result.
    body = json.dumps({"work": work, "inputs": upstream}, sort_keys=True, default=_as_json)
    return hashlib.sha256(body.encode()).hexdigest()


def _as_json(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    raise BlueDataPlannerError(f"a signature cannot cover a value of type {type(value).__name__}")
