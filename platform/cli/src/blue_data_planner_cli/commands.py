"""The command line: what it parses, and which library call each command reaches."""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from contextlib import contextmanager

import click
from blue_data_planner import DataPlanner, RunResult, RunStatus
from rich.console import Console

from blue_data_planner_cli import __version__
from blue_data_planner_cli.config import (
    PlannerSettings,
    assemble_planner,
    build_resolver,
    resolve_settings,
)
from blue_data_planner_cli.output import print_answer, summary_line, trace_lines, trace_payload
from blue_data_planner_cli.profile import profile_group


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
@click.option("--format", "output_format", type=click.Choice(["table", "json"]), default=None)
@click.option("--log", is_flag=True, help="Print the run's trace after the run.")
@click.option("--log-limit", type=int, default=None, help="Truncate each trace line.")
@click.pass_context
def data_query(
    ctx: click.Context,
    request: str,
    registry: str | None,
    model: str | None,
    output_format: str | None,
    log: bool,
    log_limit: int | None,
) -> None:
    """Plan the request, run it, and print the answer."""
    _run_query(ctx, "run", request, registry, model, output_format, log, log_limit)


def _run_query(
    ctx: click.Context,
    operation: str,
    request: str,
    registry: str | None,
    model: str | None,
    output_format: str | None,
    log: bool,
    log_limit: int | None,
) -> None:
    resolver = build_resolver(ctx.obj["profile"], ctx.obj["environ"])
    settings = resolve_settings(resolver, registry=registry, model=model)
    chosen_format = output_format or settings.output_format
    limit = log_limit or settings.log_limit
    errors = Console(stderr=True)
    planner = PLANNER_FACTORY(settings)
    # The indicator is a terminal courtesy: a redirected stream gets none of it.
    with errors.status(f"Planning {request[:40]}…") if errors.is_terminal else _nothing():
        result: RunResult = getattr(planner, operation)(request)
    print_answer(result, chosen_format, Console())
    if log:
        _print_trace(errors, result, chosen_format, limit)
    errors.print(summary_line(result), markup=False)
    if result.status not in (RunStatus.OK, RunStatus.PLANNED):
        ctx.exit(1)


def _print_trace(errors: Console, result: RunResult, output_format: str, limit: int) -> None:
    if output_format == "json":
        errors.print_json(data=trace_payload(result, limit))
        return
    for line in trace_lines(result, limit):
        errors.print(line, markup=False, highlight=False)


@contextmanager
def _nothing() -> Generator[None]:
    yield
