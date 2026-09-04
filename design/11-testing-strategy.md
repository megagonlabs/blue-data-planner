# blue-data-planner — Testing Strategy

The bar is the Blue library standard rather than a research-script standard. The library is depended
on by other Blue components and by outside users, so its public interfaces and its failure paths
are both covered.

Two things shape everything below. The library is synchronous, so most tests are ordinary functions
with no event loop. And a request's whole outcome is one inspectable structure (ADR-015), so a test
asserts against `PlanResult` rather than against printed output.

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
([section 11](#11-coverage)).

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
| Benchmark suites | `lib/tests/benchmarks/` | No, excluded by `testpaths` | Yes, unless cassettes exist |

Unit tests are the main tier and the one that has to stay fast and offline. The benchmark tier
measures the system rather than checking it, and it is opt-in by path
([07-repo-organization.md](07-repo-organization.md#where-the-benchmark-lives)).

There is no integration tier against a live `blue-data` backend. Postgres, Redis, and the rest are
reached only through `data/`, which is doubled, so nothing here is tested against a running store.
That is a real gap and it is named as one in [section 10](#10-what-is-not-covered).

---

## 3. Tooling

| Tool | Purpose | Declared |
| --- | --- | --- |
| `pytest` | Test runner | `dev` extra, every package |
| `pytest-asyncio` | Async tests, `asyncio_mode = "auto"` | `dev` extra |
| `pytest-recording` | Records and replays provider responses | `lib/` `dev` extra |
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
`lib/scripts/` too. Four directories sit beside it and mirror nothing.

```text
lib/tests/
  conftest.py           # installs the stubs before any lazy import reaches them
  doubles/              # in-memory replacements for I/O boundaries
  stubs/                # deterministic fakes for heavy optional dependencies
  fixtures/             # recorded inputs and cassettes
  unit/                 # mirrors src/blue_data_planner/ and scripts/
    types/  plan/  data/  llm/  operators/  execution/  scripts/
  benchmarks/
    tag_bench/          # excluded from the default run
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

`doubles/data.py` holds the replacement for a live source. `SQLSourceDouble` stands in for a
returned SQL source, following the same call sequence the real one expects, so a test covering a
bind join asserts what an operator wrote rather than needing a database to write it to.

The registry itself is not doubled. A `blue-data` registry runs in memory over records a test
registers, and `data/` is what a test of the catalog readings is checking, so a double in its place
would pin only itself. A test builds the registry it needs and reads it through `DataCatalog`.

Three doubles serve the planner.

| Double | Stands in for | Lets a test |
| --- | --- | --- |
| A scripted model client | `LLMClient` | Hand a component an exact sequence of plans, covers, parameters, and reviews |
| A recording physical operator | A `PhysicalOperator`'s callable | Assert what execution passed a fragment, and return a chosen result or failure |
| A fake per injected component | `LogicalPlanner`, `PhysicalPlanner`, `FragmentRunner`, `CatalogSearcher` | Drive the run loop's scheduling, repair choice, and cover installation from returned results alone, with no model and no operator |

The scripted client is what makes planner tests deterministic and what keeps them at the level
section 1 asks for. A test of the parameter repair scripts a review that rejects and then one that
accepts, and asserts that both attempts came back in the result. No provider is involved, no prompt
is under test, and nothing in the test names a step the runner takes in between.

### Stubs

`stubs/` holds `litellm` and LOTUS. `conftest.py` installs each into `sys.modules` before anything
reaches its lazy import.

---

## 6. Recorded model responses

Cassettes cover behavior that depends on what a real model returns, which the scripted client cannot
show. Prompt shaping is exactly that: whether a prompt carrying a column's coded values gets
`PRIJEM` back rather than `credit` is a question about a model, not about this code.

| Question | Tested with |
| --- | --- |
| Does the code do the right thing given a response? | The scripted client |
| Does a real model return a usable response to this prompt? | A cassette |

Cassettes live in `lib/tests/fixtures/` and are committed. They cover the four components that write
prompts — the logical planner, the physical planner, the fragment runner's parameters and review,
and the catalog searcher — with a few requests each rather than one per prompt variation. A prompt
that changes materially means re-recording, by deleting the cassette and running with
`--record-mode=once`. A missing cassette fails the test rather than reaching the network, which is
what keeps the default run offline.

---

## 7. What each component's tests have to show

One line per component, at the level of what that component promises a caller. This is not a
checklist: a row is one sentence, and the tests under it are the few that make the sentence true.
What each component does in detail is in [06-library.md](06-library.md), and repeating it here would
produce one test per line of that document.

| Component | What its tests have to show |
| --- | --- |
| `types/`, `plan/` | An invalid plan is rejected whatever makes it invalid with an error naming what was wrong, a plan that `to_dict` produced reads back through `from_dict` unchanged, and the node sets an operator could cover are enumerated from the plan alone |
| `LogicalPlanner` | A plan a validation turned down is written again with the error in hand, and running out of corrections ends as `invalid_plan` carrying every plan that was turned down |
| `PhysicalPlanner` | A cover cuts the plan into legal fragments and binds each scan to a source the catalog names, and a node no operator can take comes back as `no_operator` without an operator call |
| `FragmentRunner` | A fragment runs with parameters written from what its inputs actually carry, a rejected review writes them again, and every attempt survives whatever ends the run |
| `CatalogSearcher` | A `DISCOVER` node resolves to what its terms found, capped, and reports the terms it dropped |
| `OperatorRouter` | An operator is found by each logical operator it declares, in registration order |
| `execution/` | An operator call comes back as a result carrying its rows or its error and its kind, billed once against the budget, and checked against the output columns its caller declared |
| `DataPlanner`, `RunState` | Each of the four repairs is reachable and counts against its own bound, installing a cover keeps the results of fragments that did not change, and every `RunStatus` comes back with counters that explain it |
| `data/` | Each catalog reading ranks and caps rather than filters, carries what its component is entitled to and nothing more, a source or column nobody enriched still renders, and a record that cannot be read as one raises |
| `llm/` | A conversation keeps its turns and the usage of every call made on it, and a provider failing after retries raises `LLMError` |
| `operators/` | An operator's declaration reaches the router intact and describes what the operation does, cross-checked against the suite's own attribute declarations and against the operation's validator wherever those two disagree (ADR-010), a bundle crosses in both directions unchanged including a multi-table bundle (ADR-012), a suite's failure is mapped to the right error kind, and LOTUS is imported only when an operator runs, so the package imports with LOTUS absent (ADR-004) |
| `operators/builtin/` | Each operation transforms the bundle as its parameters say and refuses input it cannot serve, `builtin.scan` reads the one source its fragment bound it to and carries a reference input's values into that read, and `builtin.nl2table` runs the sequence it was given and reports it, returning a recoverable failure rather than retrying when the sequence will not run |

The run loop's row is the one worth reading twice. The four repairs are what the design is for, and
a test naming each one and the counter it spends is the shortest description of the loop that anyone
can check ([04-detailed-architecture.md](04-detailed-architecture.md#5-recovering-from-a-failure)).

---

## 8. Adapter tests

A platform package tests translation and nothing else: argument parsing, response shape, error
mapping, exit codes. Planning and execution are the library's tests, not repeated here.

| Adapter | Tested for |
| --- | --- |
| MCP | Each tool's argument schema; the response shape, including `attempts` as a count and `planner_usage` collapsed to calls and cost; a run that did not finish returning success with a status in the payload; an unknown tool and a malformed argument returning `isError` |
| MCP threading | A tool that runs a plan goes through `asyncio.to_thread` rather than blocking the event loop |
| CLI | Argument and option parsing through Click's `CliRunner`, including the `--max-` flags reaching `PlanLimits`; progress on standard error and the answer on standard output; each of the four exit codes |

Adapter tests import doubles from `lib/tests/` rather than defining their own, so one scripted model
client serves every package.

The MCP threading test earns its place. Getting `asyncio.to_thread` wrong there stalls the transport
for a whole session, and it is the one place in the repository where async meets synchronous code.

---

## 9. Benchmark suites

`lib/tests/benchmarks/tag_bench/` measures the system against TAG-Bench, with a target of 60%
accuracy end to end. It is excluded from the default run by `testpaths` and runs only when named by
path.

Its inputs come from `data/tag_bench/`, built by the three scripts under `lib/scripts/tag_bench/`,
which download a release and correct it rather than editing the download in place
([10-data-model.md](10-data-model.md#6-benchmark-inputs)). A run reports accuracy plus what the
design still needs measured.

| Reported | Answers |
| --- | --- |
| Accuracy against the gold answers | Whether the system meets the 60% target |
| Operator calls per request, and model calls per component | Whether the defaults in [12-deployment-ops.md](12-deployment-ops.md) are near the truth |
| How often a plan schedules a replan | Open question 2: whether planning is two-pass in practice |
| How large a fragment the physical planner draws, and how often each repair fires | Whether the pushdown boundary lands where the design expects, and which repair is doing the work |
| The review's accept rate | Whether the review talks itself into bad results, which ADR-013 flags as the risk worth watching |

The last three are the reason the benchmark is part of the testing strategy rather than a side
project. They are design questions the decision log records as open, and none can be settled by
reasoning about the code.

---

## 10. What is not covered

Naming a gap is more useful than implying it is covered.

| Not covered | Why, and what it would take |
| --- | --- |
| A live `blue-data` backend | Everything reaches storage through `data/`, which is doubled. Covering it needs a running Postgres or Redis and a tier that is allowed to be slow |
| The import rules | The six rules in [08-coding-style.md](08-coding-style.md) are checked in review. A test that parses imports would assert a rule rather than a behavior, which is what section 1 rules out |
| Prompt quality | A cassette shows that one prompt got one response. Whether a prompt is good is what the benchmark measures |
| LOTUS itself, and the Blue operators | Each adapter is tested; a suite is a dependency and tests its own behavior |
| Concurrency | The library is synchronous and runs one fragment at a time. A caller running several requests at once is running several threads, which is the caller's concern |
| Cost | Model spend is reported per call in `PlanResult.planner_usage` and asserted nowhere, since a threshold would fail on a price change rather than on a regression |

---

## 11. Coverage

Coverage is measured and reported, and no minimum is enforced. `lib/pyproject.toml` sets
`addopts = "--cov=src --cov-report=term-missing"`, so every run prints it.

A threshold would be the wrong instrument, and a harmful one here. Chasing a percentage is the
fastest way to produce the tests section 1 rules out: one per branch, named after a step, asserting
that the code did what the code does. What the number would be chasing is already named in
[section 7](#7-what-each-components-tests-have-to-show), and a module can reach any percentage
without demonstrating a single line of it.

---

## 12. Running the tests

```bash
# Library unit tests: fast, offline, no API key
cd lib && uv run pytest

# One module
cd lib && uv run pytest tests/unit/test_physical_planner.py

# The MCP adapter
cd platform/mcp && uv run pytest

# The benchmark tier, named by path because testpaths excludes it
cd lib && uv run pytest tests/benchmarks/tag_bench

# Re-record cassettes after a prompt changes materially
cd lib && uv run pytest tests/unit --record-mode=once
```
