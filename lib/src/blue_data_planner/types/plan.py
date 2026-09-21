"""The shapes describing the work: rows, the logical vocabulary, and the two plan halves."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import BaseModel

from blue_data_planner.types.errors import (
    BlueDataPlannerError,
    PlanFormatError,
    UnknownFragmentError,
    UnknownNodeError,
)

# A record is one row, data is one table, and a bundle is the list of tables one fragment
# passes to the next. They live here because this package imports nothing else, and both
# ExecutionResult and PhysicalOperator are declared in terms of a bundle.
Record = dict[str, Any]
Data = list[Record]
DataBundle = list[Data]


class LogicalOperator(StrEnum):
    """The tool-agnostic operations a plan node can carry."""

    SCAN = "SCAN"
    FILTER = "FILTER"
    PROJECT = "PROJECT"
    EXPAND = "EXPAND"
    AGGREGATE = "AGGREGATE"
    DISTINCT = "DISTINCT"
    SORT_SUPERLATIVE = "SORT_SUPERLATIVE"
    JOIN = "JOIN"
    UNION = "UNION"
    RETURN = "RETURN"


class ControlOperator(StrEnum):
    """The operations that end a pass and return to the logical planner."""

    REPLAN = "REPLAN"
    DISCOVER = "DISCOVER"


# How many tables a node transforms, not how many inputs it may list: a node listing more
# has reference inputs, whose rows its instruction mentions without transforming them.
# SCAN reads a source rather than another node. A control node has no entry here, because
# it takes any number of inputs, including none.
INPUT_ARITY: dict[LogicalOperator, int] = {
    LogicalOperator.SCAN: 0,
    LogicalOperator.FILTER: 1,
    LogicalOperator.PROJECT: 1,
    LogicalOperator.EXPAND: 1,
    LogicalOperator.AGGREGATE: 1,
    LogicalOperator.DISTINCT: 1,
    LogicalOperator.SORT_SUPERLATIVE: 1,
    LogicalOperator.JOIN: 2,
    LogicalOperator.UNION: 2,
    LogicalOperator.RETURN: 1,
}

ColumnDataType = Literal["str", "int", "float", "bool"]


@dataclass(frozen=True)
class OutputColumn:
    """One column a node's result has to carry, with the type its values coerce to."""

    name: str
    dtype: ColumnDataType


@dataclass(frozen=True)
class PipelineNode:
    """One step of a logical plan: what it does, what it reads, and what it must produce."""

    id: str
    kind: LogicalOperator | ControlOperator
    input_node_ids: list[str] = field(default_factory=list[str])
    attributes: dict[str, Any] = field(default_factory=dict[str, Any])
    required_output_columns: list[OutputColumn] = field(default_factory=list[OutputColumn])

    @property
    def instruction(self) -> str:
        # A RETURN node carries `columns` instead, so it has no instruction to read.
        return str(self.attributes.get("instruction", ""))

    @property
    def is_operator_node(self) -> bool:
        # RETURN is a member of LogicalOperator, but it names the columns the answer carries
        # rather than a step an operator runs, so it is excluded even though the isinstance
        # check alone would include it.
        return isinstance(self.kind, LogicalOperator) and self.kind is not LogicalOperator.RETURN


