# blue-planner

`blue-planner` is the command line over `blue-data-planner`. It puts the library's two operations,
plan and run, on the command line, in the shape the Blue CLI family uses:
`blue-planner <component> <action> <parameters>`.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
bash scripts/install.sh        # ../../../blue-data, ../../../blue-data-ops, the library, the CLI
```

`blue-data` and `blue-data-ops` are checked out beside this repository
([installation.md](../../docs/installation.md)).

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

With no argument, `profile config` prompts for every required parameter, in order. With a parameter
name, it prompts for that one parameter alone. With a parameter name and a value, it sets that value
directly, except for a secret, which is never accepted on the command line: a value there would
reach the shell history. A secret is always prompted, with the input hidden, and stored either as an
obfuscated literal or as a `$VAR` reference resolved from the environment when a command runs.

```bash
blue-planner profile config OPENAI_API_KEY
# OpenAI API key (used for LLM features and embeddings): [hidden input]
```

## Parameters

| Parameter | Cast | Default | Asked by `profile config` |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | secret | None, required | Yes |
| `BLUE_DATA_REGISTRY_PATH` | file | None, required | Yes |
| `BLUE_DATA_PLANNER_MODEL` | str | `BLUE_DATA_LLM_MODEL`, then `openai/gpt-6-luna` | Yes |
| `BLUE_DATA_PLANNER_REASONING_EFFORT` | str | `low` | No |
| `BLUE_DATA_PLANNER_OPERATORS` | str | `builtin,lotus` | Yes |
| `BLUE_DATA_PLANNER_LOTUS_MODEL` | str | Unset: LOTUS uses the planner's model | No |
| `BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` | str | `none` | No |
| `BLUE_DATA_PLANNER_LOTUS_MAX_ROWS` | int | `5000` | No |
| `BLUE_DATA_PLANNER_PROVIDER_TIMEOUT_SECONDS` | int | `120` | No |
| `BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS` | int | `30` | No |
| `BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS` | int | `2` | No |
| `BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS` | int | `5` | No |
| `BLUE_DATA_PLANNER_MAX_WALL_CLOCK_SECONDS` | int | `600` | No |
| `BLUE_DATA_CLI_FORMAT` | str | `table` | No |
| `BLUE_DATA_CLI_LOG_LIMIT` | int | `80` | No |

The first row, `BLUE_DATA_CLI_FORMAT`, and `BLUE_DATA_CLI_LOG_LIMIT` are family-wide parameters,
declared once for every Blue CLI rather than by `blue-planner` alone. `BLUE_DATA_PLANNER_MODEL`
reads `BLUE_DATA_LLM_MODEL` where it is unset. LOTUS runs on the planner's model, `--model`
included, unless `BLUE_DATA_PLANNER_LOTUS_MODEL` is set, and `--model` never overrides a value set
there. Either reasoning effort takes `minimal`, `low`, `medium`, `high`, `none`, or `off`, and
anything else is refused where it is read. `off` omits the parameter from the LLM call, for a model
that does not accept it; `none` is a different, real value that litellm still forwards to the
provider, disabling reasoning rather than omitting the parameter.
`BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT` does not inherit from
`BLUE_DATA_PLANNER_REASONING_EFFORT`: it defaults to `none` on its own, since a LOTUS operation
spends many calls per row and gains little from added reasoning. A `MAX_` name is a bound on one
run: the three a run reports back in its counters, and its time limit in seconds. The bounds that
reset inside a request reach a run from a caller that builds the planner itself. `--registry-path`
and `--model` are the two settings an operator changes between one invocation and the next, so each
has its own command option in addition to its profile parameter.

The family-wide spec also contributes `ANTHROPIC_API_KEY`, `BLUE_DATA_LLM_PROVIDER`, and
`BLUE_DATA_LLM_MODEL`, all visible through `profile inspect`; `BLUE_DATA_LLM_MODEL` is required, so
`profile config` prompts for it as well as for the parameters listed above.

## Commands

`data query` runs a request end to end: it plans, covers, executes, and repairs until the request
ends, then prints the answer.

```bash
blue-planner data query "Which three countries do the most customers come from?"
```

`--explain` plans the request as a run would, searching the catalog and writing the plan again
against what it found, and prints every plan and cover up to the first operator call. It spends no
operator call.

```bash
blue-planner data query "Which three countries do the most customers come from?" --explain
```

The command takes `--registry-path PATH`, `--model NAME`, `--format table|json`, `--explain`,
`--log`, and `--log-limit N`, and reads `--profile NAME` from the root command.

The six `profile` actions manage the named sets of configuration `blue-planner` reads:

```bash
blue-planner profile add staging               # create a profile
blue-planner profile remove staging --confirm   # delete a profile and every value it holds
blue-planner profile list                       # name every profile, marking the selected one
blue-planner profile config                      # walk every required parameter
blue-planner profile inspect                     # every parameter, its value, and its source
blue-planner profile select staging              # make a profile the one later commands read
```

`profile remove` asks for confirmation before deleting a profile; `--confirm` answers that ahead of
time, which is what a script needs.

## Output and diagnostics

`--format` and `--log` choose different things. The first decides how the result is written; the
second decides whether the run behind it is reported. Every command that prints a result takes
`--format`, and reads `BLUE_DATA_CLI_FORMAT` where the option is absent.

Standard output carries the command's result: the answer, or the plan where `--explain` asked for
one. Redirecting it captures that alone. Standard error carries the progress indicator, the log,
the closing summary, and every error. Under `--format json`, standard output always holds exactly
one document: the result, or an error document if the command failed before producing one.

`--format table` renders each table of the result under its own column names, and a terminal shows a
progress indicator naming the stage that is running; a redirected stream gets none of it.
`--format json` writes one document: the result asked for, how the run ended, how each fragment
ended, what the run spent, and what the planner's own model calls cost. It holds what `--format table` leaves to
the closing summary on standard error, because a JSON document is read where no terminal is
attached. Every plan version the request produced is the one part it holds back, and that travels
where `--explain` asked for the plan as the result, or where `--log` asked for the run behind the
answer.

`--log` reports what the run did, one physical plan at a time. Its fragments come in the order the
logical plan's dependencies put them, which is the order a run could have reached them, and each
names the operator that ran it, the logical nodes it covered with the instruction each carried, the
source each scan read, the fragments whose output it read, and how it ended. A fragment that ran
carries its ending — a row count where the review kept the result, and the ending's name with the
objection where it did not — and one the pass never got to reads as "not reached under this physical
plan". A `DISCOVER` node leads the physical plan, with what its search matched. Work a later
physical plan asks for again is named rather than reported twice, since an accepted result was not
run again. A run that failed closes the log with one more line naming why.

What a narrow column cannot hold follows its table, under the id of the fragment it belongs to: the
source each scan read, the objection a review raised, the columns of the first table the fragment
produced, and the first rows of it. `--log-limit` cuts each line of the columns and the rows, and
reaches neither an instruction, a source path, a match, nor an objection, so each of those is read
whole however long it is.

Where the log is drawn is what `--format` decides. Under `--format table` it is drawn on standard
error, one table per physical plan, which is what lets an instruction wrap inside its own column.
Under `--format json` it is the plan versions added to the document on standard output, so standard
error carries the closing summary alone.

The log of `blue-planner data query "Which three countries do the most customers come from?" --log`
over the demo registry, as standard error carries it:

```text
physical plan 1   logical plan 5998143e-d764-4796-816e-48d92d882579
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Fragment               ┃ Node                   ┃ Instruction            ┃ Input ┃ Result        ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━┩
│ find_customer_country… │ find_customer_country… │ Discover the data      │       │ ok  1 matches │
│                        │ DISCOVER               │ recording customers    │       │               │
│                        │                        │ and the countries they │       │               │
│                        │                        │ come from.             │       │               │
└────────────────────────┴────────────────────────┴────────────────────────┴───────┴───────────────┘
  find_customer_country_data
      matched  /source/northwind/database/northwind/collection/public/entity/customers

