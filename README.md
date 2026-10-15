# blue-data-planner

Operator-agnostic data planners for the Blue **Data Intelligence Layer (DIL)**.

A planner turns a plain-language request into a declarative plan (a DAG of operator calls), runs
the plan, and repairs it when a call fails. The planner depends on `blue-data-ops` for the Blue
operators and their registry, and on `blue-data` for data sources and the data registry.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP](docs/mcp.md)**

## What it does

- **Plan** a request as a DAG of logical operators. Each `SCAN` names the catalog entity it reads,
  and the planner searches a `blue-data` registry when the request does not name its tables.
- **Push down** as much of the plan as each operator can take. A compound operator such as
  `blue.nl2sql` runs several nodes in one call, while other operators take one node each.
- **Run** each fragment, the group of nodes one operator call covers, with parameters a model
  writes. The model reviews the returned rows before the planner accepts them.
- **Repair** a failure in three ordered steps, each rewriting more than the one before: the
  parameters of one call, then the assignment of nodes to operators, then the plan itself.
- **Combine** the deterministic `builtin` operators with the Blue operators from `blue-data-ops`,
  which enter through their own adapter.
- **Inspect** a finished run. One `RunResult` holds the plans, the per-fragment results, the failure
  reasons, and every model exchange as structured data.

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

- Python 3.11 or later
- [`blue-data`](../blue-data) and [`blue-data-ops`](../blue-data-ops) checked out beside this
  repository. The install scripts install both from there.
- An OpenAI API key for the default model. Every planning stage calls a model.

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
export BLUE_DATA_PLANNER_OPERATORS=builtin        # the built-in operators only

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
| [platform/planner-mcp/](platform/planner-mcp/README.md) | `blue-data-planner-mcp`: the MCP server of the same name, whose one tool, `query_data`, runs the planner |
