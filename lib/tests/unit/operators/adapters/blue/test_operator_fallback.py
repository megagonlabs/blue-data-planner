"""A contained Blue operation failure must leave another implementation available."""

from __future__ import annotations

import json
from typing import Any

import pytest
from blue_data_ops.llm.errors import LLMError
from blue_data_ops.operators.types import AttributeSpec, ErrorCode, FunctionResult

from blue_data_planner.data import DataCatalog, DataRegistry
from blue_data_planner.data_planner import DataPlanner
from blue_data_planner.fragment_runner import FragmentRunner
from blue_data_planner.operators.adapters.blue import blue_operators
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.physical_planner import PhysicalPlanner
from blue_data_planner.types import (
    FragmentResult,
    LogicalOperator,
    LogicalPlan,
    LogicalPlanResult,
    OperatorOutput,
    OutputColumn,
    PipelineNode,
    RunLimits,
    RunResult,
    RunStatus,
)
from tests.doubles.blue_operations import a_registry_answering_every_operation, scripted_operation
from tests.doubles.components import FakeCatalogSearcher, FakeLogicalPlanner
from tests.doubles.llm import ScriptedLLMClient
from tests.doubles.operators import RecordingOperator, recording_operator
from tests.unit.test_physical_planner import a_binding, a_router

FAILURES = (
    ErrorCode.DEFAULTS_FAILED,
    ErrorCode.VALIDATION_FAILED,
    ErrorCode.VALIDATION_RESULT_INVALID,
    ErrorCode.EXECUTION_RESULT_INVALID,
    ErrorCode.EXECUTION_FAILED,
)


class CannotCopy:
    """A broken operation default, failing when the real ops boundary materializes it."""

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        raise RuntimeError("the operation's default cannot be copied")


