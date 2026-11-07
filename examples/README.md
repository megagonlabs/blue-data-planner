# blue-data-planner examples

Runnable scripts that use the Northwind demo registry. Each script has its captured output next to
it.

| Directory | Holds |
| --- | --- |
| `feature-demos/` | One library feature per script |
| `framework-demos/` | The planner used from an agent framework |

## Quick start

After the [prerequisites](#prerequisites), run:

```bash
python examples/feature-demos/run_a_request.py
```

## Prerequisites

| Requirement | Why |
| --- | --- |
| The library installed | See [`docs/installation.md`](../docs/installation.md) |
| `blue-data` and `blue-data-ops` checked out next to this repository | The install scripts install both from there. The demo data is copied from `../blue-data/data` |
| `OPENAI_API_KEY` | Every planning stage calls a model. Only `choose_operator_suites.py` runs without one |

Then build the demo registry once. Run these from the repository root, in the virtual environment
that [installation.md](../docs/installation.md) creates:

```bash
python lib/scripts/demo/fetch_northwind.py
python lib/scripts/demo/register_northwind.py
```

This writes `data/demo/registry.json`, which every script reads. See
[quickstart.md](../docs/quickstart.md#2-build-the-demo-registry) for details.

## feature-demos/ — library features, one per script

### Answer a request (`run_a_request.py`)

Builds a planner, runs one request, and prints the rows. This is the shortest example.

```bash
python examples/feature-demos/run_a_request.py
```

**API key:** OpenAI | **[Captured output](feature-demos/run_a_request.output.txt)**

### Plan without running (`plan_without_running.py`)

Writes a plan with `planner.plan`, which calls no operator.

If the request has to find its data first, the planner runs the catalog search and then writes the
plan again against what the search found. The script prints that plan and the cover the operators
would run.

```bash
python examples/feature-demos/plan_without_running.py
```

**API key:** OpenAI | **[Captured output](feature-demos/plan_without_running.output.txt)**

### Read back what a run did (`inspect_a_run.py`)

Runs a request, then prints:

- the plan the operators ran
- what the catalog search matched
- each fragment's operator, inputs, status, and row count

```bash
python examples/feature-demos/inspect_a_run.py
```

**API key:** OpenAI | **[Captured output](feature-demos/inspect_a_run.output.txt)**

### Choose the operator suites (`choose_operator_suites.py`)

Prints:

- the operators each suite offers
- which operators can implement each logical operator
- the operators a planner built with `suites=[BuiltinSuite()]` holds

The script calls no model, because building a planner only registers its operators.

The captured output shows `lotus: 0 operators` because LOTUS was not installed. The LOTUS suite
registers nothing when LOTUS is missing.

```bash
python examples/feature-demos/choose_operator_suites.py
```

**API key:** not required | **[Captured output](feature-demos/choose_operator_suites.output.txt)**

### Push a plan down to a compound operator (`push_down_to_nl2sql.py`)

`blue.nl2sql` writes one SQL query and runs it. One call can cover a scan, a filter, a join, an
aggregate, and a sort over one database.

The script runs one request that SQL can answer from end to end, with the built-in and Blue
suites. The physical planner decides how much of the plan goes to `blue.nl2sql`, and it may split
the plan across other operators.

For each plan version, the script prints:

- the logical plan's nodes, grouped by the fragment that covers them
- what each fragment's operator was sent and returned, including the SQL `blue.nl2sql` wrote
- whether the answer holds the expected rows

```bash
python examples/feature-demos/push_down_to_nl2sql.py
```

A run takes under a minute and prints nothing until it finishes.

**API key:** OpenAI

## framework-demos/ — agent frameworks

### LangChain agent over MCP (`langgraph_mcp_demo.py`)

A LangChain agent writes a short Markdown report on Northwind's orders, using the `query_data`
tool from the `blue-data-planner-mcp` server.

- The agent is built with `create_agent` on LangGraph.
- It decides which data questions to ask and sends each one to the planner.
- It builds the report from the rows that come back.
- The run prints each tool call as the agent makes it.

The server runs as a subprocess over stdio, with the `builtin` operator suite only.

```bash
# in the virtual environment docs/installation.md creates
bash platform/planner-mcp/scripts/install.sh
pip install langchain langchain-openai langchain-mcp-adapters
python examples/framework-demos/langgraph_mcp_demo.py
```

A run takes a few minutes, because each tool call plans and runs a whole request. In the captured
run, the planner failed on one question. The agent split it into two simpler questions and
combined the rows itself.

**API key:** OpenAI | **[Captured output](framework-demos/langgraph_mcp_demo.output.txt)**

## Summary table

| File | Folder | API key | External | Output |
| --- | --- | --- | --- | --- |
| `run_a_request.py` | feature-demos | OpenAI | — | [output](feature-demos/run_a_request.output.txt) |
| `plan_without_running.py` | feature-demos | OpenAI | — | [output](feature-demos/plan_without_running.output.txt) |
| `inspect_a_run.py` | feature-demos | OpenAI | — | [output](feature-demos/inspect_a_run.output.txt) |
| `choose_operator_suites.py` | feature-demos | none | — | [output](feature-demos/choose_operator_suites.output.txt) |
| `push_down_to_nl2sql.py` | feature-demos | OpenAI | — | — |
| `langgraph_mcp_demo.py` | framework-demos | OpenAI | `blue-data-planner-mcp`, LangChain | [output](framework-demos/langgraph_mcp_demo.output.txt) |

Every script reads the demo registry that the [prerequisites](#prerequisites) build.
