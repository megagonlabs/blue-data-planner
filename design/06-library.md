# blue-data-planner — Library

This document names the classes and functions of the `blue-data-planner` package and says what each
one is responsible for. It settles the names that [02-scenarios.md](02-scenarios.md) shows only for
illustration.

Three things are out of scope here.

- Exact argument and return shapes for the external interfaces belong to
    [09-api-interface-spec.md](09-api-interface-spec.md).
- Field-by-field shapes, JSON forms, and the rules values follow belong to
    [10-data-model.md](10-data-model.md). Classes fixed there appear below with their methods and a
    pointer, not with their fields repeated.
- A per-operator reference belongs to the suite that owns the operators: `blue-data-ops` for the
    Blue operators, LOTUS for its own. This document says only what an adapter reads off them.

Signatures are written in Python syntax for precision. They give names, arguments, and return types
rather than a copy of the code.

---

## Module map

| Module | Holds |
| --- | --- |
| `blue_data_planner/__init__.py` | The run path in one import: `DataPlanner`, `build_data_planner`, `PlannerSettings`, `build_data_planner_from_settings`, `RunResult`, `RunLimits`, `RunStatus`, `BlueDataPlannerError`, and `__version__` |
| `blue_data_planner/data_planner.py` | `DataPlanner`, `build_data_planner` |
| `blue_data_planner/settings.py` | `PlannerSettings`, `build_data_planner_from_settings` |
| `blue_data_planner/run_state.py` | `RunState` |
| `blue_data_planner/logical_planner.py` | `LogicalPlanner` |
| `blue_data_planner/physical_planner.py` | `PhysicalPlanner` |
| `blue_data_planner/fragment_runner.py` | `FragmentRunner` |
| `blue_data_planner/catalog_searcher.py` | `CatalogSearcher` |
| `blue_data_planner/prompts/rendering.py` | `render_sample_rows`, `render_attribute_views`: the run data every component that writes prompts renders the same way |
| `blue_data_planner/types/` | Every data class that crosses a module boundary: plans, nodes, fragments, operators, results, catalog readings, model usage, limits. `summarize_table` sits there too, since it builds one of those shapes from another and reaches nothing else |
| `blue_data_planner/plan/` | `validate_logical_plan`, `validate_physical_plan`, `candidate_fragments`, `signatures`, and the response schemas the planning stages send |
| `blue_data_planner/execution/` | `execute_physical_operator`, `check_output_columns`, `project_answer` |
| `blue_data_planner/operators/` | `OperatorRouter`, `operator_suite` over the suite names a deployment configures, one adapter per outside suite (`blue_operators` over `blue-data-ops`, `lotus_operators` over LOTUS), and `builtin/`, the suite this repository owns |
| `blue_data_planner/data/` | The `blue-data` boundary, the three catalog readings, and opening a serialized registry |
| `blue_data_planner/llm/` | `LLMClient`, usage accounting, prompt templating, error types |

---

## The public entry point

