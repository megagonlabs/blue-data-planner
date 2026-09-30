"""How a plan is cut into fragments, and what a repair changes."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from pydantic import BaseModel

from blue_data_planner.operators import OperatorRouter
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.types import (
    AttributeView,
    BlueDataPlannerError,
    ControlOperator,
    Conversation,
    DiscoveryResult,
    EntityMatch,
    ErrorInfo,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    FailedFragment,
    FragmentResult,
    LogicalOperator,
    LogicalPlan,
    OutputColumn,
    ParameterAttempt,
    PhysicalOperator,
    PhysicalPlanFragment,
    PipelineNode,
    RunStatus,
)
from tests.doubles.catalog import an_attribute_view
from tests.doubles.llm import ScriptedLLMClient

POSTINGS = "/source/jobs/database/jobs/collection/public/entity/postings"
REVIEWS = "/source/reviews/database/reviews/collection/default/entity/reviews"
REQUEST = "list the job titles"


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


def a_plan(source: str | None = POSTINGS) -> LogicalPlan:
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
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "do it", "source": source},
                required_output_columns=[],
            ),
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


def a_scan_and_return(source: str | None = POSTINGS) -> LogicalPlan:
    """n1 SCAN -> n2 PROJECT -> n3 RETURN, the smallest valid plan holding a scan."""
    return LogicalPlan(
        id="p2",
        parent_id=None,
        output_node_id="n3",
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "name the planets", "source": source},
                required_output_columns=[],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.PROJECT,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the title column"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n2"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_scan_a_filter_and_return(source: str | None = POSTINGS) -> LogicalPlan:
    """n1 SCAN -> n2 FILTER -> n3 RETURN, so one node can finish while another still needs one."""
    return LogicalPlan(
        id="p3",
        parent_id=None,
        output_node_id="n3",
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "name the planets", "source": source},
                required_output_columns=[],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the rocky ones"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n2"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_fan_out_scan() -> LogicalPlan:
    """n1 SCAN read by both n2 FILTER and n3 FILTER, joined at n4, ended at n5."""
    return LogicalPlan(
        id="p6",
        parent_id=None,
        output_node_id="n5",
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "read the postings", "source": POSTINGS},
                required_output_columns=[],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the open postings"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the closed postings"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n4",
                kind=LogicalOperator.JOIN,
                input_node_ids=["n2", "n3"],
                attributes={"instruction": "join them on title"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n5",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n4"],
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
                    attributes=[an_attribute_view("title")],
                    score=0.9,
                    search_term="job postings",
                    row_count=None,
                )
            ],
            dropped_terms=0,
        )
    }


def a_join_of_two_scans(left_source: str | None, right_source: str | None) -> LogicalPlan:
    """n1 SCAN and n2 SCAN -> n3 JOIN -> n4 RETURN, each scan naming the source given."""

    def scan(node_id: str, source: str | None) -> PipelineNode:
        return PipelineNode(
            id=node_id,
            kind=LogicalOperator.SCAN,
            input_node_ids=[],
            attributes={"instruction": "read it", "source": source},
            required_output_columns=[],
        )

    return LogicalPlan(
        id="p4",
        parent_id=None,
        output_node_id="n4",
        nodes=[
            scan("n1", left_source),
            scan("n2", right_source),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.JOIN,
                input_node_ids=["n1", "n2"],
                attributes={"instruction": "join them on title"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n4",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n3"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def a_discovery_of_two_source_kinds() -> dict[str, Any]:
    """The postings entity in SQLite, plus a reviews entity in a vector store."""
    reviews = EntityMatch(
        path=REVIEWS,
        name="reviews",
        description="company reviews",
        source_kind="vector/chroma",
        attributes=[an_attribute_view("title")],
        score=0.8,
        search_term="reviews",
        row_count=None,
    )
    discovery = a_discovery()["d1"]
    return {
        "d1": DiscoveryResult(
            search_terms=["job postings", "reviews"],
            matches=[*discovery.matches, reviews],
            dropped_terms=0,
        )
    }


def a_discovery_of_a_sized_and_an_unsized_entity() -> dict[str, Any]:
    """The postings entity with a recorded row count, plus a reviews entity with none."""
    return {
        "d1": DiscoveryResult(
            search_terms=["job postings", "reviews"],
            matches=[
                EntityMatch(
                    path=POSTINGS,
                    name="postings",
                    description="job postings",
                    source_kind="sql/sqlite",
                    attributes=[an_attribute_view("title"), a_remote_flag_view()],
                    score=0.9,
                    search_term="job postings",
                    row_count=11060,
                ),
                EntityMatch(
                    path=REVIEWS,
                    name="reviews",
                    description="company reviews",
                    source_kind="vector/chroma",
                    attributes=[an_attribute_view("title")],
                    score=0.8,
                    search_term="reviews",
                    row_count=None,
                ),
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


def a_discovery_holding_n2() -> dict[str, Any]:
    """A discovery, plus a result already sitting under n2, which is what offers reuse."""
    return {
        **a_discovery(),
        "n2": ExecutionResult(
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
        "required_output_columns": [{"name": "title", "dtype": "str"}],
    }
    for node_id in inputs:
        payload[f"input_name_{node_id}"] = f"the {node_id} table"
        payload[f"input_description_{node_id}"] = f"the rows {node_id} produced"
    payload.update(extra)
    return {"choice": payload}


def a_none_choice(reason: str) -> dict[str, Any]:
    """A binding decision that turns every candidate offered down."""
    return {"choice": {"candidate": "none", "reason": reason}}


def a_failed_fragment(fragment: PhysicalPlanFragment) -> FailedFragment:
    """The fragment that failed three ways: a call error, two wrong rows, then a repeat."""
    return a_failed_fragment_with(
        fragment,
        [
            ParameterAttempt(
                parameters={"predicate": "moon > 0"},
                result=ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    operator_id="op.one",
                    error=ErrorInfo(kind=ErrorKind.RECOVERABLE, message="no column named moon"),
                ),
                assessment=None,
            ),
            an_attempt_that_returned([{"title": "Mercury"}, {"title": "Venus"}]),
            ParameterAttempt(parameters={"predicate": "rocky"}, result=None, assessment=None),
        ],
    )


def an_attempt_that_returned(rows: list[dict[str, Any]]) -> ParameterAttempt:
    """One attempt whose call returned these rows, judged not to do what was asked."""
    return ParameterAttempt(
        parameters={"predicate": "rocky"},
        result=ExecutionResult(
            status=ExecutionStatus.OK, output=[rows], operator_id="op.one", row_count=len(rows)
        ),
        assessment="it kept the gas giants",
    )


def a_failed_fragment_with(
    fragment: PhysicalPlanFragment, attempts: list[ParameterAttempt]
) -> FailedFragment:
    """The fragment ending needs_repair after these attempts."""
    return FailedFragment(
        fragment=fragment,
        result=FragmentResult(
            status=RunStatus.NEEDS_REPAIR,
            attempts=attempts,
            failure_reason="Steps n1, n2 failed in all attempts",
        ),
    )


def a_planner(client: ScriptedLLMClient, router: OperatorRouter) -> PhysicalPlanner:
    """A planner over this router, whose model answers come from this scripted client."""
    # ScriptedLLMClient stands in for LLMClient structurally; pyright's strict nominal check
    # does not see the two as related.
    return PhysicalPlanner(client, router)  # pyright: ignore[reportArgumentType]


def a_new_version_of(plan: LogicalPlan) -> LogicalPlan:
    """A continuation of the plan holding the same nodes, as a rewritten ending would."""
    return dataclasses.replace(plan, id=f"{plan.id}-next", parent_id=plan.id)


def test_a_plan_one_operator_can_absorb_comes_back_as_one_fragment() -> None:
    """The widest candidate is offered first, and taking it ends the cover in one turn."""
    client = ScriptedLLMClient([a_binding("n3:builtin.nl2table:[n1,n2,n3]")])
    planner = a_planner(client, a_router(an_operator("builtin.nl2table", ALL_KINDS)))
    result = planner.cover(REQUEST, a_plan(), Conversation(), None, a_discovery())
    assert result.plan is not None
    assert [sorted(f.logical_node_ids) for f in result.plan.fragments] == [["n1", "n2", "n3"]]


def test_a_node_no_operator_can_run_ends_the_cover_with_the_reason_naming_it() -> None:
    """No operator call is spent; the run goes straight back to the logical planner."""
    planner = a_planner(
        ScriptedLLMClient([]),
        a_router(an_operator("builtin.filter", frozenset({LogicalOperator.FILTER}))),
    )
    result = planner.cover(REQUEST, a_plan(), Conversation(), None, a_discovery())
    assert result.plan is None
    assert result.failure_reason is not None
    assert "n3" in result.failure_reason


def test_a_binding_that_turns_every_candidate_down_ends_the_cover_with_no_plan() -> None:
    """No fragment is bound and no operator runs; the reason names the node and the model's why."""
    reason = "the plan filters on what a photo shows, and no operator offered reads images"
    client = ScriptedLLMClient([a_none_choice(reason)])
    planner = a_planner(client, a_router(an_operator("builtin.nl2table", ALL_KINDS)))

    result = planner.cover(REQUEST, a_plan(), Conversation(), None, a_discovery())

    assert result.plan is None
    assert result.failure_reason is not None
    assert "n3" in result.failure_reason
    assert reason in result.failure_reason


