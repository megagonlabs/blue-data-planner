"""Checking and running one written program, where it reaches nothing but its tables."""

from __future__ import annotations

import pytest

from blue_data_planner.operators.builtin.program_run import ProgramRun, check_program, run_program
from blue_data_planner.types import RecoverableOperatorError

STORES = [{"store_id": 1, "city": "Reno"}, {"store_id": 2, "city": "Oakland"}]


def test_a_program_assigning_a_result_from_its_tables_is_allowed() -> None:
    """The ordinary shape passes: names in scope, a computation, an assignment to result."""
    code = "senior = [row for row in postings if row['level'] == 'senior']\nresult = senior"

    assert check_program(code) is None


def test_a_program_importing_something_that_only_computes_is_allowed() -> None:
    """A module with no way out of the process is on the list."""
    assert check_program("import math\nresult = [{'x': math.sqrt(4)}]") is None


def test_a_program_calling_an_allowed_modules_ordinary_entry_point_is_allowed() -> None:
    """re.compile is re's own safe entry point, not the interpreter's compile(), and stays usable
    even though attrgetter and methodcaller are refused as attributes of the same kind of module."""
    code = "import re\nresult = [{'m': re.compile('a').match('a') is not None}]"

    assert check_program(code) is None

    run = run_program(code, {})

    assert run == ProgramRun(table=[{"m": True}], error=None)


def test_a_program_reaching_a_submodule_of_an_allowed_module_is_refused_at_runtime() -> None:
    """re.enum is a real module reachable as re's own attribute; check_program's static walk has
    no rule against a plain, non-dunder attribute name, so the runtime boundary is what refuses
    it."""
    code = "import re\nresult = [{'x': str(type(re.enum))}]"

    assert check_program(code) is None  # the static walk has nothing to say about this

    run = run_program(code, {})

    assert run.table is None
    assert run.error is not None
    assert "enum" in run.error


def test_a_program_may_use_a_class_imported_from_an_allowed_module() -> None:
    """Counter is a class, not a module, so the runtime wrapper hands it back unwrapped."""
    code = "from collections import Counter\nresult = [{'n': Counter([1, 1, 2])[1]}]"

    run = run_program(code, {})

    assert run == ProgramRun(table=[{"n": 2}], error=None)


@pytest.mark.parametrize(
    ("code", "refusal"),
    [
        ("import os\nresult = []", "os"),
        ("from subprocess import run\nresult = []", "subprocess"),
        ("result = eval('1')", "eval"),
        ("result = open('/etc/passwd').read()", "open"),
        ("result = getattr(postings, 'clear')()", "getattr"),
        ("result = [].__class__.__base__.__subclasses__()", "__class__"),
        ("import operator\nresult = operator.attrgetter('__class__')([])", "attrgetter"),
        ("from operator import attrgetter as ag\nresult = ag('__class__')([])", "attrgetter"),
        ("from operator import methodcaller as mc\nresult = []", "methodcaller"),
        ("import string\nresult = string.Formatter().get_field('x', (), {})", "get_field"),
    ],
)
def test_a_program_reaching_outside_its_tables_is_refused(code: str, refusal: str) -> None:
    """Every route out of the given names is refused before anything runs, whatever local name it
    is imported under."""
    reason = check_program(code)

    assert reason is not None
    assert refusal in reason


def test_check_program_does_not_raise_on_a_deeply_nested_expression() -> None:
    """An adversarially deep attribute chain is walked with an explicit stack, not recursion, so
    it cannot crash the check with a RecursionError of its own."""
    code = "result = x" + ".a" * 5000

    assert check_program(code) is None


def test_a_program_that_assigns_no_result_is_refused() -> None:
    """Nothing would come back, so the program is refused rather than run and read."""
    reason = check_program("answer = [{'a': 1}]")

    assert reason is not None
    assert "result" in reason


def test_a_program_that_will_not_parse_is_refused_with_the_syntax_error() -> None:
    """A syntax error is the check's answer rather than an exception the caller handles."""
    reason = check_program("result = [")

    assert reason is not None
    assert "syntax" in reason.lower()


def test_a_programs_result_comes_back_as_the_table_it_assigned() -> None:
    """The rows the program put in result are the rows the caller gets."""
    run = run_program(
        "result = [row for row in stores if row['city'] == 'Reno']", {"stores": STORES}
    )

    assert run == ProgramRun(table=[{"store_id": 1, "city": "Reno"}], error=None)


def test_a_program_may_import_an_allowed_module() -> None:
    """An import on the allowed list actually resolves when the program runs, not just when
    checked: the check and the run agree on what an import may reach."""
    run = run_program("import math\nresult = [{'root': math.sqrt(4)}]", {})

    assert run == ProgramRun(table=[{"root": 2.0}], error=None)


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
        ("import os\nresult = []", "os"),
        ("result = [", "syntax"),
        ("result = 3", "table"),
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
