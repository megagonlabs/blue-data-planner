# Quick start

From a clean checkout to an answered question. Needs `blue-data` and `blue-data-ops` checked out
beside this repository and a provider key, both explained in [installation.md](installation.md).

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
bash platform/planner-cli/scripts/install.sh
```

## 2. Build the demo registry

The demo runs on Northwind, a seven-table retail schema that `blue-data` already holds. Two
scripts copy it out of the `blue-data` checkout and build a registry over it. Neither calls a
model. Run them from the repository root, in the virtual environment step 1 created:

```bash
python lib/scripts/demo/fetch_northwind.py
python lib/scripts/demo/register_northwind.py
```

That writes `data/demo/registry.json`. `data/` is git-ignored, so nothing here is committed.
[`lib/scripts/demo/README.md`](../lib/scripts/demo/README.md) says what the two scripts do.

## 3. Point the CLI at it

```bash
export OPENAI_API_KEY=sk-...                      # a real key, not this placeholder
export BLUE_DATA_REGISTRY_PATH=$PWD/data/demo/registry.json
export BLUE_DATA_PLANNER_MODEL=openai/gpt-5.6-luna
export BLUE_DATA_PLANNER_OPERATORS=builtin        # builtin alone: the lotus extra is not installed
```

A profile stores the same values and keeps the key out of the shell history
([installation.md](installation.md#environment-variables)).

## 4. Ask a question

```bash
blue-planner data query "Which three countries do the most customers come from?"
```

```text
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country ┃ customer_count ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ Mexico  │ 3              │
│ Germany │ 2              │
│ UK      │ 2              │
└─────────┴────────────────┘
ok  operator calls 3  model calls 12  $0.00
```

Nothing in the request named a table. The planner searched the registry, wrote a plan over what it
found, and ran it.

## 5. See what the run did

```bash
blue-planner data query "Which three countries do the most customers come from?" --log
```

`--log` prints each fragment: the operator it bound, the columns it produced, and the first rows.
Its closing two lines are the bounds the run spent against:

```text
counters  operator calls 3/30   failure replans 0/2   control replans 1/5
model     12 calls   $0.00
```

[`examples/feature-demos/inspect_a_run.output.txt`](../examples/feature-demos/inspect_a_run.output.txt) shows the whole shape.

## 6. The same from Python

```python
from pathlib import Path

from blue_data_planner import build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite

planner = build_data_planner(
    open_registry(Path("data/demo/registry.json")),
    suites=[BuiltinSuite()],
    model="openai/gpt-5.6-luna",
)

result = planner.run("Which three countries do the most customers come from?")
print(result.status)   # ok
print(result.answer)   # one table of rows, such as [[{'country': 'Mexico', ...}, ...]]
```

## Next steps

- [examples/](../examples/README.md) — five runnable scripts with captured output
- [guide.md](guide.md) — the library task by task
- [features.md](features.md) — what the planner can do, one section per capability
- [architecture.md](architecture.md) — the three stages, the repairs, and how a run flows
