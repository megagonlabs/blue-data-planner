# Installation

## To use it

### Components

| Package | Source | Provides |
| --- | --- | --- |
| `blue-data-planner` | [`lib/`](../lib/README.md) | The planner library: `DataPlanner`, `build_data_planner`, and the operator suites |
| `blue-data-planner-cli` | [`platform/planner-cli/`](../platform/planner-cli/README.md) | The `blue-planner` command |
| `blue-data-planner-mcp` | [`platform/planner-mcp/`](../platform/planner-mcp/README.md) | The `blue-data-planner-mcp` server, one tool over the planner |

Install the library first. The CLI package depends on it.
[`lib/README.md`](../lib/README.md) and
[`platform/planner-cli/README.md`](../platform/planner-cli/README.md) are the source of truth for each package's
own install and requirements; this page collects them.

### Requirements

| Requirement | Notes |
| --- | --- |
| Python 3.11 or later | The `lotus` extra narrows this to 3.11 or 3.12 |
| `blue-data` and `blue-data-ops` checked out beside this repository | The install scripts install both, editable, from `../blue-data/lib` and `../blue-data-ops/lib` |
| A model API key | Every planning stage calls a model, a plan-only run included |

### Install

No package is published to an index yet, so all three install from checkouts:

```text
dev/
├── blue-data/
├── blue-data-ops/
└── blue-data-planner/
```

Each package has a `scripts/install.sh`. The library's installs the two siblings and then the
library, and each platform package's runs the library's and then installs itself, so either one
installs without the other.

```bash
python -m venv .venv && source .venv/bin/activate

bash lib/scripts/install.sh            # the library
bash platform/planner-cli/scripts/install.sh   # + the blue-planner command
bash platform/planner-mcp/scripts/install.sh   # + the MCP server
```

### Extras

| Extra | Adds | For |
| --- | --- | --- |
| none | — | The library, with the `builtin` and `blue` operator suites |
| `lotus` | `lotus-ai>=1.2,<2` | The LOTUS suite: semantic filter, projection, extraction, aggregation, top-k, and join |
| `dev` | `pytest`, `ruff`, `pyright` | The quality gates |

```bash
pip install -e './lib[lotus]'
```

### The `lotus` extra needs an environment of its own

LOTUS holds numpy below 2, pandas below 3, and Python to 3.11 or 3.12, and it installs a
machine-learning stack of several gigabytes. Install it on its own rather than beside a project
that needs numpy 2. [`lib/README.md`](../lib/README.md#the-lotus-environment) gives the `uv` form
used inside this repository.

### Environment variables

`OPENAI_API_KEY` is required in every case. The library takes no key of its own, but `litellm`
reads the provider key from the process environment on each call, so a library-only caller sets it
too.

The rest configure the `blue-planner` CLI, which resolves each setting as command option, then
environment variable, then profile, then default, and the MCP server, which reads the environment
alone. A library caller passes the same values to `PlannerSettings` and reads no variable.

| Variable | Default | Used by | Sets |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | None, required | Library, CLI, MCP | The provider key `litellm` reads on each call |
| `BLUE_DATA_REGISTRY_PATH` | None, required | CLI, MCP | The serialized `blue-data` registry to plan against |
| `BLUE_DATA_PLANNER_MODEL` | `BLUE_DATA_LLM_MODEL`, then `openai/gpt-6-luna` | CLI, MCP | The litellm model string every component uses |
| `BLUE_DATA_PLANNER_REASONING_EFFORT` | `low` | CLI, MCP | `minimal`, `low`, `medium`, `high`, `none`, or `off` to leave the parameter out of the call |
| `BLUE_DATA_PLANNER_OPERATORS` | `builtin,lotus` | CLI, MCP | Which suites are registered, over `builtin`, `lotus`, and `blue` |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | The planner's model | CLI, MCP | The model LOTUS runs on |
| `BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` | `none` | CLI, MCP | The reasoning effort LOTUS's own model spends |
| `BLUE_DATA_PLANNER_LOTUS_MAX_ROWS` | `5000` | CLI, MCP | The rows a LOTUS operation accepts before refusing the call |
| `BLUE_DATA_PLANNER_BLUE_MODEL` | The planner's model | CLI, MCP | The model the Blue operations run on |
| `BLUE_DATA_PLANNER_BLUE_REASONING_EFFORT` | `none` | CLI, MCP | The reasoning effort the Blue operations' model spends |
| `BLUE_DATA_LLM_PROVIDER` | `openai` | `blue-data-ops` | The provider every Blue operation's model call is sent to. It has to be the provider serving the Blue model |
| `BLUE_DATA_PLANNER_PROVIDER_TIMEOUT_SECONDS` | `120` | CLI, MCP | The seconds one provider call may take before it fails and is retried |
| `BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS` | `30` | CLI, MCP | The operator calls one request may spend |
| `BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS` | `2` | CLI, MCP | The replans a failure may force |
| `BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS` | `5` | CLI, MCP | The replans a control node may ask for |
| `BLUE_DATA_PLANNER_MAX_WALL_CLOCK_SECONDS` | `600` | CLI, MCP | The seconds a request may run, checked before each step |
| `BLUE_DATA_LLM_MODEL` | `gpt-4o`, which the planner never falls back to | CLI, MCP | The family-wide model. A value set here is used where `BLUE_DATA_PLANNER_MODEL` is unset |
| `BLUE_DATA_CLI_FORMAT` | `table` | CLI | The default output format, `table` or `json` |
| `BLUE_DATA_CLI_LOG_LIMIT` | `80` | CLI | The characters each line of `--log` is cut to |
| `BLUE_DATA_PLANNER_HOME` | `~/.blue-planner` | CLI | The directory holding the CLI's profiles |

`blue-planner profile config` stores the same values in a profile and prompts for the key, so it
never reaches the shell history. An MCP client sets them in the `env` block it launches the server
with ([`platform/planner-mcp/README.md`](../platform/planner-mcp/README.md#configuration-reference)).
[`design/12-deployment-ops.md`](../design/12-deployment-ops.md#4-configuration) is the source of
truth for the variables and their defaults.

## Next steps

- [quickstart.md](quickstart.md): build the demo registry and answer a first question.
- [guide.md](guide.md): use the library from Python, task by task.
- [cli.md](cli.md) and [mcp.md](mcp.md): the two interfaces this page installs.

## To work on it

[`lib/README.md`](../lib/README.md) holds the development setup, the separate LOTUS environment,
the test commands, and the quality gates.
