"""Tests for lib/tests/benchmarks/tag_bench/benchmark/report.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.benchmarks.tag_bench.benchmark.report import read_records, summarize


def a_record(
    query_id: int,
    query_type: str,
    correct: bool,
    status: str = "ok",
    operator_calls: int = 2,
) -> dict[str, Any]:
    """One record shaped as the runner writes it, with the fields a summary reads."""
    return {
        "query_id": query_id,
        "query_type": query_type,
        "db_name": "retail",
        "correct": correct,
        "status": status,
        "failure_reason": None if status == "ok" else "no cover survived",
        "counters": {
            "operator_calls": operator_calls,
            "failure_replans": 1,
            "control_replans": 0,
        },
        "usage": {
            "calls": 4,
            "cost": 0.25,
            "by_component": {"logical_planner": {"calls": 1, "cost": 0.1}},
        },
        "pipeline": {
            "logical_plans": {"lp1": {}},
            "physical_plans": {
                "pp1": {
                    "logical_plan_id": "lp1",
                    "fragments": [{"logical_node_ids": ["n1", "n2"]}],
                }
            },
        },
        "elapsed_seconds": 12.5,
    }


def a_run_dir_with_three_records(root: Path) -> Path:
    """Two Match records, one of them wrong, one correct Ranking record, and the two files a run
    writes beside them: a config and one query's transcripts."""
    run_dir = root / "runs" / "one"
    run_dir.mkdir(parents=True)
    for record in (
        a_record(1, "Match", correct=True),
        a_record(2, "Match", correct=False, status="budget_exhausted"),
        a_record(3, "Ranking", correct=True),
    ):
        (run_dir / f"query_{record['query_id']}.json").write_text(json.dumps(record))
    (run_dir / "config.json").write_text(json.dumps({"model": "openai/gpt-5.6-luna"}))
    (run_dir / "query_1.conversations.json").write_text(json.dumps({"logical_planner": {}}))
    return run_dir


def test_a_run_directory_reads_back_as_its_query_records(tmp_path: Path) -> None:
    """Only the query records are read, in id order: neither the config nor a transcript is one."""
    run_dir = a_run_dir_with_three_records(tmp_path)

    records = read_records(run_dir)

    assert [record["query_id"] for record in records] == [1, 2, 3]


def test_a_summary_reports_exact_match_overall_and_per_query_type(tmp_path: Path) -> None:
    """Two of three correct overall, one of two for Match, one of one for Ranking."""
    records = read_records(a_run_dir_with_three_records(tmp_path))

    summary = summarize(records)

    assert summary["scored"] == 3
    assert summary["exact_match"] == 2 / 3
    assert summary["by_query_type"]["Match"] == {"scored": 2, "correct": 1, "exact_match": 0.5}
    assert summary["by_query_type"]["Ranking"]["exact_match"] == 1.0


def test_a_summary_counts_what_the_run_spent_and_how_its_queries_ended(tmp_path: Path) -> None:
    """The run-level measurements the design asks the benchmark to report."""
    records = read_records(a_run_dir_with_three_records(tmp_path))

    summary = summarize(records)

    assert summary["operator_calls"]["mean"] == 2.0
    assert summary["model_calls"]["total"] == 12
    assert summary["cost"]["total"] == 0.75
    assert summary["model_calls_by_component"]["logical_planner"]["calls"] == 3
    assert summary["status_counts"] == {"ok": 2, "budget_exhausted": 1}
    assert summary["failure_reasons"] == {"no cover survived": 1}
    assert summary["replans"]["failure_replans_total"] == 3
    assert summary["covers_per_logical_plan"]["mean"] == 1.0
    assert summary["fragment_size"]["mean_nodes"] == 2.0


def test_a_run_directory_with_no_record_is_refused(tmp_path: Path) -> None:
    """Reporting a run that never wrote a record raises rather than reporting zero of zero."""
    empty = tmp_path / "runs" / "empty"
    empty.mkdir(parents=True)

    with pytest.raises(ValueError, match="no query record"):
        read_records(empty)
