"""A PhysicalOperator whose callable records what execution passed it.

Lets a test assert the inputs and parameters an operator was called with, and script the
results or failures the operator hands back, without any real operator involved.
A scripted result is an OperatorOutput, the shape an operator returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from blue_data_planner.types import DataBundle, LogicalOperator, OperatorOutput, PhysicalOperator

# What a test scripts for one call: the output the operator returns, or the error it raises.
ScriptedResult = OperatorOutput | Exception
RecordedCall = tuple[DataBundle, dict[str, Any]]


class NoParameters(BaseModel):
    """A schema taking one optional key, so a test can pass a parameter or an unknown one."""

    # Unknown keys are refused, as every real operator schema refuses them, so a test can
    # exercise what happens when a caller writes a parameter the operator cannot take.
    model_config = ConfigDict(extra="forbid")

    key: str | None = None


@dataclass
class RecordingOperator:
    """Records every call and returns the results a test scripted, in order."""

    results: list[ScriptedResult] = field(default_factory=list[ScriptedResult])
    calls: list[RecordedCall] = field(default_factory=list[RecordedCall])

    def __call__(self, inputs: DataBundle, parameters: dict[str, Any]) -> OperatorOutput:
        self.calls.append((inputs, parameters))
        if not self.results:
            raise AssertionError("the recording operator ran out of scripted results")
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def recording_operator(
    operator_id: str,
    logical_operators: frozenset[LogicalOperator],
    results: list[ScriptedResult],
) -> tuple[PhysicalOperator, RecordingOperator]:
    """Return a physical operator backed by a recording callable, and the recorder itself."""
    recorder = RecordingOperator(results=list(results))
    declared = ", ".join(sorted(logical_op.value for logical_op in logical_operators))
    operator = PhysicalOperator(
        operator_id=operator_id,
        logical_operators=logical_operators,
        description=f"records the calls made for {declared}",
        parameter_schema=NoParameters,
        call=recorder,
    )
    return operator, recorder
