"""The Pydantic models the two planning stages are held to."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from blue_data_planner.plan.fragments import fragment_frontier
from blue_data_planner.types import (
    BlueDataPlannerError,
    ColumnDataType,
    LogicalOperator,
    LogicalPlan,
    PhysicalOperator,
    PhysicalPlanFragment,
)

# Never an operator id, because those are suite-qualified.
REUSE = "reuse"


def candidate_label(root_node_id: str, operator_id: str, node_ids: frozenset[str]) -> str:
    """Return the name one binding candidate answers to, stable across turns."""
    # A candidate names what it is rather than where it sat in the list it arrived in: the list
    # differs from turn to turn, and from Task 8 the conversation keeps the earlier turns.
    return f"{root_node_id}:{operator_id}:[{','.join(sorted(node_ids))}]"


class _Strict(BaseModel):
    # A provider enforcing a schema strictly needs additionalProperties: false, which is
    # what extra="forbid" puts in the generated JSON Schema.
    model_config = ConfigDict(extra="forbid")


class OutputColumnModel(_Strict):
    """One column a node's result has to carry, with the type its values coerce to."""

    name: str
    dtype: ColumnDataType


class OperatorNodeModel(_Strict):
    """A node an operator runs: one instruction and the columns it must produce."""

    id: str
    kind: Literal[
        "SCAN",
        "FILTER",
        "PROJECT",
        "EXPAND",
        "AGGREGATE",
        "DISTINCT",
        "SORT_SUPERLATIVE",
        "JOIN",
        "UNION",
    ]
    input_node_ids: list[str]
    instruction: str
    required_output_columns: list[OutputColumnModel]


class ReturnNodeModel(_Strict):
    """The node naming the columns the answer carries."""

    id: str
    kind: Literal["RETURN"]
    input_node_ids: list[str]
    columns: list[str]


class ControlNodeModel(_Strict):
    """A node that ends the pass and returns to the logical planner."""

    id: str
    kind: Literal["REPLAN", "DISCOVER"]
    input_node_ids: list[str]
    instruction: str


# A plain union rather than a discriminated one: a discriminator is rendered as the JSON Schema
# keywords `oneOf` and `discriminator`, and a provider enforcing a schema strictly accepts
# neither. The kinds are disjoint literals, so the node a response holds is unambiguous anyway.
PlanNodeModel = OperatorNodeModel | ReturnNodeModel | ControlNodeModel


class LogicalPlanModel(_Strict):
    """A logical plan as a model writes it, before it is read into a LogicalPlan."""

    nodes: list[PlanNodeModel]
    output_node_id: str | None


def logical_plan_schema() -> type[BaseModel]:
    """Return the response schema a logical plan is written into."""
    return LogicalPlanModel


def cover_choice_schema(
    plan: LogicalPlan,
    root_node_id: str,
    candidates: list[tuple[frozenset[str], PhysicalOperator]],
    reusable: PhysicalPlanFragment | None,
    entity_source_kinds: Mapping[str, str],
) -> type[BaseModel]:
    """Return the response schema one binding decision is written into."""
    if not candidates:
        raise BlueDataPlannerError(
            "a binding decision takes at least one candidate; a node with none is the "
            "physical planner's no_operator case"
        )
    variants = tuple(
        _candidate_model(root_node_id, plan, index, node_ids, operator, entity_source_kinds)
        for index, (node_ids, operator) in enumerate(candidates)
    )
    if reusable is not None:
        variants = (*variants, _reuse_model(root_node_id, reusable))
    if len(variants) == 1:
        choice: Any = variants[0]
    else:
        union: Any = Union
        # Undiscriminated, for the reason PlanNodeModel gives. Each variant's label is its own
        # literal, so the candidate a response took is unambiguous.
        choice = union[variants]
    return create_model("CoverChoice", __base__=_Strict, choice=(choice, ...))


def _reuse_model(root_node_id: str, reusable: PhysicalPlanFragment) -> type[BaseModel]:
    label = candidate_label(root_node_id, REUSE, frozenset(reusable.logical_node_ids))
    return create_model("CandidateReuse", __base__=_Strict, candidate=(_only(label), ...))


def _candidate_model(
    root_node_id: str,
    plan: LogicalPlan,
    index: int,
    node_ids: frozenset[str],
    operator: PhysicalOperator,
    entity_source_kinds: Mapping[str, str],
) -> type[BaseModel]:
    label = candidate_label(root_node_id, operator.operator_id, node_ids)
    fields: dict[str, Any] = {
        "candidate": (_only(label), ...),
        "instruction": (
            str,
            Field(description="What this fragment is to do, as one instruction to the operator."),
        ),
        "required_output_columns": (list[OutputColumnModel] | None, None),
    }
    # The fragment's inputs are the nodes outside it that feed it, which the plan already
    # decides. Only the naming is the model's, so it gets one field per input rather than a
    # list it could write a node id into that the run holds no rows for.
    for frontier_id in fragment_frontier(plan, node_ids):
        fields[f"input_name_{frontier_id}"] = (
            str,
            Field(description=f"A short name for the table node {frontier_id} produced."),
        )
        fields[f"input_description_{frontier_id}"] = (
            str,
            Field(description=f"What the rows node {frontier_id} produced hold."),
        )
    scan_ids = [
        node_id for node_id in sorted(node_ids) if plan.node(node_id).kind is LogicalOperator.SCAN
    ]
    # An operator declaring no source kinds reads no registered source, so every scan it
    # takes is left out of scan_sources and the schema offers nowhere to name one
    # (10-data-model.md, the Sources rule).
    if scan_ids and operator.source_kinds:
        readable = sorted(
            path
            for path, source_kind in entity_source_kinds.items()
            if source_kind in operator.source_kinds
        )
        # PhysicalPlanner drops a candidate it cannot bind before the schema is built, so
        # an empty list here is a bug rather than a plan worth offering. Falling back to a
        # bare str would let the model invent a path.
        if not readable:
            raise BlueDataPlannerError(
                f"candidate {label!r} binds {operator.operator_id} to {scan_ids}, but no "
                f"discovered entity has a source kind it reads "
                f"({sorted(operator.source_kinds)})"
            )
        paths = _only(*readable)
        for node_id in scan_ids:
            fields[f"scan_source_{node_id}"] = (paths, ...)
    return create_model(f"Candidate{index}", __base__=_Strict, **fields)


def _only(*values: object) -> Any:
    # A Literal over values known only at run time. Typed Any because a type checker cannot
    # follow one, and pydantic is what validates the field against it.
    literal: Any = Literal
    return literal[values]
