"""What each planning component reads off the catalog."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
    DataRegistry,  # blue-data ships no py.typed
)

from blue_data_planner.data.catalog import DataCatalog, source_kind
from blue_data_planner.types.catalog import AttributeRef, ValueCount
from blue_data_planner.types.errors import SourceUnreachableError


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


def test_a_relevant_source_is_ranked_ahead_of_a_registry_too_large_to_score_in_one_page() -> None:
    """The search page limit does not truncate the candidate pool before scoring."""
    filler = [a_source(f"filler_{i}", description="unrelated") for i in range(120)]
    catalog = DataCatalog(
        a_registry_holding(*filler, a_source("zzz_target", description="job postings"))
    )

    assert catalog.preview("job postings", limit=5).sources[0].name == "zzz_target"


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


def a_record(
    level: str,
    name: str,
    scope: str,
    description: str = "",
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "type": level,
        "scope": scope,
        "description": description,
        "properties": {"stats": stats} if stats is not None else {},
        "contents": {},
    }


CITY = AttributeRef(
    source="retail",
    database="shop",
    collection="public",
    entity="stores",
    attribute="city",
)
STORES = "/source/retail/database/shop/collection/public/entity/stores"


def an_attribute(
    ref: AttributeRef,
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


def sibling_of(city: AttributeRef, attribute: str) -> AttributeRef:
    return AttributeRef(
        source=city.source,
        database=city.database,
        collection=city.collection,
        entity=city.entity,
        attribute=attribute,
    )


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
        an_attribute(CITY, description="city the store is in"),
        an_attribute(sibling_of(CITY, "store_id"), dtype="INTEGER"),
    )


def test_a_search_returns_entities_and_nothing_at_the_levels_around_them() -> None:
    """The search runs at the entity level, which is the unit a SCAN is bound to."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    matches = catalog.search_records("stores in a city")

    assert [match.path for match in matches] == [STORES]
    assert matches[0].name == "stores"
    assert matches[0].description == "one row per store"


def test_a_matched_entity_carries_every_attribute_it_holds() -> None:
    """The whole attribute list is what makes a declared column name a fact."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    attributes = catalog.search_records("stores in a city")[0].attributes

    assert {attribute.ref.attribute: attribute.dtype for attribute in attributes} == {
        "city": "TEXT",
        "store_id": "INTEGER",
    }
    city = next(attribute for attribute in attributes if attribute.ref.attribute == "city")
    assert city.description == "city the store is in"
    assert attributes == catalog.attribute_views(STORES)


def test_a_matched_entity_carries_the_kind_of_source_it_sits_in() -> None:
    """source_kind decides which operators may run a fragment scanning this entity."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    assert catalog.search_records("stores in a city")[0].source_kind == "sql/sqlite"


def records_for_a_store_table_recording(stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        a_source("retail", description="store records"),
        a_record("database", "shop", "/source/retail"),
        a_record("collection", "public", "/source/retail/database/shop"),
        a_record(
            "entity",
            "stores",
            "/source/retail/database/shop/collection/public",
            "one row per store",
            stats=stats,
        ),
    ]


def a_registry_with_a_store_table_recording(stats: dict[str, Any] | None) -> DataRegistry:
    return a_registry_holding(*records_for_a_store_table_recording(stats))


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        pytest.param({"row_count": 11060}, 11060, id="row_count_recorded"),
        pytest.param(None, None, id="stats_not_recorded"),
        pytest.param({}, None, id="stats_recorded_without_row_count"),
    ],
)
def test_a_matched_entitys_row_count_reflects_what_the_registry_recorded(
    stats: dict[str, Any] | None, expected: int | None
) -> None:
    """A search hit carries the row count the entity's record stores, or None if it stores none."""
    catalog = DataCatalog(a_registry_with_a_store_table_recording(stats))

    assert catalog.search_records("stores in a city")[0].row_count == expected


@pytest.mark.parametrize(
    ("stats", "entity_path", "expected"),
    [
        pytest.param({"row_count": 5716}, STORES, 5716, id="row_count_recorded"),
        pytest.param({}, STORES, None, id="stats_recorded_without_row_count"),
        pytest.param(
            {"row_count": 5716},
            "/source/retail/database/shop/collection/public/entity/warehouses",
            None,
            id="entity_not_registered",
        ),
    ],
)
def test_an_entitys_row_count_read_by_path_reflects_what_the_registry_recorded(
    stats: dict[str, Any], entity_path: str, expected: int | None
) -> None:
    """The fragment runner reads the same stored count by path, or None where none is stored."""
    catalog = DataCatalog(a_registry_with_a_store_table_recording(stats))

    assert catalog.entity_row_count(entity_path) == expected


