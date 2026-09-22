"""Running one written program over copies of its tables."""

from __future__ import annotations

import os

import pytest

from blue_data_planner.operators.builtin.program_run import ProgramRun, run_program
from blue_data_planner.types import RecoverableOperatorError

STORES = [{"store_id": 1, "city": "Reno"}, {"store_id": 2, "city": "Oakland"}]


def test_a_programs_result_comes_back_as_the_table_it_assigned() -> None:
    """The rows the program put in result are the rows the caller gets."""
    run = run_program(
        "result = [row for row in stores if row['city'] == 'Reno']", {"stores": STORES}
    )

    assert run == ProgramRun(table=[{"store_id": 1, "city": "Reno"}], error=None)


def test_a_program_may_import_from_the_standard_library() -> None:
    """The child runs the program with the interpreter's own import machinery, so a module the
    program names resolves whatever it is."""
    run = run_program("import os\nresult = [{'sep': os.sep}]", {})

    assert run == ProgramRun(table=[{"sep": os.sep}], error=None)


def test_a_program_may_call_the_shared_transformations() -> None:
    """The transformations builtin's own entries wrap are in scope for the program too."""
    code = "result = aggregate_rows(stores, ['city'], [{'function': 'count', 'into': 'n'}])"
    run = run_program(code, {"stores": STORES})

    assert run.error is None
    assert run.table == [{"city": "Reno", "n": 1}, {"city": "Oakland", "n": 1}]


def test_a_program_may_call_filter_rows_with_a_plain_comparison_dict() -> None:
    """A single comparison dict is parsed into the Comparison branch of Predicate's union."""
    code = "result = filter_rows(stores, {'column': 'city', 'operator': 'eq', 'value': 'Reno'})"
    run = run_program(code, {"stores": STORES})

    assert run.error is None
    assert run.table == [{"store_id": 1, "city": "Reno"}]


def test_a_program_may_call_filter_rows_with_a_compound_predicate_dict() -> None:
    """An and/or/not predicate dict is parsed through the same union's other three branches."""
    code = (
        "result = filter_rows(stores, {'and': ["
        "{'column': 'city', 'operator': 'eq', 'value': 'Reno'}, "
        "{'column': 'store_id', 'operator': 'gt', 'value': 0}]})"
    )
    run = run_program(code, {"stores": STORES})

    assert run.error is None
    assert run.table == [{"store_id": 1, "city": "Reno"}]


def test_a_program_may_call_project_columns_with_plain_column_dicts() -> None:
    """A plain column dict is parsed into the ProjectedColumn project_columns expects."""
    run = run_program("result = project_columns(stores, [{'column': 'city'}])", {"stores": STORES})

    assert run.error is None
    assert run.table == [{"city": "Reno"}, {"city": "Oakland"}]


def test_a_program_may_call_sort_rows_with_plain_key_dicts() -> None:
    """A plain sort-key dict is parsed into the SortKey sort_rows expects."""
    code = "result = sort_rows(stores, [{'column': 'city', 'direction': 'desc'}], None)"
    run = run_program(code, {"stores": STORES})

    assert run.error is None
    assert run.table == [{"store_id": 1, "city": "Reno"}, {"store_id": 2, "city": "Oakland"}]


def test_a_program_may_call_join_tables_with_plain_key_dicts() -> None:
    """A plain join-key dict is parsed into the JoinKey join_tables expects."""
    code = (
        "result = join_tables(stores, stores, "
        "[{'left_column': 'store_id', 'right_column': 'store_id'}], 'inner')"
    )
    run = run_program(code, {"stores": STORES})

    assert run.error is None
    assert run.table is not None
    assert len(run.table) == 2


def test_join_tables_refuses_a_how_outside_inner_or_left() -> None:
    """An invalid how is refused rather than silently treated as an inner join."""
    code = (
        "result = join_tables(stores, stores, "
        "[{'left_column': 'store_id', 'right_column': 'store_id'}], 'outer')"
    )
    run = run_program(code, {"stores": STORES})

    assert run.table is None
    assert run.error is not None
    assert "outer" in run.error


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("result = stores[9]['city']", "IndexError"),
        ("result = [", "syntax"),
        ("result = 3", "table"),
        ("answer = [{'a': 1}]", "result"),
    ],
)
def test_a_program_that_produces_no_table_comes_back_as_the_reason(
    code: str, expected: str
) -> None:
    """Every way a program fails is one error string, whether it ran or never started."""
    run = run_program(code, {"stores": STORES})

    assert run.table is None
    assert run.error is not None
    assert expected.lower() in run.error.lower()


def test_a_program_that_will_not_stop_is_killed() -> None:
    """The parent enforces the wall clock, so a blocking program cannot outlive its call."""
    run = run_program("while True:\n    pass\nresult = []", {}, timeout_seconds=2.0)

    assert run.table is None
    assert run.error is not None
    assert "did not finish" in run.error


def test_a_table_holding_a_value_that_cannot_be_sent_refuses_the_call() -> None:
    """A value JSON cannot carry is refused rather than converted behind the caller's back, and
    the message names the column that holds it."""
    with pytest.raises(RecoverableOperatorError, match="cannot be sent") as excinfo:
        run_program("result = []", {"stores": [{"blob": b"\x00"}]})

    assert "blob" in str(excinfo.value)


def test_what_a_program_does_to_its_tables_leaves_the_callers_untouched() -> None:
    """The child holds copies, so a program that clears a table changes nothing here."""
    tables = {"stores": [dict(row) for row in STORES]}

    run = run_program("stores.clear()\nresult = stores", tables)

    assert run.table == []
    assert tables["stores"] == STORES
