from __future__ import annotations

import json
import math
import multiprocessing
import os
import re
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
    WorkflowArtifact,
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
_DEFAULT_PROVENANCE = object()


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
    provenance: ToolProvenance | None | object = _DEFAULT_PROVENANCE,
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
            if quality is not None
            else {
                "real_execution": True,
                "docking_inputs": {
                    "receptor_provided": True,
                    "ligand_provided": True,
                    "ligand_mode": "file",
                    "center": [1, 2, 3],
                    "size": [20, 20, 20],
                }
            },
            provenance=(
                ToolProvenance(
                    tool_name="molecular_docking",
                    tool_version="vina-test-1",
                )
                if provenance is _DEFAULT_PROVENANCE
                else provenance
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
            quality={"real_execution": True},
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


@pytest.mark.parametrize(
    "provenance",
    [
        ToolProvenance(
            tool_name="molecular_docking",
            tool_version="vina-test-1",
            demo_mode=True,
        ),
        ToolProvenance(
            tool_name="molecular_docking",
            tool_version="vina-test-1",
            fallback_used=True,
        ),
    ],
    ids=["demo", "fallback"],
)
def test_demo_or_fallback_provenance_is_rejected_without_forgery(
    tmp_path: Path,
    provenance: ToolProvenance,
) -> None:
    manifest = _stage(tmp_path)

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, [], provenance=provenance),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["provenance"] is None
    assert not _completion(tmp_path).exists()


@pytest.mark.parametrize(
    ("provenance", "quality"),
    [
        (None, {"real_execution": True}),
        (
            ToolProvenance(
                tool_name="molecular_docking",
                tool_version="vina-test-1",
            ),
            {},
        ),
        (
            ToolProvenance(
                tool_name="molecular_docking",
                tool_version="vina-test-1",
            ),
            {"real_execution": False},
        ),
    ],
    ids=["missing-provenance", "missing-real-execution", "false-real-execution"],
)
def test_scientific_authority_requires_real_provenance_and_execution(
    tmp_path: Path,
    provenance: ToolProvenance | None,
    quality: dict[str, Any],
) -> None:
    manifest = _stage(tmp_path)

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(
            tmp_path,
            [],
            provenance=provenance,
            quality=quality,
        ),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


def test_tool_and_model_provenance_survives_completion_reuse(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    provenance = ToolProvenance(
        tool_name="molecular_docking",
        tool_version="vina-1.2.3",
        model_name="vina-scoring",
        model_version="2026.1",
        demo_mode=False,
        fallback_used=False,
    )
    runner = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, [], provenance=provenance),
    )

    first = runner.run_verified("task-1", manifest)
    reused = runner.run_verified("task-1", manifest)

    assert first["success"] is True
    assert reused["success"] is True
    for result in (first, reused):
        assert result["provenance"]["tool_version"] == "vina-1.2.3"
        assert result["provenance"]["model_name"] == "vina-scoring"
        assert result["provenance"]["model_version"] == "2026.1"
        assert result["provenance"]["demo_mode"] is False
        assert result["provenance"]["fallback_used"] is False


@pytest.mark.parametrize("sensitive_identifier", ["CCO", "sk-12345678"])
def test_sensitive_provenance_identifier_is_rejected(
    tmp_path: Path,
    sensitive_identifier: str,
) -> None:
    manifest = DockingInputStager(tmp_path).stage(
        "task-sensitive",
        "receptor.pdb",
        b"ATOM\n",
        None,
        None,
        "CCO",
        CONFIG,
    )
    provenance = ToolProvenance(
        tool_name="molecular_docking",
        tool_version="vina-test-1",
        model_name=sensitive_identifier,
        demo_mode=False,
        fallback_used=False,
    )

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, [], provenance=provenance),
    ).run_verified("task-sensitive", manifest)

    assert result["success"] is False
    assert sensitive_identifier not in json.dumps(result)
    assert not _completion(tmp_path, "task-sensitive").exists()


