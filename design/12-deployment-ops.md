# $PROJECT_NAME$ — Deployment / Ops

## Overview

$PROJECT_NAME$  is distributed in two forms:

- **PyPI package** — the Python library, for embedding in other Python applications
- **Docker image** — packages the REST API and MCP server for standalone deployment

Production deployments are self-hosted. Operators provision and manage their own storage backends and observability infrastructure. UserDb emits to standard interfaces and does not assume any specific platform.

---

## 1. Versioning

UserDb follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`). Version is declared once in `pyproject.toml` and read from there by all build tooling.

- **PATCH** — bug fixes, no interface changes
- **MINOR** — backwards-compatible new features
- **MAJOR** — breaking changes to the Python library API or data model

---

## 2. PyPI Package

The python package exposes the Python library. It does not include the REST API, MCP server, or CLI by default. Those are available as optional extras:

Example:

```
pip install userdb               # library only
pip install userdb[server]       # library + REST API + MCP server
pip install userdb[cli]          # library + CLI
pip install userdb[all]          # everything
```

---

## 3. Docker Image

The docker image runs both the REST API (Uvicorn/FastAPI) and the MCP server as a combined process. The image is published to a container registry alongside each release.

| Port | Service |
|------|---------|
| `8000` | REST API (HTTP) |
| `8001` | MCP Server |

**Base image:** `python:3.12-slim`

**Entrypoint:** starts both services; each can be disabled via environment variable (see §5).

**Health check:** `GET /health` on port 8000.

---

## 4. Storage Backends

<!-- Describe storage backends
-->

<!--
Example:
UserDb does not bundle or manage storage backends. The operator provisions:

- A **JSON backend** (e.g., Redis, MongoDB) and provides its connection details via environment variables
- A **Vector backend** (e.g., ChromaDB) and provides its connection details via environment variables

Both backends must be available before UserDb starts. UserDb will fail at startup if a backend is unreachable.
-->

### Reference docker-compose

A `docker-compose.yml` is provided in the repository root for local development and operator reference. It starts UserDb alongside minimal backend instances.

Example:

```yaml
services:
  userdb:
    image: userdb-server:latest
    ports:
      - "8000:8000"
      - "8001:8001"
    env_file: .env
    depends_on:
      - redis
      - chromadb

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  chromadb:
    image: chromadb/chroma:latest
    ports:
      - "8002:8000"
```

---

## 5. Configuration

All configuration is via environment variables. There are no configuration files — operators use `.env` files, secrets managers, or platform-native injection.

<!-- Describe config properties
-->

<!--
Example: 

### Core

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `USERDB_DATABASE` | No | — | Default user database ID used when none is specified per request |

### Storage

| Variable | Required | Description |
|----------|----------|-------------|
| `USERDB_JSON_BACKEND_URL` | Yes | Connection URL for the JSON backend (e.g., `redis://localhost:6379`) |
| `USERDB_VECTOR_BACKEND_URL` | Yes | Connection URL for the Vector backend (e.g., `http://localhost:8002`) |

### LLM and Embeddings

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes (default provider) | API key for the LLM and embedding model provider |
| `USERDB_LLM_MODEL` | No | LLM model name (default: `gpt-4o`) |
| `USERDB_EMBED_MODEL` | No | Embedding model name (default: `text-embedding-3-small`) |

### Server

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `USERDB_REST_PORT` | No | `8000` | Port for the REST API |
| `USERDB_MCP_PORT` | No | `8001` | Port for the MCP server |
| `USERDB_REST_ENABLED` | No | `true` | Set to `false` to disable the REST API |
| `USERDB_MCP_ENABLED` | No | `true` | Set to `false` to disable the MCP server |

-->

### Observability

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | — | OpenTelemetry collector endpoint; tracing disabled if unset |
| `OTEL_SERVICE_NAME` | No | `userdb` | Service name reported in traces and metrics |

---

## 6. Observability

Emits structured logs, metrics, and traces. Operators wire these into their own observability stack.

### Structured Logging

All log output is JSON-formatted to stdout. Log level is controlled by the standard `LOG_LEVEL` environment variable (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default: `INFO`).

Each log line includes: `timestamp`, `level`, `service`, `trace_id` (when a trace is active), and a `message` field.

### Metrics

Metrics are exposed via a Prometheus-compatible `/metrics` endpoint on the REST API port. Key metrics:

Example:

| Metric | Type | Description |
|--------|------|-------------|
| `userdb_requests_total` | Counter | Total requests by operation (`query`, `import`) and status |
| `userdb_request_duration_seconds` | Histogram | End-to-end request latency |
| `userdb_llm_calls_total` | Counter | LLM API calls by component and status |
| `userdb_llm_duration_seconds` | Histogram | LLM call latency |
| `userdb_storage_ops_total` | Counter | Storage operations by store (`json`, `vector`), op type, and status |

### Tracing

Distributed tracing uses [OpenTelemetry](https://opentelemetry.io/) with OTLP export. Tracing is enabled when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

Trace spans are created at:

- Each external interface request
- Each Reasoning Layer operation (query rewrite, data assessment)
- Each DML operation (query plan, query execution)
- Each storage read/write
- Each LLM call

All spans include `user_id` (hashed) and `operation` as attributes. Raw user data is never included in span attributes.

---

## 7. CI/CD Pipeline

The CI/CD platform is not yet selected. Regardless of platform, the pipeline defines these stages:

| Stage | Trigger | Actions |
|-------|---------|---------|
| **Verify** | Every push / PR | Run unit tests, type check (pyright), lint |
| **Build** | Every push to `main` | Build PyPI package, build Docker image |
| **Publish** | Git tag (`v*.*.*`) | Publish to PyPI, push Docker image to registry |

The **Verify** stage must pass before any merge. The **Publish** stage is gated on a semver tag pushed by a maintainer.

---

## 8. Release Process

1. Bump version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Merge to `main`
4. Push a tag: `git tag v1.2.3 && git push origin v1.2.3`
5. CI publishes the PyPI package and Docker image automatically

No manual upload steps. All publishing is performed by CI using credentials injected as secrets.
