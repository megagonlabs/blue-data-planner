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
that do several things at once: a text-to-SQL operator over a relational database scans, filters,
joins, and aggregates inside one call, and a graph store answers a multi-hop question in one. A plan
already cut into one operation per step has nothing to hand them.

blue-data-planner keeps both. It turns a request written in plain language into a plan of what the
request asks for, decides separately how much of that plan each operator takes on, runs the result,
and repairs it where it fails. The plan and every decision made about it are structured data, so a
run can be read afterwards and repaired at the level that was actually wrong.

blue-data-planner is part of the Blue Data Intelligence Layer (DIL). It builds on `blue-data`, which
makes data sources discoverable and understandable, and runs the operators an installed suite
provides. Deciding what to run and when is its job.

## What blue-data-planner does

### Data planners

The shape is borrowed from federated query processing. A request becomes a logical plan of what was
asked. A physical plan then decides how much of that plan each operator absorbs and against which
source: that grouping is a cover, and one group in it is a fragment, the nodes one operator runs in
a single call. Each fragment then runs. A planner works the same way whatever operator suite is
installed.

Three things are not borrowed. There is no cost model, so a model reading the plan and the
operators' declarations makes the pushdown choice rather than estimated cardinalities. The operators
are arbitrary rather than relational wrappers, which is also why a federated database is not the
tool for this job: many of the operations a request needs are not relational, and some are judgments
only a model can make. And a result is reviewed rather than trusted, because an operator answering a
semantic question can return well-shaped rows that are wrong.

Each stage asks a model a different question, and each answer is recorded on its own.

| Stage | Question | Produces |
| --- | --- | --- |
| Logical planning | What is the user asking for | A DAG of typed nodes, each carrying one instruction in plain language |
| Physical planning | How much of the plan does each operator take on, and against which source | A cover: nodes grouped into fragments, each fragment bound to one operator |
| Fragment execution | What parameters does the call run with, and was the result any good | Rows, and the decision to accept them or run again |

The middle stage carries the decision the rest is built around. A semantic filter operator takes one
node and nothing more; a text-to-SQL operator takes a whole region. A larger region means fewer
calls and less data moved between them, and the operator either does the whole thing correctly or
fails as a unit. A smaller group costs more calls and can be checked and repaired step by step.
blue-data-planner makes that trade a decision rather than a fixed rule, and records where the
boundary was drawn, so the choice can be measured afterwards.

The logical vocabulary is ten operations that belong to no operator suite: `SCAN`, `FILTER`,
`PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION`, and `RETURN`. Two
control nodes let a plan stop where it cannot yet be written. `REPLAN` waits for the columns and
values earlier nodes produce; `DISCOVER` searches the data registry for a source the request never
named, and the planner writes the rest once it can see what the search returned.

Beyond the three stages, a planner:

- checks a plan before running it, and refuses one that cannot run
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

Operators are what fragments are bound to. Each suite reaches a planner through one adapter that
presents its operations in the one shape planning reads. `blue-data-ops` holds the Blue operator
library, LOTUS is its own package, and any other suite enters the same way.

An operator declares the set of logical operations it can carry out, the kinds of source it can
read, whether it accepts tables bound in from elsewhere, and what it cannot do. One that declares a
single operation covers a single node; one that declares several covers a region. That declaration
is all the physical planner reads, which is what lets an outside suite and a Blue operator compete
for the same work.

A planner is reachable three ways: as an importable Python library, as a CLI for asking a question
and getting an answer directly, and as an MCP server that gives agent frameworks such as LangChain a
planning tool.

## Design philosophy

Three questions are kept apart because they fail apart. What the request asks for, how much of it
one operator should take on, and what parameters that operator runs with are decided by different
stages against different information, and each is recorded separately. A wrong value in a parameter
is then repaired by writing the parameter again, and the plan above it stands.

That separation is what a logical node is for. It says what a step is for and nothing about how to
carry it out: one instruction in plain language, no source, no operator, and none of the parameters
an implementation would need. All of those belong to a particular attempt, and all can differ
between attempts at the same node.

A plan is a typed structure that can be stored, read back, and checked. So are the groupings laid
over it, each fragment's inputs and outputs, and every failure reason. Nothing that matters about a
run exists only as formatted prose. Plans are never edited in place: a repair adds a new version, so
what ran earlier stays readable exactly as it ran.

The planner never quietly ignores malformed state or missing required values. Failures it is meant
to recover from, such as invalid operator parameters or a result that does not hold up, are recorded
as outcomes with a reason; anything else, such as a plan object that is not a plan or a missing
configuration value, is raised. Recovery is then something the planner decides to do, not something
that happens by accident.

The library holds all the behavior: the CLI and the MCP server read requests, format responses, map
errors, and load configuration, and nothing more. Everything read or written goes through one
module, `lib/src/blue_data_planner/data/`, so no other module imports `blue-data` and no code
reaches Redis, a vector store, or a file directly.