@dataclass(frozen=True)
class LogicalPlan:
    """One version of the graph a request asks for, in the tool-agnostic vocabulary."""

    id: str
    parent_id: str | None
    nodes: list[PipelineNode]
    output_node_id: str | None

    def node(self, node_id: str) -> PipelineNode:
        """Return the node with this id, raising when the plan holds no such node."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise UnknownNodeError(f"no node with id {node_id!r} in the plan")

    def consumers(self, node_id: str) -> list[PipelineNode]:
        """Return every node reading this one, raising when the plan holds no such node."""
        # The id is resolved first so that a typo comes back as an error rather than as an
        # empty list, which would read as "nothing consumes this node".
        self.node(node_id)
        return [node for node in self.nodes if node_id in node.input_node_ids]

    def nodes_in_dependency_order(self) -> list[PipelineNode]:
        """Return every node behind the nodes it reads, raising when the plan has a cycle."""
        ordered: list[PipelineNode] = []
        placed: set[str] = set()
        while len(ordered) < len(self.nodes):
            ready = [
                node
                for node in self.nodes
                if node.id not in placed
                and all(input_id in placed for input_id in node.input_node_ids)
            ]
            if not ready:
                unplaced = [node.id for node in self.nodes if node.id not in placed]
                raise BlueDataPlannerError(
                    f"the plan contains a cycle among nodes {sorted(unplaced)}"
                )
            for node in ready:
                ordered.append(node)
                placed.add(node.id)
        return ordered

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form a plan travels in whenever it leaves the process."""
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "output_node_id": self.output_node_id,
            # Written in dependency order so a recorded plan reads top to bottom.
            "nodes": [_node_to_dict(node) for node in self.nodes_in_dependency_order()],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LogicalPlan:
        """Read a plan back from its JSON form, raising rather than repairing it."""
        _require_keys(payload, _LOGICAL_PLAN_KEYS, "logical plan")
        nodes = [
            _node_from_dict(node_payload)
            for node_payload in _require_list(payload, "nodes", "logical plan")
        ]
        known_ids = {node.id for node in nodes}
        for node in nodes:
            unknown = [input_id for input_id in node.input_node_ids if input_id not in known_ids]
            if unknown:
                raise PlanFormatError(
                    f"node {node.id!r} reads nodes the plan does not hold: {unknown}"
                )
        return cls(
            id=_require_str(payload, "id", "logical plan"),
            parent_id=_require_str_or_none(payload, "parent_id", "logical plan"),
            nodes=nodes,
            output_node_id=_require_str_or_none(payload, "output_node_id", "logical plan"),
        )


@dataclass(frozen=True)
class InputData:
    """One frontier node's output, named and described for the operator that reads it."""

    logical_node_id: str
    name: str
    description: str


@dataclass(frozen=True)
class PhysicalPlanFragment:
    """The group of logical nodes one operator call covers, with what it reads and produces."""

    id: str
    logical_node_ids: list[str]
    root_logical_node_id: str
    operator_id: str
    scan_sources: dict[str, str]
    instruction: str
    input_data: list[InputData]
    required_output_columns: list[OutputColumn]


