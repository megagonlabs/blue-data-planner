# blue-data-planner

Operator-agnostic data planners for the Blue **Data Intelligence Layer (DIL)**. A planner turns a
request written in plain language into a declarative plan — a DAG of operator calls — runs it, and
repairs it where it fails.

Answering a request takes three stages, each asking a model a different question and recording its
answer on its own:

| Stage | Question | Produces |
| --- | --- | --- |
| `LogicalPlanner` | What is the user asking for | A DAG of typed nodes, each carrying one instruction in plain language |
| `PhysicalPlanner` | How much of the plan does each operator take on, and against which source | A cover: nodes grouped into fragments, each bound to one operator |
| `FragmentRunner` | What parameters does the call run with, and was the result any good | Rows, and the decision to accept them or run again |

The middle stage carries the pushdown decision the rest is built around. Some operators take a
single node; others, such as `nl2sql`, take a whole region of the plan at once. Which groups an
operator is a candidate for follows from what it declares, so registering an operator is enough to
put it in play.

Plans, per-fragment inputs and outputs, and failure reasons are structured data, so a run can be
read back afterwards and repaired at the level that was actually wrong.

## Relationship to other Blue repositories

- **`blue-data-ops`** provides the Blue operators and the operator registry. This repository binds
  fragments to them through one adapter, the way it binds LOTUS. Its own `builtin` suite is the one
  set of operators implemented here.
- **`blue-data`** provides data source abstractions and the data registry, reached only through
  `lib/src/blue_data_planner/data/`.

Agents and UI are out of scope; they come from other Blue repositories or from third-party
frameworks such as LangChain.

## Requirements

- Python 3.11 or later; 3.11 or 3.12 with the `lotus` extra
- `blue-data` and `blue-data-ops` checked out beside this repository, which the install scripts
  install from
- An OpenAI API key: every planning stage calls a model

## Quick start

From the repository root, with the two sibling checkouts in place:

```bash
python -m venv .venv && source .venv/bin/activate
bash platform/cli/scripts/install.sh

python lib/scripts/demo/fetch_northwind.py
python lib/scripts/demo/register_northwind.py

export OPENAI_API_KEY=sk-...                      # a real key, not this placeholder
export BLUE_DATA_REGISTRY_PATH=$PWD/data/demo/registry.json
export BLUE_DATA_PLANNER_MODEL=openai/gpt-5.6-luna
export BLUE_DATA_PLANNER_OPERATORS=builtin        # builtin alone: the lotus extra is not installed

blue-planner data query "Which three countries do the most customers come from?"
```

[`docs/quickstart.md`](docs/quickstart.md) walks through the same steps and is the source of truth
for them.

## Documentation

- [`docs/`](docs/README.md) — the documentation set, and what to read when
- [`docs/quickstart.md`](docs/quickstart.md) — a clean checkout to an answered question
- [`examples/`](examples/README.md) — five runnable scripts with captured output

## Layout

```text
blue-data-planner/
├── design/        # the numbered design documents and the decision log
├── docs/          # documentation for readers outside the project
├── examples/      # runnable examples over the demo registry
├── lib/           # the blue_data_planner package, its tests, and its scripts
└── platform/      # one thin adapter per interface
```

## Packages

| Package | Path | Provides |
| --- | --- | --- |
| `blue-data-planner` | [`lib/`](lib/README.md) | The planner library |
| `blue-data-planner-cli` | [`platform/cli/`](platform/cli/README.md) | The `blue-planner` command |
| `blue-data-planner-mcp` | [`platform/mcp/`](platform/mcp/README.md) | The `blue-data-planner-mcp` server, one tool over the planner |
