from __future__ import annotations

import json
import hashlib
import inspect
import math
import multiprocessing
import os
import re
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import httpx

from src.agent.contracts import (
    AgentErrorCode,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
from src.task_runtime.docking_execution import DockingExecution as _DockingExecution
from src.task_runtime.completion import CompletionError
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


def _vina_pdbqt(*energies: float) -> str:
    return "".join(
        f"MODEL {index}\n"
        f"REMARK VINA RESULT: {energy:.3f} 0.000 0.000\n"
        "ROOT\n"
        "ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
        "ENDROOT\nTORSDOF 0\nENDMDL\n"
        for index, energy in enumerate(energies, start=1)
    )


def _allowed_output_root(root: Path) -> Path:
    return root / "allowed-docking-output"


def _task_output_dir(root: Path, task_id: str = "task-1") -> Path:
    output = _allowed_output_root(root) / f"docking_{task_id}"
    output.mkdir(parents=True, exist_ok=True)
    return output


def DockingExecution(
    root: Path,
    raw_executor=None,
    *,
    allowed_output_root: Path | None = None,
) -> _DockingExecution:
    """Construct a test runner with an explicit fake-tool output authority."""

    if raw_executor is not None and allowed_output_root is None:
        allowed_output_root = _allowed_output_root(root)
    return _DockingExecution(
        root,
        raw_executor=raw_executor,
        allowed_output_root=allowed_output_root,
    )


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
        task_id = str(
            control.get("job_id")
            or Path(str(payload["receptor_path"])).parents[1].name
        )
        pose = _task_output_dir(root, task_id) / "result.pdbqt"
        pose.write_text(_vina_pdbqt(float(energy)), encoding="utf-8")
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


def _pose_result(path: Path, *, energy: Any = -7.2, pose_count: Any = 1) -> ToolResult:
    return ToolResult.success_result(
        "molecular_docking",
        data={
            "total_poses": pose_count,
            "pose_file": str(path),
            "best_pose": {"binding_energy": energy, "pose_file": str(path)},
        },
        quality={"real_execution": True},
        provenance=ToolProvenance(
            tool_name="molecular_docking",
            tool_version="vina-test-1",
            model_name="AutoDock Vina",
        ),
    )


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
        pose = _task_output_dir(root_path) / "result.pdbqt"
        pose.write_text(_vina_pdbqt(-7.2), encoding="utf-8")
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


def test_injected_executor_requires_explicit_output_authority(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allowed output root"):
        _DockingExecution(tmp_path, raw_executor=lambda _payload, **_control: None)


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
    assert result["error"]["details"]["reason"] == "provenance_rejected"
    assert not _completion(tmp_path).exists()


@pytest.mark.parametrize(
    ("content", "energy", "pose_count"),
    [
        ("ordinary text\n", -7.2, 1),
        ("MODEL 1\nROOT\nENDROOT\nTORSDOF 0\nENDMDL\n", -7.2, 1),
        (_vina_pdbqt(float("nan")), -7.2, 1),
        (_vina_pdbqt(float("inf")), -7.2, 1),
        (_vina_pdbqt(-7.2, -6.8), -7.2, 1),
        (_vina_pdbqt(-7.2), -6.9, 1),
    ],
    ids=[
        "ordinary-text",
        "missing-vina-result",
        "nan-energy",
        "infinite-energy",
        "pose-count-mismatch",
        "best-energy-mismatch",
    ],
)
def test_pose_pdbqt_must_match_reported_scientific_values(
    tmp_path: Path,
    content: str,
    energy: float,
    pose_count: int,
) -> None:
    manifest = _stage(tmp_path)
    pose = _task_output_dir(tmp_path) / "result.pdbqt"
    pose.write_text(content, encoding="utf-8")

    result = DockingExecution(
        tmp_path,
        raw_executor=lambda _payload, **_control: _pose_result(
            pose,
            energy=energy,
            pose_count=pose_count,
        ),
        allowed_output_root=_allowed_output_root(tmp_path),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


def test_large_pose_io_is_streamed_with_bounded_reads_and_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    pose = _task_output_dir(tmp_path) / "result.pdbqt"
    real_read = completion_module.os.read
    read_sizes: list[int] = []

    def observed_read(descriptor: int, size: int) -> bytes:
        read_sizes.append(size)
        assert 0 < size <= 1024 * 1024
        return real_read(descriptor, size)

    def executor(_payload: dict[str, Any], **_control: Any) -> ToolResult:
        with pose.open("wb") as stream:
            stream.write(
                b"MODEL 1\nREMARK VINA RESULT: -7.200 0.000 0.000\n"
                b"ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
            )
            filler = b"REMARK bounded streaming padding 0123456789abcdef\n"
            for _ in range(120_000):
                stream.write(filler)
            stream.write(b"ENDMDL\n")
        return _pose_result(pose)

    monkeypatch.setattr(completion_module.os, "read", observed_read)
    tracemalloc.start()
    try:
        result = DockingExecution(
            tmp_path,
            raw_executor=executor,
        ).run_verified("task-1", manifest)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result["success"] is True
    assert read_sizes
    assert peak < 12 * 1024 * 1024
    source = inspect.getsource(completion_module._secure_read_regular)
    assert "chunks" not in source
    assert ".join(" not in source


def test_newline_dense_pose_parser_uses_partial_tail_not_front_deletion() -> None:
    import src.task_runtime.docking_execution as docking_module

    source = inspect.getsource(docking_module._VinaPoseStreamValidator.feed)
    assert "del self._pending" not in source
    assert "self._pending.extend" not in source

    prefix = (
        b"MODEL 1\r\n"
        b"REMARK VINA RESULT: -7.200 0.000 0.000\r\n"
        b"ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\r\n"
    )
    dense_line = b"REMARK dense newline payload\r\n"
    suffix = b"ENDMDL\r\n"
    repeat = (8 * 1024 * 1024 - len(prefix) - len(suffix)) // len(dense_line)
    content = prefix + dense_line * repeat + suffix
    parser = docking_module._VinaPoseStreamValidator(
        expected_pose_count=1,
        expected_best_energy=-7.2,
    )

    started = time.perf_counter()
    tracemalloc.start()
    try:
        for offset in range(0, len(content), 1024 * 1024 - 17):
            parser.feed(content[offset : offset + 1024 * 1024 - 17])
            assert len(parser._pending) <= docking_module._MAX_PDBQT_LINE_BYTES
        parser.finish()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert time.perf_counter() - started < 10.0
    assert peak < 4 * 1024 * 1024


def test_pose_parser_enforces_cross_chunk_line_limit_and_crlf() -> None:
    import src.task_runtime.docking_execution as docking_module

    parser = docking_module._VinaPoseStreamValidator(
        expected_pose_count=1,
        expected_best_energy=-7.2,
    )
    valid = (
        b"MODEL 1\r\n"
        b"REMARK VINA RESULT: -7.200 0.000 0.000\r\n"
        b"ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\r\n"
        + b"R" * (64 * 1024 - 1)
        + b"\r\nENDMDL\r\n"
    )
    for offset in range(0, len(valid), 8191):
        parser.feed(valid[offset : offset + 8191])
    parser.finish()

    oversized = docking_module._VinaPoseStreamValidator(
        expected_pose_count=1,
        expected_best_energy=-7.2,
    )
    oversized.feed(b"MODEL 1\n" + b"X" * (32 * 1024))
    with pytest.raises(
        docking_module.CompletionError,
        match="completion_artifact_invalid",
    ):
        oversized.feed(b"X" * (32 * 1024 + 1))


def test_pose_and_extra_artifact_validation_never_collects_large_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    report = tmp_path / "task-1" / "reports" / "large-report.bin"
    report.parent.mkdir()
    with report.open("wb") as stream:
        block = b"bounded-extra-artifact\n" * 4096
        for _ in range(32):
            stream.write(block)
    real_bounded_read = completion_module._secure_read_regular

    def reject_large_collection(path: Path, **kwargs: Any) -> tuple[bytes, str]:
        assert kwargs["max_bytes"] <= completion_module.MAX_COMPLETION_MANIFEST_BYTES
        return real_bounded_read(path, **kwargs)

    def executor(payload: dict[str, Any], **control: Any) -> ToolResult:
        result = _executor(tmp_path, [])(payload, **control)
        result.artifacts = [
            WorkflowArtifact("report", str(report), "large report")
        ]
        return result

    monkeypatch.setattr(
        completion_module,
        "_secure_read_regular",
        reject_large_collection,
    )
    runner = DockingExecution(tmp_path, raw_executor=executor)
    first = runner.run_verified("task-1", manifest)
    reused = runner.run_verified("task-1", manifest)

    assert first["success"] is True
    assert reused["success"] is True
    assert reused["reused_completion"] is True


def test_pose_parser_rejects_unbounded_line(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    pose = _task_output_dir(tmp_path) / "result.pdbqt"
    pose.write_bytes(
        b"MODEL 1\nREMARK VINA RESULT: -7.200 0.000 0.000\n"
        b"REMARK " + b"x" * (128 * 1024) + b"\n"
        b"ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
        b"ENDMDL\n"
    )

    result = DockingExecution(
        tmp_path,
        raw_executor=lambda _payload, **_control: _pose_result(pose),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


@pytest.mark.parametrize("valid_pdbqt", [False, True], ids=["ordinary", "pdbqt"])
def test_external_pose_file_is_never_authoritative(
    tmp_path: Path,
    valid_pdbqt: bool,
) -> None:
    manifest = _stage(tmp_path)
    pose = tmp_path / "external-result.pdbqt"
    pose.write_text(_vina_pdbqt(-7.2) if valid_pdbqt else "ordinary text\n")

    result = DockingExecution(
        tmp_path,
        raw_executor=lambda _payload, **_control: _pose_result(pose),
        allowed_output_root=_allowed_output_root(tmp_path),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


def test_pose_from_different_job_below_allowed_root_is_rejected(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    pose = _task_output_dir(tmp_path, "different-task") / "result.pdbqt"
    pose.write_text(_vina_pdbqt(-7.2), encoding="utf-8")

    result = DockingExecution(
        tmp_path,
        raw_executor=lambda _payload, **_control: _pose_result(pose),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert not _completion(tmp_path).exists()


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_linked_pose_in_allowed_job_directory_is_rejected(
    tmp_path: Path,
    link_kind: str,
) -> None:
    manifest = _stage(tmp_path)
    external = tmp_path / "external-source.pdbqt"
    external.write_text(_vina_pdbqt(-7.2), encoding="utf-8")
    pose = _task_output_dir(tmp_path) / "result.pdbqt"
    try:
        if link_kind == "symlink":
            pose.symlink_to(external)
        else:
            os.link(external, pose)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"{link_kind} unavailable: {exc}")

    result = DockingExecution(
        tmp_path,
        raw_executor=lambda _payload, **_control: _pose_result(pose),
        allowed_output_root=_allowed_output_root(tmp_path),
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
    assert not (tmp_path / "task-1" / "completion_invalid.marker").exists()
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
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(execution_module, "MolecularDocking", lambda: sentinel_tool)

    def compat(tool: Any, payload: dict[str, Any], **control: Any) -> ToolResult:
        assert tool is sentinel_tool
        result = raw(payload, **control)
        source = Path(result.data["pose_file"])
        pose = tmp_path / "temp_docking" / "docking_task-1" / "result.pdbqt"
        pose.parent.mkdir(parents=True)
        source.replace(pose)
        result.data["pose_file"] = str(pose)
        result.data["best_pose"]["pose_file"] = str(pose)
        return result

    monkeypatch.setattr(execution_module, "execute_tool_compat", compat)

    result = DockingExecution(tmp_path).run_verified("task-1", manifest)

    assert result["success"] is True
    assert len(calls) == 1


def test_opensandbox_backend_delegates_to_runner_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.docking.sandbox_runner as sandbox_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    raw = _executor(tmp_path, calls)
    output_root = _allowed_output_root(tmp_path)

    class FakeRunner:
        def execute(self, payload: dict[str, Any], **control: Any) -> ToolResult:
            return raw(payload, **control)

    def from_env(allowed_output_root: Path) -> FakeRunner:
        assert allowed_output_root == output_root.resolve()
        return FakeRunner()

    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setattr(sandbox_module.SandboxDockingRunner, "from_env", from_env)

    result = DockingExecution(
        tmp_path,
        allowed_output_root=output_root,
    ).run_verified("task-1", manifest)

    assert result["success"] is True
    assert len(calls) == 1


def test_opensandbox_retry_reuses_pose_after_completion_prepare_crash(
    tmp_path: Path,
) -> None:
    import src.docking.sandbox_runner as sandbox_module

    manifest_path = _stage(tmp_path)
    output_root = _allowed_output_root(tmp_path)
    staged = json.loads(manifest_path.read_text(encoding="utf-8"))
    receptor = (tmp_path / "task-1" / staged["receptor"]["path"]).read_bytes()
    ligand = (tmp_path / "task-1" / staged["ligand"]["path"]).read_bytes()
    pose = _vina_pdbqt(-7.2).encode("ascii")
    broker_job_id = "1" * 32
    artifact_id = "2" * 32
    provenance = {
        "sandbox_id": "sandbox-public-id",
        "image_uri": "medchat-docking",
        "image_digest": "a" * 64,
        "secure_runtime": "gvisor",
        "vina_version": "1.2.5",
        "meeko_version": "0.6.1",
        "receptor_sha256": hashlib.sha256(receptor).hexdigest(),
        "ligand_sha256": hashlib.sha256(ligand).hexdigest(),
        "cleanup_status": "succeeded",
        "demo_mode": False,
        "fallback_used": False,
    }
    job = {
        "job_id": broker_job_id,
        "trace_id": "trace-public",
        "status": "succeeded",
        "phase": "succeeded",
        "error_code": None,
        "warnings": [],
        "provenance": provenance,
        "cancel_requested": False,
        "cleanup_status": "succeeded",
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    broker_manifest = {
        "schema_version": 1,
        "job_id": broker_job_id,
        "trace_id": "trace-public",
        "pose_count": 1,
        "best_energy": -7.2,
        "artifacts": [
            {
                "artifact_id": artifact_id,
                "media_type": "chemical/x-pdbqt",
                "sha256": hashlib.sha256(pose).hexdigest(),
                "size_bytes": len(pose),
            }
        ],
        "warnings": [],
        "provenance": provenance,
    }
    artifact_downloads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal artifact_downloads
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=job)
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=broker_manifest)
        if request.url.path.endswith(f"/artifacts/{artifact_id}"):
            artifact_downloads += 1
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json={**job, "status": "cancelled"})
        raise AssertionError(request.url.path)

    sandbox = sandbox_module.SandboxDockingRunner(
        tmp_path / "broker.sock",
        output_root,
        transport=httpx.MockTransport(handler),
    )
    execution = DockingExecution(
        tmp_path,
        raw_executor=sandbox.execute,
        allowed_output_root=output_root,
    )
    real_prepare = execution._completions.prepare
    prepare_calls = 0

    def crash_once(*args: Any, **kwargs: Any):
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_calls == 1:
            raise CompletionError("forced_prepare_crash")
        return real_prepare(*args, **kwargs)

    execution._completions.prepare = crash_once

    first = execution.run_verified("task-1", manifest_path)
    pose_path = output_root / "docking_task-1" / "result.pdbqt"
    assert first["success"] is False
    assert not _completion(tmp_path).exists()
    assert pose_path.read_bytes() == pose
    before = pose_path.stat()
    second = execution.run_verified("task-1", manifest_path)
    after = pose_path.stat()

    assert second["success"] is True
    assert artifact_downloads == 1
    assert pose_path.read_bytes() == pose
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


def test_opensandbox_smiles_failure_keeps_stable_invalid_input_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.docking.sandbox_runner as sandbox_module

    manifest = _stage(tmp_path, task_id="task-smiles", smiles=True)
    output_root = _allowed_output_root(tmp_path)
    runner = sandbox_module.SandboxDockingRunner(
        tmp_path / "broker.sock",
        output_root,
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("SMILES must not reach the broker")
        ),
    )
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setattr(
        sandbox_module.SandboxDockingRunner,
        "from_env",
        lambda _output_root: runner,
    )

    result = DockingExecution(
        tmp_path,
        allowed_output_root=output_root,
    ).run_verified("task-smiles", manifest)

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.INVALID_INPUT.value
    assert result["error"]["details"]["reason"] == (
        "smiles_not_supported_by_opensandbox"
    )


@pytest.mark.parametrize(
    ("receptor_name", "ligand_name"),
    [
        ("receptor.pdbqt", "ligand.sdf"),
        ("receptor.pdb", "ligand.pdb"),
        ("receptor.pdb", "ligand.pdbqt"),
    ],
)
def test_opensandbox_rejects_unsupported_staged_suffix_without_local_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receptor_name: str,
    ligand_name: str,
) -> None:
    import src.docking.sandbox_runner as sandbox_module
    import src.task_runtime.docking_execution as execution_module

    manifest = DockingInputStager(tmp_path).stage(
        "task-remote-suffix",
        receptor_name,
        b"ATOM\n",
        ligand_name,
        b"$$$$\n",
        None,
        CONFIG,
    )
    output_root = _allowed_output_root(tmp_path)
    network_calls = 0
    local_calls = 0

    def forbidden_network(_request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("unsupported suffix reached broker")

    def forbidden_local(*_args: Any, **_kwargs: Any) -> ToolResult:
        nonlocal local_calls
        local_calls += 1
        raise AssertionError("OpenSandbox input rejection fell back locally")

    runner = sandbox_module.SandboxDockingRunner(
        tmp_path / "broker.sock",
        output_root,
        transport=httpx.MockTransport(forbidden_network),
    )
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setattr(execution_module, "execute_tool_compat", forbidden_local)
    monkeypatch.setattr(
        sandbox_module.SandboxDockingRunner,
        "from_env",
        lambda _output_root: runner,
    )

    result = DockingExecution(
        tmp_path,
        allowed_output_root=output_root,
    ).run_verified("task-remote-suffix", manifest)

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.INVALID_INPUT.value
    assert result["error"]["details"]["reason"] == "input_invalid"
    assert network_calls == 0
    assert local_calls == 0


def test_opensandbox_broker_failure_never_calls_real_local_tool_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.docking.sandbox_runner as sandbox_module
    import src.task_runtime.docking_execution as execution_module

    manifest = _stage(tmp_path)
    output_root = _allowed_output_root(tmp_path)
    local_calls = {"tool": 0, "compat": 0}

    def molecular_docking():
        local_calls["tool"] += 1
        return object()

    def compat(*_args, **_kwargs):
        local_calls["compat"] += 1
        pytest.fail("local fallback is forbidden")

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(r"C:\private\secret-broker.sock", request=request)

    runner = sandbox_module.SandboxDockingRunner(
        tmp_path / "broker.sock",
        output_root,
        transport=httpx.MockTransport(unavailable),
    )
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setattr(execution_module, "MolecularDocking", molecular_docking)
    monkeypatch.setattr(execution_module, "execute_tool_compat", compat)
    monkeypatch.setattr(
        sandbox_module.SandboxDockingRunner,
        "from_env",
        lambda _output_root: runner,
    )

    result = DockingExecution(
        tmp_path,
        allowed_output_root=output_root,
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE.value
    assert result["error"]["details"]["reason"] == "environment_unavailable"
    assert local_calls == {"tool": 0, "compat": 0}
    assert "private" not in json.dumps(result).lower()
    assert "secret" not in json.dumps(result).lower()


def test_opensandbox_connect_timeout_keeps_environment_unavailable_legacy_reason(
    tmp_path: Path,
) -> None:
    import src.docking.sandbox_runner as sandbox_module

    manifest = _stage(tmp_path)
    output_root = _allowed_output_root(tmp_path)

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(
            "secret broker socket timeout",
            request=request,
        )

    sandbox = sandbox_module.SandboxDockingRunner(
        tmp_path / "broker.sock",
        output_root,
        transport=httpx.MockTransport(unavailable),
    )
    result = DockingExecution(
        tmp_path,
        raw_executor=sandbox.execute,
        allowed_output_root=output_root,
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE.value
    assert result["error"]["details"] == {"reason": "environment_unavailable"}
    assert "secret" not in json.dumps(result).lower()


def test_invalid_docking_execution_backend_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "tcp")

    with pytest.raises(ValueError, match="^invalid docking execution backend$"):
        DockingExecution(tmp_path)


def test_injected_executor_ignores_backend_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "invalid")

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls),
    ).run_verified("task-1", manifest)

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
    assert result["error"]["details"]["reason"] == "validator_rejected"
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


def test_long_concurrent_same_task_executes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls, delay=5.2),
    )
    prepared = threading.Event()
    allow_finalize = threading.Event()
    real_finalize = runner._finalize_prepared

    def gated_finalize(*args: Any, **kwargs: Any) -> dict[str, Any]:
        prepared.set()
        assert allow_finalize.wait(10)
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(runner, "_finalize_prepared", gated_finalize)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                runner.run_verified,
                "task-1",
                manifest,
                lease_timeout_seconds=15,
            )
            for _ in range(2)
        ]
        assert prepared.wait(10)
        time.sleep(0.1)
        allow_finalize.set()
        results = [future.result(timeout=20) for future in futures]

    assert all(result["success"] is True for result in results)
    assert sorted(result["reused_completion"] for result in results) == [False, True]
    assert len(calls) == 1


