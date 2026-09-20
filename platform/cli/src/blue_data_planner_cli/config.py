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
from blue_data_ops.operators.registry import (  # pyright: ignore[reportMissingTypeStubs]
    default_registry,
)
from blue_data_planner import DataPlanner, RunLimits, build_data_planner
from blue_data_planner.data import DataCatalog, open_registry
from blue_data_planner.llm import LLMClient
from blue_data_planner.operators import OperatorRouter
from blue_data_planner.operators.adapters.blue import blue_operators
from blue_data_planner.operators.adapters.lotus import lotus_operators
from blue_data_planner.operators.builtin import builtin_operators
from blue_data_planner.types import PhysicalOperator

CLI_HOME_VARIABLE: str = "BLUE_DATA_PLANNER_HOME"
DEFAULT_CLI_HOME: str = "~/.blue-planner"

OPERATOR_SUITES: tuple[str, ...] = ("builtin", "lotus", "blue")

_LLM_CLIENT_NAME: str = "blue_planner"

# The one value that means "omit this parameter," for a model that rejects it. Distinct from
# "none", a real litellm reasoning_effort value that still has to reach the provider.
_REASONING_EFFORT_OFF: str = "off"

OUTPUT_FORMATS: tuple[str, ...] = ("table", "json")

_LIMIT_FIELDS: tuple[str, ...] = (
    "operator_calls",
    "plan_corrections",
    "parameter_repairs",
    "cover_repairs",
    "failure_replans",
    "control_replans",
)

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
class PlannerSettings:
    """Every value one invocation resolved, before anything is built from them."""

    registry_path: Path
    model: str
    reasoning_effort: str
    operator_suites: tuple[str, ...]
    lotus_model: str
    lotus_reasoning_effort: str
    lotus_max_rows: int
    enable_nl2table: bool
    limits: RunLimits
    output_format: str
    trace_max_fragment_rows: int
    trace_max_output_chars: int


def _effective_reasoning_effort(value: str) -> str | None:
    """Return the value litellm should receive, or None to omit the parameter."""
    return value if value and value != _REASONING_EFFORT_OFF else None


def build_resolver(profile: str | None, environ: dict[str, str]) -> ConfigResolver:
    """Return a resolver over this CLI's spec, its profiles, and the given environment."""
    return build_config_resolver(config_spec(), cli_home(environ), profile=profile, environ=environ)


def resolve_settings(
    resolver: ConfigResolver, registry: str | None = None, model: str | None = None
) -> PlannerSettings:
    """Resolve every setting one invocation needs, raising CliError on the first that fails."""
    registry_path = Path(str(_require(resolver, "BLUE_DATA_REGISTRY_PATH", registry)))
    if not registry_path.is_file():
        raise CliError("REGISTRY_UNREADABLE", f"no registry file at {registry_path}")
    suites = tuple(
        name.strip()
        for name in str(_require(resolver, "BLUE_DATA_PLANNER_OPERATORS")).split(",")
        if name.strip()
    )
    unknown = [name for name in suites if name not in OPERATOR_SUITES]
    if unknown:
        raise CliError(
            "CONFIG_INVALID",
            f"BLUE_DATA_PLANNER_OPERATORS names {', '.join(unknown)}; "
            f"expected one or more of {', '.join(OPERATOR_SUITES)}",
        )
    planner_model = str(_require(resolver, "BLUE_DATA_PLANNER_MODEL", model))
    output_format = str(_get(resolver, "BLUE_DATA_CLI_FORMAT"))
    if output_format not in OUTPUT_FORMATS:
        raise CliError(
            "CONFIG_INVALID",
            f"BLUE_DATA_CLI_FORMAT names {output_format}; "
            f"expected one of {', '.join(OUTPUT_FORMATS)}",
        )
    return PlannerSettings(
        registry_path=registry_path,
        model=planner_model,
        reasoning_effort=str(_get(resolver, "BLUE_DATA_PLANNER_REASONING_EFFORT") or ""),
        operator_suites=suites,
        # --model names the model every component uses, and LOTUS_MODEL inherits
        # BLUE_DATA_PLANNER_MODEL, so its own resolution never sees the override.
        lotus_model=str(model or _get(resolver, "BLUE_DATA_PLANNER_LOTUS_MODEL") or planner_model),
        lotus_reasoning_effort=str(
            _get(resolver, "BLUE_DATA_PLANNER_LOTUS_REASONING_EFFORT") or ""
        ),
        lotus_max_rows=int(_get(resolver, "BLUE_DATA_PLANNER_LOTUS_MAX_ROWS")),
        enable_nl2table=bool(_get(resolver, "BLUE_DATA_PLANNER_ENABLE_NL2TABLE")),
        limits=RunLimits(
            **{
                field: int(_get(resolver, f"BLUE_DATA_PLANNER_MAX_{field.upper()}"))
                for field in _LIMIT_FIELDS
            }
        ),
        output_format=output_format,
        trace_max_fragment_rows=int(_get(resolver, "BLUE_DATA_CLI_TRACE_MAX_FRAGMENT_ROWS")),
        trace_max_output_chars=int(_get(resolver, "BLUE_DATA_CLI_TRACE_MAX_OUTPUT_CHARS")),
    )


