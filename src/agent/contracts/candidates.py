from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .scientific import ObservationStatus


def build_candidate_id(candidate_index: int, canonical_smiles: str) -> str:
    digest = sha256(canonical_smiles.encode("utf-8")).hexdigest()[:8]
    return f"cand-{candidate_index:03d}-{digest}"


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    source_index: int
    original_smiles: str
    canonical_smiles: str
    validation: dict[str, Any]
    generation_provenance: dict[str, Any]
    metadata: dict[str, Any]

    @classmethod
    def from_smiles(
        cls,
        candidate_index: int,
        source_index: int,
        original_smiles: str,
        canonical_smiles: str,
        generation_provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CandidateRecord:
        return cls(
            candidate_id=build_candidate_id(candidate_index, canonical_smiles),
            source_index=source_index,
            original_smiles=original_smiles,
            canonical_smiles=canonical_smiles,
            validation={"valid": True, "method": "RDKit"},
            generation_provenance=dict(generation_provenance or {}),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_index": self.source_index,
            "original_smiles": self.original_smiles,
            "canonical_smiles": self.canonical_smiles,
            "validation": self.validation,
            "generation_provenance": self.generation_provenance,
            "metadata": self.metadata,
            "smiles": self.canonical_smiles,
        }


@dataclass(frozen=True)
class CandidateSet:
    requested_count: int
    candidates: tuple[CandidateRecord, ...]
    invalid_count: int = 0
    duplicate_count: int = 0
    rejected: tuple[dict[str, Any], ...] = ()
    status: ObservationStatus = ObservationStatus.SUCCEEDED
    version: str = "1"

    @property
    def valid_count(self) -> int:
        return len(self.candidates)

    @property
    def unique_count(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "requested_count": self.requested_count,
            "valid_count": self.valid_count,
            "unique_count": self.unique_count,
            "invalid_count": self.invalid_count,
            "duplicate_count": self.duplicate_count,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "rejected": list(self.rejected),
            "status": self.status.value,
        }
