# blue-data-planner MCP server

`platform/mcp/` ships `blue-data-planner-mcp`, an MCP server exposing one tool, `query_data`, over
stdio. It plans a request over the registered data sources, runs the plan, repairs what fails, and
returns the rows — the same two operations the library and the CLI offer, reachable by an agent
that speaks MCP. Installation, running, launching it from an agent framework, and the
configuration reference are in [`platform/mcp/README.md`](../platform/mcp/README.md); this page
covers the tool itself.

## Tool reference

### `query_data(request, explain=False, include_plan=False) -> str`

| Parameter | Description |
| --- | --- |
| `request` | The question to answer, in plain language, self-contained. |
| `explain` | Write the plan and stop before the first operator call. Catalog searches run; no operator runs and no answer comes back. |
| `include_plan` | Return every plan the request produced alongside the answer. `explain` already returns them. |

Each parameter carries its description in the MCP schema, so a client renders it without reading
this page. The schema in `platform/mcp/src/blue_data_planner_mcp/server.py` is the source of truth
for these descriptions; this table and the one in
[`platform/mcp/README.md`](../platform/mcp/README.md#the-tool) copy it.

## The response

The return value is `RunResult.to_dict()`, serialized as one JSON string. `status`, `answer`,
`failure_reason`, `results`, `counters`, and `conversations` travel on every call.

`results` is keyed by a signature of the work behind one node, a sha256 hex digest rather than a
fragment id. Each entry is either how that fragment ended — `status`, `failure_reason`,
`operator_id`, `row_count`, and an `output` list of `{row_count, columns, sample_rows}` table
summaries — or what a catalog search found: `search_terms`, `matches`, and `dropped_terms`.

`pipeline` — every logical and physical plan the request produced — travels only when it was asked
for, because it grows with the retries a request took rather than with its answer:

| Call | `pipeline` |
| --- | --- |
| `query_data(request)` | Left out |
| `query_data(request, include_plan=True)` | Present |
| `query_data(request, explain=True)` | Present. `status` is `planned`, or `invalid_plan`, `replan_exhausted`, `llm_failed`, or `planner_failed` when no plan could be written; `answer` is `null`, and the plan is all that comes back |

A run that did not finish still returns the normal payload, with `status` and `failure_reason`
saying why: `budget_exhausted`, `replan_exhausted`, `operator_failed`, `llm_failed`,
`invalid_plan`, or `planner_failed`. `answer` is `null` in that case, never an empty list.

## Errors

Anything the tool body raises — an unreadable registry, an unknown operator suite name, an invalid
setting — comes back as `{"error": "<message>", "code": "<CODE>"}` in place of the run payload
above. The server reports it as a successful tool result rather than a protocol-level error, so a
client tells the two apart by the `error` key rather than by the protocol's error flag.

| `code` | Meaning |
| --- | --- |
| `REGISTRY_UNREADABLE` | The registry path does not exist or does not parse |
| `CONFIG_INVALID` | A setting has no value, or a value that cannot be used, such as an unknown operator suite |
| `INTERNAL_ERROR` | Anything else. The message names the exception's type |

These are the codes the `blue-planner` CLI prints, so a client that reads both branches on one set.

An argument that fails the tool's schema is rejected before the tool body runs, so it reaches
the client as a tool error instead.

## Connecting a LangChain agent

`langchain-mcp-adapters` turns the server's tool into a LangChain tool, so an agent built with
`create_agent`, which runs on LangGraph, calls it the way it calls any other.

```python
import asyncio
import os
from pathlib import Path

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI

# The demo registry, resolved from the repository root.
REGISTRY = str(Path("data/demo/registry.json").resolve())

client = MultiServerMCPClient(
    {
        "blue-data-planner": {
            "transport": "stdio",
            "command": "blue-data-planner-mcp",
            "args": [],
            "env": {**os.environ, "BLUE_DATA_REGISTRY_PATH": REGISTRY},
        }
    }
)


async def main() -> None:
    async with client.session("blue-data-planner") as session:
        tools = await load_mcp_tools(session)
        agent = create_agent(ChatOpenAI(model="gpt-5.6-luna"), tools)
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "Which countries do our customers come from?"}]}
        )
        print(result["messages"][-1].content)


asyncio.run(main())
```

Hold one session for the agent's run. Without one, `MultiServerMCPClient` opens a session per tool
call, and over stdio that starts a new server, which builds a new planner, every time. Pass the
parent environment on in `env`, since the server reads the provider key from it.

The agent runs the conversation and decides when it needs data; the planner takes one
self-contained request and answers it. A request that takes minutes returns one tool result, so
give the agent a timeout that allows for it.
[`examples/framework-demos/langgraph_mcp_demo.py`](../examples/framework-demos/langgraph_mcp_demo.py)
is a runnable agent that asks several questions and writes a report from the rows.

## Elsewhere

The same two operations are available through:

- the [Python library](api-reference.md), via `DataPlanner.plan` and `DataPlanner.run`, or
- the [`blue-planner` CLI](cli.md), via `data query` with and without `--explain`.
