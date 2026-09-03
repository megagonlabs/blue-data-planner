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
| `blue_data_planner/__init__.py` | `__version__`, and nothing else. The public surface is the modules below it (ADR-016) |
| `blue_data_planner/types/` | Every data class that crosses a module boundary: plans, nodes, fragments, operators, results, catalog readings, model usage, limits. Split across `errors.py`, `plan.py`, `catalog.py`, and `run.py`, re-exported by `__init__.py` as `blue_data_planner.types`. Imports nothing outside itself |
| `blue_data_planner/validation.py` | `validate_logical_plan`, `validate_physical_plan`, and their error type |
| `blue_data_planner/planner/logical_planner.py` | `LogicalPlanner` |
| `blue_data_planner/planner/physical_planner.py` | `PhysicalPlanner` |
| `blue_data_planner/planner/fragment_runner.py` | `FragmentRunner` |
| `blue_data_planner/planner/catalog_searcher.py` | `CatalogSearcher` |
| `blue_data_planner/planner/operator_router.py` | `OperatorRouter` |
| `blue_data_planner/planner/data_planner.py` | `DataPlanner`, `build_data_planner` |
| `blue_data_planner/planner/run_state.py` | `RunState` |
| `blue_data_planner/execution/` | `execute_physical_operator`, `check_output_columns` |
| `blue_data_planner/operators/` | One adapter per operator suite: `blue_operators` over `blue-data-ops`, `lotus_operators` over LOTUS |
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
| `PhysicalPlanner` | `llm_client`, `catalog`, `router` | Enumerates candidates from what operators declare, and binds each scan to a catalog path |
| `FragmentRunner` | `llm_client`, `catalog`, `router`, `max_parameter_attempts` | Resolves a fragment's operator id, reads column views, writes parameters, reviews the result, and writes them again when the review rejects one |
| `CatalogSearcher` | `llm_client`, `catalog`, `max_search_terms` | Turns an instruction into search terms and searches the registry |
| `DataPlanner` | The four above, and `limits` | Creates the run state, schedules, chooses repairs, and assembles the result |

Injecting the components is what lets a caller give the three stages different models, and what lets
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

The one `try` in the library sits here. A provider that fails after the client's retries is neither
a programming error nor malformed state, so it ends the request with a status like any other
failure, and the plans, attempts, and usage the run had already accumulated come back with it.
Catching it at the top of the loop rather than inside a stage is what keeps every stage free of
provider handling.

```python
def _run_pass(self, state: RunState) -> RunStatus | None:
    work = state.next_ready()
    if work is None:
        return RunStatus.OK
    if isinstance(work, PipelineNode):
        return self._resolve_control_node(state, work)
    result = self._fragment_runner.run(work, state.inputs_for(work), state.budget)
    state.record_fragment(result)
    if result.decision is RunDecision.CONTINUE:
        return None
    if result.decision is RunDecision.STOP:
        return result.status
    if result.decision is RunDecision.REPLAN:
        return self._replan_after_failure(state, result.failure_reason)
    return self._repair(state, work, result.failure_reason)
```

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

```python
def _repair(
    self, state: RunState, fragment: PhysicalPlanFragment, reason: str | None
) -> RunStatus | None:
    node_ids = frozenset(fragment.logical_node_ids)
    if state.fragment_attempts[node_ids] < self._limits.fragment_attempts:
        state.fragment_attempts[node_ids] += 1
        result = self._physical_planner.recover_fragment(
            state.logical_plan, state.cover, node_ids, reason
        )
    elif state.cover_attempts < self._limits.cover_attempts:
        state.cover_attempts += 1
        result = self._physical_planner.cover(
            state.logical_plan, state.current_results, reason
        )
    else:
        return self._replan_after_failure(state, reason)
    state.record_usage(result.usage)
    if result.plan is None:
        return self._replan_after_failure(state, result.failure_reason)
    state.install_cover(result.plan)
    return None
```

