from __future__ import annotations

import logging
import math
import subprocess
import sys
import threading
from dataclasses import FrozenInstanceError
from enum import IntEnum
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.parser import text_string_to_metric_families
from pydantic import ValidationError

import src.sandbox_broker.telemetry as telemetry_module
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


class NumericValue(IntEnum):
    ONE = 1


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
        ("schema_version", True),
        ("schema_version", b"1"),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("schema_version", NumericValue.ONE),
        ("trace_id", True),
        ("trace_id", b"trace-1"),
        ("trace_id", 123),
        ("trace_id", NumericValue.ONE),
        ("job_id", b"job-1"),
        ("phase", "provisioning_started"),
        ("phase", b"provisioning_started"),
        ("phase", "1"),
        ("phase", 1),
        ("phase", NumericValue.ONE),
        ("failure_class", "none"),
        ("failure_class", b"none"),
        ("failure_class", NumericValue.ONE),
        ("outcome", b"passed"),
        ("outcome", 1),
        ("outcome", NumericValue.ONE),
        ("cleanup_status", b"not_started"),
        ("cleanup_status", 1),
        ("image_digest", b"b" * 64),
        ("image_digest", 123),
        ("image_digest", NumericValue.ONE),
        ("vina_version", b"1.2.3"),
        ("vina_version", 123),
        ("vina_version", NumericValue.ONE),
        ("meeko_version", b"1.2.3"),
        ("meeko_version", 123),
        ("meeko_version", NumericValue.ONE),
    ],
)
def test_event_schema_rejects_all_coercible_input_types(
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
    "name",
    [
        "authorization",
        "token",
        "api_key",
        "api-key",
        "apikey",
        "password",
        "secret",
        "access_key",
        "accesskey",
        "client_secret",
        "clientsecret",
        "passwd",
        "pwd",
        "credential",
        "credentials",
        "cookie",
        "session_cookie",
        "private_key",
    ],
)
@pytest.mark.parametrize("delimiter", [":", " = "])
def test_local_detector_rejects_all_bounded_assignment_families(
    name: str,
    delimiter: str,
) -> None:
    value = f"{name.upper()}{delimiter}Abcd1234"

    assert _contains_credential_like(value) is True


def test_local_detector_rejects_short_assignment_values() -> None:
    assert _contains_credential_like("pwd=x") is True


