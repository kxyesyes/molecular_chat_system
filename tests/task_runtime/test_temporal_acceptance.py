from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def _valid_run(**overrides):
    run = {
        "status": "succeeded",
        "backend": "temporal",
        "execution_backend": "opensandbox",
        "secure_runtime": "gvisor",
        "sandbox_image_digest": "b" * 64,
        "cleanup_status": "succeeded",
        "workflow_id": "medchat-docking-task-1",
        "vina_attempts": 1,
        "terminal_event_count": 1,
        "pose_exists": True,
        "pose_count": 2,
        "binding_energy": -7.0,
        "artifact_path": "task-1/artifacts/docking_pose.pdbqt",
        "artifact_sha256": "a" * 64,
        "tool_name": "molecular_docking",
        "tool_version": "vina-1.2.5",
        "model_name": "AutoDock-Vina",
        "model_version": "1.2.5",
        "provenance_complete": True,
        "demo_mode": False,
        "fallback_used": False,
        "latency_ms": 125,
        "warnings": [],
        "artifact_integrity_valid": True,
        "terminal_event_valid": True,
    }
    run.update(overrides)
    return run


def test_report_requires_unique_execution_and_terminal_event():
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs([_valid_run()])

    assert report["status"] == "passed"
    assert report["duplicate_vina_count"] == 0
    assert report["terminal_event_violation_count"] == 0
    assert report["pass_rate"] == 1.0
    assert report["runs"][0]["provenance"] == {
        "tool_name": "molecular_docking",
        "tool_version": "vina-1.2.5",
        "model_name": "AutoDock-Vina",
        "model_version": "1.2.5",
    }
    assert report["runs"][0]["warnings"] == []


def test_report_allowlists_verified_opensandbox_execution_contract():
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs(
        [_valid_run(untrusted_internal={"container_id": "raw-container"})],
        expected_execution_backend="opensandbox",
    )

    assert report["status"] == "passed"
    projected = report["runs"][0]
    assert projected["execution_backend"] == "opensandbox"
    assert projected["secure_runtime"] == "gvisor"
    assert projected["sandbox_image_digest"] == "b" * 64
    assert projected["cleanup_status"] == "succeeded"
    assert "untrusted_internal" not in projected
    assert "raw-container" not in json.dumps(report)


