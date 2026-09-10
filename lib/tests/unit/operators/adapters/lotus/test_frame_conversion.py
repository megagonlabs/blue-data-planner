"""The bundle as data frames and back."""

from __future__ import annotations

import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed
import pytest

from blue_data_planner.operators.adapters.lotus.frame_conversion import (
    blanks_to_none,
    frame_from_table,
    rename_join_columns,
    table_from_frame,
)
from blue_data_planner.types import RecoverableOperatorError


def test_a_table_crossing_to_a_frame_and_back_is_unchanged() -> None:
    """Values survive the round trip as the Python types they went in as."""
    table = [{"city": "Reno", "stores": 2}, {"city": "Oakland", "stores": 5}]

    assert table_from_frame(frame_from_table(table)) == table


def test_a_missing_value_comes_back_as_none_rather_than_a_float() -> None:
    """pandas writes NaN for a gap in a numeric column, and no row leaves the adapter with one."""
    table = [{"city": "Reno", "stores": 2}, {"city": "Oakland", "stores": None}]

    assert table_from_frame(frame_from_table(table)) == [
        {"city": "Reno", "stores": 2},
        {"city": "Oakland", "stores": None},
    ]


def test_a_numpy_scalar_comes_back_as_a_python_value() -> None:
    """A frame built by LOTUS holds numpy scalars, and a row leaving the adapter holds neither."""
    frame = pd.DataFrame({"stores": [2, 5]})

    values = [row["stores"] for row in table_from_frame(frame)]

    assert [type(value) for value in values] == [int, int]


def test_a_joins_side_tags_become_this_repositorys_naming() -> None:
    """A column both tables carried arrives as the plain name and a '_right' suffix."""
    frame = pd.DataFrame({"city:left": ["Reno"], "city:right": ["Reno"], "sales": [10]})

    assert list(rename_join_columns(frame).columns) == ["city", "city_right", "sales"]


def test_a_join_whose_renaming_collides_is_refused() -> None:
    """Two columns that rename to one name would lose a value, so the call fails recoverably."""
    frame = pd.DataFrame({"city:right": ["Reno"], "city_right": ["Oakland"]})

    with pytest.raises(RecoverableOperatorError):
        rename_join_columns(frame)


def test_a_blank_cell_in_a_created_column_becomes_none() -> None:
    """A semantic operation that had no answer wrote a blank, and None says so to the type check."""
    table = [{"city": "Reno", "mood": "  "}, {"city": "  ", "mood": "calm"}]

    assert blanks_to_none(table, ["mood"]) == [
        {"city": "Reno", "mood": None},
        {"city": "  ", "mood": "calm"},
    ]