def test_reuse_rejects_manifest_provenance_matching_staged_smiles(
    tmp_path: Path,
) -> None:
    manifest = DockingInputStager(tmp_path).stage(
        "task-sensitive",
        "receptor.pdb",
        b"ATOM\n",
        None,
        None,
        "CCO",
        CONFIG,
    )
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    assert runner.run_verified("task-sensitive", manifest)["success"] is True
    completion_path = _completion(tmp_path, "task-sensitive")
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload["model_name"] = "CCO"
    completion_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.run_verified("task-sensitive", manifest)

    assert result["success"] is False
    assert "CCO" not in json.dumps(result)
    assert len(calls) == 1


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


def test_default_executor_does_not_invent_missing_compat_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.docking_execution as execution_module

    manifest = _stage(tmp_path)
    raw = _executor(tmp_path, [], provenance=None)

    monkeypatch.setattr(
        execution_module,
        "execute_tool_compat",
        lambda _tool, payload, **control: raw(payload, **control),
    )

    result = DockingExecution(tmp_path).run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["provenance"] is None
    assert not _completion(tmp_path).exists()


def test_injected_executor_missing_provenance_fails_closed(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, [], provenance=None),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["provenance"] is None
    assert not _completion(tmp_path).exists()


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


def test_concurrent_call_waits_for_prepared_completion_in_lease_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    prepared = threading.Event()
    allow_finalize = threading.Event()
    real_finalize = runner._finalize_prepared

    def gated_finalize(*args: Any, **kwargs: Any) -> dict[str, Any]:
        prepared.set()
        assert allow_finalize.wait(10)
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(runner, "_finalize_prepared", gated_finalize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(runner.run_verified, "task-1", manifest)
        assert prepared.wait(10)
        second_future = pool.submit(runner.run_verified, "task-1", manifest)
        time.sleep(0.1)
        allow_finalize.set()
        first = first_future.result(timeout=10)
        second = second_future.result(timeout=10)

    assert first["success"] is True
    assert second["success"] is True
    assert sorted([first["reused_completion"], second["reused_completion"]]) == [
        False,
        True,
    ]
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


def test_non_tool_result_fails_without_completion(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)

    def executor(_payload: dict[str, Any], **_control: Any) -> Any:
        return {"success": True, "binding_energy": -99.0}

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
    )

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.INVALID_OUTPUT.value
    assert result["provenance"] is None
    assert not _completion(tmp_path).exists()


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


def test_final_snapshot_exit_integrity_failure_revokes_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_verify = VerifiedDockingInputs.verify_integrity
    verifications = {"count": 0}

    def fail_fifth(inputs: VerifiedDockingInputs) -> bool:
        verifications["count"] += 1
        if verifications["count"] == 5:
            raise ManifestError("manifest_integrity_failed")
        return real_verify(inputs)

    monkeypatch.setattr(VerifiedDockingInputs, "verify_integrity", fail_fifth)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))

    result = runner.run_verified("task-1", manifest)

    assert verifications["count"] == 5
    assert result["success"] is False
    assert not _completion(tmp_path).exists()

    monkeypatch.undo()
    retried = runner.run_verified("task-1", manifest)
    assert retried["success"] is True
    assert retried["reused_completion"] is False
    assert len(calls) == 2


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