def test_verified_completion_projects_opensandbox_trust_into_temporal_report(
    tmp_path: Path,
):
    from scripts.run_temporal_docking_acceptance import (
        _project_real_record,
        summarize_runs,
    )
    from src.agent.contracts import ToolProvenance, ToolResult, WorkflowArtifact
    from src.task_runtime.docking_execution import DockingExecution
    from src.task_runtime.models import TaskStatus
    from src.task_runtime.staging import DockingInputStager
    from src.task_runtime.store import TaskStore
    from src.task_runtime.temporal.activities import (
        TemporalDockingActivities,
        _safe_execution_contract,
        _safe_provenance,
        _summarize_docking_result,
    )

    task_id = "00000000-0000-4000-8000-000000000099"
    staging_root = tmp_path / "staging"
    output_root = tmp_path / "worker-output"
    manifest = DockingInputStager(staging_root).stage(
        task_id,
        "receptor.pdb",
        b"ATOM\n",
        "ligand.sdf",
        b"$$$$\n",
        None,
        {
            "center": [1, 2, 3],
            "size": [20, 20, 20],
            "exhaustiveness": 8,
            "num_modes": 10,
        },
    )
    image_digest = "b" * 64

    def execute(_payload, **_control):
        pose = output_root / f"docking_{task_id}" / "result.pdbqt"
        pose.parent.mkdir(parents=True)
        pose.write_text(
            "MODEL 1\nREMARK VINA RESULT: -7.200 0.000 0.000\n"
            "ROOT\nATOM      1  C   LIG A   1       0.000   0.000   0.000"
            "  0.00  0.00    +0.000 C\nENDROOT\nTORSDOF 0\nENDMDL\n",
            encoding="ascii",
        )
        pose_digest = hashlib.sha256(pose.read_bytes()).hexdigest()
        return ToolResult.success_result(
            "molecular_docking",
            data={
                "total_poses": 1,
                "pose_file": str(pose),
                "best_pose": {"binding_energy": -7.2, "pose_file": str(pose)},
            },
            artifacts=[
                WorkflowArtifact(
                    "docking_pose",
                    str(pose),
                    "Verified docking pose",
                    mime_type="chemical/x-pdbqt",
                    metadata={"sha256": pose_digest},
                )
            ],
            quality={
                "real_execution": True,
                "execution_backend": "opensandbox",
                "secure_runtime": "gvisor",
                "sandbox_image_digest": image_digest,
                "cleanup_status": "succeeded",
                "raw_endpoint": "https://sandbox.internal.invalid",
                "container_id": "container-internal-123",
                "host_path": str(tmp_path.resolve()),
            },
            provenance=ToolProvenance(
                tool_name="molecular_docking",
                tool_version="molecular-docking-adapter-1",
                model_name="AutoDock Vina",
                demo_mode=False,
                fallback_used=False,
            ),
        )

    execution = DockingExecution(
        staging_root,
        raw_executor=execute,
        allowed_output_root=output_root,
    )
    completed = execution.run_verified(task_id, manifest, attempt=1)
    reused = execution.run_verified(task_id, manifest, attempt=2)
    assert completed["success"] is True, completed
    assert reused["reused_completion"] is True
    for result in (completed, reused):
        assert {
            key: result["quality"].get(key)
            for key in (
                "execution_backend",
                "secure_runtime",
                "sandbox_image_digest",
                "cleanup_status",
            )
        } == {
            "execution_backend": "opensandbox",
            "secure_runtime": "gvisor",
            "sandbox_image_digest": image_digest,
            "cleanup_status": "succeeded",
        }
        encoded = json.dumps(result)
        assert "sandbox.internal.invalid" not in encoded
        assert "container-internal-123" not in encoded
        assert str(tmp_path.resolve()) not in encoded

    assert _safe_provenance(completed["provenance"], 1) is not None
    assert _safe_execution_contract(completed["quality"]) is not None
    outcome = _summarize_docking_result(completed, task_id=task_id, attempt=1)
    assert outcome["status"] == "succeeded", (outcome, completed)
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create(
        task_id=task_id,
        task_type="docking",
        payload={},
        backend="temporal",
        external_workflow_id=f"medchat-docking-{task_id}",
    )
    assert store.claim_running(task_id, attempt=1)
    activities = TemporalDockingActivities(store, execution)
    projected = asyncio.run(
        activities._project_terminal({"operation": "terminal", **outcome})
    )
    assert projected["applied"] is True
    record = store.get(task_id)
    assert record.status is TaskStatus.SUCCEEDED
    assert record.result == {
        "best_energy": -7.2,
        "cleanup_status": "succeeded",
        "execution_backend": "opensandbox",
        "pose_count": 1,
        "sandbox_image_digest": image_digest,
        "secure_runtime": "gvisor",
        "warnings": [],
    }

    run = _project_real_record(
        record,
        store.events(task_id),
        staging_root=staging_root,
        task_id=task_id,
        latency_ms=10,
    )
    report = summarize_runs(
        [run],
        expected_backend="temporal",
        expected_execution_backend="opensandbox",
    )

    assert report["status"] == "passed"
    assert report["runs"][0]["execution_backend"] == "opensandbox"
    assert report["runs"][0]["secure_runtime"] == "gvisor"
    assert report["runs"][0]["sandbox_image_digest"] == image_digest
    assert report["runs"][0]["cleanup_status"] == "succeeded"