def run_with_a_broken_blue_operator(
    failure: ErrorCode,
    monkeypatch: pytest.MonkeyPatch,
    limits: RunLimits,
    execution_failure: Exception | None = None,
) -> tuple[RunResult, RecordingOperator, list[ErrorCode], ScriptedLLMClient]:
    """Drive real covering, Blue execution, fragment repair, and builtin fallback offline."""
    broken = scripted_operation("select")

    def refuse_validation(*args: Any) -> Any:
        raise RuntimeError("the operation's validator is broken")

    def invalid_validation(*args: Any) -> None:
        return None

    def invalid_execution(*args: Any) -> FunctionResult:
        # Deliberately violate the upstream contract to exercise its real result validator.
        invalid_rows: Any = [["not a record"]]
        return FunctionResult(invalid_rows, {})

    def refuse_execution(*args: Any) -> Any:
        raise execution_failure or RuntimeError("the operation's execution is broken")

    if failure is ErrorCode.DEFAULTS_FAILED:
        monkeypatch.setattr(
            broken,
            "_refresh_attribute_projection",
            lambda: {"broken_default": AttributeSpec(type="str", default=CannotCopy())},
        )
    elif failure is ErrorCode.VALIDATION_FAILED:
        monkeypatch.setattr(broken, "_validate_input", refuse_validation)
    elif failure is ErrorCode.VALIDATION_RESULT_INVALID:
        monkeypatch.setattr(broken, "_validate_input", invalid_validation)
    elif failure is ErrorCode.EXECUTION_FAILED:
        monkeypatch.setattr(broken, "_execute", refuse_execution)
    else:
        monkeypatch.setattr(broken, "_execute", invalid_execution)
    run = broken.run
    failures_seen: list[ErrorCode] = []

    def record_failure(*args: Any) -> Any:
        outcome = run(*args)
        failures_seen.extend(error.code for error in outcome[2].errors)
        return outcome

    monkeypatch.setattr(broken, "run", record_failure)
    registry = DataRegistry(name="operator_fallback", rebuild=True)
    blue = blue_operators(
        a_registry_answering_every_operation(broken), registry, model="openai/gpt-6-luna"
    )
    scan, scan_calls = recording_operator(
        "test.scan",
        frozenset({LogicalOperator.SCAN}),
        [OperatorOutput(data=[[{"title": "SWE"}, {"title": "Designer"}]])],
    )
    router = a_router(
        scan,
        next(operator for operator in blue if operator.operator_id == "blue.select"),
        next(
            operator
            for operator in builtin_operators(registry)
            if operator.operator_id == "builtin.filter"
        ),
    )
    plan = LogicalPlan(
        id="filter-postings",
        parent_id=None,
        output_node_id="n3",
        nodes=[
            PipelineNode(
                id="n1",
                kind=LogicalOperator.SCAN,
                input_node_ids=[],
                attributes={"instruction": "read the postings", "source": None},
                required_output_columns=[],
            ),
            PipelineNode(
                id="n2",
                kind=LogicalOperator.FILTER,
                input_node_ids=["n1"],
                attributes={"instruction": "keep the SWE posting"},
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
    client = ScriptedLLMClient(
        [
            a_binding("n2:blue.select:[n2]", inputs=("n1",)),
            a_binding("n1:test.scan:[n1]"),
            {},
            {"decision": "accept", "assessment": "The postings were read."},
            {"operand_key": "title", "operand": "=", "operand_val": "SWE"},
            a_binding("n2:builtin.filter:[n2]", inputs=("n1",)),
            {"choice": {"candidate": "n1:reuse:[n1]"}},
            {
                "predicate": {
                    "left": {"column": "title"},
                    "operator": "eq",
                    "right": {"value": "SWE"},
                }
            },
            {"decision": "accept", "assessment": "Only the SWE posting remains."},
        ]
    )
    catalog = DataCatalog(registry)
    planner = DataPlanner(
        FakeLogicalPlanner([LogicalPlanResult(RunStatus.OK, plan, None)]),  # pyright: ignore[reportArgumentType]
        PhysicalPlanner(client, router),  # pyright: ignore[reportArgumentType]
        FragmentRunner(client, catalog, router),  # pyright: ignore[reportArgumentType]
        FakeCatalogSearcher(),  # pyright: ignore[reportArgumentType]
        limits,
    )
    return planner.run("Keep the SWE posting."), scan_calls, failures_seen, client


@pytest.mark.parametrize("failure", FAILURES)
def test_a_broken_blue_operation_can_be_replaced_and_the_request_finishes(
    failure: ErrorCode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad operation gets one call, a repaired cover uses builtin, and held rows are reused."""
    result, scan, failures, client = run_with_a_broken_blue_operator(
        failure,
        monkeypatch,
        RunLimits(operator_calls=3, parameter_repairs=4, cover_repairs=1, failure_replans=0),
    )

    assert result.status is RunStatus.OK
    assert result.answer == [[{"title": "SWE"}]]
    assert result.counters.operator_calls == 3
    assert result.counters.failure_replans == 0
    assert len(result.pipeline.physical_plans) == 2
    assert len(scan.calls) == 1
    assert failures == [failure]
    assert client.answers == []
    failed = [
        item
        for item in result.results.values()
        if isinstance(item, FragmentResult) and item.status is RunStatus.NEEDS_REPAIR
    ]
    assert len(failed) == 1 and len(failed[0].attempts) == 1
    execution = failed[0].attempts[0].result
    assert execution is not None and execution.error is not None
    assert execution.error.kind.value == "operator_unavailable"
    assert "blue.select" in execution.error.message
    repair_prompt = "\n".join(content for _, content in client.conversations[5])
    assert execution.error.message in repair_prompt
    assert "builtin.filter" in repair_prompt
    assert "operator_unavailable" in json.dumps(result.to_dict())


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        (RunLimits(cover_repairs=0, failure_replans=0), RunStatus.REPLAN_EXHAUSTED),
        (
            RunLimits(operator_calls=2, cover_repairs=1, failure_replans=0),
            RunStatus.BUDGET_EXHAUSTED,
        ),
    ],
)
def test_operator_fallback_still_obeys_the_run_budgets(
    limits: RunLimits, expected: RunStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing another operator must not create an unbounded retry path."""
    result, _, failures, _ = run_with_a_broken_blue_operator(
        ErrorCode.VALIDATION_FAILED, monkeypatch, limits
    )

    assert result.status is expected
    assert result.answer is None
    assert failures == [ErrorCode.VALIDATION_FAILED]
    assert result.counters.operator_calls == 2


def test_a_rejected_model_setting_moves_to_another_operator_without_parameter_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing FILTER parameters cannot fix a model's unsupported generation setting."""
    result, scan, failures, client = run_with_a_broken_blue_operator(
        ErrorCode.EXECUTION_FAILED,
        monkeypatch,
        RunLimits(operator_calls=3, parameter_repairs=4, cover_repairs=1, failure_replans=0),
        execution_failure=LLMError("unsupported generation setting", code="unsupported_parameter"),
    )

    assert result.status is RunStatus.OK
    assert result.answer == [[{"title": "SWE"}]]
    assert failures == [ErrorCode.EXECUTION_FAILED]
    assert result.counters.operator_calls == 3
    assert len(scan.calls) == 1
    assert client.answers == []
