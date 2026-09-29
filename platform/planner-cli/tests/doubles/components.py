"""A fake planner and the runs the CLI's own tests render, so no test reaches a model."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from blue_data_planner import RunResult, RunStatus
from blue_data_planner.plan import signatures
from blue_data_planner.types import (
    AttributeRef,
    AttributeView,
    ControlOperator,
    DataBundle,
    DataPipeline,
    DiscoveryResult,
    EntityMatch,
    ExecutionResult,
    ExecutionStatus,
    FragmentResult,
    InputData,
    LogicalOperator,
    LogicalPlan,
    ParameterAttempt,
    PhysicalPlan,
    PhysicalPlanFragment,
    PipelineNode,
    RunConversations,
    RunCounters,
    RunLimits,
)


class FakeDataPlanner:
    """Answers run and plan with prepared results, so an adapter is tested without a model."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.requests: list[str] = []
        self.operations: list[str] = []

    def run(self, request: str) -> RunResult:
        self.requests.append(request)
        self.operations.append("run")
        return self.result

    def plan(self, request: str) -> RunResult:
        self.requests.append(request)
        self.operations.append("plan")
        return self.result


def a_run_result(
    status: RunStatus = RunStatus.OK,
    answer: DataBundle | None = None,
    failure_reason: str | None = None,
    pipeline: DataPipeline | None = None,
    results: Mapping[str, FragmentResult | DiscoveryResult] | None = None,
    counters: RunCounters | None = None,
    conversations: RunConversations | None = None,
) -> RunResult:
    """Return a RunResult whose unset parts are the empty shapes a finished run would carry."""
    return RunResult(
        status=status,
        answer=answer,
        failure_reason=failure_reason,
        pipeline=pipeline
        or DataPipeline(
            logical_plans={},
            physical_plans={},
            current_logical_plan_id=None,
            current_physical_plan_id=None,
        ),
        results=dict(results or {}),
        counters=counters or RunCounters(limits=RunLimits()),
        conversations=conversations or RunConversations(),
    )


def a_discovery_of_one_match() -> DiscoveryResult:
    """Return a catalog search that found one entity and lost a term to the per-node cap."""
    return DiscoveryResult(
        search_terms=["schools"],
        matches=[
            EntityMatch(
                path="california_schools/schools",
                name="schools",
                description="every school in the state",
                source_kind="sql/sqlite",
                attributes=[
                    AttributeView(
                        ref=AttributeRef(
                            source="bird",
                            database="california_schools",
                            collection="main",
                            entity="schools",
                            attribute="County",
                        ),
                        dtype="str",
                        description=None,
                        frequent_values=[],
                        sample_values=[],
                        distinct_count=None,
                        null_count=None,
                        empty_count=None,
                        counted_rows=None,
                        min=None,
                        max=None,
                        semantic_type=None,
                    )
                ],
                score=0.91,
                search_term="schools",
                row_count=17686,
            )
        ],
        dropped_terms=1,
    )


def a_run_over_one_cover_of_two_fragments(
    filter_result: FragmentResult | None = None,
    discovery: DiscoveryResult | None = None,
    instruction: str = "keep the postings in the bay area",
) -> RunResult:
    """Return a run whose one cover scanned a source and filtered what the scan read."""
    nodes = [
        PipelineNode(
            id="n1",
            kind=LogicalOperator.SCAN,
            attributes={"instruction": "read every posting", "source": "jobs/postings"},
        ),
        PipelineNode(
            id="n2",
            kind=LogicalOperator.FILTER,
            input_node_ids=["n1"],
            attributes={"instruction": instruction},
        ),
    ]
    if discovery is not None:
        nodes.append(
            PipelineNode(
                id="n3",
                kind=ControlOperator.DISCOVER,
                attributes={"instruction": "find a source holding schools"},
            )
        )
    plan = LogicalPlan(id="lp1", parent_id=None, nodes=nodes, output_node_id="n2")
    scan = PhysicalPlanFragment(
        id="f1",
        logical_node_ids=["n1"],
        root_logical_node_id="n1",
        operator_id="builtin.source_scan",
        scan_sources={"n1": "jobs/postings"},
        instruction="read every posting",
        input_data=[],
        required_output_columns=[],
    )
    sem_filter = PhysicalPlanFragment(
        id="f2",
        logical_node_ids=["n2"],
        root_logical_node_id="n2",
        operator_id="lotus.sem_filter",
        scan_sources={},
        instruction=instruction,
        input_data=[InputData(logical_node_id="n1", name="postings", description="every posting")],
        required_output_columns=[],
    )
    # The physical planner writes a cover's fragments in no particular order, so the one a run
    # reaches first is listed second here.
    cover = PhysicalPlan(
        id="pp1", parent_id=None, logical_plan_id=plan.id, fragments=[sem_filter, scan]
    )
    signed = signatures(plan, cover)
    results: dict[str, FragmentResult | DiscoveryResult] = {
        signed["n1"]: FragmentResult(
            status=RunStatus.OK,
            attempts=[
                ParameterAttempt(
                    parameters={},
                    result=ExecutionResult(
                        status=ExecutionStatus.OK,
                        operator_id=scan.operator_id,
                        output=[
                            [
                                {"title": f"posting {index}", "county": "Alameda"}
                                for index in range(140)
                            ]
                        ],
                        row_count=140,
                    ),
                    assessment=None,
                )
            ],
            failure_reason=None,
        )
    }
    if filter_result is not None:
        results[signed["n2"]] = filter_result
    if discovery is not None:
        results[signed["n3"]] = discovery
    return a_run_result(
        pipeline=DataPipeline(
            logical_plans={plan.id: plan},
            physical_plans={cover.id: cover},
            current_logical_plan_id=plan.id,
            current_physical_plan_id=cover.id,
        ),
        results=results,
    )


def a_rerun_whose_second_cover_keeps_the_scan() -> RunResult:
    """Return a run whose cover repair kept the scan and bound the filter to another operator."""
    first_pass = a_run_over_one_cover_of_two_fragments()
    first = first_pass.pipeline.physical_plans["pp1"]
    scan = next(one for one in first.fragments if one.root_logical_node_id == "n1")
    rebound = dataclasses.replace(
        next(one for one in first.fragments if one.root_logical_node_id == "n2"),
        id="f3",
        operator_id="builtin.filter",
    )
    second = PhysicalPlan(
        id="pp2", parent_id="pp1", logical_plan_id="lp1", fragments=[scan, rebound]
    )
    return a_run_result(
        pipeline=DataPipeline(
            logical_plans=dict(first_pass.pipeline.logical_plans),
            physical_plans={"pp1": first, "pp2": second},
            current_logical_plan_id="lp1",
            current_physical_plan_id="pp2",
        ),
        results=dict(first_pass.results),
    )


def a_planning_run() -> RunResult:
    """Return what --explain produces: a plan and its cover, with no operator call spent."""
    return dataclasses.replace(
        a_run_over_one_cover_of_two_fragments(),
        status=RunStatus.PLANNED,
        answer=None,
        results={},
    )
