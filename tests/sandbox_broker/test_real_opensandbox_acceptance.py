from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_opensandbox_docking_acceptance.py"
README = ROOT / "deployment/opensandbox/README.md"
OPENSANDBOX_SERVICE = ROOT / "deployment/opensandbox/medchat-opensandbox.service"
DEPLOYMENT_VALIDATOR = ROOT / "scripts/validate_opensandbox_deployment.py"
RUN_REAL = os.environ.get("MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE") == "1"


def _module():
    import scripts.run_opensandbox_docking_acceptance as acceptance

    return acceptance


def _pose(tmp_path: Path, name: str = "pose.pdbqt") -> tuple[Path, str]:
    path = tmp_path / name
    content = (
        b"MODEL 1\nREMARK VINA RESULT: -7.4 0.0 0.0\n"
        b"ATOM      1  C   LIG A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\nENDMDL\n"
    )
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def _success_result(path: Path, digest: str, *, latency: int = 100):
    return SimpleNamespace(
        success=True,
        data={
            "total_poses": 1,
            "best_pose": {"binding_energy": -7.4, "pose_file": str(path)},
            "pose_file": str(path),
        },
        elapsed_ms=latency,
        warnings=[],
        artifacts=[
            SimpleNamespace(
                path=str(path),
                mime_type="chemical/x-pdbqt",
                metadata={"sha256": digest},
            )
        ],
        quality={
            "real_execution": True,
            "execution_backend": "opensandbox",
            "secure_runtime": "gvisor",
            "sandbox_image_digest": "b" * 64,
            "cleanup_status": "succeeded",
        },
        provenance=SimpleNamespace(
            tool_name="molecular_docking",
            tool_version="AutoDock Vina 1.2.5",
            demo_mode=False,
            fallback_used=False,
        ),
        error=None,
    )


class _FakeRunner:
    def __init__(self, results: list[object], calls: list[tuple[dict, str]]) -> None:
        self._results = results
        self._calls = calls

    def execute(self, payload, *, job_id, progress_callback=None, cancel_event=None):
        self._calls.append((dict(payload), job_id))
        if cancel_event is not None and cancel_event.is_set():
            return SimpleNamespace(
                success=False,
                error=SimpleNamespace(code=SimpleNamespace(value="cancelled")),
                warnings=[],
            )
        return self._results.pop(0)


class _FakeObserver:
    def __init__(self, *, failures=(), sandbox_count=1) -> None:
        self.failure_codes = list(failures)
        self.sandbox_count = sandbox_count
        self.tool_versions = {
            "vina": "AutoDock Vina 1.2.5",
            "meeko": "Meeko 0.6.1",
        }
        self.image_digest = "b" * 64
        self.seen = sandbox_count > 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def wait_for_seen(self, _timeout=30.0) -> bool:
        return self.seen

    def wait_for_inspection_ready(self, _timeout=30.0) -> bool:
        return self.seen

    def assert_no_running(self) -> bool:
        return True


@pytest.mark.skipif(not RUN_REAL, reason="real OpenSandbox acceptance is opt-in")
def test_magl_sample_runs_three_times_through_gvisor(tmp_path: Path) -> None:
    acceptance = _module()
    report = acceptance.run_real_acceptance(
        receptor=ROOT / "data/samples/MAGL_5zun.pdb",
        ligand=ROOT / "data/samples/5.sdf",
        center=(5.99, 3.01, 17.345),
        size=(20.0, 20.0, 20.0),
        repeat=3,
        report_path=tmp_path / "report.json",
    )

    assert report["status"] == "passed"
    assert report["pass_rate"] == 1.0
    assert all(run["secure_runtime"] == "gvisor" for run in report["runs"])
    assert all(run["pose_count"] > 0 for run in report["runs"])
    assert all(math.isfinite(run["best_energy"]) for run in report["runs"])


