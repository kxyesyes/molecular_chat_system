"""Canonical scientific observation and run outcome states.

The enums are shared by scientific execution boundaries and Agent adapters.
Legacy Agent imports remain available from ``src.agent.contracts.scientific``.
"""

from enum import Enum


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


__all__ = ["ObservationStatus", "RunOutcome"]
