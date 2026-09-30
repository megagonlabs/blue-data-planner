"""The TAG-Bench benchmark: run a selection of queries, or report a run already on disk."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blue_data_planner import DataPlanner, RunLimits
from tests.benchmarks.tag_bench.benchmark import report
from tests.benchmarks.tag_bench.benchmark.planner import BenchmarkSettings, build_benchmark_planner
from tests.benchmarks.tag_bench.benchmark.queries import (
    DEFAULT_QUERY_TYPES,
    QUERY_TYPES,
    SPLITS,
    select_queries,
)
from tests.benchmarks.tag_bench.benchmark.runner import QueryOutcome, run_query
from tests.benchmarks.tag_bench.fetch_queries import QueryRecord
from tests.benchmarks.tag_bench.paths import DEFAULT_DATA_DIR

DEFAULT_MODEL: str = "openai/gpt-6-luna"
DEFAULT_REASONING_EFFORT: str = "low"
DEFAULT_LOTUS_MAX_ROWS: int = 5000
DEFAULT_WORKERS: int = 4
# A run reaches a provider on its first model call, so the key is checked before a worker starts
# rather than once per query. A model whose prefix is not listed here is not checked.
PROVIDER_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_worker_planner: DataPlanner | None = None


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """Parse one invocation of the benchmark."""
    parser = argparse.ArgumentParser(
        prog="tests.benchmarks.tag_bench.benchmark", description=__doc__
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run a selection of queries and report it.")
    run.add_argument("--split", choices=SPLITS, default="dev")
    run.add_argument(
        "--query-type",
        action="extend",
        nargs="+",
        type=_query_type,
        default=None,
        help=f"One or more of {', '.join(QUERY_TYPES)}, case-insensitive.",
    )
    run.add_argument("--query-id", action="append", type=int, default=None)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run.add_argument("--run-name", default=None)
    run.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    run.add_argument("--registry", type=Path, default=None)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    run.add_argument("--lotus-max-rows", type=int, default=DEFAULT_LOTUS_MAX_ROWS)

    report_command = commands.add_parser("report", help="Summarize a run already on disk.")
    report_command.add_argument("--run-name", required=True)
    report_command.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)

    arguments = parser.parse_args(argv)
    if arguments.command == "run" and arguments.query_type is None:
        arguments.query_type = list(DEFAULT_QUERY_TYPES)
    return arguments


def record_path(run_dir: Path, query_id: int) -> Path:
    """Return where one query's record lives."""
    return run_dir / f"query_{query_id}.json"


def prepare_run_dir(data_dir: Path, run_name: str) -> Path:
    """Return this run's directory, made empty of records, refusing one already holding some."""
    run_dir = data_dir / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    held = sorted(path.name for path in run_dir.glob("query_*.json"))
    if held:
        raise ValueError(
            f"the run directory {run_name!r} already holds {len(held)} record(s), starting with "
            f"{held[0]}; name another run with --run-name"
        )
    return run_dir


