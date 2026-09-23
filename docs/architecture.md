# Architecture

## Overview

`blue-data-planner` turns a request in plain language into a plan, runs the plan against whatever
operators are registered, and repairs what fails. It has two tiers, `lib/` and `platform/`.

### lib/ core

```text
                        External interfaces

   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │  Python lib  │   │     CLI      │   │  MCP server  │
   │              │   │ blue-planner │   │ (not built)  │
   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
          └──────────────────┴──────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                  DataPlanner   (data_planner.py)                │
 │         run(request) · plan(request) — the one entry point      │
 │         owns the run loop and the three nested repairs          │
 │                                                                 │
 │    RunState (run_state.py): every plan version, every result,   │
 │    every exchange with the model, and the counters              │
 └───┬─────────────┬───────────────┬──────────────┬────────────────┘
     │             │               │              │
 ┌───▼─────────┐ ┌─▼───────────┐ ┌─▼───────────┐ ┌▼───────────────┐
 │  Logical    │ │  Physical   │ │  Fragment   │ │  Catalog       │
 │  Planner    │ │  Planner    │ │  Runner     │ │  Searcher      │
 └───┬─────────┘ └─┬─────────┬─┘ └─┬───────┬───┘ └┬───────────────┘
     │             │         │     │       │      │
     │             │         ▼     ▼       ▼      │
     │             │     ┌──────────────┐ ┌───────────────────────┐
     │             │     │ operators/   │ │ execution/            │
     │             │     │  router.py   │ │ one operator call,    │
     │             │     │  builtin/    │ │ billed, then checked  │
     │             │     │  adapters/   │ │ against the declared  │
     │             │     │   blue ──────┼─┼─▶ blue-data-ops       │
     │             │     │   lotus ─────┼─┼─▶ LOTUS               │
     │             │     └──────────────┘ └───────────────────────┘
     │             │
     └─────────────┴──────────┬─────────────────┐
                              ▼                 ▼
                    ┌──────────────────┐ ┌──────────────────┐
                    │      llm/        │ │      data/       │
                    │  LLMClient, the  │ │  DataCatalog ────┼──▶ blue-data
                    │  one model call  │ │  open_registry   │    DataRegistry
                    └──────────────────┘ └──────────────────┘

  types/  every data class that crosses a module boundary
  plan/   plan checks, candidate fragments, signatures, response schemas
```

| Module | Holds |
| --- | --- |
| `data_planner.py` | `DataPlanner` and `build_data_planner`. The entry point, the run loop, and the repair order |
| `logical_planner.py` | `LogicalPlanner`. Writes a plan, and a continuation for every pass after the first |
| `physical_planner.py` | `PhysicalPlanner`. Covers a plan's operator nodes with fragments and binds each fragment to one operator |
| `fragment_runner.py` | `FragmentRunner`. Writes one fragment's parameters, has it executed, and reviews what came back |
| `catalog_searcher.py` | `CatalogSearcher`. Turns a `DISCOVER` node into the entities its search terms found |
| `run_state.py` | `RunState`, the one mutable object per request. The run loop alone installs plans and records results |
| `execution/` | `execute_physical_operator`, the column check, and the projection that produces the answer |
| `operators/` | `OperatorRouter`, the built-in suite, and one adapter per outside suite |
| `data/` | `DataCatalog`, `open_registry`, and the source reads. The only place `blue-data` is imported |
| `llm/` | `LLMClient` and its errors. The only place a model is called |
| `types/`, `plan/` | The shared shapes, and what can be answered about a plan with no model and no operator call |

### platform/ layer

```text
 ┌───────────────────────────────────────────────────────────────┐
 │                     platform/ components                      │
 │                                                               │
 │  ┌──────────────────────┐      ┌──────────────────────┐       │
 │  │         cli          │      │         mcp          │       │
 │  │  blue-planner        │      │  (directory only)    │       │
 │  │  Click + Rich        │      │  two tools sketched  │       │
 │  │  data query          │      │  in design/09        │       │
 │  │  profile ...         │      │                      │       │
 │  └──────────┬───────────┘      └──────────────────────┘       │
 └─────────────┼─────────────────────────────────────────────────┘
               │  Python import of the public names
               ▼
          blue_data_planner       lib/src/
```

| Component | Package | Entry point | Status |
| --- | --- | --- | --- |
| `platform/cli/` | `blue-data-planner-cli` | `blue-planner` (terminal, Click) | Built — see [cli.md](cli.md) |
| `platform/mcp/` | `blue-data-planner-mcp` | stdio (MCP) | Not built — see [mcp.md](mcp.md) |
| `platform/api/` | — | HTTP | Reserved, not built, not in the first release — see [api.md](api.md) |

An adapter translates arguments and results and holds no planning logic.

---

## The three stages

| Stage | Question to the model | Component | Runs |
| --- | --- | --- | --- |
| Logical planning | What is the request asking for | `LogicalPlanner` | Once per pass, before anything executes |
| Physical planning | How much of the plan does each operator take on, and against which entity | `PhysicalPlanner` | Once per cover, over the whole plan |
| Fragment execution | What parameters does this call run with, and are the rows any good | `FragmentRunner` | Once per fragment, during execution |

