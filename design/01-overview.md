# blue-data-planner — Overview

## Objective

Most questions worth asking about company data take more than one operation to answer. "What are the
data science jobs in the SF Bay Area that are suitable for me?" needs a lookup over structured
tables, then a filter by location, then a ranking against what a user profile says, then a summary.
Each step reads what the step before it produced.

A language model asked to answer a request like that in one shot writes a single compound query
against a single data source. When the result comes back wrong, there is nothing to inspect and
nothing to retry.

Cutting the request into single steps fails the other way. The operators worth the most are the ones
that do several things at once: `nl2sql` scans, filters, joins, and aggregates inside one call, and a
knowledge base's `answer` resolves a multi-hop question inside its own graph. A plan already cut into
one operation per step has nothing to hand them.

blue-data-planner keeps both. It turns a request written in plain language into a plan of what the
request asks for, decides separately how much of that plan each operator takes on, runs the result,
and repairs it where it fails. The plan and every decision made about it are structured data, so a
run can be read afterwards and repaired at the level that was actually wrong.

blue-data-planner is part of the Blue Data Intelligence Layer (DIL). It builds on `blue-data`, which
makes data sources discoverable and understandable, and on `blue-data-ops`, which provides the
operators a plan is run with. blue-data-planner is the layer that decides what to run and when.

## What blue-data-planner does

The repository holds the planners. The operators they run come from `blue-data-ops`.

### Data planners

A planner behaves the same way whatever operator suite is installed. Answering a request takes three
stages. Each asks a model a different question, and each answer is recorded on its own.

| Stage | Question | Produces |
| --- | --- | --- |
| Logical planning | What is the user asking for | A DAG of typed nodes, each carrying one instruction in plain language |
| Physical planning | How much of the plan does each operator take on, and against which source | A cover: nodes grouped into fragments, each fragment bound to one operator |
| Fragment execution | What parameters does the call run with, and was the result any good | Rows, and the decision to accept them or run again |

The middle stage carries the decision the rest is built around. Most operators take one node:
`lotus.sem_filter` filters a table and nothing more. Others take a whole region of the plan at once.
Handing a region to one operator is a pushdown, in the sense the term has in federated query
processing — fewer calls, less data moved between them, and the operator either does the whole thing
correctly or fails as a unit. A smaller group costs more calls and can be checked and repaired step
by step. blue-data-planner makes that trade a decision rather than a fixed rule, and records where the
boundary was drawn, so the choice can be measured after the fact.

The logical vocabulary is ten operations that belong to no operator suite: `SCAN`, `FILTER`,
`PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION`, and `RETURN`. Two
control nodes let a plan stop where it cannot yet be written. `REPLAN` waits for the columns and
values earlier nodes produce; `DISCOVER` searches the data registry for a source the request never
named, and the planner writes the rest once it can see what the search returned.

Beyond the three stages, a planner:

- checks a plan before running it, looking for cycles, references to nodes that do not exist,
    repeated node ids, a missing or duplicated ending, nodes whose output nothing reads, and nodes
    given the wrong number of inputs
- runs the plan, starting each fragment as soon as its inputs are ready, and keeps every fragment's
    inputs, outputs, and failure reasons as structured data
- repairs a failure where it was caused, moving on to a more expensive repair only when the
    narrower one runs out of attempts

| Repair | What a failure rewrites |
| --- | --- |
| Parameter | The parameters of the call, against the same group and operator |
| Fragment | The operator, or the way this group of nodes is cut |
| Cover | The whole cover, against the same logical plan |
| Replan | The logical plan |

### Operators

Operators are what fragments are bound to, and none of them are written here. The Blue operator
library and its registry live in `blue-data-ops`; LOTUS is its own package. This repository holds
one adapter per suite, which reads a suite's own declarations and presents each operator to physical
planning in the single shape the planner works against.

An operator declares the set of logical operations it can carry out, the kinds of source it can read,
whether it accepts tables bound in from elsewhere, and what it cannot do. One that declares a single
operation can only ever cover a single node; one that declares several can cover a region. Which
groups of nodes an operator is a candidate for follows from that declaration, so registering an
operator is enough to put it in play and no planner code changes.

A planner is reachable three ways: as an importable Python library, as a CLI for asking a question
and getting an answer directly, and as an MCP server that gives agent frameworks such as LangChain a
planning tool.

## Design philosophy

Three questions are kept apart because they fail apart. What the request asks for, how much of it one
operator should take on, and what parameters that operator runs with are decided by different stages
against different information, and each is recorded separately. A wrong value in a parameter is then
repaired by writing the parameter again, and the plan above it stands.

A logical node says what a step is for and nothing about how to carry it out. It carries one
instruction in plain language, names no source and no operator, and leaves out every parameter an
implementation would need. Both of those are properties of a particular attempt, and both can differ
between attempts at the same node.

A plan is a typed structure that can be stored, read back, and checked. So are the groupings laid
over it, each fragment's inputs and outputs, and every failure reason. Nothing that
matters about a run exists only as formatted prose. Plans are never edited in place: a repair adds a
new version, so what ran earlier stays readable exactly as it ran.

An operator joins the system by declaring what it can carry out, what parameters it takes, and how
to call it. The planner reads only that declaration, which is what lets an outside suite and a Blue
operator compete for the same work.

The planner never quietly ignores malformed state or missing required values. Failures it is meant to
recover from, such as invalid operator parameters or a result that does not hold up, are recorded as
outcomes with a reason; anything else, such as a plan object that is not a plan or a missing
configuration value, is raised. Recovery is then something the planner decides to do, not something
that happens by accident.

The library holds all the behavior. The CLI and the MCP server read requests, format responses, map
errors, and load configuration, and nothing more. Planning, operator selection, and execution never
live in an adapter.

Everything the system reads or writes goes through one module, `lib/src/blue_data_planner/data/`. No
other module imports `blue-data`, and no code reaches Redis, a vector store, or a file directly.
Concrete implementations are supplied at startup.
