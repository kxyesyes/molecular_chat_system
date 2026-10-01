"""Durable docking results must expose explicit legacy-result identity and pose mapping."""

from __future__ import annotations

from src.agent.contracts import ToolResult
from src.task_runtime.completion import (
    DockingCompletionManifest,
    DockingPoseRecord,
    DockingResultMetadata,
    _parse_manifest,
)
from src.task_runtime.docking_execution import (
    _trusted_result,
    _validated_scientific_values,
)


def _completion(*, best_pose_index: int | None) -> DockingCompletionManifest:
    return DockingCompletionManifest(
        schema="DockingCompletionManifest@1",
        task_id="task-123",
        input_hash="a" * 64,
        config_hash="b" * 64,
        tool_version="vina-1.2.3",
        model_name=None,
        model_version=None,
        demo_mode=False,
        fallback_used=False,
        real_execution=True,
        pose=DockingPoseRecord(
            path="artifacts/docking_pose.pdbqt",
            size=128,
            sha256="c" * 64,
        ),
        pose_count=3,
        best_energy=-9.1,
        best_pose_index=best_pose_index,
        validator_status="succeeded",
        attempt=1,
        completed_at="2026-10-02T00:00:00Z",
        result_metadata=DockingResultMetadata(
            elapsed_ms=100,
            formatted="verified",
            warnings=[],
            evidence=[],
            artifacts=[],
            quality={
                "real_execution": True,
                "engine": None,
                "execution_status": None,
                "validated": True,
            },
        ),
    )


def test_trusted_result_exposes_explicit_result_identity_and_best_pose() -> None:
    result = _trusted_result(_completion(best_pose_index=2), reused=False)

    assert result["data"]["result_job_id"] == "task-123"
    assert result["data"]["best_pose"]["pose"] == 2


def test_legacy_completion_without_pose_mapping_does_not_invent_one() -> None:
    result = _trusted_result(_completion(best_pose_index=None), reused=True)

    assert result["data"]["result_job_id"] == "task-123"
    assert "pose" not in result["data"]["best_pose"]


def test_scientific_validation_carries_the_verified_pose_index() -> None:
    tool_result = ToolResult.success_result(
        "molecular_docking",
        data={
            "total_poses": 3,
            "best_pose": {
                "binding_energy": -9.1,
                "pose": 2,
                "pose_file": "D:/safe/result.pdbqt",
            },
        },
    )

    assert _validated_scientific_values(tool_result)[3] == 2


def test_legacy_completion_manifest_remains_readable_without_pose_index() -> None:
    payload = _completion(best_pose_index=2).to_dict()
    payload.pop("best_pose_index")

    parsed = _parse_manifest(payload, "task-123")

    assert parsed.best_pose_index is None
