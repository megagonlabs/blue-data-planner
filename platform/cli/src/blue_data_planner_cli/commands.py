"""The command line: what it parses, and which library call each command reaches."""

from __future__ import annotations

import os

import click

from blue_data_planner_cli import __version__
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
