# blue-data-planner — Repo Organization

This document fixes where every file belongs and where a new one goes. It follows the common Blue
repository structure in `docs/internal/blue_oss/repo-organization-design.md` and the skeleton in
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
├── data/
│   └── tag_bench/                     # benchmark inputs, downloaded and built, not committed
├── examples/                          # runnable examples that use lib/
├── lib/
│   ├── LICENSE
│   ├── pyproject.toml
│   ├── scripts/
│   │   ├── build.sh
│   │   ├── publish.sh
│   │   └── tag_bench/
│   │       ├── fetch_queries.py       # reads a release and writes corrected queries
│   │       ├── fetch_databases.py     # downloads the five BIRD databases
│   │       └── register_databases.py  # builds one registry over them and serializes it
│   ├── src/blue_data_planner/
│   │   ├── __init__.py                # __version__ only (ADR-016)
│   │   ├── types/                     # every data class crossing a module boundary
│   │   │   ├── __init__.py            # re-exports the four modules as blue_data_planner.types
│   │   │   ├── errors.py              # BlueDataPlannerError and everything inheriting it
│   │   │   ├── plan.py                # rows, the logical vocabulary, the two plan halves
│   │   │   ├── catalog.py             # the three catalog readings
│   │   │   └── run.py                 # results, attempts, budgets, limits
│   │   ├── validation.py              # validate_logical_plan, validate_physical_plan
│   │   ├── planner/
│   │   │   ├── run_state.py           # RunState
│   │   │   ├── data_planner.py        # DataPlanner, build_data_planner
│   │   │   ├── logical_planner.py     # LogicalPlanner
│   │   │   ├── physical_planner.py    # PhysicalPlanner
│   │   │   ├── fragment_runner.py     # FragmentRunner
│   │   │   ├── catalog_searcher.py    # CatalogSearcher
│   │   │   ├── operator_router.py     # OperatorRouter
│   │   │   └── prompts/               # hand-written, versioned prompt text
│   │   ├── execution/
│   │   │   ├── physical_operator.py   # execute_physical_operator, the one operator call site
│   │   │   └── output_columns.py      # check_output_columns and its coercions
│   │   ├── operators/                 # one adapter per operator suite; no operator is written here
│   │   │   ├── blue.py                # blue_operators, over blue-data-ops
│   │   │   └── lotus/                 # lotus_operators
│   │   ├── data/
│   │   │   ├── provider.py            # DataRegistryProvider
│   │   │   ├── registry_client.py     # DataRegistryClient, open_registry
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
│       ├── fixtures/                  # recorded inputs and cassettes
│       ├── unit/                      # mirrors src/blue_data_planner/ and scripts/
│       └── benchmarks/
│           └── tag_bench/             # excluded from the default pytest run
└── platform/
    ├── cli/
    │   ├── LICENSE
    │   ├── README.md
    │   ├── pyproject.toml
    │   ├── scripts/
    │   ├── src/blue_data_planner_cli/
    │   │   └── commands/
    │   └── tests/
    └── mcp/
        ├── LICENSE
        ├── README.md
        ├── pyproject.toml
        ├── scripts/
        ├── src/blue_data_planner_mcp/
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

The division that matters is between `lib/` and `platform/`. `lib/` owns behavior; a platform package
parses requests, formats responses, maps errors, and loads configuration. Planning, operator
selection, and execution never live in an adapter, and an adapter imports only the public surface
(ADR-002).

A second division cuts through `lib/` itself. Only `lib/src/` is packaged, since
`[tool.setuptools.packages.find]` looks nowhere else. `lib/scripts/` and `lib/tests/` are therefore
build steps and measurements rather than shipped code, which is what lets the benchmark scripts hold
an import the library does not allow itself.

---

## Inside `lib/src/blue_data_planner/`

| Path | Grouping principle |
| --- | --- |
| `types/`, `validation.py` | At the package root because everything may import them. `types/` imports nothing outside itself, and `validation.py` only `types/` (ADR-011). Every data class that crosses a module boundary lives in `types/`, the catalog readings and `LLMUsage` included, since a type `PlanResult` names has to be nameable by everything that reads one. Its four modules split those classes by subject, and `__init__.py` re-exports them under the one name `blue_data_planner.types` |
| `planner/` | The three planning stages, the discovery step and the router they read, the run state they write, and the entry point that drives all of it |
| `execution/` | Beside `planner/` rather than inside it, so invoking an operator needs no model |
| `operators/` | One module per operator suite, holding the adapter that presents it to the planner |
| `data/` | The only packaged module that imports `blue-data` (ADR-003) |
| `llm/` | The only module that calls a model provider (ADR-005) |

### Why the entry point sits in `planner/`

`DataPlanner` and `build_data_planner` live in `planner/data_planner.py`, and `RunState` in
`planner/run_state.py`. There is no `assembly.py` and nothing but `types/` and `validation.py` at
the package root.

The diagram in [03-high-level-architecture.md](03-high-level-architecture.md) draws `DataPlanner`
outside the `planner/` box, which invites the opposite conclusion. That diagram is about which
component reads which, not about directories, and the same diagram puts the run state inside the box.
Reading `planner/` as "the stages" rather than as "everything that turns a request into an answer" is
a choice about the package, and the second reading is the one that keeps the entry point beside the
loop it runs and the state that loop writes.

