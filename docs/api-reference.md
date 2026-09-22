# API Reference

Signatures and types for the `blue_data_planner` library, grouped by the module that owns them.
[guide.md](guide.md) is the task-oriented walkthrough of the same surface, and
[architecture.md](architecture.md) explains the run loop these types describe. The `blue-planner`
command is documented in [cli.md](cli.md); the MCP server is [not built](mcp.md).

---

## The public surface

- Public is the package root, every module directly inside it, and the `__init__.py` of `types/`,
  `plan/`, `execution/`, `operators/`, `data/`, and `llm/`. A public name is covered by semantic
  versioning and typed, since the package ships `py.typed`, and `platform/*` imports nothing else.
- A module nested inside one of those directories can be split or renamed within a minor version.
- `operators/__init__.py` names the router and the suite lookup, never a suite itself, so each
  suite is imported from the module named for it. That keeps the root cheap to import: it loads
  the run path and no suite.

| Import | Holds |
| --- | --- |
| `blue_data_planner` | `DataPlanner`, `build_data_planner`, `RunResult`, `RunStatus`, `RunLimits`, `BlueDataPlannerError` |
| `blue_data_planner.data_planner` | `DataPlanner`, `build_data_planner` |
| `blue_data_planner.logical_planner`, `.physical_planner`, `.fragment_runner` | The three planning components, one module each |
| `blue_data_planner.catalog_searcher` | `CatalogSearcher` |
| `blue_data_planner.run_state` | `RunState`, `NodeResult`, `FiledResult` |
| `blue_data_planner.types`, `.plan`, `.execution`, `.operators`, `.data`, `.llm` | One section each under [modules reached by name](#modules-reached-by-name) |
| `blue_data_planner.operators.builtin`, `.adapters.blue`, `.adapters.lotus` | One suite each |

---

## `DataPlanner`

```python
from blue_data_planner import DataPlanner
```

```python
def run(self, request: str) -> RunResult: ...
def plan(self, request: str) -> RunResult: ...
```

| Operation | Does | Status it can return | Spends operator calls |
| --- | --- | --- | --- |
| `run` | Plans, covers, executes, and repairs until the request ends | Any member of `RunStatus` other than `planned`, `needs_repair`, and `needs_replan` | Yes |
| `plan` | Writes one logical plan and one cover, then stops | `planned`, `invalid_plan`, `llm_failed`, `planner_failed` | No |

Both take a request in plain language and nothing else. A plan is what a request produced, so
neither operation accepts one: `LogicalPlan.from_dict` reads a recorded plan back for inspection.
Limits are fixed when the planner is built, so neither takes an override. A `plan` call whose
first pass ends in a `DISCOVER` node comes back as it stands, with no search run, and sets
`failure_reason` only when no cover could be written.

The constructor takes the four components and the limits — `DataPlanner(logical_planner,
physical_planner, fragment_runner, catalog_searcher, limits)` — which is how a caller gives one
component a different model or substitutes one in a test. `build_data_planner` covers every other
case.

---

## `build_data_planner`

```python
from blue_data_planner import build_data_planner
```

```python
def build_data_planner(
    data_registry: DataRegistry,
    *,
    suites: Sequence[OperatorSuite],
    model: str,
    reasoning_effort: str | None = None,
    limits: RunLimits | None = None,
    llm_client_name: str = "blue_data_planner",
) -> DataPlanner: ...
```

| Parameter | Type | Default | Holds |
| --- | --- | --- | --- |
| `data_registry` | `DataRegistry` | required, positional | The `blue-data` registry to plan over, from `open_registry` or built by the caller |
| `suites` | `Sequence[OperatorSuite]` | required | The suites whose operators are registered, in order. A later duplicate operator id raises `OperatorRoutingError` |
| `model` | `str` | required | The litellm model string every component calls, such as `"openai/gpt-5.6-luna"` |
| `reasoning_effort` | `str \| None` | `None` | Passed to the model client where the provider accepts one. `None` omits the parameter |
| `limits` | `RunLimits \| None` | `None` | The bounds the run obeys. `None` uses `RunLimits()` |
| `llm_client_name` | `str` | `"blue_data_planner"` | Prefixes every property the model client reads, which keeps one process's clients apart in a log |

The call builds the model client, registers what each suite returns with an `OperatorRouter`,
wraps the registry in a `DataCatalog`, and hands `DataPlanner` its four components.
[guide.md](guide.md) works through an assembled planner end to end.

---

## Result types

### `RunResult`

Frozen. Returned whichever way a request ends.

| Field | Type | Holds |
| --- | --- | --- |
| `status` | `RunStatus` | How the request ended |
| `answer` | `DataBundle \| None` | The rows the `RETURN` node named. `None` on every status but `ok` |
| `failure_reason` | `str \| None` | Why the run ended as it did. `None` when the status is `ok` |
| `pipeline` | `DataPipeline` | Every logical plan and every cover the request produced, with the current pair named |
| `results` | `dict[str, FragmentResult \| DiscoveryResult]` | How each fragment ended and what each search found, keyed by the signature of the work |
| `counters` | `RunCounters` | What the run spent, and the bounds it was spent against |
| `conversations` | `RunConversations` | Every exchange with the model, by the component that had it |

```python
def to_dict(self) -> dict[str, Any]: ...
```

`to_dict` returns the JSON form every interface writes a run in. `conversations` shrinks to a call
count and a cost there; each table in `results` shrinks to a `TableSummary` of
`SUMMARY_SAMPLE_ROWS` rows.

No field reports one node's outcome: that is the entry `results` holds under the signature the
current cover gives the node.

### `RunStatus`

A `StrEnum`, so `print(result.status)` prints `ok`.

| Member | Value | Means |
| --- | --- | --- |
| `OK` | `ok` | The request was answered, or one fragment's review accepted its result |
| `NEEDS_REPAIR` | `needs_repair` | Fragment only: the parameter repairs ran out, so the run loop writes the cover again |
| `NEEDS_REPLAN` | `needs_replan` | Fragment only: the review judged the plan wrong, so the run loop writes the logical plan again |
| `PLANNED` | `planned` | `plan` finished: one logical plan and one cover, nothing run |
| `INVALID_PLAN` | `invalid_plan` | Plan generation ran out of corrections |
| `REPLAN_EXHAUSTED` | `replan_exhausted` | `failure_replans` or `control_replans` was reached |
| `BUDGET_EXHAUSTED` | `budget_exhausted` | The operator call budget ran out |
| `OPERATOR_FAILED` | `operator_failed` | An operator failed for a reason no other operator would avoid |
| `LLM_FAILED` | `llm_failed` | The provider failed after the client's retries |
| `PLANNER_FAILED` | `planner_failed` | A defect in the planner ended the run, with the message in `failure_reason` |

### `RunLimits`

Frozen. Every bound a run obeys, set once when the planner is built.

| Field | Default | Counts | Reaching it |
| --- | --- | --- | --- |
| `operator_calls` | `30` | Every operator call in the request | `budget_exhausted` |
| `plan_corrections` | `5` | Rewrites after validation turned a plan down | `invalid_plan` |
| `parameter_repairs` | `3` | Parameter rewrites within one fragment | The cover repair |
| `cover_repairs` | `2` | Covers after the first, per logical plan | The replan |
| `failure_replans` | `2` | Replans a failure forced | `replan_exhausted` |
| `control_replans` | `2` | Replans a control node asked for | `replan_exhausted` |

Model calls are not budgeted. The three repairs and their order are in
[architecture.md](architecture.md#the-three-repairs).

### `RunCounters`

Mutable. Built per request from a `RunLimits`, and carried into every component that spends
against a bound.

| Member | Type | Holds |
| --- | --- | --- |
| `limits` | `RunLimits` | The bounds this run was given |
| `operator_calls` | `int` | Operator calls spent |
| `failure_replans` | `int` | Replans spent on a failure |
| `control_replans` | `int` | Replans spent on a control node |
| `spend_operator_call()` | `-> None` | Bills one call, raising `BudgetExhaustedError` at the cap |
| `spend_failure_replan()` | `-> bool` | Bills one replan, `False` when the cap leaves no room |
| `spend_control_replan()` | `-> bool` | The same, for a control node |
| `to_dict()` | `-> dict[str, Any]` | What the run spent, and the bounds it was spent against |

The three bounds that reset inside a request — `plan_corrections`, `parameter_repairs`, and
`cover_repairs` — have no counter here. Each is counted by the loop that obeys it.

### The per-node results

Each entry in `RunResult.results` is one of the first two.

| Type | Holds |
| --- | --- |
| `FragmentResult` | `status`, the last call's `ExecutionResult` in `result`, and `failure_reason`. Filed even when the review turned the result down |
| `DiscoveryResult` | `search_terms`, the `EntityMatch` list they found in `matches`, and `dropped_terms`, the count the per-node cap cut |
| `ExecutionResult` | `status` (`ExecutionStatus`), `output`, `operator_id`, `error` (`ErrorInfo`), `row_count` across every table, and the operator's own `report` |

---

## Exceptions

Every exception the library defines is in `blue_data_planner.types` and inherits
`BlueDataPlannerError`. Exhaustion, operator failure, and provider failure are statuses rather
than exceptions, so a caller holding a `DataPlanner` meets almost none of them.

| Exception | Raised by | Reaches a caller |
| --- | --- | --- |
| `BlueDataPlannerError` | The base class, and raised directly on a defect such as `DataPipeline.cover` where the pipeline names no current physical plan | The run loop catches it and ends the request as `planner_failed` |
| `PlanFormatError` | `LogicalPlan.from_dict`, `PhysicalPlan.from_dict`, `DataPipeline.from_dict`, on a payload missing a key or carrying a value its node kind does not take | Always |
| `PlanValidationError` | `validate_logical_plan`, `validate_physical_plan` | Only from a direct call. `LogicalPlanner` catches it and writes the plan again |
| `UnknownNodeError` | `LogicalPlan.node`, `LogicalPlan.consumers` | Always |
| `UnknownFragmentError` | `PhysicalPlan.fragment`, `PhysicalPlan.fragment_for_node` | Always |
| `ReturnAnswerError` | `project_answer`, when the finished rows do not carry a column the answer names | The run loop answers it with a replan |
| `OperatorRoutingError` | `OperatorRouter.register` on a repeated operator id, `OperatorRouter.resolve` on an unknown one | Always, including from `build_data_planner` |
| `UnknownOperatorSuiteError` | `operator_suite`, on a name no suite answers to | Always |
| `BudgetExhaustedError` | `RunCounters.spend_operator_call`, at the cap | `FragmentRunner` catches it and returns `budget_exhausted` |
| `RecoverableOperatorError` | An operator refusing input another operator might accept | `execute_physical_operator` turns it into a failed `ExecutionResult` |
| `UnrecoverableOperatorError` | An operator failing for a reason no other operator would avoid | The same, with `ErrorKind.UNRECOVERABLE` |
| `SourceQueryError` | `execute_query`, `execute_write`, when a source refuses | Always |
| `SourceUnreachableError` | `connect_source`, when the registry holds no connectable source under the name | Always |

Both operator errors carry a `report: dict[str, Any] | None`, which reaches the caller on the
`ExecutionResult`.

`LLMError` is the one exception outside that tree. It is declared in `blue_data_planner.llm`,
inherits `Exception`, and carries `message`, `code`, and `provider_error`. `DataPlanner` catches
it and ends the request as `llm_failed`.

---

## Modules reached by name

### `blue_data_planner.types`

Every data class that crosses a module boundary. The package imports nothing else in
`blue_data_planner`, so anything may import it.

| Group | Names |
| --- | --- |
| Rows | `Record` (one row), `Data` (one table), `DataBundle` (what one fragment passes on), `TableSummary`, `summarize_table`, `SUMMARY_SAMPLE_ROWS` |
| Plan | `LogicalPlan`, `PhysicalPlan`, `PhysicalPlanFragment`, `PipelineNode`, `DataPipeline`, `InputData`, `OutputColumn`, `ColumnDataType` |
| Vocabulary | `LogicalOperator`, `ControlOperator`, `INPUT_ARITY` |
| Operators | `PhysicalOperator`, `OperatorOutput` |
| Catalog | `AttributeRef`, `AttributeRecord`, `AttributeView`, `EntityMatch`, `SourceRecord`, `CatalogPreview` |
| Run | `RunResult`, `RunStatus`, `RunLimits`, `RunCounters`, `RunConversations`, `Conversation`, `Turn`, `LLMUsage` |
| Per-step results | `ExecutionResult`, `ExecutionStatus`, `ErrorInfo`, `ErrorKind`, `FragmentResult`, `FragmentReviewDecision`, `DiscoveryResult`, `LogicalPlanResult`, `PhysicalPlanResult` |
| Exceptions | The thirteen in the table above |

`LogicalPlan`, `PhysicalPlan`, and `DataPipeline` each carry `to_dict` and `from_dict`, which is
how a plan travels out of the process and back.

### `blue_data_planner.plan`

What can be answered about a plan with no model, no source, and no operator call.

```python
def validate_logical_plan(plan: LogicalPlan, carried_node_ids: frozenset[str] = frozenset()) -> None: ...
def validate_physical_plan(plan: LogicalPlan, physical_plan: PhysicalPlan, entity_paths: frozenset[str]) -> None: ...
def candidate_fragments(plan: LogicalPlan, root_node_id: str, logical_operators: frozenset[LogicalOperator]) -> list[frozenset[str]]: ...
def fragment_frontier(plan: LogicalPlan, node_ids: frozenset[str]) -> list[str]: ...
def signatures(plan: LogicalPlan, cover: PhysicalPlan) -> dict[str, str]: ...
def logical_plan_schema() -> type[BaseModel]: ...
def cover_choice_schema(
    plan: LogicalPlan, root_node_id: str, candidates: list[tuple[frozenset[str], PhysicalOperator]],
    reusable: PhysicalPlanFragment | None, entity_source_kinds: Mapping[str, str],
) -> type[BaseModel]: ...
def candidate_label(root_node_id: str, operator_id: str, node_ids: frozenset[str]) -> str: ...

REUSE = "reuse"
```

Both validators raise `PlanValidationError` rather than returning a value. `signatures` returns
one per node that produces a result, and a change anywhere above a node changes that node's
signature, which is what lets a new cover reuse what an old one produced. `OutputColumnModel` is
the shape one required output column is written in, and `REUSE` stands in for an operator id in
the label of the candidate that reuses an earlier fragment.

### `blue_data_planner.execution`

One operator call and the check on what it returned. The package imports only `types`.

```python
def execute_physical_operator(
    operator: PhysicalOperator,
    parameters: dict[str, Any],
    inputs: DataBundle,
    required_output_columns: list[OutputColumn],
    counters: RunCounters,
) -> ExecutionResult: ...

def check_output_columns(
    result: ExecutionResult, required_output_columns: list[OutputColumn], operator_id: str
) -> ExecutionResult: ...

def project_answer(inputs: DataBundle, columns: list[str]) -> DataBundle: ...
```

`execute_physical_operator` is the single place any operator is invoked: it validates the
parameters, bills one operator call, runs the operator, and checks the result's declared columns.
An operator error becomes a failed `ExecutionResult`; parameters the schema rejects raise.

### `blue_data_planner.operators`

```python
class OperatorRouter:
    def register(self, operator: PhysicalOperator) -> None: ...
    def operators_for(self, logical_op: LogicalOperator) -> list[PhysicalOperator]: ...
    def resolve(self, operator_id: str) -> PhysicalOperator: ...


class OperatorSuite(Protocol):
    name: ClassVar[str]

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]: ...


def operator_suite(name: str) -> OperatorSuite: ...

OPERATOR_SUITE_NAMES: tuple[str, ...] = ("builtin", "lotus", "blue")
```

`operator_suite` returns the suite a name registers, with its default options, and raises
`UnknownOperatorSuiteError` on any other name.

| Suite | Import | Options | Also exports |
| --- | --- | --- | --- |
| `BuiltinSuite` | `blue_data_planner.operators.builtin` | `with_nl2table: bool = False` ([experimental](features.md#builtinnl2table-is-experimental)) | `builtin_operators` |
| `LotusSuite` | `blue_data_planner.operators.adapters.lotus` | `model: str \| None = None`, `max_rows: int = 5000`, `reasoning_effort: str \| None = None` | `lotus_operators`, `lotus_is_installed` |
| `BlueSuite` | `blue_data_planner.operators.adapters.blue` | `operator_registry: OperatorRegistry \| None = None` | `blue_operators` |

`LotusSuite.model` defaults to the model every other component uses, and `LotusSuite.operators`
returns an empty list where LOTUS is not installed. What each suite offers is in
[architecture.md](architecture.md#operator-suites); adding one, in [extending.md](extending.md).

### `blue_data_planner.data`

The only module that imports `blue-data`.

```python
def open_registry(registry_path: Path) -> DataRegistry: ...
def connect_source(registry: DataRegistry, source: str) -> Any: ...
def execute_query(
    registry: DataRegistry, source: str, query: str, database: str, collection: str | None = None
) -> Data: ...
def execute_write(
    registry: DataRegistry, source: str, statement: str, database: str,
    collection: str | None = None,
) -> None: ...


class DataCatalog:
    def __init__(self, registry: DataRegistry, max_search_results: int = 3) -> None: ...
    def preview(self, request: str, limit: int) -> CatalogPreview: ...
    def search_records(self, term: str) -> list[EntityMatch]: ...
    def attribute_views(self, entity_path: str) -> list[AttributeView]: ...
```

`open_registry` raises `FileNotFoundError` on a missing file rather than handing back an empty
registry, and `DataRegistry` is re-exported from `blue-data`. The three `DataCatalog` readings are
all the planning components see of the catalog: `preview` for the logical planner,
`search_records` for the catalog searcher, `attribute_views` for the fragment runner.

### `blue_data_planner.llm`

The only module that calls a provider.

```python
class LLMClient:
    def __init__(self, name: str, properties: dict[str, Any] | None = None) -> None: ...
    def complete_structured(self, conversation: Conversation, response_model: type[M]) -> M: ...
    def execute_api_call(
        self, input: Any, properties: dict[str, Any] | None = None,
        additional_data: dict[str, Any] | None = None,
    ) -> Any: ...
    async def aexecute_api_call(
        self, input: Any, properties: dict[str, Any] | None = None,
        additional_data: dict[str, Any] | None = None,
    ) -> Any: ...


class LLMClientProvider(ABC):
    def get_client(self, properties: dict[str, Any]) -> LLMClient: ...


def resolve_llm_client(
    properties: dict[str, Any] | None, provider: LLMClientProvider | None = None
) -> LLMClient: ...
```

`complete_structured` is the call the planner makes: it answers a `Conversation` with a value the
Pydantic `response_model` shapes, appends the answer as a turn, and records an `LLMUsage` on the
conversation. `aexecute_api_call` is the library's one async method.
`CachingLLMClientProvider` reuses one client per configuration. `UsageLedger` holds a client's own
per-call accounting, separate from the conversation's: one `UsageRecord` per call, carrying the
model, the token counts, the cost, the latency, and whether the call was served from the cache.

### The components

Each takes its dependencies in its constructor and holds no bound of its own: a component reads
one off the `RunCounters` it is handed per call.

| Class | Module | Constructor | Operation |
| --- | --- | --- | --- |
| `LogicalPlanner` | `logical_planner` | `(llm_client, catalog)` | `generate(request, conversation, counters, parent=None, carried_node_ids=frozenset(), results=None, reason=None) -> LogicalPlanResult` |
| `PhysicalPlanner` | `physical_planner` | `(llm_client, router)` | `cover(plan, conversation, previous, results, reason=None) -> PhysicalPlanResult` |
| `FragmentRunner` | `fragment_runner` | `(llm_client, catalog, router)` | `run(fragment, conversation, inputs, counters) -> FragmentResult` |
| `CatalogSearcher` | `catalog_searcher` | `(llm_client, catalog, max_search_terms=8)` | `search(node, conversation, inputs) -> DiscoveryResult` |

`max_search_terms` is the one number a component's constructor takes, because it is not a bound
on a run: reaching it drops the extra terms and reports the count on the `DiscoveryResult`.

`RunState`, in `blue_data_planner.run_state`, is the one mutable object per request. The run loop
alone writes into it, and a caller reads the same contents off the `RunResult` it produces.
