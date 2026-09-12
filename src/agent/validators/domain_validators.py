from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any

from src.agent.contracts import ToolResult, WorkflowArtifact
from src.task_runtime.secure_io import read_file_snapshot


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_VERIFIED_POSE_BYTES = 64 * 1024 * 1024


class DockingResultValidator:
    @staticmethod
    def _numeric_triplet(value: Any, *, positive: bool = False) -> bool:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return False
        for item in value:
            if type(item) not in (int, float) or not math.isfinite(float(item)):
                return False
            if positive and float(item) <= 0:
                return False
        return True

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
        result: ToolResult,
        pose_path: object,
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
        if len(matching) != 1:
            return False
        metadata = matching[0].metadata
        if not isinstance(metadata, dict):
            return False
        digest = metadata.get("sha256")
        if type(digest) is not str or _SHA256_PATTERN.fullmatch(digest) is None:
            return False
        try:
            snapshot = read_file_snapshot(
                Path(pose_path),
                _MAX_VERIFIED_POSE_BYTES,
            )
        except ValueError:
            return False
        return bool(snapshot.content) and snapshot.sha256 == digest

    @classmethod
    def _has_opensandbox_trust_contract(
        cls,
        result: ToolResult,
        pose_path: object,
    ) -> bool:
        quality = result.quality
        digest = quality.get("sandbox_image_digest")
        provenance = result.provenance
        return bool(
            quality.get("real_execution") is True
            and quality.get("secure_runtime") == "gvisor"
            and type(digest) is str
            and _SHA256_PATTERN.fullmatch(digest) is not None
            and quality.get("cleanup_status") == "succeeded"
            and provenance is not None
            and provenance.tool_name == "molecular_docking"
            and provenance.demo_mode is False
            and provenance.fallback_used is False
            and cls._has_verified_opensandbox_artifact(result, pose_path)
        )

    def validate(self, result: ToolResult) -> str | None:
        if result.tool_name not in {
            "molecular_docking",
            "run_docking",
            "get_docking_result",
        }:
            return None
        data = result.data if isinstance(result.data, dict) else {}
        best_pose = data.get("best_pose") if isinstance(data.get("best_pose"), dict) else {}
        has_docking_claim = bool(
            data.get("total_poses")
            or data.get("pose_file")
            or best_pose.get("binding_energy") is not None
        )
        if not has_docking_claim:
            return None
        inputs = result.quality.get("docking_inputs", {})
        energy = best_pose.get("binding_energy")
        pose_path = (
            best_pose.get("pose_file")
            or best_pose.get("output_file")
            or data.get("pose_file")
        )
        is_opensandbox = (
            data.get("execution_backend") == "opensandbox"
            or result.quality.get("execution_backend") == "opensandbox"
        )
        has_inputs = (
            inputs.get("receptor_provided") is True
            and inputs.get("ligand_provided") is True
            if is_opensandbox
            else self._has_verified_inputs(inputs)
        )
        pose_exists = bool(pose_path and Path(str(pose_path)).is_file())
        valid_energy = (
            type(energy) in (int, float) and math.isfinite(float(energy))
        )
        pose_count = data.get("total_poses")
        valid_pose_count = type(pose_count) is int and pose_count > 0
        opensandbox_trusted = (
            not is_opensandbox
            or self._has_opensandbox_trust_contract(result, pose_path)
        )
        if (
            not valid_energy
            or not valid_pose_count
            or not has_inputs
            or not pose_exists
            or not opensandbox_trusted
        ):
            return (
                "Docking evidence is incomplete: receptor, ligand, box, "
                "pose file, and numeric binding energy are required"
            )
        return None


