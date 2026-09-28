"""The bundle as data frames and back."""

from __future__ import annotations

import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed
import pytest

from blue_data_planner.operators.adapters.lotus.frame_conversion import (
    frame_from_table,
    missing_to_none,
    rename_join_columns,
    table_from_frame,
)
from blue_data_planner.types import ColumnDataType, OutputColumn


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
    """A column both tables carried arrives with a '_left' and a '_right' suffix."""
    frame = pd.DataFrame({"city:left": ["Reno"], "city:right": ["Reno"], "sales": [10]})

    assert list(rename_join_columns(frame).columns) == ["city_left", "city_right", "sales"]


def test_a_joins_side_tag_whose_name_is_taken_takes_the_first_free_name() -> None:
    """A suffixed name the frame already holds is not reused, so no value is lost."""
    frame = pd.DataFrame({"city:right": ["Reno"], "city_right": ["Oakland"]})

    assert list(rename_join_columns(frame).columns) == ["city_right_2", "city_right"]


@pytest.mark.parametrize("dtype", ["int", "float", "bool"])
@pytest.mark.parametrize("spelling", ["  ", "null", "None", " NaN ", "N/A"])
def test_a_spelling_of_no_answer_in_a_created_typed_column_becomes_none(
    dtype: ColumnDataType, spelling: str
) -> None:
    """A semantic operation with no answer writes it as text, and None says so to the type check.

    A column the operation did not create keeps what its source held.
    """
    table = [{"city": "null", "rate": spelling}, {"city": "Reno", "rate": "0.5"}]

    assert missing_to_none(table, ["rate"], [OutputColumn(name="rate", dtype=dtype)]) == [
        {"city": "null", "rate": None},
        {"city": "Reno", "rate": "0.5"},
    ]


@pytest.mark.parametrize("declared", [[OutputColumn(name="mood", dtype="str")], []])
def test_only_a_blank_in_a_created_text_column_becomes_none(declared: list[OutputColumn]) -> None:
    """In a column declared str, or not declared at all, "None" may be a real value."""
    table = [{"mood": "  "}, {"mood": "None"}, {"mood": "null"}]

    assert missing_to_none(table, ["mood"], declared) == [
        {"mood": None},
        {"mood": "None"},
        {"mood": "null"},
    ]
