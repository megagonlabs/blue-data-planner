# Guide

Task by task through the `blue_data_planner` library, in the order a caller meets them. The
command line does the same two operations without Python; see [cli.md](cli.md).
[api-reference.md](api-reference.md) holds every signature, parameter, and result type. The
development setup and the test commands are in [lib/README.md](../lib/README.md).

---

## Setup

Three things before the first call.

| Needed | How |
| --- | --- |
| The library | `bash lib/scripts/install.sh`, per [installation.md](installation.md) |
| A model provider key | `OPENAI_API_KEY` in the environment. `litellm` reads it on each call, so a library-only caller sets it too |
| A serialized `blue-data` registry | Built by `blue-data` before it reaches this package: see [quickstart.md](quickstart.md) |

```python
from pathlib import Path

from blue_data_planner import RunLimits, RunStatus, build_data_planner
from blue_data_planner.data import open_registry

registry = open_registry(Path("data/demo/registry.json"))
```

A caller that built a `DataRegistry` in process passes that object in place of this call.

---

## Assembling a planner

`build_data_planner` is the one call that assembles a planner.

```python
from blue_data_planner.operators.adapters.lotus import LotusSuite
from blue_data_planner.operators.builtin import BuiltinSuite

planner = build_data_planner(
    registry,
    suites=[BuiltinSuite(), LotusSuite()],
    model="openai/gpt-6-luna",  # a litellm model string, used by every component
    reasoning_effort="low",       # omitted from the call where it is left out
    limits=RunLimits(),
)
```

Nothing here reads the environment: every value a planner runs on is resolved by whoever builds
it, the provider key aside. A planner holds no state of its own between requests, so one object
serves many runs.

