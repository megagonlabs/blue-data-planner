# blue-data-planner — API / Interface Spec

Three interfaces call into the library: the Python library itself, a CLI, and an MCP server. A REST
API is reserved and not built ([03-high-level-architecture.md](03-high-level-architecture.md)). No
planning, operator selection, or execution logic lives in an interface; each one parses requests,
formats responses, maps errors, and loads configuration. Authentication and authorization belong to
the deployment.

This document covers what each interface accepts and returns. What each class does is in
[06-library.md](06-library.md), the shape of a plan and a result and the JSON form they travel in is
in [10-data-model.md](10-data-model.md), and every configuration value with its default is in
[12-deployment-ops.md](12-deployment-ops.md#4-configuration).

---

## 1. Python library

The canonical interface. The CLI and the MCP server call it; neither adds behavior of its own.

### The public surface

| Import | Holds |
| --- | --- |
| `blue_data_planner` | `DataPlanner`, `build_data_planner`, `PlannerSettings`, `build_data_planner_from_settings`, `RunResult`, `RunLimits`, `RunStatus`, `BlueDataPlannerError`, `__version__` |
| `blue_data_planner.data_planner` | `DataPlanner`, `build_data_planner`, `DEFAULT_LLM_CLIENT_NAME` |
| `blue_data_planner.settings` | `PlannerSettings`, `build_data_planner_from_settings`, `DEFAULT_MODEL`, `DEFAULT_OPERATOR_SUITES`, `REASONING_EFFORTS` |
| `blue_data_planner.logical_planner`, `.physical_planner`, `.fragment_runner` | The three planning components, one module each |
| `blue_data_planner.catalog_searcher` | `CatalogSearcher` |
| `blue_data_planner.run_state` | `RunState` |
| `blue_data_planner.types` | Plan, node, result, catalog, and limit types, the enums, `summarize_table`, `total_cost`, and every exception this library defines |
| `blue_data_planner.plan` | `validate_logical_plan`, `validate_physical_plan`, `candidate_fragments`, `candidate_label`, `fragment_frontier`, `signatures`, the response schemas, and the `NONE` and `REUSE` markers |
| `blue_data_planner.execution` | `execute_physical_operator`, `check_output_columns`, `project_answer` |
| `blue_data_planner.operators` | `OperatorRouter`, `OperatorSuite`, `operator_suite`, `OPERATOR_SUITE_NAMES` |
| `blue_data_planner.operators.builtin` | `BuiltinSuite`, `builtin_operators` |
| `blue_data_planner.operators.adapters.blue` | `BlueSuite`, `blue_operators` |
| `blue_data_planner.operators.adapters.lotus` | `LotusSuite`, `lotus_operators`, `lotus_is_installed` |
| `blue_data_planner.data` | `DataCatalog`, `DataRegistry`, `open_registry`, `connect_source`, `execute_query`, `execute_write` |
| `blue_data_planner.llm` | `LLMClient`, `Conversation`, `Turn`, `LLMError`, `LLMClientProvider`, `CachingLLMClientProvider`, `resolve_llm_client`, `UsageLedger`, `UsageRecord` |

Public is the package root, every module directly inside it, the `__init__.py` of `types/`,
`plan/`, `execution/`, `operators/`, `data/`, and `llm/`, and the module of each suite listed above.
`prompts/` is not public: the prompt text and the functions that render run data into it change
with the prompts, within a minor version. A module nested anywhere else is not public either, and
can be split or renamed within a minor version. A caller
who wants physical operators alone reads a suite's module and `types`, and constructs no planner.

A public name is covered by semantic versioning, so renaming or removing one breaks a version.
`platform/*` imports these modules and no others ([08-coding-style.md](08-coding-style.md)), and
every signature here is typed, since the package ships `py.typed`. When an interface needs
something absent from this list, the library is missing a method, and the fix is to add one.

### Assembly

```python
def build_data_planner(
    data_registry: DataRegistry,
    *,
    suites: Sequence[OperatorSuite],
    model: str,
    reasoning_effort: str | None = None,
    limits: RunLimits | None = None,
    llm_client_name: str = "blue_data_planner",
    provider_timeout_seconds: int | None = None,  # None: the client's default of 120
) -> DataPlanner: ...


def build_data_planner_from_settings(
    settings: PlannerSettings, *, llm_client_name: str = "blue_data_planner"
) -> DataPlanner: ...


@dataclass(frozen=True)
class PlannerSettings:
    registry_path: Path
    model: str = DEFAULT_MODEL
    reasoning_effort: str = "low"
    operator_suites: tuple[str, ...] = DEFAULT_OPERATOR_SUITES
    lotus_model: str | None = None            # None: use model
    lotus_reasoning_effort: str = "none"
    lotus_max_rows: int = 5000
    blue_model: str | None = None             # None: use model
    blue_reasoning_effort: str = "none"
    provider_timeout_seconds: int = 120
    limits: RunLimits = RunLimits()


class DataPlanner:
    def __init__(
        self,
        logical_planner: LogicalPlanner,
        physical_planner: PhysicalPlanner,
        fragment_runner: FragmentRunner,
        catalog_searcher: CatalogSearcher,
        limits: RunLimits,
    ) -> None: ...
```

The three ways to build a planner differ in what the caller already holds.

| Caller holds | Calls |
| --- | --- |
| A registry and suite objects | `build_data_planner` |
| Configured values, such as an interface resolving its own settings | `build_data_planner_from_settings`, which opens the registry and builds the suites the settings name |
| Four components, such as a different model per component or a fake in a test | The `DataPlanner` constructor |

The suites a caller passes are objects, each carrying its own options.

```python
def open_registry(registry_path: Path) -> DataRegistry: ...
def operator_suite(name: str) -> OperatorSuite: ...


@dataclass(frozen=True)
class BuiltinSuite:
    pass


@dataclass(frozen=True)
class BlueSuite:
    operator_registry: OperatorRegistry | None = None
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class LotusSuite:
    model: str | None = None
    max_rows: int = 5000
    reasoning_effort: str | None = None
```

A registry is populated by `blue-data` before it reaches this package (ADR-003). `open_registry`
reads a serialized one; a caller holding a `DataRegistry` it assembled itself passes that one
instead. `operator_suite` maps a configured name (`builtin`, `blue`, or `lotus`) to a suite with its
default options. A `LotusSuite` registers nothing where LOTUS is not installed.

```python
from pathlib import Path

from blue_data_planner import build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators.builtin import BuiltinSuite

planner = build_data_planner(
    open_registry(Path("registry.json")),
    suites=[BuiltinSuite()],
    model="openai/gpt-6-luna",
)
result = planner.run("Which three countries do the most customers come from?")
```

### Operations

```python
class DataPlanner:
    def run(self, request: str, *, cancel: threading.Event | None = None) -> RunResult: ...
    def plan(self, request: str, *, cancel: threading.Event | None = None) -> RunResult: ...
```

| Operation | Does | Can end as | Spends operator calls |
| --- | --- | --- | --- |
| `run` | Plans, covers, executes, and repairs until the request ends | Any request ending in [Results](#results) except `planned` | Yes |
| `plan` | Plans, resolves the discovery it can, and covers, stopping before the first operator call | `planned`, `invalid_plan`, `replan_exhausted`, `llm_failed`, `planner_failed`, `deadline_exceeded`, `cancelled` | No |

Both take a request in plain language, and optionally an event the caller sets from another thread
to cancel it. No operation accepts a plan, and neither takes a limit, since limits are fixed when the
planner is built. `plan` returns the plan and the
cover `run` would start executing: the catalog searches, continuations, and replans that need no
operator call have already run. A `DISCOVER` node waiting on a fragment's rows stays unresolved.

### Results

Both operations return a `RunResult`, whichever way the request ended. Its fields are fixed in [10,
what a run produces](10-data-model.md#3-what-a-run-produces), and `to_dict` writes the JSON form
every interface sends ([10, shapes that leave the
process](10-data-model.md#5-shapes-that-leave-the-process)).

| Field | Type | Holds |
| --- | --- | --- |
| `status` | `RunStatus` | How the request ended |
| `answer` | `DataBundle \| None` | The `RETURN` node's rows. `None` unless `status` is `ok` |
| `failure_reason` | `str \| None` | Why the run ended as it did. Every failure status carries one; `ok` and `planned` never do |
| `pipeline` | `DataPipeline` | Every logical plan and every cover the request produced, with the current pair named |
| `results` | `dict[str, FragmentResult \| DiscoveryResult]` | How each fragment ended and what each search found, keyed by the signature of the work |
| `counters` | `RunCounters` | What the run spent against the three bounds counted per request, and the `RunLimits` it ran under |
| `conversations` | `RunConversations` | Every exchange with the model, by the component that had it, with its usage |

`answer` is `None` rather than `[]` on any status other than `ok`, so a caller who ignores the
status cannot mistake a failed run for an empty answer (ADR-015).

| `RunStatus` | The request ended because |
| --- | --- |
| `ok` | The plan finished and the answer was projected |
| `planned` | `plan` reached the first operator call and stopped before it |
| `invalid_plan` | The logical planner ran out of corrections without a plan that passes validation |
| `replan_exhausted` | `failure_replans` or `control_replans` was reached |
| `budget_exhausted` | `operator_calls` was reached |
| `operator_failed` | An operator failed for a reason no other operator would avoid |
| `llm_failed` | The model provider failed after retries, or answered in a shape the client cannot read |
| `planner_failed` | A defect in the planner, with its message as the failure reason |
| `deadline_exceeded` | `wall_clock_seconds` passed, checked before the next step |
| `cancelled` | The caller set the `cancel` event, checked before the next step |

`RunStatus` has two more members, `needs_repair` and `needs_replan`, which a fragment returns inside
a run. A `RunResult` never carries either.

### Limits

```python
@dataclass(frozen=True)
class RunLimits:
    operator_calls: int = 30
    plan_corrections: int = 5
    parameter_repairs: int = 3
    cover_repairs: int = 2
    failure_replans: int = 2
    control_replans: int = 5
    wall_clock_seconds: int = 600
```

| Bound | Counted | Reaching it |
| --- | --- | --- |
| `operator_calls` | Per request | `budget_exhausted` |
| `plan_corrections` | Per plan | `invalid_plan` |
| `parameter_repairs` | Per fragment | The cover repair |
| `cover_repairs` | Per logical plan | The replan |
| `failure_replans` | Per request | `replan_exhausted` |
| `control_replans` | Per request | `replan_exhausted` |
| `wall_clock_seconds` | Per request, checked before each step | `deadline_exceeded` |

The three counted per request are the fields of `RunCounters`. The other three reset inside a
request. What each bound limits is in [06-library.md](06-library.md#budgets-and-limits), and how a
deployment sets one is in [12-deployment-ops.md](12-deployment-ops.md#5-request-limits).

### Exceptions

Every exception this library defines is in `types/errors.py` and inherits `BlueDataPlannerError`.
Exhaustion, operator failure, and provider failure are statuses, not exceptions (ADR-015).

Each row is one cause. "Raised by" names the calls that raise the exception to a caller who makes
them directly. "Inside a run" says what a request does when the exception is raised during it.

| Exception | Raised by | Raised when | Inside a run |
| --- | --- | --- | --- |
| `UnknownOperatorSuiteError` | `operator_suite`, `build_data_planner_from_settings` | A name registers no operator suite | Not raised |
| `RegistryUnreadableError` | `open_registry`, `build_data_planner_from_settings` | The path a registry is opened from names no file, or the file cannot be read | Not raised |
| `InvalidSettingError` | The `PlannerSettings` constructor | A field carries a value the planner cannot be built with | Not raised |
| `InvalidSettingError` | The `LLMClient` constructor, `build_data_planner` | The properties the client is built with carry a value the client cannot be built with | Not raised |
| `InvalidSettingError` | `LotusSuite.operators`, `build_data_planner` | A second LOTUS suite asks for a model or reasoning effort other than the one LOTUS is configured with in this process | Not raised |
| `InvalidSettingError` | `LLMClient.execute_api_call`, `LLMClient.aexecute_api_call` | The properties one call carries hold a value the client cannot send | Ends the request as `planner_failed` |
| `OperatorRoutingError` | `OperatorRouter.register`, `build_data_planner` | Two suites register the same operator id | Not raised |
| `OperatorRoutingError` | `BlueSuite.operators`, `build_data_planner` | The operator registry a `BlueSuite` resolves against does not carry an operation the suite is bound to | Not raised |
| `OperatorRoutingError` | `OperatorRouter.resolve` | No registered operator has the operator id | Ends the request as `planner_failed` |
| `PlanFormatError` | `from_dict` | A plan payload is missing a key or carries a value its node kind does not take | Not raised |
| `UnknownNodeError` | `LogicalPlan.node`, `LogicalPlan.consumers` | No node of the plan has the id | Ends the request as `planner_failed` |
| `UnknownFragmentError` | `PhysicalPlan.fragment`, `PhysicalPlan.fragment_for_node` | No fragment of the cover has the id or covers the node | Ends the request as `planner_failed` |
| `PlanValidationError` | `validate_logical_plan` | A logical plan fails a structural check | `LogicalPlanner` catches it and writes the plan again |
| `PlanValidationError` | `validate_physical_plan` | A cover fails a structural check | Ends the request as `planner_failed` |
| `ReturnAnswerError` | `project_answer` | The finished rows do not carry a column the ending names | The run loop catches it and replans |
| `BudgetExhaustedError` | `RunCounters.spend_operator_call`, `execute_physical_operator` | The operator call budget reaches its cap | `FragmentRunner` catches it and returns `budget_exhausted` |
| `DeclaredTypeMismatchError` | The coercion in `execution/output_columns.py` | A value does not spell the type declared for its column | `check_output_columns` catches it and returns a recoverable failed result |
| `RecoverableOperatorError` | An operator's callable | The operator refuses its input | `execute_physical_operator` catches it and returns a recoverable failed `ExecutionResult` |
| `UnrecoverableOperatorError` | An operator's callable | The operator fails for a reason no other operator would avoid | `execute_physical_operator` catches it and returns an unrecoverable failed `ExecutionResult` |
| `SourceQueryError` | `execute_query`, `execute_write` | A source refuses a query | The operator reading the source catches it and raises `RecoverableOperatorError` |
| `SourceUnreachableError` | `connect_source`, `execute_query`, `execute_write` | The registry holds no connectable source under a name | The operator reading the source catches it and raises `UnrecoverableOperatorError` |
| `LLMError` | `LLMClient.complete_structured`, `LLMClient.execute_api_call`, `LLMClient.aexecute_api_call` | The provider fails after retries, or answers in a shape the client cannot read | Ends the request as `llm_failed` |
| Any other `BlueDataPlannerError` | The component that raised it | The planner reaches a state it treats as a defect | Ends the request as `planner_failed` |
| `ValueError`, `TypeError`, and an operator suite's own exceptions | Any call | The code meets malformed state or a missing required value, or an operator raises an exception other than the two operator errors | Propagates out of `run` and `plan` |

While a request runs, `run` and `plan` return a `RunResult` unless an exception in the last row
propagates.

---

## 2. CLI

`blue-planner` puts the two library operations on the command line, in the shape the Blue CLI
family uses: `<cli> <component> <action> <parameters>` (ADR-027).

### Global options

```text
blue-planner [--profile NAME] COMPONENT ACTION [PARAMETERS] [OPTIONS]
blue-planner --version
```

| Option | Effect |
| --- | --- |
| `--profile NAME` | Which profile supplies configuration. Given before the component. Without it, `BLUE_DATA_PROFILE` names the profile, then the one `profile select` chose |
| `--version` | Print the version of the CLI package and exit |

### `blue-planner data`

```text
blue-planner data query REQUEST [--registry-path PATH] [--model NAME] [--format table|json]
                                [--explain] [--log] [--log-limit N]
```

`data query` runs `run` and prints the answer. With `--explain` it runs `plan` instead, prints every
plan version and every cover the planning produced, and spends no operator call. The request is a
positional parameter in plain language; no command accepts a plan.

| Option | Effect |
| --- | --- |
| `--registry-path PATH` | The `blue-data` registry to plan over. Overrides `BLUE_DATA_REGISTRY_PATH` |
| `--model NAME` | The litellm model string the planner uses. Overrides `BLUE_DATA_PLANNER_MODEL`. Reaches LOTUS where `BLUE_DATA_PLANNER_LOTUS_MODEL` is unset, and the Blue suite where `BLUE_DATA_PLANNER_BLUE_MODEL` is unset |
| `--format table\|json` | How the result is written. Defaults to `BLUE_DATA_CLI_FORMAT` |
| `--explain` | Write the plan for the request and stop before the first operator call |
| `--log` | Report what the run did, fragment by fragment, after the run |
| `--log-limit N` | The characters each line of the log is cut to. Defaults to `BLUE_DATA_CLI_LOG_LIMIT` |

`--format` and `--log` are independent (ADR-032): the first decides how a result is written, the
second whether the run behind it is reported. A bound on a run is set through its profile parameter
(ADR-028). The formats are `table` and `json` (ADR-029).

### `blue-planner profile`

```text
blue-planner profile add NAME
blue-planner profile remove NAME [--confirm]
blue-planner profile list [--format table|json]
blue-planner profile config [PARAM [VALUE]]
blue-planner profile inspect [--format table|json]
blue-planner profile select NAME
```

| Action | Does |
| --- | --- |
| `add` | Creates a profile |
| `remove` | Deletes a profile after a confirmation; `--confirm` skips the prompt |
| `list` | Names every profile, marking the selected one |
| `config` | Prompts for every required parameter, prompts for one, or sets one. A secret is always prompted for, never taken as a value |
| `inspect` | Every parameter, the value in force, and the tier that supplied it |
| `select` | Makes one profile the one later commands read, unless `--profile` or `BLUE_DATA_PROFILE` names another |

### Output

Standard output carries the command's result: the answer, or the plan where `--explain` asked for
one. Standard error carries the progress indicator, the log, the closing summary, and errors, so
redirecting standard output captures the result alone. Under `--format json`, standard output
carries exactly one document whatever happened: the result, or an error document where the command
failed before producing one.

| `--format` | Standard output | `--log` |
| --- | --- | --- |
| `table` | Each table of the result under its own column names | One table per physical plan on standard error |
| `json` | One document: `RunResult.to_dict` without `pipeline` ([10](10-data-model.md#5-shapes-that-leave-the-process)) | Adds `pipeline` to the document instead of drawing the log on standard error |

With `--explain`, the JSON document carries `pipeline`, since the plan is the result. What the log
shows for each fragment, and how, is in [platform/planner-cli/README.md](../platform/planner-cli/README.md).

### Errors and exit codes

An error other than a usage error reaches standard error as `ERROR: <CODE> <description>`. Under
`--format json`, it also reaches standard output as one document (ADR-044):

```json
{"error": "<description>", "code": "<CODE>"}
```

The format is the one in force for the command. Where the error came before the format was resolved,
or is that it cannot be — a named profile that does not exist, `BLUE_DATA_CLI_FORMAT` naming no
format — `--format` decides it, then `BLUE_DATA_CLI_FORMAT` in the environment, and the plain line
alone is written when neither says `json`. A command without `--format` writes the plain line alone.
A usage error prints the usage message alone, whatever `--format` says.

| Code | Raised for |
| --- | --- |
| `CONFIG_INVALID` | A required parameter with no value, such as the registry path, a value that cannot be used, such as an unknown operator suite, or `profile add` naming a profile that already exists |
| `REGISTRY_UNREADABLE` | A registry path that does not exist or does not parse |
| `PROFILE_NOT_FOUND` | A named profile that does not exist |
| `ABORTED` | A declined confirmation prompt |
| `INVALID_PLAN`, `REPLAN_EXHAUSTED`, `BUDGET_EXHAUSTED`, `OPERATOR_FAILED`, `LLM_FAILED`, `PLANNER_FAILED`, `DEADLINE_EXCEEDED`, `CANCELLED` | A run that ended with that status, carrying its failure reason |
| `INTERNAL_ERROR` | Any exception no code above covers, reported with its type and message. Under `--log`, its traceback follows (ADR-043) |

A missing or rejected API key is reported by the provider on the first model call, so the run ends
as `LLM_FAILED` with the provider's message as its failure reason. Under `--format json`, a run that
ended with a failure status prints its run document rather than an error document, so standard
output holds either a run document, which carries `status`, or an error document, which carries
`error`.

| Exit code | Meaning |
| --- | --- |
| 0 | Success; for `data query`, the run ended `ok` or `planned` |
| 1 | Any error above (ADR-030) |
| 2 | A usage error: a missing argument, an unknown option, a value outside what an option allows |

---

## 3. MCP server

`blue-data-planner-mcp` hosts one tool, which an agent uses to hand over a whole question. It builds
one planner from the environment on the first tool call and holds it for the process lifetime.

| Tool | Calls |
| --- | --- |
| `query_data(request, explain=False, include_plan=False)` | `DataPlanner.run`, or `DataPlanner.plan` when `explain` is set |

The tool returns `RunResult.to_dict` as a JSON string
([10, shapes that leave the process](10-data-model.md#5-shapes-that-leave-the-process)), with
`pipeline` only when `explain` or `include_plan` is set. No argument takes a limit: how much a run
may spend is a deployment decision.

The verb and the noun are the CLI's `data query` (ADR-038). Each parameter's description and an
example response are in [platform/planner-mcp/README.md](../platform/planner-mcp/README.md#the-tool).

| Not exposed | Because |
| --- | --- |
| Profile management | The server has no interactive user; the `env` block of whoever launches it configures it |
| Registry and catalog lookups | `blue-data`'s `registry-mcp` already exposes them, and the operator registry is in-process (ADR-009) |
| One tool per operator | Driving a single operator is an operator suite's concern |
| MCP resources | The family is tools-only (ADR-038) |

### Errors

| Condition | Result |
| --- | --- |
| A run that did not finish, or an operator failing inside one | A successful tool call, with the status and the fragment's attempt in the payload |
| Anything the tool raises: an unreadable registry, an unknown operator suite name, an invalid setting | A successful tool call whose payload is `{"error": "<message>", "code": "<CODE>"}` (ADR-044) |
| An argument the tool's schema refuses | A protocol-level tool error, raised before the tool runs |

The codes are the CLI's, for the failures the two interfaces share: `REGISTRY_UNREADABLE` for a
registry path that does not exist or does not parse, `CONFIG_INVALID` for a setting with no value or
one that cannot be used, and `INTERNAL_ERROR` for anything else, whose message names the exception's
type.

---

## 4. REST API

Not in the first release. `platform/planner-api/` is reserved, and no package declares a web framework. A
REST API maps to the same two library operations the other interfaces use, so its endpoint shapes
are specified when it is built.

---

## Configuration

The library reads no environment variable: every value arrives through a constructor or a
`PlannerSettings`. Each interface resolves its own values and hands them over.

| Interface | Reads | Order |
| --- | --- | --- |
| CLI | Command options, the environment, and a profile | Command option, then environment variable, then profile, then default (ADR-028). `profile inspect` shows the tier that supplied each value |
| MCP server | The environment | Environment variable, then default |

Every variable, its default, and what it sets is in
[12-deployment-ops.md](12-deployment-ops.md#4-configuration), with the directory a CLI profile lives
in. Which parameters `profile config` asks for, and each one's type, are in
[platform/planner-cli/README.md](../platform/planner-cli/README.md).
