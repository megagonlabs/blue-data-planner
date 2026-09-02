# $PROJECT_NAME$ — Software Stack

<!--
List the software stack including programming language development, 3rd party libraries, LLM preferences, package and test libraries, etc. 
-->

<!-- 
Example:

> Interfaces marked **provisional** are initial suggestions based on expected usage patterns. They will be revised once the respective library is installed and inspected.

---

## Language & Runtime

| Concern | Choice |
|---------|--------|
| Language | Python |
| Minimum version | 3.10 |
| Package manager | pip |
| Project config | `pyproject.toml` |

---

## LLM

| Concern | Choice |
|---------|--------|
| Abstraction layer | LlamaIndex (`llama-index-core`) |
| Initial provider | OpenAI (`llama-index-llms-openai`) |

LlamaIndex is used to provide a provider-agnostic LLM interface. All LLM calls within UserDb go through the LlamaIndex `LLM` abstraction, making it straightforward to swap providers without changing application code.

**LlamaIndex classes used:**

| Class | Package | Role |
|-------|---------|------|
| `llama_index.core.llms.LLM` | `llama-index-core` | Abstract LLM base |
| `llama_index.llms.openai.OpenAI` | `llama-index-llms-openai` | Initial concrete provider |
| `llama_index.core.Settings` | `llama-index-core` | Global LLM configuration |

**Configuration:**

LLM provider and model are configured globally via `llama_index.core.Settings.llm` at application startup. Individual components (`UserQueryReasoner`, `UserDataMetric`) consume the LLM through this setting rather than instantiating providers directly.

```python
from llama_index.core import Settings
from llama_index.llms.openai import OpenAI

Settings.llm = OpenAI(model="gpt-4o")
```

---

## Storage

Both storage backends are provided by `blue-data-core`. UserDb interacts only with their abstract interfaces and does not depend on any specific backend implementation.

### JSONSource

Used for: user metadata (profile, contact methods, source registry), semi-structured query results.

**Provisional interface** (to be revised once `blue-data-core` is installed):

| Method | Signature | Description |
|--------|-----------|-------------|
| `set` | `set(key: str, value: dict) -> None` | Store or overwrite a JSON value |
| `get` | `get(key: str) -> dict \| None` | Retrieve by key |
| `delete` | `delete(key: str) -> None` | Remove a key |
| `query` | `query(expression: str) -> list[dict]` | Query using JSONPath or equivalent expression |
| `keys` | `keys(prefix: str = None) -> list[str]` | List all keys, optionally filtered by prefix |

### VectorSource

Used for: vectorized user data enabling semantic retrieval.

**Provisional interface** (to be revised once `blue-data-core` is installed):

| Method | Signature | Description |
|--------|-----------|-------------|
| `set` | `set(key: str, text: str, metadata: dict = None) -> None` | Embed and store text with optional metadata |
| `get` | `get(key: str) -> dict \| None` | Retrieve entry by key |
| `delete` | `delete(key: str) -> None` | Remove an entry |
| `query` | `query(text: str, top_k: int = 10, filters: dict = None) -> list[dict]` | Semantic similarity search |

---

## External Interfaces

### REST API

| Concern | Choice |
|---------|--------|
| Framework | FastAPI |
| ASGI server | Uvicorn |

FastAPI provides automatic OpenAPI schema generation and async request handling. Endpoints map 1:1 to `execute_query` and `import`.

### MCP Server

| Concern | Choice |
|---------|--------|
| Library | `mcp` (official MCP Python SDK) |

Exposes UserDb operations as MCP tools for consumption by LLM agents. The `mcp` package provides the server scaffolding; tool handlers delegate directly to the Python library.

### CLI

| Concern | Choice |
|---------|--------|
| Argument parsing | Click |
| Output formatting | Rich |

Click handles command routing and argument parsing. Rich provides formatted terminal output (tables, progress indicators) for interactive use.

---

## External Libraries

### blue-data

Provides base classes and storage backends consumed by UserDb. Assumed to exist; interfaces below are provisional and will be revised once the library is installed.

**Provisional classes:**

| Class | Role |
|-------|------|
| `DataSource` | Abstract base for all data sources |
| `FileSource(DataSource)` | File-based data source |
| `SessionSource(DataSource)` | Streaming session data source |
| `Session` | Session lifecycle; exposes `cid` (session ID) and `get_source() -> SessionSource` |
| `JSONSource(DataSource)` | JSON storage (see interface above) |
| `VectorSource(DataSource)` | Vector storage (see interface above) |
| `DataPlanner` | Abstract base for query planners |
| `DataRegistry` | Abstract base for data registries; supports source registration and query execution |

### Summary

| Package | Version | Source | Role |
|---------|---------|--------|------|
| `blue-data` | TBD | internal | Base classes, storage backends |
| `llama-index-core` | latest stable | PyPI | LLM abstraction, settings |
| `llama-index-llms-openai` | latest stable | PyPI | OpenAI LLM provider |
| `openai` | latest stable | PyPI | OpenAI SDK (transitive via LlamaIndex) |
| `fastapi` | latest stable | PyPI | REST API framework |
| `uvicorn` | latest stable | PyPI | ASGI server for FastAPI |
| `mcp` | latest stable | PyPI | MCP server SDK |
| `click` | latest stable | PyPI | CLI argument parsing |
| `rich` | latest stable | PyPI | CLI output formatting |
-->