# API Reference

Signatures and types for the `blue_data_planner` library, grouped by the module that owns them.
[guide.md](guide.md) walks through the same names by task, and [architecture.md](architecture.md)
explains the run loop these types describe. [cli.md](cli.md) documents the `blue-planner` command,
and [mcp.md](mcp.md) the MCP server. The specification for everything on this page is
[design/09-api-interface-spec.md](../design/09-api-interface-spec.md), and the two are kept in
sync.

---

## The public surface

- The public names are those in the package root, in every module directly inside it, and in the
  `__init__.py` of `types/`, `plan/`, `execution/`, `operators/`, `data/`, and `llm/`.
- Public names follow semantic versioning and carry types, since the package ships `py.typed`.
  `platform/*` imports nothing else.
- A module nested inside one of those directories may be split or renamed in a minor version.
- `operators/__init__.py` names the router and the suite lookup but no suite, so each suite is
  imported from its own module. Importing the root loads the run path and no suite.

| Import | Holds |
| --- | --- |
| `blue_data_planner` | `DataPlanner`, `build_data_planner`, `PlannerSettings`, `build_data_planner_from_settings`, `RunResult`, `RunStatus`, `RunLimits`, `BlueDataPlannerError`, `__version__` |
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
def run(self, request: str, *, cancel: threading.Event | None = None) -> RunResult: ...
def plan(self, request: str, *, cancel: threading.Event | None = None) -> RunResult: ...
```

| Operation | Does | Status it can return | Spends operator calls |
| --- | --- | --- | --- |
| `run` | Plans, covers, executes, and repairs until the request ends | Any member of `RunStatus` other than `planned`, `needs_repair`, and `needs_replan` | Yes |
| `plan` | Plans, searches the catalog, and covers as `run` does, then stops before the first operator call | `planned`, `invalid_plan`, `replan_exhausted`, `llm_failed`, `planner_failed`, `deadline_exceeded`, `cancelled` | No |

Both take a request in plain language. Setting the optional `cancel` event from another thread
stops the request before its next step, with the status `cancelled`. Neither operation accepts a
plan, because a plan is produced from a request; `LogicalPlan.from_dict` reads a recorded plan back
for inspection. Neither takes limits either: they are fixed when the planner is built.

`plan` returns the plan and the cover that `run` would start executing. Each `DISCOVER` node with
nothing to wait for has been searched, and the continuation after it written and covered. A
`DISCOVER` node waiting on rows that a fragment produces stays unresolved. On `planned`,
`failure_reason` is `None`.

The constructor takes the four components and the limits: `DataPlanner(logical_planner,
physical_planner, fragment_runner, catalog_searcher, limits)`. Call it directly to give one
component a different model or to substitute one in a test, and use `build_data_planner` for every
other case.

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
    provider_timeout_seconds: int | None = None,
) -> DataPlanner: ...
```

| Parameter | Type | Default | Holds |
| --- | --- | --- | --- |
| `data_registry` | `DataRegistry` | required, positional | The `blue-data` registry to plan over, from `open_registry` or built by the caller |
| `suites` | `Sequence[OperatorSuite]` | required | The suites whose operators are registered, in order. A later duplicate operator id raises `OperatorRoutingError` |
| `model` | `str` | required | The litellm model string every component calls, such as `"openai/gpt-6-luna"` |
| `reasoning_effort` | `str \| None` | `None` | Passed to the model client where the provider accepts one. `None` omits the parameter |
| `limits` | `RunLimits \| None` | `None` | The bounds the run obeys. `None` uses `RunLimits()` |
| `llm_client_name` | `str` | `"blue_data_planner"` | Prefixes every property the model client reads, which keeps one process's clients apart in a log |
| `provider_timeout_seconds` | `int \| None` | `None` | How many seconds one provider call may take before it fails and is retried. `None` uses the client's default of 120 |

The call builds the model client, registers what each suite returns with an `OperatorRouter`,
wraps the registry in a `DataCatalog`, and hands `DataPlanner` its four components.
[guide.md](guide.md) works through an assembled planner end to end.

---

