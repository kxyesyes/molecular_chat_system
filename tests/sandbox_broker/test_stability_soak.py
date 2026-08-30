from __future__ import annotations

import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_opensandbox_stability_soak as soak


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
            diagnostics_client.events.extend(
                soak.synthetic_event(job_id, phase) for phase in PHASES
            )
            return object()

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
