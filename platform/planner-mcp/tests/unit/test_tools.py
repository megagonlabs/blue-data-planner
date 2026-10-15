"""What one tool call returns, and what it leaves out."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import threading

import pytest
from blue_data_planner.types import RegistryUnreadableError, UnknownOperatorSuiteError

import blue_data_planner_mcp.config as config
from blue_data_planner_mcp.server import mcp, query_data
from tests.doubles.planner import (
    FakePlanner,
    PlannerWaitingForCancel,
    a_run_that_answered,
    a_run_that_ran_out_of_budget,
)


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


def test_the_server_reports_the_installed_package_version_to_a_client():
    """A client reads serverInfo.version from the initialization options, not the SDK version."""
    options = mcp._mcp_server.create_initialization_options()  # pyright: ignore[reportPrivateUsage]
    assert options.server_version == importlib.metadata.version("blue-data-planner-mcp")


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (
            RegistryUnreadableError("no registry file at /srv/registry.json"),
            {"error": "no registry file at /srv/registry.json", "code": "REGISTRY_UNREADABLE"},
        ),
        (
            UnknownOperatorSuiteError("no operator suite named 'blu'"),
            {"error": "no operator suite named 'blu'", "code": "CONFIG_INVALID"},
        ),
        (
            RuntimeError("disk gone"),
            {"error": "RuntimeError: disk gone", "code": "INTERNAL_ERROR"},
        ),
    ],
)
async def test_a_failure_the_server_meets_returns_an_error_payload_carrying_its_code(
    monkeypatch: pytest.MonkeyPatch, raised: Exception, expected: dict[str, str]
):
    """A tool error would end the call; the agent is told what went wrong, and why, instead."""

    def refuse() -> FakePlanner:
        raise raised

    monkeypatch.setattr(config, "get_planner", refuse)
    payload = json.loads(await query_data("which postings"))
    assert payload == expected


async def test_the_planner_runs_off_the_event_loop_thread(planner: FakePlanner):
    """A run takes minutes, and a blocked stdio loop stops answering pings and cancellations."""
    await query_data("which postings")
    assert planner.thread_ids != [threading.get_ident()]


async def test_cancelling_a_call_tells_its_run_to_stop(monkeypatch: pytest.MonkeyPatch):
    """The thread cannot be stopped from outside, so the run is handed an event it checks."""
    planner = PlannerWaitingForCancel(a_run_that_answered())
    monkeypatch.setattr(config, "get_planner", lambda: planner)

    call = asyncio.create_task(query_data("which postings"))
    assert await asyncio.to_thread(planner.started.wait, 5)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert await asyncio.to_thread(planner.saw_cancel.wait, 5)
