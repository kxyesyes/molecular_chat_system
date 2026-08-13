from __future__ import annotations

import json
import math
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from src.agent.contracts import (
    AgentErrorCode,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
)
from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.staging import (
    DockingInputStager,
    ManifestError,
    VerifiedDockingInputs,
)


CONFIG = {
    "center": [1, 2, 3],
    "size": [20, 20, 20],
    "exhaustiveness": 8,
    "num_modes": 10,
}
SECRET_SMILES = "CC(=O)Oc1ccccc1C(=O)O"


def _stage(
    root: Path,
    task_id: str = "task-1",
    *,
    smiles: bool = False,
) -> Path:
    return DockingInputStager(root).stage(
        task_id,
        "receptor.pdb",
        b"ATOM\n",
        None if smiles else "ligand.sdf",
        None if smiles else b"$$$$\n",
        SECRET_SMILES if smiles else None,
        CONFIG,
    )


def _executor(
    root: Path,
    calls: list[dict[str, Any]],
    *,
    energy: Any = -7.2,
    pose_count: Any = 1,
    quality: dict[str, Any] | None = None,
    delay: float = 0.0,
):
    def execute(payload: dict[str, Any], **control: Any) -> ToolResult:
        calls.append({"payload": dict(payload), "control": dict(control)})
        if delay:
            time.sleep(delay)
        pose = root / f"raw-pose-{threading.get_ident()}.pdbqt"
        pose.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return ToolResult.success_result(
            "molecular_docking",
            message="done",
            data={
                "total_poses": pose_count,
                "pose_file": str(pose),
                "best_pose": {
                    "binding_energy": energy,
                    "pose_file": str(pose),
                },
            },
            quality=quality
            or {
                "docking_inputs": {
                    "receptor_provided": True,
                    "ligand_provided": True,
                    "ligand_mode": "file",
                    "center": [1, 2, 3],
                    "size": [20, 20, 20],
                }
            },
            provenance=ToolProvenance(
                tool_name="molecular_docking",
                tool_version="vina-test-1",
            ),
        )

    return execute


def _completion(root: Path, task_id: str = "task-1") -> Path:
    return root / task_id / "completion_manifest.json"


def _run_in_process(root: str, manifest: str, results: Any) -> None:
    root_path = Path(root)

    def execute(payload: dict[str, Any], **_control: Any) -> ToolResult:
        marker = root_path / "raw-executor-calls.txt"
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, b"called\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        time.sleep(0.1)
        pose = root_path / f"process-pose-{os.getpid()}.pdbqt"
        pose.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return ToolResult.success_result(
            "molecular_docking",
            data={
                "total_poses": 1,
                "pose_file": str(pose),
                "best_pose": {"binding_energy": -7.2, "pose_file": str(pose)},
            },
            provenance=ToolProvenance(
                tool_name="molecular_docking",
                tool_version="vina-test-1",
            ),
        )

    result = DockingExecution(root_path, raw_executor=execute).run_verified(
        "task-1",
        manifest,
    )
    results.put((result["success"], result["reused_completion"]))


def test_verified_completion_is_committed_and_reused_without_second_call(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))

    first = runner.run_verified("task-1", manifest, attempt=3)
    second = runner.run_verified("task-1", manifest, attempt=4)

    assert first["success"] is True
    assert first["reused_completion"] is False
    assert second["success"] is True
    assert second["reused_completion"] is True
    assert len(calls) == 1
    completion = json.loads(_completion(tmp_path).read_text(encoding="utf-8"))
    assert completion["schema"] == "DockingCompletionManifest@1"
    assert completion["task_id"] == "task-1"
    assert completion["input_hash"]
    assert completion["config_hash"]
    assert completion["tool_version"] == "vina-test-1"
    assert completion["pose"]["path"] == "artifacts/docking_pose.pdbqt"
    assert completion["pose_count"] == 1
    assert completion["best_energy"] == -7.2
    assert completion["validator_status"] == "succeeded"
    assert completion["attempt"] == 3
    assert completion["completed_at"].endswith("Z")
    assert second["data"]["pose_file"] == "artifacts/docking_pose.pdbqt"
    assert second["artifacts"][0]["path"] == "artifacts/docking_pose.pdbqt"


