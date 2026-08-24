"""Shared production contracts for Temporal deployment validation."""

from __future__ import annotations

from types import MappingProxyType


TEMPORAL_RELEASE_BLOCKER_DURATIONS = MappingProxyType(
    {
        "TemporalWorkerHeartbeatStale": "60s",
        "TemporalQueueBacklogGrowing": "10m",
        "TemporalWorkflowStartUnexpectedErrors": "5m",
        "TemporalDuplicateVinaExecution": None,
        "TemporalMultipleTerminalEvents": None,
        "TemporalArtifactValidationFailure": None,
        "TemporalDockingP95TooHigh": "10m",
        "TemporalDockingP95BaselineMissing": None,
        "TemporalRuntimeFailureRateHigh": "10m",
        "TemporalPostgresUnavailable": "60s",
        "TemporalBackupVerificationStale": "15m",
    }
)
TEMPORAL_RELEASE_BLOCKER_NAMES = frozenset(
    TEMPORAL_RELEASE_BLOCKER_DURATIONS
)
