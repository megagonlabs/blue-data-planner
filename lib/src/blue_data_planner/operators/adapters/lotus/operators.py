"""LOTUS operations as physical operators, converting at the edge and mapping errors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators.adapters.lotus import frame_conversion, settings
from blue_data_planner.operators.adapters.lotus.declared_columns import (
    check_named_columns,
    one_new_column,
)
from blue_data_planner.operators.adapters.lotus.instruction_columns import (
    check_instruction_columns,
)
from blue_data_planner.operators.adapters.lotus.schemas import (
    SemAggParameters,
    SemExtractParameters,
    SemFilterParameters,
    SemJoinParameters,
    SemMapParameters,
    SemTopKParameters,
)
from blue_data_planner.operators.validation import check_table_count, validated_parameters
from blue_data_planner.types import (
    INPUT_ARITY,
    Data,
    DataBundle,
    LogicalOperator,
    OperatorOutput,
    OutputColumn,
    PhysicalOperator,
    RecoverableOperatorError,
)

DEFAULT_MAX_ROWS = 5000
# sem_topk is quickselect over model-judged pairwise comparisons, so its model calls grow with
# the comparison count rather than the row count — measured at roughly 8x the rows in one
# partitioning. A lower cap here keeps its worst case in the same order of magnitude as what
# DEFAULT_MAX_ROWS already bounds for the other operators.
SEM_TOPK_MAX_ROWS = 500

_Run = Callable[[list[Any], Any, list[OutputColumn], str], tuple[Any, list[str]]]


@dataclass(frozen=True)
class _Entry:
    operator_id: str
    logical_operator: LogicalOperator
    description: str
    parameter_schema: type[BaseModel]
    run: _Run
    limitations: str | None = None
    # The cap counts the rows the call receives. A fragment grouping a narrowing condition
    # with this operation hands it every row, since the operation applies that condition itself.
    row_cap: str = (
        "be handed more than {max_rows} rows in one call, counted before any condition in "
        "its fragment narrows them"
    )
    # None means "use the suite's cap." Only sem_topk sets this, since its model calls grow
    # with the comparison count rather than the row count the suite's cap was sized for.
    max_rows: int | None = None

    @property
    def table_count(self) -> int:
        """Return the tables this operation reads, which its logical operator fixes."""
        return INPUT_ARITY[self.logical_operator]


def lotus_operators(
    model: str, max_rows: int = DEFAULT_MAX_ROWS, reasoning_effort: str | None = None
) -> list[PhysicalOperator]:
    """Return one physical operator per LOTUS operation, or none where LOTUS is not installed."""
    if not settings.lotus_is_installed():
        return []
    return [
        PhysicalOperator(
            operator_id=entry.operator_id,
            logical_operators=frozenset({entry.logical_operator}),
            description=entry.description,
            parameter_schema=entry.parameter_schema,
            call=_build_call(entry, model, max_rows, reasoning_effort),
            source_kinds=None,
            accepts_bound_inputs=False,
            limitations=_limitations(entry, max_rows),
        )
        for entry in _ENTRIES
    ]


def check_row_cap(operator_id: str, tables: list[Data], max_rows: int) -> None:
    """Raise when an operation would read more rows than the cap allows."""
    # A join judges every pair, so the two tables multiply rather than add. One table
    # multiplies against the empty product, which is its own length.
    counted = 1
    for table in tables:
        counted *= len(table)
    if counted <= max_rows:
        return
    raise RecoverableOperatorError(
        f"{operator_id} was handed {counted} rows to judge, above the {max_rows}-row cap. A "
        "built-in operation can narrow the table before a semantic one reads it."
    )


def _resolved_max_rows(entry: _Entry, max_rows: int) -> int:
    # An operator's own cap is a safety bound derived from measurement; the suite's cap is a
    # budget setting. The two compose as a minimum, so a tighter suite setting still holds even
    # where an operator declares its own, usually looser, bound.
    return max_rows if entry.max_rows is None else min(entry.max_rows, max_rows)


def _limitations(entry: _Entry, max_rows: int) -> str:
    cap = entry.row_cap.format(max_rows=_resolved_max_rows(entry, max_rows))
    return cap if entry.limitations is None else f"{entry.limitations}; {cap}"


def _build_call(
    entry: _Entry, model: str, max_rows: int, reasoning_effort: str | None
) -> Callable[[DataBundle, dict[str, Any], list[OutputColumn]], OperatorOutput]:
    def call(
        inputs: DataBundle,
        parameters: dict[str, Any],
        required_output_columns: list[OutputColumn],
    ) -> OperatorOutput:
        validated = validated_parameters(entry.parameter_schema, parameters, entry.operator_id)
        check_table_count(entry.operator_id, inputs, entry.table_count)
        check_row_cap(entry.operator_id, list(inputs), _resolved_max_rows(entry, max_rows))
        # An empty table carries no column names for an instruction to name, and a model
        # call over no rows can only return nothing. A join over an empty side is empty too.
        if any(not table for table in inputs):
            return OperatorOutput(data=[[]])
        lotus = settings.configured_lotus(model, reasoning_effort)
        # Annotated Any rather than inferred as pandas' DataFrame: pandas ships no type stubs,
        # so frame.columns below would otherwise resolve to an unknown type.
        frames: list[Any] = [frame_conversion.frame_from_table(table) for table in inputs]
        instruction = getattr(validated, "instruction", None)
        if isinstance(instruction, str):
            check_instruction_columns(
                entry.operator_id,
                instruction,
                [[str(name) for name in frame.columns] for frame in frames],
            )
        spent_before = _usage(lotus)
        try:
            frame, created = entry.run(
                frames, validated, required_output_columns, entry.operator_id
            )
        # sem_join reports a join column it could not find with a bare assert rather than
        # an exception a caller can tell from a bug, so both arrive as a refusal.
        except (ValueError, AssertionError) as error:
            raise RecoverableOperatorError(
                f"{entry.operator_id} refused its input: {error}"
            ) from error
        table = frame_conversion.table_from_frame(frame)
        _check_created(entry.operator_id, table, created)
        return OperatorOutput(
            data=[frame_conversion.missing_to_none(table, created, required_output_columns)],
            report=_report(lotus, spent_before),
        )

    return call


def _check_created(operator_id: str, table: list[dict[str, Any]], created: list[str]) -> None:
    if not table:
        return
    missing = [name for name in created if name not in table[0]]
    if missing:
        raise RecoverableOperatorError(
            f"{operator_id} did not produce {', '.join(missing)}; the model's answers either "
            "failed to parse or left the column out"
        )


# What a report calls each number, and what LOTUS records it under.
_USAGE_FIELDS: dict[str, str] = {
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "total_tokens": "total_tokens",
    "cost": "total_cost",
}


def _usage(lotus: Any) -> dict[str, float]:
    # Read as numbers rather than held as an object, because LOTUS updates its usage in
    # place and a reference would move with it.
    usage = lotus.settings.lm.stats.physical_usage
    return {name: getattr(usage, attribute) for name, attribute in _USAGE_FIELDS.items()}


def _report(lotus: Any, before: dict[str, float]) -> dict[str, Any]:
    after = _usage(lotus)
    return {name: value - before[name] for name, value in after.items()}


def _run_filter(
    frames: list[Any], parameters: Any, required: list[OutputColumn], operator_id: str
) -> tuple[Any, list[str]]:
    return frames[0].sem_filter(parameters.instruction), []


def _run_map(
    frames: list[Any], parameters: Any, required: list[OutputColumn], operator_id: str
) -> tuple[Any, list[str]]:
    column = one_new_column(
        operator_id, required, [str(name) for name in frames[0].columns], parameters.output_column
    )
    return frames[0].sem_map(parameters.instruction, suffix=column), [column]


def _run_extract(
    frames: list[Any], parameters: Any, required: list[OutputColumn], operator_id: str
) -> tuple[Any, list[str]]:
    named = [str(column.name) for column in parameters.output_columns]
    check_named_columns(operator_id, required, [str(name) for name in frames[0].columns], named)
    extracted = frames[0].sem_extract(
        input_cols=parameters.input_columns,
        output_cols={
            str(column.name): str(column.extracts) for column in parameters.output_columns
        },
    )
    return extracted, named


def _run_topk(
    frames: list[Any], parameters: Any, required: list[OutputColumn], operator_id: str
) -> tuple[Any, list[str]]:
    return frames[0].sem_topk(parameters.instruction, K=parameters.k), []


def _run_agg(
    frames: list[Any], parameters: Any, required: list[OutputColumn], operator_id: str
) -> tuple[Any, list[str]]:
    present = [str(name) for name in frames[0].columns]
    missing = [name for name in parameters.group_by if name not in present]
    if missing:
        # A grouping column LOTUS cannot find reaches pandas as a bare KeyError, which the
        # mapping around the call does not read as a refusal.
        raise RecoverableOperatorError(
            f"{operator_id} was given a group_by naming "
            f"{', '.join(repr(one) for one in missing)}, which its input does not carry; it "
            f"holds {', '.join(repr(one) for one in present)}"
        )
    # sem_agg keeps the columns it groups by and drops the rest, so what it creates is
    # whatever the node declares beyond them.
    column = one_new_column(operator_id, required, parameters.group_by, parameters.output_column)
    summarized = frames[0].sem_agg(
        parameters.instruction, suffix=column, group_by=parameters.group_by or None
    )
    return summarized, [column]


def _run_join(
    frames: list[Any], parameters: Any, required: list[OutputColumn], operator_id: str
) -> tuple[Any, list[str]]:
    joined = frames[0].sem_join(frames[1], parameters.instruction)
    return frame_conversion.rename_join_columns(joined), []


_ENTRIES: tuple[_Entry, ...] = (
    _Entry(
        "lotus.sem_filter",
        LogicalOperator.FILTER,
        "Keeps the rows a language model judges to satisfy a condition over named columns.",
        SemFilterParameters,
        _run_filter,
    ),
    _Entry(
        "lotus.sem_map",
        LogicalOperator.PROJECT,
        "Writes one new column from a language model's reading of named columns.",
        SemMapParameters,
        _run_map,
        limitations="write more than one new column per call, or change a column it read",
    ),
    _Entry(
        "lotus.sem_extract",
        LogicalOperator.PROJECT,
        "Pulls named values out of text columns into new columns, one row in and one row out.",
        SemExtractParameters,
        _run_extract,
        limitations="use anything but the text of the columns it is pointed at",
    ),
    _Entry(
        "lotus.sem_topk",
        LogicalOperator.SORT_SUPERLATIVE,
        "Keeps the rows a language model ranks highest against a stated preference.",
        SemTopKParameters,
        _run_topk,
        limitations="report how the rows it keeps ranked",
        max_rows=SEM_TOPK_MAX_ROWS,
    ),
    _Entry(
        "lotus.sem_agg",
        LogicalOperator.AGGREGATE,
        "Summarizes the rows of each group into one value a language model wrote.",
        SemAggParameters,
        _run_agg,
        limitations=(
            "compute a sum, average, or count, since it writes one text value per group, so a "
            "node asking for a number is not its to take"
        ),
    ),
    _Entry(
        "lotus.sem_join",
        LogicalOperator.JOIN,
        "Matches the rows of two tables on a language model's judgment of one column from each.",
        SemJoinParameters,
        _run_join,
        limitations=(
            "match on more than one column from each table, or produce anything but inner-join rows"
        ),
        row_cap=(
            "be handed two tables whose row counts multiply past {max_rows}, since it compares "
            "every pair of rows"
        ),
    ),
)


@dataclass(frozen=True)
class LotusSuite:
    """LOTUS's operations, and the model and row cap they run under."""

    name: ClassVar[str] = "lotus"
    # None runs LOTUS on the model every other component uses.
    model: str | None = None
    max_rows: int = DEFAULT_MAX_ROWS
    # None omits the parameter, for a model that does not accept one.
    reasoning_effort: str | None = None

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]:
        """Return one operator per LOTUS operation, or none where LOTUS is not installed."""
        return lotus_operators(
            self.model or model, self.max_rows, reasoning_effort=self.reasoning_effort
        )
