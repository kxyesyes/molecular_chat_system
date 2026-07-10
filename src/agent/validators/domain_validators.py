from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.contracts import ToolResult


class DockingResultValidator:
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
        has_inputs = all(
            inputs.get(key)
            for key in ("receptor_path", "ligand_input", "center", "size")
        )
        pose_exists = bool(pose_path and Path(str(pose_path)).is_file())
        if (
            not isinstance(energy, (int, float))
            or int(data.get("total_poses") or 0) < 1
            or not has_inputs
            or not pose_exists
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
