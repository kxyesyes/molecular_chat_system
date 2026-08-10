from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from typing import Any

from .scientific import ObservationStatus


_CANDIDATE_ID_PATTERN = re.compile(r"^cand-(\d+)-([0-9a-f]{8})$")
_REJECTED_REASONS = {
    "invalid_smiles",
    "invalid_candidate_metadata",
    "duplicate_smiles",
    "excess_candidate",
}


def _freeze_json(value: Any, active_containers: set[int] | None = None) -> Any:
    if active_containers is None:
        active_containers = set()

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError("JSON values cannot contain circular references")
        active_containers.add(container_id)
        try:
            frozen = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("JSON mapping keys must be strings")
                frozen[key] = _freeze_json(item, active_containers)
            return MappingProxyType(frozen)
        finally:
            active_containers.remove(container_id)

    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError("JSON values cannot contain circular references")
        active_containers.add(container_id)
        try:
            return tuple(_freeze_json(item, active_containers) for item in value)
        finally:
            active_containers.remove(container_id)

    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("JSON float values must be finite")
        return value
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")


def _freeze_json_mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON mapping")
    return _freeze_json(value)


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


def _require_non_empty_str(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")


def _require_exact_mapping(
    name: str,
    value: Any,
    expected_fields: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    actual_fields = set(value)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        unexpected = sorted(actual_fields - expected_fields)
        raise ValueError(
            f"{name} fields do not match contract; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return value


def build_candidate_id(candidate_index: int, canonical_smiles: str) -> str:
    _require_positive_int("candidate_index", candidate_index)
    _require_non_empty_str("canonical_smiles", canonical_smiles)
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
        _require_positive_int("source_index", self.source_index)
        _require_non_empty_str("original_smiles", self.original_smiles)
        _require_non_empty_str("canonical_smiles", self.canonical_smiles)

        match = (
            _CANDIDATE_ID_PATTERN.fullmatch(self.candidate_id)
            if isinstance(self.candidate_id, str)
            else None
        )
        if match is None:
            raise ValueError("candidate_id has an invalid format")
        candidate_index = int(match.group(1))
        _require_positive_int("candidate index in candidate_id", candidate_index)
        expected_id = build_candidate_id(candidate_index, self.canonical_smiles)
        if self.candidate_id != expected_id:
            raise ValueError("candidate_id does not match canonical_smiles")

        if (
            not isinstance(self.validation, Mapping)
            or set(self.validation) != {"valid", "method"}
            or self.validation.get("valid") is not True
            or self.validation.get("method") != "RDKit"
        ):
            raise ValueError(
                "validation must be exactly {'valid': True, 'method': 'RDKit'}"
            )

        object.__setattr__(
            self,
            "validation",
            _freeze_json_mapping("validation", self.validation),
        )
        object.__setattr__(
            self,
            "generation_provenance",
            _freeze_json_mapping(
                "generation_provenance",
                self.generation_provenance,
            ),
        )
        object.__setattr__(
            self,
            "metadata",
            _freeze_json_mapping("metadata", self.metadata),
        )

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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CandidateRecord:
        """Strictly restore a serialized CandidateRecord."""
        payload = _require_exact_mapping(
            "candidate record",
            value,
            {
                "candidate_id",
                "source_index",
                "original_smiles",
                "canonical_smiles",
                "validation",
                "generation_provenance",
                "metadata",
                "smiles",
            },
        )
        canonical_smiles = payload["canonical_smiles"]
        if not isinstance(payload["smiles"], str):
            raise ValueError("smiles must be a string")
        if payload["smiles"] != canonical_smiles:
            raise ValueError("smiles must match canonical_smiles")
        return cls(
            candidate_id=payload["candidate_id"],
            source_index=payload["source_index"],
            original_smiles=payload["original_smiles"],
            canonical_smiles=canonical_smiles,
            validation=payload["validation"],
            generation_provenance=payload["generation_provenance"],
            metadata=payload["metadata"],
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
        if not isinstance(self.status, ObservationStatus):
            raise ValueError("status must be an ObservationStatus")
        _require_non_negative_int("requested_count", self.requested_count)
        _require_non_negative_int("invalid_count", self.invalid_count)
        _require_non_negative_int("duplicate_count", self.duplicate_count)

        try:
            candidates = tuple(self.candidates)
        except TypeError as exc:
            raise ValueError(
                "candidates must be an iterable of CandidateRecord"
            ) from exc
        if any(not isinstance(candidate, CandidateRecord) for candidate in candidates):
            raise ValueError("candidates must contain only CandidateRecord values")

        try:
            rejected_items = tuple(self.rejected)
        except TypeError as exc:
            raise ValueError("rejected must be an iterable of JSON mappings") from exc
        rejected = tuple(
            _freeze_json_mapping("rejected item", item) for item in rejected_items
        )
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

        for candidate_index, candidate in enumerate(candidates, start=1):
            expected_id = build_candidate_id(
                candidate_index,
                candidate.canonical_smiles,
            )
            if candidate.candidate_id != expected_id:
                raise ValueError(
                    "candidate_id indexes must follow candidate order from 1"
                )

        invalid_reasons = 0
        duplicate_reasons = 0
        rejected_source_indexes: list[int] = []
        for item in rejected:
            source_index = item.get("source_index")
            smiles = item.get("smiles")
            reason = item.get("reason")
            _require_positive_int("rejected source_index", source_index)
            if reason not in _REJECTED_REASONS:
                raise ValueError("rejected reason is not allowed")
            if not isinstance(smiles, str):
                raise ValueError("rejected smiles must be a string")
            if reason != "invalid_smiles" and not smiles.strip():
                raise ValueError("rejected smiles cannot be empty")
            rejected_source_indexes.append(source_index)
            if reason in {"invalid_smiles", "invalid_candidate_metadata"}:
                invalid_reasons += 1
            elif reason == "duplicate_smiles":
                duplicate_reasons += 1

        if self.invalid_count != invalid_reasons:
            raise ValueError("invalid_count does not match rejected reasons")
        if self.duplicate_count != duplicate_reasons:
            raise ValueError("duplicate_count does not match rejected reasons")

        source_indexes = [
            candidate.source_index for candidate in candidates
        ] + rejected_source_indexes
        if (
            len(set(source_indexes)) != len(source_indexes)
            or set(source_indexes) != set(range(1, len(source_indexes) + 1))
        ):
            raise ValueError(
                "source_index values must be unique and contiguous from 1"
            )

        valid_count = len(candidates)
        if self.status in (ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL):
            if self.requested_count == 0:
                raise ValueError(
                    "succeeded and partial candidate sets require requested candidates"
                )

        if self.status == ObservationStatus.SUCCEEDED:
            if valid_count != self.requested_count:
                raise ValueError(
                    "succeeded candidate sets require all requested candidates"
                )
        elif self.status == ObservationStatus.PARTIAL:
            if not 0 < valid_count < self.requested_count:
                raise ValueError(
                    "partial candidate sets require some but not all candidates"
                )
        elif candidates:
            raise ValueError("non-success candidate sets cannot contain candidates")

    @property
    def valid_count(self) -> int:
        return len(self.candidates)

    @property
    def unique_count(self) -> int:
        return len(self.candidates)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CandidateSet:
        """Strictly restore a version 1 serialized CandidateSet."""
        payload = _require_exact_mapping(
            "candidate set",
            value,
            {
                "version",
                "requested_count",
                "valid_count",
                "unique_count",
                "invalid_count",
                "duplicate_count",
                "candidates",
                "rejected",
                "status",
            },
        )
        if payload["version"] != "1":
            raise ValueError("candidate set version must be '1'")
        for field_name in (
            "requested_count",
            "valid_count",
            "unique_count",
            "invalid_count",
            "duplicate_count",
        ):
            _require_non_negative_int(field_name, payload[field_name])
        if not isinstance(payload["candidates"], list):
            raise ValueError("candidates must be a list")
        if not isinstance(payload["rejected"], list):
            raise ValueError("rejected must be a list")
        if not isinstance(payload["status"], str):
            raise ValueError("status must be a string")
        try:
            status = ObservationStatus(payload["status"])
        except ValueError as exc:
            raise ValueError("status is not a valid ObservationStatus") from exc

        candidates = tuple(
            CandidateRecord.from_dict(candidate)
            for candidate in payload["candidates"]
        )
        actual_count = len(candidates)
        if payload["valid_count"] != actual_count:
            raise ValueError("valid_count does not match candidates")
        if payload["unique_count"] != actual_count:
            raise ValueError("unique_count does not match candidates")
        return cls(
            requested_count=payload["requested_count"],
            candidates=candidates,
            invalid_count=payload["invalid_count"],
            duplicate_count=payload["duplicate_count"],
            rejected=tuple(payload["rejected"]),
            status=status,
        )

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