@pytest.mark.parametrize(
    ("override", "failure"),
    [
        ({"execution_backend": None}, "execution_backend_mismatch"),
        ({"execution_backend": "local"}, "execution_backend_mismatch"),
        ({"secure_runtime": None}, "secure_runtime_invalid"),
        ({"secure_runtime": "runc"}, "secure_runtime_invalid"),
        ({"sandbox_image_digest": None}, "sandbox_image_digest_invalid"),
        ({"sandbox_image_digest": "B" * 64}, "sandbox_image_digest_invalid"),
        ({"sandbox_image_digest": "b" * 63}, "sandbox_image_digest_invalid"),
        ({"cleanup_status": None}, "cleanup_status_invalid"),
        ({"cleanup_status": "failed"}, "cleanup_status_invalid"),
    ],
)
def test_report_fails_closed_on_incomplete_opensandbox_execution_contract(
    override,
    failure,
):
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs(
        [_valid_run(**override)],
        expected_execution_backend="opensandbox",
    )

    assert report["status"] == "failed"
    assert failure in report["runs"][0]["failures"]
    assert report["failure_type_distribution"][failure] == 1


def test_report_has_stable_failure_distribution() -> None:
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs(
        [
            _valid_run(),
            _valid_run(pose_exists=False, provenance_complete=False),
        ]
    )

    assert report["failure_type_distribution"] == {
        "pose_missing": 1,
        "provenance_incomplete": 1,
    }


def test_real_release_gate_rejects_local_fallback_backend():
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs(
        [_valid_run(backend="local")],
        expected_backend="temporal",
    )

    assert report["status"] == "failed"
    assert "backend_mismatch" in report["runs"][0]["failures"]


@pytest.mark.parametrize(
    ("override", "failure"),
    [
        ({"vina_attempts": 2}, "duplicate_vina_execution"),
        ({"terminal_event_count": 2}, "terminal_event_count_invalid"),
        ({"pose_exists": False}, "pose_missing"),
        ({"binding_energy": "-7.0"}, "binding_energy_invalid"),
        ({"provenance_complete": False}, "provenance_incomplete"),
        ({"demo_mode": True}, "demo_science_forbidden"),
        ({"fallback_used": True}, "fallback_science_forbidden"),
        ({"artifact_path": "C:/private/pose.pdbqt"}, "artifact_path_invalid"),
    ],
)
def test_report_fails_closed_on_scientific_gate_violation(override, failure):
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs([_valid_run(**override)])

    assert report["status"] == "failed"
    assert failure in report["runs"][0]["failures"]


def test_report_does_not_serialize_credentials_or_absolute_paths():
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs(
        [
            _valid_run(
                warnings=["sk-" + "contract-secret-value-123456789", "C:/private/input.pdb"],
                debug_payload={"token": "AKIAABCDEFGHIJKLMNOP"},
                opensandbox_api_key="sk-" + "fake-opensandbox-key-123456789",
                opensandbox_endpoint="https://sandbox.internal.invalid:9443",
                sandbox_id="sandbox-internal-id-123",
                container_id="container-internal-id-456",
                host_path="D:/private/receptor.pdb",
                prompt="DO_NOT_SERIALIZE_THIS_PROMPT",
                receptor_contents="ATOM DO_NOT SERIALIZE",
                ligand_contents="$$$$ DO NOT SERIALIZE",
                raw_stderr="RAW STDERR DO NOT SERIALIZE",
            )
        ]
    )
    encoded = json.dumps(report, ensure_ascii=False)

    assert "sk-contract-secret" not in encoded
    assert "AKIA" not in encoded
    assert "C:/private" not in encoded
    for forbidden in (
        "fake-opensandbox-key",
        "sandbox.internal.invalid",
        "sandbox-internal-id",
        "container-internal-id",
        "D:/private",
        "DO_NOT_SERIALIZE_THIS_PROMPT",
        "ATOM DO NOT SERIALIZE",
        "$$$$ DO NOT SERIALIZE",
        "RAW STDERR DO NOT SERIALIZE",
    ):
        assert forbidden not in encoded
    assert report["status"] == "failed"
    assert "sensitive_output_detected" in report["runs"][0]["failures"]


def test_unknown_fields_do_not_change_stable_failure_projection():
    from scripts.run_temporal_docking_acceptance import summarize_runs

    report = summarize_runs(
        [
            _valid_run(cleanup_status="failed", unknown="drop-me"),
            _valid_run(cleanup_status="failed", another_unknown={"nested": True}),
        ],
        expected_execution_backend="opensandbox",
    )

    assert report["status"] == "failed"
    assert report["failure_type_distribution"] == {"cleanup_status_invalid": 2}
    encoded = json.dumps(report, sort_keys=True)
    assert "unknown" not in encoded
    assert "drop-me" not in encoded


