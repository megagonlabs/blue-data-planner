# blue-data-planner — Data Model

blue-data-planner owns no durable storage. Every shape in this document lives in memory for the
length of one request, and a few of them leave the process as JSON: a plan and a result in an MCP
response, a result printed as JSON by the CLI, and whatever a library caller chooses to write down.
The data this system reads is owned by `blue-data`; the data it writes is returned to the caller.

This document fixes the shapes. [06-library.md](06-library.md) names the classes and says what each
is responsible for; here each one gets its fields, its JSON form, and the rules its values follow.
[09-api-interface-spec.md](09-api-interface-spec.md) says which interface accepts and returns which
of them.

## Overview

Four kinds of shape appear, in the order a request produces them.

| Kind | Produced by | Lives | Crosses a process boundary |
| --- | --- | --- | --- |
| A plan: logical nodes, then the fragments covering them | The logical and physical planners | One request, unless a caller keeps it | Yes, as JSON |
| Rows moving between fragments | Operator calls | One request | No |
| A run's record: attempts, model usage, counters | The run loop | One request, unless a caller keeps it | Yes, as JSON |
| Catalog readings from `blue-data` | `data/` | One planning or execution call | No |

One sentence links them. A request becomes a logical plan; a physical plan cuts that plan into
fragments and binds each one to an operator; running a fragment produces rows and one attempt
record; the plan, the rows, and every attempt end up in the `PlanResult` the caller receives.

The vocabulary follows [04-detailed-architecture.md](04-detailed-architecture.md). A logical
operator is a node kind such as `FILTER`. A physical operator is one implementation such as
`textsuite.semantic_filter`. A fragment is the group of nodes one physical operator runs in a single
call.

The word type is kept for data types. `ColumnDataType` and a field called `dtype` describe the type
of a value; `kind` describes everything else, such as a node kind or a source kind
([08-coding-style.md](08-coding-style.md)).

---

## What is not stored

