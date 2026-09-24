# blue-data-planner — Repo Organization

This document fixes where every file belongs and where a new one goes. It follows the skeleton in
[repo-organization-template.md](repo-organization-template.md), which the `blue-data` and
`blue-data-kb-source` repositories also follow.

The module map in [06-library.md](06-library.md) says what each module holds. This document says
where each one sits and why.

---

## Target tree

```text
blue-data-planner/
├── LICENSE
├── README.md
├── AGENTS.md                          # canonical instructions; .claude/CLAUDE.md symlinks to it
├── design/                            # numbered design documents and the decision log
├── docs/
│   └── internal/                      # background notes, git-ignored
├── data/                              # benchmark and demo inputs, fetched and built, not committed
│   ├── tag_bench/
│   └── demo/                          # Northwind, fetched from blue-data, and the registry over it
├── examples/                          # runnable examples that use lib/
├── lib/
│   ├── pyproject.toml
│   ├── scripts/
│   │   ├── build.sh
│   │   ├── install.sh              # installs the package into a local environment
│   │   ├── publish.sh
│   │   ├── tag_bench/
│   │   │   ├── fetch_queries.py       # reads a release and writes corrected queries
│   │   │   ├── fetch_databases.py     # downloads the five BIRD databases
│   │   │   ├── register_databases.py  # builds one registry over them and serializes it
│   │   │   └── benchmark/             # runs the queries, scores the answers, reports a run
│   │   └── demo/
│   │       ├── fetch_northwind.py     # reads blue-data's pinned revision and fetches its seed
│   │       └── register_northwind.py  # builds the database and the registry the examples use
│   ├── src/blue_data_planner/
│   │   ├── __init__.py                # __version__ and the run path
│   │   ├── data_planner.py            # DataPlanner, build_data_planner
│   │   ├── settings.py                # what a planner is built from
│   │   ├── run_state.py               # RunState
│   │   ├── logical_planner.py         # LogicalPlanner
│   │   ├── physical_planner.py        # PhysicalPlanner
│   │   ├── fragment_runner.py         # FragmentRunner
│   │   ├── catalog_searcher.py        # CatalogSearcher
│   │   ├── prompts/                   # hand-written, versioned prompt text
│   │   ├── types/                     # every data class crossing a module boundary
│   │   │   ├── __init__.py            # re-exports the four modules as blue_data_planner.types
│   │   │   ├── errors.py              # BlueDataPlannerError and everything inheriting it
│   │   │   ├── plan.py                # rows, the logical vocabulary, the two plan halves
│   │   │   ├── catalog.py             # the three catalog readings
│   │   │   └── run.py                 # results, counters, conversations, limits
│   │   ├── plan/                      # functions over a plan; no model, no source, no operator
│   │   │   ├── validation.py          # validate_logical_plan, validate_physical_plan
│   │   │   ├── fragments.py           # candidate_fragments
│   │   │   └── schemas.py             # the response schemas the planning stages send
│   │   ├── execution/
│   │   │   ├── physical_operator.py   # execute_physical_operator, the one operator call site
│   │   │   ├── output_columns.py      # check_output_columns and its coercions
│   │   │   └── return_answer.py       # project_answer, the columns a RETURN names
│   │   ├── operators/                 # the router, one adapter per outside suite, and the owned suite
│   │   │   ├── __init__.py            # OperatorRouter only
│   │   │   ├── router.py              # OperatorRouter
│   │   │   ├── adapters/              # one per outside suite
│   │   │   │   ├── blue/              # blue_operators, over blue-data-ops
│   │   │   │   └── lotus/             # lotus_operators, over LOTUS
│   │   │   └── builtin/               # builtin_operators, the suite written here
│   │   ├── data/
│   │   │   ├── registry.py            # open_registry, the source-level reads
│   │   │   └── catalog.py             # DataCatalog: three readings, shapes from types/
│   │   └── llm/
│   │       ├── client.py              # LLMClient, Conversation
│   │       ├── provider.py
│   │       ├── templating.py
│   │       └── errors.py
│   └── tests/
│       ├── conftest.py
│       ├── doubles/                   # in-memory replacements for I/O boundaries
│       ├── stubs/                     # deterministic fakes for heavy optional dependencies
│       ├── fixtures/                  # recorded inputs
│       ├── unit/                      # mirrors src/blue_data_planner/ and scripts/
│       ├── lotus/                     # against an installed LOTUS; excluded from the default run
│       └── end_to_end/                # one real request through a provider; excluded by default
└── platform/
    ├── cli/
    │   ├── README.md
    │   ├── pyproject.toml              # blue-planner = commands:main
    │   ├── scripts/
    │   │   ├── build.sh
    │   │   └── install.sh
    │   ├── src/blue_data_planner_cli/
    │   │   ├── __init__.py             # __version__, read by --version
    │   │   ├── commands.py             # the data, plan, and profile groups
    │   │   ├── config.py               # resolved settings, and the planner they assemble
    │   │   ├── profile.py              # the profile actions
    │   │   ├── profile_spec.json       # the parameters a profile holds
    │   │   ├── output.py               # tables, result JSON, error lines
    │   │   └── trace.py                # what a run did, as records and as a table
    │   └── tests/
    └── mcp/
        ├── README.md
        ├── pyproject.toml              # blue-data-planner-mcp = server:main
        ├── scripts/
        │   ├── build.sh
        │   └── install.sh
        ├── src/blue_data_planner_mcp/
        │   ├── __init__.py             # __version__
        │   ├── server.py               # FastMCP, query_data, main
        │   └── config.py               # what the environment supplies, and the planner it builds
        └── tests/
```

