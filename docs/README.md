# Documentation

## Start here

| Doc | Read this to... |
|---|---|
| [quickstart.md](quickstart.md) | Go from a clean checkout to an answered question |
| [installation.md](installation.md) | Install the library and the CLI, with every extra and environment variable |
| [features.md](features.md) | See what the planner does, one section per capability with a snippet |
| [examples/](../examples/README.md) | Run something: five scripts with captured output |

## Using it

| Doc | Read this to... |
|---|---|
| [guide.md](guide.md) | Work through the library task by task, from assembling a planner to reading a run back |
| [api-reference.md](api-reference.md) | Look up an exact signature, parameter, default, or result type |
| [cli.md](cli.md) | Use the `blue-planner` command: every command, profiles, output formats, exit codes |
| [mcp.md](mcp.md) | Call the planner from an agent: the `query_data` tool, its response, and its errors |
| [api.md](api.md) | REST API — reserved, not in the first release |

## Building on it

| Doc | Read this to... |
|---|---|
| [architecture.md](architecture.md) | Understand the three stages, the repairs, the dependency rules, and how a run flows |
| [extending.md](extending.md) | Add an operator suite, register operators, or swap the model provider |

## Measuring it

| Doc | Read this to... |
|---|---|
| [tag-bench-benchmark.md](tag-bench-benchmark.md) | Build the TAG-Bench inputs, run the benchmark, and read a report |

## Something's out of date

These pages describe what the code does, not what is planned. A page that has drifted from the code
is a bug in the page. The code is the source of truth, along with:

- [`lib/README.md`](../lib/README.md) for the development setup and the test commands
- [`platform/planner-cli/README.md`](../platform/planner-cli/README.md) for the CLI, which
  [cli.md](cli.md) forwards to
- [`platform/planner-mcp/README.md`](../platform/planner-mcp/README.md) for installing, running, and
  configuring the MCP server
