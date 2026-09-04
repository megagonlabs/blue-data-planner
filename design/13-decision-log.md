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

**Rationale:** One boundary gives one place to change when `blue-data` changes and one place to
substitute an in-memory double. Keeping ingestion out is what stops the boundary from growing a
second surface that duplicates a dependency's own tooling. The benchmark still needs a registry
built, and a build step is not shipped code.

**Consequences:** Anything the planner needs from `blue-data` is exposed through the boundary first.
A caller with data and no registry builds one with `blue-data` before constructing a planner. The
rule stays checkable by reading a file's imports, with one path exclusion.

---

## ADR-004 — Operator Suites Enter Through Adapters, Never Vendored

**Decision:** Every operator suite with an owner elsewhere, `blue-data-ops` included, is integrated
by an adapter under `operators/` that describes each of the suite's operations as a
`PhysicalOperator` and calls the suite as an outside dependency. No such suite's source is copied
into this repository and none is a git submodule. Writing a suite of our own is a different act and
is ADR-020's; what is ruled out here is holding a copy of someone else's. A suite is a pinned dependency, optional where its install is heavy, and an optional
suite's adapter imports it inside the callable rather than at module import time.

**Rationale:** Copied source cannot pick up upstream fixes and quietly becomes a fork nobody
maintains. A git submodule appears in neither an sdist nor a wheel and cannot be resolved by pip, so
`pip install blue-data-planner[lotus]` would hand a consumer an adapter with nothing behind it,
which contradicts the consumer install path of ADR-001. Pinning rather than floating matters because
an adapter names each operation and its parameters. The lazy import is what makes `import
blue_data_planner` work with the suite absent, and what keeps the default test suite runnable
without a multi-gigabyte install.

**Consequences:** The repository depends on how often each suite releases and how stable its
interface is, `blue-data-ops` included. An optional suite absent from the environment means the
router registers none of its operators, which is a smaller catalog rather than an error. Each suite
needs a stub under `lib/tests/stubs/`. Should a patched suite ever be needed, the pin becomes a git
URL against a fork rather than a submodule.

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
`PlanResult.planner_usage` with its tokens, while a model call a suite makes internally is invisible
there and bills as part of the one operator call containing it (ADR-014). Planner spend is exact;
operator spend is a call count. The `builtin` suite is not an outside suite and calls through this
module like everything else here, so it can say what its own calls cost; that figure travels in the
operator call's report rather than in `planner_usage`, which keeps the scope its name states
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
operator declares the set of logical operators it can absorb, candidate fragments follow
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
holds a result for, absorbed nodes included, and drops the rest. Results live in a map the run owns,
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
an absorbed node forces the fragment that absorbed it to run again.
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
bundle and gives one back alongside its report (ADR-020). A fragment's inputs are the tables its
upstream fragments produced, concatenated in the order it lists them. Each adapter converts at its own edge: the LOTUS adapter builds one data frame per
table and flattens coming out.

**Rationale:** A row that is a plain dict belongs to no suite. It keeps pandas out of every module
except the adapter that needs it, and it makes a stored intermediate result readable. A bundle
rather than a single table is what a multi-table operation needs, since a join, a union, and an
intersect each read every table handed to them. Declaring a fragment's value to be a single table
would make a multi-output operator unrepresentable by construction rather than by decision.

**Consequences:** Conversion happens per fragment rather than once, so consecutive LOTUS fragments
pay for a data frame they could have kept. Every reader of a fragment's
output indexes a table before a row, so the rows of a single-output fragment are `result.output[0]`.
`row_count` counts rows across every table and describes no shape; nothing reports table count.

---

## ADR-013 — A Fragment's Result Is Checked, Then Reviewed

**Decision:** A result is judged in two passes. `execute_physical_operator` checks it against the
fragment root's `required_output_columns`, coercing each column to its declared type and failing the
call as a recoverable error when it cannot. A result clearing that check goes to a model review turn
returning an assessment and one of accept, reject, or replan. The review sees the fused instruction,
the operator's description, the instruction of every node the fragment absorbed, and a sample of the
result. Only the root is checked, since a fused call produces no intermediate tables.

**Rationale:** The column check is cheap, deterministic, and catches the mistakes with an obvious
right answer. It cannot catch a result that is well shaped and wrong, which is the failure that
matters most for a semantic operator. The absorbed nodes' instructions are what make a fused call
checkable at all: one call asked to scan, filter, and join returns one table and no evidence of the
steps inside it, and a call handed a semantic predicate SQL cannot express tends to return rows that
ignore it.

