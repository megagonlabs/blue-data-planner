# blue-data-planner — Decision Log

Each entry records a decision that had a real alternative and that other documents are written
against. Anything still provisional, and anything that only says what a release leaves out, is an
open question at the end rather than an entry here.

No entry carries a date or a status, and nothing here is superseded. The open questions at the end
are everything not yet decided.

---

## ADR-001 — Python Packaging and Tooling

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

**Decision:** An operator node carries exactly one field: an instruction written in plain language.
It names no operator and no source, because both can differ between passes over the same node. The
columns a later node depends on are recorded on the node as `required_output_columns`, each one a
name and a type drawn from `str`, `int`, `float`, and `bool`. `RETURN` carries none, since it names
input columns to project rather than promising output.

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

**Consequences:** An operator's description carries all the weight in the physical planner's prompt,
because one plain-language instruction does not tell operators apart. The logical planner writes one type per column
and can get it wrong, which shows up as a recoverable failure and a retry rather than as a wrong
answer. There is no date type, so a date travels as an ISO 8601 string, and a comma-formatted number
such as `"142,984"` declared `int` is refused rather than repaired.

---

## ADR-009 — Operator Routing Is Separate from the Operator Registry

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

**Decision:** The value one fragment passes to the next is a list of tables, where a table is a list
of row dicts.

```python
Record = dict[str, Any]  # one row
Data = list[Record]  # one table
DataBundle = list[Data]  # what one fragment passes to the next
```

The data an operator receives and the data it returns are both this shape, so a callable takes a
bundle and gives one back alongside its report (ADR-020). A callable is also handed the columns
the fragment's root declares, to read or to ignore; what the result carries is checked after the
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
benchmark sweep aborts on the first operator bug unless its harness catches `BlueDataPlannerError`
per query and scores that query as an error.

---

## ADR-017 — The Package Root Exports the Run Path

**Decision:** `blue_data_planner/__init__.py` holds `__version__` and the run path: `DataPlanner`,
`build_data_planner`, `RunResult`, `RunLimits`, `RunStatus`, and `BlueDataPlannerError`. Public is
the root, every module directly inside it, and the `__init__.py` of `types/`, `plan/`, `execution/`,
`operators/`, `data/`, and `llm/`. An operator adapter is the one exception: `operators/__init__.py`
re-exports the router alone, so an adapter is imported from the module named for its suite.

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
cases still have no built-in candidate: one bound to a source kind the suite does not read, and one
of the model's world knowledge, which is bound to no source and so cannot be given to an operator
declaring `source_kinds`.

---

## ADR-021 — The Catalog Readings Rank and Cap, and Never Filter by Score

**Decision:** Every catalog reading that searches asks the registry to rank and takes the top
results, and none of them drops a record for scoring below a bar. `preview` takes its cap from the
logical planner, and `search_records` takes one fixed when the catalog is built. What a component
sees is therefore the best matches the registry can offer, in order, and never nothing.