@pytest.mark.parametrize(
    ("field", "credential"),
    [
        ("workflow_id", "github_pat_" + "A" * 24),
        ("artifact_path", "artifacts/github_pat_" + "B" * 24 + ".pdbqt"),
        ("tool_version", "github_pat_" + "C" * 24),
        ("workflow_id", "xgithub_pat_" + "D" * 24),
        ("artifact_path", "artifacts/xgithub_pat_" + "E" * 24 + ".pdbqt"),
        ("tool_version", "xgithub_pat_" + "F" * 24),
        ("artifact_path", "artifacts/token=supersecret123456789.pdbqt"),
        ("warnings", "xgithub_pat_" + "G" * 24),
    ],
)
def test_every_report_string_is_projected_fail_closed(field, credential):
    from scripts.run_temporal_docking_acceptance import summarize_runs

    value = [credential] if field == "warnings" else credential
    report = summarize_runs([_valid_run(**{field: value})])
    encoded = json.dumps(report, ensure_ascii=False)

    assert credential not in encoded
    assert report["status"] == "failed"
    assert "sensitive_output_detected" in report["runs"][0]["failures"]


def test_real_projection_rehashes_pose_and_requires_matching_terminal_event(tmp_path):
    from scripts.run_temporal_docking_acceptance import _project_real_record, summarize_runs

    task_id = "task-tampered"
    relative = "artifacts/docking_pose.pdbqt"
    pose = tmp_path / task_id / relative
    pose.parent.mkdir(parents=True)
    pose.write_bytes(b"tampered pose")
    record = SimpleNamespace(
        status=SimpleNamespace(value="succeeded"),
        backend="temporal",
        external_workflow_id="medchat-docking-task-tampered",
        attempt=1,
        result={"pose_count": 1, "best_energy": -7.0, "pose_file": relative},
        artifacts=[
            {
                "artifact_type": "log",
                "status": "failed",
                "path": relative,
                "sha256": "d" * 64,
            }
        ],
        provenance={
            "tool_name": "molecular_docking",
            "tool_version": "vina-1.2.5",
            "demo_mode": False,
            "fallback_used": False,
        },
        warnings=[],
    )
    events = [SimpleNamespace(is_terminal=True, event_type="task_failed")]

    run = _project_real_record(
        record,
        events,
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=10,
    )
    report = summarize_runs([run], expected_backend="temporal")

    assert report["status"] == "failed"
    assert "artifact_integrity_invalid" in report["runs"][0]["failures"]
    assert "terminal_event_mismatch" in report["runs"][0]["failures"]


def test_real_projection_resolves_pose_from_strict_artifact_record(tmp_path):
    from scripts.run_temporal_docking_acceptance import _project_real_record, summarize_runs

    task_id = "00000000-0000-4000-8000-000000000010"
    relative = "artifacts/docking_pose.pdbqt"
    pose = tmp_path / task_id / relative
    pose.parent.mkdir(parents=True)
    pose.write_bytes(b"REMARK VINA RESULT: -7.0 0.0 0.0\n")
    import hashlib

    digest = hashlib.sha256(pose.read_bytes()).hexdigest()
    record = SimpleNamespace(
        status=SimpleNamespace(value="succeeded"),
        backend="temporal",
        external_workflow_id=f"medchat-docking-{task_id}",
        attempt=1,
        result={"pose_count": 1, "best_energy": -7.0},
        artifacts=[
            {
                "artifact_type": "docking_pose",
                "path": relative,
                "sha256": digest,
            }
        ],
        provenance={
            "tool_name": "molecular_docking",
            "tool_version": "molecular-docking-adapter-1",
            "model_name": "AutoDock Vina",
            "demo_mode": False,
            "fallback_used": False,
        },
        warnings=[],
    )
    events = [SimpleNamespace(is_terminal=True, event_type="task_succeeded")]

    run = _project_real_record(
        record,
        events,
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=10,
    )
    report = summarize_runs([run], expected_backend="temporal")

    assert report["status"] == "passed"
    assert report["runs"][0]["artifact_path"] == relative
    assert report["runs"][0]["artifact_sha256"] == digest