## `PlannerSettings`

```python
from blue_data_planner import PlannerSettings
```

Frozen. Every value a planner is built from, with the default each one takes.

| Field | Type | Default | Holds |
| --- | --- | --- | --- |
| `registry_path` | `Path` | required | The serialized `blue-data` registry to plan over |
| `model` | `str` | `"openai/gpt-6-luna"` | The litellm model string every planning component calls |
| `reasoning_effort` | `str` | `"low"` | One of `minimal`, `low`, `medium`, `high`, `none`, `off`. `off` omits the parameter, for a model that refuses it |
| `operator_suites` | `tuple[str, ...]` | `("builtin", "lotus")` | The suites to register, in order |
| `lotus_model` | `str \| None` | `None` | The model LOTUS operations run on. `None` runs them on `model` |
| `lotus_reasoning_effort` | `str` | `"none"` | The reasoning effort a LOTUS call spends, from the same six values |
| `lotus_max_rows` | `int` | `5000` | The most rows a LOTUS operation accepts. A call with more is refused |
| `blue_model` | `str \| None` | `None` | The model the Blue operations run on. `None` runs them on `model` |
| `blue_reasoning_effort` | `str` | `"none"` | The reasoning effort a Blue operation's model call spends, from the same six values |
| `provider_timeout_seconds` | `int` | `120` | How many seconds one provider call may take before it fails and is retried |
| `limits` | `RunLimits` | `RunLimits()` | The bounds every run obeys |

The constructor raises `InvalidSettingError` when the reasoning effort is not one of the six values
or the provider timeout is zero or less. The error names the field. The constructor checks nothing
else: an unknown suite name or an unreadable registry is reported when the planner is built.

---

## `build_data_planner_from_settings`

```python
from blue_data_planner import build_data_planner_from_settings
```

```python
def build_data_planner_from_settings(
    settings: PlannerSettings,
    *,
    llm_client_name: str = "blue_data_planner",
) -> DataPlanner: ...
```

Opens the registry the settings name and builds a planner over the suites they list. It raises
`RegistryUnreadableError` when the registry cannot be read, and `UnknownOperatorSuiteError` when a
name in `operator_suites` matches no suite.

The library reads no environment variable. Each interface resolves values its own way and hands one
`PlannerSettings` over: `blue-planner` resolves a command option, then the environment, then the
selected profile, then the default; the MCP server reads the environment alone. A library caller
constructs one directly, or calls `build_data_planner` with values already resolved.

---

## Result types

### `RunResult`

Frozen. Returned whichever way a request ends.

| Field | Type | Holds |
| --- | --- | --- |
| `status` | `RunStatus` | How the request ended |
| `answer` | `DataBundle \| None` | The rows the `RETURN` node named. `None` on every status but `ok` |
| `failure_reason` | `str \| None` | Why the run stopped. `None` when the status is `ok` or `planned` |
| `pipeline` | `DataPipeline` | Every logical plan and every cover the request produced, with the current pair named |
| `results` | `dict[str, FragmentResult \| DiscoveryResult]` | How each fragment ended and what each search found, keyed by signature |
| `counters` | `RunCounters` | What the run spent, and the bounds it was spent against |
| `conversations` | `RunConversations` | Every exchange with the model, by the component that had it |

```python
def to_dict(self) -> dict[str, Any]: ...
```

`to_dict` returns the JSON form every interface writes a run in. In that form, `conversations`
becomes a call count and a cost, and the cost is `null` when any call used a model with no known
price. Each table in `results` becomes a `TableSummary` holding `SUMMARY_SAMPLE_ROWS` sample rows.

No field reports a single node's outcome. To find it, look up the node's signature under the
current cover in `results`.

### `RunStatus`

A `StrEnum`, so `print(result.status)` prints `ok`.

