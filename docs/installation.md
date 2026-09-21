# Installation

## To use it

### Components

| Package | Source | Provides |
| --- | --- | --- |
| `blue-data-planner` | [`lib/`](../lib/README.md) | The planner library: `DataPlanner`, `build_data_planner`, and the operator suites |
| `blue-data-planner-cli` | [`platform/cli/`](../platform/cli/README.md) | The `blue-planner` command |
| `blue-data-planner-mcp` | `platform/mcp/` | Not built. See [mcp.md](mcp.md) |

Install the library first. The CLI package depends on it.
[`lib/README.md`](../lib/README.md) and
[`platform/cli/README.md`](../platform/cli/README.md) are the source of truth for each package's
own install and requirements; this page collects them.

### Requirements

| Requirement | Notes |
| --- | --- |
| Python 3.11 or later | The `lotus` extra narrows this to 3.11 or 3.12 |
| SSH access to `github.com/megagon-internal` | `blue-data` and `blue-data-ops` are declared as git sources in [`lib/pyproject.toml`](../lib/pyproject.toml) and cloned over SSH |
| A model API key | Every planning stage calls a model, a plan-only run included |

### Install

Neither package is published to an index yet, so both install from a checkout.

```bash
python -m venv .venv && source .venv/bin/activate

cd lib && pip install -e .                 # the library
cd ../platform/cli && pip install -e .     # the blue-planner command
```

### Extras

| Extra | Adds | For |
| --- | --- | --- |
| none | — | The library, with the `builtin` and `blue` operator suites |
| `lotus` | `lotus-ai>=1.2,<2` | The LOTUS suite: semantic filter, projection, extraction, aggregation, top-k, and join |
| `dev` | `pytest`, `ruff`, `pyright`, `diskcache` | The quality gates |

```bash
pip install -e '.[lotus]'
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
environment variable, then profile, then default.

| Variable | Default | Sets |
| --- | --- | --- |
| `BLUE_DATA_REGISTRY_PATH` | None, required | The serialized `blue-data` registry to plan against |
| `BLUE_DATA_PLANNER_MODEL` | `BLUE_DATA_LLM_MODEL`, then `openai/gpt-5.6-luna` | The litellm model string every component uses |
| `BLUE_DATA_PLANNER_OPERATORS` | `builtin,lotus` | Which suites are registered, over `builtin`, `lotus`, and `blue` |

`blue-planner profile config` stores the same values in a profile and prompts for the key, so it
never reaches the shell history. The remaining parameters (LOTUS settings, run bounds, output
format) are in [`platform/cli/README.md`](../platform/cli/README.md#parameters) and
[`design/09-api-interface-spec.md`](../design/09-api-interface-spec.md#configuration).

## To work on it

[`lib/README.md`](../lib/README.md) holds the development setup, the separate LOTUS environment,
the test commands, and the quality gates.