def test_a_binding_turning_every_candidate_down_after_others_were_bound_returns_no_plan() -> None:
    """The fragments already bound are dropped with the rest, so no partial cover comes back."""
    client = ScriptedLLMClient(
        [
            a_binding("n3:builtin.aggregate:[n3]", inputs=("n2",)),
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_none_choice("no operator offered reads this source"),
        ]
    )

    result = a_planner(client, a_three_step_router()).cover(
        REQUEST, a_plan(), Conversation(), None, a_discovery()
    )

    assert result.plan is None
    assert result.failure_reason is not None
    assert "n1" in result.failure_reason


def test_a_cover_taking_one_node_at_a_time_sends_one_turn_per_node() -> None:
    """Three atomic bindings are three turns of one conversation and three fragments."""
    client = ScriptedLLMClient(
        [
            a_binding("n3:builtin.aggregate:[n3]", inputs=("n2",)),
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]"),
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
    result = planner.cover(REQUEST, a_plan(), Conversation(), None, a_discovery())
    assert result.plan is not None
    assert sorted(sorted(f.logical_node_ids) for f in result.plan.fragments) == [
        ["n1"],
        ["n2"],
        ["n3"],
    ]


def test_a_scan_an_operator_answers_from_its_own_knowledge_is_bound_to_no_source() -> None:
    """An operator declaring no source kinds needs no discovery, and names no entity path."""
    planner = a_planner(
        ScriptedLLMClient([a_binding("n2:builtin.nl2table:[n1,n2]")]),
        a_router(an_operator("builtin.nl2table", ALL_KINDS, source_kinds=None)),
    )
    result = planner.cover(REQUEST, a_scan_and_return(source=None), Conversation(), None, {})
    assert result.plan is not None
    assert result.plan.fragments[0].scan_sources == {}


def test_a_scan_no_discovered_source_matches_leaves_its_node_with_no_candidate() -> None:
    """The reason names both kinds, so the logical planner can search for a different one."""
    planner = a_planner(
        ScriptedLLMClient([a_binding("n2:builtin.project:[n2]", inputs=("n1",))]),
        a_router(
            an_operator("builtin.project", frozenset({LogicalOperator.PROJECT})),
            an_operator(
                "builtin.scan",
                frozenset({LogicalOperator.SCAN}),
                source_kinds=frozenset({"vector/chroma"}),
            ),
        ),
    )
    result = planner.cover(REQUEST, a_scan_and_return(), Conversation(), None, a_discovery())
    assert result.plan is None
    assert result.failure_reason is not None
    assert "n1" in result.failure_reason
    assert "vector/chroma" in result.failure_reason
    assert "sql/sqlite" in result.failure_reason


def test_a_world_knowledge_scan_finds_no_candidate_when_every_operator_needs_a_source() -> None:
    """An operator declaring source kinds reads registered data, never the model's own knowledge."""
    planner = a_planner(
        ScriptedLLMClient([a_binding("n2:builtin.project:[n2]", inputs=("n1",))]),
        a_router(
            an_operator("builtin.project", frozenset({LogicalOperator.PROJECT})),
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
        ),
    )
    result = planner.cover(REQUEST, a_scan_and_return(source=None), Conversation(), None, {})
    assert result.plan is None
    assert result.failure_reason is not None
    assert "n1" in result.failure_reason
    assert "world knowledge" in result.failure_reason


def test_a_scan_naming_a_source_absent_from_the_results_raises() -> None:
    """Results not matching the plan are a caller's bug, not a cover worth writing."""
    planner = a_planner(
        ScriptedLLMClient([]),
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN}))),
    )
    with pytest.raises(BlueDataPlannerError, match="n1"):
        planner.cover(REQUEST, a_scan_and_return(), Conversation(), None, {})