def test_default_executor_uses_compat_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.docking_execution as execution_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    sentinel_tool = object()
    raw = _executor(tmp_path, calls)

    monkeypatch.setattr(execution_module, "MolecularDocking", lambda: sentinel_tool)

    def compat(tool: Any, payload: dict[str, Any], **control: Any) -> ToolResult:
        assert tool is sentinel_tool
        return raw(payload, **control)

    monkeypatch.setattr(execution_module, "execute_tool_compat", compat)

    result = DockingExecution(tmp_path).run_verified("task-1", manifest)

    assert result["success"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("energy", [math.nan, math.inf, -math.inf, True])
def test_non_finite_or_boolean_energy_never_commits(
    tmp_path: Path,
    energy: Any,
) -> None:
    manifest = _stage(tmp_path)
    runner = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, [], energy=energy),
    )

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.INVALID_OUTPUT.value
    assert not _completion(tmp_path).exists()


@pytest.mark.parametrize("pose_count", [0, True])
def test_zero_or_boolean_pose_count_never_commits(
    tmp_path: Path,
    pose_count: Any,
) -> None:
    manifest = _stage(tmp_path)
    runner = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, [], pose_count=pose_count),
    )

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("best_energy", math.nan),
        ("best_energy", True),
        ("pose_count", 0),
        ("pose_count", True),
        ("validator_status", "failed"),
    ],
)
def test_tampered_scientific_completion_fields_are_rejected(
    tmp_path: Path,
    key: str,
    value: Any,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    assert runner.run_verified("task-1", manifest)["success"] is True
    completion_path = _completion(tmp_path)
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload[key] = value
    completion_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert len(calls) == 1


def test_validator_failure_never_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.docking_execution as execution_module

    manifest = _stage(tmp_path)

    def reject(_self: Any, result: ToolResult) -> ToolResult:
        return ToolResult.error_result(
            result.tool_name,
            AgentErrorCode.INVALID_OUTPUT,
            "Docking evidence failed scientific validation",
        )

    monkeypatch.setattr(
        execution_module.AgentResultValidator,
        "validate_tool_result",
        reject,
    )
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, []))

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert "docking evidence" in result["message"].lower()
    assert not _completion(tmp_path).exists()


@pytest.mark.parametrize("identity_key", ["input_hash", "config_hash"])
def test_completion_identity_mismatch_is_not_reused(
    tmp_path: Path,
    identity_key: str,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    assert runner.run_verified("task-1", manifest)["success"] is True
    completion_path = _completion(tmp_path)
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload[identity_key] = "f" * 64
    completion_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_missing_or_changed_pose_is_not_reused(
    tmp_path: Path,
    mutation: str,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    assert runner.run_verified("task-1", manifest)["success"] is True
    pose = tmp_path / "task-1" / "artifacts" / "docking_pose.pdbqt"
    if mutation == "missing":
        pose.unlink()
    else:
        pose.write_text("TAMPER", encoding="utf-8")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.pdbqt", "C:/escape.pdbqt", "/escape.pdbqt"],
)
def test_completion_rejects_pose_path_escape(tmp_path: Path, bad_path: str) -> None:
    manifest = _stage(tmp_path)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, []))
    assert runner.run_verified("task-1", manifest)["success"] is True
    completion_path = _completion(tmp_path)
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload["pose"]["path"] = bad_path
    completion_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False


def test_completion_rejects_symlink_or_hardlink_pose(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, []))
    assert runner.run_verified("task-1", manifest)["success"] is True
    pose = tmp_path / "task-1" / "artifacts" / "docking_pose.pdbqt"
    original = tmp_path / "original-pose.pdbqt"
    pose.replace(original)
    try:
        os.link(original, pose)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {type(exc).__name__}")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False


@pytest.mark.parametrize(
    "content",
    [b"{broken", b" " * (64 * 1024 + 1)],
    ids=["malformed", "oversized"],
)
def test_malformed_or_oversized_completion_is_rejected(
    tmp_path: Path,
    content: bytes,
) -> None:
    manifest = _stage(tmp_path)
    completion = _completion(tmp_path)
    completion.write_bytes(content)
    calls: list[dict[str, Any]] = []

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert calls == []