Only the loop knows how many attempts each repair has left, which is why the choice sits here rather
than in either planner. The two replan paths count separately, because a replan a control node asked
for and a replan a failure forced are different events with different limits.

```python
def _replan_after_failure(self, state: RunState, reason: str | None) -> RunStatus | None:
    if state.failure_replans >= self._limits.failure_replans:
        return RunStatus.REPLAN_EXHAUSTED
    state.failure_replans += 1
    return self._plan_and_cover(state, reason)


def _resolve_control_node(self, state: RunState, node: PipelineNode) -> RunStatus | None:
    if node.kind is ControlOperator.DISCOVER:
        found = self._catalog_searcher.search(node, state.inputs_for_node(node))
        state.record_usage(found.usage)
        state.record_discovery(node.id, found.discovery)
    if state.control_replans >= self._limits.control_replans:
        return RunStatus.REPLAN_EXHAUSTED
    state.control_replans += 1
    return self._plan_and_cover(state, node.attributes["instruction"])
```

`_plan_and_cover` is the one path into both planners, used for the first plan, for every
continuation, and for the cover that follows each.

```python
def _plan_and_cover(self, state: RunState, reason: str | None) -> RunStatus | None:
    plan = self._logical_planner.generate(
        state.request,
        parent=state.logical_plan_or_none,
        carried_node_ids=state.carried_node_ids(),
        results=state.current_results,
        reason=reason,
    )
    state.record_usage(plan.usage)
    state.record_rejected_plans(plan.rejected)
    if plan.plan is None:
        return plan.status
    state.install_logical_plan(plan.plan)
    cover = self._physical_planner.cover(plan.plan, state.current_results)
    state.record_usage(cover.usage)
    if cover.plan is None:
        return self._replan_after_failure(state, cover.failure_reason)
    state.install_cover(cover.plan)
    return None
```

