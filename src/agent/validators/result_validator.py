from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.contracts import (
    AgentErrorCode,
    AgentExecutionError,
    CandidateSet,
    ObservationStatus,
    ToolResult,
)

from .domain_validators import DOMAIN_VALIDATORS
from .molecule_candidates import (
    CandidateValidationUnavailable,
    sanitize_generated_candidates,
)


class AgentResultValidator:
    """Validate common agent tool outputs without blocking partial success."""

    def validate_tool_result(self, result: ToolResult) -> ToolResult:
        if not result.success:
            return result

        if result.tool_name == "llm_molecular_generator":
            requested_count = result.quality.get("requested_count")
            generation_provenance = (
                result.provenance.to_dict() if result.provenance else None
            )
            try:
                sanitized = sanitize_generated_candidates(
                    result.data,
                    requested_count=requested_count,
                    generation_provenance=generation_provenance,
                )
            except CandidateValidationUnavailable as exc:
                unavailable_requested = self._known_requested_count(
                    requested_count,
                    result.data,
                )
                candidate_set = CandidateSet(
                    requested_count=unavailable_requested,
                    candidates=(),
                    status=ObservationStatus.UNAVAILABLE,
                )
                result.success = False
                result.status = ObservationStatus.UNAVAILABLE
                result.data = candidate_set.to_dict()
                result.formatted = ""
                result.message = str(exc)
                result.quality = {
                    **result.quality,
                    "requested_count": unavailable_requested,
                    "valid_count": 0,
                    "unique_count": 0,
                    "invalid_count": 0,
                    "duplicate_count": 0,
                    "validation_method": "RDKit",
                    "output_contract": "CandidateSet@1",
                }
                result.error = AgentExecutionError(
                    code=AgentErrorCode.TOOL_UNAVAILABLE,
                    message=str(exc),
                    details={"tool_name": result.tool_name},
                )
                return result

            candidate_set = sanitized.candidate_set
            result.data = candidate_set.to_dict()
            result.status = candidate_set.status
            result.quality = {
                **result.quality,
                "requested_count": candidate_set.requested_count,
                "valid_count": candidate_set.valid_count,
                "unique_count": candidate_set.unique_count,
                "invalid_count": candidate_set.invalid_count,
                "duplicate_count": candidate_set.duplicate_count,
                "validation_method": "RDKit",
                "output_contract": "CandidateSet@1",
            }
            if not candidate_set.candidates:
                result.success = False
                result.status = ObservationStatus.FAILED
                result.formatted = ""
                result.message = "Generated output contained no valid unique SMILES"
                result.error = AgentExecutionError(
                    code=AgentErrorCode.INVALID_OUTPUT,
                    message=result.message,
                    details=result.quality,
                )
                return result
            if candidate_set.status == ObservationStatus.PARTIAL:
                result.warnings.append(
                    f"Generated {candidate_set.valid_count} valid unique SMILES "
                    f"out of {candidate_set.requested_count} requested"
                )

        warnings = list(result.warnings)
        warnings.extend(self._validate_smiles_payload(result.data))
        warnings.extend(self._validate_file_payload(result.data))
        domain_error = next(
            (
                message
                for validator in DOMAIN_VALIDATORS
                if (message := validator.validate(result))
            ),
            None,
        )
        if domain_error:
            warnings.append(domain_error)
            result.success = False
            result.status = ObservationStatus.FAILED
            result.message = domain_error
            result.formatted = ""
            result.error = AgentExecutionError(
                code=AgentErrorCode.INVALID_OUTPUT,
                message=domain_error,
                details={"tool_name": result.tool_name},
            )
        result.warnings = warnings
        result.quality = {
            **result.quality,
            "validated": not bool(warnings) and result.success,
        }
        return result

    @staticmethod
    def _known_requested_count(requested_count: Any, data: Any) -> int:
        if (
            not isinstance(requested_count, bool)
            and isinstance(requested_count, int)
            and requested_count >= 0
        ):
            return requested_count
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            for key in ("molecules", "candidates", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return len(value)
        return 0

    def _validate_smiles_payload(self, data: Any) -> list[str]:
        warnings: list[str] = []
        smiles_values = self._collect_smiles(data)
        if not smiles_values:
            return warnings

        try:
            from rdkit import Chem
        except Exception:
            for smiles in smiles_values:
                if self._looks_invalid_without_rdkit(str(smiles)):
                    warnings.append(f"Invalid SMILES marked: {smiles}")
            if not warnings:
                warnings.append("RDKit is unavailable; SMILES validation was skipped")
            return warnings

        for smiles in smiles_values:
            if not smiles or Chem.MolFromSmiles(str(smiles)) is None:
                warnings.append(f"Invalid SMILES marked: {smiles}")
        return warnings

    def _validate_file_payload(self, data: Any) -> list[str]:
        warnings: list[str] = []
        if not isinstance(data, dict):
            return warnings

        for key in ["pose_file", "protein_file", "ligand_file", "local_file_path"]:
            value = data.get(key)
            if value and not Path(str(value)).exists():
                warnings.append(f"{key} file does not exist: {value}")
        return warnings

    def _collect_smiles(self, data: Any) -> list[str]:
        values: list[str] = []
        if isinstance(data, dict):
            if "smiles" in data:
                values.append(str(data["smiles"]))
            for collection_key in ("molecules", "candidates", "ligands"):
                for item in data.get(collection_key, []) or []:
                    if isinstance(item, dict) and "smiles" in item:
                        values.append(str(item["smiles"]))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "smiles" in item:
                    values.append(str(item["smiles"]))
        return values

    @staticmethod
    def _looks_invalid_without_rdkit(smiles: str) -> bool:
        if not smiles.strip():
            return True
        if any(char.isspace() for char in smiles):
            return True
        if smiles.count("(") != smiles.count(")") or smiles.count("[") != smiles.count("]"):
            return True
        aromatic_lowercase = {"b", "c", "n", "o", "p", "s"}
        for char in smiles:
            if char.islower() and char not in aromatic_lowercase:
                return True
        return False