def _acceptance_record(task_id: str, relative: str, digest: str) -> SimpleNamespace:
    return SimpleNamespace(
        status=SimpleNamespace(value="succeeded"),
        backend="temporal",
        external_workflow_id=f"medchat-docking-{task_id}",
        attempt=1,
        result={"pose_count": 1, "best_energy": -7.0},
        artifacts=[
            {
                "artifact_type": "docking_pose",
                "path": relative,
                "sha256": digest,
            }
        ],
        provenance={
            "tool_name": "molecular_docking",
            "tool_version": "vina-1.2.5",
            "model_name": "AutoDock Vina",
            "model_version": "1.2.5",
            "demo_mode": False,
            "fallback_used": False,
        },
        warnings=[],
    )


def test_real_projection_rejects_oversize_pose(tmp_path: Path) -> None:
    from scripts.run_temporal_docking_acceptance import _project_real_record

    task_id = "00000000-0000-4000-8000-000000000020"
    relative = "artifacts/docking_pose.pdbqt"
    pose = tmp_path / task_id / relative
    pose.parent.mkdir(parents=True)
    with pose.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)

    run = _project_real_record(
        _acceptance_record(task_id, relative, "a" * 64),
        [SimpleNamespace(is_terminal=True, event_type="task_succeeded")],
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=1,
    )

    assert run["pose_exists"] is False
    assert run["artifact_sha256"] is None
    assert run["artifact_integrity_valid"] is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX nofollow artifact test")
def test_real_projection_rejects_symlink_fifo_and_device(tmp_path: Path) -> None:
    from scripts.run_temporal_docking_acceptance import _project_real_record
    from src.task_runtime.secure_io import read_file_snapshot

    task_id = "00000000-0000-4000-8000-000000000021"
    relative = "artifacts/docking_pose.pdbqt"
    artifact = tmp_path / task_id / relative
    artifact.parent.mkdir(parents=True)
    target = tmp_path / "target.pdbqt"
    target.write_bytes(b"pose")
    artifact.symlink_to(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()

    symlink_run = _project_real_record(
        _acceptance_record(task_id, relative, digest),
        [SimpleNamespace(is_terminal=True, event_type="task_succeeded")],
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=1,
    )
    artifact.unlink()
    os.mkfifo(artifact)
    fifo_run = _project_real_record(
        _acceptance_record(task_id, relative, digest),
        [SimpleNamespace(is_terminal=True, event_type="task_succeeded")],
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=1,
    )

    assert symlink_run["artifact_integrity_valid"] is False
    assert fifo_run["artifact_integrity_valid"] is False
    with pytest.raises(ValueError, match="unsafe file snapshot"):
        read_file_snapshot(Path("/dev/null"), 1024)


@pytest.mark.skipif(os.name != "posix", reason="POSIX nofollow component test")
def test_real_projection_rejects_symlinked_artifact_ancestor(tmp_path: Path) -> None:
    from scripts.run_temporal_docking_acceptance import _project_real_record

    task_id = "00000000-0000-4000-8000-000000000023"
    relative = "artifacts/docking_pose.pdbqt"
    task_root = tmp_path / task_id
    task_root.mkdir()
    external = tmp_path / "external-artifacts"
    external.mkdir()
    pose = external / "docking_pose.pdbqt"
    pose.write_bytes(b"pose")
    (task_root / "artifacts").symlink_to(external, target_is_directory=True)
    digest = hashlib.sha256(pose.read_bytes()).hexdigest()

    run = _project_real_record(
        _acceptance_record(task_id, relative, digest),
        [SimpleNamespace(is_terminal=True, event_type="task_succeeded")],
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=1,
    )

    assert run["pose_exists"] is False
    assert run["artifact_integrity_valid"] is False


def test_real_projection_rejects_same_size_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.secure_io as secure_io
    from scripts.run_temporal_docking_acceptance import _project_real_record

    task_id = "00000000-0000-4000-8000-000000000022"
    relative = "artifacts/docking_pose.pdbqt"
    artifact = tmp_path / task_id / relative
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"safe")
    replacement = tmp_path / "replacement.pdbqt"
    replacement.write_bytes(b"evil")
    original = secure_io._bounded_read

    def replace_after_read(descriptor: int, maximum_bytes: int) -> bytes:
        content = original(descriptor, maximum_bytes)
        os.replace(replacement, artifact)
        return content

    monkeypatch.setattr(secure_io, "_bounded_read", replace_after_read)
    run = _project_real_record(
        _acceptance_record(
            task_id, relative, hashlib.sha256(b"safe").hexdigest()
        ),
        [SimpleNamespace(is_terminal=True, event_type="task_succeeded")],
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=1,
    )

    assert run["pose_exists"] is False
    assert run["artifact_integrity_valid"] is False