def test_safe_validated_metadata_survives_first_result_and_reuse(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path, task_id="task-smiles", smiles=True)
    calls: list[dict[str, Any]] = []
    safe_extra = tmp_path / "task-smiles" / "reports" / "summary.json"
    safe_extra.parent.mkdir()
    safe_extra.write_text('{"status":"verified"}', encoding="utf-8")
    outside = tmp_path / "outside.log"
    outside.write_text("outside", encoding="utf-8")

    def executor(payload: dict[str, Any], **control: Any) -> ToolResult:
        calls.append({"payload": dict(payload), "control": dict(control)})
        pose = tmp_path / "metadata-pose.pdbqt"
        pose.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return ToolResult.success_result(
            "molecular_docking",
            message="raw message is not persisted",
            data={
                "total_poses": 1,
                "pose_file": str(pose),
                "best_pose": {"binding_energy": -7.2, "pose_file": str(pose)},
            },
            formatted=(
                f"Binding energy -7.2 kcal/mol; source {tmp_path}; "
                f"ligand {SECRET_SMILES}"
            ),
            elapsed_ms=321,
            warnings=[
                "Vina convergence warning",
                f"unsafe path {tmp_path}",
                f"unsafe ligand {SECRET_SMILES}",
            ],
            evidence=[
                {
                    "source": "vina",
                    "description": "Parsed real Vina output",
                    "path": str(tmp_path),
                    "smiles": SECRET_SMILES,
                }
            ],
            artifacts=[
                WorkflowArtifact(
                    artifact_type="docking_report",
                    path=str(safe_extra),
                    label="Docking summary",
                    mime_type="application/json",
                    metadata={"kind": "summary", "unsafe_path": str(tmp_path)},
                ),
                WorkflowArtifact(
                    artifact_type="debug_log",
                    path=str(outside),
                    label="Outside log",
                    mime_type="text/plain",
                ),
            ],
            quality={
                "real_execution": True,
                "engine": "AutoDock Vina",
                "execution_status": "completed",
            },
            provenance=ToolProvenance(
                tool_name="molecular_docking",
                tool_version="vina-1.2.3",
                model_name="vina-scoring",
                model_version="2026.1",
                demo_mode=False,
                fallback_used=False,
            ),
        )

    runner = DockingExecution(tmp_path, raw_executor=executor)
    first = runner.run_verified("task-smiles", manifest)
    reused = runner.run_verified("task-smiles", manifest)

    assert len(calls) == 1
    for result in (first, reused):
        assert result["success"] is True
        assert result["elapsed_ms"] == 321
        assert "Binding energy -7.2 kcal/mol" in result["formatted"]
        assert "Vina convergence warning" in result["warnings"]
        assert "additional_artifact_omitted" in result["warnings"]
        assert result["evidence"] == [
            {"source": "vina", "description": "Parsed real Vina output"}
        ]
        assert result["quality"]["engine"] == "AutoDock Vina"
        assert result["quality"]["execution_status"] == "completed"
        assert result["provenance"]["model_name"] == "vina-scoring"
        assert result["artifacts"][0]["path"] == "artifacts/docking_pose.pdbqt"
        assert re.fullmatch(
            r"artifacts/extra-01-[0-9a-f]{12}\.json",
            result["artifacts"][1]["path"],
        )
        assert result["artifacts"][1]["artifact_type"] == "generic_file"
        assert result["artifacts"][1]["label"] == "Verified additional artifact"
        assert result["artifacts"][1]["metadata"] == {}
        serialized = json.dumps(result)
        assert SECRET_SMILES not in serialized
        assert str(tmp_path.resolve()) not in serialized

    serialized_completion = _completion(tmp_path, "task-smiles").read_text(
        encoding="utf-8"
    )
    assert SECRET_SMILES not in serialized_completion
    assert str(tmp_path.resolve()) not in serialized_completion


