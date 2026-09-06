from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agent.contracts import CandidateRecord, CandidateSet, ObservationStatus


class CandidateValidationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateSanitization:
    """Internal immutable result produced by generated-candidate sanitization."""

    candidate_set: CandidateSet

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return [candidate.to_dict() for candidate in self.candidate_set.candidates]

    @property
    def valid_count(self) -> int:
        return self.candidate_set.valid_count

    @property
    def invalid_count(self) -> int:
        return self.candidate_set.invalid_count

    @property
    def duplicate_count(self) -> int:
        return self.candidate_set.duplicate_count


def sanitize_generated_candidates(
    data: Any,
    requested_count: int | None = None,
    generation_provenance: dict[str, Any] | None = None,
) -> CandidateSanitization:
    raw = _extract_candidate_items(data)

    if requested_count is None:
        requested = len(raw)
    else:
        if isinstance(requested_count, bool) or not isinstance(requested_count, int):
            raise TypeError("requested_count must be a non-negative integer")
        if requested_count < 0:
            raise ValueError("requested_count must be a non-negative integer")
        requested = requested_count

    try:
        from rdkit import Chem
    except Exception as exc:
        raise CandidateValidationUnavailable(
            "RDKit is required to validate generated molecules"
        ) from exc

    accepted: list[CandidateRecord] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid_count = 0
    duplicate_count = 0
    for source_index, item in enumerate(raw, start=1):
        if isinstance(item, dict):
            smiles = str(item.get("smiles") or "").strip()
            metadata = {key: value for key, value in item.items() if key != "smiles"}
        else:
            smiles = str(item or "").strip()
            metadata = {}

        mol = None
        try:
            mol = Chem.MolFromSmiles(smiles) if smiles else None
            canonical = Chem.MolToSmiles(mol) if mol is not None else None
        except Exception:
            mol = None
            canonical = None
        if mol is None:
            invalid_count += 1
            rejected.append(
                {
                    "source_index": source_index,
                    "smiles": smiles,
                    "reason": "invalid_smiles",
                }
            )
            continue
        assert canonical is not None
        if canonical in seen:
            duplicate_count += 1
            rejected.append(
                {
                    "source_index": source_index,
                    "smiles": smiles,
                    "reason": "duplicate_smiles",
                }
            )
            continue

        try:
            candidate = CandidateRecord.from_smiles(
                candidate_index=len(accepted) + 1,
                source_index=source_index,
                original_smiles=smiles,
                canonical_smiles=canonical,
                generation_provenance=generation_provenance,
                metadata=metadata,
            )
        except ValueError:
            invalid_count += 1
            rejected.append(
                {
                    "source_index": source_index,
                    "smiles": smiles,
                    "reason": "invalid_candidate_metadata",
                }
            )
            continue

        seen.add(canonical)
        if len(accepted) >= requested:
            rejected.append(
                {
                    "source_index": source_index,
                    "smiles": smiles,
                    "reason": "excess_candidate",
                }
            )
            continue
        accepted.append(candidate)

    if requested > 0 and len(accepted) == requested:
        status = ObservationStatus.SUCCEEDED
    elif accepted:
        status = ObservationStatus.PARTIAL
    else:
        status = ObservationStatus.FAILED

    return CandidateSanitization(
        candidate_set=CandidateSet(
            requested_count=requested,
            candidates=tuple(accepted),
            invalid_count=invalid_count,
            duplicate_count=duplicate_count,
            rejected=tuple(rejected),
            status=status,
        )
    )


def _extract_candidate_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    first_empty: list[Any] | None = None
    for key in ("molecules", "candidates", "data"):
        value = data.get(key)
        if not isinstance(value, list):
            continue
        if value:
            return value
        if first_empty is None:
            first_empty = value
    return first_empty if first_empty is not None else []