| Member | Value | Means |
| --- | --- | --- |
| `OK` | `ok` | The request was answered, or one fragment's review accepted its result |
| `NEEDS_REPAIR` | `needs_repair` | Fragment only: the parameter repairs ran out, so the run loop writes the cover again |
| `NEEDS_REPLAN` | `needs_replan` | Fragment only: the review judged the plan wrong, so the run loop writes the logical plan again |
| `PLANNED` | `planned` | `plan` reached the first operator call and stopped before it |
| `INVALID_PLAN` | `invalid_plan` | Plan generation ran out of corrections |
| `REPLAN_EXHAUSTED` | `replan_exhausted` | `failure_replans` or `control_replans` was reached |
| `BUDGET_EXHAUSTED` | `budget_exhausted` | The operator call budget ran out |
| `OPERATOR_FAILED` | `operator_failed` | An operator failed for a reason no other operator would avoid |
| `LLM_FAILED` | `llm_failed` | The provider failed after the client's retries |
| `PLANNER_FAILED` | `planner_failed` | A defect in the planner ended the run, with the message in `failure_reason` |
| `DEADLINE_EXCEEDED` | `deadline_exceeded` | The request reached `wall_clock_seconds`, checked before its next step |
| `CANCELLED` | `cancelled` | The caller set the `cancel` event, checked before its next step |

### `RunLimits`

Frozen. Every bound a run obeys, set once when the planner is built.

| Field | Default | Counts | Reaching it |
| --- | --- | --- | --- |
| `operator_calls` | `30` | Operator calls in the request. Also caps how many operator and `DISCOVER` nodes one plan may hold | `budget_exhausted` |
| `plan_corrections` | `5` | Rewrites after validation turned a plan down | `invalid_plan` |
| `parameter_repairs` | `3` | Parameter rewrites within one fragment | The cover repair |
| `cover_repairs` | `2` | Covers after the first, per logical plan | The replan |
| `failure_replans` | `2` | Replans a failure forced | `replan_exhausted` |
| `control_replans` | `5` | Replans a control node asked for | `replan_exhausted` |
| `wall_clock_seconds` | `600` | Seconds the request has run, checked before each step | `deadline_exceeded` |

Model calls have no limit of their own; the size of a plan bounds them. A plan whose operator and
`DISCOVER` nodes outnumber the value of `operator_calls` is rejected and written again, which uses
one of the `plan_corrections`. The three repairs and their order are in
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
| `spend_failure_replan()` | `-> bool` | Bills one replan, or returns `False` without billing when the cap is reached |
| `spend_control_replan()` | `-> bool` | The same, for a control node |
| `to_dict()` | `-> dict[str, Any]` | What the run spent, and the bounds it was spent against |

The three bounds that reset inside a request (`plan_corrections`, `parameter_repairs`, and
`cover_repairs`) have no counter here. Each is counted by the loop that obeys it.

### The per-node results

Each entry in `RunResult.results` is a `FragmentResult` or a `DiscoveryResult`. The other two
types appear inside them.

