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
| A run's record: results, conversations, counters | The run loop | One request, unless a caller keeps it | Yes, as JSON |
| Catalog readings from `blue-data` | `data/` | One planning or execution call | No |

One sentence links them. A request becomes a logical plan; a physical plan cuts that plan into
fragments and binds each one to an operator; running a fragment produces rows; the plan, the rows,
and every exchange that produced them end up in the `RunResult` the caller receives.

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
temporary table into a SQL source, and the insert operators write rows a caller asked
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
| Cover | A `PhysicalPlan` with a new id over the same `logical_plan_id` |
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
| An operator node | It belongs to a fragment whose result the run still holds. The nodes that fragment grouped are carried with it |
| `DISCOVER` | Its search resolved, so the run holds a `DiscoveryResult` for it |
| `REPLAN`, `RETURN` | Never. Neither holds anything, and a `REPLAN` node that fired must not fire again |

A grouped node materialized no table, so the run holds no result under its id. It is carried
anyway, because the fragment's root reads it and dropping it would leave that edge pointing at
nothing, and because a later cover can only group those nodes again if they are still in the plan.
The carried set is therefore a superset of the nodes the run holds a result for. Nothing is moved
forward: a result is filed under the signature of the work that produced it, and a carried node's
work is found again by the cover written for the new version
([reusing a result](#reusing-a-result)).

A control node is terminal. Nothing reads one, because what it produces is catalog records or a
reason to plan again rather than rows, and the next plan is what reads that. Validation turns down a
plan where any node lists a control node among its inputs, which is what keeps every input a node
reads a table the run can hand it.

Within one lineage a continuation may not give an existing node id to a different node. It names an
existing id only to read a node it carried over, and validation turns down a continuation that
writes one. Grouping such a node instead would attach a carried node's name to work nobody
planned.

A `RETURN` node's `columns` name the columns holding the answer and nothing else, so what a caller
receives is the values the request asked for rather than the rows they were found in. They name
columns the node it reads declares, and that node's `required_output_columns` may not be empty.
Validation checks the two against each other. This is the one place a declaration is required rather
than optional, because it is the one place nothing downstream would catch a mismatch: the answer is
projected after the last fragment has run, so a column nothing produces costs a whole run's work
before the planner is asked for the ending again.

`output_node_id` is the id of the `RETURN` node, and is `None` only while a plan ends in control
nodes and its ending has not been written yet. Validation accepts exactly two shapes: one `RETURN`
and no terminal control node, or one or more terminal control nodes and no `RETURN` (ADR-007). A
plan carrying both is rejected, since the planner cannot both defer the ending and write it.

A plan holds at most one `REPLAN` node and any number of `DISCOVER` nodes. Resolving a `REPLAN`
runs nothing, so a second one asks for a continuation the first already asked for, while each
`DISCOVER` carries its own instruction and searches on its own terms
([04](04-detailed-architecture.md#three-kinds-of-node)).

A plan holding a `REPLAN` has to write at least one step it did not carry over. The node marks where
the pass stops so the rest can be written against the rows the pass produced, and a pass running
nothing produces none: the next version would be written from the same prompt, and the run would
spend a replan on it.

Several terminal control nodes is the ordinary shape for a request needing two or more things found. "Find
data scientist openings at Fortune 500 companies" needs a list of those companies and a job postings
source, and neither search narrows the other, so both `DISCOVER` nodes belong to the same pass.
Reachability is checked only where a plan ends at `RETURN`. A `REPLAN` reads nothing, so the last
step a pass wrote ends unread, and the next version is what reads the rows it produced.

### `PipelineNode`

| Field | Type | Written by | Present |
| --- | --- | --- | --- |
| `id` | `str` | The logical planner | Always |
| `kind` | `LogicalOperator \| ControlOperator` | The logical planner | Always |
| `input_node_ids` | `list[str]` | The logical planner | Always, possibly empty |
| `attributes` | `dict[str, Any]` | The logical planner | Always |
| `required_output_columns` | `list[OutputColumn]` | The logical planner | Always; empty for `SCAN` and `RETURN` |

A `SCAN` names the source it reads; no node names an operator, operator parameters, or a result.
The source is the logical planner's to name because it is the stage that reads what the searches
found, and the nodes reading a scan declare their columns from that entity's attributes. Which
operator runs a node is a property of the physical plan, because it can differ between covers of the
same node. What a node
produced belongs to the run ([section 3](#what-a-run-holds-while-it-runs)).

### Node kinds and what their attributes hold

A node's `kind` decides which keys its `attributes` carries. Nothing else is accepted.

| `kind` | Node | `attributes` |
| --- | --- | --- |
| `SCAN` | Operator | `{"instruction": str, "source": str \| None}` |
| `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION` | Operator | `{"instruction": str}` |
| `RETURN` | Terminal | `{"columns": list[str]}` |
| `REPLAN`, `DISCOVER` | Control | `{"instruction": str}` |

The instruction is one sentence in plain language telling whatever runs the node what to do, so it
starts with a verb: `load job postings`, `find the postings whose title is a data scientist role`,
`find a source holding one profile per company`. It carries no operator parameters (ADR-008).
Working those out belongs to the fragment runner, and what it writes is shaped by the chosen
operator's `parameter_schema`.

A scan's `source` is the catalog path of an entity a `DISCOVER` node found, or `None` for a scan that
reads no registered source, whose operator produces the data itself: from the model's world
knowledge, for example. Validation rejects a path the run's catalog context does not hold, so
a scan reads only an entity the run searched for.

### How many inputs a node takes

`INPUT_ARITY` fixes how many tables a node transforms, and validation rejects a node given fewer.

| `kind` | Transforms |
| --- | --- |
| `SCAN` | 0, since it reads a source rather than another node |
| `JOIN`, `UNION` | 2. Merging three tables is two unions |
| `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `RETURN` | 1 |
| `DISCOVER` | Any number, including zero. A control node has no arity (ADR-007) |
| `REPLAN` | None. It reads nothing, so there is no node for it to name |

A node may list more inputs than it transforms. The first `INPUT_ARITY[kind]` are the tables it
works on, and the rest are reference inputs, whose rows its instruction mentions without
transforming them.

Arity has one job: telling a plan that cannot run from one that can. A `JOIN` given one input is
broken, and that is what the check catches.

### Declared output columns

A node other than `SCAN` and `RETURN` declares both the name and the type of every column its
result has to carry. The logical planner writes them, since it is the component that knows what a
later node will read. A `SCAN` declares none: what it reads is its source's attributes, and copying
them into a declaration only adds a place to misspell one. The physical planner writes the columns
of each fragment ([the physical half](#the-physical-half)).

```python
ColumnDataType = Literal["str", "int", "float", "bool"]


@dataclass(frozen=True)
class OutputColumn:
    name: str
    dtype: ColumnDataType


required_output_columns: list[OutputColumn]
```

After an operator returns, `execute_physical_operator` checks the result against the fragment's
`required_output_columns`: every named column has to be present, and every value in it has to
coerce to the declared type. A missing column, or a value spelling no such type, fails the call as a
recoverable error, so the fragment is planned again
([04, failures that skip recovery](04-detailed-architecture.md#failures-that-skip-recovery)).
A column the list does not name travels on unchanged: the declaration is a floor the result has to
carry, not a projection.
A `RETURN` node carries none, since it names input columns to project instead of promising output,
and the node it reads has to declare the columns it projects
([the logical half](#the-logical-half)), so a `RETURN` never reads a `SCAN` directly.

The declarations on grouped nodes are checked against nothing, because a fragment call produces no
intermediate tables. Every such node still declares its columns, because any of them is the starting
point for a fragment's list as soon as that node is the root of a smaller fragment.

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

A physical plan cuts the logical plan into fragments and binds each one to a physical operator. It
carries no operator parameters: those are written when the fragment runs, against the data its
inputs produced. A physical plan is a prepared plan whose parameters are still unbound.

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
| Sources | `scan_sources` is copied from the fragment's `SCAN` nodes: each scan naming a source maps to that path, and a scan naming none is left out. An operator declaring `source_kinds` takes a fragment only when every scan in it names a source of one of those kinds, and an operator declaring none only when every scan is left out. A fragment containing no `SCAN` is bound to an operator that owns its engine (ADR-020) |
| Instruction | One instruction to the operator, starting with a verb as a node instruction does, stating every condition the grouped nodes state and joining them with *and*, and saying what the fragment produces rather than how to produce it: "Find Bay Area job postings whose title is a data scientist role, with their title, company id, and location" |
| Inputs | `input_data` lists the frontier nodes, which are the nodes outside the fragment feeding nodes inside it, in the order the fragment's nodes read them. The list is the plan's to decide, and only each entry's name and description are written during physical planning. One entry covers everything that node produced, so it may stand for more than one table; those tables share the name and keep their order |
| Columns | `required_output_columns` is written by the model and holds at least one column, worked out from the declarations of the fragment's nodes and from what reads its root, named as the operator produces them ([04, choosing among candidates](04-detailed-architecture.md#choosing-among-candidates)) |
| Identity | `id` names one fragment, and a fragment carried into a new cover keeps it, so the exchange that wrote its parameters stays attached to the work. What the fragment is for is its signature, which covers everything deciding what it produces, so that is what says whether two covers ask for the same work |

A cover is written whole rather than patched, and what carries over from the previous cover is
decided by each fragment's signature rather than by comparing the two covers
([reusing a result](#reusing-a-result)).

| A fragment asking for | What happens |
| --- | --- |
| Work the run has a result for | Reads that result and does not run |
| Anything else | Runs, and files its result under its own signature |

The repair loop follows the root node rather than the node set. A rejected fragment is covered again
over its own node set, and the result either keeps that set or splits it into smaller ones. Splitting
strictly shrinks a set, so the process ends at single nodes, and the root is the one node every cut
leaves in place.

### A physical operator

One candidate implementation, as the router holds it and as the physical planner reads it.

```python
@dataclass(frozen=True)
class PhysicalOperator:
    operator_id: str
    logical_operators: frozenset[LogicalOperator]
    description: str
    parameter_schema: type[BaseModel]
    call: Callable[[DataBundle, dict[str, Any], list[OutputColumn]], OperatorOutput]
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
| `description`, `parameter_schema`, `call` | What it does, the shape of its parameters, and the callable execution invokes. A callable is handed the bundle, its parameters, and the fragment's `required_output_columns` (ADR-012) |

`parameter_schema` is a Pydantic model class, hand-written per operator (ADR-010). It serves twice:
the fragment runner passes it to the model client as a response schema, and
`execute_physical_operator` validates the parameters against it before billing a call
([05, structured output](05-software-stack.md#structured-output)). A `PhysicalOperator` is held in
process and never serialized, so nothing needs it as JSON.

`logical_operators` is the whole matching rule. Candidate fragments are the connected, convex
subgraphs built from nodes whose kind is in that set, and `source_kinds` then decides whether the
operator reads the sources those scans name. Nothing declares the order the kinds may appear in,
and nothing declares that two scans in one fragment have to read the same source (question 44).

### Identifiers

Six formats travel in this data, and they are not interchangeable. Node ids and fragment ids are
scoped to one plan and stay short. Plan ids are UUIDs, because a physical plan names the logical
plan it covers by id, and because a plan can be serialized, stored, and read back beside plans from
other runs. A signature is the one value derived rather than assigned: it names what a node does, so
the same work in two covers gets the same value and an id never does.

| Id | Format | Example | Resolved by |
| --- | --- | --- | --- |
| Node id | `n` followed by an integer, unique within a plan | `n6` | `LogicalPlan.node`, which raises on an unknown id |
| Fragment id | `f` followed by an integer, fresh when the fragment is, kept when it is carried | `f2` | `PhysicalPlan.fragment` |
| Signature | A hash of the work one node does | `a3f1...` | `signatures`, over a plan and a cover |
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
          "attributes": {
            "instruction": "load job postings",
            "source": "/source/jobs/database/jobs/collection/public/entity/postings"
          },
          "required_output_columns": []
        },
        {
          "id": "n2",
          "kind": "FILTER",
          "input_node_ids": ["n1"],
          "attributes": {"instruction": "find the postings whose title is a data scientist role"},
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
          "attributes": {"columns": ["title"]},
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
to `RunResult`.

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

### A table's summary

A table is summarized wherever it is shown rather than passed on: in a prompt putting a fragment's
input or its result in front of a model, and in the CLI's trace.

```python
@dataclass(frozen=True)
class TableSummary:
    row_count: int
    columns: list[str]
    sample_rows: Data
```

`sample_rows` holds the first rows of the table, and whoever asks for the summary chooses how many.
The cap belongs to the caller because the two readers spend rows against different things: a prompt
against a context window, a person reading a trace against a screen.

A table is as large as the data its fragment read. A summary has a fixed size, which is why it is
what gets shown.

---

## 3. What a run produces

`RunResult` is returned whichever way a request ends, and holds everything about the run.

```python
@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    answer: DataBundle | None
    failure_reason: str | None
    pipeline: DataPipeline
    results: dict[str, FragmentResult | DiscoveryResult]   # keyed by signature
    counters: RunCounters
    conversations: RunConversations
```

`answer` is `None` rather than `[]` unless `status` is `ok`, so a caller who ignores the status
cannot mistake a failed run for an empty answer (ADR-015). `failure_reason` runs the other way: every
failure status carries one, holding why the run ended in the words of whatever ended it — the reason
no repair answered, the operator failure that stopped the run, the objection the last rejected plan
drew, the defect the run loop caught. `ok` never carries one, and `planned` carries one only when no
cover could be written. That covers the planning-only operation
too: it returns the same object with status `planned`, holding the logical plan and the cover
written for it and nothing run
([03-high-level-architecture.md](03-high-level-architecture.md#dataplanner)).

`pipeline` holds every plan the request produced, superseded versions included, so a reader sees not
only how the request ran but every arrangement that was tried on the way. `parent_id` gives the
order, and `current_logical_plan_id` and `current_physical_plan_id` name the pair that produced the
answer.

`conversations` holds what the planner said to the model and what came back, one exchange per
component. Everything a run tried and did not keep is there: a plan validation turned down and the
objection that turned it down, a binding the physical planner replaced, the parameters a review
rejected and what it said about them. A run that failed is readable from the same field as a run that
worked, and no second record of the same events is kept beside it.

`counters` holds what the run spent against the bounds it obeyed, and carries those bounds with it.

Four questions a reader asks, and where the answer is.

| Question | Read |
| --- | --- |
| Did node `n3` succeed | The `status` of the entry `results` holds under the signature the current cover gives `n3` |
| Which operator ran it | The `operator_id` of the fragment `n3` roots, in the current cover |
| How much was pushed down | The `logical_node_ids` of each fragment, in every cover in `pipeline.physical_plans` |
| What did discovery find | The `DiscoveryResult` under the `DISCOVER` node's signature |

### What a run holds while it runs

`results` maps a signature to how the work under that signature ended. `RunState` owns it during
a run and `to_result` hands it to the caller unchanged. A fragment the review turned down is filed
with the rest, so the record says which step of a cover ran and what it was answered with, and not
only which ones produced something (ADR-024).

| Node | Entry |
| --- | --- |
| An operator node that roots a fragment | The `FragmentResult` its run came to, kept or not |
| An operator node a fragment grouped | None. A fragment call produces no intermediate table |
| `DISCOVER` | The `DiscoveryResult` the catalog search produced |
| `REPLAN`, `RETURN` | None. The answer belongs to `RunResult.answer` |

### Reusing a result

A signature covers everything that decides what one node produces, the signatures of the work feeding
it included, so two pieces of identical work share one and any change that would change the output
changes it ([06, signing the work](06-library.md#signing-the-work)).

Keying results by signature is what settles reuse without a comparison. A new cover reuses a result
exactly when it asks for the same work again, and a fragment whose operator, instruction, sources, or
upstream changed finds nothing and runs. The same rule carries results across logical plan versions,
so a continuation that keeps a node keeps what that node produced, and one that rewrites the work
does not.

Asking for the same work again is something a cover does deliberately. A node whose fragment already
produced a result is offered that fragment as one of the candidates the model chooses among, and
choosing it carries the fragment into the new cover unchanged
([04](04-detailed-architecture.md#choosing-among-candidates)). That is what makes reuse turn on the
decision rather than on how the model happened to word an instruction the second time. The two
answer different questions and both are needed: the choice says what should happen at the node, and
the signature says whether what was chosen still stands, so a fragment kept above work that has
since changed signs differently and runs.

A node id is not the key, because it answers a different question. Two plan versions may use one id
for different nodes, and a repair that cuts a node set again may leave the same id covering a
different set. An id names a position in a graph; a signature names the work.

Scheduling reads the current cover's signatures. A fragment is ready when every node on its frontier
holds an accepted result, and a fragment's input is those results' outputs. A fragment whose own
signature holds one has run and is skipped; one whose entry says the review turned it down is
offered again, since nothing downstream can be built on a result nobody kept.

Nothing is removed. An entry a later cover has no use for stays in the map and is never read again,
because no signature in that cover matches it
([04, a cover is written whole](04-detailed-architecture.md#a-cover-is-written-whole)).

Three atomic fragments running `SCAN`, `FILTER`, and `PROJECT` leave three entries. If the next cover
fuses all three into one Text-to-SQL fragment, that fragment carries a signature none of the three
had, so it runs and records a fourth entry. The scan's rows are produced inside the SQL query and
never materialize again, and the three older entries are unreachable because nothing in the new cover
asks for them. Reading a table the current cover no longer produces is impossible rather than
forbidden.

Where a result came from is not stored beside it. `ExecutionResult.operator_id` names the operator,
and the fragment whose signature holds that result names the node set and the cover, so the two never
disagree.

### What each component returns

`LogicalPlanResult` and `PhysicalPlanResult` never leave the process. `DataPlanner` reads each one,
records what it holds on the run state, and moves on. `FragmentResult` and `DiscoveryResult` do
leave: each is what the run files under a signature.

```python
@dataclass(frozen=True)
class LogicalPlanResult:
    status: RunStatus  # ok, or invalid_plan when the corrections ran out
    plan: LogicalPlan | None
    failure_reason: str | None


@dataclass(frozen=True)
class PhysicalPlanResult:
    plan: PhysicalPlan | None  # None exactly when a node has no operator
    failure_reason: str | None


@dataclass(frozen=True)
class FragmentResult:
    status: RunStatus
    result: ExecutionResult | None  # what the last call returned, whatever the ending
    failure_reason: str | None
```

No component reports what its own model calls cost. Each writes its turns to the conversation it was
handed, that conversation carries the usage of every call made on it, and the run state owns the
conversations ([06, `llm/`](06-library.md#llm)).

The same rule covers what a component tried and discarded. A plan validation turned down, a binding
the physical planner replaced, parameters a review rejected: each is a turn in the conversation that
produced it. A result type carries the outcome, and the conversation carries the road to it.

`LogicalPlanResult.plan` is `None` exactly when `status` is `invalid_plan`, and `failure_reason` then
holds the objection the last correction drew. A plan validation turned down never enters
`DataPipeline`, since nothing covered it and no version continues it.

`FragmentResult.result` holds what the last call returned, whether the review kept it or not, so
`status` alone says whether anything may read it. It is `None` only where no call was made. The
parameter repair is a loop inside the runner: it writes the operator's parameters again, in the
conversation it already holds, until one is accepted or `parameter_repairs` runs out
([04, the parameter repair](04-detailed-architecture.md#the-parameter-repair)). One call therefore
makes several attempts, and what each of them wrote is in that conversation.

`FragmentResult.status` says which of five endings the fragment reached, and `ok` means the result
was accepted. The runner never returns `invalid_plan` or `replan_exhausted`, which belong to the
logical planner and to the replan allowance, neither of which it can see.

| Ending | `status` |
| --- | --- |
| The result was accepted | `ok` |
| The runner used its parameter repairs without an accepted result | `needs_repair` |
| The review said no arrangement of operators will do this | `needs_replan` |
| An operator failed unrecoverably | `operator_failed` |
| The operator call budget reached its cap | `budget_exhausted` |

`needs_repair` goes to the cover repair, and `needs_replan` goes straight to the replan. A
`needs_repair` the cover loop cannot answer moves outward to the replan, which is how the repairs
are ordered ([04, recovering from a
failure](04-detailed-architecture.md#5-recovering-from-a-failure)). The two remaining statuses end
the run, and each is the status the caller's `RunResult` carries.

Each component returns what it did instead of writing it down as it goes, so one that raises cannot
leave a half-written record behind, and plans and results have one writer.

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
    matches: list[EntityMatch]
    dropped_terms: int             # terms cut by the per-node cap
```

A `DISCOVER` node's entry in the run's `results` map holds one of these, where an operator node's
entry holds rows. The entries of every resolved `DISCOVER` node, taken together, are the run's
catalog context: the logical planner reads them when it writes the continuation and names the
source each scan reads, and the physical planner reads each named source's kind to choose among
operators.

`dropped_terms` is what a run holds for a reader rather than for itself. Nothing branches on it, and
a search runs the terms it kept whatever the number is. It reaches a library caller inside
`RunResult.results`, where it is the one sign that a node wanted more searches than
`CatalogSearcher` allows it.

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

One record per model call, held by the conversation the call was made on, in call order. There is no
accumulator: a total is a `sum()` over the list. Prompt and completion tokens are reported per call
and never summed together, since the two are priced differently and a total of them answers nothing.

The conversations fix the scope. These are the calls the three planning components and the catalog
searcher made through `llm/`, and nothing else. A model call an operator makes internally, as a LOTUS
operator does, is billed as part of the one operator call that contains it and never appears here,
because such an operator reports no per-call token count
([05-software-stack.md](05-software-stack.md#two-model-configurations-one-budget)). What a run spent
on planning is exact; what it spent inside operators is the call count in `counters`.

### Conversations

```python
@dataclass
class RunConversations:
    logical_planner: Conversation
    physical_planner: dict[str, Conversation]    # keyed by logical plan id
    fragment_runner: dict[str, Conversation]     # keyed by fragment id
    catalog_searcher: dict[str, Conversation]    # keyed by the DISCOVER node's signature
```

One exchange per component, cut at the scope over which continuing one helps. The logical planner
keeps one for a whole request, because every continuation answers the plans already written. The
physical planner keeps one per logical plan, because a repair rebinds work the same plan described
and a new plan's node ids name a different graph. The fragment runner keeps one per fragment, because
the parameters it writes are shaped by that fragment's operator. A catalog search is a single
exchange with nothing to continue, and its key is the signature its `DiscoveryResult` sits under in
`results`, so the search and what it found are read with the same value.

Cutting a conversation anywhere else would either put a model's earlier answers in front of a
question they no longer describe, or throw away the errors it has already been told about.

### Counters

```python
@dataclass
class RunCounters:
    limits: RunLimits
    operator_calls: int
    failure_replans: int
    control_replans: int
```

Three counts, one per bound that lasts a whole request, each named after the bound it counts against.
`RunCounters` carries the `RunLimits` it was counted against, so a reader has the spend and the cap
side by side without knowing how the planner was built.

The other three bounds reset inside a request: `plan_corrections` and `parameter_repairs` bound a
loop inside a component, and `cover_repairs` bounds one inside the run loop
([04, recovering from a failure](04-detailed-architecture.md#5-recovering-from-a-failure)). A bound
that resets has no one number to report.

```json
{
  "operator_calls": 7,
  "failure_replans": 0,
  "control_replans": 1
}
```

How many registry searches a run made is not a counter. Every term is in the `search_terms` of the
`DiscoveryResult` each `DISCOVER` node produced, and counting them is a reader's question rather than
a bound the run obeyed.

The bounds themselves are in [12-deployment-ops.md](12-deployment-ops.md).

### Stored values

| Enum | Values |
| --- | --- |
| `LogicalOperator` | `SCAN`, `FILTER`, `PROJECT`, `EXPAND`, `AGGREGATE`, `DISTINCT`, `SORT_SUPERLATIVE`, `JOIN`, `UNION`, `RETURN` |
| `ControlOperator` | `REPLAN`, `DISCOVER` |
| `RunStatus` | `ok`, `needs_repair`, `needs_replan`, `planned`, `invalid_plan`, `replan_exhausted`, `budget_exhausted`, `operator_failed`, `llm_failed`, `planner_failed` |
| `ExecutionStatus` | `ok`, `failed` |
| `ErrorKind` | `recoverable`, `unrecoverable` |
| `FragmentReviewDecision` | `accept`, `reject`, `replan` |

`SORT_SUPERLATIVE` is one logical operator covering both ordering and cutting, since a superlative
is an ordering with a limit.

---

## 4. Catalog readings

Three readings of the same registry serve planning, and none of them is stored. `DataCatalog`
selects and renders what `blue-data` has collected and enriched, and never profiles an attribute or
samples a table of its own.

The shapes below live in `types/` with the rest of the shared data classes, and `data/catalog.py`
holds the code that fills them. `EntityMatch` is why: it reaches a caller inside
`RunResult.results`, so `types/` has to name it, and splitting one reading from the other two on
that basis would draw a line no reader could see
([06-library.md](06-library.md#shared-types)).

| Reading | Read by | Answers |
| --- | --- | --- |
| `CatalogPreview` | The logical planner, once per request, until the first cover installs | Which sources exist and what each is for |
| `EntityMatch` | The logical planner and the physical planner, as the run's catalog context | Which entities exist and what each holds |
| `AttributeView` | The fragment runner, for the entities its own fragment scans | How an attribute's values are actually spelled |

The last two differ in shape rather than in degree. An `EntityMatch` is broad and shallow — one
entity and every attribute's name, type, and description — so a run can carry it in every prompt. An
`AttributeView` is narrow and deep, and its size is set by how long the stored sample values are, so
it is read for one fragment's scans and goes no further.

Catalog detail below the source level enters a run one way, through a `DISCOVER` node
([04, resolving a discovery node](04-detailed-architecture.md#resolving-a-discovery-node)). What
those nodes found is the run's catalog context, and an `AttributeView` deepens one entity the
context already names.

### Naming a record

`blue-data` keys every catalog record by a path encoding its full ancestry, from source down to
attribute. An `AttributeRef` is that path in parts.

```python
@dataclass(frozen=True)
class AttributeRef:
    source: str
    database: str
    collection: str
    entity: str
    attribute: str
```

The path form of the same reference is
`/source/jobs/database/jobs/collection/public/entity/postings/attribute/location`, which is what the
registry stores, what a fragment's `scan_sources` holds a prefix of, and what a lookup uses. An
entity is the unit a `SCAN` is bound to, whatever kind of source holds it: `blue-data` records the
same five levels for a SQL database, a document store, and a graph.

A catalog record's column is an attribute, following `blue-data`. A table moving between fragments
keeps the word column, since a fragment passes tables of rows whatever kind of source produced them
([08-coding-style.md](08-coding-style.md#naming)).

### What the logical planner reads first

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

The preview tells the logical planner what the registered data could offer, which is worth knowing
even for a request answered from the model's own world knowledge. It names no entity and no
attribute, so a plan that scans registered data is written against what a `DISCOVER` node found
rather than against the preview
([04, logical planning](04-detailed-architecture.md#1-logical-planning)).

A `description` of `None` is what says nobody wrote one. A registry stores an empty string where
enrichment never ran, and an empty field should not travel into a prompt as though it were a
description, so every reading below renders one as absent.

### What a search returns

A search hits an entity, and the match carries that entity together with every attribute it holds.

```python
@dataclass(frozen=True)
class AttributeRecord:
    name: str
    dtype: str | None
    description: str | None


@dataclass(frozen=True)
class EntityMatch:
    path: str                          # the entity's full catalog path
    name: str
    description: str | None
    source_kind: str                   # "sql/sqlite", read from the source record above it
    attributes: list[AttributeRecord]
    score: float
    search_term: str                   # which term found it
```

The full attribute list is what makes a declared column name a fact rather than a guess. A logical
planner that has seen `customers -> CustomerID, Currency, Segment` writes `required_output_columns`
from those names, and the check `execute_physical_operator` runs against them has something real to
pass ([declared output columns](#declared-output-columns)).

`source_kind` rides on the match because it is what decides which operators may run a fragment
containing a `SCAN` over this entity. It is recorded on the source record rather than on the entity
record, and the search reads it from there.

`search_records` takes one query string and returns the entities that match it, ranked. A discovery
node that expands into several terms calls it once per term. `score` is the registry's own relevance
score, where a higher number is a better match, and the list is ordered by it. The list is capped
rather than filtered, so a weak match appears at the bottom rather than not at all (ADR-021).

An entity is found by its own name and description. Reaching one through an attribute's name or
through a value stored in it is not something the search offers, so a request whose vocabulary
matches only an attribute needs a term the entity itself answers to.

### What the fragment runner reads

The runner reads its fragment's scans from the catalog and its inputs from the tables themselves.

| Source of names | Carries | For |
| --- | --- | --- |
| An `AttributeView` per attribute of each entity in `scan_sources` | Name, type, description, sample values, counts, range, inferred meaning | An attribute the fragment is about to scan |
| The input tables | Column names, their values' type, the row count, and a sample of rows | A table an earlier fragment produced |

A fragment whose frontier is empty has no input tables, which is the ordinary shape for pushdown, so
the catalog is the only thing that can name its columns. A fragment reading an input table needs no
catalog call for it: the rows in hand are better evidence than a record describing the attribute as
it was scanned, and whole-column counts taken before a filter ran would describe a table that no
longer exists. Input columns are therefore read from the table and annotated from nowhere.

The views cover every attribute of the entities the fragment scans, rather than a chosen few. Which
attributes it will name is the decision the runner has yet to take when it reads them. They cover
its own scans rather than the run's whole catalog context, which would put entities the fragment
cannot reach in front of the model.

```python
@dataclass(frozen=True)
class AttributeView:
    ref: AttributeRef
    dtype: str | None
    description: str | None
    sample_values: list[Any]
    distinct_count: int | None
    null_count: int | None
    min: Any | None
    max: Any | None
    semantic_type: str | None
```

`sample_values` is what a filter's values are written from. In `debit_card_specializing`,
`customers.Segment` holds `SME`, `LAM`, and `KAM`, and a runner writing a filter for "small and
medium enterprises" without having seen those three produces a query returning zero rows and no
error. `semantic_type` says what the registry inferred the values mean, and it is read alongside the
samples rather than instead of them.

The view carries the first three of the attribute's stored sample values and caps nothing else. That
one cap is where a wide entity's cost is controlled: annotating all 115 attributes of a match-events
table in full runs to hundreds of kilobytes, and almost all of it is a handful of attributes holding
long text rather than the attribute count.

An attribute whose statistics were never collected still gets a view, carrying its type and whatever
else the record holds. A type is absent the way a description is: a source with dynamic typing
registers a column the registry records no type for, and both readings render that as nothing rather
than refusing the record. Absence and corruption are told apart: no record at all is an attribute
with no view, and a record that exists but cannot be read as one — no type field where a type field
belongs, or statistics that are not statistics — is a corrupt registry and raises
([06-library.md](06-library.md#errors)).

---

## 5. Shapes that leave the process

The library's shapes are the reference. A library caller holds them in memory; the CLI and the MCP
server write documents, and a document carries a measure of whatever grows with the data a run read.

| Shape | Library | The JSON form |
| --- | --- | --- |
| `answer`, `status`, `failure_reason` | The rows, the status, the reason | The same |
| `counters` | The counts and the bounds they were counted against | The same |
| `conversations` | Every turn of every exchange | A count of calls and what they cost |
| `results`, apart from a call's rows | How each fragment ended, with its reason | The same |
| `FragmentResult.result.output` | The rows the call returned | One `TableSummary` per table, carrying the first five rows |
| A `DISCOVER` node's entry in `results` | Every term and every match | The same |
| `pipeline` | Every plan the request produced | The same |

`RunResult.to_dict` writes that form, and every interface sends it, so one serialization answers for
all of them. Two shapes are reduced because both grow with the work a request did rather than with
its answer: a transcript and a run's intermediate tables. Each becomes a measure, which keeps a
document the size of what a reader can act on (ADR-032). `pipeline` travels only where it was asked
for: the CLI sends it under `--explain` or `--log`
([09](09-api-interface-spec.md#output)), and the MCP server sends it under `explain` or
`include_plan` ([09](09-api-interface-spec.md#the-response)).

A run that did not finish comes back as the same object in every interface, with its status,
`answer` null, and its counters.

---

## 6. Benchmark inputs and runs

TAG-Bench inputs live under `data/tag_bench/`, downloaded rather than committed
([07-repo-organization.md](07-repo-organization.md#where-the-benchmark-lives)).
The released files contain errors, so the build step reads a release and writes corrected queries
beside it, leaving the download untouched.

| Path | Holds | Written by |
| --- | --- | --- |
| `data/tag_bench/queries.json` | The corrected queries the harness reads | `lib/scripts/tag_bench/fetch_queries.py` |
| `data/tag_bench/databases/*.db` | The five BIRD SQLite databases | `lib/scripts/tag_bench/fetch_databases.py` |
| `data/tag_bench/registry.json` | A `blue-data` registry over those databases, synced, with stats collected and enriched | `lib/scripts/tag_bench/register_databases.py` |

The registry is built once and kept, so a test and a benchmark run open a file instead of re-syncing
five databases.

One query record carries the released fields, the split it belongs to, and what a correction needs
to be traceable. It is the whole shape the harness reads, so nothing about a query lives in a second
file.

```json
{
  "query_id": 42,
  "question": "How many schools have the difference in enrollements between K-12 and ages 5-17 as more than the number of days in April?",
  "query_type": "Comparison",
  "db_name": "california_schools",
  "gold_answer": 1239,
  "split": "dev",
  "correction": "the released answer of 1236 disagrees with the reference program, which returns 1239"
}
```

`correction` is absent on a query the build step passed through unchanged, which is what makes the
corrected set auditable against the release. `split` is `dev` on the queries a debugging pass uses
and `test` on the rest, assigned once per query type when the queries are built.

A query whose `gold_answer` is empty states no answer to score. TAG-Bench's twenty aggregation
queries each ask for a summary and carry one, so a benchmark selection leaves them out
([11-testing-strategy.md](11-testing-strategy.md#8-the-benchmark)).

### What a run writes

A run writes under `data/tag_bench/runs/<run-name>/`, git-ignored like the inputs beside it.

| Path | Holds |
| --- | --- |
| `config.json` | Every setting the run resolved, the query ids it selected, and the commit it ran |
| `query_<id>.json` | The query, its gold answer, the answer extracted from the run, whether the two match, and the `RunResult` behind it: status, failure reason, every plan version, each fragment's result, the counters, and what the query spent |
| `query_<id>.conversations.json` | That query's model transcripts, apart from the record because triage starts with the record |
| `summary.json` | Exact match overall and per query type, and the run-level measurements ([11-testing-strategy.md](11-testing-strategy.md#8-the-benchmark)) |

A record is written as its query finishes, so a run that stops keeps what it finished. A query that
raised carries the traceback in place of a result, which is how one defect leaves the rest of a run
scorable. A run writes into a directory of its own: one already holding records is refused rather
than added to, so no summary mixes two runs.

---

## What is deliberately absent

| Not here | Why |
| --- | --- |
| A `schema_version` field in a serialized plan | Nothing persists a plan. The shape is public interface, covered by semver |
| A stored session, plan, or run | A `RunResult` is the record, and it is returned to the caller |
| A model response cache | Every model call reaches the provider |
| Operator or router state between requests | Both are assembled once and held in process |
| A separate error response shape | A run that did not finish returns a `RunResult` with a status (ADR-015) |
| A row type of its own | A fragment passes plain dicts, so no shape belongs to a suite (ADR-012) |
| Cost and cardinality estimates on a node or a fragment | Out of the first release |
| A per-node record in `RunResult` | A node's outcome is derivable from the attempt that covered it, and two places holding it would drift |