@pytest.mark.parametrize(
    "value",
    [
        "access_key_rotation",
        "accesskey format",
        "client_secretary",
        "clientsecret migration",
        "password_policy",
        "passwdless",
        "pwdless-login",
        "credential schema",
        "credentials guide",
        "cookie format",
        "session_cookie_policy",
        "private_key_format",
        "token bucket",
        "secret sharing",
        "access_key         :value",
    ],
)
def test_local_detector_allows_ordinary_assignment_family_words(
    value: str,
) -> None:
    assert _contains_credential_like(value) is False


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


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"phase": Phase.UPLOAD_STARTED, "outcome": "passed"},
            id="started-outcome",
        ),
        pytest.param(
            {
                "phase": Phase.COMMAND_STARTED,
                "failure_class": FailureClass.NONE,
            },
            id="started-failure",
        ),
        pytest.param(
            {"phase": Phase.VALIDATION_STARTED, "duration_ms": 0},
            id="started-duration",
        ),
        pytest.param(
            {"phase": Phase.PROVISIONING_STARTED, "vina_version": "1.2"},
            id="started-vina-version",
        ),
        pytest.param(
            {"phase": Phase.PROVISIONING_STARTED, "meeko_version": "0.6"},
            id="started-meeko-version",
        ),
        pytest.param(
            {"phase": Phase.CLEANUP_STARTED, "cleanup_status": "not_started"},
            id="cleanup-started-status",
        ),
        pytest.param(
            {
                "phase": Phase.PROVISIONING_STARTED,
                "cleanup_status": "in_progress",
            },
            id="non-cleanup-started-status",
        ),
        pytest.param(
            {"phase": Phase.UPLOAD_COMPLETED, "duration_ms": 1},
            id="completed-missing-outcome",
        ),
        pytest.param(
            {"phase": Phase.UPLOAD_COMPLETED, "outcome": "passed"},
            id="completed-missing-duration",
        ),
        pytest.param(
            {
                "phase": Phase.COMMAND_COMPLETED,
                "outcome": "passed",
                "failure_class": FailureClass.COMMAND_TIMEOUT,
                "duration_ms": 1,
            },
            id="passed-completed-failure",
        ),
        pytest.param(
            {
                "phase": Phase.COMMAND_COMPLETED,
                "outcome": "cancelled",
                "failure_class": FailureClass.COMMAND_TIMEOUT,
                "duration_ms": 1,
            },
            id="cancelled-completed-failure",
        ),
        pytest.param(
            {
                "phase": Phase.COMMAND_COMPLETED,
                "outcome": "passed",
                "duration_ms": 1,
                "vina_version": "1.2",
            },
            id="version-on-non-validation",
        ),
        pytest.param(
            {
                "phase": Phase.VALIDATION_COMPLETED,
                "outcome": "failed",
                "failure_class": FailureClass.RESOURCE_LIMIT,
                "duration_ms": 1,
                "meeko_version": "0.6",
            },
            id="version-on-failed-validation",
        ),
        pytest.param(
            {
                "phase": Phase.CLEANUP_COMPLETED,
                "outcome": "passed",
                "duration_ms": 1,
                "cleanup_status": "not_started",
            },
            id="cleanup-completed-status-required",
        ),
        pytest.param(
            {
                "phase": Phase.CLEANUP_COMPLETED,
                "outcome": "passed",
                "duration_ms": 1,
                "cleanup_status": "failed",
            },
            id="passed-cleanup-status",
        ),
        pytest.param(
            {
                "phase": Phase.CLEANUP_COMPLETED,
                "outcome": "failed",
                "failure_class": FailureClass.DESTROY_FAILED,
                "duration_ms": 1,
                "cleanup_status": "succeeded",
            },
            id="failed-cleanup-status",
        ),
        pytest.param(
            {
                "phase": Phase.CLEANUP_COMPLETED,
                "outcome": "cancelled",
                "duration_ms": 1,
                "cleanup_status": "succeeded",
            },
            id="cancelled-cleanup-status",
        ),
        pytest.param(
            {
                "phase": Phase.PROVISIONING_COMPLETED,
                "outcome": "passed",
                "duration_ms": 1,
                "cleanup_status": "succeeded",
            },
            id="non-cleanup-completed-status",
        ),
        pytest.param(
            {"phase": Phase.JOB_RECEIVED, "outcome": "passed"},
            id="job-received-outcome",
        ),
        pytest.param(
            {
                "phase": Phase.QUEUE_ENTERED,
                "failure_class": FailureClass.NONE,
            },
            id="queue-entered-failure",
        ),
        pytest.param(
            {"phase": Phase.JOB_RECEIVED, "duration_ms": 0},
            id="job-received-duration",
        ),
        pytest.param(
            {"phase": Phase.QUEUE_ENTERED, "vina_version": "1.2"},
            id="queue-entered-version",
        ),
        pytest.param(
            {"phase": Phase.JOB_RECEIVED, "cleanup_status": "in_progress"},
            id="job-received-cleanup-status",
        ),
        pytest.param(
            {"phase": Phase.JOB_TERMINAL, "cleanup_status": "succeeded"},
            id="terminal-missing-outcome",
        ),
        pytest.param(
            {
                "phase": Phase.JOB_TERMINAL,
                "outcome": "passed",
                "duration_ms": 1,
                "cleanup_status": "succeeded",
            },
            id="terminal-duration",
        ),
        pytest.param(
            {
                "phase": Phase.JOB_TERMINAL,
                "outcome": "passed",
                "vina_version": "1.2",
                "cleanup_status": "succeeded",
            },
            id="terminal-version",
        ),
        pytest.param(
            {"phase": Phase.JOB_TERMINAL, "outcome": "passed"},
            id="terminal-cleanup-status",
        ),
        pytest.param(
            {
                "phase": Phase.JOB_TERMINAL,
                "outcome": "passed",
                "failure_class": FailureClass.SERVER_500,
                "cleanup_status": "succeeded",
            },
            id="passed-terminal-failure",
        ),
        pytest.param(
            {
                "phase": Phase.JOB_TERMINAL,
                "outcome": "cancelled",
                "failure_class": FailureClass.SERVER_500,
                "cleanup_status": "failed",
            },
            id="cancelled-terminal-failure",
        ),
    ],
)
def test_event_schema_rejects_incoherent_lifecycle_combinations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        BrokerTelemetryEvent.model_validate(valid_event_payload(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"phase": Phase.JOB_RECEIVED},
        {"phase": Phase.QUEUE_ENTERED},
        {
            "phase": Phase.JOB_TERMINAL,
            "outcome": "passed",
            "cleanup_status": "succeeded",
        },
        {
            "phase": Phase.JOB_TERMINAL,
            "outcome": "failed",
            "failure_class": FailureClass.SERVER_500,
            "cleanup_status": "failed",
        },
        {
            "phase": Phase.JOB_TERMINAL,
            "outcome": "cancelled",
            "cleanup_status": "failed",
        },
        {
            "phase": Phase.VALIDATION_COMPLETED,
            "outcome": "passed",
            "duration_ms": 1,
            "vina_version": "1.2",
            "meeko_version": "0.6",
        },
        {
            "phase": Phase.CLEANUP_COMPLETED,
            "outcome": "passed",
            "duration_ms": 1,
            "cleanup_status": "succeeded",
        },
        {
            "phase": Phase.CLEANUP_COMPLETED,
            "outcome": "failed",
            "failure_class": FailureClass.DESTROY_FAILED,
            "duration_ms": 1,
            "cleanup_status": "failed",
        },
        {
            "phase": Phase.CLEANUP_COMPLETED,
            "outcome": "cancelled",
            "duration_ms": 1,
            "cleanup_status": "failed",
        },
    ],
)
def test_event_schema_accepts_coherent_lifecycle_combinations(
    overrides: dict[str, object],
) -> None:
    telemetry_event = BrokerTelemetryEvent.model_validate(
        valid_event_payload(**overrides)
    )

    assert telemetry_event.phase is overrides["phase"]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {
                "phase": Phase.VALIDATION_COMPLETED,
                "outcome": "failed",
                "duration_ms": 1,
            },
            id="failed-scientific-validation-without-control-plane-class",
        ),
        pytest.param(
            {
                "phase": Phase.UPLOAD_COMPLETED,
                "outcome": "failed",
                "failure_class": FailureClass.NONE,
                "duration_ms": 1,
            },
            id="failed-artifact-business-outcome-with-none-class",
        ),
        pytest.param(
            {
                "phase": Phase.JOB_TERMINAL,
                "outcome": "failed",
                "cleanup_status": "failed",
            },
            id="failed-terminal-without-control-plane-class",
        ),
        pytest.param(
            {
                "phase": Phase.JOB_TERMINAL,
                "outcome": "failed",
                "failure_class": FailureClass.NONE,
                "cleanup_status": "failed",
            },
            id="failed-terminal-with-none-class",
        ),
        pytest.param(
            {
                "phase": Phase.COMMAND_COMPLETED,
                "outcome": "failed",
                "failure_class": FailureClass.COMMAND_TIMEOUT,
                "duration_ms": 1,
            },
            id="failed-control-plane-outcome-with-concrete-class",
        ),
    ],
)
def test_event_schema_accepts_failed_outcomes_with_truthful_classification(
    overrides: dict[str, object],
) -> None:
    telemetry_event = BrokerTelemetryEvent.model_validate(
        valid_event_payload(**overrides)
    )

    assert telemetry_event.outcome == "failed"


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


