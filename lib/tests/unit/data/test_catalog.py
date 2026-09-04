"""What each planning component reads off the catalog."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.data.catalog import DataCatalog
from blue_data_planner.types.catalog import ColumnRef


def a_registry_holding(*records: dict[str, Any]) -> DataRegistry:
    registry: Any = DataRegistry(name="test", rebuild=True)
    registry.loads(json.dumps(list(records)))
    return registry


def a_source(name: str, protocol: str = "sql/sqlite", description: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "type": "source",
        "scope": "/",
        "description": description,
        "properties": {"connection": {"protocol": protocol}},
        "contents": {},
    }


def test_the_preview_lists_every_registered_source_when_they_fit_under_the_cap() -> None:
    """A cap no smaller than the registry returns the whole registry, ranked."""
    catalog = DataCatalog(
        a_registry_holding(
            a_source("tag_bench", description="retail and racing databases"),
            a_source("jobs", protocol="sql/postgres", description="job postings"),
            a_source("reviews_lake", protocol="file/csv"),
        )
    )

    preview = catalog.preview("job postings", limit=10)

    by_name = {source.name: source for source in preview.sources}
    assert set(by_name) == {"tag_bench", "jobs", "reviews_lake"}
    assert by_name["jobs"].kind == "sql/postgres"
    assert by_name["jobs"].description == "job postings"


def test_the_preview_puts_the_sources_a_request_matches_ahead_of_the_rest() -> None:
    """Ranking is against the request text, so the match leads the list."""
    catalog = DataCatalog(
        a_registry_holding(
            a_source("tag_bench", description="retail and racing databases"),
            a_source("jobs", protocol="sql/postgres", description="job postings"),
        )
    )

    assert catalog.preview("job postings", limit=10).sources[0].name == "jobs"
    assert catalog.preview("racing results", limit=10).sources[0].name == "tag_bench"


def test_the_preview_reports_the_registered_total_when_it_returns_a_sample() -> None:
    """A capped list still says how many sources the registry holds."""
    catalog = DataCatalog(
        a_registry_holding(a_source("tag_bench"), a_source("jobs"), a_source("reviews_lake"))
    )

    preview = catalog.preview("job postings", limit=2)

    assert len(preview.sources) == 2
    assert preview.total_sources == 3


def test_a_source_nobody_described_renders_with_no_description() -> None:
    """A registry stores an empty string where enrichment never ran; None is what that means."""
    catalog = DataCatalog(a_registry_holding(a_source("reviews_lake", protocol="file/csv")))

    source = catalog.preview("anything", limit=10).sources[0]

    assert source.description is None
    assert source.kind == "file/csv"
    assert source.path == "/source/reviews_lake"


def a_record(level: str, name: str, scope: str, description: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "type": level,
        "scope": scope,
        "description": description,
        "properties": {},
        "contents": {},
    }


def a_registry_with_a_store_table() -> DataRegistry:
    return a_registry_holding(
        a_source("retail", description="store records"),
        a_record("database", "shop", "/source/retail"),
        a_record("collection", "public", "/source/retail/database/shop"),
        a_record(
            "entity",
            "stores",
            "/source/retail/database/shop/collection/public",
            "one row per store",
        ),
        a_record(
            "attribute",
            "city",
            "/source/retail/database/shop/collection/public/entity/stores",
            "city the store is in",
        ),
    )


def test_a_search_ranks_records_at_every_level_and_names_the_term_that_found_each() -> None:
    """One term searches the whole catalog, and each match says which term found it."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    matches = catalog.search_records("stores in a city")

    assert {match.level for match in matches} == {
        "source",
        "database",
        "collection",
        "entity",
        "attribute",
    }
    assert [match.search_term for match in matches] == ["stores in a city"] * len(matches)
    assert matches == sorted(matches, key=lambda match: match.score, reverse=True)
    assert (
        matches[0].path
        == "/source/retail/database/shop/collection/public/entity/stores/attribute/city"
    )


