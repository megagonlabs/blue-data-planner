"""Functions over a plan that need no model, no source, and no operator."""

from __future__ import annotations

from blue_data_planner.plan.fragments import candidate_fragments
from blue_data_planner.plan.validation import validate_logical_plan, validate_physical_plan

__all__ = [
    "candidate_fragments",
    "validate_logical_plan",
    "validate_physical_plan",
]
