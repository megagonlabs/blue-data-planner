"""A Blue operation whose result a test scripts, and a registry the whole suite resolves against.

`blue_operators` resolves all eighteen ids, so a test that wants one scripted operation still needs
a registry answering for the other seventeen. `a_registry_answering_every_operation` builds one.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from blue_data_ops.operators.operator import Operator
from blue_data_ops.operators.registry import OperatorRegistry
from blue_data_ops.operators.types import (
    Attributes,
    AttributeSpec,
    DataBundle,
    FunctionResult,
    Properties,
)

# The eighteen operations design/06 names, under the names blue-data-ops registers them by.
OPERATION_NAMES: tuple[str, ...] = (
    "select",
    "project",
    "join",
    "intersect",
    "union",
    "sum",
    "avg",
    "min",
    "max",
    "argmax",
    "argmin",
    "semantic_filter",
    "semantic_project",
    "semantic_extract",
    "semantic_transform",
    "semantic_join",
    "nl2llm",
    "nl2sql",
)

# Both versions the adapter binds, so a registry built here answers whichever a row names.
_VERSIONS: tuple[str, ...] = ("2.0.0", "3.0.0")


class ScriptedOperation(Operator):
    """Returns the result a test scripted, or fails as scripted, recording what it was handed."""

    name: ClassVar[str] = "scripted"
    description: ClassVar[str] = "returns a scripted result"
    attributes: ClassVar[dict[str, AttributeSpec]] = {}

    def __init__(
        self,
        output: DataBundle | None = None,
        output_attributes: Attributes | None = None,
        failure: str | None = None,
    ) -> None:
        super().__init__()
        self.output: DataBundle = [[]] if output is None else output
        self.output_attributes: Attributes = output_attributes or {}
        self.failure: str | None = failure
        self.seen_inputs: DataBundle | None = None
        self.seen_attributes: Attributes | None = None
        self.seen_properties: Properties | None = None

    def apply(
        self,
        input_data: DataBundle,
        attributes: Attributes,
        properties: Properties | None = None,
    ) -> DataBundle:
        return self._execute(input_data, attributes, properties).output_data

    def _execute(
        self,
        input_data: DataBundle,
        attributes: Attributes,
        properties: Properties | None = None,
    ) -> FunctionResult:
        self.seen_inputs = input_data
        self.seen_attributes = attributes
        self.seen_properties = properties
        if self.failure is not None:
            # Operator.run turns any exception out of _execute into an EXECUTION_FAILED error,
            # which is how a test reaches the failure mapping through a real run.
            raise RuntimeError(self.failure)
        return FunctionResult(self.output, self.output_attributes)


def scripted_operation(
    name: str,
    output: DataBundle | None = None,
    output_attributes: Attributes | None = None,
    failure: str | None = None,
) -> ScriptedOperation:
    """Return a scripted operation the registry will file under one Blue operation's name."""
    # The registry files an operator under its class-level `name`, so one subclass per name is
    # what lets a scripted operation stand in for a real one.
    named = cast(
        "type[ScriptedOperation]",
        type(f"Scripted_{name}", (ScriptedOperation,), {"name": name}),
    )
    return named(output=output, output_attributes=output_attributes, failure=failure)


def a_registry_answering_every_operation(*scripted: ScriptedOperation) -> OperatorRegistry:
    """Return a registry where all eighteen operations resolve, using the scripted ones given."""
    by_name: dict[str, ScriptedOperation] = {one.name: one for one in scripted}
    unknown = sorted(set(by_name) - set(OPERATION_NAMES))
    if unknown:
        # A misspelled name would register under nothing the adapter resolves, leaving the test
        # asserting against an object the call never reached.
        raise ValueError(f"no Blue operation is registered under {unknown}")
    registry = OperatorRegistry()
    for name in OPERATION_NAMES:
        operation: Any = by_name.get(name) or scripted_operation(name)
        for version in _VERSIONS:
            registry.register(operation, version)
    return registry
