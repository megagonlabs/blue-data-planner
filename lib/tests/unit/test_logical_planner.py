"""What the logical planner writes, and what it does with a plan validation turns down."""

from __future__ import annotations

from typing import Any

from blue_data_planner.logical_planner import LogicalPlanner
from blue_data_planner.types import (
    CatalogPreview,
    ControlOperator,
    Conversation,
    ExecutionResult,
    ExecutionStatus,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PipelineNode,
    RunCounters,
    RunLimits,
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


def a_plan_ending_in_a_replan() -> dict[str, Any]:
    """A pass writing one step and stopping, so the rest is written against the rows it holds."""
    return {
        "output_node_id": None,
        "nodes": [
            {
                "id": "n1",
                "kind": "SCAN",
                "input_node_ids": [],
                "instruction": "read the users",
                "required_output_columns": [{"name": "DisplayName", "dtype": "str"}],
            },
            {
                "id": "n2",
                "kind": "REPLAN",
                "instruction": "Stop until the display names are known.",
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


def a_continuation_rewriting_n1() -> dict[str, Any]:
    """A written plan that names n1 again, instead of naming it only to read it."""
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


def an_invalid_plan() -> dict[str, Any]:
    """A plan whose ending projects a column its input does not declare."""
    plan = a_complete_plan()
    plan["nodes"][1]["columns"] = ["posting_count"]
    return plan


def a_parent_plan() -> LogicalPlan:
    """A one-node plan holding n1, standing in for a version a prior pass already wrote."""
    return LogicalPlan(
        id="p0",
        parent_id=None,
        output_node_id=None,
        nodes=[
            PipelineNode(
                id="n1",
                kind=ControlOperator.DISCOVER,
                input_node_ids=[],
                attributes={"instruction": "find a source holding job postings"},
            )
        ],
    )


def a_parent_plan_holding_a_scan() -> LogicalPlan:
    """A one-node plan holding n1 SCAN, standing in for a version whose scan already ran."""
    return LogicalPlan(
        id="p0",
        parent_id=None,
        output_node_id=None,
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "read the users"},
                required_output_columns=[OutputColumn(name="DisplayName", dtype="str")],
            )
        ],
    )


def five_rows_from_n1() -> dict[str, Any]:
    """What the run holds for n1: five user rows, of which the prompt shows the first few."""
    return {
        "n1": ExecutionResult(
            status=ExecutionStatus.OK,
            output=[
                [
                    {"DisplayName": "whuber", "Views": 20932},
                    {"DisplayName": "gung", "Views": 18022},
                    {"DisplayName": "chl", "Views": 9421},
                    {"DisplayName": "Glen_b", "Views": 8812},
                    {"DisplayName": "Tim", "Views": 7104},
                ]
            ],
            operator_id="builtin.scan",
            row_count=5,
        )
    }


def a_planner_over(client: ScriptedLLMClient) -> LogicalPlanner:
    """A planner whose model answers with what this client is scripted to say."""
    # ScriptedLLMClient and FakeCatalog stand in for LLMClient and DataCatalog
    # structurally; pyright's strict nominal check does not see them as related.
    return LogicalPlanner(
        client,  # pyright: ignore[reportArgumentType]
        FakeCatalog(),  # pyright: ignore[reportArgumentType]
    )


def counters(corrections: int = 3) -> RunCounters:
    """Counters bounding one generate() call, standing in for the constructor argument."""
    return RunCounters(RunLimits(plan_corrections=corrections))


def test_a_request_world_knowledge_answers_comes_back_as_a_complete_plan() -> None:
    """A first plan carrying no control node is one the planner could write in full."""
    result = a_planner_over(ScriptedLLMClient([a_complete_plan()])).generate(
        "list the job titles", Conversation(), counters()
    )
    assert result.status is RunStatus.OK
    assert result.plan is not None
    assert result.plan.output_node_id == "n2"


def test_a_request_over_registered_data_comes_back_as_one_discover_node_per_thing_to_find() -> None:
    """The preview names sources and nothing under them, so the first pass only asks."""
    result = a_planner_over(ScriptedLLMClient([a_plan_ending_in_discovery()])).generate(
        "Python jobs at Fortune 500", Conversation(), counters()
    )
    assert result.plan is not None
    assert [node.kind for node in result.plan.nodes] == [
        ControlOperator.DISCOVER,
        ControlOperator.DISCOVER,
    ]


def test_a_pass_stopping_at_a_replan_writes_the_step_the_replan_waits_for() -> None:
    """A REPLAN ends the pass over the rows this pass produced, and lists no node of its own."""
    result = a_planner_over(ScriptedLLMClient([a_plan_ending_in_a_replan()])).generate(
        "who has the most views", Conversation(), counters()
    )

    assert result.status is RunStatus.OK
    assert result.plan is not None
    assert result.plan.node("n2").kind is ControlOperator.REPLAN
    assert result.plan.node("n2").input_node_ids == []


def test_a_continuation_carries_the_nodes_the_run_has_something_for() -> None:
    """The new version keeps what already ran and covers the rest with what came back."""
    conversation = Conversation()
    first = a_planner_over(ScriptedLLMClient([a_plan_ending_in_discovery()])).generate(
        "Python jobs at Fortune 500", conversation, counters()
    )
    assert first.plan is not None
    continuation = a_planner_over(ScriptedLLMClient([a_continuation_plan()])).generate(
        "Python jobs at Fortune 500",
        conversation,
        counters(),
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


def test_a_carried_node_is_shown_with_its_columns_and_the_first_few_of_its_rows() -> None:
    """An ending naming the column holding the answer is written against the rows themselves."""
    conversation = Conversation()
    a_planner_over(ScriptedLLMClient([a_continuation_plan()])).generate(
        "who has the most views",
        conversation,
        counters(),
        parent=a_parent_plan_holding_a_scan(),
        carried_node_ids=frozenset({"n1"}),
        results=five_rows_from_n1(),
        reason="the ending named a column the rows do not carry",
    )

    asked = [turn for turn in conversation.turns if turn.role == "user"][-1].content
    assert "- n1 (SCAN): 5 row(s), ['DisplayName', 'Views']" in asked
    assert "{'DisplayName': 'whuber', 'Views': 20932}" in asked
    assert "{'DisplayName': 'chl', 'Views': 9421}" in asked
    assert "(+2 more row(s))" in asked


def test_a_plan_validation_turned_down_is_written_again_with_the_error_in_the_conversation() -> (
    None
):
    """A model answers what it wrote, so the objection is a turn rather than a new prompt."""
    client = ScriptedLLMClient([an_invalid_plan(), a_complete_plan()])
    conversation = Conversation()

    written = a_planner_over(client).generate("a request", conversation, RunCounters(RunLimits()))

    assert written.status is RunStatus.OK
    assert [turn.role for turn in conversation.turns] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_running_out_of_corrections_ends_as_invalid_plan_carrying_the_last_objection() -> None:
    """Every plan that was turned down stays in the conversation; the result carries the reason."""
    client = ScriptedLLMClient([an_invalid_plan()] * 3)
    conversation = Conversation()

    written = a_planner_over(client).generate(
        "a request", conversation, RunCounters(RunLimits(plan_corrections=2))
    )

    assert written.status is RunStatus.INVALID_PLAN
    assert written.plan is None
    assert written.failure_reason is not None
    assert sum(1 for turn in conversation.turns if turn.role == "assistant") == 3


def test_a_continuation_is_a_turn_on_top_of_the_plans_already_written() -> None:
    """One conversation serves a whole request, so a second pass is not a fresh exchange."""
    client = ScriptedLLMClient([a_plan_ending_in_discovery(), a_continuation_plan()])
    planner = a_planner_over(client)
    conversation = Conversation()
    counters = RunCounters(RunLimits())

    first = planner.generate("a request", conversation, counters)
    assert first.plan is not None
    planner.generate(
        "a request",
        conversation,
        counters,
        parent=first.plan,
        carried_node_ids=frozenset({"n1"}),
        results={},
        reason="the discovery found two tables",
    )

    assert [turn.role for turn in conversation.turns] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert "the discovery found two tables" in conversation.turns[3].content
    # The request and catalog preview were already sent on turn 1; a continuation must not
    # resend them, or the prefix the model already cached stops matching (ADR-025).
    assert "a request" not in conversation.turns[3].content


def test_the_opening_instructions_carry_nothing_that_varies_by_request() -> None:
    """An identical opening is what makes the prefix cache across runs and across passes."""
    first, second = Conversation(), Conversation()
    a_planner_over(ScriptedLLMClient([a_complete_plan()])).generate(
        "how many stores are in Reno", first, RunCounters(RunLimits())
    )
    a_planner_over(ScriptedLLMClient([a_complete_plan()])).generate(
        "which cities have stores", second, RunCounters(RunLimits())
    )

    assert first.turns[0].content == second.turns[0].content
    assert "how many stores are in Reno" in first.turns[1].content


def test_a_continuation_writing_a_carried_nodes_id_is_turned_down() -> None:
    """A carried node's name must not end up attached to work nobody planned."""
    client = ScriptedLLMClient([a_continuation_rewriting_n1()] * 3)
    conversation = Conversation()

    written = a_planner_over(client).generate(
        "a request",
        conversation,
        RunCounters(RunLimits(plan_corrections=2)),
        parent=a_parent_plan(),
        carried_node_ids=frozenset({"n1"}),
    )

    assert written.status is RunStatus.INVALID_PLAN
    assert "n1" in (written.failure_reason or "")
