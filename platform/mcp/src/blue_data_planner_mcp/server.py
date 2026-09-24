r"""blue-data-planner MCP server.

Exposes the planner as one tool, so an agent written with LangChain, LangGraph, or any MCP client
can hand over a whole data question and get rows back.

Tools
-----
query_data — plan a question over the registered data sources, run the plan, repair what fails,
             and return the rows. With `explain`, write the plan and stop.

Quick start
-----------
    # Install
    pip install -e ../../lib
    pip install -e .

    # Run (stdio transport for Claude Desktop / Claude Code / a LangGraph client)
    BLUE_DATA_REGISTRY_PATH=/srv/blue-data/registry.json blue-data-planner-mcp

Claude Desktop config — ~/Library/Application Support/Claude/claude_desktop_config.json on
macOS, %APPDATA%\Claude\claude_desktop_config.json on Windows:
    {
      "mcpServers": {
        "blue-data-planner": {
          "command": "blue-data-planner-mcp",
          "env": {
            "BLUE_DATA_REGISTRY_PATH": "/srv/blue-data/registry.json",
            "OPENAI_API_KEY": "sk-..."
          }
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from blue_data_planner import RunResult
from mcp.server.fastmcp import FastMCP
from pydantic import Field

import blue_data_planner_mcp.config as config

mcp = FastMCP(
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
        Field(description="Write the plan and stop. No operator runs and no answer comes back."),
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


def main() -> None:
    """Run the server over stdio, the transport a client launches it as a subprocess for."""
    mcp.run()


if __name__ == "__main__":
    main()