**Rationale:** A search engine's score threshold is tuned for a consumer, and the consumer here is a
model reading a prompt rather than a user reading a result list. A model weighs a weak match at the
bottom of a ranked list against the rest of the list and discounts it. An empty list it cannot
weigh at all: nothing in one distinguishes a registry holding nothing relevant from a registry
holding nothing, so a logical planner handed an empty preview has no way to tell a bad search from
an empty catalog and will plan as though there were no sources. Ranking also degrades gracefully
where a registry has no vector index, which is the state a registry built by ingestion alone is in.
Choosing what each component sees is this repository's business, and a threshold chosen for a
different consumer is not that choice ([06-library.md](06-library.md#datacatalog)).

**Consequences:** A weak match reaches a prompt, so a component has to be written to expect
irrelevant records in a ranked list rather than to trust that everything returned is relevant. The
caps are the only bound on how much catalog a prompt carries, which makes them the number to tune
when a prompt grows too large. Where the underlying registry exposes score thresholds, `data/` sets
them out of the way rather than relying on their defaults.

---

## ADR-022 — Catalog Detail Below the Source Level Enters Only Through `DISCOVER`

**Decision:** A `DISCOVER` node is the one way a run learns anything below the source level. The
search runs at the entity level, and a hit returns that entity together with every attribute it
holds: each attribute's name, its type, and the description enrichment wrote for it. What every
resolved node found, taken together, is the run's catalog context. The logical planner writes its
continuation against it and the physical planner binds each `SCAN` to an entity named in it, so
neither searches the registry itself.

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
one pass apart, so validation and reachability admit more than one
([04](04-detailed-architecture.md#validation)).

An entity is found by its own name and description. A search cannot reach one through an attribute's
name or through a value stored in it, so a request whose vocabulary matches only an attribute needs
a term the entity itself answers to.

The attribute list travels in every planning prompt for the rest of the run, which is what keeps
`max_search_results` small and what makes the per-attribute view's sample-value cap the place a wide
entity's cost is controlled ([06-library.md](06-library.md#datacatalog)).

A path reaching a cover is a path a `DISCOVER` node found, so `validate_physical_plan` can check
every bound path against the catalog context and refuse one a model invented.

---

## ADR-023 — A Bind Join's Table Is Dropped by the Operator That Wrote It

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

**Decision:** A fragment whose parameter repairs ran out sends the run to the cover repair, which
covers the whole logical plan again. That one repair carries every fix physical planning has: binding
a different operator, cutting a node set into smaller fragments, binding a scan to a different
entity, and growing a fragment across nodes that already ran. `PhysicalPlanner` has one entry point
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
one, or as a new component, while a mode of an existing capability arrives as a flag. Deviations from
the family are recorded, since a reader is entitled to assume the family's meaning everywhere it is
not. The MCP server keeps two tools where the CLI has one command, because a tool call carries no
flags.

---

## ADR-028 — CLI Configuration Lives in a Profile

**Decision:** `blue-planner` resolves every setting as command option, then environment variable,
then the selected profile, then the default. Profiles live in `~/.blue-planner/.profiles`, over the
configuration engine `blue-data` publishes, and `BLUE_DATA_PLANNER_HOME` relocates the directory. A
secret is stored encoded or as an environment reference, never accepted as a command-line value, and
masked wherever the CLI prints it. The CLI loads no `.env` file. The MCP server keeps reading the
environment, and loads one.

**Rationale:** A profile is what lets one machine hold a demonstration setup and a production one
without a shell full of exports, and the engine that implements it already exists in `blue-data`, so
every Blue CLI resolves settings and stores secrets by the same rules. `load_dotenv(override=True)`
is what keeps the CLI out of that scheme: a file that overrides the exported environment sits
between the option and the profile in a way the documented order cannot describe. The MCP server has
no interactive user to configure a profile, and a client that launches it as a subprocess passes an
environment, so the file stays useful there.

**Consequences:** The CLI package depends on `blue-data` directly, which the library reaches only
through `data/`. A bound a run reports back is a profile parameter rather than a flag, so a
request that needs a different budget selects a different profile. A demonstration configures itself with
`profile config` rather than with a `.env` file in the checkout.

---

## ADR-029 — The CLI Prints Rows, and Offers No Prose Format

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

**Decision:** The CLI exits 0 when the command succeeded, 2 for a usage error, and 1 for everything
else, including a run that did not finish and a configuration failure. What kind of failure it was
is the `<CODE>` in the `ERROR: <CODE> <description>` line on standard error.

**Rationale:** The other Blue CLIs use these three, and a script written against one of them should
read the same number from all of them. A fourth code for configuration would separate one failure
class in this repository alone, while the error codes separate every class in every repository at
once, which is the separation a caller can actually rely on.

**Consequences:** A caller distinguishing a missing API key from an exhausted budget reads the error
code or the JSON payload rather than `$?`. A run that did not finish still prints its result under
`--format json`, so the status is machine-readable without parsing the error line.

---

## ADR-031 — The Benchmark Is a Script, Not a Test Tier

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

## Open Questions

Design questions first, then defects in code that exists today.

| # | Question | Blocks |
| --- | --- | --- |
| 3 | Would annotating a frontier column from the catalog earn its risk? The fragment runner reads a view of every attribute of the entities its own fragment scans, and reads a column arriving on the frontier off the input table it came in on, which carries names, types, and a sample of rows (ADR-022). A frontier column may also have a catalog record, and reaching it means tracing back through the cover to whichever fragment scanned it. Tracing through fragments built only from the kinds that leave columns unchanged — `FILTER`, `DISTINCT`, `SORT_SUPERLATIVE` — is safe, while tracing by name regardless of kind can put a scanned attribute's statistics in front of a model for a column a `PROJECT` redefined. What is unmeasured is whether whole-column statistics add anything the sample rows do not, for a column the run already holds rows for. The first release annotates neither way. | Whether a `FILTER` running alone over rows an earlier fragment scanned sees catalog statistics as well as its input's rows ([10-data-model.md](10-data-model.md#what-the-fragment-runner-reads)) |
| 6 | Should a result stay valid when only the cut around it changed? A continuation reading a node a fragment grouped forces that fragment to be dropped and every node in it to run again, even the ones whose logical inputs did not change ([04, what reading a grouped node costs](04-detailed-architecture.md#what-reading-a-grouped-node-costs)). Two ways out. Keying validity on a node's upstream logical nodes rather than on the shape of the cover would save the downstream work. Dropping the disjointness rule would let a continuation build its own fragment over `SCAN`, `FILTER`, and the new node while the earlier fragment keeps its result, at the price of running the same scan twice and of giving up the property that each node runs exactly once, which is what makes an attempt list readable as a record of where each pushdown boundary was drawn. Both carry the same consistency risk: a saved result from a fragment call sitting beside newly materialized rows from a different cut, so two nodes in one plan could hold values their operators never agreed on. | Whether a failure-driven replan pays for a whole fragment call a second time, and whether the attempt record still answers where the boundary was |
| 8 | Should a fragment pattern be allowed to constrain the order of the nodes it covers? `logical_operators` is a set, so a candidate is any connected convex subgraph built from those kinds, and an operator that can only group a filter *after* a scan has no way to say so. No declared operator's capability depends on the order of the nodes it covers, so a set says everything a declaration needs to. | Whether an operator whose capability depends on node order can be declared at all |
| 15 | Where does the check that an operator receives the number of tables its logical operator declares belong? `INPUT_ARITY` governs what the logical planner writes, and validation checks a node's input count against it, but the bundle an operator is handed is the concatenation of its upstream outputs (ADR-012), and nothing ties that length back to the arity. Each adapter refuses a bundle of the wrong length inside its own callable, which is one rule written once per suite and leaves the next suite free to forget it. `execute_physical_operator` is the single place every call goes through, and for any operator declaring one logical operator it could read the count off `INPUT_ARITY` itself, so a suite added later would get the rule without doing anything. Against moving it: an adapter needs the same guard anyway before it can take its tables apart, so the check would be written twice rather than once. | Whether a wrongly shaped bundle is refused by one rule or by one rule per suite |
| 18 | Should the three planning components and the discovery step share one model by default? [12-deployment-ops.md](12-deployment-ops.md#4-configuration) sets `BLUE_DATA_PLANNER_MODEL` for all of them, and `DataPlanner`'s constructor takes a client per component, so the split is available and simply not the default ([09-api-interface-spec.md](09-api-interface-spec.md#assembly)). The components do different work. Logical planning writes a plan from a catalog preview and is the one call whose mistakes every later stage inherits, since a badly cut plan cannot be rescued by a good cover. Writing parameters and reviewing a result are narrower, run several times per request, and see concrete columns and values rather than a whole catalog. That shape favors a stronger model for the first and a cheaper one for the rest, which is also what the cost profile favors, and nothing has measured whether the quality holds. Against it: a split default doubles what a deployment configures before it can run anything, and two model strings is two things to get wrong. The TAG-Bench harness reports model calls per component, which says where the spend is but not whether a cheaper model would have answered the same. | What a deployment pays per request, and whether one badly chosen model string degrades every component at once |
| 32 | Should a prompt ever be optimized automatically rather than written by hand? ADR-005 fixes hand-written prompts versioned in the repository, and the reason is that tuning needs a score to tune against. The TAG-Bench harness produces end-to-end accuracy, which scores a whole run rather than one component's prompt, so a tuner reading it would credit the logical planner for what the fragment runner repaired. Answering it means either a per-component score or accepting that a whole-run score is enough, and `gepa` is the candidate dependency ([05-software-stack.md](05-software-stack.md#what-is-deliberately-absent)). | Whether `prompts/` stays hand-written, and whether `lib/` gains a tuning dependency |
| 33 | Does the fragment rule list still need convexity? [04-detailed-architecture.md](04-detailed-architecture.md#what-a-fragment-may-be) lists connected, convex, disjoint, and single exit as four separate properties, and `validate_physical_plan` enforces that list. For a plan that already passed `validate_logical_plan`, the other three give convexity for nothing. Every non-root node of a fragment has all its consumers inside the fragment; following consumers terminates only at the root, since a valid plan holds no node nothing reads; so every node of a fragment reaches its root, and an outside node lying between two inside nodes would close a cycle, which a DAG has none of. Both premises carry the argument, and one of them is already in question: dropping the disjointness rule, which question 6 weighs, would take the argument with it. Two ways to close it. Say in `04` which of the four the others already give, so a reader learns the list is not independent and a check for it need not be written. Or leave the four standing, on the grounds that the implication holds only as long as disjointness does. | Whether the fragment rules read as four independent constraints when three of them imply the fourth |
| 37 | What may an adapter absorb, and what must it refuse? Every suite mismatches what a plan needs in its own way, and with no shared rule each mismatch is settled in whichever adapter hits it first. Five absorptions look like translation rather than behavior, and are the candidates for a permitted list: shaping a parameter schema an operation has none of, converting between the bundle and the suite's own shape, mapping the suite's failures onto the recoverable and unrecoverable kinds, supplying the registry or model client an operation needs to run at all, and offering fewer operations or narrower capability than the suite advertises. Four look like behavior and are the candidates for a forbidden list: declaring a capability the operation lacks, computing or patching part of a result, retrying or splitting or reordering work the run loop owns, and returning an empty result in place of a reported failure. What ADR-020 settles is the planning case alone: an operation may plan how to carry out its own fragment and may not take a decision the run loop owns, which permits one that writes a query and refuses one that decomposes the request. The rest of both lists is unsettled, and so is an operation whose declared capability contradicts its own metadata, where an adapter that binds it anyway fails partway through a run and never offering the candidate would have cost nothing. | Whether a capability gap is answered in the adapter or upstream, and which `blue-data-ops` operations an adapter offers at all |
| 38 | Where does an operation's capability declaration come from? A `PhysicalOperator` carries `logical_operators`, `source_kinds`, `accepts_bound_inputs`, and `limitations`, and the adapter hand-writes all four. `blue-data-ops` states most of them itself, on an `OperatorCard`: closed `accepts-*`, `requires-*`, and `capability-*` tags plus a `when_to_invoke` line, written by whoever wrote the operation. Reading the card where it exists would have caught the `nl2sql` defect, since the card says `accepts-none`. Against reading it: the card is `blue-data-ops`'s own scheme and nothing else has one — LOTUS declares no capability metadata at all, and a suite this repository writes declares whatever it chooses — so deriving one suite's declarations and hand-writing every other suite's is two rules for one field. The tag vocabulary is also close to but not the same question: `capability-filter` and `LogicalOperator.FILTER` agree, while `accepts-data` says an operation reads a bundle and not whether it can read one alongside a source it scans. A third route is to keep hand-writing and have the conformance test compare against the card where a suite has one, so a disagreement fails a test rather than a run. | Whether a hand-written declaration is checked against anything, and whether the adapter conformance test reads more than `AttributeSpec` |
| 41 | Is the bundle shape chosen to match one suite's operator signature? ADR-012 fixes a list of tables on both sides of an operator call, and no document says how much conversion the Blue adapter then needs ([06-library.md](06-library.md#what-an-adapter-reads)). Two things are unsettled. Whether the alignment with the Blue operations is deliberate or incidental: if it is deliberate, the shape follows one suite and every other adapter pays the conversion, and if it is not, the shape is chosen for what a plan passes and the Blue adapter converts like any other. And what a callable invokes on the suite's side, which no document names either: a dependency's method name is a fact that goes stale, and naming it in two places is how two documents came to disagree about it. | Whether ADR-012's shape rests on a dependency's signature, and whether the Blue adapter is a pass-through |
| 42 | Does anything here emit traces or logs of its own, and if so which package? [12-deployment-ops.md](12-deployment-ops.md#7-logging) fixes the library at standard-library logging with no handler configured, so an embedding application decides the format and the destination, and nothing in [05-software-stack.md](05-software-stack.md) declares a tracing dependency. Three routes. The adapters own it: the MCP package declares an exporter, emits structured JSON logs, and opens one span per tool request, leaving the library uninstrumented. The library owns it: spans cover each planning component, each operator call, and each model call, which is the only route that can show where a run's time and money went, at the price of a tracing dependency in `lib/` and an import in code every consumer runs. Or nothing ships in the first release and the run record stays the instrument. What argues for instrumenting at all is the fragment span: it would carry the logical nodes a fragment covered and the operator that ran them, which is where the pushdown boundary was drawn, and a trace can show that where a counter cannot. Against it: `RunResult` already carries every plan, every attempt, the counters, and one usage record per model call, so a trace would restate what the caller is handed. | Whether a deployment can see inside a run without reading the returned result, and which package pays for it |
| 43 | How is the physical planner made to choose a more capable operator over a built-in one? Where `builtin.nl2table` is registered every operator node kind has a built-in candidate (ADR-020), so a node is almost never answered as `no_operator` and a wrong choice is refused at review instead, one operator call later. `description` and `limitations` are the whole of what separates two candidates in the prompt, so an operator that cannot judge meaning has to say so in terms a model weighs against a description promising a general engine, and one that reads whole tables into memory to work over them has to say that too. A built-in program can approximate a semantic predicate — matching a phrase where a reader would have judged a title — and the approximation returns rows and reports no error, so nothing but review stands between it and an answer. The review rejection rate of fragments bound to a built-in entry, which the TAG-Bench harness reports, is what measures it. | Whether a floor that always has a candidate raises the cost of every request that needed the ceiling |
| 44 | Can an operator declare that every scan in one fragment reads the same source? `source_kinds` is a set of kinds and says nothing about identity, and `scan_sources` binds each `SCAN` in a fragment separately, so nothing stops a cover from handing one operator two databases. `blue.nl2sql` needs the constraint, since one SQL query cannot span two of them, while `builtin.nl2table` deliberately lacks it and covering such a fragment is the point of it. Without a way to declare the difference the constraint lives in `limitations` as plain language, and a candidate that cannot work is offered anyway. This is question 8's family: a capability the declaration has no vocabulary for. | Whether an operator limited to one source at a time can be declared at all, or is offered candidates it has to refuse |
| 45 | Is a bound an operator enforces inside its own call a bound of ADR-014's kind? `RunLimits` holds every bound the run loop obeys, and three numbers sit below it instead: the distinct values a bind join carries, the times `builtin.nl2table` writes its program again before returning a recoverable failure, and the rows a LOTUS operation accepts before refusing the call. All three are shaped like `RunLimits` fields, and they do not agree with each other about who sets them: the LOTUS cap is an argument to `lotus_operators`, so a deployment chooses it where it builds the suite, while the other two are fixed in the operator that enforces them. `builtin.scan` reads only `sql/sqlite`, a source with a predicate language, and implements two of the three realizations in [06](06-library.md#data): values embedded in the query text, capped at 100 distinct values, and a temporary table, capped by nothing. Past the embedded-values cap it escalates to the temporary table rather than refusing the input, so `builtin.scan` never refuses a bind join for carrying too many values; 100 is where it switches realization, not a limit it enforces. The one-call-per-value realization, and the tighter cap that would bound it, belong to an operator reading a source with no predicate language, a case nothing in the built-in suite covers. Every number here is a placeholder set low enough not to break a run rather than measured against real volumes or real failure rates. A `RunLimits` field would let a deployment raise a cap past a default nobody has tested; leaving each number inside its operator keeps the choice with whichever component already sees what decides it. | Whether a bound below the run loop is configurable per deployment, and what real volumes and failure rates call for |
| 47 | Should the operator vocabulary gain a node that looks the catalog up once per input row? ADR-022 keeps a `DISCOVER` node to a bounded set of entities however many rows it was handed, so a request such as "find the press release document for each of these 500 companies" has nowhere to go: that lookup is per row, what it returns is data rather than context, and it should be billed against the operator budget and reviewed like any other data operation. An operator node would give it all three. Against adding one: none of the ten logical operators reads the catalog, so this would be the first, and what it returns — paths rather than values — is a kind of row nothing else in a plan produces. How often a request needs one is unmeasured. | Whether a per-row catalog lookup is expressible at all, and whether the logical vocabulary stays as it is |
| 48 | Which component reads a `relation` record when a plan scans a knowledge-base source? `blue-data` records relations alongside entities and attributes, each with a name, a source, and a target, and nothing in this design reads one. A `JOIN` is the obvious consumer, since a relation says which attributes join two entities, and it could arrive as part of the run's catalog context or as part of the fragment runner's deeper reading. What stops the question being answered for tables alone is that relations are the structure a knowledge-base source carries, and how this planner reads a knowledge base is itself unsettled, so an answer shaped for two SQL tables would likely be the wrong shape for a graph. | Whether a `JOIN` is written against a declared relation or against column names a model matched up, and what a knowledge-base source contributes to a plan |
| 51 | What does a compound operation do with a fragment whose frontier carries rows, before it can read one? `blue.nl2sql` declares bound inputs, so the physical planner offers it groups whose inputs arrive from an earlier fragment. A query written from a source's schema alone would answer from that source and leave those rows out, which is a wrong answer rather than a failure, and refusing the bundle as a recoverable failure is what keeps it from being returned. That refusal costs an operator call on a candidate that could not have worked. Narrowing the declaration instead would stop the candidate being enumerated at all, and would have to be widened again by hand once the operation reads its bundle, so the declaration states the target state and is not narrowed. What is unsettled is whether the refusal should cost a call, and whether a candidate an operator is known to refuse should be enumerated at all. That is question 44's shape: a constraint the declaration has no vocabulary for is stated in `limitations`, and the candidate is offered anyway. | Whether a fragment with a non-empty frontier reaches the compound Blue operation, and what a run spends finding out that it cannot serve one |
| 52 | Should a group whose node lists reference inputs be offered to an operation declaring one logical operator? Arity is a floor in a plan, so a node may list more inputs than its kind transforms ([10-data-model.md](10-data-model.md#how-many-inputs-a-node-takes)), and the bundle built for such a group carries one table per input the fragment reads, which is more tables than the arity names. An operation reading only the tables its arity names refuses that bundle as a recoverable failure, and the physical planner covers the node set again, so the run spends an operator call on a candidate the cover could have skipped. Nothing in a declaration says whether an operation can read a table without transforming it: `logical_operators` names the kinds an operation covers and no field carries the rest, which is the shape questions 44 and 51 share. Two routes. The cover compares a node's input count against the arity and offers only operators declared to read a reference input, which needs that field written first. Or the refusal stays where it is and its cost is accepted, on the grounds that what an operation can read moves with its own suite while a hand-written declaration has to be rewritten to follow. | Whether a plan the arity floor admits is one every operator bound to it can run, and what a run spends on a call refused for the length of its bundle |
| 54 | Should the library be able to phrase an answer, and the CLI then offer `--format nl`? The family offers a natural-language format where a source answers in prose, and ADR-029 leaves it out because a planner answers in rows. A benchmark whose expected answers are single values, and an agent that wants a sentence rather than a table, both suggest the phrasing has to happen somewhere; doing it in an interface is behavior in an adapter (ADR-002), so the question is whether a library operation should return prose alongside the rows, and what it costs in model calls per request. | Whether `--format nl` and the MCP response ever carry a sentence, and which component writes it |
| 55 | Should a run be kept on disk so a plan can be read back? The CLI's home directory holds profiles and nothing else, so a plan printed by `data query --explain` is gone when the terminal scrolls, and `data query` keeps no record a later command could inspect. The family's storage convention has a place for one (`~/.<cli>/<profile>/<component>/`), and `from_dict` already reads a recorded plan back ([06-library.md](06-library.md#two-operations)). What is unsettled is whether a CLI should accumulate state at all for a planner that holds none between requests, and what would remove an old run. | Whether the CLI grows an action that reads a plan it wrote earlier |
| 56 | Should the CLI be able to write a run's whole record to a file? A fragment's result reaches a person as a summary, and the JSON on standard output carries the answer and how the run ended, so every intermediate table and every model turn is reachable only by a caller who embeds the library (ADR-032). Diagnosing a wrong answer is the case that wants them: a summary says a fragment returned 140 rows over three columns and not which 140, and a review that rejected a fragment is readable only in the conversation. A file is where they would go, since what makes them unwelcome on standard output is size rather than secrecy, and the family's storage convention already has a place for per-profile state. Against adding one: a record holds whatever the sources held, so writing one puts source data on disk, and whether a CLI over a planner that keeps nothing between requests should accumulate state at all is question 55. | Whether a wrong answer can be diagnosed from the CLI alone |