@pytest.mark.parametrize(
    ("right_source", "right_operator"),
    [
        (None, an_operator("wk.all", ALL_KINDS, source_kinds=None)),
        (REVIEWS, an_operator("vec.all", ALL_KINDS, source_kinds=frozenset({"vector/chroma"}))),
    ],
    ids=["registered_and_world_knowledge", "two_source_kinds"],
)
def test_a_join_over_scans_no_one_operator_reads_is_offered_only_per_scan_cuts(
    right_source: str | None, right_operator: PhysicalOperator
) -> None:
    """No operator reads both scans, so none is offered the fused join, only one side each."""
    left_operator = an_operator("sql.all", ALL_KINDS)
    client = ScriptedLLMClient(
        [
            a_binding("n3:sql.all:[n1,n3]", inputs=("n2",)),
            a_binding(f"n2:{right_operator.operator_id}:[n2]"),
        ]
    )
    planner = a_planner(client, a_router(left_operator, right_operator))

    planner.cover(
        REQUEST,
        a_join_of_two_scans(POSTINGS, right_source),
        Conversation(),
        None,
        a_discovery_of_two_source_kinds(),
    )

    join_turn = client.conversations[0][-1][1]
    assert "[n1,n2,n3]" not in join_turn
    assert "n3:sql.all:[n1,n3]" in join_turn
    assert f"n3:{right_operator.operator_id}:[n2,n3]" in join_turn


