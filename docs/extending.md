# Extending blue-data-planner

The four extension points. Signatures for everything named here are in
[api-reference.md](api-reference.md).

## Adding an operator suite

A suite is a named set of physical operators, and how a new operator library reaches the planner.
It satisfies one protocol:

```python
from typing import ClassVar

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.types import PhysicalOperator


class MySuite:
    name: ClassVar[str] = "mine"

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]:
        return my_operators(data_registry)
```

`build_data_planner` calls `operators` once, handing it the registry the planner reads, the model
client every component shares, and the model string. Returning an empty list registers nothing
rather than failing the build, which is how the LOTUS adapter reports that LOTUS is not
installed.

The shipped suites are under `lib/src/blue_data_planner/operators/`: `builtin/` for the suite
this repository owns, `adapters/blue/` and `adapters/lotus/` for the two outside ones. A new
adapter goes beside them and is the only place importing its dependency, inside a callable rather
than at module import time.

### What a `PhysicalOperator` declares

Planning binds against the declaration and never looks inside the operation, so a declaration
that overstates what the operation does produces a candidate the operator cannot serve.

| Field | Type | Holds |
| --- | --- | --- |
| `operator_id` | `str` | Suite-qualified and unique across every registered suite, such as `mine.rank` |
| `logical_operators` | `frozenset[LogicalOperator]` | Every kind this operation can carry out. A single member covers one node; a larger set is what makes pushdown possible |
| `description` | `str` | What the operation does, in the words the physical planner reads while choosing between candidates |
| `parameter_schema` | `type[BaseModel]` | A Pydantic model. The fragment runner fills it as a response schema, and `execute_physical_operator` validates against it |
| `call` | `Callable[[DataBundle, dict[str, Any], list[OutputColumn]], OperatorOutput]` | The operation: the input tables, the validated parameters, and the columns the result must carry |
| `source_kinds` | `frozenset[str] \| None` | The connection protocols this operation can read, in `blue-data`'s vocabulary, such as `sql/sqlite`. `None` means it reads no registered source |
| `accepts_bound_inputs` | `bool` | Whether a scan may be narrowed by the values a reference input carries. Meaningful only where `source_kinds` is set |
| `limitations` | `str \| None` | What the operation cannot do, which keeps it from being bound to work it would fail. Write it as a phrase that completes "Cannot: …", such as `judge what text means`, and put what it can do in `description` |

A `call` raises `RecoverableOperatorError` where a different operator might accept the same input
and `UnrecoverableOperatorError` otherwise; what each becomes is in
[api-reference.md](api-reference.md#exceptions).

### How the router picks an operator

`build_data_planner` registers every operator each suite returns with one `OperatorRouter`, which
indexes it under each kind in its `logical_operators`. The physical planner then works one node at
a time:

1. `operators_for(kind)` returns every operator declaring that node's kind, in registration order.
2. For each, `candidate_fragments` enumerates the node sets rooted at that node which the
   operator's declared kinds could absorb.
3. A candidate is dropped unless the operator reads the source kind of every scan in it. An
   operator declaring no `source_kinds` takes only scans that name no source.
4. What survives is offered to the model, largest fragment first, and the model binds one.

Two operators covering the same node set are offered in registration order, and the declaration
decides whether an operator appears at all. A node no registered operator can cover goes back to
the logical planner rather than failing the run.

## Registering operators without a suite

`OperatorRouter` takes operators directly, for a caller assembling the components itself rather
than through `build_data_planner`. `register` raises `OperatorRoutingError` on an operator id
something else already registered.

```python
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.builtin import builtin_operators

router = OperatorRouter()
for operator in builtin_operators(data_registry, llm_client):
    router.register(operator)
router.register(my_operator)
```

## Swapping the model provider

Every provider call goes through `LLMClient`, which reaches the provider through litellm. Naming
a different provider means a different model string: `"anthropic/claude-sonnet-4-5"` in place of
`"openai/gpt-5.6-luna"`, with credentials in the environment litellm reads.

Reaching a provider litellm does not cover means substituting the client. The four components call
one method on it:

```python
def complete_structured(self, conversation: Conversation, response_model: type[M]) -> M: ...
```

An implementation answers the conversation with a value of `response_model`, appends that answer
to `conversation.turns` as an assistant turn, and appends one `LLMUsage` to `conversation.usage`.
Each answer is appended, so the conversation holds the whole exchange, and the usage is what
`RunResult.conversations` reports as the run's cost.

`build_data_planner` builds the client itself, so a substitute goes to the four component
constructors instead. Each takes its own, which is how one stage runs on a larger model.

```python
catalog = DataCatalog(data_registry)
planner = DataPlanner(
    logical_planner=LogicalPlanner(big_client, catalog),
    physical_planner=PhysicalPlanner(my_client, router),
    fragment_runner=FragmentRunner(my_client, catalog, router),
    catalog_searcher=CatalogSearcher(my_client, catalog),
    limits=RunLimits(),
)
```

## Reaching a new data source

Sources belong to [`blue-data`](https://github.com/megagon-internal/blue-data), not to this
repository. A planner is handed a populated `DataRegistry` and reads it through
`blue_data_planner.data`, the one module that imports `blue-data` at all: `DataCatalog` for the
four catalog readings the planning stages need, and `connect_source`, `execute_query`, and
`execute_write` for the reads an operator makes.

Adding a backend therefore means implementing `DataSource` in `blue-data` and registering it
there; that repository's own `docs/extending.md` covers it. Its connection protocol then reaches a
planner as `SourceRecord.kind` and `EntityMatch.source_kind`.