`CatalogSearcher` is not one of the three: it feeds catalog records to the two planners, and its
output never enters the data flow.

### The logical vocabulary

A plan is a DAG of typed nodes. Each operator node carries one instruction in plain language and
names no operator: that is chosen during physical planning, and it can differ between passes over
the same node. A `SCAN` also names the discovered entity it reads, or no source when it reads the
model's world knowledge.

| Kind | Nodes | What happens |
| --- | --- | --- |
| Operator | `SCAN`, `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION` | Absorbed into a fragment and run by the operator that fragment is bound to |
| Terminal | `RETURN` | Names the columns holding the answer. Bound to no operator |
| Control | `REPLAN`, `DISCOVER` | Ends the pass and returns to the logical planner. Ready only once every input it lists has a result |

### Fragments and pushdown

A fragment is a group of neighboring operator nodes that one operator runs in a single call. A
semantic filter takes one `FILTER` node; a Text-to-SQL operator writes one query that scans,
filters, joins, and aggregates, so it can take a whole region at once. How much to hand over is the
pushdown decision.

Every fragment obeys five rules, of which `plan/validation.py` checks four; convexity follows
from the rest.

| Rule | Why |
| --- | --- |
| Connected | The nodes describe one computation |
| Convex — no outside node lies on a path between two inside it | Execution cannot leave an operator and come back |
| Disjoint from every other fragment | Each node runs once, by one operator |
| Single exit — only the root may have consumers outside | A fragment returns its root's output, so an absorbed node's table is never materialized |
| Operator nodes only | `RETURN`, `REPLAN`, and `DISCOVER` are never absorbed |

Candidates follow from what each operator declares: the logical operators it can execute, the
source kinds it can read, and whether it accepts tables bound in from elsewhere. A model picks one
per uncovered node, offered largest first.

---

## The run loop

`DataPlanner.run` schedules, dispatches on what came up, installs a cover, and assembles the result.
It calls no model and invokes no operator itself.

### The three repairs

Recovery is three nested loops, ordered by how much work each throws away. A loop hands the failure
outward once its own bound is spent.

| Repair | Rewrites | Run by | Bounded by |
| --- | --- | --- | --- |
| Parameter repair | The parameters, against the same fragment and operator | `FragmentRunner` | `parameter_repairs`, per fragment |
| Cover repair | The whole cover, against the same logical plan | `PhysicalPlanner` | `cover_repairs`, per logical plan |
| Replan | The logical plan | `LogicalPlanner` | `failure_replans` and `control_replans`, per request |

The cover repair decides every binding again, and results already produced are filed under a
signature of the work that produced them (`plan/signatures.py`), so a new cover re-runs only what
the model changed.

### Limits

`RunLimits` fixes six bounds when the planner is built.

| Field | Default | Counts |
| --- | --- | --- |
| `operator_calls` | 30 | Every operator call in the request. The one budget |
| `plan_corrections` | 5 | Rewrites after validation turned a plan down |
| `parameter_repairs` | 3 | Parameter rewrites within one fragment |
| `cover_repairs` | 2 | Covers after the first, per logical plan |
| `failure_replans` | 2 | Replans a failure forced |
| `control_replans` | 2 | Replans a control node asked for |

Model calls are not budgeted.

### How a request ends

Every request returns a `RunResult`: exhaustion, operator failure, and provider failure are
statuses rather than exceptions, and `answer` is `None` unless the status is `ok`.

| `RunStatus` | Means |
| --- | --- |
| `ok` | The `RETURN` node produced rows, and `answer` holds them |
| `planned` | `plan` finished: one logical plan and one cover, nothing run |
| `invalid_plan` | Plan generation ran out of corrections |
| `replan_exhausted` | A replan bound was reached |
| `budget_exhausted` | The operator call budget ran out |
| `operator_failed` | An unrecoverable operator failure ended the run |
| `llm_failed` | The provider failed after the client's retries |
| `planner_failed` | A defect in the planner ended the run, with the message in `failure_reason` |

---

## Operator suites

A suite is a named set of physical operators. `build_data_planner` takes the suites a deployment
wants and registers what each returns with the `OperatorRouter`, which maps a logical operator to
the operators declaring it and resolves an operator id back to its implementation.

