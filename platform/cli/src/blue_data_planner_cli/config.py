"""What a profile supplies, and the planner those settings assemble."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs] (no py.typed)
    ConfigError,
    ConfigResolver,
    ConfigSpec,
    SecretError,
    base_spec,
    load_spec,
)
from blue_data.utils.config import (  # pyright: ignore[reportMissingTypeStubs]
    build_resolver as build_config_resolver,
)
from blue_data_planner import DataPlanner, RunLimits, build_data_planner
from blue_data_planner.data import open_registry
from blue_data_planner.operators import OperatorSuite, operator_suite
from blue_data_planner.operators.adapters.lotus import LotusSuite
from blue_data_planner.operators.builtin import BuiltinSuite
from blue_data_planner.types import UnknownOperatorSuiteError

CLI_HOME_VARIABLE: str = "BLUE_DATA_PLANNER_HOME"
DEFAULT_CLI_HOME: str = "~/.blue-planner"

_LLM_CLIENT_NAME: str = "blue_planner"

OUTPUT_FORMATS: tuple[str, ...] = ("table", "json")

REASONING_EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high", "none", "off")

# The one value that means "omit this parameter," for a model that rejects it. Distinct from
# "none", a real litellm reasoning_effort value that still has to reach the provider.
_REASONING_EFFORT_OFF: str = "off"

_SPEC_PATH: Path = Path(__file__).with_name("profile_spec.json")


def config_spec() -> ConfigSpec:
    """Return the family-wide parameters merged with this CLI's own."""
    return base_spec().merged_with(load_spec(_SPEC_PATH))


def cli_home(environ: dict[str, str]) -> str:
    """Return the directory holding this CLI's profiles."""
    return environ.get(CLI_HOME_VARIABLE, DEFAULT_CLI_HOME)


class CliError(Exception):
    """A failure the CLI reports as one ERROR line and a non-zero exit code."""

    def __init__(self, code: str, description: str) -> None:
        super().__init__(description)
        self.code = code
        self.description = description


@dataclass(frozen=True)
class ResolvedSettings:
    """Every value one invocation resolved, before anything is built or printed from them."""

    registry_path: Path
    model: str
    reasoning_effort: str | None
    operator_suites: tuple[str, ...]
    lotus_model: str
    lotus_reasoning_effort: str | None
    lotus_max_rows: int
    enable_nl2table: bool
    limits: RunLimits
    output_format: str
    log_limit: int


def build_resolver(profile: str | None, environ: dict[str, str]) -> ConfigResolver:
    """Return a resolver over this CLI's spec, its profiles, and the given environment."""
    return build_config_resolver(config_spec(), cli_home(environ), profile=profile, environ=environ)


def resolve_settings(
    resolver: ConfigResolver,
    registry: str | None = None,
    model: str | None = None,
    output_format: str | None = None,
    log_limit: int | None = None,
) -> ResolvedSettings:
    """Resolve every setting one invocation needs, raising CliError on the first that fails."""
    registry_path = Path(str(_require(resolver, "BLUE_DATA_REGISTRY_PATH", registry)))
    if not registry_path.is_file():
        raise CliError("REGISTRY_UNREADABLE", f"no registry file at {registry_path}")
    suites = tuple(
        name.strip()
        for name in str(_require(resolver, "BLUE_DATA_PLANNER_OPERATORS")).split(",")
        if name.strip()
    )
    planner_model = str(_require(resolver, "BLUE_DATA_PLANNER_MODEL", model))
    chosen_format = resolve_output_format(resolver, output_format)
    return ResolvedSettings(
        registry_path=registry_path,
        model=planner_model,
        reasoning_effort=_reasoning_effort(resolver, "BLUE_DATA_PLANNER_REASONING_EFFORT"),
        operator_suites=suites,
        lotus_model=str(_require(resolver, "BLUE_DATA_PLANNER_LOTUS_MODEL", model)),
        lotus_reasoning_effort=_reasoning_effort(
            resolver, "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT"
        ),
        lotus_max_rows=int(_get(resolver, "BLUE_DATA_PLANNER_LOTUS_MAX_ROWS")),
        enable_nl2table=bool(_get(resolver, "BLUE_DATA_PLANNER_ENABLE_NL2TABLE")),
        # The three bounds a run reports back are the three a profile sets. The rest reset
        # inside a request, so nothing reports what one spent against them, and they keep the
        # RunLimits defaults unless a library caller sets them.
        limits=RunLimits(
            operator_calls=int(_get(resolver, "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS")),
            failure_replans=int(_get(resolver, "BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS")),
            control_replans=int(_get(resolver, "BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS")),
        ),
        output_format=chosen_format,
        log_limit=int(_get(resolver, "BLUE_DATA_CLI_LOG_LIMIT", log_limit)),
    )


