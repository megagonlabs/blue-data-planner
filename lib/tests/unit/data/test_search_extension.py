"""The client's search normalization, against REAL blue-data.

Pins the two delegate result shapes (wrapped keyword fallback, flat hybrid), the score
inversion and derived-id rules, the arguments the client forwards, and the upstream
behavior a consumer must know: how a search degrades when the registry has no vectors.
"""

# blue-data ships no py.typed:
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

import inspect
from typing import Any, cast

import pytest

blue_data = pytest.importorskip("blue_data")

from blue_data.registry.data_registry import DataRegistry  # noqa: E402

from blue_data_planner.data import DataRegistryClient  # noqa: E402
from blue_data_planner.data.registry_client import (  # noqa: E402
    _normalize_search_result,  # pyright: ignore[reportPrivateUsage]
)


def _keyword_registry() -> DataRegistryClient:
    # No vector source: the registry scores on keyword match alone.
    registry: Any = DataRegistry(name="kw_probe", rebuild=True)
    registry.register_record("jobs_db", "source", "/", description="job postings database")
    registry.register_record("jobs_kb", "source", "/", description="jobs knowledge")
    registry.register_record("other", "source", "/", description="unrelated")
    return DataRegistryClient(registry)


def a_wrapped_result(score: int, **record: Any) -> dict[str, Any]:
    """The keyword fallback's shape: the record nested under a key, beside an integer score."""
    return {"record": {"type": "source", "scope": "/", **record}, "score": score}


# --- The two delegate shapes, normalized --------------------------------------------- #
def test_a_wrapped_keyword_result_is_flattened_with_a_derived_id() -> None:
    normalized = a_wrapped_result(15, name="jobs_db", description="job postings")

    assert _normalize_search_result(normalized) == {
        "name": "jobs_db",
        "type": "source",
        "scope": "/",
        "id": "//source/jobs_db",  # "{scope}/{type}/{name}" with scope "/"
        "score": pytest.approx(0.0),
        "description": "job postings",
    }


def test_a_flat_hybrid_result_drops_its_underscored_fields() -> None:
    normalized = _normalize_search_result(
        {
            "name": "jobs_db",
            "type": "source",
            "scope": "/",
            "description": "job postings",
            "_score": 0.75,
            "_most_relevant_values": ["x"],
        }
    )

    assert normalized == {
        "name": "jobs_db",
        "type": "source",
        "scope": "/",
        "id": "//source/jobs_db",
        "score": pytest.approx(0.25),
        "description": "job postings",
    }


@pytest.mark.parametrize(
    ("raw", "expected_score"),
    [
        # The keyword engine scores a name hit 10 and a description hit 5, so 15 is its best.
        (a_wrapped_result(15, name="both"), 0.0),
        (a_wrapped_result(10, name="name only"), 1.0 / 3.0),
        (a_wrapped_result(5, name="description only"), 2.0 / 3.0),
        (a_wrapped_result(0, name="no hit"), 1.0),
        ({"name": "flat", "type": "source", "scope": "/", "_score": 0.75}, 0.25),
        # A flat result with no score at all counts as the worst possible match.
        ({"name": "flat", "type": "source", "scope": "/"}, 1.0),
    ],
)
def test_a_better_delegate_score_becomes_a_lower_consumer_score(
    raw: dict[str, Any], expected_score: float
) -> None:
    assert _normalize_search_result(raw)["score"] == pytest.approx(expected_score)


# --- What the client forwards -------------------------------------------------------- #
def test_search_records_forwards_every_argument_by_keyword() -> None:
    class _Recorder:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def search_records(self, query: Any, **kwargs: Any) -> list[dict[str, Any]]:
            self.kwargs = {"input_query": query, **kwargs}
            return []

    recorder = _Recorder()
    client = DataRegistryClient(cast(Any, recorder))

    client.search_records("q", type="source", scope="/", page=1, page_size=3)

    assert recorder.kwargs == {
        "input_query": "q",
        "type": "source",
        "scope": "/",
        "page": 1,
        "page_size": 3,
    }


def test_blue_data_still_takes_the_search_arguments_this_client_forwards() -> None:
    method: Any = cast(
        Any,
        DataRegistry.search_records,  # pyright: ignore[reportUnknownMemberType]
    )
    parameters = list(inspect.signature(method).parameters)
    assert parameters[1:6] == ["input_query", "type", "scope", "page", "page_size"]


# --- An upstream behavior the catalog has to live with ------------------------------- #
def test_a_registry_with_no_vector_source_still_returns_keyword_matches() -> None:
    # With no embedding function the engine drops the vector threshold to 0 and folds the
    # vector weight into BM25, so search degrades to keyword-only rather than going blind.
    # The combined threshold (0.36) still applies, so a weaker match is dropped: "jobs_kb"
    # hits name and description, "jobs_db" hits both too but normalizes lower and falls out.
    client = _keyword_registry()

    results = client.search_records("jobs", type="source", page=0, page_size=10)

    assert [r["name"] for r in results] == ["jobs_kb"]
    assert results[0]["score"] < 0.5  # inverted: lower is better
