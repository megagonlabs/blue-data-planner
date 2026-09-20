"""Selecting the TAG-Bench queries one benchmark run covers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.tag_bench.benchmark.scoring import states_an_answer
from scripts.tag_bench.fetch_queries import QueryRecord

QUERY_TYPES: tuple[str, ...] = ("Match", "Comparison", "Ranking", "Aggregation")
# Every aggregation query asks for a summary and carries an empty gold answer, so exact match
# cannot score one. states_an_answer() is what leaves them out; the type list names the default.
DEFAULT_QUERY_TYPES: tuple[str, ...] = ("Match", "Comparison", "Ranking")
SPLITS: tuple[str, ...] = ("dev", "test", "all")


def load_queries(data_dir: Path) -> list[QueryRecord]:
    """Return every corrected query, in id order."""
    path = data_dir / "queries.json"
    if not path.is_file():
        raise ValueError(
            f"no queries at {path}; build the inputs first with "
            "lib/scripts/tag_bench/fetch_queries.py"
        )
    document: Any = json.loads(path.read_text())
    queries: list[QueryRecord] = document["queries"]
    return sorted(queries, key=lambda query: query["query_id"])


def select_queries(
    data_dir: Path,
    split: str = "dev",
    query_types: Sequence[str] = DEFAULT_QUERY_TYPES,
    query_ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> list[QueryRecord]:
    """Return the queries a run covers, in id order."""
    queries = load_queries(data_dir)
    if query_ids is not None:
        by_id = {query["query_id"]: query for query in queries}
        missing = [query_id for query_id in query_ids if query_id not in by_id]
        if missing:
            raise ValueError(f"the dataset holds no query with id {missing}")
        # A named id runs whatever it carries, a query with no gold answer included: naming one
        # is how its plan is inspected.
        return [by_id[query_id] for query_id in sorted(set(query_ids))][:limit]
    unknown_types = [name for name in query_types if name not in QUERY_TYPES]
    if unknown_types:
        raise ValueError(
            f"unknown query type {unknown_types}; expected one or more of {list(QUERY_TYPES)}"
        )
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; expected one of {list(SPLITS)}")
    # A slice by None is the whole list, so no branch on limit is needed.
    return [
        query
        for query in queries
        if (split == "all" or query["split"] == split)
        and query["query_type"] in query_types
        and states_an_answer(query["gold_answer"])
    ][:limit]
