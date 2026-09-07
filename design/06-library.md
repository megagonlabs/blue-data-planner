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
| `blue_data_planner/__init__.py` | The run path in one import: `DataPlanner`, `build_data_planner`, `PlanResult`, `PlanLimits`, `RunStatus`, `BlueDataPlannerError`, and `__version__` |
| `blue_data_planner/data_planner.py` | `DataPlanner`, `build_data_planner` |
| `blue_data_planner/run_state.py` | `RunState` |
| `blue_data_planner/logical_planner.py` | `LogicalPlanner` |
| `blue_data_planner/physical_planner.py` | `PhysicalPlanner` |
| `blue_data_planner/fragment_runner.py` | `FragmentRunner` |
| `blue_data_planner/catalog_searcher.py` | `CatalogSearcher` |
| `blue_data_planner/types/` | Every data class that crosses a module boundary: plans, nodes, fragments, operators, results, catalog readings, model usage, limits |
| `blue_data_planner/plan/` | `validate_logical_plan`, `validate_physical_plan`, and `candidate_fragments` |
| `blue_data_planner/execution/` | `execute_physical_operator`, `check_output_columns` |
| `blue_data_planner/operators/` | `OperatorRouter`, one adapter per outside suite (`blue_operators` over `blue-data-ops`, `lotus_operators` over LOTUS), and `builtin/`, the suite this repository owns |
| `blue_data_planner/data/` | The `blue-data` boundary, the three catalog readings, and opening a serialized registry |
| `blue_data_planner/llm/` | `LLMClient`, usage accounting, prompt templating, error types |

---

## The public entry point

```python
class DataPlanner:
    def __init__(
        self,
        logical_planner: LogicalPlanner,
        physical_planner: PhysicalPlanner,
        fragment_runner: FragmentRunner,
        catalog_searcher: CatalogSearcher,
        limits: PlanLimits,
    ) -> None: ...

    def run(self, request: str) -> PlanResult: ...

    def plan(self, request: str) -> PlanResult: ...


def build_data_planner(
    router: OperatorRouter,
    catalog: DataCatalog,
    llm_client: LLMClient,
    limits: PlanLimits | None = None,
) -> DataPlanner: ...
```

`DataPlanner` is given its four components rather than building them. It holds no model client, no
router, and no catalog, because every one of those belongs to a component that reads it. What it
owns is the loop.

| Component | Built with | Because |
| --- | --- | --- |
| `LogicalPlanner` | `llm_client`, `catalog`, `max_plan_corrections` | Writes plans, reads a catalog preview ranked against the request, and rewrites a plan validation turned down |
| `PhysicalPlanner` | `llm_client`, `router` | Enumerates candidates from what operators declare, and binds each scan to an entity discovery found. It is the one component built without a catalog, since every entity it can bind to arrives in `results` |
| `FragmentRunner` | `llm_client`, `catalog`, `router`, `max_parameter_attempts` | Resolves a fragment's operator id, reads the attribute views of the entities it scans, writes parameters, reviews the result, and writes them again when the review rejects one |
| `CatalogSearcher` | `llm_client`, `catalog`, `max_search_terms` | Turns an instruction into search terms and searches the registry for entities |
| `DataPlanner` | The four above, and `limits` | Creates the run state, schedules, chooses repairs, and assembles the result |

Injecting the components is what lets a caller give the three planning components different models, and what lets
the run loop be tested against four fakes and no provider. Everything a caller injects arrives
through a constructor; nothing is constructed in place, and nothing reads the environment.

Limits are fixed when the planner is built, so `run` and `plan` take a request and nothing else.
Three of the eight bounds are enforced inside a component's own loop rather than by the run loop,
and each of those components is built with the one number it obeys, so its constructor says which
bound that is. `build_data_planner` takes one `PlanLimits` and distributes those three, which is why
assembling by hand is the only path that sets a bound in more than one place.

`build_data_planner` is the default wiring, and the only thing an adapter needs. Without it the CLI,
the MCP server, and a future REST API would each repeat the same assembly, and wiring is not
translation ([07-repo-organization.md](07-repo-organization.md)).

### Two operations

Both take a request in plain language, and neither takes a plan. A plan is what a request produced,
so there is no entry point that accepts one and runs it, and `from_dict` reads a recorded plan
rather than feeding one back in.

| Operation | Does | Returns |
| --- | --- | --- |
| `run` | Plans, covers, executes, repairs, until the request ends | A `PlanResult` whose status says how it ended |
| `plan` | Writes one logical plan and one cover, then stops | A `PlanResult` with status `planned` and `answer` set to `None` |

`plan` is what the CLI's `--explain` prints and what a measurement of where pushdown boundaries get
drawn reads. It spends no operator call. A plan whose first pass ends in a `DISCOVER` node comes
back as it stands, with no search run, since resolving one would start the second pass.

