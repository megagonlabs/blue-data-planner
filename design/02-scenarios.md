# blue-data-planner — Scenarios

The scenarios below cover data planning, and all of them share one setting: a hiring analyst whose
organization has a few dozen sources registered in `blue-data`. Two of them matter here.

| Source | Kind | Holds | Its description in the catalog |
| --- | --- | --- | --- |
| `jobs` | SQLite database | A `postings` table with title, company id, location, salary, and description | "Job postings collected from partner boards" |
| `research_library` | Document source | Several document collections, one of which holds a profile per company | "Internal research documents" |

The analyst's questions name neither source. Working out which one answers which part of a question
is the planner's job, and the two are not equally easy to find: the catalog preview a planner reads
lists sources and their descriptions, so `jobs` is visible from the request text alone, while
nothing at the source level says `research_library` holds company profiles. Scenario 1 shows what
the planner does about that.

> Everything shown below is for illustration. Commands, class and method names, JSON field names,
> operator ids, and printed output are written to show the shape of each interaction, and none of
> them define the implementation. The real names and structures are settled in
> [06-library.md](06-library.md), [09-api-interface-spec.md](09-api-interface-spec.md), and
> [10-data-model.md](10-data-model.md).

---

## Scenario 1: Asking a Question from the Command Line

An analyst has a question about data that is already registered, and does not want to write code.
The CLI takes the question in plain language, plans it, runs the plan, and prints the result.

The analyst does not say which source holds what, and does not work out which parts of the question
a database can answer and which parts need a language model. In the question below, location is a
column a database can compare directly, while "data scientist role" and "healthcare company" are
judgments no column holds.

```bash
blue-data-planner ask "Which Bay Area data scientist postings are at healthcare companies?"
```

### The first plan: a question about the catalog

The logical planner reads the request and the catalog preview. The preview shows that a job postings
source exists. It shows nothing that holds company profiles, and whether one exists changes the
shape of everything after the postings: with profiles, the healthcare judgment is a join and then a
filter over profile text; without them, it is a filter over the posting's own description, and the
postings scan has to carry that description as a column. Rather than guess between two different
plans, the planner writes a `DISCOVER` node and ends there.

```json
{
  "id": "3f2b9c14",
  "parent_id": null,
  "output_node_id": null,
  "nodes": [
    {"id": "n1", "kind": "DISCOVER", "input_node_ids": [],
     "attributes": {"instruction": "a source holding company profiles or industry information, one record per company"},
     "required_output_columns": []}
  ]
}
```

`output_node_id` is `null` because the plan has no `RETURN` node yet, which is what a plan ending on
a control node looks like. `n1` is bound to no operator and spends no operator call. Physical
planning has nothing to cover, so the pass goes straight to the node.

`CatalogSearcher` turns the instruction into search terms and runs one registry search per term. The
search reaches records below the source level, which is where the preview stops, and that is what
finds the collection the source description never mentioned.

```text
n1 DISCOVER  2 terms, 4 matches
   /source/research_library/database/docs/collection/profiles/entity/company_profiles
   /source/research_library/database/docs/collection/profiles/entity/market_reports
   ...
```

### The continuation: a complete plan

Control returns to the logical planner, which now sees the request, the preview, the node it already
resolved, and the records the search found. It writes the rest as one plan, from the scans through
to the `RETURN`.

Writing it complete is what makes the next stage possible. A plan that stopped at its scans and left
filtering for later would give physical planning nothing to group, and the question of how much one
operator should take on would never come up.

