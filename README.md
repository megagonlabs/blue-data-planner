# blue-data-planner

Operator-agnostic data planners for the Blue **Data Intelligence Layer (DIL)**.

A planner turns a plain-language request into a declarative plan, a DAG of operator calls, then
runs the plan and repairs it where it fails. The operators come from other packages: the Blue
operators and their registry from `blue-data-ops`, semantic operators from LOTUS, and the data
sources and data registry from `blue-data`. Agents and UI are out of scope and come from other Blue
repositories or from frameworks such as LangChain.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP](docs/mcp.md)**

## What it does

- **Plan** a request as a DAG of logical operators, with each `SCAN` naming the catalog entity it
  reads, and search a `blue-data` registry when the tables it needs are not yet known.
- **Push down** as much of the plan as each operator can take. A compound operator such as
  `blue.nl2sql` covers a whole region of the plan in one call; others take one node.
- **Run** each fragment with parameters a model writes, then review the rows before accepting them.
- **Repair** a failure in three ordered steps: the parameter repair, the cover repair, the replan.
- **Combine** operator suites: the deterministic `builtin` suite, LOTUS, and `blue-data-ops`, each
  entering through one adapter.
- **Read back** a run afterwards. Plans, per-fragment results, failure reasons, and every model
  exchange are structured data in one `RunResult`.

See [docs/features.md](docs/features.md) for the full list with snippets, and
[examples/](examples/README.md) for runnable scripts with captured output.

## Repository layout

```text
blue-data-planner/
├── lib/                    # Python library (blue-data-planner)
│   ├── src/blue_data_planner/
│   ├── scripts/            # install and build scripts, demo registry scripts
│   └── tests/              # unit tests and the TAG-Bench harness
├── platform/
│   ├── planner-cli/        # Command-line interface (blue-planner)
│   └── planner-mcp/        # MCP server (blue-data-planner-mcp)
├── docs/                   # User-facing documentation (start at docs/README.md)
├── examples/               # Runnable examples over the demo registry
├── design/                 # Numbered design documents and the decision log
├── data/                   # Demo and benchmark data written by the scripts (git-ignored)
└── example.envrc           # Environment template: copy to .envrc and fill in
```

## Requirements

- Python 3.11 or later; the `lotus` extra needs 3.11 or 3.12
- [`blue-data`](../blue-data) and [`blue-data-ops`](../blue-data-ops) checked out beside this
  repository. The install scripts install both from there.
- An OpenAI API key, since every planning stage calls a model

```bash
export OPENAI_API_KEY=sk-...
```

The CLI and the MCP server read their configuration from the environment. `example.envrc` is a
template: copy it to `.envrc` and load it with `direnv allow` or `source .envrc`.
[docs/installation.md](docs/installation.md) lists every variable and its default.

## Quick start

From the repository root, with the two sibling checkouts in place:

```bash
# Install the library and the CLI
python -m venv .venv && source .venv/bin/activate
bash platform/planner-cli/scripts/install.sh

# Build the Northwind demo registry (no model calls)
python lib/scripts/demo/fetch_northwind.py
python lib/scripts/demo/register_northwind.py

# Point the planner at it
export OPENAI_API_KEY=sk-...                      # a real key, not this placeholder
export BLUE_DATA_REGISTRY_PATH=$PWD/data/demo/registry.json
export BLUE_DATA_PLANNER_MODEL=openai/gpt-6-luna
export BLUE_DATA_PLANNER_OPERATORS=builtin        # builtin alone: the lotus extra is not installed

# Ask a question
blue-planner data query "Which three countries do the most customers come from?"
```

[docs/quickstart.md](docs/quickstart.md) walks through the same steps with the expected output,
shows how to read back what the run did, and runs the same request from Python.

## Packages

| Package | Description |
| --- | --- |
| [lib/](lib/README.md) | `blue-data-planner`: the planner library |
| [platform/planner-cli/](platform/planner-cli/README.md) | `blue-data-planner-cli`: the `blue-planner` command |
| [platform/planner-mcp/](platform/planner-mcp/README.md) | `blue-data-planner-mcp`: the `blue-data-planner-mcp` MCP server, with one tool over the planner |