def _reasoning_effort(resolver: ConfigResolver, name: str) -> str | None:
    """Return the effort litellm should receive, or None where the parameter is left out."""
    configured = str(_require(resolver, name))
    if configured not in REASONING_EFFORTS:
        raise CliError(
            "CONFIG_INVALID",
            f"{name} names {configured}; expected one of {', '.join(REASONING_EFFORTS)}",
        )
    return None if configured == _REASONING_EFFORT_OFF else configured


def resolve_output_format(resolver: ConfigResolver, override: str | None = None) -> str:
    """Return the format in force for a command that prints a result."""
    chosen = str(_get(resolver, "BLUE_DATA_CLI_FORMAT", override))
    if chosen not in OUTPUT_FORMATS:
        raise CliError(
            "CONFIG_INVALID",
            f"BLUE_DATA_CLI_FORMAT names {chosen}; expected one of {', '.join(OUTPUT_FORMATS)}",
        )
    return chosen


def assemble_planner(settings: ResolvedSettings) -> DataPlanner:
    """Open the registry and build the planner over the suites this profile named."""
    suites = _suites(settings)
    try:
        data_registry = open_registry(settings.registry_path)
    except Exception as error:
        # A registry read reaches backends whose failures share no common type.
        raise CliError(
            "REGISTRY_UNREADABLE",
            f"cannot read the registry at {settings.registry_path}: {error}",
        ) from error
    return build_data_planner(
        data_registry,
        suites=suites,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        limits=settings.limits,
        llm_client_name=_LLM_CLIENT_NAME,
    )


def _suites(settings: ResolvedSettings) -> list[OperatorSuite]:
    """Return one suite per configured name, holding the values this profile resolved."""
    try:
        return [_configured(name, settings) for name in settings.operator_suites]
    except UnknownOperatorSuiteError as error:
        raise CliError("CONFIG_INVALID", str(error)) from error


def _configured(name: str, settings: ResolvedSettings) -> OperatorSuite:
    if name == "builtin":
        return BuiltinSuite(with_nl2table=settings.enable_nl2table)
    if name == "lotus":
        return LotusSuite(
            model=settings.lotus_model,
            max_rows=settings.lotus_max_rows,
            reasoning_effort=settings.lotus_reasoning_effort,
        )
    return operator_suite(name)


def _get(resolver: ConfigResolver, name: str, override: str | int | None = None) -> Any:
    # An override is resolved rather than returned, so a command-line path is expanded
    # the same way a profile one is.
    try:
        return resolver.get(name, override)
    except ConfigError as error:
        raise CliError("CONFIG_INVALID", str(error)) from error


def resolved_environment(resolver: ConfigResolver) -> dict[str, str]:
    """Return every resolved parameter as environment variables, raising CliError on a stored
    value that fails to resolve."""
    try:
        return resolver.export()
    except (ConfigError, SecretError) as error:
        raise CliError("CONFIG_INVALID", str(error)) from error


def _require(resolver: ConfigResolver, name: str, override: str | int | None = None) -> Any:
    value = _get(resolver, name, override)
    if value is None:
        raise CliError(
            "CONFIG_MISSING",
            f"{name} is not configured. Set it with `profile config {name}`, "
            f"or export {name} in the environment.",
        )
    return value
