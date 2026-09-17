"""How a plan is cut into fragments, and what a repair changes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from blue_data_planner.operators import OperatorRouter
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.types import (
    AttributeRecord,
    DiscoveryResult,
    EntityMatch,
    ExecutionResult,
    ExecutionStatus,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    PhysicalOperator,
    PipelineNode,
)
from tests.doubles.llm import ScriptedLLMClient

POSTINGS = "/source/jobs/database/jobs/collection/public/entity/postings"


class NoParameters(BaseModel):
    """A parameter schema for an operator these tests never call."""


def an_operator(
    operator_id: str,
    kinds: frozenset[LogicalOperator],
    source_kinds: frozenset[str] | None = frozenset({"sql/sqlite"}),
) -> PhysicalOperator:
    """One registered operator declaring the kinds given and the source kinds it reads."""
    return PhysicalOperator(
        operator_id=operator_id,
        logical_operators=kinds,
        description=f"{operator_id} does its one job.",
        parameter_schema=NoParameters,
        call=lambda inputs, parameters, columns: None,  # pyright: ignore[reportArgumentType]
        source_kinds=source_kinds,
    )


ALL_KINDS = frozenset(kind for kind in LogicalOperator if kind is not LogicalOperator.RETURN)


def a_router(*operators: PhysicalOperator) -> OperatorRouter:
    """A router holding these operators, in the order given."""
    router = OperatorRouter()
    for operator in operators:
        router.register(operator)
    return router


def a_plan() -> LogicalPlan:
    """n1 SCAN -> n2 FILTER -> n3 AGGREGATE -> n4 RETURN."""

    def node(node_id: str, kind: LogicalOperator, inputs: list[str]) -> PipelineNode:
        return PipelineNode(
            id=node_id,
            kind=kind,
            input_node_ids=inputs,
            attributes={"instruction": "do it"},
            required_output_columns=[OutputColumn(name="title", dtype="str")],
        )

    return LogicalPlan(
        id="p1",
        parent_id=None,
        output_node_id="n4",
        nodes=[
            node("n1", LogicalOperator.SCAN, []),
            node("n2", LogicalOperator.FILTER, ["n1"]),
            node("n3", LogicalOperator.AGGREGATE, ["n2"]),
            PipelineNode(
                id="n4",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n3"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_scan_and_return() -> LogicalPlan:
    """n1 SCAN -> n2 RETURN, the smallest plan one operator can cover on its own."""
    return LogicalPlan(
        id="p2",
        parent_id=None,
        output_node_id="n2",
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "name the planets"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n1"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_discovery() -> dict[str, Any]:
    """A results map holding one resolved DISCOVER node naming the postings entity."""
    return {
        "d1": DiscoveryResult(
            search_terms=["job postings"],
            matches=[
                EntityMatch(
                    path=POSTINGS,
                    name="postings",
                    description="job postings",
                    source_kind="sql/sqlite",
                    attributes=[AttributeRecord(name="title", dtype="str", description=None)],
                    score=0.9,
                    search_term="job postings",
                )
            ],
            dropped_terms=0,
        )
    }


def a_discovery_holding_n1() -> dict[str, Any]:
    """A discovery, plus a result already sitting under n1, which is what offers reuse."""
    return {
        **a_discovery(),
        "n1": ExecutionResult(
            status=ExecutionStatus.OK,
            output=[[{"title": "a planet"}]],
            operator_id="op.one",
            row_count=1,
        ),
    }


def a_binding(label: str, inputs: tuple[str, ...] = (), **extra: Any) -> dict[str, Any]:
    """One binding decision's payload, naming each node on the chosen candidate's frontier."""
    payload: dict[str, Any] = {
        "candidate": label,
        "instruction": "do the whole thing",
        "required_output_columns": None,
    }
    for node_id in inputs:
        payload[f"input_name_{node_id}"] = f"the {node_id} table"
        payload[f"input_description_{node_id}"] = f"the rows {node_id} produced"
    payload.update(extra)
    return {"choice": payload}


def a_planner(client: ScriptedLLMClient, router: OperatorRouter) -> PhysicalPlanner:
    """A planner over this router, whose model answers come from this scripted client."""
    # ScriptedLLMClient stands in for LLMClient structurally; pyright's strict nominal
    # check does not see the two as related.
    return PhysicalPlanner(client, router)  # pyright: ignore[reportArgumentType]


def test_a_plan_one_operator_can_absorb_comes_back_as_one_fragment() -> None:
    """The widest candidate is offered first, and taking it ends the cover in one turn."""
    client = ScriptedLLMClient(
        [a_binding("n3:builtin.nl2table:[n1,n2,n3]", scan_source_n1=POSTINGS)]
    )
    planner = a_planner(client, a_router(an_operator("builtin.nl2table", ALL_KINDS)))
    result = planner.cover(a_plan(), None, a_discovery())
    assert result.plan is not None
    assert [sorted(f.logical_node_ids) for f in result.plan.fragments] == [["n1", "n2", "n3"]]


def test_a_node_no_operator_can_run_ends_the_cover_with_the_reason_naming_it() -> None:
    """No operator call is spent; the run goes straight back to the logical planner."""
    planner = a_planner(
        ScriptedLLMClient([]),
        a_router(an_operator("builtin.filter", frozenset({LogicalOperator.FILTER}))),
    )
    result = planner.cover(a_plan(), None, a_discovery())
    assert result.plan is None
    assert result.failure_reason is not None
    assert "n3" in result.failure_reason


def test_a_cover_taking_one_node_at_a_time_sends_one_turn_per_node() -> None:
    """Three atomic bindings are three turns of one conversation and three fragments."""
    client = ScriptedLLMClient(
        [
            a_binding("n3:builtin.aggregate:[n3]", inputs=("n2",)),
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]", scan_source_n1=POSTINGS),
        ]
    )
    planner = a_planner(
        client,
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
            an_operator("builtin.aggregate", frozenset({LogicalOperator.AGGREGATE})),
        ),
    )
    result = planner.cover(a_plan(), None, a_discovery())
    assert result.plan is not None
    assert sorted(sorted(f.logical_node_ids) for f in result.plan.fragments) == [
        ["n1"],
        ["n2"],
        ["n3"],
    ]


def test_a_fragment_repair_rebinds_one_node_set_and_leaves_the_rest_of_the_cover_intact() -> None:
    """Only the failed set is cut again, so every other fragment carries across untouched."""
    atomics = (
        an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
        an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
        an_operator("builtin.aggregate", frozenset({LogicalOperator.AGGREGATE})),
    )
    first = a_planner(
        ScriptedLLMClient(
            [
                a_binding("n3:builtin.aggregate:[n3]", inputs=("n2",)),
                a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
                a_binding("n1:builtin.scan:[n1]", scan_source_n1=POSTINGS),
            ]
        ),
        a_router(*atomics),
    ).cover(a_plan(), None, a_discovery())
    assert first.plan is not None
    failed = frozenset({"n3"})

    # The router now offers a second operator for AGGREGATE, so the repair has somewhere to go.
    repaired = a_planner(
        ScriptedLLMClient([a_binding("n3:builtin.nl2table:[n3]", inputs=("n2",))]),
        a_router(*atomics, an_operator("builtin.nl2table", ALL_KINDS)),
    ).recover_fragment(
        a_plan(), first.plan, failed, a_discovery(), "the aggregation returned one row too many"
    )

    assert repaired.plan is not None
    assert sorted(sorted(f.logical_node_ids) for f in repaired.plan.fragments) == [
        ["n1"],
        ["n2"],
        ["n3"],
    ]
    rebound = next(f for f in repaired.plan.fragments if f.logical_node_ids == ["n3"])
    assert rebound.operator_id != "builtin.aggregate"


def test_a_repair_starts_a_fresh_conversation() -> None:
    """A superseded cut should not sit in front of the model as though it still applied."""
    # The widest candidate over this plan and router absorbs the SCAN node too, so, as in
    # test_a_plan_one_operator_can_absorb_comes_back_as_one_fragment above, each binding must
    # name a scan source or the scripted answer fails the response schema before the code
    # under test runs at all.
    #
    # One planner and one client are reused across both calls, so the second conversation is
    # genuinely a second use of the same client: if a future change accidentally carried the
    # first call's turns into the second, conversations[1] would come back longer than 2 and
    # this test would fail. Two separate clients, as in an earlier version of this test, could
    # never observe that: each would start empty regardless of what the implementation did.
    client = ScriptedLLMClient(
        [
            a_binding("n3:builtin.nl2table:[n1,n2,n3]", scan_source_n1=POSTINGS),
            a_binding("n3:builtin.nl2table:[n1,n2,n3]", scan_source_n1=POSTINGS),
        ]
    )
    planner = a_planner(client, a_router(an_operator("builtin.nl2table", ALL_KINDS)))
    first = planner.cover(a_plan(), None, a_discovery())
    assert first.plan is not None
    planner.cover(a_plan(), None, a_discovery(), reason="the fused call ignored the filter")
    assert len(client.conversations[1]) == 2


def test_a_scan_an_operator_answers_from_its_own_knowledge_is_bound_to_no_source() -> None:
    """An operator declaring no source kinds needs no discovery, and names no entity path."""
    planner = a_planner(
        ScriptedLLMClient([a_binding("n1:builtin.nl2table:[n1]")]),
        a_router(an_operator("builtin.nl2table", ALL_KINDS, source_kinds=None)),
    )
    result = planner.cover(a_scan_and_return(), None, {})
    assert result.plan is not None
    assert result.plan.fragments[0].scan_sources == {}


def test_a_scan_no_discovered_source_matches_leaves_its_node_with_no_candidate() -> None:
    """The reason names both kinds, so the logical planner can search for a different one."""
    planner = a_planner(
        ScriptedLLMClient([]),
        a_router(an_operator("builtin.scan", ALL_KINDS, source_kinds=frozenset({"vector/chroma"}))),
    )
    result = planner.cover(a_scan_and_return(), None, a_discovery())
    assert result.plan is None
    assert result.failure_reason is not None
    assert "n1" in result.failure_reason
    assert "vector/chroma" in result.failure_reason
    assert "sql/sqlite" in result.failure_reason


def test_a_fragments_inputs_are_the_nodes_feeding_it_and_not_a_model_choice() -> None:
    """The plan decides which nodes a fragment reads, and the model only names them."""
    planner = a_planner(
        ScriptedLLMClient(
            [
                a_binding("n3:builtin.aggregate:[n3]", inputs=("n2",)),
                a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
                a_binding("n1:builtin.scan:[n1]", scan_source_n1=POSTINGS),
            ]
        ),
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
            an_operator("builtin.aggregate", frozenset({LogicalOperator.AGGREGATE})),
        ),
    )
    result = planner.cover(a_plan(), None, a_discovery())
    assert result.plan is not None
    reads = {
        fragment.logical_node_ids[0]: [entry.logical_node_id for entry in fragment.input_data]
        for fragment in result.plan.fragments
    }
    assert reads == {"n1": [], "n2": ["n1"], "n3": ["n2"]}


def test_a_node_whose_fragment_produced_a_result_can_reuse_it_unchanged() -> None:
    """Reuse turns on the decision, so a reworded instruction cannot throw a good result away."""
    client = ScriptedLLMClient(
        [
            a_binding("n1:op.one:[n1]", scan_source_n1=POSTINGS),
            {"choice": {"candidate": "n1:reuse:[n1]"}},
        ]
    )
    planner = a_planner(client, a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN}))))
    plan = a_scan_and_return()

    first = planner.cover(plan, None, a_discovery())
    assert first.plan is not None
    again = planner.cover(plan, first.plan, a_discovery_holding_n1(), "try again")

    assert again.plan is not None
    assert again.plan.fragments[0] == first.plan.fragments[0]


def test_a_node_with_no_result_is_offered_no_way_to_reuse_its_fragment() -> None:
    """The failed fragment has no result, so the model must pick again and reword its work."""
    client = ScriptedLLMClient(
        [
            a_binding("n1:op.one:[n1]", scan_source_n1=POSTINGS),
            a_binding("n1:op.one:[n1]", scan_source_n1=POSTINGS),
        ]
    )
    planner = a_planner(client, a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN}))))
    plan = a_scan_and_return()

    first = planner.cover(plan, None, a_discovery())
    assert first.plan is not None
    again = planner.cover(plan, first.plan, a_discovery(), "it returned nothing")

    assert again.plan is not None
    assert again.plan.fragments[0].id != first.plan.fragments[0].id
