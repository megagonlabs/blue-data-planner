"""Which columns an operation creates, read off the node's declaration."""

from __future__ import annotations

import pytest

from blue_data_planner.operators.adapters.lotus.declared_columns import (
    check_named_columns,
    new_columns,
    one_new_column,
)
from blue_data_planner.types import OutputColumn, RecoverableOperatorError


def declared(*names: str) -> list[OutputColumn]:
    """Return one declaration per name, all of them strings."""
    return [OutputColumn(name=name, dtype="str") for name in names]


def test_the_new_columns_are_the_declared_ones_the_input_does_not_carry() -> None:
    """A declaration is read against the input's columns, in the order it was declared."""
    assert new_columns(declared("city", "summary", "rank"), ["city"]) == ["summary", "rank"]


def test_a_single_new_column_is_taken_from_the_declaration() -> None:
    """An operation creating one column takes its name from the node rather than a parameter."""
    assert one_new_column("lotus.sem_map", declared("city", "mood"), ["city"], None) == "mood"


@pytest.mark.parametrize(
    "names, present",
    [(("city",), ["city"]), (("city", "mood", "tone"), ["city"])],
    ids=["none new", "two new"],
)
def test_a_declaration_of_any_other_count_is_refused(
    names: tuple[str, ...], present: list[str]
) -> None:
    """An operation creating one column refuses a node asking for none or several."""
    with pytest.raises(RecoverableOperatorError):
        one_new_column("lotus.sem_map", declared(*names), present, None)


def test_an_undeclared_node_falls_back_to_the_parameter() -> None:
    """With nothing declared, the operation takes the column name the parameter carries."""
    assert one_new_column("lotus.sem_map", [], ["city"], "mood") == "mood"


def test_an_undeclared_node_with_no_parameter_is_refused() -> None:
    """With nothing declared and no parameter, there is no name to create the column under."""
    with pytest.raises(RecoverableOperatorError):
        one_new_column("lotus.sem_map", [], ["city"], None)


def test_named_columns_matching_the_declaration_are_accepted() -> None:
    """An extraction naming exactly the new declared columns passes."""
    check_named_columns("lotus.sem_extract", declared("bio", "born"), ["bio"], ["born"])


def test_named_columns_that_are_not_the_declared_ones_are_refused() -> None:
    """An extraction naming a column the node did not declare raises, naming both sets."""
    with pytest.raises(RecoverableOperatorError) as refusal:
        check_named_columns("lotus.sem_extract", declared("bio", "born"), ["bio"], ["born_year"])

    assert "born" in str(refusal.value)
    assert "born_year" in str(refusal.value)
