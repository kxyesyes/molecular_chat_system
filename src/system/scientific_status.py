"""Canonical scientific observation and run outcome states.

The enums are shared by scientific execution boundaries and Agent adapters.
Legacy Agent imports remain available from ``src.agent.contracts.scientific``.
"""

from enum import Enum
from typing import Any


class ObservationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    NOT_CALCULATED = "not_calculated"
    INVALID_INPUT = "invalid_input"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    NOT_CALCULATED = "not_calculated"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


TIMEOUT_STATUS_ALIASES = frozenset({"timeout", "timed_out", "timed-out"})
CANCELLED_STATUS_ALIASES = frozenset({"cancelled", "canceled"})
OBSERVATION_STATUS_ALIASES = {
    "passed": ObservationStatus.SUCCEEDED.value,
    "timed_out": ObservationStatus.TIMEOUT.value,
    "timed-out": ObservationStatus.TIMEOUT.value,
    "canceled": ObservationStatus.CANCELLED.value,
    "not-calculated": ObservationStatus.NOT_CALCULATED.value,
}
NON_SUCCESS_STATUS = frozenset(
    {
        *(status.value for status in ObservationStatus if status is not ObservationStatus.SUCCEEDED),
        "error",
        "unknown",
    }
)


def reported_status(result: Any) -> str:
    """Normalize a provider result's status without changing its payload."""

    if not isinstance(result, dict):
        return ""
    return str(result.get("status", "")).strip().lower()


def normalize_observation_status(value: Any) -> str:
    """Normalize legacy observation-status spellings to canonical values."""

    normalized = str(value).strip().lower() if isinstance(value, str) else ""
    return OBSERVATION_STATUS_ALIASES.get(normalized, normalized)


def is_timeout_status(status: str) -> bool:
    return status in TIMEOUT_STATUS_ALIASES


def is_cancelled_status(status: str) -> bool:
    return status in CANCELLED_STATUS_ALIASES


def is_non_success_status(status: str) -> bool:
    return status in NON_SUCCESS_STATUS


def summarize_completion(completed: int, total: int) -> tuple[str, bool]:
    """Map aggregate item counts to a truthful run status and success flag."""

    if type(completed) is not int or type(total) is not int or total <= 0:
        return RunOutcome.FAILED.value, False
    if completed >= total:
        return RunOutcome.COMPLETED.value, True
    if completed > 0:
        return RunOutcome.PARTIAL.value, False
    return RunOutcome.FAILED.value, False


def aggregate_run_outcome(
    statuses: Any,
    *,
    all_succeeded: bool,
    any_usable: bool,
) -> RunOutcome:
    """Preserve the most specific non-success reason at an Agent boundary."""

    values = {
        value.value if isinstance(value, ObservationStatus) else str(value).strip().lower()
        for value in statuses
    }
    if all_succeeded and values:
        return RunOutcome.COMPLETED
    if any_usable:
        return RunOutcome.PARTIAL
    for status, outcome in (
        (ObservationStatus.CANCELLED.value, RunOutcome.CANCELLED),
        (ObservationStatus.REJECTED.value, RunOutcome.REJECTED),
        (ObservationStatus.TIMEOUT.value, RunOutcome.TIMEOUT),
        (ObservationStatus.UNAVAILABLE.value, RunOutcome.UNAVAILABLE),
        (ObservationStatus.NOT_CALCULATED.value, RunOutcome.NOT_CALCULATED),
    ):
        if status in values:
            return outcome
    return RunOutcome.FAILED


__all__ = [
    "CANCELLED_STATUS_ALIASES",
    "NON_SUCCESS_STATUS",
    "OBSERVATION_STATUS_ALIASES",
    "ObservationStatus",
    "RunOutcome",
    "TIMEOUT_STATUS_ALIASES",
    "is_cancelled_status",
    "is_non_success_status",
    "is_timeout_status",
    "normalize_observation_status",
    "aggregate_run_outcome",
    "reported_status",
    "summarize_completion",
]
