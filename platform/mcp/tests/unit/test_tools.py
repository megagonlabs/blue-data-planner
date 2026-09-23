"""What one tool call returns, and what it leaves out."""

from __future__ import annotations

import json

import pytest

import blue_data_planner_mcp.config as config
from blue_data_planner_mcp.server import mcp, query_data
from tests.doubles.planner import FakePlanner, a_run_that_answered, a_run_that_ran_out_of_budget


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> FakePlanner:
    fake = FakePlanner(a_run_that_answered())
    monkeypatch.setattr(config, "get_planner", lambda: fake)
    return fake


async def test_a_request_returns_the_runs_json_form_without_the_plans(planner: FakePlanner):
    """Every plan the request produced grows with the retries, so an agent is not sent them."""
    payload = json.loads(await query_data("which postings are at healthcare companies"))
    assert planner.operations == ["run"]
    assert planner.requests == ["which postings are at healthcare companies"]
    assert "pipeline" not in payload
    assert payload["status"] == "ok"
    assert payload["answer"] == [[{"title": "Senior Data Scientist", "company": "Corvus Health"}]]
    assert set(payload) == {
        "status",
        "answer",
        "failure_reason",
        "results",
        "counters",
        "conversations",
    }


async def test_include_plan_adds_every_plan_the_request_produced(planner: FakePlanner):
    payload = json.loads(await query_data("which postings", include_plan=True))
    assert payload["pipeline"]["current_logical_plan_id"] == "8c4a71d0"


async def test_explain_returns_the_plan_and_spends_no_operator_call(planner: FakePlanner):
    """`explain` is the CLI's --explain: the plan is the answer, and nothing runs."""
    payload = json.loads(await query_data("which postings", explain=True))
    assert planner.operations == ["plan"]
    assert payload["pipeline"]["current_physical_plan_id"] == "a6e02f77"


async def test_a_run_that_did_not_finish_returns_its_status_and_no_answer(
    monkeypatch: pytest.MonkeyPatch,
):
    """A failed run is a successful tool call: the status is what the agent reads."""
    monkeypatch.setattr(config, "get_planner", lambda: FakePlanner(a_run_that_ran_out_of_budget()))
    payload = json.loads(await query_data("which postings"))
    assert payload["status"] == "budget_exhausted"
    assert payload["answer"] is None
    assert payload["failure_reason"]


async def test_the_schema_a_client_reads_describes_every_parameter():
    """A Google-style Args: block never reaches inputSchema, so the descriptions are annotated."""
    tool = next(one for one in await mcp.list_tools() if one.name == "query_data")
    properties = tool.inputSchema["properties"]
    assert set(properties) == {"request", "explain", "include_plan"}
    assert all(one.get("description") for one in properties.values())
