# blue-data-planner examples

Runnable scripts over the Northwind demo registry, grouped the way the `blue-data` and
`blue-data-kb-source` examples are. Each script has its captured output beside it.

| Directory | Holds |
| --- | --- |
| `feature-demos/` | One library feature per script: run a request, plan without running, read back a run, choose operator suites |
| `framework-demos/` | The planner used from an agent framework |

## Quick start

```bash
python examples/feature-demos/run_a_request.py
```

That needs the prerequisites and the registry below.

## Prerequisites

| Requirement | Why |
| --- | --- |
| The library installed | [`docs/installation.md`](../docs/installation.md) |
| `blue-data` and `blue-data-ops` checked out beside this repository | The install scripts install both from there, and the demo seed is copied from `../blue-data/data` |
| `OPENAI_API_KEY` | Every planning stage calls a model. `choose_operator_suites.py` is the one script that does not |

Build the registry once, from the repository root, in the virtual environment
[installation.md](../docs/installation.md) creates:

```bash
python lib/scripts/demo/fetch_northwind.py
python lib/scripts/demo/register_northwind.py
```

That writes `data/demo/registry.json`, which every script reads
([quickstart.md](../docs/quickstart.md#2-build-the-demo-registry)).

## feature-demos/ — library features, one per script

### Answer a request (`run_a_request.py`)

The shortest path: assemble a planner, run one request, print the rows.

```bash
python examples/feature-demos/run_a_request.py
```

**[Captured output](feature-demos/run_a_request.output.txt)**

### Plan without running (`plan_without_running.py`)

`planner.plan` spends no operator call. A request that has to find its data has its catalog
search run and its plan written again against what the search found, so the pass prints the plan
and the cover the operators would run against.

```bash
python examples/feature-demos/plan_without_running.py
```

**[Captured output](feature-demos/plan_without_running.output.txt)**

### Read back what a run did (`inspect_a_run.py`)

The plan the operators ran against, what the catalog search matched, and each fragment's operator,
inputs, status, and row count.

```bash
python examples/feature-demos/inspect_a_run.py
```

**[Captured output](feature-demos/inspect_a_run.output.txt)**

### Choose the operator suites (`choose_operator_suites.py`)

What each suite offers, which operators can implement each logical operator, and what a planner
built with `suites=[BuiltinSuite()]` ends up holding. No model call, no API key: assembling a
planner only registers its operators.

The captured output records `lotus: 0 operators` because LOTUS is not installed, and the suite
registers nothing where it is absent. It records the `blue` suite as unavailable: it resolves
operator cards the installed `blue-data-ops` does not register.

```bash
python examples/feature-demos/choose_operator_suites.py
```

**[Captured output](feature-demos/choose_operator_suites.output.txt)**

## framework-demos/ — agent frameworks

### LangChain agent over MCP (`langgraph_mcp_demo.py`)

A LangChain agent, built with `create_agent` on LangGraph, gets the `query_data` tool from the
`blue-data-planner-mcp` server and writes a short Markdown report on Northwind's orders. The agent
decides which data questions to ask, sends each one to the planner, and builds the report from the
rows that come back. The run streams each tool call as the agent makes it. The server is launched
as a subprocess over stdio and runs with the `builtin` operator suite only.

```bash
bash platform/mcp/scripts/install.sh
pip install langchain langchain-openai langchain-mcp-adapters
python examples/framework-demos/langgraph_mcp_demo.py
```

A run takes a few minutes, since each tool call plans and runs a whole request. In the captured
run, one question failed in the planner, and the agent split it into two simpler questions and
combined their rows itself.

**[Captured output](framework-demos/langgraph_mcp_demo.output.txt)**