A logical plan no operator can cover is the `no_operator` case from
[04](04-detailed-architecture.md#failures-that-skip-recovery). It goes straight back to the logical
planner without an operator call being spent.

---

## Shared types

`types/` imports nothing outside itself, so `execution/`, `planner/`, and the adapters can
share a vocabulary without importing each other.

That rule fixes what belongs here, because a type `PlanResult` names has to be nameable by
everything that reads a `PlanResult`. Following it through reaches further than the plan types:
`PlanResult` holds `LLMUsage`, and the `DiscoveryResult` in its `results` map holds `CatalogMatch`.
So the line is drawn once and drawn wide — **`types/` holds data, and every other module holds
behavior.** `data/` produces the catalog readings and `llm/` produces the usage records, and both
import their shapes from here.

[10-data-model.md](10-data-model.md) fixes every field and every JSON form; what follows is what
each class answers for and the methods it carries.

### The plan

```python
class LogicalOperator(str, Enum):
    SCAN = "SCAN"
    FILTER = "FILTER"
    PROJECT = "PROJECT"
    EXPAND = "EXPAND"
    AGGREGATE = "AGGREGATE"
    DISTINCT = "DISTINCT"
    SORT_SUPERLATIVE = "SORT_SUPERLATIVE"
    JOIN = "JOIN"
    UNION = "UNION"
    RETURN = "RETURN"


class ControlOperator(str, Enum):
    REPLAN = "REPLAN"
    DISCOVER = "DISCOVER"


INPUT_ARITY: dict[LogicalOperator, int]  # SCAN 0, JOIN and UNION 2, every other 1
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
questions about its own shape without reaching into `planner/` or `execution/` (ADR-011). `node`
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
    logical_plans: dict[str, LogicalPlan]
    physical_plans: dict[str, PhysicalPlan]
    current_logical_plan_id: str | None
    current_physical_plan_id: str | None

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

```python
@dataclass(frozen=True)
class PhysicalOperator:
    operator_id: str  # suite-qualified: "blue.nl2sql", "lotus.sem_filter"
    logical_operators: frozenset[LogicalOperator]
    description: str
    parameter_schema: type[BaseModel]
    call: Callable[[DataBundle, dict[str, Any]], DataBundle]
    source_kinds: frozenset[str] | None = None
    accepts_bound_inputs: bool = False
    limitations: str | None = None
```

One candidate implementation, as the router holds it and as both the physical planner and the
fragment runner read it. `logical_operators` is the whole matching rule: candidate fragments are the
connected, convex subgraphs built from nodes whose kind is in that set, and `source_kinds` then
constrains which sources the scans in such a fragment may be bound to
([10](10-data-model.md#a-physical-operator)).

A single-member set means the operator can only ever cover one node. `description` and `limitations`
carry the weight in the physical planner's prompt, since the parameter schema is what a later stage
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
    operator_calls: int
    plan_corrections: int
    parameter_attempts: int
    fragment_attempts: int
    cover_attempts: int
    failure_replans: int
    control_replans: int
    search_terms: int


class CallBudget:
    def __init__(self, cap: int) -> None: ...

    def spend(self, amount: int = 1) -> None: ...  # raises BudgetExhaustedError at the cap

    @property
    def spent(self) -> int: ...
```

Every bound the system obeys is a field of `PlanLimits`, and it is set once, when the planner is
built. Nothing overrides one per request, so a planner configured for a benchmark run and a planner
configured for an interactive session are two objects rather than one object called two ways.

Five of the eight are counted by the run loop. The other three bound a loop inside a component, and
`build_data_planner` hands each of those components its own number.

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

```python
class RunStatus(str, Enum):
    OK = "ok"
    PLANNED = "planned"
    INVALID_PLAN = "invalid_plan"
    REPLAN_EXHAUSTED = "replan_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    OPERATOR_FAILED = "operator_failed"
    LLM_FAILED = "llm_failed"
```

`planned` is what `plan` returns, and `llm_failed` is what a provider that fails after retries
returns. Neither is reachable from a stage: `LogicalPlanResult` and `FragmentResult` carry a
`RunStatus` too, and both belong to the run loop.

The four per-stage result types never leave the process. `DataPlanner` reads each one, records what
it holds on the run state, and moves on.

```python
@dataclass(frozen=True)
class LogicalPlanResult:
    status: RunStatus  # ok, or invalid_plan when the corrections ran out
    plan: LogicalPlan | None
    rejected: list[RejectedPlan]
    usage: list[LLMUsage]


@dataclass(frozen=True)
class PhysicalPlanResult:
    plan: PhysicalPlan | None  # None exactly when a node has no operator
    failure_reason: str | None
    usage: list[LLMUsage]


@dataclass(frozen=True)
class FragmentResult:
    status: RunStatus
    decision: RunDecision
    attempts: list[FragmentAttempt]  # rejected ones included, in order
    failure_reason: str | None
    usage: list[LLMUsage]


@dataclass(frozen=True)
class CatalogSearchResult:
    discovery: DiscoveryResult
    usage: list[LLMUsage]
```

Each stage returns what it did instead of writing it down as it goes, so a stage that raises cannot
leave a half-written record behind and the run state keeps one writer. Model usage follows the same
rule: every stage reports what its own calls cost, and `DataPlanner` appends each list to the run in
the order the stages ran.

`CatalogSearchResult` exists because `DiscoveryResult` goes into the run's results map and reaches
the caller inside `PlanResult.results`. Usage carried there would be counted twice, so the search
returns a pair: what it found, and what finding it cost.

One thing is accumulated rather than returned. `CallBudget` is billed inside
`execute_physical_operator`, and it has to keep its count when a fragment raises, so it travels as
an argument. `FragmentRunner` catching `BudgetExhaustedError` is the same concern from the other
side: the attempts already made and their usage are the record of what the runner was doing when the
request ended, and they are returned rather than lost.

---

## Validation

```python
def validate_logical_plan(
    plan: LogicalPlan, carried_node_ids: frozenset[str] = frozenset()
) -> None: ...


def validate_physical_plan(plan: LogicalPlan, physical_plan: PhysicalPlan) -> None: ...


class PlanValidationError(BlueDataPlannerError): ...
```

Both raise rather than return, and both run before anything is executed. `validation.py` imports
only `types/`.

`validate_logical_plan` enforces the plan rules ([10-data-model.md](10-data-model.md#1-the-plan))
and `validate_physical_plan` the fragment and cover rules
([04, what a fragment may be](04-detailed-architecture.md#what-a-fragment-may-be)). Neither adds a
rule of its own.

`carried_node_ids` exempts the nodes a continuation carried over from the shape and reachability
checks, whose objections do not apply to a node the run already has something for. `LogicalPlanner`
catches the error and regenerates.

The physical check is defensive. The physical planner enumerates only fragments that satisfy the
rules, so a cover it assembles should already pass. It is worth running because a broken cover does
not fail loudly on its own: a fragment whose non-root node is read from outside leaves that reader
waiting for a table nobody produces, which shows up as a run that stops making progress rather than
as an error.

---

## `planner/`

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
real columns and values. A `DISCOVER` node's entry holds the catalog records its search found, which
is what lets the continuation's scans name a source. One argument covers both because both live in
the same map.

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

### `PhysicalPlanner`

```python
class PhysicalPlanner:
    def __init__(
        self, llm_client: LLMClient, catalog: DataCatalog, router: OperatorRouter
    ) -> None: ...

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

`results` answers two questions here too. A `DISCOVER` entry's catalog records are the paths a
`SCAN` may be bound to, and the presence of an entry says the node already ran, which is the cost
the model weighs when it considers absorbing that node into a wider fragment ([04, the cover
repair](04-detailed-architecture.md#the-cover-repair)).

Inside, the planner works top down from the root of the plan. For each uncovered node it enumerates
the candidate fragments rooted there, one per matching operator per subgraph, and a model picks one.
The response schema is built from that call's own candidates, so a candidate set with no `SCAN` has
no field in which to name a source, and where a scan does appear the catalog's paths are the allowed
values. `plan` is `None` exactly when some node has no candidate at all, alone or in any fragment
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

For one fragment it loops until a result is accepted or the parameter attempts run out.

1. Resolve `fragment.operator_id` through the router.
2. Describe each input table: its row count, and for every column its name, its type, and a sample
    of values. Then annotate the columns that resolve to a catalog record with their `ColumnView`.
    One list per table, annotated in place, so a column the catalog has never seen still appears
    with what the rows show.
3. Write the operator's parameters, constrained by its `parameter_schema`, as structured output.
4. Call `execute_physical_operator` with the operator, the parameters, the inputs, the root's
    declared output columns, and the budget. This is the only place an operator is invoked. An
    unrecoverable failure returns `decision=stop` with `status=operator_failed` and no review turn,
    since no other operator would avoid the same failure.
5. Review the result. The review sees the fused instruction, the operator's description, the
    instructions of every node the fragment absorbed, and a sample of the result.
6. On `accept`, return `decision=continue`. On `reject`, spend an attempt and go back to step 3. On
    `replan`, return `decision=replan` with the review's assessment as the failure reason. Out of
    attempts, return `decision=escalate`.

Steps 3 and 5 are turns in one conversation, so the review sees what was written and why. Every
attempt runs against the same `inputs`, read once by the caller, so a reduction one attempt achieved
cannot compound into the next.

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
`DataCatalog.search_records`. The `DiscoveryResult` holds the terms, the `CatalogMatch` values they
found, and how many terms the cap dropped. It is kept under the node's id in the run's results map
and read by the logical planner when it writes the continuation.

This is not a fourth stage. Its output is catalog records rather than rows, it enters no data flow,
and no operator call is spent ([04](04-detailed-architecture.md#resolving-a-discovery-node)). It
takes `inputs` because an instruction such as "find press release documents for each company in the
input" expands into one term per company.

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

### `RunState`

```python
@dataclass
class RunState:
    request: str
    budget: CallBudget
    pipeline: DataPipeline
    results: dict[str, dict[str, ExecutionResult | DiscoveryResult]]
    attempts: list[FragmentAttempt]
    rejected_plans: list[RejectedPlan]
    planner_usage: list[LLMUsage]
    fragment_attempts: dict[frozenset[str], int]
    cover_attempts: int
    failure_replans: int
    control_replans: int

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
`to_result` freezes it into the `PlanResult` a caller receives. No stage holds it: each takes what
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
nothing in `planner/`.

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

`execute_physical_operator` does four things in order.

1. Validates `parameters` against `operator.parameter_schema`. A failure here is a bug rather than a
    spent call, since the runner wrote them under that same schema, so it raises and the budget is
    not touched.
2. Bills one operator call. An exhausted budget raises `BudgetExhaustedError`, which the runner
    catches.
3. Invokes `operator.call` on the bundle it was handed. A Blue operator takes that bundle as it
    stands; a LOTUS operator converts at its own edge (ADR-012).
4. Runs `check_output_columns` against the declared columns it was handed, matching the declared
    names and coercing each column to the type declared beside it. A missing or uncoercible column
    is a recoverable failure ([10](10-data-model.md#declared-output-columns)).

The two orderings are deliberate. The parameter check runs before the billing, because the runner
wrote the parameters under that same schema, so a mismatch is a bug rather than a spent call. The
billing runs before the operator, so a call that really happened still counts when the column check
rejects its output.

The caller passes the fragment root's declarations and nothing else, because a fused call produces
no intermediate tables to check the absorbed nodes against. Neither a zero-row table nor an empty
bundle is a failure. A model call an operator makes internally, as LOTUS does, happens inside this
call and is billed as part of it rather than tracked separately.

Three things `execution/` does not do, and each one removes an import. It does not schedule, because
the run loop decides what is ready and hands down one call at a time. It does not resolve an
operator id, because `FragmentRunner` hands it a resolved operator. It does not call a model,
because writing parameters and reviewing a result both belong to the runner (ADR-011).

---

## `operators/`

No operator is written here. `blue-data-ops` owns the Blue operators, the abstract `Operator`, and
the `OperatorRegistry` that versions them; LOTUS owns its own. This module holds one adapter per
suite, and an adapter is the only place either dependency is imported.

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
than as a wrong answer, and the run record names the node that had no candidate. Which of the ten
lack one, and who writes the operators that would close a gap, are open questions 5 and 39 in
[13-decision-log.md](13-decision-log.md#open-questions).

### The adapters

```python
def blue_operators(
    registry: OperatorRegistry, data_registry: DataRegistryClient
) -> list[PhysicalOperator]: ...


def lotus_operators() -> list[PhysicalOperator]: ...
```

Each adapter returns its `PhysicalOperator` values, and whoever assembles the planner registers what
comes back (ADR-009). Naming `OperatorRouter` in a public signature would require importing it,
whether or not the import sits behind `TYPE_CHECKING`, so an adapter that registered its own
operators would make `operators/` import `planner/`. Returning a list keeps that import out.

Nothing else changes when a suite is added. The physical planner builds its candidates from the
router while it covers a plan, so a newly registered operator competes for every fragment its
declaration allows without a code or prompt edit.

The Blue adapter converts no data: a `DataBundle` is what the Blue operations take and return, so a
callable validates parameters, invokes the operation, and maps errors (ADR-012). The LOTUS adapter
converts at its own edge, building one data frame per table going in and flattening coming out, and
imports LOTUS inside the callable rather than at module import time, so the package imports with
LOTUS absent and the router simply gains no LOTUS operators (ADR-004).

---

## `data/`

The only module in the shipped package that imports `blue-data` (ADR-003). Source and store
construction sit behind it too, so no class from `blue-data` escapes.

```python
class DataRegistryProvider(ABC):
    @abstractmethod
    def get_registry(
        self, platform_name: str, registry_name: str, properties: Properties
    ) -> DataRegistryClient: ...


def resolve_registry(properties: Properties) -> DataRegistryClient: ...


class DataRegistryClient:
    """Everything this package does with a blue-data DataRegistry."""

    def search_records(self, query: str) -> list[dict[str, Any]]: ...
    def get_data_source_schema(self, source: str) -> dict[str, Any]: ...
    def get_source_properties(self, source: str) -> dict[str, Any]: ...
    def connect_source(self, source: str) -> Any: ...
    def execute_query(self, query: str, source: str) -> Data: ...
```

Four of the five read: the catalog, a source's schema, a source's properties, and a query's rows.
`connect_source` hands back a live source object, which is what an operator writes through.

Two kinds of write have to be told apart, because the design answers them differently.

| Write | Goes to | Done by |
| --- | --- | --- |
| A session-scoped temporary table a bind join pushes into a SQL source | The source | An operator declaring `accepts_bound_inputs`, choosing by size between values in the SQL text and a table out of band ([04](04-detailed-architecture.md#what-an-operator-does-with-its-input)) |
| Registering a source, syncing it, collecting its statistics, enriching it | The registry | `blue-data`'s own tooling, before a registry ever reaches this package |

The boundary has to permit the first. Without `connect_source` a bind join cannot scale past an `IN`
list, and pushing a table into a source is how a fragment reaches data another fragment produced.

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
    def preview(self, request: str, limit: int) -> CatalogPreview: ...
    def search_records(self, term: str) -> list[CatalogMatch]: ...
    def column_views(self, columns: Sequence[ColumnRef]) -> list[ColumnView]: ...
```

Three readings of the same registry, one per caller, all fixed in
[10, catalog readings](10-data-model.md#4-catalog-readings).

| Method | Read by | Answers |
| --- | --- | --- |
| `preview` | `LogicalPlanner`, once per pass | Which sources exist. Source-level records only, ranked against the request and capped, with the total count so the planner can tell a complete list from a sample |
| `search_records` | `CatalogSearcher` for a `DISCOVER` node, and `PhysicalPlanner` when it binds a scan | Where the data a request needs lives. One call per search term |
| `column_views` | `FragmentRunner`, for the columns its fragment touches | How a column's values are actually spelled |

`DataCatalog` selects and renders what `blue-data` has already collected and enriched. It profiles
no column, samples no table, and infers no type of its own, so a `ColumnView` is a projection rather
than a computation: it pairs an attribute's stored `stats` and `value_semantics` with its
description. What belongs to this repository is the choice of what each stage sees, which is prompt
shaping.

| What a stage needs | Where `blue-data` already has it |
| --- | --- |
| Sources, databases, collections, entities, attributes | The per-level getters on the registry |
| Descriptions written during enrichment | `get_source_description` and its per-level counterparts |
| Sample values, distinct and null counts, min and max | The `stats` property filled by stats collection |
| Whether a column holds short codes rather than readable words | The `value_semantics` property, whose semantic types include `ENUM_CATEGORY`, `CODE`, and `POSTAL_CODE` |
| Finding a source nobody named | The registry's ranked record search |

Two consequences follow. Planning quality depends on whether a source has been enriched, and a
source that never was renders with names and kinds alone, which planning still works with. And the
ranked search combines keyword match, value match, and vector similarity, but vector similarity
needs a vector source and an embedding function; a file-backed registry without them ranks on the
other two signals.

### Which column gets a view

A `ColumnRef` is a full catalog path in parts, from source down to attribute, and a column earns a
view only when it resolves to one. `column_views` returns nothing for a column that does not.

A view is an annotation, never the column list. The runner reads the columns from the input tables,
so a column with no view still reaches the prompt with its name, its type, and its sample values,
and only the catalog's whole-column statistics are missing. That is what keeps an operator-generated
column from reading as a column that does not exist.

Which columns resolve is not settled. `fragment.scan_sources` names an entity path for each `SCAN`
in the fragment, so a column of a table the fragment itself scans is that path plus the column name.
A column arriving on the frontier is harder: resolving it means tracing back through the cover to
whichever fragment scanned it, which can be done for the fragment kinds that leave columns unchanged
and cannot be done safely through one that derives them. How far to trace decides whether a `FILTER`
running alone over scanned rows sees catalog values at all, and it is open question 3 in
[13-decision-log.md](13-decision-log.md#open-questions).

### Opening a registry

```python
def open_registry(registry_path: Path) -> DataRegistryClient: ...
```

Reads a serialized `blue-data` registry into memory. A reopened registry reconnects to its own
sources, so the databases behind it are never converted and a query still runs against the live
files.

This is the read counterpart of an ingestion step the library does not have. The TAG-Bench registry
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

The conversation is what makes two of the design's patterns work. The logical planner appends a
validation error and asks again. The fragment runner writes parameters and then reviews the result
in the same exchange, so the review sees what was written and why.

It also carries the usage, which is what lets a stage report what its calls cost without an
out-parameter: the stage reads `conversation.usage` when it builds its result. A stage making
several exchanges concatenates them. There is no accumulator class, since a total is a `sum()` over
the list.

`PlanResult.planner_usage` therefore covers calls through this module and nothing else, and the name
says so. A model call an operator makes internally, as a LOTUS operator does, is invisible here:
LOTUS holds its own model configuration, is not routed through `llm/`, and reports no per-call token
count, so the call is billed as part of the one operator call that contains it
([05-software-stack.md](05-software-stack.md#two-model-configurations-one-budget)). What a run spent
on planning is exact; what it spent inside operators is a call count.

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
than in each stage is what keeps provider handling out of all four components, and what lets the run
return everything it had already accumulated.

---

## Using it

```python
from blue_data_planner.operators import blue_operators, lotus_operators
from blue_data_planner.planner import OperatorRouter, build_data_planner
from blue_data_planner.types import LogicalOperator, PlanLimits

router = OperatorRouter()
for operator in blue_operators(registry, data_registry):
    router.register(operator)
for operator in lotus_operators():  # empty when LOTUS is not installed
    router.register(operator)

planner = build_data_planner(
    router=router, catalog=catalog, llm_client=client, limits=PlanLimits(...)
)
result = planner.run(
    "Which Bay Area data scientist postings are at healthcare companies?"
)

result.status  # RunStatus.OK
result.answer  # the rows
result.attempts  # every fragment run, rejected ones included, in order
result.counters["operator_calls"]  # what the run spent on operators
sum(call.cost for call in result.planner_usage)  # what planning cost
```

Where each pushdown boundary was drawn is read off the cover, and every earlier cover is still there
beside it:

```python
cover = result.pipeline.cover
[(f.operator_id, f.logical_node_ids) for f in cover.fragments]
# [("tablesuite.nl2sql", ["n1", "n2", "n3"]), ("textsuite.semantic_filter", ["n4"])]
```

Planning without spending anything is the same call with a different name:

```python
explained = planner.plan("Which Bay Area data scientist postings are at healthcare companies?")
explained.status  # RunStatus.PLANNED
explained.answer  # None
```

The operators registered for one logical operator are what the physical planner builds its
candidates from, so registering a suite is the whole of adding it:

```python
[op.operator_id for op in router.operators_for(LogicalOperator.FILTER)]
# ["tablesuite.select", "tablesuite.nl2sql", "textsuite.semantic_filter"]
```
