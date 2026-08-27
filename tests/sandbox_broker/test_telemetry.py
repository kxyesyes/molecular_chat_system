from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import CollectorRegistry
from pydantic import ValidationError

from src.sandbox_broker.telemetry import (
    BrokerTelemetry,
    BrokerTelemetryEvent,
    FailureClass,
    Phase,
    _contains_credential_like,
)


class Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        self.calls.append((message, args, kwargs))


def valid_event_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "trace_id": "trace-1",
        "job_id": "a" * 32,
        "phase": Phase.PROVISIONING_STARTED,
        "attempt": 1,
        "outcome": None,
        "failure_class": None,
        "duration_ms": None,
        "queue_depth": 0,
        "active_job_count": 0,
        "cleanup_status": "not_started",
        "image_digest": "b" * 64,
        "vina_version": None,
        "meeko_version": None,
    }
    payload.update(overrides)
    return payload


def event(**overrides: object) -> BrokerTelemetryEvent:
    return BrokerTelemetryEvent.model_validate(valid_event_payload(**overrides))


def test_enums_have_only_the_fixed_contract_values() -> None:
    assert {item.value for item in Phase} == {
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
    }
    assert {item.value for item in FailureClass} == {
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


def test_event_schema_is_frozen_exact_and_forbids_extra_fields() -> None:
    telemetry_event = event()

    assert set(BrokerTelemetryEvent.model_fields) == {
        "schema_version",
        "trace_id",
        "job_id",
        "phase",
        "attempt",
        "outcome",
        "failure_class",
        "duration_ms",
        "queue_depth",
        "active_job_count",
        "cleanup_status",
        "image_digest",
        "vina_version",
        "meeko_version",
    }
    with pytest.raises(ValidationError):
        telemetry_event.phase = Phase.JOB_RECEIVED
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(
            valid_event_payload(unexpected="value")
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("phase", "user-controlled-phase"),
        ("outcome", "maybe"),
        ("failure_class", "HTTP 502"),
        ("cleanup_status", "pending"),
    ],
)
def test_event_schema_rejects_invalid_fixed_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(valid_event_payload(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", ""),
        ("trace_id", "-starts-with-punctuation"),
        ("trace_id", "contains space"),
        ("trace_id", "contains/path"),
        ("trace_id", "a" * 129),
        ("job_id", "job?query"),
        ("job_id", "sk-" + "x" * 16),
        ("vina_version", "sk-" + "v" * 16),
    ],
)
def test_event_schema_rejects_unsafe_ids_and_credentials(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(valid_event_payload(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", "token:abcdefghijk"),
        ("trace_id", "api-key:abcdefghijk"),
        ("job_id", "password:abcdefghijk"),
        ("job_id", "secret:abcdefghijk"),
        ("job_id", "authorization:Bearer_abcdefghijk"),
        ("vina_version", "Bearer abcdefghijk"),
    ],
)
def test_event_schema_rejects_local_credential_like_assignments(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(valid_event_payload(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", "AKIA" + "A1" * 8),
        ("job_id", "ASIA" + "B2" * 8),
        ("vina_version", "ghp_" + "A1" * 18),
        (
            "meeko_version",
            "github_pat_" + "B2" * 11 + "_" + "C3" * 20,
        ),
    ],
)
def test_event_schema_rejects_credential_families_in_every_string_field(
    field: str,
    value: str,
) -> None:
    assert _contains_credential_like(value) is True
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(valid_event_payload(**{field: value}))


@pytest.mark.parametrize(
    "value",
    [
        "AKIA" + "A1" * 8,
        "ASIA" + "B2" * 8,
        "ghp_" + "A1" * 18,
        "gho_" + "B2" * 18,
        "ghu_" + "C3" * 18,
        "ghs_" + "D4" * 18,
        "ghr_" + "E5" * 18,
        "github_pat_" + "F6" * 11 + "_" + "G7" * 20,
    ],
)
def test_local_detector_rejects_all_required_credential_families(
    value: str,
) -> None:
    assert _contains_credential_like(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "AKIA-release-1",
        "ASIA.2026.08",
        "AKIA" + "A" * 15,
        "ghp_release_candidate",
        "gho_" + "A" * 19,
        "github_pat_schema",
        "Vina 1.2.3",
        "Meeko release_2026",
    ],
)
def test_local_detector_preserves_normal_identifiers_and_versions(
    value: str,
) -> None:
    assert _contains_credential_like(value) is False


def test_local_credential_detector_has_a_fixed_scan_bound() -> None:
    value = "v" * 256 + "AKIA" + "A1" * 8

    assert _contains_credential_like(value) is False


def test_importing_telemetry_does_not_initialize_agent_persistence() -> None:
    workspace = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import src.sandbox_broker.telemetry; "
                "assert 'src.agent.persistence' not in sys.modules"
            ),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("attempt", [0, 3, True, 1.0, "1"])