def the_plan_nodes(turn: str) -> list[dict[str, Any]]:
    """Parse the JSON array under "## Plan", one node object per line, into a list of dicts."""
    return _json_array_section(turn, "## Plan\n", "\n## ")


def the_readers(turn: str) -> list[dict[str, Any]]:
    """Parse the JSON array under "Read by:", one reader object per line, into a list of dicts."""
    return _json_array_section(turn, "Read by:\n\n", "\n\nCandidates:")


def _json_array_section(turn: str, start: str, end: str) -> list[dict[str, Any]]:
    section = turn.split(start, 1)[1].split(end, 1)[0].strip()
    if section == "[]":
        return []
    lines = section.splitlines()
    assert lines[0] == "["
    assert lines[-1] == "]"
    return [json.loads(line.rstrip(",")) for line in lines[1:-1]]


def test_the_opening_turn_shows_the_request_and_the_plan_and_a_later_turn_repeats_neither() -> None:
    """The request and the whole plan open the conversation once, and are not sent again."""
    client = ScriptedLLMClient(
        [
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]"),
        ]
    )
    planner = a_planner(
        client,
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
        ),
    )

    planner.cover(REQUEST, a_scan_a_filter_and_return(), Conversation(), None, a_discovery())

    opening_turn = client.conversations[0][-1][1]
    assert "## Request" in opening_turn
    assert REQUEST in opening_turn.split("## Plan", 1)[0]
    assert the_plan_nodes(opening_turn) == [
        {
            "id": "n1",
            "kind": "SCAN",
            "input_node_ids": [],
            "instruction": "name the planets",
            "source": POSTINGS,
        },
        {
            "id": "n2",
            "kind": "FILTER",
            "input_node_ids": ["n1"],
            "instruction": "keep the rocky ones",
            "required_output_columns": ["title: str"],
        },
        {"id": "n3", "kind": "RETURN", "input_node_ids": ["n2"], "columns": ["title"]},
    ]
    later_turn = client.conversations[1][-1][1]
    assert "## Request" not in later_turn
    assert "## Plan" not in later_turn


def a_remote_flag_view() -> AttributeView:
    """The postings entity's remote attribute, with frequent values and counts recorded."""
    return an_attribute_view(
        "remote",
        dtype="bool",
        description="whether the role is remote",
        frequent_values=[(True, 7000), (False, 4060)],
        distinct_count=2,
        null_count=0,
        counted_rows=11060,
    )


def test_the_opening_turn_shows_each_scanned_entitys_attribute_views_and_no_other_entity() -> None:
    """A scanned entity's attributes show type, values, and counts; an unscanned one is absent."""
    client = ScriptedLLMClient(
        [a_binding("n2:builtin.filter:[n2]", inputs=("n1",)), a_binding("n1:builtin.scan:[n1]")]
    )
    planner = a_planner(
        client,
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
        ),
    )

    planner.cover(
        REQUEST,
        a_scan_a_filter_and_return(),
        Conversation(),
        None,
        a_discovery_of_a_sized_and_an_unsized_entity(),
    )

    section = client.conversations[0][-1][1].split("## The data the plan scans", 1)[1]
    assert f"- {POSTINGS} (sql/sqlite, 11060 row(s)): job postings" in section
    assert "- postings.remote [bool]: whether the role is remote" in section
    assert "values, most frequent first: True (7000 rows), False (4060 rows)" in section
    assert "of 11060 rows counted: 2 distinct, 0 null" in section
    assert REVIEWS not in section


def a_discovery_holding_five_rows_under_n1() -> dict[str, Any]:
    """A discovery, plus five rows already sitting under n1."""
    rows = [{"title": f"planet {index}"} for index in range(5)]
    return {
        **a_discovery(),
        "n1": ExecutionResult(
            status=ExecutionStatus.OK, output=[rows], operator_id="op.one", row_count=5
        ),
    }


def test_a_held_result_is_shown_with_its_first_rows_and_a_count_of_the_rows_left_out() -> None:
    """Three of five rows are shown, and the two left out are counted."""
    client = ScriptedLLMClient([a_binding("n2:op.one:[n2]", inputs=("n1",))])
    planner = a_planner(
        client, a_router(an_operator("op.one", frozenset({LogicalOperator.PROJECT})))
    )

    planner.cover(
        REQUEST, a_scan_and_return(), Conversation(), None, a_discovery_holding_five_rows_under_n1()
    )

    section = client.conversations[0][-1][1].split("## What has run so far", 1)[1]
    assert "- n1: 5 row(s) from op.one, columns ['title']" in section
    assert "{'title': 'planet 2'}" in section
    assert "{'title': 'planet 3'}" not in section
    assert "(+2 more row(s))" in section