Writes through `data/` go to a source, never to storage of this system's own. A bind join pushes a
session-scoped temporary table into a SQL source, and the insert operators write rows a caller asked
for; both belong to the source rather than here. Whether an operator may also register what it
created, putting it in the catalog, is a `blue-data-ops` question rather than one this design
settles. What a reader might expect to find stored and will not is under
[what is deliberately absent](#what-is-deliberately-absent).

---

## 1. The plan

`DataPipeline` holds every plan a request produced, and names the two that are current.

```python
@dataclass
class DataPipeline:
    logical_plans: dict[str, LogicalPlan]
    physical_plans: dict[str, PhysicalPlan]
    current_logical_plan_id: str | None
    current_physical_plan_id: str | None
```

The container is mutable and everything in it is frozen. A plan is never edited: rewriting one means
adding a new version and moving the pointer, so what ran earlier stays readable exactly as it ran.
Every repair is the same operation ([04, recovering from a
failure](04-detailed-architecture.md#5-recovering-from-a-failure)): add one immutable plan, move one
pointer.

| Repair | What is added |
| --- | --- |
| Fragment, cover | A `PhysicalPlan` with a new id over the same `logical_plan_id` |
| Replan | A `LogicalPlan` with a new id whose `parent_id` names the version it continues, then a cover for it |

The logical half is the DAG. The physical half annotates it: which nodes are grouped, which operator
runs each group, and which source each scan reads. Every node id a physical plan holds names a node
of the logical plan it covers, so a physical plan on its own describes no graph and is read together
with that plan.

### The logical half

A logical plan is a directed acyclic graph of typed nodes held as structured data (ADR-007).

```python
@dataclass(frozen=True)
class LogicalPlan:
    id: str                # a UUID
    parent_id: str | None  # the version this continues, None on the first
    nodes: list[PipelineNode]
    output_node_id: str | None
```

A new version carries over every node the run has something for, and drops the rest. The
continuation the planner writes covers everything else, including work that had been planned but not
yet reached.

| Node | Carried over when |
| --- | --- |
| An operator node | It belongs to a fragment whose result the run still holds. The nodes that fragment absorbed are carried with it |
| `DISCOVER` | Its search resolved, so the run holds a `DiscoveryResult` for it |
| `REPLAN`, `RETURN` | Never. Neither holds anything, and a `REPLAN` node that fired must not fire again |

An absorbed node materialized no table, so the run holds no result under its id. It is carried
anyway, because the fragment's root reads it and dropping it would leave that edge pointing at
nothing, and because a later cover can only fuse those nodes again if they are still in the plan.
The carried set is therefore a superset of the keys of the run's result map for the parent version,
and moving those results forward is still a copy of one map
([section 3](#what-a-run-holds-while-it-runs)).

Within one lineage a continuation may not give an existing node id to a different node. It names an
existing id only to read a node it carried over.

`output_node_id` is the id of the `RETURN` node, and is `None` only while a plan ends in a control
node and its ending has not been written yet. Validation accepts exactly two shapes: one `RETURN`
and no terminal control node, or a terminal control node and no `RETURN` (ADR-007). A plan carrying
both is rejected, since the planner cannot both defer the ending and write it.

### `PipelineNode`

| Field | Type | Written by | Present |
| --- | --- | --- | --- |
| `id` | `str` | The logical planner | Always |
| `kind` | `LogicalOperator \| ControlOperator` | The logical planner | Always |
| `input_node_ids` | `list[str]` | The logical planner | Always, possibly empty |
| `attributes` | `dict[str, Any]` | The logical planner | Always |
| `required_output_columns` | `list[OutputColumn]` | The logical planner | Always, possibly empty |

A node carries no source, no operator id, no operator parameters, and no result. Which source a scan
reads and which operator runs it are properties of the physical plan, because both can differ
between passes over the same node. What a node produced belongs to the run
([section 3](#what-a-run-holds-while-it-runs)).

### Node kinds and what their attributes hold

A node's `kind` decides which keys its `attributes` carries. Nothing else is accepted.

| `kind` | Node | `attributes` |
| --- | --- | --- |
| `SCAN`, `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION` | Operator | `{"instruction": str}` |
| `RETURN` | Terminal | `{"columns": list[str]}` |
| `REPLAN`, `DISCOVER` | Control | `{"instruction": str}` |

The instruction is one sentence in plain language saying what the step is for, and it carries no
operator parameters (ADR-008). Working those out belongs to the fragment runner, and they land in
`FragmentAttempt.operator_attributes` shaped by the chosen operator's `parameter_schema`.

### How many inputs a node takes

`INPUT_ARITY` fixes how many tables a node transforms, and validation rejects a node given fewer.

| `kind` | Transforms |
| --- | --- |
| `SCAN` | 0, since it reads a source rather than another node |
| `JOIN`, `UNION` | 2. Merging three tables is two unions |
| `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `RETURN` | 1 |
| `REPLAN`, `DISCOVER` | Any number, including zero. A control node has no arity (ADR-007) |

A node may list more inputs than it transforms. The first `INPUT_ARITY[kind]` are the tables it
works on, and the rest are reference inputs, whose rows its instruction mentions without
transforming them.

Arity has one job: telling a plan that cannot run from one that can. A `JOIN` given one input is
broken, and that is what the check catches.

### Declared output columns

A node declares both the name and the type of every column its result has to carry. The logical
planner writes them, since it is the component that knows what a later node will read.

```python
ColumnDataType = Literal["str", "int", "float", "bool"]


@dataclass(frozen=True)
class OutputColumn:
    name: str
    dtype: ColumnDataType


required_output_columns: list[OutputColumn]
```

After an operator returns, `execute_physical_operator` checks the result against the list the
fragment's root node declares: every named column has to be present, and every value in it has to
coerce to the declared type. A missing column, or a value spelling no such type, fails the call as a
recoverable error, so the fragment is planned again
([04, failures that skip recovery](04-detailed-architecture.md#failures-that-skip-recovery)).
An empty list means no check, and a `RETURN` node carries none, since it names input columns to
project instead of promising output.

The declarations on absorbed nodes are checked against nothing, because a fused call produces no
intermediate tables. They are still written and still read: the physical planner uses them to
describe what a fragment must produce, and one of them becomes a checked declaration again as soon
as that node is the root of a smaller fragment.

The check exists because semantic operators return text for everything. A LOTUS operator that
extracts a year gives back `"1815"`, and the numeric operator downstream rejects it. Coercing at the
fragment boundary keeps a plan's later steps from failing on a type nobody declared.

| Declared | Accepts | Rejects |
| --- | --- | --- |
| `str` | Any value, unchanged | Nothing |
| `int` | An integer, or a string or float spelling a whole number | A string spelling no number |
| `float` | Any number, or a string spelling one | A string spelling no number |
| `bool` | A boolean, or `true`, `false`, `yes`, `no`, `y`, `n`, `1`, `0` | Any other string |

Being told the type lets the check accept spellings that inference has to refuse: `y` is a boolean
only because a node said the column is one. The job is narrow, and three rules follow from that.

- `None` passes every declared type. A missing value is a value the source holds, not a type error.
- A value declared `str` passes through unchanged and is not stringified. `str` rejects nothing, and
    turning `42` into `"42"` would be a value change, not a type check.
- A comma-formatted number such as `"142,984"` declared as `int` is refused, not repaired. Reading
    it as a number is a value change, and that belongs to the operator call. Nothing here infers a
    type from content, so the ambiguity inference would have to resolve never comes up.

There is no date type. A date travels as a `str` in its ISO 8601 form.

### The physical half

A physical plan cuts the logical plan into fragments and binds each one to a physical operator and,
where the fragment scans, to a source. It carries no operator parameters: those are written when the
fragment runs, against the data its inputs produced. A physical plan is a prepared plan whose
parameters are still unbound.

```python
@dataclass(frozen=True)
class InputData:
    logical_node_id: str  # the frontier node whose output arrives in this position
    name: str             # what the operator should call it
    description: str      # what it holds, in plain language


@dataclass(frozen=True)
class PhysicalPlanFragment:
    id: str
    logical_node_ids: list[str]       # in topological order
    root_logical_node_id: str         # the last node, whose output the fragment produces
    operator_id: str
    scan_sources: dict[str, str]      # SCAN node's logical id to catalog path
    instruction: str                  # what the operator is told to do
    input_data: list[InputData]       # one per frontier node, in the order they are passed
    required_output_columns: list[OutputColumn]


@dataclass(frozen=True)
class PhysicalPlan:
    id: str                # a UUID
    parent_id: str | None  # the cover this replaces, None on the first for a logical plan
    logical_plan_id: str   # the LogicalPlan this covers
    fragments: list[PhysicalPlanFragment]
```

| Rule | Detail |
| --- | --- |
| Coverage | Every operator node of the logical plan appears in exactly one fragment. `RETURN`, `REPLAN`, and `DISCOVER` appear in none |
| Shape | Each fragment is connected, convex, and single exit: only the root may have consumers outside it ([04, what a fragment may be](04-detailed-architecture.md#what-a-fragment-may-be)) |
| Sources | `scan_sources` names a catalog path for each `SCAN` in the fragment. A `SCAN` left out of it reads no registered source, which is what a scan of the model's world knowledge looks like. An operator declaring `source_kinds` needs every scan bound to a source of one of those kinds, and an operator declaring none needs every scan left out. A fragment containing no `SCAN` is bound to an operator that owns its engine (ADR-020) |
| Instruction | Written as an instruction to the operator: "Find data scientist job postings, with their title, company id, and location" |
| Inputs | `input_data` lists the frontier nodes, which are the nodes outside the fragment feeding nodes inside it, in the order their tables arrive. One entry covers everything that node produced, so it may stand for more than one table; those tables share the name and keep their order |
| Columns | `required_output_columns` is copied from the root node unless the model declares that the fusion cannot produce that list |
| Identity | A fragment is what its node set says it is. `id` is fresh in every cover, so an attempt names its cover as well, and two covers are compared by node set, not by id |

A cover is written whole rather than patched, and what carries over from the previous cover is
decided by comparing the two.

A fragment keeps its id across covers while its node set is unchanged, so the ids say which groups
the new cover cut differently.

| A fragment whose node set | What happens |
| --- | --- |
| Is unchanged from the previous cover | Keeps its id, keeps its root's result, and does not run again |
| Changed, or is new | Drops the results of every node in it, and of every executed node downstream of those, then runs |

Node sets also bound the retry loop. A rejected fragment is covered again over its own node set, and
the result either keeps that set, which spends another attempt against it, or splits it into smaller
sets, each of which starts its own attempt count. Splitting strictly shrinks a set, so the process
ends at single nodes.

### A physical operator

One candidate implementation, as the router holds it and as the physical planner reads it.

```python
@dataclass(frozen=True)
class PhysicalOperator:
    operator_id: str
    logical_operators: frozenset[LogicalOperator]
    description: str
    parameter_schema: type[BaseModel]
    call: Callable[[DataBundle, dict[str, Any]], OperatorOutput]
    source_kinds: frozenset[str] | None = None
    accepts_bound_inputs: bool = False
    limitations: str | None = None
```

| Field | Holds |
| --- | --- |
| `logical_operators` | Every logical operator this implementation can execute. A single member means it can only ever cover one node |
| `source_kinds` | The kinds of registered source it can read, or `None` when it reads none |
| `accepts_bound_inputs` | Whether it can take tables from other fragments alongside the source it reads. A source-access operator that cannot is limited to fragments with an empty frontier |
| `limitations` | What it cannot do, in plain language, where its author can state it. A Text-to-SQL operator cannot express a semantic predicate, and saying so is what gives the physical planner something to weigh against a description that otherwise promises everything |
| `description`, `parameter_schema`, `call` | What it does, the shape of its parameters, and the callable execution invokes |

`parameter_schema` is a Pydantic model class, hand-written per operator (ADR-010). It serves twice:
the fragment runner passes it to the model client as a response schema, and
`execute_physical_operator` validates the parameters against it before billing a call
([05, structured output](05-software-stack.md#structured-output)). A `PhysicalOperator` is held in
process and never serialized, so nothing needs it as JSON.

`logical_operators` is the whole matching rule. Candidate fragments are the connected, convex
subgraphs built from nodes whose kind is in that set, and `source_kinds` then constrains which
sources those scans may be bound to. Nothing declares the order the kinds may appear in, and nothing
declares that two scans in one fragment have to read the same source (question 44).

### Identifiers

Five id formats travel in this data, and they are not interchangeable. Node ids and fragment ids are
scoped to one plan and stay short. Plan ids are UUIDs, because a physical plan names the logical
plan it covers by id, and because a plan can be serialized, stored, and read back beside plans from
other runs.

| Id | Format | Example | Resolved by |
| --- | --- | --- | --- |
| Node id | `n` followed by an integer, unique within a plan | `n6` | `LogicalPlan.node`, which raises on an unknown id |
| Fragment id | `f` followed by an integer, fresh in every cover | `f2` | `PhysicalPlan.fragment` |
| Plan id | A UUID, on a logical plan and on a physical plan | `9f1c...` | `DataPipeline.logical_plans` and `.physical_plans` |
| Operator id | Suite name, a dot, then the operation | `blue.nl2sql`, `lotus.sem_filter` | `OperatorRouter`, while the physical plan is written |
| Registry operator id | The operator's `name`, `@`, then a version | `select@2.0.0` | `blue-data-ops`'s `OperatorRegistry.resolve` |

A physical plan records an operator id, and that is what the router answers for. A registry operator
id never appears in a plan. The adapter that builds a physical operator from a registry class is the
only thing holding the two in agreement (ADR-009).

### Serialized form

`to_dict` and `from_dict` are the JSON form. It carries the same field names as the class and writes
enum members as their values. Every field is written whatever it holds, and `from_dict` requires
every key: nothing is optional, and nothing is repaired.

A `DataPipeline` serializes as its two maps and its two pointers. One version of each is shown here.

```json
{
  "current_logical_plan_id": "3f2b9c14",
  "current_physical_plan_id": "b7c1d0e2",
  "logical_plans": {
    "3f2b9c14": {
      "id": "3f2b9c14",
      "parent_id": null,
      "output_node_id": "n7",
      "nodes": [
        {
          "id": "n1",
          "kind": "SCAN",
          "input_node_ids": [],
          "attributes": {"instruction": "job postings"},
          "required_output_columns": [
            {"name": "title", "dtype": "str"},
            {"name": "company_id", "dtype": "int"},
            {"name": "location", "dtype": "str"}
          ]
        },
        {
          "id": "n2",
          "kind": "FILTER",
          "input_node_ids": ["n1"],
          "attributes": {"instruction": "the posting is a data scientist role"},
          "required_output_columns": [
            {"name": "title", "dtype": "str"},
            {"name": "company_id", "dtype": "int"},
            {"name": "location", "dtype": "str"}
          ]
        },
        {
          "id": "n7",
          "kind": "RETURN",
          "input_node_ids": ["n2"],
          "attributes": {"columns": ["title", "company_id", "location"]},
          "required_output_columns": []
        }
      ]
    }
  },
  "physical_plans": {
    "b7c1d0e2": {
      "id": "b7c1d0e2",
      "parent_id": null,
      "logical_plan_id": "3f2b9c14",
      "fragments": [
        {
          "id": "f1",
          "logical_node_ids": ["n1", "n2"],
          "root_logical_node_id": "n2",
          "operator_id": "tablesuite.nl2sql",
          "scan_sources": {
            "n1": "/source/jobs/database/jobs/collection/public/entity/postings"
          },
          "instruction": "Find data scientist job postings, with their title, company id, and location",
          "input_data": [],
          "required_output_columns": [
            {"name": "title", "dtype": "str"},
            {"name": "company_id", "dtype": "int"},
            {"name": "location", "dtype": "str"}
          ]
        }
      ]
    }
  }
}
```

A plan carries no results. It is the description of the work, and what running it produced belongs
to `PlanResult`.

Serialization runs one way. `to_dict` is how a plan leaves the process, in an MCP response, in CLI
output, or into whatever a library caller writes down, and nothing reads one back in to run it. A
plan is what a request produced, not an input a request can be given. `to_dict` also orders what
it writes: a logical plan's nodes come out in dependency order, so a plan that has been through
`to_dict` and `from_dict` holds them in that order rather than in the order it was built.

`from_dict` exists for reading a recorded plan back: a test comparing what a run wrote, a benchmark
harness reading many runs, an inspection tool. It is strict. A missing required key, an unknown
`kind`, an `attributes` key the kind does not take, or an `input_node_ids` entry naming no node in
the plan raises, and none of them is repaired or ignored.

---

## 2. Rows moving between fragments

One fragment passes a list of tables to the next, and a table is a plain list of row dicts
(ADR-012). No data frame, no record wrapper, and no type belonging to an operator suite reaches a
plan.

```python
Record = dict[str, Any]  # one row
Data = list[Record]  # one table
DataBundle = list[Data]  # what one fragment passes to the next
```

| Rule | Detail |
| --- | --- |
| Keys | Column names as strings. A row is not required to carry every key another row carries |
| Values | Anything JSON can hold, plus `None` for a missing value |
| Order | The table order and the row order a fragment produces are the order the next one receives |
| Empty | `[]` is a valid result and not a failure, as a bundle and as a table ([04, executing](04-detailed-architecture.md#executing)) |
| Tables | Every Blue operator returns one table, so a bundle usually holds one. Nothing requires it |

A fragment's input is the bundles its frontier nodes produced, concatenated in the order
`input_data` lists them, so an operator receives one bundle rather than one per input. A fragment
with an empty frontier receives an empty bundle.

An operator returns its bundle alongside an optional report of what it did.

```python
@dataclass(frozen=True)
class OperatorOutput:
    data: DataBundle
    report: dict[str, Any] | None = None
```

The report is free-form and nothing reads it while a run continues. It travels to the caller so a
finished run can be read: the query an operator wrote, the operations it ran, what its own model
calls cost. Most operations can report nothing and leave it unset, which is why the field is one
untyped dict rather than named fields (ADR-020). What an operation puts in it is that operation's
own business, so a benchmark may rely on the keys `builtin` writes without this shape promising
them.

Each adapter converts between this shape and its own suite's at its own edge: the LOTUS adapter
builds one data frame per table going in and flattens coming out.

---

## 3. What a run produces

`PlanResult` is returned whichever way a request ends, and holds everything about the run.

```python
@dataclass(frozen=True)
class PlanResult:
    status: RunStatus
    answer: DataBundle | None
    pipeline: DataPipeline
    results: dict[str, dict[str, ExecutionResult | DiscoveryResult]]
    attempts: list[FragmentAttempt]
    rejected_plans: list[RejectedPlan]
    counters: dict[str, int]
    planner_usage: list[LLMUsage]
```

`answer` is `None` rather than `[]` unless `status` is `ok`, so a caller who ignores the status
cannot mistake a failed run for an empty answer (ADR-015). That covers the planning-only operation
too: it returns the same object with status `planned`, holding the logical plan and the cover
written for it and nothing run
([03-high-level-architecture.md](03-high-level-architecture.md#dataplanner)).

`pipeline` holds every plan the request produced, superseded versions included, so a reader sees not
only how the request ran but every arrangement that was tried on the way. `parent_id` gives the
order, and `current_logical_plan_id` and `current_physical_plan_id` name the pair that produced the
answer.

`attempts` holds every fragment run, in run order, rejected ones included. Each one records the node
set it covered and the operator that ran it, so the list is also the record of where each pushdown
boundary was drawn and what happened to it. The boundary decision can therefore be reviewed without
running anything again.

`rejected_plans` holds every logical plan validation turned down, across the first generation and
every replan, each with the error that turned it down. It is empty on a run whose plans all passed.
It is what makes an `invalid_plan` run readable: the counter says how many corrections were spent,
and this says what was wrong each time.

Four questions a reader asks, and where the answer is.

| Question | Read |
| --- | --- |
| Did node `n3` succeed | Whether the current plan's slot in `results` holds an entry for `n3` |
| Which operator ran it | That entry's `operator_id`, or the attempt that produced it |
| How much was pushed down | The `logical_node_ids` of each fragment, in every cover in `pipeline.physical_plans` |
| What did discovery find | The same slot, under the `DISCOVER` node's id |

### What a run holds while it runs

`results` maps a logical plan id to a map from node id to what that node produced. `RunState` owns
it during a run, `DataPlanner` is the only writer, and `to_result` hands it to the caller unchanged.

The outer key is the logical plan version. Two versions may use the same node id for different
nodes, and two independent plans certainly will, so scoping by version is what keeps a node id
enough of an inner key. When a new version carries nodes over, its slot starts as a copy of its
parent's, which costs a map copy because the values are shared references.

| Node | Entry |
| --- | --- |
| An operator node that roots a fragment | The `ExecutionResult` that was accepted |
| An operator node a fragment absorbed | None. A fused call produces no intermediate table |
| `DISCOVER` | The `DiscoveryResult` the catalog search produced |
| `REPLAN`, `RETURN` | None. The answer belongs to `PlanResult.answer` |

Scheduling reads the current version's slot. A fragment is ready when every node on its frontier has
an entry there, and a fragment's input is those entries' outputs. A fragment whose root already has
an entry has already run and is skipped.

Entries are removed as well as added. Installing a new cover compares it with the previous one and
drops the entries of every node whose fragment changed, along with the entries of every executed
node downstream of those
([04, a cover is written whole](04-detailed-architecture.md#a-cover-is-written-whole)).

Three atomic fragments running `SCAN`, `FILTER`, and `PROJECT` leave three entries. If the next
cover fuses all three into one Text-to-SQL fragment, all three entries go, and the fused call
records one entry under the `PROJECT` node. The scan's rows are produced inside the SQL query and
never materialize again, so the old entry has to go instead of being kept under a second key:
nothing may read a table the current cover no longer produces. The single-exit rule is what makes
the removal safe, since no node outside the fragment was reading those tables.

Nothing is lost by the removal. `attempts` still holds every attempt, including the ones whose
results were dropped.

Where a result came from is not stored beside it. `ExecutionResult.operator_id` names the operator,
and the attempt that produced it is the last entry in `attempts` covering that node, so the two
never disagree.

### One attempt at one fragment

```python
@dataclass(frozen=True)
class FragmentAttempt:
    physical_plan_id: str
    fragment_id: str
    logical_node_ids: list[str]
    operator_id: str
    operator_attributes: dict[str, Any]
    result: ExecutionResult | None
    assessment: str | None
    decision: FragmentReviewDecision
```

A node set accepted on its second operator carries both tries. `result` is `None` when the operator
raised before returning anything, and `assessment` is the review's reasoning in plain language.

### What each component returns

Four result types never leave the process. `DataPlanner` reads each one, records what it holds on
the run state, and moves on. `RejectedPlan`, last below, is not one of them: it is the shape
`LogicalPlanResult.rejected` holds, and it reaches the caller inside `PlanResult.rejected_plans`.

```python
@dataclass(frozen=True)
class LogicalPlanResult:
    status: RunStatus  # ok, or invalid_plan when the corrections ran out
    plan: LogicalPlan | None
    rejected: list[RejectedPlan]
    usage: list[LLMUsage]


@dataclass(frozen=True)
class PhysicalPlanResult:
    plan: PhysicalPlan | None  # None exactly when a node has no operator
    failure_reason: str | None
    usage: list[LLMUsage]


@dataclass(frozen=True)
class FragmentResult:
    status: RunStatus
    decision: RunDecision
    attempts: list[FragmentAttempt]  # rejected ones included, in order
    failure_reason: str | None
    usage: list[LLMUsage]


@dataclass(frozen=True)
class CatalogSearchResult:
    discovery: DiscoveryResult
    usage: list[LLMUsage]


@dataclass(frozen=True)
class RejectedPlan:
    plan: LogicalPlan
    error: str
```

Every component reports what its own model calls cost, and `DataPlanner` appends each list to
`PlanResult.planner_usage` in the order they ran. `CatalogSearchResult` is a pair rather than
a field on `DiscoveryResult` because a `DiscoveryResult` goes into the run's results map and reaches
the caller inside `PlanResult.results`, where usage would be counted a second time.

`LogicalPlanResult.plan` is `None` exactly when `status` is `invalid_plan`, and `rejected` holds
every plan validation turned down, in the order they were written. A plan accepted on the first try
comes back with an empty `rejected`. A rejected plan never enters `DataPipeline`, since nothing
covered it and no version continues it, but it carries an id and a `parent_id` of its own, so a
reader can still tell which version a failed correction was written against.

`FragmentResult.attempts` holds every attempt the call made, because the parameter repair is a loop
inside the runner: it writes the operator's parameters again, in the conversation it already holds,
until one is accepted or `parameter_attempts` runs out
([04, the parameter repair](04-detailed-architecture.md#the-parameter-repair)). A single call can
therefore produce several attempts, and returning only one would lose the rejected ones that
`PlanResult.attempts` promises to carry.

`FragmentResult.status` says which ending a `stop` means, and is `ok` unless `decision` is `stop`.
The runner never returns `invalid_plan` or `replan_exhausted`, which belong to the logical planner
and to the replan allowance, neither of which it can see.

| Ending | `status` | `decision` |
| --- | --- | --- |
| The result was accepted | `ok` | `continue` |
| The runner used its parameter attempts without an accepted result | `ok` | `escalate` |
| The review said no arrangement of operators will do this | `ok` | `replan` |
| An operator failed unrecoverably | `operator_failed` | `stop` |
| The operator call budget reached its cap | `budget_exhausted` | `stop` |

`escalate` sends the failure on to the fragment repair, and `replan` sends it straight to the
replan. Which repair an `escalate` reaches is the run loop's decision, since only the loop knows how
many attempts each one has left
([04, recovering from a failure](04-detailed-architecture.md#5-recovering-from-a-failure)).

Each component returns what it did instead of writing it down as it goes, so one that raises cannot
leave a half-written record behind, and the session has one writer.

### One operator call and its outcome

```python
@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    output: DataBundle | None
    operator_id: str | None
    error: ErrorInfo | None
    row_count: int | None
    report: dict[str, Any] | None


@dataclass(frozen=True)
class ErrorInfo:
    kind: ErrorKind
    message: str
```

`output` and `row_count` are set when `status` is `ok`, and `error` when it is `failed`. No result
carries both. `report` is carried whichever way the call ended, since an operator that failed
partway through is the one with the most to say. `row_count` counts rows across every table in `output`, so it says how much data a
fragment produced rather than how its bundle is split. Nothing reports table count.

`ErrorInfo.message` names what went wrong and what was expected, in plain language, because it is
what a review turn reads and what a failure reason is written from. `kind` decides the response to a
failure, and the mapping is in
[04](04-detailed-architecture.md#failures-that-skip-recovery).

### One discovery

```python
@dataclass(frozen=True)
class DiscoveryResult:
    search_terms: list[str]        # one registry search runs per term
    matches: list[CatalogMatch]
    dropped_terms: int             # terms cut by the per-node cap
```

A `DISCOVER` node's entry in the run's `results` map holds one of these, where an operator node's
entry holds rows. Whoever reads the map reads this one the same way: the logical planner when it
writes the continuation, and the physical planner when it binds a scan to a source.

### One model call

```python
@dataclass(frozen=True)
class LLMUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    latency_seconds: float
```

`PlanResult.planner_usage` holds one record per model call in call order. There is no accumulator: a
total is a `sum()` over the list. Prompt and completion tokens are reported per call and never
summed together, since the two are priced differently and a total of them answers nothing.

The name says the scope. These are the calls the three planning components and the catalog searcher made
through `llm/`, and nothing else. A model call an operator makes internally, as a LOTUS operator
does, is billed as part of the one operator call that contains it and never appears here, because
such an operator reports no per-call token count
([05-software-stack.md](05-software-stack.md#two-model-configurations-one-budget)). What a run spent
on planning is exact; what it spent inside operators is the call count in `counters`.

### Counters

`counters` holds what the run spent, keyed by the name of the limit that bounds it: one entry per
`PlanLimits` field, and `PlanLimits` holds every bound the system obeys. Every key appears whether
or not it was spent, so a caller reads a number instead of checking for a key. The bounds themselves
are fixed when the planner is built, not per request, so nothing in a result reports them
([06-library.md](06-library.md#budgets-and-limits)).

```json
{
  "operator_calls": 7,
  "plan_corrections": 1,
  "parameter_attempts": 3,
  "fragment_attempts": 2,
  "cover_attempts": 1,
  "failure_replans": 0,
  "control_replans": 1,
  "search_terms": 12
}
```

The four recovery counters are one repair each
([04, recovering from a failure](04-detailed-architecture.md#5-recovering-from-a-failure)). The two
bounded per fragment or per node set, `parameter_attempts` and `fragment_attempts`, hold the highest
number any one of them spent rather than a total, since that is the number their bound applies to.
`search_terms` counts every term every `DISCOVER` node expanded into. The bounds themselves are in
[12-deployment-ops.md](12-deployment-ops.md).

### Stored values

Every enum inherits `str`, so a value serializes as itself. Casing follows the rule in
[08-coding-style.md](08-coding-style.md): logical operators are upper case, and statuses and
decisions are lower case.

| Enum | Values |
| --- | --- |
| `LogicalOperator` | `SCAN`, `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION`, `RETURN` |
| `ControlOperator` | `REPLAN`, `DISCOVER` |
| `RunStatus` | `ok`, `planned`, `invalid_plan`, `replan_exhausted`, `budget_exhausted`, `operator_failed`, `llm_failed` |
| `ExecutionStatus` | `ok`, `failed` |
| `ErrorKind` | `recoverable`, `unrecoverable`, `no_operator` |
| `FragmentReviewDecision` | `accept`, `reject`, `replan` |
| `RunDecision` | `continue`, `escalate`, `replan`, `stop` |

`SORT_SUPERLATIVE` is one logical operator covering both ordering and cutting, since a superlative
is an ordering with a limit.

---

## 4. Catalog readings

Three readings of the same registry serve the three planning components, and none of them is
stored. `DataCatalog`
selects and renders what `blue-data` has collected and enriched, and never profiles a column or
samples a table of its own.

The shapes below live in `types/` with the rest of the shared data classes, and `data/catalog.py`
holds the code that fills them. `CatalogMatch` is why: it reaches a caller inside
`PlanResult.results`, so `types/` has to name it, and splitting one reading from the other two on
that basis would draw a line no reader could see
([06-library.md](06-library.md#shared-types)).

| Reading | Read by | Answers |
| --- | --- | --- |
| `CatalogPreview` | The logical planner, once per pass | Which sources exist |
| `CatalogMatch` | `CatalogSearcher` when a `DISCOVER` node comes up, and the physical planner when it binds a scan | Where the data a request needs lives |
| `ColumnView` | The fragment runner, for the columns its fragment touches | How a column's values are actually spelled |

### Naming a record

`blue-data` keys every catalog record by a path encoding its full ancestry, from source down to
attribute. A `ColumnRef` is that path in parts.

```python
@dataclass(frozen=True)
class ColumnRef:
    source: str
    database: str
    collection: str
    entity: str
    attribute: str
```

An entity is a table and an attribute is a column, in `blue-data`'s naming. The path form of the
same reference is `/source/jobs/database/jobs/collection/public/entity/postings/attribute/location`,
which is what the registry stores, what a fragment's `scan_sources` holds a prefix of, and what a
lookup uses.

### What the logical planner reads

`CatalogPreview` carries source-level records and nothing below them.

```python
@dataclass(frozen=True)
class SourceRecord:
    path: str          # "/source/jobs"
    name: str
    kind: str          # the connection protocol blue-data recorded, such as "sql/sqlite"
    description: str | None


@dataclass(frozen=True)
class CatalogPreview:
    sources: list[SourceRecord]
    total_sources: int  # how many are registered, which may exceed len(sources)
```

The list is ranked against the request text and capped, and `total_sources` says whether it is the
whole registry or a sample. A source that has never been enriched renders with a name and a kind
alone, and planning still works with less to go on.

`kind` is the connection protocol `blue-data` records at registration, so it is available without
enrichment and without a search. It is what tells the physical planner whether a Text-to-SQL
operator can run a fragment scanning this source.

### What a search returns

```python
@dataclass(frozen=True)
class CatalogMatch:
    path: str          # the full path of the record that matched
    level: str         # source, database, collection, entity, or attribute
    name: str
    description: str | None
    score: float
    search_term: str   # which term found it
```

`search_records` takes one query string and returns the catalog records that match it, ranked. A
discovery node that expands into several terms calls it once per term.

### What the fragment runner reads

A `ColumnView` annotates a column the runner already knows about. The authoritative list of columns
comes from the input tables themselves, which the runner reads directly: every column, its values'
type, the row count, and a sample of rows. A view adds what only the catalog knows, over the whole
column rather than over a sample, and it is asked for one column at a time so the prompt does not
grow with the catalog.

| Field | Holds | From |
| --- | --- | --- |
| `ref` | Which column this describes | The fragment, resolved to a catalog path |
| `dtype` | The declared type | The attribute record |
| `description` | The enrichment description, where one exists | The attribute record |
| `sample_values` | A few values as they are actually spelled | The statistics collected over the column |
| `distinct_count`, `null_count`, `min`, `max` | Value counts and range | The same |
| `semantic_type` | The meaning inferred for the column's values, which is what says a column holds short codes rather than readable words | The value semantics the registry recorded |

`semantic_type` is what tells the runner a column holds short codes instead of readable words, so it
writes a filter against `"PRIJEM"` instead of guessing at `credit`.

A column with no view is still a column. It appears in the input table's column list with its type
and its sample values, and only the annotation is missing, so nothing about it reads as absent. Two
kinds of column have none. One an operator generated has no registry record at all, which is why a
column a model wrote and a semantically derived column never resolve. One arriving on the frontier
may have a record, but reaching it means tracing back through the cover to whichever fragment
scanned it, and how far to trace is open question 3 in
[13-decision-log.md](13-decision-log.md#open-questions).

---

## 5. Shapes that leave the process

The library's shapes are the reference. Each interface renders them, and the MCP server is the one
that changes them.

| Shape | Library | MCP response |
| --- | --- | --- |
| `attempts` | Every attempt, with its parameters, assessment, and decision | A count |
| `rejected_plans` | Every turned-down plan with its error | A count |
| A `DISCOVER` node's entry in `results` | Every term and every match | A count of matches |
| `planner_usage` | One `LLMUsage` per call | `{"calls": int, "cost": float}` |
| Everything else, `pipeline` included | As above | The same, as JSON |

These differences exist because an agent reasons about the outcome, and a rejected attempt's full
record would bloat every response ([09](09-api-interface-spec.md#planning-tools)). The library and
the CLI carry both in full.

A run that did not finish returns the same object with its status, `answer` null, and its counters,
in every interface. There is no separate error shape.

---

## 6. Benchmark inputs

TAG-Bench inputs live under `data/tag_bench/`, downloaded rather than committed
([07-repo-organization.md](07-repo-organization.md#where-the-benchmark-lives)).
The released files contain errors, so the build step reads a release and writes corrected queries
beside it, leaving the download untouched.

| Path | Holds | Written by |
| --- | --- | --- |
| `data/tag_bench/queries.json` | The corrected queries the harness reads | `lib/scripts/tag_bench/fetch_queries.py` |
| `data/tag_bench/databases/*.db` | The five BIRD SQLite databases | `lib/scripts/tag_bench/fetch_databases.py` |
| `data/tag_bench/registry.json` | A `blue-data` registry over those databases, synced, with stats collected and enriched | `lib/scripts/tag_bench/register_databases.py` |

The registry is built once and kept, so tests and benchmark runs open a file instead of re-syncing
five databases.

A corrected query carries the released fields plus what a correction needs to be traceable.

```json
{
  "query_id": 42,
  "db": "california_schools",
  "query": "Which schools in Alameda County have an average SAT math score above 600?",
  "answer": ["Alameda High", "Piedmont High"],
  "query_type": "match",
  "correction": "the released answer omitted two schools present in the source tables"
}
```

`correction` is absent on a query the build step passed through unchanged, which is what makes the
corrected set auditable against the release.

---

## What is deliberately absent

| Not here | Why |
| --- | --- |
| A `schema_version` field in a serialized plan | Nothing persists a plan. The shape is public interface, covered by semver |
| A stored session, plan, or run | A `PlanResult` is the record, and it is returned to the caller |
| A model response cache | Every model call reaches the provider |
| Operator or router state between requests | Both are assembled at startup and held in process |
| A separate error response shape | A run that did not finish returns a `PlanResult` with a status (ADR-015) |
| A row type of its own | A fragment passes plain dicts, so no shape belongs to a suite (ADR-012) |
| Cost and cardinality estimates on a node or a fragment | Out of the first release |
| A per-node record in `PlanResult` | A node's outcome is derivable from the attempt that covered it, and two places holding it would drift |