def test_fake_success_uses_worker_runner_path_and_exact_report_schema(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    calls: list[tuple[dict, str]] = []
    results = [_success_result(pose, digest, latency=value) for value in (100, 200, 300)]
    observers: list[_FakeObserver] = []

    def observer_factory():
        observer = _FakeObserver()
        observers.append(observer)
        return observer

    report_path = tmp_path / "nested/report.json"
    report = acceptance.run_real_acceptance(
        receptor=ROOT / "data/samples/MAGL_5zun.pdb",
        ligand=ROOT / "data/samples/5.sdf",
        center=(5.99, 3.01, 17.345),
        size=(20.0, 20.0, 20.0),
        repeat=3,
        report_path=report_path,
        runner_factory=lambda _root: _FakeRunner(results, calls),
        deployment_validator=lambda: [],
        observer_factory=observer_factory,
        exercise_faults=False,
    )

    assert set(report) == {
        "schema_version",
        "status",
        "backend",
        "secure_runtime",
        "repeat",
        "pass_rate",
        "p50_latency_ms",
        "p95_latency_ms",
        "security_failures",
        "failure_type_distribution",
        "runs",
    }
    assert report["status"] == "passed"
    assert report["pass_rate"] == 1.0
    assert report["p50_latency_ms"] == 200
    assert report["p95_latency_ms"] == 290
    assert len(calls) == 3
    assert all(call[0]["center"] == [5.99, 3.01, 17.345] for call in calls)
    assert all(call[0]["size"] == [20.0, 20.0, 20.0] for call in calls)
    assert all(observer.sandbox_count == 1 for observer in observers)
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_report_projection_is_trace_safe_and_validates_artifact(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.prompt = "secret molecular prompt"
    result.stderr = "API key=never-report-this"
    result.quality["container_id"] = "raw-container-id"

    projected = acceptance._project_result(
        result,
        trace_id="acceptance-001",
        project_root=tmp_path,
        observer=_FakeObserver(),
    )

    assert set(projected) == {
        "trace_id",
        "status",
        "pose_count",
        "best_energy",
        "artifact_path",
        "artifact_sha256",
        "tool_versions",
        "image_digest",
        "secure_runtime",
        "cleanup_status",
        "latency_ms",
        "warning_codes",
        "failure_codes",
    }
    encoded = json.dumps(projected)
    assert "secret molecular prompt" not in encoded
    assert "never-report-this" not in encoded
    assert "raw-container-id" not in encoded
    assert str(tmp_path) not in encoded
    assert projected["artifact_path"] == "pose.pdbqt"

    pose.write_bytes(pose.read_bytes() + b"tamper")
    failed = acceptance._project_result(
        result,
        trace_id="acceptance-002",
        project_root=tmp_path,
        observer=_FakeObserver(),
    )
    assert failed["status"] == "failed"
    assert "artifact_identity_invalid" in failed["failure_codes"]


def test_projection_rejects_pose_count_energy_and_inspected_digest_mismatch(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    observer = _FakeObserver()
    observer.image_digest = "c" * 64

    failed = acceptance._project_result(
        result,
        trace_id="acceptance-mismatch",
        project_root=tmp_path,
        observer=observer,
    )

    assert failed["status"] == "failed"
    assert "container_image_digest_mismatch" in failed["failure_codes"]

    result.quality["sandbox_image_digest"] = "c" * 64
    result.data["total_poses"] = 2
    result.data["best_pose"]["binding_energy"] = -6.0
    failed = acceptance._project_result(
        result,
        trace_id="acceptance-science-mismatch",
        project_root=tmp_path,
        observer=observer,
    )
    assert "pose_count_mismatch" in failed["failure_codes"]
    assert "pose_energy_mismatch" in failed["failure_codes"]


def test_raw_vina_package_version_matches_equivalent_version_banner(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.provenance.tool_version = "1.2.5"
    observer = _FakeObserver()
    observer.tool_versions["vina"] = "AutoDock Vina v1.2.5"

    projected = acceptance._project_result(
        result,
        trace_id="acceptance-version-equivalent",
        project_root=tmp_path,
        observer=observer,
    )

    assert projected["status"] == "passed"
    assert "tool_version_mismatch" not in projected["failure_codes"]
    assert projected["tool_versions"]["vina"] == "1.2.5"


@pytest.mark.parametrize(
    "observed_version",
    [
        "AutoDock Vina v1.2.6",
        "AutoDock Vina development",
        "AutoDock Vina 4c3644e-mod",
    ],
)
def test_vina_version_comparison_remains_fail_closed(
    tmp_path: Path,
    observed_version: str,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.provenance.tool_version = "1.2.5"
    observer = _FakeObserver()
    observer.tool_versions["vina"] = observed_version

    projected = acceptance._project_result(
        result,
        trace_id="acceptance-version-rejected",
        project_root=tmp_path,
        observer=observer,
    )

    assert projected["status"] == "failed"
    assert "tool_version_mismatch" in projected["failure_codes"]


def test_identical_unparseable_vina_versions_are_rejected(tmp_path: Path) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.provenance.tool_version = "development"
    observer = _FakeObserver()
    observer.tool_versions["vina"] = "development"

    projected = acceptance._project_result(
        result,
        trace_id="acceptance-version-unparseable",
        project_root=tmp_path,
        observer=observer,
    )

    assert projected["status"] == "failed"
    assert "tool_version_invalid" in projected["failure_codes"]


def test_projection_uses_explicit_broker_validated_tool_versions(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.provenance.tool_version = "1.2.5"
    observer = _FakeObserver()
    observer.tool_versions = {}

    projected = acceptance._project_result(
        result,
        trace_id="acceptance-broker-versions",
        project_root=tmp_path,
        observer=observer,
        validated_tool_versions={"vina": "1.2.5", "meeko": "0.7.1"},
    )

    assert projected["status"] == "passed"
    assert projected["tool_versions"] == {"vina": "1.2.5", "meeko": "0.7.1"}
    assert "tool_version_invalid" not in projected["failure_codes"]
    assert "tool_version_mismatch" not in projected["failure_codes"]


@pytest.mark.parametrize(
    ("validated_tool_versions", "expected_failure"),
    [
        ({}, "tool_version_invalid"),
        ({"vina": "1.2.5"}, "tool_version_invalid"),
        ({"vina": "1.2.6", "meeko": "0.7.1"}, "tool_version_mismatch"),
        ({"vina": "1.2.5", "meeko": "/private/meeko"}, "tool_version_invalid"),
    ],
)
def test_projection_fails_closed_for_invalid_broker_validated_versions(
    tmp_path: Path,
    validated_tool_versions: dict[str, object],
    expected_failure: str,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    observer = _FakeObserver()

    projected = acceptance._project_result(
        result,
        trace_id="acceptance-broker-version-rejected",
        project_root=tmp_path,
        observer=observer,
        validated_tool_versions=validated_tool_versions,
    )

    assert projected["status"] == "failed"
    assert expected_failure in projected["failure_codes"]


def test_failed_projection_preserves_safe_bounded_latency(tmp_path: Path) -> None:
    acceptance = _module()
    failure = SimpleNamespace(
        success=False,
        elapsed_ms=271000,
        error=SimpleNamespace(
            code=SimpleNamespace(value="tool_timeout"),
            message=f"failed beside {tmp_path / 'private' / 'pose.pdbqt'}",
        ),
        warnings=[f"inspect {tmp_path / 'private' / 'runner.log'}"],
    )

    projected = acceptance._project_result(
        failure,
        trace_id="acceptance-failed-latency",
        project_root=tmp_path,
        observer=_FakeObserver(),
    )

    encoded = json.dumps(projected)
    assert projected["status"] == "failed"
    assert projected["latency_ms"] == 271000
    assert projected["failure_codes"] == ["tool_timeout"]
    assert projected["warning_codes"] == ["warning_redacted"]
    assert "failed beside" not in encoded
    assert str(tmp_path) not in encoded


@pytest.mark.parametrize("elapsed_ms", [-1, True, 420001, "271000"])
def test_failed_projection_rejects_unsafe_latency(
    tmp_path: Path,
    elapsed_ms: object,
) -> None:
    acceptance = _module()
    failure = SimpleNamespace(
        success=False,
        elapsed_ms=elapsed_ms,
        error=SimpleNamespace(
            code=SimpleNamespace(value="tool_timeout"),
            message=f"failed beside {tmp_path / 'private' / 'pose.pdbqt'}",
        ),
        warnings=[f"inspect {tmp_path / 'private' / 'runner.log'}"],
    )

    projected = acceptance._project_result(
        failure,
        trace_id="acceptance-failed-latency-rejected",
        project_root=tmp_path,
        observer=_FakeObserver(),
    )

    encoded = json.dumps(projected)
    assert projected["status"] == "failed"
    assert projected["latency_ms"] == 0
    assert projected["failure_codes"] == ["tool_timeout"]
    assert projected["warning_codes"] == ["warning_redacted"]
    assert "failed beside" not in encoded
    assert str(tmp_path) not in encoded


def test_fake_failure_and_security_failures_are_stable_and_counted(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    failure = SimpleNamespace(
        success=False,
        error=SimpleNamespace(code=SimpleNamespace(value="tool_timeout")),
        warnings=["unsafe warning with /host/private"],
    )
    report = acceptance.run_real_acceptance(
        receptor=ROOT / "data/samples/MAGL_5zun.pdb",
        ligand=ROOT / "data/samples/5.sdf",
        center=(5.99, 3.01, 17.345),
        size=(20.0, 20.0, 20.0),
        repeat=1,
        report_path=tmp_path / "report.json",
        runner_factory=lambda _root: _FakeRunner([failure], []),
        deployment_validator=lambda: ["runtime_not_gvisor"],
        observer_factory=lambda: _FakeObserver(failures=["network_probe_failed"]),
        exercise_faults=False,
    )

    assert report["status"] == "failed"
    assert report["pass_rate"] == 0.0
    assert report["security_failures"] == [
        "network_probe_failed",
        "runtime_not_gvisor",
    ]
    assert report["failure_type_distribution"] == {
        "network_probe_failed": 1,
        "runtime_not_gvisor": 1,
        "tool_timeout": 1,
    }
    assert report["runs"][0]["warning_codes"] == ["warning_redacted"]


def test_failure_distribution_counts_repeated_security_failures() -> None:
    acceptance = _module()

    report = acceptance._report(
        repeat=1,
        runs=[],
        security_failures=["network_probe_failed"] * 3,
    )

    assert report["security_failures"] == ["network_probe_failed"]
    assert report["failure_type_distribution"] == {"network_probe_failed": 3}


def test_invalid_arguments_and_disabled_gate_never_pass(tmp_path: Path) -> None:
    acceptance = _module()
    with pytest.raises(ValueError, match="repeat"):
        acceptance.run_real_acceptance(
            receptor=ROOT / "data/samples/MAGL_5zun.pdb",
            ligand=ROOT / "data/samples/5.sdf",
            center=(5.99, 3.01, 17.345),
            size=(20.0, 20.0, 20.0),
            repeat=0,
            report_path=tmp_path / "report.json",
            runner_factory=lambda _root: None,
            deployment_validator=lambda: [],
            observer_factory=lambda: _FakeObserver(),
        )

    exit_code = acceptance.main(
        ["--repeat", "3", "--report", str(tmp_path / "skipped.json")],
        environ={},
    )
    skipped = json.loads((tmp_path / "skipped.json").read_text(encoding="utf-8"))
    assert exit_code == 2
    assert skipped["status"] == "skipped"
    assert skipped["security_failures"] == ["acceptance_gate_disabled"]
    assert skipped["pass_rate"] == 0.0


def test_default_dependency_failure_short_circuits_without_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acceptance = _module()
    monkeypatch.setattr(
        acceptance,
        "_deployment_failures",
        lambda: ["docker_runtime_unregistered"],
    )
    monkeypatch.setattr(
        acceptance.SandboxDockingRunner,
        "from_env",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner was constructed")),
    )

    report = acceptance.run_real_acceptance(
        receptor=ROOT / "data/samples/MAGL_5zun.pdb",
        ligand=ROOT / "data/samples/5.sdf",
        center=(5.99, 3.01, 17.345),
        size=(20.0, 20.0, 20.0),
        repeat=3,
        report_path=tmp_path / "dependency-failed.json",
        exercise_faults=False,
    )

    assert report["status"] == "failed"
    assert report["security_failures"] == ["docker_runtime_unregistered"]
    assert report["runs"] == []


def test_real_vina_git_banner_uses_independent_package_metadata(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    calls: list[tuple[str, ...]] = []
    container_id = "a" * 64
    image_digest = "b" * 64

    def command(argv, *, timeout=12.0):
        call = tuple(argv)
        calls.append(call)
        if call[:4] == ("docker", "container", "inspect", "--format"):
            values = {
                "{{.HostConfig.Runtime}}": "runsc",
                "{{.HostConfig.NanoCpus}}": "2000000000",
                "{{.HostConfig.Memory}}": "4294967296",
                "{{.HostConfig.PidsLimit}}": "128",
                "{{.Config.Image}}": "registry.test/medchat@sha256:" + image_digest,
                "{{json .NetworkSettings.Networks}}": json.dumps(
                    {"medchat-opensandbox": {}}
                ),
            }
            return acceptance._CommandResult(True, values[call[4]])
        if call[:4] == ("docker", "network", "inspect", "--format"):
            values = {
                "{{.Name}}": "medchat-opensandbox",
                "{{json .Internal}}": "true",
                '{{index .Labels "com.medchat.opensandbox.network"}}': "v1",
                "{{(index .IPAM.Config 0).Gateway}}": "172.30.0.1",
            }
            return acceptance._CommandResult(True, values[call[4]])
        if call[:3] == ("docker", "exec", container_id):
            if call[-2:] == ("/opt/conda/bin/vina", "--version"):
                return acceptance._CommandResult(True, "AutoDock Vina 4c3644e-mod")
            if "m.version('vina')" in call[-1]:
                return acceptance._CommandResult(True, "1.2.5")
            if "m.version('meeko')" in call[-1]:
                return acceptance._CommandResult(True, "0.7.1")
            return acceptance._CommandResult(True, "")
        raise AssertionError(call)

    observer = acceptance.DockerSandboxObserver(command)
    observer.enable_intrusive_probes()
    observer._inspect_and_probe(container_id)

    assert observer.failure_codes == []
    assert observer.image_digest == image_digest
    assert observer.tool_versions == {
        "vina": "1.2.5",
        "meeko": "0.7.1",
    }
    assert any(
        call[-2:] == ("/opt/conda/bin/vina", "--version") for call in calls
    )
    assert any("m.version('vina')" in call[-1] for call in calls)

    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.provenance.tool_version = "1.2.5"
    projected = acceptance._project_result(
        result,
        trace_id="acceptance-real-vina-banner",
        project_root=tmp_path,
        observer=observer,
    )
    assert projected["status"] == "passed"
    assert projected["tool_versions"]["vina"] == "1.2.5"
    probe_sources = "\n".join(
        call[-1]
        for call in calls
        if call[:3] == ("docker", "exec", container_id)
    )
    assert "172.30.0.1" in probe_sources
    assert "172.17.0.1" not in probe_sources
    assert "/opt/medchat/run_docking.py" in probe_sources
    assert "_run_tool" in probe_sources
    assert "os.setsid" in probe_sources
    exec_calls = [
        call for call in calls if call[:3] == ("docker", "exec", container_id)
    ]
    pid_probe_index = next(
        index for index, call in enumerate(exec_calls) if "range(256)" in call[-1]
    )
    setsid_probe_index = next(
        index for index, call in enumerate(exec_calls) if "_run_tool" in call[-1]
    )
    version_probe_indices = [
        index
        for index, call in enumerate(exec_calls)
        if call[-2:] == (acceptance._VINA_BINARY, "--version")
        or "m.version('vina')" in call[-1]
        or "m.version('meeko')" in call[-1]
    ]
    assert pid_probe_index > setsid_probe_index
    assert version_probe_indices
    assert pid_probe_index > max(version_probe_indices)


def test_intrusive_observer_waits_for_container_exec_readiness() -> None:
    acceptance = _module()
    container_id = "a" * 64
    image_digest = "b" * 64
    readiness_attempts = 0

    def command(argv, *, timeout=12.0):
        nonlocal readiness_attempts
        call = tuple(argv)
        if call[:4] == ("docker", "container", "inspect", "--format"):
            values = {
                "{{.HostConfig.Runtime}}": "runsc",
                "{{.HostConfig.NanoCpus}}": "2000000000",
                "{{.HostConfig.Memory}}": "4294967296",
                "{{.HostConfig.PidsLimit}}": "128",
                "{{.Config.Image}}": "registry.test/medchat@sha256:" + image_digest,
                "{{json .NetworkSettings.Networks}}": json.dumps(
                    {"medchat-opensandbox": {}}
                ),
            }
            return acceptance._CommandResult(True, values[call[4]])
        if call[:4] == ("docker", "network", "inspect", "--format"):
            values = {
                "{{.Name}}": "medchat-opensandbox",
                "{{json .Internal}}": "true",
                '{{index .Labels "com.medchat.opensandbox.network"}}': "v1",
                "{{(index .IPAM.Config 0).Gateway}}": "172.30.0.1",
            }
            return acceptance._CommandResult(True, values[call[4]])
        if call[:3] == ("docker", "exec", container_id):
            if call[-1] == "raise SystemExit(0)":
                readiness_attempts += 1
                return acceptance._CommandResult(readiness_attempts >= 3, "")
            if call[-2:] == ("/opt/conda/bin/vina", "--version"):
                return acceptance._CommandResult(True, "AutoDock Vina 1.2.5")
            if "m.version('vina')" in call[-1]:
                return acceptance._CommandResult(True, "1.2.5")
            if "m.version('meeko')" in call[-1]:
                return acceptance._CommandResult(True, "0.7.1")
            return acceptance._CommandResult(True, "")
        raise AssertionError(call)

    observer = acceptance.DockerSandboxObserver(command)
    observer.enable_intrusive_probes()
    observer._inspect_and_probe(container_id)

    assert readiness_attempts == 3
    assert observer.wait_for_inspection_ready(0.0)
    assert observer.wait_for_probes_complete(0.0)
    assert observer.failure_codes == []


def test_measured_observer_never_executes_destructive_probes() -> None:
    acceptance = _module()
    container_id = "a" * 64
    calls: list[tuple[str, ...]] = []

    def command(argv, *, timeout=12.0):
        call = tuple(argv)
        calls.append(call)
        if call[:3] == ("docker", "exec", container_id):
            raise AssertionError("measured scientific sandbox received docker exec probe")
        if call[:4] == ("docker", "container", "inspect", "--format"):
            values = {
                "{{.HostConfig.Runtime}}": "runsc",
                "{{.HostConfig.NanoCpus}}": "2000000000",
                "{{.HostConfig.Memory}}": "4294967296",
                "{{.HostConfig.PidsLimit}}": "128",
                "{{.Config.Image}}": "registry.test/medchat@sha256:" + "b" * 64,
                "{{json .NetworkSettings.Networks}}": json.dumps(
                    {"medchat-opensandbox": {}}
                ),
            }
            return acceptance._CommandResult(True, values[call[4]])
        if call[:4] == ("docker", "network", "inspect", "--format"):
            values = {
                "{{.Name}}": "medchat-opensandbox",
                "{{json .Internal}}": "true",
                '{{index .Labels "com.medchat.opensandbox.network"}}': "v1",
                "{{(index .IPAM.Config 0).Gateway}}": "172.30.0.1",
            }
            return acceptance._CommandResult(True, values[call[4]])
        raise AssertionError(call)

    observer = acceptance.DockerSandboxObserver(command)
    observer._inspect_and_probe(container_id)

    assert observer.failure_codes == []
    assert observer.wait_for_inspection_ready(0.0)
    assert not any(call[:3] == ("docker", "exec", container_id) for call in calls)


def test_cancellation_waits_for_inspection_before_immediate_removal() -> None:
    acceptance = _module()
    container_id = "a" * 64
    removed = threading.Event()

    def command(argv, *, timeout=12.0):
        call = tuple(argv)
        if removed.is_set():
            return acceptance._CommandResult(False, "")
        if call[:4] == ("docker", "container", "inspect", "--format"):
            values = {
                "{{.HostConfig.Runtime}}": "runsc",
                "{{.HostConfig.NanoCpus}}": "2000000000",
                "{{.HostConfig.Memory}}": "4294967296",
                "{{.HostConfig.PidsLimit}}": "128",
                "{{.Config.Image}}": "registry.test/medchat@sha256:" + "b" * 64,
                "{{json .NetworkSettings.Networks}}": json.dumps(
                    {"medchat-opensandbox": {}}
                ),
            }
            return acceptance._CommandResult(True, values[call[4]])
        if call[:4] == ("docker", "network", "inspect", "--format"):
            values = {
                "{{.Name}}": "medchat-opensandbox",
                "{{json .Internal}}": "true",
                '{{index .Labels "com.medchat.opensandbox.network"}}': "v1",
                "{{(index .IPAM.Config 0).Gateway}}": "172.30.0.1",
            }
            return acceptance._CommandResult(True, values[call[4]])
        raise AssertionError(call)

    class ImmediateRemovalEvent(threading.Event):
        def set(self) -> None:
            removed.set()
            super().set()

    observer = acceptance.DockerSandboxObserver(command)
    cancel_event = ImmediateRemovalEvent()
    inspection = threading.Thread(
        target=observer._inspect_and_probe,
        args=(container_id,),
    )
    cancellation = threading.Thread(
        target=acceptance._request_cancellation_after_inspection,
        args=(observer, cancel_event),
    )

    cancellation.start()
    time.sleep(0.01)
    assert not removed.is_set()
    inspection.start()
    inspection.join(timeout=2.0)
    cancellation.join(timeout=2.0)

    assert not inspection.is_alive()
    assert not cancellation.is_alive()
    assert cancel_event.is_set()
    assert removed.is_set()
    assert observer.failure_codes == []


def test_destructive_probes_use_dedicated_worker_broker_sandbox() -> None:
    acceptance = _module()

    class SacrificialObserver(_FakeObserver):
        def __init__(self) -> None:
            super().__init__()
            self.intrusive_enabled = False
            self.probes_complete = threading.Event()

        def enable_intrusive_probes(self) -> None:
            self.intrusive_enabled = True

        def start(self) -> None:
            assert self.intrusive_enabled
            self.probes_complete.set()

        def wait_for_probes_complete(self, timeout=30.0) -> bool:
            return self.probes_complete.wait(timeout)

    observer = SacrificialObserver()
    calls: list[tuple[dict, str]] = []

    class SacrificialRunner:
        def execute(self, payload, *, job_id, progress_callback=None, cancel_event=None):
            calls.append((dict(payload), job_id))
            deadline = time.monotonic() + 1.0
            while cancel_event is not None and not cancel_event.is_set():
                if time.monotonic() >= deadline:
                    raise AssertionError("sacrificial cancellation was not requested")
                time.sleep(0.001)
            return SimpleNamespace(
                success=False,
                error=SimpleNamespace(code=SimpleNamespace(value="cancelled")),
                warnings=[],
            )

    failures, versions, image_digest = acceptance._exercise_sacrificial_security_contract(
        SacrificialRunner(),
        {"center": [5.99, 3.01, 17.345]},
        lambda: observer,
    )

    assert failures == []
    assert versions == observer.tool_versions
    assert image_digest == observer.image_digest
    assert len(calls) == 1
    assert calls[0][1].startswith("acceptance-sacrificial-")
    assert observer.intrusive_enabled


def test_measured_image_must_match_sacrificial_tool_provenance_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)

    class Runner:
        def execute(
            self,
            payload,
            *,
            job_id,
            progress_callback=None,
            cancel_event=None,
        ):
            if cancel_event is not None:
                deadline = time.monotonic() + 1.0
                while not cancel_event.is_set():
                    if time.monotonic() >= deadline:
                        raise AssertionError("sacrificial cancellation was not requested")
                    time.sleep(0.001)
                return SimpleNamespace(
                    success=False,
                    error=SimpleNamespace(code=SimpleNamespace(value="cancelled")),
                    warnings=[],
                )
            return result

    class SacrificialObserver(_FakeObserver):
        def __init__(self, image_digest: str) -> None:
            super().__init__()
            self.image_digest = image_digest
            self.probes_complete = threading.Event()

        def enable_intrusive_probes(self) -> None:
            pass

        def start(self) -> None:
            self.probes_complete.set()

        def wait_for_probes_complete(self, timeout=30.0) -> bool:
            return self.probes_complete.wait(timeout)

    observers = iter(
        [
            SacrificialObserver("a" * 64),
            SacrificialObserver("b" * 64),
        ]
    )
    monkeypatch.setattr(acceptance, "_exercise_fault_contracts", lambda *_args: [])

    report = acceptance.run_real_acceptance(
        receptor=ROOT / "data/samples/MAGL_5zun.pdb",
        ligand=ROOT / "data/samples/5.sdf",
        center=(5.99, 3.01, 17.345),
        size=(20.0, 20.0, 20.0),
        repeat=1,
        report_path=tmp_path / "report.json",
        runner_factory=lambda _root: Runner(),
        deployment_validator=lambda: [],
        observer_factory=lambda: next(observers),
        exercise_faults=True,
    )

    assert report["status"] == "failed"
    assert "tool_provenance_image_mismatch" in report["security_failures"]


def test_fault_observer_can_skip_intrusive_exec_probes() -> None:
    acceptance = _module()
    container_id = "a" * 64

    def command(argv, *, timeout=12.0):
        call = tuple(argv)
        if call[:3] == ("docker", "exec", container_id):
            raise AssertionError("intrusive probe ran during fault lifecycle")
        if call[:4] == ("docker", "container", "inspect", "--format"):
            values = {
                "{{.HostConfig.Runtime}}": "runsc",
                "{{.HostConfig.NanoCpus}}": "2000000000",
                "{{.HostConfig.Memory}}": "4294967296",
                "{{.HostConfig.PidsLimit}}": "128",
                "{{.Config.Image}}": "registry.test/medchat@sha256:" + "b" * 64,
                "{{json .NetworkSettings.Networks}}": json.dumps(
                    {"medchat-opensandbox": {}}
                ),
            }
            return acceptance._CommandResult(True, values[call[4]])
        if call[:4] == ("docker", "network", "inspect", "--format"):
            values = {
                "{{.Name}}": "medchat-opensandbox",
                "{{json .Internal}}": "true",
                '{{index .Labels "com.medchat.opensandbox.network"}}': "v1",
                "{{(index .IPAM.Config 0).Gateway}}": "172.30.0.1",
            }
            return acceptance._CommandResult(True, values[call[4]])
        raise AssertionError(call)

    observer = acceptance.DockerSandboxObserver(command)
    observer.disable_intrusive_probes()
    observer._inspect_and_probe(container_id)

    assert observer.failure_codes == []


def test_script_has_no_direct_opensandbox_or_unopted_docker_execution() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not any(name == "opensandbox" or name.startswith("opensandbox.") for name in imports)
    assert "OpenSandboxAdapter" not in constructed
    assert "SandboxDockingRunner" in source
    assert "MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE" in source
    assert "subprocess.run" in source
    assert not any("llm" in name.lower() for name in imports)
    assert not any(name.lower().startswith("llm") for name in constructed)
    assert "scientific_mock" not in source.lower()


def test_real_probes_use_the_pinned_image_python_interpreter() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '_SANDBOX_PYTHON = "/opt/conda/bin/python"' in source
    assert '_VINA_BINARY = "/opt/conda/bin/vina"' in source
    assert 'self._exec(container_id, "python"' not in source
    assert 'container_id,\n            "python",' not in source


def test_real_fixture_uses_production_default_exhaustiveness() -> None:
    acceptance = _module()

    payload = acceptance._payload(
        ROOT / "data/samples/MAGL_5zun.pdb",
        ROOT / "data/samples/5.sdf",
        (5.99, 3.01, 17.345),
        (20.0, 20.0, 20.0),
    )

    assert payload["exhaustiveness"] == 8


def test_fault_contracts_use_fast_real_probe_parameters(tmp_path: Path) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    payloads: list[dict[str, object]] = []

    class ProbeRunner:
        def execute(
            self,
            payload,
            *,
            job_id,
            progress_callback=None,
            cancel_event=None,
        ):
            del progress_callback
            payloads.append(dict(payload))
            if cancel_event is not None:
                cancel_event.wait(1)
                return SimpleNamespace(
                    success=False,
                    error=SimpleNamespace(
                        code=SimpleNamespace(value="cancelled")
                    ),
                    warnings=[],
                )
            if "timeout" in job_id:
                return SimpleNamespace(
                    success=False,
                    error=SimpleNamespace(
                        code=SimpleNamespace(value="tool_timeout")
                    ),
                    warnings=[],
                )
            return _success_result(pose, digest)

    failures = acceptance._exercise_fault_contracts(
        ProbeRunner(),
        {
            "receptor_path": "receptor.pdb",
            "ligand_path": "ligand.sdf",
            "center": [5.99, 3.01, 17.345],
            "size": [20.0, 20.0, 20.0],
            "exhaustiveness": 32,
            "num_modes": 9,
            "energy_range": 3.0,
        },
        _FakeObserver,
    )

    assert failures == []
    assert len(payloads) == 4
    assert all(payload["exhaustiveness"] == 8 for payload in payloads)


def test_readme_covers_qemu_install_secret_image_validation_and_rollback() -> None:
    text = README.read_text(encoding="utf-8")
    required = (
        "Ubuntu 24.04",
        "QEMU",
        "25.0.5",
        "runsc",
        "/etc/medchat/opensandbox.env",
        "0600",
        "openssl rand -hex 32",
        "medchat-opensandbox-firewall.service",
        "scripts/validate_opensandbox_deployment.py --runtime",
        "MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1",
        "MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock",
        "data/samples/MAGL_5zun.pdb",
        "data/samples/5.sdf",
        "local registry",
        "@sha256:",
        "rollback",
        "conda create --yes --prefix /opt/conda/envs/medchat python=3.10.14",
        "requirements-opensandbox-broker.txt",
        "requirements-agent-temporal.txt",
        "conda create --yes --prefix /opt/conda/envs/opensandbox-server python=3.10.14",
        "requirements-opensandbox-server.txt",
        "opensandbox-server==0.2.2",
        "/opt/conda/envs/opensandbox-server/bin/opensandbox-server",
        "python -m pip check",
    )
    assert all(value in text for value in required)
    assert "never" in text.lower()


def test_readme_keeps_server_dependencies_isolated_behind_validated_launcher() -> None:
    text = README.read_text(encoding="utf-8")
    broker_install = re.compile(
        r"/opt/conda/envs/medchat/bin/python -m pip install[^\n]*\\\n"
        r"\s+--require-hashes\s*\\\n"
        r"\s+--requirement deployment/opensandbox/"
        r"requirements-medchat-linux-x86_64\.lock"
    )
    server_install = re.compile(
        r"/opt/conda/envs/opensandbox-server/bin/python -m pip install\s*\\\n"
        r"\s+--disable-pip-version-check --require-hashes\s*\\\n"
        r"\s+--requirement deployment/opensandbox/"
        r"requirements-opensandbox-server-linux-x86_64\.lock"
    )
    server_in_medchat = re.compile(
        r"/opt/conda/envs/medchat/bin/python -m pip install[^\n]*(?:\\\n[^\n]*)?"
        r"requirements-opensandbox-server-linux-x86_64\.lock"
    )

    assert broker_install.search(text)
    assert server_install.search(text)
    assert not server_in_medchat.search(text)
    assert (
        "sudo ln -sfn /opt/conda/envs/opensandbox-server/bin/opensandbox-server"
        in text
    )
    assert "readlink -f /opt/conda/envs/medchat/bin/opensandbox-server" in text

    launcher = "/opt/conda/envs/medchat/bin/opensandbox-server"
    service = OPENSANDBOX_SERVICE.read_text(encoding="utf-8")
    validator = DEPLOYMENT_VALIDATOR.read_text(encoding="utf-8")
    assert f"ExecStart={launcher} --config /etc/medchat/opensandbox.toml" in service
    assert f'"{launcher} --config "' in validator
    assert '"/etc/medchat/opensandbox.toml"' in validator