def test_event_schema_rejects_invalid_attempts(attempt: object) -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(valid_event_payload(attempt=attempt))


@pytest.mark.parametrize(
    "duration",
    [math.inf, -math.inf, math.nan, True, 1.0, "125", -1, 300_001],
)
def test_event_schema_rejects_non_finite_or_wrong_duration_types(
    duration: object,
) -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(
            valid_event_payload(duration_ms=duration)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_digest", "b" * 63),
        ("image_digest", "B" * 64),
        ("image_digest", "g" * 64),
        ("vina_version", "v" * 129),
        ("vina_version", "1.2/path"),
        ("meeko_version", "1.2:unsafe"),
        ("meeko_version", True),
    ],
)
def test_event_schema_rejects_invalid_digest_and_versions(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(valid_event_payload(**{field: value}))


def test_phase_timer_emits_matching_events_and_finishes_exactly_once() -> None:
    clock = Clock()
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)

    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="provisioning",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    clock.advance(0.125)
    completed = telemetry.finish_phase(token, outcome="passed")

    assert completed.phase is Phase.PROVISIONING_COMPLETED
    assert completed.duration_ms == 125
    assert [item["phase"] for item in telemetry.snapshot()["recent_events"]] == [
        "provisioning_started",
        "provisioning_completed",
    ]
    with pytest.raises(RuntimeError, match="already finished"):
        telemetry.finish_phase(token, outcome="passed")


def test_recent_events_and_phase_latency_retention_are_bounded() -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    for index in range(1_030):
        telemetry.emit(event(job_id=f"job-{index}"))

    recent_events = telemetry.snapshot()["recent_events"]
    assert len(recent_events) == 1_024
    assert recent_events[0]["job_id"] == "job-6"
    assert recent_events[-1]["job_id"] == "job-1029"

    clock = Clock()
    timed = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)
    for index in range(260):
        token = timed.start_phase(
            trace_id=f"trace-{index}",
            job_id=f"job-{index}",
            phase="command",
            attempt=1,
            queue_depth=0,
            active_job_count=1,
            image_digest="c" * 64,
        )
        clock.advance(0.001)
        timed.finish_phase(token, outcome="passed")

    latencies = timed.snapshot()["phase_latency_ms"]
    assert set(latencies) == {"command|passed"}
    assert len(latencies["command|passed"]) == 256
    assert latencies["command|passed"] == [1] * 256


