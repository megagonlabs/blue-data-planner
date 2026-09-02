# blue-data-planner — Software Stack

This document names the languages, libraries, and tools the components in
[03-high-level-architecture.md](03-high-level-architecture.md) are built from, and says which
dependency belongs to which package.

---

## Language and runtime

| Concern | Choice |
| --- | --- |
| Language | Python |
| Minimum version | 3.10 |
| Maximum version | None, except with the `lotus` extra installed, which caps at 3.12 (ADR-004) |
| Source layout | `src/`, one `pyproject.toml` per package |
| Build backend | setuptools |
| Development runner | `uv run python ...` |
| Documented install path for consumers | `python -m venv` plus `pip install` |

The two install paths are deliberate and have to agree about extras and minimum versions (ADR-001).
Type hints use everything from 3.10 onward, including `X | Y` unions, and every module opens with
`from __future__ import annotations`.

---

## Packages

Three distributions are built from this repository. Each declares its own dependencies, and no
platform package is a dependency of the library.

| Package | Path | Depends on |
| --- | --- | --- |
| `blue-data-planner` | `lib/` | `blue-data-ops`, `blue-data`, `pydantic`, `jsonmerge`, `jsonpath-ng` |
| `blue-data-planner-cli` | `platform/cli/` | `blue-data-planner`, `click`, `rich`, `python-dotenv` |
| `blue-data-planner-mcp` | `platform/mcp/` | `blue-data-planner`, `mcp`, `pydantic` |

The MCP package pins `blue-data-planner` to an exact version. The CLI follows the same rule, because a
platform package translates for one library version and has no reason to float ahead of it.

`blue-data-ops` is pinned to an exact version too. `operators/blue.py` reads what each operator
declares — its logical operators, its source kinds, whether it accepts bound inputs — and a plan is
bound against those declarations, so a floating operator library would change which fragments are
coverable between runs.

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
| Prompt templating | `jinja2` plus `string.Template`, in `llm/templating.py` |
| API key | `OPENAI_API_KEY` in the environment, read by litellm |

Litellm is chosen over an SDK for one provider because the rest of the stack speaks it: the
`blue-data-ops` operators call models through the same client, and LOTUS configures its own model
the same way. One installed provider library then serves all three, and swapping providers is a
model string rather than a code change.

`litellm`, `tenacity`, and `jinja2` are required dependencies of `lib/` rather than an extra, since
the planner cannot run without a model.

### Structured output

Five model calls return structured values rather than prose, and each is constrained by a Pydantic
response schema rather than parsed out of text.

