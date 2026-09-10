"""What an instruction may name before LOTUS reads it."""

from __future__ import annotations

import pytest

from blue_data_planner.operators.adapters.lotus.instruction_columns import (
    check_instruction_columns,
)
from blue_data_planner.types import RecoverableOperatorError


def test_an_instruction_naming_a_column_the_frame_carries_is_accepted() -> None:
    """A reference to a column that is present passes, awkward spelling included."""
    check_instruction_columns(
        "lotus.sem_filter",
        "Is {Percent (%) Eligible Free (K-12)} high?",
        [["Percent (%) Eligible Free (K-12)"]],
    )


@pytest.mark.parametrize(
    "instruction",
    [
        "Is this row interesting?",
        "Is {absent} high?",
        "Is {a.b} high?",
        "Is {a[0]} high?",
    ],
    ids=["no reference", "absent column", "dotted name", "indexed name"],
)
def test_an_instruction_lotus_cannot_resolve_is_refused(instruction: str) -> None:
    """An instruction naming no column, an absent one, or one str.format cannot read raises."""
    with pytest.raises(RecoverableOperatorError):
        check_instruction_columns("lotus.sem_filter", instruction, [["city"]])


def test_a_side_tag_is_looked_up_in_the_frame_it_names() -> None:
    """A join's tagged reference is checked against the frame the tag names, not the other."""
    check_instruction_columns(
        "lotus.sem_join", "Does {city:left} match {city:right}?", [["city"], ["city"]]
    )

    with pytest.raises(RecoverableOperatorError):
        check_instruction_columns(
            "lotus.sem_join", "Does {city:left} match {town:right}?", [["city"], ["city"]]
        )