`platform/api/` is reserved for a REST API and is not created, since none is in the first release
([03-high-level-architecture.md](03-high-level-architecture.md)).

---

## What each top-level directory owns

| Directory | Owns | Published |
| --- | --- | --- |
| `design/` | The numbered design documents and the decision log | Yes, in the repository |
| `docs/` | Documentation for readers outside the project. `docs/internal/` holds notes and links and is git-ignored | Partly |
| `data/` | Benchmark inputs, downloaded and built rather than committed | No |
| `examples/` | Runnable examples that import `lib/` and demonstrate one feature each | Yes |
| `lib/` | All planning behavior. The `blue-data-planner` package | Yes, to PyPI |
| `platform/` | One directory per external interface, each independently built and versioned | Yes, to PyPI |

The division that matters is between `lib/` and `platform/`. `lib/` owns behavior; a platform
package parses requests, formats responses, maps errors, and loads configuration. Planning, operator
selection, and execution never live in an adapter, and an adapter imports only the public surface
(ADR-002).

A second division cuts through `lib/` itself. Only `lib/src/` is packaged, so `lib/scripts/` and
`lib/tests/` are build steps and measurements rather than shipped code, which is what lets the
benchmark and demo scripts hold an import the library does not allow itself.

---

## Inside `lib/src/blue_data_planner/`

| Path | Grouping principle |
| --- | --- |
| `data_planner.py`, `run_state.py` | The entry point and the state its loop writes. At the root because a package that is a planner has no inner level to put them in |
| `logical_planner.py`, `physical_planner.py`, `fragment_runner.py` | The three planning components (ADR-006), one module each, beside the loop that drives them |
| `catalog_searcher.py` | The discovery step. Beside the planning components rather than one of them: it resolves a `DISCOVER` node and writes no plan |
| `prompts/` | Prompt text, one file per component that writes prompts, read by those components and written nowhere else (ADR-005) |
| `types/` | Every data class that crosses a module boundary, the catalog readings and `LLMUsage` included, since a type `RunResult` names has to be nameable by everything that reads one. Its four modules split those classes by subject, and `__init__.py` re-exports them under the one name `blue_data_planner.types`. Imports nothing outside itself |
| `plan/` | The functions over a plan that need no model, no source, and no operator: checking one, enumerating the fragments an operator could cover, and building the response schema a planning stage sends. Imports only `types/` |
| `execution/` | Beside the planning components rather than inside one, so invoking an operator, checking what it returned, and projecting the answer all need no model |
| `operators/` | The router, one adapter per outside operator suite, and `builtin/`, the suite written here (ADR-020) |
| `data/` | The only packaged module that imports `blue-data` (ADR-003) |
| `llm/` | The only module that calls a model provider (ADR-005) |

### Why the planning components sit at the package root

`data_planner.py`, `run_state.py`, the three planning components, and the discovery step are root
modules, and
there is no `assembly.py`.

A level grouping them would have to be named for planning, and this package is a planner, so the
name would separate nothing: it would appear in every import a consumer writes while distinguishing
none of them from any other. The five directories that remain each name something a reader has to
tell apart from the rest — a data shape, a plan checked without running it, one operator call, an
operator suite, a data source, a model provider.

