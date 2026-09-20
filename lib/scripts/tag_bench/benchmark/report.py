"""Reading a run directory's records and summarizing what they say."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Any

SUMMARY_FILENAME: str = "summary.json"


def read_records(run_dir: Path) -> list[dict[str, Any]]:
    """Return every query record in the run directory, in query id order."""
    if not run_dir.is_dir():
        raise ValueError(f"no run directory at {run_dir}")
    records: list[dict[str, Any]] = [
        json.loads(path.read_text())
        for path in run_dir.glob("query_*.json")
        if not path.name.endswith(".conversations.json")
    ]
    if not records:
        raise ValueError(f"the run directory at {run_dir} holds no query record")
    return sorted(records, key=lambda record: int(record["query_id"]))


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return exact match and the run-level measurements over these records."""
    correct = [record for record in records if record["correct"]]
    return {
        "scored": len(records),
        "correct": len(correct),
        "exact_match": len(correct) / len(records) if records else 0.0,
        "by_query_type": _by_key(records, "query_type"),
        "by_database": _by_key(records, "db_name"),
        "status_counts": _counts(str(record["status"]) for record in records),
        "failure_reasons": _counts(
            str(record["failure_reason"])
            for record in records
            if record.get("failure_reason") is not None
        ),
        "operator_calls": _spread([_counter(record, "operator_calls") for record in records]),
        "model_calls": {"total": int(sum(_usage(record, "calls") for record in records))},
        "cost": {"total": round(sum(_usage(record, "cost") for record in records), 6)},
        "model_calls_by_component": _by_component(records),
        "replans": {
            "failure_replans_total": sum(_counter(record, "failure_replans") for record in records),
            "control_replans_total": sum(_counter(record, "control_replans") for record in records),
            "logical_plans": _spread([_plan_count(record, "logical_plans") for record in records]),
        },
        "covers_per_logical_plan": _spread(_covers_per_logical_plan(records)),
        "fragment_size": {"mean_nodes": _mean(_fragment_sizes(records))},
        "elapsed_seconds": _spread(
            [float(record.get("elapsed_seconds") or 0.0) for record in records]
        ),
    }


def summary_lines(summary: dict[str, Any]) -> list[str]:
    """Return the closing lines a run prints, one measurement per line."""
    lines = [f"exact match {summary['correct']}/{summary['scored']} ({summary['exact_match']:.1%})"]
    for query_type, scores in sorted(summary["by_query_type"].items()):
        lines.append(
            f"  {query_type}: {scores['correct']}/{scores['scored']} ({scores['exact_match']:.1%})"
        )
    lines.append(
        f"operator calls mean {summary['operator_calls']['mean']:.1f}  "
        f"model calls {summary['model_calls']['total']}  "
        f"cost ${summary['cost']['total']:.2f}"
    )
    lines.append(f"statuses {summary['status_counts']}")
    return lines


def write_summary(run_dir: Path, summary: dict[str, Any]) -> Path:
    """Write the summary into the run directory and return its path."""
    path = run_dir / SUMMARY_FILENAME
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    return path


def _by_key(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    scored = Counter(str(record[key]) for record in records)
    correct = Counter(str(record[key]) for record in records if record["correct"])
    return {
        name: {"scored": total, "correct": correct[name], "exact_match": correct[name] / total}
        for name, total in sorted(scored.items())
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(Counter(values))


def _counter(record: dict[str, Any], name: str) -> float:
    counters: dict[str, Any] = record.get("counters") or {}
    return float(counters.get(name, 0))


def _usage(record: dict[str, Any], name: str) -> float:
    usage: dict[str, Any] = record.get("usage") or {}
    return float(usage.get(name, 0))


def _by_component(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for record in records:
        usage: dict[str, Any] = record.get("usage") or {}
        by_component: dict[str, Any] = usage.get("by_component") or {}
        for component, spend in by_component.items():
            held = totals.setdefault(component, {"calls": 0, "cost": 0.0})
            held["calls"] += int(spend.get("calls", 0))
            held["cost"] = round(held["cost"] + float(spend.get("cost", 0.0)), 6)
    return totals


def _plan_count(record: dict[str, Any], key: str) -> float:
    pipeline: dict[str, Any] = record.get("pipeline") or {}
    return float(len(pipeline.get(key) or {}))


def _covers_per_logical_plan(records: list[dict[str, Any]]) -> list[float]:
    counts: list[float] = []
    for record in records:
        pipeline: dict[str, Any] = record.get("pipeline") or {}
        physical_plans: dict[str, Any] = pipeline.get("physical_plans") or {}
        per_plan: dict[str, int] = {}
        for cover in physical_plans.values():
            plan_id = str(cover.get("logical_plan_id"))
            per_plan[plan_id] = per_plan.get(plan_id, 0) + 1
        counts.extend(float(count) for count in per_plan.values())
    return counts


def _fragment_sizes(records: list[dict[str, Any]]) -> list[float]:
    sizes: list[float] = []
    for record in records:
        pipeline: dict[str, Any] = record.get("pipeline") or {}
        physical_plans: dict[str, Any] = pipeline.get("physical_plans") or {}
        for cover in physical_plans.values():
            fragments: list[Any] = cover.get("fragments") or []
            for fragment in fragments:
                sizes.append(float(len(fragment.get("logical_node_ids") or [])))
    return sizes


def _spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "max": 0.0, "total": 0.0}
    return {"mean": round(mean(values), 3), "max": max(values), "total": round(sum(values), 3)}


def _mean(values: list[float]) -> float:
    return round(mean(values), 3) if values else 0.0