def test_concurrent_call_waits_while_prepared_completion_holds_lease(
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


def test_owner_unlink_failure_cannot_expose_active_attempt_to_concurrent_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    finalize_entered = threading.Event()
    allow_finalize = threading.Event()
    real_finalize = runner._finalize_prepared
    real_unlink = Path.unlink
    owner_unlinks = {"count": 0}

    def fail_first_attempt_owner_unlink(
        path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if (
            path.name == ".completion-attempt-owner"
            and owner_unlinks["count"] == 0
        ):
            owner_unlinks["count"] += 1
            raise OSError("attempt owner unlink failed")
        real_unlink(path, *args, **kwargs)

    def gated_finalize(*args: Any, **kwargs: Any) -> dict[str, Any]:
        finalize_entered.set()
        assert allow_finalize.wait(10)
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_attempt_owner_unlink)
    monkeypatch.setattr(runner, "_finalize_prepared", gated_finalize)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(runner.run_verified, "task-1", manifest)
        assert finalize_entered.wait(10)
        second_future = pool.submit(runner.run_verified, "task-1", manifest)
        deadline = time.monotonic() + 2
        while len(calls) == 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        allow_finalize.set()
        results = [
            first_future.result(timeout=10),
            second_future.result(timeout=10),
        ]

    assert owner_unlinks["count"] == 1
    assert len(calls) == 1
    assert all(result["success"] is True for result in results)
    assert sorted(result["reused_completion"] for result in results) == [False, True]


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
    if mode == "failure":
        assert result["error"]["details"]["reason"] == "environment_unavailable"


def test_tool_timeout_keeps_stable_legacy_execution_timeout_reason(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)

    def executor(_payload: dict[str, Any], **_control: Any) -> ToolResult:
        return ToolResult.error_result(
            "molecular_docking",
            AgentErrorCode.TOOL_TIMEOUT,
            "raw timeout details must not survive",
            details={"reason": "sandbox_timeout", "path": str(tmp_path)},
        )

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
    )

    assert result["success"] is False
    assert result["error"]["code"] == AgentErrorCode.TOOL_TIMEOUT.value
    assert result["error"]["details"] == {"reason": "execution_timeout"}
    assert str(tmp_path) not in json.dumps(result)


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
    assert result["error"]["details"]["reason"] == "completion_invalid"
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