def test_real_runs_cancels_timeout_before_starting_next_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_temporal_docking_acceptance as acceptance
    from src.task_runtime.models import TaskStatus

    samples = tmp_path / "data" / "samples"
    samples.mkdir(parents=True)
    (samples / "MAGL_5zun.pdb").write_bytes(b"ATOM\n")
    (samples / "5.sdf").write_bytes(b"$$$$\n")
    monkeypatch.setattr(acceptance, "PROJECT_ROOT", tmp_path)
    events: list[str] = []

    class FakeRuntime:
        canceled: set[str]

        def __init__(self, **_kwargs: object) -> None:
            self.canceled = set()

        async def submit_docking(self, **_kwargs: object) -> object:
            task_id = f"task-{sum(item.startswith('submit') for item in events) + 1}"
            events.append(f"submit:{task_id}")
            return SimpleNamespace(task_id=task_id)

        async def get(self, task_id: str) -> object:
            status = TaskStatus.CANCELED if task_id in self.canceled else TaskStatus.RUNNING
            return SimpleNamespace(status=status)

        async def cancel(self, task_id: str, reason: str | None = None) -> object:
            assert reason == "acceptance_timeout"
            events.append(f"cancel:{task_id}")
            self.canceled.add(task_id)
            return SimpleNamespace(status=TaskStatus.CANCELED)

        async def close(self) -> None:
            events.append("close")

    config = SimpleNamespace(
        backend="temporal_canary", canary_percent=100, staging_root=tmp_path / "staging"
    )
    runs = asyncio.run(
        acceptance._real_runs(
            2,
            0.001,
            config=config,
            runtime_factory=FakeRuntime,
            poll_interval_seconds=0,
        )
    )

    assert events.index("cancel:task-1") < events.index("submit:task-2")
    assert all(run["status"] == "failed" for run in runs)
    assert all("acceptance_timeout" in run["warnings"] for run in runs)


def test_real_runs_stops_when_timeout_cleanup_does_not_converge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_temporal_docking_acceptance as acceptance
    from src.task_runtime.models import TaskStatus

    samples = tmp_path / "data" / "samples"
    samples.mkdir(parents=True)
    (samples / "MAGL_5zun.pdb").write_bytes(b"ATOM\n")
    (samples / "5.sdf").write_bytes(b"$$$$\n")
    monkeypatch.setattr(acceptance, "PROJECT_ROOT", tmp_path)
    submits: list[str] = []

    class FakeRuntime:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def submit_docking(self, **_kwargs: object) -> object:
            submits.append("submit")
            return SimpleNamespace(task_id="task-leftover")

        async def get(self, _task_id: str) -> object:
            return SimpleNamespace(status=TaskStatus.RUNNING)

        async def cancel(self, _task_id: str, reason: str | None = None) -> object:
            raise RuntimeError("private cleanup failure")

        async def close(self) -> None:
            return None

    config = SimpleNamespace(
        backend="temporal_canary", canary_percent=100, staging_root=tmp_path / "staging"
    )
    runs = asyncio.run(
        acceptance._real_runs(
            2,
            0.001,
            config=config,
            runtime_factory=FakeRuntime,
            poll_interval_seconds=0,
        )
    )

    assert submits == ["submit"]
    assert runs[0]["status"] == "failed"
    assert "cleanup_failed" in runs[0]["warnings"]