def test_a_repair_turn_shows_every_result_held_now_including_ones_run_after_opening() -> None:
    """A result that did not exist when the conversation opened appears on the repair turn."""
    client = ScriptedLLMClient(
        [
            a_binding("n2:op.one:[n2]", inputs=("n1",)),
            a_binding("n1:op.one:[n1]"),
            a_binding("n2:op.one:[n2]", inputs=("n1",)),
            {"choice": {"candidate": "n1:reuse:[n1]"}},
        ]
    )
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )
    conversation = Conversation()
    plan = a_scan_and_return()
    first = planner.cover(REQUEST, plan, conversation, None, a_discovery())
    assert first.plan is not None
    rejected = next(one for one in first.plan.fragments if one.root_logical_node_id == "n2")

    planner.cover(
        REQUEST,
        plan,
        conversation,
        first.plan,
        a_discovery_holding_n1(),
        a_failed_fragment(rejected),
    )

    assert "## What has run so far" not in conversation.turns[1].content
    repair_turn = next(
        turn.content for turn in conversation.turns[3:] if "## What has run so far" in turn.content
    )
    assert "- n1: 1 row(s) from op.one" in repair_turn


def a_three_step_router() -> OperatorRouter:
    """One single-kind operator each for SCAN, FILTER, and AGGREGATE."""
    return a_router(
        an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
        an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
        an_operator("builtin.aggregate", frozenset({LogicalOperator.AGGREGATE})),
    )


@pytest.mark.parametrize(
    ("root_node_id", "reader_node_id"),
    [("n1", "n2"), ("n2", "n3")],
    ids=["scan_root", "non_scan_root"],
)
def test_a_candidate_turn_lists_the_fragment_reading_its_root_with_what_it_produces_and_receives(
    root_node_id: str, reader_node_id: str
) -> None:
    """A root's turn names the bound fragment reading it, its output, and how it takes the root."""
    client = ScriptedLLMClient(
        [
            a_binding("n3:builtin.aggregate:[n3]", inputs=("n2",)),
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]"),
        ]
    )

    result = a_planner(client, a_three_step_router()).cover(
        REQUEST, a_plan(), Conversation(), None, a_discovery()
    )

    assert result.plan is not None
    turn = client.conversations[{"n3": 0, "n2": 1, "n1": 2}[root_node_id]][-1][1]
    reader = result.plan.fragment_for_root(reader_node_id)
    assert reader is not None
    assert the_readers(turn) == [
        {
            "fragment": reader.id,
            "logical_node_ids": [reader_node_id],
            "instruction": "do the whole thing",
            "required_output_columns": ["title: str"],
            "input": {
                "name": f"the {root_node_id} table",
                "description": f"the rows {root_node_id} produced",
            },
        }
    ]


def test_a_root_read_by_two_fragments_lists_both_in_its_turn() -> None:
    """Every fragment reading a root is named, not only the first one bound."""
    client = ScriptedLLMClient(
        [
            a_binding("n4:builtin.join:[n4]", inputs=("n2", "n3")),
            a_binding("n3:builtin.filter:[n3]", inputs=("n1",)),
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]"),
        ]
    )
    planner = a_planner(
        client,
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
            an_operator("builtin.join", frozenset({LogicalOperator.JOIN})),
        ),
    )

    result = planner.cover(REQUEST, a_fan_out_scan(), Conversation(), None, a_discovery())

    assert result.plan is not None
    scan_turn = client.conversations[3][-1][1]
    fragment_n2 = result.plan.fragment_for_root("n2")
    fragment_n3 = result.plan.fragment_for_root("n3")
    assert fragment_n2 is not None
    assert fragment_n3 is not None
    assert [entry["fragment"] for entry in the_readers(scan_turn)] == [
        fragment_n2.id,
        fragment_n3.id,
    ]


def a_scan_read_twice_by_one_fragment() -> LogicalPlan:
    """n1 SCAN read by n2 FILTER and by n3 JOIN of n1 and n2, ended by n4 RETURN."""
    plan = a_fan_out_scan()
    return LogicalPlan(
        id="p7",
        parent_id=None,
        output_node_id="n4",
        nodes=[
            plan.node("n1"),
            plan.node("n2"),
            PipelineNode(
                id="n3",
                kind=LogicalOperator.JOIN,
                input_node_ids=["n1", "n2"],
                attributes={"instruction": "join them on title"},
                required_output_columns=[OutputColumn(name="title", dtype="str")],
            ),
            PipelineNode(
                id="n4",
                kind=LogicalOperator.RETURN,
                input_node_ids=["n3"],
                attributes={"columns": ["title"]},
            ),
        ],
    )


def test_a_fragment_holding_two_readers_of_a_root_is_listed_once() -> None:
    """Two readers grouped into one fragment share one line naming both."""
    client = ScriptedLLMClient(
        [a_binding("n3:op.all:[n2,n3]", inputs=("n1",)), a_binding("n1:op.all:[n1]")]
    )
    planner = a_planner(client, a_router(an_operator("op.all", ALL_KINDS)))

    result = planner.cover(
        REQUEST, a_scan_read_twice_by_one_fragment(), Conversation(), None, a_discovery()
    )

    assert result.plan is not None
    reader = result.plan.fragment_for_root("n3")
    assert reader is not None
    readers = the_readers(client.conversations[1][-1][1])
    assert len(readers) == 1
    assert readers[0]["fragment"] == reader.id
    assert readers[0]["logical_node_ids"] == ["n2", "n3"]


