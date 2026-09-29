"""A LangChain agent that pulls tables from blue-data-planner over MCP and writes a report.

The agent gets one tool, `query_data`, from the blue-data-planner-mcp server, which it launches
as a subprocess over stdio. Given a reporting task, the agent decides which data questions to ask,
sends each one to the planner, and writes a short Markdown report from the rows that come back.
The planner answers each question over the Northwind demo registry; the agent never sees a
table or a query language, only questions and rows.

`create_agent` builds a LangGraph graph, so the run is streamed step by step: each tool call is
printed as the agent makes it, and the report is printed at the end.

Run, from the repository root:
    bash platform/planner-mcp/scripts/install.sh
    pip install langchain langchain-openai langchain-mcp-adapters
    export OPENAI_API_KEY=sk-...
    python examples/framework-demos/langgraph_mcp_demo.py

The registry comes from `lib/scripts/demo/register_northwind.py` (see examples/README.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import dotenv
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "demo" / "registry.json"
SERVER_COMMAND = "blue-data-planner-mcp"
AGENT_MODEL = "gpt-5.6-luna"

SYSTEM_PROMPT = """\
You are a sales analyst at Northwind Traders, a food wholesaler. You reach the company's data
only through the query_data tool. Each call takes one self-contained question in plain English
and returns JSON whose "answer" holds the rows.

Ask one simple question per call: a count, a sum of an existing column, or a top-N list. Do not
ask for values computed from several columns, such as revenue from price and quantity.

When you have what you need, write the report in Markdown: a title, three to five bullet
findings with numbers, and one small table. Every number must come from a tool result.
"""

TASK = (
    "Write a one-page brief on Northwind's orders: which customers order most often, which "
    "products sell the most units, and which countries the orders ship to."
)


async def main() -> None:
    dotenv.load_dotenv(override=True)
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; the agent and the planner both call a model")
    if shutil.which(SERVER_COMMAND) is None:
        raise SystemExit(f"{SERVER_COMMAND} is not on PATH; run platform/planner-mcp/scripts/install.sh")
    if not REGISTRY_PATH.is_file():
        raise SystemExit(f"{REGISTRY_PATH} is missing; build it as examples/README.md describes")

    client = MultiServerMCPClient(
        {
            "blue-data-planner": {
                "transport": "stdio",
                "command": SERVER_COMMAND,
                "args": [],
                "env": {
                    **os.environ,
                    "BLUE_DATA_REGISTRY_PATH": str(REGISTRY_PATH),
                    "BLUE_DATA_PLANNER_OPERATORS": "builtin",
                },
            }
        }
    )

    # One session for the whole run. The client's default opens a session, and so starts a
    # server and builds a planner, for every tool call.
    async with client.session("blue-data-planner") as session:
        tools = await load_mcp_tools(session)
        print(f"Tools from {SERVER_COMMAND}: {[tool.name for tool in tools]}\n")
        print(f"Task: {TASK}\n")

        # gpt-5.6-luna takes function tools with reasoning only through the Responses API.
        model = ChatOpenAI(model=AGENT_MODEL, use_responses_api=True)
        agent = create_agent(model, tools, system_prompt=SYSTEM_PROMPT)
        report = ""
        async for update in agent.astream(
            {"messages": [{"role": "user", "content": TASK}]}, stream_mode="updates"
        ):
            for step in update.values():
                for message in step.get("messages", []):
                    report = print_step(message) or report

    print("\n" + "=" * 72 + "\n")
    print(report)


def print_step(message: Any) -> str | None:
    """Print one tool call or tool result, returning the text of a final answer."""
    if isinstance(message, AIMessage):
        for call in message.tool_calls:
            print(f"-> {call['name']}: {call['args'].get('request')}")
        return None if message.tool_calls else message.text
    if isinstance(message, ToolMessage):
        payload = json.loads(tool_text(message))
        if "error" in payload:
            print(f"   error: {payload['error']}")
        else:
            rows = payload["answer"][0] if payload["answer"] else []
            print(f"   {payload['status']}, {len(rows)} rows: {rows[:3]}")
    return None


def tool_text(message: ToolMessage) -> str:
    """Return the text a tool result carries, whether stored as a string or as content blocks."""
    if isinstance(message.content, str):
        return message.content
    return "".join(
        block["text"] for block in message.content if isinstance(block, dict) and "text" in block
    )


if __name__ == "__main__":
    asyncio.run(main())
