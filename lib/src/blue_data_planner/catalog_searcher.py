"""Resolving a DISCOVER node: an instruction becomes search terms, and terms become entities."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from blue_data_planner.data import DataCatalog
from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import safe_substitute
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import (
    CatalogSearchResult,
    DataBundle,
    DiscoveryResult,
    EntityMatch,
    PipelineNode,
)

SAMPLE_ROWS = 5


class SearchTerms(BaseModel):
    """The terms one discovery node expands into."""

    model_config = ConfigDict(extra="forbid")

    terms: list[str]


class CatalogSearcher:
    """Turns a DISCOVER node into the entities its search terms found."""

    def __init__(self, llm_client: LLMClient, catalog: DataCatalog, max_search_terms: int) -> None:
        self._llm_client = llm_client
        self._catalog = catalog
        self._max_search_terms = max_search_terms

    def search(self, node: PipelineNode, inputs: DataBundle) -> CatalogSearchResult:
        """Write search terms from the node's instruction and search the registry for each."""
        conversation = Conversation(turns=[Turn(role="system", content=self._prompt(node, inputs))])
        written = self._llm_client.complete_structured(conversation, SearchTerms)
        kept = written.terms[: self._max_search_terms]
        matches: dict[str, EntityMatch] = {}
        for term in kept:
            for match in self._catalog.search_records(term):
                held = matches.get(match.path)
                if held is None or match.score > held.score:
                    matches[match.path] = match
        return CatalogSearchResult(
            discovery=DiscoveryResult(
                search_terms=kept,
                matches=sorted(matches.values(), key=lambda match: -match.score),
                dropped_terms=len(written.terms) - len(kept),
            ),
            usage=list(conversation.usage),
        )

    def _prompt(self, node: PipelineNode, inputs: DataBundle) -> str:
        return safe_substitute(
            prompt_text("catalog_searcher.md"),
            instruction=node.attributes.get("instruction", ""),
            max_search_terms=self._max_search_terms,
            input_tables=_render_tables(inputs),
        )


def _render_tables(inputs: DataBundle) -> str:
    if not inputs:
        return ""
    lines: list[str] = []
    for index, table in enumerate(inputs):
        lines.append(f"Table {index}: {len(table)} row(s)")
        for row in table[:SAMPLE_ROWS]:
            lines.append(f"  {row}")
    return "\n".join(lines)
