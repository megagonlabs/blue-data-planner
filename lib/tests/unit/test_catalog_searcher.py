"""What one DISCOVER node resolves to."""

from __future__ import annotations

from typing import Any

from blue_data_planner.catalog_searcher import CatalogSearcher
from blue_data_planner.types import (
    ControlOperator,
    Conversation,
    EntityMatch,
    PipelineNode,
)
from tests.doubles.catalog import an_attribute_view
from tests.doubles.llm import ScriptedLLMClient

POSTINGS = "/source/jobs/database/jobs/collection/public/entity/postings"


class FakeCatalog:
    """A catalog whose search returns a fixed match per term."""

    def __init__(self, by_term: dict[str, list[EntityMatch]]) -> None:
        self.by_term = by_term
        self.searched: list[str] = []

    def search_records(self, term: str) -> list[EntityMatch]:
        self.searched.append(term)
        return self.by_term.get(term, [])


def a_match(term: str, score: float) -> EntityMatch:
    """One entity a term found, carrying one attribute."""
    return EntityMatch(
        path=POSTINGS,
        name="postings",
        description="job postings",
        source_kind="sql/sqlite",
        attributes=[an_attribute_view("title")],
        score=score,
        search_term=term,
        row_count=None,
    )


def a_discover_node(instruction: str = "find a source holding job postings") -> PipelineNode:
    """A DISCOVER node with no inputs, asking for a source of job postings."""
    return PipelineNode(
        id="n1",
        kind=ControlOperator.DISCOVER,
        input_node_ids=[],
        attributes={"instruction": instruction},
    )


def a_searcher(terms: list[str], catalog: Any, cap: int = 8) -> CatalogSearcher:
    """A searcher whose one model call answers with these terms."""
    # ScriptedLLMClient stands in for LLMClient structurally; pyright's strict nominal
    # check does not see the two as related.
    return CatalogSearcher(
        ScriptedLLMClient([{"terms": terms}]),  # pyright: ignore[reportArgumentType]
        catalog,
        cap,
    )


def test_a_discovery_nodes_instruction_becomes_terms_and_each_searches_once() -> None:
    """One model call writes the terms, and every term reaches the registry."""
    catalog = FakeCatalog({"job postings": [a_match("job postings", 0.9)]})
    result = a_searcher(["job postings", "vacancies"], catalog).search(
        a_discover_node(), Conversation(), []
    )
    assert catalog.searched == ["job postings", "vacancies"]
    assert result.search_terms == ["job postings", "vacancies"]


def test_terms_past_the_cap_are_dropped_and_counted() -> None:
    """A reader of the result can tell a complete search from a capped one."""
    catalog = FakeCatalog({})
    result = a_searcher(["a", "b", "c"], catalog, cap=2).search(
        a_discover_node(), Conversation(), []
    )
    assert catalog.searched == ["a", "b"]
    assert result.dropped_terms == 1


def test_one_entity_found_by_two_terms_appears_once_keeping_its_better_score() -> None:
    """The catalog context names each entity once, however many terms reached it."""
    catalog = FakeCatalog(
        {"job postings": [a_match("job postings", 0.4)], "vacancies": [a_match("vacancies", 0.9)]}
    )
    result = a_searcher(["job postings", "vacancies"], catalog).search(
        a_discover_node(), Conversation(), []
    )
    assert [(match.path, match.score) for match in result.matches] == [(POSTINGS, 0.9)]


def test_an_input_table_is_shown_with_its_first_rows_and_a_count_of_the_rows_left_out() -> None:
    """The rows not shown are counted, so the model does not take the sample for the table."""
    conversation = Conversation()
    rows = [{"owner_id": index} for index in range(8)]

    a_searcher(["users"], FakeCatalog({})).search(a_discover_node(), conversation, [rows])

    turn = conversation.turns[-2].content
    assert "Table 0: 8 row(s)" in turn
    assert "{'owner_id': 4}" in turn
    assert "{'owner_id': 5}" not in turn
    assert "(+3 more row(s))" in turn  # eight rows, five shown


def test_the_opening_instructions_carry_nothing_that_varies_by_node() -> None:
    """A search is one exchange, and its opening is the same one every search opens with."""
    first, second = Conversation(), Conversation()
    a_searcher(["stores"], FakeCatalog({})).search(a_discover_node(), first, [])
    a_searcher(["salaries"], FakeCatalog({})).search(
        a_discover_node(instruction="find the salary tables"), second, []
    )

    assert first.turns[0].content == second.turns[0].content
    assert "find the salary tables" in second.turns[1].content
