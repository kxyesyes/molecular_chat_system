from __future__ import annotations

import json
import math
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_opensandbox_stability_soak as soak
from src.sandbox_broker.telemetry import BrokerTelemetryEvent, FailureClass, Phase


HEX = "a" * 64
PHASES = (
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


def valid_run(index: int, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "run_index": index,
        "trace_id": f"soak-safe-{index:02d}",
        "status": "passed",
        "latency_ms": 1000 + index,
        "pose_count": 9,
        "best_energy": -7.25,
        "artifact_present": True,
        "artifact_path": f"outputs/opensandbox_stability/run-{index:02d}/poses.pdbqt",
        "artifact_sha256": HEX,
        "artifact_sha256_valid": True,
        "cleanup_status": "succeeded",
        "image_digest": HEX,
        "image_digest_valid": True,
        "secure_runtime": "gvisor",
        "vina_version": "1.2.5",
        "meeko_version": "0.6.1",
        "warning_codes": [],
        "failure_codes": [],
        "failure_class": "none",
        "events_complete": True,
    }
    value.update(overrides)
    return value


def diagnostics(event_count: int = 0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "telemetry": {
            "schema_version": 1,
            "counters": {"terminal|succeeded|none": event_count},
            "phase_latency_ms": {
                "provisioning|passed": [100],
                "upload|passed": [50],
                "command|passed": [800],
                "validation|passed": [25],
                "cleanup|passed": [25],
            },
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
            "failure_threshold": 5,
            "window_seconds": 60,
            "open_seconds": 30,
        },
    }


def lifecycle_event(
    trace_id: str,
    job_id: str,
    phase: str,
    *,
    outcome: str | None = None,
    failure_class: str | None = None,
    cleanup_status: str | None = None,
    vina_version: str | None = None,
    meeko_version: str | None = None,
) -> dict[str, object]:
    event = soak.synthetic_event(job_id, phase)
    event["trace_id"] = trace_id
    event["job_id"] = job_id
    if outcome is not None:
        event["outcome"] = outcome
    if failure_class is not None:
        event["failure_class"] = failure_class
    if cleanup_status is not None:
        event["cleanup_status"] = cleanup_status
    if phase == "validation_completed":
        event["vina_version"] = vina_version or "1.2.5"
        event["meeko_version"] = meeko_version or "0.7.1"
    return event


def successful_lifecycle(
    trace_id: str = "broker-trace-1",
    job_id: str = "a" * 32,
) -> list[dict[str, object]]:
    events = [lifecycle_event(trace_id, job_id, phase) for phase in PHASES]
    events[-3]["cleanup_status"] = "in_progress"
    events[-2]["outcome"] = "passed"
    events[-2]["cleanup_status"] = "succeeded"
    events[-1]["outcome"] = "passed"
    events[-1]["cleanup_status"] = "succeeded"
    return events


def successful_lifecycle_with_recovered_retry(
    operation: str,
    *,
    trace_id: str = "broker-recovered-retry",
    job_id: str = "b" * 32,
) -> list[dict[str, object]]:
    events = successful_lifecycle(trace_id, job_id)
    started_phase = f"{operation}_started"
    completed_phase = f"{operation}_completed"
    started_index = next(
        index for index, event in enumerate(events) if event["phase"] == started_phase
    )
    started = events[started_index]
    completed = events[started_index + 1]
    passed_completed = dict(completed)
    started["attempt"] = 1
    completed["attempt"] = 1
    completed["outcome"] = "failed"
    completed["failure_class"] = {
        "provisioning": "connection_failed",
        "upload": "command_transport_failed",
        "command": "command_timeout",
        "validation": None,
        "cleanup": "destroy_failed",
    }[operation]
    if operation == "validation":
        completed["vina_version"] = None
        completed["meeko_version"] = None
    if operation == "cleanup":
        completed["cleanup_status"] = "failed"
    retry_started = dict(started, attempt=2)
    retry_completed = dict(
        passed_completed,
        attempt=2,
        outcome="passed",
        failure_class=None,
    )
    if operation == "cleanup":
        retry_completed["cleanup_status"] = "succeeded"
    events[started_index + 2 : started_index + 2] = [
        retry_started,
        retry_completed,
    ]
    return events


def failed_science_lifecycle_with_recovered_cleanup_retry(
    *,
    trace_id: str = "broker-failed-recovered-cleanup",
    job_id: str = "9" * 32,
) -> list[dict[str, object]]:
    events = failed_lifecycle(
        failed_phase="command_completed",
        failure_class="command_timeout",
        cleanup_status="succeeded",
        trace_id=trace_id,
        job_id=job_id,
    )
    cleanup_index = next(
        index
        for index, event in enumerate(events)
        if event["phase"] == "cleanup_started"
    )
    cleanup_started = events[cleanup_index]
    cleanup_completed = events[cleanup_index + 1]
    recovered_cleanup = dict(cleanup_completed)
    cleanup_started["attempt"] = 1
    cleanup_completed["attempt"] = 1
    cleanup_completed["outcome"] = "failed"
    cleanup_completed["failure_class"] = "destroy_failed"
    cleanup_completed["cleanup_status"] = "failed"
    events[cleanup_index + 2 : cleanup_index + 2] = [
        dict(cleanup_started, attempt=2),
        dict(
            recovered_cleanup,
            attempt=2,
            outcome="passed",
            failure_class=None,
            cleanup_status="succeeded",
        ),
    ]
    return events


def validate_broker_events(events: list[dict[str, object]]) -> None:
    for event in events:
        normalized = dict(event)
        normalized["phase"] = Phase(event["phase"])
        failure_class = event["failure_class"]
        normalized["failure_class"] = (
            FailureClass(failure_class) if failure_class is not None else None
        )
        BrokerTelemetryEvent.model_validate(normalized)


def failed_lifecycle(
    *,
    failed_phase: str = "command_completed",
    failure_class: str | None = None,
    terminal_outcome: str = "failed",
    cleanup_status: str = "succeeded",
    trace_id: str = "broker-failed",
    job_id: str = "d" * 32,
) -> list[dict[str, object]]:
    if failed_phase not in {
        "provisioning_completed",
        "upload_completed",
        "command_completed",
        "validation_completed",
    }:
        raise ValueError("unsupported failed phase")
    failed_index = PHASES.index(failed_phase)
    events = [
        lifecycle_event(trace_id, job_id, phase)
        for phase in PHASES[: failed_index + 1]
    ]
    events[-1]["outcome"] = terminal_outcome
    events[-1]["failure_class"] = failure_class
    if failed_phase == "validation_completed":
        events[-1]["vina_version"] = None
        events[-1]["meeko_version"] = None
    events.extend(
        lifecycle_event(trace_id, job_id, phase) for phase in PHASES[-3:]
    )
    events[-3]["cleanup_status"] = "in_progress"
    events[-2]["outcome"] = "passed" if cleanup_status == "succeeded" else "failed"
    events[-2]["cleanup_status"] = cleanup_status
    events[-1]["outcome"] = terminal_outcome
    events[-1]["cleanup_status"] = cleanup_status
    events[-1]["failure_class"] = (
        (failure_class if failure_class is not None else "none")
        if terminal_outcome == "failed"
        else None
    )
    return events


def retrying_provisioning_failure_lifecycle() -> list[dict[str, object]]:
    events = failed_lifecycle(
        failed_phase="provisioning_completed",
        failure_class="proxy_502",
    )
    events[2]["attempt"] = 1
    events[3]["attempt"] = 1
    retry_started = dict(events[2], attempt=2)
    retry_completed = dict(events[3], attempt=2)
    events[4:4] = [retry_started, retry_completed]
    return events


def retrying_failed_operation_lifecycle(
    operation: str,
) -> list[dict[str, object]]:
    completed_phase = f"{operation}_completed"
    events = failed_lifecycle(
        failed_phase=completed_phase,
        failure_class="command_timeout",
    )
    started_index = next(
        index
        for index, event in enumerate(events)
        if event["phase"] == f"{operation}_started"
    )
    events[started_index]["attempt"] = 1
    events[started_index + 1]["attempt"] = 1
    events[started_index + 2 : started_index + 2] = [
        dict(events[started_index], attempt=2),
        dict(events[started_index + 1], attempt=2),
    ]
    return events


class ExplodingComparison:
    def __eq__(self, other: object) -> bool:
        del other
        raise RuntimeError("comparison must not run")

    def __ne__(self, other: object) -> bool:
        del other
        raise RuntimeError("comparison must not run")


class FakeDiagnostics:
    def __init__(self) -> None:
        self.calls = 0

    def snapshot(self) -> dict[str, object]:
        self.calls += 1
        value = diagnostics(self.calls - 1)
        if self.calls == 1:
            value["telemetry"]["phase_latency_ms"] = {}
        return value

    def delta(self, before: object, after: object) -> dict[str, object]:
        return soak.diagnostics_delta(before, after)


class FakeExecutor:
    def __init__(self, failed_index: int | None = None) -> None:
        self.failed_index = failed_index
        self.calls: list[object] = []
        self.suite_calls = 1

    def measured_run(self, index: int) -> dict[str, object]:
        self.calls.append(("measured", index))
        if index == self.failed_index:
            return valid_run(
                index,
                status="failed",
                failure_codes=["proxy_failure"],
                failure_class="proxy_502",
                events_complete=False,
            )
        return valid_run(index)

    def idempotency_probe(self) -> dict[str, object]:
        self.calls.append("idempotency")
        return {"passed": True, "sandbox_count": 1}

    def queue_probe(self) -> dict[str, object]:
        self.calls.append("queue")
        return {
            "passed": True,
            "accepted": 9,
            "saturated": 1,
        }

    def cancellation_probe(self) -> bool:
        self.calls.append("cancellation")
        return True

    def timeout_probe(self) -> bool:
        self.calls.append("timeout")
        return True

    def security_probe(self) -> bool:
        self.calls.append("security")
        return True


def test_all_thirty_pass_with_exact_order_and_schema() -> None:
    executor = FakeExecutor()
    client = FakeDiagnostics()
    orphan_calls: list[str] = []

    report = soak.run_stability_soak(
        executor=executor,
        repeat=30,
        diagnostics=client,
        orphan_counter=lambda: orphan_calls.append("orphan") or 0,
    )

    assert report["status"] == "passed"
    assert report["repeat"] == 30
    assert report["pass_rate"] == report["cleanup_rate"] == 1.0
    assert report["event_completeness_rate"] == 1.0
    assert len(report["runs"]) == 30
    assert set(report) == soak.REPORT_FIELDS
    assert all(set(run) == soak.RUN_FIELDS for run in report["runs"])
    assert executor.calls == [
        *(("measured", index) for index in range(1, 31)),
        "idempotency",
        "queue",
        "cancellation",
        "timeout",
        "security",
    ]
    assert executor.suite_calls == 1
    assert client.calls == 2
    assert orphan_calls == ["orphan"]


def test_one_proxy_502_is_preserved_and_fails_without_short_circuit() -> None:
    executor = FakeExecutor(failed_index=7)
    report = soak.run_stability_soak(
        executor=executor,
        repeat=30,
        diagnostics=FakeDiagnostics(),
        orphan_counter=lambda: 0,
    )

    assert report["status"] == "failed"
    assert [run["run_index"] for run in report["runs"]] == list(range(1, 31))
    assert report["runs"][6]["failure_class"] == "proxy_502"
    assert report["failure_type_distribution"] == {"proxy_502": 1}
    assert report["pass_rate"] == pytest.approx(29 / 30)
    assert executor.calls.count("idempotency") == 1
    assert executor.suite_calls == 1


def test_failed_run_never_reruns_full_suite() -> None:
    class RaisingExecutor(FakeExecutor):
        def measured_run(self, index: int) -> dict[str, object]:
            self.calls.append(("measured", index))
            if index == 2:
                raise RuntimeError("Authorization: must never escape")
            return valid_run(index)

    executor = RaisingExecutor()
    report = soak.run_stability_soak(
        executor=executor,
        repeat=30,
        diagnostics=FakeDiagnostics(),
        orphan_counter=lambda: 0,
    )

    assert executor.suite_calls == 1
    assert [call for call in executor.calls if isinstance(call, tuple)] == [
        ("measured", index) for index in range(1, 31)
    ]
    assert report["runs"][1]["status"] == "failed"
    assert "Authorization" not in json.dumps(report)


def test_executor_advances_after_one_measured_execution_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DiagnosticsClient:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        def snapshot(self) -> dict[str, object]:
            value = diagnostics(len(self.events) // len(PHASES))
            value["telemetry"]["recent_events"] = list(self.events)
            return value

    diagnostics_client = DiagnosticsClient()

    class Runner:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, payload: object, *, job_id: str) -> object:
            del payload
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("one measured execution failed")
            diagnostics_client.events.extend(successful_lifecycle(job_id, job_id))
            return SimpleNamespace(success=True)

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    monkeypatch.setattr(
        soak,
        "_project_result",
        lambda *args, **kwargs: {
            "status": "passed",
            "latency_ms": 100,
            "pose_count": 1,
            "best_energy": -7.0,
            "artifact_path": "outputs/opensandbox_stability/pose.pdbqt",
            "artifact_sha256": HEX,
            "cleanup_status": "succeeded",
            "image_digest": HEX,
            "secure_runtime": "gvisor",
            "tool_versions": {"vina": "1.2.5", "meeko": "0.6.1"},
            "warning_codes": [],
            "failure_codes": [],
        },
    )
    runner = Runner()
    executor = soak.StabilityExecutor(
        runner=runner,
        payload={},
        observer_factory=Observer,
        diagnostics_client=diagnostics_client,
        project_root=Path.cwd(),
    )

    failures = 0
    for index in range(1, 31):
        try:
            executor.measured_run(index)
        except RuntimeError:
            failures += 1

    assert failures == 1
    assert runner.calls == 30
    assert executor.measured_calls == 30


def test_measured_run_uses_single_broker_identity_and_validation_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_trace = "broker-trace-real"
    broker_job = "b" * 32
    caller_keys: list[str] = []
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    after = diagnostics(1)
    after["telemetry"]["recent_events"] = successful_lifecycle(
        broker_trace,
        broker_job,
    )

    class DiagnosticsClient:
        def __init__(self) -> None:
            self.values = iter([before, after])

        def snapshot(self) -> dict[str, object]:
            return next(self.values)

    class Runner:
        def execute(self, payload: object, *, job_id: str) -> object:
            del payload
            caller_keys.append(job_id)
            return SimpleNamespace(success=True)

    class Observer:
        failure_codes: list[str] = []
        tool_versions: dict[str, str] = {}

        def disable_intrusive_probes(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    captured: dict[str, object] = {}

    def project(*args: object, **kwargs: object) -> dict[str, object]:
        del args
        captured.update(kwargs)
        return {
            "status": "passed",
            "latency_ms": 100,
            "pose_count": 9,
            "best_energy": -7.0,
            "artifact_path": "outputs/opensandbox_stability/run/result.pdbqt",
            "artifact_sha256": HEX,
            "cleanup_status": "succeeded",
            "image_digest": HEX,
            "secure_runtime": "gvisor",
            "tool_versions": kwargs.get("validated_tool_versions", {}),
            "warning_codes": [],
            "failure_codes": [],
        }

    monkeypatch.setattr(soak, "_project_result", project)
    executor = soak.StabilityExecutor(
        runner=Runner(),
        payload={},
        observer_factory=Observer,
        diagnostics_client=DiagnosticsClient(),
        project_root=tmp_path,
    )

    run = executor.measured_run(1)

    assert len(caller_keys) == 1
    assert caller_keys[0] not in {broker_trace, broker_job}
    assert run["trace_id"] == broker_trace
    assert run["status"] == "passed"
    assert run["events_complete"] is True
    assert run["cleanup_status"] == "succeeded"
    assert run["failure_class"] == "none"
    assert run["vina_version"] == "1.2.5"
    assert run["meeko_version"] == "0.7.1"
    assert captured["trace_id"] == broker_trace
    assert captured["validated_tool_versions"] == {
        "vina": "1.2.5",
        "meeko": "0.7.1",
    }


@pytest.mark.parametrize("operation", ["provisioning", "cleanup"])
def test_measured_run_fails_on_recovered_broker_retry_without_losing_completeness(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    after = diagnostics(1)
    after["telemetry"]["recent_events"] = successful_lifecycle_with_recovered_retry(
        operation
    )

    class DiagnosticsClient:
        def __init__(self) -> None:
            self.values = iter([before, after])

        def snapshot(self) -> dict[str, object]:
            return next(self.values)

    class Runner:
        def __init__(self) -> None:
            self.run_count = 0

        def execute(self, payload: object, *, job_id: str) -> object:
            del payload, job_id
            self.run_count += 1
            return SimpleNamespace(success=True)

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    monkeypatch.setattr(
        soak,
        "_project_result",
        lambda *args, **kwargs: {
            "status": "passed",
            "latency_ms": 100,
            "pose_count": 1,
            "best_energy": -7.0,
            "artifact_path": "outputs/opensandbox_stability/retry/result.pdbqt",
            "artifact_sha256": HEX,
            "cleanup_status": "succeeded",
            "image_digest": HEX,
            "secure_runtime": "gvisor",
            "tool_versions": kwargs.get("validated_tool_versions", {}),
            "warning_codes": [],
            "failure_codes": [],
        },
    )
    runner = Runner()
    executor = soak.StabilityExecutor(
        runner=runner,
        payload={},
        observer_factory=Observer,
        diagnostics_client=DiagnosticsClient(),
        project_root=tmp_path,
    )

    run = executor.measured_run(1)

    assert runner.run_count == 1
    assert run["status"] == "failed"
    assert run["events_complete"] is True
    assert run["failure_codes"] == ["broker_retry_observed"]
    assert run["failure_class"] == "none"


def test_measured_run_preserves_scientific_failure_and_recovered_cleanup_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    after = diagnostics(1)
    after["telemetry"]["recent_events"] = (
        failed_science_lifecycle_with_recovered_cleanup_retry()
    )

    class DiagnosticsClient:
        def __init__(self) -> None:
            self.values = iter([before, after])

        def snapshot(self) -> dict[str, object]:
            return next(self.values)

    class Runner:
        def __init__(self) -> None:
            self.run_count = 0

        def execute(self, payload: object, *, job_id: str) -> object:
            del payload, job_id
            self.run_count += 1
            return SimpleNamespace(success=False)

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    monkeypatch.setattr(
        soak,
        "_project_result",
        lambda *args, **kwargs: {
            "status": "failed",
            "latency_ms": 271000,
            "pose_count": 0,
            "best_energy": None,
            "artifact_path": None,
            "artifact_sha256": None,
            "cleanup_status": None,
            "image_digest": None,
            "secure_runtime": None,
            "tool_versions": kwargs.get("validated_tool_versions", {}),
            "warning_codes": [],
            "failure_codes": ["docking_failed"],
        },
    )
    runner = Runner()
    executor = soak.StabilityExecutor(
        runner=runner,
        payload={},
        observer_factory=Observer,
        diagnostics_client=DiagnosticsClient(),
        project_root=tmp_path,
    )

    run = executor.measured_run(1)

    assert runner.run_count == 1
    assert run["status"] == "failed"
    assert run["events_complete"] is True
    assert run["failure_class"] == "command_timeout"
    assert run["failure_codes"] == ["broker_retry_observed", "docking_failed"]


def test_measured_run_merges_scientific_and_lifecycle_failures_without_false_cleanup_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_trace = "broker-trace-failed-result"
    broker_job = "c" * 32
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    after = diagnostics(1)
    after["telemetry"]["recent_events"] = successful_lifecycle(
        broker_trace,
        broker_job,
    )

    class DiagnosticsClient:
        def __init__(self) -> None:
            self.values = iter([before, after])

        def snapshot(self) -> dict[str, object]:
            return next(self.values)

    class Runner:
        def execute(self, payload: object, *, job_id: str) -> object:
            del payload, job_id
            return SimpleNamespace(success=False)

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    monkeypatch.setattr(
        soak,
        "_project_result",
        lambda *args, **kwargs: {
            "status": "failed",
            "latency_ms": 271000,
            "pose_count": 0,
            "best_energy": None,
            "artifact_path": None,
            "artifact_sha256": None,
            "cleanup_status": None,
            "image_digest": None,
            "secure_runtime": None,
            "tool_versions": {},
            "warning_codes": [],
            "failure_codes": ["docking_failed"],
        },
    )
    executor = soak.StabilityExecutor(
        runner=Runner(),
        payload={},
        observer_factory=Observer,
        diagnostics_client=DiagnosticsClient(),
        project_root=tmp_path,
    )

    run = executor.measured_run(1)

    assert run["trace_id"] == broker_trace
    assert run["status"] == "failed"
    assert run["latency_ms"] == 271000
    assert run["cleanup_status"] == "succeeded"
    assert run["failure_class"] == "none"
    assert run["events_complete"] is False
    assert run["failure_codes"] == ["docking_failed", "events_incomplete"]
    assert "cleanup_status_mismatch" not in run["failure_codes"]


def test_measured_run_preserves_bounded_long_failed_latency_with_complete_lifecycle(
    tmp_path: Path,
) -> None:
    broker_trace = "broker-trace-long-timeout"
    broker_job = "f" * 32
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    after = diagnostics(1)
    after["telemetry"]["recent_events"] = failed_lifecycle(
        failure_class="command_timeout",
        trace_id=broker_trace,
        job_id=broker_job,
    )

    class DiagnosticsClient:
        def __init__(self) -> None:
            self.values = iter([before, after])

        def snapshot(self) -> dict[str, object]:
            return next(self.values)

    class Runner:
        def execute(self, payload: object, *, job_id: str) -> object:
            del payload, job_id
            return SimpleNamespace(
                success=False,
                elapsed_ms=420000,
                error=SimpleNamespace(
                    code=SimpleNamespace(value="tool_timeout"),
                ),
                warnings=[],
            )

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    executor = soak.StabilityExecutor(
        runner=Runner(),
        payload={},
        observer_factory=Observer,
        diagnostics_client=DiagnosticsClient(),
        project_root=tmp_path,
    )

    run = executor.measured_run(1)

    assert run["trace_id"] == broker_trace
    assert run["status"] == "failed"
    assert run["latency_ms"] == 420000
    assert run["failure_codes"] == ["tool_timeout"]
    assert run["failure_class"] == "command_timeout"
    assert run["events_complete"] is True
    assert run["cleanup_status"] == "succeeded"


def test_measured_run_rejects_success_cleanup_evidence_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_trace = "broker-trace-cleanup-mismatch"
    broker_job = "e" * 32
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    after = diagnostics(1)
    after["telemetry"]["recent_events"] = successful_lifecycle(
        broker_trace,
        broker_job,
    )

    class DiagnosticsClient:
        def __init__(self) -> None:
            self.values = iter([before, after])

        def snapshot(self) -> dict[str, object]:
            return next(self.values)

    class Runner:
        def execute(self, payload: object, *, job_id: str) -> object:
            del payload, job_id
            return SimpleNamespace(success=True)

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    monkeypatch.setattr(
        soak,
        "_project_result",
        lambda *args, **kwargs: {
            "status": "passed",
            "latency_ms": 100,
            "pose_count": 9,
            "best_energy": -7.0,
            "artifact_path": None,
            "artifact_sha256": HEX,
            "cleanup_status": "failed",
            "image_digest": HEX,
            "secure_runtime": "gvisor",
            "tool_versions": kwargs.get("validated_tool_versions", {}),
            "warning_codes": [],
            "failure_codes": [],
        },
    )
    executor = soak.StabilityExecutor(
        runner=Runner(),
        payload={},
        observer_factory=Observer,
        diagnostics_client=DiagnosticsClient(),
        project_root=tmp_path,
    )

    run = executor.measured_run(1)

    assert run["trace_id"] == broker_trace
    assert run["status"] == "failed"
    assert run["cleanup_status"] == "succeeded"
    assert run["failure_class"] == "none"
    assert run["events_complete"] is True
    assert run["failure_codes"] == ["cleanup_status_mismatch"]


def test_success_lifecycle_has_exact_order_identity_cleanup_and_versions() -> None:
    events = successful_lifecycle()

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert [event["phase"] for event in events] == list(PHASES)
    assert evidence.trace_id == "broker-trace-1"
    assert evidence.job_id == "a" * 32
    assert evidence.events_complete is True
    assert evidence.terminal_outcome == "passed"
    assert evidence.cleanup_status == "succeeded"
    assert evidence.tool_versions == {"vina": "1.2.5", "meeko": "0.7.1"}
    assert evidence.failure_codes == ()


def test_success_lifecycle_accepts_production_attempt_one_pairs() -> None:
    events = successful_lifecycle()
    for event in events:
        if event["phase"].endswith(("_started", "_completed")):
            event["attempt"] = 1

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is True
    assert evidence.failure_codes == ()


@pytest.mark.parametrize(
    "mutation",
    [
        "started_completed_mismatch",
        "single_pair_attempt_two",
        "bool_attempt",
        "attempt_above_two",
        "job_received_attempt",
        "queue_entered_attempt",
        "terminal_attempt",
    ],
)
def test_success_lifecycle_attempt_mutations_fail_closed(mutation: str) -> None:
    events = successful_lifecycle()
    if mutation == "started_completed_mismatch":
        events[2]["attempt"] = 1
        events[3]["attempt"] = 2
    elif mutation == "single_pair_attempt_two":
        events[2]["attempt"] = 2
        events[3]["attempt"] = 2
    elif mutation == "bool_attempt":
        events[2]["attempt"] = True
        events[3]["attempt"] = True
    elif mutation == "attempt_above_two":
        events[2]["attempt"] = 3
        events[3]["attempt"] = 3
    else:
        phase = {
            "job_received_attempt": "job_received",
            "queue_entered_attempt": "queue_entered",
            "terminal_attempt": "job_terminal",
        }[mutation]
        next(event for event in events if event["phase"] == phase)["attempt"] = 1

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


def test_success_lifecycle_rejects_completion_fields_on_cleanup_started() -> None:
    events = successful_lifecycle()
    next(
        event for event in events if event["phase"] == "cleanup_started"
    )["outcome"] = "passed"

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


def test_lifecycle_evidence_tool_versions_are_immutable() -> None:
    evidence = soak.evaluate_lifecycle(
        successful_lifecycle(),
        result_succeeded=True,
    )

    with pytest.raises(TypeError):
        evidence.tool_versions["vina"] = "tampered"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_event",
        "duplicate_validation",
        "reordered_events",
        "second_identity",
        "missing_meeko",
        "missing_vina",
    ],
)
def test_success_lifecycle_mutations_fail_closed(mutation: str) -> None:
    events = successful_lifecycle()
    if mutation == "missing_event":
        events.pop(4)
    elif mutation == "duplicate_validation":
        validation = next(
            event for event in events if event["phase"] == "validation_completed"
        )
        events.insert(events.index(validation) + 1, dict(validation))
    elif mutation == "reordered_events":
        events[5], events[6] = events[6], events[5]
    elif mutation == "second_identity":
        events.extend(successful_lifecycle("other-trace", "c" * 32))
    else:
        field = "meeko_version" if mutation == "missing_meeko" else "vina_version"
        next(
            event for event in events if event["phase"] == "validation_completed"
        )[field] = None

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


@pytest.mark.parametrize(
    ("phase", "field", "value"),
    [
        ("job_terminal", "outcome", "failed"),
        ("job_terminal", "cleanup_status", "failed"),
        ("cleanup_started", "cleanup_status", "not_started"),
        ("cleanup_completed", "cleanup_status", "failed"),
        ("validation_completed", "outcome", "failed"),
    ],
)
def test_success_lifecycle_rejects_terminal_cleanup_or_validation_mismatch(
    phase: str,
    field: str,
    value: object,
) -> None:
    events = successful_lifecycle()
    next(event for event in events if event["phase"] == phase)[field] = value

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


@pytest.mark.parametrize(
    ("failed_phase", "failure_class", "terminal_outcome", "cleanup_status"),
    [
        ("provisioning_completed", "create_timeout", "failed", "succeeded"),
        ("command_completed", "command_timeout", "failed", "succeeded"),
        ("validation_completed", None, "failed", "succeeded"),
        ("command_completed", None, "failed", "failed"),
        ("command_completed", None, "cancelled", "succeeded"),
    ],
)
def test_truthful_failed_or_cancelled_lifecycle_is_recorded(
    failed_phase: str,
    failure_class: str | None,
    terminal_outcome: str,
    cleanup_status: str,
) -> None:
    events = failed_lifecycle(
        failed_phase=failed_phase,
        failure_class=failure_class,
        terminal_outcome=terminal_outcome,
        cleanup_status=cleanup_status,
    )
    cleanup_completed = next(
        event for event in events if event["phase"] == "cleanup_completed"
    )
    assert cleanup_completed["outcome"] == (
        "passed" if cleanup_status == "succeeded" else "failed"
    )
    failed_index = PHASES.index(failed_phase)
    assert [event["phase"] for event in events] == [
        *PHASES[: failed_index + 1],
        *PHASES[-3:],
    ]
    assert failure_class is None or failure_class in soak.FAILURE_CLASSES
    terminal = next(event for event in events if event["phase"] == "job_terminal")
    assert terminal["failure_class"] == (
        (failure_class if failure_class is not None else "none")
        if terminal_outcome == "failed"
        else None
    )

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.trace_id == "broker-failed"
    assert evidence.job_id == "d" * 32
    assert evidence.events_complete is True
    assert evidence.tool_versions == {}
    assert evidence.terminal_outcome == terminal_outcome
    assert evidence.cleanup_status == cleanup_status
    assert evidence.failure_class == (failure_class or "none")
    assert evidence.failure_codes == ()


def test_cancelled_lifecycle_rejects_failed_cleanup() -> None:
    events = failed_lifecycle(
        failed_phase="command_completed",
        terminal_outcome="cancelled",
        cleanup_status="failed",
    )

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.terminal_outcome == "cancelled"
    assert evidence.cleanup_status == "failed"
    assert evidence.failure_codes == ("events_incomplete",)


def test_failed_lifecycle_rejects_versions_on_failed_validation() -> None:
    events = failed_lifecycle(failed_phase="validation_completed")
    validation = next(
        event for event in events if event["phase"] == "validation_completed"
    )
    validation["vina_version"] = "1.2.5"
    validation["meeko_version"] = "0.7.1"

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.tool_versions == {}
    assert evidence.failure_codes == ("events_incomplete",)


def test_failed_cleanup_after_passed_validation_requires_complete_tool_versions() -> None:
    events = successful_lifecycle()
    validation = next(
        event for event in events if event["phase"] == "validation_completed"
    )
    validation["vina_version"] = None
    validation["meeko_version"] = None
    cleanup = next(
        event for event in events if event["phase"] == "cleanup_completed"
    )
    cleanup["outcome"] = "failed"
    cleanup["failure_class"] = "destroy_failed"
    cleanup["cleanup_status"] = "failed"
    terminal = events[-1]
    terminal["outcome"] = "failed"
    terminal["failure_class"] = "destroy_failed"
    terminal["cleanup_status"] = "failed"

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.tool_versions == {}
    assert evidence.failure_codes == ("events_incomplete",)


def test_failed_cleanup_after_passed_validation_keeps_complete_tool_versions() -> None:
    events = successful_lifecycle()
    cleanup = next(
        event for event in events if event["phase"] == "cleanup_completed"
    )
    cleanup["outcome"] = "failed"
    cleanup["failure_class"] = "destroy_failed"
    cleanup["cleanup_status"] = "failed"
    terminal = events[-1]
    terminal["outcome"] = "failed"
    terminal["failure_class"] = "destroy_failed"
    terminal["cleanup_status"] = "failed"

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is True
    assert evidence.tool_versions == {"vina": "1.2.5", "meeko": "0.7.1"}
    assert evidence.failure_codes == ()


def test_validation_provenance_check_fails_closed_without_comparing_malformed_outcome() -> None:
    events = successful_lifecycle()
    validation = next(
        event for event in events if event["phase"] == "validation_completed"
    )
    validation["outcome"] = ExplodingComparison()

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


def test_compound_broker_failures_keep_truthful_lifecycle_complete() -> None:
    events = failed_lifecycle(
        failed_phase="command_completed",
        failure_class="command_timeout",
        cleanup_status="failed",
    )
    cleanup_completed = next(
        event for event in events if event["phase"] == "cleanup_completed"
    )
    terminal = next(event for event in events if event["phase"] == "job_terminal")
    cleanup_completed["failure_class"] = "destroy_failed"
    terminal["failure_class"] = "destroy_failed"

    validate_broker_events(events)
    assert [event["phase"] for event in events] == [
        *PHASES[: PHASES.index("command_completed") + 1],
        *PHASES[-3:],
    ]
    assert cleanup_completed["outcome"] == "failed"
    assert cleanup_completed["cleanup_status"] == "failed"
    assert terminal["outcome"] == "failed"
    assert terminal["cleanup_status"] == "failed"
    assert {
        event["failure_class"]
        for event in events
        if event["failure_class"] is not None
    } == {"command_timeout", "destroy_failed"}

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is True
    assert evidence.cleanup_status == "failed"
    assert evidence.failure_class == "unknown_control_plane_failure"
    assert evidence.failure_codes == ()


def test_failed_cleanup_retry_pairs_fold_without_hiding_compound_failure() -> None:
    events = failed_lifecycle(
        failed_phase="command_completed",
        failure_class="command_timeout",
        cleanup_status="failed",
    )
    cleanup_started = next(
        event for event in events if event["phase"] == "cleanup_started"
    )
    cleanup_completed = next(
        event for event in events if event["phase"] == "cleanup_completed"
    )
    cleanup_started["attempt"] = 1
    cleanup_completed["attempt"] = 1
    cleanup_completed["failure_class"] = "destroy_failed"
    terminal = events[-1]
    terminal["failure_class"] = "destroy_failed"
    events[-1:-1] = [
        dict(cleanup_started, attempt=2),
        dict(cleanup_completed, attempt=2),
    ]

    validate_broker_events(events)
    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is True
    assert evidence.cleanup_status == "failed"
    assert evidence.terminal_outcome == "failed"
    assert evidence.failure_class == "unknown_control_plane_failure"
    assert evidence.failure_codes == ("broker_retry_observed",)


def test_failed_science_preserves_recovered_cleanup_retry_evidence() -> None:
    events = failed_science_lifecycle_with_recovered_cleanup_retry()

    validate_broker_events(events)
    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is True
    assert evidence.cleanup_status == "succeeded"
    assert evidence.terminal_outcome == "failed"
    assert evidence.failure_class == "command_timeout"
    assert evidence.failure_codes == ("broker_retry_observed",)


@pytest.mark.parametrize("operation", ["upload", "command", "validation"])
def test_failed_lifecycle_rejects_retry_for_non_control_plane_operation(
    operation: str,
) -> None:
    events = retrying_failed_operation_lifecycle(operation)

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


@pytest.mark.parametrize(
    "mutation",
    [
        "completed_attempt",
        "retry_next_phase",
        "retry_outcome",
        "terminal_outcome",
    ],
)
def test_lifecycle_comparison_objects_fail_closed_without_raising(
    mutation: str,
) -> None:
    events = retrying_provisioning_failure_lifecycle()
    if mutation == "completed_attempt":
        events[3]["attempt"] = ExplodingComparison()
    elif mutation == "retry_next_phase":
        events[4]["phase"] = ExplodingComparison()
    elif mutation == "retry_outcome":
        events[3]["outcome"] = ExplodingComparison()
    else:
        events[-1]["outcome"] = ExplodingComparison()

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_attempt",
        "skipped_attempt",
        "attempt_above_two",
        "attempt_wrong_type",
        "unhashable_phase",
        "started_completed_mismatch",
        "nonadjacent_retry_pair",
        "missing_completed",
        "third_pair_before_terminal",
    ],
)
def test_failed_retry_lifecycle_mutations_fail_closed(mutation: str) -> None:
    events = retrying_provisioning_failure_lifecycle()
    if mutation == "duplicate_attempt":
        events[4]["attempt"] = 1
        events[5]["attempt"] = 1
    elif mutation == "skipped_attempt":
        events[2]["attempt"] = None
        events[3]["attempt"] = None
    elif mutation == "attempt_above_two":
        events[4]["attempt"] = 3
        events[5]["attempt"] = 3
    elif mutation == "attempt_wrong_type":
        events[2]["attempt"] = True
        events[3]["attempt"] = True
    elif mutation == "unhashable_phase":
        events[2]["phase"] = []
    elif mutation == "started_completed_mismatch":
        events[5]["attempt"] = 1
    elif mutation == "nonadjacent_retry_pair":
        events.insert(
            4,
            lifecycle_event(
                "broker-failed",
                "d" * 32,
                "upload_started",
            ),
        )
    elif mutation == "missing_completed":
        events.pop(5)
    else:
        events[-1:-1] = [dict(events[4]), dict(events[5])]

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


