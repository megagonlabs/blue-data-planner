# Building the Northwind demo registry

Two scripts, run in order, produce the registry the examples and the quickstart plan against.
Nothing they write is committed: `data/` is in `.gitignore`, so a clean checkout rebuilds it.

Run them from the repository root, so the default `--data-dir` of `data/demo` lands where the
examples look:

```bash
python lib/scripts/demo/fetch_northwind.py     # data/demo/*.sql, *.json
python lib/scripts/demo/register_northwind.py  # data/demo/northwind.db, registry.json
```

Each script skips its work when its output is already there. `--force` rebuilds.

| Script | Writes | Notes |
| --- | --- | --- |
| `fetch_northwind.py` | `northwind_sqlite.sql`, `northwind_descriptions.json` | Copies the two files out of `../blue-data/data`, the `blue-data` checkout the install scripts also install from. `--blue-data-data-dir` names another one |
| `register_northwind.py` | `northwind.db`, `registry.json` | Builds the database from the seed, registers it with `blue-data`, syncs it, collects statistics, and writes the fetched attribute descriptions onto it |

Neither script calls a model: syncing and statistics are deterministic, and the
descriptions are the ones `blue-data` generated once and committed beside the seed.

## What the registry holds

One source, `northwind`, holding one database of seven tables. SQLite has no schemas, so
`blue-data` reports a single collection called `public`, and a catalog path reads
`source → database → public → entity → attribute`. SQLite's own `sqlite_sequence` table is
dropped, so only the Northwind tables are searchable.

`registry.json` holds metadata only, about 85 KB. Each attribute record carries its sample values,
distinct and null counts, min and max, and the description. The rows stay in SQLite: a reopened
registry reconnects to `northwind.db` and answers real queries, which is why the recorded database
directory is absolute.