def a_registry_with_two_store_tables_and_a_supplier_table() -> DataRegistry:
    warehouses = AttributeRef(
        source="retail",
        database="shop",
        collection="public",
        entity="warehouses",
        attribute="city",
    )
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
            "entity",
            "warehouses",
            "/source/retail/database/shop/collection/public",
            "one row per warehouse that restocks a store",
        ),
        a_record(
            "entity",
            "suppliers",
            "/source/retail/database/shop/collection/public",
            "one row per supplier",
        ),
        an_attribute(CITY),
        an_attribute(warehouses),
    )


def test_a_search_ranks_its_matches_and_names_the_term_that_found_them() -> None:
    """The entity matching more of the term leads the list."""
    catalog = DataCatalog(a_registry_with_two_store_tables_and_a_supplier_table())

    matches = catalog.search_records("store warehouse")

    assert [match.name for match in matches] == ["warehouses", "stores"]
    assert [match.search_term for match in matches] == ["store warehouse", "store warehouse"]
    assert matches == sorted(matches, key=lambda match: match.score, reverse=True)


def test_a_search_matching_no_entity_returns_nothing() -> None:
    """An entity the term does not match is never presented as a hit, however few matched."""
    catalog = DataCatalog(a_registry_with_two_store_tables_and_a_supplier_table())

    assert catalog.search_records("customer") == []


def test_a_search_returns_no_more_matches_than_the_catalog_was_built_to_return() -> None:
    """max_search_results is what bounds how much catalog one prompt carries."""
    catalog = DataCatalog(
        a_registry_with_two_store_tables_and_a_supplier_table(), max_search_results=1
    )

    # Both entities match "store", so the cap is what makes this one.
    assert len(catalog.search_records("store")) == 1


def a_registry_holding_a_profiled_city_column() -> DataRegistry:
    return a_registry_holding(
        an_attribute(
            CITY,
            description="city the store is in",
            stats={
                "sample_values": ["Napa", "Sonoma"],
                "most_common_vals": [["Napa", 3], ["Sonoma", 1]],
                "count": 4,
                "distinct_count": 2,
                "null_count": 1,
                "min": "Napa",
                "max": "Sonoma",
            },
            semantic_type="CITY",
        )
    )


def test_an_attribute_view_carries_the_catalogs_whole_attribute_statistics() -> None:
    """A view is the registry's own profile of the attribute, not a sample of it."""
    catalog = DataCatalog(a_registry_holding_a_profiled_city_column())

    view = catalog.attribute_views(STORES)[0]

    assert view.ref == CITY
    assert view.dtype == "TEXT"
    assert view.description == "city the store is in"
    assert view.frequent_values == [ValueCount("Napa", 3), ValueCount("Sonoma", 1)]
    assert view.sample_values == ["Napa", "Sonoma"]
    assert (view.distinct_count, view.null_count, view.counted_rows) == (2, 1, 5)
    assert (view.min, view.max) == ("Napa", "Sonoma")
    assert view.semantic_type == "CITY"
    assert view.more_values_recorded is False