def test_verified_execution_reports_only_real_phase_boundaries(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    phases: list[str] = []

    def executor(payload: dict[str, Any], **control: Any) -> ToolResult:
        callback = control["progress_callback"]
        callback("receptor_preparation", 10)
        callback("ligand_preparation", 35)
        callback("vina_running", 60)
        return _executor(tmp_path, [])(payload, **control)

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
        progress_callback=lambda phase, _progress: phases.append(phase),
    )

    assert result["success"] is True
    assert phases == [
        "environment_check",
        "input_verification",
        "receptor_preparation",
        "ligand_preparation",
        "vina_running",
        "result_parsing",
        "scientific_validation",
        "artifact_commit",
    ]


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
        result = _executor(tmp_path, [])(payload, **_control)
        executed["value"] = True
        return result

    monkeypatch.setattr(VerifiedDockingInputs, "verify_integrity", verify)

    result = DockingExecution(tmp_path, raw_executor=executor).run_verified(
        "task-1",
        manifest,
    )

    assert result["success"] is False
    assert result["error"]["details"]["reason"] == "input_hash_mismatch"
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
        pose = _task_output_dir(tmp_path, "task-smiles") / "result.pdbqt"
        pose.write_text(_vina_pdbqt(-7.2), encoding="utf-8")
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


