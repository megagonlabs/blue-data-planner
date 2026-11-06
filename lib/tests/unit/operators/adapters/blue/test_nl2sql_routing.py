"""Only offer Blue SQL fragments the operation can execute without external tables."""

from __future__ import annotations

import pytest

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.types import Conversation, LogicalOperator
from tests.doubles.llm import ScriptedLLMClient
from tests.unit.test_physical_planner import (
    REQUEST,
    a_binding,
    a_discovery,
    a_plan,
    a_plan_whose_n3_takes_n2_without_transforming_it,
    a_planner,
    a_router,
)


@pytest.fixture
def blue_router(sqlite_registry: DataRegistry) -> OperatorRouter:
    """Use the public suite's actual declarations, rather than a hand-written SQL stand-in."""
    model = "openai/gpt-6-luna"
    return a_router(
        *BlueSuite().operators(
            data_registry=sqlite_registry,
            llm_client=LLMClient(name="routing", properties={"llm.model": model}),
            model=model,
        )
    )


@pytest.mark.parametrize("reference_kind", [LogicalOperator.SCAN, LogicalOperator.FILTER])
def test_blue_sql_is_not_offered_a_fragment_with_external_reference_tables(
    blue_router: OperatorRouter, reference_kind: LogicalOperator
) -> None:
    """A bound scan or filter must include its reference producer inside the SQL region."""
    client = ScriptedLLMClient([a_binding("n3:blue.nl2sql:[n1,n2,n3]")])

    result = a_planner(client, blue_router).cover(
        REQUEST,
        a_plan_whose_n3_takes_n2_without_transforming_it(reference_kind),
        Conversation(),
        None,
        a_discovery(),
    )

    assert result.plan is not None
    assert result.plan.fragments[0].input_data == []
    offered = client.conversations[0][-1][1]
    assert "n3:blue.nl2sql:[n1,n2,n3]" in offered
    assert "n3:blue.nl2sql:[n3]" not in offered
    assert "n3:blue.nl2sql:[n2,n3]" not in offered


def test_blue_sql_keeps_the_whole_sql_region_but_not_table_only_cuts(
    blue_router: OperatorRouter,
) -> None:
    """Changing the bound-input flag alone must not leave cuts with ordinary input rows."""
    client = ScriptedLLMClient([a_binding("n3:blue.nl2sql:[n1,n2,n3]")])

    result = a_planner(client, blue_router).cover(
        REQUEST, a_plan(), Conversation(), None, a_discovery()
    )

    assert result.plan is not None
    assert result.plan.fragments[0].logical_node_ids == ["n1", "n2", "n3"]
    assert result.plan.fragments[0].input_data == []
    offered = client.conversations[0][-1][1]
    assert "n3:blue.nl2sql:[n1,n2,n3]" in offered
    assert "n3:blue.nl2sql:[n2,n3]" not in offered
    assert "n3:blue.nl2sql:[n3]" not in offered


def test_table_operations_still_cover_rows_from_another_fragment(
    blue_router: OperatorRouter,
) -> None:
    """The source-only restriction must not remove normal Blue operations over input rows."""
    client = ScriptedLLMClient(
        [
            a_binding("n3:blue.avg:[n3]", inputs=("n2",)),
            a_binding("n2:blue.select:[n2]", inputs=("n1",)),
            a_binding("n1:blue.nl2llm:[n1]"),
        ]
    )

    result = a_planner(client, blue_router).cover(
        REQUEST, a_plan(source=None), Conversation(), None, {}
    )

    assert result.plan is not None
    assert [fragment.operator_id for fragment in result.plan.fragments] == [
        "blue.avg",
        "blue.select",
        "blue.nl2llm",
    ]
    assert all("blue.nl2sql" not in turns[-1][1] for turns in client.conversations)


def test_no_operator_ends_the_cover_before_a_model_or_operator_call(
    blue_router: OperatorRouter,
) -> None:
    """A source-only SQL suite must not repeatedly try to process an external table."""
    client = ScriptedLLMClient([])
    router = a_router(blue_router.resolve("blue.nl2sql"))

    result = a_planner(client, router).cover(REQUEST, a_plan(source=None), Conversation(), None, {})

    assert result.plan is None
    assert result.failure_reason is not None
    assert "n3" in result.failure_reason
    assert "n2" in result.failure_reason
    assert client.conversations == []


def test_builtin_bound_scans_remain_available(
    blue_router: OperatorRouter, sqlite_registry: DataRegistry
) -> None:
    """The SQL restriction must preserve operators that actually implement bound inputs."""
    for operator in builtin_operators(sqlite_registry):
        blue_router.register(operator)
    client = ScriptedLLMClient(
        [
            a_binding("n3:builtin.scan:[n3]", inputs=("n2",)),
            a_binding("n2:blue.project:[n2]", inputs=("n1",)),
            a_binding("n1:blue.nl2sql:[n1]"),
        ]
    )

    result = a_planner(client, blue_router).cover(
        REQUEST,
        a_plan_whose_n3_takes_n2_without_transforming_it(LogicalOperator.SCAN),
        Conversation(),
        None,
        a_discovery(),
    )

    assert result.plan is not None
    assert [fragment.operator_id for fragment in result.plan.fragments] == [
        "builtin.scan",
        "blue.project",
        "blue.nl2sql",
    ]
    assert result.plan.fragments[0].input_data[0].logical_node_id == "n2"
