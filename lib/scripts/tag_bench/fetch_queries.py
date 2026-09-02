"""Download and correct TAG-Bench's tag_queries.csv into data/tag_bench/queries.json."""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import random
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

AnswerValue = int | str | list[str]

_SPECIAL_ANSWER_QUERY_ID = 51

# Corrections for known TAG-Bench vendor CSV errors. Reimplemented from (not copied from)
# ~/dev/redundancy-aware-data-planning/preprocessing/tag_bench/build_queries.py; the
# gold-LOTUS-program corrections there have no counterpart here, since this planner
# does not run TAG-Bench's hand-written LOTUS pipelines.
ANSWER_OVERRIDES: dict[int, AnswerValue] = {
    # Query 33's vendor answer of 2 counts a district aggregate row alongside the one
    # actual school matching the filter; the correct count of schools is 1.
    33: 1,
    # Query 42's vendor answer is 1236; the reference pipeline returns 1239 from the
    # same "days in April" constant the program logic depends on.
    42: 1239,
    # Query 45 asks for weight "more than 77.1kg" (= 169.976 lb); two players weigh
    # exactly 170 lb, so the intended threshold is the original "> 170 lbs" filter,
    # which gives 26, not the vendor answer of 24.
    45: 26,
    # Query 53's vendor cell wraps the gold comment in extra quotes and drops its
    # trailing "\n;-)", which the database's stored text actually ends with.
    53: (
        "That pirates / global warming chart is clearly cooked up by conspiracy "
        "theorists - anyone can see they have deliberately plotted even spacing "
        "for unequal time periods to avoid showing the recent sharp increase in "
        "temperature as pirates are almost entirely wiped out.\n"
        "We all know that as temperatures rise it makes the rum evaporate and "
        "pirates cannot survive those conditions.\n"
        ";-)"
    ),
    # Query 1000's vendor cell drops the diacritic in "São Paulo".
    1000: ["Mexico City", "São Paulo", "Abu Dhabi", "Austin", "Suzuka"],
}

# Query 25's vendor row assigns the wrong db_name; its gold program reads only
# debit_card_specializing tables.
DB_NAME_OVERRIDES: dict[int, str] = {
    25: "debit_card_specializing",
}

KNOWN_CORRECTION_IDS: frozenset[int] = (
    frozenset(ANSWER_OVERRIDES) | {_SPECIAL_ANSWER_QUERY_ID} | frozenset(DB_NAME_OVERRIDES)
)


def parse_answer_cell(raw_answer: str) -> AnswerValue:
    """Parse an uncorrected TAG-Bench vendor CSV answer cell into a typed value."""
    text = raw_answer.strip()
    try:
        return int(text)
    except ValueError:
        pass
    if text.startswith("[") and text.endswith("]"):
        parsed = ast.literal_eval(text)
        return [str(item) for item in parsed]
    return text


def corrected_answer(query_id: int, raw_answer: str) -> AnswerValue:
    """Return one query's gold answer, applying known TAG-Bench vendor CSV corrections."""
    if query_id == _SPECIAL_ANSWER_QUERY_ID:
        # Query 51's vendor cell is a comma-separated string, not a Python list literal,
        # and collapses a double space the database's stored title actually has.
        titles = [title.strip() for title in raw_answer.split(", ")]
        return [title.replace("report eta squared", "report  eta squared") for title in titles]
    if query_id in ANSWER_OVERRIDES:
        return ANSWER_OVERRIDES[query_id]
    return parse_answer_cell(raw_answer)


def corrected_db_name(query_id: int, raw_db_name: str) -> str:
    """Return one query's database name, applying the known vendor CSV db_name correction."""
    return DB_NAME_OVERRIDES.get(query_id, raw_db_name)


class QueryRecord(TypedDict):
    query_id: int
    question: str
    query_type: str
    db_name: str
    answer: AnswerValue


def parse_query_rows(
    csv_text: str, required_correction_ids: frozenset[int] = KNOWN_CORRECTION_IDS
) -> list[QueryRecord]:
    """Parse tag_queries.csv text into corrected query records.

    Raises:
        ValueError: If a query id in required_correction_ids is absent from csv_text.
    """
    records: list[QueryRecord] = []
    seen_ids: set[int] = set()
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        query_id = int(row["Query ID"])
        seen_ids.add(query_id)
        records.append(
            QueryRecord(
                query_id=query_id,
                question=row["Query"],
                query_type=row["Query type"],
                db_name=corrected_db_name(query_id, row["DB used"]),
                answer=corrected_answer(query_id, row["Answer"]),
            )
        )
    missing = sorted(required_correction_ids - seen_ids)
    if missing:
        raise ValueError(f"Expected query ids {missing} in tag_queries.csv, but they were absent")
    return records


