"""The command line: what it parses, and which library call each command reaches."""

from __future__ import annotations

import os
import sys
from contextlib import nullcontext

import click
from blue_data_planner import BlueDataPlannerError, RunStatus
from rich.console import Console

from blue_data_planner_cli import __version__
from blue_data_planner_cli.config import (
    CliError,
    assemble_planner,
    build_resolver,
    export_secrets,
    resolve_settings,
)
from blue_data_planner_cli.output import (
    format_option,
    print_answer,
    print_error,
    print_plan,
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


@data_group.command("query")
@click.argument("request")
@click.option(
    "--registry-path", "registry", default=None, help="The blue-data registry to plan over."
)
@click.option("--model", default=None, help="The litellm model string every component uses.")
@format_option
@click.option("--explain", is_flag=True, help="Write the plan and stop, running nothing.")
@click.option("--log", is_flag=True, help="Report what the run did, after the run.")
@click.option(
    "--log-limit", type=int, default=None, help="Characters each line of the log is cut to."
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
    except CliError as failure:
        print_error(Console(stderr=True), failure.code, failure.description)
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
    # litellm and the provider SDKs read their keys from the process environment, so what a
    # profile resolved has to be applied to it before a planner makes its first call.
    os.environ.update(export_secrets(resolver))
    settings = resolve_settings(
        resolver,
        registry=registry,
        model=model,
        output_format=output_format,
        log_limit=log_limit,
    )
    errors = Console(stderr=True)
    planner = assemble_planner(settings)
    # The indicator is a terminal courtesy: a redirected stream gets none of it.
    with errors.status(f"Planning {request[:40]}…") if errors.is_terminal else nullcontext():
        result = planner.plan(request) if explain else planner.run(request)
    printer = print_plan if explain else print_answer
    printer(result, settings.output_format, Console(), log=log)
    # Under --format json the log is two more keys in the document on standard output, so
    # there is nothing left to draw here.
    if log and settings.output_format == "table":
        print_log(result, errors, max_output_chars=settings.log_limit)
    errors.print(summary_line(result), markup=False)
    if result.status not in (RunStatus.OK, RunStatus.PLANNED):
        # A status a run ended on is its own error code, so a status added to RunStatus
        # reports under its own name rather than under a plausible neighbor's.
        print_error(errors, result.status.name, result.failure_reason or "")
        ctx.exit(1)


def main() -> None:
    """Run the command line, reporting a failure as one ERROR line and exit code 1."""
    errors = Console(stderr=True)
    try:
        # Non-standalone mode returns the exit code instead of calling sys.exit itself,
        # so the return value has to be propagated explicitly.
        sys.exit(cli.main(standalone_mode=False) or 0)
    except click.UsageError as usage:  # Click's own exit code 2
        usage.show()
        sys.exit(2)
    except click.Abort:  # a confirmation the user declined
        print_error(errors, "ABORTED", "the command was not confirmed")
        sys.exit(1)
    except CliError as failure:
        print_error(errors, failure.code, failure.description)
        sys.exit(1)
    except BlueDataPlannerError as defect:
        print_error(errors, "INTERNAL_ERROR", str(defect))
        sys.exit(1)