def test_phase_token_identity_is_frozen_and_has_no_public_finished_state() -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="provisioning",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )

    with pytest.raises((FrozenInstanceError, AttributeError)):
        token.trace_id = "changed"
    with pytest.raises((FrozenInstanceError, AttributeError)):
        token.phase = "command"
    assert not hasattr(token, "finished")


def test_sixteen_threads_can_finish_one_token_exactly_once() -> None:
    registry = CollectorRegistry()
    clock = Clock()
    telemetry = BrokerTelemetry(registry=registry, monotonic=clock)
    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="command",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    clock.advance(0.125)
    barrier = threading.Barrier(16)
    result_lock = threading.Lock()
    successes: list[BrokerTelemetryEvent] = []
    failures: list[BaseException] = []

    def finish() -> None:
        barrier.wait()
        try:
            completed = telemetry.finish_phase(token, outcome="passed")
        except BaseException as failure:
            with result_lock:
                failures.append(failure)
        else:
            with result_lock:
                successes.append(completed)

    threads = [threading.Thread(target=finish) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) == 1
    assert len(failures) == 15
    assert all(isinstance(failure, RuntimeError) for failure in failures)
    completed_events = [
        item
        for item in telemetry.snapshot()["recent_events"]
        if item["phase"] == "command_completed"
    ]
    assert len(completed_events) == 1
    histogram = next(
        family
        for family in registry.collect()
        if family.name == "medchat_sandbox_phase_duration_seconds"
    )
    count_samples = [
        sample
        for sample in histogram.samples
        if sample.name == "medchat_sandbox_phase_duration_seconds_count"
    ]
    assert len(count_samples) == 1
    assert count_samples[0].value == 1


def test_cleanup_finish_requires_explicit_coherent_status() -> None:
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
    clock.advance(0.125)

    with pytest.raises(ValueError, match="cleanup status"):
        telemetry.finish_phase(token, outcome="passed")

    completed = telemetry.finish_phase(
        token,
        outcome="passed",
        cleanup_status="succeeded",
    )
    assert completed.cleanup_status == "succeeded"


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


