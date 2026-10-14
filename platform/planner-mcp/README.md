# blue-data-planner-mcp

`blue-data-planner-mcp` is the MCP server over `blue-data-planner`. It exposes one tool,
`query_data`, so an agent written with LangChain, LangGraph, or any MCP client can hand over a whole
data question and get rows back, over stdio. It is read-only with respect to the registry: the tool
plans and runs a request, and writes nothing the registry holds.

## Install

```bash
cd platform/planner-mcp
python -m venv .venv && source .venv/bin/activate
bash scripts/install.sh        # ../../../blue-data, ../../../blue-data-ops, the library, the server
```

`blue-data` and `blue-data-ops` are checked out beside this repository
([installation.md](../../docs/installation.md)).

The install does not include LOTUS. The default operator suites (`BLUE_DATA_PLANNER_OPERATORS`)
name `lotus`, and the LOTUS suite registers no operators where LOTUS is not installed, so the
server plans with `builtin` alone. Set `BLUE_DATA_PLANNER_OPERATORS=builtin` to say so
explicitly.

## Running

From the repository root, over the demo registry
([quickstart.md](../../docs/quickstart.md#2-build-the-demo-registry) builds it):

```bash
BLUE_DATA_REGISTRY_PATH=$PWD/data/demo/registry.json blue-data-planner-mcp
```

The server speaks the stdio transport, so a client launches it as a subprocess rather than
connecting to it as a running service. stdout carries the protocol and nothing else: anything else
the process writes there goes to stderr. LiteLLM's logging is held at WARNING, so its per-call INFO
records do not reach a client's terminal.

## Launching it from an agent framework

An agent framework's MCP client starts the server as a subprocess and sets its environment. With
LangChain's `langchain-mcp-adapters`:

```python
import os
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

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
```

If `blue-data-planner-mcp` is not on the `PATH` the client runs with, give the full path to the
console script as `command`, such as `/path/to/venv/bin/blue-data-planner-mcp`. Any MCP client that
launches a stdio server and sets its environment works the same way.
[`docs/mcp.md`](../../docs/mcp.md#connecting-a-langchain-agent) builds an agent over it, and
[`examples/framework-demos/langgraph_mcp_demo.py`](../../examples/framework-demos/langgraph_mcp_demo.py)
runs one.

## The tool

### `query_data`

| Parameter | Type | Description |
| --- | --- | --- |
| `request` | `str` | The question to answer, in plain language, self-contained. |
| `explain` | `bool` | Write the plan and stop before the first operator call. Catalog searches run; no operator runs and no answer comes back. Default `False`. |
| `include_plan` | `bool` | Return every plan the request produced alongside the answer. `explain` already returns them. Default `False`. |

`query_data` reaches `DataPlanner.run`, or `DataPlanner.plan` when `explain` is set, and returns the
run's JSON form as a string. `pipeline` is left out unless `include_plan` or `explain` asked for
it. [`docs/mcp.md`](../../docs/mcp.md) covers the response keys and the error payload.

```json
{
  "status": "ok",
  "answer": [
    [
      {"country": "Mexico", "customer_count": 3},
      {"country": "Germany", "customer_count": 2},
      {"country": "UK", "customer_count": 2}
    ]
  ],
  "failure_reason": null,
  "results": {
    "9d2f61b4…": {
      "search_terms": ["customers"],
      "matches": [
        {
          "path": "/source/northwind/database/northwind/collection/public/entity/customers",
          "name": "customers",
          "search_term": "customers",
          "...": "..."
        }
      ],
      "dropped_terms": 0
    },
    "4b9e0c7a…": {
      "status": "ok",
      "failure_reason": null,
      "operator_id": "builtin.sort_superlative",
      "row_count": 3,
      "error": null,
      "report": null,
      "output": [
        {
          "row_count": 3,
          "columns": ["country", "customer_count"],
          "sample_rows": [{"country": "Mexico", "customer_count": 3}, "..."]
        }
      ],
      "attempts": ["..."]
    }
  },
  "counters": {
    "operator_calls": 3, "failure_replans": 0, "control_replans": 0, "limits": {"...": "..."}
  },
  "conversations": {"calls": 12, "cost": 0.0}
}
```

Each `results` key is shortened here; a full one is a sha256 signature of the work behind one
node. A client reads an entry by iterating, since there is no fragment id to index by. An entry
carrying `search_terms` is what a `DISCOVER` node's registry search found; an entry carrying
`status` is how one fragment ended, with its rows as a table summary.

An error the server catches — an unreadable registry, an unknown operator suite name, an invalid
setting — returns `{"error": "<message>", "code": "<CODE>"}` instead. `code` is
`REGISTRY_UNREADABLE`, `CONFIG_INVALID`, or `INTERNAL_ERROR`, the same codes the `blue-planner`
CLI prints.

## Configuration reference

The server reads the environment and nothing else. Every variable is the CLI's, so a value means
the same thing to both.

| Variable | Field it fills | Default |
| --- | --- | --- |
| `BLUE_DATA_REGISTRY_PATH` | `registry_path` | None, required |
| `OPENAI_API_KEY` | — | None, required for the default model |
| `BLUE_DATA_PLANNER_MODEL`, else `BLUE_DATA_LLM_MODEL` | `model` | `openai/gpt-6-luna` |
| `BLUE_DATA_PLANNER_REASONING_EFFORT` | `reasoning_effort` | `low` |
| `BLUE_DATA_PLANNER_OPERATORS` | `operator_suites`, comma separated | `builtin,lotus` |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | `lotus_model` | Unset: LOTUS uses the planner's model |
| `BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` | `lotus_reasoning_effort` | `none` |
| `BLUE_DATA_PLANNER_LOTUS_MAX_ROWS` | `lotus_max_rows` | `5000` |
| `BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS` | `limits.operator_calls` | `30` |
| `BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS` | `limits.failure_replans` | `2` |
| `BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS` | `limits.control_replans` | `5` |

`OPENAI_API_KEY` fills no settings field: litellm reads it straight from the process environment.
A model from another provider takes that provider's own key variable instead.

A boolean variable accepts `1`, `true`, `yes`, `y`, or `on` for true, and `0`, `false`, `no`, `n`, or
`off` for false, matching `blue_data.utils.config`. Either reasoning effort takes `minimal`, `low`,
`medium`, `high`, `none`, or `off`; anything else is refused on the first call.

`BLUE_DATA_CLI_FORMAT` and `BLUE_DATA_CLI_LOG_LIMIT` are the CLI's alone: an MCP tool's wire format
is JSON by protocol, so a format option has nothing to choose.

## Architecture

There is no configuration file. A client launches this server as a subprocess with a working
directory it did not choose, so a file loaded from disk would either find nothing or find the wrong
one, and would take precedence over the `env` block the client passed on purpose. The server also
reads no CLI profile: `blue-planner`'s `~/.blue-planner/.profiles` never reaches it, so a
developer's profile cannot silently reconfigure a server already running for a client.

The planner is built on the first `query_data` call and held for the process lifetime. An
unreachable registry therefore returns an error payload on that first call rather than stopping the
server at startup.

## Use it elsewhere

The same two operations, plan and run, are reachable through
[the Python library](../../docs/api-reference.md), via `DataPlanner.plan` and `DataPlanner.run`,
and through [the `blue-planner` CLI](../planner-cli/README.md), via `data query` with and without
`--explain`.

See [`docs/mcp.md`](../../docs/mcp.md) for the full tool reference and a LangChain connection
example, and
[`examples/framework-demos/langgraph_mcp_demo.py`](../../examples/framework-demos/langgraph_mcp_demo.py)
for a runnable agent.

## Running tests

```bash
cd platform/planner-mcp
python -m pytest
```

`install.sh` installs `pytest` with the server. The tests replace the planner with a double, so
they run offline and need no API key.
