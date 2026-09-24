# TAG-Bench benchmark

TAG-Bench is a set of natural-language questions over five BIRD SQLite databases. It measures the
planner's answer accuracy end to end, against a target of 60% exact match
([design/11-testing-strategy.md](../design/11-testing-strategy.md#8-the-benchmark)). The planner is
tested against it running LOTUS operators, since LOTUS is established and TAG-Bench is designed to
be solvable with it.

Sixty of its eighty questions carry a gold answer: the Match, Comparison, and Ranking ones. The
twenty aggregation questions ask for a summary and carry an empty answer, so exact match cannot
score them, and the benchmark leaves them out.

None of the benchmark's input data ships in the package or is committed; a clean checkout rebuilds
it. Building the inputs is three scripts, and running the benchmark over them is a fourth entry
beside them.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| The library environment | `cd lib && uv sync --extra dev`, per [lib/README.md](../lib/README.md#development-setup) |
| The `lotus` extra | Needed to run the benchmark itself, since LOTUS supplies the operators it is solved with. See [lib/README.md](../lib/README.md#the-lotus-environment) |
| A model provider key | Needed for `--describe` below and for running the benchmark. Set in `.env`, read through `python-dotenv` |
| About 1 GB of disk | `fetch_databases.py` downloads the BIRD dev set |

## Building the input data

Three scripts, run from the repository root in order, produce everything under `data/tag_bench/`.
Each one skips its work when its output already exists; `--force` rebuilds it. Full detail on each
script, including the registry's record shape, is in
[lib/scripts/tag_bench/README.md](../lib/scripts/tag_bench/README.md).

```bash
uv run --project lib python lib/scripts/tag_bench/fetch_queries.py       # queries.json
uv run --project lib python lib/scripts/tag_bench/fetch_databases.py     # databases/*.db
uv run --project lib python lib/scripts/tag_bench/register_databases.py  # registry.json
```

| Script | Writes | Does |
| --- | --- | --- |
| `fetch_queries.py` | `queries.json` | Downloads the released query CSV, corrects its known errors, and assigns each query to the dev or the test split, leaving the download itself untouched |
| `fetch_databases.py` | `databases/*.db` | Downloads the BIRD dev set and keeps the five databases the queries name |
| `register_databases.py` | `registry.json` | Registers those databases with `blue-data`, syncs them, collects statistics, and infers semantic types |

After all three, `data/tag_bench/` holds:

```
data/tag_bench/
├── queries.json     # corrected TAG-Bench queries, their gold answers, and their splits
├── databases/
│   └── *.db         # the five BIRD SQLite databases the queries name
└── registry.json    # blue-data catalog metadata over those databases
```

The `.db` files are never converted: `registry.json` holds catalog metadata only, and a reopened
registry reconnects to the SQLite files to answer real queries.

## Adding model-written descriptions

By default, `register_databases.py` infers each column's semantic type by pattern matching, with no
model involved. Passing `--describe` additionally asks a model for a plain-language description of
each table and column, one call per table plus several per column:

```bash
uv run --project lib python lib/scripts/tag_bench/register_databases.py --force --describe \
    --model gpt-5.6-luna --reasoning-effort none
```

`--describe` needs a model, given by `--model` or the `BLUE_DATA_OPS_MODEL` environment variable,
and a provider key that model's client accepts (`OPENAI_API_KEY` for an OpenAI model).
`--reasoning-effort` sets how hard that model reasons over each description, and defaults to
`none`; `off` omits the parameter entirely, for a model that rejects it. Keeping one registry built
without `--describe` around is deliberate: it is the "nobody enriched this" case the design commits
to handling, and planning quality against it is one of the things the benchmark is meant to
measure.

## Running the benchmark

The harness lives at `lib/scripts/tag_bench/benchmark/`, beside the three build scripts, and runs as
a module rather than through `pytest`. It needs the environment carrying the `lotus` extra
([lib/README.md](../lib/README.md#the-lotus-environment)) and a provider key:

```bash
cd lib && export UV_PROJECT_ENVIRONMENT=.venv-lotus
uv run --extra lotus --extra dev python -m scripts.tag_bench.benchmark run --split dev --workers 4
```

A run spends paid model calls for as long as its selection takes, so it is started by a person who
watches it. One line reaches standard error as each query finishes, and the record is written at the
same moment, so a run that stops keeps what it finished.

| Option | Default | Selects |
| --- | --- | --- |
| `--split` | `dev` | `dev`, `test`, or `all` |
| `--query-type` | `Match Comparison Ranking` | The query types to score, case-insensitive |
| `--query-id` | — | One or more query ids, which override `--split` and `--query-type` |
| `--limit` | — | The first N of the selection, for a smoke run |
| `--workers` | 4 | Worker processes. `1` runs in the calling process, so a breakpoint works |
| `--run-name` | a timestamp and the model | The directory under `data/tag_bench/runs/`. One already holding records is refused |
| `--data-dir` | `<repository>/data/tag_bench` | Where the inputs and the run directories live |
| `--registry` | `<data-dir>/registry.json` | The registry to plan over |
| `--model`, `--reasoning-effort` | `openai/gpt-5.6-luna`, `low` | The model every planner component calls, LOTUS included. `off` omits the parameter |
| `--lotus-max-rows` | 5000 | Rows a LOTUS operation accepts before refusing the call |

There is no resume: a stopped run is summarized with `report` for what it holds, and the queries
that never ran go in a run of their own, named with `--query-id`.

A first run is worth keeping small:

```bash
uv run --extra lotus --extra dev python -m scripts.tag_bench.benchmark run \
    --split dev --limit 1 --workers 1
```

A selection covers only the queries a gold answer can score, so the twenty aggregation queries are
never in one. Naming one with `--query-id` runs it anyway, which is how its plan is inspected; its
record says the gold answer is empty and scores it wrong.

### What a run writes

```
data/tag_bench/runs/<run-name>/
├── config.json                     # every resolved setting, the query ids, the commit
├── query_<id>.json                 # the scored outcome, the plans, and what the query spent
├── query_<id>.conversations.json   # that query's model transcripts
└── summary.json                    # exact match and the run-level measurements
```

Triage starts with `query_<id>.json`: it names the status the run ended in, the answer that was
extracted, every plan version, and each fragment's result. The transcripts sit in their own file
because they are large.

### Reading a report

`report` re-aggregates the records already on disk and reaches no model, so it costs nothing to
repeat:

```bash
uv run --extra dev python -m scripts.tag_bench.benchmark report --run-name <run-name>
```

It rewrites `summary.json` and prints exact match overall and per query type, the operator and model
calls a query spent, the cost, and how the runs ended. The target is 60% exact match over the Match,
Comparison, and Ranking queries of the test split
([design/11-testing-strategy.md](../design/11-testing-strategy.md#8-the-benchmark)).