| Type | Holds |
| --- | --- |
| `FragmentResult` | `status`, every `ParameterAttempt` in `attempts`, the last call's `ExecutionResult` in `result`, and `failure_reason`. Recorded even when the review rejected the result |
| `ParameterAttempt` | One attempt's `parameters`, the call's `ExecutionResult` in `result` (`None` when the parameters repeated an earlier attempt's), and the review's `assessment` |
| `DiscoveryResult` | `search_terms`, the `EntityMatch` list they found in `matches`, and `dropped_terms`, how many terms `max_search_terms` dropped |
| `ExecutionResult` | `status` (`ExecutionStatus`), `output`, `operator_id`, `error` (`ErrorInfo`), `row_count` summed over every table, and the operator's own `report` |

### Plan types

The types `RunResult.pipeline` holds. All are in `blue_data_planner.types`, and all but
`DataPipeline` are frozen.

#### `DataPipeline`

| Member | Type | Holds |
| --- | --- | --- |
| `logical_plans` | `dict[str, LogicalPlan]` | Every logical plan version the request produced, by id |
| `physical_plans` | `dict[str, PhysicalPlan]` | Every cover the request produced, by id |
| `current_logical_plan_id` | `str \| None` | The logical plan in force. `None` when the request ended before one was installed |
| `current_physical_plan_id` | `str \| None` | The cover in force, always one written for the current logical plan |
| `logical_plan` | property, `LogicalPlan` | The current logical plan. Raises `BlueDataPlannerError` when there is none |
| `cover` | property, `PhysicalPlan` | The current cover. Raises `BlueDataPlannerError` when there is none |

Check `current_physical_plan_id` before reading `cover` on a run that did not end `ok` or `planned`.

#### `LogicalPlan`

| Member | Type | Holds |
| --- | --- | --- |
| `id` | `str` | This version's id |
| `parent_id` | `str \| None` | The version this one continues. `None` on the first |
| `nodes` | `list[PipelineNode]` | The nodes, in the order the planner wrote them |
| `output_node_id` | `str \| None` | The `RETURN` node. `None` only while the plan ends in control nodes |
| `node(node_id)` | `-> PipelineNode` | One node. Raises `UnknownNodeError` when no node has the id |
| `consumers(node_id)` | `-> list[PipelineNode]` | Every node reading this one. Raises `UnknownNodeError` when no node has the id |
| `nodes_in_dependency_order()` | `-> list[PipelineNode]` | Every node, each placed after the nodes it reads |

#### `PipelineNode`

| Member | Type | Holds |
| --- | --- | --- |
| `id` | `str` | The node's id, written by the planner |
| `kind` | `LogicalOperator \| ControlOperator` | `SCAN`, `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION`, `RETURN`; or `REPLAN`, `DISCOVER` |
| `input_node_ids` | `list[str]` | The nodes it reads |
| `attributes` | `dict[str, Any]` | What its kind carries: an `instruction`, a `SCAN`'s source, a `RETURN`'s `columns` |
| `required_output_columns` | `list[OutputColumn]` | The columns its result has to carry, each a `name` and a `dtype` of `str`, `int`, `float`, or `bool` |
| `instruction` | property, `str` | The instruction in plain language. Empty on a `RETURN` node |
| `is_operator_node` | property, `bool` | `True` when the kind is a logical operator other than `RETURN`, so an operator runs it |

#### `PhysicalPlan`

| Member | Type | Holds |
| --- | --- | --- |
| `id` | `str` | This cover's id |
| `parent_id` | `str \| None` | The cover this one replaces. `None` on a logical plan's first cover |
| `logical_plan_id` | `str` | The logical plan it covers |
| `fragments` | `list[PhysicalPlanFragment]` | The fragments, together covering every operator node |
| `fragment(fragment_id)` | `-> PhysicalPlanFragment` | One fragment. Raises `UnknownFragmentError` when no fragment has the id |
| `fragment_for_node(node_id)` | `-> PhysicalPlanFragment` | The fragment covering a node. Raises `UnknownFragmentError` when no fragment covers it |
| `fragment_for_root(node_id)` | `-> PhysicalPlanFragment \| None` | The fragment whose root is this node. `None` when no fragment has it as its root |

#### `PhysicalPlanFragment`

| Field | Type | Holds |
| --- | --- | --- |
| `id` | `str` | The fragment's id |
| `logical_node_ids` | `list[str]` | The nodes one operator call covers |
| `root_logical_node_id` | `str` | The last of them, whose output the fragment produces |
| `operator_id` | `str` | The operator bound to it, such as `builtin.filter` |
| `scan_sources` | `dict[str, str]` | Each `SCAN` node's id, mapped to the catalog path it reads |
| `instruction` | `str` | What the call has to do, in plain language |
| `input_data` | `list[InputData]` | The outputs it reads from other fragments, each a `logical_node_id`, a `name`, and a `description` |
| `required_output_columns` | `list[OutputColumn]` | The columns its result has to carry |

`signatures(plan, cover)` in `blue_data_planner.plan` maps each fragment's root node and each
control node to its key in `RunResult.results`.

#### `EntityMatch`

One entity a `DISCOVER` node's search found, in `DiscoveryResult.matches`.

| Field | Type | Holds |
| --- | --- | --- |
| `path` | `str` | The entity's catalog path, which a `SCAN` names as its source |
| `name` | `str` | The entity's name |
| `description` | `str \| None` | Its description in the registry |
| `source_kind` | `str` | The connection protocol of the source above it, such as `sql/sqlite` |
| `attributes` | `list[AttributeView]` | A view of every attribute it holds: its type, description, most frequent values, and how many of the counted rows are null or empty |
| `score` | `float` | How well it matched |
| `search_term` | `str` | The term that found it |
| `row_count` | `int \| None` | The row count the registry records. `None` when it records none |

---

## Exceptions

Every exception the library defines is in `blue_data_planner.types` and inherits
`BlueDataPlannerError`. Running out of a limit, an operator failure, and a provider failure end a
request with a status, not an exception, so a caller of `DataPlanner` rarely sees one.

Each row is one cause. "Raised by" names the calls that raise the exception to a caller who makes
them directly. "Inside a run" says what a request does when the exception is raised during it.

| Exception | Raised by | Raised when | Inside a run |
| --- | --- | --- | --- |
| `UnknownOperatorSuiteError` | `operator_suite`, `build_data_planner_from_settings` | No suite has the name | Not raised |
| `RegistryUnreadableError` | `open_registry`, `build_data_planner_from_settings` | The path names no file, or the file cannot be read | Not raised |
| `InvalidSettingError` | The `PlannerSettings` constructor | A field carries a value the planner cannot be built with | Not raised |
| `InvalidSettingError` | The `LLMClient` constructor, `build_data_planner` | The properties name no model, carry an unknown `llm.*` key, or ask for a cache without `diskcache` installed | Not raised |
| `InvalidSettingError` | `LotusSuite.operators`, `build_data_planner` | A second LOTUS suite asks for a model or reasoning effort other than the one LOTUS is configured with in this process | Not raised |
| `InvalidSettingError` | `LLMClient.execute_api_call`, `LLMClient.aexecute_api_call` | The properties one call carries hold a value the client cannot send | Ends the request as `planner_failed` |
| `OperatorRoutingError` | `OperatorRouter.register`, `build_data_planner` | Two suites register the same operator id | Not raised |
| `OperatorRoutingError` | `BlueSuite.operators`, `build_data_planner` | The operator registry does not carry an operation the suite is bound to | Not raised |
| `OperatorRoutingError` | `OperatorRouter.resolve` | No registered operator has the operator id | Ends the request as `planner_failed` |
| `PlanFormatError` | `LogicalPlan.from_dict`, `PhysicalPlan.from_dict`, `DataPipeline.from_dict` | A payload is missing a key or carries a value its node kind does not take | Not raised |
| `UnknownNodeError` | `LogicalPlan.node`, `LogicalPlan.consumers` | No node of the plan has the id | Ends the request as `planner_failed` |
| `UnknownFragmentError` | `PhysicalPlan.fragment`, `PhysicalPlan.fragment_for_node` | No fragment of the cover has the id or covers the node | Ends the request as `planner_failed` |
| `PlanValidationError` | `validate_logical_plan` | A logical plan fails a structural check | `LogicalPlanner` catches it and writes the plan again |
| `PlanValidationError` | `validate_physical_plan` | A cover fails a structural check | Ends the request as `planner_failed` |
| `ReturnAnswerError` | `project_answer` | The finished rows do not carry a column the answer names | The run loop catches it and replans |
| `BudgetExhaustedError` | `RunCounters.spend_operator_call`, `execute_physical_operator` | The operator call budget reaches its cap | `FragmentRunner` catches it and returns `budget_exhausted` |
| `RecoverableOperatorError` | An operator's callable | The operator refuses input another operator might accept | `execute_physical_operator` catches it and returns a failed `ExecutionResult` with `ErrorKind.RECOVERABLE` |
| `UnrecoverableOperatorError` | An operator's callable | The operator fails for a reason no other operator would avoid | `execute_physical_operator` catches it and returns a failed `ExecutionResult` with `ErrorKind.UNRECOVERABLE` |
| `SourceQueryError` | `execute_query`, `execute_write` | The source refuses the query or statement | The operator reading the source catches it and raises `RecoverableOperatorError` |
| `SourceUnreachableError` | `connect_source`, `execute_query`, `execute_write` | The registry holds no connectable source under the name | The operator reading the source catches it and raises `UnrecoverableOperatorError` |
| `LLMError` | `LLMClient.complete_structured`, `LLMClient.execute_api_call`, `LLMClient.aexecute_api_call` | The provider fails after the client's retries, or answers in a shape the client cannot read | Ends the request as `llm_failed` |
| `BlueDataPlannerError` | Any component, such as `DataPipeline.cover` | The planner reaches a state it treats as a defect, such as a pipeline with no current physical plan | Ends the request as `planner_failed` |

Both operator errors carry a `report: dict[str, Any] | None`, which reaches the caller as
`ExecutionResult.report`.

`LLMError` carries `code` (`provider_error` or `bad_response`) and `provider_error`, the
exception the provider raised when there was one. `blue_data_planner.llm` re-exports it.

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
| Catalog | `AttributeRef`, `AttributeView`, `ValueCount`, `EntityMatch`, `SourceRecord`, `CatalogPreview` |
| Run | `RunResult`, `RunStatus`, `RunLimits`, `RunCounters`, `RunConversations`, `Conversation`, `Turn`, `LLMUsage`, `total_cost` |
| Per-step results | `ExecutionResult`, `ExecutionStatus`, `ErrorInfo`, `ErrorKind`, `FragmentResult`, `ParameterAttempt`, `FragmentReviewDecision`, `FailedFragment`, `DiscoveryResult`, `LogicalPlanResult`, `PhysicalPlanResult` |
| Exceptions | Every exception in [Exceptions](#exceptions) |

`LogicalPlan`, `PhysicalPlan`, and `DataPipeline` each have `to_dict` and `from_dict`, which
convert a plan to a plain dict and back for storing or sending it outside the process.

### `blue_data_planner.plan`

Functions over a plan that need no model, no source, and no operator call.

```python
def validate_logical_plan(plan: LogicalPlan, entity_paths: frozenset[str], carried_node_ids: frozenset[str] = frozenset(), *, max_nodes: int) -> None: ...
def validate_physical_plan(plan: LogicalPlan, physical_plan: PhysicalPlan) -> None: ...
def candidate_fragments(plan: LogicalPlan, root_node_id: str, logical_operators: frozenset[LogicalOperator]) -> list[frozenset[str]]: ...
def fragment_frontier(plan: LogicalPlan, node_ids: frozenset[str]) -> list[str]: ...
def signatures(plan: LogicalPlan, cover: PhysicalPlan) -> dict[str, str]: ...
def logical_plan_schema() -> type[BaseModel]: ...
def cover_choice_schema(
    plan: LogicalPlan, root_node_id: str, candidates: list[tuple[frozenset[str], PhysicalOperator]],
    reusable: PhysicalPlanFragment | None,
) -> type[BaseModel]: ...
def candidate_label(root_node_id: str, operator_id: str, node_ids: frozenset[str]) -> str: ...

REUSE = "reuse"
NONE = "none"
```

Both validators return nothing and raise `PlanValidationError` when the plan is invalid.
`signatures` returns one signature per node that produces a result. A change anywhere above a node
changes the node's signature, and a new cover reuses an old cover's result for a node whose
signature is unchanged. `OutputColumnModel` is the response schema for one required output column.
`REUSE` stands in for an operator id in the label of the candidate that reuses an earlier
fragment. `NONE` is the label a cover choice
writes when it takes none of the candidates.

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

Every operator call goes through `execute_physical_operator`. It validates the parameters, bills
one operator call, runs the operator, and checks the result's declared columns. It returns a failed
`ExecutionResult` when the operator raises an operator error, and raises `BlueDataPlannerError`
when the schema rejects the parameters.

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

`operator_suite` returns the suite registered under a name, with its default options. It raises
`UnknownOperatorSuiteError` when no suite has the name.

| Suite | Import | Options | Also exports |
| --- | --- | --- | --- |
| `BuiltinSuite` | `blue_data_planner.operators.builtin` | None | `builtin_operators` |
| `LotusSuite` | `blue_data_planner.operators.adapters.lotus` | `model: str \| None = None`, `max_rows: int = 5000`, `reasoning_effort: str \| None = None` | `lotus_operators`, `lotus_is_installed` |
| `BlueSuite` | `blue_data_planner.operators.adapters.blue` | `operator_registry: OperatorRegistry \| None = None`, `model: str \| None = None`, `reasoning_effort: str \| None = None` | `blue_operators` |

`LotusSuite.model` defaults to the model every other component uses. `LotusSuite.operators`
returns an empty list when LOTUS is not installed. `BlueSuite.operator_registry` defaults to the
active registry of `blue-data-ops`, which holds the one reviewed version of each operation.
`BlueSuite.model` defaults to the model every other component uses, and reaches `blue-data-ops`
without its provider route. `blue-data-ops` sends the call to the provider `BLUE_DATA_LLM_PROVIDER`
names, `openai` by default.
[architecture.md](architecture.md#operator-suites) describes what each suite offers, and
[extending.md](extending.md) how to add one.

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
    def entity_row_count(self, entity_path: str) -> int | None: ...
```

`open_registry` raises `RegistryUnreadableError` when the file is missing or unreadable.
`DataRegistry` is re-exported from `blue-data`. The four `DataCatalog` readings are all the planning
components see of the catalog: `preview` for the logical planner, `search_records` for the catalog
searcher, `attribute_views` and `entity_row_count` for the fragment runner.

### `blue_data_planner.llm`

The only module that calls a provider.

```python
class LLMClient:
    def __init__(self, name: str, properties: dict[str, Any] | None = None) -> None: ...
    def complete_structured(self, conversation: Conversation, response_model: type[M]) -> M: ...
    def execute_api_call(self, input: Any, properties: dict[str, Any] | None = None) -> Any: ...
    async def aexecute_api_call(
        self, input: Any, properties: dict[str, Any] | None = None
    ) -> Any: ...


class LLMClientProvider(ABC):
    def get_client(self, properties: dict[str, Any]) -> LLMClient: ...


def resolve_llm_client(
    properties: dict[str, Any] | None, provider: LLMClientProvider | None = None
) -> LLMClient: ...
```

The planning components call `complete_structured`. It sends a `Conversation`, parses the answer
into the Pydantic `response_model`, appends the answer as a turn, and records an `LLMUsage` on the
conversation. `aexecute_api_call` is the library's one async method.
`CachingLLMClientProvider` reuses one client per configuration. `UsageLedger` holds a client's own
per-call accounting, separate from the conversation's: one `UsageRecord` per call, carrying the
model, the token counts, the cost, the latency, and whether the call was served from the cache.

### The components

Each component takes its dependencies in its constructor and holds no bound of its own. It reads
the bounds from the `RunCounters` passed to each call.

| Class | Module | Constructor | Operation |
| --- | --- | --- | --- |
| `LogicalPlanner` | `logical_planner` | `(llm_client, catalog)` | `generate(request, conversation, counters, parent=None, carried_node_ids=frozenset(), results=None, replan_reason=None, new_discovery_node_ids=frozenset()) -> LogicalPlanResult` |
| `PhysicalPlanner` | `physical_planner` | `(llm_client, router)` | `cover(request, plan, conversation, previous, results, failed_fragment=None) -> PhysicalPlanResult` |
| `FragmentRunner` | `fragment_runner` | `(llm_client, catalog, router)` | `run(fragment, conversation, inputs, counters) -> FragmentResult` |
| `CatalogSearcher` | `catalog_searcher` | `(llm_client, catalog, max_search_terms=8)` | `search(node, conversation, inputs) -> DiscoveryResult` |

`max_search_terms` is the only number a constructor takes, because it does not bound a run. When
a search writes more terms, the extra terms are dropped and their count is reported as
`DiscoveryResult.dropped_terms`.

`RunState`, in `blue_data_planner.run_state`, holds the mutable state of one request. Only the
run loop installs plans and records results in it. The components the run loop calls add turns to
their own conversations and spend from its counters. A caller reads the same contents from the
request's `RunResult`.