def check_provider_key(model: str) -> None:
    """Raise where the model's provider needs a key the environment does not hold."""
    variable = PROVIDER_KEYS.get(model.partition("/")[0])
    if variable is not None and not os.environ.get(variable):
        raise ValueError(
            f"{variable} is not set, and model {model!r} needs it; export it in the environment"
        )


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark, or report a run, and return the process exit code."""
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    if arguments.command == "report":
        _report(arguments.data_dir / "runs" / arguments.run_name)
        return 0
    return _run(arguments)


def _run(arguments: argparse.Namespace) -> int:
    settings = _settings(arguments)
    queries = select_queries(
        arguments.data_dir,
        split=arguments.split,
        query_types=arguments.query_type,
        query_ids=arguments.query_id,
        limit=arguments.limit,
    )
    if not queries:
        raise ValueError("the selection holds no query; nothing would be run")
    run_dir = prepare_run_dir(arguments.data_dir, arguments.run_name or _run_name(settings.model))
    _write_config(run_dir, settings, arguments, queries)
    print(
        f"{run_dir}: {len(queries)} queries to run, model {settings.model}, "
        f"{arguments.workers} worker(s)",
        file=sys.stderr,
    )
    for outcome in _outcomes(queries, settings, arguments.workers):
        _write_outcome(run_dir, outcome)
        print(_progress_line(outcome), file=sys.stderr)
    _report(run_dir)
    return 0


def _report(run_dir: Path) -> None:
    summary = report.summarize(report.read_records(run_dir))
    report.write_summary(run_dir, summary)
    for line in report.summary_lines(summary):
        print(line)


def _outcomes(
    queries: list[QueryRecord], settings: BenchmarkSettings, workers: int
) -> Iterator[QueryOutcome]:
    if workers <= 1:
        # One process, so a breakpoint works and LOTUS is configured here rather than in a pool.
        planner = build_benchmark_planner(settings)
        for query in queries:
            yield run_query(planner, query)
        return
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_initialize_worker, initargs=(settings,)
    ) as pool:
        # In completion order, so one slow query holds back no record that already finished.
        futures = [pool.submit(_run_in_worker, query) for query in queries]
        for future in as_completed(futures):
            yield future.result()


def _initialize_worker(settings: BenchmarkSettings) -> None:
    global _worker_planner
    # One planner per worker process: opening the registry and configuring LOTUS per query would
    # pay for five database syncs over and over.
    _worker_planner = build_benchmark_planner(settings)


def _run_in_worker(query: QueryRecord) -> QueryOutcome:
    if _worker_planner is None:
        raise RuntimeError("this worker holds no planner, so its initializer did not run")
    return run_query(_worker_planner, query)


def _settings(arguments: argparse.Namespace) -> BenchmarkSettings:
    registry_path = arguments.registry or (arguments.data_dir / "registry.json")
    if not registry_path.is_file():
        raise ValueError(
            f"no registry at {registry_path}; build one with "
            "python -m tests.benchmarks.tag_bench.register_databases, run from lib/"
        )
    check_provider_key(arguments.model)
    return BenchmarkSettings(
        registry_path=registry_path,
        model=arguments.model,
        reasoning_effort=arguments.reasoning_effort,
        lotus_max_rows=arguments.lotus_max_rows,
        limits=RunLimits(),
    )


def _query_type(value: str) -> str:
    for name in QUERY_TYPES:
        if name.lower() == value.strip().lower():
            return name
    raise argparse.ArgumentTypeError(
        f"unknown query type {value!r}; expected one of {', '.join(QUERY_TYPES)}"
    )


def _run_name(model: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{model.replace('/', '_')}"


def _write_config(
    run_dir: Path,
    settings: BenchmarkSettings,
    arguments: argparse.Namespace,
    queries: list[QueryRecord],
) -> None:
    payload: dict[str, Any] = {
        "settings": {
            **dataclasses.asdict(settings),
            "registry_path": str(settings.registry_path),
        },
        "selection": {
            "split": arguments.split,
            "query_types": arguments.query_type,
            "query_ids": [query["query_id"] for query in queries],
            "limit": arguments.limit,
        },
        "workers": arguments.workers,
        "started_at": datetime.now(UTC).isoformat(),
        "commit": _commit(),
    }
    (run_dir / "config.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def _write_outcome(run_dir: Path, outcome: QueryOutcome) -> None:
    query_id = int(outcome.record["query_id"])
    record_path(run_dir, query_id).write_text(
        json.dumps(outcome.record, indent=2, default=str) + "\n"
    )
    (run_dir / f"query_{query_id}.conversations.json").write_text(
        json.dumps(outcome.conversations, indent=2, default=str) + "\n"
    )


def _progress_line(outcome: QueryOutcome) -> str:
    record = outcome.record
    mark = "correct" if record["correct"] else "wrong  "
    usage: dict[str, Any] = record["usage"] or {}
    cost = usage.get("cost", 0.0)
    return (
        f"query {record['query_id']:>4}  {mark}  {record['status']:<18} "
        f"{record['elapsed_seconds']:>7.1f}s  {report.dollars(cost, places=3)}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
