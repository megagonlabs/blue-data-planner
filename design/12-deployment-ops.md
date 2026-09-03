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

The MCP package pins `blue-data-planner` to an exact version, so a library release requires an MCP
release. That is deliberate: an adapter translates for one library version and has no reason to
float ahead of it. `blue-data-planner` pins `blue-data-ops` the same way, because a plan is bound
against what each operator declares ([05-software-stack.md](05-software-stack.md#packages)).

---

## 2. Installation

All three packages publish to PyPI, and the documented consumer path is `python -m venv` plus
`pip install` (ADR-001).

```bash
python -m venv .venv && source .venv/bin/activate
pip install blue-data-planner
pip install blue-data-planner-mcp
```

Development installs from a checkout instead, the same way `blue-data` and `blue-data-kb-source` are
installed.

```bash
cd lib && pip install -e .
cd ../platform/mcp && pip install -e .
```

The library's extras select what an install pays for.

```bash
pip install -e .            # library only
pip install -e '.[lotus]'   # + LOTUS
pip install -e '.[dev]'     # + the test and lint gates
```

Which Blue operators an install can bind is a `blue-data-ops` question, answered by that package's
own extras.

A model client is not an extra. The planner cannot run without one, so `litellm`, `tenacity`, and
`jinja2` are required dependencies of `lib/` ([05-software-stack.md](05-software-stack.md)).

The CLI and the MCP server are separate packages rather than extras of the library, so there is no
`blue-data-planner[server]`. Each is installed on its own.

---

## 3. Running the MCP server

`blue-data-planner-mcp` is a network service over streamable HTTP, not a stdio subprocess. It binds
loopback by default.

```bash
python -c 'from blue_data_planner_mcp import serve; serve()'
```

| Setting | Default | Set by |
| --- | --- | --- |
| Host | `127.0.0.1` | `serve(host=...)` |
| Port | `8000` | `serve(port=...)` |
| Path | `/mcp` | `serve(path=...)` |

The server takes its host, port, and path as arguments, and everything else from the environment in
[section 4](#4-configuration). Changing the host off loopback lets the network run plans against
every source the registry holds, including the operators that write to one, so it is worth deciding
deliberately.

`plan_and_execute` and `plan` are the whole tool list. Neither takes limits, so the server builds
one planner at startup from the environment in section 4, and every call runs under it
([09-api-interface-spec.md](09-api-interface-spec.md#planning-tools)). Both need a reachable
registry and a model before the first call, so a server that starts is not yet a server that can
answer.

---

## 4. Configuration

The library takes settings as injected values and reads no environment variable itself. Each adapter
loads a `.env` file with `dotenv.load_dotenv(override=True)` at startup and passes what it reads
into the constructor. `.env` files are never committed.

| Variable | Default | For |
| --- | --- | --- |
| `OPENAI_API_KEY` | None, required | The four planning stages, and LOTUS operations |
| `BLUE_DATA_PLANNER_MODEL` | `openai/gpt-4o` | The litellm model string all four stages use |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | `openai/gpt-4o` | The litellm model string LOTUS configures for itself, independently of `BLUE_DATA_PLANNER_MODEL` (ADR-005) |
| `BLUE_DATA_REGISTRY_PLATFORM` | `blue` | Which platform the registry belongs to |
| `BLUE_DATA_REGISTRY_NAME` | `default` | Which registry to read |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

One model string serves every stage. `DataPlanner`'s constructor takes a client per stage, so a
deployment that wants a stronger model for logical planning than for writing parameters builds the
stages itself ([09-api-interface-spec.md](09-api-interface-spec.md#assembly)). Whether that split
should be the default is open question 18.

---

## 5. Request limits

`PlanLimits` holds every bound the system obeys, and it is set once, when the planner is built
(ADR-014). Nothing overrides one per request. Each is reported in `PlanResult.counters` whether the
run succeeded or not, under the name it carries here.

| Bound | Default | Counted | Reaching it |
| --- | --- | --- | --- |
| `operator_calls` | 30 | Per request | `budget_exhausted` |
| `plan_corrections` | 5 | Per plan | `invalid_plan` |
| `parameter_attempts` | 3 | Per fragment | The run moves on to the fragment repair |
| `fragment_attempts` | 2 | Per node set | The run moves on to the cover repair |
| `cover_attempts` | 2 | Per request | The run moves on to the replan |
| `failure_replans` | 2 | Per request | `replan_exhausted` |
| `control_replans` | 2 | Per request | `replan_exhausted` |
| `search_terms` | 8 | Per `DISCOVER` node | The remaining terms are dropped and the count is reported |

### How the eight compose

The four recovery bounds decide *which* repair a rejected fragment gets, not how much a request may
spend in total
([04-detailed-architecture.md](04-detailed-architecture.md#5-recovering-from-a-failure)). Multiplied
out they permit far more work than any request should do: three cuts of one node set, inside three
covers, inside three logical plans, each cut writing parameters three times.

`operator_calls` is the only ceiling on the total, which is why ADR-014 says its default matters
more than it otherwise would. A four-fragment plan that runs cleanly spends four calls. One rejected
fragment adds up to three parameter attempts and then up to two further cuts, so reaching 30 takes a
run in which most fragments failed and the repairs kept finding something else to try. A request
stopped by `operator_calls` rather than by a recovery bound is a request that was not converging.

Model calls are not budgeted (ADR-014). Every one of them happens inside a loop one of these bounds
already limits, so a separate cap would restate them under another name.

### Where these numbers come from

None of the eight is a measured value. Three are deliberately conservative: `fragment_attempts` and
`cover_attempts` allow one alternative each, on the reasoning that a second cut that also fails is
better answered by a new plan than by a third cut, and `search_terms` caps one `DISCOVER` node at
eight registry searches.

The TAG-Bench harness reports operator calls per request, model calls per stage, how often a plan
schedules a replan, and how often each repair fires
([11-testing-strategy.md](11-testing-strategy.md#9-benchmark-suites)), and these numbers are meant
to be replaced by what it shows. Open question 2 holds the one that most needs the measurement.

A caller who needs different bounds builds a planner with its own `PlanLimits`. A planner configured
for a benchmark run and one configured for an interactive session are two objects rather than one
object called two ways ([06-library.md](06-library.md#budgets-and-limits)).

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

blue-data-planner writes nothing of its own to disk. There is no plan store, no session log, and no
model response cache ([10-data-model.md](10-data-model.md#what-is-not-stored)).

---

## 7. Logging

Logging uses the standard library, on a logger named for the package, with no handler configured.
The library attaches nothing to the root logger and prints nothing on import, so an embedding
application decides the format and the destination.

```python
logging.getLogger("blue_data_planner")
```

The run record is the more useful instrument, and it is not a log. `PlanResult` carries every plan
the request produced, every fragment attempt including the rejected ones, the counters, and one
usage record per model call. A failed run is as inspectable as a successful one (ADR-015), so
diagnosing one means reading the returned structure rather than reconstructing it from log lines.

| Question | Read |
| --- | --- |
| What did the planner decide to do? | `result.pipeline.logical_plans` |
| How much was pushed into each operator? | Each cover in `result.pipeline.physical_plans` |
| Which operator ran a node, and what did it return? | `result.results`, under the current plan id |
| Why was a fragment rejected? | `result.attempts`, each with its assessment and decision |
| Where did the request stop? | `result.status` and `result.counters` |
| What did planning cost? | `result.planner_usage` |

Raw row values are never written to a log. They appear in the result, which is returned to the
caller rather than emitted.

---

## 8. Observability

Nothing here emits traces or metrics of its own, and the run record is what a deployment reads
instead ([section 7](#7-logging)). Whether the library or the adapters gain instrumentation, and
what it would cover, is open question 42 in [13-decision-log.md](13-decision-log.md#open-questions).

Two things hold whichever way that is answered. Row values stay out of anything emitted, whether a
log line or a span attribute. And a `/metrics` endpoint belongs to whatever hosts the library rather
than to the library, which is embedded and synchronous, so designing one before `platform/api/`
exists would be guessing at its shape.

---

## 9. Release process

A release is per package and driven by a tag.

1. Bump the package's version in its own `pyproject.toml`.
2. Add a `CHANGELOG.md` entry.
3. Merge, then push a `v*.*.*` tag, which is what CI publishes on.

The library and the MCP package release together whenever the pin between them changes. Publishing
builds and uploads one package at a time:

```bash
cd lib && python -m build && twine upload dist/*
```

---

## 10. CI/CD

GitHub Actions runs the gates, since the repositories are already on GitHub and nothing here needs a
runner another platform provides.

| Stage | Trigger | Actions |
| --- | --- | --- |
| Verify | Every push and pull request | `pytest`, `ruff check`, `ruff format --check`, `pyright`, per package |
| Build | Every push to `dev` | Build each package's wheel and sdist |
| Publish | A `v*.*.*` tag | Publish each changed package to PyPI |

Verify has to pass before a merge. Publish is gated on a tag a maintainer pushes.

Two things the pipeline deliberately does not do. It does not run the benchmark suites, which need a
provider and cost money per run, so TAG-Bench stays a run someone asks for
([11-testing-strategy.md](11-testing-strategy.md)). And it enforces no coverage threshold, only
reporting the number.

---

## 11. What is deliberately absent

| Not here | Why |
| --- | --- |
| A Docker image | Nothing to containerize until there is a long-running service. The MCP server is the closest thing, and it is run from an installed package |
| A `docker-compose.yml` | Backends are a `blue-data` decision, and none is bundled |
| A REST API and a `/health` endpoint | Not in the first release. `platform/api/` is reserved |
| A hosted operator registry | The registry and router are in-process ([13-decision-log.md](13-decision-log.md#open-questions)) |
| An ingestion path | Building and populating a registry belongs to `blue-data`, and this package only reads one (ADR-003) |
| Authentication and authorization | Belongs to the deployment. The MCP server binds loopback by default and adds no auth of its own |
| Per-request limit overrides | Bounds are set when a planner is built. A deployment that needs two sets of bounds builds two planners |
