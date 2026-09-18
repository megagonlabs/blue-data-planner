"""What each format prints, and what it keeps off standard output."""

from __future__ import annotations

import io
import json

from blue_data_planner import RunStatus
from blue_data_planner.plan import signatures
from blue_data_planner.types import (
    AttributeRecord,
    DataPipeline,
    DiscoveryResult,
    EntityMatch,
    ErrorInfo,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    InputData,
    LogicalOperator,
    LogicalPlan,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    RunResult,
)
from rich.console import Console

from blue_data_planner_cli.output import (
    error_line,
    print_answer,
    result_payload,
    summary_line,
    trace_lines,
    trace_payload,
)
from tests.doubles.components import a_run_result


def a_console() -> tuple[Console, io.StringIO]:
    """Return a console writing into a buffer, and the buffer."""
    buffer = io.StringIO()
    return Console(file=buffer, width=100), buffer


def a_discovery_of_one_match() -> DiscoveryResult:
    """Return a catalog search that found one entity and lost a term to the per-node cap."""
    return DiscoveryResult(
        search_terms=["schools"],
        matches=[
            EntityMatch(
                path="california_schools/schools",
                name="schools",
                description="every school in the state",
                source_kind="sql/sqlite",
                attributes=[AttributeRecord(name="County", dtype="str", description=None)],
                score=0.91,
                search_term="schools",
            )
        ],
        dropped_terms=1,
    )


def a_run_over_one_cover_of_two_fragments() -> RunResult:
    """Return a run whose one cover scanned a source and filtered what the scan read."""
    plan = LogicalPlan(
        id="lp1",
        parent_id=None,
        nodes=[
            PipelineNode(id="n1", kind=LogicalOperator.SCAN),
            PipelineNode(id="n2", kind=LogicalOperator.FILTER, input_node_ids=["n1"]),
        ],
        output_node_id="n2",
    )
    scan = PhysicalPlanFragment(
        id="f1",
        logical_node_ids=["n1"],
        root_logical_node_id="n1",
        operator_id="builtin.source_scan",
        scan_sources={"n1": "postings"},
        instruction="read every posting",
        input_data=[],
        required_output_columns=[],
    )
    sem_filter = PhysicalPlanFragment(
        id="f2",
        logical_node_ids=["n2"],
        root_logical_node_id="n2",
        operator_id="lotus.sem_filter",
        scan_sources={},
        instruction="keep the postings in the bay area",
        input_data=[InputData(logical_node_id="n1", name="postings", description="every posting")],
        required_output_columns=[],
    )
    cover = PhysicalPlan(
        id="pp1", parent_id=None, logical_plan_id=plan.id, fragments=[scan, sem_filter]
    )
    signed = signatures(plan, cover)
    return a_run_result(
        pipeline=DataPipeline(
            logical_plans={plan.id: plan},
            physical_plans={cover.id: cover},
            current_logical_plan_id=plan.id,
            current_physical_plan_id=cover.id,
        ),
        results={
            signed["n1"]: ExecutionResult(
                status=ExecutionStatus.OK, operator_id=scan.operator_id, row_count=140
            ),
            signed["n2"]: ExecutionResult(
                status=ExecutionStatus.OK, operator_id=sem_filter.operator_id, row_count=11
            ),
        },
    )


def test_a_table_carries_the_column_names_the_rows_came_with() -> None:
    """A reader identifies a column by its own name, not by a position."""
    result = a_run_result(answer=[[{"school": "Lowell", "takers": 44}]])
    console, buffer = a_console()

    print_answer(result, "table", console)

    printed = buffer.getvalue()
    assert "school" in printed and "takers" in printed and "Lowell" in printed


def test_an_answer_value_holding_square_brackets_reaches_the_table_whole() -> None:
    """An answer is data a model wrote, so Rich must not read a bracket in one as markup."""
    result = a_run_result(answer=[[{"co[/red]lumn": "cols [red, blue]"}]])
    console, buffer = a_console()

    print_answer(result, "table", console)

    printed = buffer.getvalue()
    assert "co[/red]lumn" in printed
    assert "cols [red, blue]" in printed


def test_the_json_format_prints_the_payload_rather_than_a_table() -> None:
    """Under --format json the one thing standard output carries is the result."""
    console, buffer = a_console()

    print_answer(a_run_result(answer=[[{"school": "Lowell"}]]), "json", console)

    assert json.loads(buffer.getvalue())["answer"] == [[{"school": "Lowell"}]]


