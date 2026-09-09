# blue-data-planner — High Level Architecture

## Diagram

Requests enter through one class, `DataPlanner`. The three planning components, the operator lookup,
and execution are separate parts that share only the data classes at the package root.

Arrows are dependencies, read as "uses", except the one dashed arrow, which is what happens once at
startup rather than while a request runs. `types/` and `plan/` are drawn without arrows
because anything may import them.

The diagram is structure, not sequence. What order the stages run in, and where a failure sends the
run back to, are in [04-detailed-architecture.md](04-detailed-architecture.md).

```mermaid
flowchart TB
    subgraph interfaces ["External interfaces"]
        CLI["CLI<br/>platform/cli"]
        MCP["MCP server<br/>platform/mcp"]
        LIB["Python library"]
    end

    subgraph rootbox ["Package root"]
        ENTRY["data_planner.py<br/>DataPlanner: the one public<br/>entry point, and the run loop"]
        LOGICAL["logical_planner.py<br/>writes the plan"]
        PHYSICAL["physical_planner.py<br/>cuts it into fragments,<br/>binds each to an operator"]
        RUNNER["fragment_runner.py<br/>writes parameters,<br/>reviews the result"]
        SEARCH["catalog_searcher.py<br/>resolves a DISCOVER node"]
        STATE["run_state.py<br/>plans, results, attempts"]
    end

    EXEC["execution/<br/>runs one fragment<br/>against one operator"]

    SHARED["types/, plan/<br/>plan data classes, checks,<br/>and candidate fragments"]

    subgraph opsbox ["operators/"]
        ROUTER["router.py<br/>logical operator to<br/>physical operators"]
        ADAPT["blue.py, lotus/<br/>one adapter per outside suite"]
        BUILTIN["builtin/<br/>the suite written here"]
    end

    OPS["blue-data-ops<br/>Blue operators and registry"]
    LOTUS["LOTUS<br/>outside dependency"]
    DATA["data/<br/>blue-data boundary"]
    LLM["llm/<br/>model client"]
    BD["blue-data<br/>DataRegistry and sources"]

    interfaces --> ENTRY
    ENTRY --> LOGICAL
    ENTRY --> PHYSICAL
    ENTRY --> RUNNER
    ENTRY --> SEARCH
    ENTRY --> STATE
    PHYSICAL --> ROUTER
    RUNNER --> ROUTER
    RUNNER --> EXEC
    ADAPT -.->|"registered at startup"| ROUTER
    BUILTIN -.->|"registered at startup"| ROUTER
    ADAPT --> OPS
    ADAPT --> LOTUS
    ADAPT --> DATA
    BUILTIN --> DATA
    BUILTIN --> LLM
    LOGICAL --> DATA
    PHYSICAL --> DATA
    RUNNER --> DATA
    SEARCH --> DATA
    LOGICAL --> LLM
    PHYSICAL --> LLM
    RUNNER --> LLM
    SEARCH --> LLM
    DATA --> BD
```

---

## Components

### External interfaces

The CLI and the MCP server are adapters in front of the library. Each translates requests and
responses and holds no planning or execution logic (ADR-002).

| Interface | Package | Use |
| --- | --- | --- |
| Python library | `lib/` | Embedding planning in a Python application |
| CLI | `platform/cli/` | Asking a question and reading the answer directly |
| MCP server | `platform/mcp/` | Giving an agent framework a planning tool |

### DataPlanner

The one public entry point, with two operations. Both take a request in plain language, and neither
takes a plan: a plan is what a request produced, not something a caller hands in.

| Operation | Returns | Spends operator calls |
| --- | --- | --- |
| Run a request | The rows that answer it, together with every plan and every attempt the run produced | Yes |
| Plan a request | The same record with status `planned` and no rows: the logical plan and the cover over it, and nothing run | No |

The second is what the CLI's `--explain` prints, and what a measurement of where pushdown boundaries
get drawn reads. Argument and return shapes are in
[09-api-interface-spec.md](09-api-interface-spec.md).

`DataPlanner` owns the loop from request to answer, set out in
[04-detailed-architecture.md](04-detailed-architecture.md). The loop does seven things, and none of
them calls a model or invokes an operator directly.

| The loop | Detail |
| --- | --- |
| Schedules | Takes the next fragment whose frontier all has results, or a control node that is ready |
| Dispatches on kind | A fragment goes to the fragment runner, a `DISCOVER` node to the catalog searcher, a `REPLAN` node ends the pass |
| Counts attempts | Per node set, per cover, and per request, which is what tells one repair from the next |
| Chooses the repair | Only the loop knows how many attempts each repair has left, so only the loop can pick one |
| Installs a cover | Compares a new cover with the previous one and drops the results the change invalidated |
| Bills the budget | Operator calls against the operator call budget |
| Assembles the result | One `PlanResult` whether the request succeeded or not |

