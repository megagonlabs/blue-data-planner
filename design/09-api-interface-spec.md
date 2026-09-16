# blue-data-planner — API / Interface Spec

Three interfaces call into the library: the Python library itself, a CLI, and an MCP server. A REST
API is reserved and not built ([03-high-level-architecture.md](03-high-level-architecture.md)). No
planning, operator selection, or execution logic lives in an interface; each one parses requests,
formats responses, maps errors, and loads configuration. Authentication and authorization belong to
the deployment.

This document covers what each interface accepts and returns.
[10-data-model.md](10-data-model.md) specifies the shape of a plan and a result, including the JSON
form they travel in, which is the part that has to stay stable across versions.

---

## 1. Python library

The canonical interface. The CLI and the MCP server call it; neither adds behavior of its own.

### The public surface

`blue_data_planner/__init__.py` holds `__version__` and the run path, so a caller who runs a planner
and reads its result writes one import. Everything else is reached through the module that owns it.

| Import | Holds |
| --- | --- |
| `blue_data_planner` | `DataPlanner`, `build_data_planner`, `PlanResult`, `RunLimits`, `RunStatus`, `BlueDataPlannerError`, `__version__` |
| `blue_data_planner.data_planner` | `DataPlanner`, `build_data_planner` |
| `blue_data_planner.logical_planner`, `.physical_planner`, `.fragment_runner` | The three planning components, one module each |
| `blue_data_planner.catalog_searcher` | `CatalogSearcher` |
| `blue_data_planner.run_state` | `RunState` |
| `blue_data_planner.types` | Plan, node, result, and limit types, the enums, and every exception this library defines, `BlueDataPlannerError` first |
| `blue_data_planner.plan` | `validate_logical_plan`, `validate_physical_plan`, `candidate_fragments` |
| `blue_data_planner.execution` | `execute_physical_operator`, `check_output_columns` |
| `blue_data_planner.operators` | `OperatorRouter` |
| `blue_data_planner.operators.adapters.blue`, `.adapters.lotus`, `.builtin` | `blue_operators`, `lotus_operators`, `builtin_operators` |
| `blue_data_planner.data` | `DataCatalog`, `open_registry`, and the source-level reads |
| `blue_data_planner.llm` | `LLMClient`, `Conversation`, `LLMUsage`, `LLMError` |

Public is the package root, every module directly inside it, and the `__init__.py` of `types/`,
`plan/`, `execution/`, `operators/`, `data/`, and `llm/`. A module nested inside one of those
directories is not, and can be split or renamed within a minor version. The adapters are the one
exception: `operators/__init__.py` re-exports the router alone, so an adapter is imported from
`adapters/`, under the name of its suite, and each of those modules is public where it sits.

That exception is what keeps the root cheap to import. Python runs a package's `__init__.py` before
any module inside it, so what the root names is paid by every import from this package. The run path
costs little — `types/` reaches nothing outside itself, `llm/` imports its provider inside
`LLMClient`'s own methods, and `data/` imports a light dependency — while an operator suite is the
one import heavy enough to be worth keeping off that path.

Public means three things at once. A name here is covered by semantic versioning, so renaming or
removing it breaks a version. `platform/*` imports these modules and no others
([08-coding-style.md](08-coding-style.md)). And every signature here is typed, since the package
ships `py.typed`.

A caller who wants physical operators alone reads `operators.adapters.blue` and `types`, and
constructs no planner.

When an adapter needs something absent from this list, the library is missing a method, and the fix
is to add one rather than to import a private module.

### Assembly

Nothing is constructed in place and nothing reads the environment, so a caller builds the parts and
passes them in.

```python
from pathlib import Path

from blue_data_planner import RunLimits, build_data_planner
from blue_data_planner.data import DataCatalog, open_registry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.adapters.blue import blue_operators
from blue_data_planner.operators.adapters.lotus import lotus_operators
from blue_data_planner.operators.builtin import builtin_operators

data_registry = open_registry(Path("registry.json"))
llm_client = LLMClient(name="planner", properties={"model": "openai/gpt-4o"})

router = OperatorRouter()
# operator_registry is a blue-data-ops OperatorRegistry, built the way that package documents
for operator in blue_operators(operator_registry, data_registry):
    router.register(operator)
for operator in lotus_operators("openai/gpt-4o"):  # empty when LOTUS is not installed
    router.register(operator)
# with_nl2table enables the entry that writes and runs a Python program (ADR-020)
for operator in builtin_operators(data_registry, llm_client, with_nl2table=True):
    router.register(operator)

planner = build_data_planner(
    router=router,
    catalog=DataCatalog(data_registry),
    llm_client=llm_client,
    limits=RunLimits(...),
)
```

