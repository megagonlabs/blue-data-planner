"""What opening a serialized blue-data registry gives back."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blue_data_planner.data import open_registry


def test_a_reopened_registry_holds_the_records_it_was_written_with(tmp_path: Path) -> None:
    """A registry written to disk reads back with its records addressable by path."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "name": "jobs",
                    "type": "source",
                    "scope": "/",
                    "description": "job postings",
                    "properties": {"connection": {"protocol": "sql/postgres"}},
                    "contents": {},
                }
            ]
        )
    )

    registry = open_registry(registry_path)

    # blue-data ships no py.typed, so get_record's return stays partially unknown here,
    # the one place a test reads the raw registry `open_registry` hands back.
    assert (
        registry.get_record("jobs", "source", "/")[  # pyright: ignore[reportUnknownMemberType]
            "description"
        ]
        == "job postings"
    )


def test_opening_a_registry_that_is_not_there_raises(tmp_path: Path) -> None:
    """A path naming no file is a caller error, not an empty registry."""
    with pytest.raises(FileNotFoundError):
        open_registry(tmp_path / "absent.json")