def test_real_runs_cancellation_converges_submitted_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_temporal_docking_acceptance as acceptance
    from src.task_runtime.models import TaskStatus

    samples = tmp_path / "data" / "samples"
    samples.mkdir(parents=True)
    (samples / "MAGL_5zun.pdb").write_bytes(b"ATOM\n")
    (samples / "5.sdf").write_bytes(b"$$$$\n")
    monkeypatch.setattr(acceptance, "PROJECT_ROOT", tmp_path)
    events: list[str] = []

    class FakeRuntime:
        canceled = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def submit_docking(self, **_kwargs: object) -> object:
            events.append("submit")
            return SimpleNamespace(task_id="task-cancelled")

        async def get(self, _task_id: str) -> object:
            if self.canceled:
                return SimpleNamespace(status=TaskStatus.CANCELED)
            raise asyncio.CancelledError

        async def cancel(self, _task_id: str, reason: str | None = None) -> object:
            assert reason == "acceptance_cancelled"
            self.canceled = True
            events.append("cancel")
            return SimpleNamespace(status=TaskStatus.CANCELED)

        async def close(self) -> None:
            events.append("close")

    config = SimpleNamespace(
        backend="temporal_canary", canary_percent=100, staging_root=tmp_path / "staging"
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            acceptance._real_runs(
                2,
                1,
                config=config,
                runtime_factory=FakeRuntime,
                poll_interval_seconds=0,
            )
        )

    assert events == ["submit", "cancel", "close"]


def test_real_cli_signal_supervisor_cancels_active_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_temporal_docking_acceptance as acceptance

    canceled: list[bool] = []
    handlers: dict[int, object] = {}

    async def active_run(_repeat: int, _timeout: float) -> list[dict[str, object]]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            canceled.append(True)
            raise

    def install(selected: int, handler: object) -> object:
        handlers[selected] = handler
        return handler

    monkeypatch.setattr(acceptance, "_real_runs", active_run)
    monkeypatch.setattr(acceptance.signal, "getsignal", lambda _selected: "previous")
    monkeypatch.setattr(acceptance.signal, "signal", install)

    async def scenario() -> list[dict[str, object]]:
        supervisor = asyncio.create_task(
            acceptance._real_runs_with_signal_cancellation(3, 2400)
        )
        while acceptance.signal.SIGTERM not in handlers:
            await asyncio.sleep(0)
        handler = handlers[acceptance.signal.SIGTERM]
        assert callable(handler)
        handler(acceptance.signal.SIGTERM, None)
        return await supervisor

    runs = asyncio.run(scenario())

    assert canceled == [True]
    assert len(runs) == 3
    assert all(run["warnings"] == ["acceptance_cancelled"] for run in runs)


def test_real_projection_rejects_conflicting_artifact_type_aliases(tmp_path):
    from scripts.run_temporal_docking_acceptance import _project_real_record, summarize_runs

    task_id = "00000000-0000-4000-8000-000000000011"
    relative = "artifacts/docking_pose.pdbqt"
    pose = tmp_path / task_id / relative
    pose.parent.mkdir(parents=True)
    pose.write_bytes(b"REMARK VINA RESULT: -7.0 0.0 0.0\n")
    digest = __import__("hashlib").sha256(pose.read_bytes()).hexdigest()
    record = SimpleNamespace(
        status=SimpleNamespace(value="succeeded"),
        backend="temporal",
        external_workflow_id=f"medchat-docking-{task_id}",
        attempt=1,
        result={"pose_count": 1, "best_energy": -7.0},
        artifacts=[
            {
                "artifact_type": "docking_pose",
                "type": "log",
                "path": relative,
                "sha256": digest,
            }
        ],
        provenance={
            "tool_name": "molecular_docking",
            "tool_version": "molecular-docking-adapter-1",
            "model_name": "AutoDock Vina",
            "demo_mode": False,
            "fallback_used": False,
        },
        warnings=[],
    )

    run = _project_real_record(
        record,
        [SimpleNamespace(is_terminal=True, event_type="task_succeeded")],
        staging_root=tmp_path,
        task_id=task_id,
        latency_ms=10,
    )
    report = summarize_runs([run], expected_backend="temporal")

    assert report["status"] == "failed"
    assert "artifact_integrity_invalid" in report["runs"][0]["failures"]