def test_the_json_payload_holds_the_status_the_answer_and_the_counters() -> None:
    """A caller reading JSON gets the run, not a rendering of it."""
    result = a_run_result(answer=[[{"school": "Lowell"}]])

    payload = result_payload(result)

    assert payload["status"] == "ok"
    assert payload["answer"] == [[{"school": "Lowell"}]]
    assert payload["counters"]["operator_calls"] == 0
    assert "conversations" not in payload  # transcripts stay in the library's own result
    json.dumps(payload)  # the payload has to survive encoding


def test_the_json_payload_carries_a_discovery_and_the_bounds_in_full() -> None:
    """The CLI carries the three shapes the MCP server reduces to counts."""
    result = a_run_result(results={"a3f1c208": a_discovery_of_one_match()})

    payload = result_payload(result)

    discovery = payload["results"]["a3f1c208"]
    assert discovery["search_terms"] == ["schools"]
    assert discovery["dropped_terms"] == 1
    assert discovery["matches"][0]["name"] == "schools"
    assert discovery["matches"][0]["attributes"] == [
        {"name": "County", "dtype": "str", "description": None}
    ]
    assert payload["counters"]["limits"]["operator_calls"] == 30  # the default RunLimits ceiling
    json.dumps(payload)  # the payload has to survive encoding


def test_the_json_payload_of_a_failed_fragment_says_why_it_failed() -> None:
    """A failed run stays machine-readable, down to the operator's own objection."""
    failure = ExecutionResult(
        status=ExecutionStatus.FAILED,
        operator_id="lotus.sem_filter",
        error=ErrorInfo(kind=ErrorKind.RECOVERABLE, message="the column 'county' is missing"),
        report={"rows_read": 0},
    )
    result = a_run_result(
        status=RunStatus.OPERATOR_FAILED,
        failure_reason="the operator refused its input",
        results={"7b40de91": failure},
    )

    payload = result_payload(result)

    failed = payload["results"]["7b40de91"]
    assert failed["status"] == "failed"
    assert failed["error"] == {"kind": "recoverable", "message": "the column 'county' is missing"}
    assert failed["report"] == {"rows_read": 0}
    json.dumps(payload)  # the payload has to survive encoding


def test_a_failed_run_carries_its_status_and_reason_rather_than_an_empty_answer() -> None:
    """A caller who ignores the status must not read a failure as an empty result."""
    result = a_run_result(status=RunStatus.BUDGET_EXHAUSTED, failure_reason="30 calls spent")

    payload = result_payload(result)

    assert payload["status"] == "budget_exhausted"
    assert payload["answer"] is None
    assert payload["failure_reason"] == "30 calls spent"


def test_a_fragment_line_names_the_fragment_its_nodes_its_operator_and_how_it_ended() -> None:
    """One line is one attempt, under the cover that made it."""
    lines = trace_lines(a_run_over_one_cover_of_two_fragments(), log_limit=200)

    assert lines[:3] == [
        "cover 1",
        "  f1  n1  builtin.source_scan  ok (140 rows)",
        "  f2  n2  lotus.sem_filter  ok (11 rows)",
    ]


def test_a_trace_line_is_truncated_to_the_log_limit_rather_than_dropped() -> None:
    """A review's wording has no bound; the number of attempts does."""
    lines = trace_lines(a_run_result(failure_reason="x" * 500), log_limit=40)

    assert len(lines) == 3  # a run over no cover traces its counters, its spend, and its ending
    assert all(len(line) <= 40 for line in lines)


def test_the_trace_payload_holds_the_truncated_lines_under_one_key() -> None:
    """Under --format json the trace reaches standard error as one object."""
    result = a_run_result(failure_reason="x" * 500)

    payload = trace_payload(result, log_limit=40)

    assert list(payload) == ["trace"]
    assert payload["trace"] == trace_lines(result, 40)


def test_an_error_line_names_its_code_first() -> None:
    """Every Blue CLI reports a failure in this one shape."""
    assert error_line("CONFIG_MISSING", "BLUE_DATA_REGISTRY_PATH is not configured").startswith(
        "ERROR: CONFIG_MISSING "
    )


def test_the_summary_names_the_status_and_what_the_run_spent() -> None:
    """The closing line is what a demonstration reads out loud."""
    assert "ok" in summary_line(a_run_result())