**Consequences:** One extra model call per fragment in the normal case. Writing parameters and
reviewing the result are turns in one conversation, so the review sees what was written and why. The
review is a place the system can talk itself into accepting a bad result, so its prompt and its
accept rate need measuring rather than assuming.

---

## ADR-014 — One Operator-Call Budget; Model Calls Go Unmetered

**Decision:** A request carries one budget, counting calls through `execute_physical_operator`;
reaching its cap ends the request. Every other bound the system obeys is a field of `PlanLimits`,
set once when the planner is built: plan corrections, parameter attempts, fragment attempts, cover
attempts, failure replans, control replans, and discovery search terms. Calls through `llm/` are not
separately capped.

**Rationale:** A model-call cap does not scale independently of the bounds a request already
carries. Every model call happens inside a loop one of those bounds already limits, so a further
number would restate them under a different name. The operator call is what scales on its own — a
single semantic operator can touch a source of any size regardless of how few model calls it took to
reach it — so it is the one call worth billing against a request-level cap.

**Consequences:** A caller bounds spending through operator calls and replans alone.
`PlanResult.planner_usage` records one entry per model call, so model cost is visible after the fact
even though nothing stops it mid-run: a badly set `parameter_attempts` or `plan_corrections` can run
up a bill without ever tripping `operator_calls`. With no cost model, the operator call budget is
the only thing standing between a badly ordered plan and an unbounded bill, so its default matters
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
`build_data_planner`, `PlanResult`, `PlanLimits`, `RunStatus`, and `BlueDataPlannerError`. Public is
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
`operators.blue`. Adding a name to the root is a deliberate act, and adding one that pulls in an
adapter changes what every import from the package costs. The import rule an adapter obeys names a
shape rather than a list of modules ([08-coding-style.md](08-coding-style.md)).

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
wrote say. One compound entry, `builtin.nl2table`, holds a model call: it turns a fragment's
instruction into a sequence of those same operations and runs it. Both kinds wrap one shared module
of functions over the bundle, so no entry calls another.

Most deterministic entries declare one logical operator. `builtin.scan` declares the scan together
with the filter and the projection it can push into a source, reads one registered source, and binds
values taken from an input table; whatever it cannot push is left to the operations that run in
process. `builtin.nl2table` declares a vocabulary covering every operator kind, reads registered
sources, and accepts bound inputs, and it is the one operator with no same-source constraint, so a
fragment scanning two sources has a candidate. Neither entry retries inside itself.

An operation may decide how to carry out the fragment it was given. It may not decide what the
fragment is, which source to read beyond the one the cover bound, or how many times to run. That
rule holds for every suite rather than only this one.

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

The model call sits inside the compound entry rather than in its parameter schema, where the
fragment runner would have written the sequence under the mechanism that already exists. The planner
has to bind operations that absorb many nodes, plan inside themselves, and fail as a unit, so one of
our own, with an honest declaration and a working bind join, is worth having as the case the rest is
written against.

The rule about which decision an operation may take is what separates an operation that plans inside
a fragment from one that takes over the run loop's work. Planning inside is what a compound operator
is (ADR-006); an operation that decomposes the request or picks its own sources is doing the logical
planner's job somewhere nothing can see it.

Reporting is one untyped field rather than named ones because most operations cannot report at all,
and a named field would promise what an outside suite has no way to give.

**Consequences:** Every operator node kind has a candidate, so `no_operator` becomes rare and the
failure it used to prevent moves to run time: a semantic predicate now reaches a deterministic
operator and is refused at review, one operator call later. What keeps that from happening routinely
is open question 43. A fragment containing no `SCAN` is hosted by an operator that owns its engine,
and `builtin.nl2table` is one.

The model call inside `builtin.nl2table` happens after the call is billed, unlike the parameters,
which are checked before. A sequence the model got wrong spends an operator call where a bad
parameter spends none. `LLMError` raised inside a callable reaches the run loop and ends the request
as `llm_failed`.

`operators/` holds more than adapters, and an owned entry carries behavior to test rather than
translation ([11-testing-strategy.md](11-testing-strategy.md)). Every adapter returns a report
alongside its data, and an outside suite's is empty.

