"""The profile component: the six actions every Blue CLI shares."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import click
from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs] (no py.typed)
    DEFAULT_PROFILE,
    MASK,
    ConfigError,
    ConfigResolver,
    ParamSpec,
    ProfileError,
    ProfileStore,
    display,
    encode,
)
from rich.console import Console

from blue_data_planner_cli.config import (
    CliError,
    build_resolver,
    cli_home,
    config_spec,
    resolve_output_format,
)
from blue_data_planner_cli.output import format_option, print_json, print_table


@click.group("profile")
def profile_group() -> None:
    """Manage the named sets of configuration this CLI reads."""


@profile_group.command("add")
@click.argument("name")
@click.pass_context
def profile_add(ctx: click.Context, name: str) -> None:
    """Create a profile."""
    with _as_cli_error("CONFIG_INVALID"):
        _store(ctx).add(name)
    click.echo(f"Added profile: {name}")


@profile_group.command("remove")
@click.argument("name")
@click.option("--confirm", is_flag=True, help="Answer the confirmation ahead of time.")
@click.pass_context
def profile_remove(ctx: click.Context, name: str, confirm: bool) -> None:
    """Delete a profile and the values it holds."""
    if not confirm:
        click.confirm(f"Remove profile {name!r} and every value it holds?", abort=True)
    with _as_cli_error():
        _store(ctx).remove(name)
    click.echo(f"Removed profile: {name}")


@profile_group.command("list")
@format_option
@click.pass_context
def profile_list(ctx: click.Context, output_format: str | None) -> None:
    """Name every profile, marking the selected one."""
    store = _store(ctx)
    selected = store.selected()
    names = store.list_profiles() or [DEFAULT_PROFILE]
    if resolve_output_format(_resolver(ctx), output_format) == "json":
        print_json({"profiles": names, "selected": selected}, Console())
        return
    print_table(
        ("Profile", "Selected"),
        ((name, "*" if name == selected else "") for name in names),
        Console(),
    )


@profile_group.command("config")
@click.argument("param", required=False)
@click.argument("value", required=False)
@click.pass_context
def profile_config(ctx: click.Context, param: str | None, value: str | None) -> None:
    """Set one parameter, prompt for one, or walk every required parameter."""
    spec = config_spec()
    store = _store(ctx)
    with _as_cli_error("CONFIG_INVALID"):
        # The resolver decides which profile a read sees, so a write follows it.
        profile = _resolver(ctx).profile
        # Keyed on the file rather than the section, so a fresh install has a profile to
        # configure while one the user removed stays removed.
        store.initialize()
        asked = [spec[param]] if param else spec.required()
    if not store.exists(profile):
        raise CliError(
            "PROFILE_NOT_FOUND",
            f"no such profile: {profile!r}. Create it with `profile add {profile}`.",
        )
    for one in asked:
        stored = _store_parameter(store, profile, one, value if param else None)
        click.echo(f"{one.name}: {'set' if stored else 'left unchanged'}")


@profile_group.command("inspect")
@format_option
@click.pass_context
def profile_inspect(ctx: click.Context, output_format: str | None) -> None:
    """Every parameter, the value in force, and the tier that supplied it."""
    resolver = _resolver(ctx)
    with _as_cli_error():
        rows = [
            (one.name, _displayed_value(resolver, one), resolver.source_of(one.name))
            for one in config_spec()
        ]
    if resolve_output_format(resolver, output_format) == "json":
        keys = ("parameter", "value", "source")
        print_json([dict(zip(keys, row)) for row in rows], Console())
        return
    print_table(("Parameter", "Value", "Source"), rows, Console())


@profile_group.command("select")
@click.argument("name")
@click.pass_context
def profile_select(ctx: click.Context, name: str) -> None:
    """Make one profile the one later commands read."""
    store = _store(ctx)
    if not store.exists(name):
        raise CliError(
            "PROFILE_NOT_FOUND",
            f"no such profile: {name!r}. Create it with `profile add {name}`.",
        )
    with _as_cli_error():
        store.select(name)
    click.echo(f"Selected profile: {name}")


@contextmanager
def _as_cli_error(profile_code: str = "PROFILE_NOT_FOUND") -> Generator[None]:
    """Report the configuration engine's own failures under this CLI's error codes."""
    try:
        yield
    except ProfileError as error:
        raise CliError(profile_code, str(error)) from error
    except ConfigError as error:
        raise CliError("CONFIG_INVALID", str(error)) from error


def _store(ctx: click.Context) -> ProfileStore:
    return ProfileStore(cli_home(ctx.obj["environ"]))


def _resolver(ctx: click.Context) -> ConfigResolver:
    return build_resolver(ctx.obj["profile"], ctx.obj["environ"])


def _store_parameter(
    store: ProfileStore, profile: str, param: ParamSpec, value: str | None
) -> bool:
    """Store one parameter, prompting where no value was given, and say whether it was written."""
    if param.is_secret:
        # A secret on the command line reaches the shell history, so it is always prompted.
        given = click.prompt(
            param.prompt or param.name, hide_input=True, default="", show_default=False
        )
        if not given:
            return False
        with _as_cli_error():
            store.set_value(profile, param.name, encode(given))
        return True
    given = (
        value
        if value is not None
        else click.prompt(
            param.prompt or param.name, default=str(param.default or ""), show_default=True
        )
    )
    if given == "":
        return False
    with _as_cli_error():
        param.cast_value(given)  # raises ConfigError on a value the cast refuses
        store.set_value(profile, param.name, given)
    return True


def _displayed_value(resolver: ConfigResolver, param: ParamSpec) -> str:
    """The value inspect prints, masked where the parameter is a secret."""
    if param.is_secret:
        if resolver.source_of(param.name) == "environment":
            return MASK
        stored = resolver.profile_values().get(param.name)
        return display(stored, dict(resolver.environ)) if stored else "(unset)"
    value = resolver.get(param.name)
    return "(unset)" if value is None else str(value)