def test_the_views_cover_every_attribute_of_the_entity_asked_for() -> None:
    """Which attributes to name is the decision the caller has yet to take."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    views = catalog.attribute_views(STORES)

    assert {view.ref.attribute for view in views} == {"city", "store_id"}


def test_a_view_holds_a_bounded_number_of_values_each_cut_to_a_bounded_length() -> None:
    """Five frequent values and three samples, each at most 60 characters, marked where cut."""
    long_text = "x" * 200
    stats = {
        "sample_values": [long_text, "Sonoma", "Ukiah", "Davis", "Chico"],
        "most_common_vals": [[long_text, 9], *[[f"city {rank}", 8 - rank] for rank in range(6)]],
    }
    catalog = DataCatalog(a_registry_holding(an_attribute(CITY, stats=stats)))

    view = catalog.attribute_views(STORES)[0]

    cut = "x" * 59 + "…"
    assert [one.value for one in view.frequent_values] == [cut] + [f"city {r}" for r in range(4)]
    assert view.sample_values == [cut, "Sonoma", "Ukiah"]
    assert view.more_values_recorded is True  # seven recorded, five kept


@pytest.mark.parametrize(
    ("most_common_vals", "empty_count"),
    [
        # Six values ahead of it: past the five a view holds, and still counted.
        pytest.param([*[[f"user{rank}", 50 - rank] for rank in range(6)], ["", 3]], 3, id="listed"),
        pytest.param([["user28", 65]], None, id="not_listed"),
    ],
)
def test_the_empty_string_count_is_read_off_every_recorded_frequent_value(
    most_common_vals: list[list[Any]], empty_count: int | None
) -> None:
    """A frequent-value list not naming the empty string leaves its count unknown, not zero."""
    stats = {"most_common_vals": most_common_vals, "count": 2000, "null_count": 0}
    catalog = DataCatalog(a_registry_holding(an_attribute(CITY, stats=stats)))

    assert catalog.attribute_views(STORES)[0].empty_count == empty_count


@pytest.mark.parametrize(
    "most_common_vals",
    [
        pytest.param(["Napa", "Sonoma"], id="flat_strings"),
        pytest.param([94558, 95476], id="flat_numbers"),
        pytest.param("{Napa,Sonoma}", id="postgres_array_text"),
        pytest.param([["Napa", "3"]], id="non_integer_count"),
        pytest.param([["Napa", 2.5]], id="fractional_count"),
        pytest.param([["Napa"]], id="value_without_a_count"),
        pytest.param([["Napa", 3], "Sonoma"], id="valid_pair_beside_a_bare_value"),
        pytest.param([["Napa", 3], ["Sonoma", "1"]], id="valid_pair_beside_a_bad_count"),
    ],
)
def test_frequent_values_that_are_not_all_value_and_count_pairs_read_as_none(
    most_common_vals: Any,
) -> None:
    """Sources write other shapes, so the view keeps its samples and states no frequencies."""
    stats = {"sample_values": ["Napa", "Sonoma"], "most_common_vals": most_common_vals}
    catalog = DataCatalog(a_registry_holding(an_attribute(CITY, stats=stats)))

    view = catalog.attribute_views(STORES)[0]

    assert view.frequent_values == []
    assert view.sample_values == ["Napa", "Sonoma"]
    assert view.empty_count is None


def test_an_unprofiled_attribute_renders_with_its_type_and_nothing_else() -> None:
    """An attribute nobody profiled still gets a view; only the annotation is missing."""
    catalog = DataCatalog(a_registry_holding(an_attribute(CITY)))

    view = catalog.attribute_views(STORES)[0]

    assert view.dtype == "TEXT"
    assert view.description is None
    assert (view.frequent_values, view.sample_values) == ([], [])
    assert (view.distinct_count, view.null_count, view.empty_count, view.counted_rows) == (
        None,
        None,
        None,
        None,
    )
    assert (view.min, view.max) == (None, None)
    assert view.semantic_type is None


@pytest.mark.parametrize(
    ("properties", "expected_dtype"),
    [
        pytest.param({"info": {"type": "TEXT"}}, "TEXT", id="sql_mapping"),
        pytest.param({"info": "str"}, "str", id="doc_user_string"),
        pytest.param({"info": "str|None"}, "str|None", id="optional_type_string"),
        pytest.param({}, None, id="mongo_missing_info"),
        pytest.param({"info": None}, None, id="null_info"),
        pytest.param({"info": ""}, None, id="empty_info"),
        pytest.param({"info": {}}, None, id="no_type_field"),
        pytest.param({"info": {"type": ""}}, None, id="empty_type"),
        pytest.param({"info": {"type": None}}, None, id="null_type"),
    ],
)
def test_an_attribute_type_is_read_from_its_source_metadata(
    properties: dict[str, Any], expected_dtype: str | None
) -> None:
    record = an_attribute(CITY)
    record["properties"] = properties
    catalog = DataCatalog(a_registry_holding(*records_for_a_store_table_recording(None), record))

    views = catalog.attribute_views(STORES)
    assert views[0].dtype == expected_dtype
    matches = catalog.search_records("stores")
    assert [match.path for match in matches] == [STORES]
    assert matches[0].attributes == views


def test_an_entity_the_registry_holds_no_attributes_for_gets_no_views() -> None:
    """No record at all is an attribute with no view."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    assert (
        catalog.attribute_views("/source/retail/database/shop/collection/public/entity/warehouses")
        == []
    )


