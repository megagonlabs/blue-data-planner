"""The bundle as data frames and back."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from blue_data_planner.types import Data, OutputColumn

if TYPE_CHECKING:
    import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed

# LOTUS tags a column both tables carried with the side it came from, and design/06 fixes
# '_left' and '_right' for every JOIN this repository answers.
_SIDE_TAGS = {":left": "_left", ":right": "_right"}

# What a model writes in a text answer when it has no value. Read as missing only in a column
# declared other than str, where none of them can be a real value; in a str column "None" may be
# a name, so there only a blank counts.
MISSING_VALUE_SPELLINGS: frozenset[str] = frozenset({"null", "none", "nan", "n/a"})


def frame_from_table(table: Data) -> pd.DataFrame:
    """Return one table as the data frame LOTUS reads."""
    import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed

    return pd.DataFrame(table)


def table_from_frame(frame: pd.DataFrame) -> Data:
    """Return one frame as plain rows, with numpy scalars and missing values in Python's terms."""
    import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed

    # pandas ships no stubs, so to_dict's member type is unresolved; casting the frame to Any
    # before the call keeps that unresolved type from spreading into the comprehension below.
    records = cast(list[dict[Any, Any]], cast(Any, frame).to_dict("records"))
    return [{str(name): _plain(value, pd) for name, value in record.items()} for record in records]


def rename_join_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a join's frame with LOTUS's side tags in this repository's naming."""
    # Same reasoning as table_from_frame: frame.columns has no resolved type of its own.
    names = [str(name) for name in cast(list[Any], list(frame.columns))]
    taken = set(names)
    renamed: dict[str, str] = {}
    for name in names:
        new_name = _join_name(name, taken)
        taken.add(new_name)
        renamed[name] = new_name
    return frame.rename(columns=renamed)


def missing_to_none(
    table: Data, created: list[str], required_output_columns: list[OutputColumn]
) -> Data:
    """Return rows whose created columns read a model's spelling of no answer as None."""
    named = set(created)
    # A created column the node did not declare is text, like one declared str.
    typed = {column.name for column in required_output_columns if column.dtype != "str"}
    return [
        {
            name: None if name in named and _is_missing(value, name in typed) else value
            for name, value in row.items()
        }
        for row in table
    ]


def _is_missing(value: Any, typed: bool) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return not normalized or (typed and normalized in MISSING_VALUE_SPELLINGS)


def _join_name(name: str, taken: set[str]) -> str:
    for tag, suffix in _SIDE_TAGS.items():
        if name.endswith(tag):
            return _free_name(f"{name[: -len(tag)]}{suffix}", taken)
    return name


def _free_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    suffix = 2
    while f"{name}_{suffix}" in taken:
        suffix += 1
    return f"{name}_{suffix}"


def _plain(value: Any, pd: Any) -> Any:
    if value is None:
        return None
    try:
        missing = bool(pd.isna(value))
    except (TypeError, ValueError):
        # A list or an array answers isna element-wise rather than with one boolean.
        return value
    if missing:
        return None
    # A numpy scalar answers item() with the Python value behind it; a str has no item().
    item = getattr(value, "item", None)
    return item() if callable(item) else value
