from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from .scientific import ObservationStatus


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def build_candidate_id(candidate_index: int, canonical_smiles: str) -> str:
    _require_positive_int("candidate_index", candidate_index)
    if not isinstance(canonical_smiles, str) or not canonical_smiles.strip():
        raise ValueError("canonical_smiles cannot be empty")
    digest = sha256(canonical_smiles.encode("utf-8")).hexdigest()[:8]
    return f"cand-{candidate_index:03d}-{digest}"


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    source_index: int
    original_smiles: str
    canonical_smiles: str
    validation: Mapping[str, Any]
    generation_provenance: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "validation", _freeze_json(self.validation))
        object.__setattr__(
            self,
            "generation_provenance",
            _freeze_json(self.generation_provenance),
        )
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))

    @classmethod
    def from_smiles(
        cls,
        candidate_index: int,
        source_index: int,
        original_smiles: str,
        canonical_smiles: str,
        generation_provenance: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CandidateRecord:
        """Build from SMILES already validated and canonicalized by RDKit."""
        _require_positive_int("source_index", source_index)
        return cls(
            candidate_id=build_candidate_id(candidate_index, canonical_smiles),
            source_index=source_index,
            original_smiles=original_smiles,
            canonical_smiles=canonical_smiles,
            validation={"valid": True, "method": "RDKit"},
            generation_provenance=(
                generation_provenance if generation_provenance is not None else {}
            ),
            metadata=metadata if metadata is not None else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_index": self.source_index,
            "original_smiles": self.original_smiles,
            "canonical_smiles": self.canonical_smiles,
            "validation": _thaw_json(self.validation),
            "generation_provenance": _thaw_json(self.generation_provenance),
            "metadata": _thaw_json(self.metadata),
            "smiles": self.canonical_smiles,
        }


@dataclass(frozen=True)
class CandidateSet:
    requested_count: int
    candidates: tuple[CandidateRecord, ...]
    invalid_count: int = 0
    duplicate_count: int = 0
    rejected: tuple[Mapping[str, Any], ...] = ()
    status: ObservationStatus = ObservationStatus.SUCCEEDED
    version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        _require_positive_int("requested_count", self.requested_count)
        _require_non_negative_int("invalid_count", self.invalid_count)
        _require_non_negative_int("duplicate_count", self.duplicate_count)

        candidates = tuple(self.candidates)
        rejected = tuple(_freeze_json(item) for item in self.rejected)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "rejected", rejected)

        if len(candidates) > self.requested_count:
            raise ValueError("candidate count cannot exceed requested_count")

        candidate_ids = [candidate.candidate_id for candidate in candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate_id values must be unique")

        canonical_smiles = [candidate.canonical_smiles for candidate in candidates]
        if len(set(canonical_smiles)) != len(canonical_smiles):
            raise ValueError("canonical_smiles values must be unique")

        valid_count = len(candidates)
        if self.status == ObservationStatus.SUCCEEDED:
            if valid_count == 0 or valid_count != self.requested_count:
                raise ValueError(
                    "succeeded candidate sets require all requested candidates"
                )
        elif self.status == ObservationStatus.PARTIAL:
            if not 0 < valid_count < self.requested_count:
                raise ValueError(
                    "partial candidate sets require some but not all candidates"
                )

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
            "rejected": _thaw_json(self.rejected),
            "status": self.status.value,
        }
