# Features

`blue-data-planner` turns a request in plain language into a plan over operators, runs the plan,
and repairs what fails.

Every snippet below needs a serialized `blue-data` registry and a model provider key: all three
planning stages call a model, a plan-only run included. Building a registry is covered in
[quickstart.md](quickstart.md); the key is in [installation.md](installation.md).
[api-reference.md](api-reference.md) holds every signature and result type named here.

---

## Planning in three stages

A request becomes a logical plan of what was asked, then a cover saying which operator runs which
part of it and against which entity, then rows. Each stage asks a model a different question, and
each answer is recorded on its own — see [architecture.md](architecture.md#the-three-stages).

```python
from pathlib import Path

from blue_data_planner import build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite

registry = open_registry(Path("data/demo/registry.json"))
request = "Which three countries do the most customers come from?"

planner = build_data_planner(registry, suites=[BuiltinSuite()], model="openai/gpt-5.6-luna")

result = planner.run(request)
print(result.status)  # ok
print(result.answer)  # one list of rows per table the answer carries
```

---

## The logical operator vocabulary

A logical node says what a step is for and nothing about how to carry it out. The vocabulary
belongs to no operator suite, so the same plan can be covered by LOTUS on one pass and by
Text-to-SQL on the next.

| Node | Asks for |
| --- | --- |
| `SCAN` | The rows of one registered source |
| `FILTER` | The rows that satisfy a condition |
| `PROJECT` | Named columns, or a new column derived from the ones a row holds |
| `EXPAND` | One row per element of a list column or a delimited string |
| `AGGREGATE` | Rows grouped and summarized |
| `DISTINCT` | One row per distinct combination of values |
| `SORT_SUPERLATIVE` | Rows ordered by a preference, with the first of them kept |
| `JOIN` | Two tables matched |
| `UNION` | Two tables concatenated |
| `RETURN` | The columns holding the answer |

Two control nodes let a plan stop where it cannot yet be written. `REPLAN` waits for the columns
and values earlier nodes produce; `DISCOVER` searches the data registry and comes back with the
tables it matched. Either ends the pass and returns to the logical planner.

```python
result = planner.plan(request)  # searches the catalog and covers, no operator call spent
for node in result.pipeline.logical_plan.nodes_in_dependency_order():
    print(node.id, node.kind, node.attributes)
```

---

## Pushdown and fragments

A fragment is a group of neighboring nodes that one operator runs in a single call, and how much
to hand over is the pushdown decision: a semantic filter takes one `FILTER` node, while a
Text-to-SQL operator can take a whole region that scans, filters, joins, and aggregates.

A larger fragment means fewer calls and less data moved between them, and the operator either does
the whole thing or fails as a unit. A smaller one costs more calls and can be repaired step by
step. The cover records where the boundary was drawn.

```python
for fragment in result.pipeline.cover.fragments:
    print(fragment.id, fragment.operator_id, fragment.logical_node_ids)
```

The five rules a cover obeys are in
[architecture.md](architecture.md#fragments-and-pushdown).

---

## Three ordered repairs

A failure is answered where it was caused, by the parameter repair, the cover repair, or the
replan, and it moves outward only when the narrower repair runs out of attempts. What each
rewrites and what bounds it are in [architecture.md](architecture.md#the-three-repairs).

Plans are never edited in place. A repair adds a version, and results already produced are filed
under a signature of the work that produced them, so a new cover re-runs only what changed.

```python
from blue_data_planner import RunLimits, build_data_planner

planner = build_data_planner(
    registry,
    suites=[BuiltinSuite()],
    model="openai/gpt-5.6-luna",
    limits=RunLimits(operator_calls=10, parameter_repairs=1, cover_repairs=1),
)

result = planner.run(request)
print(result.counters.operator_calls, result.counters.failure_replans)
```

---

## Operator suites

A suite is a named set of physical operators, registered when the planner is built. What each
operator declares is all the physical planner reads, which is what lets an outside suite and a
Blue operator compete for the same work.

| Suite | Offers | Needs |
| --- | --- | --- |
| `builtin` | One deterministic operator per node kind: `builtin.scan`, `builtin.filter`, `builtin.project`, `builtin.expand`, `builtin.aggregate`, `builtin.distinct`, `builtin.sort_superlative`, `builtin.join`, `builtin.union`. `builtin.nl2table` writes a Python program from the fragment's instruction and runs it, and is registered only when asked for ([experimental](#builtinnl2table-is-experimental)) | Nothing beyond the library |
| `lotus` | LOTUS's semantic operations: `lotus.sem_filter`, `lotus.sem_map`, `lotus.sem_extract`, `lotus.sem_topk`, `lotus.sem_agg`, `lotus.sem_join` | The `lotus` extra. The suite registers nothing where LOTUS is not installed |
| `blue` | One operator per operation in a `blue-data-ops` registry: eleven relational and aggregate operations, five semantic ones, `blue.nl2llm` for an answer from the model's own knowledge, and the compound `blue.nl2sql`, which covers a whole SQL region in one call | `blue-data-ops` |

```python
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.adapters.lotus import LotusSuite
from blue_data_planner.operators.builtin import BuiltinSuite

planner = build_data_planner(
    registry,
    suites=[BlueSuite(), LotusSuite(max_rows=2000), BuiltinSuite(with_nl2table=True)],
    model="openai/gpt-5.6-luna",
)
```

Adding a suite through an adapter is covered in [extending.md](extending.md).

---

### `builtin.nl2table` is experimental

`builtin.nl2table` writes a Python program from the fragment's instruction and runs it in a child
process. It is off unless a caller asks for it, and turning it on accepts three things.

**It is not a security boundary.** The program runs with the interpreter's own builtins and
whatever it imports. Nothing checks it first. A program that reads the filesystem, opens a network
connection, or reaches out of the child process is doing something this entry does not prevent and
does not try to. The child process bounds a program that is wrong — a wall clock everywhere, a
memory and CPU limit and no file writes where the platform offers them — not one written to escape.

**Turn it on only with a model and sources the deployment trusts.** Not on a deployment that
untrusted requests reach.

**The rows in a registered source reach the prompt.** The entry renders five sample rows of every
entity a fragment reads into the prompt the program is written from. Text stored in a source is
read by the model that writes the program, so a source someone else can write to is an input to
that program. This is the route by which a model is coerced into writing a program it would not
otherwise write. CVE-2025-68613 turned the same feature in LangChain's PythonREPLTool into remote
code execution, against a check like the one this entry no longer pretends to have.

---

## Catalog search over a `blue-data` registry

The logical planner sees a preview of the registered sources, ranked against the request. Where
that is not enough to name the tables a request needs, it writes a `DISCOVER` node instead of
guessing, and writes the rest of the plan once it can see what the search came back with.

```python
from blue_data_planner.types import DiscoveryResult

for found in result.results.values():
    if isinstance(found, DiscoveryResult):
        print(found.search_terms, found.dropped_terms)
        for match in found.matches:
            print(match.path, match.source_kind, [one.name for one in match.attributes])
```

The same search is reachable without a plan, through the catalog reading that `data/` exposes:

```python
from blue_data_planner.data import DataCatalog, open_registry

catalog = DataCatalog(open_registry(Path("data/demo/registry.json")))
for match in catalog.search_records("orders"):
    print(match.path, match.score)
```

---

## A run result that can be read back

One `RunResult` comes back whichever way a request ends. Exhaustion, operator failure, and provider
failure are statuses rather than exceptions, and `answer` is `None` unless the status is `ok`. It
also carries the plans, the per-fragment results, the counters, and every exchange with the model;
the fields are in [api-reference.md](api-reference.md#runresult).

```python
import json

result = planner.run(request)
print(json.dumps(result.to_dict(), indent=2))
```

`to_dict` is the JSON form every interface writes a run in, and it summarizes each fragment's
output. The rows themselves are read off `results` while the run is still in memory.

---

## The `blue-planner` CLI

The same two operations on the command line, with profiles for the settings a run needs.

```bash
blue-planner data query "Which three countries do the most customers come from?"
blue-planner data query "..." --explain            # the plan, no operator call spent
blue-planner data query "..." --log --format json  # the answer, plus what the run did
```

`--log` reports the run cover by cover: each fragment's operator, the nodes it covered, the source
each scan read, and how it ended. [cli.md](cli.md) forwards to the source of truth for commands,
profiles, output, and exit codes.

---

## MCP server

`blue-data-planner-mcp` exposes the same two operations as one tool, `query_data`, over stdio, so
an agent written with LangChain, LangGraph, or any MCP client hands over a whole data question and
gets rows back.

```python
import os
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

# The demo registry, resolved from the repository root.
REGISTRY = str(Path("data/demo/registry.json").resolve())

client = MultiServerMCPClient(
    {
        "blue-data-planner": {
            "transport": "stdio",
            "command": "blue-data-planner-mcp",
            "args": [],
            "env": {**os.environ, "BLUE_DATA_REGISTRY_PATH": REGISTRY},
        }
    }
)
```

`explain` writes the plan and stops; `include_plan` returns every plan the request produced
alongside the answer. [mcp.md](mcp.md) covers the tool, the response, and the error payload.

---

## REST API

Not built, and not in the first release. [api.md](api.md) says what to use instead.

---

## TAG-Bench harness

TAG-Bench is a set of natural-language questions over five BIRD SQLite databases, and the
benchmark the planner is measured against end to end, running LOTUS operators. Three scripts build
the input data and a fourth runs the benchmark in parallel worker processes, writing one record per
query and a scored report.

```bash
cd lib && export UV_PROJECT_ENVIRONMENT=.venv-lotus
uv run --extra lotus --extra dev python -m tests.benchmarks.tag_bench.benchmark run --split dev --limit 1
```

See [tag-bench-benchmark.md](tag-bench-benchmark.md) for the prerequisites, the build scripts, and
how a report is read.
