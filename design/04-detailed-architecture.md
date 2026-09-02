# $PROJECT_NAME$ — Detailed Architecture

<!--
Describe each component in the architecture in more detail, specifying the flow of information and processing steps in more detail.
-->

<!-- 
Example: 

## External Interfaces

All four interfaces are thin adapters over the Python library. They each ultimately delegate to `UserSource.execute_query()` and `UserSource.import()`.

| Interface | Technology | Primary Use |
|-----------|-----------|-------------|
| Python Library | Native Python API | Embedding UserDb in Python applications |
| MCP Server | MCP protocol | LLM agent tool use (e.g. Claude) |
| REST API | FastAPI (HTTP/JSON) | Non-Python consumers and microservice deployments |
| CLI | Click + Rich | Development, debugging, and scripting |

### Python Library

`UserSource` and related classes are the native interface. Direct method calls with no serialization overhead. All other interfaces wrap this layer.

### MCP Server

Exposes `execute_query` and `import` as MCP tools, allowing LLM agents to query and populate UserDb directly within an agentic context.

### REST API

HTTP interface built with FastAPI. Endpoints map 1:1 to `execute_query` and `import`. Enables non-Python services and remote clients to interact with UserDb.

### CLI

Command-line interface built with Click and Rich. Thin wrapper over the Python library for interactive use, scripting, and development workflows.

---

## Information Flow Overview

### Query Path

1. NLQI receives `query()`, forwards to Reasoning Layer
2. Reasoning Layer rewrites query, expands into question set Q
3. For each qi in Q: DML retrieves data di, Reasoning Layer evaluates metrics Mi
4. If Mi falls below thresholds: Interaction Layer clarifies with user, returns updated data for reassessment
5. Reasoning Layer aggregates all resolved (qi, di, Mi) into final response
6. Response returned through NLQI to caller

### Import Path — Type 1: Source Registration

1. NLQI receives `import(source)`
2. DML registers source in Source Manager as user metadata
3. Source becomes immediately available for future queries

### Import Path — Type 2: Query-over-Source

1. NLQI receives `import(source, query)`
2. Reasoning Layer executes query over the imported source via DML
3. Extracted results are injected into user data
4. Source optionally registered for future queries

Entity extraction from an uploaded file (e.g., resume parsing) is an instance of Type 2.

---

## 1. Natural Language Query Interface (NLQI)

The NLQI layer is the entry point for all requests to UserDb. It receives and routes incoming requests without performing query interpretation itself.

### Responsibilities

- Receive incoming requests
- Route `query()` and `import()` to the appropriate processing path
- Maintain session context for the duration of the request

### Query Intake

Receives:

- `query()` — natural language retrieval request
- `import()` — data ingestion request (source registration or query-over-source)

Examples:

- `"jobs suitable for me"`
- `"what skills do I have?"`
- `"find management roles for me"`

---

## 2. Reasoning Layer

The Reasoning Layer orchestrates query rewriting, retrieval coordination, quality evaluation, and iterative refinement. It determines whether the system knows enough to confidently answer the query.

### Responsibilities

- Rewrite and expand incoming queries
- Coordinate question execution across the DML
- Evaluate data quality
- Determine whether user clarification is needed
- Drive iterative refinement loops
- Aggregate final responses

### Query Rewriter

Transforms the original query into a normalized query and an expanded question set.

**Example**

Input: `"jobs suitable for me"`

Rewritten query: `"What jobs are suitable for this user?"`

Expanded question set Q:

- `"What skills does the user have?"`
- `"What titles is the user interested in?"`
- `"Does the user have location constraints?"`
- `"What is the user's expected compensation?"`
- `"Does the user prefer remote work?"`

**Context Expansion Sources**

- User profile
- Session history
- Previous interactions
- Historical preferences
- Organizational knowledge
- Domain templates

### Processing Flow

For each question qi in Q:

1. Execute qi through the Data Management Layer
2. Receive result di
3. Evaluate quality metrics Mi via the Data Assessor
4. If Mi falls below thresholds, forward (qi, di, Mi) to Interaction Layer
5. Repeat until metrics are satisfactory

Result tuple: **(qi, di, Mi)**

| Symbol | Meaning |
|--------|---------|
| qi | Question |
| di | Retrieved data / result |
| Mi | Metric scores |

### Data Assessor

Evaluates the quality and validity of retrieved data.

**Responsibilities**

