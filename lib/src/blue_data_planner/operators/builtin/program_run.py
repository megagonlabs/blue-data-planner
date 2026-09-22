"""Running one written program over copies of the tables it was given."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter

from blue_data_planner.operators.builtin import table_transformations
from blue_data_planner.operators.builtin.schemas import (
    Aggregation,
    JoinKey,
    JoinKind,
    Predicate,
    ProjectedColumn,
    SortKey,
)
from blue_data_planner.types import Data, RecoverableOperatorError

# The program a model wrote runs with the interpreter's own builtins and whatever it imports. No
# check stands in front of it, deliberately: an AST walk cannot police a language that resolves
# names at run time, and one that looks like it can is worse than none, because what rests on it
# is written as though it held (ADR-020). The child process bounds a program that is wrong, not
# one that is hostile.
RESULT_NAME = "result"


# Bounds on one program, set to keep a run finite rather than measured against real work
# (open question 45). The child enforces the first two where the platform offers them; the
# parent enforces the wall clock everywhere.
ADDRESS_SPACE_BYTES = 2 * 1024 * 1024 * 1024
CPU_SECONDS = 30
WALL_CLOCK_SECONDS = 60.0


@dataclass(frozen=True)
class ProgramRun:
    """The table one program produced, or the reason it produced none."""

    table: Data | None
    error: str | None


def run_program(
    code: str, tables: dict[str, Data], timeout_seconds: float = WALL_CLOCK_SECONDS
) -> ProgramRun:
    """Run the program in a child process and read back the table it assigned."""
    try:
        request = json.dumps({"code": code, "tables": tables})
    except (TypeError, ValueError) as error:
        # Raised rather than reported as a program failure: the table was wrong before any
        # program was written, and no new program would fix it. Converting the value instead
        # would change what a source said behind the caller's back.
        raise RecoverableOperatorError(_unsendable_value_message(tables, error)) from error
    return _reply_of(_child_output(request, timeout_seconds))


def _unsendable_value_message(tables: dict[str, Data], error: Exception) -> str:
    for table_name, rows in tables.items():
        for row in rows:
            for column, value in row.items():
                try:
                    json.dumps(value)
                except (TypeError, ValueError) as column_error:
                    return (
                        f"table {table_name!r} column {column!r} holds a value that cannot be "
                        f"sent to a program: {column_error}"
                    )
    # The failure was not localized to one column's value; something about the tables' own shape
    # (a non-string key, for instance) is what json.dumps actually objected to.
    return f"a table holds a value that cannot be sent to a program: {error}"


def _child_output(request: str, timeout_seconds: float) -> str:
    process = subprocess.Popen(
        [sys.executable, "-m", "blue_data_planner.operators.builtin.program_run"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # its own process group, so a timeout kills what it started
    )
    try:
        stdout, stderr = process.communicate(request, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill(process)
        process.communicate()
        return json.dumps({"error": f"the program did not finish in {timeout_seconds} seconds"})
    if process.returncode != 0 and not stdout:
        return json.dumps({"error": f"the program ended without an answer: {stderr.strip()}"})
    return stdout


def _kill(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, AttributeError):
        # No process groups on this platform, or the child is already gone.
        process.kill()


def _reply_of(stdout: str) -> ProgramRun:
    try:
        reply: Any = json.loads(stdout)
    except json.JSONDecodeError:
        return ProgramRun(
            table=None, error=f"the program answered with something unreadable: {stdout!r}"
        )
    if not isinstance(reply, dict):
        return ProgramRun(
            table=None, error=f"the program answered with something unreadable: {stdout!r}"
        )
    reply = cast("dict[str, Any]", reply)
    error = reply.get("error")
    if isinstance(error, str):
        return ProgramRun(table=None, error=error)
    return ProgramRun(table=_as_table(reply.get("table")), error=None)


def _as_table(value: Any) -> Data | None:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):  # pyright: ignore[reportUnknownVariableType]
        return cast("Data", value)
    return None


def _child_namespace(tables: dict[str, Data]) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "aggregate_rows": _wrapped_aggregate_rows,
        "distinct_rows": table_transformations.distinct_rows,
        "expand_rows": table_transformations.expand_rows,
        "filter_rows": _wrapped_filter_rows,
        "join_tables": _wrapped_join_tables,
        "project_columns": _wrapped_project_columns,
        "sort_rows": _wrapped_sort_rows,
        "union_tables": table_transformations.union_tables,
    }
    namespace.update(tables)
    return namespace


# A written program names a predicate, a projected column, an aggregation, a sort key, or a join
# key the way the prompt shows it and a fragment's own parameters arrive: a plain dict, not the
# pydantic model table_transformations.py itself takes. Each wrapper below parses that dict into
# the model the underlying function expects and then delegates; distinct_rows, expand_rows, and
# union_tables need no such wrapper, since none of their parameters are pydantic models.


def _wrapped_filter_rows(table: Data, predicate: Any) -> Data:
    return table_transformations.filter_rows(
        table, cast(Predicate, _validated(Predicate, predicate))
    )


def _wrapped_project_columns(table: Data, columns: Any) -> Data:
    return table_transformations.project_columns(
        table, [_validated(ProjectedColumn, column) for column in columns]
    )


def _wrapped_aggregate_rows(table: Data, group_by: list[str], aggregations: Any) -> Data:
    return table_transformations.aggregate_rows(
        table, group_by, [_validated(Aggregation, aggregation) for aggregation in aggregations]
    )


def _wrapped_sort_rows(table: Data, keys: Any, limit: int | None = None) -> Data:
    return table_transformations.sort_rows(table, [_validated(SortKey, key) for key in keys], limit)


def _wrapped_join_tables(left: Data, right: Data, on: Any, how: Any = "inner") -> Data:
    return table_transformations.join_tables(
        left,
        right,
        [_validated(JoinKey, key) for key in on],
        cast(JoinKind, _validated(JoinKind, how)),
    )


def _validated(schema: Any, value: Any) -> Any:
    # schema is a model class for most callers, Predicate's union of six for filter_rows, and
    # JoinKind's two-way string literal for join_tables's how; TypeAdapter validates all three
    # shapes the same way, and raises the same way on a value such as how="outer" that fits none
    # of them, rather than letting it fall through to inner-join behavior unnoticed.
    return TypeAdapter(schema).validate_python(value)


def _apply_limits() -> None:
    try:
        import resource
    except ImportError:
        return  # not a POSIX platform; the parent's wall clock is the only bound
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def _run_as_child() -> None:
    request: Any = json.loads(sys.stdin.read())
    namespace = _child_namespace(request["tables"])
    _apply_limits()
    try:
        exec(compile(request["code"], "<program>", "exec"), namespace)
    except BaseException as error:
        # A program's own exception, an interpreter-level exit, or a resource limit tripping all
        # look the same from here: something stopped the program before it produced a table. Every
        # one of them has to reach this reply rather than an ambiguous nonzero exit, which is why
        # this catches BaseException rather than Exception, the one other deliberate exception
        # to this repository's catch-specific-types rule (data/'s source-read handling is the
        # other).
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
        return
    table = namespace.get(RESULT_NAME)
    if not isinstance(table, list) or not all(isinstance(row, dict) for row in table):  # pyright: ignore[reportUnknownVariableType]
        print(json.dumps({"error": f"the program's {RESULT_NAME!r} is not a table of rows"}))
        return
    try:
        print(json.dumps({"table": table}))
    except (TypeError, ValueError) as error:
        print(
            json.dumps({"error": f"the program produced a value that cannot be returned: {error}"})
        )


if __name__ == "__main__":
    _run_as_child()