Keeping the loop here rather than in `execution/` is what lets `execution/` run without importing
a planning component (ADR-011). `DataPlanner` is given the components below rather than building them, so it
looks up no operator and calls no model itself. `build_data_planner` does the wiring every
adapter needs, which keeps assembly out of the adapters.

### The components

The three planning components, the discovery step, the run state, and the loop that drives them. All are
modules at the package root, because a level grouping them would have to be named for planning and
this package is a planner ([07-repo-organization.md](07-repo-organization.md#why-the-planning-components-sit-at-the-package-root)).

| Module | Owns |
| --- | --- |
| `logical_planner.py` | `LogicalPlanner`. Reads the request, a preview of the catalog, and the entities discovery has found, and writes a plan in the fixed vocabulary of logical operators. Every node it writes carries one instruction in plain language, and no source, no operator, and no operator parameters. |
| `physical_planner.py` | `PhysicalPlanner`. Cuts the plan into fragments and binds each one to a physical operator, and each `SCAN` to one of the entities discovery found. It enumerates the fragments each operator's declaration allows and has a model pick among them, since the size of a fragment is the pushdown decision itself. |
| `fragment_runner.py` | `FragmentRunner`. Runs one ready fragment: writes the operator's parameters against the columns its input tables carry and the attributes of the entities it scans, has it executed, checks the declared columns, and reviews the result. Returns accept, escalate, replan, or stop. |
| `catalog_searcher.py` | `CatalogSearcher`. Resolves a `DISCOVER` node by turning its instruction into search terms and searching the registry for entities, each returned with every attribute it holds. Not one of the three planning stages: its output is catalog records read by the two planners, not rows entering the data flow. |
| `run_state.py` | `RunState`, the one mutable object per request. Holds every plan version, the results keyed by logical plan and then node, every fragment attempt including the rejected ones, the model usage, and the counters the loop reads to choose a repair. `DataPlanner` creates it and alone writes to it. |
| `data_planner.py` | `DataPlanner` and `build_data_planner`. The entry point lives beside the planning components it drives, since everything in this package turns a request into an answer and nothing outside it reads `RunState`. |

Four terms carry the design, and each is fixed in
[04-detailed-architecture.md](04-detailed-architecture.md) and
[10-data-model.md](10-data-model.md).

| Term | Means |
| --- | --- |
| Logical operator | What a step is for, in a vocabulary that belongs to no operator suite: `SCAN`, `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION`, `RETURN` |
| Physical operator | One implementation declaring which logical operators it can carry out, which kinds of source it can read, whether it accepts tables bound in from elsewhere, and what it cannot do |
| Fragment | A group of neighboring nodes one physical operator runs in a single call. Connected, convex, disjoint from every other fragment, and single exit |
| Cover | One physical plan's fragments over one logical plan. Written whole, never patched, and kept beside the covers it replaced |

### `execution/`

The single place any operator is invoked. It runs one physical operator over the tables it was
handed, bills the call against the operator call budget, and checks the result against the output
columns its caller declared, coercing each to its declared type.

Three things it does not do, and each one removes an import. It does not schedule, because the run
loop decides what is ready and hands down one call at a time. It does not resolve an operator id,
because its caller hands it a resolved operator. It does not call a model, because writing
parameters and reviewing a result both belong to the fragment runner. Together those keep
every planning component, `operators/`, and `llm/` out of its imports (ADR-011).

### `types/` and `plan/`

Both sit at the package root: `types/` imports nothing outside itself, and `plan/` imports only
`types/`. Keeping them there lets `execution/`, the planning components, and `operators/` share a
vocabulary without importing each other.

`types/` holds every data class that crosses a module boundary: `DataPipeline` and the plan
versions it contains, `PipelineNode`, the physical plan and its fragments, every result type, the
three catalog readings `data/` produces, and the `LLMUsage` records `llm/` produces. A type
`PlanResult` names has to be nameable by everything that reads a `PlanResult`, so the line lands
here: `types/` holds data, and every other module holds behavior.

Inside it, `errors.py`, `plan.py`, `catalog.py`, and `run.py` split those classes by subject, and
`__init__.py` re-exports all four. Every importer reads the one name `blue_data_planner.types`.

`plan/` holds what can be answered about a plan without a model, a source, or an operator call. Two
structural checks run before anything is executed: one rejects a logical plan the run loop could not
schedule, the other a cover that could not be run. Beside them sits the enumeration of the node sets
an operator could cover, which is graph work the physical planner reads rather than repeats. What
each check turns down is in [06-library.md](06-library.md#plan).

### `operators/`

The Blue operators and the registry that resolves them belong to `blue-data-ops`, and LOTUS is its
own package. This module holds the lookup planning reads, one adapter per outside suite, and the one
suite this repository owns.

| Path | Holds |
| --- | --- |
| `router.py` | `OperatorRouter`. Maps a logical operator to the physical operators that can carry it out, and resolves an operator id to the operator behind it. Lookup and nothing more: whether a particular node has an operator depends on the fragment it sits in and the source its scans are bound to, so it is asked per node by the physical planner and answered as `no_operator`. Only the router is read while planning, never the registry (ADR-009). |
| `blue.py` | Declares the operators of a `blue-data-ops` registry as physical operators. |
| `lotus/` | Declares LOTUS operations as physical operators. |
| `builtin/` | The suite written here (ADR-020): deterministic operations over the bundle, one per operator node kind, and `builtin.nl2table`, which writes a Python program from a fragment's instruction and runs it in a process holding copies of its tables and no path to a source, a registry, or a provider. This is what the planner falls back to when no outside suite can take a node, and what hosts a fragment that scans nothing. |

`__init__.py` re-exports the router alone, and an adapter is imported from the module named for its
suite, so importing the router loads no suite.

An adapter returns a list of physical operators rather than registering them into the router it sits
beside, which leaves which suites a deployment offers with whoever assembles the planner. That is
the dashed arrow in the diagram, and it happens once at startup.

A fragment passes a list of tables, each a plain list of row dicts, and each adapter converts
between that shape and its own suite's at its own edge (ADR-012).

### `data/`

The only place in the shipped package where `blue-data` is imported. `DataCatalog` selects and
renders what `blue-data` has already collected and enriched, and profiles nothing of its own. Four
callers go through this boundary.

| Caller | Reads |
| --- | --- |
| `LogicalPlanner` | A preview of the catalog: which sources exist, and what each is for |
| `CatalogSearcher` | A registry search, one call per search term. A hit is one entity and every attribute it holds |
| `FragmentRunner` | A view of every attribute of each entity its own fragment scans: sample values as they are spelled, and what `blue-data` inferred about them |
| An operator bound to a fragment | Its own source, for reading and for the few calls that write |

`PhysicalPlanner` is not among them. Every entity it can bind a `SCAN` to reached the run through a
`DISCOVER` node, so it reads the run's results rather than the registry
(ADR-022).

Concrete implementations are supplied at startup rather than constructed in place.

The boundary reads a registry and never builds one. Registering sources, syncing them, collecting
their statistics, and enriching them are ingestion, which belongs to `blue-data`, so a registry
reaches this package already populated and a caller with no registry builds one there first. The one
place that does build a registry is the TAG-Bench script, which is a build step rather than shipped
code and calls `blue-data` directly ([07-repo-organization.md](07-repo-organization.md)).

### `llm/`

The model client shared by all four components that call one: the logical planner, the physical
planner, the fragment runner, and the catalog searcher. It holds prompt templating and its own error
types. Nothing else in the library calls a model.

---

## Dependency rules

Each rule is checkable by reading imports.

1. `types/` imports nothing outside itself, and `plan/` imports only `types/`.
    Anything may import either.
2. `execution/` imports only `types/`: no planning component, no `operators/`, no `llm/`. It runs one fragment
    against one operator it is handed.
3. Nothing outside `data/` imports `blue-data`, and nothing outside `operators/lotus/`
    imports LOTUS. The LOTUS import sits inside a callable, never at module import time.
4. `operators/` imports no planning component. An adapter returns physical operators; it registers none.
5. `platform/*` imports only the public surface. No adapter reaches into a module below it.

---

## What is deliberately absent

| Not here | Why |
| --- | --- |
| Agents | Out of scope for this repository. Agent frameworks call in through the MCP server. |
| REST API | Not part of the first release. The CLI covers direct use and the demo, and MCP covers the agent frameworks that are the stated integration target, so a REST API would add deployment and operations work no consumer has asked for. `platform/api/` is reserved for one. |
| A plan store, and any path that runs a plan handed in from outside | A plan is what a request produced. `DataPlanner` takes a request and nothing else. |
| Benchmark harness | TAG-Bench is a target for measuring this architecture, not a component of it. Its inputs live under `data/` ([11-testing-strategy.md](11-testing-strategy.md)). |
| Cost and cardinality estimates | Choosing an operator or a fragment size by estimated cost is out of scope for the first release. |
| Plan optimization | Beyond the structural checks, a plan is not rewritten to a cheaper equivalent. |
