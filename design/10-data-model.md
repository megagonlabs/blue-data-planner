# $PROJECT_NAME$ — Data Model

## Overview

<!--
Describe overview of the data model
-->

<!--
Example: 

UserDb maintains two parallel per-user stores:

- **JSONSource** — structured key-value store; holds both user metadata and user data entries; supports JSONPath queries
- **VectorSource** — semantic key-value store; mirrors user data entries only; supports vector similarity search

Both stores are addressed by user ID. User data entries are written to both stores simultaneously (dual-write). The JSON store is the system of record.

-->

<!-- Describe more specifics 
-->

<!--
Example: 

## 1. User ID

Each user database is addressed by a string user ID. The format is an opaque string; examples in this document use Firebase-style UIDs (e.g., `5WgRzdacRdOEvj8JBmCXFZSvWmH3`).

---

## 2. JSONSource

### 2.1 Structure

A user's JSON database is divided into two namespaces:

| Namespace | Contents |
|-----------|----------|
| `metadata` | Structured profile, contact methods, and source registry |
| `data` | Arbitrary user data entries |

---

### 2.2 User Metadata

#### General Profile

An extensible document. Required fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name |
| `picture` | string | Profile picture URL |
| `last_login` | ISO 8601 datetime | Most recent login timestamp |

Additional fields may be present depending on the authentication provider or import source.

**Example:**

```json
{
  "name": "Ada Lovelace",
  "picture": "https://example.com/avatar.jpg",
  "last_login": "2026-05-12T09:00:00Z",
  "timezone": "America/New_York"
}
```

#### Contact Methods

A list of contact method entries. Each entry:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Channel type (e.g., `"email"`, `"phone"`, `"sms"`, `"x"`) |
| `value` | string | Channel-specific address or handle |
| `preference` | integer | Relative priority; lower value = higher priority |
| `verified` | boolean | Whether the channel has been verified |
| `last_use` | ISO 8601 datetime | Most recent use timestamp |

**Example:**

```json
[
  { "type": "email", "value": "ada@example.com",  "preference": 1, "verified": true, "last_use": "2026-05-12T09:00:00Z" },
  { "type": "phone", "value": "+15551234567",      "preference": 2, "verified": false, "last_use": "2026-05-12T10:00:00Z" },
  { "type": "x",     "value": "@ada",              "preference": 3, "verified": false, "last_use": "2026-05-12T11:00:00Z" }
]
```

#### Source Registry

A list of registered external source entries. Each entry:

| Field | Type | Description |
|-------|------|-------------|
| `source_id` | string | Unique identifier for the registered source |
| `source_type` | string | One of `"file"`, `"session"`, or other registered types |
| `location` | string | Filesystem path, URL, or session ID identifying the source |
| `registered_at` | ISO 8601 datetime | When the source was registered |
| `queries` | list[string] | Queries run at import time; empty for Type 1 registrations |

**Example:**

```json
[
  {
    "source_id": "src-abc123",
    "source_type": "file",
    "location": "~/resume.txt",
    "registered_at": "2026-05-01T10:00:00Z",
    "queries": ["What skills does the user have?"]
  },
  {
    "source_id": "src-xyz789",
    "source_type": "session",
    "location": "session-id-value",
    "registered_at": "2026-05-10T08:30:00Z",
    "queries": []
  }
]
```

---

### 2.3 User Data Entries

Arbitrary key-value data extracted from user sources or stored through queries. Keys are **not** unique — multiple entries may share the same key (e.g., `"skills"` populated independently from a resume and a session).

Each entry:

| Field | Type | Description |
|-------|------|-------------|
| `key` | string | Semantic label for the data (e.g., `"skills"`, `"job_preferences"`) |
| `value` | JSON array | One or more values associated with the key |
| `metadata` | object | Entry-level provenance metadata (see below) |

#### Entry Metadata

| Field | Type | Description |
|-------|------|-------------|
| `date` | ISO 8601 datetime | When the entry was created or last updated |
| `agent` | string | Identifier of the agent that created the entry |
| `source_id` | string \| null | ID of the source the entry was derived from; `null` for entries not tied to a specific source |

**Example:**

```json
{
  "key": "skills",
  "value": ["Python", "SQL", "machine learning"],
  "metadata": {
    "date": "2026-05-01T10:05:00Z",
    "agent": "import-agent-v1",
    "source_id": "src-abc123"
  }
}
```

**Example — same key, different source:**

```json
{
  "key": "skills",
  "value": ["project management", "stakeholder communication"],
  "metadata": {
    "date": "2026-05-10T09:15:00Z",
    "agent": "session-agent-v1",
    "source_id": "src-xyz789"
  }
}
```

---

## 3. VectorSource

The Vector store mirrors all user data entries from the JSON store. User metadata (profile, contact methods, source registry) is **not** stored in the Vector store.

### 3.1 Vector Entry

Each user data entry is stored as a vectorized record. Both the key and the value are independently embedded.

| Field | Type | Description |
|-------|------|-------------|
| `key_vector` | float[] | Embedding of the key string |
| `value_vector` | float[] | Embedding of the serialized value array |
| `key` | string | Original key, stored verbatim for retrieval |
| `value` | JSON array | Original value, stored verbatim for retrieval |
| `metadata` | object | Same metadata object as the corresponding JSONSource entry |

Semantic search may operate on key vectors, value vectors, or both, depending on query intent.

### 3.2 Embedding Model

The embedding model used to vectorize keys and values is pluggable and selected at deployment time. All entries within a single user database must be produced by the same model version; a model change requires re-embedding the entire database.

---

## 4. Synchronization

Every write to a user data entry is applied to both JSONSource and VectorSource within the same operation before the write is acknowledged (dual-write). JSONSource is the system of record.

User metadata writes (profile, contact methods, source registry) target JSONSource only.

---

## 5. Source Types

| `source_type` | Class | Description |
|---------------|-------|-------------|
| `"file"` | `FileSource` | Uploaded or local file (e.g., a resume) |
| `"session"` | `SessionSource` | Active or historical conversational session |

Additional source types may be registered as extensions.

-->