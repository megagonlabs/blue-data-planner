"""Tests for lib/tests/benchmarks/tag_bench/benchmark/scoring.py."""

from __future__ import annotations

from typing import Any

import pytest

from tests.benchmarks.tag_bench.benchmark.scoring import extract_prediction, score_answer
from tests.benchmarks.tag_bench.fetch_queries import AnswerValue


def one_table(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Wrap rows as the one-table bundle a RETURN node produces."""
    return [rows]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([{"count": 54}], 54),
        ([{"phone": "(562) 944-0033"}], "(562) 944-0033"),
        ([{"name": "A High"}, {"name": "B High"}], ["A High", "B High"]),
        ([{"first": "Michael", "last": "Schumacher"}], [["Michael", "Schumacher"]]),
        ([{"count": "54"}], "54"),
    ],
)
def test_an_answer_table_reads_as_the_shape_a_gold_answer_is_written_in(
    rows: list[dict[str, Any]], expected: Any
) -> None:
    """One cell reads as a scalar, one column as a list, several columns as rows of fields, and
    every value stays as the operator produced it."""
    assert extract_prediction(one_table(rows)) == expected


@pytest.mark.parametrize(
    ("rows", "gold"),
    [
        ([{"count": 54}], 54),
        ([{"count": "54"}], 54),
        ([{"charter": True}], "Yes"),
        ([{"charter": "true"}], True),
        ([{"name": "Whitney High"}], ["Whitney High"]),
        ([{"name": "A High"}, {"name": "B High"}], ["A High", "B High"]),
        ([{"takers": 115372}, {"takers": 115254}], ["115372", "115254"]),
        ([{"first": "Michael", "last": "Schumacher"}], ["Michael", "Schumacher"]),
    ],
)
def test_an_answer_written_another_way_than_the_gold_still_scores_correct(
    rows: list[dict[str, Any]], gold: AnswerValue
) -> None:
    """A number written as a string on either side, a boolean spelling, and a scalar for a
    one-element list are all the same answer."""
    assert score_answer(one_table(rows), gold).correct


@pytest.mark.parametrize(
    ("rows", "gold"),
    [
        ([{"count": 55}], 54),
        ([{"rate": "9.1"}], "9.09"),
        ([{"charter": True}], 1),
        ([{"name": "B High"}, {"name": "A High"}], ["A High", "B High"]),
        ([{"name": "A High"}], ["A High", "B High"]),
        ([{"name": "A High"}, {"name": "B High"}, {"name": "C High"}], ["A High", "B High"]),
    ],
)
def test_an_answer_the_gold_does_not_state_scores_wrong(
    rows: list[dict[str, Any]], gold: AnswerValue
) -> None:
    """A wrong value, a wrong order, a wrong row count, and a boolean against a count all
    score wrong."""
    assert not score_answer(one_table(rows), gold).correct


@pytest.mark.parametrize(
    "answer",
    [None, [], [[]], [[{"name": "A High"}], [{"name": "B High"}]]],
)
def test_a_run_that_stated_no_single_answer_scores_wrong_and_says_why(
    answer: list[list[dict[str, Any]]] | None,
) -> None:
    """No answer, an empty table, and several tables leave no prediction and a note."""
    scored = score_answer(answer, 54)

    assert (scored.prediction, scored.correct) == (None, False)
    assert scored.note is not None


def test_a_query_with_no_gold_answer_scores_wrong_and_says_so() -> None:
    """An aggregation query names no answer to match, so nothing can score correct."""
    scored = score_answer(one_table([{"summary": "the circuits are fast"}]), "")

    assert not scored.correct
    assert scored.note is not None


def test_a_gold_answer_of_zero_is_an_answer_and_scores_a_matching_count_correct() -> None:
    """A count of zero states an answer, unlike an empty string, so it is scored."""
    assert score_answer(one_table([{"count": 0}]), 0).correct