def test_a_reader_fragment_is_listed_with_every_node_it_groups() -> None:
    """A fragment is named by all of its nodes, including those that do not read the root."""
    client = ScriptedLLMClient(
        [a_binding("n3:op.rest:[n2,n3]", inputs=("n1",)), a_binding("n1:builtin.scan:[n1]")]
    )
    planner = a_planner(
        client,
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("op.rest", frozenset({LogicalOperator.FILTER, LogicalOperator.AGGREGATE})),
        ),
    )

    planner.cover(REQUEST, a_plan(), Conversation(), None, a_discovery())

    readers = the_readers(client.conversations[1][-1][1])
    assert [reader["logical_node_ids"] for reader in readers] == [["n2", "n3"]]


def a_scan_read_by(kind: ControlOperator, instruction: str) -> LogicalPlan:
    """n1 SCAN read by n2 PROJECT, which nothing reads, and by a control node c1 of this kind."""
    plan = a_scan_and_return()
    return LogicalPlan(
        id="p8",
        parent_id=None,
        output_node_id=None,
        nodes=[
            plan.node("n1"),
            plan.node("n2"),
            PipelineNode(
                id="c1",
                kind=kind,
                input_node_ids=["n1"],
                attributes={"instruction": instruction},
            ),
        ],
    )


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (a_scan_a_filter_and_return(), {"id": "n3", "kind": "RETURN", "columns": ["title"]}),
        (
            a_scan_read_by(ControlOperator.DISCOVER, "find the companies"),
            {"id": "c1", "kind": "DISCOVER", "instruction": "find the companies"},
        ),
        (
            a_scan_read_by(ControlOperator.REPLAN, "need the companies first"),
            {"id": "c1", "kind": "REPLAN", "instruction": "need the companies first"},
        ),
    ],
    ids=["return", "discover", "replan"],
)
def test_a_candidate_turn_names_a_reader_no_operator_runs_by_what_it_asks_for(
    plan: LogicalPlan, expected: dict[str, Any]
) -> None:
    """A RETURN is listed by the columns it answers with, a control node by its instruction."""
    client = ScriptedLLMClient(
        [a_binding("n2:op.all:[n2]", inputs=("n1",)), a_binding("n1:op.all:[n1]")]
    )

    a_planner(client, a_router(an_operator("op.all", ALL_KINDS))).cover(
        REQUEST, plan, Conversation(), None, a_discovery()
    )

    assert any(
        expected in the_readers(conversation[-1][1]) for conversation in client.conversations
    )


def test_a_root_nothing_reads_is_listed_with_an_empty_readers_array() -> None:
    """A root with no reader offers the model an empty `Read by` array."""
    client = ScriptedLLMClient(
        [a_binding("n2:op.all:[n2]", inputs=("n1",)), a_binding("n1:op.all:[n1]")]
    )

    a_planner(client, a_router(an_operator("op.all", ALL_KINDS))).cover(
        REQUEST,
        a_scan_read_by(ControlOperator.DISCOVER, "find the companies"),
        Conversation(),
        None,
        a_discovery(),
    )

    assert the_readers(client.conversations[0][-1][1]) == []


def test_a_reader_covered_by_a_reused_fragment_is_listed_by_that_fragment() -> None:
    """A fragment kept from the previous cover reads the root just as a new one would."""
    client = ScriptedLLMClient(
        [
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]"),
            {"choice": {"candidate": "n2:reuse:[n2]"}},
            a_binding("n1:builtin.scan:[n1]"),
        ]
    )
    planner = a_planner(
        client,
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
        ),
    )
    plan = a_scan_a_filter_and_return()

    first = planner.cover(REQUEST, plan, Conversation(), None, a_discovery())
    assert first.plan is not None
    planner.cover(
        REQUEST,
        plan,
        Conversation(),
        first.plan,
        a_discovery_holding_n2(),
        a_failed_fragment(first.plan.fragments[0]),
    )

    kept = first.plan.fragment_for_root("n2")
    assert kept is not None
    readers = the_readers(client.conversations[3][-1][1])
    assert len(readers) == 1
    assert readers[0]["fragment"] == kept.id
    assert readers[0]["logical_node_ids"] == ["n2"]


def test_a_fragments_scan_sources_is_copied_from_what_the_plans_scans_name() -> None:
    """The physical planner reads the source off the plan; the model never names one."""
    client = ScriptedLLMClient([a_binding("n2:op.one:[n1,n2]")])
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )

    result = planner.cover(REQUEST, a_scan_and_return(), Conversation(), None, a_discovery())

    assert result.plan is not None
    assert result.plan.fragments[0].scan_sources == {"n1": POSTINGS}