def test_runtime_gauge_group_and_observers_are_atomic(monkeypatch) -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    telemetry.update_runtime_gauges(
        queue_depth=0,
        active_job_count=0,
        cleanup_task_count=0,
        isolated_task_count=0,
        breaker_state="closed",
    )
    set_entered = threading.Event()
    release_set = threading.Event()
    original_set = telemetry._active_jobs.set

    def blocking_active_set(value: int) -> None:
        set_entered.set()
        assert release_set.wait(timeout=2)
        original_set(value)

    monkeypatch.setattr(telemetry._active_jobs, "set", blocking_active_set)
    errors: list[BaseException] = []

    def update() -> None:
        try:
            telemetry.update_runtime_gauges(
                queue_depth=8,
                active_job_count=1,
                cleanup_task_count=2,
                isolated_task_count=3,
                breaker_state="open",
            )
        except BaseException as failure:
            errors.append(failure)

    writer = threading.Thread(target=update, daemon=True)
    writer.start()
    assert set_entered.wait(timeout=1)

    snapshot_started = threading.Event()
    snapshot_done = threading.Event()
    scrape_started = threading.Event()
    scrape_done = threading.Event()
    snapshot_result: dict[str, object] = {}
    scrape_result: dict[str, bytes] = {}

    def read_snapshot() -> None:
        snapshot_started.set()
        snapshot_result["value"] = telemetry.snapshot()
        snapshot_done.set()

    def scrape_metrics() -> None:
        scrape_started.set()
        scrape_result["value"] = telemetry.prometheus_text()
        scrape_done.set()

    snapshot_reader = threading.Thread(target=read_snapshot, daemon=True)
    scraper = threading.Thread(target=scrape_metrics, daemon=True)
    snapshot_reader.start()
    scraper.start()
    assert snapshot_started.wait(timeout=1)
    assert scrape_started.wait(timeout=1)
    snapshot_was_blocked = not snapshot_done.wait(timeout=0.2)
    scrape_was_blocked = not scrape_done.wait(timeout=0.2)
    release_set.set()
    writer.join(timeout=2)
    snapshot_reader.join(timeout=2)
    scraper.join(timeout=2)

    assert snapshot_was_blocked is True
    assert scrape_was_blocked is True
    assert not errors
    assert not writer.is_alive()
    assert not snapshot_reader.is_alive()
    assert not scraper.is_alive()
    assert snapshot_result["value"]["runtime"] == {
        "queue_depth": 8,
        "active_job_count": 1,
        "cleanup_task_count": 2,
        "isolated_task_count": 3,
        "breaker_state": "open",
    }
    gauge_values = {
        sample.name: sample.value
        for family in text_string_to_metric_families(
            scrape_result["value"].decode("utf-8")
        )
        for sample in family.samples
        if not sample.labels
    }
    assert gauge_values == {
        "medchat_sandbox_queue_depth": 8,
        "medchat_sandbox_active_jobs": 1,
        "medchat_sandbox_cleanup_tasks": 2,
        "medchat_sandbox_isolated_tasks": 3,
        "medchat_sandbox_circuit_breaker_state": 1,
    }


@pytest.mark.parametrize("kind", ["control_failure", "retry", "terminal"])
def test_counter_projection_and_snapshot_share_one_atomic_generation(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    telemetry = BrokerTelemetry(registry=CollectorRegistry())
    if kind == "control_failure":
        metric = telemetry._control_failures.labels(
            operation="create",
            failure_class="proxy_502",
        )
        mutate = lambda: telemetry.record_control_plane_failure(
            operation="create",
            failure_class=FailureClass.PROXY_502,
        )
        counter_key = "control_failure|create|proxy_502"
        sample_name = "medchat_sandbox_control_plane_failures_total"
        sample_labels = {"operation": "create", "failure_class": "proxy_502"}
    elif kind == "retry":
        metric = telemetry._retries.labels(operation="create", outcome="attempted")
        mutate = lambda: telemetry.record_retry("create", "attempted")
        counter_key = "retry|create|attempted"
        sample_name = "medchat_sandbox_retry_total"
        sample_labels = {"operation": "create", "outcome": "attempted"}
    else:
        metric = telemetry._jobs.labels(
            terminal_status="failed",
            failure_class="proxy_502",
        )
        mutate = lambda: telemetry.record_terminal(
            "failed",
            FailureClass.PROXY_502,
        )
        counter_key = "terminal|failed|proxy_502"
        sample_name = "medchat_sandbox_jobs_total"
        sample_labels = {
            "terminal_status": "failed",
            "failure_class": "proxy_502",
        }

    projection_entered = threading.Event()
    release_projection = threading.Event()
    original_inc = metric.inc

    def blocking_inc() -> None:
        projection_entered.set()
        assert release_projection.wait(timeout=2.0)
        original_inc()

    monkeypatch.setattr(metric, "inc", blocking_inc)
    writer = threading.Thread(target=mutate, daemon=True)
    writer.start()
    assert projection_entered.wait(timeout=1.0)

    snapshot_result: dict[str, object] = {}
    scrape_result: dict[str, bytes] = {}
    snapshot_done = threading.Event()
    scrape_done = threading.Event()

    def read_snapshot() -> None:
        snapshot_result["value"] = telemetry.snapshot()
        snapshot_done.set()

    def scrape() -> None:
        scrape_result["value"] = telemetry.prometheus_text()
        scrape_done.set()

    snapshot_reader = threading.Thread(target=read_snapshot, daemon=True)
    scraper = threading.Thread(target=scrape, daemon=True)
    snapshot_reader.start()
    scraper.start()
    snapshot_was_blocked = not snapshot_done.wait(timeout=0.2)
    scrape_was_blocked = not scrape_done.wait(timeout=0.2)
    release_projection.set()
    writer.join(timeout=2.0)
    snapshot_reader.join(timeout=2.0)
    scraper.join(timeout=2.0)

    assert snapshot_was_blocked is True
    assert scrape_was_blocked is True
    assert snapshot_result["value"]["counters"][counter_key] == 1
    matching = [
        sample.value
        for family in text_string_to_metric_families(
            scrape_result["value"].decode("utf-8")
        )
        for sample in family.samples
        if sample.name == sample_name and sample.labels == sample_labels
    ]
    assert matching == [1.0]


def test_phase_histogram_projection_and_snapshot_share_one_atomic_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)
    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="upload",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    clock.advance(0.125)
    metric = telemetry._phase_duration.labels(phase="upload", outcome="passed")
    projection_entered = threading.Event()
    release_projection = threading.Event()
    original_observe = metric.observe

    def blocking_observe(value: float) -> None:
        projection_entered.set()
        assert release_projection.wait(timeout=2.0)
        original_observe(value)

    monkeypatch.setattr(metric, "observe", blocking_observe)
    writer = threading.Thread(
        target=lambda: telemetry.finish_phase(token, outcome="passed"),
        daemon=True,
    )
    writer.start()
    assert projection_entered.wait(timeout=1.0)

    snapshot_result: dict[str, object] = {}
    scrape_result: dict[str, bytes] = {}
    snapshot_done = threading.Event()
    scrape_done = threading.Event()
    snapshot_reader = threading.Thread(
        target=lambda: (
            snapshot_result.setdefault("value", telemetry.snapshot()),
            snapshot_done.set(),
        ),
        daemon=True,
    )
    scraper = threading.Thread(
        target=lambda: (
            scrape_result.setdefault("value", telemetry.prometheus_text()),
            scrape_done.set(),
        ),
        daemon=True,
    )
    snapshot_reader.start()
    scraper.start()
    snapshot_was_blocked = not snapshot_done.wait(timeout=0.2)
    scrape_was_blocked = not scrape_done.wait(timeout=0.2)
    release_projection.set()
    writer.join(timeout=2.0)
    snapshot_reader.join(timeout=2.0)
    scraper.join(timeout=2.0)

    assert snapshot_was_blocked is True
    assert scrape_was_blocked is True
    assert snapshot_result["value"]["phase_latency_ms"]["upload|passed"] == [125]
    samples = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(
            scrape_result["value"].decode("utf-8")
        )
        for sample in family.samples
    }
    labels = (("outcome", "passed"), ("phase", "upload"))
    assert samples[("medchat_sandbox_phase_duration_seconds_count", labels)] == 1.0
    assert samples[("medchat_sandbox_phase_duration_seconds_sum", labels)] == 0.125


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