`data_registry` is a `blue-data` registry, obtained from `data/` and passed back into it. A caller
reads nothing off it and constructs no source from it. `open_registry` is the one way in: a registry
is populated by `blue-data` before it reaches this package (ADR-003), so a caller that assembled one
itself passes that one in place of this call. It reaches a suite adapter unchanged, because a suite
that reads data takes a registry of its own (ADR-004).

`build_data_planner` is the default wiring, and what every adapter calls. It constructs the three
planning components and the discovery step, and hands `DataPlanner` the limits. A caller who wants a
different model per component, or a fake component in a test, builds them and calls the constructor:

```python
from blue_data_planner import DataPlanner
from blue_data_planner.catalog_searcher import CatalogSearcher
from blue_data_planner.fragment_runner import FragmentRunner
from blue_data_planner.logical_planner import LogicalPlanner
from blue_data_planner.physical_planner import PhysicalPlanner

planner = DataPlanner(
    logical_planner=LogicalPlanner(llm_client=big_model, catalog=catalog),
    physical_planner=PhysicalPlanner(llm_client=small_model, router=router),
    fragment_runner=FragmentRunner(llm_client=small_model, catalog=catalog, router=router),
    catalog_searcher=CatalogSearcher(llm_client=small_model, catalog=catalog),
    limits=limits,
)
```

A bound is set once, here. `DataPlanner` is the only object holding `RunLimits`, and a component that
obeys a bound reads it off the `RunCounters` it is handed per call. A number a component's
constructor does take is a number that is not a bound on a run, which is why `CatalogSearcher` can
be given one for the search terms a `DISCOVER` node expands into.

### `DataPlanner`

```python
def run(self, request: str) -> PlanResult: ...
def plan(self, request: str) -> PlanResult: ...
```

| Operation | Does | Returns | Spends operator calls |
| --- | --- | --- | --- |
| `run` | Plans, covers, executes, and repairs until the request ends | A `PlanResult` whose status says how it ended | Yes |
| `plan` | Writes one logical plan and one cover, then stops | A `PlanResult` with status `planned` and `answer` set to `None` | No |