def test_a_fragments_output_columns_is_what_the_binding_wrote() -> None:
    """The physical planner writes the fragment's columns; nothing falls back to the root's."""
    client = ScriptedLLMClient(
        [
            a_binding(
                "n2:op.one:[n1,n2]",
                required_output_columns=[
                    {"name": "planet_name", "dtype": "str"},
                    {"name": "distance_au", "dtype": "float"},
                ],
            )
        ]
    )
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )

    result = planner.cover(REQUEST, a_scan_and_return(), Conversation(), None, a_discovery())

    assert result.plan is not None
    assert result.plan.fragments[0].required_output_columns == [
        OutputColumn(name="planet_name", dtype="str"),
        OutputColumn(name="distance_au", dtype="float"),
    ]


def test_a_fragments_inputs_are_the_nodes_feeding_it_and_not_a_model_choice() -> None:
    """The plan decides which nodes a fragment reads, and the model only names them."""
    planner = a_planner(
        ScriptedLLMClient(
            [
                a_binding("n3:builtin.aggregate:[n3]", inputs=("n2",)),
                a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
                a_binding("n1:builtin.scan:[n1]"),
            ]
        ),
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
            an_operator("builtin.aggregate", frozenset({LogicalOperator.AGGREGATE})),
        ),
    )
    result = planner.cover(REQUEST, a_plan(), Conversation(), None, a_discovery())
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
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            a_binding("n1:builtin.scan:[n1]"),
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            {"choice": {"candidate": "n1:reuse:[n1]"}},
        ]
    )
    planner = a_planner(
        client,
        a_router(
            an_operator("builtin.scan", frozenset({LogicalOperator.SCAN})),
            an_operator("builtin.filter", frozenset({LogicalOperator.FILTER})),
        ),
    )
    plan = a_scan_a_filter_and_return()

    first = planner.cover(REQUEST, plan, Conversation(), None, a_discovery())
    assert first.plan is not None
    again = planner.cover(
        REQUEST,
        plan,
        Conversation(),
        first.plan,
        a_discovery_holding_n1(),
        a_failed_fragment(first.plan.fragments[0]),
    )

    assert again.plan is not None
    assert again.plan.fragment_for_root("n1") == first.plan.fragment_for_root("n1")


def test_a_plan_whose_every_node_already_ran_carries_the_cover_over_unasked() -> None:
    """An ending written again asks for no work, so no binding is worth a model call."""
    client = ScriptedLLMClient([a_binding("n2:op.one:[n1,n2]")])
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )
    plan = a_scan_and_return()

    first = planner.cover(REQUEST, plan, Conversation(), None, a_discovery())
    assert first.plan is not None
    conversation = Conversation()
    again = planner.cover(REQUEST, plan, conversation, first.plan, a_discovery_holding_n2())

    assert again.plan is not None
    assert again.plan.fragments == first.plan.fragments
    assert conversation.turns == []


@pytest.mark.parametrize("covers_the_same_plan", [True, False])
def test_a_cover_names_the_previous_cover_as_its_parent_only_for_the_same_logical_plan(
    covers_the_same_plan: bool,
) -> None:
    """A cover repair replaces the cover before it; a new plan version starts its own lineage."""
    client = ScriptedLLMClient([a_binding("n2:op.one:[n1,n2]"), a_binding("n2:op.one:[n1,n2]")])
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )
    plan = a_scan_and_return()
    first = planner.cover(REQUEST, plan, Conversation(), None, a_discovery())
    assert first.plan is not None
    next_plan = plan if covers_the_same_plan else a_new_version_of(plan)

    again = planner.cover(
        REQUEST,
        next_plan,
        Conversation(),
        first.plan,
        a_discovery(),
        a_failed_fragment(first.plan.fragments[0]),
    )

    assert again.plan is not None
    assert again.plan.parent_id == (first.plan.id if covers_the_same_plan else None)


def test_a_carried_over_cover_of_a_new_plan_version_names_no_parent() -> None:
    """A continuation rewriting only the ending still starts a new lineage of covers."""
    client = ScriptedLLMClient([a_binding("n2:op.one:[n1,n2]")])
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )
    plan = a_scan_and_return()
    first = planner.cover(REQUEST, plan, Conversation(), None, a_discovery())
    assert first.plan is not None

    again = planner.cover(
        REQUEST, a_new_version_of(plan), Conversation(), first.plan, a_discovery_holding_n2()
    )

    assert again.plan is not None
    assert again.plan.fragments == first.plan.fragments
    assert again.plan.parent_id is None


