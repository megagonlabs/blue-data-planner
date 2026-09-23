# blue-data-planner — Testing Strategy

The bar is the Blue library standard rather than a research-script standard. The library is depended
on by other Blue components and by outside users, so its public interfaces and its failure paths
are both covered.

Two things shape everything below. The library is synchronous, so most tests are ordinary functions
with no event loop. And a request's whole outcome is one inspectable structure (ADR-015), so a test
asserts against `RunResult` rather than against printed output.

---

## 1. What a test is for

A test says what correct behavior is for whoever calls the code. That is the whole job, and three
things follow from it.

A test is not a record of a decision. Why the code is shaped this way is in
[13-decision-log.md](13-decision-log.md), and a test written to enforce a decision usually pins the
decision rather than the behavior it produced.

A test is not a description of a mechanism. If a test can only fail when someone deliberately
rewrites an internal step, it is describing how the code works rather than what it does, and it will
have to be rewritten every time the step changes for a reason nobody disagrees with.

A test is not a checklist of branches. Coverage is a report, not a target
([section 10](#10-coverage)).

### The names are the description

A test name says what state the code is in, what happens to it, and what results. Read one module's
test names in order, and the reading has to describe what that module does. That is the only check
this document places on whether the tests are at the right level, and it is enough, because both
ways of getting it wrong show up in the reading.

| Reading | Means |
| --- | --- |
| Two names saying the same thing in different words | The tests are split by branch where the behavior is one |
| A name that cannot be understood without the body | The name describes a step rather than an outcome |
| A name that could be a sentence in `design/` | Right level |

One invariant split four ways is the shape to watch for. A column check that never writes to the
records it reads is one behavior, whether the check passed, failed on the first table, or failed on
the fourth:

```python
# Four tests where the module has one thing to say
def test_a_rejected_coercion_leaves_the_records_untouched(): ...
def test_an_accepted_coercion_leaves_the_records_untouched(): ...
def test_a_later_column_failing_writes_nothing_from_an_earlier_one(): ...
def test_a_failure_in_a_later_table_writes_nothing_from_an_earlier_table(): ...

# One test, parametrized over the four inputs
def test_the_column_check_never_writes_to_the_records_it_reads(): ...
```

### One test, one outcome

A test asserts one outcome a reader can state in one sentence. Several assertions are fine when they
describe one outcome: a failed result's status and the reason it carries are one sentence. Two
outcomes are two tests, or a sign that the code under test does two things.

### The body says what; a helper says how

A test body reads as the sentence its name promises. Building a plan of six nodes, a router holding
three operators, and a scripted client with four responses queued is not that sentence, so it goes
into a helper or a fixture. What stays in the body is the input that makes this case this case, the
call, and the assertion.

A helper is named for what it produces — `a_plan_with_two_independent_branches`, `a_cover_fusing`
— rather than for the steps it runs, so a test body still reads as a sentence after the helper is
factored out.

### Comments

A comment says why an expected value is that value, when that is not obvious from the test. An
expected count that equals a bound deserves one line; a returned status does not.

### What makes tests multiply

| Shape | Instead |
| --- | --- |
| Two tests differing only in an input value | One test parametrized over the values |
| One test per branch of one function | One test per outcome the caller can observe |
| One test per bullet in a design document | One test per behavior the module promises |
| One test per rejection reason of one validator | One test that invalid plans are rejected, parametrized over what makes each invalid, plus one that the error names what was wrong |
| A test asserting that a call was made | Assert the result the call produced, unless making the call is the behavior, as when an operator asks the registry for a source |

---

## 2. Tiers

| Tier | Path | Runs by default | Needs a provider |
| --- | --- | --- | --- |
| Library unit tests | `lib/tests/unit/` | Yes | No |
| Adapter tests | `platform/*/tests/` | Yes, in that package | No |
| LOTUS suite tests | `lib/tests/lotus/` | No, excluded by `testpaths` | Yes, for an operation that calls a model |
| End-to-end tests | `lib/tests/end_to_end/` | No, excluded by `testpaths` | Yes |

Unit tests are the main tier and the one that has to stay fast and offline, so a component that
writes prompts is tested against a scripted model client ([section 5](#5-doubles-and-stubs)). The
end-to-end tier plans one real request through a real provider, which is why it is opt-in by path.
The benchmark is no tier at all: it is a script, and [section 8](#8-the-benchmark) says what it
measures and how it runs
([07-repo-organization.md](07-repo-organization.md#where-the-benchmark-lives)).

The LOTUS tier answers what a stub cannot: whether the adapter calls the operations an installed
LOTUS offers, with the arguments they take, and whether the tables they return convert back to
rows. It runs in an environment carrying the `lotus` extra, which is why it is opt-in by path like
the end-to-end tier. Every other tier passes with LOTUS absent (ADR-004).

There is no integration tier against a live networked backend. A source that runs embedded is tested
against a real instance instead of a double ([section 5](#5-doubles-and-stubs)); a source reached
over a network is not, since covering it needs a running instance and a tier allowed to be slow. That
is a real gap and it is named as one in [section 9](#9-what-is-not-covered).

---

## 3. Tooling

| Tool | Purpose | Declared |
| --- | --- | --- |
| `pytest` | Test runner | `dev` extra, every package |
| `pytest-asyncio` | Async tests, `asyncio_mode = "auto"` | `dev` extra |
| `pytest-cov` | Coverage, reported per run | `dev` extra |
| `ruff` | Lint and import order, line length 100 | `dev` extra |
| `pyright` | Types, strict mode | `dev` extra |

All gates pass before work is reported as complete, in every package. A pyright error is a build
failure, not a suggestion.

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

---

## 4. Layout

`lib/tests/unit/` mirrors `lib/src/blue_data_planner/` directory for directory, so the test for
`physical_planner.py` is `tests/unit/test_physical_planner.py` and the test for `plan/validation.py`
is `tests/unit/plan/test_validation.py`. It mirrors
`lib/scripts/` too, which is why `tests/unit/scripts/tag_bench/` exists. Five directories sit beside
it and mirror nothing.

```text
lib/tests/
  conftest.py           # fixtures every tier shares
  doubles/              # in-memory replacements for I/O boundaries
  stubs/                # deterministic fakes for heavy optional dependencies
  fixtures/             # recorded inputs
  unit/                 # mirrors src/blue_data_planner/ and scripts/
    types/  plan/  data/  llm/  operators/  execution/  scripts/
  lotus/                # against an installed LOTUS; excluded from the default run
  end_to_end/           # one real request through a provider; excluded from the default run
```

Every directory under `unit/` carries an `__init__.py`, so pytest does not confuse two test modules
that share a filename across directories.

Each test carries a docstring naming the case it covers, in the same terms as its name. A name and a
docstring that disagree mean one of them is describing a step.

---

## 5. Doubles and stubs

The two serve different purposes and live apart.

| Directory | Replaces | Why |
| --- | --- | --- |
| `doubles/` | An I/O boundary this repository owns | So a test runs offline against real behavior |
| `stubs/` | A heavy optional dependency | So the default suite runs without it installed |

Doubles are injected through a fixture. Stubs are put on the import path, and on pyright's
resolution path through `extraPaths`, so both the test run and the type check see them.

### Doubles

Three doubles serve the planner.

| Double | Stands in for | Lets a test |
| --- | --- | --- |
| A scripted model client | `LLMClient` | Hand a component an exact sequence of plans, covers, parameters, and reviews |
| A recording physical operator | A `PhysicalOperator`'s callable | Assert what execution passed a fragment, and return a chosen result or failure |
| A fake per injected component | `LogicalPlanner`, `PhysicalPlanner`, `FragmentRunner`, `CatalogSearcher` | Drive the run loop's scheduling, its repair loops, and cover installation from returned results alone, with no model and no operator |

The scripted client is what makes planner tests deterministic and what keeps them at the level
section 1 asks for. A test of the parameter repair scripts a review that rejects and then one that
accepts, and asserts that the accepted result is the one the run kept and that both turns are in the fragment's conversation. No provider is involved, no prompt
is under test, and nothing in the test names a step the runner takes in between.

Two boundaries are not doubled. A source that runs embedded is tested against a real instance
instead: a test of a source read, a source write, or a bind join runs against it and checks what
happened there — a table written, a table dropped again, the rows a query returned. The registry is
not doubled either: a `blue-data` registry runs in memory over records a test registers, and `data/`
is what a test of the catalog readings is checking, so a double in its place would pin only itself. A
test builds the registry it needs and reads it through `DataCatalog`.

### Stubs

`stubs/` holds `litellm` and LOTUS, and each is installed before anything reaches its lazy import.
The LOTUS stub registers the same data frame operations the suite does and returns scripted frames,
so the adapter's whole path runs with LOTUS absent. A stub stands in for the tiers that run by
default; the LOTUS tier reaches the installed package.

---

## 6. What each component's tests have to show

One line per component, at the level of what that component promises a caller. This is not a
checklist: a row is one sentence, and the tests under it are the few that make the sentence true.
What each component does in detail is in [06-library.md](06-library.md), and repeating it here would
produce one test per line of that document.

| Component | What its tests have to show |
| --- | --- |
| `types/`, `plan/` | An invalid plan is rejected whatever makes it invalid with an error naming what was wrong, a plan that `to_dict` produced reads back through `from_dict` unchanged, the node sets an operator could cover are enumerated from the plan alone, and two pieces of identical work sign the same while any change that would change an output — a change upstream included — signs differently |
| `LogicalPlanner` | A plan a validation turned down is written again with the error in hand, running out of corrections ends as `invalid_plan` carrying the objection that stopped it, and a continuation writing a carried node's id is turned down |
| `PhysicalPlanner` | A cover cuts the plan into legal fragments, binds each scan to an entity the run's catalog context names and each fragment to the nodes feeding it, refuses a scan bound to any other path, lets a node whose fragment already produced a result keep that fragment unchanged, carries the cover over without a model call when every node in the plan already ran, and comes back with no cover and a reason naming the node when no operator can take one or when the binding turns down every candidate, without an operator call |
| `FragmentRunner` | A fragment runs with parameters written from what its input tables carry and from the attributes of the entities it scans, a rejected review writes them again, and what the review accepted comes back with the result while every attempt stays in the conversation whatever ends the run |
| `CatalogSearcher` | A `DISCOVER` node resolves to the entities its terms found, each carrying its attributes, capped, and reports the terms it dropped |
| `OperatorRouter` | An operator is found by each logical operator it declares, in registration order |
| `execution/` | An operator call comes back as a result carrying its rows or its error and its kind, billed once against the budget, and checked against the output columns its caller declared; the answer holds the columns the ending names and refuses a name the rows do not carry |
| `DataPlanner`, `RunState` | Each of the three repairs is reachable and counts against its own bound, a bound spent inside one loop starts again when the loop outside it turns, a result survives a cover that asks for the same work and is unreadable once the work above it changed, an ending the finished rows cannot answer is written again rather than ending the run, every `RunStatus` comes back with counters and a reason that explain it, and a defect ends the request as a status rather than raising at the caller |
| `data/` | Each catalog reading ranks and caps rather than filters, carries what its component is entitled to and nothing more, a source or attribute nobody enriched still renders, and a record that cannot be read as one raises; a source read returns the rows a query produced, a source write changes what the source holds, and a source that could not be reached is told from one that refused what it was sent |
| `llm/` | A conversation keeps its turns and the usage of every call made on it, and a provider failing after retries raises `LLMError` |
| `operators/` | An operator's declaration reaches the router intact and describes what the operation does, cross-checked against the suite's own attribute declarations and against the operation's validator wherever those two disagree (ADR-010), a bundle crosses in both directions unchanged including a multi-table bundle (ADR-012), a bundle of the wrong length is refused before the operation runs, a suite's failure is mapped to the right error kind, what an operation reported about its own run reaches the result whether the call succeeded or failed, and LOTUS is imported only when an operator runs, so the package imports with LOTUS absent (ADR-004) |
| `operators/builtin/` | Each operation transforms the bundle as its parameters say and refuses input it cannot serve, `builtin.scan` reads the one source its fragment bound it to, carries a reference input's values into that read and leaves nothing behind in the source when it does, and `builtin.nl2table` reads its fragment's sources before its program runs, writes the program again when it will not run, and reports every program it wrote |

The run loop's row is the one worth reading twice. The three repairs are what the design is for, and
a test naming each one and the counter it spends is the shortest description of the loop that anyone
can check ([04-detailed-architecture.md](04-detailed-architecture.md#5-recovering-from-a-failure)).

One behavior spans all four components and is tested in each: the turn opening a conversation is the
same for two runs asking different things. That is the whole of what the caching rule promises
([04](04-detailed-architecture.md#one-conversation-per-component)), and the thing a later prompt edit
would otherwise break silently.

---

## 7. Adapter tests

A platform package tests translation and nothing else: argument parsing, response shape, error
mapping, exit codes. Planning and execution are the library's tests, not repeated here.

| Adapter | Tested for |
| --- | --- |
| MCP | Each tool's argument schema; the response shape, including `conversations` collapsed to calls and cost; a run that did not finish returning success with a status in the payload; an unknown tool and a malformed argument returning `isError` |
| MCP threading | A tool that runs a plan goes through `asyncio.to_thread` rather than blocking the event loop |
| CLI | Argument and option parsing through Click's `CliRunner`; a profile's parameters resolved in the documented order, with a secret masked rather than printed; the result on standard output and everything else on standard error, with `--format` deciding how the result is written and `--log` what is reported about the run; each error code and each of the three exit codes |

Each platform package keeps its own doubles under `tests/doubles/`: `lib/tests/` ships in no
package, and a platform package's tests resolve imports against its own root. An adapter test
replaces the library's entry point with a fake planner that returns a prepared result, so no
adapter test reaches a model.

The MCP threading test earns its place. Getting `asyncio.to_thread` wrong there stalls the transport
for a whole session, and it is the one place in the repository where async meets synchronous code.

---

## 8. The benchmark

`lib/scripts/tag_bench/benchmark/` measures the system against TAG-Bench. The target is 60% exact
match over the Match, Comparison, and Ranking queries, 48 of the test split's 64. The aggregation
queries are left out of the benchmark: each one asks for a summary and carries an empty gold answer,
so no exact-match score exists for it
([10-data-model.md](10-data-model.md#6-benchmark-inputs-and-runs)).

It is a script rather than a test (ADR-031). A run spends paid model calls for as long as its
selection takes, so a person starts it, watches the line it prints as each query finishes, and reads
a number at the end.

Its inputs come from `data/tag_bench/`, built by the three scripts beside it, which download a
release and correct it rather than editing the download in place
([10-data-model.md](10-data-model.md#6-benchmark-inputs-and-runs)). A run writes one record per
query and one summary over them, both structured data, and the summary reports what the design still
needs measured.

| Reported | Answers |
| --- | --- |
| Exact match against the gold answers, overall and per query type | Whether the system meets the 60% target, and which kind of question it misses |
| Operator calls per query, and model calls per component | Whether `RunLimits`'s defaults are near the truth ([12-deployment-ops.md](12-deployment-ops.md#5-request-limits)) |
| How often a run replans, and how many covers one logical plan needed | Whether planning is two-pass in practice, and which repair is doing the work |
| How large a fragment the physical planner draws | Whether the pushdown boundary lands where the design expects |
| How each run ended, and its failure reasons grouped | Which defect to fix next, since a status names the loop that ran out |

The rows past the first are the reason the benchmark is part of the testing strategy rather than a
side project: they are design questions the decision log records as open, and none can be settled by
reasoning about the code.

The review's accept rate, which ADR-013 flags as the risk worth watching, is counted off `results`:
a fragment the review turned down is filed with the rest, under its own status. How often a
parameter repair fires is not, because the repair is a loop inside one fragment run and only its
ending is filed; that count lives in the fragment runner's conversation as turns.

---

## 9. What is not covered

Naming a gap is more useful than implying it is covered.

| Not covered | Why, and what it would take |
| --- | --- |
| A live networked backend | A source that runs embedded is tested against a real instance instead ([section 5](#5-doubles-and-stubs)); one reached over a network needs a running instance and a tier allowed to be slow |
| The import rules | The six rules in [08-coding-style.md](08-coding-style.md) are checked in review. A test that parses imports would assert a rule rather than a behavior, which is what section 1 rules out |
| Prompt quality | Whether a prompt draws a usable answer out of a real model is what the benchmark measures, against gold answers ([section 8](#8-the-benchmark)). A model changes under a fixed prompt, so an assertion written against one answer would say nothing about the next |
| LOTUS itself, and the Blue operators | Each adapter is tested; a suite is a dependency and tests its own behavior |
| Concurrency | The library is synchronous and runs one fragment at a time. A caller running several requests at once is running several threads, which is the caller's concern |
| Cost | Model spend is reported per call in `RunResult.conversations` and asserted nowhere, since a threshold would fail on a price change rather than on a regression |

---

## 10. Coverage

Coverage is measured and reported, and no minimum is enforced. `lib/pyproject.toml` sets
`addopts = "--cov=src --cov-report=term-missing"`, so every run prints it.

A threshold would be the wrong instrument, and a harmful one here. Chasing a percentage is the
fastest way to produce the tests section 1 rules out: one per branch, named after a step, asserting
that the code did what the code does. What the number would be chasing is already named in
[section 6](#6-what-each-components-tests-have-to-show), and a module can reach any percentage
without demonstrating a single line of it.

---

## 11. Running the tests

```bash
# Library unit tests: fast, offline, no API key
cd lib && uv run pytest

# One module
cd lib && uv run pytest tests/unit/test_physical_planner.py

# The MCP adapter
cd platform/mcp && uv run pytest

# The LOTUS tier, in an environment carrying the lotus extra
cd lib && uv run pytest tests/lotus

# The end-to-end tier, named by path because testpaths excludes it, against a real provider
cd lib && uv run pytest tests/end_to_end
```

The benchmark is a script rather than a test ([section 8](#8-the-benchmark)), so `pytest` does not
run it:

```bash
cd lib && export UV_PROJECT_ENVIRONMENT=.venv-lotus
uv run --extra lotus --extra dev python -m scripts.tag_bench.benchmark run --split dev
```
