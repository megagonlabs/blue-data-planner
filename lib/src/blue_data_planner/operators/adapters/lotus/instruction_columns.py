"""What an instruction may name, checked before LOTUS reads it."""

from __future__ import annotations

import re

from blue_data_planner.types import RecoverableOperatorError

# The pattern LOTUS reads column references with: single braces, doubled ones escaped.
_REFERENCE = re.compile(r"(?<!\{)\{(?!\{)(.*?)(?<!\})\}(?!\})")
# LOTUS resolves a reference through str.format, whose field syntax reads "." as an
# attribute and "[" as an index, so a column named with either cannot be reached at all.
_UNRESOLVABLE = (".", "[")
_SIDES = {":left": 0, ":right": 1}


def check_instruction_columns(operator_id: str, instruction: str, frames: list[list[str]]) -> None:
    """Raise when an instruction names no column, an absent one, or one LOTUS cannot resolve."""
    references = _REFERENCE.findall(instruction)
    if not references:
        raise RecoverableOperatorError(
            f"{operator_id} needs an instruction naming the columns to read, each in braces, "
            "such as 'Is {city} on the coast?'"
        )
    for reference in references:
        name, columns = _resolve(operator_id, reference, frames)
        if any(character in name for character in _UNRESOLVABLE):
            raise RecoverableOperatorError(
                f"{operator_id} cannot read the column {name!r}, because a column reference "
                "holding '.' or '[' is not resolvable"
            )
        if name not in columns:
            raise RecoverableOperatorError(
                f"{operator_id} was given an instruction naming the column {name!r}, which its "
                f"input does not carry; it holds {', '.join(repr(one) for one in columns)}"
            )


def _resolve(operator_id: str, reference: str, frames: list[list[str]]) -> tuple[str, list[str]]:
    """Return the column a reference names and the columns of the table it is looked up in."""
    for tag, index in _SIDES.items():
        if reference.endswith(tag):
            if index >= len(frames):
                raise RecoverableOperatorError(
                    f"{operator_id} reads one table, and the instruction tags {reference!r} as "
                    "belonging to another"
                )
            return reference[: -len(tag)], frames[index]
    return reference, [column for frame in frames for column in frame]