@dataclass(frozen=True)
class PhysicalPlan:
    """One cover of a logical plan: which operator runs which group of its nodes."""

    id: str
    parent_id: str | None
    logical_plan_id: str
    fragments: list[PhysicalPlanFragment]

    def fragment(self, fragment_id: str) -> PhysicalPlanFragment:
        """Return the fragment with this id, raising when the cover holds no such fragment."""
        for fragment in self.fragments:
            if fragment.id == fragment_id:
                return fragment
        raise UnknownFragmentError(f"no fragment with id {fragment_id!r} in the cover")

    def fragment_for_node(self, node_id: str) -> PhysicalPlanFragment:
        """Return the fragment covering this node, raising when no fragment covers it."""
        for fragment in self.fragments:
            if node_id in fragment.logical_node_ids:
                return fragment
        raise UnknownFragmentError(f"no fragment of the cover covers node {node_id!r}")

    def fragment_for_root(self, node_id: str) -> PhysicalPlanFragment | None:
        """Return the fragment rooted at this node, or nothing when none is."""
        # None rather than a raise, because a node rooting no fragment of the cover being replaced
        # is the ordinary case: it may have been absorbed, or the plan may have grown since.
        for fragment in self.fragments:
            if fragment.root_logical_node_id == node_id:
                return fragment
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form a cover travels in whenever it leaves the process."""
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "logical_plan_id": self.logical_plan_id,
            "fragments": [_fragment_to_dict(fragment) for fragment in self.fragments],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PhysicalPlan:
        """Read a cover back from its JSON form, raising rather than repairing it."""
        _require_keys(payload, _PHYSICAL_PLAN_KEYS, "physical plan")
        return cls(
            id=_require_str(payload, "id", "physical plan"),
            parent_id=_require_str_or_none(payload, "parent_id", "physical plan"),
            logical_plan_id=_require_str(payload, "logical_plan_id", "physical plan"),
            fragments=[
                _fragment_from_dict(fragment_payload)
                for fragment_payload in _require_list(payload, "fragments", "physical plan")
            ],
        )


@dataclass
class DataPipeline:
    """Every plan version one request produced, and the two versions that are current."""

    logical_plans: dict[str, LogicalPlan]
    physical_plans: dict[str, PhysicalPlan]
    current_logical_plan_id: str | None
    current_physical_plan_id: str | None

    # The two properties raise BlueDataPlannerError rather than UnknownNodeError or
    # UnknownFragmentError: nothing here is missing a node or a fragment. The run has not
    # written a plan yet, which is true only before the first pass.
    @property
    def logical_plan(self) -> LogicalPlan:
        if self.current_logical_plan_id is None:
            raise BlueDataPlannerError("the pipeline names no current logical plan")
        plan = self.logical_plans.get(self.current_logical_plan_id)
        if plan is None:
            raise BlueDataPlannerError(
                f"the pipeline names {self.current_logical_plan_id!r} as its current logical "
                "plan, but holds no logical plan with that id"
            )
        return plan

    @property
    def cover(self) -> PhysicalPlan:
        if self.current_physical_plan_id is None:
            raise BlueDataPlannerError("the pipeline names no current physical plan")
        cover = self.physical_plans.get(self.current_physical_plan_id)
        if cover is None:
            raise BlueDataPlannerError(
                f"the pipeline names {self.current_physical_plan_id!r} as its current physical "
                "plan, but holds no physical plan with that id"
            )
        return cover

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form every plan version travels in, keyed by plan id."""
        return {
            "current_logical_plan_id": self.current_logical_plan_id,
            "current_physical_plan_id": self.current_physical_plan_id,
            "logical_plans": {
                plan_id: plan.to_dict() for plan_id, plan in self.logical_plans.items()
            },
            "physical_plans": {
                plan_id: cover.to_dict() for plan_id, cover in self.physical_plans.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DataPipeline:
        """Read every plan version back from its JSON form, raising rather than repairing it."""
        _require_keys(payload, _PIPELINE_KEYS, "pipeline")
        logical_payloads = _require_dict(payload, "logical_plans", "pipeline")
        physical_payloads = _require_dict(payload, "physical_plans", "pipeline")
        return cls(
            logical_plans={
                plan_id: LogicalPlan.from_dict(_as_dict(plan_payload, "a logical plan"))
                for plan_id, plan_payload in logical_payloads.items()
            },
            physical_plans={
                plan_id: PhysicalPlan.from_dict(_as_dict(cover_payload, "a physical plan"))
                for plan_id, cover_payload in physical_payloads.items()
            },
            current_logical_plan_id=_require_str_or_none(
                payload, "current_logical_plan_id", "pipeline"
            ),
            current_physical_plan_id=_require_str_or_none(
                payload, "current_physical_plan_id", "pipeline"
            ),
        )


@dataclass(frozen=True)
class OperatorOutput:
    """What one operator call returned: its tables, and whatever it has to report."""

    data: DataBundle
    # Free-form, and nothing reads it while a run continues. It travels to the caller so a
    # finished run can be read: the query an operator wrote, the operations it ran, what its
    # own model calls cost.
    report: dict[str, Any] | None = None


@dataclass(frozen=True)
class TableSummary:
    """What one table holds, for a reader that is shown the table rather than handed it."""

    row_count: int
    columns: list[str]
    sample_rows: Data

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "row_count": self.row_count,
            "columns": list(self.columns),
            "sample_rows": list(self.sample_rows),
        }


def summarize_table(table: Data, max_rows: int) -> TableSummary:
    """Return a table's row count, its column names, and its first `max_rows` rows."""
    if max_rows < 0:
        raise ValueError(f"max_rows is {max_rows}; expected zero or more")
    columns = sorted({column for row in table for column in row})
    return TableSummary(row_count=len(table), columns=columns, sample_rows=table[:max_rows])


@dataclass(frozen=True)
class PhysicalOperator:
    """One candidate implementation of one or more logical operators."""

    operator_id: str
    logical_operators: frozenset[LogicalOperator]
    description: str
    parameter_schema: type[BaseModel]
    call: Callable[[DataBundle, dict[str, Any], list[OutputColumn]], OperatorOutput]
    source_kinds: frozenset[str] | None = None
    accepts_bound_inputs: bool = False
    limitations: str | None = None


_LOGICAL_PLAN_KEYS: tuple[str, ...] = ("id", "parent_id", "output_node_id", "nodes")
_PHYSICAL_PLAN_KEYS: tuple[str, ...] = ("id", "parent_id", "logical_plan_id", "fragments")
_PIPELINE_KEYS: tuple[str, ...] = (
    "current_logical_plan_id",
    "current_physical_plan_id",
    "logical_plans",
    "physical_plans",
)
_NODE_KEYS: tuple[str, ...] = (
    "id",
    "kind",
    "input_node_ids",
    "attributes",
    "required_output_columns",
)
_FRAGMENT_KEYS: tuple[str, ...] = (
    "id",
    "logical_node_ids",
    "root_logical_node_id",
    "operator_id",
    "scan_sources",
    "instruction",
    "input_data",
    "required_output_columns",
)
_INPUT_DATA_KEYS: tuple[str, ...] = ("logical_node_id", "name", "description")
_COLUMN_DATA_TYPES: frozenset[str] = frozenset({"str", "int", "float", "bool"})


def _require_keys(payload: dict[str, Any], keys: tuple[str, ...], what: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise PlanFormatError(f"{what} payload is missing required keys: {missing}")


def _require_str(payload: dict[str, Any], key: str, what: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise PlanFormatError(
            f"{what} payload key {key!r} must be a string, got {type(value).__name__}"
        )
    return value


def _require_str_or_none(payload: dict[str, Any], key: str, what: str) -> str | None:
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise PlanFormatError(
            f"{what} payload key {key!r} must be a string or null, got {type(value).__name__}"
        )
    return value


def _require_list(payload: dict[str, Any], key: str, what: str) -> list[Any]:
    value = payload[key]
    if not isinstance(value, list):
        raise PlanFormatError(
            f"{what} payload key {key!r} must be a list, got {type(value).__name__}"
        )
    return cast(list[Any], value)


def _require_dict(payload: dict[str, Any], key: str, what: str) -> dict[str, Any]:
    value = payload[key]
    if not isinstance(value, dict):
        raise PlanFormatError(
            f"{what} payload key {key!r} must be an object, got {type(value).__name__}"
        )
    return cast(dict[str, Any], value)


def _node_to_dict(node: PipelineNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind.value,
        "input_node_ids": list(node.input_node_ids),
        "attributes": dict(node.attributes),
        "required_output_columns": [
            _column_to_dict(column) for column in node.required_output_columns
        ],
    }


def _node_from_dict(node_payload: Any) -> PipelineNode:
    payload = _as_dict(node_payload, "a node")
    _require_keys(payload, _NODE_KEYS, "node")
    node_id = _require_str(payload, "id", "node")
    kind = _kind_from_value(payload["kind"])
    attributes = _require_dict(payload, "attributes", "node")
    expected_key = "columns" if kind is LogicalOperator.RETURN else "instruction"
    if set(attributes) != {expected_key}:
        raise PlanFormatError(
            f"node {node_id!r} of kind {kind.value} takes the attribute "
            f"{expected_key!r}, got {sorted(attributes)}"
        )
    if expected_key == "instruction":
        _as_str(attributes[expected_key], f"the instruction of node {node_id!r}")
    else:
        for column in _as_list(attributes[expected_key], f"the columns of node {node_id!r}"):
            _as_str(column, f"a column of node {node_id!r}")
    return PipelineNode(
        id=node_id,
        kind=kind,
        input_node_ids=[
            _as_str(input_id, f"an input node id of node {node_id!r}")
            for input_id in _require_list(payload, "input_node_ids", "node")
        ],
        attributes=dict(attributes),
        required_output_columns=[
            _column_from_dict(column)
            for column in _require_list(payload, "required_output_columns", "node")
        ],
    )


def _kind_from_value(value: Any) -> LogicalOperator | ControlOperator:
    try:
        return LogicalOperator(value)
    except ValueError:
        pass
    try:
        return ControlOperator(value)
    except ValueError:
        known = [member.value for member in LogicalOperator]
        known += [member.value for member in ControlOperator]
        raise PlanFormatError(f"unknown node kind {value!r}; expected one of {known}") from None


def _column_to_dict(column: OutputColumn) -> dict[str, Any]:
    return {"name": column.name, "dtype": column.dtype}


def _column_from_dict(column_payload: Any) -> OutputColumn:
    payload = _as_dict(column_payload, "an output column")
    if set(payload) != {"name", "dtype"}:
        raise PlanFormatError(f"an output column takes 'name' and 'dtype', got {sorted(payload)}")
    dtype = payload["dtype"]
    if not isinstance(dtype, str) or dtype not in _COLUMN_DATA_TYPES:
        raise PlanFormatError(
            f"unknown column data type {dtype!r}; expected one of {sorted(_COLUMN_DATA_TYPES)}"
        )
    return OutputColumn(
        name=_require_str(payload, "name", "output column"),
        dtype=cast(ColumnDataType, dtype),
    )


def _fragment_to_dict(fragment: PhysicalPlanFragment) -> dict[str, Any]:
    return {
        "id": fragment.id,
        "logical_node_ids": list(fragment.logical_node_ids),
        "root_logical_node_id": fragment.root_logical_node_id,
        "operator_id": fragment.operator_id,
        "scan_sources": dict(fragment.scan_sources),
        "instruction": fragment.instruction,
        "input_data": [_input_data_to_dict(entry) for entry in fragment.input_data],
        "required_output_columns": [
            _column_to_dict(column) for column in fragment.required_output_columns
        ],
    }


def _fragment_from_dict(fragment_payload: Any) -> PhysicalPlanFragment:
    payload = _as_dict(fragment_payload, "a fragment")
    _require_keys(payload, _FRAGMENT_KEYS, "fragment")
    fragment_id = _require_str(payload, "id", "fragment")
    scan_sources = _require_dict(payload, "scan_sources", "fragment")
    return PhysicalPlanFragment(
        id=fragment_id,
        logical_node_ids=[
            _as_str(node_id, f"a logical node id of fragment {fragment_id!r}")
            for node_id in _require_list(payload, "logical_node_ids", "fragment")
        ],
        root_logical_node_id=_require_str(payload, "root_logical_node_id", "fragment"),
        operator_id=_require_str(payload, "operator_id", "fragment"),
        scan_sources={
            node_id: _as_str(source, f"a scan source of fragment {fragment_id!r}")
            for node_id, source in scan_sources.items()
        },
        instruction=_require_str(payload, "instruction", "fragment"),
        input_data=[
            _input_data_from_dict(entry)
            for entry in _require_list(payload, "input_data", "fragment")
        ],
        required_output_columns=[
            _column_from_dict(column)
            for column in _require_list(payload, "required_output_columns", "fragment")
        ],
    )


def _input_data_to_dict(entry: InputData) -> dict[str, Any]:
    return {
        "logical_node_id": entry.logical_node_id,
        "name": entry.name,
        "description": entry.description,
    }


def _input_data_from_dict(entry_payload: Any) -> InputData:
    payload = _as_dict(entry_payload, "an input data entry")
    _require_keys(payload, _INPUT_DATA_KEYS, "input data")
    return InputData(
        logical_node_id=_require_str(payload, "logical_node_id", "input data"),
        name=_require_str(payload, "name", "input data"),
        description=_require_str(payload, "description", "input data"),
    )


def _as_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanFormatError(f"{what} must be an object, got {type(value).__name__}")
    return cast(dict[str, Any], value)


def _as_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise PlanFormatError(f"{what} must be a list, got {type(value).__name__}")
    return cast(list[Any], value)


def _as_str(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise PlanFormatError(f"{what} must be a string, got {type(value).__name__}")
    return value