physical plan 2   logical plan 2d0c5d89-189f-4698-ac36-e5bfd0cd1d3f
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Fragment           ┃ Node              ┃ Instruction        ┃ Input          ┃ Result            ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ find_customer_cou… │ find_customer_co… │ Discover the data  │                │ ok  1 matches     │
│                    │ DISCOVER          │ recording          │                │ (reused from      │
│                    │                   │ customers and the  │                │ physical plan 1)  │
│                    │                   │ countries they     │                │                   │
│                    │                   │ come from.         │                │                   │
├────────────────────┼───────────────────┼────────────────────┼────────────────┼───────────────────┤
│ fbc12ae51          │ scan_customers    │ Read the customer  │                │ ok  15 rows       │
│ builtin.scan       │ SCAN              │ entity.            │                │                   │
├────────────────────┼───────────────────┼────────────────────┼────────────────┼───────────────────┤
│ ff5883b61          │ count_customers_… │ Group customers by │ from fbc12ae51 │ ok  10 rows       │
│ builtin.aggregate  │ AGGREGATE         │ country and count  │                │                   │
│                    │                   │ them as            │                │                   │
│                    │                   │ customer_count.    │                │                   │
├────────────────────┼───────────────────┼────────────────────┼────────────────┼───────────────────┤
│ f5ece2b57          │ top_three_countr… │ Order countries by │ from ff5883b61 │ ok  3 rows        │
│ builtin.sort_supe… │ SORT_SUPERLATIVE  │ customer_count     │                │                   │
│                    │                   │ from highest to    │                │                   │
│                    │                   │ lowest and keep    │                │                   │
│                    │                   │ the top three.     │                │                   │
└────────────────────┴───────────────────┴────────────────────┴────────────────┴───────────────────┘
  fbc12ae51
      reads  scan_customers  /source/northwind/database/northwind/collection/public/entity/customers
      columns  city, company_name, contact_name, country, customer_id
      {'customer_id': 'ALFKI', 'company_name': 'Alfreds Futterkiste', 'contact_…
      {'customer_id': 'ANATR', 'company_name': 'Ana Trujillo Emparedados y hela…
      {'customer_id': 'ANTON', 'company_name': 'Antonio Moreno Taqueria', 'cont…
      {'customer_id': 'AROUT', 'company_name': 'Around the Horn', 'contact_name…
      {'customer_id': 'BERGS', 'company_name': 'Berglunds snabbkop', 'contact_n…
  ff5883b61
      columns  country, customer_count
      {'country': 'Germany', 'customer_count': 2}
      {'country': 'Mexico', 'customer_count': 3}
      {'country': 'UK', 'customer_count': 2}
      {'country': 'Sweden', 'customer_count': 1}
      {'country': 'France', 'customer_count': 2}
  f5ece2b57
      columns  country, customer_count
      {'country': 'Mexico', 'customer_count': 3}
      {'country': 'Germany', 'customer_count': 2}
      {'country': 'UK', 'customer_count': 2}

counters  operator calls 3/30   failure replans 0/2   control replans 1/5
planner   12 model calls   $0.01
ok  operator calls 3  planner model calls 12  $0.01
```

Every run closes with a summary line on standard error: the status it ended with, the operator calls
it spent, and the model calls the planner made and their cost. A model call an operator makes on its
own, as a LOTUS operation does, is not counted there; the operator reports its cost on its result.

## Errors and exit codes

An error reaches standard error as `ERROR: <CODE> <description>`. Under `--format json`, it also
reaches standard output as one document:

```json
{"error": "BLUE_DATA_REGISTRY_PATH is not configured. ...", "code": "CONFIG_INVALID"}
```

The MCP server reports its errors in the same shape.

| Code | Raised for |
| --- | --- |
| `CONFIG_INVALID` | A required parameter with no value, such as the registry path, or a parameter whose value cannot be used, such as an unknown operator suite |
| `REGISTRY_UNREADABLE` | A registry path that does not exist or does not parse |
| `PROFILE_NOT_FOUND` | A named profile that does not exist |
| `ABORTED` | A confirmation prompt, such as `profile remove` without `--confirm`, declined |
| `INVALID_PLAN`, `REPLAN_EXHAUSTED`, `BUDGET_EXHAUSTED`, `OPERATOR_FAILED`, `LLM_FAILED`, `PLANNER_FAILED`, `DEADLINE_EXCEEDED` | A run that ended with that status, carrying its failure reason |
| `INTERNAL_ERROR` | Any exception no code above covers, with its type and message. `data query --log` also prints its traceback |

A missing or rejected API key is reported by the provider on the first model call, so the run
ends as `LLM_FAILED` and the provider's message is its failure reason.

| Exit code | Meaning |
| --- | --- |
| 0 | The command succeeded; for a query action, the run ended `ok` or `planned` |
| 1 | Any error above |
| 2 | A usage error: a missing argument, an unknown option, a value outside what an option allows |

Under `--format json`, a run that ended on a failure status prints its run document, not an error
document, so standard output holds either a document with `status` or a document with `error`.

The format of an error document is the one the command would have written its result in. If the
error comes before the format is known, or the format itself cannot be resolved (for example, a
named profile that does not exist), `--format` decides, then `BLUE_DATA_CLI_FORMAT` in the
environment. A format set only in a profile does not apply in that case. `profile add`, `remove`,
`config`, and `select` take no `--format`, so their errors are always the plain line. A usage error
prints Click's usage message and exits 2, whatever `--format` says.

## TAG-Bench walkthrough

This walkthrough builds a `blue-data` registry over the TAG-Bench databases, points a profile at it,
and asks one question. The commands below have not been run as part of writing this document, so
treat the sequence as a guide to follow rather than a confirmed transcript.

```bash
# 1. Install the CLI and the library it adapts, from the repository root.
python -m venv .venv && source .venv/bin/activate
bash platform/planner-cli/scripts/install.sh

# 2. Build the registry from the TAG-Bench databases.
(cd lib && python -m tests.benchmarks.tag_bench.fetch_queries)
(cd lib && python -m tests.benchmarks.tag_bench.fetch_databases)
(cd lib && python -m tests.benchmarks.tag_bench.register_databases)

# 3. Configure a profile. The registry path is stored as given, so it is absolute; the API key is
#    prompted, never passed as an argument.
blue-planner profile config BLUE_DATA_REGISTRY_PATH "$PWD/data/tag_bench/registry.json"
blue-planner profile config BLUE_DATA_PLANNER_OPERATORS builtin
blue-planner profile config OPENAI_API_KEY
blue-planner profile inspect

# 4. Write a plan without running it.
blue-planner data query "Among the schools with the average score in Math over 560 in the SAT test, how many schools are in counties in the bay area?" --explain

# 5. Run it.
blue-planner data query "Among the schools with the average score in Math over 560 in the SAT test, how many schools are in counties in the bay area?" --log
```

## Running tests

```bash
cd platform/planner-cli
python -m pytest
```

`install.sh` installs `pytest` with the CLI. The tests replace the planner and the model with
doubles, so they run offline and need no API key.