@pytest.mark.parametrize("operation", ["provisioning", "cleanup"])
def test_success_lifecycle_preserves_recovered_broker_retry(operation: str) -> None:
    events = successful_lifecycle_with_recovered_retry(operation)

    validate_broker_events(events)
    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is True
    assert evidence.failure_codes == ("broker_retry_observed",)
    assert evidence.failure_class == "none"


@pytest.mark.parametrize("operation", ["upload", "command", "validation"])
def test_success_lifecycle_rejects_non_control_plane_retry(operation: str) -> None:
    events = successful_lifecycle_with_recovered_retry(operation)

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


@pytest.mark.parametrize(
    "mutation",
    [
        "second_attempt_failed",
        "second_attempt_wrong_number",
        "third_pair",
        "retry_out_of_order",
    ],
)
def test_success_recovered_retry_mutations_fail_closed(mutation: str) -> None:
    events = successful_lifecycle_with_recovered_retry("provisioning")
    if mutation == "second_attempt_failed":
        events[5]["outcome"] = "failed"
        events[5]["failure_class"] = "connection_failed"
        events[-1]["outcome"] = "failed"
        events[-1]["failure_class"] = "connection_failed"
    elif mutation == "second_attempt_wrong_number":
        events[4]["attempt"] = 3
        events[5]["attempt"] = 3
    elif mutation == "third_pair":
        events[6:6] = [dict(events[4], attempt=3), dict(events[5], attempt=3)]
    else:
        events[4], events[6] = events[6], events[4]

    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


