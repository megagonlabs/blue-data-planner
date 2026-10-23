# blue-data-planner — Decision Log

Each entry records a decision that had a real alternative and that other documents are written
against. Anything still provisional, and anything that only says what a release leaves out, is an
open question at the end rather than an entry here.

Every entry carries its date and its status. An entry is not rewritten once written: a changed
decision is a new entry, and the entry it replaces keeps its text under the status
`Superseded by ADR-NNN`. An entry found to rest on a guess is `Withdrawn`, and its question moves to
the open questions at the end, which are everything not yet decided.

---

## ADR-001 — Python Packaging and Tooling

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Python 3.11 or newer. One `pyproject.toml` per package, `src/` layout, setuptools as
the build backend. Runtime dependencies in `dependencies`, development tooling in
`optional-dependencies.dev`, and anything one adapter alone needs in that adapter's own package.

Local development, tests, and scripts run through `uv`. The install path documented for consumers is
`python -m venv` plus `pip install`.

**Rationale:** The rest of the Blue Data Intelligence Layer builds this way, so every package
installs the same way. The `src/` layout keeps a test run honest about what is installed, since the
package is not importable from the project root. `uv` gives fast, repeatable local environments, but
published instructions have to work for anyone with a standard toolchain and nothing extra installed
first.

**Consequences:** `lib/`, `platform/cli/`, and `platform/mcp/` each build on their own. Two
documented install paths have to agree about extras and minimum versions, and a published README is
never rewritten into `uv` commands.

---

## ADR-002 — Behavior Lives in `lib/`; Platform Packages Translate Only

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** `lib/` owns all behavior. A package under `platform/` parses requests, formats
responses, maps errors, and loads configuration, and imports only the library's public surface.
Planning, operator selection, and execution never live in an adapter.

**Rationale:** An adapter that can reach an internal module grows logic that then exists once per
interface. Restricting it to the public surface makes that impossible to do by accident, and it
means every interface exercises the same surface a library consumer sees.

**Consequences:** Anything two interfaces both need is a library function, the default wiring
included: `build_data_planner` lives in `lib/`, because wiring is not translation. A capability the
public surface does not expose cannot be reached from the CLI or from MCP, so exposing one is a
deliberate act.

---

## ADR-003 — Data Access Through a Single `blue-data` Boundary

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Within `lib/src/`, all access to `blue-data` goes through `blue_data_planner/data/`.
No other packaged module imports `blue-data`, and no application code talks to Redis, vector stores,
or files directly. Concrete implementations are supplied at startup.

The boundary reads a registry and never builds one. Registering sources, syncing them, collecting
statistics, and enriching them are ingestion, which belongs to `blue-data`, so a registry reaches
this package already populated. `lib/scripts/` and `lib/tests/` ship in no package and stand outside
the rule, which is what lets the benchmark build its own registry.

A registry travels as a plain `blue-data` value rather than behind a wrapper class of this package's
own. `data/` hands one out, takes one back, and owns every call made on one; a caller and every
other module hold it without reading it.

**Rationale:** One boundary gives one place to change when `blue-data` changes. Keeping ingestion
out is what stops the boundary from growing a second surface that duplicates a dependency's own
tooling. The benchmark still needs a registry built, and a build step is not shipped code.

A wrapper class would weaken the boundary rather than enforce it. An operator library reached
through an adapter takes a registry of its own (ADR-004), so the adapter would have to unwrap the
wrapper and hand over the `blue-data` registry, which is what that library would hold either way. A
wrapper that only forwards calls also has to be kept in step with the dependency it forwards to.
Passing the value keeps the rule that reading a file's imports can check.

**Consequences:** Anything the planner needs from `blue-data` is exposed through the boundary first,
as a function taking the registry. A caller with data and no registry builds one with `blue-data`
before constructing a planner. A `blue-data` type is named where the boundary hands a registry out
and takes one back, which is the price of not wrapping it, and `data/` hands that name out so the
dependency is imported in one place. The rule stays checkable by reading a file's imports, with one
path exclusion.

---

## ADR-004 — Operator Suites Enter Through Adapters, Never Vendored

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Every operator suite with an owner elsewhere, `blue-data-ops` included, is integrated
by an adapter under `operators/` that describes each of the suite's operations as a
`PhysicalOperator` and calls the suite as an outside dependency. No such suite's source is copied
into this repository and none is a git submodule. Writing a suite of our own is a different act and
is ADR-020's; what is ruled out here is holding a copy of someone else's. A suite is a
version-bounded dependency, optional where its install is heavy, and an optional suite's adapter
imports it inside the callable rather than at module import time.

**Rationale:** Copied source cannot pick up upstream fixes and quietly becomes a fork nobody
maintains. A git submodule appears in neither an sdist nor a wheel and cannot be resolved by pip, so
`pip install blue-data-planner[lotus]` would hand a consumer an adapter with nothing behind it,
which contradicts the consumer install path of ADR-001. A bounded version matters because an
adapter names each operation and its parameters, so a suite that renames one changes which
fragments are coverable. The lazy import is what makes `import blue_data_planner` work with the
suite absent, and what keeps the default test suite runnable without a multi-gigabyte install.

**Consequences:** The repository depends on how often each suite releases and how stable its
interface is, `blue-data-ops` included. An optional suite absent from the environment means the
router registers none of its operators, which is a smaller catalog rather than an error. Each suite
needs a stub under `lib/tests/stubs/`. Should a patched suite ever be needed, the requirement
becomes a git URL against a fork rather than a submodule.

---

## ADR-005 — Model Calls Go Through litellm, in One Module

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Every model call the library makes goes through `lib/src/blue_data_planner/llm/`,
which wraps `litellm`, and a provider is named by a model string rather than by an import. Every
call returning a structured value is constrained by a Pydantic response schema. Prompts are written
by hand and versioned in the repository. An outside operator suite keeps its own model configuration
and is not routed through this module.

**Rationale:** Two parts of the stack already speak litellm: the `blue-data-ops` operators call
models through this client, and LOTUS configures a litellm model of its own, so anything else would
mean two provider abstractions in one process for no gain. A model string instead of an import keeps
a provider change out of the code. A response schema is what makes a plan naming an invented
operation impossible rather than merely rejected, and what checks operator parameters before an
operator runs. Prompts are hand-written because automatic optimization needs a score to tune
against, and end-to-end accuracy scores a whole run rather than one component's prompt.

**Consequences:** Two model configurations exist and are set independently, which is a place they
can drift apart. Accounting follows the split: a call through `llm/` is recorded in
the run's conversations with its tokens, while a model call a suite makes internally is invisible
there and bills as part of the one operator call containing it (ADR-014). Planner spend is exact;
operator spend is a call count. The `builtin` suite is not an outside suite and calls through this
module like everything else here, so it can say what its own calls cost; that figure travels in the
operator call's report rather than beside the planner's own calls, which keeps that scope clear
(ADR-020).

---

## ADR-006 — Three-Stage Planning: Logical, Physical, Fragment Execution

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Planning happens in three stages, and the middle one decides how much of the plan each
operator takes on.

| Stage | Decides | Component |
| --- | --- | --- |
| Logical planning | What the request asks for, in a vocabulary naming no operator suite | `LogicalPlanner` |
| Physical planning | Which group of logical nodes each operator covers, and against which source | `PhysicalPlanner` |
| Fragment execution | What parameters a call runs with, and whether its result is any good | `FragmentRunner` |

The group of nodes one operator covers is a fragment: connected, convex, and single-exit. An
operator declares the set of logical operators it can group, candidate fragments follow
mechanically from that declaration, and a model picks among the candidates.

**Rationale:** Choosing the shape of a computation and choosing an implementation are different
problems, and doing both at once makes each harder. One operator per node excludes the most capable
operators in the catalog: `nl2sql` selects, filters, joins, and aggregates in one call, and a
knowledge base source resolves a multi-hop question inside its own graph. The size of a fragment is
the pushdown decision, and it is worth asking explicitly rather than answering with a rule that
always takes the largest match.

Federated query processing has this shape already, which is where the names come from. Calling
`nl2sql` a physical operator means calling something that contains a whole planner a physical
operator, which is exactly what a mediator does when it pushes a subplan to a remote source that
plans it again from scratch. Physical is relative to a level, not absolute.

**Consequences:** Three sets of prompts and three failure modes. The logical vocabulary becomes an
interface every operator suite maps onto, so adding to it invalidates every declaration written
against it. A node no registered operator declares is answered as `no_operator` and sent back to the
logical planner without spending an operator call. There is no startup check that every logical
operator has an implementation, because an operator declaring one alongside a source kind does not
make every node of that kind runnable.

---

## ADR-007 — A Plan Is a DAG of Typed Nodes, and Plans Are Versioned

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A plan is a directed acyclic graph of typed nodes held as structured data, not as
generated text or code, and it is checked before it runs. A node is one of three kinds.

| Kind | Nodes | Bound to an operator |
| --- | --- | --- |
| Operator | `SCAN`, `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION` | Yes, as part of a fragment |
| Terminal | `RETURN` | No. Projecting named columns admits no implementation choice |
| Control | `REPLAN`, `DISCOVER` | No. Both end the pass and return to the logical planner |

A plan is never edited. A replan adds a new logical plan with a new id and a `parent_id`, and a new
cover adds a new physical plan the same way. A new logical version carries over every node the run
holds a result for, grouped nodes included, and drops the rest. Results live in a map the run owns,
keyed by logical plan id and then node id, rather than on the nodes.

A plan is an output, never an input. No entry point accepts a plan and runs it, and `from_dict`
exists for reading a recorded plan rather than for feeding one back in.

**Rationale:** A plan held as data can be inspected at every stage and checked before it runs, which
catches whole classes of model mistakes without spending an operator call. A plan whose later steps
depend on data nobody has seen yet cannot be written in one pass, which is what a control node is
for; making it a kind rather than a flag on every node keeps an operator node's field count at one.

Versioning rather than editing removes a contradiction: a plan cannot be frozen and also have
continuations spliced into it. It also lets several covers of one logical plan hold their own
results, which one slot per node made impossible, and it settles the control nodes with no extra
mechanism — a resolved `DISCOVER` node carries over and never searches twice, and a `REPLAN` node
that fired is dropped and cannot fire again.

**Consequences:** A plan's shape is public interface, serialized into every MCP response, and a
change to it follows the package's own versioning; nothing persists a plan, so it carries no version
field of its own. A continuation rewrites unexecuted work in unrelated branches, and one that reads
a grouped node forces the fragment that grouped it to run again.
[10-data-model.md](10-data-model.md) fixes the shape.

---

## ADR-008 — A Logical Node Carries One Instruction and Typed Output Columns

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** An operator node carries an instruction written in plain language. It names no
operator, because the operator can differ between covers of the same node. A `SCAN` also names the
source it reads (ADR-022). The columns a later node depends on are recorded on the node as
`required_output_columns`, each one a name and a type drawn from `str`, `int`, `float`, and `bool`.
`SCAN` and `RETURN` carry none: a scan reads its source's attributes, and a `RETURN` names input
columns to project rather than promising output. The physical planner writes the columns of every
fragment.

**Rationale:** The logical planner has no way to write good parameters. It does not know which
operators are available, what they call their arguments, or what values the data holds, so
parameters written at that stage are guesses a later one throws away. One instruction also makes an
operator cheap to describe to a model, since a description is the only thing separating one from
another.

The column type has to be declared rather than inferred. Semantic operators return text for
everything — one asked to extract a year gives back `"1815"` — so a numeric operator downstream
fails on a type nobody chose. Inference cannot fix it, because it has to refuse the spellings a plan
most needs to accept: `y` is a boolean only when something declares the column one. The logical
planner is the component that knows what a later node will read.

A `SCAN` declaring columns only copies its entity's attribute list, and the copy is where names get
misspelled and types guessed, failing a call on a column the source never held. What a fragment
can produce depends on the operator it is bound to, which the physical planner knows and the
logical planner does not, so the physical planner writes each fragment's list, from the
declarations of the nodes it groups and from what reads its root. A scan that names no source gets
its columns there too.

**Consequences:** An operator's description carries all the weight in the physical planner's prompt,
because one plain-language instruction does not tell operators apart. The logical planner writes one type per column
and can get it wrong, which shows up as a recoverable failure and a retry rather than as a wrong
answer. There is no date type, so a date travels as an ISO 8601 string, and a comma-formatted number
such as `"142,984"` declared `int` is refused rather than repaired.

---

## ADR-009 — Operator Routing Is Separate from the Operator Registry

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Two lookups answer different questions. `blue-data-ops`'s `OperatorRegistry`, the
versioned catalog of Blue operator classes, answers which classes exist and at what version.
`operators/router.py` answers which operators, from any suite, declare a given logical operator,
holding one `PhysicalOperator` per candidate. Only the router is read while planning, and
it is the only one of the two this repository owns.

Each adapter is a function returning `list[PhysicalOperator]`; whoever assembles the planner
registers them.

**Rationale:** The two jobs pull in different directions. The registry carries versioning, pinned
ids, and discovery that planning never needs, and it is built around `blue-data-ops`'s `Operator`
base class; forcing an outside suite's operations into that base class to make them registrable
would mean a wrapper class per operation for no gain.

An adapter returns rather than registers, though the router sits beside it and registering would be
possible. Which suites a deployment offers is a decision about that deployment, and an
adapter that registered its own operators would take it; returning a list leaves it with whoever
assembles the planner, and makes an adapter's whole result assertable in one test.

**Consequences:** A Blue operator appears in both lookups, once as a class and once as a
`PhysicalOperator`, and the adapter producing the second is the only thing holding the two in
agreement. The two use different id formats: `select@2.0.0` in the registry, `blue.select` in the
router. Assembly writes a loop per suite rather than one call, and an adapter's whole result can be
asserted directly in a test.

---

## ADR-010 — An Operator's Parameter Schema Is Hand-Written

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** One Pydantic model per operator, defined in the adapter, refusing unknown keys. It is
never generated from the operator's own attribute declarations.

**Rationale:** A suite declares its attributes for its own purposes, and what a planner has to fill
is narrower. Three mismatches recur. A declaration may leave a type open, saying only that some
value is accepted, where a response schema needs a closed set of types a model can be held to. It
describes every attribute the operation takes, including the tuning knobs its author set once and a
plan should never vary, rather than the few that change per call. And it can be inconsistent about
what a caller must supply, marking an attribute required while also giving it a default, so
required-ness cannot be read off it at all. Generation carries all three through into the schema.

No suite declares its attributes for a planner's benefit, and `blue-data-ops` shows all three
mismatches.
A generated model is also opaque to pyright, so a mistake in one shows up when a call runs rather
than when the types are checked.

**Consequences:** A model can drift from the operation it describes, so a conformance test
cross-checks each one against that suite's own attribute declarations, and against the operation's
validator wherever declaration and validator disagree.

---

## ADR-011 — `execution/` Invokes Operators; `DataPlanner` Owns the Run Loop

**Date:** 2026-09-25
**Status:** Superseded by ADR-055

**Decision:** `execution/` is a directory of its own beside the modules that plan, holding
`execute_physical_operator` and `check_output_columns` and nothing else. It runs one operator its caller resolved and handed to it,
bills the call against the operator budget, and checks the result against the output columns its
caller declared. It imports only `types/`: no model client, no registry, no router, and no planning component.

The run loop lives in `DataPlanner`. It schedules, dispatches on node kind, counts attempts, chooses
the repair, installs a cover and drops what the change invalidated, and assembles the result.

**Rationale:** Invoking a resolved operator and producing a plan are different jobs with different
dependencies: writing parameters needs a model client and the router, running a call needs neither.
Keeping `execution/` free of every planning component is what makes it testable without a model and keeps the
dependency direction one-way. A separate plan runner beside `FragmentRunner` was considered and not
taken: the reason for keeping the loop out of `execution/` is satisfied either way, and one class is
simpler than two.

