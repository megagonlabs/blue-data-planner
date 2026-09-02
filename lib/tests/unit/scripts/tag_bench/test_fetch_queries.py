"""Tests for lib/scripts/tag_bench/fetch_queries.py."""

from __future__ import annotations

import pytest

from scripts.tag_bench.fetch_queries import (
    build_dev_test_split,
    build_queries_document,
    corrected_answer,
    corrected_db_name,
    parse_answer_cell,
    parse_query_rows,
)


def test_parse_answer_cell_parses_int() -> None:
    """A plain integer cell parses to an int."""
    assert parse_answer_cell("42") == 42


def test_parse_answer_cell_parses_list_literal() -> None:
    """A Python list-literal cell parses to a list of strings."""
    assert parse_answer_cell("['a', 'b']") == ["a", "b"]


def test_parse_answer_cell_falls_back_to_string() -> None:
    """A cell that is neither an int nor a list literal stays a plain string."""
    assert parse_answer_cell("Lakeport") == "Lakeport"


def test_corrected_answer_applies_static_override() -> None:
    """Query 33's answer override replaces the vendor CSV cell entirely."""
    assert corrected_answer(33, "2") == 1


def test_corrected_answer_applies_query_51_special_case() -> None:
    """Query 51's raw comma-separated cell is split and one title's spacing is restored."""
    result = corrected_answer(51, "report eta squared paper, another paper")
    assert result == ["report  eta squared paper", "another paper"]


def test_corrected_answer_parses_uncorrected_cell() -> None:
    """A query id with no override parses the raw cell with parse_answer_cell."""
    assert corrected_answer(999, "7") == 7


def test_corrected_db_name_applies_override() -> None:
    """Query 25's db_name override replaces its vendor CSV value."""
    assert corrected_db_name(25, "codebase_community") == "debit_card_specializing"


def test_corrected_db_name_passes_through_uncorrected() -> None:
    """A query id with no db_name override keeps its vendor CSV value."""
    assert corrected_db_name(999, "formula_1") == "formula_1"


_FIXTURE_CSV = (
    "Query ID,Query,Query type,DB used,Answer\n"
    '33,"How many schools?",Match,california_schools,2\n'
    '42,"How many days?",Aggregation,formula_1,1236\n'
    '45,"How many players?",Comparison,european_football_2,24\n'
    '51,"Which titles?",Ranking,codebase_community,'
    '"report eta squared paper, another paper"\n'
    '53,"What comment?",Match,codebase_community,"wrong text"\n'
    '1000,"Which cities?",Ranking,formula_1,"[\'Mexico City\']"\n'
    '25,"Which cards?",Match,codebase_community,7\n'
)


def test_parse_query_rows_applies_all_corrections() -> None:
    """Parsing the fixture CSV applies every override to its target row."""
    records = parse_query_rows(
        _FIXTURE_CSV, required_correction_ids=frozenset({33, 42, 45, 51, 53, 1000, 25})
    )
    by_id = {record["query_id"]: record for record in records}
    assert by_id[33]["answer"] == 1
    assert by_id[42]["answer"] == 1239
    assert by_id[45]["answer"] == 26
    assert by_id[25]["db_name"] == "debit_card_specializing"


def test_parse_query_rows_raises_on_missing_correction_target() -> None:
    """Parsing raises when a required correction's query id is absent from the CSV."""
    with pytest.raises(ValueError, match="999"):
        parse_query_rows(_FIXTURE_CSV, required_correction_ids=frozenset({999}))


def test_parse_query_rows_keeps_uncorrected_fields() -> None:
    """A row untouched by any correction keeps its question and query_type as parsed."""
    records = parse_query_rows(_FIXTURE_CSV, required_correction_ids=frozenset())
    by_id = {record["query_id"]: record for record in records}
    assert by_id[42]["question"] == "How many days?"
    assert by_id[42]["query_type"] == "Aggregation"


def test_build_dev_test_split_is_deterministic_and_covers_every_id() -> None:
    """The same seed produces the same split, and every id lands in exactly one side."""
    records = parse_query_rows(_FIXTURE_CSV, required_correction_ids=frozenset())
    dev_ids, test_ids = build_dev_test_split(records, dev_per_type=1, split_seed=0)
    assert sorted(dev_ids + test_ids) == sorted(record["query_id"] for record in records)
    assert set(dev_ids).isdisjoint(test_ids)
    dev_ids_again, test_ids_again = build_dev_test_split(records, dev_per_type=1, split_seed=0)
    assert (dev_ids, test_ids) == (dev_ids_again, test_ids_again)


def test_build_dev_test_split_raises_when_a_type_is_too_small() -> None:
    """A query_type with fewer rows than dev_per_type raises, naming that type."""
    records = parse_query_rows(_FIXTURE_CSV, required_correction_ids=frozenset())
    with pytest.raises(ValueError, match="Aggregation"):
        build_dev_test_split(records, dev_per_type=2, split_seed=0)


def test_build_queries_document_sorts_by_query_id_and_carries_metadata() -> None:
    """The document carries the given commit/timestamp and sorts queries by id."""
    records = parse_query_rows(_FIXTURE_CSV, required_correction_ids=frozenset())
    document = build_queries_document(
        records, source_commit="abc123", fetched_at="2026-08-18T00:00:00+00:00"
    )
    assert document["source_commit"] == "abc123"
    assert document["fetched_at"] == "2026-08-18T00:00:00+00:00"
    assert [q["query_id"] for q in document["queries"]] == sorted(
        record["query_id"] for record in records
    )