def assemble_planner(settings: PlannerSettings) -> DataPlanner:
    """Open the registry, register the listed suites, and build the planner over them."""
    try:
        data_registry = open_registry(settings.registry_path)
    except Exception as error:
        # A registry read reaches backends whose failures share no common type.
        raise CliError(
            "REGISTRY_UNREADABLE",
            f"cannot read the registry at {settings.registry_path}: {error}",
        ) from error
    llm_properties: dict[str, Any] = {f"{_LLM_CLIENT_NAME}.model": settings.model}
    effort = _effective_reasoning_effort(settings.reasoning_effort)
    if effort is not None:
        llm_properties[f"{_LLM_CLIENT_NAME}.reasoning_effort"] = effort
    llm_client = LLMClient(name=_LLM_CLIENT_NAME, properties=llm_properties)
    router = OperatorRouter()
    for operator in _operators(settings, data_registry, llm_client):
        router.register(operator)
    return build_data_planner(router, DataCatalog(data_registry), llm_client, settings.limits)


def _operators(
    settings: PlannerSettings, data_registry: Any, llm_client: LLMClient
) -> list[PhysicalOperator]:
    operators: list[PhysicalOperator] = []
    for suite in settings.operator_suites:
        if suite == "builtin":
            operators.extend(
                builtin_operators(data_registry, llm_client, with_nl2table=settings.enable_nl2table)
            )
        elif suite == "lotus":
            # An empty list where LOTUS is not installed, which is how the extra is optional.
            operators.extend(
                lotus_operators(
                    settings.lotus_model,
                    settings.lotus_max_rows,
                    reasoning_effort=_effective_reasoning_effort(settings.lotus_reasoning_effort),
                )
            )
        elif suite == "blue":
            operators.extend(blue_operators(default_registry(), data_registry))
    return operators


def _get(resolver: ConfigResolver, name: str, override: str | None = None) -> Any:
    # An override is resolved rather than returned, so a command-line path is expanded
    # the same way a profile one is.
    try:
        return resolver.get(name, override)
    except ConfigError as error:
        raise CliError("CONFIG_INVALID", str(error)) from error


def export_secrets(resolver: ConfigResolver) -> dict[str, str]:
    """Write every resolved parameter into the real process environment, raising CliError on
    a stored value that fails to resolve."""
    try:
        return resolver.export()
    except (ConfigError, SecretError) as error:
        raise CliError("CONFIG_INVALID", str(error)) from error


def _require(resolver: ConfigResolver, name: str, override: str | None = None) -> Any:
    value = _get(resolver, name, override)
    if value is None:
        raise CliError(
            "CONFIG_MISSING",
            f"{name} is not configured. Set it with `profile config {name}`, "
            f"or export {name} in the environment.",
        )
    return value