**Consequences:** `execution/` is two modules and names no `RunStatus`. `DataPlanner` is the only
thing that runs a plan. The types both name — plans, nodes, fragments, operators, results — live in
the package root `types/`, so `execution/` can name them without importing a planning component.

---

## ADR-012 — A Fragment Passes a List of Tables

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** The value one fragment passes to the next is a list of tables, where a table is a list
of row dicts.

```python
Record = dict[str, Any]  # one row
Data = list[Record]  # one table
DataBundle = list[Data]  # what one fragment passes to the next
```

The data an operator receives and the data it returns are both this shape, so a callable takes a
bundle and gives one back alongside its report (ADR-020). A callable is also handed the fragment's
`required_output_columns`, to read or to ignore; what the result carries is checked after the
call either way ([04](04-detailed-architecture.md#executing)). A fragment's inputs are the tables
its upstream fragments produced, concatenated in the order it lists them. Each adapter converts at its own edge: the LOTUS adapter builds one data frame per
table and flattens coming out.

**Rationale:** A row that is a plain dict belongs to no suite. It keeps pandas out of every module
except the adapter that needs it, and it makes a stored intermediate result readable. A bundle
rather than a single table is what a multi-table operation needs, since a join, a union, and an
intersect each read every table handed to them. Declaring a fragment's value to be a single table
would make a multi-output operator unrepresentable by construction rather than by decision.

An operation that writes its own query or program has to aim at a named set of columns, and the
declaration is where that set is already written. Handing it to the callable puts the names in
front of the operation that needs them, in the form the check after the call will use.

**Consequences:** Most callables ignore the declared columns, since an operation doing what its
parameters say has no use for them. Conversion happens per fragment rather than once, so
consecutive LOTUS fragments pay for a data frame they could have kept. Every reader of a fragment's
output indexes a table before a row, so the rows of a single-output fragment are `result.output[0]`.
`row_count` counts rows across every table and describes no shape; nothing reports table count.

---

## ADR-013 — A Fragment's Result Is Checked, Then Reviewed

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A result is judged in two passes. `execute_physical_operator` checks it against the
fragment root's `required_output_columns`, coercing each column to its declared type and failing the
call as a recoverable error when it cannot. A result clearing that check goes to a model review turn
returning an assessment and one of accept, reject, or replan. The review sees the fragment instruction,
the operator's description, and a sample of the result, and judges the rows against the instruction.
Which columns the result carries is settled by the check, and the review is not asked about it. Only
the root is checked, since a fragment call produces no intermediate tables.

**Rationale:** The column check is cheap, deterministic, and catches the mistakes with an obvious
right answer. It cannot catch a result that is well shaped and wrong, which is the failure that
matters most for a semantic operator. The fragment instruction is what makes a fragment call checkable:
the physical planner writes one instruction covering every node in the fragment, so a call asked to
scan, filter, and join has a single statement of what it owes, and a call handed a semantic
predicate SQL cannot express tends to return rows that ignore it. The column rule stays with the
check alone because a review that is told which columns were required reads the requirement as an
exclusive list and turns down a correct result for carrying more.

**Consequences:** One extra model call per fragment in the normal case. Writing parameters and
reviewing the result are turns in one conversation, so the review sees what was written and why. The
review is a place the system can talk itself into accepting a bad result, so its prompt and its
accept rate need measuring rather than assuming. A fragment's instruction is the only record of what
it owes, so an incomplete one leaves the review nothing to catch a bad call with.

---

## ADR-014 — One Operator-Call Budget; Model Calls Go Unmetered

**Date:** 2026-09-25
**Status:** Superseded by ADR-060

**Decision:** A request carries one budget, counting calls through `execute_physical_operator`;
reaching its cap ends the request. Every bound the run loop obeys is a field of `RunLimits`, set
once when the planner is built: plan corrections, parameter repairs, cover repairs, failure
replans, and control replans. `RunCounters` counts one request against them,
carries the `RunLimits` it counted against, and is what a result reports. A number whose only effect
on reaching it is to drop work rather than to redirect a request is not a bound of this kind, which
is where the cap on one `DISCOVER` node's search terms sits: `CatalogSearcher` holds it, as
`LogicalPlanner` holds the size of the catalog preview. An operator may bound work
it repeats inside one call, and that number belongs to the operator. Calls through `llm/` are not
separately capped.

**Rationale:** A model-call cap does not scale independently of the bounds a request already
carries. Every model call happens inside a loop one of those bounds already limits, so a further
number would restate them under a different name. The operator call is what scales on its own — a
single semantic operator can touch a source of any size regardless of how few model calls it took to
reach it — so it is the one call worth billing against a request-level cap.

**Consequences:** A caller bounds spending through operator calls and replans alone. The
conversations a run holds record one usage entry per model call, so model cost is visible after the fact
even though nothing stops it mid-run: a badly set `parameter_repairs` or `plan_corrections` can run
up a bill without ever tripping `operator_calls`. With no cost model, the operator call budget is
the only thing standing between a badly ordered plan and an unbounded bill, so its value matters
more than it otherwise would.

---

## ADR-015 — Recoverable Failures Are Statuses; Everything Else Raises

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A request that runs out of attempts, replans, or budget, that hits an unrecoverable
operator failure, or whose provider fails after retries, returns a result whose status says why,
carrying every plan, every node's outcome, and every rejected attempt. Bad parameters, a missing
required column, and an operator raising on input it cannot handle are recoverable results, so
another candidate is tried. Everything else raises: a plan object that is not a plan, a missing
configuration value, an unknown node id, and a bug in an operator's own validation.

**Rationale:** A run that spends its budget or cannot reach a source is reporting an outcome of the
work rather than a fault in the caller, and it is the outcome a caller most needs to read. An agent
can reason about a structured failure and cannot reason about a stack trace, and returning the same
shape for success and failure means one path to serialize.

The dedicated recoverable and unrecoverable exception types are scoped to failures a planner is
expected to recover from, and a crashed validator is not one. Turning a bug in this repository into
a failed node would make it read as a planning failure.

**Consequences:** `answer` is `None` rather than an empty list when a run did not finish, so a
caller who ignores the status cannot mistake a failed run for an empty answer. This sits alongside
rather than against the fail-loudly rule: malformed state and missing required values still raise. A
`BlueDataPlannerError` raised inside a run ends it as `planner_failed`, but an operator suite's own
exception propagates, so a benchmark sweep aborts on the first operator bug unless its harness
catches exceptions per query and scores that query as an error. A model client that cannot be built
as configured raises `InvalidSettingError` when it is built, so `llm_failed` is reserved for a
provider that failed, and a caller reading that status knows a retry, not a configuration change, is
what might help.

---

## ADR-017 — The Package Root Exports the Run Path

**Date:** 2026-09-25
**Status:** Superseded by ADR-056

**Decision:** `blue_data_planner/__init__.py` holds `__version__` and the run path: `DataPlanner`,
`build_data_planner`, `PlannerSettings`, `build_data_planner_from_settings`, `RunResult`,
`RunLimits`, `RunStatus`, and `BlueDataPlannerError`. Public is the root, every module directly
inside it, and the `__init__.py` of `types/`, `plan/`, `execution/`, `operators/`, `data/`, and
`llm/`. An operator adapter is the one exception: `operators/__init__.py` re-exports the router
alone, so an adapter is imported from the module named for its suite.

**Rationale:** Python runs a package's `__init__.py` before any module inside it, so whatever the
root names is paid by every import from the package. Of everything this library imports, an operator
suite is the only one heavy enough for that to matter: the plan types reach nothing outside
themselves, the model client imports its provider inside its own methods, and the data boundary's
dependency is light. So the root can carry the run path without making a plan data class expensive,
as long as no adapter sits on that path.

The alternative, a root re-exporting everything a caller could need, is the more common shape for a
Python package and reads better at a call site. It cannot be had here without putting a suite import
in front of every consumer, including one that only wanted to read a recorded plan.

**Consequences:** `from blue_data_planner import DataPlanner` works and
`from blue_data_planner.operators import blue_operators` does not, since an adapter is
`operators.adapters.blue`. Adding a name to the root is a deliberate act, and adding one that
pulls in an adapter changes what every import from the package costs. The import rule an adapter
obeys names a shape rather than a list of modules ([08-coding-style.md](08-coding-style.md)).

---

## ADR-018 — Intersection Is a `JOIN`; the Vocabulary Gains No Set Operations

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A request meaning "the rows in both" is a `JOIN` node whose instruction says so. The
logical vocabulary holds no `INTERSECT` and no set-difference operator, and the ten operators stay
as they are. The descriptions the logical planner reads carry the distinction: a `JOIN` is the rows
matching between two tables, a `UNION` is the rows from either.

**Rationale:** Only `JOIN` and `UNION` take two inputs, and the response schema holds the planner to
the ten (ADR-005), so writing a two-table merge is a choice between two named operators rather than
an open one. An inner join over matching values is what an intersection asks for, and "both" against
"either" is the whole of the discrimination.

An `INTERSECT` operator would move that discrimination rather than remove it. A planner would then
choose between `INTERSECT` and `JOIN` for every two-table merge, and the mistake worth worrying
about is unchanged: a request for a set operation reaching for `UNION` because `UNION` is the one
operator that looks like one. Either way, what settles the choice is how the two are described to the
planner, which is prompt text rather than an operator. Adding one also invalidates every operator
declaration written against the vocabulary (ADR-006), which is a lot to pay for a distinction a
clause in a description already draws.

**Consequences:** Nothing structural catches a `UNION` written where a `JOIN` was meant. Both take
two inputs, so the arity check passes, and both carry the same columns, so the output column check
passes. The fragment review is what catches it, from the instruction and a sample of the result
(ADR-013), at the cost of one operator call and a replan. The physical planner cannot prefer a set
operation over a general join, because routing is by logical operator; a capability finer than the
kind is declared in an operator's `limitations` and reported as a recoverable failure.

---

## ADR-019 — The MCP Server Runs Over stdio

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** `blue-data-planner-mcp` runs over stdio. A client launches it as a subprocess and owns
its lifetime, so the server binds no socket and has no host, port, or path. Each platform package
names a console script under `[project.scripts]`, which is the command a client is configured with.

**Rationale:** Nothing asks for a network service. The stated integration target is an agent
framework, which configures a command and an environment rather than a URL, and one planner per
client is what a subprocess gives for free. Streamable HTTP would be justified if several clients had to
share one assembled planner, and no scenario needs that.

The `blue-data` and `blue-data-kb-source` MCP servers both run over stdio, so a client that already
launches one launches this the same way.

**Consequences:** Nothing here is a long-running service, so no image and no health endpoint. There
is no listening socket to authenticate, and whoever may launch the subprocess may plan. A deployment
that later needs several clients on one planner changes the transport, which is a change to this
entry and to the two documents written against it
([05-software-stack.md](05-software-stack.md), [12-deployment-ops.md](12-deployment-ops.md)).

---

## ADR-020 — This Repository Owns a Suite of Built-in Operators

**Date:** 2026-09-25
**Status:** Superseded by ADR-058

**Decision:** A suite named `builtin`, written here rather than depended on, holding two kinds of
entry. A deterministic entry holds no model call and does what the parameters the fragment runner
wrote say. One compound entry, `builtin.nl2table`, writes a Python program from a fragment's
instruction and runs it in a child process holding copies of its tables. It is experimental, and a
deployment registers it or does not. Both kinds work through one shared module of functions over the
bundle, and that module is what the program calls.

Every deterministic entry declares one logical operator. `builtin.scan` declares the scan alone: it
reads one registered source, and a reference input's values reach that source as a bind join
([06](06-library.md#the-built-in-suite)). `builtin.nl2table` declares a vocabulary covering every
operator kind, reads registered sources, and accepts bound inputs, and it is the one operator with
no same-source constraint, so a fragment scanning two sources has a candidate.

`builtin.nl2table` reads every source its fragment names before the program starts, so the program
receives tables and is given no source, no registry, and no model client. It writes the program
again when the program will not run, bounded by an attempt count of its own, and returns a
recoverable failure once that count is spent. A deployment decides whether it is registered at all.

An operation may decide how to carry out the fragment it was given, and may repeat work inside its
own call. It may not decide what the fragment is, which source to read beyond the one the cover
bound, or how many times the fragment runs. That rule holds for every suite rather than only this
one.

An operator reports what it did through one untyped field on its result, set whether the call
succeeded or failed, so a callable returns that report alongside its data. The suite adds no runtime
dependency.

**Rationale:** A coordinator able to finish what a source refused is what makes an all-or-nothing
source interface worth having: a source may accept a predicate whole or reject it whole, because
something downstream always can. That is a permanent property of a federated architecture rather
than a wait for the operator suites to improve, which is why the suite is owned rather than
requested. Without one, a capability gap in an outside suite is answered as `no_operator` and a
replan, a fragment that scans nothing has no engine at all, and no plan runs unless a suite is
installed.

The compound entry writes a program because a program reaches what a set of named operations cannot:
a derived value, an aggregate under a condition, a column whose values have to be normalized before
they compare. The planner has to bind operations that group many nodes, plan inside themselves, and
fail as a unit, so one of our own, with an honest declaration and a working bind join, is worth
having as the case the rest is written against.

The entry repairs its program inside the call because what failed there is the program, and the
traceback names the fix. The repairs outside rewrite the fragment's parameters, which the failure
gives no reason to doubt, so a failure sent out comes back with the same program written a second
time.

What the call hands the program is what the entry's declaration rests on. The program is given
tables and no model client, so it does not judge meaning, and a predicate that needs reading text
belongs to a semantic operator. It is given no registry handle, so the tables it works over are the
ones the cover bound. It is given copies, so what it writes does not reach a source.

Each of those is a fact about the call rather than a restraint on a program written to do
otherwise. The entry is not a security boundary and holds no check pretending to be one: a check
over a language that resolves names at run time refuses the spellings it was taught and not the
others, and what rests on such a check gets written as though it held. This is why the entry is
experimental and off until a deployment asks for it.

The rule about which decision an operation may take is what separates an operation that plans inside
a fragment from one that takes over the run loop's work. Planning inside is what a compound operator
is (ADR-006); an operation that decomposes the request or picks its own sources is doing the logical
planner's job somewhere nothing can see it.

Reporting is one untyped field rather than named ones because most operations cannot report at all,
and a named field would promise what an outside suite has no way to give.

**Consequences:** Where `builtin.nl2table` is registered every operator node kind has a candidate,
so `no_operator` is rare and a node bound to the wrong operator fails at run time rather than at
planning time: a semantic predicate reaches an operator whose program can only approximate it, and
the approximation returns rows and reports no error, so review is what catches it, one operator
call later. What keeps that
from happening routinely is open question 43. A fragment containing no `SCAN` is hosted by an
operator that owns its engine, and `builtin.nl2table` is one.

The model call inside `builtin.nl2table` happens after the call is billed, unlike the parameters,
which are checked before. A program the model got wrong spends an operator call where a bad
parameter spends none, however many times the entry writes it again. `LLMError` raised inside a
callable reaches the run loop and ends the request as `llm_failed`.

The report is where a run's record of the entry lives: the program that ran, the programs that did
not and why, and what the model calls cost. Reading a run therefore means reading source text, which
is the one place the record is not structured data.

A deployment enabling the entry runs model-written code as the user who launched the planner, and
what that code may reach beyond the tables it was given is the deployment's to bound
([12](12-deployment-ops.md#11-what-is-deliberately-absent)).

The prompt the program is written from renders sample rows of every entity the fragment reads, so
what a source holds is an input to the program that runs. A deployment that registers the entry
decides who can write to the sources it reads.

`operators/` holds more than adapters, and an owned entry carries behavior to test rather than
translation ([11-testing-strategy.md](11-testing-strategy.md)). Every adapter returns a report
alongside its data, and an outside suite's is empty.

`EXPAND` stops needing its two readings told apart: a deterministic unnest and a semantic expansion
are two candidates for one node kind, and the physical planner chooses between them. Two `SCAN`
cases still have no built-in candidate: one naming a source of a kind the suite does not read, and
one naming no source, such as a scan of the model's world knowledge, which cannot be given to an
operator declaring `source_kinds`.

---

## ADR-021 — The Catalog Readings Rank and Cap, and the Entity Search Drops What Matched Nothing

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Every catalog reading that searches asks the registry to rank and takes the top
results. `preview` takes its cap from the logical planner and keeps every source it ranked, so a
registry holding any source never previews as empty. `search_records` takes a cap fixed when the
catalog is built and keeps only the entities scoring above zero, so a term that matches nothing
finds nothing. That zero-score cut is the only filter, and `data/` applies it itself; where the
registry exposes score thresholds, `data/` sets them out of the way. The logical planner is shown
each `DISCOVER` node's terms next to what they found, a node that found nothing included.

**Rationale:** A search engine's score threshold is tuned for a consumer, and the consumer here is a
model reading a prompt rather than a user reading a result list. A model weighs a weak match at the
bottom of a ranked list against the rest of the list and discounts it, so a match that is weak but
real stays. A zero score is not a weak match. It means no signal matched at all, and the records
filling the page are whichever ones the registry happens to list first, the same ones for every
term. Handed those, a planner binds a `SCAN` to an unrelated entity, and nothing in a ranked list
tells it that the entity at the top was a filler. In TAG-Bench runs, `customer` came back as three
unrelated tables at score 0.0 while the `customers` table went unfound.

The preview keeps its fillers because its failure runs the other way. Its query is the whole
request and its records are the few registered sources, so the list is short and a filler costs
little. An empty preview cannot be told apart from an empty registry, though, and a planner handed
one plans as though there were no sources. An empty discovery result can be told apart, as long as
the planner sees which node searched and for which terms: a search that found nothing is a fact the
planner can act on, by searching with other terms or by answering that part from world knowledge.

**Consequences:** A weak match still reaches a prompt, so a component has to be written to expect
irrelevant records in a ranked list rather than to trust that everything returned is relevant. The
caps are the only bound on how much catalog a prompt carries, which makes them the number to tune
when a prompt grows too large.

What a discovery finds depends on whether a term uses the words an entity's name and description
use, since a term matching none of them now finds nothing rather than a filler. The catalog searcher
therefore writes several forms of what it looks for: a noun in both the singular and the plural,
and the general thing a specific one belongs to.

---

## ADR-022 — Catalog Detail Below the Source Level Enters Only Through `DISCOVER`

**Date:** 2026-09-25
**Status:** Superseded by ADR-045

**Decision:** A `DISCOVER` node is the one way a run learns anything below the source level. The
search runs at the entity level, and a hit returns that entity together with every attribute it
holds: each attribute's name, its type, and the description enrichment wrote for it. What every
resolved node found, taken together, is the run's catalog context. The logical planner writes its
continuation against it and names, for each `SCAN`, an entity found in it or no source at all. The
physical planner reads the kind of each named source to choose among operators. Neither searches
the registry itself.

A `DISCOVER` node's inputs narrow the vocabulary the searcher writes terms in. A node returns a
bounded set of entities whatever its inputs hold, never one match per input row.

The fragment runner takes a second, deeper reading, and only of the entities its own fragment scans:
every attribute of each, with sample values, counts, range, and inferred meaning
([10-data-model.md](10-data-model.md#4-catalog-readings)).

**Rationale:** The catalog preview names sources and nothing under them, so nothing in a first pass
says which entities exist or what they hold, and a plan cannot be written against data whose shape
is unknown. Discovery is what supplies that shape, and the shape has to include the attribute list.
A planner handed the matched entity record alone writes `required_output_columns` from the entity's
description, `execute_physical_operator` fails the call on a column the result does not carry, and
the parameter repair cannot converge because every rewrite repeats the same guess. The whole list
makes a declared column name a fact.

The logical planner names the entity each `SCAN` reads, since it is the stage that reads what the
searches found, and the nodes reading the scan declare their columns from that entity's attributes.
Choosing the entity again during physical planning would let a cover move a scan to a similar
entity in a source a better operator reads, but the nodes downstream still declare the first
entity's columns, so the move rarely survives the column check, and two components choosing one
thing can disagree.

The entity is the level to search because it is the unit a `SCAN` is bound to, and because
`blue-data` records the same five levels for every source kind, so one rule covers a SQL database, a
document store, and a graph without a second rule for each.

One search per input row was weighed and refused permanently rather than deferred. A cap on it
produces a wrong answer rather than a degraded one — 500 input rows against any cap leaves most rows
unsearched, while the same cap on a vocabulary-narrowing search only weakens the prior — and the
mapping it returns would be carried by every planning prompt for the rest of the run, which is data
travelling as context. A request that genuinely needs a per-row lookup is asking for a data
operation, whose results enter the data flow and are billed and reviewed like any other (question
47).

The two readings are split by shape rather than by degree. An attribute list is broad and shallow,
so a run can carry it everywhere; a per-attribute view is narrow and deep, and its size is set by
how long the stored sample values are, so it is read for one fragment and goes no further.

**Consequences:** A request touching registered data plans at least twice, and a first plan carrying
no `DISCOVER` node is one the planner judged answerable from world knowledge alone. A first pass may
end on several terminal control nodes, since two things needing to be found do not have to be found
one pass apart, so validation admits more than one
([04](04-detailed-architecture.md#validation)).

An entity is found by its own name and description. A search cannot reach one through an attribute's
name or through a value stored in it, so a request whose vocabulary matches only an attribute needs
a term the entity itself answers to.

The attribute list travels in every planning prompt for the rest of the run, which is what keeps
`max_search_results` small and what makes the per-attribute view's sample-value cap the place a wide
entity's cost is controlled ([06-library.md](06-library.md#datacatalog)).

A path a `SCAN` names is a path a `DISCOVER` node found, so `validate_logical_plan` checks every one
against the catalog context and sends a plan naming an invented path back for correction. A scan
that named the wrong entity is answered by the replan, which writes a new `SCAN`, not by the cover
repair.

---

## ADR-023 — A Bind Join's Table Is Dropped by the Operator That Wrote It

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** The temporary-table realization of a bind join writes an ordinary table under a name
unique to the call, and the operator that wrote it drops it before its call returns, on the failing
path as much as on the succeeding one ([06-library.md](06-library.md#data)). A drop that itself
fails ends the request rather than being retried.

**Rationale:** A request must leave nothing behind in a source a caller owns. Of the three scopes
that could own the table, only the operator's still holds when a run ends by raising. Handing the
run the job would let one written table serve several calls, at the price of leaving a table behind
whenever the process dies first. Leaning on a source's own session scope costs nothing to implement
and is available only where a source kind offers it, so it cannot be the whole answer for a
boundary that reads several kinds. A per-call name settles the other half of the question by
construction: two runs writing into one source cannot collide.

**Consequences:** A written table serves exactly one call, so a fragment repeated after a rejected
review writes its values again. A failed drop is unrecoverable rather than recoverable, because a
repair would write a second table rather than remove the first.

---

## ADR-024 — A Result Is Reused by Signature

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Every node that produces a result has a signature covering what decides that result,
the signatures of the work feeding it included. A run files results by signature, and installing a
cover or a plan version compares nothing and removes nothing: a fragment reads a result when it asks
for work already done, and runs when it does not. Whether a cover asks for work already done is the
model's choice: a node whose fragment produced a result is offered that fragment among the
candidates it picks from, and choosing it carries the fragment across unchanged.

**Rationale:** Reuse is a question about the work, and a node id does not name the work. Two plan
versions may use one id for different nodes, and a repair that cuts a node set again may leave the
same id over a different set. Deciding reuse by comparing two covers means writing down what counts
as a change, which is a rule that can be incomplete without anything failing loudly: a fragment
matching on node set and operator but carrying a different instruction would keep a result the new
instruction would not have produced. Folding the inputs' signatures into a node's own replaces the
separate walk that invalidated everything downstream of a change.

The choice and the signature answer different questions, and reuse needs both. A fragment is written
by a model, so an instruction reworded without being changed in substance signs differently and
would throw a good result away; and a fragment kept above work that has since changed would keep a
result that no longer stands. Letting the model say what should happen at a node and the signature
say whether what it chose still stands settles both without narrowing what a signature covers.

**Consequences:** Results accumulate and are never deleted; an entry no later cover asks for is
unreachable rather than removed. What a signature covers is read off the fragment rather than listed,
so a field added later is covered without anyone remembering to add it, and the cost of getting that
wrong is a stale result rather than an error. A signature is derived, so it is the one value in the
data model that is not assigned. A kept fragment keeps its id, so the exchange that wrote its
parameters stays attached to the work those parameters were written for.

---

## ADR-025 — One Conversation per Component, Continued Through Repairs

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** Each component talks to the model in a conversation the run owns: one per request for
the logical planner, one per logical plan for the physical planner, one per fragment for the fragment
runner, and one per `DISCOVER` node for the catalog searcher. A repair continues the conversation it
repairs. Fixed instructions open a conversation and everything varying by request or by turn is
asked in a later turn, and no turn already written is rewritten.

**Rationale:** A model asked to fix something does better answering what it wrote than being told
about it from outside, and a conversation that only grows keeps its unchanged prefix cacheable, which
is most of what a repair costs. Putting the request and the run's state into the opening instructions
instead would change the prefix on every pass and give up both.

**Consequences:** The conversations are where a run's discarded work is read, so no second record of
rejected plans or rejected parameters is kept beside them, and no component reports what its own
calls cost. Conversations grow with the repairs a request makes, which the repair bounds already
limit. A binding decision names its candidate by operator and node set rather than by a position in
a list, because the list differs from turn to turn.

---

## ADR-026 — One Cover Repair Answers Every Physical Failure

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A fragment whose parameter repairs ran out sends the run to the cover repair, which
covers the whole logical plan again. That one repair carries every fix physical planning has: binding
a different operator, cutting a node set into smaller fragments, and growing a fragment across nodes
that already ran. `PhysicalPlanner` has one entry point
for writing a cover, and `cover_repairs` is the only bound between the parameters and a new plan.

**Rationale:** A repair confined to the failed node set looks cheaper and is not. Results are filed
by signature and a cover keeps every fragment the model chooses to keep (ADR-024), so covering a
whole plan again re-runs only what the model changed — the same work a confined repair would re-run,
reached by the same decision. Confining it therefore buys nothing back, while costing the one fix
that needs the whole plan in view. Its bound would also have to be counted against a node set that
the repair is itself allowed to split, which is a scope no count holds still.

**Consequences:** A repair decides every binding again, so it spends one model call per fragment
rather than one in total; model calls are not budgeted (ADR-014) and the conversation's prefix is
unchanged, so what grows is latency rather than a bound. A logical plan gets `cover_repairs` covers
after its first, and a request those cannot fix replans.

---

## ADR-027 — The CLI Follows the Blue CLI Family's Command Shape

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** The command is `blue-planner`, and every invocation reads
`<cli> <component> <action> <parameters>`. Components are `data` and `profile`; actions come from the
family's verb list; `--profile` and `--format` carry the meanings they carry in the other Blue CLIs.
Running a request is `data query`, and `--explain` writes the plan for that request without running
it. The one deviation is that `nl` is absent from `--format` (ADR-029).

**Rationale:** The planner is one component of the Data Intelligence Layer, and a user reaching it
from `blue-registry` or `blue-kb` should not have to learn a second grammar for the same kind of
work. `data query` is also the operation those CLIs already name that way: a question in plain
language, answered from registered data. A single verb such as `ask` would be shorter and would have
no place to put the profile actions, which every Blue CLI needs and which are defined only inside
this grammar. Planning a request is a mode of answering it rather than a component of its own: the
two take the same request and the same options and differ in how far they go, which is what
`EXPLAIN` names in every database this planner reads.

**Consequences:** A component is a thing a user acts on, so a new capability arrives as an action on
one, or as a new component, while a mode of an existing capability arrives as a flag. Deviations
from the family are recorded, since a reader is entitled to assume the family's meaning everywhere
it is not. The MCP server has one tool where the CLI has one command, and `--explain` becomes an
argument of it, because a tool's arguments are its flags.

---

## ADR-028 — CLI Configuration Lives in a Profile

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** `blue-planner` resolves every setting as command option, then environment variable,
then the selected profile, then the default. Profiles live in `~/.blue-planner/.profiles`, over the
configuration engine `blue-data` publishes, and `BLUE_DATA_PLANNER_HOME` relocates the directory. A
secret is stored encoded or as an environment reference, never accepted as a command-line value, and
masked wherever the CLI prints it. The CLI loads no `.env` file.

**Rationale:** A profile is what lets one machine hold a demonstration setup and a production one
without a shell full of exports, and the engine that implements it already exists in `blue-data`, so
every Blue CLI resolves settings and stores secrets by the same rules.

**Consequences:** The CLI package depends on `blue-data` directly, which the library reaches only
through `data/`. A bound a run reports back is a profile parameter rather than a flag, so a request
that needs a different budget selects a different profile. A demonstration configures itself with
`profile config` rather than with a `.env` file in the checkout. The MCP server reads no
configuration file: a file read from disk would take precedence over the `env` block a client
launching the server passes deliberately, and a server started as a subprocess has a working
directory it did not choose.

---

## ADR-029 — The CLI Prints Rows, and Offers No Prose Format

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** `--format` accepts `table` and `json`. The family's third name, `nl`, is not offered.

**Rationale:** The family offers `nl` where a source answers in prose. A planner's answer is the
`RETURN` node's rows, and turning rows into a sentence is a model call nothing in the library makes,
so offering the format would mean either an interface that plans behavior of its own (ADR-002) or a
format that prints something other than what it promises. Naming a format the command cannot honor
is worse than leaving it out.

**Consequences:** A caller wanting a sentence writes one from the rows. Whether the library should
be able to phrase an answer is open question 54, and the format arrives with it if it ever does.

---

## ADR-030 — Three Exit Codes, and an Error Code for What They Do Not Separate

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** The CLI exits 0 when the command succeeded, 2 for a usage error, and 1 for everything
else, including a run that did not finish and a configuration failure. What kind of failure it was
is the `<CODE>` in the `ERROR: <CODE> <description>` line on standard error.

**Rationale:** The other Blue CLIs use these three, and a script written against one of them should
read the same number from all of them. A fourth code for configuration would separate one failure
class in this repository alone, while the error codes separate every class in every repository at
once, which is the separation a caller can actually rely on.

**Consequences:** A caller distinguishing a configuration failure from an exhausted budget reads the error
code or the JSON payload rather than `$?`. A run that did not finish still prints its result under
`--format json`, so the status is machine-readable without parsing the error line.

---

## ADR-031 — The Benchmark Is a Script, Not a Test Tier

**Date:** 2026-09-25
**Status:** Superseded by ADR-052

**Decision:** The TAG-Bench harness lives at `lib/scripts/tag_bench/benchmark/`, beside the three
scripts that build its inputs, and runs as a module. `pytest` runs no part of it; the harness's own
unit tests live at `tests/unit/scripts/tag_bench/benchmark/`, which is where the mirror puts them.

**Rationale:** A run is an experiment rather than a check. It spends paid model calls for as long as
its selection takes, so a person starts it, reads the lines it prints while it works, and stops it
when something looks wrong. What it produces is a number and a per-query record to triage, and a
stopped run has to resume where it left off. Selecting queries, naming a run, resuming one, and
aggregating records written earlier would each arrive as a `pytest` option, and the assertion at the
end is the least useful thing in the output.

**Consequences:** No CI job can gate on the accuracy target; the number is read by whoever ran it,
and the pipeline runs no benchmark ([12-deployment-ops.md](12-deployment-ops.md)). The harness
assembles a planner through the library's public surface, the way the CLI does, so a benchmark run
and a CLI run answer the same request the same way. `lib/tests/` holds no benchmark directory, and
the opt-in tier that remains there is `end_to_end/`, one real request through a provider.

---

## ADR-032 — Format and Detail Are Separate Options

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** `--format` decides how the CLI writes a result, and `--log` decides whether the run
behind it is reported. Without `--log`, both formats write the result alone — the answer, or the
plan where `--explain` asked for one — and `--format json` writes it as `RunResult.to_dict`, which
carries the run's status, failure reason, how each fragment ended, counters, and model spend with
it. With `--log`, the run is reported cover by cover: on standard error as one table per cover under
`--format table`, and under `--format json` as the plan versions added to the document standard
output already carries. A fragment's rows leave the process as a `TableSummary` either way
([10-data-model.md](10-data-model.md#5-shapes-that-leave-the-process)).

**Rationale:** The log is there so a person can judge whether to believe the answer. A planner's
answer is the end of a chain of model decisions, and a row count does not say whether that chain was
sound, while the source that was read, the instruction each step carried, and the shape that came
back do. How much of a run is reported is a different question from how a result is written, which
is what `--format` answers, and tying the two together makes a caller who wants a machine-readable
answer take every plan version with it.

A summary answers that question at a size both a screen and a context window hold. A fragment's
result is as large as the data that fragment read, while the answer is already on standard output,
so carrying those rows a second time adds nothing the summary has not said.

A JSON document says all of this in structured fields rather than in the lines a table is drawn
from. A reader parsing indented English to find out how a fragment ended has a worse path to it
than the person reading the table, which is backwards, so the document carries the records and the
table is the one place they are rendered.

**Consequences:** `--log` and `--log-limit` are the family's own names for the option and its cap,
so `BLUE_DATA_CLI_LOG_LIMIT` is what the cap resolves from and this CLI declares no parameter of its
own for it. The rows a fragment's summary shows are a fixed sample rather than an option, since a
sample shows the shape of a result and nobody tunes five. `RunResult` reaches a caller in full only
through the library, so a consumer needing every intermediate table embeds it rather than parsing
CLI output. Open question 56 asks whether the CLI should be able to write the whole record to a
file.

---

## ADR-033 — A Fragment's Ending Is Filed Whether Its Result Was Kept or Not

**Date:** 2026-09-25
**Status:** Superseded by ADR-047

**Decision:** `results` maps a signature to the `FragmentResult` the run came to under it, rejections
included, and a `FragmentResult` carries the last call's result whatever the ending. What makes a
fragment done is an accepted entry rather than an entry, so a rejected one is offered again and its
rows never reach the next fragment.

**Rationale:** A run's record has to say which step of a cover sent the request to a repair. Filing
only what a review accepted leaves a rejected fragment, one the pass never reached, and every
fragment of a planning-only run reading the same way, which is a record that cannot be triaged: a
reader sees that a cover produced nothing and not where it stopped or what it was answered with. A
failed result is a result, so it belongs in the map the run already keys by signature rather than in
a second one beside it, which would ask every consumer to read two places and keep them agreeing.
What the two need to be told apart by is the status the ending already carries.

**Consequences:** `results` is typed by `FragmentResult` rather than by what one operator call
returned, so a consumer reads `.status` before `.result`. The three reads that decide scheduling go
through one accessor that answers nothing for an entry the review turned down, which is what keeps a
cover repair asking for the same work from resuming on top of it. The record grows by one entry per
fragment rather than by one per call, so it stays bounded by the cover's size. The review's accept
rate becomes countable off a returned result ([11-testing-strategy.md](11-testing-strategy.md#8-the-benchmark)); how
often a parameter repair fires does not, since that loop runs inside one fragment run and only its
ending is filed.

---

## ADR-034 — An Ending the Rows Cannot Answer Is a Replan

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** The columns holding the answer are named by the logical planner and by nothing else.
When the rows a finished run holds do not carry one of them, the run returns to the logical planner
with the reason, and the ending is written again against the rows the run now holds. The prompt that
writes an ending asks for the columns holding the answer and nothing else, and the planner is shown
each carried node's columns and the first of its rows.

**Rationale:** Two things go wrong with an ending, and neither is a defect. A column is named by the
name it had when the plan was written, which a join is free to change once it collides, and an
ending may read a node a fragment grouped, which produced no table of its own. Both leave a run
that did every piece of work correctly and cannot state its answer, so ending the request throws all
of it away. The alternative that reads the rows and picks the columns at the end would answer the
same two cases, at the price of a model call on every request that already got its ending right,
and of moving a decision out of the one component that holds the request in view.

**Consequences:** The answer step raises `ReturnAnswerError`, which the run loop turns into a
replan, bounded by `failure_replans` like any other, so a planner that keeps naming the same wrong
column ends the request as `replan_exhausted`. A replan that only rewrites the ending asks for no
work, which is what ADR-035 answers. Nothing downstream of the logical planner narrows an answer, so
a plan whose ending names a supporting column returns that column: what the prompt asks for is what
a caller receives.

---

## ADR-035 — A Cover of Work Already Done Is Carried Over, Unasked

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A logical plan whose operator nodes all have results keeps the cover the run already
has, under the new plan's id. No binding is offered to a model and no candidate is enumerated.

**Rationale:** Reusing one finished fragment is a decision the model takes among candidates
(ADR-026), because a repair may want that node run differently. A plan with nothing left to run
offers no such choice: every candidate is a node whose rows the run already holds, and the only
answer that does not throw work away is to keep all of them. Asking anyway costs one model call per
fragment to be told what the results already say, on the path a replanned ending takes every time.

**Consequences:** A continuation that only rewrites the ending costs one model call, the one that
wrote it. The cover is carried over rather than rebuilt, so the run's record shows a new cover whose
fragments are the previous cover's, and the signatures under which results sit are unchanged
(ADR-024). Where a plan still holds work, every binding is decided afresh as before.

---

## ADR-036 — The MCP Server Reports a Failure in Its Payload

**Date:** 2026-09-25
**Status:** Superseded by ADR-044

**Decision:** A run that ended on a failure status, and an operator that failed inside one, are
successful tool calls whose payload carries the status. Anything raised becomes
`{"error": "<message>"}`, also a successful tool call.

**Rationale:** All four Blue Data MCP servers do this, and a client loading several of them reads
one shape.

**Consequences:** Every raised failure carries the same one-key payload, so a client tells one
configuration failure from another only by reading the message. Whether to carry the CLI's error
codes in the payload is open question 57.

---

## ADR-037 — The Values a Planner Is Built From Are a Library Type

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** `PlannerSettings` holds them with their defaults and their validation; each interface
resolves its own way and hands one over.

**Rationale:** The CLI and the MCP server need the same eleven values, and `blue-data` already puts
shared builders in its library.

**Consequences:** The defaults are written once. `profile_spec.json` keeps copies because
`ConfigResolver` resolves a spec default as its own tier, so without one `profile inspect` reports
those parameters as unset. A test guards the two against drift.

---

## ADR-038 — The MCP Surface Is Tools-Only, and a Tool Name Borrows the CLI's Verb

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** No Blue Data server exposes MCP resources. Where a tool and a CLI command name the
same operation, the tool takes the command's verb and its component as the noun.

**Rationale:** A client loading several Blue Data servers sees one naming convention, and a reader
moving between a CLI and a tool sees one vocabulary.

**Consequences:** A new tool's name is read off the CLI command naming the same operation, and MCP
resources are not added without revisiting this.

---

## ADR-039 — A `REPLAN` Reads Nothing, and Waits for Work the Same Pass Wrote

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A `REPLAN` node lists no inputs. It is ready once no fragment is, and what it hands
the planner is what the pass's fragments produced. A plan holding one has to write at least one step
it did not carry over, which validation enforces. A plan ending at control nodes is exempt from the
rule that every node's rows are read by another node.

**Rationale:** Every other edge in a plan is dataflow, and a `REPLAN` reads no rows, so an edge into
one would be the single edge meaning something else. It would also take a decision away from the
physical planner: a node listed by a control node has a consumer outside every fragment and can only
be a fragment root, which forbids the grouping physical planning exists to choose. Nothing is lost
by dropping the edges, because every fragment root holding a result is carried into the next version
and shown to the planner with its columns and a sample of its rows.

A pass writing nothing runs nothing, so the next version is written from the same prompt, and each
turn of the loop changes only the sentence the previous `REPLAN` wrote about why it stopped. The
run then spends its control replans without making an operator call. Refusing the plan answers this
where a bound cannot: a bound sets how many times a run may arrive back where
it started, and the validation error tells the planner what to write instead while the correction
costs neither a replan nor an operator call.

**Consequences:** The response schema carries one node model per control kind, and the one for
`REPLAN` has no field a node id could go in, so the rule holds wherever a provider enforces a schema
and needs no check of its own. A pass that cannot write a step writes a `DISCOVER` node or the whole
plan from world knowledge; one that can do neither ends the request as `invalid_plan` with the
objection as its reason, rather than as `replan_exhausted` several passes later. What already ran is
the one place values from the run reach a continuation, which is what a carried node's sample rows
are for ([04](04-detailed-architecture.md#writing-a-continuation)).

---

## ADR-040 — A Binding May Turn Down Every Candidate

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** The schema one binding decision is written into carries one choice beyond the
candidates and the reuse candidate: taking none of them, with a reason. Choosing it ends the cover,
and the run goes back to the logical planner with that reason. It is the answer where every
candidate would return rows that ignore part of what the plan states; where one candidate does part
of the work correctly, that smaller candidate is the answer and the rest becomes another fragment.

**Rationale:** An operator declares the node kinds it groups and the source kinds it reads, and says
nothing about what it can do with the values inside those nodes. A node whose condition needs text
read for meaning is therefore offered candidates that group it happily and cannot carry it. With no
way to say so, a binding takes one of them and does one of two things. It writes an instruction
loose enough to fit, and the result is accepted, because the instruction is the whole of what the
review holds the rows against — a wrong answer returned as `ok`. Or it writes an honest instruction
and spends an operator call learning what the declaration could not state. The logical planner is
the component that can answer a gap of this kind, by writing steps the registered operators do
cover, and the reason is what it writes against.

**Consequences:** A cover with no plan has two causes: a node with no candidate at all, which the
router decides in code, and a binding turning every candidate down, which a model decides. Both
return to the logical planner, and neither spends an operator call. A refusal does spend a replan,
so one written where a smaller candidate would have done part of the work turns a recoverable
request into `replan_exhausted`. The examples in the physical planner's prompt are what separate the
two cases, and the status mix of a benchmark run is what measures whether they do.

---

## ADR-041 — The Built-In Filter Searches Strings by Regular Expression

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** A comparison in `builtin.filter`'s predicate can be a `re.search` over a string
column, alongside the value, membership, and null comparisons.

**Rationale:** A string's shape — a prefix, a first token, a code format — is a condition on the
stored value, not on what it means. A predicate with no way to state it leaves the planner two
choices: send the condition to a semantic operator, which costs a model call per row and a row
cap, or loosen it into one the predicate can express and return a wrong answer. A
general-purpose pattern language covers the whole family of string-shape conditions in one operator
name, where a growing list of narrower string operators (starts-with, ends-with, matches-format)
has no natural end. Extending the predicate also keeps the built-in suite small, which
matters because a third-party suite is meant to plug in beside it rather than duplicate it.

**Consequences:** The pattern is written by the fragment runner and matched deterministically, with
no model call. A pattern that does not compile is a recoverable failure, so the parameter repair
can rewrite it.

---

## ADR-042 — Planning Runs the Run Loop Up to the First Operator Call

**Date:** 2026-09-25
**Status:** Accepted

**Decision:** `plan` runs the loop `run` runs and ends as `planned` at the first point that loop
would call an operator: a ready fragment, or a plan with nothing left to run. Control nodes, the
catalog searches they ask for, continuations, and the replan a plan no operator can cover all run
before that point.

**Rationale:** A request over registered data is planned at least twice, because its first plan
asks the catalog what exists, so a planning-only operation that stops after the first plan shows
the question rather than the plan that answers it. A search reads the catalog and spends no
operator call, so nothing about the promise `--explain` makes requires stopping before it. Two
stopping rules fit that promise. Skipping every fragment while resolving every control node would
show more for a plan mixing both, but the plans it writes are ones `run` would never write, since a
run's continuation sees the rows its fragments produced. Stopping at the first operator call shows
exactly what `run` would start executing, and it reuses the loop instead of keeping a second path
beside it.

**Consequences:** `plan` and `run` share every step before execution, so a change to either
reaches both. `plan` spends model calls and `control_replans` on the way and can end as
`replan_exhausted`. A `DISCOVER` node waiting on a fragment's rows is shown unresolved. `planned`
always names a matching plan and cover and never carries a failure reason.

---

## ADR-043 — An Unexpected Exception Is One Error Line; `--log` Adds Its Traceback

**Date:** 2026-09-27
**Status:** Accepted

**Decision:** The CLI reports any exception no error code covers as
`ERROR: INTERNAL_ERROR <type>: <message>` on standard error and exits 1. `data query --log` prints
the traceback after that line. The catch sits in `main`, for every command, and in `data query`,
the one command that carries `--log`.

**Rationale:** The Blue CLI checklist asks that an unexpected exception not show a stack trace
unless debugging or logging is asked for, and both sibling CLIs catch broadly at their commands:
`blue-registry` reports `COMMAND_FAILED`, and `blue-kb` wraps the exception in a `ClickException`.
One error line keeps a script reading standard error on the family's format, and `--log` is the
switch a user already turns on to see what a run did, so it is where the traceback belongs.

**Consequences:** The CLI is the fourth place `design/08` permits `except Exception`, beside the
source read, the provider call, and the MCP tool handler. A defect in a command other than
`data query` shows its type and message only; reproducing it with a traceback means calling the
library directly.

---

## ADR-044 — An Error Is a Document Carrying Its Code, in Both Interfaces

**Date:** 2026-09-27
**Status:** Accepted

**Decision:** Under `--format json`, a CLI command that takes `--format` writes an error as
`{"error": "<description>", "code": "<CODE>"}` on standard output, and still writes the `ERROR:
<CODE> <description>` line on standard error. Where the error came before the format was resolved,
or is that it cannot be, `--format` and then `BLUE_DATA_CLI_FORMAT` in the environment decide the
format, and neither saying `json` means the plain line alone. A usage error stays Click's usage
message and exit code 2. The MCP server's payload for anything raised is the same document, with
`REGISTRY_UNREADABLE`, `CONFIG_INVALID`, or `INTERNAL_ERROR` as its code. A run that ended on a
failure status keeps its run document in both interfaces.

**Rationale:** A caller that asked for JSON parses standard output, and a failed run already gives
it a document there, so every other failure should as well; standard error cannot carry one, since
the closing summary shares it. The error line stays because it is the family's format for a person
reading the terminal and for a script reading standard error (ADR-030). The shape is the family's
MCP payload with one key added: a client reading `error` as a string reads it unchanged, and a
client that wants to act on why a call failed reads `code` rather than the message text. One shape
and one set of codes keep the CLI and the MCP server from drifting. A usage error is left out
because the option that would select the format may be what failed to parse, and it is fixed when a
script is written rather than handled when one runs.

**Consequences:** Under `--format json`, standard output holds either a run document, carrying
`status`, or an error document, carrying `error`; a failure exits 1 with either. A format set only
in a profile does not reach an error raised before the profile was read. `profile add`, `remove`,
`config`, and `select` take no `--format`, so their errors stay plain. The MCP server maps the
library's exceptions to the CLI's codes itself, since it imports nothing from the CLI.

---

## ADR-045 — Catalog Detail Below the Source Level Enters Through `DISCOVER`, and the Deep Reading Follows the Scans

**Date:** 2026-09-26
**Status:** Superseded by ADR-049

**Decision:** A `DISCOVER` node is the one way a run learns anything below the source level. The
search runs at the entity level, and a hit returns that entity together with every attribute it
holds: each attribute's name, its type, and the description enrichment wrote for it. What every
resolved node found, taken together, is the run's catalog context. The logical planner writes its
continuation against it and names, for each `SCAN`, an entity found in it or no source at all. The
physical planner reads the kind of each named source to choose among operators. Neither searches
the registry itself.

A `DISCOVER` node's inputs narrow the vocabulary the searcher writes terms in. A node returns a
bounded set of entities whatever its inputs hold, never one match per input row.

The deep reading, `AttributeView`, is taken of the entities a component's own work scans: the
fragment runner for its fragment, the physical planner for its plan. It covers every attribute of
each, with sample values, counts, range, and inferred meaning
([10-data-model.md](10-data-model.md#4-catalog-readings)).

**Rationale:** The catalog preview names sources and nothing under them, so nothing in a first pass
says which entities exist or what they hold, and a plan cannot be written against data whose shape
is unknown. Discovery is what supplies that shape, and the shape has to include the attribute list.
A planner handed the matched entity record alone writes `required_output_columns` from the entity's
description, `execute_physical_operator` fails the call on a column the result does not carry, and
the parameter repair cannot converge because every rewrite repeats the same guess. The whole list
makes a declared column name a fact.

The logical planner names the entity each `SCAN` reads, since it is the stage that reads what the
searches found, and the nodes reading the scan declare their columns from that entity's attributes.
Choosing the entity again during physical planning would let a cover move a scan to a similar
entity in a source a better operator reads, but the nodes downstream still declare the first
entity's columns, so the move rarely survives the column check, and two components choosing one
thing can disagree.

The entity is the level to search because it is the unit a `SCAN` is bound to, and because
`blue-data` records the same five levels for every source kind, so one rule covers a SQL database, a
document store, and a graph without a second rule for each.

One search per input row was weighed and refused permanently rather than deferred. A cap on it
produces a wrong answer rather than a degraded one — 500 input rows against any cap leaves most rows
unsearched, while the same cap on a vocabulary-narrowing search only weakens the prior — and the
mapping it returns would be carried by every planning prompt for the rest of the run, which is data
travelling as context. A request that genuinely needs a per-row lookup is asking for a data
operation, whose results enter the data flow and are billed and reviewed like any other (question
47).

The two readings are split by shape rather than by degree. An attribute list is broad and shallow,
so a run can carry it everywhere; a per-attribute view is narrow and deep, and its size is set by
how long the stored sample values are, so it is read only where scans bound it.

The operator choice depends on what a column holds as much as the parameters do. A condition on a
stored number, flag, or category can go to an operator that compares stored values, and the same
condition on free text needs one that reads meaning. An attribute's type and description do not
always settle which of the two a column is, and its sample values do. The physical planner makes
that choice, so it reads the view for the same reason the fragment runner does, bounded the same
way: by the scans in its own work.

**Consequences:** A request touching registered data plans at least twice, and a first plan carrying
no `DISCOVER` node is one the planner judged answerable from world knowledge alone. A first pass may
end on several terminal control nodes, since two things needing to be found do not have to be found
one pass apart, so validation admits more than one
([04](04-detailed-architecture.md#validation)).

An entity is found by its own name and description. A search cannot reach one through an attribute's
name or through a value stored in it, so a request whose vocabulary matches only an attribute needs
a term the entity itself answers to.

The attribute list travels in every planning prompt for the rest of the run, which is what keeps
`max_search_results` small and what makes the per-attribute view's sample-value cap the place a wide
entity's cost is controlled ([06-library.md](06-library.md#datacatalog)).

A path a `SCAN` names is a path a `DISCOVER` node found, so `validate_logical_plan` checks every one
against the catalog context and sends a plan naming an invented path back for correction. A scan
that named the wrong entity is answered by the replan, which writes a new `SCAN`, not by the cover
repair.

The physical planner is built with the catalog and makes one `attribute_views` call per distinct
entity its plan scans, once per logical plan. It lists no entity its plan does not scan.

---

## ADR-046 — Each Cover Turn Shows the Run's State; a Repair Shows the Fragment That Failed

**Date:** 2026-09-26
**Status:** Superseded by ADR-048

**Decision:** Every turn that starts a cover, the opening turn of a physical planner's conversation
and each cover repair turn, shows the results the run holds at that moment: each one's row count,
operator, columns, and first rows, with a count of the rows left out. A cover repair turn also shows
the fragment that failed, its failure reason, and the rows its last call returned, marked as
rejected because nothing can read or reuse them. The first cover of a new logical plan is shown
nothing about how the previous plan ended. `PhysicalPlanner.cover` takes the failed fragment as a
`FailedFragment`, and the reason a plan is written again, the replan reason, goes to the logical
planner alone.

**Rationale:** A repair that cannot see what just ran cannot weigh reusing it against regrouping
it. Fragments run between the opening turn and the repair, and their row counts are the cost of
grouping them into a wider fragment. A failure reason is also easier to act on beside the rows it
describes: a reason saying a result ignored a condition is checkable against the rejected rows,
and without them the model has to take the reason on trust.

The previous plan's ending is the logical planner's input, and the plan in front of the physical
planner is already what the logical planner made of it. Showing that ending again adds nothing the
plan does not state and can contradict it.

Two alternatives were weighed. Showing only what changed since the last turn would keep each turn
smaller, but it needs the planner to keep state between calls, and the planner keeps none; a
repeated entry costs a few rows in an appended turn and leaves the cached prefix intact. Passing the
previous plan's ending to the physical planner under a corrected heading would keep one argument
for both kinds of reason, but that ending is not always a failure: a resolved `DISCOVER` ends a pass
too, and a physical planner told about it has nothing to repair.

**Consequences:** Each repair turn repeats the held results it shows, so a conversation grows by a
few rows per result per repair, which `cover_repairs` bounds. `DataPlanner` carries the failed
fragment from the ending of the fragment loop to the cover it asks for, and a failure reason lives
in that fragment's `FragmentResult` rather than beside it. A `FailedFragment` whose result carries
no failure reason is a defect and raises.

---

## ADR-047 — A Fragment's Ending Is Filed With Every Parameter Attempt

**Date:** 2026-09-28
**Status:** Accepted

**Decision:** `results` maps a signature to the `FragmentResult` the run came to under it, rejections
included. What makes a fragment done is an accepted entry rather than an entry, so a rejected one is
offered again and its rows never reach the next fragment. A `FragmentResult` carries every parameter
attempt the fragment made, in order, as a `ParameterAttempt`: the parameters, what the call returned,
and what the review said. `FragmentResult.result` is the last call that ran, read off the attempts.

A fragment's failure reason names the logical nodes it covered and the instruction they were
executed as, then the review's assessment where one exists. It names no operator and quotes no
operator error, because the logical planner reads it too, as the replan reason, once the cover
repairs run out.

**Rationale:** A run's record has to say which step of a cover sent the request to a repair, and a
failed result is a result, so it belongs in the map the run already keys by signature; the status
the ending carries is what tells a kept result from a rejected one.

The cover repair has to see more than the last attempt. It runs in the physical planner's
conversation, which holds none of the fragment runner's turns, and the attempt that says what to
change is often not the last one: a value that does not fit the type the cover declared for a column
cannot be fixed by any parameter, so the runner tries others and ends on an unrelated error. In the
TAG-Bench triage that motivated this, the first attempt's error named the mismatch and the last one
named a missing input, and the cover repair saw only the second. Carrying the attempts on the result,
rather than a rendered history in the failure reason, keeps each attempt's rows beside the attempt
that returned them, and keeps the attempts inspectable as data in the run record.

The failure reason quotes the fragment's instruction as the one the nodes were executed as because
the physical planner rewrote it: a logical planner reading its own node ids beside a different
instruction would otherwise take it for its own.

**Consequences:** `results` is typed by `FragmentResult` rather than by what one operator call
returned, so a consumer reads `.status` before `.result`. The three reads that decide scheduling go
through one accessor that answers nothing for an entry the review turned down. The record grows by
one entry per fragment and one attempt per parameter repair, so it stays bounded by the cover's size
and `parameter_repairs`. Both the review's accept rate and how often a parameter repair fires are
countable off a returned result ([11-testing-strategy.md](11-testing-strategy.md#8-the-benchmark)).

---

## ADR-048 — Each Cover Turn Shows the Run's State; a Repair Shows Each Attempt of the Fragment That Failed

**Date:** 2026-09-28
**Status:** Accepted

**Decision:** Every turn that starts a cover, the opening turn of a physical planner's conversation
and each cover repair turn, shows the results the run holds at that moment: each one's row count,
operator, columns, and first rows, with a count of the rows left out. A cover repair turn also shows
the fragment that failed, its failure reason, and each of its parameter attempts in order: the
parameters, how the attempt ended, and the first rows it returned under it, marked as unreadable
because nothing can read or reuse them. Every attempt is described as failed, in words that assume
no knowledge of the fragment runner's review. The first cover of a new logical plan is shown nothing
about how the previous plan ended. `PhysicalPlanner.cover` takes the failed fragment as a
`FailedFragment`, and the replan reason goes to the logical planner alone.

**Rationale:** A repair that cannot see what just ran cannot weigh reusing it against regrouping it,
and the row counts of fragments run since the opening turn are the cost of grouping them into a
wider fragment. A failure is easier to act on beside the parameters and rows that produced it: an
error is checkable against the parameters that caused it, and a result that ignored a condition
against its rows. The physical planner never took part in a review, so *rejected* or *accepted*
could be read as a label in the data rather than as the review's decision.

The previous plan's ending is the logical planner's input, and the plan in front of the physical
planner is already what the logical planner made of it; showing that ending again adds nothing the
plan does not state and can contradict it.

**Consequences:** Each repair turn repeats the held results it shows and adds one short block per
attempt, with at most a few rows each, which `cover_repairs` and `parameter_repairs` bound.
`DataPlanner` carries the failed fragment from the ending of the fragment loop to the cover it asks
for, and a failure reason lives in that fragment's `FragmentResult` rather than beside it. A
`FailedFragment` whose result carries no failure reason is a defect and raises.

---

## ADR-049 — Every Attribute a Planning Prompt Lists Carries Its Values, and a Conversation Shows an Entity Once

**Date:** 2026-09-28
**Status:** Accepted

**Decision:** A `DISCOVER` node is the one way a run learns anything below the source level. The
search runs at the entity level, and a hit returns that entity, its recorded row count, and a view
of every attribute it holds. What every resolved node found, taken together, is the run's catalog
context. The logical planner writes its continuation against it and names, for each `SCAN`, an
entity found in it or no source at all. Neither planner searches the registry itself. A `DISCOVER`
node's inputs narrow the vocabulary the searcher writes terms in, and a node returns a bounded set
of entities whatever its inputs hold, never one match per input row.

There is one reading of an attribute, `AttributeView`, and every component that lists an attribute
shows it: the logical planner for every entity the catalog context holds, the physical planner for
every entity its plan scans, and the fragment runner for every entity its fragment scans. A view
carries the attribute's name, type, and description; its most frequent values, each with the number
of rows holding it; the number of rows that are null and the number holding the empty string; and
the number of rows those counts were taken over. Where the catalog records no frequent values, the
view carries its sample values instead.

What varies between components is how many values a prompt shows: three for the logical planner,
five for the physical planner and the fragment runner. Every value is cut at a fixed length. Every
list of values says how many distinct values it leaves out, and every count is shown against the
rows it was taken over, so nothing shown reads as the whole column.

The logical planner is shown each search's result once, in the turn right after its `DISCOVER`
node resolved. That turn shows every entity the search found in full, whether or not an earlier
search found the same entity, and a search that found nothing is shown as having found nothing.
Later turns do not repeat it.

A value a prompt shows is the spelling a condition is written in. Where the words of the request or
of an instruction spell a value differently from the data, the component writing the value writes
the data's spelling: the physical planner in a fragment's instruction, the fragment runner in its
parameters, reading attribute views and input rows alike.

**Rationale:** The logical planner decides which entities are scanned and joined, and which
attribute a condition reads. No later stage can change that: the physical planner binds nodes and
adds none. An attribute whose name and description fit the request can be nearly empty. In
`codebase_community`, `posts.OwnerDisplayName` is null in 1842 of the 2000 rows its statistics were
taken over; the owner is reached through `OwnerUserId` and a join to `users`. Only the attribute's
values show that, so the component writing the join has to see them.

Two readings, one shallow and one deep, were justified by size: a view's size was set by how long
the stored values are. A fixed cut on each value's length and a fixed number of values bound that
size instead, and with the cause gone one reading is simpler than two. The bound is modest: every
attribute of every entity in `codebase_community`, with three values each, is about three thousand
tokens, of which the values are about a fifth, and a discovery node returns only the few entities
its search ranked highest.

Frequent values are shown before samples because a sample is whichever values the source returned
first, while a list of the most frequent values, each with its count, shows which spelling
dominates a column and how much of it the list covers. The empty string reaches that list like any
other value, so its count is read from recorded statistics rather than collected again. It is
counted apart from null because the two can mean different things: null is a missing value, and the
empty string is sometimes a missing value a source application wrote and sometimes a real one.

A registry may take its statistics over part of a table, so an entity's row count is not the
denominator of an attribute's counts; the rows the statistics were read over are.

Nothing already written in a conversation is rewritten, and a result shown once is still in front
of the model, so showing it again costs its full size once per pass and adds nothing. The unit shown
once is the search rather than the entity: a search that ran has to be seen to have run and to
have returned what it returned, even when its entities overlap an earlier search's, and a rule by
search needs no record of which entities an earlier turn showed.

The component that writes a value into a condition is the one that sees the data, and the words it
is handed were written by a component that saw less of it, or by a person who did not see it at all.

**Consequences:** `EntityMatch.attributes` is a list of `AttributeView`, and no separate attribute
record exists. The physical planner reads the views off the matches the run already holds and is
built without the catalog. The fragment runner, which holds no discovery results, reads
`attribute_views` for the entities its fragment scans.

A request touching registered data plans at least twice, and a first plan carrying no `DISCOVER`
node is one the planner judged answerable from world knowledge alone. A first pass may end on
several terminal control nodes, so validation admits more than one
([04](04-detailed-architecture.md#validation)). A path a `SCAN` names is a path a `DISCOVER` node
found, so `validate_logical_plan` checks every one against the catalog context. A scan that named
the wrong entity is answered by the replan, which writes a new `SCAN`, not by the cover repair.

An entity is found by its own name and description, never through an attribute's name or a value
stored in it. A spelling that is neither among an attribute's most frequent values nor in the rows a
fragment is handed reaches no component, which is what the values shown leave open (question 61).

The empty string is exactly the string of length zero. A value made of whitespace is listed as the
value it is.

---

## ADR-050 — A Column Both Sides of a Join Hold Is Renamed on Both Sides

**Date:** 2026-09-28
**Status:** Accepted

**Decision:** `builtin.join` and `lotus.sem_join` name a joined table's columns by one rule. A key
both tables name alike, matched on equal values, is kept once. Any other column both tables hold
becomes `<name>_left` and `<name>_right`, and a name already taken gets `_2`, `_3`, and so on
appended. Neither side keeps the bare name.

**Rationale:** The column check reads a declared column by name and cannot tell which table a value
came from. When the left table keeps the bare name and only the right one is suffixed, a declaration
written for the right table's column passes the check holding the left table's values, and the run
answers from the wrong table with nothing to show it. With both copies renamed, a declaration naming
the bare column fails the check, the failure lists both columns it could have meant, and either one
can be declared. The alternative that keeps the bare name and refuses a declaration naming a column
both inputs hold leaves no way to declare the left one, and no join parameter can answer the
refusal. A key named alike needs no second copy, since both hold one value in every matched row, and
dropping it keeps a chain of joins on one key from growing a suffixed copy per join. On TAG-Bench,
query 952 answered with a race's name for a question about a constructor, and query 49 ended as
`replan_exhausted` because a later join refused a key an earlier one had already suffixed.

**Consequences:** A declaration or an ending meaning a column both tables held has to name its
suffixed form, and `builtin.join`'s description states the rule so the physical planner can write
it from the start. A plan written against the bare name costs one repair where it used to run, which
is the price of never answering from the wrong side. A join never refuses a table because a suffixed
name is taken. Whether `blue.join` follows the same rule is open question 62.

---

## ADR-051 — A Built-In Filter Comparison Has an Operand on Each Side

**Date:** 2026-09-29
**Status:** Accepted

**Decision:** Every comparison in `builtin.filter`'s predicate takes operands, each a column of the
row or a non-null literal: two operands compared with `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, or
`contains`; one operand against a list of literals (`in`); one operand searched by a regular
expression (`re.search`, ADR-041); or one operand tested for a missing value (`is_null`). A
comparison is checked once over the whole table before filtering and refused as recoverable when it
names a column the table does not have, or when an operand shares no type with the other side or
with its list of literals (a bool is its own type, and counts as a number only against a list of
literals). `contains` or `re.search` over an operand holding no strings, and a pattern that does not
compile, are refused the same way. Each row is then evaluated with SQL's three-valued logic: a
comparison is unknown when a value is missing or the row's values differ in type, `is_null` is
always true or false, `not` of unknown is unknown, `and` is false when any condition is false and
otherwise unknown when any is unknown, `or` is true when any condition is true and otherwise unknown
when any is unknown, and only rows whose predicate is true are kept. A value against an empty `in`
list is false. A decimal number compares exactly, and a floating-point value meeting one is read as
the shortest decimal that prints it.

**Rationale:** A logical `FILTER` may compare two columns of one row, as a plan counting two things
per group and keeping the groups where the counts agree does, and a predicate that can compare a
column only with a literal leaves that node no built-in operator. Giving both sides of every
comparison the same operand shape states column-to-column comparison without a variant of its own,
keeps one type rule and one missing-value rule for every comparison, and follows the SQL form the
models writing parameters already know. SQL's rule for unknown is used so that a negated comparison
and its opposite operator keep the same rows, and a plan written from SQL filters the same rows. A
null literal is left out because a comparison against it is never true in SQL and a missing value
already has `is_null`. Separating the whole-table check from the per-row one tells a wrong plan from
dirty data: a column wholly of another type means the parameters are wrong and the parameter repair
should rewrite them, while one stray cell in an otherwise uniform column should not cost an answer
the rest of the column gives. On TAG-Bench, query 61 spent 30 operator calls on a filter asked to
keep cities where two counts are equal.

**Consequences:** A comparison written against a column, or with a literal on the left, needs no
separate operator. The ordering operators, `contains`, and `re.search` no longer refuse a table for
one stray cell; the stray row does not match. `ne` and any negated comparison no longer keep a row
whose value is missing or is a stray cell of another type (for example `not (state eq 'CA')` no
longer keeps rows with no state), since such a comparison is unknown. Computing a value before
comparing it stays outside the predicate, as a computed column in a projection.

---

## ADR-052 — The Benchmark Lives Under `lib/tests/benchmarks/` and Runs as Modules

**Date:** 2026-09-29
**Status:** Accepted

**Decision:** The TAG-Bench build steps and harness live at `lib/tests/benchmarks/tag_bench/`: the
three scripts that build the inputs, and `benchmark/`, which runs the queries, scores the answers,
and reports a run. All four run as modules from `lib/` (`python -m tests.benchmarks.tag_bench.<name>`)
and write under `<repository>/data/tag_bench/` whatever the working directory. `pytest` runs no part
of them; their unit tests live at `tests/unit/benchmarks/tag_bench/`, in the default run.
`lib/scripts/` holds the build, install, and publish scripts and the demo scripts, all run by path,
and is not a package. A test of a demo script loads it by its file path.

**Rationale:** The `src/` layout keeps the importable code at a component root to what the
distribution ships, so anything run from `lib/` imports the package the way an installed user does.
A package at the root of `lib/` with a name as generic as `scripts` breaks that and can shadow, or be
shadowed by, another top-level `scripts` on the path. The benchmark measures the system and ships in
no package, which is what `lib/tests/` already holds. A run is still an experiment rather than a
check, for the reasons ADR-031 gives: it spends paid model calls, a person watches it, and its output
is a number and per-query records to triage, so living under `tests/` does not make it a `pytest`
tier. Running every entry as a module lets a build script import a sibling module, which a script
run by path cannot do without editing `sys.path`.

**Consequences:** The build scripts' commands change from a file path at the repository root to a
module name from `lib/`. `lib/tests/` now holds a benchmark directory beside the opt-in `lotus/` and
`end_to_end/` tiers, and `testpaths` keeps it out of the default run. The CI consequences of
ADR-031 stand: no job gates on the accuracy target.

---

## ADR-053 — Platform Directories Are Named `planner-<interface>`

**Date:** 2026-09-29
**Status:** Accepted

**Decision:** Each package under `platform/` lives in a directory named `planner-<interface>`: the
CLI in `platform/planner-cli/`, the MCP server in `platform/planner-mcp/`, and the reserved REST
API in `platform/planner-api/`. The package names are unchanged.

**Rationale:** The family's repository checklist names a service directory `<family>-<service>`, so
that a path read out of context says which repository's interface it is. A bare `cli/` or `mcp/`
does not, and `blue-data` (`registry-cli`) and `blue-data-ops` (`ops-cli`) already follow the rule.

**Consequences:** Every path naming a platform package changes: the install and build commands in
the READMEs and `docs/`, the design set, and the platform packages' own paths to each other. A
checkout with an environment under the old directories re-creates it. The paths in ADR-001 stand as
written.

---

## ADR-054 — The Model Response Cache Stays Opt-In

**Date:** 2026-09-29
**Status:** Accepted

**Decision:** `LLMClient` keeps its response cache, opened only for a call carrying the `llm.cache`
property, in the directory `llm.cache_dir` names. Nothing in this repository sets the property, and
`diskcache`, which backs the cache, stays in the `dev` extra. A consumer that turns the cache on
installs `diskcache` itself, and a client asked for a cache it cannot open is refused when it is
built.

**Rationale:** For the calls the planning components make, a cache keyed on the request rarely
hits, since each request carries the catalog, an input sample, and the conversation so far, and what
a cache reliably does there is make a run's cost depend on state left by an earlier run. A caller
reaching `execute_api_call` directly with a request that repeats is the case the property was
written for, and removing the path would take that away for no gain. Keeping it off by default and
out of the runtime dependencies costs a planner caller nothing.

**Consequences:** Every model call a run makes reaches the provider. The design set describes the
cache as present and off by default rather than absent. Question 58 is answered.

---

## ADR-055 — `execution/` Invokes Operators and Projects the Answer; `DataPlanner` Owns the Run Loop

**Date:** 2026-09-29
**Status:** Accepted

**Decision:** `execution/` is a directory of its own beside the modules that plan, holding
`execute_physical_operator`, `check_output_columns`, and `project_answer`. It runs one operator its
caller resolved and handed to it, bills the call against the operator budget, checks the result
against the output columns its caller declared, and projects the finished rows onto the columns a
`RETURN` node names. It imports only `types/`: no model client, no registry, no router, and no
planning component.

The run loop lives in `DataPlanner`. It schedules, dispatches on node kind, counts attempts, chooses
the repair, installs a cover and drops what the change invalidated, and assembles the result.

**Rationale:** Invoking a resolved operator and producing a plan are different jobs with different
dependencies: writing parameters needs a model client and the router, running a call needs neither.
Projecting the answer needs neither too: it reads rows and a list of column names. Keeping
`execution/` free of every planning component is what makes it testable without a model and keeps
the dependency direction one-way.

**Consequences:** `execution/` is three modules and names no `RunStatus`. `DataPlanner` is the only
thing that runs a plan. The types all three name live in the package root `types/`, so
`execution/` can name them without importing a planning component.

---

## ADR-056 — The Package Root Exports the Run Path; `operators` Exports the Router and the Suite Lookup

**Date:** 2026-09-29
**Status:** Accepted

**Decision:** `blue_data_planner/__init__.py` holds `__version__` and the run path: `DataPlanner`,
`build_data_planner`, `PlannerSettings`, `build_data_planner_from_settings`, `RunResult`,
`RunLimits`, `RunStatus`, and `BlueDataPlannerError`. Public is the root, every module directly
inside it, and the `__init__.py` of `types/`, `plan/`, `execution/`, `operators/`, `data/`, and
`llm/`. `prompts/` is not public. `operators/__init__.py` re-exports the router and the suite lookup
(`OperatorSuite`, `operator_suite`, `OPERATOR_SUITE_NAMES`), and the lookup imports an adapter only
when it is asked for that adapter's suite, so an adapter is imported from the module named for its
suite.

**Rationale:** Python runs a package's `__init__.py` before any module inside it, so whatever the
root names is paid by every import from the package. Of everything this library imports, an operator
suite is the only one heavy enough for that to matter, so the root can carry the run path as long as
no adapter sits on it. The suite lookup keeps that property, since it names each adapter only inside
the branch that builds it, and it is what `build_data_planner_from_settings` and both interfaces
resolve a configured suite name through. The prompt text changes with the prompts, which is not a
change a caller should have to track as an interface.

**Consequences:** `from blue_data_planner import DataPlanner` and
`from blue_data_planner.operators import operator_suite` work, and importing either loads no suite.
`from blue_data_planner.operators import blue_operators` does not, since an adapter is
`operators.adapters.blue`. Adding a name to the root is a deliberate act, and adding one that pulls
in an adapter changes what every import from the package costs. The prompt files and
`prompts/rendering.py` can change within a minor version.

---

## ADR-057 — Convexity Follows from the Other Fragment Rules and Is Not Checked on Its Own

**Date:** 2026-09-29
**Status:** Accepted

**Decision:** A fragment is connected, convex, disjoint from every other fragment, and single exit.
Over a plan that passed `validate_logical_plan`, convexity follows from the single-exit and
disjointness rules, so neither the candidate enumeration nor `validate_physical_plan` checks it
separately. [04-detailed-architecture.md](04-detailed-architecture.md#what-a-fragment-may-be) says
which rules give it.

**Rationale:** Every non-root node of a fragment has all its consumers inside the fragment, and
following consumers ends only at the root, since a valid plan holds no node nothing reads. So every
node of a fragment reaches its root, and an outside node lying between two inside nodes would close
a cycle, which a valid plan has none of. A separate check would test a property the other checks
already guarantee, and could only disagree with them through a bug in one of the two.

**Consequences:** The fragment rules read as three checked rules and one property that follows from
them. A change that drops the disjointness rule, which question 6 weighs, has to bring back a
convexity check with it. Question 33 is answered.

## ADR-058 — The Built-in Suite Runs No Code a Model Wrote

**Date:** 2026-10-14
**Status:** Accepted

**Decision:** A suite named `builtin`, written here rather than depended on, holds deterministic
operations only. Each declares one logical operator, holds no model call, and does what the
parameters the fragment runner wrote say. Together they cover every operator node kind. `builtin.scan`
declares the scan alone: it reads one registered source, and a reference input's values reach that
source as a bind join ([06](06-library.md#the-built-in-suite)). The suite runs no program a model
wrote, and builds its operators without a model client.

Two rules hold for every suite. An operation may decide how to carry out the fragment it was given,
and may repeat work inside its own call. It may not decide what the fragment is, which source to
read beyond the one the cover bound, or how many times the fragment runs. An operator reports what
it did through one untyped field on its result, set whether the call succeeded or failed, so a
callable returns that report alongside its data. The suite adds no runtime dependency.

**Rationale:** A coordinator able to finish what a source refused is what makes an all-or-nothing
source interface worth having: a source may accept a predicate whole or reject it whole, because
something downstream always can. That is a permanent property of a federated architecture, which is
why the suite is owned rather than requested. Without one, a capability gap in an outside suite is
answered as `no_operator` and a replan, a fragment that scans nothing has no engine at all, and no
plan runs unless a suite is installed. Deterministic entries covering every operator node kind give
a plan all three.

An operation that runs a program a model wrote runs it with the privileges of whoever launched the
planner. Nothing can check such a program before it runs, since a check over a language that
resolves names at run time refuses the spellings it was taught and not the others. The request text
and the values a source holds both reach the prompt that writes the program, so whoever can put text
in either can steer what runs. A child process bounds a program that is wrong, and what it bounds
depends on which limits the operating system enforces; macOS on Apple Silicon refuses an
address-space limit outright. A library consumed as an MCP server cannot hand that risk to each
deployment to contain, so the suite does not carry such an operation.

Compound operators remain the case the pushdown design is written against, and outside suites
provide them: `blue.nl2sql` groups many nodes, plans inside itself, and fails as a unit.

The rule about which decision an operation may take is what separates an operation that plans inside
a fragment from one that takes over the run loop's work. Planning inside is what a compound operator
is (ADR-006); an operation that decomposes the request or picks its own sources is doing the logical
planner's job somewhere nothing can see it.

Reporting is one untyped field rather than named ones because most operations cannot report at all,
and a named field would promise what an outside suite has no way to give.

**Consequences:** Every operator node kind keeps a built-in candidate, so `no_operator` stays rare,
and a node bound to an entry that cannot do what it asks fails at review rather than at planning
time. What keeps that from happening routinely is open question 43. A fragment containing no `SCAN`
is hosted by a transform entry, which owns its engine and takes one node. A fragment scanning two
sources has no built-in candidate, so the cover splits it into single-node fragments unless an
outside suite takes it whole.

No code a model wrote runs inside the library, so the library needs no isolation for it and a
deployment has nothing to turn on or off: `builtin_operators` takes the data registry alone, and
`BuiltinSuite` takes no options. Whether writing a program beats sequencing named operations on
TAG-Bench is no longer measured inside this repository.

## ADR-059 — A Candidate Pairs a Fragment Only with an Operator That Can Read Its Frontier

**Date:** 2026-10-14
**Status:** Accepted

**Decision:** The physical planner leaves out a candidate whose operator could not read the tables
the fragment's frontier hands it. A frontier table is a bound input when the fragment contains a
`SCAN` that reads a source, or when no node of the fragment transforms it, which is what a reference
input arriving from outside the fragment is. A candidate whose fragment receives a bound input is
offered only with an operator declaring `accepts_bound_inputs`.

`accepts_bound_inputs` is a declaration about reading a source: whether an operator can take tables
from other fragments alongside the source it reads. An operator declaring no source kinds cannot
declare it, and a `PhysicalOperator` declaring both raises when it is built.

**Rationale:** A candidate an operator is certain to refuse costs a run more than the refusal. The
fragment runner answers a recoverable failure by writing the parameters again, so each attempt is
billed as an operator call, then the cover repair and the replan spend their own bounds on a node no
parameter could have fixed. A node listing a reference input admitted by the arity floor reaches an
operation that transforms only the tables its arity names, and such a run has spent most of its
operator calls before the logical planner hears that the node cannot run. Left out at enumeration,
the same node has no candidate, and the run returns to the logical planner with a reason and no
call spent.

The declaration already says what the check needs. An operator reading a source and accepting
bound inputs, such as `builtin.scan` and its bind join, takes a table it does not transform. No
operator reading no source takes one, so a second field naming that capability would declare
something no operator has. A declaration set where it means nothing is one a suite author reads as
meaning something, which is why the contradictory pair raises rather than being ignored.

**Consequences:** Question 52 is answered. A node whose reference input arrives from outside its
fragment is offered only to operators accepting bound inputs, or inside a fragment that also holds
the node producing the reference input. Where no registered operator qualifies, the node has no
operator and the reason names the reference input, so the logical planner can write a plan reaching
the value another way, such as through a `REPLAN` that reads it first. An operator declaring bound
inputs and refusing a frontier it was offered was question 51, resolved for `blue.nl2sql` by
ADR-064. A bundle of the wrong length for any other reason is still refused inside each adapter,
which is question 15.

## ADR-060 — The Operator-Call Budget Also Bounds the Size of a Plan

**Date:** 2026-10-14
**Status:** Accepted

**Decision:** A request carries one budget, counting calls through `execute_physical_operator`;
reaching its cap ends the request. Every bound the run loop obeys is a field of `RunLimits`, set
once when the planner is built: plan corrections, parameter repairs, cover repairs, failure
replans, and control replans. `RunCounters` counts one request against them, carries the
`RunLimits` it counted against, and is what a result reports. A number whose only effect on
reaching it is to drop work rather than to redirect a request is not a bound of this kind, which is
where the cap on one `DISCOVER` node's search terms sits: `CatalogSearcher` holds it, as
`LogicalPlanner` holds the size of the catalog preview. An operator may bound work it repeats
inside one call, and the number belongs to the operator.

A logical plan holding more operator and `DISCOVER` nodes than `operator_calls` fails validation,
and the logical planner writes it again as it does any plan validation turns down. `RETURN` and
`REPLAN` are not counted. Calls through `llm/` have no cap of their own.

**Rationale:** Two kinds of model call grow with the size of a plan: one catalog search per
`DISCOVER` node, and one binding per operator node in every cover. The model writing the plan
decides how many nodes it holds, and the request shapes what the model writes, so the request
decides how many of these calls a run makes, even in a run that calls no operator. In a review, one
request produced 30 `DISCOVER` nodes and 53 model calls.

When each fragment runs one node, as it usually does, a plan with more nodes than `operator_calls`
cannot run to its end. Refusing it at validation costs one correction instead of the calls the plan
would make before the budget stopped it. Reusing `operator_calls` keeps one number for how much work
a request may do: raising it lets a request both run and plan more. The bound is approximate, since
a compound operator can run several nodes in one call and a plan over the bound might still fit.
Plans that large are rare, so the occasional extra correction is acceptable.

A separate cap on model calls would need a counter carried into every component that calls a model.
With the plan size bounded, every model call happens inside a loop an existing bound limits, so the
calls are bounded without such a counter.

**Consequences:** The model calls one request makes are bounded by the product of the existing
bounds. One pass makes at most `1 + plan_corrections` logical planning calls, at most
`operator_calls` catalog searches, and at most `operator_calls × (1 + cover_repairs)` binding calls,
and a request makes at most `1 + failure_replans + control_replans` passes. The fragment runner's
calls grow with the operator calls it bills. With the defaults, the product is about a thousand
calls: the count is finite, but it is not a cost ceiling, and no setting caps the cost of one
request.

The conversations a run holds record one usage entry per model call, so model cost is visible after
the fact. With no cost model, `operator_calls` bounds both the work a request runs and the work it
plans, so its value matters more than it otherwise would.

## ADR-061 — Every Provider Call Carries a Timeout

**Date:** 2026-10-14
**Status:** Accepted

**Decision:** `LLMClient` passes a timeout to every provider call it makes, 120 seconds unless its
properties set another. The timeout applies to each attempt, and a call that times out fails like
any other provider failure: the client retries it, and once the retries are spent the run ends
`llm_failed`. `PlannerSettings.provider_timeout_seconds` sets the value for a planner, and
`BLUE_DATA_PLANNER_PROVIDER_TIMEOUT_SECONDS` sets it for the CLI and the MCP server.

**Rationale:** A provider that accepts a request and never answers holds the run on that call, and
litellm's own default waits 6000 seconds per attempt. With three attempts, one stalled call can
hold a run for hours while the caller sees nothing. Every other wait in a run is either bounded by
the run loop or is an operator's own work, so the provider call is the one wait the library starts
and could not end.

The value is per call rather than per request because a planning call is the unit the client sees.
120 seconds is long enough for a structured answer from a reasoning model and short enough that
three attempts end within minutes. It is not measured, which is why a deployment can change it.

**Consequences:** The longest a run waits on one provider call is three timeouts plus the backoff
between them. A model slow enough to need more than 120 seconds per answer needs the setting raised,
or every call it makes fails. A model call an operator makes through its own suite, as LOTUS does,
is outside `LLMClient` and keeps that suite's own timeout.

## ADR-062 — A Model-Written Pattern Is Searched Under a Time Limit

**Date:** 2026-10-15
**Status:** Accepted

**Decision:** `builtin.filter` searches a regular expression the fragment runner wrote with the
`regex` package, under a limit of one second per value. A search that reaches the limit is refused
as a recoverable failure naming the pattern, so the parameter repair writes the pattern again.
`regex` becomes a runtime dependency of `lib/`.

**Rationale:** A pattern with nested repetition can take time exponential in the length of the
value it searches, and the model writing the pattern does not see that cost. Python's `re` offers
no way to stop a search once it starts, and the search runs on the thread running the request, so
one pattern over one long value could hold a run with nothing in the run loop or the operator able
to end it. `regex` accepts every pattern `re` accepts and takes a timeout per search. Refusing patterns by
their shape before running them would turn down useful patterns and still miss slow ones, since
which patterns backtrack badly depends on the values too.

One second per value is not measured. A pattern that does not backtrack badly searches a cell in
microseconds, so the limit only fires on a pattern that is already failing.

**Consequences:** Question 59 is answered. A pattern reaching the limit costs one operator call
before the repair, as any recoverable failure does. A filter over many rows can still take up to the
limit once per row on a pattern that is slow on every value but never quite reaches it, which the
limit does not bound. The `lib/` install gains one compiled dependency, which litellm's own
dependencies already install.

## ADR-063 — A Request Has a Time Limit and Can Be Cancelled, Both Checked Between Steps

**Date:** 2026-10-15
**Status:** Accepted

**Decision:** `RunLimits.wall_clock_seconds`, 600 by default, bounds how long one request runs.
`DataPlanner.run` and `plan` also take an optional `threading.Event`, and setting it cancels the
request. The run loop checks both before each step: before writing a plan, before writing a cover,
and before running a fragment or resolving control nodes. A request past its time limit ends
`deadline_exceeded`, and a cancelled one ends `cancelled`, each with the `RunResult` built so far.
A step already running finishes first. The MCP server sets the event when the client cancels a
`query_data` call. `BLUE_DATA_PLANNER_MAX_WALL_CLOCK_SECONDS` sets the limit for the CLI and the
MCP server.

**Rationale:** The library is synchronous, so the MCP server runs each request on a thread, and a
thread cannot be stopped from outside. A client that times out and retries otherwise leaves the
first run spending beside the second. Only an event the run reads between steps can stop it, and a
time limit read at the same points covers a caller that never cancels. Checking between steps rather
than inside one keeps every component unaware of either: a model call, an operator call, and a
source read each end on their own terms, and the provider timeout (ADR-061) and the pattern time
limit (ADR-062) bound the two waits a step could otherwise prolong without end.

Two statuses rather than reusing `budget_exhausted` let a caller tell a request that ran out of
time from one that ran out of operator calls, and from one it stopped itself.

600 seconds is not measured. A TAG-Bench question finishes in a few minutes, and a limit several
times that stops a run that is not converging without stopping one that is.

**Consequences:** A request can run past its limit by as long as its last step takes: the
operator calls of one fragment with its parameter repairs, or the bindings of one cover. The answer
of a run whose steps all finished is returned even past the limit, since no step is left to check
before. `RunCounters` does not report elapsed time, so unlike the other three bounds spanning a
whole request, the time limit has no count in the result. A caller of the library that wants cancellation creates the
event and sets it from another thread.

---

## ADR-064 — Source-Reading Operators Declare Their Current Input Capability

**Date:** 2026-10-23
**Status:** Accepted

**Decision:** `blue.nl2sql` declares `accepts_bound_inputs=False`, matching the operation it
wraps: it writes SQL from a source schema and cannot incorporate external tables. For every
source-reading operator with this flag false, the physical planner requires an empty fragment
frontier, including a cut containing no `SCAN`. Operators that transform tables and declare no
source kinds retain their ordinary inputs. A source-reading operator that implements bound
inputs, such as `builtin.scan`, can still receive them.

**Rationale:** A capability declaration describes the current implementation. Advertising a
future capability offers work the operation always refuses and spends parameter repairs on a
failure no parameter can fix. Checking only reference inputs is insufficient: a table-only
`FILTER` or `AGGREGATE` cut has ordinary transformed inputs and no scan, but is equally unusable
by `blue.nl2sql`.

**Consequences:** Open question 51 is resolved. Unsupported SQL fragments are excluded before
binding or operator execution. Complete SQL regions remain eligible, and a direct call with an
input bundle is still refused defensively. If a future version of the operation incorporates
external tables, the adapter can widen its declaration after testing that behavior. This
clarifies ADR-059 for source-reading operators; the single-database limitation remains open
question 44.

---

## ADR-065 — A Broken Operator Implementation Goes Straight to Cover Repair

**Date:** 2026-10-23
**Status:** Accepted

**Decision:** `OperatorUnavailableError` becomes a failed `ExecutionResult` with
`ErrorKind.OPERATOR_UNAVAILABLE`. `FragmentRunner` retains that attempt and immediately returns
`needs_repair`, so the existing cover-repair loop can select another operator. It spends no
further parameter attempts on the unavailable implementation. The Blue adapter uses this path
for `DEFAULTS_FAILED`, `VALIDATION_FAILED`, `REFINEMENT_FAILED`, and `*_RESULT_INVALID`, retaining
the operation's diagnostic message, suggestion, and report.

**Rationale:** Those structured Blue errors describe the chosen implementation failing, not the
planner failing. Turning them into a bare `BlueDataPlannerError` ended the request as
`planner_failed` even when another operator could serve the node (#54). Parameter retries cannot
repair a broken validator or malformed result, while an unrecoverable error would still end the
whole run. A separate kind lets the component that chooses implementations answer the failure.

**Consequences:** Existing cover-repair, failure-replan, and operator-call budgets still bound
recovery. The failed attempt and any reusable upstream results remain available to the cover
repair. Existing `INVALID_INPUT`, `EXECUTION_FAILED`, and timeout classifications are unchanged;
the treatment of non-retryable execution errors remains #55. Unexpected planner exceptions still
follow the existing defect path. This extends ADR-015's operator failure classification and adds
one serialized error-kind value without adding a run status.

---

## ADR-066 — Execution Failures Use Recovery Metadata Before Spending Parameter Repairs

**Date:** 2026-10-23
**Status:** Accepted

**Decision:** The Blue adapter no longer treats every `EXECUTION_FAILED` as recoverable. A
recoverable category or a true `retryable` flag allows parameter repair. A non-retryable `FAILED`
execution instead raises `OperatorUnavailableError`, using ADR-065's immediate cover repair.
`INVALID_INPUT` remains repairable even when its unchanged call is non-retryable.

**Rationale:** A broken execution implementation or an unsupported model setting cannot be
fixed by changing fragment parameters. Treating its failure as recoverable spends every
parameter attempt before another operator is considered (#55). Metadata distinguishes that
case without turning one operation's failure into a whole-run failure.

For `blue.nl2sql`, ops also reports correctable query mistakes as generic execution failures.
The existing invocation-local diagnostics therefore mark malformed generated queries, SQL policy
refusals, and known SQLite syntax/name errors as parameter-repairable. This preserves #37's
repair path. Unknown exceptions, provider failures, connection failures, and database locks do
not gain parameter retries merely because a diagnostic was recorded.

**Consequences:** Diagnostics, suggestions, reports, successful upstream work, and existing
repair budgets are retained. Timeouts and unexpected planner exceptions keep their existing
handling. This extends ADR-065 to #55 without changing run statuses or upstream operations.

---

## ADR-067 — Optional Response Caching Uses Private, Deployment-Owned JSON Storage

**Date:** 2026-10-23
**Status:** Accepted

**Decision:** The planner's general sync/async LLM calls retain opt-in caching through
`llm.cache`, using standard-library JSON files instead of pickle-backed `diskcache`. The directory
is fixed when a client is constructed: its `cache_dir` keyword overrides
`BLUE_DATA_PLANNER_LLM_CACHE_DIR`, and the default is `~/.cache/blue-data-planner/llm-v1`.
Configured paths must be absolute. `llm.cache_dir` is no longer an accepted property.

The directory must belong to the current process user with mode `0700`, checked on every access.
Files use mode `0600`. Directory-relative, no-follow file access and atomic replacement prevent
linked entries and concurrent writes from redirecting or partially publishing cache data. Cache
keys and JSON envelopes carry a planner-specific format namespace. Corrupt, foreign, linked, or
legacy entries are misses; old pickle caches are never loaded or migrated.

**Rationale:** The old shared `.llm_cache` default let unrelated clients read each other's
responses, and a caller-selected directory made pickle deserialization an unsafe trust boundary
(#60, cross-repo blue-data#122). JSON removes object deserialization; deployment configuration
and private permissions make the storage boundary explicit.

**Consequences:** This supersedes ADR-054's storage and dependency choices, retaining its opt-in
policy. The planner no longer needs `diskcache`; other repositories' caches remain their own
per-repo work. Existing deployments using `llm.cache_dir` must move the path to constructor or
environment configuration, use an owner-only directory, and start with a cold JSON cache.
Caching requires POSIX owner and permission checks. Cache-hit accounting, provider retries, and
uncached `complete_structured` calls are unchanged.

---

## ADR-068: Open the registry through blue-data's shared store

**Status:** Accepted (2026-10-23)

**Decision:** With no explicit path, `open_registry()` delegates to
`blue_data.config.build_store` for `BLUE_DATA_REGISTRY_STORE=file` or `redis`, using the same
file, Redis URL, and namespace settings as blue-data's registry CLI/API/MCP and other clients.
`PlannerSettings.registry_path` defaults to `None` to select this configuration. The CLI
exports resolved profile values before building; MCP reads only its process environment.
Both interfaces accept the shared settings without requiring `BLUE_DATA_REGISTRY_PATH`.

An explicit path (including the CLI's `--registry-path`) takes precedence and accepts a
standard dictionary file store or a legacy list snapshot. The old `BLUE_DATA_REGISTRY_PATH`
is a fallback only when no shared backend is selected. Invalid backend configuration and
unreadable stores fail before planning; a missing file is not created and does not trigger
a fallback to another registry. New demo and benchmark registries use the dictionary store.

**Reason:** The planner's list snapshots could not be opened by blue-data's file store, and
the planner could not open the dictionary file store other components wrote (#61, blue-data#119).
Sharing blue-data's store reader removes the incompatible registry path for normal deployments.

**Consequences:** Existing explicit-file callers and snapshots remain usable. Deployments
using snapshots can rebuild them with the demo/benchmark registration script and select the
shared file backend. Redis support retains blue-data's optional dependency and RedisJSON
requirements and delegates its connection behavior upstream. In-memory callers continue to
construct a `DataRegistry` and pass it directly to `build_data_planner`.

---

## Open Questions

Design questions first, then defects in the code.

| # | Question | Blocks |
| --- | --- | --- |
| 3 | Would annotating a frontier column from the catalog earn its risk? The fragment runner reads a view of every attribute of the entities its own fragment scans, and reads a column arriving on the frontier off the input table it came in on, which carries names, types, and a sample of rows (ADR-049). A frontier column may also have a catalog record, and reaching it means tracing back through the cover to whichever fragment scanned it. Tracing through fragments built only from the kinds that leave columns unchanged — `FILTER`, `DISTINCT`, `SORT_SUPERLATIVE` — is safe, while tracing by name regardless of kind can put a scanned attribute's statistics in front of a model for a column a `PROJECT` redefined. What is unmeasured is whether whole-column statistics add anything the sample rows do not, for a column the run already holds rows for. The first release annotates neither way. | Whether a `FILTER` running alone over rows an earlier fragment scanned sees catalog statistics as well as its input's rows ([10-data-model.md](10-data-model.md#what-the-fragment-runner-reads)) |
| 6 | Should a result stay valid when only the cut around it changed? A continuation reading a node a fragment grouped forces that fragment to be dropped and every node in it to run again, even the ones whose logical inputs did not change ([04, what reading a grouped node costs](04-detailed-architecture.md#what-reading-a-grouped-node-costs)). Two ways out. Keying validity on a node's upstream logical nodes rather than on the shape of the cover would save the downstream work. Dropping the disjointness rule would let a continuation build its own fragment over `SCAN`, `FILTER`, and the new node while the earlier fragment keeps its result, at the price of running the same scan twice and of giving up the property that each node runs exactly once, which is what makes an attempt list readable as a record of where each pushdown boundary was drawn. Both carry the same consistency risk: a saved result from a fragment call sitting beside newly materialized rows from a different cut, so two nodes in one plan could hold values their operators never agreed on. | Whether a failure-driven replan pays for a whole fragment call a second time, and whether the attempt record still answers where the boundary was |
| 8 | Should a fragment pattern be allowed to constrain the order of the nodes it covers? `logical_operators` is a set, so a candidate is any connected convex subgraph built from those kinds, and an operator that can only group a filter *after* a scan has no way to say so. No declared operator's capability depends on the order of the nodes it covers, so a set says everything a declaration needs to. | Whether an operator whose capability depends on node order can be declared at all |
| 15 | Where does the check that an operator receives the number of tables its logical operator declares belong? `INPUT_ARITY` governs what the logical planner writes, and validation checks a node's input count against it, but the bundle an operator is handed is the concatenation of its upstream outputs (ADR-012), and nothing ties that length back to the arity. Each adapter refuses a bundle of the wrong length inside its own callable, which is one rule written once per suite and leaves the next suite free to forget it. `execute_physical_operator` is the single place every call goes through, and for any operator declaring one logical operator it could read the count off `INPUT_ARITY` itself, so a suite added later would get the rule without doing anything. Against moving it: an adapter needs the same guard anyway before it can take its tables apart, so the check would be written twice rather than once. | Whether a wrongly shaped bundle is refused by one rule or by one rule per suite |
| 18 | Should the three planning components and the discovery step share one model by default? [12-deployment-ops.md](12-deployment-ops.md#4-configuration) sets `BLUE_DATA_PLANNER_MODEL` for all of them, and `DataPlanner`'s constructor takes a client per component, so the split is available and simply not the default ([09-api-interface-spec.md](09-api-interface-spec.md#assembly)). The components do different work. Logical planning writes a plan from a catalog preview and is the one call whose mistakes every later stage inherits, since a badly cut plan cannot be rescued by a good cover. Writing parameters and reviewing a result are narrower, run several times per request, and see concrete columns and values rather than a whole catalog. That shape favors a stronger model for the first and a cheaper one for the rest, which is also what the cost profile favors, and nothing has measured whether the quality holds. Against it: a split default doubles what a deployment configures before it can run anything, and two model strings is two things to get wrong. The TAG-Bench harness reports model calls per component, which says where the spend is but not whether a cheaper model would have answered the same. | What a deployment pays per request, and whether one badly chosen model string degrades every component at once |
| 32 | Should a prompt ever be optimized automatically rather than written by hand? ADR-005 fixes hand-written prompts versioned in the repository, and the reason is that tuning needs a score to tune against. The TAG-Bench harness produces end-to-end accuracy, which scores a whole run rather than one component's prompt, so a tuner reading it would credit the logical planner for what the fragment runner repaired. Answering it means either a per-component score or accepting that a whole-run score is enough, and `gepa` is the candidate dependency ([05-software-stack.md](05-software-stack.md#what-is-deliberately-absent)). | Whether `prompts/` stays hand-written, and whether `lib/` gains a tuning dependency |
| 37 | What may an adapter absorb, and what must it refuse? Every suite mismatches what a plan needs in its own way, and with no shared rule each mismatch is settled in whichever adapter hits it first. Five absorptions look like translation rather than behavior, and are the candidates for a permitted list: shaping a parameter schema an operation has none of, converting between the bundle and the suite's own shape, mapping the suite's failures onto the planner's error kinds, supplying the registry or model client an operation needs to run at all, and offering fewer operations or narrower capability than the suite advertises. Four look like behavior and are the candidates for a forbidden list: declaring a capability the operation lacks, computing or patching part of a result, retrying or splitting or reordering work the run loop owns, and returning an empty result in place of a reported failure. What ADR-058 settles is the planning case alone: an operation may plan how to carry out its own fragment and may not take a decision the run loop owns, which permits one that writes a query and refuses one that decomposes the request. The rest of both lists is unsettled, and so is an operation whose declared capability contradicts its own metadata, where an adapter that binds it anyway fails partway through a run and never offering the candidate would have cost nothing. | Whether a capability gap is answered in the adapter or upstream, and which `blue-data-ops` operations an adapter offers at all |
| 38 | Where does an operation's capability declaration come from? A `PhysicalOperator` carries `logical_operators`, `source_kinds`, `accepts_bound_inputs`, and `limitations`, and the adapter hand-writes all four. `blue-data-ops` states most of them itself, on an `OperatorCard`: closed `accepts-*`, `requires-*`, and `capability-*` tags plus a `when_to_invoke` line, written by whoever wrote the operation. Reading the card where it exists would have caught the `nl2sql` defect, since the card says `accepts-none`. Against reading it: the card is `blue-data-ops`'s own scheme and nothing else has one — LOTUS declares no capability metadata at all, and a suite this repository writes declares whatever it chooses — so deriving one suite's declarations and hand-writing every other suite's is two rules for one field. The tag vocabulary is also close to but not the same question: `capability-filter` and `LogicalOperator.FILTER` agree, while `accepts-data` says an operation reads a bundle and not whether it can read one alongside a source it scans. A third route is to keep hand-writing and have the conformance test compare against the card where a suite has one, so a disagreement fails a test rather than a run. | Whether a hand-written declaration is checked against anything, and whether the adapter conformance test reads more than `AttributeSpec` |
| 41 | Is the bundle shape chosen to match one suite's operator signature? ADR-012 fixes a list of tables on both sides of an operator call, and no document says how much conversion the Blue adapter then needs ([06-library.md](06-library.md#what-an-adapter-reads)). Two things are unsettled. Whether the alignment with the Blue operations is deliberate or incidental: if it is deliberate, the shape follows one suite and every other adapter pays the conversion, and if it is not, the shape is chosen for what a plan passes and the Blue adapter converts like any other. And what a callable invokes on the suite's side, which no document names either: a dependency's method name is a fact that goes stale, and naming it in two places is how two documents came to disagree about it. | Whether ADR-012's shape rests on a dependency's signature, and whether the Blue adapter is a pass-through |
| 42 | Does anything here emit traces or logs of its own, and if so which package? [12-deployment-ops.md](12-deployment-ops.md#7-logging) fixes the library at standard-library logging with no handler configured, so an embedding application decides the format and the destination, and nothing in [05-software-stack.md](05-software-stack.md) declares a tracing dependency. Three routes. The adapters own it: the MCP package declares an exporter, emits structured JSON logs, and opens one span per tool request, leaving the library uninstrumented. The library owns it: spans cover each planning component, each operator call, and each model call, which is the only route that can show where a run's time and money went, at the price of a tracing dependency in `lib/` and an import in code every consumer runs. Or nothing ships in the first release and the run record stays the instrument. What argues for instrumenting at all is the fragment span: it would carry the logical nodes a fragment covered and the operator that ran them, which is where the pushdown boundary was drawn, and a trace can show that where a counter cannot. Against it: `RunResult` already carries every plan, every attempt, the counters, and one usage record per model call, so a trace would restate what the caller is handed. | Whether a deployment can see inside a run without reading the returned result, and which package pays for it |
| 43 | How is the physical planner made to choose a more capable operator over a built-in one? The built-in suite has a deterministic candidate for every operator node kind (ADR-058), so a node is almost never answered as `no_operator` and a wrong choice is refused at review instead, one operator call later. A binding turning every candidate down (ADR-040) answers the node nothing fits; this question is the other one, where a built-in candidate returns rows that look like an answer. `description` and `limitations` are the whole of what separates two candidates in the prompt, so a built-in entry that cannot judge meaning has to say so in terms a model weighs against a LOTUS or Blue operator that can, and one that reads whole tables into memory to work over them has to say that too. `builtin.filter` can approximate a semantic predicate — a regular expression matching a phrase where a reader would have judged a title — and the approximation returns rows and reports no error, so nothing but review stands between it and an answer. The review rejection rate of fragments bound to a built-in entry, which the TAG-Bench harness reports, is what measures it. | Whether a floor that always has a candidate raises the cost of every request that needed the ceiling |
| 44 | Can an operator declare that every scan in one fragment reads the same source? `source_kinds` is a set of kinds and says nothing about identity, and each `SCAN` names its source separately, so nothing stops a cover from grouping scans of two databases into one operator's fragment. `blue.nl2sql` needs the constraint, since one SQL query cannot span two of them. Without a way to declare it the constraint lives in `limitations` as plain language, and a candidate that cannot work is offered anyway. This is question 8's family: a capability the declaration has no vocabulary for. | Whether an operator limited to one source at a time can be declared at all, or is offered candidates it has to refuse |
| 45 | Is a bound an operator enforces inside its own call a bound of ADR-060's kind? `RunLimits` holds every bound the run loop obeys, and two numbers sit below it instead: the distinct values a bind join carries, and the rows a LOTUS operation accepts before refusing the call. Both are shaped like `RunLimits` fields, and they do not agree with each other about who sets them: the LOTUS cap is an argument to `lotus_operators`, so a deployment chooses it where it builds the suite, while the bind-join cap is fixed in the operator that enforces it. `builtin.scan` reads only `sql/sqlite`, a source with a predicate language, and implements two of the three realizations in [06](06-library.md#data): values embedded in the query text, capped at 100 distinct values, and a temporary table, capped by nothing. Past the embedded-values cap it escalates to the temporary table rather than refusing the input, so `builtin.scan` never refuses a bind join for carrying too many values; 100 is where it switches realization, not a limit it enforces. The one-call-per-value realization, and the tighter cap that would bound it, belong to an operator reading a source with no predicate language, a case nothing in the built-in suite covers. Every number here is a placeholder set low enough not to break a run rather than measured against real volumes or real failure rates. A `RunLimits` field would let a deployment raise a cap past a default nobody has tested; leaving each number inside its operator keeps the choice with whichever component already sees what decides it. | Whether a bound below the run loop is configurable per deployment, and what real volumes and failure rates call for |
| 47 | Should the operator vocabulary gain a node that looks the catalog up once per input row? ADR-049 keeps a `DISCOVER` node to a bounded set of entities however many rows it was handed, so a request such as "find the press release document for each of these 500 companies" has nowhere to go: that lookup is per row, what it returns is data rather than context, and it should be billed against the operator budget and reviewed like any other data operation. An operator node would give it all three. Against adding one: none of the ten logical operators reads the catalog, so this would be the first, and what it returns — paths rather than values — is a kind of row nothing else in a plan produces. How often a request needs one is unmeasured. | Whether a per-row catalog lookup is expressible at all, and whether the logical vocabulary stays as it is |
| 48 | Which component reads a `relation` record when a plan scans a knowledge-base source? `blue-data` records relations alongside entities and attributes, each with a name, a source, and a target, and nothing in this design reads one. A `JOIN` is the obvious consumer, since a relation says which attributes join two entities, and it could arrive as part of the run's catalog context or as part of the fragment runner's deeper reading. What stops the question being answered for tables alone is that relations are the structure a knowledge-base source carries, and how this planner reads a knowledge base is itself unsettled, so an answer shaped for two SQL tables would likely be the wrong shape for a graph. | Whether a `JOIN` is written against a declared relation or against column names a model matched up, and what a knowledge-base source contributes to a plan |
| 54 | Should the library be able to phrase an answer, and the CLI then offer `--format nl`? The family offers a natural-language format where a source answers in prose, and ADR-029 leaves it out because a planner answers in rows. A benchmark whose expected answers are single values, and an agent that wants a sentence rather than a table, both suggest the phrasing has to happen somewhere; doing it in an interface is behavior in an adapter (ADR-002), so the question is whether a library operation should return prose alongside the rows, and what it costs in model calls per request. | Whether `--format nl` and the MCP response ever carry a sentence, and which component writes it |
| 55 | Should a run be kept on disk so a plan can be read back? The CLI's home directory holds profiles and nothing else, so a plan printed by `data query --explain` is gone when the terminal scrolls, and `data query` keeps no record a later command could inspect. The family's storage convention has a place for one (`~/.<cli>/<profile>/<component>/`), and `from_dict` already reads a recorded plan back ([06-library.md](06-library.md#two-operations)). What is unsettled is whether a CLI should accumulate state at all for a planner that holds none between requests, and what would remove an old run. | Whether the CLI grows an action that reads a plan it wrote earlier |
| 56 | Should the CLI be able to write a run's whole record to a file? A fragment's result reaches a person as a summary, and the JSON on standard output carries the answer and how the run ended, so every intermediate table and every model turn is reachable only by a caller who embeds the library (ADR-032). Diagnosing a wrong answer is the case that wants them: a summary says a fragment returned 140 rows over three columns and not which 140, and a review that rejected a fragment is readable only in the conversation. A file is where they would go, since what makes them unwelcome on standard output is size rather than secrecy, and the family's storage convention already has a place for per-profile state. Against adding one: a record holds whatever the sources held, so writing one puts source data on disk, and whether a CLI over a planner that keeps nothing between requests should accumulate state at all is question 55. | Whether a wrong answer can be diagnosed from the CLI alone |
| 60 | Should a value that does not fit the type the cover declared for a column go straight to the cover repair? The physical planner writes each fragment's required output columns and their types, and the fragment runner cannot change them, so no parameter attempt can fix the mismatch: the runner spends its remaining attempts on parameters that cannot help, and each one that reaches the operator is an operator call. Every attempt reaches the cover repair (ADR-047), so the mismatch is visible to the component that can fix it, and what is at stake is the attempts spent first. ADR-065 provides an `operator_unavailable` kind that could carry this case too. Against sending it straight on: a model that knows which column it filled might still fix the value, as when an operator returns a unit the declaration did not expect. Column mismatches remain recoverable pending that decision. | How many operator calls a wrong type declaration costs before the cover repair sees it |
| 61 | How does a component find a spelling the values it is shown leave out? A view lists an attribute's most frequent values and the fragment runner sees the first rows of each input (ADR-049), so a spelling that is rare within a column of many distinct values reaches no component. In `codebase_community`, `users.Location` holds `Vienna, Austria` in 42 rows and `Vienna` in 4, and a filter written for `Vienna` returns the 4 rows and reports no error. Three routes. The fragment runner writes a condition that tolerates spelling, such as a pattern for `builtin.filter` (ADR-041), which needs nothing new but relies on the model guessing that the spelling varies. The catalog is searched for stored values matching a term, which `blue-data`'s registry search ranks by, and the matches are shown with the view, which puts a search per condition into prompt writing. Or a lookup is a data operation of its own, which is question 47's shape. | Whether a condition on a text attribute with many distinct values is written against a spelling any component saw |
| 62 | Should `blue.join` name a joined table's columns by ADR-050's rule? `builtin.join` and `lotus.sem_join` keep a key named alike once and suffix any other shared column `_left` and `_right`, so a declaration written for one reads the other's output. `blue.join` takes its suffixes as a parameter, which the adapter's parameter schema does not expose, so they are left at `blue-data-ops`'s default, `_ds` and the table's position. The adapter could fix them to `_left` and `_right`, or expose them to the fragment runner, which then has to know the rule to write a declaration that matches. Neither makes `blue.join` drop a key's second copy. | Whether a plan's declared columns mean the same thing whichever suite runs its join |
