# blue-data-planner-mcp

`blue-data-planner-mcp` is the MCP server over `blue-data-planner`. It exposes one tool,
`query_data`, so an agent written with LangChain, LangGraph, or any MCP client can hand over a whole
data question and get rows back, over stdio. It is read-only with respect to the registry: the tool
plans and runs a request, and writes nothing the registry holds.

## Install

```bash
cd platform/mcp
pip install -e ../../lib       # the library this server adapts
pip install -e .               # the server itself
```

`scripts/install.sh` runs the same two commands.

The default operator suites (`BLUE_DATA_PLANNER_OPERATORS`) include `lotus`, and LOTUS is not among
the library's runtime dependencies: it comes in only through the library's `lotus` extra. See
[`lib/README.md`](../../lib/README.md)'s Install section for the exact command and the environment
constraints LOTUS brings with it.

## Running

```bash
BLUE_DATA_REGISTRY_PATH=/srv/blue-data/registry.json blue-data-planner-mcp
```

The server speaks the stdio transport, so a client launches it as a subprocess rather than
connecting to it as a running service.

## Client configuration

Add it to Claude Desktop's configuration file —
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
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
```

If `blue-data-planner-mcp` is not on the client's `PATH`, give the full path to the console script
instead:

```json
{
  "mcpServers": {
    "blue-data-planner": {
      "command": "/path/to/venv/bin/blue-data-planner-mcp",
      "env": {
        "BLUE_DATA_REGISTRY_PATH": "/srv/blue-data/registry.json",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

Any MCP client that launches a stdio server and sets its `env` block works the same way.

## The tool

### `query_data`

| Parameter | Type | Description |
| --- | --- | --- |
| `request` | `str` | The question to answer, in plain language, self-contained. |
| `explain` | `bool` | Write the plan and stop. No operator runs and no answer comes back. Default `False`. |
| `include_plan` | `bool` | Return every plan the request produced alongside the answer. `explain` already returns them. Default `False`. |

`query_data` reaches `DataPlanner.run`, or `DataPlanner.plan` when `explain` is set, and returns the
run's JSON form as a string. `pipeline` is left out unless `include_plan` or `explain` asked for
it. [`docs/mcp.md`](../../docs/mcp.md) covers the response keys and the error payload.

```json
{
  "status": "ok",
  "answer": [
    [
      {"title": "Senior Data Scientist", "company": "Corvus Health", "location": "San Francisco, CA"}
    ]
  ],
  "failure_reason": null,
  "results": {
    "4b9e0c7a…": {
      "status": "ok",
      "failure_reason": null,
      "operator_id": "lotus.sem_filter",
      "row_count": 1,
      "output": [
        {
          "row_count": 1,
          "columns": ["company", "location", "title"],
          "sample_rows": [
            {"title": "Senior Data Scientist", "company": "Corvus Health", "location": "San Francisco, CA"}
          ]
        }
      ]
    }
  },
  "counters": {"operator_calls": 4, "failure_replans": 0, "control_replans": 0, "limits": {"...": "..."}},
  "conversations": {"calls": 6, "cost": 0.11}
}
```

Each `results` key is shortened here; a full one is a sha256 signature of the work behind one
node. A client reads an entry by iterating, since there is no fragment id to index by.

An error the server catches — an unreadable registry, a missing API key, an invalid setting —
returns `{"error": "<message>"}` instead.

## Configuration reference

The server reads the environment and nothing else. Every variable is the CLI's, so a value means
the same thing to both.

| Variable | Field it fills | Default |
| --- | --- | --- |
| `BLUE_DATA_REGISTRY_PATH` | `registry_path` | None, required |
| `OPENAI_API_KEY` | — | None, required for the default model |
| `BLUE_DATA_PLANNER_MODEL`, else `BLUE_DATA_LLM_MODEL` | `model` | `openai/gpt-5.6-luna` |
| `BLUE_DATA_PLANNER_REASONING_EFFORT` | `reasoning_effort` | `low` |
| `BLUE_DATA_PLANNER_OPERATORS` | `operator_suites`, comma separated | `builtin,lotus` |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | `lotus_model` | Inherits `BLUE_DATA_PLANNER_MODEL` |
| `BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` | `lotus_reasoning_effort` | `none` |
| `BLUE_DATA_PLANNER_LOTUS_MAX_ROWS` | `lotus_max_rows` | `5000` |
| `BLUE_DATA_PLANNER_ENABLE_NL2TABLE` | `enable_nl2table` | `false` |
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
one — sitting above the `env` block the client passed on purpose. The server also reads no CLI
profile: `blue-planner`'s `~/.blue-planner/.profiles` never reaches it, so a developer's profile
cannot silently reconfigure a server already running for a client.

The planner is built on the first `query_data` call and held for the process lifetime. A server
that starts is not yet a server that can answer: an unreachable registry or a missing API key
surfaces as an error payload on that first call, not at startup.

## Use it elsewhere

The same two operations, plan and run, are reachable through
[the Python library](../../docs/api-reference.md), via `DataPlanner.plan` and `DataPlanner.run`,
and through [the `blue-planner` CLI](../cli/README.md), via `data query` with and without
`--explain`.

See [`docs/mcp.md`](../../docs/mcp.md) for the full tool reference and a LangGraph connection
example.
