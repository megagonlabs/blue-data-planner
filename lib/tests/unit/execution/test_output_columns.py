"""Unit tests for the check a result has to pass against a node's declared columns."""

from __future__ import annotations

import pytest

from blue_data_planner.execution.output_columns import (
    DeclaredTypeMismatch,
    check_output_columns,
    coerce_value,
)
from blue_data_planner.types import (
    Data,
    ErrorKind,
    ExecutionResult,
    ExecutionStatus,
    OutputColumn,
)


def _ok(*tables: Data) -> ExecutionResult:
    """A successful result over the tables given, so a test writes only rows."""
    return ExecutionResult(
        status=ExecutionStatus.OK,
        output=list(tables),
        operator_id="blue.select",
        row_count=sum(len(table) for table in tables),
    )


@pytest.mark.parametrize(
    ("value", "declared_type", "expected"),
    [
        (42, "str", 42),
        ("anything", "str", "anything"),
        ("1815", "int", 1815),
        (1815.0, "int", 1815),
        (7, "int", 7),
        ("48.5", "float", 48.5),
        (3, "float", 3.0),
        (True, "bool", True),
        (1, "bool", True),
        (0, "bool", False),
        ("YES", "bool", True),
        ("n", "bool", False),
        ("0", "bool", False),
    ],
)
def test_coerce_value_accepts_the_declared_spellings(
    value: object, declared_type: str, expected: object
) -> None:
    """Being told the type lets the check accept spellings inference has to refuse."""
    assert coerce_value(value, declared_type) == expected  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("declared_type", ["str", "int", "float", "bool"])
def test_none_passes_every_declared_type(declared_type: str) -> None:
    """A missing value is a value the source holds, not a type error."""
    assert coerce_value(None, declared_type) is None  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("value", "declared_type"),
    [
        ("K-5", "int"),
        ("142,984", "int"),
        (48.7, "int"),
        ("nan", "float"),
        ("inf", "float"),
        ("maybe", "bool"),
        (7, "bool"),
    ],
)
def test_coerce_value_refuses_a_value_that_spells_no_such_type(
    value: object, declared_type: str
) -> None:
    """A value the declared type cannot hold is refused rather than salvaged."""
    with pytest.raises(DeclaredTypeMismatch):
        coerce_value(value, declared_type)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("declared_type", ["int", "float"])
def test_a_boolean_is_refused_for_a_numeric_column(declared_type: str) -> None:
    """bool subclasses int, so True would otherwise pass as 1 without being caught."""
    with pytest.raises(DeclaredTypeMismatch):
        coerce_value(True, declared_type)  # pyright: ignore[reportArgumentType]


