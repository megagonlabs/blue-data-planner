"""What a planner is built from, and the planner those values assemble."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from blue_data_planner.data import open_registry
from blue_data_planner.data_planner import (
    DEFAULT_LLM_CLIENT_NAME,
    DataPlanner,
    build_data_planner,
)
from blue_data_planner.operators import OperatorSuite, operator_suite
from blue_data_planner.types import InvalidSettingError, RunLimits

DEFAULT_MODEL: str = "openai/gpt-6-luna"
DEFAULT_OPERATOR_SUITES: tuple[str, ...] = ("builtin", "lotus")
REASONING_EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high", "none", "off")

# The one value that means "omit this parameter," for a model that rejects it. Distinct from
# "none", a real litellm reasoning_effort value that still has to reach the provider.
_REASONING_EFFORT_OFF: str = "off"


@dataclass(frozen=True)
class PlannerSettings:
    """Every value a planner is built from, and the default each one takes."""

    # None opens blue-data's shared store from the process environment.
    registry_path: Path | None = None
    model: str = DEFAULT_MODEL
    reasoning_effort: str = "low"
    operator_suites: tuple[str, ...] = DEFAULT_OPERATOR_SUITES
    # None runs the LOTUS suite on `model`.
    lotus_model: str | None = None
    lotus_reasoning_effort: str = "none"
    lotus_max_rows: int = 5000
    # None runs the Blue suite on `model`.
    blue_model: str | None = None
    blue_reasoning_effort: str = "none"
    provider_timeout_seconds: int = 120
    limits: RunLimits = RunLimits()

    def __post_init__(self) -> None:
        for field in ("reasoning_effort", "lotus_reasoning_effort", "blue_reasoning_effort"):
            value: str = getattr(self, field)
            if value not in REASONING_EFFORTS:
                raise InvalidSettingError(
                    f"{field} is {value!r}; expected one of {', '.join(REASONING_EFFORTS)}"
                )
        if self.provider_timeout_seconds <= 0:
            raise InvalidSettingError(
                f"provider_timeout_seconds is {self.provider_timeout_seconds}; expected a "
                "number of seconds above 0"
            )


def build_data_planner_from_settings(
    settings: PlannerSettings, *, llm_client_name: str = DEFAULT_LLM_CLIENT_NAME
) -> DataPlanner:
    """Open the registry these settings name and build a planner over the suites they list."""
    return build_data_planner(
        open_registry(settings.registry_path),
        suites=[_suite(name, settings) for name in settings.operator_suites],
        model=settings.model,
        reasoning_effort=_effort(settings.reasoning_effort),
        limits=settings.limits,
        llm_client_name=llm_client_name,
        provider_timeout_seconds=settings.provider_timeout_seconds,
    )


def _suite(name: str, settings: PlannerSettings) -> OperatorSuite:
    # Each import sits inside the branch that needs it, so naming one suite never loads
    # another's dependency.
    if name == "builtin":
        from blue_data_planner.operators.builtin import BuiltinSuite

        return BuiltinSuite()
    if name == "lotus":
        from blue_data_planner.operators.adapters.lotus import LotusSuite

        return LotusSuite(
            model=settings.lotus_model or settings.model,
            max_rows=settings.lotus_max_rows,
            reasoning_effort=_effort(settings.lotus_reasoning_effort),
        )
    if name == "blue":
        from blue_data_planner.operators.adapters.blue import BlueSuite

        return BlueSuite(
            model=settings.blue_model or settings.model,
            reasoning_effort=_effort(settings.blue_reasoning_effort),
        )
    return operator_suite(name)


def _effort(configured: str) -> str | None:
    return None if configured == _REASONING_EFFORT_OFF else configured
