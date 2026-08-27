from __future__ import annotations

import ast
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_opensandbox_docking_acceptance.py"
README = ROOT / "deployment/opensandbox/README.md"
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
    ["AutoDock Vina v1.2.6", "AutoDock Vina development"],
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


def test_fake_docker_inspection_uses_owned_gateway_and_fixed_security_probes() -> None:
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
            if call[-2:] == ("vina", "--version"):
                return acceptance._CommandResult(True, "AutoDock Vina 1.2.5")
            if "importlib.metadata" in call[-1]:
                return acceptance._CommandResult(True, "Meeko 0.6.1")
            return acceptance._CommandResult(True, "")
        raise AssertionError(call)

    observer = acceptance.DockerSandboxObserver(command)
    observer._inspect_and_probe(container_id)

    assert observer.failure_codes == []
    assert observer.image_digest == image_digest
    assert observer.tool_versions == {
        "vina": "AutoDock Vina 1.2.5",
        "meeko": "Meeko 0.6.1",
    }
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
    )
    assert all(value in text for value in required)
    assert "never" in text.lower()
