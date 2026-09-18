# blue-planner

`blue-planner` is the command line over `blue-data-planner`. It puts the library's two operations,
plan and run, on the command line, in the shape the Blue CLI family uses:
`blue-planner <component> <action> <parameters>`.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../../lib       # the library this CLI adapts
pip install -e .               # the CLI itself
```

The default profile's operator suite (`BLUE_DATA_PLANNER_OPERATORS`) includes `lotus`, and LOTUS is
not among the library's runtime dependencies: it comes in only through the `lotus` extra, and
without it `blue-planner` registers no LOTUS operators and raises no error saying why. See
[`lib/README.md`](../../lib/README.md)'s Install section for the exact command and the environment
constraints LOTUS brings with it.

## Profile

`blue-planner` resolves each setting in one order: command option, then environment variable, then
profile, then default. `profile inspect` reports which tier supplied each value.

```bash
blue-planner profile config
```

With no argument, `profile config` prompts for every parameter marked as asked, in order. With a
parameter name, it prompts for that one parameter alone. With a parameter name and a value, it sets
that value directly, except for a secret, which is never accepted on the command line: a value
there would reach the shell history. A secret is always prompted, with the input hidden, and stored
either as an obfuscated literal or as a `$VAR` reference resolved from the environment when a command
runs.

```bash
blue-planner profile config OPENAI_API_KEY
# OpenAI API key (used for LLM features and embeddings): [hidden input]
```

## Parameters

| Parameter | Cast | Default | Asked by `profile config` |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | secret | None, required | Yes |
| `BLUE_DATA_REGISTRY_PATH` | file | None, required | Yes |
| `BLUE_DATA_PLANNER_MODEL` | str | `BLUE_DATA_LLM_MODEL`, then `openai/gpt-4o` | Yes |
| `BLUE_DATA_PLANNER_OPERATORS` | str | `builtin,lotus` | Yes |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | str | Inherits `BLUE_DATA_PLANNER_MODEL` | No |
| `BLUE_DATA_PLANNER_LOTUS_MAX_ROWS` | int | `5000` | No |
| `BLUE_DATA_PLANNER_ENABLE_NL2TABLE` | bool | `false` | No |
| `BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS` | int | `30` | No |
| `BLUE_DATA_PLANNER_MAX_PLAN_CORRECTIONS` | int | `5` | No |
| `BLUE_DATA_PLANNER_MAX_PARAMETER_REPAIRS` | int | `3` | No |
| `BLUE_DATA_PLANNER_MAX_COVER_REPAIRS` | int | `2` | No |
| `BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS` | int | `2` | No |
| `BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS` | int | `2` | No |
| `BLUE_DATA_CLI_FORMAT` | str | `table` | No |
| `BLUE_DATA_CLI_LOG_LIMIT` | int | `80` | No |

The first row and the two `BLUE_DATA_CLI_` rows are family-wide parameters, declared once for
every Blue CLI rather than by `blue-planner` alone. `BLUE_DATA_PLANNER_MODEL` reads
`BLUE_DATA_LLM_MODEL` where it is unset, and `BLUE_DATA_PLANNER_LOTUS_MODEL` reads
`BLUE_DATA_PLANNER_MODEL` the same way. A `MAX_` name is a bound on one run; `--registry` and
`--model` are the two settings an operator changes between one invocation and the next, so each has
its own command option in addition to its profile parameter.

The family-wide spec also contributes `ANTHROPIC_API_KEY`, `BLUE_DATA_LLM_PROVIDER`, and
`BLUE_DATA_LLM_MODEL`, all visible through `profile inspect`; `BLUE_DATA_LLM_MODEL` is required, so
`profile config` prompts for it as well as for the parameters listed above.

## Commands

`data query` runs a request end to end: it plans, covers, executes, and repairs until the request
ends, then prints the answer.

```bash
blue-planner data query "Which Bay Area data scientist postings are at healthcare companies?"
```

`plan query` writes one logical plan and one cover, prints them, and spends no operator call.

```bash
blue-planner plan query "Which Bay Area data scientist postings are at healthcare companies?"
```

Both commands take `--registry PATH`, `--model NAME`, `--format table|json`, `--log`, and
`--log-limit N`, and both read `--profile NAME` from the root command.

The six `profile` actions manage the named sets of configuration `blue-planner` reads:

```bash
blue-planner profile add staging               # create a profile
blue-planner profile remove staging --confirm   # delete a profile and every value it holds
blue-planner profile list                       # name every profile, marking the selected one
blue-planner profile config                      # walk every parameter marked as asked
blue-planner profile inspect                     # every parameter, its value, and its source
blue-planner profile select staging              # make a profile the one later commands read
```

`profile remove` asks for confirmation before deleting a profile; `--confirm` answers that ahead of
time, which is what a script needs.

## Output and diagnostics

Standard output carries the result, so redirecting it captures the answer alone. Standard error
carries the progress indicator, the trace, the closing summary, and every error.

`--format table` renders each table in the answer under its own column names. `--format json` writes
the full result as one JSON object, and nothing else reaches standard output. While a run is in
flight, a terminal shows a progress indicator naming the stage that is running; a redirected stream
gets none of it.

`--log` prints the run's trace after the run, grouped by cover: one line per fragment, naming the
logical nodes it covered, the operator that ran it, and how it ended, as one of "ok" with a row
count, "ok (discovery)", or "no result under this cover". A run that failed closes the trace with
one more line naming why. Under `--format json` the trace is one JSON object on standard error
instead. `--log-limit` truncates each trace line, since that closing failure reason has no fixed
length.

```text
cover 1
  f1  n2,n3   builtin.scan        ok (140 rows)
  f2  n4,n5   lotus.sem_filter    no result under this cover
