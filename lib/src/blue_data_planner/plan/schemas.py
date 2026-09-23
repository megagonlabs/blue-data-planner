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

    name: str = Field(description="The column's name, spelled as the result carries it.")
    dtype: ColumnDataType = Field(description="The type every value in the column coerces to.")


class OperatorNodeModel(_Strict):
    """A node an operator runs: one instruction and the columns it must produce."""

    id: str = Field(
        description="A short name for this node, unique in the plan, used by the nodes reading it."
    )
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
    ] = Field(description="The operation this node performs.")
    input_node_ids: list[str] = Field(
        description="The ids of the nodes whose rows this node reads, in the order it reads them."
    )
    instruction: str = Field(
        description=(
            "What this node does, in plain language, starting with a verb. Name no source, no "
            "tool, and no query language. The columns the result has to carry are declared in "
            "required_output_columns; do not restate them here."
        )
    )
    required_output_columns: list[OutputColumnModel] = Field(
        description=(
            "The columns this node's result has to carry so the nodes reading it can. A result "
            "carrying further columns satisfies this."
        )
    )


class ReturnNodeModel(_Strict):
    """The node naming the columns the answer carries."""

    id: str = Field(description="A short name for this node, unique in the plan.")
    kind: Literal["RETURN"] = Field(description="The operation this node performs.")
    input_node_ids: list[str] = Field(
        description="The id of the one node whose rows hold the answer."
    )
    columns: list[str] = Field(
        description=(
            "The columns holding the answer, each one declared by the node this reads. Name the "
            "columns the request asks for and no others."
        )
    )


class DiscoverNodeModel(_Strict):
    """A node that searches the catalog and ends the pass."""

    id: str = Field(description="A short name for this node, unique in the plan.")
    kind: Literal["DISCOVER"] = Field(description="The operation this node performs.")
    input_node_ids: list[str] = Field(
        description=(
            "The ids of the nodes whose rows the search terms are written against, empty when "
            "the search needs none."
        )
    )
    instruction: str = Field(
        description=(
            "What has to be found in the catalog, in plain language, starting with a verb. Data "
            "you can supply yourself is not one of these; write it into the plan."
        )
    )


class ReplanNodeModel(_Strict):
    """A node that ends the pass so the rest can be written against the rows it produced."""

    id: str = Field(description="A short name for this node, unique in the plan.")
    kind: Literal["REPLAN"] = Field(description="The operation this node performs.")
    instruction: str = Field(
        description=(
            "Why the pass stops here: what the steps it wrote have to show before the rest of "
            "the plan can be written."
        )
    )


# A plain union rather than a discriminated one: a discriminator is rendered as the JSON Schema
# keywords `oneOf` and `discriminator`, and a provider enforcing a schema strictly accepts
# neither. The kinds are disjoint literals, so the node a response holds is unambiguous anyway.
PlanNodeModel = OperatorNodeModel | ReturnNodeModel | DiscoverNodeModel | ReplanNodeModel


class LogicalPlanModel(_Strict):
    """A logical plan as a model writes it, before it is read into a LogicalPlan."""

    nodes: list[PlanNodeModel] = Field(description="Every node this pass writes.")
    output_node_id: str | None = Field(
        description=(
            "The id of the RETURN node, or null when the plan ends at a control node and has no "
            "answer yet."
        )
    )


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
    return create_model(
        "CoverChoice",
        __base__=_Strict,
        choice=(choice, Field(description="The candidate this decision picks, and what it needs.")),
    )


def _reuse_model(root_node_id: str, reusable: PhysicalPlanFragment) -> type[BaseModel]:
    label = candidate_label(root_node_id, REUSE, frozenset(reusable.logical_node_ids))
    return create_model(
        "CandidateReuse",
        __base__=_Strict,
        candidate=(
            _only(label),
            Field(description="Keep the fragment already run for this node and run nothing new."),
        ),
    )


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
        "candidate": (
            _only(label),
            Field(description="The candidate this decision picks, named by its label."),
        ),
        "instruction": (
            str,
            Field(
                description=(
                    "What this fragment is to do, as one instruction to the operator. Say what "
                    "the fragment has to produce, not what it has to leave out."
                )
            ),
        ),
        "required_output_columns": (
            list[OutputColumnModel] | None,
            Field(
                default=None,
                description=(
                    "Leave this unset, and the fragment owes what its root node declares. Set "
                    "it only when the operator's stated limitations mean it cannot produce that, "
                    "naming what it does produce instead."
                ),
            ),
        ),
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
            fields[f"scan_source_{node_id}"] = (
                paths,
                Field(description=f"The catalog path node {node_id} scans."),
            )
    return create_model(f"Candidate{index}", __base__=_Strict, **fields)


def _only(*values: object) -> Any:
    # A Literal over values known only at run time. Typed Any because a type checker cannot
    # follow one, and pydantic is what validates the field against it.
    literal: Any = Literal
    return literal[values]