def test_corrected_report_shape_keeps_eighteen_successes_and_twelve_failures() -> None:
    failure_specs = [
        *(
            ("provider_error", "provisioning_completed", "server_500")
            for _ in range(5)
        ),
        *(
            ("tool_timeout", "command_completed", "command_timeout")
            for _ in range(5)
        ),
        *(
            ("invalid_output", "validation_completed", None)
            for _ in range(2)
        ),
    ]
    runs: list[dict[str, object]] = []
    broker_identities: list[tuple[str, str]] = []

    for index in range(1, 31):
        trace_id = f"broker-replay-{index:02d}"
        job_id = f"{index:032x}"
        if index <= 18:
            events = successful_lifecycle(trace_id, job_id)
            validate_broker_events(events)
            evidence = soak.evaluate_lifecycle(
                events,
                result_succeeded=True,
            )
            runs.append(
                valid_run(
                    index,
                    trace_id=evidence.trace_id,
                    cleanup_status=evidence.cleanup_status,
                    vina_version=evidence.tool_versions["vina"],
                    meeko_version=evidence.tool_versions["meeko"],
                    failure_class=evidence.failure_class,
                    failure_codes=list(evidence.failure_codes),
                    events_complete=evidence.events_complete,
                )
            )
        else:
            error_code, failed_phase, failure_class = failure_specs[index - 19]
            events = failed_lifecycle(
                failed_phase=failed_phase,
                failure_class=failure_class,
                trace_id=trace_id,
                job_id=job_id,
            )
            validate_broker_events(events)
            evidence = soak.evaluate_lifecycle(
                events,
                result_succeeded=False,
            )
            if error_code == "invalid_output":
                validation = next(
                    event
                    for event in events
                    if event["phase"] == "validation_completed"
                )
                terminal = next(
                    event for event in events if event["phase"] == "job_terminal"
                )
                assert validation["outcome"] == "failed"
                assert validation["failure_class"] is None
                assert terminal["failure_class"] == "none"
                assert evidence.failure_class == "none"
                assert evidence.events_complete is True
            runs.append(
                valid_run(
                    index,
                    trace_id=evidence.trace_id,
                    status="failed",
                    pose_count=0,
                    best_energy=None,
                    artifact_present=False,
                    artifact_path=None,
                    artifact_sha256=None,
                    artifact_sha256_valid=False,
                    cleanup_status=evidence.cleanup_status,
                    vina_version=None,
                    meeko_version=None,
                    failure_codes=[error_code],
                    failure_class=evidence.failure_class,
                    events_complete=evidence.events_complete,
                )
            )
        broker_identities.append((evidence.trace_id, evidence.job_id))

    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    report = soak.build_report(
        runs=runs,
        diagnostics=soak.diagnostics_delta(before, diagnostics(1)),
        idempotency={"passed": True, "sandbox_count": 1},
        queue={"passed": True, "accepted": 9, "saturated": 1},
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )

    report_runs = report["runs"]
    failed_runs = [run for run in report_runs if run["status"] == "failed"]
    assert report["status"] == "failed"
    assert report["pass_rate"] == 0.6
    assert report["event_completeness_rate"] == 1.0
    assert len(report_runs) == 30
    assert len(broker_identities) == len(set(broker_identities)) == 30
    assert sum(run["status"] == "passed" for run in report_runs) == 18
    assert len(failed_runs) == 12
    assert all(run["events_complete"] is True for run in report_runs)
    assert report["failure_type_distribution"] == {
        "server_500": 5,
        "command_timeout": 5,
        "none": 2,
    }
    assert Counter(
        code
        for run in failed_runs
        for code in run["failure_codes"]
    ) == {
        "provider_error": 5,
        "tool_timeout": 5,
        "invalid_output": 2,
    }
    assert Counter(
        (code, run["failure_class"])
        for run in failed_runs
        for code in run["failure_codes"]
    ) == {
        ("provider_error", "server_500"): 5,
        ("tool_timeout", "command_timeout"): 5,
        ("invalid_output", "none"): 2,
    }
    assert all(
        run["pose_count"] == 0
        and run["best_energy"] is None
        and run["artifact_present"] is False
        and run["artifact_path"] is None
        and run["artifact_sha256"] is None
        and run["artifact_sha256_valid"] is False
        and run["vina_version"] is None
        and run["meeko_version"] is None
        and soak.scientific_success(run) is False
        for run in failed_runs
    )
    assert all(
        "tool_version_invalid" not in run["failure_codes"]
        and "tool_version_mismatch" not in run["failure_codes"]
        for run in report_runs
        if run["status"] == "passed"
    )