def test_additional_artifact_capture_exception_rolls_back_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    first_extra = tmp_path / "task-1" / "reports" / "first.json"
    second_extra = tmp_path / "task-1" / "reports" / "second.json"
    first_extra.parent.mkdir()
    first_extra.write_text('{"first":true}', encoding="utf-8")
    second_extra.write_text('{"second":true}', encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def executor(payload: dict[str, Any], **control: Any) -> ToolResult:
        result = _executor(tmp_path, calls)(payload, **control)
        result.artifacts = [
            WorkflowArtifact("report", str(first_extra), "first"),
            WorkflowArtifact("report", str(second_extra), "second"),
        ]
        return result

    runner = DockingExecution(tmp_path, raw_executor=executor)
    real_capture = runner._completions.capture_additional_artifact
    captures = {"count": 0}

    def fail_second(*args: Any, **kwargs: Any) -> Any:
        captures["count"] += 1
        if captures["count"] == 2:
            raise RuntimeError("malformed second artifact")
        return real_capture(*args, **kwargs)

    monkeypatch.setattr(runner._completions, "capture_additional_artifact", fail_second)
    failed = runner.run_verified("task-1", manifest)

    assert failed["success"] is False
    assert not list((tmp_path / "task-1").glob(".completion-attempt-*"))
    assert not (tmp_path / "task-1" / "artifacts").exists()

    monkeypatch.setattr(runner._completions, "capture_additional_artifact", real_capture)
    retried = runner.run_verified("task-1", manifest)
    assert retried["success"] is True
    assert len(calls) == 2


def test_attempt_owner_write_failure_does_not_leave_unowned_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_atomic_write = completion_module._atomic_write_new

    def fail_owner(destination: Path, *args: Any, **kwargs: Any) -> None:
        if destination.name == ".completion-attempt-owner":
            raise completion_module.CompletionError("completion_io_error")
        real_atomic_write(destination, *args, **kwargs)

    monkeypatch.setattr(completion_module, "_atomic_write_new", fail_owner)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    failed = runner.run_verified("task-1", manifest)

    assert failed["success"] is False
    assert not list((tmp_path / "task-1").glob(".completion-attempt-*"))

    monkeypatch.setattr(completion_module, "_atomic_write_new", real_atomic_write)
    retried = runner.run_verified("task-1", manifest)
    assert retried["success"] is True
    assert len(calls) == 2


def test_stale_owned_artifact_attempt_is_cleaned_before_retry(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, []))
    attempt = runner._completions.begin_artifact_attempt("task-1")
    stale_root = attempt.root
    (stale_root / "artifacts" / "extra-01-deadbeefdead.json").write_text(
        "stale",
        encoding="utf-8",
    )

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is True
    assert not stale_root.exists()