```json
{
  "id": "8c4a71d0",
  "parent_id": "3f2b9c14",
  "output_node_id": "n8",
  "nodes": [
    {"id": "n1", "kind": "DISCOVER", "input_node_ids": [],
     "attributes": {"instruction": "a source holding company profiles or industry information, one record per company"},
     "required_output_columns": []},

    {"id": "n2", "kind": "SCAN", "input_node_ids": [],
     "attributes": {"instruction": "load job postings"},
     "required_output_columns": [{"name": "title", "dtype": "str"},
                                 {"name": "company_id", "dtype": "int"},
                                 {"name": "location", "dtype": "str"}]},

    {"id": "n3", "kind": "FILTER", "input_node_ids": ["n2"],
     "attributes": {"instruction": "find the postings whose location is in the San Francisco Bay Area"},
     "required_output_columns": [{"name": "title", "dtype": "str"},
                                 {"name": "company_id", "dtype": "int"},
                                 {"name": "location", "dtype": "str"}]},

    {"id": "n4", "kind": "FILTER", "input_node_ids": ["n3"],
     "attributes": {"instruction": "find the postings whose title is a data scientist role"},
     "required_output_columns": [{"name": "title", "dtype": "str"},
                                 {"name": "company_id", "dtype": "int"},
                                 {"name": "location", "dtype": "str"}]},

    {"id": "n5", "kind": "SCAN", "input_node_ids": [],
     "attributes": {"instruction": "load company profile documents"},
     "required_output_columns": [{"name": "company_id", "dtype": "int"},
                                 {"name": "company", "dtype": "str"},
                                 {"name": "profile", "dtype": "str"}]},

    {"id": "n6", "kind": "JOIN", "input_node_ids": ["n4", "n5"],
     "attributes": {"instruction": "match each posting to the profile of the company that posted it"},
     "required_output_columns": [{"name": "title", "dtype": "str"},
                                 {"name": "location", "dtype": "str"},
                                 {"name": "company", "dtype": "str"},
                                 {"name": "profile", "dtype": "str"}]},

    {"id": "n7", "kind": "FILTER", "input_node_ids": ["n6"],
     "attributes": {"instruction": "find the postings whose company works in healthcare"},
     "required_output_columns": [{"name": "title", "dtype": "str"},
                                 {"name": "location", "dtype": "str"},
                                 {"name": "company", "dtype": "str"}]},

    {"id": "n8", "kind": "RETURN", "input_node_ids": ["n7"],
     "attributes": {"columns": ["title", "company", "location"]},
     "required_output_columns": []}
  ]
}
```

This is a new version of the logical plan rather than an edit of the first one. It names its parent,
and it carries over `n1` because `n1` produced a result. Nothing else carried over, since nothing
else had run. The first version stays readable exactly as it was written.

### Deciding how much to push down

Physical planning covers the operator nodes with fragments and binds each fragment to one operator.
Three of the four choices here are forced, and one is not.

| Nodes | Operator | Why |
| --- | --- | --- |
| `n5` | `tablesuite.document_fetch` | The profiles are documents. No operator in this registry reads a document source and does anything else in the same call |
| `n6` | `tablesuite.join` | It joins tables from two different sources, which no single source can do |
| `n7` | `textsuite.semantic_filter` | Reading a profile for whether the company works in healthcare is a judgment, and only a semantic operator declares `FILTER` over text |
| `n2`, `n3`, `n4` | `tablesuite.nl2sql` | All three are kinds `nl2sql` declares, and they scan a SQLite source, so any group of them is a candidate |

The last row is the decision physical planning exists to make. `nl2sql` may take the scan alone, the scan
and the location filter, or all three, and each is a different trade: one SQL query that filters 312
rows down to 48 inside the database, against three calls whose middle step can be checked on its
own. The planner takes all three.

```json
{
  "id": "d19f5b3c",
  "parent_id": null,
  "logical_plan_id": "8c4a71d0",
  "fragments": [
    {
      "id": "f1",
      "logical_node_ids": ["n2", "n3", "n4"],
      "root_logical_node_id": "n4",
      "operator_id": "tablesuite.nl2sql",
      "scan_sources": {"n2": "/source/jobs/database/jobs/collection/public/entity/postings"},
      "instruction": "Find Bay Area data scientist job postings, with their title, company id, and location",
      "input_data": [],
      "required_output_columns": [{"name": "title", "dtype": "str"},
                                  {"name": "company_id", "dtype": "int"},
                                  {"name": "location", "dtype": "str"}]
    },
    {"id": "f2", "logical_node_ids": ["n5"], "operator_id": "tablesuite.document_fetch", "...": {}},
    {"id": "f3", "logical_node_ids": ["n6"], "operator_id": "tablesuite.join", "...": {}},
    {"id": "f4", "logical_node_ids": ["n7"], "operator_id": "textsuite.semantic_filter", "...": {}}
  ]
}
```