def test_metric_payload_contains_only_fixed_safe_dimensions():
    from src.task_runtime.metrics import TaskMetrics

    event = TaskMetrics().duration("task-1", "temporal", "docking", 125)

    assert event["name"] == "task_duration_ms"
    assert event["value"] == 125.0
    assert set(event["dimensions"]) == {"backend", "task_type"}
    assert "task-1" not in str(event)


def test_metric_rejects_unknown_names_dimensions_and_nonfinite_values():
    from src.task_runtime.metrics import TaskMetrics

    metrics = TaskMetrics()
    with pytest.raises(ValueError, match="metric name"):
        metrics.event("custom_metric", 1, backend="temporal", task_type="docking")
    with pytest.raises(ValueError, match="metric value"):
        metrics.event(
            "task_duration_ms",
            float("nan"),
            backend="temporal",
            task_type="docking",
        )
    with pytest.raises(ValueError, match="metric dimension"):
        metrics.event(
            "task_duration_ms",
            1,
            backend="temporal/address",
            task_type="docking",
        )
    with pytest.raises(ValueError, match="metric dimension"):
        metrics.event(
            "task_duration_ms",
            1,
            backend="temporal.999",
            task_type="docking-customer-42",
        )
    with pytest.raises(ValueError, match="metric dimension"):
        metrics.event(
            "task_duration_ms",
            1,
            backend="sk-" + "metric-secret-value",
            task_type="docking",
        )


def test_contract_cli_marks_report_as_non_scientific(tmp_path: Path):
    from scripts.run_temporal_docking_acceptance import main

    output = tmp_path / "contract.json"
    exit_code = main(
        ["--mode", "contract", "--repeat", "2", "--output", str(output)]
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["mode"] == "contract"
    assert report["scientific_execution"] is False
    assert report["run_count"] == 2
    assert report["provenance_gate"] == {
        "status": "passed",
        "evidence_type": "contract_replay",
    }
    projected = dict(report)
    digest = projected.pop("sha256")
    canonical = json.dumps(
        projected,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert digest == __import__("hashlib").sha256(canonical).hexdigest()
    assert report["contract_checks"] == {
        "cancellation_confirmed": True,
        "completion_reused": True,
        "heartbeat_observed": True,
        "probe_count": 2,
        "raw_executor_calls": 2,
        "startup_fallback_only": True,
    }


def test_real_cli_requires_opensandbox_execution_contract(tmp_path: Path, monkeypatch):
    import scripts.run_temporal_docking_acceptance as acceptance

    captured = {}
    original = acceptance.summarize_runs

    async def fake_real_runs(_repeat, _timeout_seconds):
        return [_valid_run(execution_backend=None)]

    def capture(runs, **kwargs):
        captured.update(kwargs)
        return original(runs, **kwargs)

    monkeypatch.setattr(acceptance, "_real_runs_with_signal_cancellation", fake_real_runs)
    monkeypatch.setattr(acceptance, "summarize_runs", capture)
    output = tmp_path / "real.json"

    exit_code = acceptance.main(
        ["--mode", "real", "--repeat", "1", "--output", str(output)]
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert captured["expected_execution_backend"] == "opensandbox"
    assert report["status"] == "failed"
    assert "execution_backend_mismatch" in report["runs"][0]["failures"]


def test_cli_projection_failure_writes_safe_report_without_crashing(
    tmp_path: Path,
    monkeypatch,
):
    import scripts.run_temporal_docking_acceptance as acceptance

    output = tmp_path / "projection-failure.json"
    monkeypatch.setattr(acceptance, "_contains_sensitive", lambda _value: True)

    exit_code = acceptance.main(
        ["--mode", "contract", "--repeat", "1", "--output", str(output)]
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["error"] == "report_projection_failed"
    assert report["mode"] == "contract"
    assert report["run_count"] == 0
    assert report["scientific_execution"] is False
    assert report["status"] == "failed"
    assert report["provenance_gate"]["status"] == "failed"