@pytest.mark.parametrize("result_succeeded", [True, False])
@pytest.mark.parametrize(
    ("terminal_cleanup_status", "completed_cleanup_status", "completed_outcome"),
    [
        ("failed", "succeeded", "passed"),
        ("succeeded", "failed", "failed"),
    ],
)
def test_lifecycle_rejects_cleanup_terminal_disagreement(
    result_succeeded: bool,
    terminal_cleanup_status: str,
    completed_cleanup_status: str,
    completed_outcome: str,
) -> None:
    events = (
        successful_lifecycle()
        if result_succeeded
        else failed_lifecycle(failure_class="command_timeout")
    )
    terminal = next(event for event in events if event["phase"] == "job_terminal")
    cleanup = next(
        event for event in events if event["phase"] == "cleanup_completed"
    )
    terminal["cleanup_status"] = terminal_cleanup_status
    cleanup["cleanup_status"] = completed_cleanup_status
    cleanup["outcome"] = completed_outcome

    evidence = soak.evaluate_lifecycle(
        events,
        result_succeeded=result_succeeded,
    )

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


@pytest.mark.parametrize(
    "mutation",
    [
        "forged_success",
        "missing_terminal",
        "terminal_too_early",
        "multiple_terminals",
        "cleanup_completed_mismatch",
        "cleanup_mismatch",
        "failure_class_conflict",
        "illegal_failure_class",
        "second_identity",
    ],
)
def test_failed_lifecycle_mutations_fail_closed(mutation: str) -> None:
    events = failed_lifecycle(failure_class="command_timeout")
    if mutation == "forged_success":
        events[-1]["outcome"] = "passed"
    elif mutation == "missing_terminal":
        events.pop()
    elif mutation == "terminal_too_early":
        terminal = events.pop()
        events.insert(-2, terminal)
    elif mutation == "multiple_terminals":
        events.append(dict(events[-1]))
    elif mutation == "cleanup_completed_mismatch":
        events[-2]["outcome"] = "failed"
    elif mutation == "cleanup_mismatch":
        events[-1]["cleanup_status"] = "failed"
    elif mutation == "failure_class_conflict":
        events[-1]["failure_class"] = "server_500"
    elif mutation == "illegal_failure_class":
        events[-1]["failure_class"] = "not_allowed"
    else:
        events.extend(successful_lifecycle("other-trace", "e" * 32))

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


