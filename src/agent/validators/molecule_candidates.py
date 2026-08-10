from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CandidateValidationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateSanitization:
    candidates: list[dict[str, Any]]
    invalid_count: int
    duplicate_count: int

    @property
    def valid_count(self) -> int:
        return len(self.candidates)


def sanitize_generated_candidates(data: Any) -> CandidateSanitization:
    try:
        from rdkit import Chem
    except Exception as exc:
        raise CandidateValidationUnavailable(
            "RDKit is required to validate generated molecules"
        ) from exc

    raw = data
    if isinstance(data, dict):
        raw = (
            data.get("molecules")
            or data.get("candidates")
            or data.get("data")
            or []
        )
    if not isinstance(raw, list):
        raw = []

    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid_count = 0
    duplicate_count = 0
    for item in raw:
        candidate = dict(item) if isinstance(item, dict) else {"smiles": str(item)}
        smiles = str(candidate.get("smiles") or "").strip()
        mol = Chem.MolFromSmiles(smiles) if smiles else None
        if mol is None:
            invalid_count += 1
            continue
        canonical = Chem.MolToSmiles(mol)
        if canonical in seen:
            duplicate_count += 1
            continue
        seen.add(canonical)
        candidate["smiles"] = canonical
        candidate["validation"] = {"valid": True, "method": "RDKit"}
        accepted.append(candidate)

    return CandidateSanitization(
        candidates=accepted,
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
    )
