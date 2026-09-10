# blue-data-planner — Coding Style

This follows the shared Blue style in the `blue-data-kb-source` repository's own
`08-coding-style.md`, with the differences this repository has: it is synchronous, its errors are
partly statuses rather than exceptions, and it has import rules that hold the components apart.

`.github/instructions/python.instructions.md` is the working copy of these rules, read by coding
agents. This document is the source of truth; that file follows it.

## Tooling

| Concern | Tool | Config |
| --- | --- | --- |
| Formatting | `ruff format` | `pyproject.toml` |
| Linting | `ruff` | `pyproject.toml` |
| Type checking | `pyright` (strict) | `pyproject.toml` |
| Tests | `pytest` | `pyproject.toml` |

All four pass before work is reported as complete, in every package, and they run through `uv run`.

### `pyproject.toml` configuration

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]   # pycodestyle, pyflakes, isort, pyupgrade

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "strict"
venvPath = "."
venv = ".venv"
extraPaths = ["tests/stubs"]     # lib/ only: fakes for heavy optional dependencies
```

Prompt text is the one thing exempt from `E501`, because a line break inside a prompt changes the
bytes sent to a provider. The exemption names each file holding prompt text and nothing else, which
is the planning prompts under `prompts/`.

---

## Type hints

Required on every public method, public class attribute, and module-level constant. Private methods
are annotated too. `pyright` runs in strict mode, and its errors are build failures rather than
suggestions.

Every module opens with `from __future__ import annotations`, which allows forward references and
keeps type-only imports from causing circular imports.

Use 3.11 syntax and built-in generics:

```python
# correct
def operators_for(self, logical_op: LogicalOperator) -> list[PhysicalOperator]: ...
def next_ready(self) -> PhysicalPlanFragment | PipelineNode | None: ...


# incorrect
from typing import List, Optional


def operators_for(self, logical_op: LogicalOperator) -> List[PhysicalOperator]: ...
def next_ready(self) -> Optional[PhysicalPlanFragment]: ...
```

Every package ships a `py.typed` marker, so public signatures are part of the released interface.

A dependency shipping no types of its own is imported behind a targeted `pyright: ignore` where it
enters and delegates through `Any` from there. The import rules below put those imports in three
places, so untyped values stop at `data/`, at `operators/adapters/lotus/`, and at the one benchmark
script that imports `blue-data` directly, rather than spreading past them.

---

## Synchronous by default

The library is synchronous. `DataPlanner.run`, `RunState.next_ready`, `execute_physical_operator`,
every physical operator's callable, and every call through `data/` are ordinary functions.

```python
# correct
class DataPlanner:
    def run(self, request: str) -> PlanResult: ...


# incorrect — async through the core for no I/O concurrency
class DataPlanner:
    async def run(self, request: str) -> PlanResult: ...
