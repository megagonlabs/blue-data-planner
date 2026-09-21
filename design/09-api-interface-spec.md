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
| `blue_data_planner` | `DataPlanner`, `build_data_planner`, `RunResult`, `RunLimits`, `RunStatus`, `BlueDataPlannerError`, `__version__` |
| `blue_data_planner.data_planner` | `DataPlanner`, `build_data_planner` |
| `blue_data_planner.logical_planner`, `.physical_planner`, `.fragment_runner` | The three planning components, one module each |
| `blue_data_planner.catalog_searcher` | `CatalogSearcher` |
| `blue_data_planner.run_state` | `RunState` |
| `blue_data_planner.types` | Plan, node, result, and limit types, the enums, `summarize_table`, and every exception this library defines, `BlueDataPlannerError` first |
| `blue_data_planner.plan` | `validate_logical_plan`, `validate_physical_plan`, `candidate_fragments` |
| `blue_data_planner.execution` | `execute_physical_operator`, `check_output_columns`, `project_answer` |
| `blue_data_planner.operators` | `OperatorRouter`, `OperatorSuite`, `operator_suite`, `OPERATOR_SUITE_NAMES` |
| `blue_data_planner.operators.adapters.blue`, `.adapters.lotus`, `.builtin` | `BlueSuite`, `LotusSuite`, `BuiltinSuite`, and the `blue_operators`, `lotus_operators`, and `builtin_operators` each is built over |
| `blue_data_planner.data` | `DataCatalog`, `open_registry`, and the source-level reads |
| `blue_data_planner.llm` | `LLMClient`, `Conversation`, `LLMUsage`, `LLMError` |

Public is the package root, every module directly inside it, and the `__init__.py` of `types/`,
`plan/`, `execution/`, `operators/`, `data/`, and `llm/`. A module nested inside one of those
directories is not, and can be split or renamed within a minor version. The adapters are the one
exception: `operators/__init__.py` names the router and the suite lookup and nothing of a suite
itself, so an adapter is imported from `adapters/`, under the name of its suite, and each of
those modules is public where it sits. `operator_suite` imports the one it is asked for inside
its own body, so naming a suite never loads another's dependency.

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

Nothing reads the environment: every value a planner runs on is resolved by whoever builds it. What
building it takes is one call.

```python
from pathlib import Path

from blue_data_planner import RunLimits, build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.adapters.lotus import LotusSuite
from blue_data_planner.operators.builtin import BuiltinSuite

planner = build_data_planner(
    open_registry(Path("registry.json")),
    suites=[
        BlueSuite(),
        LotusSuite(),  # registers nothing where LOTUS is not installed
        # with_nl2table enables the entry that writes and runs a Python program (ADR-020)
        BuiltinSuite(with_nl2table=True),
    ],
    model="openai/gpt-4o",
    limits=RunLimits(),
)
```

The first argument is a `blue-data` registry, obtained from `data/` and passed back into it. A
caller reads nothing off it and constructs no source from it. `open_registry` is the one way in: a
registry is populated by `blue-data` before it reaches this package (ADR-003), so a caller that
assembled one itself passes that one in place of this call. It reaches a suite adapter unchanged,
because a suite that reads data takes a registry of its own (ADR-004).