def test_a_declared_column_is_coerced_in_the_returned_result() -> None:
    """A year an operator returned as text reaches the next node as a number."""
    result = check_output_columns(
        _ok([{"title": "a", "year": "1815"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.status is ExecutionStatus.OK
    assert result.output == [[{"title": "a", "year": 1815}]]


def test_an_undeclared_column_is_left_alone() -> None:
    """Only the columns a node declared are coerced; the rest travel as they arrived."""
    result = check_output_columns(
        _ok([{"code": "0728", "year": "1815"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.output == [[{"code": "0728", "year": 1815}]]


def test_no_declared_columns_means_no_check() -> None:
    """A RETURN node declares no output columns, so its result passes through."""
    original = _ok([{"answer": "1815"}])
    assert check_output_columns(original, [], "blue.return") is original


def test_a_zero_row_result_is_not_a_failure() -> None:
    """An empty result is an answer, and carries no column evidence either way."""
    original = _ok([])
    result = check_output_columns(original, [OutputColumn(name="year", dtype="int")], "blue.select")
    assert result.status is ExecutionStatus.OK
    assert result.output == [[]]


def test_a_failed_result_passes_through_unchecked() -> None:
    """A call that already failed has no output to check."""
    failed = ExecutionResult(status=ExecutionStatus.FAILED, operator_id="blue.select")
    assert (
        check_output_columns(failed, [OutputColumn(name="year", dtype="int")], "blue.select")
        is failed
    )


def test_a_missing_declared_column_fails_recoverably() -> None:
    """A different operator might produce the column, so the run retries rather than ends."""
    result = check_output_columns(
        _ok([{"title": "a"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.kind is ErrorKind.RECOVERABLE
    assert "'year'" in result.error.message
    assert "title" in result.error.message


def test_a_column_missing_from_a_later_row_fails_recoverably() -> None:
    """Every row is checked, not only the first, so a ragged result cannot slip through."""
    result = check_output_columns(
        _ok([{"year": "1815"}, {"title": "a"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.kind is ErrorKind.RECOVERABLE


def test_a_mismatched_value_names_the_column_and_the_row() -> None:
    """The message a review turn reads has to say which value in which row was wrong."""
    result = check_output_columns(
        _ok([{"year": "1815"}, {"year": "K-5"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.kind is ErrorKind.RECOVERABLE
    assert "'year'" in result.error.message
    assert "'K-5'" in result.error.message
    assert "row 1" in result.error.message


@pytest.mark.parametrize(
    ("tables", "declared_columns"),
    [
        ([[{"year": "1815"}, {"year": "K-5"}]], [OutputColumn(name="year", dtype="int")]),
        ([[{"year": "1815"}]], [OutputColumn(name="year", dtype="int")]),
        (
            [[{"year": "1815", "charter": "maybe"}]],
            [OutputColumn(name="year", dtype="int"), OutputColumn(name="charter", dtype="bool")],
        ),
        ([[{"year": "1815"}], [{"year": "K-5"}]], [OutputColumn(name="year", dtype="int")]),
    ],
    ids=[
        "a-rejected-coercion",
        "an-accepted-coercion",
        "a-later-column-failing",
        "a-later-table-failing",
    ],
)
def test_the_column_check_never_writes_to_the_records_it_reads(
    tables: list[Data], declared_columns: list[OutputColumn]
) -> None:
    """A passthrough operator hands back its input node's own dicts, so writing a coerced
    value in place would retype a finished node's committed output from underneath it."""
    before = [[dict(record) for record in table] for table in tables]

    check_output_columns(_ok(*tables), declared_columns, "blue.select")

    assert tables == before


# --- a bundle holding more than one table ---------------------------------------------


def test_every_table_in_a_bundle_is_coerced() -> None:
    """Every table reaches the next node, so a declared column is coerced in each."""
    result = check_output_columns(
        _ok([{"year": "1815"}], [{"year": "1901"}, {"year": "1902"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.output == [[{"year": 1815}], [{"year": 1901}, {"year": 1902}]]
    assert result.row_count == 3


def test_a_column_missing_from_a_later_table_names_that_table() -> None:
    """The message says which table lacked the column, so a review turn can act on it."""
    result = check_output_columns(
        _ok([{"year": "1815"}], [{"title": "a"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.kind is ErrorKind.RECOVERABLE
    assert "'year'" in result.error.message
    assert "in table 1" in result.error.message


def test_a_mismatched_value_in_a_later_table_names_the_table_and_the_row() -> None:
    """Row numbers restart per table, so the message gives the table and the row."""
    result = check_output_columns(
        _ok([{"year": "1815"}], [{"year": "1901"}, {"year": "K-5"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert "'K-5'" in result.error.message
    assert "row 1" in result.error.message
    assert "in table 1" in result.error.message


def test_a_single_table_result_does_not_name_a_table() -> None:
    """With a single table there is nothing to disambiguate, so the message omits it."""
    result = check_output_columns(
        _ok([{"title": "a"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.error is not None
    assert "table" not in result.error.message


def test_an_empty_table_beside_a_full_one_passes() -> None:
    """An empty table has no columns to check, and stays in place in the bundle."""
    result = check_output_columns(
        _ok([], [{"year": "1815"}]),
        [OutputColumn(name="year", dtype="int")],
        "blue.select",
    )
    assert result.status is ExecutionStatus.OK
    assert result.output == [[], [{"year": 1815}]]


def test_an_empty_bundle_passes_unchanged() -> None:
    """An empty bundle is a result with nothing in it, not a failed check."""
    result = check_output_columns(_ok(), [OutputColumn(name="year", dtype="int")], "blue.select")
    assert result.status is ExecutionStatus.OK
    assert result.output == []
    assert result.row_count == 0
