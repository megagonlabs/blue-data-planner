# blue-data-planner — Deployment / Ops

blue-data-planner is a Python library plus adapters, embedded in another process or run as an MCP
server. This document covers how the three packages are versioned, installed, configured, and
operated.

---

## 1. Versioning

Each package follows [semantic versioning](https://semver.org/) and declares its own version in its
own `pyproject.toml`. There is no repository-wide version.

| Package | Breaking change means |
| --- | --- |
| `blue-data-planner` | A change to the public surface, the plan shape, or a result dataclass |
| `blue-data-planner-mcp` | A change to a tool's arguments, its response shape, or the tool list |
| `blue-data-planner-cli` | A change to a command, an option, or an exit code |

The CLI and MCP packages each pin `blue-data-planner` to an exact version, so a library release
requires a release of both. That is deliberate: an adapter translates for one library version and
has no reason to float ahead of it. Every operator suite is version-bounded for the reason ADR-004
gives: an adapter names each operation and its parameters.

---

## 2. Installation

All three packages publish to PyPI, and the documented consumer path is `python -m venv` plus
`pip install` (ADR-001).

```bash
python -m venv .venv && source .venv/bin/activate
pip install blue-data-planner
pip install blue-data-planner-cli
pip install blue-data-planner-mcp
```

Installing the CLI package puts `blue-planner` on the path, and installing the MCP package puts
`blue-data-planner-mcp` there.

Development installs from checkouts instead, the same way `blue-data-kb-source` installs `blue-data`.
`blue-data` and `blue-data-ops` are checked out beside this repository, and every package is
installed editable, so a change in either sibling reaches the planner without a reinstall.

```bash
bash lib/scripts/install.sh            # ../blue-data, ../blue-data-ops, then lib/
bash platform/planner-cli/scripts/install.sh   # the same, then the CLI
bash platform/planner-mcp/scripts/install.sh   # the same, then the MCP server
```

`[tool.uv.sources]` in each `pyproject.toml` points at the same two sibling paths, so `uv sync`
builds the same layout.

The library's extras select what an install pays for.

```bash
pip install -e .            # library only
pip install -e '.[lotus]'   # + LOTUS
pip install -e '.[dev]'     # + the test and lint gates
```

The `lotus` extra also narrows what else the environment can hold, which
[05-software-stack.md](05-software-stack.md#operator-suites) describes.

Which Blue operators an install can bind is a `blue-data-ops` question, answered by that package's
own extras. The `builtin` suite is behind no extra: it is written here and needs nothing installed
(ADR-058).

A model client is not an extra: the planner cannot run without one, so its dependencies are
required rather than optional ([05-software-stack.md](05-software-stack.md)).

The CLI and the MCP server are separate packages rather than extras of the library, so there is no
`blue-data-planner[server]`. Each is installed on its own. The MCP package pins `mcp` below 2.0,
since the server is written against the 1.x interface and moving to a new major version is one
decision for every Blue server at once.

---

## 3. Running the MCP server

`blue-data-planner-mcp` runs over stdio, so an agent framework launches it as a subprocess and owns
its lifetime. Installing the package puts the command on the path.

```bash
blue-data-planner-mcp
```

There is nothing to bind and no port to choose. A client is configured with the command and the
environment the server needs ([02-scenarios.md](02-scenarios.md#scenario-2-answering-a-data-question-inside-an-agent)),
and the server reads everything else from the environment in [section 4](#4-configuration).

On the first tool call the server builds one planner from the environment in section 4 and holds it
for the process lifetime ([09-api-interface-spec.md](09-api-interface-spec.md#3-mcp-server)). It
needs a reachable registry and a model to build one, so an unreachable registry reaches the client
as an error payload on that first call rather than stopping the server at startup.

---

## 4. Configuration

The library takes settings as injected values and reads no environment variable itself. Each
interface loads what it needs and passes it into the constructors.

The MCP server reads no configuration file. It reads the environment, which is how a client
launching it as a subprocess configures it.

`blue-planner` reads a profile, and resolves each setting in one order (ADR-028): **command option,
then environment variable, then profile, then default**. It loads no `.env` file, so nothing takes
precedence over the exported environment.

| Variable | Default | For |
| --- | --- | --- |
| `OPENAI_API_KEY` | None, required | Every component that calls a model, and LOTUS operations |
| `BLUE_DATA_REGISTRY_PATH` | None, required | The serialized registry an interface opens and plans against |
| `BLUE_DATA_PLANNER_MODEL` | `BLUE_DATA_LLM_MODEL`, then `openai/gpt-6-luna` | The litellm model string every component uses |
| `BLUE_DATA_PLANNER_REASONING_EFFORT` | `low` | The reasoning effort each component's model call spends: `minimal`, `low`, `medium`, `high`, or `none`, which is sent to the provider and disables reasoning. `off` omits the parameter, for a model that does not accept it. Anything else is refused where it is read |
| `BLUE_DATA_PLANNER_OPERATORS` | `builtin,lotus` | Which operator suites are registered, as a comma-separated list over `builtin`, `lotus`, and `blue` |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | The planner's model, wherever it came from | The litellm model string an interface passes to `lotus_operators`, which LOTUS then configures for itself, and which a deployment can set away from the planner's model (ADR-005). A value set here is never overridden by the CLI's `--model` |
| `BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` | `none` | The reasoning effort LOTUS's own model spends per call, passed to `lotus_operators`, and independent of `BLUE_DATA_PLANNER_REASONING_EFFORT` |
| `BLUE_DATA_PLANNER_LOTUS_MAX_ROWS` | `5000` | The rows a LOTUS operation accepts before refusing the call, passed to `lotus_operators` |
| `BLUE_DATA_PLANNER_PROVIDER_TIMEOUT_SECONDS` | `120` | The seconds one provider call through `llm/` may take before it fails and is retried (ADR-061) |
| `BLUE_DATA_PLANNER_MAX_*` | The `RunLimits` defaults | One per bound a run reports back, listed in [section 5](#5-request-limits) |
| `BLUE_DATA_PROFILE` | The profile `profile select` chose | The profile the CLI reads, when no `--profile` is given. A name with no profile behind it is refused as `PROFILE_NOT_FOUND` |
| `BLUE_DATA_CLI_FORMAT` | `table` | The CLI's default output format |
| `BLUE_DATA_CLI_LOG_LIMIT` | `80` | The characters each line of the log is cut to |

Three of these — `OPENAI_API_KEY`, `BLUE_DATA_CLI_FORMAT`, and `BLUE_DATA_CLI_LOG_LIMIT` — are
declared once for every Blue CLI. The family also declares `BLUE_DATA_LLM_MODEL`, which
`BLUE_DATA_PLANNER_MODEL` falls back to, `ANTHROPIC_API_KEY`, and `BLUE_DATA_LLM_PROVIDER`, and
`profile inspect` shows all of them.

One model string serves every component. `DataPlanner`'s constructor takes a client per component,
so a deployment that wants a stronger model for logical planning than for writing parameters builds
them itself ([09-api-interface-spec.md](09-api-interface-spec.md#assembly)). Whether that split
should be the default is open question 18.

### The CLI's profile

A profile is a named set of these values, and `blue-planner profile` manages them
([09-api-interface-spec.md](09-api-interface-spec.md#2-cli)). The CLI keeps them under its own home
directory, which `BLUE_DATA_PLANNER_HOME` relocates.

```text
~/.blue-planner/
├── .profiles           # one section per profile, mode 0600
└── .selected.profile   # what `profile select` wrote
```

A secret is stored either as an encoded value or as a reference such as `$OPENAI_API_KEY`, read from
the environment when a command runs. The reference form keeps a key off disk entirely; the encoded
form keeps it off a screen, and the file mode is what protects it.

`BLUE_DATA_PLANNER_HOME` is not itself a profile parameter, since the profiles live inside the
directory it names.

---

## 5. Request limits

`RunLimits` holds every bound the run loop obeys, and it is set once, when the planner is built
(ADR-060). Nothing overrides one per request. Whether a bound an operator enforces inside its own
call belongs here is open question 45 in [13-decision-log.md](13-decision-log.md#open-questions).
The values in force are the defaults on `RunLimits`. A deployment raises or lowers one of the three
a run reports back by setting `BLUE_DATA_PLANNER_MAX_` plus the field's name ([section
4](#4-configuration)); the other three reach a run from a caller that builds the planner itself.

Where a bound is counted decides whether a result can report it. Three are counted across a whole
request and are the fields of `RunCounters`. The other three are spent inside a loop and start again
when that loop does, so no one number says what a request spent against them.

| Bound | Counted over | Starts again | Reaching it |
| --- | --- | --- | --- |
| `operator_calls` | The whole request | Never | `budget_exhausted` |
| `failure_replans` | The whole request | Never | `replan_exhausted` |
| `control_replans` | The whole request | Never | `replan_exhausted` |
| `cover_repairs` | One logical plan | At every new logical plan | The run moves on to the replan |
| `parameter_repairs` | One fragment run | At every fragment run | The run moves on to the cover repair |
| `plan_corrections` | One plan being written | At every call to the logical planner | `invalid_plan` |

Every recovery bound counts tries after the first, so a bound of two means one try and two repairs,
and every loop spends it the same way. `plan_corrections`, `parameter_repairs`, `cover_repairs`,
`failure_replans`, and `control_replans` are all named for what repeats rather than for the total,
which is what keeps one convention readable from the names. `operator_calls` is a total rather than
a repeat count and stands outside the rule.

How many search terms one `DISCOVER` node expands into is not here. Reaching that number changes
nothing about how a request proceeds: the extra terms are dropped and the search runs. It shapes what
one component does rather than bounding a run, which puts it with the size of the catalog preview,
as a number `CatalogSearcher` holds ([06-library.md](06-library.md#catalogsearcher)).

### How the six compose

The three recovery bounds decide *which* repair a rejected fragment gets, not how much a request may
spend in total
([04-detailed-architecture.md](04-detailed-architecture.md#5-recovering-from-a-failure)). Multiplied
out they permit far more work than any request should do: several covers of one logical plan, inside
several logical plans, each fragment of each cover writing parameters several times over.

`operator_calls` is the only ceiling on the total, which is why ADR-060 says its value matters more
than it otherwise would. A plan that runs cleanly spends one call per fragment. A cover repair
spends one more per fragment the model chose to change, and a rejected fragment spends its parameter
repairs on top, so exhausting the budget takes a run in which most fragments failed and the repairs
kept finding something else to try. A request stopped by `operator_calls` rather than by a recovery
bound is a request that was not converging.

`operator_calls` also caps the size of a plan: validation turns down a plan holding more operator
and `DISCOVER` nodes than it, not counting `RETURN` and `REPLAN` (ADR-060). This cap bounds the
model calls made while planning: a run makes one catalog search per `DISCOVER` node and at most one
binding per operator node in each cover.

Model calls have no cap of their own. One request makes at most the product of these limits:

| Model calls | At most |
| --- | --- |
| Logical planning, per pass | `1 + plan_corrections` |
| Catalog searches, per pass | `operator_calls` |
| Bindings, per pass | `operator_calls × (1 + cover_repairs)` |
| Passes, per request | `1 + failure_replans + control_replans` |
| Parameter writing and review | A number that grows with the operator calls billed |

With the defaults, the product is about a thousand calls. The count is finite, but it is not a cost
ceiling: no setting caps what one request spends on model calls.

### Where the values come from

None of the six is measured. The recovery bounds are deliberately conservative, on the reasoning
that a second cover that also fails is better answered by a new plan than by a third cover, and the
rest are set low enough to stop a run that is not converging without stopping one that is.

The TAG-Bench harness reports operator calls per request, model calls per component, how often a plan
schedules a replan, and how often each repair fires
([11-testing-strategy.md](11-testing-strategy.md#8-the-benchmark)), and the values are meant to be
replaced by what it shows.

---

## 6. Storage backends

No storage driver is declared in any `pyproject.toml` here, and no backend is bundled or managed.
Everything reaches storage through `blue-data`, and which backend a deployment uses is a `blue-data`
decision, made by installing the extra for it.

A registry reaches this package already populated. Registering sources, syncing them, collecting
statistics, and enriching them are ingestion, which belongs to `blue-data`, so a deployment with
data and no registry builds one there before it constructs a planner
([06-library.md](06-library.md#data)). How well that population was done is what planning quality
depends on: a source nobody enriched renders with names and kinds alone.

A backend has to be reachable before a request runs. An unreachable source ends a request as an
unrecoverable operator failure, since retrying with a different operator would fail the same way
([04-detailed-architecture.md](04-detailed-architecture.md#failures-that-skip-recovery)).

blue-data-planner writes nothing of its own to disk by default. There is no plan store and no
session log, and the model response cache is opened only for a caller that turns it on (ADR-054,
[10-data-model.md](10-data-model.md#what-is-not-stored)).

---

## 7. Logging

The library writes no log records of its own, attaches nothing to the root logger, and prints
nothing on import. A dependency it calls, such as litellm, logs under its own logger, and an
embedding application decides the level, the format, and the destination of those. The MCP server
sets litellm's loggers to warnings when it starts, so a per-call record does not reach the client's
log.

The run record is the more useful instrument, and it is not a log. `RunResult` carries every plan
the request produced, every fragment attempt including the rejected ones, the counters, and one
usage record per model call. A failed run is as inspectable as a successful one (ADR-015), so
diagnosing one means reading the returned structure rather than reconstructing it from log lines.

| Question | Read |
| --- | --- |
| What did the planner decide to do? | `result.pipeline.logical_plans` |
| How much was pushed into each operator? | Each cover in `result.pipeline.physical_plans` |
| Which operator ran a node, and what did it return? | `result.results`, under the signature the current cover gives it |
| Why was a fragment rejected? | `result.conversations.fragment_runner`, where the review said so |
| Where did the request stop? | `result.status` and `result.counters` |
| What did planning cost? | `result.conversations`, which carries one usage record per call |

Raw row values are never written to a log. They appear in the result, which is returned to the
caller rather than emitted.

---

## 8. Observability

Nothing here emits traces or metrics of its own, and the run record is what a deployment reads
instead ([section 7](#7-logging)). Whether the library or the adapters gain instrumentation, and
what it would cover, is open question 42 in [13-decision-log.md](13-decision-log.md#open-questions).

Two things hold whichever way that is answered. Row values stay out of anything emitted, whether a
log line or a span attribute. And a `/metrics` endpoint belongs to whatever hosts the library rather
than to the library, which is embedded and synchronous, so designing one before `platform/planner-api/`
exists would be guessing at its shape.

---

## 9. Versioning a release

A release is per package. Its version is bumped in the package's own `pyproject.toml`, and the
library releases together with the CLI and MCP packages whenever the pin between them changes
([section 1](#1-versioning)).

---

## 10. Publishing

Each package builds its wheel and sdist with its own `scripts/build.sh`. The library is published by
hand, from the repository root:

```bash
bash lib/scripts/build.sh && bash lib/scripts/publish.sh
```

`publish.sh` uploads what `build.sh` wrote into `lib/dist/` to PyPI with `twine`.

---

## 11. What is deliberately absent

| Not here | Why |
| --- | --- |
| A Docker image | Nothing here is a long-running service. The MCP server is a subprocess its client starts, from an installed package |
| A `docker-compose.yml` | Backends are a `blue-data` decision, and none is bundled |
| A REST API and a `/health` endpoint | Not in the first release. `platform/planner-api/` is reserved |
| A hosted operator registry | The registry and router are in-process, built by whoever assembles the planner |
| An ingestion path | Building and populating a registry belongs to `blue-data`, and this package only reads one (ADR-003) |
| Authentication and authorization | Belongs to the deployment. The MCP server listens on no socket, so whoever may launch the subprocess may plan |
| Per-request limit overrides | Bounds are set when a planner is built. A deployment that needs two sets of bounds builds two planners |
