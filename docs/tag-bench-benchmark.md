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
it. The harness that runs requests against it and scores the answers is not implemented yet
(tracked under issue #4). This document covers what already works: building the input data. A
section on running the benchmark will follow once the harness exists.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| The library environment | `cd lib && uv sync --extra dev`, per [development.md](development.md) |
| The `lotus` extra | Needed to run the benchmark itself, since LOTUS supplies the operators it is solved with. See [development.md](development.md#installing-the-lotus-extra) |
| A model provider key | Needed for `--describe` below, and for running the benchmark once the harness exists. Set in `.env`, read through `python-dotenv` |
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
uv run --project lib python lib/scripts/tag_bench/register_databases.py --force --describe --model gpt-5.6-luna
```

`--describe` needs a model, given by `--model` or the `BLUE_DATA_OPS_MODEL` environment variable,
and a provider key that model's client accepts (`OPENAI_API_KEY` for an OpenAI model). Keeping one
registry built without `--describe` around is deliberate: it is the "nobody enriched this" case the
design commits to handling, and planning quality against it is one of the things the benchmark is
meant to measure.

## Running the benchmark

Not yet implemented. Once the harness lands, this section will cover invoking it and reading its
report. For the shape it is expected to take, see
[design/07-repo-organization.md](../design/07-repo-organization.md#where-the-benchmark-lives) and
[design/11-testing-strategy.md](../design/11-testing-strategy.md#8-the-benchmark): the harness lives
at `lib/scripts/tag_bench/benchmark/` beside the three build scripts, runs as a module rather than
through `pytest`, and writes one record per query under `data/tag_bench/runs/`.
