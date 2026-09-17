"""One real request, planned twice through a DISCOVER node and answered.

Excluded from testpaths, so it runs only when named, and it calls a real provider:

    uv run pytest tests/benchmarks/test_end_to_end.py
"""

from __future__ import annotations

from blue_data_planner import RunStatus, build_data_planner
from blue_data_planner.data import DataCatalog, DataRegistry
from blue_data_planner.llm import resolve_llm_client
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.builtin import builtin_operators


def test_a_request_over_a_registered_source_plans_twice_and_answers(
    sqlite_registry: DataRegistry,
) -> None:
    """The first pass only asks the catalog; the second is written against what it found."""
    client = resolve_llm_client(None)
    router = OperatorRouter()
    # with_nl2table stays off: the atomic entries answer this request, and the compound one
    # runs a generated program, which open questions 49 and 50 have not settled.
    for operator in builtin_operators(sqlite_registry, client, with_nl2table=False):
        router.register(operator)
    planner = build_data_planner(router, DataCatalog(sqlite_registry), client)

    result = planner.run("which cities have stores")

    assert result.status is RunStatus.OK
    assert result.answer is not None
    assert len(result.pipeline.logical_plans) >= 2
    assert result.counters.control_replans >= 1