def test_a_node_with_no_result_is_offered_no_way_to_reuse_its_fragment() -> None:
    """The failed fragment has no result, so the model must pick again and reword its work."""
    client = ScriptedLLMClient(
        [
            a_binding("n2:op.one:[n1,n2]"),
            a_binding("n2:op.one:[n1,n2]"),
        ]
    )
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )
    plan = a_scan_and_return()

    first = planner.cover(REQUEST, plan, Conversation(), None, a_discovery())
    assert first.plan is not None
    again = planner.cover(
        REQUEST,
        plan,
        Conversation(),
        first.plan,
        a_discovery(),
        a_failed_fragment(first.plan.fragments[0]),
    )

    assert again.plan is not None
    assert again.plan.fragments[0].id != first.plan.fragments[0].id


def a_repair_turn_section(failed_with: list[ParameterAttempt] | None = None) -> tuple[str, str]:
    """The failed fragment's id and the section a repair turn shows for it."""
    client = ScriptedLLMClient([a_binding("n2:op.one:[n1,n2]"), a_binding("n2:op.one:[n1,n2]")])
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )
    conversation = Conversation()
    plan = a_scan_and_return()
    first = planner.cover(REQUEST, plan, conversation, None, a_discovery())
    assert first.plan is not None
    fragment = first.plan.fragments[0]
    failed = (
        a_failed_fragment(fragment)
        if failed_with is None
        else a_failed_fragment_with(fragment, failed_with)
    )

    planner.cover(REQUEST, plan, conversation, first.plan, a_discovery(), failed)

    return fragment.id, conversation.turns[-2].content.split("## The fragment that failed", 1)[1]


def test_a_repair_turn_shows_why_the_fragment_failed_and_each_attempt_in_order() -> None:
    """Each attempt appears under its number with its parameters and how it ended, and the
    rows an attempt returned appear under that attempt, marked unreadable."""
    fragment_id, section = a_repair_turn_section()

    assert section.index(f"Fragment {fragment_id}, bound to op.one, failed.") < section.index(
        "Steps n1, n2 failed in all attempts"
    )
    assert (
        section.index("Attempt 1 (failed)")
        < section.index('Parameters: {"predicate": "moon > 0"}')
        < section.index("The call failed: no column named moon")
        < section.index("Attempt 2 (failed)")
        < section.index("it kept the gas giants")
        < section.index("{'title': 'Mercury'}")
        < section.index("Attempt 3 (failed)")
        < section.index("Not run: its parameters repeat attempt 2.")
        < section.index("No rows above can be read or reused")
    )


def test_a_repair_turn_for_a_fragment_that_returned_no_rows_marks_nothing_unreadable() -> None:
    """With no rows shown, the repair turn says what came back but adds no note about rows."""
    _, section = a_repair_turn_section([an_attempt_that_returned([])])

    assert "It returned no rows" in section
    assert "it kept the gas giants" in section
    assert "No rows above" not in section


def test_a_repair_answers_the_binding_it_is_replacing_in_the_same_conversation() -> None:
    """One logical plan is one conversation, so a rebinding is a turn on top of what failed."""
    client = ScriptedLLMClient(
        [
            a_binding("n2:op.one:[n1,n2]"),
            a_binding("n2:op.one:[n1,n2]"),
        ]
    )
    planner = a_planner(
        client,
        a_router(an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))),
    )
    conversation = Conversation()
    plan = a_scan_and_return()

    first = planner.cover(REQUEST, plan, conversation, None, a_discovery())
    assert first.plan is not None
    planner.cover(
        REQUEST,
        plan,
        conversation,
        first.plan,
        a_discovery(),
        a_failed_fragment(first.plan.fragments[0]),
    )

    assert sum(1 for turn in conversation.turns if turn.role == "system") == 1
    assert "it kept the gas giants" in conversation.turns[-2].content
    # The scanned entities were already sent on the opening turn; a repair must not
    # resend them, or the prefix the model already cached stops matching (ADR-025).
    assert "The data the plan scans" in conversation.turns[1].content
    assert "The data the plan scans" not in conversation.turns[-2].content
    assert "## Request" in conversation.turns[1].content
    assert "## Request" not in conversation.turns[-2].content
    assert "## Plan" in conversation.turns[1].content
    assert "## Plan" not in conversation.turns[-2].content


def test_the_opening_instructions_carry_nothing_that_varies_by_plan() -> None:
    """An identical opening is what makes the prefix cache across runs and across plans."""
    first, second = Conversation(), Conversation()
    router = a_router(
        an_operator("op.one", frozenset({LogicalOperator.SCAN, LogicalOperator.PROJECT}))
    )
    a_planner(ScriptedLLMClient([a_binding("n2:op.one:[n1,n2]")]), router).cover(
        REQUEST, a_scan_and_return(), first, None, a_discovery()
    )
    a_planner(ScriptedLLMClient([a_binding("n2:op.one:[n2]", inputs=("n1",))]), router).cover(
        REQUEST, a_scan_and_return(source=None), second, None, {}
    )

    assert first.turns[0].content == second.turns[0].content