def test_prometheus_projection_has_exact_family_names_and_label_keys() -> None:
    registry = CollectorRegistry()
    clock = Clock()
    telemetry = BrokerTelemetry(registry=registry, monotonic=clock)
    token = telemetry.start_phase(
        trace_id="trace-private",
        job_id="job-private",
        phase="upload",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="d" * 64,
    )
    clock.advance(0.125)
    telemetry.finish_phase(token, outcome="passed")

    telemetry.record_control_plane_failure(
        operation="create",
        failure_class=FailureClass.PROXY_502,
    )
    telemetry.record_retry("metadata", "attempted")
    telemetry.record_terminal("failed", FailureClass.PROXY_502)
    telemetry.update_runtime_gauges(
        queue_depth=8,
        active_job_count=1,
        cleanup_task_count=2,
        isolated_task_count=0,
        breaker_state="open",
    )

    families = {family.name: family for family in registry.collect()}
    assert set(families) == {
        "medchat_sandbox_jobs",
        "medchat_sandbox_phase_duration_seconds",
        "medchat_sandbox_control_plane_failures",
        "medchat_sandbox_retry",
        "medchat_sandbox_queue_depth",
        "medchat_sandbox_active_jobs",
        "medchat_sandbox_cleanup_tasks",
        "medchat_sandbox_isolated_tasks",
        "medchat_sandbox_circuit_breaker_state",
    }
    expected_label_keys = {
        "medchat_sandbox_jobs": {"terminal_status", "failure_class"},
        "medchat_sandbox_control_plane_failures": {
            "operation",
            "failure_class",
        },
        "medchat_sandbox_retry": {"operation", "outcome"},
    }
    for family_name, label_keys in expected_label_keys.items():
        assert family_name in families
        assert families[family_name].samples
        assert {
            frozenset(sample.labels) for sample in families[family_name].samples
        } == {frozenset(label_keys)}

    histogram = families["medchat_sandbox_phase_duration_seconds"]
    assert histogram.samples
    for sample in histogram.samples:
        expected = {"phase", "outcome", "le"}
        if not sample.name.endswith("_bucket"):
            expected.remove("le")
        assert set(sample.labels) == expected
    for gauge_name in {
        "medchat_sandbox_queue_depth",
        "medchat_sandbox_active_jobs",
        "medchat_sandbox_cleanup_tasks",
        "medchat_sandbox_isolated_tasks",
        "medchat_sandbox_circuit_breaker_state",
    }:
        assert [sample.labels for sample in families[gauge_name].samples] == [{}]

    sample_names = {
        sample.name
        for family in families.values()
        for sample in family.samples
    }
    assert sample_names == {
        "medchat_sandbox_jobs_total",
        "medchat_sandbox_jobs_created",
        "medchat_sandbox_phase_duration_seconds_bucket",
        "medchat_sandbox_phase_duration_seconds_count",
        "medchat_sandbox_phase_duration_seconds_sum",
        "medchat_sandbox_phase_duration_seconds_created",
        "medchat_sandbox_control_plane_failures_total",
        "medchat_sandbox_control_plane_failures_created",
        "medchat_sandbox_retry_total",
        "medchat_sandbox_retry_created",
        "medchat_sandbox_queue_depth",
        "medchat_sandbox_active_jobs",
        "medchat_sandbox_cleanup_tasks",
        "medchat_sandbox_isolated_tasks",
        "medchat_sandbox_circuit_breaker_state",
    }
    encoded = telemetry.prometheus_text()
    assert b"trace-private" not in encoded
    assert b"job-private" not in encoded
    assert b"sk-" not in encoded


def test_public_attributes_cannot_expose_collectors_or_inject_metric_labels() -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    public_collectors = {
        name
        for name in dir(telemetry)
        if not name.startswith("_")
        and callable(getattr(getattr(telemetry, name), "labels", None))
    }

    assert public_collectors == set()
    assert not hasattr(telemetry, "registry")

    with pytest.raises(ValueError):
        telemetry.record_control_plane_failure(
            operation="trace-injected",
            failure_class=FailureClass.PROXY_502,
        )
    with pytest.raises(ValueError):
        telemetry.record_retry("job-injected", "attempted")
    with pytest.raises(ValueError):
        telemetry.record_terminal("credential-injected")

    encoded = telemetry.prometheus_text()
    assert b"trace-injected" not in encoded
    assert b"job-injected" not in encoded
    assert b"credential-injected" not in encoded


def test_snapshot_has_strict_shape_and_runtime_bounds() -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    telemetry.update_runtime_gauges(
        queue_depth=8,
        active_job_count=1,
        cleanup_task_count=2,
        isolated_task_count=0,
        breaker_state="open",
    )

    snapshot = telemetry.snapshot()

    assert set(snapshot) == {
        "schema_version",
        "counters",
        "phase_latency_ms",
        "runtime",
        "recent_events",
    }
    assert snapshot["runtime"] == {
        "queue_depth": 8,
        "active_job_count": 1,
        "cleanup_task_count": 2,
        "isolated_task_count": 0,
        "breaker_state": "open",
    }