`EXPAND` stops needing its two readings told apart: a deterministic unnest and a semantic expansion
are two candidates for one node kind, and the physical planner chooses between them. Two `SCAN`
cases still have no built-in candidate: one bound to a source kind the suite does not read, and one
of the model's world knowledge, which is bound to no source and so cannot be given to an operator
declaring `source_kinds`.

---

## Open Questions

Design questions first, then defects in code that exists today.

| # | Question | Blocks |
| --- | --- | --- |
| 2 | How often do plans actually schedule a replan? If rarely, the limit can be low; if it is most requests over discovered sources, planning is two-pass in practice and the design should say so. The TAG-Bench harness reports it ([11-testing-strategy.md](11-testing-strategy.md)), so what is left is running it. | The `PlanLimits` defaults |
| 3 | Which of a fragment's columns can be resolved to a `ColumnRef`, so the fragment runner sees the catalog's `distinct_count`, `min`, `max`, and `semantic_type` for it? A fragment's `scan_sources` names a catalog path for each `SCAN` it contains, so a column of a table the fragment itself scans resolves without ambiguity. A column arriving on the frontier does not: resolving it means tracing back through the cover to whichever fragment scanned it. Three rules are on the table. The narrow one resolves only the fragment's own scans, and is safe because its failure is missing information rather than wrong information. The middle one traces through fragments built only from the kinds that leave columns unchanged, which are `FILTER`, `DISTINCT`, and `SORT_SUPERLATIVE`, and stops at any fragment containing `PROJECT`, `EXPAND`, `AGGREGATE`, `JOIN`, or `SCAN`. The wide one traces by column name regardless of kind, and can put a scanned column's statistics in front of a model for a column a `PROJECT` quietly redefined. The narrow rule loses the case a lone `FILTER` over scanned rows presents, which is the shape TAG-Bench produces most, so the choice is worth measuring rather than guessing. | What the fragment runner sees when it writes parameters for a fragment that scans nothing ([06-library.md](06-library.md#which-column-gets-a-view)) |
| 6 | Should a result stay valid when only the cut around it changed? A continuation reading a node a fragment absorbed forces that fragment to be dropped and every node in it to run again, even the ones whose logical inputs did not change ([04, what reading an absorbed node costs](04-detailed-architecture.md#what-reading-an-absorbed-node-costs)). Two ways out. Keying validity on a node's upstream logical nodes rather than on the shape of the cover would save the downstream work. Dropping the disjointness rule would let a continuation build its own fragment over `SCAN`, `FILTER`, and the new node while the earlier fragment keeps its result, at the price of running the same scan twice and of giving up the property that each node runs exactly once, which is what makes an attempt list readable as a record of where each pushdown boundary was drawn. Both carry the same consistency risk: a saved result from a fused call sitting beside newly materialized rows from a different cut, so two nodes in one plan could hold values their operators never agreed on. | Whether a failure-driven replan pays for a whole fused call a second time, and whether the attempt record still answers where the boundary was |
| 7 | When a control node's inputs all have results and a fragment is also ready, which runs first? Running the fragments first gives the logical planner more results to write the continuation against, and costs nothing in wasted work, since a new logical version carries over every node the run has something for. Firing the control node first can save operator calls on a branch the continuation turns out not to need, at the price of planning against less. [06-library.md](06-library.md#the-run-loop) leaves `next_ready` free to do either, and nothing has measured which is better. | How much a `DISCOVER` node costs on a plan with parallel branches |
| 8 | Should a fragment pattern be allowed to constrain the order of the nodes it covers? `logical_operators` is a set, so a candidate is any connected convex subgraph built from those kinds, and an operator that can only absorb a filter *after* a scan has no way to say so. No declared operator's capability depends on the order of the nodes it covers, so a set says everything a declaration needs to. | Whether an operator whose capability depends on node order can be declared at all |
| 15 | Nothing checks that an operator receives the number of tables its logical operator declares. `INPUT_ARITY` governs what the logical planner writes and validation checks a node's input count against it, but the bundle an operator is handed is the concatenation of its upstream outputs (ADR-012), and no check ties that length back to the arity. `blue.join` given one table returns an empty result rather than a recoverable failure naming the problem. The two counts can only diverge once a multi-output operator exists. | Whether a wrongly shaped bundle fails loudly, as the error-handling rules require, or quietly |
| 18 | Should the three planning components and the discovery step share one model by default? [12-deployment-ops.md](12-deployment-ops.md#4-configuration) sets `BLUE_DATA_PLANNER_MODEL` for all of them, and `DataPlanner`'s constructor takes a client per component, so the split is available and simply not the default ([09-api-interface-spec.md](09-api-interface-spec.md#assembly)). The components do different work. Logical planning writes a plan from a catalog preview and is the one call whose mistakes every later stage inherits, since a badly cut plan cannot be rescued by a good cover. Writing parameters and reviewing a result are narrower, run several times per request, and see concrete columns and values rather than a whole catalog. That shape favors a stronger model for the first and a cheaper one for the rest, which is also what the cost profile favors, and nothing has measured whether the quality holds. Against it: a split default doubles what a deployment configures before it can run anything, and two model strings is two things to get wrong. The TAG-Bench harness reports model calls per component, which says where the spend is but not whether a cheaper model would have answered the same. | What a deployment pays per request, and whether one badly chosen model string degrades every component at once |
| 32 | Should a prompt ever be optimized automatically rather than written by hand? ADR-005 fixes hand-written prompts versioned in the repository, and the reason is that tuning needs a score to tune against. The TAG-Bench harness produces end-to-end accuracy, which scores a whole run rather than one component's prompt, so a tuner reading it would credit the logical planner for what the fragment runner repaired. Answering it means either a per-component score or accepting that a whole-run score is enough, and `gepa` is the candidate dependency ([05-software-stack.md](05-software-stack.md#what-is-deliberately-absent)). | Whether `prompts/` stays hand-written, and whether `lib/` gains a tuning dependency |
| 33 | Does the fragment rule list still need convexity? [04-detailed-architecture.md](04-detailed-architecture.md#what-a-fragment-may-be) lists connected, convex, disjoint, and single exit as four separate properties, and `validate_physical_plan` enforces that list. For a plan that already passed `validate_logical_plan`, the other three give convexity for nothing. Every non-root node of a fragment has all its consumers inside the fragment; following consumers terminates only at the root, since a valid plan holds no node nothing reads; so every node of a fragment reaches its root, and an outside node lying between two inside nodes would close a cycle, which a DAG has none of. Both premises carry the argument, and one of them is already in question: dropping the disjointness rule, which question 6 weighs, would take the argument with it. Two ways to close it. Say in `04` which of the four the others already give, so a reader learns the list is not independent and a check for it need not be written. Or leave the four standing, on the grounds that the implication holds only as long as disjointness does. | Whether the fragment rules read as four independent constraints when three of them imply the fourth |
| 37 | What may an adapter absorb, and what must it refuse? Every suite mismatches what a plan needs in its own way, and with no shared rule each mismatch is settled in whichever adapter hits it first. Five absorptions look like translation rather than behavior, and are the candidates for a permitted list: shaping a parameter schema an operation has none of, converting between the bundle and the suite's own shape, mapping the suite's failures onto the recoverable and unrecoverable kinds, supplying the registry or model client an operation needs to run at all, and offering fewer operations or narrower capability than the suite advertises. Four look like behavior and are the candidates for a forbidden list: declaring a capability the operation lacks, computing or patching part of a result, retrying or splitting or reordering work the run loop owns, and returning an empty result in place of a reported failure. What ADR-020 settles is the planning case alone: an operation may plan how to carry out its own fragment and may not take a decision the run loop owns, which permits one that writes a query and refuses one that decomposes the request. The rest of both lists is unsettled, and so is an operation whose declared capability contradicts its own metadata, where an adapter that binds it anyway fails partway through a run and never offering the candidate would have cost nothing. | Whether a capability gap is answered in the adapter or upstream, and which `blue-data-ops` operations an adapter offers at all |
| 38 | Where does an operation's capability declaration come from? A `PhysicalOperator` carries `logical_operators`, `source_kinds`, `accepts_bound_inputs`, and `limitations`, and the adapter hand-writes all four. `blue-data-ops` states most of them itself, on an `OperatorCard`: closed `accepts-*`, `requires-*`, and `capability-*` tags plus a `when_to_invoke` line, written by whoever wrote the operation. Reading the card where it exists would have caught the `nl2sql` defect, since the card says `accepts-none`. Against reading it: the card is `blue-data-ops`'s own scheme and nothing else has one — LOTUS declares no capability metadata at all, and a suite this repository writes declares whatever it chooses — so deriving one suite's declarations and hand-writing every other suite's is two rules for one field. The tag vocabulary is also close to but not the same question: `capability-filter` and `LogicalOperator.FILTER` agree, while `accepts-data` says an operation reads a bundle and not whether it can read one alongside a source it scans. A third route is to keep hand-writing and have the conformance test compare against the card where a suite has one, so a disagreement fails a test rather than a run. | Whether a hand-written declaration is checked against anything, and whether the adapter conformance test reads more than `AttributeSpec` |
| 39 | Should the `builtin` suite gain an entry that writes and runs a program from a fragment's instruction? It subsumes the deterministic operations in principle, and a model writes such a program well, so it may answer fragments a fixed vocabulary cannot reach. Against it: `limitations` stops being enforceable. A fixed vocabulary makes "no semantic predicate" structurally true, since nothing in it can express one, while a program can express one badly and still look right, so the field falls from a fact to a request. A failure also arrives as a traceback or a wrong answer rather than a refusal, the run record holds text where the rest holds structured data, and running model-written code in the planner's process needs an answer on what may name what before it is offered outside a benchmark. Both would be candidates for the same node sets, so this is answerable by measurement rather than by argument. | Whether `builtin` holds an entry that can fail in ways the others cannot |
| 41 | Is the bundle shape chosen to match one suite's operator signature? ADR-012 fixes a list of tables on both sides of an operator call, and no document says how much conversion the Blue adapter then needs ([06-library.md](06-library.md#the-adapters)). Two things are unsettled. Whether the alignment with the Blue operations is deliberate or incidental: if it is deliberate, the shape follows one suite and every other adapter pays the conversion, and if it is not, the shape is chosen for what a plan passes and the Blue adapter converts like any other. And what a callable invokes on the suite's side, which no document names either: a dependency's method name is a fact that goes stale, and naming it in two places is how two documents came to disagree about it. | Whether ADR-012's shape rests on a dependency's signature, and whether the Blue adapter is a pass-through |
| 42 | Does anything here emit traces or logs of its own, and if so which package? [12-deployment-ops.md](12-deployment-ops.md#7-logging) fixes the library at standard-library logging with no handler configured, so an embedding application decides the format and the destination, and nothing in [05-software-stack.md](05-software-stack.md) declares a tracing dependency. Three routes. The adapters own it: the MCP package declares an exporter, emits structured JSON logs, and opens one span per tool request, leaving the library uninstrumented. The library owns it: spans cover each planning component, each operator call, and each model call, which is the only route that can show where a run's time and money went, at the price of a tracing dependency in `lib/` and an import in code every consumer runs. Or nothing ships in the first release and the run record stays the instrument. What argues for instrumenting at all is the fragment span: it would carry the logical nodes a fragment covered and the operator that ran them, which is where the pushdown boundary was drawn, and a trace can show that where a counter cannot. Against it: `PlanResult` already carries every plan, every attempt, the counters, and one usage record per model call, so a trace would restate what the caller is handed. | Whether a deployment can see inside a run without reading the returned result, and which package pays for it |
| 43 | How is the physical planner made to choose a more capable operator over a built-in one? Every operator node kind has a built-in candidate (ADR-020), so a node is almost never answered as `no_operator` and a wrong choice is refused at review instead, one operator call later. `description` and `limitations` are the whole of what separates two candidates in the prompt, so a deterministic operator that cannot express a semantic predicate has to say so in terms a model weighs against a description promising a general engine, and one that runs unoptimized in the planner's process has to say that too. The review rejection rate of fragments bound to a built-in entry, which the TAG-Bench harness reports, is what measures it. | Whether a floor that always has a candidate raises the cost of every request that needed the ceiling |
| 44 | Can an operator declare that every scan in one fragment reads the same source? `source_kinds` is a set of kinds and says nothing about identity, and `scan_sources` binds each `SCAN` in a fragment separately, so nothing stops a cover from handing one operator two databases. `blue.nl2sql` needs the constraint, since one SQL query cannot span two of them, while `builtin.nl2table` deliberately lacks it and covering such a fragment is the point of it. Without a way to declare the difference the constraint lives in `limitations` as plain language, and a candidate that cannot work is offered anyway. This is question 8's family: a capability the declaration has no vocabulary for. | Whether an operator limited to one source at a time can be declared at all, or is offered candidates it has to refuse |
