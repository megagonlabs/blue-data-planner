# $PROJECT_NAME$ — Coding Style

## Tooling

<!--
List tools used for code formatting, linking and type checking.
-->
| Concern | Tool | Config |
|---------|------|--------|
| Formatting | `ruff format` | `pyproject.toml` |
| Linting | `ruff` | `pyproject.toml` |
| Type checking | `pyright` (strict) | `pyproject.toml` |

All three run in CI. `ruff format` and `ruff` are also expected to pass locally before committing.

### `pyproject.toml` configuration

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]   # pycodestyle, pyflakes, isort, pyupgrade

[tool.pyright]
pythonVersion = "3.10"
typeCheckingMode = "strict"
venvPath = "."
venv = ".venv"
```

---

## Type Hints

Type hints are required on all public methods, public class attributes, and module-level constants. Private methods must also be annotated.

Use Python 3.10+ syntax throughout — no imports from `typing` where newer syntax applies:

```python
# correct
def find(self, key: str) -> dict | None: ...
async def execute_query(self, query: str, database: str | None = None) -> list[QueryResult]: ...

# incorrect
from typing import Optional, Union
def find(self, key: str) -> Optional[dict]: ...
```

All packages must include a `py.typed` marker file to declare inline type information.

---

## Async

All I/O-bound methods are `async def`. This applies uniformly across all layers — core library, reasoning, data, and interaction — not just at interface boundaries.

Example:

```python
# correct — async throughout
class UserSource:
    async def execute_query(self, query: str, ...) -> list[QueryResult]: ...
    async def import_source(self, source: DataSource, ...) -> None: ...

class UserDataManager:
    async def execute_query(self, query: str, database: str) -> list[tuple]: ...

# incorrect — sync in core, async only at interface
class UserSource:
    def execute_query(self, query: str, ...) -> list[QueryResult]: ...
```

**CLI bridge** — Click commands are synchronous. Use `asyncio.run()` to call async library methods from CLI handlers:

```python
@click.command()
def query(text: str) -> None:
    results = asyncio.run(source.execute_query(text))
    ...
```

---

## Naming

| Construct | Convention | Example |
|-----------|-----------|---------|
| Classes | PascalCase | `UserQueryReasoner` |
| Async methods / functions | snake_case | `execute_query` |
| Module-level constants | UPPER_SNAKE_CASE | `DEFAULT_TOP_K` |
| Private members | single `_` prefix | `_identify_user_data_metrics` |
| Modules / packages | lowercase | `reasoning/`, `metric.py` |

Do not use double-underscore (`__`) name mangling. Use `_` for all private members.

---

## Imports

- Absolute imports only within the package; no relative imports.
- Order: stdlib → third-party → internal, each group separated by a blank line.
- No wildcard imports (`from x import *`).

Example: 

```python
import asyncio
from typing import Any

from llama_index.core import Settings

from blue_data_user_source.types import QueryResult
```

Import ordering is enforced by `ruff` (isort rules).

---

## Docstrings

One-line docstring on public classes and public methods only. No docstrings on private methods, `__init__`, properties, or anything self-explanatory from the name. No multi-line parameter documentation — types belong in signatures.

Example: 

```python
class UserQueryReasoner:
    """Orchestrates query rewriting, assessment, and interaction for a user database."""

    async def expand_query(self, query: str) -> list[str]:
        """Return complementary questions that provide context for the original query."""
        ...

    async def _identify_user_data_metrics(self, ...) -> list[UserDataMetric]:
        # no docstring — private method
        ...
```

---

## Comments

Write a comment only when the *why* is non-obvious: a hidden constraint, a subtle invariant, or a workaround for a specific external behaviour. Never describe what the code does — well-named identifiers already do that.

Example:

```python
# correct — explains a non-obvious constraint
# LlamaIndex streams partial tokens; we must await the full response before scoring
response = await llm.acomplete(prompt)

# incorrect — restates the code
# call expand_query to get expanded queries
expanded = await self.expand_query(query)
```

---

## Error Handling

Create a base exception is defined in `types.py`. All custom exceptions inherit from it.

Example:

```python
class UserDbError(Exception): ...
class QueryError(UserDbError): ...
class ImportError(UserDbError): ...  # noqa: A001
class InteractionError(UserDbError): ...
```

- Do not catch and swallow exceptions silently.
- Let exceptions propagate to interface boundaries, where each interface handles them appropriately:
  - **REST API** — FastAPI exception handlers convert `UserDbError` subtypes to HTTP responses.
  - **MCP server** — tool handlers catch `UserDbError` and return structured error responses.
  - **CLI** — top-level Click error handler renders `UserDbError` messages via Rich.
- Only catch specific exception types; never bare `except:` or `except Exception:`.
