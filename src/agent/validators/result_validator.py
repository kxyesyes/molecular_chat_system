from __future__ import annotations

import re
from collections.abc import Mapping
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
    _extract_candidate_items,
    sanitize_generated_candidates,
)


_GENERATOR_QUALITY_FIELDS = {
    "raw_count",
    "requested_count",
    "actual_count",
    "valid_count",
    "unique_count",
    "invalid_count",
    "duplicate_count",
    "partial_generation",
    "output_contract",
    "validated",
    "validation_available",
    "validation_method",
}


class AgentResultValidator:
    """Validate common agent tool outputs without blocking partial success."""

    def validate_tool_result(self, result: ToolResult) -> ToolResult:
        if result.tool_name == "llm_molecular_generator" and result.success:
            return self._validate_generator_result(result)
        if not result.success:
            return result

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

    def _validate_generator_result(self, result: ToolResult) -> ToolResult:
        original_data = result.data
        raw_count = self._raw_candidate_count(original_data)
        requested_count = result.quality.get("requested_count")
        generation_provenance = (
            result.provenance.to_dict() if result.provenance else None
        )
        try:
            if self._looks_like_candidate_set(original_data):
                candidate_set = CandidateSet.from_dict(original_data)
                raw_count = candidate_set.valid_count + len(candidate_set.rejected)
                self._revalidate_candidate_set(candidate_set)
            else:
                candidate_set = sanitize_generated_candidates(
                    original_data,
                    requested_count=requested_count,
                    generation_provenance=generation_provenance,
                ).candidate_set
        except CandidateValidationUnavailable as exc:
            requested = self._safe_requested_count(requested_count, raw_count)
            candidate_set = CandidateSet(
                requested_count=requested,
                candidates=(),
                status=ObservationStatus.UNAVAILABLE,
            )
            return self._fail_generator_result(
                result,
                candidate_set,
                raw_count=raw_count,
                message=str(exc),
                code=AgentErrorCode.TOOL_UNAVAILABLE,
                status=ObservationStatus.UNAVAILABLE,
                validation_available=False,
            )
        except (TypeError, ValueError) as exc:
            candidate_set = CandidateSet(
                requested_count=raw_count,
                candidates=(),
                status=ObservationStatus.FAILED,
            )
            return self._fail_generator_result(
                result,
                candidate_set,
                raw_count=raw_count,
                message=f"Invalid generated candidate output: {exc}",
                code=AgentErrorCode.INVALID_OUTPUT,
                status=ObservationStatus.FAILED,
                validation_available=False,
            )

        validation_available = candidate_set.status not in {
            ObservationStatus.UNAVAILABLE,
            ObservationStatus.INVALID_INPUT,
            ObservationStatus.REJECTED,
            ObservationStatus.CANCELLED,
        }
        result.data = candidate_set.to_dict()
        result.status = candidate_set.status
        result.quality = self._generator_quality(
            result.quality,
            candidate_set,
            raw_count=raw_count,
            validation_available=validation_available,
        )
        result.warnings = self._generator_warnings(
            result.warnings,
            candidate_set,
        )

        if candidate_set.status not in {
            ObservationStatus.SUCCEEDED,
            ObservationStatus.PARTIAL,
        }:
            code, message = self._candidate_set_failure(candidate_set.status)
            return self._fail_generator_result(
                result,
                candidate_set,
                raw_count=raw_count,
                message=message,
                code=code,
                status=candidate_set.status,
                validation_available=validation_available,
            )

        result.success = True
        result.error = None
        result.formatted = self._format_candidate_set(candidate_set)
        return result

    def _fail_generator_result(
        self,
        result: ToolResult,
        candidate_set: CandidateSet,
        *,
        raw_count: int,
        message: str,
        code: AgentErrorCode,
        status: ObservationStatus,
        validation_available: bool,
    ) -> ToolResult:
        result.success = False
        result.status = status
        result.data = candidate_set.to_dict()
        result.formatted = ""
        result.message = message
        result.warnings = self._generator_warnings(result.warnings, candidate_set)
        result.quality = self._generator_quality(
            result.quality,
            candidate_set,
            raw_count=raw_count,
            validation_available=validation_available,
        )
        result.error = AgentExecutionError(
            code=code,
            message=message,
            details=result.quality,
        )
        return result

    @staticmethod
    def _looks_like_candidate_set(data: Any) -> bool:
        return isinstance(data, Mapping) and {
            "version",
            "requested_count",
            "candidates",
            "status",
        }.issubset(data)

    @staticmethod
    def _revalidate_candidate_set(candidate_set: CandidateSet) -> None:
        if not candidate_set.candidates:
            return
        try:
            from rdkit import Chem
        except Exception as exc:
            raise CandidateValidationUnavailable(
                "RDKit is required to revalidate serialized candidates"
            ) from exc

        for candidate in candidate_set.candidates:
            try:
                molecule = Chem.MolFromSmiles(candidate.canonical_smiles)
                canonical = (
                    Chem.MolToSmiles(molecule)
                    if molecule is not None
                    else None
                )
            except Exception as exc:
                raise ValueError(
                    "serialized candidate failed RDKit validation"
                ) from exc
            if molecule is None or canonical != candidate.canonical_smiles:
                raise ValueError(
                    "serialized canonical_smiles does not match RDKit"
                )

    @staticmethod
    def _candidate_set_failure(
        status: ObservationStatus,
    ) -> tuple[AgentErrorCode, str]:
        failures = {
            ObservationStatus.FAILED: (
                AgentErrorCode.INVALID_OUTPUT,
                "Generated output contained no valid unique SMILES",
            ),
            ObservationStatus.UNAVAILABLE: (
                AgentErrorCode.TOOL_UNAVAILABLE,
                "Generated candidate validation is unavailable",
            ),
            ObservationStatus.INVALID_INPUT: (
                AgentErrorCode.INVALID_INPUT,
                "Generated candidate input is invalid",
            ),
            ObservationStatus.REJECTED: (
                AgentErrorCode.VALIDATION_ERROR,
                "Generated candidate output was rejected",
            ),
            ObservationStatus.CANCELLED: (
                AgentErrorCode.INTERNAL_ERROR,
                "Generated candidate validation was cancelled",
            ),
        }
        return failures[status]

    @staticmethod
    def _raw_candidate_count(data: Any) -> int:
        return len(_extract_candidate_items(data))

    @staticmethod
    def _safe_requested_count(requested_count: Any, raw_count: int) -> int:
        if (
            not isinstance(requested_count, bool)
            and isinstance(requested_count, int)
            and requested_count >= 0
        ):
            return requested_count
        return raw_count

    @staticmethod
    def _generator_quality(
        quality: Mapping[str, Any],
        candidate_set: CandidateSet,
        *,
        raw_count: int,
        validation_available: bool,
    ) -> dict[str, Any]:
        normalized = {
            key: value
            for key, value in quality.items()
            if key not in _GENERATOR_QUALITY_FIELDS
        }
        actual_count = candidate_set.valid_count
        normalized.update(
            {
                "raw_count": raw_count,
                "requested_count": candidate_set.requested_count,
                "actual_count": actual_count,
                "valid_count": actual_count,
                "unique_count": candidate_set.unique_count,
                "invalid_count": candidate_set.invalid_count,
                "duplicate_count": candidate_set.duplicate_count,
                "partial_generation": candidate_set.requested_count > actual_count,
                "output_contract": "CandidateSet@1",
                "validated": validation_available,
                "validation_available": validation_available,
                "validation_method": "RDKit" if validation_available else None,
            }
        )
        return normalized

    @classmethod
    def _generator_warnings(
        cls,
        warnings: list[str],
        candidate_set: CandidateSet,
    ) -> list[str]:
        normalized: list[str] = []
        for warning in warnings:
            if cls._is_generation_count_warning(warning):
                continue
            if warning not in normalized:
                normalized.append(warning)
        if candidate_set.status == ObservationStatus.PARTIAL:
            normalized.append(
                f"Generated {candidate_set.valid_count} valid unique SMILES "
                f"out of {candidate_set.requested_count} requested"
            )
        return normalized

    @staticmethod
    def _is_generation_count_warning(warning: str) -> bool:
        text = str(warning).strip()
        return bool(
            re.fullmatch(
                r"Only generated \d+ valid unique SMILES out of requested \d+\.?",
                text,
                flags=re.IGNORECASE,
            )
            or re.fullmatch(
                r"Generated \d+ valid unique SMILES out of \d+ requested",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _format_candidate_set(candidate_set: CandidateSet) -> str:
        lines = [
            "## Validated molecular candidates",
            "",
            "| Candidate ID | Canonical SMILES |",
            "| --- | --- |",
        ]
        lines.extend(
            "| "
            f"{AgentResultValidator._escape_markdown_table_cell(candidate.candidate_id)}"
            " | "
            f"{AgentResultValidator._escape_markdown_table_cell(candidate.canonical_smiles)}"
            " |"
            for candidate in candidate_set.candidates
        )
        return "\n".join(lines)

    @staticmethod
    def _escape_markdown_table_cell(value: Any) -> str:
        return (
            str(value)
            .replace("\r", " ")
            .replace("\n", " ")
            .replace("|", "\\|")
            .replace(chr(96), "\\" + chr(96))
        )

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