def test_disabled_gate_writes_safe_report_and_stable_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "soak.json"
    secret = "sk-" + "x" * 24
    code = soak.main(
        ["--report", str(report_path)],
        environ={"MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": "0", "API_KEY": secret},
        platform_name="posix",
    )

    payload = report_path.read_text(encoding="utf-8")
    assert code == 2
    assert capsys.readouterr().out == (
        "opensandbox_stability_soak=skipped code=stability_gate_disabled\n"
    )
    assert str(tmp_path) not in payload
    assert secret not in payload
    assert json.loads(payload)["status"] == "skipped"


@pytest.mark.parametrize(
    ("values", "percentile", "expected"),
    [([0], 0.95, 0.0), ([0, 10], 0.50, 5.0), ([0, 10], 0.95, 9.5), ([3, 1, 2], 0.50, 2.0)],
)
def test_percentile_uses_deterministic_linear_interpolation(
    values: list[int], percentile: float, expected: float
) -> None:
    assert soak.percentile(values, percentile) == expected


@pytest.mark.parametrize("value", [True, False, -1, math.nan, math.inf, "1"])
def test_percentile_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError):
        soak.percentile([value], 0.95)  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pose_count", True),
        ("pose_count", 0),
        ("best_energy", True),
        ("best_energy", math.nan),
        ("latency_ms", -1),
        ("artifact_sha256", "A" * 64),
        ("image_digest", "b" * 63),
        ("artifact_path", "../outputs/pose.pdbqt"),
        ("artifact_path", "C:/outputs/pose.pdbqt"),
        ("artifact_path", "/outputs/pose.pdbqt"),
        ("artifact_path", "outputs\\pose.pdbqt"),
        ("trace_id", "bad\ntrace"),
        ("vina_version", ""),
    ],
)
def test_scientific_success_fails_closed_on_invalid_run_fields(field: str, value: object) -> None:
    run = valid_run(1, **{field: value})
    assert soak.scientific_success(run) is False