def test_credential_shaped_metadata_is_redacted_before_completion(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    credential = "sk-12345678"

    def executor(payload: dict[str, Any], **_control: Any) -> ToolResult:
        result = _executor(tmp_path, [])(payload)
        result.formatted = f"Vina output using {credential}"
        result.warnings = [f"provider warning {credential}"]
        result.evidence = [
            {"source": "vina", "description": f"credential {credential}"}
        ]
        return result

    runner = DockingExecution(tmp_path, raw_executor=executor)
    first = runner.run_verified("task-1", manifest)
    reused = runner.run_verified("task-1", manifest)

    for value in (first, reused):
        assert value["success"] is True
        assert credential not in json.dumps(value)
    assert credential not in _completion(tmp_path).read_text(encoding="utf-8")


def test_extra_artifact_and_unc_metadata_use_fixed_safe_publication(
    tmp_path: Path,
) -> None:
    manifest = DockingInputStager(tmp_path).stage(
        "task-unc",
        "receptor.pdb",
        b"ATOM\n",
        None,
        None,
        "CCO",
        CONFIG,
    )
    source = tmp_path / "task-unc" / "reports" / "sk-12345678-CCO.log"
    source.parent.mkdir()
    source.write_text("verified report", encoding="utf-8")
    unc = r"\\server\share\secret\CCO.txt"
    extended = r"\\?\UNC\server\share\sk-12345678\CCO.log"
    device = r"\\.\PhysicalDrive0"

    def executor(payload: dict[str, Any], **_control: Any) -> ToolResult:
        result = _executor(tmp_path, [])(payload)
        result.formatted = f"paths {unc} {extended} {device}"
        result.warnings = [f"unsafe UNC {unc}"]
        result.evidence = [
            {"source": "vina", "description": f"extended {extended}"}
        ]
        result.quality["engine"] = f"AutoDock Vina at {device}"
        result.artifacts = [
            WorkflowArtifact(
                artifact_type="sk-12345678-CCO",
                path=str(source),
                label="sk-12345678 CCO report",
                mime_type="text/plain",
                metadata={"kind": "sk-12345678-CCO"},
            )
        ]
        return result

    runner = DockingExecution(tmp_path, raw_executor=executor)
    first = runner.run_verified("task-unc", manifest)
    reused = runner.run_verified("task-unc", manifest)

    assert first["success"] is True
    assert reused["success"] is True
    assert first["artifacts"][1]["path"] == reused["artifacts"][1]["path"]
    extra_path = first["artifacts"][1]["path"]
    assert re.fullmatch(r"artifacts/extra-01-[0-9a-f]{12}\.log", extra_path)
    for result in (first, reused):
        extra = result["artifacts"][1]
        assert extra["artifact_type"] == "generic_file"
        assert extra["label"] == "Verified additional artifact"
        serialized = json.dumps(result)
        for secret in ("sk-12345678", "CCO", "server", "share", "PhysicalDrive0"):
            assert secret not in serialized

    completion_text = _completion(tmp_path, "task-unc").read_text(encoding="utf-8")
    for secret in (
        "reports",
        "sk-12345678",
        "CCO",
        "server",
        "share",
        "PhysicalDrive0",
    ):
        assert secret not in completion_text


def test_completion_rejects_excessive_cached_metadata(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    assert runner.run_verified("task-1", manifest)["success"] is True
    completion_path = _completion(tmp_path)
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload["result_metadata"]["warnings"] = ["warning"] * 33
    completion_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert len(calls) == 1


def test_aggregate_cached_metadata_over_limit_leaves_no_manifest(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)

    def executor(payload: dict[str, Any], **_control: Any) -> ToolResult:
        result = _executor(tmp_path, [])(payload)
        result.formatted = "f" * 2048
        result.warnings = [f"warning-{index}-" + "w" * 235 for index in range(32)]
        result.evidence = [
            {
                "source": f"source-{index}-" + "s" * 230,
                "description": "d" * 250,
                "method": "m" * 250,
                "version": "v" * 250,
                "identifier": "i" * 250,
                "type": "t" * 250,
                "label": "l" * 250,
                "sha256": "a" * 64,
            }
            for index in range(32)
        ]
        return result

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
    )

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


def test_verified_identity_object_is_rejected(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    with DockingInputStager(tmp_path).execution_snapshot("task-1", manifest) as inputs:
        result = runner.run_verified("task-1", inputs)

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.INVALID_INPUT.value
    assert calls == []


def test_same_verified_identity_object_concurrently_executes_zero_tools(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls, delay=0.1),
    )
    with DockingInputStager(tmp_path).execution_snapshot("task-1", manifest) as inputs:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: runner.run_verified("task-1", inputs),
                    range(2),
                )
            )

    assert all(result["success"] is False for result in results)
    assert calls == []


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