def test_emit_logs_only_the_validated_structured_event() -> None:
    logger = CapturingLogger()
    telemetry = BrokerTelemetry(
        registry=CollectorRegistry(),
        logger=logger,  # type: ignore[arg-type]
    )
    telemetry_event = event(
        phase=Phase.COMMAND_COMPLETED,
        outcome="failed",
        failure_class=FailureClass.COMMAND_TIMEOUT,
        duration_ms=300_000,
    )

    telemetry.emit(telemetry_event)

    expected = telemetry_event.model_dump(mode="json")
    assert telemetry.snapshot()["recent_events"] == [expected]
    assert logger.calls == [
        ("sandbox_broker_event", (), {"extra": {"event": expected}})
    ]
    assert set(expected) == set(BrokerTelemetryEvent.model_fields)
    assert not {
        "exception",
        "content",
        "path",
        "headers",
        "stdout",
        "stderr",
    }.intersection(expected)


@pytest.mark.parametrize(
    "call",
    [
        lambda telemetry: telemetry.emit({}),
        lambda telemetry: telemetry.start_phase(
            trace_id="trace-1",
            job_id="a" * 32,
            phase="download",
            attempt=1,
            queue_depth=0,
            active_job_count=1,
            image_digest="b" * 64,
        ),
        lambda telemetry: telemetry.record_control_plane_failure(
            operation="login",
            failure_class=FailureClass.CONNECTION_FAILED,
        ),
        lambda telemetry: telemetry.record_control_plane_failure(
            operation="create",
            failure_class="connection_failed",
        ),
        lambda telemetry: telemetry.record_retry("upload", "attempted"),
        lambda telemetry: telemetry.record_retry("create", "pending"),
        lambda telemetry: telemetry.record_terminal("unknown"),
        lambda telemetry: telemetry.record_terminal("failed", "proxy_502"),
        lambda telemetry: telemetry.update_runtime_gauges(
            queue_depth=9,
            active_job_count=1,
            cleanup_task_count=0,
            isolated_task_count=0,
            breaker_state="closed",
        ),
        lambda telemetry: telemetry.update_runtime_gauges(
            queue_depth=0,
            active_job_count=True,
            cleanup_task_count=0,
            isolated_task_count=0,
            breaker_state="closed",
        ),
        lambda telemetry: telemetry.update_runtime_gauges(
            queue_depth=0,
            active_job_count=0,
            cleanup_task_count=-1,
            isolated_task_count=0,
            breaker_state="closed",
        ),
        lambda telemetry: telemetry.update_runtime_gauges(
            queue_depth=0,
            active_job_count=0,
            cleanup_task_count=0,
            isolated_task_count=0,
            breaker_state="broken",
        ),
    ],
)
def test_invalid_api_calls_raise_value_error(call: Any) -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    with pytest.raises(ValueError):
        call(telemetry)


def test_invalid_clock_and_finish_calls_raise_without_finishing_token() -> None:
    with pytest.raises(ValueError, match="clock"):
        BrokerTelemetry(registry=CollectorRegistry(), monotonic=object())  # type: ignore[arg-type]

    clock = Clock()
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)
    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="cleanup",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    with pytest.raises(ValueError, match="outcome"):
        telemetry.finish_phase(token, outcome="unknown")
    assert token.finished is False

    clock.advance(-1.0)
    with pytest.raises(ValueError, match="duration"):
        telemetry.finish_phase(token, outcome="failed")
    assert token.finished is False


def test_large_finite_phase_duration_is_safely_capped() -> None:
    clock = Clock()
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)
    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="validation",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    clock.value = 1e308

    completed = telemetry.finish_phase(token, outcome="passed")

    assert completed.duration_ms == 300_000