def test_validate_report_rejects_unknown_fields_and_non_json_values() -> None:
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    report = soak.build_report(
        runs=[valid_run(index) for index in range(1, 31)],
        diagnostics=soak.diagnostics_delta(before, diagnostics(1)),
        idempotency={"passed": True, "sandbox_count": 1},
        queue={
            "passed": True,
            "accepted": 9,
            "saturated": 1,
        },
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )
    report["unknown"] = "value"
    with pytest.raises(soak.SafeReportError):
        soak.validate_report(report)
    report.pop("unknown")
    report["runs"][0]["unknown"] = object()
    with pytest.raises(soak.SafeReportError):
        soak.validate_report(report)


@pytest.mark.parametrize("latency_ms", [300001, 420000])
def test_report_records_safe_long_latency_without_relaxing_performance_gate(
    latency_ms: int,
) -> None:
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    runs = [valid_run(index, latency_ms=latency_ms) for index in range(1, 31)]
    runs[0].update(
        status="failed",
        failure_codes=["tool_timeout"],
        failure_class="command_timeout",
    )

    report = soak.build_report(
        runs=runs,
        diagnostics=soak.diagnostics_delta(before, diagnostics(1)),
        idempotency={"passed": True, "sandbox_count": 1},
        queue={"passed": True, "accepted": 9, "saturated": 1},
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )

    assert report["status"] == "failed"
    assert report["p50_latency_ms"] == latency_ms
    assert report["p95_latency_ms"] == latency_ms
    assert report["gates"]["latency"] is False
    assert soak.validate_report(report) == report