def test_a_path_that_does_not_name_an_entity_raises() -> None:
    """attribute_views is a boundary, and an entity path is what it takes."""
    catalog = DataCatalog(a_registry_with_a_store_table())

    with pytest.raises(ValueError, match="is not an entity path"):
        catalog.attribute_views("/source/retail/database/shop")


def an_attribute_with_malformed_properties() -> dict[str, Any]:
    record = an_attribute(CITY)
    record["properties"] = "oops"
    return record


def an_attribute_with_a_non_string_description() -> dict[str, Any]:
    record = an_attribute(CITY)
    record["description"] = 123
    return record


def an_attribute_with_malformed_value_semantics() -> dict[str, Any]:
    record = an_attribute(CITY)
    record["properties"]["value_semantics"] = "oops"
    return record


def a_source_with_a_malformed_connection() -> dict[str, Any]:
    record = a_source("broken")
    record["properties"]["connection"] = "oops"
    return record


def a_source_missing_a_protocol() -> dict[str, Any]:
    record = a_source("broken")
    record["properties"]["connection"] = {}
    return record


def a_store_table_whose_source_record_is_missing() -> list[dict[str, Any]]:
    return [
        a_record(
            "entity",
            "stores",
            "/source/retail/database/shop/collection/public",
            "one row per store",
        )
    ]


def a_store_table_with_a_non_int_row_count() -> list[dict[str, Any]]:
    return records_for_a_store_table_recording({"row_count": "eleven thousand"})


@pytest.mark.parametrize(
    ("records", "call_kind", "expected_message"),
    [
        pytest.param(
            [an_attribute_with_malformed_properties()],
            "attribute_views",
            "properties is",
            id="properties_not_a_mapping",
        ),
        pytest.param(
            [an_attribute_with_a_non_string_description()],
            "attribute_views",
            "description is",
            id="description_not_a_string",
        ),
        pytest.param(
            [an_attribute(CITY) | {"properties": {"info": []}}],
            "attribute_views",
            "properties.info is list",
            id="info_is_list",
        ),
        pytest.param(
            [an_attribute(CITY) | {"properties": {"info": 123}}],
            "attribute_views",
            "properties.info is int",
            id="info_is_number",
        ),
        pytest.param(
            [an_attribute(CITY, stats=cast("Any", "two rows"))],
            "attribute_views",
            "stats is",
            id="stats_not_a_mapping",
        ),
        pytest.param(
            [an_attribute_with_malformed_value_semantics()],
            "attribute_views",
            "value_semantics is",
            id="value_semantics_not_a_mapping",
        ),
        pytest.param(
            a_store_table_whose_source_record_is_missing(),
            "search_records",
            "no source record",
            id="matched_entity_has_no_source_record",
        ),
        pytest.param(
            a_store_table_with_a_non_int_row_count(),
            "search_records",
            "row_count is",
            id="matched_entitys_row_count_not_an_int",
        ),
        pytest.param(
            records_for_a_store_table_recording({"row_count": True}),
            "search_records",
            "row_count is",
            id="matched_entitys_row_count_a_bool",
        ),
        pytest.param(
            [a_source_with_a_malformed_connection()],
            "preview",
            "properties.connection missing or not a mapping",
            id="source_connection_not_a_mapping",
        ),
        pytest.param(
            [a_source_missing_a_protocol()],
            "preview",
            "properties.connection has no protocol field",
            id="source_protocol_missing",
        ),
    ],
)
def test_a_malformed_record_raises(
    records: list[dict[str, Any]], call_kind: str, expected_message: str
) -> None:
    """A record that exists but cannot be read is a corrupt registry."""
    catalog = DataCatalog(a_registry_holding(*records))

    with pytest.raises(ValueError, match=expected_message):
        if call_kind == "attribute_views":
            catalog.attribute_views(STORES)
        elif call_kind == "search_records":
            catalog.search_records("stores")
        else:
            catalog.preview("anything", limit=10)


def test_a_sources_kind_is_read_off_the_record_registered_under_its_name() -> None:
    """An operator that reads a source by name learns its protocol the way the catalog does."""
    registry = a_registry_holding(a_source("tag_bench"), a_source("jobs", protocol="sql/postgres"))

    assert source_kind(registry, "jobs") == "sql/postgres"


def test_the_kind_of_a_source_the_registry_does_not_hold_cannot_be_read() -> None:
    """A name with no record behind it is a source nothing can be read from."""
    registry = a_registry_holding(a_source("tag_bench"))

    with pytest.raises(SourceUnreachableError, match="no source named 'jobs'"):
        source_kind(registry, "jobs")
