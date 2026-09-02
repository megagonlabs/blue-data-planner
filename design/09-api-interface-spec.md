# $PROJECT_NAME$ — API / Interface Spec

<!--
High level API description
-->

<!--
Example: 
All four external interfaces are thin adapters over the Python library. No business logic belongs in any interface layer. Authentication and authorization are left to the deployment layer.

-->

## 1. Python Library

<!--
Description library
-->

<!--
Example:

The canonical interface. All other interfaces delegate to it.

### `UserSource`

```python
class UserSource:
    async def execute_query(
        self,
        query: str,
        database: str | None = None,
        optional_properties: dict = {}
    ) -> list[QueryResult]: ...

    async def import_source(
        self,
        source: DataSource,
        database: str | None = None,
        optional_properties: dict = {}
    ) -> None: ...

    async def create_database(self, name: str) -> None: ...
```

`database` defaults to the value set at construction time when `None` is passed. Raises `QueryError` if no default is configured and no value is provided.

**`optional_properties` keys**

| Key | Type | Method | Description |
|-----|------|--------|-------------|
| `"session"` | `str` | `execute_query` | Session ID; used as query context and primary contact channel for user interaction |
| `"queries"` | `list[str]` | `import_source` | Queries to run over the source (Type 2 import); omit for Type 1 registration |

### Constructor

```python
UserSource(properties: dict = {})
```

`properties` may include `"database"` to set a default user database for all calls.

```python
us = UserSource(properties={"database": "5WgRzdacRdOEvj8JBmCXFZSvWmH3"})

# Type 1 — register file source for future queries
await us.import_source(FileSource("~/resume.txt"))

# Type 2 — extract and store structured data from file
await us.import_source(
    FileSource("~/resume.txt"),
    optional_properties={"queries": ["What skills does the user have?"]}
)

# Query
results = await us.execute_query(
    "jobs suitable for me",
    optional_properties={"session": session.cid}
)
```

### Result type

```python
class QueryResult:
    query: str          # the sub-question answered
    results: list[Any]  # retrieved data for that question
```

`execute_query` returns one `QueryResult` per resolved sub-question.

### Exceptions

All exceptions inherit from `UserDbError` (defined in `types.py`).

| Exception | Raised when |
|-----------|-------------|
| `QueryError` | Query cannot be processed or no database is available |
| `ImportError` | Import fails (file not found, source registration error, etc.) |
| `InteractionError` | User interaction fails (channel unavailable, timeout, etc.) |

---
-->

## 2. REST API

<!--
Describe REST API
-->

<!--
Example: 

FastAPI application. Endpoints map 1:1 to Python library operations. All handlers are `async def`; blocking behavior on long-running queries is acceptable — callers are expected to hold the connection.

A default database may be configured server-side at startup; when configured, the `database` field in request bodies is optional.

### `POST /query`

Execute a natural language query.

**Request** (`application/json`)

```json
{
  "query": "jobs suitable for me",
  "database": "5WgRzdacRdOEvj8JBmCXFZSvWmH3",
  "session": "optional-session-id"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Natural language query |
| `database` | string | no | User database ID; uses server default if omitted |
| `session` | string | no | Session ID for query context and user interaction |

**Response `200 OK`** (`application/json`)

```json
{
  "results": [
    {
      "query": "What skills does the user have?",
      "results": ["Python", "SQL", "machine learning"]
    },
    {
      "query": "Does the user prefer remote work?",
      "results": [true]
    }
  ]
}
```

---

### `POST /import`

Two request shapes depending on source type.

#### File source (`multipart/form-data`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | yes | File to import |
| `database` | string | no | User database ID; uses server default if omitted |
| `queries` | string (JSON array) | no | Queries to run over the file (Type 2); omit for Type 1 |

```
POST /import
Content-Type: multipart/form-data