@pytest.mark.parametrize("latency_ms", [420001, True, -1])
def test_validate_run_rejects_latency_outside_recorded_range(
    latency_ms: object,
) -> None:
    with pytest.raises(soak.SafeReportError):
        soak.validate_run(valid_run(1, latency_ms=latency_ms))


@pytest.mark.parametrize("field", ["p50_latency_ms", "p95_latency_ms"])
@pytest.mark.parametrize("latency_ms", [420001, True, -1])
def test_validate_report_rejects_summary_latency_outside_recorded_range(
    field: str,
    latency_ms: object,
) -> None:
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    report = soak.build_report(
        runs=[valid_run(index) for index in range(1, 31)],
        diagnostics=soak.diagnostics_delta(before, diagnostics(1)),
        idempotency={"passed": True, "sandbox_count": 1},
        queue={"passed": True, "accepted": 9, "saturated": 1},
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )
    report[field] = latency_ms

    with pytest.raises(soak.SafeReportError):
        soak.validate_report(report)


@pytest.mark.parametrize(
    "unsafe",
    [
        "Authorization: Bearer abcdefghijklmnop",
        "API_KEY=abcdefghijklmnop",
        "sk-abcdefghijklmnop",
        "Bearer abcdefghijklmnop",
        "Basic abcdefghijklmnop",
        "/run/medchat-sandbox/broker.sock",
        "/home/operator/report.json",
        "C:\\Users\\operator\\report.json",
        "..\\secret",
        "line\x00break",
        "x" * 2049,
    ],
)
def test_recursive_report_validation_rejects_sensitive_or_unsafe_strings(unsafe: str) -> None:
    run = valid_run(1, warning_codes=[unsafe])
    with pytest.raises(soak.SafeReportError):
        soak.validate_run(run)


def test_corrected_replay_remains_redacted_and_non_clobbering(tmp_path: Path) -> None:
    secret = "sk-" + "x" * 24
    with pytest.raises(soak.SafeReportError):
        soak.validate_run(valid_run(1, warning_codes=[secret]))

    destination = tmp_path / "preserved-failed.json"
    failed = soak.gate_report("failed", "runtime_failed")
    soak.atomic_write_report(destination, failed)
    original = destination.read_bytes()

    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    safe_candidate = soak.build_report(
        runs=[valid_run(index) for index in range(1, 31)],
        diagnostics=soak.diagnostics_delta(before, diagnostics(1)),
        idempotency={"passed": True, "sandbox_count": 1},
        queue={"passed": True, "accepted": 9, "saturated": 1},
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )
    unsafe_candidate = json.loads(json.dumps(safe_candidate))
    unsafe_candidate["runs"][0]["warning_codes"] = [secret]

    with pytest.raises(soak.SafeReportError):
        soak.atomic_write_report(destination, unsafe_candidate)
    assert destination.read_bytes() == original
    remaining = list(tmp_path.iterdir())
    assert remaining == [destination]
    regular_files = [path for path in remaining if path.is_file()]
    assert regular_files == [destination]
    assert all(secret.encode() not in path.read_bytes() for path in regular_files)

    with pytest.raises(soak.SafeReportError):
        soak.gate_report("passed", "runtime_failed")
    with pytest.raises(soak.SafeReportError):
        soak.atomic_write_report(
            destination,
            soak.gate_report("skipped", "stability_gate_disabled"),
        )

    assert destination.read_bytes() == original
    assert secret.encode() not in original
    assert list(tmp_path.iterdir()) == [destination]


def test_artifact_path_rejects_symlink_escape(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outside = tmp_path / "outside"
    outputs.mkdir()
    outside.mkdir()
    link = outputs / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(soak.SafeReportError):
        soak.validate_artifact_path("outputs/linked/pose.pdbqt", project_root=tmp_path)


def test_atomic_write_is_private_and_rejects_symlink_or_hardlink(
    tmp_path: Path,
) -> None:
    report = soak.gate_report("skipped", "gate_disabled")
    destination = tmp_path / "report.json"
    soak.atomic_write_report(destination, report)
    assert json.loads(destination.read_text(encoding="utf-8")) == report
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600

    target = tmp_path / "target.json"
    target.write_text("sentinel", encoding="utf-8")
    destination.unlink()
    try:
        destination.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(soak.SafeReportError):
        soak.atomic_write_report(destination, report)
    assert target.read_text(encoding="utf-8") == "sentinel"


def test_atomic_write_rejects_symlink_parent_and_existing_hardlink(tmp_path: Path) -> None:
    report = soak.gate_report("failed", "write_failed")
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        linked_parent = None
    if linked_parent is not None:
        with pytest.raises(soak.SafeReportError):
            soak.atomic_write(linked_parent / "report.json", report)

    outside = tmp_path / "outside.json"
    outside.write_text("sentinel", encoding="utf-8")
    hardlink = tmp_path / "hardlink.json"
    try:
        os.link(outside, hardlink)
    except OSError:
        pytest.skip("hardlinks unavailable")
    with pytest.raises(soak.SafeReportError):
        soak.atomic_write(hardlink, report)
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_atomic_write_cleans_temp_when_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"

    def fail_link(source: object, target: object) -> None:
        del source, target
        raise OSError("secret-bearing platform failure")

    monkeypatch.setattr(soak.os, "link", fail_link)
    with pytest.raises(soak.SafeReportError):
        soak.atomic_write_report(destination, soak.gate_report("failed", "write_failed"))
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_never_replaces_existing_failed_report(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"
    failed = soak.gate_report("failed", "runtime_failed")
    soak.atomic_write_report(destination, failed)
    original = destination.read_bytes()

    with pytest.raises(soak.SafeReportError):
        soak.atomic_write_report(
            destination,
            soak.gate_report("skipped", "stability_gate_disabled"),
        )

    assert destination.read_bytes() == original


def test_atomic_write_concurrent_target_creation_never_clobbers_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"
    outside = tmp_path / "outside.json"
    outside.write_text("sentinel", encoding="utf-8")
    original_link = soak.os.link

    def race(source: object, target: object) -> None:
        original_link(outside, destination)
        original_link(source, target)

    monkeypatch.setattr(soak.os, "link", race)
    with pytest.raises(soak.SafeReportError):
        soak.atomic_write_report(destination, soak.gate_report("failed", "write_failed"))
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_diagnostics_client_is_uds_only_and_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return diagnostics()

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, path: str) -> Response:
            captured["path"] = path
            return Response()

    class HTTPTransport:
        def __init__(self, **kwargs: object) -> None:
            captured["transport"] = kwargs

    class Timeout:
        def __init__(self, value: float) -> None:
            self.value = value

    fake_httpx = SimpleNamespace(Client=Client, HTTPTransport=HTTPTransport, Timeout=Timeout)
    monkeypatch.setattr(soak, "httpx", fake_httpx)
    client = soak.BrokerDiagnosticsClient("/run/safe/broker.sock")
    assert client.snapshot() == diagnostics()
    assert captured["transport"] == {"uds": "/run/safe/broker.sock"}
    assert captured["client"]["base_url"] == "http://medchat-sandbox-broker"
    assert captured["client"]["timeout"].value == 5.0
    assert captured["path"] == "/v1/diagnostics"
    with pytest.raises(soak.SanitizedRuntimeError):
        soak.BrokerDiagnosticsClient("relative.sock")


def test_diagnostics_delta_handles_ring_rollover_by_identity() -> None:
    before = diagnostics()
    after = diagnostics()
    before_events = [
        soak.synthetic_event(f"old-{index}", "job_received") for index in range(1024)
    ]
    after_events = before_events[24:] + [
        soak.synthetic_event("new-job", phase) for phase in PHASES
    ]
    before["telemetry"]["recent_events"] = before_events
    after["telemetry"]["recent_events"] = after_events[-1024:]
    delta = soak.diagnostics_delta(before, after)
    assert [event["phase"] for event in delta["events"]] == list(PHASES)


def test_diagnostics_delta_uses_new_completed_events_for_repeated_latency_rollover() -> None:
    before = diagnostics()
    after = diagnostics()
    before["telemetry"]["phase_latency_ms"] = {"command|passed": [100] * 256}
    after["telemetry"]["phase_latency_ms"] = {"command|passed": [100] * 256}
    old_events = [soak.synthetic_event(f"old-{index}", "job_received") for index in range(1012)]
    completed = soak.synthetic_event("new-job", "command_completed")
    completed["duration_ms"] = 100
    before["telemetry"]["recent_events"] = old_events
    after["telemetry"]["recent_events"] = old_events[1:] + [completed]
    delta = soak.diagnostics_delta(before, after)
    assert delta["phase_latency_ms"] == {"command|passed": [100]}


@pytest.mark.parametrize("mutation", ["unknown", "bad_counter", "bad_runtime", "bad_breaker"])
def test_diagnostics_validation_fails_closed(mutation: str) -> None:
    value = diagnostics()
    if mutation == "unknown":
        value["unknown"] = True
    elif mutation == "bad_counter":
        value["telemetry"]["counters"] = {"secret": True}
    elif mutation == "bad_runtime":
        value["telemetry"]["runtime"]["queue_depth"] = 9
    else:
        value["circuit_breaker"]["state"] = "unknown"
    with pytest.raises(soak.SanitizedRuntimeError):
        soak.validate_diagnostics(value)


def test_running_container_count_uses_fixed_label_argv() -> None:
    calls: list[object] = []

    def command(argv: object, *, timeout: float) -> object:
        calls.append((argv, timeout))
        return SimpleNamespace(ok=True, stdout="id-one\nid-two\n")

    assert soak.running_labelled_container_count(command_runner=command) == 2
    assert calls[0][0] == [
        "docker",
        "ps",
        "--filter",
        "label=medchat.operation=molecular_docking",
        "--format",
        "{{.ID}}",
    ]


@pytest.mark.parametrize(
    ("gate", "platform", "deployment", "expected_code", "expected_status"),
    [
        ("0", "posix", [], 2, "skipped"),
        ("1", "nt", [], 2, "skipped"),
        ("1", "posix", ["deployment_failed"], 1, "failed"),
    ],
)
def test_cli_preflight_gates_never_execute_suite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gate: str,
    platform: str,
    deployment: list[str],
    expected_code: int,
    expected_status: str,
) -> None:
    report_path = tmp_path / "report.json"
    called: list[bool] = []
    monkeypatch.setattr(soak, "_deployment_failures", lambda: deployment)
    monkeypatch.setattr(soak, "run_stability_soak", lambda **kwargs: called.append(True))
    code = soak.main(
        ["--report", str(report_path)],
        environ={"MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": gate},
        platform_name=platform,
    )
    assert code == expected_code
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == expected_status
    assert called == []
    output = capsys.readouterr().out
    assert str(tmp_path) not in output


