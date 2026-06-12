from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.contracts import ToolResult


class AgentResultValidator:
    """Validate common agent tool outputs without blocking partial success."""

    def validate_tool_result(self, result: ToolResult) -> ToolResult:
        if not result.success:
            return result

        warnings = list(result.warnings)
        warnings.extend(self._validate_smiles_payload(result.data))
        warnings.extend(self._validate_file_payload(result.data))
        result.warnings = warnings
        result.quality = {**result.quality, "validated": not bool(warnings)}
        return result

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
