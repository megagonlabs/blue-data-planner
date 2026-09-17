"""What one request produces: execution outcomes, plan results, and the bounds that govern a run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from blue_data_planner.types.catalog import EntityMatch
from blue_data_planner.types.errors import BudgetExhaustedError
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
    """How one fragment or one request ended, whether it produced an answer or not."""

    OK = "ok"
    NEEDS_REPAIR = "needs_repair"
    NEEDS_REPLAN = "needs_replan"
    PLANNED = "planned"
    INVALID_PLAN = "invalid_plan"
    REPLAN_EXHAUSTED = "replan_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    OPERATOR_FAILED = "operator_failed"
    LLM_FAILED = "llm_failed"
    PLANNER_FAILED = "planner_failed"


class FragmentReviewDecision(StrEnum):
    """What one review turn decided about one fragment's result."""

    ACCEPT = "accept"
    REJECT = "reject"
    REPLAN = "replan"


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
    # Carried whichever way the call ended: an operator that failed partway through is the
    # one with the most to say.
    report: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    """What one DISCOVER node's catalog search found."""

    search_terms: list[str]
    matches: list[EntityMatch]
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
    attempts: list[FragmentAttempt]  # rejected ones included, in order
    failure_reason: str | None
    usage: list[LLMUsage]


@dataclass(frozen=True)
class CatalogSearchResult:
    """What one catalog search found, and what finding it cost."""

    # A pair rather than a field on DiscoveryResult: a DiscoveryResult goes into the run's
    # results map and reaches the caller inside RunResult, where usage would be counted
    # a second time.
    discovery: DiscoveryResult
    usage: list[LLMUsage]


@dataclass(frozen=True)
class RunResult:
    """Everything one request produced, whether it worked or not."""

    status: RunStatus
    # None rather than [] unless status is ok, so a caller who ignores the status cannot
    # mistake a failed run for an empty answer.
    answer: DataBundle | None
    # Why the run ended as it did, in the words of whatever ended it. Every failure status
    # carries one; ok never does, and planned does only when no cover could be written.
    failure_reason: str | None
    pipeline: DataPipeline
    results: dict[str, dict[str, ExecutionResult | DiscoveryResult]]
    attempts: list[FragmentAttempt]
    rejected_plans: list[RejectedPlan]
    counters: dict[str, int]
    planner_usage: list[LLMUsage]


@dataclass(frozen=True)
class RunLimits:
    """Every bound a run obeys, set once when the planner is built."""

    # The one ceiling on a request's total work, across every operator call it makes.
    operator_calls: int = 30
    # How many times the logical planner may write a plan again after validation turned one down.
    plan_corrections: int = 5
    # How many times one fragment may write its parameters again before the next repair.
    parameter_repairs: int = 3
    # How many different cuts one root node's fragment may get before the cover repair.
    fragment_repairs: int = 2
    # How many times one logical plan may be covered again before the run replans.
    cover_repairs: int = 2
    # How many times an operator failure may trigger a replan before the run gives up.
    failure_replans: int = 2
    # How many times a control node may trigger a replan before the run gives up.
    control_replans: int = 2


@dataclass
class RunCounters:
    """What one run has spent, against the bounds RunLimits set."""

    limits: RunLimits
    operator_calls: int = 0
    failure_replans: int = 0
    control_replans: int = 0

    def spend_operator_call(self) -> None:
        """Bill one operator call, raising when the cap leaves no room."""
        # This one raises where the other two return, because it is billed deep inside an
        # operator call, with no caller in a position to read an answer.
        if self.operator_calls >= self.limits.operator_calls:
            raise BudgetExhaustedError(
                f"a budget of {self.limits.operator_calls} operator calls is exhausted: "
                f"{self.operator_calls} spent and one more requested"
            )
        self.operator_calls += 1

    def spend_failure_replan(self) -> bool:
        """Bill one replan an operator failure asked for, False when the cap leaves no room."""
        if self.failure_replans >= self.limits.failure_replans:
            return False
        self.failure_replans += 1
        return True

    def spend_control_replan(self) -> bool:
        """Bill one replan a control node asked for, False when the cap leaves no room."""
        if self.control_replans >= self.limits.control_replans:
            return False
        self.control_replans += 1
        return True
