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
from blue_data_planner import (
    DataPlanner,
    PlannerSettings,
    RunLimits,
    build_data_planner_from_settings,
)
from blue_data_planner.types import (
    InvalidSettingError,
    RegistryUnreadableError,
    UnknownOperatorSuiteError,
)

CLI_HOME_VARIABLE: str = "BLUE_DATA_PLANNER_HOME"
DEFAULT_CLI_HOME: str = "~/.blue-planner"

_LLM_CLIENT_NAME: str = "blue_planner"

# A PlannerSettings field, and the profile parameter that sets it. A rejected setting is reported
# by the library under its field name, which appears in no profile and no environment, so the CLI
# puts back the name its own user can act on.
_PROFILE_PARAMETERS_BY_FIELD: dict[str, str] = {
    "reasoning_effort": "BLUE_DATA_PLANNER_REASONING_EFFORT",
    "lotus_reasoning_effort": "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT",
}

OUTPUT_FORMATS: tuple[str, ...] = ("table", "json")

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

    planner: PlannerSettings
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
    planner_model = str(_require(resolver, "BLUE_DATA_PLANNER_MODEL", model))
    try:
        planner = PlannerSettings(
            registry_path=Path(str(_require(resolver, "BLUE_DATA_REGISTRY_PATH", registry))),
            model=planner_model,
            reasoning_effort=str(_require(resolver, "BLUE_DATA_PLANNER_REASONING_EFFORT")),
            operator_suites=tuple(
                name.strip()
                for name in str(_require(resolver, "BLUE_DATA_PLANNER_OPERATORS")).split(",")
                if name.strip()
            ),
            # Unset, LOTUS runs on the planner's model, so it follows --model; set, --model
            # leaves it alone, which is what setting it apart from the planner's model is for.
            lotus_model=_optional_text(resolver, "BLUE_DATA_PLANNER_LOTUS_MODEL"),
            lotus_reasoning_effort=str(
                _require(resolver, "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT")
            ),
            lotus_max_rows=int(_get(resolver, "BLUE_DATA_PLANNER_LOTUS_MAX_ROWS")),
            enable_nl2table=bool(_get(resolver, "BLUE_DATA_PLANNER_ENABLE_NL2TABLE")),
            # The three bounds a run reports back are the three a profile sets. The rest reset
            # inside a request, so nothing reports what one spent against them, and they keep
            # the RunLimits defaults unless a library caller sets them.
            limits=RunLimits(
                operator_calls=int(_get(resolver, "BLUE_DATA_PLANNER_MAX_OPERATOR_CALLS")),
                failure_replans=int(_get(resolver, "BLUE_DATA_PLANNER_MAX_FAILURE_REPLANS")),
                control_replans=int(_get(resolver, "BLUE_DATA_PLANNER_MAX_CONTROL_REPLANS")),
            ),
        )
    except InvalidSettingError as error:
        raise CliError("CONFIG_INVALID", _message_naming_the_profile_parameter(error)) from error
    return ResolvedSettings(
        planner=planner,
        output_format=resolve_output_format(resolver, output_format),
        log_limit=int(_get(resolver, "BLUE_DATA_CLI_LOG_LIMIT", log_limit)),
    )


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
    """Build the planner this invocation's settings describe."""
    try:
        return build_data_planner_from_settings(settings.planner, llm_client_name=_LLM_CLIENT_NAME)
    except RegistryUnreadableError as error:
        raise CliError("REGISTRY_UNREADABLE", str(error)) from error
    except (InvalidSettingError, UnknownOperatorSuiteError) as error:
        raise CliError("CONFIG_INVALID", str(error)) from error


def _message_naming_the_profile_parameter(error: InvalidSettingError) -> str:
    message = str(error)
    for field, parameter in _PROFILE_PARAMETERS_BY_FIELD.items():
        if message.startswith(f"{field} "):
            return parameter + message[len(field) :]
    return message


def _optional_text(resolver: ConfigResolver, name: str) -> str | None:
    value = _get(resolver, name)
    return None if value is None else str(value)


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
            "CONFIG_INVALID",
            f"{name} is not configured. Set it with `profile config {name}`, "
            f"or export {name} in the environment.",
        )
    return value
