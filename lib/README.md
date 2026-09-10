# blue-data-planner

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # the library
pip install -e '.[dev]'     # + the test and lint gates
pip install -e '.[lotus]'   # + the LOTUS operator suite
```

The `lotus` extra narrows the environment around it: LOTUS holds numpy below 2, pandas below 3,
and Python below 3.13, and it brings a machine-learning stack of several gigabytes. Install it in
an environment of its own rather than beside a project that needs numpy 2.

## Tests

```bash
uv run pytest                      # the unit tier: fast, offline, no API key
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

The LOTUS tier runs against an installed suite and is excluded from the default run:

```bash
UV_PROJECT_ENVIRONMENT=.venv-lotus uv sync --extra lotus --extra dev
UV_PROJECT_ENVIRONMENT=.venv-lotus uv run --extra lotus --extra dev pytest tests/lotus
```

Tests in that tier that reach a model skip themselves without `OPENAI_API_KEY`.