Wiring has no module of its own for the same reason. `build_data_planner` receives the router, the
catalog, and the model client and constructs only the four stages, so it names the same things
`DataPlanner` does and belongs in the same file (ADR-002).

### Why `operators/` holds adapters only

No operator is written here. The Blue operator library and its registry belong to `blue-data-ops`,
and LOTUS is its own package; both are dependencies. What this repository owns is the translation
between a suite's own declarations and the one shape physical planning reads, so `operators/` splits
by suite and holds nothing else.

| Module | Holds |
| --- | --- |
| `blue.py` | `blue_operators`, over a `blue-data-ops` `OperatorRegistry` |
| `lotus/` | `lotus_operators`, over the LOTUS package |

An adapter is a directory rather than a module when the suite needs more than one file to wrap, which
is why `lotus/` is a package and `blue.py` is not. Grouping by logical operator was rejected, because
an operator may declare several and the router already answers that question.

### Where a new thing goes

| Adding | Goes | Also needs |
| --- | --- | --- |
| An operator | Nowhere here. `blue-data-ops`, or the suite's own package | A pin bump, and an adapter entry if a plan should be able to use it |
| An operator suite | `operators/<suite>.py`, or `operators/<suite>/` | An optional dependency extra, a lazy import, and a stub in `tests/stubs/` |
| A way to reach the library | `platform/<interface>/`, its own package | Its own `pyproject.toml`, `scripts/`, and `tests/` |
| A logical operator | Nowhere. The vocabulary of ten is fixed ([10-data-model.md](10-data-model.md)) | A decision log entry arguing the exception |
| A prompt | `planner/prompts/`, one file per stage | Nothing else writes prompt text (ADR-005) |
| Anything reading `blue-data` | `data/`, and called from elsewhere | No other packaged module imports `blue-data` |
| Anything calling a model | `llm/`, and called from elsewhere | Nothing else calls a provider |

---

## Tests

`lib/tests/unit/` mirrors `lib/src/blue_data_planner/` directory for directory, so the test for
`planner/physical_planner.py` is `tests/unit/planner/test_physical_planner.py`. It mirrors `lib/scripts/` the same
way, which is why `tests/unit/scripts/tag_bench/` exists. Four directories sit beside `unit/` and
mirror nothing.

| Directory | Holds |
| --- | --- |
| `doubles/` | In-memory replacements for I/O boundaries, chiefly the data registry, plus fakes for the four planning stages |
| `stubs/` | Deterministic fakes for heavy optional dependencies: litellm, sentence-transformers, LOTUS. On pyright's resolution path through `extraPaths` |
| `fixtures/` | Recorded inputs and the cassettes that replay model responses |
| `benchmarks/` | The TAG-Bench harness, excluded from the default run by `testpaths` |

A platform package tests only translation: argument parsing, response shape, error mapping, exit
codes. Its tests import doubles from `lib/tests/` rather than defining their own.

### Where the benchmark lives

Three directories hold the benchmark, and none of them ships.

| Part | Where |
| --- | --- |
| The harness that runs requests and scores answers | `lib/tests/benchmarks/tag_bench/` |
| The queries, the databases, and the registry over them | `data/tag_bench/`, downloaded and built rather than committed |
| The steps that download a release, correct it, and build the registry | `lib/scripts/tag_bench/` |

The build step is three scripts rather than one because the three outputs are fetched and rebuilt on
different occasions. `fetch_queries.py` reads a release and writes corrected queries beside it,
leaving the download untouched. `fetch_databases.py` downloads the five BIRD databases.
`register_databases.py` builds one `blue-data` registry over those databases and serializes it once,
so a test opens a file instead of syncing five databases again. The paths each one writes are fixed
in [10-data-model.md](10-data-model.md#6-benchmark-inputs).

`register_databases.py` is the one place in the repository outside `data/` that imports `blue-data`.
Registering sources, syncing them, collecting statistics, and enriching them are ingestion, which
belongs to `blue-data` rather than here, so the library offers no way to do any of it and a caller
with no registry builds one with `blue-data` first. The benchmark still needs a registry built, and it
is a build step in an unpackaged directory, so it calls `blue-data` directly rather than forcing an
ingestion surface into `data/` ([06-library.md](06-library.md#opening-a-registry)). The rule the
library holds itself to is scoped to `lib/src/` and stated that way in
[08-coding-style.md](08-coding-style.md).

The benchmark measures the system rather than being part of it. It ships in no package and runs only
when asked for by name.

---

## What does not live here

| Not here | Where instead |
| --- | --- |
| Agents | Other Blue repositories, or a framework such as LangChain, reaching in through MCP |
| Operators, the abstract `Operator`, and the operator registry | `blue-data-ops`, reached only through `operators/` |
| Data source implementations and the registry | `blue-data`, reached only through `data/` |
| Building or populating a registry | `blue-data`'s own tooling. The benchmark script is the single exception, and it is not shipped |
| LOTUS source | Its own package, installed through the `lotus` extra (ADR-004) |
| A REST API | Reserved as `platform/api/`, not in the first release |
| A hosted operator registry | Would be `platform/registry-mcp/`, mirroring `blue-data`. Whether to build one is an open question ([13-decision-log.md](13-decision-log.md#open-questions)) |