def test_executor_stops_observer_on_base_exception() -> None:
    calls: list[str] = []

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            calls.append("disable")

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    class Runner:
        def execute(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise KeyboardInterrupt

    executor = soak.StabilityExecutor(
        runner=Runner(),
        payload={},
        observer_factory=Observer,
        diagnostics_client=FakeDiagnostics(),
        project_root=Path.cwd(),
    )
    with pytest.raises(KeyboardInterrupt):
        executor.measured_run(1)
    assert calls == ["disable", "start", "stop"]


def test_timeout_probe_restores_deadline_and_stops_observer_on_base_exception() -> None:
    import src.docking.sandbox_runner as runner_module

    calls: list[str] = []
    original_deadline = runner_module._TOTAL_DEADLINE_SECONDS

    class Observer:
        failure_codes: list[str] = []

        def disable_intrusive_probes(self) -> None:
            calls.append("disable")

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    class Runner:
        def execute(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise KeyboardInterrupt

    executor = soak.StabilityExecutor(
        runner=Runner(),
        payload={},
        observer_factory=Observer,
        diagnostics_client=FakeDiagnostics(),
        project_root=Path.cwd(),
    )
    with pytest.raises(KeyboardInterrupt):
        executor.timeout_probe()
    assert runner_module._TOTAL_DEADLINE_SECONDS == original_deadline
    assert calls == ["disable", "start", "stop"]


def test_malformed_executor_values_fail_closed() -> None:
    executor = FakeExecutor()
    executor.queue_probe = lambda: {"passed": True}  # type: ignore[method-assign]
    report = soak.run_stability_soak(
        executor=executor,
        repeat=30,
        diagnostics=FakeDiagnostics(),
        orphan_counter=lambda: False,
    )
    assert report["status"] == "failed"
    assert report["gates"]["queue"] is False
    assert report["gates"]["no_orphans"] is False


def test_approved_public_contract_uses_fixed_repeat_and_method_names() -> None:
    calls: list[object] = []

    class Diagnostics:
        def snapshot(self) -> dict[str, object]:
            calls.append("snapshot")
            return diagnostics()

        def delta(self, before: object, after: object) -> dict[str, object]:
            del before, after
            calls.append("delta")
            return {
                "schema_version": 1,
                "events": [],
                "counters": {},
                "phase_latency_ms": {
                    f"{phase}|passed": [100] for phase in soak.PHASE_NAMES
                },
                "runtime": {
                    "queue_depth": 0,
                    "active_job_count": 0,
                    "cleanup_task_count": 0,
                    "isolated_task_count": 0,
                    "breaker_state": "closed",
                },
                "circuit_breaker": {
                    "state": "closed",
                    "failure_threshold": 5,
                    "window_seconds": 60,
                    "open_seconds": 30,
                },
            }

    class Executor:
        suite_calls = 1

        def execute_measured(self, index: int) -> dict[str, object]:
            calls.append(("measured", index))
            return valid_run(index)

        def exercise_idempotency_once(self) -> dict[str, object]:
            calls.append("idempotency")
            return {"sandbox_count": 1, "passed": True}

        def exercise_queue_pressure(self, *, capacity: int, overflow: int) -> dict[str, object]:
            calls.append(("queue", capacity, overflow))
            return {"accepted": 9, "saturated": 1, "passed": True}

        def exercise_cancellation(self) -> bool:
            calls.append("cancellation")
            return True

        def exercise_timeout(self) -> bool:
            calls.append("timeout")
            return True

        def exercise_security_contract(self) -> bool:
            calls.append("security")
            return True

    client = Diagnostics()
    report = soak.run_stability_soak(
        executor=Executor(), repeat=30, diagnostics=client, orphan_counter=lambda: 0
    )
    assert report["status"] == "passed"
    assert report["queue"] == {"accepted": 9, "saturated": 1, "passed": True}
    assert calls == [
        "snapshot",
        *(("measured", index) for index in range(1, 31)),
        "idempotency",
        ("queue", 8, 1),
        "cancellation",
        "timeout",
        "security",
        "snapshot",
        "delta",
    ]
    with pytest.raises(ValueError):
        soak.run_stability_soak(
            executor=Executor(), repeat=29, diagnostics=client, orphan_counter=lambda: 0
        )


def test_approved_percentile_quantile_and_atomic_alias(tmp_path: Path) -> None:
    assert soak.percentile([0, 10], 0.95) == 9.5
    destination = tmp_path / "report.json"
    soak.atomic_write(destination, soak.gate_report("skipped", "gate_disabled"))
    assert destination.is_file()


def test_cli_accepts_only_explicit_fixed_repeat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rejected = tmp_path / "rejected.json"
    accepted = tmp_path / "accepted.json"
    assert soak.main(
        ["--repeat", "29", "--report", str(rejected)],
        environ={"MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": "0"},
        platform_name="posix",
    ) == 2
    assert soak.main(
        ["--repeat", "30", "--report", str(accepted)],
        environ={"MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": "0"},
        platform_name="posix",
    ) == 2
    assert json.loads(accepted.read_text(encoding="utf-8"))["repeat"] == 30
    assert "29" not in capsys.readouterr().out


def test_diagnostics_delta_has_exact_approved_fields() -> None:
    delta = soak.diagnostics_delta(diagnostics(), diagnostics())
    assert set(delta) == {
        "schema_version",
        "events",
        "counters",
        "phase_latency_ms",
        "runtime",
        "circuit_breaker",
    }


def test_gate_report_contains_only_stable_reason_code() -> None:
    report = soak.gate_report("failed", "runtime_unavailable")
    assert report["diagnostics"] == {
        "schema_version": 1,
        "gate_failure": "runtime_unavailable",
    }