class ActivityResultValidator:
    def validate(self, result: ToolResult) -> str | None:
        if result.tool_name != "activity_predictor":
            return None
        entries = result.data if isinstance(result.data, list) else []
        for entry in entries:
            if not isinstance(entry, dict) or not any(
                key in entry for key in ("family_id", "predicted_pIC50", "activity_probability", "activity_class")
            ):
                continue
            if all(entry.get(key) is None for key in ("predicted_pIC50", "activity_probability", "activity_class")):
                if entry.get("status") != "failed" or entry.get("success") is not False:
                    return "Empty family activity result cannot claim successful or partial observations"
                continue
            if entry.get("units") != "pIC50" or entry.get("label_threshold") != 5.0:
                return "Family activity claim does not match the fixed pIC50 label contract"
            for key in ("predicted_pIC50", "activity_probability"):
                value = entry.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                    return "Family activity result contains an invalid numeric prediction"
            probability = entry.get("activity_probability")
            if probability is not None and not 0 <= probability <= 1:
                return "Family activity probability is outside the unit interval"
            activity_class = entry.get("activity_class")
            if (activity_class not in ("有活性", "无活性") or probability is None
                    or entry.get("probability_threshold") != 0.5
                    or activity_class != ("有活性" if probability >= 0.5 else "无活性")):
                return "Family activity classification lacks a valid probability decision"
            errors = entry.get("errors")
            if not isinstance(errors, dict) or any(key != "regression" for key in errors):
                return "Family activity claim contradicts stage errors"
            if entry.get("status") == "passed":
                valid_stage = entry.get("success") is True and entry.get("predicted_pIC50") is not None and not errors
            elif entry.get("status") == "partial":
                valid_stage = entry.get("success") is False and entry.get("predicted_pIC50") is None
            else:
                valid_stage = False
            if not valid_stage:
                return "Family activity claim contradicts stage status"
            provenance = entry.get("provenance")
            if (not isinstance(provenance, dict) or not provenance.get("bundle_id")
                    or provenance["bundle_id"] != entry.get("bundle_id")):
                return "Family activity result lacks pinned two-model provenance"
            models = provenance.get("models")
            if not isinstance(models, dict):
                return "Family activity result lacks pinned two-model provenance"
            for task in ("classification", "regression"):
                model = models.get(task)
                if (not isinstance(model, dict) or not model.get("model_id")
                        or any(not isinstance(model.get(key), str)
                               or not _SHA256_PATTERN.fullmatch(model[key])
                               for key in ("weights_sha256", "model_card_sha256", "prepared_dataset_sha256"))
                        or model.get("task_type") != task
                        or model.get("target_id") != entry.get("family_id")
                        or model.get("demo_mode") is not False
                        or model.get("fallback_used") is not False):
                    return "Family activity result lacks real two-model provenance"
        has_activity_claim = any(
            isinstance(entry, dict)
            and (
                entry.get("activity_score") is not None
                or entry.get("pic50") is not None
            )
            for entry in entries
        )
        if not has_activity_claim:
            return None
        provenance = result.quality.get("model_provenance", {})
        if (
            not provenance.get("model_path")
            or provenance.get("demo_mode") is not False
        ):
            return "Activity result lacks real model provenance"
        return None


class ADMETResultValidator:
    def validate(self, result: ToolResult) -> str | None:
        if result.tool_name != "admet_predictor":
            return None
        entries = result.data if isinstance(result.data, list) else []
        has_admet_claim = any(
            isinstance(entry, dict) and "admet" in entry
            for entry in entries
        )
        if not has_admet_claim:
            return None
        for entry in entries:
            admet = entry.get("admet") if isinstance(entry, dict) else None
            if not isinstance(admet, dict) or not admet.get("prediction_method"):
                return "ADMET result lacks prediction-method provenance"
        return None


class TargetEvidenceValidator:
    def validate(self, result: ToolResult) -> str | None:
        if result.tool_name not in {
            "reverse_target_predictor",
            "target_database_search",
        }:
            return None
        entries = result.data if isinstance(result.data, list) else []
        for entry in entries:
            if not isinstance(entry, dict):
                return "Target result contains an invalid evidence record"
            if not any(
                key in entry
                for key in (
                    "target_name",
                    "gene_symbol",
                    "protein_name",
                    "structure_id",
                    "pdb_id",
                )
            ):
                continue
            has_evidence = bool(
                result.evidence
                or entry.get("final_similarity") is not None
                or entry.get("match_reason")
                or entry.get("uniprot_id")
                or entry.get("source")
            )
            if not has_evidence:
                return "Target result lacks database or similarity evidence"
        return None


DOMAIN_VALIDATORS = (
    DockingResultValidator(),
    ActivityResultValidator(),
    ADMETResultValidator(),
    TargetEvidenceValidator(),
)
