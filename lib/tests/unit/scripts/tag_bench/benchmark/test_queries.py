"""Tests for lib/scripts/tag_bench/benchmark/queries.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.tag_bench.benchmark.queries import load_queries, select_queries


def a_data_dir_with_both_splits(root: Path) -> Path:
    """Write a queries.json of six queries: three dev, three test, two unscorable."""
    queries = [
        {
            "query_id": 1,
            "question": "one match",
            "query_type": "Match",
            "db_name": "california_schools",
            "gold_answer": 54,
            "split": "dev",
        },
        {
            "query_id": 2,
            "question": "one comparison",
            "query_type": "Comparison",
            "db_name": "formula_1",
            "gold_answer": "Monza",
            "split": "dev",
        },
        {
            "query_id": 3,
            "question": "one summary",
            "query_type": "Aggregation",
            "db_name": "formula_1",
            "gold_answer": "",
            "split": "dev",
        },
        {
            "query_id": 4,
            "question": "one ranking",
            "query_type": "Ranking",
            "db_name": "codebase_community",
            "gold_answer": ["a", "b"],
            "split": "test",
        },
        {
            "query_id": 5,
            "question": "another match",
            "query_type": "Match",
            "db_name": "codebase_community",
            "gold_answer": "Lakeport",
            "split": "test",
        },
        {
            "query_id": 6,
            "question": "another summary",
            "query_type": "Aggregation",
            "db_name": "european_football_2",
            "gold_answer": "",
            "split": "test",
        },
    ]
    data_dir = root / "tag_bench"
    data_dir.mkdir(parents=True)
    (data_dir / "queries.json").write_text(
        json.dumps({"source_commit": "abc", "fetched_at": "2026-01-01", "queries": queries})
    )
    return data_dir


def test_a_split_selects_its_own_queries_of_the_named_types(tmp_path: Path) -> None:
    """The dev split holds ids 1, 2, and 3, and the default types leave the summary out."""
    data_dir = a_data_dir_with_both_splits(tmp_path)

    selected = select_queries(data_dir, split="dev")

    assert [query["query_id"] for query in selected] == [1, 2]


def test_every_split_together_selects_every_query_of_the_named_types(tmp_path: Path) -> None:
    """Split "all" ignores which split a query belongs to and keeps the type filter."""
    data_dir = a_data_dir_with_both_splits(tmp_path)

    selected = select_queries(data_dir, split="all", query_types=("Match",))

    assert [query["query_id"] for query in selected] == [1, 5]


def test_a_query_carrying_no_gold_answer_is_left_out_of_a_type_selection(tmp_path: Path) -> None:
    """An aggregation query states no answer to score, so asking for its type selects nothing."""
    data_dir = a_data_dir_with_both_splits(tmp_path)

    assert select_queries(data_dir, split="all", query_types=("Aggregation",)) == []


def test_naming_query_ids_selects_them_whatever_the_split_and_the_gold_answer_hold(
    tmp_path: Path,
) -> None:
    """An id list is the selection: neither the split, the type, nor an empty answer narrows it."""
    data_dir = a_data_dir_with_both_splits(tmp_path)

    selected = select_queries(data_dir, split="dev", query_ids=[3, 4])

    assert [query["query_id"] for query in selected] == [3, 4]


def test_a_limit_keeps_the_first_queries_of_the_selection(tmp_path: Path) -> None:
    """A limit truncates the selection rather than sampling it."""
    data_dir = a_data_dir_with_both_splits(tmp_path)

    selected = select_queries(data_dir, split="all", limit=2)

    assert [query["query_id"] for query in selected] == [1, 2]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"query_ids": [99]}, "99"),
        ({"query_types": ("Trivia",)}, "Trivia"),
        ({"split": "holdout"}, "holdout"),
    ],
)
def test_a_selection_naming_something_the_dataset_does_not_hold_is_refused(
    tmp_path: Path, kwargs: dict[str, object], expected: str
) -> None:
    """An unknown id, type, or split raises rather than silently selecting nothing."""
    data_dir = a_data_dir_with_both_splits(tmp_path)

    with pytest.raises(ValueError, match=expected):
        select_queries(data_dir, **kwargs)  # pyright: ignore[reportArgumentType]


def test_a_missing_query_document_is_refused_before_a_run_starts(tmp_path: Path) -> None:
    """A data directory nobody built names the script that builds it."""
    with pytest.raises(ValueError, match="fetch_queries"):
        load_queries(tmp_path)