`planned` is a `RunStatus` member alongside `ok` and the failure statuses. One result type serves
both operations, and the rule that `answer` is `None` unless the status is `ok` survives unchanged.
`plan` can also return `invalid_plan` and `llm_failed`, which are the two failures it can reach.

### The run loop

The loop schedules, dispatches on what came up, counts attempts, chooses the repair, installs a
cover, and assembles the result. None of those calls a model or invokes an operator directly.

```python
def run(self, request: str) -> PlanResult:
    state = RunState(request=request, budget=CallBudget(self._limits.operator_calls))
    try:
        status = self._plan_and_cover(state, reason=None)
        while status is None:
            status = self._run_pass(state)
    except LLMError:
        status = RunStatus.LLM_FAILED
    return state.to_result(status)
```

`None` means the request is not over. Every helper below returns `RunStatus | None` on that rule, so
the loop reads as one condition rather than a set of flags.

The one `try` in the library sits here rather than inside a component, which is what keeps every
component free of provider handling. Why a provider failure ends the request with a status rather
than an exception is under [Errors](#errors).

One pass takes what `next_ready` hands back and dispatches on it. A fragment goes to
`FragmentRunner`, and its result is recorded whatever it says. A control node is resolved. A plan
with nothing left ends the request as `ok`. The fragment's own `RunDecision` then says whether the
pass continues, ends the request with the status the runner returned, replans, or goes to a repair.

`next_ready` returns the next fragment whose frontier all has results, a control node whose inputs
all have results, or `None` when the plan is finished. A union return is what lets one scheduler
serve both, and the two are told apart by type rather than by a flag, since a control node belongs
to no fragment.

Which one comes first when both are ready is left open. Running the fragments first gives the
logical planner more results to write the continuation against; firing the control node first can
save operator calls on a branch the continuation turns out not to need. Neither has been measured,
so nothing here fixes the order, and open question 7 in
[13-decision-log.md](13-decision-log.md#open-questions) holds the trade.

Nothing here re-runs a fragment whose root already holds a result. That rule lives in `next_ready`,
so a cover that left most fragments alone resumes on top of what already ran with no bookkeeping
outside the run state.

### Choosing a repair

A rejected fragment goes to the narrowest repair with attempts left
([04, section 5](04-detailed-architecture.md#5-recovering-from-a-failure)). The parameter repair
never appears here, because `FragmentRunner` has already spent its attempts by the time it returns
anything other than `continue`.

The order is fixed. While `fragment_attempts` has an attempt left for this node set, the physical
planner covers that set again; then, while `cover_attempts` has one, it covers the whole plan again;
then the run replans. Each step spends its attempt before asking, and a planner that comes back with
no plan sends the run to the replan rather than ending it.

Only the loop knows how many attempts each repair has left, which is why the choice sits here rather
than in either planner. The two replan paths count separately, because a replan a control node asked
for and a replan a failure forced are different events with different limits, and either returns
`replan_exhausted` once its own bound is spent.

Resolving a control node searches the catalog when the node is `DISCOVER`, records what came back
under the node's id, and returns to the logical planner against `control_replans`.

One path leads into both planners, used for the first plan, for every continuation, and for the
cover that follows each. The logical planner writes a plan from the request, the parent plan, the
nodes carried over, the results the run holds, and the reason if there is one; a plan it could not
write ends the request with its status. The physical planner then covers whatever was installed, and
a cover it could not write sends the run to the replan.

A logical plan no operator can cover is the `no_operator` case from
[04](04-detailed-architecture.md#failures-that-skip-recovery). It goes straight back to the logical
planner without an operator call being spent.

---

## Shared types

`types/` imports nothing outside itself, so `execution/`, the planning components, and the adapters can
share a vocabulary without importing each other.

That rule fixes what belongs here, because a type `PlanResult` names has to be nameable by
everything that reads a `PlanResult`. Following it through reaches further than the plan types:
`PlanResult` holds `LLMUsage`, and the `DiscoveryResult` in its `results` map holds `EntityMatch`.
So the line is drawn once and drawn wide — **`types/` holds data, and every other module holds
behavior.** `data/` produces the catalog readings and `llm/` produces the usage records, and both
import their shapes from here.

[10-data-model.md](10-data-model.md) fixes every field and every JSON form; what follows is what
each class answers for and the methods it carries.

### The plan

```python
class LogicalOperator(str, Enum): ...   # the ten, named in 10-data-model.md
class ControlOperator(str, Enum): ...   # REPLAN, DISCOVER

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

A node carries one plain-language instruction and nothing else: no source, no operator id, no
operator parameters, and no result (ADR-008). All four are properties of a cover or of the run,
since all four can differ between passes over the same node.

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
constrains which sources the scans in such a fragment may be bound to
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

These live at the package root rather than in `operators/types.py`. Root `types/` imports nothing
outside itself, so a type it uses cannot live below it, and `ExecutionResult` and
`PhysicalOperator` both name a bundle. `operators/types.py` re-exports all three.

### Budgets and limits

```python
@dataclass(frozen=True)
class PlanLimits:
    ...   # one field per bound in the table below


class CallBudget:
    def __init__(self, cap: int) -> None: ...

    def spend(self, amount: int = 1) -> None: ...  # raises BudgetExhaustedError at the cap

    @property
    def spent(self) -> int: ...
```

Every bound the system obeys is a field of `PlanLimits`, and it is set once, when the planner is
built. Nothing overrides one per request, so a planner configured for a benchmark run and a planner
configured for an interactive session are two objects rather than one object called two ways.

Four of the eight are counted by the run loop and one by the call budget. The other three bound a
loop inside a component, and `build_data_planner` hands each of those components its own number.

| Bound | Enforced by |
| --- | --- |
| `operator_calls` | `CallBudget`, billed in `execute_physical_operator` |
| `fragment_attempts`, `cover_attempts`, `failure_replans`, `control_replans` | The run loop |
| `plan_corrections` | `LogicalPlanner`, rewriting a plan validation turned down |
| `parameter_attempts` | `FragmentRunner`, writing parameters again after a rejected review |
| `search_terms` | `CatalogSearcher`, capping what one `DISCOVER` node expands into |

Default values are in [12-deployment-ops.md](12-deployment-ops.md).

Model calls are not budgeted (ADR-014): how many a request makes already falls out of the bounds
above, and `PlanResult.planner_usage` records one entry per call so the spend stays readable.

### What a caller receives

`PlanResult`, `FragmentAttempt`, `ExecutionResult`, `DiscoveryResult`, `RejectedPlan`, and the four
enums that travel in them are fixed in
[10, what a run produces](10-data-model.md#3-what-a-run-produces). Every request returns a
`PlanResult` whether it worked or not: exhaustion, operator failure, and provider failure are
statuses rather than exceptions, so a failed run is as inspectable as a successful one and one
structure serves every adapter (ADR-015).

`RunStatus` has one member per way a request can end, and
[09-api-interface-spec.md](09-api-interface-spec.md#errors) lists them against what each means to a
caller. `planned` is what `plan` returns, and `llm_failed` is what a provider that fails after retries
returns. Neither is reachable from a component: `LogicalPlanResult` and `FragmentResult` carry a
`RunStatus` too, and both belong to the run loop.

The four per-component result types never leave the process. `DataPlanner` reads each one, records what
it holds on the run state, and moves on.

They are `LogicalPlanResult`, `PhysicalPlanResult`, `FragmentResult`, and `CatalogSearchResult`, and
[10, what each component returns](10-data-model.md#what-each-component-returns) fixes their fields.

Each component returns what it did instead of writing it down as it goes, so one that raises cannot
leave a half-written record behind and the run state keeps one writer. Model usage follows the same
rule: every component reports what its own calls cost, and `DataPlanner` appends each list to the
run in the order they ran.

`CatalogSearchResult` exists because `DiscoveryResult` goes into the run's results map and reaches
the caller inside `PlanResult.results`. Usage carried there would be counted twice, so the search
returns a pair: what it found, and what finding it cost.

One thing is accumulated rather than returned. `CallBudget` is billed inside
`execute_physical_operator`, and it has to keep its count when a fragment raises, so it travels as
an argument. `FragmentRunner` catches `BudgetExhaustedError` for the same reason, from the other
side.

---

## `plan/`

Everything that can be answered about a plan without a model, a source, or an operator call. The
module imports only `types/`, so a check and an enumeration are testable with a plan and nothing
else.

### Checking a plan

```python
def validate_logical_plan(
    plan: LogicalPlan, carried_node_ids: frozenset[str] = frozenset()
) -> None: ...


def validate_physical_plan(
    plan: LogicalPlan, physical_plan: PhysicalPlan, entity_paths: frozenset[str]
) -> None: ...
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

`entity_paths` is the run's catalog context flattened to the paths it names, and every value in
every fragment's `scan_sources` has to be one of them. A path the run never searched for is a path a
model wrote from nothing. The response schema already confines the choice where a provider enforces
one strictly, so this is the check that catches it where a provider does not
([05](05-software-stack.md#structured-output)). A frozenset rather than the results map keeps
`plan/` reading one kind of value.

The physical check is defensive in its other rules. The physical planner enumerates only fragments
that satisfy them, so a cover it assembles should already pass. It is worth running because a broken
cover does not fail loudly on its own: a fragment whose non-root node is read from outside leaves
that reader waiting for a table nobody produces, which shows up as a run that stops making progress
rather than as an error.

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

---

## The components

The three planning components, the discovery step, and the state the run loop writes.

### `LogicalPlanner`

```python
class LogicalPlanner:
    def __init__(
        self, llm_client: LLMClient, catalog: DataCatalog, max_plan_corrections: int
    ) -> None: ...

    def generate(
        self,
        request: str,
        parent: LogicalPlan | None = None,
        carried_node_ids: frozenset[str] = frozenset(),
        results: dict[str, ExecutionResult | DiscoveryResult] | None = None,
        reason: str | None = None,
    ) -> LogicalPlanResult: ...
```

One method serves the first plan and every continuation. With no `parent` it writes a whole plan.
With one it writes a continuation covering the remaining work, assembles it with the nodes
`carried_node_ids` names into a new version whose `parent_id` is the parent's id, and validates the
result.

The planner reads a catalog preview it fetches itself, ranked against `request`, rather than one
handed in. That costs one registry read per pass and removes the preview from four signatures and
from the run state.

`results` is the run's map for the parent version, and it answers two different questions. A
`REPLAN` node's continuation is written against the rows its inputs produced, so the planner reads
real columns and values. A `DISCOVER` node's entry holds the entities its search found and every
attribute each one holds, which is what lets the continuation's scans name a source and declare
column names the registry actually carries. One argument covers both because both live in the same
map.

`reason` is either a failure reason from the review or the physical planner, or the instruction from
the control node that ended the pass. It stays at the level the planner works in: it says what could
not be done rather than which operator failed to do it, because naming an operator would invite the
planner to work around an implementation it does not choose.

Two things happen inside `generate` rather than above it. The response schema is assembled from the
node schemas, so a plan naming an invented operation cannot be produced by a provider that enforces
one, and is refused when it is read where a provider does not
([05](05-software-stack.md#structured-output)). A plan that fails validation goes back to the same
conversation with the error appended, up to `max_plan_corrections`, and every rejected plan comes
back in `rejected` with the error that rejected it.

The prompt asks for a complete plan from `SCAN` through `RETURN` whenever the planner has enough to
write one, because `nl2sql` can only absorb a scan and its filters if those nodes exist by the time
the cover is written. Nothing in validation enforces that ([04, writing a plan that can be pushed
down](04-detailed-architecture.md#writing-a-plan-that-can-be-pushed-down)).

Over registered data the planner has enough on the second pass rather than the first. The preview it
reads names sources and nothing below them, so a first plan writes one `DISCOVER` node per thing the
request needs found, and the continuation is the complete plan
([04](04-detailed-architecture.md#1-logical-planning)).

### `PhysicalPlanner`

```python
class PhysicalPlanner:
    def __init__(self, llm_client: LLMClient, router: OperatorRouter) -> None: ...

    def cover(
        self,
        plan: LogicalPlan,
        results: dict[str, ExecutionResult | DiscoveryResult],
        reason: str | None = None,
    ) -> PhysicalPlanResult: ...

    def recover_fragment(
        self,
        plan: LogicalPlan,
        previous: PhysicalPlan,
        node_ids: frozenset[str],
        results: dict[str, ExecutionResult | DiscoveryResult],
        reason: str,
    ) -> PhysicalPlanResult: ...
```

Two entry points for three moments. `cover` writes a cover for the whole plan, and serves both the
first cover and the cover repair, which differ only in whether a failure reason is in the prompt.
`recover_fragment` cuts one node set again, which is the fragment repair.

Both return a whole new `PhysicalPlan`, because a cover is written whole and never patched into an
older one ([04](04-detailed-architecture.md#a-cover-is-written-whole)). What `recover_fragment`
changes is confined to `node_ids`: every other fragment in `previous` is carried into the new plan
with its node set intact, which is what lets the results of those fragments survive installation.

`results` answers two questions here too, and both entry points need it. A `DISCOVER` entry's
entities are the ones a `SCAN` may be bound to, and the presence of an entry says the node already
ran, which is the cost the model weighs when it considers absorbing that node into a wider fragment
([04, the cover repair](04-detailed-architecture.md#the-cover-repair)). `recover_fragment` needs the
first of those as much as `cover` does: rebinding a scan to a different entity is one of the fixes a
fragment repair can reach, and `previous` holds only the entity already chosen
([04, the fragment repair](04-detailed-architecture.md#the-fragment-repair)).

Inside, the planner works top down from the root of the plan. For each uncovered node it asks
`candidate_fragments` for the node sets each operator the router offers could cover, pairs each set
with that operator, and a model picks one. One covering operation is one conversation, with the
run's catalog context in the system message and one turn per binding decision, and a repair starts
a fresh one ([04, choosing among candidates](04-detailed-architecture.md#choosing-among-candidates)).

The response schema is built from that call's own candidates, so a candidate set with no `SCAN` has
no field in which to name a source, and where a scan does appear the entity paths in `results` are
the allowed values. The planner searches nothing itself: a path reaches it because a `DISCOVER` node
found it. `plan` is `None` exactly when some node has no candidate at all, alone or in any fragment
containing it, and `failure_reason` then says which node and why.

### `FragmentRunner`

```python
class FragmentRunner:
    def __init__(
        self,
        llm_client: LLMClient,
        catalog: DataCatalog,
        router: OperatorRouter,
        max_parameter_attempts: int,
    ) -> None: ...

    def run(
        self,
        fragment: PhysicalPlanFragment,
        inputs: DataBundle,
        budget: CallBudget,
    ) -> FragmentResult: ...
```

`run` takes the rows for one fragment rather than the plan they came from, and the budget rather
than the object holding it. Everything it works on arrives per call, so it holds nothing about a
request between calls and a test builds a fragment, some rows, a catalog double, and a counter.

The fragment carries the reading it needs. `scan_sources` names an entity path per `SCAN`, and one
`attribute_views` call per path returns every attribute of that entity, so nothing about the run
beyond the fragment has to be passed in. The run's whole catalog context is deliberately not: it
would put entities this fragment cannot reach in front of the model, and the deeper reading of the
ones it can reach is what writing parameters needs
([10, what the fragment runner reads](10-data-model.md#what-the-fragment-runner-reads)).

For one fragment it loops until a result is accepted or the parameter attempts run out.

It resolves the fragment's operator id through the router, describes the input tables and the
attribute views of the entities it scans, writes the operator's parameters against its
`parameter_schema`, calls `execute_physical_operator`, and reviews what came back.

A review that accepts ends the loop with `decision=continue`. One that rejects spends an attempt and
the parameters are written again; one that asks for a replan returns `decision=replan` with its
assessment as the failure reason; running out of attempts returns `decision=escalate`. An
unrecoverable operator failure returns `decision=stop` with `status=operator_failed` and no review
turn at all, since no other operator would avoid the same failure.

Writing the parameters and reviewing the result are turns in one conversation, so the review sees
what was written and why. Every attempt runs against the same `inputs`, read once by the caller, so
a reduction one attempt achieved cannot compound into the next.

`run` catches `BudgetExhaustedError` for one reason: a budget that runs out mid-fragment would
otherwise take with it every attempt already made, and those attempts and their usage are the record
of what the runner was doing when the request ended. Catching it is what lets them be returned as
`decision=stop` with `status=budget_exhausted`.

The absorbed nodes' instructions are what make a fused call checkable. A single call asked to scan,
filter, and join returns one table and no evidence of the steps inside it, so the review asks
whether each absorbed step is reflected in the result ([04, reviewing the
result](04-detailed-architecture.md#reviewing-the-result)).

### `CatalogSearcher`

```python
class CatalogSearcher:
    def __init__(
        self, llm_client: LLMClient, catalog: DataCatalog, max_search_terms: int
    ) -> None: ...

    def search(
        self, node: PipelineNode, inputs: DataBundle
    ) -> CatalogSearchResult: ...
```

Resolves a `DISCOVER` node. A model turns the node's instruction and the rows its inputs produced
into concrete search terms, capped at `max_search_terms`, and each term goes to
`DataCatalog.search_records`. The `DiscoveryResult` holds the terms, the `EntityMatch` values they
found, and how many terms the cap dropped. It is kept under the node's id in the run's results map,
and together with the other resolved nodes' entries it is the run's catalog context: the logical
planner reads it to write the continuation, the physical planner to bind a scan.

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
    ...

    @property
    def logical_plan(self) -> LogicalPlan: ...
    @property
    def cover(self) -> PhysicalPlan: ...
    @property
    def current_results(self) -> dict[str, ExecutionResult | DiscoveryResult]: ...

    def next_ready(self) -> PhysicalPlanFragment | PipelineNode | None: ...
    def inputs_for(self, fragment: PhysicalPlanFragment) -> DataBundle: ...
    def inputs_for_node(self, node: PipelineNode) -> DataBundle: ...
    def carried_node_ids(self) -> frozenset[str]: ...
    def install_logical_plan(self, plan: LogicalPlan) -> None: ...
    def install_cover(self, cover: PhysicalPlan) -> None: ...
    def record_fragment(self, result: FragmentResult) -> None: ...
    def record_discovery(self, node_id: str, result: DiscoveryResult) -> None: ...
    def record_rejected_plans(self, rejected: list[RejectedPlan]) -> None: ...
    def record_usage(self, usage: list[LLMUsage]) -> None: ...
    def to_result(self, status: RunStatus) -> PlanResult: ...
```

The one mutable object per request. `DataPlanner` creates it, `DataPlanner` alone writes to it, and
`to_result` freezes it into the `PlanResult` a caller receives. No component holds it: each takes what
it reads and returns what it produced, which is what keeps the four components carrying nothing
about a request between calls. It holds no `PlanLimits`, because the bounds belong to the planner
rather than to one run of it; the loop reads them off itself.

The four counters here are the ones the loop reads to choose a repair. `fragment_attempts` is keyed
by node set because that is the unit the fragment repair is bounded against, and covering a set
again either keeps it, spending another attempt, or splits it into smaller sets that each start
their own count. Splitting strictly shrinks a set, so the repair ends at single nodes.

`record_fragment` takes the whole `FragmentResult`, so it appends that fragment's attempts and its
model usage together. The logical and physical planners return usage beside their result, so the
loop appends it with `record_usage`, and `CatalogSearchResult` is split the same way.

Everything else `PlanResult.counters` reports is derived in `to_result` rather than tracked twice:
`operator_calls` from `budget.spent`, `plan_corrections` from the length of `rejected_plans`,
`parameter_attempts` from the most any one fragment spent in `attempts`, and `search_terms` from the
discovery entries in `results`. One number, one place it comes from.

Three methods carry the rules that would otherwise be scattered through the loop.

`next_ready` reads the current version's results slot. A fragment is ready when every node on its
frontier has an entry there and its own root does not; a control node is ready when every node it
lists has one. It rebuilds the runnable set on each call, so a new cover is picked up without a
restart.

`carried_node_ids` answers what a new logical version keeps: every node the run has something for,
which for an operator node means belonging to a fragment whose result the run still holds, absorbed
nodes included. A resolved `DISCOVER` node carries over and never searches twice. `REPLAN` and
`RETURN` never carry over, which is what stops a `REPLAN` node that fired from firing again and
removes the need for a separate set of handled nodes
([10](10-data-model.md#the-logical-half)). It is a method rather than a field because it is a
question about the current plan, the current cover, and the results map, and this is the only object
holding all three.

`install_cover` compares the new cover with the previous one fragment by fragment. A fragment whose
node set is unchanged keeps its root's result. One that changed or is new drops the results of every
node in it and of every executed node downstream of those, then runs again. The single-exit rule is
what makes the drop safe: no node outside a fragment was reading those tables. Nothing is lost by
the removal, since `attempts` still holds every attempt including the ones whose results were
dropped.

`install_logical_plan` adds the version, moves the pointer, and seeds its results slot with a copy
of the parent's restricted to the carried nodes. Because the values are shared references, that is a
map copy rather than a data copy.

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
    budget: CallBudget,
) -> ExecutionResult: ...


def check_output_columns(
    result: ExecutionResult,
    required_output_columns: list[OutputColumn],
    operator_id: str,
) -> ExecutionResult: ...
```

`execute_physical_operator` validates the parameters against the operator's schema, bills one
operator call, invokes the operator on the bundle it was handed, and checks the result with
`check_output_columns` against the declared columns, coercing each to its declared type. A missing
or uncoercible column is a recoverable failure
([10](10-data-model.md#declared-output-columns)).

The two orderings are deliberate. The parameter check runs before the billing, because the runner
wrote the parameters under that same schema, so a mismatch is a bug rather than a spent call. The
billing runs before the operator, so a call that really happened still counts when the column check
rejects its output. An operation that writes something of its own from a model, as `builtin.nl2table`
does, writes it on the far side of that line, so what it got wrong costs an operator call where a
bad parameter costs none (ADR-020).

The caller passes the fragment root's declarations and nothing else, because a fused call produces
no intermediate tables to check the absorbed nodes against. Neither a zero-row table nor an empty
bundle is a failure. A model call an operator makes internally, as LOTUS does, happens inside this
call and is billed as part of it rather than tracked separately; an operation able to say what that
call cost puts the figure in its report, which travels out on the result unchanged.

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
and one compound operation, `builtin.nl2table`, that turns a fragment's instruction into a sequence
of them and runs it. It reaches the router the same way an adapter's operations do, through a
function returning a list.

`__init__.py` re-exports `OperatorRouter` alone, and every suite is imported from the module named
for it. Importing the router therefore loads no suite, which matters because an adapter imports its
suite at module import time.

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
([10](10-data-model.md#what-the-logical-planner-reads)), so a declaration and a source are directly
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
than as a wrong answer, and the run record names the node that had no candidate. With `builtin`
registered every operator node kind has a candidate, so the answer is rare and a poor choice is
refused at review instead. What keeps the physical planner reaching for a built-in operation where a
semantic one was needed is open question 43 in
[13-decision-log.md](13-decision-log.md#open-questions).

### What each suite offers

```python
def blue_operators(
    registry: OperatorRegistry, data_registry: DataRegistry
) -> list[PhysicalOperator]: ...


def lotus_operators() -> list[PhysicalOperator]: ...


def builtin_operators(
    data_registry: DataRegistry, llm_client: LLMClient
) -> list[PhysicalOperator]: ...
```

Each returns its `PhysicalOperator` values, and whoever assembles the planner registers what comes
back. Any of the three sits beside the router and could register into it directly; returning a list
is kept because it makes a suite's whole result assertable in one test, and because it leaves which
suites a deployment offers to whoever assembles the planner.

Nothing else changes when a suite is added. The physical planner builds its candidates from the
router while it covers a plan, so a newly registered operator competes for every fragment its
declaration allows without a code or prompt edit.

Every callable validates parameters, invokes the operation, and maps errors, converting between the
bundle and its own suite's shape wherever the two differ (ADR-012). How much conversion the Blue
adapter needs is open question 41 in [13-decision-log.md](13-decision-log.md#open-questions). The
LOTUS adapter converts at its own edge, building one data frame per table going in and flattening
coming out, and imports LOTUS inside the callable rather than at module import time, so the package
imports with LOTUS absent and the router simply gains no LOTUS operators (ADR-004).

### The built-in suite

`builtin/` is written here rather than wrapped (ADR-020), and holds two kinds of entry over one
shared module of functions on the bundle. No entry calls another: both kinds call those functions.

| Entry | Declares | Holds a model call |
| --- | --- | --- |
| A transform entry | One logical operator | No. It does what its parameters say |
| `builtin.scan` | The scan alone | No |
| `builtin.nl2table` | Every operator kind | Yes. It turns the fragment's instruction into a sequence of the other operations and runs it |

`builtin.scan` is the suite's one source-reading deterministic entry, and it reads through `data/`.
It declares `SCAN` alone: it absorbs no `FILTER` and no `PROJECT`, and either kind of node downstream
of a scan runs as its own fragment, through the matching transform entry. What columns it asks the
source for is its own implementation choice, informed by its node's `required_output_columns`
([10](10-data-model.md#declared-output-columns)). That declaration only checks what must be present
in the result and coerces it to type; it does not limit the result to those columns, so a wider row
than the declaration names still passes. Narrowing to exactly a named set is `PROJECT`'s job, run as
its own fragment when the plan asks for it. Given a reference input
([10](10-data-model.md#how-many-inputs-a-node-takes)), `builtin.scan` binds that input's values into
the read, which is the bind join of
[04](04-detailed-architecture.md#what-an-operator-does-with-its-input); how a bind join reaches the
source is `data/`'s question, taken up below. Its `source_kinds` name the kinds it reads and grow as
more are implemented.

`builtin.nl2table` is the suite's black box, and the case the pushdown design is written against. It
reads registered sources, accepts bound inputs, and is the one operator with no same-source
constraint, so a fragment scanning two sources has a candidate. A fragment scanning none has one
too, since an operator that owns its engine needs no source to be bound.

Neither entry retries inside itself. A sequence `builtin.nl2table` got wrong comes back as a
recoverable failure, and the parameter repair rewrites it, so the four repairs stay the only place
attempts are counted (ADR-014).

The suite's model calls go through `llm/` like every other model call the library makes, so
`builtin.nl2table` reports its own token counts where an outside suite cannot. Those counts travel
in the call's report rather than in `PlanResult.planner_usage`, which keeps the scope its name
states (ADR-005).

---

## `data/`

The only module in the shipped package that imports `blue-data` (ADR-003). Source and store
construction sit behind it too, so nothing outside `data/` constructs a `blue-data` class or calls a
method on one.

```python
def connect_source(registry: DataRegistry, source: str) -> Any: ...
def execute_query(
    registry: DataRegistry,
    source: str,
    query: str,
    database: str,
    collection: str | None = None,
) -> Data: ...
```

A registry is a value this module hands out and takes back, and every call made on one happens here.
Whoever else holds one — a caller assembling a planner, an adapter over an operator suite that takes
a registry of its own (ADR-004) — holds it without reading it.

`open_registry` is how a registry enters, and it is the one way in. Reading the catalog is
`DataCatalog`. Both are below.

`execute_query` runs a query and returns its rows; `connect_source` hands back a live source object,
which is what an operator writes through. Both are what `builtin.scan` needs, and together they are
the whole of what a source-level read is for. Nothing here reads a source's schema or its
properties: catalog detail below the source level reaches a run through `DataCatalog`, whose
readings come from the catalog records rather than from the source.

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
| A temporary table | A source with a predicate language, many distinct values | A table, written through the object `connect_source` hands back, then joined against inside the source |
| One call per value | A source with no predicate language: a document store, or an API answering one key at a time | Nothing but the key, once per distinct value, through `execute_query` called repeatedly |

Choosing among the three is the operator's decision, not the planner's: the operator is what sees how
many rows travel and what the source can accept. Each realization caps the distinct values it will
carry, and an operator asked to carry more refuses the input rather than truncate it silently, which
is a recoverable failure like any other input an operator cannot serve (ADR-015). One call per value
carries the tightest cap, since every value is its own round trip: 10 distinct values. Values embedded
in the query text or in a temporary table share a looser cap, since the source does its own filtering:
100 distinct values. Both numbers are provisional, set low enough to bound a run rather than measured
against real bind-join volumes.

`connect_source` is what the temporary-table realization needs and the other two do not: embedding
values in the query text and reading one value at a time both run through `execute_query` alone. The
boundary has to permit the temporary table anyway, since without it a bind join into a source with a
predicate language could never scale past an `IN` list, and pushing a table into a source is how a
fragment reaches data another fragment produced.

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

Three readings of the same registry, one per caller, all fixed in
[10, catalog readings](10-data-model.md#4-catalog-readings).

| Method | Read by | Answers |
| --- | --- | --- |
| `preview` | `LogicalPlanner`, once per pass | Which sources exist. Source-level records only, ranked against the request and capped, with the total count so the planner can tell a complete list from a sample |
| `search_records` | `CatalogSearcher`, for a `DISCOVER` node | Which entities the request needs and what each holds. One call per search term |
| `attribute_views` | `FragmentRunner`, for each entity its fragment scans | How each of that entity's attributes spells its values |

`CatalogSearcher` is the only caller of `search_records`. The physical planner binds a `SCAN` to an
entity the run's catalog context already holds, and a path reaches that context only by having been
searched for, so no second search arises.

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

Planning quality depends on whether a source has been enriched. A source that never was renders with
names and kinds alone, which planning still works with. An entity nobody enriched still returns its
attributes with their names and types, which is what a scan needs to declare its columns.

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
| `LLMClientProvider`, `resolve_llm_client` | Injection points, so a client is supplied rather than constructed in place |
| `Conversation` | The turns of one exchange, so a later turn sees an earlier one |
| `LLMUsage` | One model call: model, prompt and completion tokens, cost, and latency. Defined in `types/`, since it reaches a caller inside `PlanResult` |
| `LLMError` | Every failure originating at the provider |

The planner reaches a provider through one call: it returns a value constrained by a Pydantic
response schema, over a conversation that keeps its turns.

```python
class Conversation:
    turns: list[Turn]
    usage: list[LLMUsage]  # one entry per call made on this conversation


def complete_structured(self, conversation: Conversation, response_model: type[M]) -> M: ...
```

The conversation is what makes two of the design's patterns work: the logical planner appends a
validation error and asks again, and the fragment runner reviews a result in the exchange that wrote
its parameters.

It also carries the usage, which is what lets a component report what its calls cost without an
out-parameter: the component reads `conversation.usage` when it builds its result. One making
several exchanges concatenates them. There is no accumulator class, since a total is a `sum()` over
the list.

`PlanResult.planner_usage` therefore covers calls through this module and nothing else, and the name
says so. A model call an operator makes internally, as a LOTUS operator does, is invisible here:
LOTUS holds its own model configuration, is not routed through `llm/`, and reports no per-call token
count, so the call is billed as part of the one operator call that contains it
([05-software-stack.md](05-software-stack.md#two-model-configurations-one-budget)).

An async twin exists for parallel provider calls, and is the library's one departure from being
synchronous ([08-coding-style.md](08-coding-style.md)). Prompts are written by hand and versioned in
the repository (ADR-005).

---

## Errors

Fail fast and loudly, with one exception: a failure a planner is expected to recover from gets a
dedicated type, and a run that exhausts an allowance returns a status.

| Raised | When | Caught by |
| --- | --- | --- |
| `PlanValidationError` | A generated plan fails a structural check | `LogicalPlanner`, which regenerates |
| `RecoverableOperatorError` | An operator refuses its input in a way a different operator might not | `execute_physical_operator`, which returns a recoverable failure |
| `UnrecoverableOperatorError` | An operator fails for a reason no other operator would avoid | `execute_physical_operator`, which returns an unrecoverable failure |
| `BudgetExhaustedError` | The operator call budget reaches its cap | `FragmentRunner`, which returns `budget_exhausted` with the attempts already made |
| `LLMError` | The provider fails after retries | `DataPlanner`, which ends the request with `llm_failed` |
| `ValueError`, `TypeError`, and a suite's own validation errors | Malformed state or a missing required value | Nothing. These are bugs and they propagate |

Every exception this library defines inherits `BlueDataPlannerError`, defined in `types/errors.py`.
It does not inherit `blue-data-ops`'s base, so one `except` never silently covers both a planner bug
and an operator one. An adapter raises one of
the two operator errors and `execute_physical_operator` converts each into a failed
`ExecutionResult` carrying the matching `ErrorKind`. Nothing else is caught, so an operator raising
anything else reaches the interface boundary as the bug it is (ADR-015).

`LLMError` is the one failure caught at the top of the run loop rather than by the component that
provoked it. A provider outage is neither a programming error nor malformed state, so 04's rule
applies and it becomes a status, `llm_failed`, alongside `operator_failed`. Catching it there rather
than in each component is what keeps provider handling out of all four, and what lets the run
return everything it had already accumulated.

---

## Using it

Assembling a planner, running a request, and reading a cover off the result are shown end to end in
[09, assembly](09-api-interface-spec.md#assembly) and in
[02-scenarios.md](02-scenarios.md#scenario-3-embedding-the-planner-in-an-application).