- Assess completeness
- Assess freshness
- Assess confidence
- Assess consistency
- Assess relevance
- Detect ambiguity
- Determine sufficiency thresholds

**Metrics**

| Metric | Description |
|--------|-------------|
| Completeness | Is enough information available? |
| Freshness | Is the information recent? |
| Confidence | How reliable is the evidence? |
| Consistency | Do sources agree? |
| Relevance | Is the information relevant to the query? |
| Specificity | Is the information sufficiently detailed? |

**Example**

For `"Suitable jobs for me"`:

| Metric | Score |
|--------|-------|
| Completeness | 0.45 |
| Freshness | 0.92 |
| Confidence | 0.78 |
| Relevance | 0.88 |

Completeness is below threshold → interaction required.

**Satisfaction Evaluation**

The Reasoning Layer determines:

- Whether all metrics exceed thresholds
- Whether missing information can be inferred
- Whether user interaction is necessary

If satisfactory: aggregate and return final evidence-backed results.
Otherwise: forward unresolved (qi, di, Mi) to the Interaction Layer.

---

## 3. Interaction Layer

The Interaction Layer handles clarification, verification, and conversational refinement through the most appropriate communication channel.

### Responsibilities

- Determine contact strategy
- Generate clarification prompts
- Manage conversations
- Capture user responses
- Re-trigger assessment in the Reasoning Layer
- Support iterative refinement

### Interaction Flow

Input: **(qi, di, Mi)**

The layer:

1. Selects communication channel via Contact Manager
2. Generates clarification request via Interaction Manager
3. Sends interaction to user
4. Waits for response
5. Returns updated data to Reasoning Layer for reassessment
6. Repeats if necessary

### Contact Manager

Selects the most appropriate communication mechanism.

**Responsibilities**

- Manage available communication channels
- Track active user sessions
- Maintain communication preferences
- Rank and select optimal contact method

**Supported Channels**

- Active conversational session
- Email
- Messaging applications
- SMS
- Enterprise messaging systems
- Notifications

**Example Decision**

If user is currently active → use active chat session.
Otherwise → send email or asynchronous notification.

### Interaction Manager

Coordinates conversational clarification.

**Responsibilities**

- Generate clarification prompts
- Contextualize follow-up questions
- Manage interaction state
- Await user responses
- Feed new data back to Reasoning Layer

**Example**

Input: (qi, di, Mi)

Generated questions:

- `"Do you prefer remote work?"`
- `"Would you consider staff-level roles?"`
- `"Do you have location restrictions?"`

The Interaction Manager then:

1. Sends the message
2. Waits for user response
3. Updates context
4. Triggers reassessment in the Reasoning Layer

---

## 4. Data Management Layer (DML)

The Data Management Layer handles source discovery, retrieval planning, query execution, and result aggregation across heterogeneous data sources.

### Responsibilities

- Discover relevant data sources
- Plan retrieval strategies
- Execute structured data operations
- Aggregate evidence
- Normalize results
- Support provenance tracking
- Register new sources on import

### import() Handling

**Type 1 — Source Registration**

The imported source is registered in the Source Manager as part of user metadata. It becomes available for future queries via the Query Planner.

**Type 2 — Query-over-Source**

A query is executed over the imported source by the Reasoning Layer via the DML. Extracted results are injected into user data. Entity extraction from an uploaded file is an instance of this type.

### Query Planner

Interprets an incoming query, discovers relevant sources, and produces an execution plan.

**Responsibilities**

- Interpret query intent
- Discover candidate sources via Source Manager
- Build execution plan
- Select retrieval operators
- Optimize execution strategy

**Example Plan**

For `"Suitable data science jobs"`, the planner may:

- Inspect user resume
- Inspect historical preferences
- Inspect prior job interactions
- Retrieve location preferences
- Aggregate role preferences

### Query Executor

Executes plans produced by the Query Planner.

**Responsibilities**

- Execute retrieval operations
- Coordinate operators
- Aggregate results
- Compute evidence
- Track provenance
- Normalize outputs

**Example Output**

```
(source, evidence, timestamp, confidence)
```

### Source Manager

Maintains the registry of all data sources available for a user. Serves as the UserDb-specific data registry (referred to as "Registry" in the High Level Architecture).

**Responsibilities**

- Maintain source registry
- Store source metadata
- Manage schemas
- Track access methods
- Register new sources on import
-->