def test_logger_handler_can_reenter_snapshot_without_deadlock() -> None:
    logger = logging.getLogger(f"test.telemetry.reentry.{id(object())}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), logger=logger)
    handled = threading.Event()

    class SnapshotHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            del record
            telemetry.snapshot()
            handled.set()

    handler = SnapshotHandler()
    logger.addHandler(handler)
    worker = threading.Thread(
        target=lambda: telemetry.emit(event()),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=1)
    logger.removeHandler(handler)

    assert not worker.is_alive()
    assert handled.is_set()


def test_prometheus_collector_can_reenter_snapshot_without_deadlock() -> None:
    registry = CollectorRegistry()
    telemetry = BrokerTelemetry(registry=registry)

    class SnapshotCollector:
        def collect(self):
            snapshot = telemetry.snapshot()
            yield GaugeMetricFamily(
                "medchat_test_reentrant_snapshot",
                "Reentrant telemetry snapshot schema",
                value=snapshot["schema_version"],
            )

    registry.register(SnapshotCollector())
    result: dict[str, bytes] = {}
    failures: list[BaseException] = []

    def scrape() -> None:
        try:
            result["value"] = telemetry.prometheus_text()
        except BaseException as failure:
            failures.append(failure)

    worker = threading.Thread(target=scrape, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert not failures
    assert b"medchat_test_reentrant_snapshot 1.0" in result["value"]


def test_mutations_keep_in_memory_coherence_when_sinks_fail_after_side_effect() -> None:
    class FailingLogger:
        def __init__(self) -> None:
            self.calls = 0

        def info(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            self.calls += 1
            raise KeyboardInterrupt

    class FailingMetric:
        def __init__(self) -> None:
            self.calls = 0

        def labels(self, **labels: object) -> "FailingMetric":
            del labels
            return self

        def observe(self, value: object) -> None:
            del value
            self.calls += 1
            raise SystemExit

        def inc(self) -> None:
            self.calls += 1
            raise SystemExit

        def set(self, value: object) -> None:
            del value
            self.calls += 1
            raise SystemExit

    logger = FailingLogger()
    telemetry = BrokerTelemetry(
        registry=CollectorRegistry(),
        monotonic=iter((10.0, 10.25)).__next__,
        logger=logger,  # type: ignore[arg-type]
    )
    phase_metric = FailingMetric()
    control_metric = FailingMetric()
    retry_metric = FailingMetric()
    terminal_metric = FailingMetric()
    gauges = [FailingMetric() for _ in range(5)]
    telemetry._phase_duration = phase_metric  # type: ignore[assignment]
    telemetry._control_failures = control_metric  # type: ignore[assignment]
    telemetry._retries = retry_metric  # type: ignore[assignment]
    telemetry._jobs = terminal_metric  # type: ignore[assignment]
    (
        telemetry._queue_depth,
        telemetry._active_jobs,
        telemetry._cleanup_tasks,
        telemetry._isolated_tasks,
        telemetry._breaker_state,
    ) = gauges  # type: ignore[assignment]

    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="cleanup",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    completed = telemetry.finish_phase(
        token,
        outcome="passed",
        cleanup_status="succeeded",
    )
    telemetry.record_control_plane_failure(
        operation="destroy",
        failure_class=FailureClass.DESTROY_FAILED,
    )
    telemetry.record_retry("destroy", "attempted")
    telemetry.record_terminal("succeeded")
    telemetry.update_runtime_gauges(
        queue_depth=1,
        active_job_count=1,
        cleanup_task_count=2,
        isolated_task_count=3,
        breaker_state="half_open",
    )

    snapshot = telemetry.snapshot()
    assert [item["phase"] for item in snapshot["recent_events"]] == [
        "cleanup_started",
        "cleanup_completed",
    ]
    assert completed.outcome == "passed"
    assert snapshot["phase_latency_ms"]["cleanup|passed"] == [250]
    assert snapshot["counters"] == {
        "control_failure|destroy|destroy_failed": 1,
        "retry|destroy|attempted": 1,
        "terminal|succeeded|none": 1,
    }
    assert snapshot["runtime"] == {
        "queue_depth": 1,
        "active_job_count": 1,
        "cleanup_task_count": 2,
        "isolated_task_count": 3,
        "breaker_state": "half_open",
    }
    assert logger.calls == 2
    assert phase_metric.calls == 1
    assert control_metric.calls == retry_metric.calls == terminal_metric.calls == 1
    assert [gauge.calls for gauge in gauges] == [1, 1, 1, 1, 1]
    with pytest.raises(RuntimeError, match="already finished"):
        telemetry.finish_phase(
            token,
            outcome="passed",
            cleanup_status="succeeded",
        )


def test_pre_side_effect_sink_failures_converge_to_canonical_scrape() -> None:
    class FailBeforeMutation:
        def labels(self, **labels: object) -> "FailBeforeMutation":
            del labels
            return self

        def observe(self, value: object) -> None:
            del value
            raise KeyboardInterrupt

        def inc(self, amount: object = 1) -> None:
            del amount
            raise KeyboardInterrupt

        def set(self, value: object) -> None:
            del value
            raise KeyboardInterrupt

    registry = CollectorRegistry()
    clock = Clock()
    telemetry = BrokerTelemetry(registry=registry, monotonic=clock)
    failing = FailBeforeMutation()
    telemetry._phase_duration = failing  # type: ignore[assignment]
    telemetry._control_failures = failing  # type: ignore[assignment]
    telemetry._retries = failing  # type: ignore[assignment]
    telemetry._jobs = failing  # type: ignore[assignment]
    telemetry._queue_depth = failing  # type: ignore[assignment]
    telemetry._active_jobs = failing  # type: ignore[assignment]
    telemetry._cleanup_tasks = failing  # type: ignore[assignment]
    telemetry._isolated_tasks = failing  # type: ignore[assignment]
    telemetry._breaker_state = failing  # type: ignore[assignment]

    token = telemetry.start_phase(
        trace_id="trace-1",
        job_id="a" * 32,
        phase="upload",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="b" * 64,
    )
    clock.advance(0.125)
    telemetry.finish_phase(token, outcome="passed")
    telemetry.record_control_plane_failure(
        operation="create",
        failure_class=FailureClass.PROXY_502,
    )
    telemetry.record_retry("create", "attempted")
    telemetry.record_terminal("failed", FailureClass.PROXY_502)
    telemetry.update_runtime_gauges(
        queue_depth=2,
        active_job_count=1,
        cleanup_task_count=3,
        isolated_task_count=4,
        breaker_state="half_open",
    )

    snapshot = telemetry.snapshot()
    encoded = telemetry.prometheus_text()
    samples = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(encoded.decode("utf-8"))
        for sample in family.samples
    }

    assert snapshot["counters"] == {
        "control_failure|create|proxy_502": 1,
        "retry|create|attempted": 1,
        "terminal|failed|proxy_502": 1,
    }
    assert snapshot["phase_latency_ms"] == {"upload|passed": [125]}
    assert samples[
        (
            "medchat_sandbox_control_plane_failures_total",
            (("failure_class", "proxy_502"), ("operation", "create")),
        )
    ] == 1.0
    assert samples[
        (
            "medchat_sandbox_retry_total",
            (("operation", "create"), ("outcome", "attempted")),
        )
    ] == 1.0
    assert samples[
        (
            "medchat_sandbox_jobs_total",
            (("failure_class", "proxy_502"), ("terminal_status", "failed")),
        )
    ] == 1.0
    phase_labels = (("outcome", "passed"), ("phase", "upload"))
    assert samples[
        ("medchat_sandbox_phase_duration_seconds_count", phase_labels)
    ] == 1.0
    assert samples[
        ("medchat_sandbox_phase_duration_seconds_sum", phase_labels)
    ] == 0.125
    assert samples[("medchat_sandbox_queue_depth", ())] == 2.0
    assert samples[("medchat_sandbox_active_jobs", ())] == 1.0
    assert samples[("medchat_sandbox_cleanup_tasks", ())] == 3.0
    assert samples[("medchat_sandbox_isolated_tasks", ())] == 4.0
    assert samples[("medchat_sandbox_circuit_breaker_state", ())] == 2.0


def test_dirty_projection_rebuild_is_atomic_with_snapshot_and_scrape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailBeforeMutation:
        def labels(self, **labels: object) -> "FailBeforeMutation":
            del labels
            return self

        def inc(self, amount: object = 1) -> None:
            del amount
            raise SystemExit

    registry = CollectorRegistry()
    telemetry = BrokerTelemetry(registry=registry)
    original_jobs = telemetry._jobs
    telemetry._jobs = FailBeforeMutation()  # type: ignore[assignment]
    telemetry.record_terminal("failed", FailureClass.PROXY_502)
    telemetry._jobs = original_jobs

    rebuild_entered = threading.Event()
    release_rebuild = threading.Event()
    original_register = CollectorRegistry.register

    def blocking_candidate_register(
        candidate_registry: CollectorRegistry,
        collector: object,
    ) -> None:
        if candidate_registry is not registry and not rebuild_entered.is_set():
            rebuild_entered.set()
            assert release_rebuild.wait(timeout=2.0)
        original_register(candidate_registry, collector)  # type: ignore[arg-type]

    monkeypatch.setattr(
        CollectorRegistry,
        "register",
        blocking_candidate_register,
    )
    scrape_result: dict[str, bytes] = {}
    snapshot_done = threading.Event()

    scraper = threading.Thread(
        target=lambda: scrape_result.setdefault(
            "value",
            telemetry.prometheus_text(),
        ),
        daemon=True,
    )
    snapshot_reader = threading.Thread(
        target=lambda: (telemetry.snapshot(), snapshot_done.set()),
        daemon=True,
    )
    scraper.start()
    assert rebuild_entered.wait(timeout=1.0)
    snapshot_reader.start()
    snapshot_was_blocked = not snapshot_done.wait(timeout=0.2)
    release_rebuild.set()
    scraper.join(timeout=2.0)
    snapshot_reader.join(timeout=2.0)

    assert snapshot_was_blocked is True
    samples = [
        sample.value
        for family in text_string_to_metric_families(
            scrape_result["value"].decode("utf-8")
        )
        for sample in family.samples
        if sample.name == "medchat_sandbox_jobs_total"
        and sample.labels
        == {"terminal_status": "failed", "failure_class": "proxy_502"}
    ]
    assert samples == [1.0]


def test_dirty_histogram_rebuild_preserves_all_cumulative_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailBeforeMutation:
        def labels(self, **labels: object) -> "FailBeforeMutation":
            del labels
            return self

        def observe(self, value: object) -> None:
            del value
            raise KeyboardInterrupt

    clock = Clock()
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)
    original_histogram = telemetry._phase_duration
    telemetry._phase_duration = FailBeforeMutation()  # type: ignore[assignment]
    for index in range(320):
        token = telemetry.start_phase(
            trace_id=f"trace-cumulative-{index}",
            job_id=f"job-cumulative-{index}",
            phase="command",
            attempt=1,
            queue_depth=0,
            active_job_count=1,
            image_digest="c" * 64,
        )
        clock.advance(1.0)
        telemetry.finish_phase(token, outcome="passed")
    telemetry._phase_duration = original_histogram

    replay_calls = 0
    original_observe = telemetry_module.Histogram.observe

    def counted_observe(metric: object, value: float) -> None:
        nonlocal replay_calls
        replay_calls += 1
        original_observe(metric, value)  # type: ignore[arg-type]

    monkeypatch.setattr(telemetry_module.Histogram, "observe", counted_observe)
    encoded = telemetry.prometheus_text()
    samples = [
        sample
        for family in text_string_to_metric_families(encoded.decode("utf-8"))
        for sample in family.samples
        if sample.labels.get("phase") == "command"
        and sample.labels.get("outcome") == "passed"
    ]
    by_name = {sample.name: sample.value for sample in samples}
    buckets = {
        float(sample.labels["le"]): sample.value
        for sample in samples
        if sample.name == "medchat_sandbox_phase_duration_seconds_bucket"
    }

    assert telemetry.snapshot()["phase_latency_ms"]["command|passed"] == [
        1_000
    ] * 256
    assert by_name["medchat_sandbox_phase_duration_seconds_count"] == 320.0
    assert by_name["medchat_sandbox_phase_duration_seconds_sum"] == 320.0
    assert set(buckets) == {*telemetry_module._DURATION_BUCKETS, math.inf}
    assert all(
        count == (0.0 if upper_bound < 1.0 else 320.0)
        for upper_bound, count in buckets.items()
    )
    assert replay_calls == 0
    assert len(telemetry._histogram_aggregates) == 1
    aggregate = telemetry._histogram_aggregates[("command", "passed")]
    assert aggregate.count == 320
    assert aggregate.sum_seconds == 320.0
    assert len(aggregate.bucket_counts) == len(telemetry_module._DURATION_BUCKETS)


@pytest.mark.parametrize(
    ("failure_point", "ordinal"),
    [
        ("registration", 1),
        ("registration", 5),
        ("registration", 9),
        ("counter_population", 1),
        ("histogram_population", 1),
    ],
)
def test_failed_projection_replacement_retains_complete_live_generation(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
    ordinal: int,
) -> None:
    clock = Clock()
    telemetry = BrokerTelemetry(registry=CollectorRegistry(), monotonic=clock)
    token = telemetry.start_phase(
        trace_id="trace-atomic",
        job_id="job-atomic",
        phase="upload",
        attempt=1,
        queue_depth=0,
        active_job_count=1,
        image_digest="d" * 64,
    )
    clock.advance(0.5)
    telemetry.finish_phase(token, outcome="passed")
    telemetry.record_control_plane_failure(
        operation="create",
        failure_class=FailureClass.PROXY_502,
    )
    telemetry.record_retry("create", "attempted")
    telemetry.record_terminal("failed", FailureClass.PROXY_502)
    telemetry.update_runtime_gauges(
        queue_depth=2,
        active_job_count=1,
        cleanup_task_count=3,
        isolated_task_count=4,
        breaker_state="open",
    )

    class FailBeforeMutation:
        def labels(self, **labels: object) -> "FailBeforeMutation":
            del labels
            return self

        def inc(self, amount: object = 1) -> None:
            del amount
            raise KeyboardInterrupt

    original_jobs = telemetry._jobs
    telemetry._jobs = FailBeforeMutation()  # type: ignore[assignment]
    telemetry.record_terminal("failed", FailureClass.PROXY_502)
    telemetry._jobs = original_jobs
    live_registry = telemetry._registry
    old_projection = telemetry._registered_collectors
    old_collectors = set(old_projection)
    unregister_calls = 0
    original_unregister = live_registry.unregister

    def counted_unregister(collector: object) -> object:
        nonlocal unregister_calls
        unregister_calls += 1
        return original_unregister(collector)  # type: ignore[arg-type]

    monkeypatch.setattr(live_registry, "unregister", counted_unregister)
    failed = False
    calls = 0
    if failure_point == "registration":
        original_register = CollectorRegistry.register

        def failing_register(registry: CollectorRegistry, collector: object) -> None:
            nonlocal failed, calls
            if registry is not live_registry and not failed:
                calls += 1
                if calls == ordinal:
                    failed = True
                    raise KeyboardInterrupt
            original_register(registry, collector)  # type: ignore[arg-type]

        monkeypatch.setattr(CollectorRegistry, "register", failing_register)
    elif failure_point == "counter_population":
        original_labels = telemetry_module.PromCounter.labels

        def failing_counter_labels(metric: object, *args: object, **kwargs: object):
            nonlocal failed
            if metric not in old_collectors and not failed:
                failed = True
                raise KeyboardInterrupt
            return original_labels(metric, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            telemetry_module.PromCounter,
            "labels",
            failing_counter_labels,
        )
    else:
        original_labels = telemetry_module.Histogram.labels

        def failing_histogram_labels(
            metric: object,
            *args: object,
            **kwargs: object,
        ):
            nonlocal failed
            if metric not in old_collectors and not failed:
                failed = True
                raise KeyboardInterrupt
            return original_labels(metric, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            telemetry_module.Histogram,
            "labels",
            failing_histogram_labels,
        )

    first = telemetry.prometheus_text()
    assert telemetry._registry is live_registry
    assert telemetry._registered_collectors is old_projection
    second = telemetry.prometheus_text()
    expected_help = {
        b"medchat_sandbox_jobs_total",
        b"medchat_sandbox_phase_duration_seconds",
        b"medchat_sandbox_control_plane_failures_total",
        b"medchat_sandbox_retry_total",
        b"medchat_sandbox_queue_depth",
        b"medchat_sandbox_active_jobs",
        b"medchat_sandbox_cleanup_tasks",
        b"medchat_sandbox_isolated_tasks",
        b"medchat_sandbox_circuit_breaker_state",
    }
    for encoded in (first, second):
        for family in expected_help:
            assert encoded.count(b"# HELP " + family + b" ") == 1

    def terminal_value(encoded: bytes) -> float:
        return next(
            sample.value
            for family in text_string_to_metric_families(encoded.decode("utf-8"))
            for sample in family.samples
            if sample.name == "medchat_sandbox_jobs_total"
            and sample.labels
            == {"terminal_status": "failed", "failure_class": "proxy_502"}
        )

    assert failed is True
    assert unregister_calls == 0
    assert terminal_value(first) == 1.0
    assert terminal_value(second) == 2.0


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

    clock.advance(-1.0)
    with pytest.raises(ValueError, match="duration"):
        telemetry.finish_phase(
            token,
            outcome="failed",
            failure_class=FailureClass.DESTROY_FAILED,
            cleanup_status="failed",
        )
    clock.value = 10.125
    completed = telemetry.finish_phase(
        token,
        outcome="failed",
        failure_class=FailureClass.DESTROY_FAILED,
        cleanup_status="failed",
    )
    assert completed.duration_ms == 125


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