A suite arrives as an object rather than a name, and each one holds the options that suite takes, so
this signature names no suite and grows no argument when one is added
([06-library.md](06-library.md#what-each-suite-offers)). A caller holding configured names calls
`operator_suite` for each, which raises `UnknownOperatorSuiteError` on a name no suite answers to.

`build_data_planner` constructs the model client from `model` and `reasoning_effort`, registers what
each suite returns, wraps the registry in a `DataCatalog`, and hands `DataPlanner` its four
components and the limits. A caller who wants a different model per component, or a fake component
in a test, builds the four and calls the constructor:

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
def run(self, request: str) -> RunResult: ...
def plan(self, request: str) -> RunResult: ...
```

| Operation | Does | Returns | Spends operator calls |
| --- | --- | --- | --- |
| `run` | Plans, covers, executes, and repairs until the request ends | A `RunResult` whose status says how it ended | Yes |
| `plan` | Writes one logical plan and one cover, then stops | A `RunResult` with status `planned` and `answer` set to `None` | No |

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
| `parameter_repairs` | Per fragment | `FragmentRunner`, writing parameters again after a rejected review | The cover repair |
| `cover_repairs` | Per logical plan | The run loop's cover repair loop | The replan |
| `failure_replans` | Per request | `RunCounters`, read by the run loop | `replan_exhausted` |
| `control_replans` | Per request | `RunCounters`, read by the run loop | `replan_exhausted` |

The three recovery bounds are one repair each, tried narrowest first
([04-detailed-architecture.md](04-detailed-architecture.md#5-recovering-from-a-failure)).

### `RunResult`

Returned whichever way a request ends, and fixed in
[10, what a run produces](10-data-model.md#3-what-a-run-produces). `to_dict` writes the JSON
form the CLI and the MCP server both send.

| Field | Type | Holds |
| --- | --- | --- |
| `status` | `RunStatus` | `ok`, `planned`, `invalid_plan`, `replan_exhausted`, `budget_exhausted`, `operator_failed`, `llm_failed`, `planner_failed` |
| `answer` | `DataBundle \| None` | The `RETURN` node's rows. `None` unless `status` is `ok` |
| `failure_reason` | `str \| None` | Why the run ended as it did. `None` when `status` is `ok` |
| `pipeline` | `DataPipeline` | Every logical plan and every cover the request produced, with the current pair named |
| `results` | `dict[str, FragmentResult \| DiscoveryResult]` | How each fragment ended and what each search found, keyed by the signature of the work |
| `counters` | `RunCounters` | What the run spent against the four bounds counted per request, and those bounds |
| `conversations` | `RunConversations` | Every exchange with the model, by the component that had it |

`answer` is `None` rather than `[]` on any status other than `ok`, so a caller who ignores the
status cannot mistake a failed run for an empty answer (ADR-015). `failure_reason` is the mirror of
that rule: every failure status carries one, in the words of whatever ended the run. `ok` never
carries one, and `planned` carries one only when no cover could be written.

No field reports a node's outcome on its own. How a node's work ended is the `status` of the entry
`results` holds under the signature the current cover gives it, and which operator ran it is that
fragment's `operator_id` ([10](10-data-model.md#3-what-a-run-produces)). A fragment a review turned
down is filed there too, with the objection as its `failure_reason`, so which step of a cover sent
the run to a repair is read off the record rather than out of the transcript.

`conversations` is where the words of a run's rejected work are read: a plan validation turned down,
a binding the physical planner replaced, every parameter set a review rejected and what it said
about each. It also carries what the calls cost, for
calls through `llm/` and nothing else. A model call an operator makes internally, as a LOTUS operator
does, is billed as part of the one operator call that contains it. Planner spend is exact; operator
spend is a call count.

### Exceptions

Exhaustion, operator failure, and provider failure are statuses, never exceptions (ADR-015). What
does raise, in the order a caller meets it:

| Exception | Raised when | Reaches a caller |
| --- | --- | --- |
| `PlanValidationError` | A generated plan fails a structural check | Only from a direct `validate_logical_plan` or `validate_physical_plan` call. `LogicalPlanner` catches it and regenerates |
| `UnknownOperatorSuiteError` | A name registers no operator suite | Always, from `operator_suite`. An interface maps it to its own configuration error |
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

`blue-planner` puts the two library operations on the command line, in the shape the Blue CLI family
uses: `<cli> <component> <action> <parameters>` (ADR-027).

```bash
blue-planner data query "Which Bay Area data scientist postings are at healthcare companies?"
```

### Commands

| Command | Does |
| --- | --- |
| `data query REQUEST` | Runs `run`: plans, covers, executes, and repairs until the request ends, then prints the answer |
| `data query REQUEST --explain` | Runs `plan`: writes one logical plan and one cover, prints them, and spends no operator call |
| `profile add NAME` | Creates a profile |
| `profile remove NAME` | Deletes a profile, after a confirmation |
| `profile list` | Names every profile, marking the selected one |
| `profile config [PARAM [VALUE]]` | Prompts for every parameter marked as asked, prompts for one, or sets one |
| `profile inspect` | Every parameter, the value in force, and the tier that supplied it |
| `profile select NAME` | Makes one profile the one later commands read |

The request is a positional parameter, in plain language. A plan is an output rather than an input,
so no command accepts one.

### Options

| Option | Commands | Effect |
| --- | --- | --- |
| `--profile NAME` | All | Which profile supplies configuration |
| `--registry PATH` | `data query` | The `blue-data` registry to plan over |
| `--model NAME` | `data query` | The litellm model string every component uses |
| `--format table\|json` | Anything printing a result | How the result is written, from `BLUE_DATA_CLI_FORMAT` where the option is absent |
| `--explain` | `data query` | Write the plan for the request and stop, running nothing |
| `--log` | `data query` | Report what the run did, after the run |
| `--log-limit N` | `data query` | The characters each line of the log is cut to, 80 by default |
| `--confirm` | `profile remove` | Answer the confirmation ahead of time |
| `--version` | The root | The package version |

`--format` and `--log` choose different things, and each is set independently of the other
(ADR-032). The first decides how a result is written; the second decides whether the run behind it
is reported. `--log-limit` takes effect where `--log` does.

A bound a run reports back is a profile parameter rather than a flag: a bound is configuration,
and configuration belongs to a profile (ADR-028). The three that reset inside a request carry no
parameter, since no number says what a request spent against them, and a caller who needs one
builds the planner itself. `--registry` and `--model` are the two settings an operator changes
between one invocation and the next.

`nl` is not one of the formats. An answer is a set of rows, and phrasing rows as a sentence is
behavior no library operation has (ADR-029).

### Output

Standard output carries the command's result: the answer, or the plan where `--explain` asked for
one. Standard error carries the progress indicator, the log, the closing summary, and errors, so
redirecting standard output captures the result alone.

`--format table` renders each table of the result under its own column names, and a terminal gets a
progress indicator naming the stage that is running. `--format json` writes one document, and it is
`RunResult.to_dict` ([10](10-data-model.md#5-shapes-that-leave-the-process)): the result asked
for, how the run ended, how each fragment ended, what the run spent, and what its model calls
cost. A JSON document is read where no terminal is attached, so it carries in the document what
`--format table` leaves to the closing summary on standard error. Every plan version the request
produced is the one part it holds back, since that grows with what the request tried: it travels
where `--explain` asked for the plan as the result, or where `--log` asked for the run behind the
answer.

`--log` reports what the run did, cover by cover. A cover's fragments come in
the order a run reaches them, and each names the operator that ran it, the logical nodes it covered
with the instruction each carried, the source each scan read, the fragments whose output it read,
and how it ended. A fragment that ran carries its ending — a row count where the review kept the
result, and the ending's name with the objection where it did not — and one the pass never got to
reads as "not reached under this cover" (ADR-033). A `DISCOVER` node leads the cover its plan was
written for, with what its search matched, since a search resolves before the plan reading it
exists. Work a later cover asks for again is named rather than reported twice, because an accepted
result was not run again (ADR-024). A run that failed closes the log with one more line naming
why.

What a narrow column cannot hold follows its cover, under the id of the fragment it belongs to: the
source each scan read, the objection a review raised, the columns of the first table the fragment
produced, and the first rows of it. `--log-limit` cuts each line of the columns and the rows. It
reaches neither an instruction, a source path, a match, nor an objection, so each of those is read
whole however long it is.

Where the log is drawn is what `--format` decides. Under `--format table` it is drawn on standard
error, one table per cover, which is what lets an instruction wrap inside its own column. Under
`--format json` it is the two keys it adds to the document on standard output, so standard error
carries the closing summary alone.

```text
cover 1   logical plan 8c4a71d0
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Fragment         ┃ Step   ┃ Instruction                ┃ Input   ┃ Result                ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ f1               │ n2     │ load job postings          │         │ ok  140 rows          │
│ builtin.scan     │ SCAN   │                            │         │                       │
│                  │ n3     │ find the Bay Area postings │         │                       │
│                  │ FILTER │                            │         │                       │
├──────────────────┼────────┼────────────────────────────┼─────────┼───────────────────────┤
│ f2               │ n4     │ keep the healthcare        │ from f1 │ needs_repair  31 rows │
│ lotus.sem_filter │ FILTER │ postings                   │         │                       │
└──────────────────┴────────┴────────────────────────────┴─────────┴───────────────────────┘
  f1
      reads  n2  /source/jobs/database/hiring/collection/public/entity/postings
      columns  company, location, title
      {'title': 'Senior Data Scientist', 'company': 'Corvus Health', 'location'…
  f2
      reason  the review said a keyword match cannot tell a healthcare employer apart

counters  operator calls 7/30   failure replans 0/2   control replans 1/2
model     11 calls   $0.42
```

A cover is written whole, so every fragment of it appears, and a fragment carried into a later cover
shows as the reuse it is.

### Errors and exit codes

An error reaches standard error as `ERROR: <CODE> <description>`.

| Code | Raised for |
| --- | --- |
| `CONFIG_MISSING` | A required parameter with no value: the registry path, the API key |
| `CONFIG_INVALID` | A parameter whose value cannot be used, such as an unknown operator suite |
| `REGISTRY_UNREADABLE` | A registry path that does not exist or does not parse |
| `PROFILE_NOT_FOUND` | A named profile that does not exist |
| `ABORTED` | A confirmation prompt, such as `profile remove` without `--confirm`, declined |
| `INVALID_PLAN`, `REPLAN_EXHAUSTED`, `BUDGET_EXHAUSTED`, `OPERATOR_FAILED`, `LLM_FAILED`, `PLANNER_FAILED` | A run that ended with that status, carrying its failure reason |
| `INTERNAL_ERROR` | An exception the adapter did not expect |

| Exit code | Meaning |
| --- | --- |
| 0 | The command succeeded; for a query action, the run ended `ok` or `planned` |
| 1 | Any error above |
| 2 | A usage error: a missing argument, an unknown option, a value outside what an option allows |

A configuration failure is told apart by its error code (ADR-030). A logical plan no operator can
cover is neither: the physical planner answers `no_operator` per node and returns to the logical
planner, and a request that never finds a cover ends as `replan_exhausted`, which is code 1
([06-library.md](06-library.md#operatorrouter)).

Under `--format json`, a run that produced a result prints that result on standard output whatever
its status, so a failed run stays machine-readable, and the status and failure reason in the wrapper
say how it ended. An error outside a run — a configuration
failure, an internal error, or a declined confirmation — always prints as the plain
`ERROR: <CODE> <description>` line on standard error; `--format` has no effect on it.

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
    "a3f1c208": {"search_terms": ["job postings"], "matches": [{"...": {}}], "dropped_terms": 0},
    "7b40de91": {"status": "ok", "failure_reason": null, "operator_id": "builtin.scan",
                 "row_count": 48, "output": [{"row_count": 48, "columns": ["..."],
                                              "sample_rows": [{"...": {}}]}]}
  },
  "counters": {"operator_calls": 7, "failure_replans": 0, "control_replans": 1,
               "limits": {"...": {}}},
  "conversations": {"calls": 11, "cost": 0.42}
}
```

Two shapes differ from the library's, and each is a measure where the library carries the whole
([10, shapes that leave the process](10-data-model.md#5-shapes-that-leave-the-process)): an
exchange, which travels as a call count and a cost, and the rows a fragment returned, which
travel as a table summary. Both grow with the work a request did rather than with its answer, so
carrying either whole would bloat every response. Everything else travels whole, the bounds
inside `counters` and a `DISCOVER` node's matches included.

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

The library takes settings as injected values and reads no environment variable itself. Each
interface loads what it needs and passes it into the constructors.

The MCP server reads the environment, which is how a client that launches it as a subprocess
configures it. `blue-planner` resolves each setting in one order — **command option > environment
variable > profile > default** — and `profile inspect` reports the tier that supplied each value
(ADR-028).

| Parameter | Cast | Default | Asked by `profile config` |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | secret | None, required | ✓ |
| `BLUE_DATA_REGISTRY_PATH` | file | None, required | ✓ |
| `BLUE_DATA_PLANNER_MODEL` | str | `BLUE_DATA_LLM_MODEL`, then `openai/gpt-5.6-luna` | ✓ |
| `BLUE_DATA_PLANNER_REASONING_EFFORT` | str | `low` | |
| `BLUE_DATA_PLANNER_OPERATORS` | str | `builtin,lotus` | ✓ |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | str | Inherits `BLUE_DATA_PLANNER_MODEL` | |
| `BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` | str | `none` | |
| `BLUE_DATA_PLANNER_LOTUS_MAX_ROWS` | int | `5000` | |
| `BLUE_DATA_PLANNER_ENABLE_NL2TABLE` | bool | `false` | |
| `BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS` | int | `30` | |
| `BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS` | int | `2` | |
| `BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS` | int | `2` | |
| `BLUE_DATA_CLI_FORMAT` | str | `table` | |
| `BLUE_DATA_CLI_LOG_LIMIT` | int | `80` | |

The first row, `BLUE_DATA_CLI_FORMAT`, and `BLUE_DATA_CLI_LOG_LIMIT` are family-wide parameters,
declared once for every Blue CLI rather than by `blue-planner` alone.
`BLUE_DATA_PLANNER_MODEL` reads
`BLUE_DATA_LLM_MODEL` where it is unset, and
`BLUE_DATA_PLANNER_LOTUS_MODEL` reads `BLUE_DATA_PLANNER_MODEL` the same way. Setting
`BLUE_DATA_PLANNER_REASONING_EFFORT` to `off` omits the parameter from the LLM call, for a model
that does not accept it; `none` is a different, real value that litellm still forwards to the
provider, disabling reasoning rather than omitting the parameter.
`BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` does not inherit from
`BLUE_DATA_PLANNER_REASONING_EFFORT`: it defaults to `none` on its own, since a LOTUS operation
spends many calls per row and gains little from added reasoning.

A `MAX_` name is a `RunLimits` field with a prefix saying the number is a cap, which the field name
alone does not: `RunLimits` drops it because the type name already says limits, and a profile has no
such container. The two `BLUE_DATA_CLI_` names are the family's, shared with the other Blue CLIs.

The family-wide spec also contributes `ANTHROPIC_API_KEY`, `BLUE_DATA_LLM_PROVIDER`, and
`BLUE_DATA_LLM_MODEL`, all visible through `profile inspect`. `BLUE_DATA_LLM_MODEL` is required,
so `profile config` prompts for it as well as for the parameters the table marks.

A secret is never a command-line value. `profile config OPENAI_API_KEY` prompts, and stores either
an encoded value or a reference such as `$OPENAI_API_KEY`, resolved when a command runs.
`profile inspect` masks a stored secret and shows a reference by name with whether it resolves.

Where each value lands is [12-deployment-ops.md](12-deployment-ops.md#4-configuration), together
with the directory a profile lives in.
