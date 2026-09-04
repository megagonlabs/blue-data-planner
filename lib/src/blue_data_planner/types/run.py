"""What one request produces: execution outcomes, plan results, and the bounds that govern a run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from blue_data_planner.types.catalog import CatalogMatch
from blue_data_planner.types.errors import BlueDataPlannerError, BudgetExhaustedError
from blue_data_planner.types.plan import DataBundle, DataPipeline, LogicalPlan, PhysicalPlan


class ExecutionStatus(StrEnum):
    """Whether one operator call produced a result."""

    OK = "ok"
    FAILED = "failed"


class ErrorKind(StrEnum):
    """What a failure means for the run: try another operator, end the run, or replan."""

    RECOVERABLE = "recoverable"
    UNRECOVERABLE = "unrecoverable"
    NO_OPERATOR = "no_operator"


class RunStatus(StrEnum):
    """How one request ended, whether it produced an answer or not."""

    OK = "ok"
    PLANNED = "planned"
    INVALID_PLAN = "invalid_plan"
    REPLAN_EXHAUSTED = "replan_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    OPERATOR_FAILED = "operator_failed"
    LLM_FAILED = "llm_failed"


class FragmentReviewDecision(StrEnum):
    """What one review turn decided about one fragment's result."""

    ACCEPT = "accept"
    REJECT = "reject"
    REPLAN = "replan"


class RunDecision(StrEnum):
    """What the run loop does next after one fragment."""

    CONTINUE = "continue"
    ESCALATE = "escalate"
    REPLAN = "replan"
    STOP = "stop"


@dataclass(frozen=True)
class ErrorInfo:
    """Why one operator call failed, in language a review turn can read."""

    kind: ErrorKind
    message: str


@dataclass(frozen=True)
class ExecutionResult:
    """What one operator call produced, whether it worked or not."""

    status: ExecutionStatus
    output: DataBundle | None = None
    operator_id: str | None = None
    error: ErrorInfo | None = None
    # Rows across every table in `output`, not the number of tables.
    row_count: int | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    """What one DISCOVER node's catalog search found."""

    search_terms: list[str]
    matches: list[CatalogMatch]
    # Terms cut by the per-node cap, reported so a reader knows the search was capped.
    dropped_terms: int


@dataclass(frozen=True)
class LLMUsage:
    """One model call: what it cost and how long it took."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    latency_seconds: float


@dataclass(frozen=True)
class FragmentAttempt:
    """One try at one fragment, whether or not the review kept it."""

    physical_plan_id: str
    fragment_id: str
    logical_node_ids: list[str]
    operator_id: str
    operator_attributes: dict[str, Any]
    # None when the operator raised before returning anything.
    result: ExecutionResult | None
    assessment: str | None
    decision: FragmentReviewDecision


@dataclass(frozen=True)
class RejectedPlan:
    """One logical plan validation turned down, with what it objected to."""

    plan: LogicalPlan
    error: str


@dataclass(frozen=True)
class LogicalPlanResult:
    """What the logical planner produced, and every plan it had turned down on the way."""

    status: RunStatus  # ok, or invalid_plan when the corrections ran out
    plan: LogicalPlan | None
    rejected: list[RejectedPlan]
    usage: list[LLMUsage]


@dataclass(frozen=True)
class PhysicalPlanResult:
    """One cover of a logical plan, or the reason no cover could be written."""

    plan: PhysicalPlan | None  # None exactly when a node has no operator
    failure_reason: str | None
    usage: list[LLMUsage]


@dataclass(frozen=True)
class FragmentResult:
    """What one fragment's run came to, and what the run loop does next."""

    status: RunStatus
    decision: RunDecision
    attempts: list[FragmentAttempt]  # rejected ones included, in order
    failure_reason: str | None
    usage: list[LLMUsage]


@dataclass(frozen=True)
class CatalogSearchResult:
    """What one catalog search found, and what finding it cost."""

    # A pair rather than a field on DiscoveryResult: a DiscoveryResult goes into the run's
    # results map and reaches the caller inside PlanResult, where usage would be counted
    # a second time.
    discovery: DiscoveryResult
    usage: list[LLMUsage]


@dataclass(frozen=True)
class PlanResult:
    """Everything one request produced, whether it worked or not."""

    status: RunStatus
    # None rather than [] unless status is ok, so a caller who ignores the status cannot
    # mistake a failed run for an empty answer.
    answer: DataBundle | None
    pipeline: DataPipeline
    results: dict[str, dict[str, ExecutionResult | DiscoveryResult]]
    attempts: list[FragmentAttempt]
    rejected_plans: list[RejectedPlan]
    counters: dict[str, int]
    planner_usage: list[LLMUsage]


@dataclass(frozen=True)
class PlanLimits:
    """Every bound a run obeys, set once when the planner is built."""

    # The one ceiling on a request's total work, across every operator call it makes.
    operator_calls: int = 30
    # How many times the logical planner may correct a plan validation rejected.
    plan_corrections: int = 5
    # How many times a fragment may retry with different parameters before the fragment repair.
    parameter_attempts: int = 3
    # How many different cuts one node set may get before the cover repair.
    fragment_attempts: int = 2
    # How many covers one logical plan may get before the run replans.
    cover_attempts: int = 2
    # How many times an operator failure may trigger a replan before the run gives up.
    failure_replans: int = 2
    # How many times a review's REPLAN decision may trigger a replan before the run gives up.
    control_replans: int = 2
    # How many search terms one DISCOVER node may spend on the catalog.
    search_terms: int = 8


class CallBudget:
    """Counts calls against a cap and raises once the cap is reached."""

    def __init__(self, cap: int) -> None:
        if cap < 0:
            raise BlueDataPlannerError(f"a budget cap must not be negative, got {cap}")
        self._cap = cap
        self._spent = 0

    @property
    def cap(self) -> int:
        return self._cap

    @property
    def spent(self) -> int:
        return self._spent

    def spend(self, amount: int = 1) -> None:
        """Bill the budget, raising when the call would take it past its cap."""
        if self._spent + amount > self._cap:
            raise BudgetExhaustedError(
                f"a budget of {self._cap} calls is exhausted: "
                f"{self._spent} spent and {amount} more requested"
            )
        self._spent += amount