| Suite | Offers | Pulled in by importing it |
| --- | --- | --- |
| `builtin` | One deterministic operator per operator node kind (`builtin.filter`, `builtin.join`, `builtin.scan`, and the rest), plus `builtin.nl2table`, a compound operator that writes and runs a Python program, registered only when asked for, and experimental: see [features.md](features.md#builtinnl2table-is-experimental) | Nothing beyond the library |
| `lotus` | LOTUS's semantic operations: `lotus.sem_filter`, `lotus.sem_map`, `lotus.sem_extract`, `lotus.sem_topk`, `lotus.sem_agg`, `lotus.sem_join` | LOTUS, and only inside a callable |
| `blue` | One physical operator per operation in a `blue-data-ops` operator registry | `blue-data-ops` |

Each suite satisfies one protocol:

```python
class OperatorSuite(Protocol):
    name: ClassVar[str]

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]: ...
```

- `operator_suite("lotus")` returns a suite by name with its defaults
- The suite classes take their own options: `BuiltinSuite(with_nl2table=True)`,
  `LotusSuite(model=..., max_rows=...)`, `BlueSuite(operator_registry=...)`
- Each import sits inside the branch that needs it, so naming one suite never loads another's
  dependency. Adding a suite is covered in [extending.md](extending.md).

---

## Dependency rules

Each rule is checkable by reading a file's imports. `design/08-coding-style.md` is the source of
truth for them.

1. `types/` imports nothing else in `blue_data_planner`, and `plan/` imports only `types/`.
   Anything may import either.
2. `execution/` imports only `types/`: no planning component, no `operators/`, no `llm/`, no
   `data/`. It runs one operator over the tables its caller passed in.
3. Within `lib/src/`, only `data/` imports `blue-data`. `lib/scripts/` ships in no package and
   imports it directly to build a benchmark registry.
4. Only `operators/adapters/blue/` imports `blue-data-ops`.
5. Only `operators/adapters/lotus/` imports LOTUS, and only inside a callable, never at module
   import time. `lotus_is_installed()` answers whether it can be imported without importing it.
6. `platform/*` imports the package's exported names — the root package and the subpackage
   `__init__` re-exports — and never a module below one.

---

## Data flow: run

```text
run(request)
  └─ RunState(request, counters)
      └─ replan loop, bounded by failure_replans / control_replans
          ├─ LogicalPlanner.generate(request, catalog preview, results held, reason)
          │    └─ validate_logical_plan, rewriting up to plan_corrections times
          │       → no valid plan: status invalid_plan
          │
          └─ cover loop, bounded by cover_repairs
              ├─ PhysicalPlanner.cover(plan, previous cover, results held, reason)
              │    ├─ candidate_fragments per uncovered node, in reverse dependency order
              │    ├─ a model picks one candidate per node, writing the fragment's
              │    │    instruction, its scan sources, and its input names
              │    └─ validate_physical_plan
              │       → no candidate for some node: back to the logical planner,
              │         with no operator call spent
              │
              └─ pass loop: RunState.next_ready()
                   ├─ a fragment      → FragmentRunner.run  (see below)
                   │     ok           → record and continue
                   │     needs_repair → the cover repair
                   │     needs_replan → the replan
                   │     otherwise    → end the request with that status
                   ├─ a control node  → resolve every ready one, then the replan
                   └─ nothing left    → project_answer over the RETURN node's
                                        columns → status ok
```

A ready fragment comes before a ready control node, so a pass runs everything it can before it
plans again, and the continuation is written against every result the pass produced.

## Data flow: plan

`DataPlanner.plan(request)` stops after the first cover and spends no operator call.

```text
plan(request)
  ├─ LogicalPlanner.generate(request, catalog preview)
  │    → invalid_plan when the corrections run out
  ├─ install the plan
  └─ PhysicalPlanner.cover(plan)
       → install the cover, whatever it came back with
         status planned, answer None
```

A plan whose first pass ends in a `DISCOVER` node comes back as it stands, with no search run; it
is what `blue-planner data query --explain` prints, and it can also end in `llm_failed` or
`planner_failed`.

## Data flow: running a fragment

`FragmentRunner` is the only stage that sees data, and `execute_physical_operator` is the only place
an operator is invoked.

```text
FragmentRunner.run(fragment, conversation, inputs, counters)
  └─ parameter loop, bounded by parameter_repairs
      ├─ ask the model for parameters, constrained by operator.parameter_schema
      │    the turn carries: the fragment's instruction, the operator's description
      │    and limitations, an AttributeView of every attribute of each entity it
      │    scans (names, types, and values as they are actually spelled), and a
      │    summary of each input table
      │
      ├─ execute_physical_operator(operator, parameters, inputs, declared columns)
      │    1. refuse parameters the schema does not accept — a bug, so not billed
      │    2. bill one operator call against operator_calls
      │    3. call the operator
      │    4. check the result against the declared columns, coercing each type
      │    → unrecoverable failure: status operator_failed, the run ends
      │    → recoverable failure:   write the parameters again
      │
      └─ review turn over the rows
           accept → status ok
           reject → write the parameters again
           replan → status needs_replan
      out of attempts → status needs_repair, carrying the last assessment
```

Only the fragment's root node has its declared columns checked, neither a zero-row table nor an
empty bundle is a failure, and every attempt reads the fragment's original input.

## Data flow: resolving a discovery node

A `DISCOVER` node is resolved rather than run. There is no operator to choose, no parameter schema
to fill, and no operator call spent.

```text
CatalogSearcher.search(node, conversation, inputs)
  ├─ a model writes search terms from the node's instruction and its inputs' rows,
  │    capped at MAX_SEARCH_TERMS (8); the rest are reported as dropped_terms
  ├─ DataCatalog.search_records(term) per term, through blue-data's registry search
  │    (keyword, value, and vector similarity together)
  └─ DiscoveryResult: the entities found, deduplicated by path and ranked by score,
       each carrying every attribute it holds
```