file=<binary>
database=5WgRzdacRdOEvj8JBmCXFZSvWmH3
queries=["What skills does the user have?"]
```

#### Session source (`application/json`)

```json
{
  "source_type": "session",
  "source_id": "session-id",
  "database": "5WgRzdacRdOEvj8JBmCXFZSvWmH3"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_type` | `"session"` | yes | Identifies a session source |
| `source_id` | string | yes | Session ID to register |
| `database` | string | no | User database ID; uses server default if omitted |

**Response `204 No Content`** (both shapes)

---

### `POST /databases`

Create a new user database.

**Request** (`application/json`)

```json
{ "name": "5WgRzdacRdOEvj8JBmCXFZSvWmH3" }
```

**Response `201 Created`**

```json
{ "database": "5WgRzdacRdOEvj8JBmCXFZSvWmH3" }
```

---

### `GET /health`

Liveness check.

**Response `200 OK`**

```json
{ "status": "ok" }
```

---

### Error responses

All errors use a consistent envelope:

```json
{
  "error": {
    "code": "query_error",
    "message": "No database specified and no default configured."
  }
}
```

| HTTP status | `code` | Condition |
|------------|--------|-----------|
| 400 | `invalid_request` | Malformed request body |
| 404 | `database_not_found` | Specified database does not exist |
| 422 | `query_error` | Query could not be processed |
| 422 | `import_error` | Import failed |
| 500 | `internal_error` | Unexpected failure |

FastAPI exception handlers catch `UserDbError` subtypes and map them to these responses. No error handling logic lives in route handlers.

---

## 3. MCP Server

Exposes two MCP tools. Tool handlers are thin wrappers over the Python library; all business logic remains in the library. A default database may be configured at server startup.

### Tool: `execute_query`

```json
{
  "name": "execute_query",
  "description": "Query a user database in natural language. Returns structured results for each sub-question resolved from the original query.",
  "inputSchema": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language query"
      },
      "database": {
        "type": "string",
        "description": "User database ID. Uses server default if omitted."
      },
      "session": {
        "type": "string",
        "description": "Active session ID. Used as query context and as the primary contact channel when user interaction is required."
      }
    }
  }
}
```

**Returns:** JSON text content — array of `{"query": "...", "results": [...]}` objects, one per resolved sub-question.

**On error:** structured MCP error content containing the `UserDbError` message.

---

### Tool: `import`

```json
{
  "name": "import",
  "description": "Import a file into a user database. Omit queries to register the file as a source (Type 1). Provide queries to extract and store specific data from the file (Type 2).",
  "inputSchema": {
    "type": "object",
    "required": ["source_path"],
    "properties": {
      "source_path": {
        "type": "string",
        "description": "Filesystem path to the file to import"
      },
      "database": {
        "type": "string",
        "description": "User database ID. Uses server default if omitted."
      },
      "queries": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Queries to run over the file (Type 2 import). Omit for source registration only."
      }
    }
  }
}
```

**Returns:** empty content on success. Structured MCP error content on failure.

-->

## 4. CLI

<!--
Describe CLI
-->

<!--
Example: 

Built with Click (argument parsing) and Rich (output). All commands are thin wrappers over the Python library; Click handlers use `asyncio.run()` to call async library methods.

A default database may be configured via environment variable `USERDB_DATABASE`.

### Global options

```
userdb [--database DB] COMMAND
```

| Option | Description |
|--------|-------------|
| `--database DB` | User database ID. Overrides `USERDB_DATABASE` if set. |

---

### `userdb query`

```
userdb query TEXT [--session SESSION_ID] [--format (table|json)]
```

Executes a natural language query and prints results.

| Argument / Option | Description |
|-------------------|-------------|
| `TEXT` | Natural language query (required) |
| `--session SESSION_ID` | Session ID for query context and user interaction |
| `--format` | Output format: `table` (default, via Rich) or `json` |

**Example**

```
$ userdb query "jobs suitable for me" --format table

┌─────────────────────────────────────┬──────────────────────────────┐
│ Query                               │ Results                      │
├─────────────────────────────────────┼──────────────────────────────┤
│ What skills does the user have?     │ Python, SQL, machine learning │
│ Does the user prefer remote work?   │ Yes                          │
└─────────────────────────────────────┴──────────────────────────────┘
```

---

### `userdb import`

```
userdb import FILE [--query QUERY]...
```

Imports a file. `--query` may be repeated.

| Argument / Option | Description |
|-------------------|-------------|
| `FILE` | Path to file to import (required) |
| `--query QUERY` | Query to run over the file (Type 2). Repeat for multiple queries. Omit for Type 1 (source registration only). |

**Example — Type 1**

```
$ userdb import ~/resume.txt
Registered: ~/resume.txt
```

**Example — Type 2**

```
$ userdb import ~/resume.txt --query "What skills does the user have?" --query "What is the user's most recent job title?"
Imported: ~/resume.txt (2 queries)
```

---

### `userdb database create`

```
userdb database create NAME
```

Creates a new user database.

**Example**

```
$ userdb database create 5WgRzdacRdOEvj8JBmCXFZSvWmH3
Created database: 5WgRzdacRdOEvj8JBmCXFZSvWmH3
```

---

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | `UserDbError` — message printed via Rich to stderr |
| 2 | Bad arguments (Click default) |

-->