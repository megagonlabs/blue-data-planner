"""The command line: what it parses, and which library call each command reaches."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager

import click
from blue_data_planner import BlueDataPlannerError, DataPlanner, RunResult, RunStatus
from rich.console import Console

from blue_data_planner_cli import __version__
from blue_data_planner_cli.config import (
    OUTPUT_FORMATS,
    CliError,
    PlannerSettings,
    assemble_planner,
    build_resolver,
    export_secrets,
    resolve_settings,
)
from blue_data_planner_cli.output import error_line, print_answer, print_plan, summary_line
from blue_data_planner_cli.profile import profile_group
from blue_data_planner_cli.trace import print_trace, trace_payload

RUN_STATUS_CODES: dict[RunStatus, str] = {
    RunStatus.INVALID_PLAN: "INVALID_PLAN",
    RunStatus.REPLAN_EXHAUSTED: "REPLAN_EXHAUSTED",
    RunStatus.BUDGET_EXHAUSTED: "BUDGET_EXHAUSTED",
    RunStatus.OPERATOR_FAILED: "OPERATOR_FAILED",
    RunStatus.LLM_FAILED: "LLM_FAILED",
    RunStatus.PLANNER_FAILED: "PLANNER_FAILED",
}


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

# The one name a test replaces, so no test assembles a planner over a real registry.
PLANNER_FACTORY: Callable[[PlannerSettings], DataPlanner] = assemble_planner


@cli.group("data")
def data_group() -> None:
    """Ask a question in plain language over registered data."""


@data_group.command("query")
@click.argument("request")
@click.option("--registry", default=None, help="The blue-data registry to plan over.")
@click.option("--model", default=None, help="The litellm model string every component uses.")
@click.option("--format", "output_format", type=click.Choice(OUTPUT_FORMATS), default=None)
@click.option("--explain", is_flag=True, help="Write the plan and stop, running nothing.")
@click.option("--trace", is_flag=True, help="Report what the run did, after the run.")
@click.option(
    "--trace-max-fragment-rows",
    type=int,
    default=None,
    help="Rows of a fragment's result the trace shows.",
)
@click.option(
    "--trace-max-output-chars",
    type=int,
    default=None,
    help="Characters each line of a fragment's output is cut to.",
)
@click.pass_context
def data_query(
    ctx: click.Context,
    request: str,
    registry: str | None,
    model: str | None,
    output_format: str | None,
    explain: bool,
    trace: bool,
    trace_max_fragment_rows: int | None,
    trace_max_output_chars: int | None,
) -> None:
    """Plan the request, run it, and print the answer. With --explain, print the plan instead."""
    try:
        _run_query(
            ctx,
            request,
            registry,
            model,
            output_format,
            explain,
            trace,
            trace_max_fragment_rows,
            trace_max_output_chars,
        )
    except CliError as failure:
        _print_cli_error(Console(stderr=True), failure)
        ctx.exit(1)


def _run_query(
    ctx: click.Context,
    request: str,
    registry: str | None,
    model: str | None,
    output_format: str | None,
    explain: bool,
    trace: bool,
    trace_max_fragment_rows: int | None,
    trace_max_output_chars: int | None,
) -> None:
    resolver = build_resolver(ctx.obj["profile"], ctx.obj["environ"])
    # litellm and the provider SDKs read their keys from the process environment, so what a
    # profile resolved has to be applied to it before a planner makes its first call.
    os.environ.update(export_secrets(resolver))
    settings = resolve_settings(
        resolver, registry=registry, model=model, output_format=output_format
    )
    max_fragment_rows = _chosen(trace_max_fragment_rows, settings.trace_max_fragment_rows)
    max_output_chars = _chosen(trace_max_output_chars, settings.trace_max_output_chars)
    errors = Console(stderr=True)
    planner = PLANNER_FACTORY(settings)
    # The indicator is a terminal courtesy: a redirected stream gets none of it.
    with errors.status(f"Planning {request[:40]}…") if errors.is_terminal else _nothing():
        result = planner.plan(request) if explain else planner.run(request)
    printer = print_plan if explain else print_answer
    printer(result, settings.output_format, Console())
    if trace:
        _print_trace(errors, result, settings.output_format, max_fragment_rows, max_output_chars)
    errors.print(summary_line(result), markup=False)
    if result.status not in (RunStatus.OK, RunStatus.PLANNED):
        code = RUN_STATUS_CODES.get(result.status, "PLANNER_FAILED")
        errors.print(
            error_line(code, result.failure_reason or ""),
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
        ctx.exit(1)


def _chosen(given: int | None, configured: int) -> int:
    """Return the value the command was given, letting zero stand rather than the profile's."""
    return configured if given is None else given


def _print_trace(
    errors: Console,
    result: RunResult,
    output_format: str,
    max_fragment_rows: int,
    max_output_chars: int,
) -> None:
    if output_format == "json":
        payload = trace_payload(
            result, max_fragment_rows=max_fragment_rows, max_output_chars=max_output_chars
        )
        errors.print_json(data=payload)
        return
    print_trace(
        result, errors, max_fragment_rows=max_fragment_rows, max_output_chars=max_output_chars
    )


def _print_cli_error(errors: Console, failure: CliError) -> None:
    """Print one CliError in the one shape every Blue CLI reports a failure in."""
    errors.print(
        error_line(failure.code, failure.description), markup=False, highlight=False, soft_wrap=True
    )


@contextmanager
def _nothing() -> Generator[None]:
    yield


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
        errors.print(
            error_line("ABORTED", "the command was not confirmed"),
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
        sys.exit(1)
    except CliError as failure:
        _print_cli_error(errors, failure)
        sys.exit(1)
    except BlueDataPlannerError as defect:
        errors.print(
            error_line("INTERNAL_ERROR", str(defect)), markup=False, highlight=False, soft_wrap=True
        )
        sys.exit(1)
