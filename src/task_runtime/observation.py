"""Redacted, deterministic evidence for Temporal docking canary observation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

from .deployment_contract import TEMPORAL_RELEASE_BLOCKER_NAMES
from .errors import TaskErrorCode


_CURRENT_LEVELS = frozenset({5, 10, 25})
_TERMINAL_STATUSES = ("succeeded", "failed", "canceled", "timed_out")
_KNOWN_ERROR_CODES = frozenset(code.value for code in TaskErrorCode)
_MAX_SAMPLES = 10_000
_MAX_IDENTIFIER_BYTES = 4096
_BLOCKING_ALERT_NAMES = TEMPORAL_RELEASE_BLOCKER_NAMES


def _finite_number(value: object, name: str, *, minimum: float | None = None) -> float:
    if type(value) not in {int, float}:
        raise ValueError(name)
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(name)
    return number


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(name)
    return value


def _is_representable_finite_number(value: object) -> bool:
    try:
        _finite_number(value, "invalid observation sample")
    except (OverflowError, ValueError):
        return False
    return True


@dataclass(frozen=True)
class ObservationPolicy:
    baseline_p95_seconds: float
    max_failure_rate: float = 0.05
    worker_stale_seconds: float = 60.0
    max_absolute_p95_seconds: float = 60.0
    min_task_count: int = 20
    min_window_task_count: int = 3
    min_window_hours: float = 24.0

    def __post_init__(self) -> None:
        message = "invalid observation policy"
        try:
            baseline = _finite_number(
                self.baseline_p95_seconds, message, minimum=0.0
            )
            failure_rate = _finite_number(
                self.max_failure_rate, message, minimum=0.0
            )
            worker_stale = _finite_number(
                self.worker_stale_seconds, message, minimum=0.0
            )
            absolute_p95 = _finite_number(
                self.max_absolute_p95_seconds, message, minimum=0.0
            )
            min_tasks = _positive_int(self.min_task_count, message)
            min_window_tasks = _positive_int(self.min_window_task_count, message)
            min_window_hours = _finite_number(
                self.min_window_hours, message, minimum=0.0
            )
        except (OverflowError, ValueError):
            raise ValueError(message) from None
        if (
            baseline <= 0.0
            or failure_rate > 1.0
            or worker_stale <= 0.0
            or absolute_p95 <= 0.0
            or min_window_hours <= 0.0
            or min_window_tasks > min_tasks
        ):
            raise ValueError(message)
        object.__setattr__(self, "baseline_p95_seconds", baseline)
        object.__setattr__(self, "max_failure_rate", failure_rate)
        object.__setattr__(self, "worker_stale_seconds", worker_stale)
        object.__setattr__(self, "max_absolute_p95_seconds", absolute_p95)
        object.__setattr__(self, "min_task_count", min_tasks)
        object.__setattr__(self, "min_window_task_count", min_window_tasks)
        object.__setattr__(self, "min_window_hours", min_window_hours)


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Return the exact nearest-rank percentile for finite numeric values."""

    try:
        quantile = _finite_number(fraction, "invalid percentile")
    except (OverflowError, ValueError):
        raise ValueError("invalid percentile") from None
    if not 0.0 < quantile <= 1.0:
        raise ValueError("invalid percentile")
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("invalid percentile")
    ordered: list[float] = []
    for value in values:
        try:
            ordered.append(_finite_number(value, "invalid percentile"))
        except (OverflowError, ValueError):
            raise ValueError("invalid percentile") from None
    if not ordered:
        return None
    ordered.sort()
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(name)
    try:
        if value.tzinfo is None:
            raise ValueError
        offset = value.utcoffset()
        if offset is None:
            raise ValueError
        normalized = value.astimezone(timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        raise ValueError(name) from None
    return normalized


def _fingerprint(value: object) -> str:
    if type(value) is not str or not value:
        raise ValueError("invalid observation sample")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid observation sample") from None
    if len(encoded) > _MAX_IDENTIFIER_BYTES:
        raise ValueError("invalid observation sample")
    return hashlib.sha256(encoded).hexdigest()


def _strict_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid observation sample")
    return value


def _strict_attempt(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid observation sample")
    return value


def _strict_bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("invalid observation sample")
    return value


def _strict_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return _strict_bool(value)


def _safe_error_code(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("invalid observation sample")
    return value if value in _KNOWN_ERROR_CODES else "UNKNOWN"


def _copy_mapping(value: object, message: str) -> dict[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    try:
        return dict(value)
    except Exception:
        raise ValueError(message) from None


def _project_sample(sample: object) -> dict[str, object]:
    source = _copy_mapping(sample, "invalid observation sample")
    status = source.get("status")
    if type(status) is not str or status not in _TERMINAL_STATUSES:
        raise ValueError("invalid observation sample")
    binding_energy = source.get("binding_energy")
    binding_energy_complete = status != "succeeded" or (
        _is_representable_finite_number(binding_energy)
        and source.get("binding_energy_valid") is True
    )
    try:
        latency = _finite_number(
            source.get("latency_seconds"),
            "invalid observation sample",
            minimum=0.0,
        )
    except (OverflowError, ValueError):
        raise ValueError("invalid observation sample") from None
    return {
        "task_fingerprint": _fingerprint(source.get("task_fingerprint")),
        "workflow_fingerprint": _fingerprint(source.get("workflow_fingerprint")),
        "status": status,
        "attempt": _strict_attempt(source.get("attempt")),
        "terminal_event_count": _strict_int(source.get("terminal_event_count")),
        "pose_count": _strict_int(source.get("pose_count")),
        "binding_energy_complete": binding_energy_complete,
        "artifact_exists": _strict_bool(source.get("artifact_exists")),
        "artifact_hash_matches": _strict_bool(
            source.get("artifact_hash_matches")
        ),
        "provenance_complete": _strict_optional_bool(
            source.get("provenance_complete")
        ),
        "demo_mode": _strict_optional_bool(source.get("demo_mode")),
        "fallback_used": _strict_optional_bool(source.get("fallback_used")),
        "latency_seconds": latency,
        "error_code": _safe_error_code(source.get("error_code")),
    }


def _worker_summary(value: object) -> dict[str, object]:
    source = _copy_mapping(value, "invalid worker health")
    available = source.get("available")
    if available is not None and type(available) is not bool:
        raise ValueError("invalid worker health")
    raw_age = source.get("age_seconds")
    if raw_age is None:
        age = None
    else:
        try:
            age = _finite_number(
                raw_age, "invalid worker health", minimum=0.0
            )
        except (OverflowError, ValueError):
            raise ValueError("invalid worker health") from None
    return {"available": available, "age_seconds": age}


def _infrastructure_summary(value: object) -> dict[str, bool | None]:
    source = _copy_mapping(value, "invalid infrastructure")
    summary: dict[str, bool | None] = {}
    for name in ("temporal", "namespace", "queue"):
        item = source.get(name)
        if item is not None and type(item) is not bool:
            raise ValueError("invalid infrastructure")
        summary[name] = item
    return summary


def _safe_blocking_alerts(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("invalid blocking alerts")
    return sorted(
        {
            item
            for item in value
            if type(item) is str and item in _BLOCKING_ALERT_NAMES
        }
    )


def _gate(condition: bool) -> str:
    return "passed" if condition else "failed"


def _evidence_gate(condition: bool | None) -> str:
    if condition is None:
        return "insufficient_evidence"
    return _gate(condition)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_observation_report(
    samples: Sequence[Mapping[str, object]],
    *,
    current_level: int,
    window_start: datetime,
    window_end: datetime,
    worker_health: Mapping[str, object],
    infrastructure: Mapping[str, object],
    blocking_alerts: Sequence[object] | None,
    backup_verified: bool | None,
    policy: ObservationPolicy,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build strict rollout evidence without reflecting private source fields."""

    if type(current_level) is not int or current_level not in _CURRENT_LEVELS:
        raise ValueError("invalid observation level")
    if not isinstance(policy, ObservationPolicy):
        raise ValueError("invalid observation policy")
    start = _aware_utc(window_start, "invalid observation window")
    end = _aware_utc(window_end, "invalid observation window")
    if start >= end:
        raise ValueError("invalid observation window")
    generated = _aware_utc(
        generated_at if generated_at is not None else datetime.now(timezone.utc),
        "invalid generated_at",
    )
    if generated < end:
        raise ValueError("invalid generated_at")
    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence):
        raise ValueError("invalid observation samples")
    if len(samples) > _MAX_SAMPLES:
        raise ValueError("invalid observation samples")
    projected = [_project_sample(sample) for sample in samples]
    worker = _worker_summary(worker_health)
    infra = _infrastructure_summary(infrastructure)
    alerts = _safe_blocking_alerts(blocking_alerts)
    if backup_verified is not None and type(backup_verified) is not bool:
        raise ValueError("invalid backup status")

    counts_counter = Counter(str(sample["status"]) for sample in projected)
    counts = {status: counts_counter.get(status, 0) for status in _TERMINAL_STATUSES}
    sample_count = len(projected)
    unsuccessful_count = sample_count - counts["succeeded"]
    failure_rate = unsuccessful_count / sample_count if sample_count else 0.0
    latencies = [float(sample["latency_seconds"]) for sample in projected]
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    errors = Counter(
        str(sample["error_code"])
        for sample in projected
        if sample["error_code"] is not None
    )
    error_distribution = {name: errors[name] for name in sorted(errors)}
    succeeded = [sample for sample in projected if sample["status"] == "succeeded"]
    public_tasks = [
        {
            "task_fingerprint": sample["task_fingerprint"],
            "workflow_fingerprint": sample["workflow_fingerprint"],
            "status": sample["status"],
            "latency_seconds": sample["latency_seconds"],
        }
        for sample in projected
    ]

    duration_hours = (end - start).total_seconds() / 3600.0
    quantity_sufficient = sample_count >= policy.min_task_count or (
        sample_count >= policy.min_window_task_count
        and duration_hours >= policy.min_window_hours
    )
    latency_limit = min(
        1.5 * policy.baseline_p95_seconds,
        policy.max_absolute_p95_seconds,
    )
    gates = {
        "single_vina_attempt": _gate(
            all(
                sample["attempt"] <= 1
                and (sample["status"] != "succeeded" or sample["attempt"] == 1)
                for sample in projected
            )
        ),
        "single_terminal_event": _gate(
            all(sample["terminal_event_count"] == 1 for sample in projected)
        ),
        "successful_pose": _gate(
            all(int(sample["pose_count"]) > 0 for sample in succeeded)
        ),
        "finite_binding_energy": _gate(
            all(sample["binding_energy_complete"] is True for sample in succeeded)
        ),
        "artifact_integrity": _gate(
            all(
                sample["artifact_exists"] is True
                and sample["artifact_hash_matches"] is True
                for sample in succeeded
            )
        ),
        "provenance_complete": _gate(
            all(sample["provenance_complete"] is True for sample in projected)
        ),
        "real_execution": _gate(
            all(
                sample["demo_mode"] is False
                and sample["fallback_used"] is False
                for sample in projected
            )
        ),
        "failure_rate": _gate(failure_rate <= policy.max_failure_rate),
        "latency_p95": _gate(p95 is None or p95 <= latency_limit),
        "worker_health": _evidence_gate(
            False
            if worker["available"] is False
            or (
                worker["age_seconds"] is not None
                and float(worker["age_seconds"]) > policy.worker_stale_seconds
            )
            else True
            if worker["available"] is True and worker["age_seconds"] is not None
            else None
        ),
        "temporal_available": _evidence_gate(infra["temporal"]),
        "namespace_available": _evidence_gate(infra["namespace"]),
        "queue_available": _evidence_gate(infra["queue"]),
        "blocking_alerts": _evidence_gate(None if alerts is None else not alerts),
        "backup_verified": _evidence_gate(backup_verified),
        "sample_window": "passed" if quantity_sufficient else "insufficient_evidence",
    }
    overall = (
        "failed"
        if "failed" in gates.values()
        else "partial"
        if "insufficient_evidence" in gates.values()
        else "passed"
    )
    gates["all_required_gates"] = overall

    report: dict[str, object] = {
        "schema_version": 1,
        "stage": "observation",
        "status": overall,
        "current_level": current_level,
        "generated_at": generated.isoformat(),
        "gates": gates,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "sample_count": sample_count,
        "counts": counts,
        "rates": {"failure": failure_rate},
        "latency_seconds": {"p50": p50, "p95": p95},
        "error_code_distribution": error_distribution,
        "worker_health": worker,
        "infrastructure": infra,
        "blocking_alerts": alerts,
        "backup_verified": backup_verified,
        "tasks": public_tasks,
    }
    report["sha256"] = _canonical_sha256(report)
    return report


__all__ = ["ObservationPolicy", "build_observation_report", "percentile"]
