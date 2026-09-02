# Repo Organization Template

A standard skeleton for projects that ship a Python library alongside one or more deployed platform services (REST API, MCP server, CLI, etc.).

---

## Directory Tree

```
<project-root>/
│
├── design                               # internal documentation for design and development
├── docs/
│   └── <components>/                    # external documentation, organized by components
│
├── lib/                                 # Python library (PyPI package)
│   ├── pyproject.toml                   # package metadata, dependencies, tool config
│   ├── scripts/
│   │   ├── build.sh                     # build the distribution package
│   │   └── publish.sh                   # publish to PyPI
│   ├── src/
│   │   └── <lib_package>/
│   │       ├── __init__.py
│   │       ├── types.py                 # shared types and data contracts
│   │       └── <module>/               # one directory per architecture layer
│   │           ├── __init__.py
│   │           └── <component>.py
│   └── tests/
│       ├── conftest.py                  # shared fixtures and test doubles
│       └── unit/
│           └── <module>/
│
└── platform/                            # Deployed interface services
    └── <service>/                       # one directory per interface
        ├── pyproject.toml               # depends on lib; service-specific deps
        ├── Dockerfile                   # omit for non-containerized services
        ├── scripts/
        │   ├── build.sh                 # build service artifact
        │   └── deploy.sh                # deploy service to target environment
        ├── src/
        │   └── <service_package>/
        │       ├── __init__.py
        │       └── <adapter>.py         # thin adapter over lib — no business logic
        └── tests/
            └── unit/
```

---

## Principles

**`lib/` is the single source of truth for business logic.** All domain code lives here. Platform services import from `lib` and add only the translation layer required by their interface (HTTP routing, MCP tool definitions, CLI argument parsing).

**Each platform service is independently deployable.** Services have their own `pyproject.toml`, `Dockerfile`, and `scripts/`. They can be built, versioned, and deployed on separate schedules.

**`scripts/` is consistent across all components.** Every component (lib and each platform service) has a `scripts/` directory with the same two roles: build and publish/deploy. This keeps automation uniform regardless of the component type.

**Tests are co-located with the code they test.** Library tests live under `lib/tests/`. Platform adapter tests live under `platform/<service>/tests/`. Platform tests import shared fixtures and test doubles from `lib/tests/` rather than duplicating them.

**`types.py` owns the data contracts.** Shared types used across layers and by platform adapters are defined in `lib/src/<lib_package>/types.py`. This prevents circular imports and provides a single location to update when contracts change.

---

## `scripts/` Reference

| Component | `build.sh` | `deploy.sh` / `publish.sh` |
|-----------|-----------|---------------------------|
| `lib/` | `python -m build` (wheel + sdist) | `twine upload` to PyPI |
| Containerized service | `docker build` | Push image + deploy to target |
| CLI / distributable | Build package or standalone binary | Publish to PyPI or release channel |