def test_stale_owned_attempt_after_artifact_move_is_recoverable(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, []))
    attempt = runner._completions.begin_artifact_attempt("task-1")
    stale_artifacts = attempt.root / "artifacts"
    (stale_artifacts / "docking_pose.pdbqt").write_text(
        _vina_pdbqt(-7.2),
        encoding="utf-8",
    )
    stale_artifacts.replace(tmp_path / "task-1" / "artifacts")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is True
    assert not attempt.root.exists()


def test_unowned_artifact_attempt_residue_is_fail_closed(tmp_path: Path) -> None:
    manifest = _stage(tmp_path)
    stale_root = tmp_path / "task-1" / ".completion-attempt-attacker"
    stale_root.mkdir()
    calls: list[dict[str, Any]] = []

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert calls == []
    assert stale_root.exists()

def test_credential_shaped_metadata_is_redacted_before_completion(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    credential = "sk-12345678"

    def executor(payload: dict[str, Any], **_control: Any) -> ToolResult:
        result = _executor(tmp_path, [])(payload, **_control)
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


def test_contextual_credentials_and_paths_are_sanitized_everywhere(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    sensitive = [
        "abcd123456",
        "hunter2",
        "AKIAIOSFODNN7EXAMPLE",
        "github_pat_11AA22BB33CC44DD55EE66FF",
        "url-secret-123",
        "header-secret-456",
        r"\\server\share\private\result.txt",
        r"\\?\UNC\server\share\private\result.txt",
        r"\\.\PhysicalDrive0",
        "/srv/private/result.txt",
    ]
    metadata_text = (
        "api_key=abcd123456 password: hunter2 "
        'client_secret="url-secret-123" '
        "AWS AKIAIOSFODNN7EXAMPLE "
        "github_pat_11AA22BB33CC44DD55EE66FF "
        "https://user:url-secret-123@example.test/report?token=url-secret-123 "
        "Authorization: Bearer header-secret-456 "
        r"\\server\share\private\result.txt "
        r"\\?\UNC\server\share\private\result.txt "
        r"\\.\PhysicalDrive0 /srv/private/result.txt"
    )

    def executor(payload: dict[str, Any], **control: Any) -> ToolResult:
        result = _executor(tmp_path, [])(payload, **control)
        result.formatted = metadata_text
        result.warnings = [metadata_text]
        result.evidence = [{"source": "vina", "description": metadata_text}]
        result.quality["engine"] = metadata_text
        result.quality["execution_status"] = "completed"
        return result

    runner = DockingExecution(tmp_path, raw_executor=executor)
    first = runner.run_verified("task-1", manifest)
    reused = runner.run_verified("task-1", manifest)
    manifest_text = _completion(tmp_path).read_text(encoding="utf-8")

    for value in (json.dumps(first), json.dumps(reused), manifest_text):
        assert "[redacted]" in value
        for secret in sensitive:
            assert secret not in value


def test_reviewer_authorization_and_spaced_paths_are_sanitized_end_to_end(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    metadata = (
        "Authorization = Bearer header-secret-456; "
        "'authorization': 'Basic dXNlcjpwYXNz'; "
        'POSIX "/srv/Alice Doe/private/result.txt" keep-posix; '
        r'DRIVE "C:\Users\Alice Doe\private\result.txt" keep-drive; '
        r'UNC "\\server\share\Alice Doe\private\result.txt" keep-unc; '
        r'EXTENDED "\\?\UNC\server\share\Alice Doe\private\result.txt" keep-extended'
    )
    calls: list[dict[str, Any]] = []

    def executor(payload: dict[str, Any], **control: Any) -> ToolResult:
        result = _executor(tmp_path, calls)(payload, **control)
        result.formatted = metadata
        result.warnings = [metadata]
        result.evidence = [{"source": "vina", "description": metadata}]
        result.quality["engine"] = metadata
        result.quality["execution_status"] = "completed"
        return result

    runner = DockingExecution(tmp_path, raw_executor=executor)
    first = runner.run_verified("task-1", manifest)
    reused = runner.run_verified("task-1", manifest)
    serialized = (
        json.dumps(first),
        json.dumps(reused),
        _completion(tmp_path).read_text(encoding="utf-8"),
    )

    assert first["success"] is True
    assert reused["success"] is True
    assert reused["reused_completion"] is True
    assert len(calls) == 1
    for value in serialized:
        assert "[redacted]" in value
        for secret in (
            "header-secret-456",
            "dXNlcjpwYXNz",
            "Alice Doe/private",
            "Doe/private",
            r"Alice Doe\private",
            r"Doe\private",
        ):
            assert secret not in value


def test_unsafe_cached_metadata_fails_closed_without_reexecution(
    tmp_path: Path,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    assert runner.run_verified("task-1", manifest)["success"] is True
    completion_path = _completion(tmp_path)
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    payload["result_metadata"]["warnings"] = ["api_key=abcd123456"]
    completion_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.run_verified("task-1", manifest)

    assert result["success"] is False
    assert len(calls) == 1
    assert "abcd123456" not in json.dumps(result)


@pytest.mark.parametrize(
    "identifier",
    [
        "api_key=abcd123456",
        "password:hunter2",
        "AKIAIOSFODNN7EXAMPLE",
        r"\\server\share\vina",
        "/opt/private/vina",
    ],
)
def test_sensitive_provenance_identifier_is_rejected_without_rewriting(
    tmp_path: Path,
    identifier: str,
) -> None:
    manifest = _stage(tmp_path)
    provenance = ToolProvenance(
        tool_name="molecular_docking",
        tool_version="vina-test-1",
        model_name=identifier,
    )

    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, [], provenance=provenance),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["provenance"] is None
    assert not _completion(tmp_path).exists()


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
        result = _executor(tmp_path, [])(payload, **_control)
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
        result = _executor(tmp_path, [])(payload, **_control)
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


def test_replace_success_then_exception_remains_blocked_by_authority_marker(
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
    blocked = runner.run_verified("task-1", manifest)

    assert first["success"] is False
    assert (tmp_path / "task-1" / "completion_invalid.marker").exists()
    assert blocked["success"] is False
    assert blocked["error"]["details"]["reason"] == "ownership_uncertain"
    assert blocked["reused_completion"] is False
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


def test_windows_directory_fsync_uses_flush_file_buffers_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    calls: list[Path] = []
    monkeypatch.setattr(completion_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        completion_module,
        "_windows_flush_directory",
        lambda path: calls.append(path),
        raising=False,
    )

    completion_module._fsync_directory(tmp_path)

    assert calls == [tmp_path]


def test_windows_directory_flush_failure_is_not_reported_as_synced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    monkeypatch.setattr(completion_module, "_IS_WINDOWS", True)

    def fail_flush(_path: Path) -> None:
        raise OSError("FlushFileBuffers failed")

    monkeypatch.setattr(
        completion_module,
        "_windows_flush_directory",
        fail_flush,
        raising=False,
    )

    with pytest.raises(OSError, match="FlushFileBuffers failed"):
        completion_module._fsync_directory(tmp_path)


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

    def fail_before_commit(_payload: dict[str, Any], **_control: Any) -> ToolResult:
        raise RuntimeError("tool failed before completion preparation")

    monkeypatch.setattr(staging_module._TaskLease, "release", release_then_raise)
    result = DockingExecution(
        tmp_path,
        raw_executor=fail_before_commit,
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


def test_ambiguous_replace_failure_keeps_write_ahead_marker_and_blocks_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_replace = completion_module._replace_file
    real_exact = completion_module._exact_regular_file
    real_isolate = completion_module._isolate_untrusted_manifest

    def replace_then_raise(source: Path, target: Path) -> None:
        real_replace(source, target)
        if Path(target).name == "completion_manifest.json":
            raise OSError("ambiguous publication")

    def target_reverification_unavailable(
        path: Path,
        expected: bytes,
        parent: Path,
    ) -> bool:
        if Path(path).name == "completion_manifest.json":
            return False
        return real_exact(path, expected, parent)

    monkeypatch.setattr(completion_module, "_replace_file", replace_then_raise)
    monkeypatch.setattr(
        completion_module,
        "_exact_regular_file",
        target_reverification_unavailable,
    )
    monkeypatch.setattr(
        completion_module,
        "_isolate_untrusted_manifest",
        lambda _path, _parent: False,
    )
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)

    monkeypatch.setattr(completion_module, "_replace_file", real_replace)
    monkeypatch.setattr(completion_module, "_exact_regular_file", real_exact)
    monkeypatch.setattr(
        completion_module,
        "_isolate_untrusted_manifest",
        real_isolate,
    )
    second = runner.run_verified("task-1", manifest)

    assert first["success"] is False
    assert (tmp_path / "task-1" / "completion_invalid.marker").exists()
    assert second["success"] is False
    assert second["error"]["details"]["reason"] == "ownership_uncertain"
    assert second["reused_completion"] is False
    assert len(calls) == 1


def test_write_ahead_marker_failure_prevents_manifest_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    replace_targets: list[str] = []
    real_replace = completion_module._replace_file

    def track_replace(source: Path, target: Path) -> None:
        replace_targets.append(Path(target).name)
        real_replace(source, target)

    monkeypatch.setattr(
        completion_module,
        "_write_authority_marker",
        lambda _path, _parent: False,
    )
    monkeypatch.setattr(completion_module, "_replace_file", track_replace)
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert "completion_manifest.json" not in replace_targets
    assert not _completion(tmp_path).exists()
    assert len(calls) == 1


def test_marker_removal_failure_returns_uncertain_success_and_blocks_future_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        completion_module,
        "_remove_authority_marker",
        lambda _path, _parent: False,
        raising=False,
    )
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)
    second = runner.run_verified("task-1", manifest)

    assert first["success"] is True
    assert "completion_authority_commit_unconfirmed" in first["warnings"]
    assert first["quality"]["completion_durability"] == "uncertain"
    marker = tmp_path / "task-1" / "completion_invalid.marker"
    assert marker.is_file()
    assert not marker.is_symlink()
    assert marker.stat().st_nlink == 1
    assert marker.read_bytes() == (
        b"DockingCompletionInvalid@1\nownership_uncertain\n"
    )
    assert second["success"] is False
    assert second["error"]["details"]["reason"] == "ownership_uncertain"
    assert second["reused_completion"] is False
    assert len(calls) == 1


def test_marker_unlink_after_delete_error_restores_barrier_and_blocks_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_unlink = Path.unlink

    def unlink_then_raise(path: Path, *args: Any, **kwargs: Any) -> None:
        real_unlink(path, *args, **kwargs)
        if path.name == "completion_invalid.marker":
            raise OSError("marker deletion outcome unavailable")

    monkeypatch.setattr(Path, "unlink", unlink_then_raise)
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)
    second = runner.run_verified("task-1", manifest)

    marker = tmp_path / "task-1" / "completion_invalid.marker"
    assert first["success"] is True
    assert "completion_authority_commit_unconfirmed" in first["warnings"]
    assert first["quality"]["completion_durability"] == "uncertain"
    assert marker.exists()
    assert second["success"] is False
    assert second["error"]["details"]["reason"] == "ownership_uncertain"
    assert second["reused_completion"] is False
    assert len(calls) == 1


def test_marker_unlink_ambiguity_without_recovery_is_explicitly_reusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.completion as completion_module

    manifest = _stage(tmp_path)
    calls: list[dict[str, Any]] = []
    real_unlink = Path.unlink
    real_write_marker = completion_module._write_authority_marker
    marker_writes = {"count": 0}

    def unlink_then_raise(path: Path, *args: Any, **kwargs: Any) -> None:
        real_unlink(path, *args, **kwargs)
        if path.name == "completion_invalid.marker":
            raise OSError("marker deletion outcome unavailable")

    def fail_recovery_write(path: Path, parent: Path) -> bool:
        marker_writes["count"] += 1
        if marker_writes["count"] == 1:
            return real_write_marker(path, parent)
        return False

    monkeypatch.setattr(Path, "unlink", unlink_then_raise)
    monkeypatch.setattr(
        completion_module,
        "_write_authority_marker",
        fail_recovery_write,
    )
    runner = DockingExecution(tmp_path, raw_executor=_executor(tmp_path, calls))
    first = runner.run_verified("task-1", manifest)
    second = runner.run_verified("task-1", manifest)

    assert first["success"] is True
    assert "completion_authority_delete_ambiguous_reusable" in first["warnings"]
    assert first["quality"]["completion_durability"] == "reusable"
    assert first["quality"]["completion_authority"] == "committed"
    assert not (tmp_path / "task-1" / "completion_invalid.marker").exists()
    assert second["success"] is True
    assert second["reused_completion"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("marker_kind", ["malformed", "directory", "symlink", "hardlink"])
def test_untrusted_authority_marker_shape_blocks_execution(
    tmp_path: Path,
    marker_kind: str,
) -> None:
    from src.task_runtime.completion import CompletionError, DockingCompletionStore

    manifest = _stage(tmp_path)
    marker = tmp_path / "task-1" / "completion_invalid.marker"
    source = tmp_path / "attacker-marker"
    source.write_text("attacker-controlled", encoding="utf-8")
    try:
        if marker_kind == "malformed":
            marker.write_text("not the authority schema", encoding="utf-8")
        elif marker_kind == "directory":
            marker.mkdir()
        elif marker_kind == "symlink":
            marker.symlink_to(source)
        else:
            os.link(source, marker)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"{marker_kind} is unavailable on this platform: {exc}")

    store = DockingCompletionStore(tmp_path)
    with pytest.raises(CompletionError, match="completion_ownership_uncertain"):
        store.has_manifest("task-1")
    with pytest.raises(CompletionError, match="completion_ownership_uncertain"):
        store.load_verified(
            "task-1",
            input_hash="0" * 64,
            config_hash="1" * 64,
        )

    calls: list[dict[str, Any]] = []
    result = DockingExecution(
        tmp_path,
        raw_executor=_executor(tmp_path, calls),
    ).run_verified("task-1", manifest)

    assert result["success"] is False
    assert result["error"]["details"]["reason"] == "ownership_uncertain"
    assert result["reused_completion"] is False
    assert calls == []