| Call | Fills | Described in |
| --- | --- | --- |
| Logical planning | A plan model assembled from the node schemas: one per logical operator, plus the terminal and control nodes | [04, section 1](04-detailed-architecture.md#1-logical-planning) |
| Physical planning | A choice among the candidate fragments enumerated for that call, and a source for each `SCAN` in the chosen one | [04, section 2](04-detailed-architecture.md#2-physical-planning) |
| Fragment parameters | The parameter schema of the operator the cover bound | [04, section 3](04-detailed-architecture.md#3-running-a-fragment) |
| Fragment review | An assessment in plain language and one of accept, reject, replan | [04, reviewing the result](04-detailed-architecture.md#reviewing-the-result) |
| Discovery search terms | The terms one `DISCOVER` node expands into | [04, resolving a discovery node](04-detailed-architecture.md#resolving-a-discovery-node) |

Two of those schemas are built per call rather than declared once. Physical planning's is assembled
from that call's own candidates, so a candidate set holding no `SCAN` has no field in which to name a
source, and where a scan does appear the catalog's paths are the allowed values. Fragment parameters
use the schema of whichever operator the cover bound, which is not known until the cover is written.
Building the schema per call is what keeps an impossible choice from appearing as an option, so
nothing has to reject one afterwards.

How far a schema is enforced depends on the provider. One that supports strict structured output,
OpenAI among them, applies it during decoding, and a response the schema forbids cannot be produced
at all. Where a provider does not, litellm falls back to describing the schema in the prompt. Either
way the response is validated against the schema on arrival, and one that fails is a failed call
rather than something repaired, so the difference between the two is cost and not correctness.

`pydantic` is a required dependency of `lib/`, so nothing new is installed for this.

### Two model configurations, one budget

LOTUS holds its own model configuration and is not routed through `llm/`. Both reach a provider
through litellm, so one API key serves both, but the settings are made independently, and a change
to one does not affect the other.

Only one budget exists: operator calls. A model call LOTUS makes happens inside an operator call, so
it bills against that budget like the rest of the call; a call through `llm/` bills against nothing
(ADR-014). The client records tokens, cost, and latency per call regardless, and a result carries one
such record per call, which is what a caller reads to convert a call count into money.

---

## Data access

`blue-data` is imported in `lib/src/blue_data_planner/data/` and nowhere else (ADR-003).

| Concern | Choice |
| --- | --- |
| Package | `blue-data>=0.1.0`, installed from the internal source |
| Types used | `DataRegistry`, `DataSource`, and the registry's ranked record search |
| Properties read | The descriptions enrichment wrote, and the `stats` and `value_semantics` properties collected per column |
| Typing | `blue-data` ships no `py.typed`, so its imports carry a pyright ignore at this boundary and delegate through `Any` |
| Storage backends | Chosen by installing a `blue-data` extra: `postgres`, `mysql`, `mongodb`, `neo4j`, `redis`, `chromadb` |

Which backend a deployment installs is a `blue-data` decision, not one this repository makes. No
storage driver appears in any `pyproject.toml` here.

---

## Data in flight

A fragment passes a list of tables, each a plain list of row dicts (ADR-012). Nothing in `planner/`,
`execution/`, or the plan types imports a data frame library.

| Library | Where it is used | Required |
| --- | --- | --- |
| `pandas` | Inside the LOTUS adapter only, one frame built per table going in and flattened coming out | Arrives with `blue-data`, and with LOTUS |
| `numpy` | Reached only through `blue-data-ops` operators | Arrives with `blue-data` |

`blue-data` requires `pandas` and `numpy`, so both are present in every install regardless of the
extras. What the rule in ADR-012 buys is therefore a shape that belongs to no suite and readable
intermediate results, rather than a smaller install.

---

## Operator suites

| Suite | Package | Extra |
| --- | --- | --- |
| LOTUS | `lotus-ai` | `lotus` |

LOTUS is the first suite integrated. It is called as an outside dependency and its source is never
copied in (ADR-004). Its
install constraints are heavier than the rest of the stack, which is why it sits behind an extra:

| Constraint from `lotus-ai` 1.2.x | Effect |
| --- | --- |
| `requires-python >=3.10,<3.13` | Caps Python at 3.12 for an install that includes the extra |
| `numpy>=1.25,<2` | Holds numpy on the 1.x line |
| `pandas>=2,<3` | Holds pandas on the 2.x line |
| `sentence-transformers`, `faiss-cpu`, `torch` | A multi-gigabyte install, which consumers who plan against Blue operators alone do not pay for |


The version is pinned in the extra rather than floated, because the adapter describes each LOTUS
operation by name and parameters.

---

## External interfaces

### CLI

| Concern | Choice |
| --- | --- |
| Command routing and argument parsing | `click>=8` |
| Terminal output | `rich>=13` |
| Configuration loading | `python-dotenv` |

Click matches `platform/cli/` in the sibling Blue repositories, and Rich renders the per-fragment
progress table and the result rows that [02-scenarios.md](02-scenarios.md) shows. This is a stated
departure from `.github/instructions/python.instructions.md`, which prescribes `argparse`: that rule
covers scripts, and a shipped multi-command CLI is the case it does not cover.

### MCP server

| Concern | Choice |
| --- | --- |
| Library | `mcp`, pinned below 2.0 |
| Transport | Streamable HTTP, bound to loopback by default |
| Configuration loading | `python-dotenv` |

The pin below 2.0 is deliberate: `mcp` 2.0 renamed `mcp.server.fastmcp.FastMCP`, so staying on 1.x
keeps the import path the server code uses.

The server takes its host, port, and path as arguments and reads everything else from the
environment; [12-deployment-ops.md](12-deployment-ops.md) lists those settings.

### REST API

Not part of the first release ([03-high-level-architecture.md](03-high-level-architecture.md)).
`platform/api/` is reserved for one, and no web framework is declared anywhere in this repository.

---

## Configuration

Configuration comes from environment variables. An adapter loads a `.env` file with
`dotenv.load_dotenv(override=True)` at startup and passes values into the library; the library
itself does not read the environment, since it takes concrete implementations and settings as
injected values. `.env` files are never committed.

| Variable | Read by | For |
| --- | --- | --- |
| `OPENAI_API_KEY` | litellm, through `llm/` | The five planner calls above, and LOTUS operations |
| `BLUE_DATA_PLANNER_MODEL` | The adapter, passed to `llm/` | The default litellm model string for the planning stages |
| `BLUE_DATA_REGISTRY_*` | The adapter, passed to `data/` | Which registry and store the run reads |

The full list of settings and their defaults belongs to
[12-deployment-ops.md](12-deployment-ops.md), including the operator call budget, the bound on each
of the four repairs, and the caps on plan corrections and discovery search terms (ADR-014).

---

## Quality gates

Every package runs the same three gates, and all three have to pass before work is reported as
complete.

| Gate | Tool | Settings |
| --- | --- | --- |
| Tests | `pytest` | `testpaths = ["tests/unit"]`, coverage reported per run |
| Async tests | `pytest-asyncio` | `asyncio_mode = "auto"` |
| Recorded model responses | `pytest-recording` | Cassettes for prompt-sensitive behavior, re-recorded when a prompt changes materially |
| Coverage | `pytest-cov` | Reported against `src` |
| Lint and import order | `ruff` | Line length 100, rules `E`, `F`, `I`, `UP` |
| Types | `pyright` | Strict mode, with `tests/stubs` on the resolution path |

Heavy optional dependencies are stubbed in `lib/tests/stubs/` so the default suite runs without
litellm, sentence-transformers, or LOTUS installed. I/O boundaries are replaced with in-memory
doubles in `lib/tests/doubles/`. What each tier covers is
[11-testing-strategy.md](11-testing-strategy.md).

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
| Dependency placement | A development-only extra, since the harness measures the system rather than being part of it |

Reading the databases needs no new dependency: they are SQLite, which the standard library opens,
and registering them is `blue-data`'s job. [10-data-model.md](10-data-model.md#6-benchmark-inputs)
has the files and which script writes each one.

---

## Dependency summary

| Package | Where declared | Required or extra | Role |
| --- | --- | --- | --- |
| `blue-data-ops` | `lib/` | Required, pinned to an exact version | The Blue operators and their registry |
| `blue-data` | `lib/` | Required | Data source interfaces and the registry |
| `pydantic` | `lib/`, `platform/mcp/` | Required | Response schemas for structured output, attribute validation |
| `jsonmerge` | `lib/` | Required, pinned `==1.9.2` | Blue-compatible JSON merge |
| `jsonpath-ng` | `lib/` | Required | Path expressions in templating and JSON utilities |
| `litellm` | `lib/` | Required | Provider-agnostic model calls |
| `tenacity` | `lib/` | Required | Retry with backoff around model calls |
| `jinja2` | `lib/` | Required | Prompt templating |
| `lotus-ai` | `lib/` | `lotus` extra | The first outside operator suite |
| `mcp` | `platform/mcp/` | Required, pinned below 2.0 | MCP server |
| `click` | `platform/cli/` | Required | CLI command routing |
| `rich` | `platform/cli/` | Required | CLI output formatting |
| `python-dotenv` | `platform/*` | Required | Loads `.env` at adapter startup |
| `pytest`, `pytest-asyncio`, `pytest-recording`, `pytest-cov` | All | `dev` extra | Test tiers |
| `ruff`, `pyright` | All | `dev` extra | Lint and type gates |
| `build`, `twine` | All | `dev` extra | Packaging and publishing |

---

## What is deliberately absent

| Not here | Why |
| --- | --- |
| FastAPI, Uvicorn | No REST API in the first release |
| `gepa` and automatic prompt optimization | Prompts are written by hand and versioned in the repository (ADR-005). Tuning one automatically needs a score to tune against, which is open question 32 |
| A model response cache | Every model call reaches the provider. A cache keyed on the request rarely hits, since a planner's requests carry the catalog, an input sample, and the conversation so far, and what it does reliably is make a run's cost depend on invisible state from an earlier run |
| LlamaIndex, DSPy, LangChain | The library needs a model client, not an application framework. Agent frameworks are consumers, reached through MCP |
| A direct `openai` dependency | Provider libraries arrive through litellm |
| `torch` as a declared dependency | It arrives with LOTUS, and nothing here imports it |
| `scikit-learn`, `sentence-transformers`, and the `ml` and `vector` extras | The operators needing them belong to `blue-data-ops`, which declares its own extras |
| Storage drivers | Backends are a `blue-data` choice, installed through its extras |