Both take a request in plain language and nothing else. A plan is what a request produced, so no
entry point accepts one: `to_dict` writes a plan for a reader, and `from_dict` reads a recorded plan
back rather than feeding one in ([06-library.md](06-library.md#two-operations)).

Limits are fixed when the planner is built, so neither operation takes an override. A planner
configured for a benchmark run and one configured for an interactive session are two objects.

`plan` can end as `planned`, `invalid_plan`, or `llm_failed`, which are the three outcomes reachable
without running anything. A plan whose first pass ends in a `DISCOVER` node comes back as it stands,
with no search run, since resolving one would start the second pass.

### Bounds on a run

Every bound is a field of `RunLimits`, held by `DataPlanner` and read off the `RunCounters` it builds
for each request. Three are counted across a whole request and reported in `RunCounters`; the other
four reset inside one. How many search terms one `DISCOVER` node expands into is not among them:
reaching that number drops the extra terms and changes nothing else, so `CatalogSearcher` holds it.
How a deployment sets a bound is in
[12-deployment-ops.md](12-deployment-ops.md#5-request-limits).

| Bound | Counted | Enforced by | Reaching it |
| --- | --- | --- | --- |
| `operator_calls` | Per request | `RunCounters`, billed in `execute_physical_operator` | `budget_exhausted` |
| `plan_corrections` | Per plan | `LogicalPlanner`, rewriting a plan validation turned down | `invalid_plan` |
| `parameter_repairs` | Per fragment | `FragmentRunner`, writing parameters again after a rejected review | The fragment repair |
| `fragment_repairs` | Per root node | The run loop's fragment repair loop | The cover repair |
| `cover_repairs` | Per logical plan | The run loop's cover repair loop | The replan |
| `failure_replans` | Per request | `RunCounters`, read by the run loop | `replan_exhausted` |
| `control_replans` | Per request | `RunCounters`, read by the run loop | `replan_exhausted` |

The four recovery bounds are one repair each, tried narrowest first
([04-detailed-architecture.md](04-detailed-architecture.md#5-recovering-from-a-failure)).

### `PlanResult`

Returned whichever way a request ends, and fixed in
[10, what a run produces](10-data-model.md#3-what-a-run-produces).

| Field | Type | Holds |
| --- | --- | --- |
| `status` | `RunStatus` | `ok`, `planned`, `invalid_plan`, `replan_exhausted`, `budget_exhausted`, `operator_failed`, `llm_failed`, `planner_failed` |
| `answer` | `DataBundle \| None` | The `RETURN` node's rows. `None` unless `status` is `ok` |
| `failure_reason` | `str \| None` | Why the run ended as it did. `None` when `status` is `ok` |
| `pipeline` | `DataPipeline` | Every logical plan and every cover the request produced, with the current pair named |
| `results` | `dict[str, ExecutionResult \| DiscoveryResult]` | Keyed by the signature of the work that produced each one |
| `counters` | `RunCounters` | What the run spent against the four bounds counted per request, and those bounds |
| `conversations` | `RunConversations` | Every exchange with the model, by the component that had it |

`answer` is `None` rather than `[]` on any status other than `ok`, so a caller who ignores the
status cannot mistake a failed run for an empty answer (ADR-015). `failure_reason` is the mirror of
that rule: every failure status carries one, in the words of whatever ended the run. `ok` never
carries one, and `planned` carries one only when no cover could be written.

No field reports a node's outcome on its own. Whether a node succeeded is whether `results` holds the
signature the current cover gives it, and which operator ran it is that fragment's `operator_id`
([10](10-data-model.md#3-what-a-run-produces)).

`conversations` is where a run's rejected work is read: a plan validation turned down, a binding the
physical planner replaced, parameters a review rejected. It also carries what the calls cost, for
calls through `llm/` and nothing else. A model call an operator makes internally, as a LOTUS operator
does, is billed as part of the one operator call that contains it. Planner spend is exact; operator
spend is a call count.

### Exceptions

Exhaustion, operator failure, and provider failure are statuses, never exceptions (ADR-015). What
does raise, in the order a caller meets it:

| Exception | Raised when | Reaches a caller |
| --- | --- | --- |
| `PlanValidationError` | A generated plan fails a structural check | Only from a direct `validate_logical_plan` or `validate_physical_plan` call. `LogicalPlanner` catches it and regenerates |
| Any other `BlueDataPlannerError` | A defect in the planner, raised where it is found | Only from a direct call to the component that raised it |
| `RecoverableOperatorError` | An operator refuses its input in a way a different operator might not | Only from a direct `execute_physical_operator` call |
| `UnrecoverableOperatorError` | An operator fails for a reason no other operator would avoid | The same |
| `BudgetExhaustedError` | The operator call budget reaches its cap | Only from a direct `execute_physical_operator` call. `FragmentRunner` catches it and returns `budget_exhausted` |
| `LLMError` | The provider fails after retries | Only from a direct `LLMClient` call. `DataPlanner` catches it and ends the request as `llm_failed` |
| `ValueError`, `TypeError`, and a suite's own validation errors | Malformed state, a missing configuration value, an unknown node or operator id | Always. These are bugs and they propagate |

Every exception this library defines is in `types/errors.py` and inherits `BlueDataPlannerError`,
which is there too. It inherits nothing from `blue-data-ops`, so an operator suite's own exceptions stay distinguishable
from a planner's. A caller using
`DataPlanner` sees the last row and nothing above it. Each of the others is caught by the component
that expects it, and one a component did not expect is caught by the run loop, which ends the
request as `planner_failed` with the message in `failure_reason`.

---

## 2. CLI

One command. `plan` and `run` subcommands are deliberately absent: a plan is an output rather than
an input, so there is nothing for a `run` subcommand to read.

```bash
blue-data-planner ask "Which Bay Area data scientist postings are at healthcare companies?"
```

### Arguments and options

| Option | Type | Default | Effect |
| --- | --- | --- | --- |
| `REQUEST` | positional, required | | The question, in plain language |
| `--explain` | flag | off | Call `plan` instead of `run`: print the plan and the cover, and stop |
| `--json` | flag | off | Print the whole `PlanResult` as JSON instead of a table |
| `--show-conversations` | flag | off | Include the exchanges with the model in the printed output |
| `--model` | string | from the environment | The litellm model string every component uses |
| `--registry` | string | from the environment | Which `blue-data` registry to read |
| `--quiet` | flag | off | Suppress the per-fragment progress lines |

Each of the seven bounds has a flag, named `--max-` plus its `RunLimits` field name. The prefix is
what says the number is a cap, which the field name alone does not: `RunLimits` drops it because
the type name already says limits, and a command line has no such container.

| Flag | Sets |
| --- | --- |
| `--max-operator-calls` | `operator_calls` |
| `--max-plan-corrections` | `plan_corrections` |
| `--max-parameter-repairs` | `parameter_repairs` |
| `--max-fragment-repairs` | `fragment_repairs` |
| `--max-cover-repairs` | `cover_repairs` |
| `--max-failure-replans` | `failure_replans` |
| `--max-control-replans` | `control_replans` |

A flag left off takes the deployment's default. The CLI builds one planner per invocation, so the
flags reach `RunLimits` before the planner exists and no bound is set twice.

### Output

Progress goes to standard error as each fragment finishes, grouped by cover, so redirecting standard
output captures only the answer. Each line names the fragment, the logical nodes it covered, the
operator that ran it, and what happened, which is the pushdown boundary and its outcome.

```text
cover 1
  f1  n2,n3,n4  tablesuite.nl2sql          rejected: titles that are not data scientist roles
  f2  n5        tablesuite.document_fetch  ok (140 rows)
  f1  n2,n3,n4  tablesuite.nl2sql          rejected: the same, with the predicate written into the SQL text
cover 2
  f1  n2,n3     tablesuite.nl2sql          ok (48 rows)
  f2  n4        textsuite.semantic_filter  ok (11 rows)
```

The answer follows on standard output as a Rich table, or as JSON under `--json`. A run that did not
finish prints its status, the counter that ran out, and the fragment that failed, then exits
non-zero. Under `--explain` the plan and the cover are printed and no answer follows.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | `status` is `ok` or `planned` |
| 1 | The run finished with any other status |
| 2 | Bad usage: a missing argument, an unparseable option |
| 3 | Bad configuration: no API key, an unreachable registry |

A logical plan no operator can cover is not a configuration error. The physical planner answers
`no_operator` per node and returns to the logical planner, and a request that never finds a cover
ends as `replan_exhausted`, which is code 1 ([06-library.md](06-library.md#operatorrouter)).

---

## 3. MCP server

The server hosts the planning tools, which is what an agent uses to hand over a whole question.
Driving one operation directly is an operator suite's own concern, and no tool here exposes one.

### Planning tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `plan_and_execute` | `task` (string, required) | The result below |
| `plan` | `task` (string, required) | The same shape with status `planned`, `answer` null, and an empty `results`, having spent no operator call |

Neither takes limits. The server builds one planner at startup from its environment, and how much a
run may spend is a deployment decision rather than something an agent has a basis for choosing.

```json
{
  "status": "ok",
  "answer": [
    {"title": "Senior Data Scientist", "company": "Corvus Health", "location": "San Francisco, CA"}
  ],
  "pipeline": {
    "current_logical_plan_id": "8c4a71d0",
    "current_physical_plan_id": "a6e02f77",
    "logical_plans": {"3f2b9c14": {"...": {}}, "8c4a71d0": {"...": {}}},
    "physical_plans": {"d19f5b3c": {"...": {}}, "a6e02f77": {"...": {}}}
  },
  "results": {
    "a3f1c208": {"matches": 4},
    "7b40de91": {"status": "ok", "row_count": 48}
  },
  "counters": {"operator_calls": 7, "failure_replans": 0,
               "control_replans": 1},
  "conversations": {"calls": 11, "cost": 0.42}
}
```

Three shapes differ from the library's on purpose, and each is a count where the library carries the
full record ([10, shapes that leave the process](10-data-model.md#5-shapes-that-leave-the-process)):
`conversations`, a `DISCOVER` node's matches, and the bounds inside `counters`. An agent reasons
about the outcome, and a full transcript would bloat every response. The library and the CLI carry
them in full.

A run that did not finish returns the same object with its status, `answer` null, and the counters
showing what ran out. That is a successful tool call reporting a failed run, not a tool error.

### Errors

| Condition | MCP result |
| --- | --- |
| A run that did not finish | Success, with a status in the payload |
| An operator failing inside a run | Success. The fragment's attempt carries it |
| An unknown tool, a malformed argument, or a result that will not encode | Tool error, `isError` true |
| A missing API key or an unreachable registry | Tool error, `isError` true |

### Registry access

The operator registry and the router are in-process, built by whoever assembles the planner
(ADR-009). No tool exposes a registry lookup, and
physical planning makes no network call to discover an operator. Which operators a deployment
registered is fixed at startup by what it installed and assembled.

---

## 4. REST API

Not in the first release. `platform/api/` is reserved, and no package declares a web framework. A
future REST API maps to the same two library operations the other interfaces use, so its endpoint
shapes are specified when someone builds it.

---

## Configuration

The library takes settings as injected values. Each adapter loads a `.env` file with
`dotenv.load_dotenv(override=True)` at startup and passes what it reads into the constructor.

| Variable | Used by | For |
| --- | --- | --- |
| `OPENAI_API_KEY` | Both adapters | Planning, physical planning, parameters, review, discovery, and LOTUS operations |
| `BLUE_DATA_PLANNER_MODEL` | Both adapters | The default litellm model string, which `--model` overrides |
| `BLUE_DATA_REGISTRY_PATH` | Both adapters | The serialized registry an adapter opens and plans against |

Their defaults, and how a deployment sets them, belong to
[12-deployment-ops.md](12-deployment-ops.md).
