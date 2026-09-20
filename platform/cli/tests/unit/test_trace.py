"""What the trace says a run did, as lines and as a table."""

from __future__ import annotations

import io

from blue_data_planner import RunResult, RunStatus
from blue_data_planner.types import (
    ErrorInfo,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
)
from rich.console import Console

from blue_data_planner_cli.trace import print_trace, trace_lines, trace_payload
from tests.doubles.components import (
    a_discovery_of_one_match,
    a_rerun_whose_second_cover_keeps_the_scan,
    a_run_over_one_cover_of_two_fragments,
    a_run_result,
)


def a_trace(result: RunResult, rows: int = 5, chars: int = 200) -> list[str]:
    """Return a run's trace at a cap wide enough that nothing is cut unless a test cuts it."""
    return trace_lines(result, max_fragment_rows=rows, max_output_chars=chars)


def a_printed_trace(result: RunResult, width: int = 200) -> str:
    """Return what the trace writes to a console of a fixed width."""
    buffer = io.StringIO()
    print_trace(result, Console(file=buffer, width=width), max_fragment_rows=5, max_output_chars=80)
    return buffer.getvalue()


def a_refused_filter() -> FragmentResult:
    """Return a fragment whose operator would not take the input it was handed."""
    return FragmentResult(
        status=RunStatus.OPERATOR_FAILED,
        result=ExecutionResult(
            status=ExecutionStatus.FAILED,
            operator_id="lotus.sem_filter",
            error=ErrorInfo(kind=ErrorKind.RECOVERABLE, message="the column 'county' is missing"),
        ),
        failure_reason="the column 'county' is missing",
    )


def a_rejected_filter() -> FragmentResult:
    """Return a fragment whose call returned rows the review turned down."""
    return FragmentResult(
        status=RunStatus.NEEDS_REPAIR,
        result=ExecutionResult(
            status=ExecutionStatus.OK,
            operator_id="lotus.sem_filter",
            output=[[{"title": "posting 0", "county": "Fresno"}]],
            row_count=1,
        ),
        failure_reason="the review said Fresno is not in the bay area",
    )


def test_a_fragment_traces_its_operator_its_steps_and_a_summary_of_what_it_returned() -> None:
    """A reader judges an answer from what each step was told to do and what came back."""
    lines = a_trace(a_run_over_one_cover_of_two_fragments(), rows=2)

    assert lines[:8] == [
        "cover 1   logical plan lp1",
        "",
        "  f1  builtin.source_scan",
        "      n1  SCAN  read every posting",
        "          reads postings",
        "      ok  140 rows",
        "          columns  county, title",
        "          {'title': 'posting 0', 'county': 'Alameda'}",
    ]


def test_a_fragment_names_the_fragments_whose_output_it_reads() -> None:
    """A reader follows a wrong answer upstream, and needs to know which way upstream is."""
    assert "  f2  lotus.sem_filter  from f1" in a_trace(a_run_over_one_cover_of_two_fragments())


def test_a_cover_traces_its_fragments_in_the_order_a_run_reaches_them() -> None:
    """A cover lists its fragments in no order; a reader follows the run rather than the list."""
    lines = a_trace(a_run_over_one_cover_of_two_fragments())

    assert lines.index("  f1  builtin.source_scan") < lines.index("  f2  lotus.sem_filter  from f1")


def test_a_fragment_that_ran_and_was_not_kept_says_so_where_one_never_reached_says_nothing() -> (
    None
):
    """A rejected fragment and one the pass never got to are what a reader must tell apart."""
    rejected = a_trace(a_run_over_one_cover_of_two_fragments(filter_result=a_rejected_filter()))
    unreached = a_trace(a_run_over_one_cover_of_two_fragments())

    assert "      needs_repair  1 rows" in rejected
    assert "      not reached under this cover" in unreached


