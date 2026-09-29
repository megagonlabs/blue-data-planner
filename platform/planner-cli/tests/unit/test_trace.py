"""What the log says a run did: the records behind it, and the table it is drawn in."""

from __future__ import annotations

import dataclasses

import pytest
from blue_data_planner import RunResult, RunStatus
from blue_data_planner.types import (
    SUMMARY_SAMPLE_ROWS,
    ErrorInfo,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    ParameterAttempt,
    UnknownNodeError,
)

from blue_data_planner_cli.trace import (
    discovery_ending,
    fragment_ending,
    print_log,
    spend_lines,
    traced_covers,
)
from tests.doubles.components import (
    a_discovery_of_one_match,
    a_rerun_whose_second_cover_keeps_the_scan,
    a_run_over_one_cover_of_two_fragments,
    a_run_result,
)
from tests.doubles.environment import a_console


def a_printed_log(result: RunResult, width: int = 200, chars: int = 200) -> str:
    """Return what the log writes to a console of a fixed width, at a cap nothing hits."""
    console, buffer = a_console(width)
    print_log(result, console, max_output_chars=chars)
    return buffer.getvalue()


def a_filter_the_operator_refused() -> FragmentResult:
    """Return a fragment whose operator would not take the input it was handed."""
    return FragmentResult(
        status=RunStatus.OPERATOR_FAILED,
        attempts=[
            ParameterAttempt(
                parameters={},
                result=ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    operator_id="lotus.sem_filter",
                    error=ErrorInfo(
                        kind=ErrorKind.UNRECOVERABLE, message="the column 'county' is missing"
                    ),
                ),
                assessment=None,
            )
        ],
        failure_reason="the column 'county' is missing",
    )


def a_filter_the_review_turned_down() -> FragmentResult:
    """Return a fragment whose result the review turned down."""
    return FragmentResult(
        status=RunStatus.NEEDS_REPAIR,
        attempts=[
            ParameterAttempt(
                parameters={},
                result=ExecutionResult(
                    status=ExecutionStatus.OK,
                    operator_id="lotus.sem_filter",
                    output=[[{"title": "posting 0", "county": "Fresno"}]],
                    row_count=1,
                ),
                assessment=None,
            )
        ],
        failure_reason="the review said Fresno is not in the bay area",
    )


def test_a_fragment_logs_its_operator_its_steps_and_a_summary_of_what_it_returned() -> None:
    """A reader judges an answer from what each step was told to do and what came back."""
    scan = traced_covers(a_run_over_one_cover_of_two_fragments())[0].fragments[0]

    assert (scan.id, scan.operator_id) == ("f1", "builtin.source_scan")
    assert [(node.id, node.kind, node.instruction) for node in scan.nodes] == [
        ("n1", "SCAN", "read every posting")
    ]
    assert scan.nodes[0].scan_source == "jobs/postings"
    assert (scan.status, scan.row_count) == (RunStatus.OK, 140)
    assert fragment_ending(scan) == "ok  140 rows"
    assert scan.rows is not None
    assert scan.rows.row_count == 140
    assert scan.rows.columns == ["county", "title"]


def test_a_fragment_names_the_fragments_whose_output_it_reads() -> None:
    """A reader follows a wrong answer upstream, and needs to know which way upstream is."""
    covers = traced_covers(a_run_over_one_cover_of_two_fragments())

    assert covers[0].fragments[1].input_fragment_ids == ["f1"]


def test_a_cover_lists_its_fragments_in_the_order_a_run_reaches_them() -> None:
    """A cover holds its fragments in no order; a reader follows the run rather than the list."""
    covers = traced_covers(a_run_over_one_cover_of_two_fragments())

    assert [fragment.id for fragment in covers[0].fragments] == ["f1", "f2"]


def test_a_fragment_that_ran_and_was_not_kept_says_so_where_one_never_reached_says_nothing() -> (
    None
):
    """A rejected fragment and one the pass never got to are what a reader must tell apart."""
    rejected = a_run_over_one_cover_of_two_fragments(
        filter_result=a_filter_the_review_turned_down()
    )
    unreached = a_run_over_one_cover_of_two_fragments()

    assert fragment_ending(traced_covers(rejected)[0].fragments[1]) == "needs_repair  1 rows"
    unvisited = traced_covers(unreached)[0].fragments[1]
    assert unvisited.status is None
    assert fragment_ending(unvisited) == "not reached under this physical plan"


def test_a_fragment_that_was_not_kept_names_the_reason_it_went_to_a_repair() -> None:
    """What the review objected to is what says which repair the run moved on to, and why."""
    result = a_run_over_one_cover_of_two_fragments(filter_result=a_filter_the_review_turned_down())

    assert traced_covers(result)[0].fragments[1].failure_reason == (
        "the review said Fresno is not in the bay area"
    )
    assert "reason  the review said Fresno is not in the bay area" in a_printed_log(result)


