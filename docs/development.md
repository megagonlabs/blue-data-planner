# Development setup

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.11 or later | Declared in [lib/pyproject.toml](../lib/pyproject.toml) |
| `uv` | Used for every local command. See [the uv install guide](https://docs.astral.sh/uv/getting-started/installation/) |
| SSH access to `megagon-internal` | `uv` clones both dependencies over SSH, using the local ssh agent |

## Creating the environment

Every command runs from `lib/`, which holds the library package and its `pyproject.toml`.

`blue-data-planner` depends on two packages published to no index:

- `blue-data-ops`: the operators and the operator registry.
- `blue-data`: the data registry.

Both are declared as git sources in [lib/pyproject.toml](../lib/pyproject.toml), so `uv` clones them
at a pinned commit. No particular directory layout is required.

```bash
cd lib
uv sync --extra dev
```

This creates `.venv`, clones the two dependencies at their pinned commits into the `uv` cache, and
installs the planner package in editable mode.

## Installing the `lotus` extra

The `lotus` extra adds six semantic operators built on LOTUS: filtering, projection, extraction,
aggregation, top-k, and join, each driven by a language model. It narrows numpy below 2, pandas
below 3, and the Python range to 3.11 or 3.12. It gets its own environment, built beside the
default one instead of replacing it:

```bash
cd lib
UV_PROJECT_ENVIRONMENT=.venv-lotus uv sync --extra lotus --extra dev
```

`uv.lock` resolves the extra as a separate fork (`[tool.uv] conflicts` in
[lib/pyproject.toml](../lib/pyproject.toml)), so a plain `uv sync` into the default `.venv` keeps
whatever numpy and pandas `blue-data`/`blue-data-ops` pull in, unaffected by the extra's narrower
bounds. One consequence: `uv sync --all-extras --all-groups` is not a valid combination, because
the fork's conflict declaration forbids selecting the `lotus` extra and its paired placeholder
group together.

Every later `uv run`, including `uv run pytest`, needs that same `UV_PROJECT_ENVIRONMENT` setting
to reach `.venv-lotus`. uv reads it fresh each time rather than remembering it from the sync.
Export it for the session, or repeat it per command:

```bash
export UV_PROJECT_ENVIRONMENT=.venv-lotus
uv run pytest tests/lotus
```

Without it, `uv run pytest tests/lotus` runs against the default `.venv`, where LOTUS is not
installed, and the whole tier skips itself rather than failing. A test that calls a model skips on
its own too, unless `OPENAI_API_KEY` (or whichever key the configured model needs) is set.

## Running the tests

```bash
uv run pytest
```

## Quality check

All four pass before work is reported as complete.

| Gate | Command |
| --- | --- |
| Format | `uv run ruff format .` |
| Lint | `uv run ruff check .` |
| Types | `uv run pyright` |
| Tests | `uv run pytest` |

## Sanity checks

Two commands confirm the environment resolves the way it should.

The first prints where each package is imported from:

```bash
uv run python -c "import blue_data_planner as p, blue_data_ops as o, blue_data as d; print(p.__file__); print(o.__file__); print(d.__file__)"
```

`blue_data_planner` points into this repository's `lib/src/`, because it is installed editable. The
other two point into the `uv` cache under `git-v0/checkouts/`. A path under `site-packages` instead
means a stale non-editable copy is shadowing the source, which makes tests run against the wrong
code while still appearing to pass.

The second runs a fast subset, 44 tests in about a second:

```bash
uv run pytest tests/unit/data -q
```

## How to update a dependency on `blue-data` or `blue-data-ops`

Both dependencies are pinned to a commit, so new commits pushed to `blue-data` or `blue-data-ops`
have no effect until the pin moves.

1. Find the commit to move to. The repository's commit list on GitHub
   (`github.com/megagon-internal/blue-data-ops/commits/dev`) has a "copy full SHA" button, and the
   existing pins are full SHAs. Reading it from the terminal works too:

    ```bash
    git ls-remote git@github.com:megagon-internal/blue-data-ops.git dev
    ```

2. Replace that source's `rev` under `[tool.uv.sources]` in
   [lib/pyproject.toml](../lib/pyproject.toml).

3. Re-sync and run the gates:

    ```bash
    uv sync --extra dev
    uv run pytest
    ```

The version specifier under `dependencies` usually stays as it is, because a package version does
not necessarily change when commits are pushed. When `uv sync` fails on that specifier, the
dependency did change version, and matching the specifier to it is part of the same edit.