DEFAULT_DEV_PER_TYPE = 4
DEFAULT_SPLIT_SEED = 0

TAG_BENCH_REPO = "TAG-Research/TAG-Bench"
TAG_BENCH_BRANCH = "main"


def build_dev_test_split(
    records: list[QueryRecord], dev_per_type: int, split_seed: int
) -> tuple[list[int], list[int]]:
    """Split query ids into a fixed dev/test partition, sampling dev_per_type ids per query_type.

    Raises:
        ValueError: If a query_type has fewer than dev_per_type examples.
    """
    by_type: dict[str, list[int]] = {}
    for record in records:
        by_type.setdefault(record["query_type"], []).append(record["query_id"])

    rng = random.Random(split_seed)
    dev_ids: list[int] = []
    test_ids: list[int] = []
    for query_type, ids in by_type.items():
        type_ids = sorted(ids)
        if len(type_ids) < dev_per_type:
            raise ValueError(
                f"Query type {query_type!r} has only {len(type_ids)} examples, "
                f"but dev_per_type={dev_per_type}"
            )
        dev_for_type = set(rng.sample(type_ids, dev_per_type))
        dev_ids.extend(dev_for_type)
        test_ids.extend(query_id for query_id in type_ids if query_id not in dev_for_type)

    dev_ids.sort()
    test_ids.sort()
    return dev_ids, test_ids


class QueriesDocument(TypedDict):
    source_commit: str
    fetched_at: str
    queries: list[QueryRecord]


def build_queries_document(
    records: list[QueryRecord], source_commit: str, fetched_at: str
) -> QueriesDocument:
    """Assemble the queries.json document from corrected records, sorted by query_id."""
    sorted_records = sorted(records, key=lambda record: record["query_id"])
    return QueriesDocument(
        source_commit=source_commit,
        fetched_at=fetched_at,
        queries=sorted_records,
    )


def resolve_branch_sha(repo: str, branch: str) -> str:
    """Resolve a GitHub branch to its current commit SHA."""
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request) as response:
        payload = json.load(response)
    return str(payload["sha"])


def fetch_queries_csv(
    repo: str = TAG_BENCH_REPO, branch: str = TAG_BENCH_BRANCH
) -> tuple[str, str]:
    """Download tag_queries.csv at branch's current commit. Returns (csv_text, commit_sha)."""
    sha = resolve_branch_sha(repo, branch)
    url = f"https://raw.githubusercontent.com/{repo}/{sha}/tag_queries.csv"
    with urllib.request.urlopen(url) as response:
        csv_text = response.read().decode("utf-8")
    return csv_text, sha


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and correct TAG-Bench queries")
    parser.add_argument("--data-dir", type=Path, default=Path("data/tag_bench"))
    parser.add_argument("--dev-per-type", type=int, default=DEFAULT_DEV_PER_TYPE)
    parser.add_argument("--split-seed", type=int, default=DEFAULT_SPLIT_SEED)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    queries_path: Path = args.data_dir / "queries.json"
    if queries_path.exists() and not args.force:
        print(f"skip {queries_path} (exists; use --force to rebuild)")
        return

    csv_text, source_commit = fetch_queries_csv()
    records = parse_query_rows(csv_text)
    dev_ids, test_ids = build_dev_test_split(records, args.dev_per_type, args.split_seed)
    fetched_at = datetime.now(timezone.utc).isoformat()
    document = build_queries_document(records, source_commit, fetched_at)

    splits_dir = args.data_dir / "splits"
    _write_json(queries_path, document)
    _write_json(splits_dir / "dev_query_ids.json", dev_ids)
    _write_json(splits_dir / "test_query_ids.json", test_ids)
    print(f"Wrote {len(records)} queries to {queries_path} (commit {source_commit})")
    print(f"Wrote {len(dev_ids)} dev ids and {len(test_ids)} test ids to {splits_dir}")


if __name__ == "__main__":
    main()