A caller wanting a different model per component, or a fake component in a test, builds the four
components itself and calls the `DataPlanner` constructor directly
([api-reference.md](api-reference.md#the-components)).

---

## Running a request

`run` plans, covers, executes, and repairs until the request ends.

```python
request = "Which three countries do the most customers come from?"
result = planner.run(request)

if result.status is RunStatus.OK:
    for table in result.answer or []:
        for row in table:
            print(row)
```

The answer is a list of tables, each a list of `dict` rows carrying the columns the plan's `RETURN`
node named. It is almost always one table, and it is `None` on every status other than `ok`.

---

## Planning without running

`plan` runs the same loop as `run` and stops before the first operator call. A request that has to
find its data gets its catalog searches run and its plan written again against what they found, so
the plan that comes back is the one `run` would start executing. It spends no operator call, and
it still calls a model: to write each plan, to search the catalog, and to cover each plan.

```python
result = planner.plan(request)

plan = result.pipeline.logical_plan
for node in plan.nodes_in_dependency_order():
    print(node.id, node.kind, node.attributes.get("instruction", ""))

for fragment in result.pipeline.cover.fragments:
    print(fragment.id, fragment.operator_id, fragment.logical_node_ids, fragment.instruction)
```

On `planned`, `pipeline.logical_plan` and `pipeline.cover` are always a matching pair and
`failure_reason` is `None`. Earlier plan versions, such as the one of `DISCOVER` nodes, stay in
`pipeline.logical_plans`, and what each search found is in `results`. The statuses `plan` can end in
are in [api-reference.md](api-reference.md#dataplanner).

---

## Reading the result and its status

Every request returns a `RunResult`, whichever way it ended. Exhaustion, operator failure, and
provider failure are statuses, not exceptions.

```python
if result.status is not RunStatus.OK:
    print(result.status, result.failure_reason)
```

`RunStatus` is a `StrEnum`, so `result.status == "ok"` holds too, though comparing against the
enum member reads better. `failure_reason` carries the words of whatever ended the run: every
failure status has one, and `ok` never does. What each status means is in
[architecture.md](architecture.md#how-a-request-ends).

---

## Setting the limits

`RunLimits` is fixed when the planner is built and neither operation takes an override, so a
planner configured for a benchmark and one configured for an interactive session are two objects.

```python
planner = build_data_planner(
    registry,
    suites=[BuiltinSuite()],
    model="openai/gpt-6-luna",
    limits=RunLimits(operator_calls=10, parameter_repairs=1, cover_repairs=1, failure_replans=1),
)
```

`operator_calls` is the one budget on a request's total work; the rest bound how often a stage may
try again. `wall_clock_seconds` limits how long a request runs, checked before each step. Three
bounds are counted across a whole request and reported back:

```python
counters = result.counters
print(counters.operator_calls, "of", counters.limits.operator_calls)
print(counters.failure_replans, counters.control_replans)
```

`operator_calls` also caps the size of a plan: a plan holding more operator and `DISCOVER` nodes
than it is turned down and written again. Model calls have no limit of their own; this cap bounds
the ones made while planning. What each of the planner's own calls cost is in
`result.conversations.usage`. A model call an operator makes on its own, as a LOTUS operation does,
is not there; the operator reports its cost in the `report` on its result.

---

## Choosing which operator suites are registered

A suite arrives as an object holding its own options, so a deployment picks both the suites and how
each is configured. The options each suite takes, and their defaults, are in
[api-reference.md](api-reference.md#blue_data_planneroperators).

```python
from blue_data_planner.operators.adapters.blue import BlueSuite
from blue_data_planner.operators.adapters.lotus import LotusSuite, lotus_is_installed

suites = [BuiltinSuite(), BlueSuite()]
if lotus_is_installed():
    suites.append(LotusSuite(max_rows=2000))
```

A caller that expects LOTUS operators checks `lotus_is_installed()` first, because the suite
registers nothing and gives no warning when LOTUS is missing.

A caller holding configured names rather than classes looks each one up:

```python
from blue_data_planner.operators import OPERATOR_SUITE_NAMES, operator_suite

print(OPERATOR_SUITE_NAMES)  # ('builtin', 'lotus', 'blue')
suites = [operator_suite(name) for name in "builtin,lotus".split(",")]
```

`operator_suite` raises `UnknownOperatorSuiteError` on a name no suite answers to, and
`OPERATOR_SUITE_NAMES` holds the three that exist. Where two operators can cover the same nodes,
the physical planner is offered them in the order their suites were registered.

---

## Inspecting what a run did, per fragment

`results` is keyed by the signature of the work, not by fragment id, so that a repaired cover
re-runs only what changed. `signatures` maps a plan and a cover back to those keys.

```python
from blue_data_planner.plan import signatures
from blue_data_planner.types import DiscoveryResult, FragmentResult

plan = result.pipeline.logical_plan
cover = result.pipeline.cover
signed = signatures(plan, cover)

for fragment in cover.fragments:
    found = result.results.get(signed[fragment.root_logical_node_id])
    if not isinstance(found, FragmentResult):
        continue  # the pass never reached this fragment
    call = found.result
    print(fragment.id, fragment.operator_id, found.status, found.failure_reason)
    for bound in fragment.input_data:
        print("  input:", bound.logical_node_id, bound.name, bound.description)
    if call is not None:
        print("  rows:", call.row_count, "report:", call.report)
        print("  output:", call.output)
```

`fragment.input_data` names each table the call reads: the node whose rows it takes, and the name
and description the cover gave them. A fragment that a review rejected is recorded here too, with
the objection as its `failure_reason` and the rows it produced still on `result`, so the record
shows which step sent the run to a repair. Every earlier cover is in
`result.pipeline.physical_plans`, keyed by cover id; the CLI's `--log` walks all of them.

A `DISCOVER` node's entry is a `DiscoveryResult` instead, carrying the search terms and the
entities each matched:

```python
for found in result.results.values():
    if isinstance(found, DiscoveryResult):
        print(found.search_terms, [match.path for match in found.matches])
```

What the model was asked and what it answered is in `result.conversations`: one exchange for the
logical planner, one per logical plan for the physical planner, one per fragment, and one per
search.

---

## Handling the exceptions

A caller of `DataPlanner` meets almost none. The run loop catches a provider failure and ends the
request as `llm_failed`, and catches any other `BlueDataPlannerError` and ends it as
`planner_failed` with the message in `failure_reason`, so a defect keeps what the run built.

| Raised by | Exception | Cause |
| --- | --- | --- |
| `open_registry` | `RegistryUnreadableError` | The path names no file, or the file cannot be read |
| `operator_suite` | `UnknownOperatorSuiteError` | A configured name registers no suite |
| `build_data_planner` | `OperatorRoutingError` | Two suites register the same operator id, or the operator registry a `BlueSuite` was given does not carry an operation the suite is bound to |
| A component called directly | `PlanValidationError`, `RecoverableOperatorError`, `BudgetExhaustedError`, and the rest | Each is caught by the stage that expects it during a run |

```python
from blue_data_planner import BlueDataPlannerError

try:
    planner = build_data_planner(registry, suites=suites, model="openai/gpt-6-luna")
except BlueDataPlannerError as error:
    raise SystemExit(f"cannot build a planner: {error}")
```

Every exception in `blue_data_planner.types` inherits `BlueDataPlannerError`, which inherits
nothing from `blue-data-ops`, so an operator suite's own errors stay distinguishable from a
planner's. The full list is in [api-reference.md](api-reference.md#exceptions).

## Next steps

- [api-reference.md](api-reference.md): every signature and field used above.
- [architecture.md](architecture.md): the run loop, the three repairs, and how a request flows.
- [extending.md](extending.md): add an operator suite or swap the model provider.
- [examples/](../examples/README.md): the tasks above as runnable scripts with their output.
