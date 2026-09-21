"""Resolving a DISCOVER node: an instruction becomes search terms, and terms become entities."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from blue_data_planner.data import DataCatalog
from blue_data_planner.llm import Conversation, LLMClient, Turn
from blue_data_planner.llm.templating import safe_substitute
from blue_data_planner.prompts import prompt_text
from blue_data_planner.types import (
    DataBundle,
    DiscoveryResult,
    EntityMatch,
    PipelineNode,
)

SAMPLE_ROWS = 5

# Reaching this drops the extra terms and changes nothing else about how a request proceeds, so it
# shapes one component's work rather than bounding a run, like CATALOG_PREVIEW_LIMIT.
MAX_SEARCH_TERMS = 8


class SearchTerms(BaseModel):
    """The terms one discovery node expands into."""

    model_config = ConfigDict(extra="forbid")

    terms: list[str]


class CatalogSearcher:
    """Turns a DISCOVER node into the entities its search terms found."""

    def __init__(
        self,
        llm_client: LLMClient,
        catalog: DataCatalog,
        max_search_terms: int = MAX_SEARCH_TERMS,
    ) -> None:
        self._llm_client = llm_client
        self._catalog = catalog
        self._max_search_terms = max_search_terms

    def search(
        self, node: PipelineNode, conversation: Conversation, inputs: DataBundle
    ) -> DiscoveryResult:
        """Write search terms from the node's instruction and search the registry for each."""
        if not conversation.turns:
            conversation.turns.append(
                Turn(
                    role="system",
                    content=safe_substitute(
                        prompt_text("catalog_searcher.md"),
                        max_search_terms=self._max_search_terms,
                    ),
                )
            )
        conversation.turns.append(Turn(role="user", content=self._ask(node, inputs)))
        written = self._llm_client.complete_structured(conversation, SearchTerms)
        kept = written.terms[: self._max_search_terms]
        matches: dict[str, EntityMatch] = {}
        for term in kept:
            for match in self._catalog.search_records(term):
                held = matches.get(match.path)
                if held is None or match.score > held.score:
                    matches[match.path] = match
        return DiscoveryResult(
            search_terms=kept,
            matches=sorted(matches.values(), key=lambda match: -match.score),
            dropped_terms=len(written.terms) - len(kept),
        )

    def _ask(self, node: PipelineNode, inputs: DataBundle) -> str:
        return safe_substitute(
            prompt_text("catalog_searcher_turn.md"),
            instruction=node.instruction,
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
