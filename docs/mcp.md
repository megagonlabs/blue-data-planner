# blue-data-planner MCP server

`platform/mcp/` ships `blue-data-planner-mcp`, an MCP server exposing one tool, `query_data`, over
stdio. It plans a request over the registered data sources, runs the plan, repairs what fails, and
returns the rows — the same two operations the library and the CLI offer, reachable by an agent
that speaks MCP. Installation, running, client configuration, and the configuration reference are
in [`platform/mcp/README.md`](../platform/mcp/README.md); this page covers the tool itself.

## Tool reference

### `query_data(request, explain=False, include_plan=False) -> str`

| Parameter | Description |
| --- | --- |
| `request` | The question to answer, in plain language, self-contained. |
| `explain` | Write the plan and stop. No operator runs and no answer comes back. |
| `include_plan` | Return every plan the request produced alongside the answer. `explain` already returns them. |

Each parameter carries its description in the MCP schema, so a client renders it without reading
this page.

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
| `query_data(request, explain=True)` | Present. `status` is `planned`, `answer` is `null`, and the plan is all that comes back |

A run that did not finish still returns the normal payload, with `status` and `failure_reason`
saying why: `budget_exhausted`, `replan_exhausted`, `operator_failed`, `llm_failed`,
`invalid_plan`, or `planner_failed`. `answer` is `null` in that case, never an empty list.

## Errors

Anything the tool body raises — an unreadable registry, a missing API key, an invalid setting —
comes back as `{"error": "<message>"}` in place of the run payload above. The server reports it
as a successful tool result rather than a protocol-level error, so a client tells the two apart
by the `error` key rather than by the protocol's error flag.

An argument that fails the tool's schema is rejected before the tool body runs, so it reaches
the client as a tool error instead.

## Connecting a LangGraph agent

`langchain-mcp-adapters` turns the server's tool into a LangChain tool, so an agent calls it the
way it calls any other.

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient(
    {
        "blue-data-planner": {
            "command": "blue-data-planner-mcp",
            "transport": "stdio",
            "env": {"BLUE_DATA_REGISTRY_PATH": "/srv/blue-data/registry.json"},
        }
    }
)
tools = await client.get_tools()
```

The agent runs the conversation and decides when it needs data; the planner takes one
self-contained request and answers it. A request that takes minutes returns one tool result, so
give the agent a timeout that allows for it.

## Elsewhere

The same two operations are available through:

- the [Python library](api-reference.md), via `DataPlanner.plan` and `DataPlanner.run`, or
- the [`blue-planner` CLI](cli.md), via `data query` with and without `--explain`.