```

The exception is `LLMClient`'s general provider call, `execute_api_call`, which has an async twin,
`aexecute_api_call`, so a caller with many independent provider calls can run them together
([06-library.md](06-library.md#llm)). The planner is not such a caller: the run loop takes one ready
fragment at a time, and inside a component each call is written against what the last one returned.

**Adapter bridge.** The MCP server's handlers are async because the SDK's are. They call the
synchronous library directly for fast work, and through `asyncio.to_thread` for anything that runs a
plan, so a long request does not block the event loop and stall the transport for the session.

---

## Naming

| Construct | Convention | Example |
| --- | --- | --- |
| Classes | PascalCase | `DataPlanner`, `PhysicalPlanner`, `FragmentRunner` |
| Methods and functions | snake_case | `operators_for`, `next_ready`, `execute_physical_operator` |
| Module-level constants | UPPER_SNAKE_CASE | `INPUT_ARITY` |
| Enum members | UPPER_SNAKE_CASE | `LogicalOperator.SORT_SUPERLATIVE`, `ErrorKind.RECOVERABLE` |
| Private members | single `_` prefix | `_run_pass` |
| Modules and packages | lowercase | `operators/`, `fragment_runner.py` |

No double-underscore name mangling. A single `_` marks everything private, whether a method, a
module-level helper, or a module.

### Five rules that decide a name

Each one settles a case where two plausible names were in use at once.

| Rule | Applied to |
| --- | --- |
| A data type is called a type. Everything else that could be called one is a kind | `ColumnDataType` and the field `dtype` describe the type of a value. A node kind, a source kind, and an `ErrorKind` are kinds |
| An id says which graph it names | `logical_node_ids`, `root_logical_node_id`, `InputData.logical_node_id`. A physical plan is full of logical node ids, so an unqualified `node_id` in it reads as its own |
| Two decisions with different scopes get two names | `FragmentReviewDecision` is what one review returned; `RunDecision` is what the run loop does next |
| A result type is called a result | `LogicalPlanResult`, `PhysicalPlanResult`, `FragmentResult`, `ExecutionResult`, `PlanResult` |
| A catalog record's column is an attribute; a table moving between fragments has columns | `AttributeRef`, `AttributeView`, `attribute_views`, and `EntityMatch.attributes` read the registry, which is `blue-data`'s own vocabulary. `required_output_columns`, `OutputColumn`, and `ColumnDataType` describe a table a fragment passes on, whatever kind of source produced it |

### Names a reader can picture

Names have to be guessable by a reader with no project context. Prefer a name that reveals the
result (`HtmlToMarkdownConverter`) over one that gestures at a role (`ArtifactBridge`). Concrete
words such as `adapter`, `converter`, `runner`, `router`, and `builder` are welcome where they fit.
Abstract ones are not: `artifact`, `bridge`, `layer`, `contract`, `machinery`, `seam`, `manager`,
`primitive`, and `verdict` name nothing a reader can picture. A `CatalogBridge` would say nothing
about what the class hands back; `CatalogSearcher` says it in the name. `entity` is the exception,
and only because `blue-data` gives it a fixed meaning: one level of a catalog record's path, the one
a `SCAN` is bound to. Used for anything else it is as empty as the rest of the list.

Borrowed metaphors go the same way. An ordered set of fallbacks is named by what each step rewrites
— the parameter repair, the fragment repair, the cover repair, the replan — not by a ladder and its
rungs, which says only that the steps are ordered and nothing about what any of them does.

A term borrowed from another field has to mean here what it means there. `Placement` was rejected
for the component that groups nodes because placing operators in distributed stream processing means
assigning them to machines, which runs the other way.

### Identifiers that appear in stored data

Five id formats travel in plans, results, and the registry, and they are not interchangeable. The
formats and what resolves each one are fixed in
[10-data-model.md](10-data-model.md#identifiers). One rule about them is style rather than data.

Enum *members* are upper case; their stored *values* are lower case for statuses and decisions
(`"ok"`, `"failed"`, `"accept"`) and upper case for the logical operators (`"FILTER"`), which read
as the vocabulary they are.

---

## Imports

- Absolute imports within the package. No relative imports.
- Order: standard library, third-party, then internal, separated by blank lines. `ruff`'s isort
    rules enforce it.
- One import per line, and no wildcard imports.

```python
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from blue_data_planner.types import LogicalPlan, PhysicalOperator, PhysicalPlanFragment
```

Six import rules carry architectural weight, and each can be checked by reading a file's imports.

| Rule | Why |
| --- | --- |
| `types/` imports nothing outside itself, and `plan/` imports only `types/` | Everything may import them without a cycle. It also fixes what belongs there: every data class crossing a module boundary, the catalog readings and `LLMUsage` included, since a type `PlanResult` names has to be nameable by everything that reads one. The modules inside `types/` may import each other, and nothing else in `blue_data_planner` |
| `execution/` imports only `types/`: no component, no `operators/`, no `llm/`, no `data/` | Invoking an operator needs no model, no router, and no registry (ADR-011) |
| Within `lib/src/`, only `data/` imports `blue-data` | One place to change, one place to substitute in tests (ADR-003) |
| Only `operators/adapters/lotus/` imports LOTUS, and only inside a callable | The package imports with LOTUS absent (ADR-004) |
| Only `operators/` imports `blue-data-ops` | The Blue operator library enters through one adapter, the way LOTUS does |
| `platform/*/src/` imports only the public surface | An adapter cannot grow planning logic it can reach (ADR-002). A platform package's tests stand outside the rule, which is what lets them use the library's own doubles |

The last rule names a shape rather than a count, and
[09-api-interface-spec.md](09-api-interface-spec.md#the-public-surface) fixes it: a module nested
inside one of the directories is not public, and an operator adapter is the one thing imported from
a module its own package does not re-export.

The third rule is scoped to `lib/src/` because `lib/scripts/` and `lib/tests/` ship in no package.
`lib/scripts/tag_bench/register_databases.py` imports `blue-data` directly to build a registry,
which is ingestion the library deliberately does not offer
([07-repo-organization.md](07-repo-organization.md#where-the-benchmark-lives)). Nothing else outside
`data/` imports it.

A signature elsewhere that has to name a registry takes the type from `blue_data_planner.data`,
which hands the name out beside the readings. What the rule keeps in one place is every call made on
a registry; naming one costs nothing and spreads no untyped value.

---

## Docstrings

One line on public classes and public methods. Nothing on private methods, `__init__`, properties,
or anything the name already explains. No parameter tables in prose: types live in the signature.

```python
class OperatorRouter:
    """Maps a logical operator to the physical operators that can implement it."""

    def operators_for(self, logical_op: LogicalOperator) -> list[PhysicalOperator]:
        """Return every operator declaring one logical operator, in registration order."""
        ...

    def _reject_duplicate(self, operator_id: str) -> None:
        # no docstring — private
        ...
```

A docstring that runs longer than a line is a sign the code needs splitting, with one exception:
where a rule is not obvious from the code, say why in the docstring rather than leaving a reader to
guess.

---

## Comments

Comment the *why*, never the *what*. A hidden constraint, a subtle invariant, a workaround for
something outside the repository.

```python
# correct — records a constraint the code cannot show
# Billed before the operator runs, so a call that really happened still counts when
# the column check rejects what it returned.
budget.spend()
rows = operator.call(inputs, parameters, required_output_columns)

# incorrect — restates the line below it
# get the operators for this logical operator
operators = self._router.operators_for(node.kind)
```

American English, in comments and in documentation.

---

## Error handling

Be strict. Treat an unexpected state as a bug and raise immediately. Never continue on malformed or
missing required data, and never fall back to an empty value after a parse failure.

A single base exception lives in `types/errors.py`, and everything raised by this package inherits
from it.

```python
class BlueDataPlannerError(Exception): ...


class PlanValidationError(BlueDataPlannerError): ...


class RecoverableOperatorError(BlueDataPlannerError): ...
```

Every exception the package raises, what provokes it, and what catches it are listed in
[06-library.md](06-library.md#errors). Raise with a message naming what is wrong and what was
expected. Validate at the library's boundary; internal code may then assume validated input.

### What raises and what returns a status

This is the rule that separates this repository from its siblings. A failure the planner is expected
to recover from gets a dedicated exception type, so recovery is something a component decides rather
than something that happens by accident. A run that exhausts an allowance returns a `PlanResult`
whose status says why, carrying every plan version, every fragment attempt, and every rejected one
(ADR-015).

| Outcome | Form |
| --- | --- |
| Bad parameters, a missing required column, an operator refusing input another operator might accept | A recoverable `ExecutionResult`, so a different operator can be tried |
| Out of parameter attempts, fragment attempts, covers, replans, or operator calls; an unreachable source; a provider failing after retries | A `PlanResult` with a status |
| A plan object that is not a plan, a missing required configuration value, an unknown node id or operator id | An exception |

`answer` is `None` rather than an empty list when a run did not finish, so a caller that ignores the
status cannot read a failure as an empty answer.

### Catching

Catch specific types. Never bare `except:`. Let exceptions reach the interface boundary, and let
each interface decide what to do with one.

`except Exception:` is permitted where a call crosses into a dependency reached over several
backends or providers that share no common failure type — the source read in `data/`, the provider
call in `llm/`. Listing each one's own exception type would mean listing one per backend or
provider, and the first one missed would pass the boundary uncaught — where nothing can catch it,
since no module outside the boundary may import the dependency it came from. A failure the design
classes as recoverable would reach the caller as a crash. Both boundaries convert instead, naming
the original error in its message and keeping it as the cause: the source read raises
`SourceQueryError`, the provider call raises `LLMError`.

| Interface | Handling |
| --- | --- |
| Python library | Exceptions propagate to the caller |
| MCP server | A run that did not finish, and an operator failing inside one, are successful tool calls whose payload carries the status and the attempt. An exception raised in the adapter itself becomes a tool error at the handler boundary ([09-api-interface-spec.md](09-api-interface-spec.md#errors)) |
| CLI | A top-level handler renders the message through Rich and sets an exit code |

The library has one `try` of its own that is not a conversion: `LLMError` is caught at the top of
the run loop, so a provider outage becomes the `llm_failed` status with everything the run had
already accumulated, and no component has to handle the provider itself.

---

## Command-line arguments

`platform/cli/` uses Click, and Rich for output. Scripts under `scripts/` use `argparse`.
The split is deliberate: a shipped multi-command interface earns a dependency that a build script
does not.

```python
@click.command()
@click.argument("request")
@click.option("--explain", is_flag=True, help="Print the plan without running it.")
def ask(request: str, explain: bool) -> None: ...
```

---

## Structure

Break a long function into smaller ones. Keep structures flat unless an extra layer clearly buys
readability. Use dataclasses where they help, and prefer a frozen one for anything a caller reads
off a result. A plan is frozen once written; what changes is which plan the run points at.

Keep intermediate state inspectable. Plans, per-fragment inputs and outputs, parameter attempts,
review decisions, and failure reasons are structured data, never formatted text. A value that only
exists as a rendered string cannot be checked, stored, or replayed.