cover 2
  f1  n2,n3   builtin.scan        ok (140 rows)
  f2  n4,n5   lotus.sem_filter    ok (11 rows)
counters  operator calls 7/30   failure replans 0/2   control replans 1/2
model     11 calls   $0.42
```

Every run closes with a summary line on standard error: the status it ended with, the operator calls
and model calls it spent, and their cost.

## Errors and exit codes

An error reaches standard error as `ERROR: <CODE> <description>`.

| Code | Raised for |
| --- | --- |
| `CONFIG_MISSING` | A required parameter with no value: the registry path, the API key |
| `CONFIG_INVALID` | A parameter whose value cannot be used, such as an unknown operator suite |
| `REGISTRY_UNREADABLE` | A registry path that does not exist or does not parse |
| `PROFILE_NOT_FOUND` | A named profile that does not exist |
| `ABORTED` | A confirmation prompt, such as `profile remove` without `--confirm`, declined |
| `INVALID_PLAN`, `REPLAN_EXHAUSTED`, `BUDGET_EXHAUSTED`, `OPERATOR_FAILED`, `LLM_FAILED`, `PLANNER_FAILED` | A run that ended with that status, carrying its failure reason |
| `INTERNAL_ERROR` | An exception no command expected |

| Exit code | Meaning |
| --- | --- |
| 0 | The command succeeded; for a query action, the run ended `ok` or `planned` |
| 1 | Any error above |
| 2 | A usage error: a missing argument, an unknown option, a value outside what an option allows |

Under `--format json`, a run that produced a result prints that result on standard output whatever
its status, so a failed run stays machine-readable. An error outside a run, such as a configuration
failure, an internal error, or a declined confirmation, always prints as the plain
`ERROR: <CODE> <description>` line on standard error; `--format` has no effect on it.

## TAG-Bench walkthrough

This walkthrough builds a `blue-data` registry over the TAG-Bench databases, points a profile at it,
and asks one question. The commands below have not been run as part of writing this document, so
treat the sequence as a guide to follow rather than a confirmed transcript.

```bash
# 1. Build the registry from the TAG-Bench databases, run from the repository root.
python lib/scripts/tag_bench/fetch_queries.py
python lib/scripts/tag_bench/fetch_databases.py
python lib/scripts/tag_bench/register_databases.py

# 2. Install the CLI, pointed at the library it adapts.
cd platform/cli
./scripts/install.sh

# 3. Configure a profile. The API key is prompted, never passed as an argument.
blue-planner profile config BLUE_DATA_REGISTRY_PATH ../../data/tag_bench/registry.json
blue-planner profile config BLUE_DATA_PLANNER_OPERATORS builtin
blue-planner profile config OPENAI_API_KEY
blue-planner profile inspect

# 4. Write a plan without running it.
blue-planner plan query "Among the schools with the average score in Math over 560 in the SAT test, how many schools are in counties in the bay area?"

# 5. Run it.
blue-planner data query "Among the schools with the average score in Math over 560 in the SAT test, how many schools are in counties in the bay area?" --log
```