def test_a_fragment_that_was_not_kept_traces_the_reason_it_went_to_a_repair() -> None:
    """What the review objected to is what says which repair the run moved on to, and why."""
    lines = a_trace(a_run_over_one_cover_of_two_fragments(filter_result=a_rejected_filter()))

    assert "          reason  the review said Fresno is not in the bay area" in lines


def test_a_failed_fragment_traces_the_objection_the_operator_raised() -> None:
    """An operator failure ends the run, and its own message is what names the cause."""
    lines = a_trace(a_run_over_one_cover_of_two_fragments(filter_result=a_refused_filter()))

    assert "      operator_failed" in lines
    assert "          reason  the column 'county' is missing" in lines


def test_a_discover_node_traces_what_its_search_matched_ahead_of_the_cover_reading_it() -> None:
    """A search resolves before the plan that reads it is written, so it leads the cover."""
    lines = a_trace(a_run_over_one_cover_of_two_fragments(discovery=a_discovery_of_one_match()))

    assert lines[:5] == [
        "cover 1   logical plan lp1",
        "",
        "  n3  DISCOVER  find a source holding schools",
        "      ok  1 matches  (1 terms dropped)",
        "          california_schools/schools",
    ]


def test_work_a_later_cover_asked_for_again_is_named_rather_than_traced_twice() -> None:
    """A reused result was not run again, and repeating its rows says otherwise."""
    lines = a_trace(a_rerun_whose_second_cover_keeps_the_scan())

    assert lines.count("          columns  county, title") == 1
    assert "      ok  140 rows  (reused from cover 1)" in lines


def test_the_row_cap_bounds_the_sample_a_fragment_shows() -> None:
    """A fragment's result is as large as the data it read, so the trace shows a bounded head."""
    lines = a_trace(a_run_over_one_cover_of_two_fragments(), rows=0)

    assert not [line for line in lines if "{" in line]


def test_only_a_fragment_s_own_output_is_cut_to_the_character_cap() -> None:
    """A row is as wide as the table it came from; an instruction and a source are read whole."""
    lines = a_trace(a_run_over_one_cover_of_two_fragments(), chars=30)

    assert "          {'title': 'posting …" in lines
    assert "      n1  SCAN  read every posting" in lines


def test_the_trace_closes_with_what_the_run_spent_and_why_it_ended() -> None:
    """The counters and the reason are what a reader checks once the walk is done."""
    lines = a_trace(a_run_result(failure_reason="30 calls spent"))

    assert lines[0].startswith("counters  operator calls 0/30")
    assert lines[1].startswith("model     0 calls")
    assert lines[2] == "ended     30 calls spent"


def test_the_trace_payload_holds_the_same_lines_under_one_key() -> None:
    """Under --format json the trace reaches standard error as one object."""
    result = a_run_over_one_cover_of_two_fragments()

    payload = trace_payload(result, max_fragment_rows=5, max_output_chars=200)

    assert list(payload) == ["trace"]
    assert payload["trace"] == a_trace(result)


def test_the_printed_trace_puts_one_cover_in_one_table() -> None:
    """A table wraps an instruction inside its own column, where a line would have to be cut."""
    printed = a_printed_trace(a_run_over_one_cover_of_two_fragments())

    assert "Fragment" in printed and "Instruction" in printed and "Result" in printed
    assert "builtin.source_scan" in printed
    assert "read every posting" in printed


def test_a_scanned_source_is_printed_under_the_table_rather_than_inside_a_column() -> None:
    """A catalog path is longer than any column, and a reader needs the whole of it."""
    printed = a_printed_trace(a_run_over_one_cover_of_two_fragments(), width=60)

    assert "      reads  n1  postings" in printed


def test_a_printed_instruction_holding_square_brackets_reaches_the_table_whole() -> None:
    """An instruction is model-authored, so Rich must not read a bracket in one as markup."""
    result = a_run_over_one_cover_of_two_fragments(instruction="keep [red, blue] rows")

    assert "keep [red, blue] rows" in a_printed_trace(result)