def test_a_source_level_match_has_no_double_slash_from_the_root_scope() -> None:
    """A source's scope is the registry root, so its path is /source/{name}, not //source/{name}."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    matches = catalog.search_records("stores in a city")

    source_match = next(match for match in matches if match.level == "source")
    assert source_match.path == "/source/retail"


def test_a_search_returns_no_more_matches_than_the_catalog_was_built_to_return() -> None:
    """max_search_results is what bounds how much catalog one prompt carries."""
    catalog = DataCatalog(a_registry_with_a_store_table(), max_search_results=2)

    # The registry holds five records, so the cap is what makes this two.
    assert len(catalog.search_records("stores in a city")) == 2


CITY = ColumnRef(
    source="retail",
    database="shop",
    collection="public",
    entity="stores",
    attribute="city",
)


def an_attribute(
    ref: ColumnRef,
    dtype: str | None = "TEXT",
    description: str = "",
    stats: dict[str, Any] | None = None,
    semantic_type: str | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    if dtype is not None:
        properties["info"] = {"type": dtype}
    if stats is not None:
        properties["stats"] = stats
    if semantic_type is not None:
        properties["value_semantics"] = {"semantic_type": semantic_type}
    return {
        "name": ref.attribute,
        "type": "attribute",
        "scope": (
            f"/source/{ref.source}/database/{ref.database}"
            f"/collection/{ref.collection}/entity/{ref.entity}"
        ),
        "description": description,
        "properties": properties,
        "contents": {},
    }


def a_registry_holding_a_profiled_city_column() -> DataRegistry:
    return a_registry_holding(
        an_attribute(
            CITY,
            description="city the store is in",
            stats={
                "sample_values": ["Napa", "Sonoma"],
                "distinct_count": 2,
                "null_count": 0,
                "min": "Napa",
                "max": "Sonoma",
            },
            semantic_type="CITY",
        )
    )


def test_a_column_view_carries_the_catalogs_whole_column_statistics() -> None:
    """A view is the registry's own profile of the column, not a sample of it."""
    catalog = DataCatalog(a_registry_holding_a_profiled_city_column())

    view = catalog.column_views([CITY])[0]

    assert view.ref == CITY
    assert view.dtype == "TEXT"
    assert view.description == "city the store is in"
    assert view.sample_values == ["Napa", "Sonoma"]
    assert (view.distinct_count, view.null_count) == (2, 0)
    assert (view.min, view.max) == ("Napa", "Sonoma")
    assert view.semantic_type == "CITY"


def test_a_column_an_operator_generated_gets_no_view() -> None:
    """A column with no registry record is annotated by nothing, and asks for nothing."""
    catalog = DataCatalog(a_registry_holding_a_profiled_city_column())
    generated = ColumnRef(
        source="retail",
        database="shop",
        collection="public",
        entity="stores",
        attribute="sentiment",
    )

    assert catalog.column_views([generated]) == []
    assert len(catalog.column_views([CITY, generated])) == 1


def test_an_unenriched_column_renders_with_its_type_and_no_description() -> None:
    """A column nobody profiled still gets a view; only the annotation is missing."""
    catalog = DataCatalog(a_registry_holding(an_attribute(CITY)))

    view = catalog.column_views([CITY])[0]

    assert view.dtype == "TEXT"
    assert view.description is None
    assert view.sample_values == []
    assert (view.distinct_count, view.null_count, view.min, view.max) == (
        None,
        None,
        None,
        None,
    )
    assert view.semantic_type is None


def test_a_malformed_record_raises() -> None:
    """A record that exists but cannot be read as a column is a corrupt registry."""
    no_type = DataCatalog(a_registry_holding(an_attribute(CITY, dtype=None)))
    with pytest.raises(ValueError, match="properties.info.type"):
        no_type.column_views([CITY])

    bad_stats = DataCatalog(a_registry_holding(an_attribute(CITY, stats=cast("Any", "two rows"))))
    with pytest.raises(ValueError, match="stats"):
        bad_stats.column_views([CITY])
