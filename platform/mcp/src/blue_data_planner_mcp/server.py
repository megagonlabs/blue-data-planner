r"""blue-data-planner MCP server.

Exposes the planner as one tool, so an agent written with LangChain, LangGraph, or any MCP client
can hand over a whole data question and get rows back.

Tools
-----
query_data — plan a question over the registered data sources, run the plan, repair what fails,
             and return the rows. With `explain`, stop before the first operator call.

Quick start
-----------
    # Install, with blue-data and blue-data-ops checked out beside this repository
    bash scripts/install.sh

    # Run over stdio; an agent framework's MCP client launches it as a subprocess
    BLUE_DATA_REGISTRY_PATH=/srv/blue-data/registry.json blue-data-planner-mcp
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from io import TextIOWrapper
from typing import Annotated, Any

import anyio
from blue_data_planner import RunResult
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from pydantic import Field

import blue_data_planner_mcp.config as config

# LiteLLM logs two INFO records per model call, and a request makes dozens of calls. They reach
# the client's stderr, which an agent framework prints to its user's terminal.
LITELLM_LOGGER_NAMES: tuple[str, ...] = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")


class IsolatedStdoutFastMCP(FastMCP[Any]):
    """A FastMCP server whose stdio transport owns stdout, with every other write sent to stderr."""

    async def run_stdio_async(self) -> None:
        # stdout is the transport's channel, and one stray line there is a malformed JSON-RPC
        # message to the client. LiteLLM writes its INFO records to whatever sys.stdout is when
        # they are emitted, so the transport gets a duplicate of the descriptor, and sys.stdout
        # and descriptor 1 both point at stderr for the rest of the process.
        protocol_stdout = anyio.wrap_file(
            TextIOWrapper(os.fdopen(os.dup(sys.stdout.fileno()), "wb"), encoding="utf-8")
        )
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        sys.stdout = sys.stderr
        async with stdio_server(stdout=protocol_stdout) as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream, write_stream, self._mcp_server.create_initialization_options()
            )


mcp = IsolatedStdoutFastMCP(
    "blue-data-planner",
    instructions=(
        "Data planner — takes one self-contained question in plain language, plans it over the "
        "registered data sources, runs the plan, repairs what fails, and returns the rows."
    ),
)


@mcp.tool()
async def query_data(
    request: Annotated[
        str, Field(description="The question to answer, in plain language, self-contained.")
    ],
    explain: Annotated[
        bool,
        Field(
            description=(
                "Write the plan and stop before the first operator call. Catalog searches run; "
                "no operator runs and no answer comes back."
            )
        ),
    ] = False,
    include_plan: Annotated[
        bool,
        Field(
            description=(
                "Return every plan the request produced alongside the answer. `explain` already "
                "returns them."
            )
        ),
    ] = False,
) -> str:
    """Answer a data question over the registered sources, or plan it without running it."""
    # Read through the module rather than binding the name at import time, so a test can
    # replace the planner this process holds.
    try:
        planner = config.get_planner()
        operation = planner.plan if explain else planner.run
        # A request takes minutes. A stdio server that blocks its event loop stops answering
        # pings and cancellations, so the planner runs on a thread of its own.
        result = await asyncio.to_thread(operation, request)
        return _ok(_payload(result, with_plans=explain or include_plan))
    except Exception as error:
        # The one broad catch in this package, and the shape every Blue Data MCP server
        # reports a failure in: a payload rather than a tool error.
        return _err(error)


def _payload(result: RunResult, *, with_plans: bool) -> dict[str, Any]:
    payload = result.to_dict()
    # Every plan version the request produced sits under `pipeline`, so it grows with what the
    # request tried rather than with its answer. An agent pays for that in context on every call.
    if not with_plans:
        del payload["pipeline"]
    return payload


def _ok(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _err(error: Exception) -> str:
    return json.dumps({"error": str(error)}, indent=2)


def quiet_litellm() -> None:
    """Keep LiteLLM's warnings and errors, and drop its per-call INFO and DEBUG records."""
    # A level set before litellm is imported survives the import, which happens on the first
    # query_data call.
    for name in LITELLM_LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> None:
    """Run the server over stdio, the transport a client launches it as a subprocess for."""
    quiet_litellm()
    mcp.run()


if __name__ == "__main__":
    main()
