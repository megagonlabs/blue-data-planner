"""Real atomic operations must receive separate fragments for repeated logical steps."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from blue_data_planner.data import DataCatalog, DataRegistry
from blue_data_planner.data_planner import DataPlanner
from blue_data_planner.fragment_runner import FragmentRunner
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.types import (
    Conversation,
    DataBundle,
    LogicalOperator,
    LogicalPlanResult,
    OperatorOutput,
    OutputColumn,
    PhysicalOperator,
    PipelineNode,
    RunLimits,
    RunStatus,
)
from tests.doubles.components import FakeCatalogSearcher, FakeLogicalPlanner
from tests.doubles.llm import ScriptedLLMClient
from tests.doubles.operators import recording_operator
from tests.unit.plan.test_fragments import a_repeated_kind_plan
from tests.unit.test_physical_planner import a_binding, a_discovery, a_plan, a_router


@pytest.fixture
def real_operators() -> tuple[DataRegistry, list[PhysicalOperator]]:
    """Use pinned Blue operations and the actual builtin declarations, without model calls."""
    registry = DataRegistry(name="single_node_fragments", rebuild=True)
    model = "openai/gpt-6-luna"
    operators = BlueSuite().operators(
        data_registry=registry,
        llm_client=LLMClient(name="single_node", properties={"llm.model": model}),
        model=model,
    )
    return registry, [*operators, *builtin_operators(registry)]


@pytest.mark.parametrize(
    ("operator_id", "kind"),
    [
        ("blue.select", LogicalOperator.FILTER),
        ("builtin.filter", LogicalOperator.FILTER),
        ("blue.union", LogicalOperator.UNION),
        ("builtin.union", LogicalOperator.UNION),
    ],
)
def test_repeated_atomic_steps_are_offered_and_run_as_separate_fragments(
    real_operators: tuple[DataRegistry, list[PhysicalOperator]],
    operator_id: str,
    kind: LogicalOperator,
) -> None:
    """Keep both predicates and union all three tables without arity failures or repairs."""
    registry, operators = real_operators
    operator = next(item for item in operators if item.operator_id == operator_id)
    calls: list[DataBundle] = []

    def record_call(
        inputs: DataBundle, parameters: dict[str, Any], columns: list[OutputColumn]
    ) -> OperatorOutput:
        calls.append(inputs)
        return operator.call(inputs, parameters, columns)

    plan = a_repeated_kind_plan(kind)
    scans: list[PhysicalOperator] = []
    for source in (node for node in plan.nodes if node.kind is LogicalOperator.SCAN):
        rows = (
            [{"value": value} for value in (1, 2, 3)]
            if kind is LogicalOperator.FILTER
            else [{"value": int(source.id[1:])}]
        )
        scan, _ = recording_operator(
            f"test.{source.id}", frozenset({LogicalOperator.SCAN}), [OperatorOutput(data=[rows])]
        )
        scans.append(scan)
    router = a_router(replace(operator, call=record_call), *scans)
    bindings = [
        a_binding(
            f"{node.id}:{operator_id if node.kind is kind else f'test.{node.id}'}:[{node.id}]",
            inputs=tuple(node.input_node_ids),
            required_output_columns=[{"name": "value", "dtype": "int"}],
        )
        for node in reversed(plan.nodes_in_dependency_order())
        if node.is_operator_node
    ]
    covering = ScriptedLLMClient(bindings)
    accepted = {"decision": "accept", "assessment": "The rows match this step."}
    parameters: list[Any] = [item for _ in scans for item in ({}, accepted)]
    for comparison, bound in ((">", 1), ("<", 3)):
        if kind is LogicalOperator.UNION:
            values: dict[str, Any] = {}
        elif operator_id == "blue.select":
            values = {"operand_key": "value", "operand": comparison, "operand_val": bound}
        else:
            values = {
                "predicate": {
                    "left": {"column": "value"},
                    "operator": "gt" if comparison == ">" else "lt",
                    "right": {"value": bound},
                }
            }
        parameters.extend([values, accepted])
    execution = ScriptedLLMClient(parameters)
    planner = DataPlanner(
        FakeLogicalPlanner([LogicalPlanResult(RunStatus.OK, plan, None)]),  # pyright: ignore[reportArgumentType]
        PhysicalPlanner(covering, router),  # pyright: ignore[reportArgumentType]
        FragmentRunner(execution, DataCatalog(registry), router),  # pyright: ignore[reportArgumentType]
        FakeCatalogSearcher(),  # pyright: ignore[reportArgumentType]
        RunLimits(parameter_repairs=0, cover_repairs=0, failure_replans=0),
    )

    result = planner.run("Apply both filters." if kind is LogicalOperator.FILTER else "Union all.")

    assert result.status is RunStatus.OK
    expected = [2] if kind is LogicalOperator.FILTER else [1, 2, 3]
    assert result.answer == [[{"value": value} for value in expected]]
    assert len(result.pipeline.physical_plans) == 1
    assert result.counters.operator_calls == len(scans) + 2
    assert len(calls) == 2
    assert all(len(inputs) == (1 if kind is LogicalOperator.FILTER else 2) for inputs in calls)
    assert all(len(fragment.logical_node_ids) == 1 for fragment in result.pipeline.cover.fragments)
    offered = covering.conversations[0][-1][1]
    assert f"n2:{operator_id}:[n1,n2]" not in offered
    assert execution.answers == covering.answers == []


@pytest.mark.parametrize("operator_id", ["blue.union", "builtin.union"])
@pytest.mark.parametrize("grouped_nodes", [2, 3])
def test_the_planner_rejects_a_choice_that_groups_atomic_union_nodes(
    real_operators: tuple[DataRegistry, list[PhysicalOperator]],
    operator_id: str,
    grouped_nodes: int,
) -> None:
    """Even a model trying the old three-table or three-node grouping cannot select it."""
    _, operators = real_operators
    plan = a_repeated_kind_plan(LogicalOperator.UNION)
    if grouped_nodes == 3:
        plan = replace(
            plan,
            nodes=[
                *[node for node in plan.nodes if node.id != "end"],
                PipelineNode(
                    id="s4",
                    kind=LogicalOperator.SCAN,
                    attributes={"instruction": "Read the fourth table.", "source": None},
                ),
                PipelineNode(
                    id="n3",
                    kind=LogicalOperator.UNION,
                    input_node_ids=["n2", "s4"],
                    attributes={"instruction": "Add the fourth table."},
                    required_output_columns=[OutputColumn(name="value", dtype="int")],
                ),
                replace(plan.node("end"), input_node_ids=["n3"]),
            ],
        )
    root = f"n{grouped_nodes}"
    grouped = ",".join(f"n{index}" for index in range(1, grouped_nodes + 1))
    forbidden = f"{root}:{operator_id}:[{grouped}]"
    client = ScriptedLLMClient(
        [
            a_binding(
                forbidden,
                inputs=tuple(f"s{index}" for index in range(1, grouped_nodes + 2)),
                required_output_columns=[{"name": "value", "dtype": "int"}],
            )
        ]
    )
    operator = next(item for item in operators if item.operator_id == operator_id)
    planner = PhysicalPlanner(client, a_router(operator))  # pyright: ignore[reportArgumentType]
    conversation = Conversation()

    with pytest.raises(ValidationError) as caught:
        planner.cover("Union all the tables.", plan, conversation, None, {})

    # Check the label itself was disallowed, rather than only a missing field in the answer.
    assert any(
        error["type"] == "literal_error" and error["input"] == forbidden
        for error in caught.value.errors()
    )
    assert forbidden not in conversation.turns[-1].content
    assert f"{root}:{operator_id}:[{root}]" in conversation.turns[-1].content


def test_sql_compound_pushdown_remains_available(
    real_operators: tuple[DataRegistry, list[PhysicalOperator]],
) -> None:
    """The real compound SQL operator must still absorb a scan and both repeated filters."""
    _, operators = real_operators
    plan = a_plan()
    plan = replace(
        plan,
        nodes=[
            replace(node, kind=LogicalOperator.FILTER) if node.id == "n3" else node
            for node in plan.nodes
        ],
    )
    client = ScriptedLLMClient([a_binding("n3:blue.nl2sql:[n1,n2,n3]")])
    planner = PhysicalPlanner(client, a_router(*operators))  # pyright: ignore[reportArgumentType]

    result = planner.cover("Apply both filters.", plan, Conversation(), None, a_discovery())

    assert result.plan is not None
    assert len(result.plan.fragments) == 1
    assert result.plan.fragments[0].operator_id == "blue.nl2sql"
    assert result.plan.fragments[0].logical_node_ids == ["n1", "n2", "n3"]
