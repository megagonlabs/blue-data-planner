"""The Pydantic models the two planning stages are held to."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from blue_data_planner.types import (
    BlueDataPlannerError,
    ColumnDataType,
    LogicalOperator,
    LogicalPlan,
    PhysicalOperator,
)


class _Strict(BaseModel):
    # A provider enforcing a schema strictly needs additionalProperties: false, which is
    # what extra="forbid" puts in the generated JSON Schema.
    model_config = ConfigDict(extra="forbid")


class OutputColumnModel(_Strict):
    """One column a node's result has to carry, with the type its values coerce to."""

    name: str
    dtype: ColumnDataType


class InputDataModel(_Strict):
    """One frontier node's output, named and described for the operator that reads it."""

    logical_node_id: str
    name: str
    description: str


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


PlanNodeModel = Annotated[
    OperatorNodeModel | ReturnNodeModel | ControlNodeModel,
    Field(discriminator="kind"),
]


class LogicalPlanModel(_Strict):
    """A logical plan as a model writes it, before it is read into a LogicalPlan."""

    nodes: list[PlanNodeModel]
    output_node_id: str | None


def logical_plan_schema() -> type[BaseModel]:
    """Return the response schema a logical plan is written into."""
    return LogicalPlanModel


def cover_choice_schema(
    plan: LogicalPlan,
    candidates: list[tuple[frozenset[str], PhysicalOperator]],
    entity_source_kinds: Mapping[str, str],
) -> type[BaseModel]:
    """Return the response schema one binding decision is written into."""
    if not candidates:
        raise BlueDataPlannerError(
            "a binding decision takes at least one candidate; a node with none is the "
            "physical planner's no_operator case"
        )
    variants = tuple(
        _candidate_model(plan, index, node_ids, operator, entity_source_kinds)
        for index, (node_ids, operator) in enumerate(candidates)
    )
    if len(variants) == 1:
        choice: Any = variants[0]
    else:
        union: Any = Union
        choice = Annotated[union[variants], Field(discriminator="candidate_index")]
    return create_model("CoverChoice", __base__=_Strict, choice=(choice, ...))


def _candidate_model(
    plan: LogicalPlan,
    index: int,
    node_ids: frozenset[str],
    operator: PhysicalOperator,
    entity_source_kinds: Mapping[str, str],
) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "candidate_index": (_only(index), ...),
        "instruction": (
            str,
            Field(description="What this fragment is to do, as one instruction to the operator."),
        ),
        "input_data": (list[InputDataModel], ...),
        "required_output_columns": (list[OutputColumnModel] | None, None),
    }
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
                f"candidate {index} binds {operator.operator_id} to {scan_ids}, but no "
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
