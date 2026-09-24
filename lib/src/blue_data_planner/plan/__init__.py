"""Functions over a plan that need no model, no source, and no operator."""

from __future__ import annotations

from blue_data_planner.plan.fragments import candidate_fragments, fragment_frontier
from blue_data_planner.plan.schemas import (
    NONE,
    REUSE,
    OutputColumnModel,
    candidate_label,
    cover_choice_schema,
    logical_plan_schema,
)
from blue_data_planner.plan.signatures import signatures
from blue_data_planner.plan.validation import validate_logical_plan, validate_physical_plan

__all__ = [
    "NONE",
    "REUSE",
    "OutputColumnModel",
    "candidate_fragments",
    "candidate_label",
    "cover_choice_schema",
    "fragment_frontier",
    "logical_plan_schema",
    "signatures",
    "validate_logical_plan",
    "validate_physical_plan",
]
