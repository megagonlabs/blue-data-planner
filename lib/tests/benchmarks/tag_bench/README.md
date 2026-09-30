# Building the TAG-Bench inputs

Three scripts, run in order, produce everything the TAG-Bench harness reads. None of it ships in the
package and none of it is committed: `data/` is in `.gitignore`, so a clean checkout rebuilds the
inputs instead of carrying them.

Run them as modules from `lib/`. Each writes under `<repository>/data/tag_bench/` unless given
`--data-dir`, whatever the working directory:

```bash
python -m tests.benchmarks.tag_bench.fetch_queries       # data/tag_bench/queries.json
python -m tests.benchmarks.tag_bench.fetch_databases     # data/tag_bench/databases/*.db
python -m tests.benchmarks.tag_bench.register_databases  # data/tag_bench/registry.json
```

Each script skips its work when its output is already there. `--force` rebuilds.

| Script | Writes | Notes |
| --- | --- | --- |
| `fetch_queries.py` | `queries.json` | Downloads the released query CSV, corrects the known errors in it, and assigns each query to the dev or the test split. The download itself is never edited |
| `fetch_databases.py` | `databases/*.db` | Downloads the BIRD dev set (about 1 GB) and keeps the five databases the queries name |
| `register_databases.py` | `registry.json` | Registers those databases with `blue-data`, syncs them, collects statistics, and infers semantic types |

## What the registry holds

One source, `tag_bench`, holding all five databases. SQLite has no schemas, so `blue-data` reports a
single collection called `public`, and a catalog path reads
`source → database → public → entity → attribute`.

The `.db` files are never converted. `registry.json` holds metadata only, about 5 MB for 5
databases, 39 tables, and 480 columns. Each column record carries its sample values, distinct and
null counts, min and max, and an inferred semantic type. The rows stay in SQLite: a reopened
registry reconnects to the files and answers real queries, which is why the recorded database
directory is absolute.

Semantic types are inferred by pattern matching, with no model involved. `--describe` also asks a
model for plain-language table and column descriptions, one call per table plus several per column.
It needs a provider key in the environment. `--reasoning-effort`
controls how hard that model reasons, and defaults to `none`; `off` omits the parameter entirely.

```bash
python -m tests.benchmarks.tag_bench.register_databases --force --describe --model gpt-6-luna
```

Keeping one registry of each kind is deliberate. Planning quality depends on how well a source was
enriched, and the registry without descriptions is the "nobody enriched this" case the design
promises to work with.
