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
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


TIMEOUT_STATUS_ALIASES = frozenset({"timeout", "timed_out", "timed-out"})
CANCELLED_STATUS_ALIASES = frozenset({"cancelled", "canceled"})
NON_SUCCESS_STATUS = frozenset(
    {
        *(status.value for status in ObservationStatus if status is not ObservationStatus.SUCCEEDED),
        "error",
        "unknown",
        "not_calculated",
    }
)


def reported_status(result: Any) -> str:
    """Normalize a provider result's status without changing its payload."""

    if not isinstance(result, dict):
        return ""
    return str(result.get("status", "")).strip().lower()


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


__all__ = [
    "CANCELLED_STATUS_ALIASES",
    "NON_SUCCESS_STATUS",
    "ObservationStatus",
    "RunOutcome",
    "TIMEOUT_STATUS_ALIASES",
    "is_cancelled_status",
    "is_non_success_status",
    "is_timeout_status",
    "reported_status",
    "summarize_completion",
]
