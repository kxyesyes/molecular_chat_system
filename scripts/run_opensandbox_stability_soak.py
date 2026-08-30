#!/usr/bin/env python3
"""Opt-in, trace-safe OpenSandbox stability soak under gVisor."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import stat
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # Optional on developer machines; the CLI fails closed if unavailable.
    import httpx
except ImportError:  # pragma: no cover - exercised through injected module state.
    httpx = None  # type: ignore[assignment]

from scripts.run_opensandbox_docking_acceptance import (
    DockerSandboxObserver,
    _deployment_failures,
    _error_code,
    _exercise_sacrificial_security_contract,
    _payload,
    _project_result,
    _request_cancellation_after_inspection,
    _run_command,
)
from src.agent.persistence.redaction import contains_credential
from src.docking.sandbox_runner import SandboxDockingRunner


REPEAT = 30
QUEUE_CAPACITY = 8
QUEUE_OVERFLOW = 1
MAX_RECORDED_LATENCY_MS = 420_000
LATENCY_GATE_MS = 300_000
SCHEMA_VERSION = 1
GATE = "MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK"
DEFAULT_REPORT = Path("outputs/agent_evaluation/opensandbox_stability_soak.json")
REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "backend",
        "secure_runtime",
        "repeat",
        "pass_rate",
        "cleanup_rate",
        "event_completeness_rate",
        "p50_latency_ms",
        "p95_latency_ms",
        "phase_p95_latency_ms",
        "failure_type_distribution",
        "gates",
        "idempotency",
        "queue",
        "probes",
        "running_labelled_containers",
        "diagnostics",
        "runs",
    }
)
RUN_FIELDS = frozenset(
    {
        "run_index",
        "trace_id",
        "status",
        "latency_ms",
        "pose_count",
        "best_energy",
        "artifact_present",
        "artifact_path",
        "artifact_sha256",
        "artifact_sha256_valid",
        "cleanup_status",
        "image_digest",
        "image_digest_valid",
        "secure_runtime",
        "vina_version",
        "meeko_version",
        "warning_codes",
        "failure_codes",
        "failure_class",
        "events_complete",
    }
)
ALLOWED_STATUSES = frozenset({"passed", "partial", "failed", "skipped"})
FAILURE_CLASSES = frozenset(
    {
        "none",
        "connection_failed",
        "server_500",
        "proxy_502",
        "readiness_timeout",
        "create_timeout",
        "command_transport_failed",
        "command_timeout",
        "resource_limit",
        "destroy_failed",
        "unknown_control_plane_failure",
    }
)
EXPECTED_PHASES = (
    "job_received",
    "queue_entered",
    "provisioning_started",
    "provisioning_completed",
    "upload_started",
    "upload_completed",
    "command_started",
    "command_completed",
    "validation_started",
    "validation_completed",
    "cleanup_started",
    "cleanup_completed",
    "job_terminal",
)
PHASE_NAMES = ("provisioning", "upload", "command", "validation", "cleanup")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+_()-]{0,127}\Z")
_WINDOWS_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_SECRET_KEY = re.compile(
    r"(?i)(?:authorization|api[_-]?key|token|password|passwd|pwd|secret|"
    r"credential|cookie|private[_-]?key|client[_-]?secret)"
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\bsk-[A-Za-z0-9_-]{8,}|\b(?:authorization|api[_-]?key|token|"
    r"password|secret)\s*[:=])"
)
_MAX_STRING = 2048
_MAX_LIST = 2048
_MAX_RESPONSE = 1024 * 1024
_DOCKER_TIMEOUT = 12.0


class SafeReportError(ValueError):
    """A stable report-safety validation failure."""


class SanitizedRuntimeError(RuntimeError):
    """A runtime failure whose message is always a fixed safe code."""


def _fail_report() -> None:
    raise SafeReportError("unsafe_report")


def _safe_number(
    value: object,
    *,
    integer: bool = False,
    maximum: float = LATENCY_GATE_MS,
) -> bool:
    expected = (int,) if integer else (int, float)
    return type(value) in expected and math.isfinite(float(value)) and 0 <= float(value) <= maximum


def percentile(
    values: Sequence[int | float],
    quantile: int | float,
    *,
    maximum: float = LATENCY_GATE_MS,
) -> float:
    """Return a deterministic linearly interpolated percentile."""

    if (
        type(quantile) not in (int, float)
        or not math.isfinite(float(quantile))
        or not 0 <= float(quantile) <= 1
        or not values
    ):
        raise ValueError("invalid_percentile")
    checked: list[float] = []
    for value in values:
        if not _safe_number(value, maximum=maximum):
            raise ValueError("invalid_percentile")
        checked.append(float(value))
    checked.sort()
    position = (len(checked) - 1) * float(quantile)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return checked[lower]
    weight = position - lower
    return checked[lower] + (checked[upper] - checked[lower]) * weight


def _validate_safe_string(value: object, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if type(value) is not str or not value or len(value) > _MAX_STRING:
        _fail_report()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail_report()
    if (
        _SECRET_TEXT.search(value)
        or contains_credential(value)
        or value.endswith(".sock")
        or value.startswith("/")
        or _WINDOWS_ABSOLUTE.match(value)
    ):
        _fail_report()
    path_parts = re.split(r"[\\/]", value)
    if ".." in path_parts:
        _fail_report()


def _validate_recursive(value: object, *, depth: int = 0) -> None:
    if depth > 12:
        _fail_report()
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail_report()
        return
    if type(value) is str:
        _validate_safe_string(value)
        return
    if type(value) is list:
        if len(value) > _MAX_LIST:
            _fail_report()
        for item in value:
            _validate_recursive(item, depth=depth + 1)
        return
    if type(value) is dict:
        if len(value) > 256:
            _fail_report()
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 128 or _SECRET_KEY.search(key):
                _fail_report()
            _validate_recursive(item, depth=depth + 1)
        return
    _fail_report()


def validate_artifact_path(value: object, *, project_root: Path = PROJECT_ROOT) -> str:
    if type(value) is not str or "\\" in value or len(value) > 512:
        _fail_report()
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.parts[0] != "outputs":
        _fail_report()
    if any(part in {"", ".", ".."} for part in path.parts):
        _fail_report()
    if str(path) != value or _WINDOWS_ABSOLUTE.match(value):
        _fail_report()
    current = Path(project_root)
    try:
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                _fail_report()
    except OSError:
        _fail_report()
    return value


def _validate_codes(value: object) -> None:
    if type(value) is not list or len(value) > 128:
        _fail_report()
    for code in value:
        if type(code) is not str or _SAFE_CODE.fullmatch(code) is None:
            _fail_report()


def validate_run(run: object, *, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    if type(run) is not dict or set(run) != RUN_FIELDS:
        _fail_report()
    _validate_recursive(run)
    if not _safe_number(run["run_index"], integer=True, maximum=REPEAT) or run["run_index"] < 1:
        _fail_report()
    if type(run["trace_id"]) is not str or _SAFE_ID.fullmatch(run["trace_id"]) is None:
        _fail_report()
    if run["status"] not in ALLOWED_STATUSES:
        _fail_report()
    if not _safe_number(
        run["latency_ms"],
        integer=True,
        maximum=MAX_RECORDED_LATENCY_MS,
    ):
        _fail_report()
    if type(run["pose_count"]) is not int or not 0 <= run["pose_count"] <= 256:
        _fail_report()
    energy = run["best_energy"]
    if energy is not None and (type(energy) not in (int, float) or not math.isfinite(float(energy))):
        _fail_report()
    for field in ("artifact_present", "artifact_sha256_valid", "image_digest_valid", "events_complete"):
        if type(run[field]) is not bool:
            _fail_report()
    artifact_path = run["artifact_path"]
    if artifact_path is not None:
        validate_artifact_path(artifact_path, project_root=project_root)
    for field in ("artifact_sha256", "image_digest"):
        value = run[field]
        if value is not None and (type(value) is not str or _SHA256.fullmatch(value) is None):
            _fail_report()
    if run["cleanup_status"] not in (None, "succeeded", "failed"):
        _fail_report()
    if run["secure_runtime"] not in (None, "gvisor"):
        _fail_report()
    for field in ("vina_version", "meeko_version"):
        value = run[field]
        if value is not None and (type(value) is not str or _SAFE_VERSION.fullmatch(value) is None):
            _fail_report()
    _validate_codes(run["warning_codes"])
    _validate_codes(run["failure_codes"])
    if run["failure_class"] not in FAILURE_CLASSES:
        _fail_report()
    return run


def scientific_success(run: object, *, project_root: Path = PROJECT_ROOT) -> bool:
    try:
        checked = validate_run(run, project_root=project_root)
    except SafeReportError:
        return False
    codes = checked["warning_codes"] + checked["failure_codes"]
    return bool(
        checked["status"] == "passed"
        and type(checked["pose_count"]) is int
        and checked["pose_count"] > 0
        and type(checked["best_energy"]) in (int, float)
        and math.isfinite(float(checked["best_energy"]))
        and checked["artifact_present"] is True
        and checked["artifact_path"] is not None
        and checked["artifact_sha256"] is not None
        and checked["artifact_sha256_valid"] is True
        and checked["cleanup_status"] == "succeeded"
        and checked["image_digest"] is not None
        and checked["image_digest_valid"] is True
        and checked["secure_runtime"] == "gvisor"
        and checked["vina_version"] is not None
        and checked["meeko_version"] is not None
        and checked["failure_class"] == "none"
        and checked["events_complete"] is True
        and not checked["failure_codes"]
        and not any("demo" in code or "fallback" in code for code in codes)
    )


def synthetic_event(job_id: str, phase: str) -> dict[str, object]:
    """Build a schema-shaped event for deterministic delta tests."""

    completed = phase.endswith("_completed")
    terminal = phase == "job_terminal"
    cleanup = "succeeded" if phase == "cleanup_completed" else "not_started"
    if phase == "cleanup_started":
        cleanup = "in_progress"
    return {
        "schema_version": 1,
        "trace_id": job_id,
        "job_id": job_id,
        "phase": phase,
        "attempt": None,
        "outcome": "passed" if completed or terminal else None,
        "failure_class": None,
        "duration_ms": 1 if completed else None,
        "queue_depth": 0,
        "active_job_count": 0,
        "cleanup_status": cleanup,
        "image_digest": "a" * 64,
        "vina_version": "1.2.5" if phase == "validation_completed" else None,
        "meeko_version": "0.6.1" if phase == "validation_completed" else None,
    }


_EVENT_FIELDS = frozenset(synthetic_event("job", "job_received"))
_TELEMETRY_FIELDS = frozenset(
    {"schema_version", "counters", "phase_latency_ms", "runtime", "recent_events"}
)
_RUNTIME_FIELDS = frozenset(
    {"queue_depth", "active_job_count", "cleanup_task_count", "isolated_task_count", "breaker_state"}
)
_BREAKER_FIELDS = frozenset({"state", "failure_threshold", "window_seconds", "open_seconds"})


def _validate_event(event: object) -> dict[str, Any]:
    if type(event) is not dict or set(event) != _EVENT_FIELDS:
        raise SanitizedRuntimeError("diagnostics_invalid")
    try:
        _validate_recursive(event)
    except SafeReportError:
        raise SanitizedRuntimeError("diagnostics_invalid") from None
    if (
        event["schema_version"] != 1
        or type(event["trace_id"]) is not str
        or _SAFE_ID.fullmatch(event["trace_id"]) is None
        or type(event["job_id"]) is not str
        or _SAFE_ID.fullmatch(event["job_id"]) is None
        or event["phase"] not in EXPECTED_PHASES
        or event["failure_class"] not in FAILURE_CLASSES | {None}
        or event["outcome"] not in {None, "passed", "failed", "cancelled"}
        or event["cleanup_status"] not in {"not_started", "in_progress", "succeeded", "failed"}
    ):
        raise SanitizedRuntimeError("diagnostics_invalid")
    for field, maximum in (("queue_depth", 8), ("active_job_count", 1)):
        if type(event[field]) is not int or not 0 <= event[field] <= maximum:
            raise SanitizedRuntimeError("diagnostics_invalid")
    duration = event["duration_ms"]
    if duration is not None and not _safe_number(duration, integer=True):
        raise SanitizedRuntimeError("diagnostics_invalid")
    attempt = event["attempt"]
    if attempt is not None and (type(attempt) is not int or not 1 <= attempt <= 2):
        raise SanitizedRuntimeError("diagnostics_invalid")
    if type(event["image_digest"]) is not str or _SHA256.fullmatch(event["image_digest"]) is None:
        raise SanitizedRuntimeError("diagnostics_invalid")
    for field in ("vina_version", "meeko_version"):
        value = event[field]
        if value is not None and (type(value) is not str or _SAFE_VERSION.fullmatch(value) is None):
            raise SanitizedRuntimeError("diagnostics_invalid")
    return event


def validate_diagnostics(value: object) -> dict[str, Any]:
    try:
        if type(value) is not dict or set(value) != {"schema_version", "telemetry", "circuit_breaker"}:
            raise SanitizedRuntimeError("diagnostics_invalid")
        if value["schema_version"] != 1:
            raise SanitizedRuntimeError("diagnostics_invalid")
        telemetry = value["telemetry"]
        breaker = value["circuit_breaker"]
        if type(telemetry) is not dict or set(telemetry) != _TELEMETRY_FIELDS:
            raise SanitizedRuntimeError("diagnostics_invalid")
        if telemetry["schema_version"] != 1:
            raise SanitizedRuntimeError("diagnostics_invalid")
        counters = telemetry["counters"]
        if type(counters) is not dict or len(counters) > 512:
            raise SanitizedRuntimeError("diagnostics_invalid")
        for key, count in counters.items():
            if type(key) is not str or _SAFE_ID.fullmatch(key.replace("|", "_")) is None:
                raise SanitizedRuntimeError("diagnostics_invalid")
            if type(count) is not int or not 0 <= count <= 1_000_000_000:
                raise SanitizedRuntimeError("diagnostics_invalid")
        latencies = telemetry["phase_latency_ms"]
        if type(latencies) is not dict or len(latencies) > 64:
            raise SanitizedRuntimeError("diagnostics_invalid")
        for key, samples in latencies.items():
            if type(key) is not str or _SAFE_ID.fullmatch(key.replace("|", "_")) is None:
                raise SanitizedRuntimeError("diagnostics_invalid")
            if type(samples) is not list or len(samples) > 1024:
                raise SanitizedRuntimeError("diagnostics_invalid")
            if any(not _safe_number(sample, integer=True) for sample in samples):
                raise SanitizedRuntimeError("diagnostics_invalid")
        runtime = telemetry["runtime"]
        if type(runtime) is not dict or set(runtime) != _RUNTIME_FIELDS:
            raise SanitizedRuntimeError("diagnostics_invalid")
        limits = {"queue_depth": 8, "active_job_count": 1}
        for field in ("queue_depth", "active_job_count", "cleanup_task_count", "isolated_task_count"):
            maximum = limits.get(field, 1_000_000)
            if type(runtime[field]) is not int or not 0 <= runtime[field] <= maximum:
                raise SanitizedRuntimeError("diagnostics_invalid")
        if runtime["breaker_state"] not in {"closed", "open", "half_open"}:
            raise SanitizedRuntimeError("diagnostics_invalid")
        events = telemetry["recent_events"]
        if type(events) is not list or len(events) > 1024:
            raise SanitizedRuntimeError("diagnostics_invalid")
        for event in events:
            _validate_event(event)
        if type(breaker) is not dict or set(breaker) != _BREAKER_FIELDS:
            raise SanitizedRuntimeError("diagnostics_invalid")
        if breaker["state"] not in {"closed", "open", "half_open"}:
            raise SanitizedRuntimeError("diagnostics_invalid")
        for field in ("failure_threshold", "window_seconds", "open_seconds"):
            if type(breaker[field]) is not int or not 1 <= breaker[field] <= 86_400:
                raise SanitizedRuntimeError("diagnostics_invalid")
        _validate_recursive(value)
        return value
    except SafeReportError:
        raise SanitizedRuntimeError("diagnostics_invalid") from None


def _sequence_delta(before: list[Any], after: list[Any]) -> list[Any]:
    """Find the longest safe suffix/prefix overlap, including ring rollover."""

    maximum = min(len(before), len(after))
    for overlap in range(maximum, -1, -1):
        if before[len(before) - overlap :] == after[:overlap]:
            return after[overlap:]
    return list(after)


def diagnostics_delta(before: object, after: object) -> dict[str, Any]:
    old = validate_diagnostics(before)
    new = validate_diagnostics(after)
    counters: dict[str, int] = {}
    for key, count in new["telemetry"]["counters"].items():
        delta = count - old["telemetry"]["counters"].get(key, 0)
        if delta < 0:
            raise SanitizedRuntimeError("diagnostics_invalid")
        if delta:
            counters[key] = delta
    events = _sequence_delta(
        old["telemetry"]["recent_events"], new["telemetry"]["recent_events"]
    )
    latency: dict[str, list[int]] = {}
    for event in events:
        phase = event.get("phase")
        outcome = event.get("outcome")
        duration = event.get("duration_ms")
        if (
            type(phase) is str
            and phase.endswith("_completed")
            and type(outcome) is str
            and _safe_number(duration, integer=True)
        ):
            key = f"{phase[:-10]}|{outcome}"
            latency.setdefault(key, []).append(duration)
    if not latency:
        for key, samples in new["telemetry"]["phase_latency_ms"].items():
            added = _sequence_delta(old["telemetry"]["phase_latency_ms"].get(key, []), samples)
            if added:
                latency[key] = added
    return {
        "schema_version": 1,
        "events": events,
        "counters": counters,
        "phase_latency_ms": latency,
        "runtime": dict(new["telemetry"]["runtime"]),
        "circuit_breaker": dict(new["circuit_breaker"]),
    }


_DIAGNOSTIC_DELTA_FIELDS = frozenset(
    {"schema_version", "events", "counters", "phase_latency_ms", "runtime", "circuit_breaker"}
)


def validate_diagnostics_delta(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _DIAGNOSTIC_DELTA_FIELDS or value["schema_version"] != 1:
        raise SanitizedRuntimeError("diagnostics_invalid")
    try:
        _validate_recursive(value)
    except SafeReportError:
        raise SanitizedRuntimeError("diagnostics_invalid") from None
    events = value["events"]
    if type(events) is not list or len(events) > 1024:
        raise SanitizedRuntimeError("diagnostics_invalid")
    for event in events:
        _validate_event(event)
    counters = value["counters"]
    if type(counters) is not dict or len(counters) > 512:
        raise SanitizedRuntimeError("diagnostics_invalid")
    for key, count in counters.items():
        if type(key) is not str or _SAFE_ID.fullmatch(key.replace("|", "_")) is None:
            raise SanitizedRuntimeError("diagnostics_invalid")
        if type(count) is not int or not 0 <= count <= 1_000_000_000:
            raise SanitizedRuntimeError("diagnostics_invalid")
    latencies = value["phase_latency_ms"]
    if type(latencies) is not dict or len(latencies) > 64:
        raise SanitizedRuntimeError("diagnostics_invalid")
    for key, samples in latencies.items():
        if type(key) is not str or _SAFE_ID.fullmatch(key.replace("|", "_")) is None:
            raise SanitizedRuntimeError("diagnostics_invalid")
        if type(samples) is not list or len(samples) > 1024:
            raise SanitizedRuntimeError("diagnostics_invalid")
        if any(not _safe_number(sample, integer=True) for sample in samples):
            raise SanitizedRuntimeError("diagnostics_invalid")
    runtime = value["runtime"]
    if type(runtime) is not dict or set(runtime) != _RUNTIME_FIELDS:
        raise SanitizedRuntimeError("diagnostics_invalid")
    for field, maximum in (
        ("queue_depth", 8),
        ("active_job_count", 1),
        ("cleanup_task_count", 1_000_000),
        ("isolated_task_count", 1_000_000),
    ):
        if type(runtime[field]) is not int or not 0 <= runtime[field] <= maximum:
            raise SanitizedRuntimeError("diagnostics_invalid")
    if runtime["breaker_state"] not in {"closed", "open", "half_open"}:
        raise SanitizedRuntimeError("diagnostics_invalid")
    breaker = value["circuit_breaker"]
    if type(breaker) is not dict or set(breaker) != _BREAKER_FIELDS:
        raise SanitizedRuntimeError("diagnostics_invalid")
    if breaker["state"] not in {"closed", "open", "half_open"}:
        raise SanitizedRuntimeError("diagnostics_invalid")
    for field in ("failure_threshold", "window_seconds", "open_seconds"):
        if type(breaker[field]) is not int or not 1 <= breaker[field] <= 86_400:
            raise SanitizedRuntimeError("diagnostics_invalid")
    return value


class BrokerDiagnosticsClient:
    def __init__(self, socket_path: str | os.PathLike[str]) -> None:
        raw_path = os.fspath(socket_path)
        path = PurePosixPath(raw_path)
        if not path.is_absolute() or "\\" in raw_path or httpx is None:
            raise SanitizedRuntimeError("diagnostics_unavailable")
        self._socket_path = raw_path

    def snapshot(self) -> dict[str, Any]:
        try:
            transport = httpx.HTTPTransport(uds=self._socket_path)
            with httpx.Client(
                transport=transport,
                base_url="http://medchat-sandbox-broker",
                timeout=httpx.Timeout(5.0),
            ) as client:
                response = client.get("/v1/diagnostics")
                response.raise_for_status()
                if type(response.content) is not bytes or len(response.content) > _MAX_RESPONSE:
                    raise SanitizedRuntimeError("diagnostics_unavailable")
                return validate_diagnostics(response.json())
        except SanitizedRuntimeError:
            raise
        except BaseException:
            raise SanitizedRuntimeError("diagnostics_unavailable") from None


def running_labelled_container_count(
    *, command_runner: Callable[..., object] = _run_command
) -> int:
    result = command_runner(
        [
            "docker",
            "ps",
            "--filter",
            "label=medchat.operation=molecular_docking",
            "--format",
            "{{.ID}}",
        ],
        timeout=_DOCKER_TIMEOUT,
    )
    if not bool(getattr(result, "ok", False)) or type(getattr(result, "stdout", None)) is not str:
        raise SanitizedRuntimeError("container_observation_failed")
    lines = [line for line in result.stdout.splitlines() if line]
    if len(lines) > 1024 or any(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", line) is None for line in lines):
        raise SanitizedRuntimeError("container_observation_failed")
    return len(lines)


def _empty_diagnostics() -> dict[str, object]:
    return {
        "schema_version": 1,
        "telemetry": {
            "schema_version": 1,
            "counters": {},
            "phase_latency_ms": {},
            "runtime": {
                "queue_depth": 0,
                "active_job_count": 0,
                "cleanup_task_count": 0,
                "isolated_task_count": 0,
                "breaker_state": "closed",
            },
            "recent_events": [],
        },
        "circuit_breaker": {
            "state": "closed",
            "failure_threshold": 1,
            "window_seconds": 1,
            "open_seconds": 1,
        },
    }


def _failed_run(index: int, code: str = "measured_run_failed") -> dict[str, Any]:
    return {
        "run_index": index,
        "trace_id": f"soak-failed-{index:02d}",
        "status": "failed",
        "latency_ms": 0,
        "pose_count": 0,
        "best_energy": None,
        "artifact_present": False,
        "artifact_path": None,
        "artifact_sha256": None,
        "artifact_sha256_valid": False,
        "cleanup_status": None,
        "image_digest": None,
        "image_digest_valid": False,
        "secure_runtime": None,
        "vina_version": None,
        "meeko_version": None,
        "warning_codes": [],
        "failure_codes": [code if _SAFE_CODE.fullmatch(code) else "measured_run_failed"],
        "failure_class": "unknown_control_plane_failure",
        "events_complete": False,
    }


def _valid_idempotency(value: object) -> bool:
    return type(value) is dict and set(value) == {"passed", "sandbox_count"} and value == {
        "passed": True,
        "sandbox_count": 1,
    }


def _valid_queue(value: object) -> bool:
    return type(value) is dict and set(value) == {
        "passed",
        "accepted",
        "saturated",
    } and value == {
        "passed": True,
        "accepted": 9,
        "saturated": 1,
    }


def build_report(
    *,
    runs: Sequence[dict[str, Any]],
    diagnostics: object | None = None,
    idempotency: object,
    queue: object,
    probes: object,
    running_labelled_containers: object,
    diagnostics_before: object | None = None,
    diagnostics_after: object | None = None,
    status_override: str | None = None,
) -> dict[str, Any]:
    if diagnostics is None:
        if diagnostics_before is None or diagnostics_after is None:
            raise SanitizedRuntimeError("diagnostics_invalid")
        delta = diagnostics_delta(diagnostics_before, diagnostics_after)
    else:
        delta = validate_diagnostics_delta(diagnostics)
    checked_runs = [validate_run(run) for run in runs]
    scientific = sum(scientific_success(run) for run in checked_runs)
    cleanup = sum(run["cleanup_status"] == "succeeded" for run in checked_runs)
    complete = sum(run["events_complete"] is True for run in checked_runs)
    denominator = REPEAT
    measured_latencies = [run["latency_ms"] for run in checked_runs]
    phase_p95: dict[str, float] = {}
    for phase in PHASE_NAMES:
        samples: list[int] = []
        for key, values in delta["phase_latency_ms"].items():
            if key == f"{phase}|passed":
                samples.extend(values)
        if samples:
            phase_p95[phase] = percentile(samples, 0.95)
    p50_latency = (
        percentile(
            measured_latencies,
            0.50,
            maximum=MAX_RECORDED_LATENCY_MS,
        )
        if measured_latencies
        else None
    )
    p95_latency = (
        percentile(
            measured_latencies,
            0.95,
            maximum=MAX_RECORDED_LATENCY_MS,
        )
        if measured_latencies
        else None
    )
    probes_valid = (
        type(probes) is dict
        and set(probes) == {"cancellation", "timeout", "security"}
        and all(type(probes[name]) is bool for name in probes)
    )
    orphan_valid = type(running_labelled_containers) is int and running_labelled_containers == 0
    gates = {
        "scientific_success": len(checked_runs) == REPEAT and scientific == REPEAT,
        "cleanup": len(checked_runs) == REPEAT and cleanup == REPEAT,
        "events_complete": len(checked_runs) == REPEAT and complete == REPEAT,
        "idempotency": _valid_idempotency(idempotency),
        "queue": _valid_queue(queue),
        "cancellation": probes_valid and probes["cancellation"] is True,
        "timeout": probes_valid and probes["timeout"] is True,
        "security": probes_valid and probes["security"] is True,
        "no_orphans": orphan_valid,
        "latency": p95_latency is not None and p95_latency < LATENCY_GATE_MS,
        "phase_latency": set(phase_p95) == set(PHASE_NAMES)
        and all(value < LATENCY_GATE_MS for value in phase_p95.values()),
    }
    status = status_override or ("passed" if all(gates.values()) else "failed")
    report = {
        "schema_version": 1,
        "status": status,
        "backend": "opensandbox",
        "secure_runtime": "gvisor",
        "repeat": REPEAT,
        "pass_rate": scientific / denominator,
        "cleanup_rate": cleanup / denominator,
        "event_completeness_rate": complete / denominator,
        "p50_latency_ms": p50_latency,
        "p95_latency_ms": p95_latency,
        "phase_p95_latency_ms": phase_p95,
        "failure_type_distribution": dict(
            sorted(
                Counter(
                    run["failure_class"]
                    for run in checked_runs
                    if run["status"] != "passed"
                ).items()
            )
        ),
        "gates": gates,
        "idempotency": idempotency if type(idempotency) is dict else {"passed": False, "sandbox_count": 0},
        "queue": queue
        if type(queue) is dict
        else {
            "passed": False,
            "accepted": 0,
            "saturated": 0,
        },
        "probes": probes
        if probes_valid
        else {"cancellation": False, "timeout": False, "security": False},
        "running_labelled_containers": (
            running_labelled_containers if type(running_labelled_containers) is int else -1
        ),
        "diagnostics": delta,
        "runs": checked_runs,
    }
    return validate_report(report)


def validate_report(report: object) -> dict[str, Any]:
    if type(report) is not dict or set(report) != REPORT_FIELDS:
        _fail_report()
    _validate_recursive(report)
    if report["schema_version"] != 1 or report["status"] not in ALLOWED_STATUSES:
        _fail_report()
    if report["backend"] != "opensandbox" or report["secure_runtime"] != "gvisor":
        _fail_report()
    if type(report["repeat"]) is not int or report["repeat"] != REPEAT:
        _fail_report()
    for field in ("pass_rate", "cleanup_rate", "event_completeness_rate"):
        value = report[field]
        if type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1:
            _fail_report()
    for field in ("p50_latency_ms", "p95_latency_ms"):
        value = report[field]
        if value is not None and not _safe_number(
            value,
            maximum=MAX_RECORDED_LATENCY_MS,
        ):
            _fail_report()
    if type(report["phase_p95_latency_ms"]) is not dict or not set(report["phase_p95_latency_ms"]).issubset(PHASE_NAMES):
        _fail_report()
    if any(not _safe_number(value) for value in report["phase_p95_latency_ms"].values()):
        _fail_report()
    distribution = report["failure_type_distribution"]
    if type(distribution) is not dict or any(
        key not in FAILURE_CLASSES or type(count) is not int or count < 0
        for key, count in distribution.items()
    ):
        _fail_report()
    runs = report["runs"]
    if type(runs) is not list or len(runs) > REPEAT:
        _fail_report()
    checked = [validate_run(run) for run in runs]
    if [run["run_index"] for run in checked] != list(range(1, len(checked) + 1)):
        _fail_report()
    expected_distribution = dict(
        sorted(
            Counter(
                run["failure_class"] for run in checked if run["status"] != "passed"
            ).items()
        )
    )
    if distribution != expected_distribution:
        _fail_report()
    gates = report["gates"]
    expected_gate_names = {
        "scientific_success",
        "cleanup",
        "events_complete",
        "idempotency",
        "queue",
        "cancellation",
        "timeout",
        "security",
        "no_orphans",
        "latency",
        "phase_latency",
    }
    if type(gates) is not dict or set(gates) != expected_gate_names or any(type(value) is not bool for value in gates.values()):
        _fail_report()
    if report["status"] == "passed" and (len(runs) != REPEAT or not all(gates.values())):
        _fail_report()
    diagnostics = report["diagnostics"]
    gate_diagnostics = (
        type(diagnostics) is dict
        and set(diagnostics) == {"schema_version", "gate_failure"}
        and diagnostics.get("schema_version") == 1
        and type(diagnostics.get("gate_failure")) is str
        and _SAFE_CODE.fullmatch(diagnostics["gate_failure"]) is not None
    )
    if gate_diagnostics:
        if report["status"] not in {"skipped", "failed"} or runs:
            _fail_report()
    else:
        try:
            validate_diagnostics_delta(diagnostics)
        except SanitizedRuntimeError:
            _fail_report()
    if type(report["idempotency"]) is not dict or set(report["idempotency"]) != {"passed", "sandbox_count"}:
        _fail_report()
    if type(report["idempotency"]["passed"]) is not bool or type(report["idempotency"]["sandbox_count"]) is not int:
        _fail_report()
    if type(report["queue"]) is not dict or set(report["queue"]) != {"passed", "accepted", "saturated"}:
        _fail_report()
    if type(report["queue"]["passed"]) is not bool or any(
        type(report["queue"][field]) is not int or report["queue"][field] < 0
        for field in ("accepted", "saturated")
    ):
        _fail_report()
    if type(report["probes"]) is not dict or set(report["probes"]) != {"cancellation", "timeout", "security"}:
        _fail_report()
    if any(type(value) is not bool for value in report["probes"].values()):
        _fail_report()
    if type(report["running_labelled_containers"]) is not int or not -1 <= report["running_labelled_containers"] <= 1024:
        _fail_report()
    return report


def gate_report(status: str, code: str) -> dict[str, Any]:
    if status not in {"skipped", "failed"} or _SAFE_CODE.fullmatch(code) is None:
        raise SafeReportError("unsafe_report")
    empty = _empty_diagnostics()
    empty_delta = diagnostics_delta(empty, empty)
    report = build_report(
        runs=[],
        idempotency={"passed": False, "sandbox_count": 0},
        queue={
            "passed": False,
            "accepted": 0,
            "saturated": 0,
        },
        probes={"cancellation": False, "timeout": False, "security": False},
        running_labelled_containers=-1,
        diagnostics=empty_delta,
        status_override=status,
    )
    report["runs"] = []
    report["failure_type_distribution"] = {}
    report["gates"] = {name: False for name in report["gates"]}
    report["probes"] = {"cancellation": False, "timeout": False, "security": False}
    report["idempotency"] = {"passed": False, "sandbox_count": 0}
    report["queue"] = {
        "passed": False,
        "accepted": 0,
        "saturated": 0,
    }
    report["diagnostics"] = {
        "schema_version": 1,
        "gate_failure": code,
    }
    return validate_report(report)


def _ensure_safe_parent(parent: Path) -> None:
    absolute = parent.absolute()
    missing: list[Path] = []
    current = absolute
    while not current.exists():
        if current.is_symlink():
            raise SafeReportError("unsafe_report_path")
        missing.append(current)
        if current.parent == current:
            raise SafeReportError("unsafe_report_path")
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise SafeReportError("unsafe_report_path")
    for ancestor in [current, *reversed(missing)]:
        if ancestor in missing:
            try:
                ancestor.mkdir(mode=0o700)
            except FileExistsError:
                pass
        if ancestor.is_symlink() or not ancestor.is_dir():
            raise SafeReportError("unsafe_report_path")


def _validate_destination(path: Path) -> None:
    if path.is_symlink():
        raise SafeReportError("unsafe_report_path")
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    raise SafeReportError("report_exists")


def atomic_write_report(path: Path, report: object) -> None:
    checked = validate_report(report)
    if not isinstance(path, Path) or not path.name or path.name in {".", ".."}:
        raise SafeReportError("unsafe_report_path")
    destination = path.absolute()
    _ensure_safe_parent(destination.parent)
    _validate_destination(destination)
    encoded = (json.dumps(checked, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    temporary = destination.parent / f".{destination.name}.{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise SafeReportError("unsafe_report_path")
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _validate_destination(destination)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise SafeReportError("report_exists") from None
        temporary.unlink()
        final = destination.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
        ):
            raise SafeReportError("unsafe_report_path")
        os.chmod(destination, 0o600)
        if os.name == "posix":
            try:
                directory_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    except SafeReportError:
        raise
    except BaseException:
        raise SafeReportError("report_write_failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def atomic_write(path: Path, report: object) -> None:
    atomic_write_report(path, report)


@dataclass(frozen=True)
class LifecycleEvidence:
    trace_id: str
    job_id: str
    events_complete: bool
    terminal_outcome: str | None
    cleanup_status: str | None
    failure_class: str
    tool_versions: Mapping[str, str]
    failure_codes: tuple[str, ...]


def _unavailable_lifecycle() -> LifecycleEvidence:
    return LifecycleEvidence(
        trace_id="trace-unavailable",
        job_id="job-unavailable",
        events_complete=False,
        terminal_outcome=None,
        cleanup_status=None,
        failure_class="unknown_control_plane_failure",
        tool_versions=MappingProxyType({}),
        failure_codes=("events_incomplete",),
    )


def _lifecycle_event_fields_valid(event: Mapping[str, object]) -> bool:
    phase = event.get("phase")
    if type(phase) is not str or phase not in EXPECTED_PHASES:
        return False

    outcome = event.get("outcome")
    failure_class = event.get("failure_class")
    duration = event.get("duration_ms")
    cleanup_status = event.get("cleanup_status")
    vina_version = event.get("vina_version")
    meeko_version = event.get("meeko_version")
    if outcome is not None and (
        type(outcome) is not str
        or outcome not in {"passed", "failed", "cancelled"}
    ):
        return False
    if failure_class is not None and (
        type(failure_class) is not str
        or failure_class not in FAILURE_CLASSES
    ):
        return False
    if type(cleanup_status) is not str or cleanup_status not in {
        "not_started",
        "in_progress",
        "succeeded",
        "failed",
    }:
        return False
    versions = (vina_version, meeko_version)
    if any(
        value is not None
        and (type(value) is not str or _SAFE_VERSION.fullmatch(value) is None)
        for value in versions
    ):
        return False
    versions_present = any(value is not None for value in versions)

    if phase in {"job_received", "queue_entered"}:
        return (
            outcome is None
            and failure_class is None
            and duration is None
            and not versions_present
            and cleanup_status == "not_started"
        )

    if phase.endswith("_started"):
        expected_cleanup_status = (
            "in_progress" if phase == "cleanup_started" else "not_started"
        )
        return (
            outcome is None
            and failure_class is None
            and duration is None
            and not versions_present
            and cleanup_status == expected_cleanup_status
        )

    valid_outcome = type(outcome) is str
    failure_class_valid = failure_class is None or (
        outcome == "failed"
    )

    if phase.endswith("_completed"):
        if (
            not valid_outcome
            or not _safe_number(duration, integer=True)
            or not failure_class_valid
        ):
            return False
        if versions_present and not (
            phase == "validation_completed" and outcome == "passed"
        ):
            return False
        if phase == "cleanup_completed":
            expected_cleanup_status = (
                "succeeded" if outcome == "passed" else "failed"
            )
            return cleanup_status == expected_cleanup_status
        return cleanup_status == "not_started"

    if phase == "job_terminal":
        return (
            valid_outcome
            and failure_class_valid
            and duration is None
            and not versions_present
            and cleanup_status in ("succeeded", "failed")
        )

    return False


def _normalize_operation_pairs(
    events: Sequence[Mapping[str, object]],
    *,
    allow_retry: bool,
) -> tuple[list[str], list[Mapping[str, object]], bool] | None:
    """Fold one bounded retry pair per operation or fail closed."""

    normalized_phases: list[str] = []
    effective_events: list[Mapping[str, object]] = []
    retry_observed = False
    index = 0
    while index < len(events):
        event = events[index]
        phase = event.get("phase")
        if type(phase) is not str:
            return None
        if phase in {"job_received", "queue_entered", "job_terminal"}:
            if event.get("attempt") is not None:
                return None
            normalized_phases.append(phase)
            effective_events.append(event)
            index += 1
            continue
        if not phase.endswith("_started"):
            return None

        operation = phase[:-8]
        if operation not in PHASE_NAMES or index + 1 >= len(events):
            return None
        completed_phase = f"{operation}_completed"
        completed = events[index + 1]
        attempt = event.get("attempt")
        completed_attempt = completed.get("attempt")
        actual_completed_phase = completed.get("phase")
        completed_outcome = completed.get("outcome")
        if (
            type(actual_completed_phase) is not str
            or actual_completed_phase != completed_phase
            or (attempt is not None and (type(attempt) is not int or attempt != 1))
            or (
                completed_attempt is not None
                and (type(completed_attempt) is not int or completed_attempt != 1)
            )
            or (
                attempt is None
                and completed_attempt is not None
            )
            or (
                type(attempt) is int
                and completed_attempt != attempt
            )
            or type(completed_outcome) is not str
            or completed_outcome not in {"passed", "failed", "cancelled"}
        ):
            return None

        final_started = event
        final_completed = completed
        next_index = index + 2
        if next_index < len(events):
            next_phase = events[next_index].get("phase")
            if type(next_phase) is not str:
                return None
        else:
            next_phase = None
        if next_phase == phase:
            if (
                not allow_retry
                or operation not in {"provisioning", "cleanup"}
                or attempt != 1
                or completed_outcome != "failed"
                or next_index + 1 >= len(events)
            ):
                return None
            retry_started = events[next_index]
            retry_completed = events[next_index + 1]
            retry_completed_phase = retry_completed.get("phase")
            retry_completed_outcome = retry_completed.get("outcome")
            if (
                type(retry_started.get("attempt")) is not int
                or retry_started.get("attempt") != 2
                or type(retry_completed_phase) is not str
                or retry_completed_phase != completed_phase
                or type(retry_completed.get("attempt")) is not int
                or retry_completed.get("attempt") != 2
                or type(retry_completed_outcome) is not str
                or retry_completed_outcome
                not in {"passed", "failed", "cancelled"}
            ):
                return None
            final_started = retry_started
            final_completed = retry_completed
            retry_observed = True
            next_index += 2

        normalized_phases.extend((phase, completed_phase))
        effective_events.extend((final_started, final_completed))
        index = next_index

    return normalized_phases, effective_events, retry_observed


def evaluate_lifecycle(
    events: Sequence[Mapping[str, object]],
    *,
    result_succeeded: bool,
) -> LifecycleEvidence:
    """Evaluate one Broker lifecycle using only its diagnostics delta events."""

    identities: set[tuple[str, str]] = set()
    correlated: list[Mapping[str, object]] = []
    for event in events:
        if not isinstance(event, Mapping):
            return _unavailable_lifecycle()
        trace_id = event.get("trace_id")
        job_id = event.get("job_id")
        if (
            type(trace_id) is not str
            or _SAFE_ID.fullmatch(trace_id) is None
            or type(job_id) is not str
            or _SAFE_ID.fullmatch(job_id) is None
        ):
            return _unavailable_lifecycle()
        identities.add((trace_id, job_id))
        correlated.append(event)
    if len(identities) != 1:
        return _unavailable_lifecycle()

    (trace_id, job_id) = next(iter(identities))
    terminals: list[Mapping[str, object]] = []
    for event in correlated:
        phase = event.get("phase")
        if type(phase) is str and phase == "job_terminal":
            terminals.append(event)
    terminal = terminals[0] if len(terminals) == 1 else {}
    terminal_outcome = terminal.get("outcome")
    cleanup_status = terminal.get("cleanup_status")
    fields_semantically_valid = all(
        _lifecycle_event_fields_valid(event) for event in correlated
    )
    normalization = _normalize_operation_pairs(
        correlated,
        allow_retry=type(result_succeeded) is bool,
    )
    normalized_phases, effective_events, retry_observed = normalization or (
        [],
        [],
        False,
    )
    lifecycle_events = effective_events if normalization is not None else correlated

    raw_failure_classes = [
        event.get("failure_class")
        for event in correlated
        if event.get("failure_class") is not None
    ]
    illegal_failure_class = any(
        type(value) is not str or value not in FAILURE_CLASSES
        for value in raw_failure_classes
    )
    effective_failure_classes = [
        event.get("failure_class")
        for event in lifecycle_events
        if event.get("failure_class") is not None
    ]
    legal_failure_classes = {
        value
        for value in effective_failure_classes
        if type(value) is str and value in FAILURE_CLASSES
    }
    if illegal_failure_class or len(legal_failure_classes) > 1:
        failure_class = "unknown_control_plane_failure"
    elif legal_failure_classes:
        failure_class = next(iter(legal_failure_classes))
    else:
        failure_class = "none"

    validations = [
        event
        for event in lifecycle_events
        if type(event.get("phase")) is str
        and event.get("phase") == "validation_completed"
    ]
    tool_versions: dict[str, str] = {}
    if fields_semantically_valid and len(validations) == 1:
        vina_version = validations[0].get("vina_version")
        meeko_version = validations[0].get("meeko_version")
        if (
            type(vina_version) is str
            and _SAFE_VERSION.fullmatch(vina_version) is not None
            and type(meeko_version) is str
            and _SAFE_VERSION.fullmatch(meeko_version) is not None
        ):
            tool_versions = {"vina": vina_version, "meeko": meeko_version}
    passed_validation_provenance_complete = not any(
        type(validation.get("outcome")) is str
        and validation.get("outcome") == "passed"
        for validation in validations
    ) or (
        len(validations) == 1
        and set(tool_versions) == {"vina", "meeko"}
    )

    cleanup_started_events = [
        event
        for event in lifecycle_events
        if type(event.get("phase")) is str
        and event.get("phase") == "cleanup_started"
    ]
    cleanup_completed_events = [
        event
        for event in lifecycle_events
        if type(event.get("phase")) is str
        and event.get("phase") == "cleanup_completed"
    ]
    cleanup_completed = (
        cleanup_completed_events[0] if len(cleanup_completed_events) == 1 else {}
    )
    expected_cleanup_outcome = (
        {
            "succeeded": "passed",
            "failed": "failed",
        }.get(cleanup_status)
        if type(cleanup_status) is str
        else None
    )
    cleanup_consistent = (
        len(cleanup_started_events) == 1
        and type(cleanup_started_events[0].get("cleanup_status")) is str
        and cleanup_started_events[0].get("cleanup_status") == "in_progress"
        and len(cleanup_completed_events) == 1
        and expected_cleanup_outcome is not None
        and type(cleanup_completed.get("outcome")) is str
        and cleanup_completed.get("outcome") == expected_cleanup_outcome
        and type(cleanup_completed.get("cleanup_status")) is str
        and cleanup_completed.get("cleanup_status") == cleanup_status
    )
    cancelled_cleanup_consistent = (
        type(terminal_outcome) is str
        and (
            terminal_outcome != "cancelled"
            or (
                type(cleanup_status) is str
                and cleanup_status == "succeeded"
                and expected_cleanup_outcome == "passed"
            )
        )
    )

    prior_failure_classes: list[str] = []
    for event in lifecycle_events:
        phase = event.get("phase")
        if (
            type(phase) is str
            and phase.endswith("_completed")
            and type(event.get("outcome")) is str
            and event.get("outcome") == "failed"
        ):
            value = event.get("failure_class")
            if value is None:
                prior_failure_classes.append("none")
            elif type(value) is str and value in FAILURE_CLASSES:
                prior_failure_classes.append(value)
    terminal_failure_class = terminal.get("failure_class")
    terminal_failure_consistent = not (
        type(terminal_outcome) is str
        and terminal_outcome == "failed"
        and prior_failure_classes
        and (
            type(terminal_failure_class) is not str
            or terminal_failure_class != prior_failure_classes[-1]
        )
    )
    complete = False
    if type(result_succeeded) is bool and result_succeeded:
        complete = (
            normalized_phases == list(EXPECTED_PHASES)
            and len(terminals) == 1
            and type(terminal_outcome) is str
            and terminal_outcome == "passed"
            and type(cleanup_status) is str
            and cleanup_status == "succeeded"
            and fields_semantically_valid
            and cleanup_consistent
            and len(validations) == 1
            and set(tool_versions) == {"vina", "meeko"}
            and all(
                event.get("outcome") == "passed"
                for event in lifecycle_events
                if str(event.get("phase", "")).endswith("_completed")
            )
            and all(
                event.get("failure_class") is None for event in lifecycle_events
            )
        )
    elif type(result_succeeded) is bool:
        phase_positions: list[int] = []
        for phase in normalized_phases:
            if type(phase) is not str or phase not in EXPECTED_PHASES:
                phase_positions = []
                break
            phase_positions.append(EXPECTED_PHASES.index(phase))
        ordered_lifecycle = bool(phase_positions) and all(
            left < right
            for left, right in zip(phase_positions, phase_positions[1:])
        )
        complete = (
            normalized_phases[0:1] == ["job_received"]
            and normalized_phases[-3:] == list(EXPECTED_PHASES[-3:])
            and len(terminals) == 1
            and type(terminal_outcome) is str
            and terminal_outcome in {"failed", "cancelled"}
            and fields_semantically_valid
            and cleanup_consistent
            and cancelled_cleanup_consistent
            and ordered_lifecycle
            and terminal_failure_consistent
            and passed_validation_provenance_complete
        )

    if not complete:
        failure_codes = ("events_incomplete",)
    elif retry_observed:
        failure_codes = ("broker_retry_observed",)
    else:
        failure_codes = ()
    return LifecycleEvidence(
        trace_id=trace_id,
        job_id=job_id,
        events_complete=complete,
        terminal_outcome=terminal_outcome if type(terminal_outcome) is str else None,
        cleanup_status=cleanup_status if type(cleanup_status) is str else None,
        failure_class=failure_class,
        tool_versions=MappingProxyType(dict(tool_versions)),
        failure_codes=failure_codes,
    )
class StabilityExecutor:
    """One fixed real stability suite; methods never retry a scientific command."""

    def __init__(
        self,
        *,
        runner: object,
        request: Mapping[str, Any] | None = None,
        output_root: Path | None = None,
        diagnostics: object | None = None,
        payload: Mapping[str, Any] | None = None,
        observer_factory: Callable[[], object] = DockerSandboxObserver,
        diagnostics_client: object | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.runner = runner
        selected_request = request if request is not None else payload
        if selected_request is None:
            raise SanitizedRuntimeError("executor_invalid")
        self.payload = dict(selected_request)
        self.observer_factory = observer_factory
        self.diagnostics_client = diagnostics if diagnostics is not None else diagnostics_client
        if self.diagnostics_client is None:
            raise SanitizedRuntimeError("executor_invalid")
        self.project_root = output_root.resolve(strict=False) if output_root is not None else project_root
        self.nonce = secrets.token_hex(16)
        self.measured_count = 0
        self.measured_calls = 0
        self.suite_calls = 1

    def measured_run(self, index: int) -> dict[str, Any]:
        if type(index) is not int or index != self.measured_count + 1 or not 1 <= index <= REPEAT:
            raise SanitizedRuntimeError("measured_index_invalid")
        self.measured_count += 1
        self.measured_calls += 1
        trace_id = f"soak-{self.nonce}-{index:02d}"
        before = self.diagnostics_client.snapshot()
        observer = self.observer_factory()
        disable = getattr(observer, "disable_intrusive_probes", None)
        if callable(disable):
            disable()
        observer.start()
        try:
            result = self.runner.execute(self.payload, job_id=trace_id)
        finally:
            observer.stop()
        after = self.diagnostics_client.snapshot()
        delta = diagnostics_delta(before, after)
        result_succeeded = getattr(result, "success", False) is True
        lifecycle = evaluate_lifecycle(
            delta["events"],
            result_succeeded=result_succeeded,
        )
        projected = _project_result(
            result,
            trace_id=lifecycle.trace_id,
            project_root=self.project_root,
            observer=observer,
            validated_tool_versions=lifecycle.tool_versions,
        )
        versions = projected.get("tool_versions", {})
        failures = [
            *projected.get("failure_codes", []),
            *lifecycle.failure_codes,
        ]
        if (
            result_succeeded
            and projected.get("cleanup_status") != lifecycle.cleanup_status
        ):
            failures.append("cleanup_status_mismatch")
        failure_codes = sorted(set(failures))
        run = {
            "run_index": index,
            "trace_id": lifecycle.trace_id,
            "status": (
                "passed"
                if projected.get("status") == "passed"
                and lifecycle.events_complete
                and not failure_codes
                else "failed"
            ),
            "latency_ms": projected.get("latency_ms", 0),
            "pose_count": projected.get("pose_count", 0),
            "best_energy": projected.get("best_energy"),
            "artifact_present": projected.get("artifact_path") is not None,
            "artifact_path": projected.get("artifact_path"),
            "artifact_sha256": projected.get("artifact_sha256"),
            "artifact_sha256_valid": bool(projected.get("artifact_sha256") and _SHA256.fullmatch(projected["artifact_sha256"])),
            "cleanup_status": lifecycle.cleanup_status,
            "image_digest": projected.get("image_digest"),
            "image_digest_valid": bool(projected.get("image_digest") and _SHA256.fullmatch(projected["image_digest"])),
            "secure_runtime": projected.get("secure_runtime"),
            "vina_version": versions.get("vina") if isinstance(versions, Mapping) else None,
            "meeko_version": versions.get("meeko") if isinstance(versions, Mapping) else None,
            "warning_codes": list(projected.get("warning_codes", [])),
            "failure_codes": failure_codes,
            "failure_class": lifecycle.failure_class,
            "events_complete": lifecycle.events_complete,
        }
        return validate_run(run, project_root=self.project_root)

    execute_measured = measured_run

    def idempotency_probe(self) -> dict[str, object]:
        observer = self.observer_factory()
        disable = getattr(observer, "disable_intrusive_probes", None)
        if callable(disable):
            disable()
        key = f"soak-idempotency-{self.nonce}"
        observer.start()
        try:
            first = self.runner.execute(self.payload, job_id=key)
            second = self.runner.execute(self.payload, job_id=key)
        finally:
            observer.stop()
        count = getattr(observer, "sandbox_count", -1)
        passed = (
            getattr(first, "success", False) is True
            and getattr(second, "success", False) is True
            and type(count) is int
            and count == 1
            and not getattr(observer, "failure_codes", [])
        )
        return {"passed": passed, "sandbox_count": count if type(count) is int and count >= 0 else 0}

    exercise_idempotency_once = idempotency_probe

    def exercise_queue_pressure(self, *, capacity: int, overflow: int) -> dict[str, object]:
        if (capacity, overflow) != (QUEUE_CAPACITY, QUEUE_OVERFLOW):
            raise SanitizedRuntimeError("queue_contract_invalid")
        observer = self.observer_factory()
        disable = getattr(observer, "disable_intrusive_probes", None)
        if callable(disable):
            disable()
        submission_count = QUEUE_CAPACITY + QUEUE_OVERFLOW + 1
        barrier = threading.Barrier(submission_count)

        def submit(index: int) -> object:
            barrier.wait(timeout=30.0)
            return self.runner.execute(self.payload, job_id=f"soak-queue-{self.nonce}-{index}")

        observer.start()
        try:
            with ThreadPoolExecutor(max_workers=submission_count) as pool:
                results = list(pool.map(submit, range(submission_count)))
        finally:
            observer.stop()
        saturated = 0
        accepted = 0
        for result in results:
            details = getattr(getattr(result, "error", None), "details", {})
            reason = details.get("reason") if isinstance(details, Mapping) else None
            if reason == "sandbox_queue_saturated":
                saturated += 1
            elif getattr(result, "success", False) is True:
                accepted += 1
        passed = saturated == 1 and accepted == 9 and not getattr(observer, "failure_codes", [])
        return {
            "passed": passed,
            "accepted": accepted,
            "saturated": saturated,
        }

    def queue_probe(self) -> dict[str, object]:
        return self.exercise_queue_pressure(capacity=8, overflow=1)

    def cancellation_probe(self) -> bool:
        observer = self.observer_factory()
        disable = getattr(observer, "disable_intrusive_probes", None)
        if callable(disable):
            disable()
        cancellation = threading.Event()
        state: list[bool] = []
        observer.start()

        def request() -> None:
            state.append(_request_cancellation_after_inspection(observer, cancellation))

        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        try:
            result = self.runner.execute(
                self.payload,
                job_id=f"soak-cancel-{self.nonce}",
                cancel_event=cancellation,
            )
        finally:
            thread.join(timeout=2.0)
            observer.stop()
        return bool(
            state == [True]
            and getattr(result, "success", True) is False
            and _error_code(result) == "cancelled"
            and not thread.is_alive()
            and not getattr(observer, "failure_codes", [])
        )

    exercise_cancellation = cancellation_probe

    def timeout_probe(self) -> bool:
        import src.docking.sandbox_runner as runner_module

        observer = self.observer_factory()
        disable = getattr(observer, "disable_intrusive_probes", None)
        if callable(disable):
            disable()
        original = runner_module._TOTAL_DEADLINE_SECONDS
        observer.start()
        try:
            runner_module._TOTAL_DEADLINE_SECONDS = 5.0
            result = self.runner.execute(self.payload, job_id=f"soak-timeout-{self.nonce}")
        finally:
            runner_module._TOTAL_DEADLINE_SECONDS = original
            observer.stop()
        return bool(
            getattr(result, "success", True) is False
            and _error_code(result) == "tool_timeout"
            and not getattr(observer, "failure_codes", [])
        )

    exercise_timeout = timeout_probe

    def security_probe(self) -> bool:
        failures, versions, digest = _exercise_sacrificial_security_contract(
            self.runner, self.payload, self.observer_factory
        )
        return bool(
            not failures
            and set(versions) == {"vina", "meeko"}
            and type(digest) is str
            and _SHA256.fullmatch(digest)
        )

    exercise_security_contract = security_probe


def run_stability_soak(
    *,
    executor: object,
    repeat: int = REPEAT,
    diagnostics: object | None = None,
    diagnostics_client: object | None = None,
    orphan_counter: Callable[[], object],
) -> dict[str, Any]:
    if type(repeat) is not int or repeat != REPEAT:
        raise ValueError("stability_soak_requires_30")
    client = diagnostics if diagnostics is not None else diagnostics_client
    if client is None:
        return gate_report("failed", "diagnostics_invalid")
    if type(getattr(executor, "suite_calls", None)) is not int or executor.suite_calls != 1:
        return gate_report("failed", "executor_invalid")
    before: object
    after: object
    try:
        before = client.snapshot()
        validate_diagnostics(before)
    except BaseException:
        return gate_report("failed", "diagnostics_invalid")
    runs: list[dict[str, Any]] = []
    for index in range(1, REPEAT + 1):
        try:
            measured = getattr(executor, "execute_measured", None)
            if not callable(measured):
                measured = getattr(executor, "measured_run")
            run = measured(index)
            runs.append(validate_run(run))
        except BaseException:
            runs.append(_failed_run(index))

    def call_mapping(name: str, fallback: dict[str, object]) -> dict[str, object]:
        try:
            value = getattr(executor, name)()
            return value if type(value) is dict else fallback
        except BaseException:
            return fallback

    idempotency_method = "exercise_idempotency_once" if callable(
        getattr(executor, "exercise_idempotency_once", None)
    ) else "idempotency_probe"
    idempotency = call_mapping(idempotency_method, {"passed": False, "sandbox_count": 0})
    try:
        queue_method = getattr(executor, "exercise_queue_pressure", None)
        if callable(queue_method):
            queue = queue_method(capacity=8, overflow=1)
        else:
            queue = getattr(executor, "queue_probe")()
        if type(queue) is not dict:
            raise SanitizedRuntimeError("queue_contract_invalid")
    except BaseException:
        queue = {"passed": False, "accepted": 0, "saturated": 0}

    probe_results: dict[str, bool] = {}
    for key, method in (
        ("cancellation", "exercise_cancellation"),
        ("timeout", "exercise_timeout"),
        ("security", "exercise_security_contract"),
    ):
        try:
            selected = getattr(executor, method, None)
            if not callable(selected):
                fallback = {
                    "cancellation": "cancellation_probe",
                    "timeout": "timeout_probe",
                    "security": "security_probe",
                }[key]
                selected = getattr(executor, fallback)
            value = selected()
            probe_results[key] = value if type(value) is bool else False
        except BaseException:
            probe_results[key] = False
    try:
        after = client.snapshot()
        validate_diagnostics(after)
    except BaseException:
        after = before
        probe_results = {name: False for name in probe_results}
    try:
        orphan_value = orphan_counter()
        orphans = orphan_value if type(orphan_value) is int and 0 <= orphan_value <= 1024 else -1
    except BaseException:
        orphans = -1
    try:
        delta_method = getattr(client, "delta", None)
        delta = delta_method(before, after) if callable(delta_method) else diagnostics_delta(before, after)
        return build_report(
            runs=runs,
            diagnostics=delta,
            idempotency=idempotency,
            queue=queue,
            probes=probe_results,
            running_labelled_containers=orphans,
        )
    except BaseException:
        return gate_report("failed", "report_invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fixed OpenSandbox stability soak")
    parser.add_argument("--repeat", type=int, default=REPEAT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    platform = os.name if platform_name is None else platform_name

    def finish(report: dict[str, Any], code: int) -> int:
        try:
            atomic_write_report(arguments.report, report)
        except BaseException:
            print("opensandbox_stability_soak=failed")
            return 1 if code == 1 else 2
        diagnostics = report.get("diagnostics")
        reason = (
            diagnostics.get("gate_failure")
            if type(diagnostics) is dict
            else None
        )
        suffix = f" code={reason}" if type(reason) is str else ""
        print(f"opensandbox_stability_soak={report['status']}{suffix}")
        return code

    if type(arguments.repeat) is not int or arguments.repeat != REPEAT:
        return finish(gate_report("skipped", "repeat_invalid"), 2)
    if environment.get(GATE) != "1":
        return finish(gate_report("skipped", "stability_gate_disabled"), 2)
    if platform != "posix":
        return finish(gate_report("skipped", "posix_required"), 2)
    try:
        deployment = _deployment_failures()
    except BaseException:
        deployment = ["deployment_validation_failed"]
    if type(deployment) is not list or deployment:
        return finish(gate_report("failed", "deployment_validation_failed"), 1)
    socket_value = environment.get("MEDCHAT_SANDBOX_BROKER_SOCKET")
    if (
        type(socket_value) is not str
        or not PurePosixPath(socket_value).is_absolute()
        or "\\" in socket_value
        or httpx is None
    ):
        return finish(gate_report("failed", "runtime_unavailable"), 1)
    try:
        output_root = PROJECT_ROOT / "outputs/opensandbox_stability"
        output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        runner = SandboxDockingRunner.from_env(output_root)
        client = BrokerDiagnosticsClient(socket_value)
        executor = StabilityExecutor(
            runner=runner,
            request=_payload(
                PROJECT_ROOT / "data/samples/MAGL_5zun.pdb",
                PROJECT_ROOT / "data/samples/5.sdf",
                (5.99, 3.01, 17.345),
                (20.0, 20.0, 20.0),
            ),
            output_root=PROJECT_ROOT,
            diagnostics=client,
        )
        report = run_stability_soak(
            executor=executor,
            repeat=arguments.repeat,
            diagnostics=client,
            orphan_counter=running_labelled_container_count,
        )
    except BaseException:
        report = gate_report("failed", "runtime_failed")
    return finish(report, 0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