`n8` appears in no fragment. Projecting named columns admits no implementation choice, so `RETURN`
is bound to nothing.

### Running it, and taking one boundary back

The first cover is wrong, and the run finds out by running it. `nl2sql` writes a query that filters
on location and quietly ignores the rest, because "is a data scientist role" is not a predicate SQL
can express. The result has the right number of rows to look plausible.

What catches it is the instruction of the node that was absorbed. A fused call returns one table and
no evidence of the steps inside it, so the review checks the result against each absorbed
instruction, and `n4` is the reason there is anything to check. Titles such as "Data Engineer" and
"Research Scientist" do not answer it.

The parameter repair goes first, because it is the cheapest and the most often right: the same
operator over the same nodes, with the instruction written again. Here it does not help, since no
wording makes SQL read a title for meaning. The run then moves to the fragment repair, which covers
`n2`, `n3`, and `n4` again and cuts them differently.

```text
cover 1
  f1  n2,n3,n4  tablesuite.nl2sql          rejected: titles that are not data scientist roles
  f2  n5        tablesuite.document_fetch  ok (140 rows)
  f1  n2,n3,n4  tablesuite.nl2sql          rejected: the same, with the predicate written into the SQL text
cover 2
  f1  n2,n3     tablesuite.nl2sql          ok (48 rows)
  f5  n4        textsuite.semantic_filter  ok (11 rows)
  f2  n5        tablesuite.document_fetch  kept from cover 1 (140 rows)
  f3  n6        tablesuite.join            ok (11 rows)
  f4  n7        textsuite.semantic_filter  ok (2 rows)

title                     company        location
Senior Data Scientist     Corvus Health  San Francisco, CA
Data Scientist, Clinical  Meridian Bio   Palo Alto, CA
```

The second cover is written for the whole plan, not patched into the first, and installing it
compares the two fragment by fragment. `n5` is in a fragment whose node set did not change, so its
140 rows are kept and `tablesuite.document_fetch` does not run again. The group that changed drops
what it had, and runs.

The scan of `postings` is paid for twice, which is what the wider fragment cost when it failed. That
is the trade physical planning weighs, and both attempts are in the record, so where the boundary was drawn
and what happened to it can be read afterwards without running anything again.

### Reading a plan without running it

The analyst can stop after planning, which spends no operator calls and is worth doing before an
expensive question over a large source.

```bash
blue-data-planner ask --explain "Which Bay Area data scientist postings are at healthcare companies?"
```

For this question, `--explain` can only show the first plan, up to the `DISCOVER` node. Everything
after it depends on what the search returns, and the search has not run. For a question over sources
the preview already shows, `--explain` prints the complete plan and the cover over it, which is the
pushdown boundary before anything is spent on it.

---

## Scenario 2: Answering a Data Question Inside an Agent

An agent built with a framework such as LangChain or LangGraph handles a conversation with a user
and needs data work done partway through it. The MCP server gives the agent one tool for that.

The split of work is deliberate. The agent runs the conversation, remembers what was said earlier,
and decides when it needs data. The planner takes one self-contained request and answers it. Agents
are out of scope for this repository, so the tool has to be usable by an agent somebody else wrote.

The agent framework is configured with the MCP server:

```json
{
  "mcpServers": {
    "blue-data-planner": {
      "command": "blue-data-planner-mcp",
      "env": {
        "BLUE_DATA_REGISTRY_STORE": "redis",
        "BLUE_DATA_REGISTRY_REDIS_URL": "redis://localhost:6379/0"
      }
    }
  }
}
```