def test_post_publish_does_not_call_load_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    real_load = completion_module.DockingCompletionStore.load_verified
    post_publish_calls: list[str] = []

    def fail_after_publish(
        store: Any,
        task_id: str,
        *,
        input_hash: str,
        config_hash: str,
    ) -> Any:
        if store.manifest_path(task_id).exists():
            post_publish_calls.append(task_id)
            raise completion_module.CompletionError("completion_artifact_invalid")
        return real_load(
            store,
            task_id,
            input_hash=input_hash,
            config_hash=config_hash,
        )

    monkeypatch.setattr(
        completion_module.DockingCompletionStore,
        "load_verified",
        fail_after_publish,
    )
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, []),
    ).run_verified("task-1", manifest)

    assert result["success"] is True
    assert post_publish_calls == []
    assert _completion(tmp_path).exists()
    assert (tmp_path / "task-1" / "artifacts" / "docking_pose.pdbqt").exists()


def test_replace_success_then_exception_recovers_exact_trusted_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_replace = completion_module._replace_file

    def replace_then_raise(source: Any, target: Any) -> None:
        real_replace(source, target)
        if Path(target).name == "completion_manifest.json":
            raise OSError("ambiguous replace result")

    monkeypatch.setattr(completion_module, "_replace_file", replace_then_raise)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)
    monkeypatch.setattr(completion_module, "_replace_file", real_replace)
    reused = runner.run_verified("task-1", manifest)

    assert first["success"] is True
    assert "completion_directory_sync_unconfirmed" in first["warnings"]
    assert reused["success"] is True
    assert reused["reused_completion"] is True
    assert len(calls) == 1


def test_directory_fsync_failure_after_replace_is_success_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    real_fsync_directory = completion_module._fsync_directory

    def fail_post_commit_directory_sync(path: Path) -> None:
        if path.name == "task-1" and _completion(tmp_path).exists():
            raise OSError("directory sync failed")
        real_fsync_directory(path)

    monkeypatch.setattr(
        completion_module,
        "_fsync_directory",
        fail_post_commit_directory_sync,
    )
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, []),
    ).run_verified("task-1", manifest)

    assert result["success"] is True
    assert "completion_directory_sync_unconfirmed" in result["warnings"]
    assert _completion(tmp_path).exists()


def test_trusted_result_is_built_before_manifest_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.docking_execution as execution_module

    manifest = _stage(tmp_path)
    real_trusted_result = execution_module._trusted_result
    publication_states: list[bool] = []

    def fail_if_already_published(*args: Any, **kwargs: Any) -> dict[str, Any]:
        published = _completion(tmp_path).exists()
        publication_states.append(published)
        if published:
            raise RuntimeError("trusted response built after commit")
        return real_trusted_result(*args, **kwargs)

    monkeypatch.setattr(execution_module, "_trusted_result", fail_if_already_published)
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, []),
    ).run_verified("task-1", manifest)

    assert result["success"] is True
    assert publication_states and not any(publication_states)
    assert _completion(tmp_path).exists()


def test_trusted_result_preconstruction_failure_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.docking_execution as execution_module

    manifest = _stage(tmp_path)

    def fail_response_construction(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("response serialization failed")

    monkeypatch.setattr(
        execution_module,
        "_trusted_result",
        fail_response_construction,
    )
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, []),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()
    assert not (tmp_path / "task-1" / "artifacts" / "docking_pose.pdbqt").exists()


def test_lease_release_failure_after_commit_returns_verified_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.staging as staging_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_release = staging_module._TaskLease.release

    def release_then_raise(lease: Any) -> None:
        real_release(lease)
        if _completion(tmp_path).exists():
            raise OSError("lease release status unavailable")

    monkeypatch.setattr(staging_module._TaskLease, "release", release_then_raise)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)
    monkeypatch.setattr(staging_module._TaskLease, "release", real_release)
    reused = runner.run_verified("task-1", manifest)

    assert first["success"] is True
    assert "completion_lease_release_unconfirmed" in first["warnings"]
    assert reused["success"] is True
    assert reused["reused_completion"] is True
    assert len(calls) == 1