Wiring has no module of its own for the same reason. `build_data_planner` takes resolved values
and constructs what a run needs — the model client, the router, each suite's operators, the catalog,
the three planning components, and the discovery step — so it names the same things `DataPlanner`
does and belongs in the same file (ADR-002).

### What the root exports

`__init__.py` holds `__version__` and the run path: `DataPlanner`, `build_data_planner`,
`PlannerSettings`, `build_data_planner_from_settings`, `RunResult`, `RunLimits`, `RunStatus`, and
`BlueDataPlannerError`. A caller who runs a planner and reads its result writes one import.

Everything else is reached through the module that owns it. The components a caller assembles by
hand come from `operators`, `data`, and `llm`; the shapes a caller reads off a plan come from
`types`. That split is what keeps `import blue_data_planner` from loading an operator suite:
nothing on the run path imports an adapter, and a required suite is loaded only when its adapter
module is.

[09-api-interface-spec.md](09-api-interface-spec.md) fixes the public surface and what semantic
versioning covers.

### What `operators/` holds

The router, one adapter per outside suite, and the one suite written here. The Blue operator library
and its registry belong to `blue-data-ops`, and LOTUS is its own package; `builtin/` belongs to this
repository (ADR-020).

| Module | Holds |
| --- | --- |
| `router.py` | `OperatorRouter`, the only lookup read while planning (ADR-009) |
| `adapters/blue/` | `blue_operators`, over a `blue-data-ops` `OperatorRegistry`: the parameter models and the operation table that binds them |
| `adapters/lotus/` | `lotus_operators`, over the LOTUS package |
| `builtin/` | `builtin_operators`: one module of transformations over the bundle, the deterministic entries wrapping them, `builtin.scan`, `builtin.nl2table`, and the child process its program runs in |

`builtin/` sits beside `adapters/` because the router reads every suite the same way and a
deployment turns any of them on or off the same way. It is a directory for the same reason the two
adapters are: more than one file to hold.

The router sits here rather than beside the planning components because it is a lookup over
operators and names nothing from any of them, and because an adapter can then register into it directly. It is not the
registry it sits beside: `blue-data-ops`'s `OperatorRegistry` is a versioned catalog of Blue
operator classes with ids like `select@2.0.0`, and the router answers which operators from any suite
declare a given logical operator, with ids like `blue.select` (ADR-009).

`__init__.py` re-exports `OperatorRouter` alone. An adapter is imported from `adapters/`, under the
name of its suite, so importing the router loads no suite. `adapters/__init__.py` re-exports none
of them either, so importing one adapter loads no other suite.

An adapter is a directory rather than a module when the suite needs more than one file to wrap,
which is why both adapters are packages. Grouping by logical operator was rejected, because an
operator may declare several and the router already answers that question.

### What a platform package holds

Two modules and a console script, which is the shape both sibling repositories settled on.

| File | Holds |
| --- | --- |
| `commands.py` or `server.py` | What the interface parses and which library call each command reaches, and the entry point the console script names |
| `config.py` | The settings in force, resolved once, and the planner they assemble |
| `profile.py`, `profile_spec.json`, `output.py` | The CLI's three subjects that are not command routing: the profile actions, the parameters a profile holds, and how a result is rendered |
| `pyproject.toml` | The package, and the console script under `[project.scripts]` |
| `scripts/` | `build.sh` and `install.sh`. Publishing is a tag in CI ([12-deployment-ops.md](12-deployment-ops.md)), so no platform package carries a `publish.sh` |

The CLI has no `commands/` package. Three command groups fit in one module, which is what the
sibling Blue CLIs do with more groups than that.

