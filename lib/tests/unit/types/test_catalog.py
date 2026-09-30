"""What a catalog reading carries when it leaves the process."""

from __future__ import annotations

import json

from blue_data_planner.types import EntityMatch
from tests.doubles.catalog import an_attribute_view


def test_a_match_travels_as_json_with_every_attribute_it_holds() -> None:
    """A search result reaches a caller's document whole, each attribute's view included."""
    match = EntityMatch(
        path="/source/jobs/database/jobs/collection/public/entity/postings",
        name="postings",
        description="job postings",
        source_kind="sql/sqlite",
        attributes=[an_attribute_view("title"), an_attribute_view("location")],
        score=0.9,
        search_term="job postings",
        row_count=312,
    )

    payload = json.loads(json.dumps(match.to_dict()))

    assert [view["ref"]["attribute"] for view in payload["attributes"]] == ["title", "location"]
    assert payload["row_count"] == 312