def test_lease_release_failure_before_commit_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.staging as staging_module

    manifest = _stage(tmp_path)
    real_release = staging_module._TaskLease.release

    def release_then_raise(lease: Any) -> None:
        real_release(lease)
        raise OSError("lease release failed before commit")

    monkeypatch.setattr(staging_module._TaskLease, "release", release_then_raise)
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, []),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()
    assert not (tmp_path / "task-1" / "artifacts" / "docking_pose.pdbqt").exists()


def test_unverifiable_commit_persists_tombstone_when_quarantine_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module
    import src.task_runtime.staging as staging_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_release = staging_module._TaskLease.release
    real_load = completion_module.DockingCompletionStore.load_verified

    def release_then_raise(lease: Any) -> None:
        real_release(lease)
        if _completion(tmp_path).exists():
            raise OSError("lease release status unavailable")

    def fail_published_load(
        store: Any,
        task_id: str,
        *,
        input_hash: str,
        config_hash: str,
    ) -> Any:
        if store.manifest_path(task_id).exists():
            raise completion_module.CompletionError("completion_artifact_invalid")
        return real_load(
            store,
            task_id,
            input_hash=input_hash,
            config_hash=config_hash,
        )

    monkeypatch.setattr(staging_module._TaskLease, "release", release_then_raise)
    monkeypatch.setattr(
        completion_module.DockingCompletionStore,
        "load_verified",
        fail_published_load,
    )
    monkeypatch.setattr(
        completion_module,
        "_isolate_untrusted_manifest",
        lambda _path, _parent: False,
    )
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)

    monkeypatch.setattr(staging_module._TaskLease, "release", real_release)
    monkeypatch.setattr(
        completion_module.DockingCompletionStore,
        "load_verified",
        real_load,
    )
    second = runner.run_verified("task-1", manifest)

    assert first["success"] is False
    assert first["error"]["details"]["reason"] == "ownership_uncertain"
    assert (tmp_path / "task-1" / "completion_invalid.marker").exists()
    assert second["success"] is False
    assert second["reused_completion"] is False
    assert len(calls) == 1


def test_confirmed_commit_remains_success_when_all_post_commit_storage_checks_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module
    import src.task_runtime.staging as staging_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_release = staging_module._TaskLease.release
    real_load = completion_module.DockingCompletionStore.load_verified

    def release_then_raise(lease: Any) -> None:
        real_release(lease)
        if _completion(tmp_path).exists():
            raise OSError("lease release status unavailable")

    def fail_published_load(
        store: Any,
        task_id: str,
        *,
        input_hash: str,
        config_hash: str,
    ) -> Any:
        if store.manifest_path(task_id).exists():
            raise completion_module.CompletionError("completion_io_error")
        return real_load(
            store,
            task_id,
            input_hash=input_hash,
            config_hash=config_hash,
        )

    monkeypatch.setattr(staging_module._TaskLease, "release", release_then_raise)
    monkeypatch.setattr(
        completion_module.DockingCompletionStore,
        "load_verified",
        fail_published_load,
    )
    monkeypatch.setattr(
        completion_module,
        "_persist_invalidation_marker",
        lambda _path, _parent: False,
    )
    monkeypatch.setattr(
        completion_module,
        "_isolate_untrusted_manifest",
        lambda _path, _parent: False,
    )
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)

    monkeypatch.setattr(staging_module._TaskLease, "release", real_release)
    monkeypatch.setattr(
        completion_module.DockingCompletionStore,
        "load_verified",
        real_load,
    )
    reused = runner.run_verified("task-1", manifest)

    assert first["success"] is True
    assert first["reused_completion"] is False
    assert "completion_post_commit_verification_unavailable" in first["warnings"]
    assert "completion_lease_release_unconfirmed" in first["warnings"]
    assert first["quality"]["completion_durability"] == "uncertain"
    assert reused["success"] is True
    assert reused["reused_completion"] is True
    assert len(calls) == 1