`DataPlanner` and `build_data_planner` are the entry point. Their signatures are in [09,
assembly](09-api-interface-spec.md#assembly) and [09,
operations](09-api-interface-spec.md#operations).

`DataPlanner` is given its four components rather than building them. It holds no model client, no
router, and no catalog, because every one of those belongs to a component that reads it. What it
owns is the loop.

| Component | Built with | Because |
| --- | --- | --- |
| `LogicalPlanner` | `llm_client`, `catalog` | Writes plans, reads a catalog preview ranked against the request, and rewrites a plan validation turned down |
| `PhysicalPlanner` | `llm_client`, `router`, `catalog` | Enumerates candidates from what operators declare and the kinds of the sources the plan's scans name, and reads the attribute views of the entities the plan scans |
| `FragmentRunner` | `llm_client`, `catalog`, `router` | Resolves a fragment's operator id, reads the attribute views of the entities it scans, writes parameters, reviews the result, and writes them again when the review rejects one |
| `CatalogSearcher` | `llm_client`, `catalog`, `max_search_terms` | Turns an instruction into search terms and searches the registry for entities |
| `DataPlanner` | The four above, and `limits` | Creates the run state, schedules, chooses repairs, and assembles the result |

Injecting the components is what lets a caller give the three planning components different
models, and what lets the run loop be tested against four fakes and no provider. Everything arrives
through a constructor, and nothing reads the environment: every value a planner runs on is resolved
by whoever built it.

Limits are fixed when the planner is built, so `run` and `plan` take a request and nothing else.
`DataPlanner` is the only object holding `RunLimits`. It builds one `RunCounters` per request, and
every component that obeys a bound is handed that object and reads the bound off it, so a bound is
set in one place and read where it is enforced.

A number in a component's constructor is therefore a number that is not a bound on a run.
`CatalogSearcher`'s cap on one `DISCOVER` node's search terms is the only one, which is what the
signature says about it.

`build_data_planner` takes values a caller has already resolved and returns a planner ready to
run. It opens no configuration and reads no environment variable; what it does is construct — the
model client, the router, the operators each named suite registers, the catalog, and the four
components — so the CLI, the MCP server, the benchmark harness, and a future REST API each write
the same five lines instead of the same forty. The one catalog goes to every component the table
above builds with one: the logical planner, the physical planner, the fragment runner, and the
catalog searcher. Wiring is not translation ([07-repo-organization.md](07-repo-organization.md)).

A caller who wants a different model per component, or a fake component in a test, builds the four
and calls `DataPlanner` directly.

### The values a planner is built from

`PlannerSettings` holds every value a planner is built from and carries the default each one
takes. Constructing one refuses a reasoning effort no model accepts. The defaults are in
[12-deployment-ops.md](12-deployment-ops.md#4-configuration), beside the variable each interface
reads one from.

`build_data_planner_from_settings` opens the registry the settings name and builds a planner over
the suites they list, which is the step every interface would otherwise repeat. The library still
reads no environment variable: each interface resolves values its own way and hands one
`PlannerSettings` over (ADR-037).

### Two operations

Both take a request in plain language, and neither takes a plan. A plan is what a request produced,
so there is no entry point that accepts one and runs it, and `from_dict` reads a recorded plan
rather than feeding one back in. What each returns and how each can end is in
[09, operations](09-api-interface-spec.md#operations).

`plan` is what the CLI's `data query --explain` prints and what a measurement of where pushdown
boundaries get drawn reads. It runs the same loop as `run` and ends where that loop would first
call an operator: at a ready fragment, or at a plan with nothing left to run. Everything before
that point runs as it does in `run`. A `DISCOVER` node with nothing to wait for is searched, the
continuation is written and covered, and a plan no operator can cover is replanned. None of it
spends an operator call. What comes back is the plan and the cover `run` would start executing. A
`DISCOVER` node that waits on rows a fragment produces is left unresolved, since resolving it would
take that fragment's call.

`planned` is a `RunStatus` member alongside `ok` and the failure statuses. One result type serves
both operations, and the rule that `answer` is `None` unless the status is `ok` survives unchanged.
`plan` can also return `invalid_plan`, `replan_exhausted`, `llm_failed`, and `planner_failed`, which
are the failures reachable without an operator call.

### The run loop

The loop schedules, dispatches on what came up, installs a cover, and assembles the result. None of
those calls a model or invokes an operator directly.

```python
def run(self, request: str) -> RunResult:
    state = RunState(request=request, counters=RunCounters(self._limits))
    try:
        status, failure_reason, answer = self._run_plans(state)
    except LLMError as error:
        status, failure_reason, answer = RunStatus.LLM_FAILED, str(error), None
    except BlueDataPlannerError as error:
        status, failure_reason, answer = RunStatus.PLANNER_FAILED, str(error), None
    return state.to_result(status, failure_reason, answer)
```

An ending carries the reason that produced it. Nothing writes a failure reason to the run state on
the way past, so the run state holds no field that only the last line reads.

The one `try` in the library sits here rather than inside a component, which is what keeps every
component free of provider handling. Why a provider failure ends the request with a status rather
than an exception is under [Errors](#errors).

One pass takes what `next_ready` hands back and dispatches on it. A fragment goes to
`FragmentRunner`, and its result is recorded whatever it says. A control node is resolved together
with every other control node ready beside it, and the pass then returns to the logical planner
once. A plan with nothing left ends the request as `ok`, and `project_answer` takes the `RETURN`
node's columns off the result of the node it reads; rows that cannot answer what the ending names
send the run to the replan instead. The fragment's own status then says whether the
pass continues, ends the request with that status, replans, or goes to a repair.

`plan` enters the same loop under the same `try` and differs in one place: a pass that `next_ready`
hands a fragment, or nothing, ends the request as `planned` instead of running the fragment or
projecting the answer. Control nodes, the replans they ask for, and the replan a plan no operator
can cover all run as they do under `run`, so the two operations cannot drift apart on anything
before the first operator call.

`next_ready` returns the next fragment whose frontier all has results, a control node whose inputs
all have results, or `None` when the plan is finished. A union return is what lets one scheduler
serve both, and the two are told apart by type rather than by a flag, since a control node belongs
to no fragment.

A ready fragment comes before a ready control node, so a pass runs everything it can before it
plans again and the continuation is written against every result the pass produced. That order
throws nothing away. A continuation adds work rather than retracting it, since a new version carries
over every node the run has something for
([04](04-detailed-architecture.md#writing-a-continuation)), so a fragment that ran is a fragment
whose result the next version still holds. Half the order is not a choice either: a `DISCOVER` node
listing inputs narrows its search terms with their rows, so it cannot resolve until the fragments
producing them have run.

Nothing here re-runs a fragment whose root already holds a result. That rule lives in `next_ready`,
so a cover that left most fragments alone resumes on top of what already ran with no bookkeeping
outside the run state.

### Choosing a repair

The three repairs are three nested loops
([04, section 5](04-detailed-architecture.md#5-recovering-from-a-failure)). Each loop owns one bound,
and hands the failure to the loop outside it once that bound is spent.

| Loop | Repeats | Bounded by |
| --- | --- | --- |
| Parameter | Writing one fragment's parameters | `parameter_repairs`, inside `FragmentRunner` |
| Cover | Covering one logical plan again | `cover_repairs` |
| Replan | Writing a logical plan | `failure_replans` and `control_replans` |

The nesting is what bounds each repair at its own scope without a count that outlives it. A new
logical plan enters the cover loop fresh, because the count is a loop variable and the loop is new.
Only the two replan counts last a whole request, which is why those are the two the run state holds.

One loop sits between the parameters and a new plan, because a cover reuses the fragments the model
keeps, so covering a whole plan again re-runs only what that cover changed
([04, the cover repair](04-detailed-architecture.md#the-cover-repair)).

The two replan paths count separately, because a replan a control node asked for and a replan a
failure forced are different events with different limits, and either ends the request with
`replan_exhausted` once its own bound is spent. A planner that comes back with no plan sends the run
outward rather than ending it.

Resolving a control node searches the catalog when the node is `DISCOVER` and records what came
back under the node's id. Every control node ready at that moment is resolved before the run returns
to the logical planner, and the one return spends one `control_replans`, however many nodes fired.

One path leads into both planners, used for the first plan, for every continuation, and for the
cover that follows each. The logical planner writes a plan from the request, the parent plan, the
nodes carried over, the results the run holds, and the replan reason if there is one; a plan it
could not write ends the request with its status. The physical planner then covers whatever was
installed, and a cover it could not write sends the run to the replan.

A logical plan no operator can cover is the `no_operator` case from
[04](04-detailed-architecture.md#failures-that-skip-recovery). It goes straight back to the logical
planner without an operator call being spent.

---

## Shared types

`types/` imports nothing outside itself, so `execution/`, the planning components, and the adapters can
share a vocabulary without importing each other.

That rule fixes what belongs here, because a type `RunResult` names has to be nameable by
everything that reads a `RunResult`. Following it through reaches further than the plan types:
`RunResult` holds `LLMUsage`, and the `DiscoveryResult` in its `results` map holds `EntityMatch`.
So the line is drawn once and drawn wide: **every shape crossing a boundary is declared here**,
and a function joins it only where it works on one of those shapes and reaches nothing else, as
`summarize_table` does. `data/` produces the catalog readings and `llm/` produces the usage
records, and both import their shapes from here.

[10-data-model.md](10-data-model.md) fixes every field and every JSON form; what follows is what
each class answers for and the methods it carries.

### The plan

```python
class LogicalOperator(StrEnum): ...   # the ten, named in 10-data-model.md
class ControlOperator(StrEnum): ...   # REPLAN, DISCOVER

INPUT_ARITY: dict[LogicalOperator, int]
```

The ten logical operators are frozen. Adding one changes an interface every operator suite has to
map onto, and invalidates every operator declaration already written against the set.
`ControlOperator` is separate because a control node is bound to no operator and has no arity: both
members take any number of inputs, including none.

`INPUT_ARITY` counts the tables a node transforms, not the inputs it may list. A node listing more
than its arity has reference inputs, whose rows its instruction mentions without transforming them
([10](10-data-model.md#how-many-inputs-a-node-takes)).

```python
@dataclass(frozen=True)
class PipelineNode:
    ...

    @property
    def instruction(self) -> str: ...

    @property
    def is_operator_node(self) -> bool: ...


@dataclass(frozen=True)
class LogicalPlan:
    ...

    def node(self, node_id: str) -> PipelineNode: ...
    def consumers(self, node_id: str) -> list[PipelineNode]: ...
    def nodes_in_dependency_order(self) -> list[PipelineNode]: ...
    def to_dict(self) -> dict[str, Any]: ...

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LogicalPlan: ...
```

A node carries one plain-language instruction, and a `SCAN` also the source it reads. No node
carries an operator id, operator parameters, or a result (ADR-008). All three are properties of a
cover or of the run, since all three can differ between passes over the same node.

The three query methods read `nodes` and `input_node_ids` and nothing else, so a plan answers
questions about its own shape without reaching into a planning component or `execution/` (ADR-011). `node`
raises on an unknown id rather than returning `None`. `consumers` is what the single-exit check
reads and what invalidation walks. `nodes_in_dependency_order` puts every node behind the nodes it
reads and raises on a cycle, so `to_dict` and `validate_logical_plan` share one traversal.

```python
@dataclass(frozen=True)
class PhysicalPlanFragment:
    ...


@dataclass(frozen=True)
class PhysicalPlan:
    ...

    def fragment(self, fragment_id: str) -> PhysicalPlanFragment: ...
    def fragment_for_node(self, node_id: str) -> PhysicalPlanFragment: ...


@dataclass
class DataPipeline:
    ...

    @property
    def logical_plan(self) -> LogicalPlan: ...
    @property
    def cover(self) -> PhysicalPlan: ...

    def to_dict(self) -> dict[str, Any]: ...

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DataPipeline: ...
```

`DataPipeline` is the container, and it is the one mutable plan object: the plans inside it are
frozen and are added rather than edited. The two properties raise when no plan is current, which is
true only before the first plan is written.

`fragment_for_node` raises when the node belongs to no fragment, which is what `RETURN`, `REPLAN`,
and `DISCOVER` look like. Callers reach those through `next_ready` instead, so nothing has to ask.

`to_dict` is how a plan leaves the process, in an MCP response or in CLI output. `from_dict` is
strict and exists for reading a recorded plan back, not for running one
([10](10-data-model.md#serialized-form)).

### A physical operator

`PhysicalOperator` is one candidate implementation, as the router holds it and as both the physical planner and the
fragment runner read it. `logical_operators` is the whole matching rule: candidate fragments are the
connected, convex subgraphs built from nodes whose kind is in that set, and `source_kinds` then
decides whether the operator reads the sources the scans in such a fragment name
([10](10-data-model.md#a-physical-operator)).

A single-member set means the operator can only ever cover one node. `description` and `limitations`
carry the weight in the physical planner's prompt, since the parameter schema is what a later component
fills rather than what tells two candidates apart.

`parameter_schema` is a Pydantic model rather than a JSON schema dict, because it is passed to the
model client as a response schema and validated on arrival
([05, structured output](05-software-stack.md#structured-output)). It is hand-written per operator
(ADR-010).

### Rows

```python
Record = dict[str, Any]  # one row
Data = list[Record]  # one table
DataBundle = list[Data]  # what one fragment passes to the next (ADR-012)
```

These live in `types/`, which imports nothing outside itself, because `ExecutionResult` and
`PhysicalOperator` both name a bundle.

`TableSummary` sits beside them: a table's row count, its column names, and its first few rows
([10](10-data-model.md#a-tables-summary)). A prompt and the CLI's trace show one in place of a
table.

```python
def summarize_table(table: Data, max_rows: int) -> TableSummary: ...
```

`summarize_table` builds one. Three places show a table rather than pass it on — the prompt
putting a fragment's input in front of the model that writes its parameters, the prompt putting
its result in front of the review, and the CLI's trace — and each renders the same summary its
own way. The cap is an argument because those readers differ in how many rows they can spend.

### Budgets and limits

```python
@dataclass(frozen=True)
class RunLimits:
    ...   # one field per bound in the table below


@dataclass
class RunCounters:
    limits: RunLimits
    ...   # one field per bound that lasts a whole request

    def spend_operator_call(self) -> None: ...   # raises BudgetExhaustedError at the cap
    def spend_failure_replan(self) -> bool: ...  # False when the cap leaves no room
    def spend_control_replan(self) -> bool: ...
```

Every bound the system obeys is a field of `RunLimits`, and it is set once, when the planner is
built. Nothing overrides one per request, so a planner configured for a benchmark run and a planner
configured for an interactive session are two objects rather than one object called two ways.

`RunCounters` counts against those bounds for one request, and it carries the `RunLimits` it counts
against, so a result shows each spend beside the bound it was spent against. Only `operator_calls`
stops the run by raising, because it is billed where no caller is positioned to read a return value.

| Bound | Enforced by |
| --- | --- |
| `operator_calls` | `RunCounters`, billed in `execute_physical_operator` |
| `failure_replans`, `control_replans` | `RunCounters`, read by the run loop |
| `cover_repairs` | The run loop's cover loop |
| `plan_corrections` | `LogicalPlanner`, rewriting a plan validation turned down |
| `parameter_repairs` | `FragmentRunner`, writing parameters again after a rejected review |

Three of the six last a whole request and are the fields of `RunCounters`. The other three reset
inside one: `plan_corrections` and `parameter_repairs` bound a loop inside a component, and
`cover_repairs` is a loop variable of the run loop. A bound that resets has no single number to
report, so the result carries what lasted. Every one of them is read off the
`RunCounters` a call was handed, whether or not that call spends anything against it.

How many search terms one `DISCOVER` node expands into is not a bound here. Reaching it drops the
extra terms and changes nothing else, so it shapes one component's work rather than a request's, the
way the size of the catalog preview does. `CatalogSearcher` holds it.

The values in force are the defaults on `RunLimits`, and
[12-deployment-ops.md](12-deployment-ops.md#5-request-limits) covers how a deployment sets them.

Model calls are not budgeted (ADR-014): how many a request makes already falls out of the bounds
above, and `RunResult.conversations` holds one usage record per call so the spend stays readable.

### What a caller receives

`RunResult`, `RunCounters`, `RunConversations`, `ExecutionResult`, `DiscoveryResult`, and the four
enums that travel in them are fixed in [10, what a run
produces](10-data-model.md#3-what-a-run-produces). A request returns a `RunResult` whether it worked
or not, unless a bug propagates: exhaustion, operator failure, and provider failure are statuses
rather than exceptions, so a failed run is as inspectable as a successful one and one structure
serves every adapter (ADR-015). `to_dict` writes the JSON form, and every interface sends that one
shape: rows a fragment returned as a table summary, an exchange as a call count and a cost,
everything else whole.

`RunStatus` has one member per way a request can end and one per way a fragment can end short of
an answer, and [09-api-interface-spec.md](09-api-interface-spec.md#results) lists the request
endings against what each means to a caller. Each producer writes its own subset. `planned` is
what `plan` returns, `llm_failed` is what a provider that fails after retries returns, and
`planner_failed` is what a planner defect comes back as; none of the three is reachable from a
component.
`needs_repair` and `needs_replan` run the other way: `FragmentRunner` is the only writer, the run
loop the only reader, and a `RunResult` never carries either, because both name work the loop still
has left to do.

`failure_reason` says why a run ended as it did, in the words of whatever ended it. Every failure
status carries one; `ok` and `planned` never do.

`LogicalPlanResult` and `PhysicalPlanResult` never leave the process. `DataPlanner` reads each one,
records what it holds on the run state, and moves on.
[10, what each component returns](10-data-model.md#what-each-component-returns) fixes their fields,
and `FragmentResult`'s beside them. A `FragmentResult` does leave: the run files one under the
signature of the work it came from, whether the review kept its result or turned it down, and
`CatalogSearcher`'s `DiscoveryResult` is filed the same way.

`FailedFragment` never leaves the process either. It is what `DataPlanner` hands the physical
planner on a cover repair: a fragment the cover being repaired ran, and the `FragmentResult` it came
to, whose status is `needs_repair`
([10, what each component returns](10-data-model.md#what-each-component-returns)).

```python
@dataclass(frozen=True)
class FailedFragment:
    fragment: PhysicalPlanFragment
    result: FragmentResult
```

Each component returns what it did instead of writing it down as it goes, so one that raises cannot
leave a half-written record behind and the run state keeps one writer.

Two things accumulate rather than being returned, and both for the same reason: a count that a raised
exception must not take with it. `RunCounters` is billed inside `execute_physical_operator`, so it
travels as an argument, and `FragmentRunner` catches `BudgetExhaustedError` from the other side.
Model usage lands on the conversation the component was handed, which the run state owns, so what a
run spent survives a component that raised partway through.

---

## `plan/`

Everything that can be answered about a plan without a model, a source, or an operator call. The
module imports only `types/`, so a check, an enumeration, and a schema are testable with a plan and
nothing else.

### Checking a plan

```python
def validate_logical_plan(
    plan: LogicalPlan,
    entity_paths: frozenset[str],
    carried_node_ids: frozenset[str] = frozenset(),
) -> None: ...


def validate_physical_plan(plan: LogicalPlan, physical_plan: PhysicalPlan) -> None: ...
```

Both raise rather than return, and both run before anything is executed. What they raise is
`PlanValidationError`, which is in `types/errors.py` with every other exception this library
defines.

`validate_logical_plan` enforces the plan rules ([10-data-model.md](10-data-model.md#1-the-plan))
and `validate_physical_plan` the fragment and cover rules
([04, what a fragment may be](04-detailed-architecture.md#what-a-fragment-may-be)). Neither adds a
rule of its own.

`carried_node_ids` exempts the nodes a continuation carried over from the shape and reachability
checks, whose objections do not apply to a node the run already has something for. `LogicalPlanner`
catches the error and regenerates.

`entity_paths` is the run's catalog context flattened to the paths it names, and every `SCAN` naming
a source has to name one of them. A path the run never searched for is a path a model wrote from
nothing, and the correction loop sends it back. A frozenset rather than the results map keeps
`plan/` reading one kind of value.

The physical check is defensive in its other rules. The physical planner enumerates only fragments
that satisfy them, so a cover it assembles should already pass. It is worth running because a broken
cover does not fail loudly on its own: a fragment whose non-root node is read from outside leaves
that reader waiting for a table nobody produces, which shows up as a run that stops making progress
rather than as an error.

Failing it therefore ends the request rather than starting a repair. Everything a model can get
wrong is refused before this point, by the response schema and by the candidate enumeration, so a
cover that reaches the check and fails it is a defect in the planner. `PlanValidationError` reaches
the run loop, which ends the request as `planner_failed` carrying what broke. The logical planner
regenerates on its own validation error because a plan is what a model wrote; a cover is what this
library assembled.

### Enumerating candidate fragments

```python
def candidate_fragments(
    plan: LogicalPlan, root_node_id: str, logical_operators: frozenset[LogicalOperator]
) -> list[frozenset[str]]: ...
```

Every node set rooted at `root_node_id` that an operator declaring `logical_operators` could cover:
built only from nodes whose kind is in that set, and satisfying the fragment rules
([04, what a fragment may be](04-detailed-architecture.md#what-a-fragment-may-be)). The singleton is
in the list whenever the root's own kind is in the set, so an operator covering one node and an
operator covering six come out of the same call.

Enumeration is graph work and pairing a node set with an operator is a planning decision, so this
returns node sets and the physical planner does the pairing. It reads no operator and no catalog,
which is what lets the shape of every candidate over a given plan be asserted without a model.

### Signing the work

```python
def signatures(plan: LogicalPlan, cover: PhysicalPlan) -> dict[str, str]: ...
```

One signature per node that produces a result, which is every fragment root and every control node. A
signature covers everything that decides what that node produces, and that includes the signatures of
the work feeding it. Two runs of the same work share a signature, and any change that would change
the output changes it, a change upstream included.

That is what lets the run state key results by signature, and install a new cover or a new plan
version without comparing it to the last one
([10, reusing a result](10-data-model.md#reusing-a-result)).

A fragment's inputs are other fragments, so signing is a question about a plan and a cover together
rather than a field either one carries. What a signature covers is read off the fragment rather than
listed, so a field added later is covered without anyone remembering to add it.

### Building a response schema

```python
def logical_plan_schema() -> type[BaseModel]: ...


def cover_choice_schema(
    plan: LogicalPlan,
    root_node_id: str,
    candidates: list[tuple[frozenset[str], PhysicalOperator]],
    reusable: PhysicalPlanFragment | None,
) -> type[BaseModel]: ...
```

Each builds the Pydantic model the model client is handed as a strict response schema
([05-software-stack.md](05-software-stack.md#structured-output)). `logical_plan_schema` is assembled
from the node schemas, so a plan naming an operation the vocabulary has no member for cannot be
produced. `cover_choice_schema` is built for one binding decision from that decision's own
candidates. It reads `plan` for one thing, which is the nodes feeding each candidate from outside
it, and gives each one a name and a description field. Every candidate also takes a
`required_output_columns` list holding at least one column. No field names a source, since the
plan's scans already name theirs. The candidate that reuses the fragment already covering the node
is a choice and nothing else: it carries no instruction and no input names, because everything about
that fragment is already written down. One further choice takes no candidate and carries a reason
instead, which is how a binding says that nothing offered can do what the plan asks ([04, choosing
among candidates](04-detailed-architecture.md#choosing-among-candidates)).

A response schema over a plan is a question about the plan's shape, which is what this module
answers without a model. The fixed schemas a component sends every time — a review's decision, a
discovery node's search terms, an operator's parameters — belong to the component or the operator
that sends them.

---

## The components

The three planning components, the discovery step, and the state the run loop writes.

### `LogicalPlanner`

```python
class LogicalPlanner:
    def __init__(self, llm_client: LLMClient, catalog: DataCatalog) -> None: ...

    def generate(
        self,
        request: str,
        conversation: Conversation,
        counters: RunCounters,
        parent: LogicalPlan | None = None,
        carried_node_ids: frozenset[str] = frozenset(),
        results: dict[str, FragmentResult | DiscoveryResult] | None = None,
        replan_reason: str | None = None,
    ) -> LogicalPlanResult: ...
```

One method serves the first plan and every continuation. With no `parent` it writes a whole plan.
With one it writes a continuation covering the remaining work, assembles it with the nodes
`carried_node_ids` names into a new version whose `parent_id` is the parent's id, and validates the
result.

The planner reads a catalog preview it fetches itself, ranked against `request`, rather than one
handed in. That costs one registry read per request, until the first cover installs, and removes
the preview from four signatures and from the run state.

`results` is the run's map for the parent version, and it answers two different questions. A
continuation after a `REPLAN` is written against the rows the pass's fragments produced, so the
planner reads real columns and values. A `DISCOVER` node's entry holds the entities its search
found and every attribute each one holds, which is what lets the continuation's scans name a source
and declare column names the registry actually carries. One argument covers both because both live
in the same map.

`replan_reason` is `None` on the first pass. Otherwise it says why the plan is written again: the
failure reason from the review or from a fragment whose cover repairs ran out, from the physical
planner, or from an ending the finished rows cannot answer, or the instruction of the control node
that ended the pass. It stays at the level the planner works in: it says what could not be done
rather than which operator failed to do it, because naming an operator would invite the planner to
work around an implementation it does not choose.

One conversation serves a whole request, and the run state hands the same one back on every pass. A
continuation is a turn on top of the plans already written rather than a fresh exchange that
describes them, so the planner answers what it wrote before and the unchanged turns stay cached
([04, one conversation per component](04-detailed-architecture.md#one-conversation-per-component)).

Two things happen inside `generate` rather than above it. The response schema is assembled from the
node schemas, so a plan naming an invented operation cannot be produced by a provider that enforces
one, and is refused when it is read where a provider does not
([05](05-software-stack.md#structured-output)). A plan that fails validation goes back to the same
conversation with the error appended, up to `counters.limits.plan_corrections`, and the plans validation turned
down stay in the conversation for the passes that follow.

The prompt asks for a complete plan from `SCAN` through `RETURN` whenever the planner has enough to
write one, because `nl2sql` can only group a scan and its filters if those nodes exist by the time
the cover is written. Nothing in validation enforces that ([04, writing a plan that can be pushed
down](04-detailed-architecture.md#writing-a-plan-that-can-be-pushed-down)).

Over registered data the planner has enough on the second pass rather than the first. The preview it
reads names sources and nothing below them, so a first plan writes one `DISCOVER` node per thing the
request needs found, and the continuation is the complete plan
([04](04-detailed-architecture.md#1-logical-planning)).

### `PhysicalPlanner`

```python
class PhysicalPlanner:
    def __init__(
        self, llm_client: LLMClient, router: OperatorRouter, catalog: DataCatalog
    ) -> None: ...

    def cover(
        self,
        request: str,
        plan: LogicalPlan,
        conversation: Conversation,
        previous: PhysicalPlan | None,
        results: dict[str, FragmentResult | DiscoveryResult],
        failed_fragment: FailedFragment | None = None,
    ) -> PhysicalPlanResult: ...
```

One entry point for every moment a cover is written: the first cover of a request, the cover repair,
and the cover that follows a continuation. They differ in what the arguments hold rather than in
which method is called.

`request` is the request the plan answers. It opens the conversation beside the plan, so the model
reads each node against what the whole plan is for; the plan stays what the cover follows.

It returns a whole new `PhysicalPlan`, because a cover is written whole and never patched into an
older one ([04](04-detailed-architecture.md#a-cover-is-written-whole)).

`previous` is the cover the run is replacing, and `None` before a request has one. It is what lets a
node whose fragment already produced a result be offered a candidate that reuses it, and choosing
that candidate carries the fragment into the new cover unchanged. Reusing finished work is therefore
a decision the model takes against the same alternatives as everything else
([04, choosing among candidates](04-detailed-architecture.md#choosing-among-candidates)).

A plan whose operator nodes all have results asks for no binding at all, and its cover is the
previous cover's fragments under the new plan's id, written without a model call. Every candidate
the model would weigh there is work the run already holds rows for (ADR-035).

`results` answers two questions. A `DISCOVER` entry records the source kind of each entity a `SCAN`
may name, which decides the operators a fragment holding that scan can be bound to. And an operator
node's entry says that node already ran, which is both what puts a reuse candidate in front of the
model and the cost it weighs when it considers grouping that node into a wider fragment ([04, the
cover repair](04-detailed-architecture.md#the-cover-repair)). Every turn that starts a cover shows
each accepted result held at that moment, with its row count, its operator, its columns, and its
first rows.

`failed_fragment` is set only on a cover repair. It is the fragment of the cover being repaired that
ran out of parameter repairs, with the `FragmentResult` it ended on: the failure reason and every
parameter attempt. The repair turn shows it beside the results held now, each attempt with its
parameters and how it ended, and marks the rows any attempt returned as unreadable, since nothing
can read or reuse them. The first cover of a logical plan
is passed none, so the physical planner never sees how a previous plan ended; that is the logical
planner's `replan_reason`. A `FailedFragment` whose result carries no failure reason is a defect and
raises.

The catalog is read for the entities the plan scans: one `attribute_views` call per distinct entity
path a `SCAN` names, the way `FragmentRunner` reads its fragment's scans. The opening turn shows
each entity's header from its `EntityMatch` and, below it, every attribute's type, description,
sample values, and counts, because whether a condition can go to an operator comparing stored
values or needs one reading meaning depends on what the column holds. A scan that names no source
contributes nothing, and no entity the plan does not scan is read.

Inside, the planner works top down from the root of the plan. For each uncovered node it asks
`candidate_fragments` for the node sets each operator the router offers could cover, pairs each set
with that operator, adds the reuse candidate where there is one, and a model picks. One binding
decision is one turn
([04, choosing among candidates](04-detailed-architecture.md#choosing-among-candidates)).

One logical plan is one conversation. Every cover of that plan, the first and each repair, continues
it, so a rebinding answers the bindings that already failed. A new logical plan starts a new
conversation, because the node ids in the turns already there name a different graph.

The response schema is built from that call's own candidates, so a candidate set with no `SCAN` has
no field in which to name a source, and where a scan does appear the entity paths in `results` are
the allowed values. A candidate is named by its operator and its node set rather than by a position
in the list it arrived in, so a choice still says what it chose when a later turn offers a different
list. The planner searches nothing itself: a path reaches it because a `DISCOVER` node
found it. `plan` is `None` when a node has no candidate at all, alone or in any fragment containing
it, and when the model refuses the candidates a node was offered; `failure_reason` then says which
node and why.

### `FragmentRunner`

```python
class FragmentRunner:
    def __init__(
        self,
        llm_client: LLMClient,
        catalog: DataCatalog,
        router: OperatorRouter,
    ) -> None: ...

    def run(
        self,
        fragment: PhysicalPlanFragment,
        conversation: Conversation,
        inputs: DataBundle,
        counters: RunCounters,
    ) -> FragmentResult: ...
```

`run` takes the rows for one fragment rather than the plan they came from, and the counters rather
than the object holding them. Everything it works on arrives per call, so it holds nothing about a
request between calls and a test builds a fragment, some rows, a catalog double, and a counter.

The fragment carries the reading it needs. `scan_sources` names an entity path per `SCAN`, and one
`attribute_views` call per path returns every attribute of that entity, so nothing about the run
beyond the fragment has to be passed in. The run's whole catalog context is deliberately not: it
would put entities this fragment cannot reach in front of the model, and the deeper reading of the
ones it can reach is what writing parameters needs
([10, what the fragment runner reads](10-data-model.md#what-the-fragment-runner-reads)).

For one fragment it loops until a result is accepted or `counters.limits.parameter_repairs` runs
out.

It resolves the fragment's operator id through the router, describes the input tables and the
attribute views of the entities it scans, writes the operator's parameters against its
`parameter_schema`, calls `execute_physical_operator`, and reviews what came back.

A review that accepts ends the loop with `status=ok`. One that rejects spends an attempt and the
parameters are written again; one that asks for a replan returns `status=needs_replan`; running out
of attempts returns `status=needs_repair`. Either failure reason names the fragment's logical nodes
and the instruction they were executed as, then the review's assessment where there is one: the
replan's, or the most recent one when attempts ran out. An unrecoverable operator failure returns
`status=operator_failed` and no review turn at all, since no other operator would avoid the same
failure. Every ending carries every attempt it made alongside its status, so what the review turned
down is readable rather than discarded.

Writing the parameters and reviewing the result are turns in one conversation, so the review sees
what was written and why, and a rejected review is answered on top of what it rejected. One fragment
is one conversation: a repair that binds the work to a different operator asks for parameters against
a different schema, so it starts a new one. Every attempt runs against the same `inputs`, read once
by the caller, so a reduction one attempt achieved cannot compound into the next.

`run` catches `BudgetExhaustedError` so that a budget running out mid-fragment ends the request as
`budget_exhausted` rather than as a planner defect. What the runner was doing when the budget ran out
is already in the conversation, which the run state holds.

The fragment instruction is what makes a fragment call checkable. A single call asked to scan, filter, and
join returns one table and no evidence of the steps inside it, so the review asks whether the rows
answer the one instruction the fragment carries ([04, reviewing the
result](04-detailed-architecture.md#reviewing-the-result)).

### `CatalogSearcher`

```python
class CatalogSearcher:
    def __init__(
        self, llm_client: LLMClient, catalog: DataCatalog, max_search_terms: int = ...
    ) -> None: ...

    def search(
        self, node: PipelineNode, conversation: Conversation, inputs: DataBundle
    ) -> DiscoveryResult: ...
```

Resolves a `DISCOVER` node. A model turns the node's instruction and the rows its inputs produced
into concrete search terms, capped at `max_search_terms`, and each term goes to
`DataCatalog.search_records`.

`max_search_terms` carries a default and is not a `RunLimits` field, so `build_data_planner` passes
nothing for it. Reaching it drops the extra terms and changes nothing else about how the request
proceeds, which makes it a number shaping this component's work rather than a bound on a run, like
the size of the catalog preview `LogicalPlanner` reads. The `DiscoveryResult` holds the terms, the `EntityMatch` values they
found, and how many terms the cap dropped. It is kept under the node's id in the run's results map,
and together with the other resolved nodes' entries it is the run's catalog context: the logical
planner reads it to write the continuation, the physical planner to read the kind of each source a
scan names.

It carries out no planning stage. Its output is catalog records rather than rows, it enters no data
flow, and no operator call is spent
([04](04-detailed-architecture.md#resolving-a-discovery-node)).

It takes `inputs` to narrow the vocabulary it searches in: an instruction such as "find press
release sources for the companies in the input" writes better terms for having seen which companies
those are. It writes no term per input row. What a node returns is a bounded set of entities however many
rows it was handed ([04, what a discovery node's inputs are
for](04-detailed-architecture.md#what-a-discovery-nodes-inputs-are-for)).

### `RunState`

```python
@dataclass
class RunState:
    request: str
    counters: RunCounters
    conversations: RunConversations
    pipeline: DataPipeline
    results: dict[str, FragmentResult | DiscoveryResult]   # keyed by signature

    def next_ready(self) -> PhysicalPlanFragment | PipelineNode | None: ...
    def ready_control_nodes(self) -> list[PipelineNode]: ...
    def result_for(self, node_id: str) -> ExecutionResult | DiscoveryResult | None: ...
    def signature_of(self, node_id: str) -> str: ...
    def inputs_for(self, fragment: PhysicalPlanFragment) -> DataBundle: ...
    def inputs_for_node(self, node: PipelineNode) -> DataBundle: ...
    def carried_node_ids(self) -> frozenset[str]: ...
    def install_logical_plan(self, plan: LogicalPlan) -> None: ...
    def install_cover(self, cover: PhysicalPlan) -> None: ...
    def record_fragment(self, fragment: PhysicalPlanFragment, result: FragmentResult) -> None: ...
    def record_discovery(self, node: PipelineNode, result: DiscoveryResult) -> None: ...
    def to_result(
        self, status: RunStatus, failure_reason: str | None, answer: DataBundle | None = None
    ) -> RunResult: ...
```

The one mutable object per request. `DataPlanner` creates it and `to_result` freezes it into the
`RunResult` a caller receives. No component holds it: each takes what it reads, writes its turns to
the conversation it was handed, and returns what it produced, which is what keeps the four components
carrying nothing about a request between calls. The bounds reach a run through `RunCounters`, which
carries the `RunLimits` the planner was built with, so nothing is configured per request.

Five fields cover a request. Four hold what the run is working on, and `results` holds how each
piece of work ended, keyed by the signature of that work rather than by a node id
([10, reusing a result](10-data-model.md#reusing-a-result)). The current plan and the current cover
are read off `pipeline`, which already names both, so nothing here forwards to them.

Keying by signature is what makes installation trivial. `install_cover` adds the cover, moves the
pointer, and signs the work that cover asks for; `install_logical_plan` adds a plan version and moves
its pointer. Neither compares anything and neither deletes anything, because a fragment whose work
changed has a different signature and finds nothing to reuse, while one whose work is unchanged
finds its result whatever cover or plan version it now sits in. A fragment fed by changed work is
covered by the same rule, since its inputs' signatures are part of its own.

The two pointers always name a matching pair: a logical version enters the pipeline alongside a
cover for it, so a plan no operator could cover leaves nothing behind and every signature describes
the plan and the cover that are current.

Three methods carry the rules that would otherwise be scattered through the loop.

`next_ready` asks the current cover for signatures. A fragment is ready when every node on its
frontier holds an accepted result and its own signature does not; a control node is ready when every
node it lists holds one. A fragment filed as rejected is offered again, which is what keeps a cover
repair that asks for the same work from resuming on top of a result nobody kept. It rebuilds the runnable set on each call, so a new cover is picked up without a
restart. `ready_control_nodes` answers the same question for every control node at once, because
the loop resolves all of them before it returns to the logical planner.

`result_for` is the one lookup by node id: it reads the current cover for the node's signature, then
the results map for that signature, and answers nothing where the entry there was not accepted.
Everything that reads a node's output goes through it, which is what keeps a rejected result from
reaching the next fragment.
`signature_of` returns the signature alone, which is what a `DISCOVER` node's conversation is filed
under ([10, conversations](10-data-model.md#conversations)).

`carried_node_ids` answers what a new logical version keeps: every node the run has something for,
which for an operator node means belonging to a fragment whose result the run still holds, grouped
nodes included. A resolved `DISCOVER` node carries over and never searches twice. `REPLAN` and
`RETURN` never carry over, which is what stops a `REPLAN` node that fired from firing again and
removes the need for a separate set of handled nodes
([10](10-data-model.md#the-logical-half)). It is a method rather than a field because it is a
question about the current plan, the current cover, and the results map, and this is the only object
holding all three.

---

## `execution/`

The single place any operator is invoked. It imports no model client, no registry, no router, and
no planning component.

```python
def execute_physical_operator(
    operator: PhysicalOperator,
    parameters: dict[str, Any],
    inputs: DataBundle,
    required_output_columns: list[OutputColumn],
    counters: RunCounters,
) -> ExecutionResult: ...


def check_output_columns(
    result: ExecutionResult,
    required_output_columns: list[OutputColumn],
    operator_id: str,
) -> ExecutionResult: ...


def project_answer(inputs: DataBundle, columns: list[str]) -> DataBundle: ...
```

`execute_physical_operator` validates the parameters against the operator's schema, bills one
operator call, invokes the operator on the bundle it was handed, and checks the result with
`check_output_columns` against the declared columns, coercing each to its declared type. A missing
or uncoercible column is a recoverable failure
([10](10-data-model.md#declared-output-columns)). The declared columns reach the operator as well
as the check, so an operation writing its own query or program can aim at them (ADR-012).

The two orderings are deliberate. The parameter check runs before the billing, because the runner
wrote the parameters under that same schema, so a mismatch is a bug rather than a spent call. The
billing runs before the operator, so a call that really happened still counts when the column check
rejects its output. An operation that writes something of its own from a model, as `builtin.nl2table`
does, writes it on the far side of that line, so what it got wrong costs an operator call where a
bad parameter costs none, however many times it writes it again (ADR-020).

The caller passes the fragment's `required_output_columns` and nothing else, because a fragment call
produces no intermediate tables to check the grouped nodes against. Neither a zero-row table nor an
empty bundle is a failure. A model call an operator makes internally, as LOTUS does, happens inside
this call and is billed as part of it rather than tracked separately; an operation able to say what
that call cost puts the figure in its report, which travels out on the result unchanged.

`project_answer` turns a finished run into its answer. It takes the result of the node `RETURN`
reads and the column names `RETURN` carries, and returns those columns and nothing else. Which
columns hold the answer was settled by the logical planner when it wrote the node, so nothing is
chosen here and no model is called; the choice is applied. A column the tables do not carry raises
`ReturnAnswerError`, and the run loop answers it with a replan rather than with a failed request,
because naming those columns is the logical planner's work to do again (ADR-034).

It sits beside the operator call because both are deterministic work over a bundle of tables, and
because the alternative is the run loop growing the only place it touches rows.

Three things `execution/` does not do, and each one removes an import. It does not schedule, because
the run loop decides what is ready and hands down one call at a time. It does not resolve an
operator id, because `FragmentRunner` hands it a resolved operator. It does not call a model,
because writing parameters and reviewing a result both belong to the runner (ADR-011).

---

## `operators/`

The router, one adapter per outside suite, and the one suite this repository owns. `blue-data-ops`
owns the Blue operators, the abstract `Operator`, and the `OperatorRegistry` that versions them;
LOTUS owns its own; and an adapter is the only place either dependency is imported.

`builtin/` is the owned suite (ADR-020): deterministic operations covering the operator node kinds,
and one compound operation, `builtin.nl2table`, that writes a Python program from a fragment's
instruction and runs it. It reaches the router the same way an adapter's operations do, through a
function returning a list.

`__init__.py` exports the router, `OperatorSuite`, `operator_suite`, and `OPERATOR_SUITE_NAMES`,
and nothing of a suite itself. Every suite is imported from the module named
for it, an outside suite's from `adapters/`, which re-exports no adapter of its own. Importing the
router therefore loads no suite, and importing one adapter loads no other. A required suite is
loaded when its adapter module is; an optional one only when one of its operators runs (ADR-004).

### `OperatorRouter`

```python
class OperatorRouter:
    def register(self, operator: PhysicalOperator) -> None: ...
    def operators_for(self, logical_op: LogicalOperator) -> list[PhysicalOperator]: ...
    def resolve(self, operator_id: str) -> PhysicalOperator: ...
```

Lookup, and nothing more. `register` rejects a repeated `operator_id`. `operators_for` returns every
registered operator declaring that logical operator, in registration order, and is what the physical
planner reads while it enumerates candidates. `resolve` turns a cover's recorded `operator_id` back
into the operator, and raises on an id nothing registered; the fragment runner is its only caller.

There is no startup coverage check, asking whether every logical operator has at least one
implementation registered. Fragments make that question weaker than it looks: an operator declaring
`DISTINCT` alongside SQL sources does not make a `DISTINCT` node over rows that arrived from another
fragment runnable, so passing the check proves nothing about any particular node. The question that
matters is asked per node, by the physical planner, and answered as `no_operator`, which returns to
the logical planner without spending an operator call
([04](04-detailed-architecture.md#failures-that-skip-recovery)).

Only the router is read while planning, never `OperatorRegistry` (ADR-009). The two answer different
questions and use different id formats: a registry operator id is the operator's `name`, an `@`, and
a version, such as `select@2.0.0`; a router id is suite-qualified, such as `blue.select`. The
adapter that produces a `PhysicalOperator` from a registry class is the only thing holding the two
in agreement.

### What an adapter reads

An adapter turns one suite's operations into `PhysicalOperator` values, carrying the declaration
physical planning reads: the `logical_operators` an operation can carry out, the `source_kinds` it
can read, whether it `accepts_bound_inputs`, and its `limitations`. An operation declaring one
logical operator can only ever cover one node; a set is what makes pushdown possible.

`source_kinds` is written in `blue-data`'s connection-protocol vocabulary: the string each source
class records at registration, such as `sql/sqlite`, `vector/chromadb`, or `network/bn`. It is the
same vocabulary a `SourceRecord.kind` reports
([10](10-data-model.md#what-the-logical-planner-reads-first)), so a declaration and a source are directly
comparable.

`accepts_bound_inputs` constrains only an operation that reads a registered source. One with no
source of its own receives everything it works on from the fragment's frontier, so there is nothing
for the field to allow or forbid, and it reads n/a.

A declaration has to describe what the operation does, because planning binds against the
declaration and never looks inside the operation. One that overstates a capability turns into a
candidate the operator cannot serve, and the failure arrives partway through a run where the
alternative was never being offered at all. Where a declaration comes from, and what an adapter may
absorb to make one true, are open questions 37 and 38 in
[13-decision-log.md](13-decision-log.md#open-questions).

What each suite's operations do is the suite's own reference: `blue-data-ops` for the Blue
operators, LOTUS for its own. Which of them a plan can be bound to is this repository's answer, and
a narrower one: an operation reaches the router only once its adapter carries an entry for it and a
parameter model to fill (ADR-010), so registering an operator upstream does not put it in play here
([07-repo-organization.md](07-repo-organization.md#where-a-new-thing-goes)).

A logical operator no registered operation declares is answered as `no_operator`, and the node goes
back to the logical planner. A gap in the registered suites therefore shows up as a replan rather
than as a wrong answer, and the run record names the node that had no candidate. With `builtin.nl2table`
registered every operator node kind has a candidate, so the answer is rare, and work no candidate
can carry is turned down while the cover is written rather than after an operator call. What keeps the physical planner reaching for a built-in operation where a
semantic one was needed is open question 43 in
[13-decision-log.md](13-decision-log.md#open-questions).

### What each suite offers

```python
def blue_operators(
    registry: OperatorRegistry, data_registry: DataRegistry
) -> list[PhysicalOperator]: ...


def lotus_operators(
    model: str, max_rows: int = 5000, reasoning_effort: str | None = None
) -> list[PhysicalOperator]: ...


def builtin_operators(
    data_registry: DataRegistry,
    llm_client: LLMClient,
    with_nl2table: bool = False,
) -> list[PhysicalOperator]: ...
```

Each returns its `PhysicalOperator` values, and whoever assembles the planner registers what comes
back. Any of the three sits beside the router and could register into it directly; returning a list
is kept because it makes a suite's whole result assertable in one test.

A deployment names its suites as strings, so each suite also has an object holding the options that
suite takes, and one lookup turns a name into one:

```python
class OperatorSuite(Protocol):
    name: ClassVar[str]

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]: ...


def operator_suite(name: str) -> OperatorSuite: ...
```

`BuiltinSuite`, `LotusSuite`, and `BlueSuite` each carry their own options — whether the compound
entry is registered, the model and row cap LOTUS runs under, the registry the Blue operations
resolve against — so the options one suite takes are declared beside that suite and nothing that
builds a planner names them. A name no suite registers raises `UnknownOperatorSuiteError`, which is
what keeps a typo in a suite list from running with fewer operators than it asked for. A suite
naming no model of its own runs on the planner's, and `BlueSuite` naming no registry resolves
against the one `blue-data-ops` builds by default.

Nothing else changes when a suite is added. The physical planner builds its candidates from the
router while it covers a plan, so a newly registered operator competes for every fragment its
declaration allows without a code or prompt edit.

Every callable validates parameters, invokes the operation, and maps errors, converting between the
bundle and its own suite's shape wherever the two differ (ADR-012). How much conversion the Blue
adapter needs is open question 41 in [13-decision-log.md](13-decision-log.md#open-questions). The
LOTUS adapter converts at its own edge, building one data frame per table going in and flattening
coming out, and imports LOTUS inside the callable rather than at module import time, so the package
imports with LOTUS absent and the router simply gains no LOTUS operators (ADR-004).
A column the operation created holds the model's text, including where the model had no answer.
The adapter reads a blank cell in such a column as `None`. In a created column declared other than
`str`, it also reads `null`, `none`, `nan`, and `n/a` as `None`, ignoring case. A column declared
`str`, or not declared at all, keeps those words, since `"None"` can be a real value there.

### The Blue suite

Eighteen operations reach the router, in four groups.

| Group | Operations | Covers |
| --- | --- | --- |
| Relational | `blue.select`, `blue.project`, `blue.join`, `blue.intersect`, `blue.union` | `FILTER`, `PROJECT`, `JOIN`, `UNION` |
| Aggregate | `blue.sum`, `blue.avg`, `blue.min`, `blue.max`, `blue.argmax`, `blue.argmin` | `AGGREGATE`, `SORT_SUPERLATIVE` |
| Semantic | `blue.semantic_filter`, `blue.semantic_project`, `blue.semantic_extract`, `blue.semantic_transform`, `blue.semantic_join` | `FILTER`, `PROJECT`, `JOIN` |
| Query-writing | `blue.nl2llm`, `blue.nl2sql` | `SCAN`, and six more kinds for `blue.nl2sql` |

Seventeen of them declare one logical operator and cover one node. Two node kinds take several
operations each, because summing and averaging are different operations sharing a node kind rather
than two implementations of one: an `AGGREGATE` node asking for an average has `blue.avg` as its
candidate and `blue.sum` is not one.

The first two groups cover only part of the node kinds they declare, and their `limitations` say
which part. `blue.select` compares one key against one value, where a `FILTER` node may carry a
conjunction of predicates, and the aggregate group works over a whole table, where an `AGGREGATE`
or `SORT_SUPERLATIVE` node may ask for one result per group. A built-in entry declares the same
kinds and covers more of them, so the narrower operation has to state its bound for the physical
planner to tell the two apart (open question 43).

The semantic group judges meaning with a language model, and each operation resolves the model it
calls, so what these operations spend is billed as part of the operator call that contains it
(ADR-005).

`blue.nl2llm` declares `SCAN` and reads no registered source. It answers from the model's own world
knowledge, which is why its rows cannot be held to a source and why it declares no source kind.

`blue.nl2sql` is the suite's compound operation. It declares seven kinds, the three SQL source
kinds, and bound inputs, so a connected group of nodes over a SQL source is a candidate for a single
call. Two limitations narrow it. It cannot express a semantic predicate, so a filter that needs
reading text stays outside the group. And one query reads one database, so a fragment whose scans
name two sources is not its to take, a constraint that lives in `limitations` because the
declaration has no vocabulary for it (open question 44).

Each operation names the registry version it binds, rather than the suite naming one version for all
eighteen. A version is where an operation's validator and its result shape are fixed, so an upstream
release changes what an operation does here only once the adapter names the new version, and two
operations whose versions move at different times do not have to move together.

An operation declaring one logical operator is handed the tables that kind's arity names, and a
bundle of any other length is refused before the operation runs, as a recoverable failure naming
both counts. `blue.nl2sql` is held to no count, since the group it covers decides how many tables
arrive.

What an operation reports about its own run reaches the fragment's result, whether the call
succeeded or failed (ADR-020). An operation that reports nothing leaves it unset.

### The LOTUS suite

Six operations reach the router, each declaring one logical operator, so a LOTUS operator covers one
node and never a group.

| Operation | Covers |
| --- | --- |
| `lotus.sem_filter` | `FILTER` |
| `lotus.sem_map` | `PROJECT` |
| `lotus.sem_extract` | `PROJECT` |
| `lotus.sem_agg` | `AGGREGATE` |
| `lotus.sem_topk` | `SORT_SUPERLATIVE` |
| `lotus.sem_join` | `JOIN` |

All six judge meaning with a language model and work on the tables the fragment's frontier
delivers. None declares a source kind, `accepts_bound_inputs` reads n/a for every one, and a model
string is all a deployment configures for the suite. That string reaches LOTUS's own configuration
rather than `llm/`, so what these operations spend is billed as part of the operator call that
contains it (ADR-005).

Two of them declare a limitation their name would otherwise hide. `lotus.sem_agg` summarizes each
group of rows into one text value and computes no sum, average, or count, so it answers an
`AGGREGATE` node only where the node asks for a summary. `lotus.sem_join` compares every pair of
rows, so what it costs grows with the product of the two tables.

What the suite spends grows with the rows it is handed, so it takes a row cap, 5,000 by default,
and refuses an input above it before calling a model. `lotus.sem_join` counts the two tables
multiplied together, which is how many comparisons it makes. The refusal is recoverable and says
that a deterministic operator can take the node, so a fragment that arrived with too many rows is
answered by another operator rather than by a bill. Whether a bound an operator enforces inside
its own call belongs in `RunLimits` is open question 45 in
[13-decision-log.md](13-decision-log.md#open-questions).

An instruction names the columns it reads in LOTUS's own notation, which the parameter schema states
so that a runner writing one has it in front of the model. A column an instruction names and the
table does not carry is refused before any model call, as a recoverable failure the parameter repair
answers.

### The built-in suite

`builtin/` is written here rather than wrapped (ADR-020), and holds two kinds of entry over one
shared module of transformations on the tables a bundle carries. No entry calls another: both kinds
call those functions, and so does the program `builtin.nl2table` writes.

| Entry | Declares | Holds a model call |
| --- | --- | --- |
| A transform entry | One logical operator | No. It does what its parameters say |
| `builtin.scan` | The scan alone | No |
| `builtin.nl2table` | Every operator kind | Yes. It writes a Python program from the fragment's instruction and runs it |

A transform entry takes one logical node whole. Its parameters reach as far as its node kind does,
so a filter combining conditions under and, or, and not arrives as one node and runs as one call.
What a logical plan says is settled before an operator is chosen, and no node is split to fit what
an entry can express. `builtin.filter`'s comparisons include a regular-expression search over a
string column, run deterministically against the stored value: a condition on a string's shape,
such as a prefix or a first token, is this entry's work, and a condition on what the string means is
a semantic operator's.

`builtin.scan` is the suite's one source-reading deterministic entry, and it reads through `data/`.
It declares `SCAN` alone: it groups no `FILTER` and no `PROJECT`, and either kind of node downstream
of a scan runs as its own fragment, through the matching transform entry. What columns it asks the
source for is its own implementation choice: `builtin.scan` always reads every column (`SELECT *`).
Its node's `required_output_columns` declaration ([10](10-data-model.md#declared-output-columns))
only checks what must be present in the result and coerces it to type; it does not limit the result
to those columns, so a wider row than the declaration names still passes. Narrowing to exactly a
named set is `PROJECT`'s job, run as its own fragment when the plan asks for it. Given a reference
input ([10](10-data-model.md#how-many-inputs-a-node-takes)), `builtin.scan` binds that input's
values into the read, which is the bind join of
[04](04-detailed-architecture.md#what-an-operator-does-with-its-input); how a bind join reaches the
source is `data/`'s question, taken up below. Its `source_kinds` name the kinds it reads and grow as
more are implemented. It does not retry inside itself: a read the source refused comes back as a
recoverable failure, and the parameter repair rewrites what asked for it.

`builtin.nl2table` is the suite's black box, and the case the pushdown design is written against. It
reads registered sources, accepts bound inputs, and is the one operator with no same-source
constraint, so a fragment scanning two sources has a candidate. A fragment scanning none has one
too, since an operator that owns its engine needs no source to be bound.

The call has two halves. First the operator reads every source the fragment named, carrying a
reference input's values into a read as a bind join wherever its parameters bind one, the way
`builtin.scan` does. Then it writes a Python program from the fragment's instruction and runs that
program over those tables together with the ones the frontier delivered.

The program runs in a child process holding copies of those tables, and is given no source, no
registry, and no model client. Three properties follow, and the entry's declaration rests on them.
It does not judge meaning, so a predicate that needs reading text belongs to a semantic operator.
It reads no source the cover did not bind. It changes nothing a source holds. Each of the three is
a fact about what the call hands the program rather than a restraint on a program written to do
otherwise: nothing checks the program before it runs, so a program that opens a source itself is
outside all three. The entry is experimental, and a deployment registers it or does not.

The two halves fail differently, and the split follows what a repair could fix. A scan target that
is not a catalog path, a bound column an input does not carry, a source that refused the read: no
program was at fault, so the operator returns a recoverable failure and the repairs outside rewrite
the parameters. A program that will not parse, one that raises, one that runs too long, one that
produces something other than a table: the failure names what to change, so the entry writes the
program again with that failure in front of the model, up to an attempt count it holds itself.
Spending that count returns a recoverable failure like any other.

Its report carries the program that ran, each program that did not and what it did, the query behind
each scan, and what its own model calls cost. Those counts travel there rather than in
`RunResult.conversations`, which holds what the planner itself spent (ADR-005).

---

## `data/`

The only module in the shipped package that imports `blue-data` (ADR-003). Source and store
construction sit behind it too, so nothing outside `data/` constructs a `blue-data` class or calls a
method on one. It hands out the registry type along with the readings, so a signature elsewhere can
name a registry it only passes back here.

```python
def connect_source(registry: DataRegistry, source: str) -> Any: ...
def execute_query(
    registry: DataRegistry,
    source: str,
    query: str,
    database: str,
    collection: str | None = None,
) -> Data: ...
def execute_write(
    registry: DataRegistry,
    source: str,
    statement: str,
    database: str,
    collection: str | None = None,
) -> None: ...
```

A registry is a value this module hands out and takes back, and every call made on one happens here.
Whoever else holds one — a caller assembling a planner, an adapter over an operator suite that takes
a registry of its own (ADR-004) — holds it without reading it.

`open_registry` is how a registry enters, and it is the one way in. Reading the catalog is
`DataCatalog`. Both are below.

`execute_query` runs a query and returns its rows; `execute_write` runs a statement that changes
what a source holds and returns nothing; `connect_source` hands back the live source object both of
them run through. All three are what `builtin.scan` needs, and together they are the whole of what
this boundary offers over a source. Nothing here reads a source's schema or its
properties: catalog detail below the source level reaches a run through `DataCatalog`, whose
readings come from the catalog records rather than from the source.

A call that fails does so in one of two ways, and they are told apart because a run answers them
differently. A source the registry cannot resolve to a connectable object raises
`SourceUnreachableError`, which no other query would have avoided. A source that was reached and
refused what it was sent raises `SourceQueryError`, which is what the parameter repair exists to
rewrite. Anything else a call meets — a registry record that is not a record, a result that is not a
table — is malformed state and raises as the bug it is.

A source names a database, and a collection where its kind has them. One registered source can hold
several databases, so `execute_query` cannot infer which one a query runs against — the benchmark
registry holds five under one source. `database` is therefore required and `collection` optional.

Two kinds of write have to be told apart, because the design answers them differently.

| Write | Goes to | Done by |
| --- | --- | --- |
| A bind join's values | The source | An operator declaring `accepts_bound_inputs`, choosing among three realizations, below |
| Registering a source, syncing it, collecting its statistics, enriching it | The registry | `blue-data`'s own tooling, before a registry ever reaches this package |

A bind join carries a reference input's values into a source none of them were read from
([04](04-detailed-architecture.md#what-an-operator-does-with-its-input)). What it can push determines
how it is realized.

| Realization | Fits | What travels |
| --- | --- | --- |
| Values embedded in the query text, an `IN` list | A source with a predicate language, few enough distinct values | The values, inside the query `execute_query` runs |
| A temporary table | A source with a predicate language, many distinct values | A table, written through `execute_write`, then joined against inside the source |
| One call per value | A source with no predicate language: a document store, or an API answering one key at a time | Nothing but the key, once per distinct value, through `execute_query` called repeatedly |

Choosing among the three is the operator's decision, not the planner's: the operator is what sees how
many rows travel and what the source can accept. Two of the three cap the distinct values they will
carry. One call per value carries the tighter cap, since every value is its own round trip: 10
distinct values. Values embedded in the query text carry a looser one, since the source does its own
filtering: 100 distinct values.

An operator implementing only one of the two capped realizations refuses an input past its cap
rather than truncate it silently, which is a recoverable failure like any other input an operator
cannot serve (ADR-015). An operator implementing more than one may instead escalate past a cap to a
realization it also implements. A temporary table is capped by nothing, which is what it is for:
without it a bind join into a source with a predicate language could never scale past an `IN` list.

`execute_write` is what the temporary-table realization needs and the other two do not: embedding
values in the query text and reading one value at a time both run through `execute_query` alone.
Pushing a table into a source is how a fragment reaches data another fragment produced, so the
boundary permits that write and no other.

The operator that wrote such a table drops it before its call returns, on the failing path as much
as on the succeeding one, under a name unique to the call, so a request leaves nothing behind in a
source a caller owns (ADR-023).

The second is ingestion, and the boundary refuses it. A registry reaches this package already
populated, so `register_source` and the sync, statistics, and enrichment calls have no place here.
That is a real constraint on a caller, not just a tidier signature list: a caller with data and no
registry builds one with `blue-data` first, and only then constructs a planner over it. It is also a
constraint on planning quality, which depends on how well that population was done — a source nobody
enriched renders with names and kinds alone, and planning still works with it
([10](10-data-model.md#4-catalog-readings)).

A write that creates a table through a source and registers it in one call looks as though it sits
between the two, and it does not arise. No node in the logical vocabulary asks for a write
([10-data-model.md](10-data-model.md)), so no plan can ask for one and no adapter offers an
operation that performs one. Adding a write to the vocabulary is a decision-log entry rather than an
adapter entry ([07-repo-organization.md](07-repo-organization.md#where-a-new-thing-goes)).

### `DataCatalog`

```python
class DataCatalog:
    def __init__(self, registry: DataRegistry, max_search_results: int = 3) -> None: ...

    def preview(self, request: str, limit: int) -> CatalogPreview: ...
    def search_records(self, term: str) -> list[EntityMatch]: ...
    def attribute_views(self, entity_path: str) -> list[AttributeView]: ...
```

Three readings of the same registry, all fixed in
[10, catalog readings](10-data-model.md#4-catalog-readings).

| Method | Read by | Answers |
| --- | --- | --- |
| `preview` | `LogicalPlanner`, once per request, until the first cover installs | Which sources exist. Source-level records only, ranked against the request and capped, with the total count so the planner can tell a complete list from a sample |
| `search_records` | `CatalogSearcher`, for a `DISCOVER` node | Which entities the request needs and what each holds. One call per search term |
| `attribute_views` | `FragmentRunner`, for each entity its fragment scans, and `PhysicalPlanner`, for each entity its plan scans | How each of that entity's attributes spells its values |

`CatalogSearcher` is the only caller of `search_records`. A `SCAN` names an entity the run's catalog
context already holds, and a path reaches that context only by having been searched for, so no
second search arises.

`max_search_results` is fixed when the catalog is built, since `search_records` runs once per search
term and one cap serves every term. It is small because a match now carries an entity's whole
attribute list and that list travels in every planning prompt for the rest of the run: ten
entities' worth is more than a request needs to choose between. `preview`'s cap comes from the
logical planner, which decides how much of a registry one prompt carries.

`attribute_views` takes one entity and returns a view per attribute it holds, with no way to ask for
a subset, because the caller that would select is the one deciding which attributes to name and has
not decided yet ([10](10-data-model.md#what-the-fragment-runner-reads)).

`DataCatalog` selects and renders what `blue-data` has already collected and enriched. It profiles
no attribute, samples no table, and infers no type of its own, so an `AttributeView` is a projection
rather than a computation: it pairs an attribute's stored statistics and value semantics with its
description. What belongs to this repository is the choice of what each component sees, which is
prompt shaping.

Every reading that searches ranks its results and takes the top of the list (ADR-021). A weak match
therefore reaches a prompt at the bottom of a ranked list, where a model weighs it against the rest.
`search_records` also drops every entity scoring zero, which is one no signal matched, so a term that
matches nothing returns nothing; `preview` keeps every source it ranked.

Planning quality depends on whether a source has been enriched. A source that never was renders with
names and kinds alone, which planning still works with. An entity nobody enriched still returns its
attributes with their names and types, which is what the nodes reading a scan need to declare
their columns.

### Opening a registry

```python
def open_registry(registry_path: Path) -> DataRegistry: ...
```

Reads a serialized `blue-data` registry into memory. A reopened registry reconnects to its own
sources, so the databases behind it stay where they are and a query runs against the live files.

A caller holding a registry it assembled with `blue-data` passes that one to `DataCatalog` directly.
Populating a registry is ingestion, and it belongs to `blue-data` (ADR-003). The TAG-Bench registry
is written once by `lib/scripts/tag_bench/register_databases.py`, which calls `blue-data` directly:
a script under `lib/scripts/` ships in no package, so the import rule that governs
`src/blue_data_planner/` does not reach it ([07-repo-organization.md](07-repo-organization.md),
[08-coding-style.md](08-coding-style.md)).

---

## `llm/`

The only module that calls a provider (ADR-005).

| Name | Role |
| --- | --- |
| `LLMClient` | One provider call, through litellm, with retry and rate limiting |
| `LLMClientProvider`, `CachingLLMClientProvider`, `resolve_llm_client` | Injection points, so a client is supplied rather than constructed in place |
| `Conversation`, `Turn` | The turns of one exchange, so a later turn sees an earlier one |
| `UsageLedger`, `UsageRecord` | One record per general provider call a client makes, with totals. A run reads its spend off its conversations instead |
| `LLMUsage` | One model call: model, prompt and completion tokens, cost, and latency. Defined in `types/`, since it reaches a caller inside `RunResult` |
| `LLMError` | The provider failing after retries, or answering in a shape the client cannot read. Defined in `types/`, like every exception the library defines |

The planner reaches a provider through one call: it returns a value constrained by a Pydantic
response schema, over a conversation that keeps its turns.

```python
class Conversation:
    turns: list[Turn]
    usage: list[LLMUsage]  # one entry per call made on this conversation


def complete_structured(self, conversation: Conversation, response_model: type[M]) -> M: ...
```

The conversation is what makes error recovery work at all three stages: the logical planner appends a
validation error and asks again, the physical planner rebinds on top of the binding that failed, and
the fragment runner reviews a result in the exchange that wrote its parameters.

A conversation belongs to the run rather than to the component that writes into it. `RunState` holds
them, hands the right one to each call, and a component appends turns to what it was given
([04, one conversation per component](04-detailed-architecture.md#one-conversation-per-component)).

It also carries the usage, one entry per call made on it. A run's spend is therefore the usage across
the conversations the run state holds, with no component reporting it separately and no accumulator
class, since a total is a `sum()` over the list.

`RunResult.conversations` therefore covers calls through this module and nothing else. A model call
an operator makes internally, as a LOTUS operator does, is invisible here:
LOTUS holds its own model configuration, is not routed through `llm/`, and reports no per-call token
count, so the call is billed as part of the one operator call that contains it
([05-software-stack.md](05-software-stack.md#two-model-configurations-one-budget)).

`LLMClient`'s general provider call, `execute_api_call`, has an async twin, `aexecute_api_call`, for
parallel provider calls — the library's one departure from being synchronous
([08-coding-style.md](08-coding-style.md)). Prompts are written by hand and versioned in the
repository (ADR-005).

---

## Errors

Fail fast and loudly, with one exception: a failure a planner is expected to recover from gets a
dedicated type, and a run that exhausts an allowance returns a status. Every exception, where it is
raised, and whether it reaches a caller is in
[09, exceptions](09-api-interface-spec.md#exceptions).

A recoverable failure is caught by the component positioned to answer it. `LogicalPlanner` catches
`PlanValidationError` and writes the plan again, `DataPlanner` answers `ReturnAnswerError` with a
replan, and `FragmentRunner` turns `BudgetExhaustedError` into `budget_exhausted`. An operator
reading a source turns `SourceQueryError` into a recoverable operator error and
`SourceUnreachableError` into an unrecoverable one.

Every exception this library defines inherits `BlueDataPlannerError`, defined in `types/errors.py`.
It does not inherit `blue-data-ops`'s base, so one `except` never silently covers both a planner bug
and an operator one. An adapter raises one of
the two operator errors and `execute_physical_operator` converts each into a failed
`ExecutionResult` carrying the matching `ErrorKind`. Nothing else is caught, so an operator raising
anything else reaches the interface boundary as the bug it is (ADR-015).

`LLMError` is the one failure caught at the top of the run loop rather than by the component that
provoked it. A provider outage is neither a programming error nor malformed state, so 04's rule
applies and it becomes a status, `llm_failed`, alongside `operator_failed`. A client that names no
model, carries an unknown `llm.*` key, or asks for a cache it cannot open is a setting error, and
`LLMClient` refuses it when it is built, so `llm_failed` always means the provider failed. Catching it there rather
than in each component is what keeps provider handling out of all four, and what lets the run
return everything it had already accumulated.

---

## Using it

Assembling a planner and running a request are shown in
[09, assembly](09-api-interface-spec.md#assembly) and in
[02-scenarios.md](02-scenarios.md#scenario-3-embedding-the-planner-in-an-application).