def test_concurrent_same_task_executes_once(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls, delay=0.15),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: runner.run_verified("task-1", manifest),
                range(2),
            )
        )

    assert all(result["success"] for result in results)
    assert sorted(result["reused_completion"] for result in results) == [False, True]
    assert len(calls) == 1


def test_cross_process_same_task_executes_once(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    workers = [
        context.Process(
            target=_run_in_process,
            args=(str(tmp_path), str(manifest), results),
        )
        for _ in range(2)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
            pytest.fail("docking execution worker did not exit")
        assert worker.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in workers]
    assert sorted(outcomes) == [(True, False), (True, True)]
    marker = tmp_path / "raw-executor-calls.txt"
    assert marker.read_text(encoding="utf-8").splitlines() == ["called"]


@pytest.mark.parametrize("mode", ["exception", "cancelled", "failure"])
def test_exception_or_cancellation_writes_no_completion(
    tmp_path: Path,
    mode: str,
) -> None:
    manifest = _stage(tmp_path)

    def executor(_payload: dict[str, Any], **_control: Any) -> ToolResult:
        if mode == "exception":
            raise RuntimeError("secret failure detail")
        if mode == "cancelled":
            return ToolResult.error_result(
                "molecular_docking",
                AgentErrorCode.CANCELLED,
                "Docking cancelled",
                status=ObservationStatus.CANCELLED,
            )
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "secret machine path and input",
        )

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
    )

    assert result["success"] is False
    assert not _completion(tmp_path).exists()
    assert "secret" not in json.dumps(result)


def test_input_snapshot_mutation_after_execution_is_rejected(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)

    def executor(payload: dict[str, Any], **_control: Any) -> ToolResult:
        calls: list[dict[str, Any]] = []
        result = _executor(tmp_path, calls)(payload)
        Path(payload["receptor_path"]).write_text("TAMPER", encoding="utf-8")
        return result

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
    )

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


def test_second_input_integrity_failure_after_tool_never_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    executed = {"value": False}
    real_verify = VerifiedDockingInputs.verify_integrity

    def verify(inputs: VerifiedDockingInputs) -> bool:
        if executed["value"]:
            raise ManifestError("manifest_integrity_failed")
        return real_verify(inputs)

    def executor(payload: dict[str, Any], **_control: Any) -> ToolResult:
        result = _executor(tmp_path, [])(payload)
        executed["value"] = True
        return result

    monkeypatch.setattr(VerifiedDockingInputs, "verify_integrity", verify)

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
    )

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


def test_smiles_and_absolute_paths_do_not_enter_completion_or_result(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path, task_id="task-smiles", smiles=True)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))

    result = runner.run_verified("task-smiles", manifest)

    serialized_manifest = _completion(tmp_path, "task-smiles").read_text(
        encoding="utf-8"
    )
    serialized_result = json.dumps(result)
    assert calls[0]["payload"]["smiles"] == SECRET_SMILES
    assert "ligand_path" not in calls[0]["payload"]
    assert SECRET_SMILES not in serialized_manifest
    assert SECRET_SMILES not in serialized_result
    assert str(tmp_path.resolve()) not in serialized_manifest
    assert str(tmp_path.resolve()) not in serialized_result
    assert result["provenance"]["input_digest"]


def test_verified_identity_object_is_accepted(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, []))
    with DockingInputStager(tmp_path).execution_snapshot("task-1", manifest) as inputs:
        result = runner.run_verified("task-1", inputs)

    assert result["success"] is True


def test_atomic_write_failure_leaves_no_trusted_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)

    def fail_replace(_source: Any, _target: Any) -> None:
        raise OSError("replace failed")

    real_replace = completion_module._replace_file

    def fail_completion_replace(source: Any, target: Any) -> None:
        if Path(target).name == "completion_manifest.json":
            fail_replace(source, target)
        real_replace(source, target)

    monkeypatch.setattr(completion_module, "_replace_file", fail_completion_replace)
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, []),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()
    assert not list((tmp_path / "task-1").glob(".completion-*.tmp"))
