"""The bundle as data frames and back."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from blue_data_planner.types import Data, OutputColumn, RecoverableOperatorError

if TYPE_CHECKING:
    import pandas as pd  # pyright: ignore[reportMissingTypeStubs]  # pandas ships no py.typed

# LOTUS tags a column both tables carried rather than suffixing the right-hand one, and
# design/06 fixes '_right' for every JOIN this repository answers.
_LEFT_TAG = ":left"
_RIGHT_TAG = ":right"

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
    columns: list[Any] = list(frame.columns)
    renamed = {str(name): _join_name(str(name)) for name in columns}
    if len(set(renamed.values())) != len(renamed):
        raise RecoverableOperatorError(
            "a join produced two columns that rename to one name: "
            f"{', '.join(sorted(renamed.values()))}"
        )
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


def _join_name(name: str) -> str:
    if name.endswith(_LEFT_TAG):
        return name[: -len(_LEFT_TAG)]
    if name.endswith(_RIGHT_TAG):
        return f"{name[: -len(_RIGHT_TAG)]}_right"
    return name


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
