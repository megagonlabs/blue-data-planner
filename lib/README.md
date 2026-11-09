# blue-data-planner

The planner library. It turns a request in plain language into a plan over operators, runs the
plan, and repairs what fails.

## Requirements

- Python 3.12 or 3.13; 3.12 only with the `lotus` extra
- `blue-data` and `blue-data-ops` checked out beside this repository, as `../blue-data` and
  `../blue-data-ops`: every install script installs both from there
- A model API key, which every planning stage needs
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), for every command in this
  repository other than the `pip` install above

## Install

```bash
python -m venv .venv && source .venv/bin/activate
bash scripts/install.sh     # ../blue-data, ../blue-data-ops, then this library with its dev extra
pip install -e '.[lotus]'   # + the LOTUS operator suite
```

The `lotus` extra constrains the environment around it: numpy below 2, pandas below 3, Python 3.12
only, and a machine-learning stack of several gigabytes. Install it on its own rather than
beside a project that needs numpy 2.

[docs/installation.md](../docs/installation.md) covers installing the `blue-planner` CLI beside it.

## Usage

`build_data_planner` assembles a planner over a `blue-data` registry, one model, and the operator
suites to register.

```python
from pathlib import Path

from blue_data_planner import RunLimits, build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.adapters.lotus import LotusSuite
from blue_data_planner.operators.builtin import BuiltinSuite

planner = build_data_planner(
    open_registry(Path("data/demo/registry.json")),
    suites=[BuiltinSuite(), LotusSuite()],
    model="openai/gpt-6-luna",
    limits=RunLimits(),
)

result = planner.run("Which country has the most customers, and how many does it have?")
print(result.status)  # "ok" where the request was answered
print(result.answer)  # the rows the plan's RETURN named, or None where the run did not finish
```

`LotusSuite` registers nothing where LOTUS is not installed. `planner.plan(request)` plans,
searches the catalog, and covers as `run` does, then stops before the first operator call.

## Development setup

Every command runs from `lib/`. `[tool.uv.sources]` points at the two sibling checkouts, so `uv`
installs them and this package in editable mode.

```bash
uv sync --extra dev
```

### The LOTUS environment

`uv.lock` resolves the `lotus` extra as a separate fork, so the extra's narrower numpy and pandas
bounds never reach the default `.venv`. Build it beside the default environment:

```bash
UV_PROJECT_ENVIRONMENT=.venv-lotus uv sync --extra lotus --extra dev
```

Every later `uv run` needs that same variable and the same extras. uv reads both fresh each time
and re-syncs the environment to whatever the command names, so a plain `uv run` strips LOTUS back
out of `.venv-lotus`. Export the variable for the session:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-lotus
uv run --extra lotus --extra dev pytest tests/lotus
```

Set the variable and the extras together, or neither: without the variable, that command syncs
LOTUS and its narrower bounds into the default `.venv` instead.

`uv sync --all-extras --all-groups` is not a valid combination here: the conflict declaration in
[pyproject.toml](pyproject.toml) forbids selecting the `lotus` extra and its paired placeholder
group at once.

## Running tests

```bash
uv run pytest
```

That is the unit tier: fast, offline, and needing no API key. The LOTUS tier runs against an
installed suite and is excluded from the default run:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-lotus
uv run --extra lotus --extra dev pytest tests/lotus
```

Tests in that tier that reach a model skip themselves without `OPENAI_API_KEY`.

### Quality gates

All four pass before work is reported as complete.

| Gate | Command |
| --- | --- |
| Format | `uv run ruff format .` |
| Lint | `uv run ruff check .` |
| Types | `uv run pyright` |
| Tests | `uv run pytest` |

## Documentation

- [Installation](../docs/installation.md) — components, extras, and environment variables
- [`blue-planner` CLI](../docs/cli.md) — commands, profiles, output, and exit codes
- [MCP server](../docs/mcp.md) — the `query_data` tool, its response, and its errors
- [TAG-Bench](../docs/tag-bench-benchmark.md) — the benchmark harness
- [`design/`](../design/) — the design set, starting with
  [04-detailed-architecture.md](../design/04-detailed-architecture.md)

## Source layout

```text
lib/
├── src/blue_data_planner/
│   ├── data_planner.py      # DataPlanner, build_data_planner: the entry point
│   ├── run_state.py         # the state the run loop writes
│   ├── logical_planner.py   # stage 1: what the request asks for
│   ├── physical_planner.py  # stage 2: which operator covers which nodes
│   ├── fragment_runner.py   # stage 3: the parameters a call runs with, and the review
│   ├── catalog_searcher.py  # the catalog search behind a DISCOVER node
│   ├── prompts/             # the prompt text each stage sends
│   ├── types/               # every data class crossing a module boundary
│   ├── plan/                # plan validation and fragment enumeration
│   ├── execution/           # the operator call site and the answer projection
│   ├── operators/           # the router, the builtin suite, one adapter per outside suite
│   ├── data/                # the only boundary onto blue-data
│   └── llm/                 # the model client
├── scripts/demo/            # builds the demo registry the examples run against
└── tests/
    ├── unit/                # the default tier
    ├── benchmarks/          # TAG-Bench preprocessing and the benchmark runner, in tag_bench/
    ├── end_to_end/          # a full run against scripted model responses
    ├── lotus/               # opt-in by path, needing the lotus extra
    ├── servers/             # opt-in by path, needing running MySQL, PostgreSQL, or Neo4j servers
    ├── doubles/             # in-memory stand-ins for the I/O boundaries
    ├── stubs/               # stubs for heavy optional dependencies
    └── fixtures/            # recorded inputs
```
