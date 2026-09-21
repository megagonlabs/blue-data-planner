# blue-data-planner examples

Four runnable scripts over the Northwind demo registry. Each has its captured output beside it.

## Prerequisites

| Requirement | Why |
| --- | --- |
| The library installed | [`docs/installation.md`](../docs/installation.md) |
| SSH access to `github.com/megagon-internal` | The demo seed is fetched from `blue-data` rather than committed here |
| `OPENAI_API_KEY` | Every planning stage calls a model. `choose_operator_suites.py` is the one script that does not |

Build the registry once, from the repository root, in the virtual environment
[installation.md](../docs/installation.md) creates:

```bash
python lib/scripts/demo/fetch_northwind.py
python lib/scripts/demo/register_northwind.py
```

That writes `data/demo/registry.json`, which every script reads
([quickstart.md](../docs/quickstart.md#2-build-the-demo-registry)).

## The scripts

### Answer a request (`run_a_request.py`)

The shortest path: assemble a planner, run one request, print the rows.

```bash
python examples/run_a_request.py
```

**[Captured output](run_a_request.output.txt)**

### Plan without running (`plan_without_running.py`)

`planner.plan` spends no operator call. A request that has to find its data stops at the
discovery step, so the pass produces a plan of one `DISCOVER` node and an empty cover.

```bash
python examples/plan_without_running.py
```

**[Captured output](plan_without_running.output.txt)**

### Read back what a run did (`inspect_a_run.py`)

The plan the operators ran against, what the catalog search matched, and each fragment's operator,
inputs, status, and row count.

```bash
python examples/inspect_a_run.py
```

**[Captured output](inspect_a_run.output.txt)**

### Choose the operator suites (`choose_operator_suites.py`)

What each suite offers, which operators can implement each logical operator, and what a planner
built with `suites=[BuiltinSuite()]` ends up holding. No model call, no API key: assembling a
planner only registers its operators.

The captured output records `lotus: 0 operators` because LOTUS is not installed, and the suite
registers nothing where it is absent. It records the `blue` suite as unavailable: it resolves
operator cards the installed `blue-data-ops` does not register.

```bash
python examples/choose_operator_suites.py
```

**[Captured output](choose_operator_suites.output.txt)**