A user asks the agent to help with a job search. Several turns in, the agent decides it needs
postings and calls the tool with a request it composed from the conversation:

```text
plan_and_execute(task="Bay Area data scientist postings at healthcare companies")
```

It gets back structured JSON: the rows, every plan the request produced, and what each node
produced.

```json
{
  "status": "ok",
  "answer": [
    {"title": "Senior Data Scientist", "company": "Corvus Health", "location": "San Francisco, CA"},
    {"title": "Data Scientist, Clinical", "company": "Meridian Bio", "location": "Palo Alto, CA"}
  ],
  "pipeline": {
    "current_logical_plan_id": "8c4a71d0",
    "current_physical_plan_id": "a6e02f77",
    "logical_plans": {"3f2b9c14": {"...": {}}, "8c4a71d0": {"...": {}}},
    "physical_plans": {"d19f5b3c": {"...": {}}, "a6e02f77": {"...": {}}}
  },
  "results": {
    "8c4a71d0": {
      "n1": {"matches": 4},
      "n3": {"status": "ok", "row_count": 48},
      "...": {}
    }
  },
  "attempts": 7,
  "rejected_plans": 0,
  "counters": {"operator_calls": 7, "plan_corrections": 0, "parameter_attempts": 2,
               "fragment_attempts": 1, "cover_attempts": 0, "failure_replans": 0,
               "control_replans": 1, "search_terms": 2},
  "planner_usage": {"calls": 11, "cost": 0.42}
}
```

The MCP response collapses what an agent reasons about into counts. `attempts` is a number rather
than seven full records, and the discovery node reports how many records matched rather than listing
them, because the full record would bloat every response. The library and the CLI carry both in
full.

The agent does not have to know that the run discovered a source, ended a pass, and took a pushdown
boundary back, since the tool call still reads as one request and one answer. The record is there if
it ever needs to explain where an answer came from. The planner does not know an agent called it,
and it holds nothing from one call to the next.

---

## Scenario 3: Embedding the Planner in an Application

A developer is building a data application in Python and wants planning inside it rather than behind
a command or a server. Alongside the answer, the application needs the record of how that answer was
produced, so it can show its work, store a plan that worked, and investigate one that did not.

Every part of a run is kept as data. A run that returned the wrong rows can be examined afterward
without reproducing it, and a developer looking at a bad result can find the decision that went
wrong instead of guessing at why a model answered the way it did.

```python
from blue_data_planner import build_data_planner

planner = build_data_planner(router=router, catalog=catalog, llm_client=client, limits=limits)
result = planner.run("Which Bay Area data scientist postings are at healthcare companies?")

result.answer                       # the final rows
result.pipeline.logical_plans       # both versions: the one ending at DISCOVER, and the complete one
result.pipeline.physical_plans      # both covers: the one that fused three nodes, and its replacement
result.results["8c4a71d0"]["n1"]    # the search terms discovery used and the records it found
result.attempts                     # every fragment run, in run order, rejected ones included
```

Three questions have three different answers, and each is in a different place.

| Question | Read |
| --- | --- |
| What did the request ask for | `pipeline.logical_plans`, the version `current_logical_plan_id` names |
| How much was pushed into one call | The `logical_node_ids` of each fragment, in every cover in `pipeline.physical_plans` |
| What actually ran, and what was rejected | `attempts`, each with its node set, its operator, its parameters, and the review's assessment |

A developer investigating a run that returned postings for data engineers reads `attempts` and finds
the first two: one fragment covering `n2`, `n3`, and `n4`, run by `tablesuite.nl2sql`, with an
assessment naming titles that do not answer `n4`. The plan was right and the cut was wrong, which
points at the physical planner's prompt rather than at the logical planner.

The planner takes a request and nothing else. A plan is what a request produced, not something a
caller hands in, so there is no way to store one and run it later: asking the same question again
plans it again, against whatever the catalog and the registered operators look like then. What a
caller stores is the record.

```python
record = result.pipeline.to_dict()   # structured data, safe to write to a file or a log
```

