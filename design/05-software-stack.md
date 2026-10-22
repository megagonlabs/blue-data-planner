# blue-data-planner — Software Stack

This document names the languages, libraries, and tools the components in
[03-high-level-architecture.md](03-high-level-architecture.md) are built from, and says which
dependency belongs to which package.

---

## Language and runtime

| Concern | Choice |
| --- | --- |
| Language | Python |
| Minimum version | 3.11 |
| Maximum version | None of its own. The `lotus` extra narrows the range, which is part of why it is an extra (ADR-004) |
| Source layout | `src/`, one `pyproject.toml` per package |
| Build backend | setuptools |
| Development runner | `uv run python ...` |
| Documented install path for consumers | `python -m venv` plus `pip install` |

The two install paths are deliberate and have to agree about extras and minimum versions (ADR-001).

---

## Packages

Three distributions are built from this repository. Each declares its own dependencies, and no
platform package is a dependency of the library.

| Package | Path | Depends on |
| --- | --- | --- |
| `blue-data-planner` | `lib/` | `blue-data-ops`, `blue-data`, `pydantic`, `litellm`, `tenacity`, `jinja2`, `jsonpath-ng` |
| `blue-data-planner-cli` | `platform/planner-cli/` | `blue-data-planner`, `blue-data`, `click`, `rich` |
| `blue-data-planner-mcp` | `platform/planner-mcp/` | `blue-data-planner`, `mcp`, `pydantic` |

The MCP package pins `blue-data-planner` to an exact version. The CLI follows the same rule, because
a platform package translates for one library version and has no reason to float ahead of it.

---

## Model access

