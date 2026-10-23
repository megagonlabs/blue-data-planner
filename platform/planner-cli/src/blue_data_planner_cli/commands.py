"""The command line: what it parses, and which library call each command reaches."""

from __future__ import annotations

import os
import sys
import traceback
from contextlib import nullcontext
from typing import Any

import click
from blue_data_planner import RunStatus
from rich.console import Console

from blue_data_planner_cli import __version__
from blue_data_planner_cli.config import (
    CliError,
    assemble_planner,
    build_resolver,
    resolve_output_format,
    resolve_settings,
    resolved_environment,
)
from blue_data_planner_cli.output import (
    error_format,
    format_option,
    print_answer,
    print_error,
    print_plan,
    record_output_format,
    report_error,
    summary_line,
)
from blue_data_planner_cli.profile import profile_group
from blue_data_planner_cli.trace import print_log


@click.group()
@click.option(
    "--profile", "profile_name", default=None, help="Which profile supplies configuration."
)
@click.version_option(__version__, message="%(version)s")
@click.pass_context
def cli(ctx: click.Context, profile_name: str | None) -> None:
    """Plan and run a question in plain language over registered data."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile_name
    ctx.obj["environ"] = dict(os.environ)


cli.add_command(profile_group)


@cli.group("data")
def data_group() -> None:
    """Ask a question in plain language over registered data."""


_QUERY_EPILOG = """REQUEST is the question, in plain language and self-contained.

\b
Examples:
  blue-planner data query "Which three countries do the most customers come from?"
  blue-planner data query "Which three countries do the most customers come from?" --explain
  blue-planner --profile staging data query "..." --log --format json
"""


@data_group.command("query", epilog=_QUERY_EPILOG)
@click.argument("request")
@click.option(
    "--registry-path",
    "registry",
    default=None,
    help="Open a blue-data file store or legacy snapshot. Overrides shared registry settings "
    "and BLUE_DATA_REGISTRY_PATH.",
)
@click.option(
    "--model",
    default=None,
    help="The litellm model string the planner uses. Overrides BLUE_DATA_PLANNER_MODEL.",
)
@format_option
@click.option(
    "--explain", is_flag=True, help="Write the plan and stop before the first operator call."
)
@click.option("--log", is_flag=True, help="Report what the run did, after the run.")
@click.option(
    "--log-limit",
    type=int,
    default=None,
    help="Characters each line of the log is cut to. Default: BLUE_DATA_CLI_LOG_LIMIT, else 80.",
)
@click.pass_context
def data_query(
    ctx: click.Context,
    request: str,
    registry: str | None,
    model: str | None,
    output_format: str | None,
    explain: bool,
    log: bool,
    log_limit: int | None,
) -> None:
    """Plan the request, run it, and print the answer. With --explain, print the plan instead."""
    try:
        _run_query(ctx, request, registry, model, output_format, explain, log, log_limit)
    except CliError as error:
        report_error(error.code, error.description, error_format(ctx.obj))
        ctx.exit(1)
    except click.exceptions.Exit:
        # ctx.exit raises Exit, which is an Exception too; a run that ended on a failure status
        # has already printed its own error line.
        raise
    except Exception as error:  # a defect; --log is the one switch that asks for its traceback
        report_error("INTERNAL_ERROR", _described(error), error_format(ctx.obj))
        if log:
            click.echo(traceback.format_exc(), err=True, nl=False)
        ctx.exit(1)


def _run_query(
    ctx: click.Context,
    request: str,
    registry: str | None,
    model: str | None,
    output_format: str | None,
    explain: bool,
    log: bool,
    log_limit: int | None,
) -> None:
    resolver = build_resolver(ctx.obj["profile"], ctx.obj["environ"])
    # Resolved before anything else can fail, so a format a profile sets reaches every error
    # after this point.
    record_output_format(ctx, resolve_output_format(resolver, output_format))
    # litellm and the provider SDKs read their keys from the process environment, so what a
    # profile resolved has to be applied to it before a planner makes its first call.
    os.environ.update(resolved_environment(resolver))
    settings = resolve_settings(
        resolver,
        registry=registry,
        model=model,
        output_format=output_format,
        log_limit=log_limit,
    )
    stderr_console = Console(stderr=True)
    planner = assemble_planner(settings)
    # The indicator is a terminal courtesy: a redirected stream gets none of it.
    with (
        stderr_console.status(f"Planning {request[:40]}…")
        if stderr_console.is_terminal
        else nullcontext()
    ):
        result = planner.plan(request) if explain else planner.run(request)
    printer = print_plan if explain else print_answer
    printer(result, settings.output_format, Console(), log=log)
    # Under --format json the log is the plan versions added to the document on standard
    # output, so there is nothing left to draw here.
    if log and settings.output_format == "table":
        print_log(result, stderr_console, max_output_chars=settings.log_limit)
    stderr_console.print(summary_line(result), markup=False)
    if result.status not in (RunStatus.OK, RunStatus.PLANNED):
        # A status a run ended on is its own error code, so a status added to RunStatus
        # reports under its own name rather than under a plausible neighbor's.
        print_error(stderr_console, result.status.name, result.failure_reason or "")
        ctx.exit(1)


def main() -> None:
    """Run the command line, reporting a failure as one ERROR line and exit code 1."""
    stderr_console = Console(stderr=True)
    # The object every command's context shares, and where a command records its format, so
    # a failure reaching this handler is written in the format the command was writing.
    state: dict[str, Any] = {}
    try:
        # Non-standalone mode returns the exit code instead of calling sys.exit itself,
        # so the return value has to be propagated explicitly.
        sys.exit(cli.main(standalone_mode=False, obj=state) or 0)
    except click.UsageError as error:  # Click's own exit code 2
        error.show()
        sys.exit(2)
    except click.Abort:  # a confirmation the user declined
        print_error(stderr_console, "ABORTED", "the command was not confirmed")
        sys.exit(1)
    except CliError as error:
        report_error(error.code, error.description, error_format(state))
        sys.exit(1)
    except Exception as error:  # a defect in a command with no --log: the error line alone
        report_error("INTERNAL_ERROR", _described(error), error_format(state))
        sys.exit(1)


def _described(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"