def test_a_failed_fragment_names_the_objection_the_operator_raised() -> None:
    """An operator failure ends the run, and its own message is what names the cause."""
    filtered = traced_covers(
        a_run_over_one_cover_of_two_fragments(filter_result=a_filter_the_operator_refused())
    )[0].fragments[1]

    assert fragment_ending(filtered) == "operator_failed"
    assert filtered.failure_reason == "the column 'county' is missing"


def test_a_discover_node_leads_the_cover_reading_it_with_what_its_search_matched() -> None:
    """A search resolves before the plan that reads it is written, so it leads the cover."""
    cover = traced_covers(
        a_run_over_one_cover_of_two_fragments(discovery=a_discovery_of_one_match())
    )[0]

    assert [one.node.id for one in cover.discoveries] == ["n3"]
    assert discovery_ending(cover.discoveries[0]) == "ok  1 matches  (1 terms dropped)"
    assert cover.discoveries[0].matches == ["california_schools/schools"]


def test_work_a_later_cover_asked_for_again_is_named_rather_than_logged_twice() -> None:
    """A reused result was not run again, and repeating its rows says otherwise."""
    covers = traced_covers(a_rerun_whose_second_cover_keeps_the_scan())

    reused = covers[1].fragments[0]
    assert reused.reused_from == 1
    assert fragment_ending(reused) == "ok  140 rows  (reused from physical plan 1)"
    assert reused.rows is None


def test_the_sample_a_fragment_shows_is_bounded_however_much_it_returned() -> None:
    """A fragment's result is as large as the data it read, so the log shows a bounded head."""
    scan = traced_covers(a_run_over_one_cover_of_two_fragments())[0].fragments[0]

    assert scan.rows is not None
    # The scan in the double returns 140 rows, well past the cap.
    assert len(scan.rows.sample_rows) == SUMMARY_SAMPLE_ROWS


def test_only_a_fragments_own_output_is_cut_to_the_character_cap() -> None:
    """A row is as wide as the table it came from; an instruction is read whole however long."""
    printed = a_printed_log(a_run_over_one_cover_of_two_fragments(), chars=20)

    assert "{'title': 'po…" in printed
    # Thirty-three characters, well past the cap, and still whole.
    assert "keep the postings in the bay area" in printed


def test_the_log_closes_with_what_the_run_spent_and_why_it_ended() -> None:
    """The counters and the reason are what a reader checks once the walk is done."""
    lines = spend_lines(a_run_result(failure_reason="30 calls spent"))

    assert lines[0].startswith("counters  operator calls 0/30")
    assert lines[1].startswith("model     0 calls")
    assert lines[2] == "ended     30 calls spent"


def test_the_printed_log_puts_one_cover_in_one_table() -> None:
    """A table wraps an instruction inside its own column, where a line would have to be cut."""
    printed = a_printed_log(a_run_over_one_cover_of_two_fragments())

    assert "Fragment" in printed and "Instruction" in printed and "Result" in printed
    assert "builtin.source_scan" in printed
    assert "read every posting" in printed


def test_a_scanned_source_is_printed_under_the_table_rather_than_inside_a_column() -> None:
    """A catalog path is longer than any column, and a reader needs the whole of it."""
    printed = a_printed_log(a_run_over_one_cover_of_two_fragments(), width=60)

    assert "      reads  n1  jobs/postings" in printed


def test_a_printed_instruction_holding_square_brackets_reaches_the_table_whole() -> None:
    """An instruction is model-authored, so Rich must not read a bracket in one as markup."""
    result = a_run_over_one_cover_of_two_fragments(instruction="keep [red, blue] rows")

    assert "keep [red, blue] rows" in a_printed_log(result)


def a_cover_naming_a_node_the_plan_lacks() -> RunResult:
    """A run whose cover claims a node id its own logical plan never held."""
    result = a_run_over_one_cover_of_two_fragments()
    cover = result.pipeline.physical_plans["pp1"]
    scan = next(one for one in cover.fragments if one.root_logical_node_id == "n1")
    broken = dataclasses.replace(
        cover,
        fragments=[
            dataclasses.replace(scan, logical_node_ids=["n1", "n9"]),
            *(one for one in cover.fragments if one is not scan),
        ],
    )
    return dataclasses.replace(
        result,
        pipeline=dataclasses.replace(result.pipeline, physical_plans={"pp1": broken}),
    )


def test_a_cover_naming_a_node_its_plan_does_not_hold_is_raised_rather_than_drawn() -> None:
    """Drawing a placeholder step would put malformed state in front of a reader as a fact."""
    with pytest.raises(UnknownNodeError):
        traced_covers(a_cover_naming_a_node_the_plan_lacks())