An application that wants to see the plan before anything is spent stops after planning, which is
what `--explain` does at the command line.

```python
preview = planner.plan("Which Bay Area data scientist postings are at healthcare companies?")
preview.pipeline.physical_plans   # the cover, with no operator call spent on it yet
```

---

## Scenario 4: Adding an Operator Suite

A developer wants the planner to use an outside operator suite, LOTUS in this case. LOTUS stays an
outside dependency, and its source is never copied into this repository. The developer writes an
adapter that declares each LOTUS operation as a physical operator: what it can carry out, what
parameters it takes, and how to call it.

What a declaration says decides more than which nodes an operator competes for. It decides which
groups of nodes it competes for, because candidate fragments are built from it.

```python
PhysicalOperator(
    operator_id="lotus.sem_filter",
    logical_operators=frozenset({LogicalOperator.FILTER}),
    description="Keep the rows a language model judges to satisfy a predicate over named columns.",
    parameter_schema={"instruction": {"type": "string"}},
    call=_sem_filter,
)

PhysicalOperator(
    operator_id="blue.nl2sql",
    logical_operators=frozenset({
        LogicalOperator.SCAN, LogicalOperator.FILTER, LogicalOperator.PROJECT,
        LogicalOperator.JOIN, LogicalOperator.AGGREGATE, LogicalOperator.DISTINCT,
        LogicalOperator.SORT_SUPERLATIVE,
    }),
    description="Write and run one SQL query against a relational source.",
    parameter_schema={"question": {"type": "string"}, "source": {"type": "string"}},
    call=_nl2sql,
    source_kinds=frozenset({"sql/sqlite", "sql/mysql", "sql/postgres"}),
    limitations=(
        "Cannot express a semantic predicate, and cannot bind tables from other fragments "
        "into the query it writes."
    ),
)
```

The two sit at opposite ends of what a declaration can say.

| | `lotus.sem_filter` | `blue.nl2sql` |
| --- | --- | --- |
| Logical operators | One | Seven |
| Largest group it can cover | One `FILTER` node | Any connected group of those seven kinds |
| Sources | None. It reads the rows it is handed | SQLite, MySQL, and Postgres, and every `SCAN` in a group has to be one of those |
| Tables from other fragments | Its input is one table | None. It writes its query from its parameters and the schema of the source it reads, so it is a candidate only for a fragment with an empty frontier |
| Stated limitations | None | Cannot express a semantic predicate, the limit that kept `n4` out of the SQL fragment in Scenario 1, and cannot bind tables from other fragments into the query it writes |

An operator declaring one logical operator can only ever cover one node. Declaring several is what
makes a group possible, and `limitations` is what keeps the group from growing past what the
operator can do. Without it, a description promising SQL over anything is all the planner has to go
on.

Registering is the whole integration. Nobody edits planner code and nobody edits a prompt by hand,
because both planning components build their choices from the router while they plan.

```python
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.blue import blue_operators
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.operators.lotus import lotus_operators
from blue_data_planner.types import LogicalOperator

router = OperatorRouter()
# operator_registry is a blue-data-ops OperatorRegistry, built the way that package documents
for operator in blue_operators(operator_registry, data_registry):
    router.register(operator)
for operator in lotus_operators():  # empty when LOTUS is not installed
    router.register(operator)
for operator in builtin_operators(data_registry, llm_client):
    router.register(operator)

[op.operator_id for op in router.operators_for(LogicalOperator.FILTER)]
# ["blue.select", "blue.semantic_filter", "blue.nl2sql", "lotus.sem_filter",
#  "builtin.filter", "builtin.scan", "builtin.nl2table"]
```

Four of those seven filter and nothing else. The other three answer for `FILTER` because it is one
of several kinds each declares, so a filter node can be run on its own or absorbed into a wider
call. That is why the physical planner had a choice to make at `n2`, `n3`, and `n4` in Scenario 1,
and why it had somewhere to go when the choice turned out wrong.