Every model call the library makes goes through `lib/src/blue_data_planner/llm/`, which wraps
[litellm](https://github.com/BerriAI/litellm). No other module calls a provider (ADR-005).

| Concern | Choice |
| --- | --- |
| Provider abstraction | `litellm` |
| First provider | OpenAI, through a litellm model string |
| Structured output | Pydantic models passed as a strict response schema |
| Retries | `tenacity`, with exponential backoff on provider errors |
| Prompt templating | `jinja2`, in `llm/templating.py` |
| API key | `OPENAI_API_KEY` in the environment, read by litellm |

Litellm is chosen over an SDK for one provider because the rest of the stack speaks it: the
`blue-data-ops` operators and LOTUS each reach a provider through litellm under a model
configuration of their own. One installed provider library then serves all three.

`litellm`, `tenacity`, and `jinja2` are required dependencies of `lib/` rather than an extra, since
the planner cannot run without a model.

### Structured output

Five model calls return structured values rather than prose, and each is constrained by a Pydantic
response schema rather than parsed out of text.

| Call | Fills | Described in |
| --- | --- | --- |
| Logical planning | A plan: its nodes, each with its kind, inputs, and attributes, and each `SCAN`'s source | [04, section 1](04-detailed-architecture.md#1-logical-planning) |
| Physical planning | A choice among the candidate fragments enumerated for that call, and for the chosen one its instruction, a name and description for each input, and its `required_output_columns` | [04, section 2](04-detailed-architecture.md#2-physical-planning) |
| Fragment parameters | The parameter schema of the operator the cover bound | [04, section 3](04-detailed-architecture.md#3-running-a-fragment) |
| Fragment review | An assessment in plain language and one of accept, reject, replan | [04, reviewing the result](04-detailed-architecture.md#reviewing-the-result) |
| Discovery search terms | The terms one `DISCOVER` node expands into | [04, resolving a discovery node](04-detailed-architecture.md#resolving-a-discovery-node) |

Two of those schemas are built per call rather than declared once. Physical planning's is assembled
from that call's own candidates, so only a candidate on offer can be chosen. Fragment
parameters use the schema of whichever operator the cover bound, which is not known until the cover
is written. Building the schema per call is what keeps an impossible choice from appearing as an
option, so nothing has to reject one afterwards.

How far a schema is enforced depends on the provider. One that supports strict structured output
applies it during decoding, and a response the schema forbids cannot be produced at all. Where a
provider does not, the schema reaches the model as text in the prompt. Either way the
response is validated against the schema on arrival, and one that fails is a failed call rather than
something repaired, so the difference between the two is cost and not correctness. Every schema
built here stays inside the subset a strict provider accepts, so one schema serves either kind.

`pydantic` is a required dependency of `lib/`, so nothing new is installed for this.

### Three model configurations, one budget

The Blue suite and LOTUS each hold their own model configuration and are not routed through `llm/`.
All three reach a provider through litellm, so one API key serves them, but each is set
independently, and a change to one does not affect the others.

| Configuration | Model | Provider |
| --- | --- | --- |
| `llm/` | The planner's model | The route in the model string |
| LOTUS | `LotusSuite.model`, else the planner's model | The route in the model string |
| Blue suite | `BlueSuite.model`, else the planner's model, passed without its provider route | `BLUE_DATA_LLM_PROVIDER`, read by `blue-data-ops`, defaulting to `openai` |

`blue-data-ops` refuses a model string that names a provider, so the Blue suite passes the model
name alone. A deployment whose Blue model is served by a provider other than OpenAI sets
`BLUE_DATA_LLM_PROVIDER` to that provider. Nothing checks the two against each other, and a
mismatch fails every model call a Blue operation makes.

Only one budget exists: operator calls. A model call LOTUS or a Blue operation makes happens inside
an operator call, so it bills against that budget like the rest of the call; a call through `llm/`
bills against nothing (ADR-060). The client records tokens, cost, and latency per call regardless,
and a result carries one such record per call, which is what a caller reads to convert a call count
into money.

---

## Data access

`blue-data` is imported in `lib/src/blue_data_planner/data/` and nowhere else (ADR-003).

| Concern | Choice |
| --- | --- |
| Package | `blue-data`, a required dependency of `lib/` |
| Types used | `DataRegistry`, `DataSource`, and the registry's ranked record search |
| Properties read | The descriptions enrichment wrote, and the statistics and value semantics collected per column |
| Storage backends | Chosen by installing the matching `blue-data` extra |

Which backend a deployment installs is a `blue-data` decision, not one this repository makes. No
storage driver appears in any `pyproject.toml` here.

---

## Data in flight

A fragment passes a list of tables, each a plain list of row dicts (ADR-012). Nothing in the planning
components, `execution/`, the plan types, or the `builtin` suite imports a data frame library.

| Library | Where it is used | Required |
| --- | --- | --- |
| `pandas` | Inside the LOTUS adapter only, one frame built per table going in and flattened coming out | Declared by the `lotus` extra, not by the library |
| `numpy` | Not imported by this repository | Declared by neither |

The `builtin` operators work on the bundle as it stands. A data frame would be the obvious way to
write them and is deliberately not used: it would put a required dependency in front of every
consumer to save a page of plain Python, and it would tie the library to the major lines the LOTUS
extra already pins.

What the rule in ADR-012 buys is a row shape belonging to no suite and readable intermediate
results. Whether a data frame library reaches an install at all is a dependency's decision.

---

## Operator suites

| Suite | Package | Extra |
| --- | --- | --- |
| Blue | `blue-data-ops` | Required |
| LOTUS | `lotus-ai` | `lotus` |
| `builtin` | This repository | None. Its entries are always present (ADR-058) |

LOTUS is the first outside suite integrated. It is called as an outside dependency and its source is
never copied in (ADR-004). It sits behind an extra because its install constraints are heavier than
the rest of the stack: it narrows the Python range, holds numpy and pandas each to one major line,
and brings a multi-gigabyte machine-learning stack with it. A consumer planning against other suites
pays for none of that.

The extra states a version range, because the adapter describes each LOTUS operator by name and
parameters.

`builtin` is written here rather than depended on (ADR-058), and it declares nothing new in the
dependency table. Its operators work on the bundle, which is plain lists and dicts, so no data
frame library is needed, and it reads sources through `data/`, which the library already depends
on. The cost of owning a suite is the tests
its behavior needs, not an install.

---

## External interfaces

### CLI

| Concern | Choice |
| --- | --- |
| Command routing and argument parsing | `click>=8` |
| Terminal output | `rich>=13` |
| Profiles, configuration resolution, and secret storage | `blue-data` |

Click matches the CLIs of the sibling Blue repositories, and Rich renders the progress indicator,
the log, and the result rows that [02-scenarios.md](02-scenarios.md) shows. `blue-data` carries the
configuration engine every Blue CLI resolves its profile through, so one set of rules governs
precedence and secret handling across the family (ADR-028). The CLI resolves values into a
`PlannerSettings` and hands it to `build_data_planner_from_settings`, so which operator suites exist
and how each is constructed stay inside the library and `blue-data-ops` is the library's dependency
alone ([09-api-interface-spec.md](09-api-interface-spec.md#assembly)).

### MCP server

| Concern | Choice |
| --- | --- |
| Library | `mcp`, `pydantic` |
| Transport | stdio, so a client launches the server as a subprocess |

The server needs a library that registers a tool with a decorator and runs over stdio. Which
version an install pins is in [12-deployment-ops.md](12-deployment-ops.md#2-installation).

The server reads what it needs from the environment, which
[12-deployment-ops.md](12-deployment-ops.md#4-configuration) lists, and has no host, port, or path
of its own.

### REST API

Not part of the first release ([03-high-level-architecture.md](03-high-level-architecture.md)).
`platform/planner-api/` is reserved for one, and no web framework is declared anywhere in this repository.

---

## Configuration

The library takes concrete implementations and settings as injected values and reads no environment
variable itself. The MCP server reads the environment and nothing else. The CLI reads a
profile, with the environment above it and below a command option (ADR-028).

| Setting | Read by | For |
| --- | --- | --- |
| `OPENAI_API_KEY` | litellm, through `llm/` | The model calls above, and LOTUS operators |
| `BLUE_DATA_PLANNER_MODEL` | The interface, passed to `llm/` | The default litellm model string for every component that calls a model |
| `BLUE_DATA_REGISTRY_PATH` | The interface, passed to `data/` | The serialized registry it opens and plans against |

The full list of settings and their defaults belongs to
[12-deployment-ops.md](12-deployment-ops.md), including the operator call budget, the bound on each
of the three repairs, and the cap on plan corrections (ADR-060).

---

## Quality check

The gates are `pytest`, with `pytest-asyncio` where a package has async tests, then
`ruff`, then `pyright`. How each is configured is in [08-coding-style.md](08-coding-style.md), and
what each test tier covers is in [11-testing-strategy.md](11-testing-strategy.md).

---

## Benchmark inputs

TAG-Bench is the benchmark target ([11-testing-strategy.md](11-testing-strategy.md)). Its released
files contain errors that a build step corrects, so results reproduce from a clean checkout without
editing anything downloaded.

| Concern | Choice |
| --- | --- |
| Inputs | Under `data/`, downloaded rather than committed |
| Released files read | The query set, and the five BIRD SQLite databases the queries run against |
| Build step | Writes corrected queries as JSON, and a serialized `blue-data` registry over those databases |
| Dependency placement | Nothing beyond the `dev` extra. The harness lives under `lib/tests/` and ships in no package, since it measures the system rather than being part of it |

Reading the databases needs no new dependency: they are SQLite, which the standard library opens,
and registering them is `blue-data`'s job.
[10-data-model.md](10-data-model.md#6-benchmark-inputs-and-runs) has the files and which script
writes each one.

---

## Dependency summary

| Package | Where declared | Required or extra | Role |
| --- | --- | --- | --- |
| `blue-data-ops` | `lib/` | Required | The Blue operators and their registry |
| `blue-data` | `lib/` | Required | Data source interfaces and the registry |
| `pydantic` | `lib/`, `platform/planner-mcp/` | Required | Response schemas for structured output in `lib/`; MCP tool parameter descriptions in `platform/planner-mcp/` |
| `jsonpath-ng` | `lib/` | Required | Path expressions in templating and JSON utilities |
| `litellm` | `lib/` | Required | Provider-agnostic model calls |
| `tenacity` | `lib/` | Required | Retry with backoff around model calls |
| `jinja2` | `lib/` | Required | Prompt templating |
| `regex` | `lib/` | Required | Pattern search with a time limit in `builtin.filter` (ADR-062) |
| `lotus-ai` | `lib/` | `lotus` extra | The first outside operator suite |
| `mcp` | `platform/planner-mcp/` | Required | MCP server |
| `click` | `platform/planner-cli/` | Required | CLI command routing |
| `rich` | `platform/planner-cli/` | Required | CLI output formatting |
| `blue-data` | `platform/planner-cli/` | Required | Profiles, configuration resolution, secret storage |
| `pytest` | All | `dev` extra | Test tiers |
| `pytest-asyncio` | `platform/planner-mcp/` | `dev` extra | The MCP server's async handler tests |
| `ruff`, `pyright` | All | `dev` extra | Lint and type gates |
| `build`, `twine` | All | `dev` extra | Packaging and publishing |

---

## What is deliberately absent

| Not here | Why |
| --- | --- |
| FastAPI, Uvicorn | No REST API in the first release |
| `gepa` and automatic prompt optimization | Prompts are written by hand and versioned in the repository (ADR-005). Tuning one automatically needs a score to tune against, which is open question 32 |
| A model response cache by default | Every model call reaches the provider unless a caller turns the cache on. A cache keyed on the request rarely hits, since a planner's requests carry the catalog, an input sample, and the conversation so far, and what it does reliably is make a run's cost depend on invisible state from an earlier run. `LLMClient` opens one only for a call carrying `llm.cache`, which nothing in this repository sets, and a consumer that sets it installs `diskcache` itself (ADR-054) |
| LlamaIndex, DSPy, LangChain | The library needs a model client, not an application framework. Agent frameworks are consumers, reached through MCP |
| A direct `openai` dependency | Provider libraries arrive through litellm |
| `torch` as a declared dependency | It arrives with LOTUS, and nothing here imports it |
| `scikit-learn`, `sentence-transformers`, and the `ml` and `vector` extras | The operators needing them belong to `blue-data-ops`, which declares its own extras |
| Storage drivers | Backends are a `blue-data` choice, installed through its extras |
