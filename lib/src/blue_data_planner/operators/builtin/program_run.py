"""Running one written program where it reaches nothing but the tables it was given."""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import types
from collections.abc import Iterator
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

# Modules that compute and offer no way out of the process. A module reaching a file, a socket, a
# subprocess, or the interpreter's own state is absent by construction rather than by omission.
ALLOWED_IMPORTS = frozenset(
    {
        "bisect",
        "collections",
        "datetime",
        "decimal",
        "fractions",
        "functools",
        "heapq",
        "itertools",
        "json",
        "math",
        "operator",
        "re",
        "statistics",
        "string",
        "unicodedata",
    }
)

# Names that turn a restricted namespace back into an unrestricted one, whether named directly,
# imported under an alias (from operator import attrgetter as ag), or reached as an attribute
# (operator.attrgetter).
FORBIDDEN_NAMES = frozenset(
    {
        "attrgetter",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "methodcaller",
        "open",
        "setattr",
        "vars",
        "__import__",
        "__builtins__",
    }
)

# attrgetter and methodcaller do the same by-name attribute and method lookup getattr does
# (operator.attrgetter("__class__") reaches what getattr(x, "__class__") would). Checked
# separately from FORBIDDEN_NAMES so this narrower set never catches an unrelated, safe attribute
# of an allowed module that happens to share a name with a forbidden builtin (re.compile, for
# instance, which is re's own ordinary entry point, not the interpreter's compile()). Gates both
# the AST check below and the runtime wrapper around an imported module further down.
FORBIDDEN_ATTRIBUTES = frozenset({"attrgetter", "methodcaller"})


def _is_dunder_name(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


RESULT_NAME = "result"


def check_program(code: str) -> str | None:
    """Return why the program will not be run, or None when it may be."""
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return f"the program has a syntax error: {error}"
    for node in _nodes_in_evaluation_order(tree):
        refusal = _refusal(node)
        if refusal is not None:
            return refusal
    if not _assigns_result(tree):
        return f"the program assigns no module-level {RESULT_NAME!r}, so it produces no table"
    return None


def _nodes_in_evaluation_order(root: ast.AST) -> Iterator[ast.AST]:
    # Post-order: a node's children come from it before it comes from itself, which is also the
    # order a chain such as `x.__class__.__base__` is evaluated in. ast.walk visits breadth-first
    # instead, so it would name the outermost attribute of such a chain rather than the first one
    # the program actually reaches. Written as an explicit stack rather than recursion, since the
    # program being walked is untrusted and a deeply nested expression must not raise
    # RecursionError out of a function whose whole job is answering a refusal string.
    to_visit: list[ast.AST] = [root]
    visited: list[ast.AST] = []
    while to_visit:
        node = to_visit.pop()
        visited.append(node)
        to_visit.extend(ast.iter_child_nodes(node))
    yield from reversed(visited)


def _refusal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                return f"the program imports {alias.name!r}, which it may not reach"
    if isinstance(node, ast.ImportFrom):
        module = (node.module or "").split(".")[0]
        if module not in ALLOWED_IMPORTS:
            return f"the program imports from {node.module!r}, which it may not reach"
        for alias in node.names:
            # What is imported is what is dangerous, whatever local name it is bound to; an
            # alias does not make attrgetter safe.
            if alias.name in FORBIDDEN_NAMES:
                return f"the program imports {alias.name!r}, which it may not use"
    if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
        return f"the program names {node.id!r}, which it may not use"
    if isinstance(node, ast.Attribute) and (
        node.attr in FORBIDDEN_ATTRIBUTES or _is_dunder_name(node.attr)
    ):
        return f"the program reads the attribute {node.attr!r}, which it may not use"
    return None


def _assigns_result(tree: ast.Module) -> bool:
    for statement in tree.body:
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign | ast.AugAssign):
            targets = [statement.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == RESULT_NAME:
                return True
    return False


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
    """Check the program, run it where it reaches nothing else, and read back its table."""
    refusal = check_program(code)
    if refusal is not None:
        return ProgramRun(table=None, error=refusal)
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
    namespace["__builtins__"] = _safe_builtins()
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
    # schema is a model class for most callers, Predicate's union of four for filter_rows, and
    # JoinKind's two-way string literal for join_tables's how; TypeAdapter validates all three
    # shapes the same way, and raises the same way on a value such as how="outer" that fits none
    # of them, rather than letting it fall through to inner-join behavior unnoticed.
    return TypeAdapter(schema).validate_python(value)


def _safe_builtins() -> dict[str, Any]:
    import builtins

    permitted = (
        "abs all any bool dict divmod enumerate filter float frozenset int isinstance iter len "
        "list map max min next pow range repr reversed round set slice sorted str sum tuple type "
        "zip Exception KeyError IndexError TypeError ValueError ZeroDivisionError"
    ).split()
    safe: dict[str, Any] = {name: getattr(builtins, name) for name in permitted}
    # Without a real __import__, the interpreter's own import machinery cannot run at all, and
    # check_program has already refused anything outside ALLOWED_IMPORTS at the AST level; this is
    # what lets that refusal apply to a program the interpreter is now actually executing.
    safe["__import__"] = _restricted_import
    return safe


class _RestrictedModule:
    """An imported module, exposing only what neither nests nor reflects.

    An allowed module can itself hold a real module as one of its own attributes — `re` holds
    `enum`, since it uses `enum.Flag` internally — or a function that performs its own attribute
    lookup by name, such as `operator.attrgetter`. Both reach whatever the real interpreter can
    reach, regardless of what `check_program` refused in the source it read, since neither one is
    an import statement or a literal dunder attribute the walk would see. Wrapping the module a
    program receives closes both routes at the point they would actually be used: an attribute
    access is refused if it names a dunder, names one of FORBIDDEN_ATTRIBUTES, or would return
    another module.
    """

    def __init__(self, module: types.ModuleType) -> None:
        object.__setattr__(self, "_wrapped", module)

    def __getattribute__(self, name: str) -> Any:
        if name == "_wrapped" or name in FORBIDDEN_ATTRIBUTES or _is_dunder_name(name):
            raise AttributeError(name)
        value = getattr(object.__getattribute__(self, "_wrapped"), name)
        if isinstance(value, types.ModuleType):
            raise AttributeError(name)
        return value


def _restricted_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    import builtins

    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"{name!r} may not be imported here")
    return _RestrictedModule(builtins.__import__(name, globals, locals, fromlist, level))


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