The MCP package has no module for wire shapes or for tool registration. One tool and one planner
fit beside the bootstrap, and a suite that exposes one tool per operator — which this repository
does not ([09-api-interface-spec.md](09-api-interface-spec.md#3-mcp-server)) — is what makes those
worth splitting out.

One `LICENSE` sits at the repository root and covers everything published from it.

### Where a new thing goes

| Adding | Goes | Also needs |
| --- | --- | --- |
| An operator | `blue-data-ops`, or the suite's own package, unless it is deterministic and belongs to no suite | An adapter entry, if a plan should be able to use it |
| A built-in operation | `operators/builtin/`, as a function over the bundle plus the entry that declares it | Its own tests. An owned operation has behavior, not translation ([11-testing-strategy.md](11-testing-strategy.md)) |
| An operator suite | `operators/<suite>.py`, or `operators/<suite>/` | An optional dependency extra, a lazy import, and a stub in `tests/stubs/` |
| A way to reach the library | `platform/<interface>/`, its own package | Its own `pyproject.toml` naming a console script, `scripts/`, and `tests/` |
| A logical operator | Nowhere. The vocabulary of ten is fixed ([10-data-model.md](10-data-model.md)) | A decision log entry arguing the exception |
| A check, a graph function, or a response schema over a plan | `plan/`, if it needs no model and no source | Nothing else |
| A prompt | `prompts/`, one file per component that writes prompts | Nothing else writes prompt text (ADR-005) |
| Anything reading `blue-data` | `data/`, and called from elsewhere | No other packaged module imports `blue-data` |
| Anything calling a model | `llm/`, and called from elsewhere | Nothing else calls a provider |

---

## Tests

`lib/tests/unit/` mirrors `lib/src/blue_data_planner/` directory for directory, so the test for
`physical_planner.py` is `tests/unit/test_physical_planner.py` and the test for
`plan/validation.py` is `tests/unit/plan/test_validation.py`. It mirrors `lib/scripts/` the same
way, which is why `tests/unit/scripts/tag_bench/` exists.

`doubles/`, `stubs/`, `fixtures/`, `lotus/`, and `end_to_end/` sit beside `unit/` and mirror
nothing. `lotus/` holds the tier that runs against an installed operator suite, so it is run by
name, in an environment carrying the `lotus` extra. `end_to_end/` holds the one test that plans a
real request through a provider, run by name for the same reason. What each directory holds, and
what a platform package's tests cover, is in
[11-testing-strategy.md](11-testing-strategy.md#4-layout).

### Where the benchmark lives

Two directories hold the benchmark, and neither ships.

| Part | Where |
| --- | --- |
| The steps that build the inputs, and the harness that runs the queries and scores the answers | `lib/scripts/tag_bench/` |
| The queries, the databases, the registry over them, and what each run writes | `data/tag_bench/`, downloaded, built, and written rather than committed |

The build step is three scripts rather than one because the three outputs are fetched and rebuilt on
different occasions. `fetch_queries.py` reads a release and writes corrected queries beside it,
leaving the download untouched. `fetch_databases.py` downloads the five BIRD databases.
`register_databases.py` builds one `blue-data` registry over those databases and serializes it once,
so a run opens a file instead of syncing five databases again. The paths each one writes are fixed
in [10-data-model.md](10-data-model.md#6-benchmark-inputs-and-runs).

`benchmark/` sits beside those three and is a package rather than a file, because it has four
separable jobs: selecting queries, running one, scoring an answer against the gold one, and
aggregating a run. It runs as a module, in the environment carrying the `lotus` extra, and writes
one record per query as that query finishes, so a person can read a record while the rest of the run
continues, a run that stops keeps what it finished, and a finished run is triaged from what it wrote
(ADR-031).

`register_databases.py` is the one place in the repository outside `data/` that imports `blue-data`.
Registering sources, syncing them, collecting statistics, and enriching them are ingestion, which
belongs to `blue-data` rather than here, so the library offers no way to do any of it and a caller
with no registry builds one with `blue-data` first. The benchmark still needs a registry built, and
it is a build step in an unpackaged directory, so it calls `blue-data` directly rather than forcing
an ingestion surface into `data/` ([06-library.md](06-library.md#opening-a-registry)). The rule the
library holds itself to is scoped to `lib/src/` and stated that way in
[08-coding-style.md](08-coding-style.md).

The benchmark measures the system rather than being part of it. It ships in no package, and a run is
started by a person rather than by a test run.

---

## What does not live here

| Not here | Where instead |
| --- | --- |
| Agents | Other Blue repositories, or a framework such as LangChain, reaching in through MCP |
| The Blue operators, the abstract `Operator`, and the operator registry | `blue-data-ops`, reached only through `operators/`. The deterministic operations of `builtin/` are this repository's own (ADR-020) |
| Data source implementations and the registry | `blue-data`, reached only through `data/` |
| Building or populating a registry | `blue-data`'s own tooling. The benchmark script is the single exception, and it is not shipped |
| LOTUS source | Its own package, installed through the `lotus` extra (ADR-004) |
| A REST API | Reserved as `platform/api/`, not in the first release |
| A hosted operator registry | The registry and the router are in-process, built by whoever assembles the planner. `blue-data` hosts its data registry that way, and nothing here needs a remote lookup |
