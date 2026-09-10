"""What the logical planner writes, and what it does with a plan validation turns down."""

from __future__ import annotations

from typing import Any

from blue_data_planner.logical_planner import LogicalPlanner
from blue_data_planner.types import (
    CatalogPreview,
    ControlOperator,
    LogicalOperator,
    RunStatus,
    SourceRecord,
)
from tests.doubles.llm import ScriptedLLMClient


class FakeCatalog:
    """A catalog holding one registered source and nothing below it."""

    def preview(self, request: str, limit: int) -> CatalogPreview:
        return CatalogPreview(
            sources=[
                SourceRecord(
                    path="/source/jobs",
                    name="jobs",
                    kind="sql/sqlite",
                    description="job postings and companies",
                )
            ],
            total_sources=1,
        )


def a_complete_plan() -> dict[str, Any]:
    """One scan and an ending over it, which is what world knowledge alone can produce."""
    return {
        "output_node_id": "n2",
        "nodes": [
            {
                "id": "n1",
                "kind": "SCAN",
                "input_node_ids": [],
                "instruction": "read the postings",
                "required_output_columns": [{"name": "title", "dtype": "str"}],
            },
            {"id": "n2", "kind": "RETURN", "input_node_ids": ["n1"], "columns": ["title"]},
        ],
    }


def a_plan_ending_in_discovery() -> dict[str, Any]:
    """A first pass over registered data: one DISCOVER node per thing to find."""
    return {
        "output_node_id": None,
        "nodes": [
            {
                "id": "n1",
                "kind": "DISCOVER",
                "input_node_ids": [],
                "instruction": "find a source holding job postings",
            },
            {
                "id": "n2",
                "kind": "DISCOVER",
                "input_node_ids": [],
                "instruction": "find a source holding the Fortune 500 list",
            },
        ],
    }


def a_continuation_plan() -> dict[str, Any]:
    """A scan and an ending, written with ids that do not collide with a carried node."""
    return {
        "output_node_id": "n3",
        "nodes": [
            {
                "id": "n2",
                "kind": "SCAN",
                "input_node_ids": [],
                "instruction": "read the postings",
                "required_output_columns": [{"name": "title", "dtype": "str"}],
            },
            {"id": "n3", "kind": "RETURN", "input_node_ids": ["n2"], "columns": ["title"]},
        ],
    }


def an_invalid_plan() -> dict[str, Any]:
    """A plan whose ending projects a column its input does not declare."""
    plan = a_complete_plan()
    plan["nodes"][1]["columns"] = ["posting_count"]
    return plan


def a_planner(answers: list[Any], corrections: int = 3) -> LogicalPlanner:
    """A planner whose model answers with these plans in order."""
    # ScriptedLLMClient and FakeCatalog stand in for LLMClient and DataCatalog
    # structurally; pyright's strict nominal check does not see them as related.
    return LogicalPlanner(
        ScriptedLLMClient(answers),  # pyright: ignore[reportArgumentType]
        FakeCatalog(),  # pyright: ignore[reportArgumentType]
        corrections,
    )


def test_a_request_world_knowledge_answers_comes_back_as_a_complete_plan() -> None:
    """A first plan carrying no control node is one the planner could write in full."""
    result = a_planner([a_complete_plan()]).generate("list the job titles")
    assert result.status is RunStatus.OK
    assert result.plan is not None
    assert result.plan.output_node_id == "n2"


def test_a_request_over_registered_data_comes_back_as_one_discover_node_per_thing_to_find() -> None:
    """The preview names sources and nothing below them, so the first pass only asks."""
    result = a_planner([a_plan_ending_in_discovery()]).generate("Python jobs at Fortune 500")
    assert result.plan is not None
    assert [node.kind for node in result.plan.nodes] == [
        ControlOperator.DISCOVER,
        ControlOperator.DISCOVER,
    ]


def test_a_plan_validation_rejects_is_written_again_with_the_error_in_the_conversation() -> None:
    """The rejected plan and its error both stay in the session the correction runs in."""
    client = ScriptedLLMClient([an_invalid_plan(), a_complete_plan()])
    planner = LogicalPlanner(
        client,  # pyright: ignore[reportArgumentType]
        FakeCatalog(),  # pyright: ignore[reportArgumentType]
        3,
    )
    result = planner.generate("list the job titles")
    assert result.status is RunStatus.OK
    assert len(result.rejected) == 1
    assert "posting_count" in client.conversations[1][-1][1]


def test_running_out_of_corrections_returns_invalid_plan_with_every_rejected_plan() -> None:
    """A run that ends in invalid_plan shows what was attempted."""
    result = a_planner([an_invalid_plan(), an_invalid_plan()], corrections=1).generate("list them")
    assert result.status is RunStatus.INVALID_PLAN
    assert result.plan is None
    assert len(result.rejected) == 2


def test_a_continuation_carries_the_nodes_the_run_has_something_for() -> None:
    """The new version keeps what already ran and covers the rest with what came back."""
    first = a_planner([a_plan_ending_in_discovery()]).generate("Python jobs at Fortune 500")
    assert first.plan is not None
    continuation = a_planner([a_continuation_plan()]).generate(
        "Python jobs at Fortune 500",
        parent=first.plan,
        carried_node_ids=frozenset({"n1"}),
        results={},
        reason="the searches returned",
    )
    assert continuation.plan is not None
    assert continuation.plan.parent_id == first.plan.id
    nodes_by_id = {node.id: node for node in continuation.plan.nodes}
    assert set(nodes_by_id) == {"n1", "n2", "n3"}
    assert nodes_by_id["n1"].kind is ControlOperator.DISCOVER


def test_a_freshly_written_node_replaces_a_carried_node_sharing_its_id() -> None:
    """A node the model writes again takes precedence over the carried node of the same id."""
    first = a_planner([a_plan_ending_in_discovery()]).generate("Python jobs at Fortune 500")
    assert first.plan is not None
    continuation = a_planner([a_complete_plan()]).generate(
        "Python jobs at Fortune 500",
        parent=first.plan,
        carried_node_ids=frozenset({"n1"}),
        results={},
        reason="the searches returned",
    )
    assert continuation.plan is not None
    nodes_by_id = {node.id: node for node in continuation.plan.nodes}
    assert set(nodes_by_id) == {"n1", "n2"}
    assert nodes_by_id["n1"].kind is LogicalOperator.SCAN
