"""Docking evidence checks shared by Agent and task execution."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from src.system.scientific_contracts import ToolResult, WorkflowArtifact
from src.task_runtime.secure_io import read_file_snapshot


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_VERIFIED_POSE_BYTES = 64 * 1024 * 1024
_DOCKING_TOOL_NAMES = frozenset(
    {"molecular_docking", "run_docking", "get_docking_result"}
)


class DockingResultValidator:
    """Validate the evidence required for a real docking result."""

    @staticmethod
    def _numeric_triplet(value: Any, *, positive: bool = False) -> bool:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return False
        return all(
            type(item) in (int, float)
            and math.isfinite(float(item))
            and (not positive or float(item) > 0)
            for item in value
        )

    @classmethod
    def _has_verified_inputs(cls, inputs: dict[str, Any]) -> bool:
        has_box = cls._numeric_triplet(inputs.get("center")) and cls._numeric_triplet(
            inputs.get("size"), positive=True
        )
        legacy = bool(inputs.get("receptor_path") and inputs.get("ligand_input"))
        redacted = (
            inputs.get("receptor_provided") is True
            and inputs.get("ligand_provided") is True
            and inputs.get("ligand_mode") in {"file", "smiles"}
        )
        return has_box and (legacy or redacted)

    @staticmethod
    def _has_verified_opensandbox_artifact(
        result: ToolResult, pose_path: object
    ) -> bool:
        if type(pose_path) is not str:
            return False
        matching = [
            artifact
            for artifact in result.artifacts
            if isinstance(artifact, WorkflowArtifact)
            and artifact.artifact_type == "docking_pose"
            and artifact.path == pose_path
            and artifact.mime_type == "chemical/x-pdbqt"
        ]
        if len(matching) != 1 or not isinstance(matching[0].metadata, dict):
            return False
        digest = matching[0].metadata.get("sha256")
        if type(digest) is not str or _SHA256_PATTERN.fullmatch(digest) is None:
            return False
        try:
            snapshot = read_file_snapshot(Path(pose_path), _MAX_VERIFIED_POSE_BYTES)
        except ValueError:
            return False
        return bool(snapshot.content) and snapshot.sha256 == digest

    @classmethod
    def _has_opensandbox_trust_contract(cls, result: ToolResult, pose_path: object) -> bool:
        quality = result.quality
        provenance = result.provenance
        return bool(
            quality.get("real_execution") is True
            and quality.get("secure_runtime") == "gvisor"
            and type(quality.get("sandbox_image_digest")) is str
            and _SHA256_PATTERN.fullmatch(quality["sandbox_image_digest"]) is not None
            and quality.get("cleanup_status") == "succeeded"
            and provenance is not None
            and provenance.tool_name == "molecular_docking"
            and provenance.demo_mode is False
            and provenance.fallback_used is False
            and cls._has_verified_opensandbox_artifact(result, pose_path)
        )

    def validate(self, result: ToolResult) -> str | None:
        if result.tool_name not in _DOCKING_TOOL_NAMES:
            return None
        data = result.data if isinstance(result.data, dict) else {}
        best_pose = data.get("best_pose") if isinstance(data.get("best_pose"), dict) else {}
        has_claim = bool(
            data.get("total_poses")
            or data.get("pose_file")
            or best_pose.get("binding_energy") is not None
        )
        if not has_claim:
            return None
        inputs = result.quality.get("docking_inputs", {})
        energy = best_pose.get("binding_energy")
        pose_path = best_pose.get("pose_file") or best_pose.get("output_file") or data.get("pose_file")
        is_sandbox = (
            data.get("execution_backend") == "opensandbox"
            or result.quality.get("execution_backend") == "opensandbox"
        )
        if is_sandbox:
            has_inputs = inputs.get("receptor_provided") is True and inputs.get("ligand_provided") is True
            pose_verified = self._has_opensandbox_trust_contract(result, pose_path)
        else:
            has_inputs = self._has_verified_inputs(inputs)
            pose_verified = bool(pose_path and Path(str(pose_path)).is_file())
        valid_energy = type(energy) in (int, float) and math.isfinite(float(energy))
        pose_count = data.get("total_poses")
        valid_pose_count = type(pose_count) is int and pose_count > 0
        if not (valid_energy and valid_pose_count and has_inputs and pose_verified):
            return (
                "Docking evidence is incomplete: receptor, ligand, box, pose file, "
                "and numeric binding energy are required"
            )
        return None
