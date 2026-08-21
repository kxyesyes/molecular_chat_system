from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _valid_run(**overrides):
    run = {
        "status": "succeeded",
        "backend": "temporal",
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
            )
        ]
    )
    encoded = json.dumps(report, ensure_ascii=False)

    assert "sk-contract-secret" not in encoded
    assert "AKIA" not in encoded
    assert "C:/private" not in encoded
    assert report["status"] == "failed"
    assert "sensitive_output_detected" in report["runs"][0]["failures"]


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
    assert report["contract_checks"] == {
        "cancellation_confirmed": True,
        "completion_reused": True,
        "heartbeat_observed": True,
        "probe_count": 2,
        "raw_executor_calls": 2,
        "startup_fallback_only": True,
    }


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
    assert report == {
        "error": "report_projection_failed",
        "mode": "contract",
        "run_count": 0,
        "scientific_execution": False,
        "status": "failed",
    }
