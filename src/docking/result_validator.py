"""Docking-owned validation for verified execution results.

This module contains only the scientific checks needed after a docking
execution.  Agent candidate/generation validation remains in the Agent layer.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

from src.system.scientific_contracts import (
    AgentErrorCode,
    AgentExecutionError,
    ToolResult,
    WorkflowArtifact,
)
from src.system.scientific_status import ObservationStatus


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
            with Path(pose_path).open("rb") as handle:
                content = handle.read(_MAX_VERIFIED_POSE_BYTES + 1)
        except (OSError, ValueError):
            return False
        return (
            bool(content)
            and len(content) <= _MAX_VERIFIED_POSE_BYTES
            and hashlib.sha256(content).hexdigest() == digest
        )

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
        has_inputs = (
            inputs.get("receptor_provided") is True
            and inputs.get("ligand_provided") is True
            if is_sandbox
            else self._has_verified_inputs(inputs)
        )
        valid_energy = type(energy) in (int, float) and math.isfinite(float(energy))
        pose_count = data.get("total_poses")
        valid_pose_count = type(pose_count) is int and pose_count > 0
        trusted = not is_sandbox or self._has_opensandbox_trust_contract(result, pose_path)
        if not valid_energy or not valid_pose_count or not has_inputs or not (
            pose_path and Path(str(pose_path)).is_file()
        ) or not trusted:
            return (
                "Docking evidence is incomplete: receptor, ligand, box, pose file, "
                "and numeric binding energy are required"
            )
        return None


class DockingExecutionResultValidator:
    """Apply the common result-state checks required by task runtime docking."""

    def validate_tool_result(self, result: ToolResult, *, trusted_checkpoint: bool = False) -> ToolResult:
        try:
            result.status = ObservationStatus(result.status)
        except (TypeError, ValueError):
            return self._invalid(result, "Invalid observation status")
        if result.success and result.status not in {
            ObservationStatus.SUCCEEDED,
            ObservationStatus.PARTIAL,
        }:
            return self._invalid(result, "Success flag conflicts with observation status")
        if not result.success and result.status is ObservationStatus.SUCCEEDED:
            return self._invalid(result, "Failed observation cannot have succeeded status")
        if result.success and result.error is not None:
            return self._invalid(result, "Conflicting success flag and structured error")
        if not result.success:
            return result
        warnings = list(result.warnings)
        domain_error = DockingResultValidator().validate(result)
        if domain_error:
            warnings.append(domain_error)
            result.success = False
            result.status = ObservationStatus.FAILED
            result.message = domain_error
            result.formatted = ""
            result.error = AgentExecutionError(
                AgentErrorCode.INVALID_OUTPUT,
                domain_error,
                details={"tool_name": result.tool_name},
            )
        result.warnings = warnings
        result.quality = {
            **result.quality,
            "validated": not bool(warnings) and result.success,
        }
        return result

    @staticmethod
    def _invalid(result: ToolResult, message: str) -> ToolResult:
        result.success = False
        result.status = ObservationStatus.FAILED
        result.formatted = ""
        result.error = AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, message)
        return result


__all__ = ["DockingExecutionResultValidator", "DockingResultValidator"]
