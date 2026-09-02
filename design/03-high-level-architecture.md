# $PROJECT_NAME$ — High Level Architecture

<!-- 
Example: 

> Reference image: `03-high-level-architecture.png`
-->

## Diagram

<!-- 
Provide a high-level architecture diagram.
-->

<!-- 
Example: 

```
                      External Interfaces

  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
  │ Python Lib  │  │ MCP Server  │  │  REST API   │  │     CLI     │
  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
         └────────────────┴────────────────┴────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                                   UserDb                                     │
│                                                                              │
│  ┌──────────────────────────────┐   ┌──────────────────────────────────────┐ │
│  │  Natural Language Query      │   │        Data Management Layer         │ │
│  │       Interface              │   │                                      │ │
│  └──────────────────────────────┘   │  ┌────────────────────────────────┐  │ │
│                                     │  │         Query Planner           │  │ │
│  ┌──────────────────────────────┐   │  └────────────────────────────────┘  │ │
│  │       Reasoning Layer        │   │  ┌────────────────────────────────┐  │ │
│  │                              │   │  │         Query Executor          │  │ │
│  │  ┌────────────────────────┐  │   │  └────────────────────────────────┘  │ │
│  │  │     Query Rewriter     │  │   │  ┌────────────────────────────────┐  │ │
│  │  └────────────────────────┘  │   │  │        Source Manager          │  │ │
│  │  ┌────────────────────────┐  │   │  └────────────────────────────────┘  │ │
│  │  │     Data Assessor      │  │   └─────────────────────────┬────────────┘ │
│  │  └────────────────────────┘  │                             │              │
│  └──────────────────────────────┘                             │              │
│                                                               │              │
│  ┌──────────────────────────────┐                             │              │
│  │      Interaction Layer       │                             │              │
│  │                              │                             │              │
│  │  ┌────────────────────────┐  │                             │              │
│  │  │    Contact Manager     │  │                             │              │
│  │  └────────────────────────┘  │                             │              │
│  │  ┌────────────────────────┐  │                             │              │
│  │  │  Interaction Manager    │  │                             │              │
│  │  └────────────────────────┘  │                             │              │
│  └──────────────────────────────┘                             │              │
│                                                               │              │
└───────────────────────────────────────────────────────────────┼──────────────┘
                                                                │
                                                    ┌───────────▼──────────┐
                                                    │   ┌──────┐ ┌──────┐  │
                                                    │   │ JSON │ │Vector│  │
                                                    │   └──────┘ └──────┘  │
                                                    └──────────────────────┘
```
-->

## Components

<!-- 
Describe each component in the architecture
-->

<!-- 
Example:

### External Interfaces

Four interface adapters sit in front of UserDb and delegate to the Natural Language Query Interface. They share no internal logic — each is a thin translation layer over the Python library.

**Python Library** — Native Python API (`UserSource` and related classes). Used to embed UserDb directly in Python applications.

**MCP Server** — Exposes `execute_query` and `import` as MCP tools, enabling LLM agents to interact with UserDb within an agentic context.

**REST API** — HTTP/JSON interface (FastAPI). Enables non-Python services and remote clients to call UserDb over the network.

**CLI** — Command-line interface (Click + Rich). Used for interactive development, debugging, and scripting.

### Natural Language Query Interface
The entry point for all retrieval from UserDb. Accepts natural language queries from agents.

- All retrieval is expressed as natural language queries
- Ingestion may optionally accompany a query; when it does, the ingested data is evaluated against the query and relevant data is extracted
- Queries are forwarded to the Reasoning Layer for processing
- Responses can be returned as structured data or in natural language

### Reasoning Layer
Interprets queries and query results through reasoning agents.

- Incoming queries are rewritten as necessary given user context, the query intent, and available data, sources
- Assesses query results against metrics such as freshness and completeness
- Determines whether results require verification with the user or further interaction before being returned

**Query Rewriter** — Rewrites incoming natural language queries given user context and available data to improve retrieval accuracy.

**Data Assessor** — Evaluates query results against quality metrics (freshness, completeness, and others) and determines whether user interaction is needed before the result can be returned.

### Interaction Layer
Manages interaction with the user through various contact methods.

- Maintains a set of contact methods (e.g. email, SMS, system I/O, active session)
- Contact methods are stored and retrieved as per-user metadata in the Data Management Layer
- Conversation mechanics are specific to each contact method
- Requests originate from the Reasoning Layer and responses are returned to the Reasoning Layer

**Contact Manager** — Maintains and selects the appropriate contact method for a given user and query context.

**Interaction Manager** — Conducts the actual interaction with the user using the method provided by the Contact Manager, adapting conversation mechanics accordingly.

### Data Management Layer
Manages user data and metadata across all storage types and sources.

- Stores and retrieves user metadata (general profile, contact methods, etc.)
- Stores and retrieves user data across generic stores (JSON, Vector, and others)
- Manages user-specific sources such as uploaded files and active sessions
- Uses the Source Manager to identify data sources relevant to a given query
- Decomposes queries into subqueries when necessary and constructs a fetch plan across relevant sources
- Executes queries against data sources via the Source Manager

**Query Planner** — Interprets an incoming query, identifies relevant data sources via the Source Manager, and produces an execution plan, breaking the query into subqueries as needed.

**Query Executor** — Carries out the execution plan produced by the Query Planner, dispatching subqueries to the appropriate data sources and assembling the results.

**Source Manager** — Maintains the catalog of all data sources available for a user (generic stores, uploaded files, active sessions, etc.) and provides source lookup for the Query Planner and Query Executor.

### Storage

**JSON Store** — Manages key-value JSON data.

- Abstract store with pluggable backends (e.g. Redis, MongoDB)
- Stores data given a key and value
- Supports structured queries (e.g. JSONPath)

**Vector Store** — Manages key-value vector data.

- Abstract store with pluggable backends (e.g. ChromaDB)
- Converts keys and values to vectors via an embedding model before storage
- Stores metadata alongside each vector entry (e.g. type (key or value) timestamp, source, associated key or value vector id)
- Supports semantic queries (e.g. vector similarity search) given a natural language query
-->