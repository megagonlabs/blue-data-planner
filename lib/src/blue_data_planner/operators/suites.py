"""An operator suite named, so a configured name reaches the operators it registers."""

from __future__ import annotations

from typing import ClassVar, Protocol

from blue_data_planner.data import DataRegistry
from blue_data_planner.llm import LLMClient
from blue_data_planner.types import PhysicalOperator, UnknownOperatorSuiteError

OPERATOR_SUITE_NAMES: tuple[str, ...] = ("builtin", "lotus", "blue")


class OperatorSuite(Protocol):
    """One suite and the options it registers with."""

    name: ClassVar[str]

    def operators(
        self, *, data_registry: DataRegistry, llm_client: LLMClient, model: str
    ) -> list[PhysicalOperator]:
        """Return the operators this suite registers, built with these options."""
        ...


def operator_suite(name: str) -> OperatorSuite:
    """Return the suite this name registers, with its default options."""
    # Each import sits inside the branch that needs it, so naming one suite never loads
    # another's dependency.
    if name == "builtin":
        from blue_data_planner.operators.builtin import BuiltinSuite

        return BuiltinSuite()
    if name == "lotus":
        from blue_data_planner.operators.adapters.lotus import LotusSuite

        return LotusSuite()
    if name == "blue":
        from blue_data_planner.operators.adapters.blue import BlueSuite

        return BlueSuite()
    raise UnknownOperatorSuiteError(
        f"no operator suite named {name!r}; expected one of {', '.join(OPERATOR_SUITE_NAMES)}"
    )
