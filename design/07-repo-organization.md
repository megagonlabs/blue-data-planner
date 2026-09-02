# $PROJECT_NAME$ — Repo Organization

## Directory Tree

<!-- 
Provide a more specific repo directory structure and file contents.
-->

<!--
Example:

```
blue-data-user-source/
│
├── docs/
│   └── design/                          # design documents
│
├── lib/                                 # Python library (userdb PyPI package)
│   ├── pyproject.toml                   # package metadata, dependencies, tool config
│   ├── scripts/
│   │   ├── build.sh                     # build the distribution package
│   │   └── publish.sh                   # publish to PyPI
│   ├── src/
│   │   └── blue_data_user_source/
│   │       ├── __init__.py              # re-exports UserSource
│   │       ├── source.py                # UserSource — NLQI and main entry point
│   │       ├── types.py                 # shared result types and query models
│   │       │
│   │       ├── reasoning/
│   │       │   ├── __init__.py
│   │       │   ├── reasoner.py          # UserQueryReasoner
│   │       │   └── metric.py            # UserDataMetric
│   │       │
│   │       ├── data/
│   │       │   ├── __init__.py
│   │       │   ├── manager.py           # UserDataManager
│   │       │   ├── planner.py           # UserDataPlanner
│   │       │   └── registry.py          # UserDataRegistry
│   │       │
│   │       └── interaction/
│   │           ├── __init__.py
│   │           ├── contact.py           # ContactManager, ContactMethod
│   │           └── manager.py           # InteractionManager
│   │
│   └── tests/
│       ├── conftest.py                  # shared fixtures and in-memory doubles
│       ├── cassettes/                   # recorded LLM HTTP responses (VCR)
│       └── unit/
│           ├── reasoning/
│           ├── data/
│           └── interaction/
│
└── platform/                            # External interface services
    ├── api/                             # REST API service
    │   ├── pyproject.toml
    │   ├── Dockerfile
    │   ├── scripts/
    │   │   ├── build.sh                 # build Docker image
    │   │   └── deploy.sh                # deploy service
    │   ├── src/
    │   │   └── blue_data_user_api/
    │   │       ├── __init__.py
    │   │       ├── app.py               # FastAPI app factory
    │   │       └── routes.py            # /query, /import, /databases endpoints
    │   └── tests/
    │       └── unit/
    │
    ├── mcp/                             # MCP server service
    │   ├── pyproject.toml
    │   ├── Dockerfile
    │   ├── scripts/
    │   │   ├── build.sh
    │   │   └── deploy.sh
    │   ├── src/
    │   │   └── blue_data_user_mcp/
    │   │       ├── __init__.py
    │   │       └── server.py            # MCP server and tool definitions
    │   └── tests/
    │       └── unit/
    │
    └── cli/                             # CLI tool
        ├── pyproject.toml
        ├── scripts/
        │   ├── build.sh                 # build distributable
        │   └── deploy.sh                # publish CLI package
        ├── src/
        │   └── blue_data_user_cli/
        │       ├── __init__.py
        │       └── commands.py          # Click command groups
        └── tests/
            └── unit/
```
-->
---

## Layout Rationale

<!-- 
Provide high-level guidance on repo structure, useful for expanding the directory structure as work continues.
-->

<!-- 
Example:

### `lib/` — the Python library

All core business logic lives under `lib/src/`. This is the only artifact published to PyPI. The `src/` layout within `lib/` prevents the package from being accidentally importable from the project root during development.

`lib/scripts/` provides two scripts:

| Script | Purpose |
|--------|---------|
| `build.sh` | Builds the wheel and sdist via `python -m build` |
| `publish.sh` | Publishes to PyPI via `twine upload` |

### Module-to-architecture mapping

| Module | Architecture layer |
|--------|--------------------|
| `source.py` | Natural Language Query Interface |
| `reasoning/` | Reasoning Layer |
| `data/` | Data Management Layer |
| `interaction/` | Interaction Layer |

### `platform/` — external interface services

Each external interface is an independent deployable unit under `platform/`. They depend on `lib` but contain no business logic — each is a thin adapter over `UserSource`. Separating them from `lib` keeps the library free of server and CLI dependencies and allows each service to be built, versioned, and deployed independently.

Each platform service follows the same internal structure:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata and dependencies (includes `lib` as a dependency) |
| `Dockerfile` | Container image definition (api and mcp only) |
| `scripts/build.sh` | Builds the service artifact (Docker image or distribution package) |
| `scripts/deploy.sh` | Deploys the service to the target environment |
| `src/<package>/` | Interface adapter code |
| `tests/unit/` | Unit tests for the adapter layer |

### `types.py`

Shared result types and query models used across layers are defined in `lib/src/blue_data_user_source/types.py`. This prevents circular imports and provides a single source of truth for data contracts between components.

### `tests/`

Library tests live under `lib/tests/`. Each platform service has its own `tests/` directory for adapter-layer tests.

```
lib/tests/
├── conftest.py          # shared fixtures: UserSource instance, in-memory doubles
├── cassettes/           # VCR cassettes for recorded LLM responses
└── unit/
    ├── reasoning/        # UserQueryReasoner, UserDataMetric
    ├── data/             # UserDataManager, UserDataPlanner, UserDataRegistry
    └── interaction/      # ContactManager, InteractionManager
```

Platform adapter tests use the same in-memory doubles (imported from `lib/tests/`) and test only the translation layer: request parsing, response serialization, and error mapping.
-->