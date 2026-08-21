"""Fail-closed rollout gates for the Temporal production canary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
from types import MappingProxyType


ALLOWED_LEVELS = (0, 5, 10, 25)
MAX_EVIDENCE_AGE = timedelta(hours=1)

_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "stage",
        "status",
        "current_level",
        "generated_at",
        "gates",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STAGES = frozenset({"preflight", "observation"})
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))
_PROVENANCE_KEY = secrets.token_bytes(32)
_CONTENT_SEAL_ATTRIBUTE = "_validated_content_seal"
_SOURCE_TIMESTAMP_ATTRIBUTE = "_validated_source_timestamp"
_PROMOTIONS = {
    (0, 5): "preflight",
    (5, 10): "observation",
    (10, 25): "observation",
}


class EvidenceError(ValueError):
    """A non-sensitive rollout evidence or transition failure."""


@dataclass(frozen=True)
class RolloutEvidence:
    schema_version: int
    stage: str
    status: str
    current_level: int
    generated_at: datetime
    gates: Mapping[str, str]
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "gates", MappingProxyType(dict(self.gates)))

    @classmethod
    def from_payload(
        cls,
        payload: object,
        expected_sha256: str,
    ) -> "RolloutEvidence":
        if type(payload) is not dict:
            raise EvidenceError("invalid evidence payload")
        if type(expected_sha256) is not str or _SHA256.fullmatch(expected_sha256) is None:
            raise EvidenceError("invalid evidence hash")

        try:
            canonical = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise EvidenceError("invalid evidence payload") from None

        actual_sha256 = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise EvidenceError("evidence hash mismatch")
        if set(payload) != _EVIDENCE_FIELDS:
            raise EvidenceError("invalid evidence payload")

        schema_version = payload["schema_version"]
        if type(schema_version) is not int or schema_version != 1:
            raise EvidenceError("invalid evidence schema")

        status = payload["status"]
        if type(status) is not str or status != "passed":
            raise EvidenceError("evidence is not passed")

        stage = payload["stage"]
        if type(stage) is not str or stage not in _STAGES:
            raise EvidenceError("invalid evidence stage")

        current_level = payload["current_level"]
        if type(current_level) is not int or current_level not in ALLOWED_LEVELS:
            raise EvidenceError("invalid evidence level")

        generated_at = payload["generated_at"]
        if type(generated_at) is not str:
            raise EvidenceError("invalid evidence timestamp")
        try:
            generated = datetime.fromisoformat(generated_at)
        except ValueError:
            raise EvidenceError("invalid evidence timestamp") from None
        if generated.tzinfo is None or generated.utcoffset() is None:
            raise EvidenceError("evidence timestamp must be timezone-aware")

        gates = payload["gates"]
        if type(gates) is not dict or not all(
            type(key) is str and type(value) is str
            for key, value in gates.items()
        ):
            raise EvidenceError("invalid evidence gates")
        if gates.get("all_required_gates") != "passed":
            raise EvidenceError("required gates did not pass")

        evidence = cls(
            schema_version=1,
            stage=stage,
            status="passed",
            current_level=current_level,
            generated_at=generated,
            gates=gates,
            sha256=actual_sha256,
        )
        _seal_validated_evidence(evidence, generated_at, canonical)
        return evidence


def _validated_public_snapshot(evidence: RolloutEvidence) -> dict[str, object]:
    if type(evidence.schema_version) is not int or evidence.schema_version != 1:
        raise EvidenceError("rollout evidence is not trusted")
    if type(evidence.stage) is not str or evidence.stage not in _STAGES:
        raise EvidenceError("rollout evidence is not trusted")
    if type(evidence.status) is not str or evidence.status != "passed":
        raise EvidenceError("rollout evidence is not trusted")
    if (
        type(evidence.current_level) is not int
        or evidence.current_level not in ALLOWED_LEVELS
    ):
        raise EvidenceError("rollout evidence is not trusted")
    if type(evidence.generated_at) is not datetime:
        raise EvidenceError("rollout evidence is not trusted")
    if (
        evidence.generated_at.tzinfo is None
        or evidence.generated_at.utcoffset() is None
    ):
        raise EvidenceError("rollout evidence is not trusted")
    if type(evidence.gates) is not _MAPPING_PROXY_TYPE or not all(
        type(key) is str and type(value) is str
        for key, value in evidence.gates.items()
    ):
        raise EvidenceError("rollout evidence is not trusted")
    if evidence.gates.get("all_required_gates") != "passed":
        raise EvidenceError("rollout evidence is not trusted")
    if type(evidence.sha256) is not str or _SHA256.fullmatch(evidence.sha256) is None:
        raise EvidenceError("rollout evidence is not trusted")

    return {
        "schema_version": evidence.schema_version,
        "stage": evidence.stage,
        "status": evidence.status,
        "current_level": evidence.current_level,
        "generated_at": evidence.generated_at.isoformat(),
        "gates": dict(evidence.gates),
        "sha256": evidence.sha256,
    }


def _report_payload(
    evidence: RolloutEvidence,
    source_timestamp: object,
) -> dict[str, object]:
    if type(source_timestamp) is not str:
        raise EvidenceError("rollout evidence is not trusted")
    try:
        parsed_source = datetime.fromisoformat(source_timestamp)
    except ValueError:
        raise EvidenceError("rollout evidence is not trusted") from None
    if parsed_source.tzinfo is None or parsed_source.utcoffset() is None:
        raise EvidenceError("rollout evidence is not trusted")
    if parsed_source.isoformat() != evidence.generated_at.isoformat():
        raise EvidenceError("rollout evidence is not trusted")

    return {
        "schema_version": evidence.schema_version,
        "stage": evidence.stage,
        "status": evidence.status,
        "current_level": evidence.current_level,
        "generated_at": source_timestamp,
        "gates": dict(evidence.gates),
    }


def _canonical_json(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise EvidenceError("rollout evidence is not trusted") from None


def _provenance_message(report: bytes, public_snapshot: bytes) -> bytes:
    return len(report).to_bytes(8, "big") + report + public_snapshot


def _seal_validated_evidence(
    evidence: RolloutEvidence,
    source_timestamp: str,
    canonical_report: bytes,
) -> None:
    public_snapshot = _canonical_json(_validated_public_snapshot(evidence))
    seal = hmac.new(
        _PROVENANCE_KEY,
        _provenance_message(canonical_report, public_snapshot),
        hashlib.sha256,
    ).hexdigest()
    object.__setattr__(evidence, _SOURCE_TIMESTAMP_ATTRIBUTE, source_timestamp)
    object.__setattr__(evidence, _CONTENT_SEAL_ATTRIBUTE, seal)


def _verify_evidence_provenance(evidence: RolloutEvidence) -> None:
    public_snapshot = _canonical_json(_validated_public_snapshot(evidence))
    source_timestamp = getattr(evidence, _SOURCE_TIMESTAMP_ATTRIBUTE, None)
    content_seal = getattr(evidence, _CONTENT_SEAL_ATTRIBUTE, None)
    if type(content_seal) is not str or _SHA256.fullmatch(content_seal) is None:
        raise EvidenceError("rollout evidence is not trusted")

    canonical_report = _canonical_json(_report_payload(evidence, source_timestamp))
    actual_sha256 = hashlib.sha256(canonical_report).hexdigest()
    if not hmac.compare_digest(actual_sha256, evidence.sha256):
        raise EvidenceError("rollout evidence is not trusted")

    expected_seal = hmac.new(
        _PROVENANCE_KEY,
        _provenance_message(canonical_report, public_snapshot),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_seal, content_seal):
        raise EvidenceError("rollout evidence is not trusted")


@dataclass(frozen=True)
class RolloutDecision:
    allowed: bool
    reason: str
    current_level: int
    target_level: int


def validate_transition(
    current_level: int,
    target_level: int,
    evidence: RolloutEvidence | None,
    *,
    now: datetime | None = None,
) -> RolloutDecision:
    """Validate one rollout state transition and its supporting evidence."""

    if (
        type(current_level) is not int
        or current_level not in ALLOWED_LEVELS
        or type(target_level) is not int
        or target_level not in ALLOWED_LEVELS
    ):
        raise EvidenceError("invalid rollout level")
    if current_level == target_level:
        raise EvidenceError("same-level rollout is not allowed")
    if target_level == 0 and current_level != 0:
        return RolloutDecision(True, "rollback_to_zero", current_level, target_level)

    required_stage = _PROMOTIONS.get((current_level, target_level))
    if required_stage is None:
        raise EvidenceError("invalid rollout transition")
    if type(evidence) is not RolloutEvidence:
        raise EvidenceError("rollout evidence is required")
    _verify_evidence_provenance(evidence)
    if evidence.current_level != current_level:
        raise EvidenceError("rollout evidence level mismatch")
    if evidence.stage != required_stage:
        raise EvidenceError("rollout evidence stage mismatch")

    checked_at = datetime.now(timezone.utc) if now is None else now
    if not isinstance(checked_at, datetime):
        raise EvidenceError("invalid rollout clock")
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise EvidenceError("rollout clock must be timezone-aware")
    if evidence.generated_at > checked_at:
        raise EvidenceError("rollout evidence is from the future")
    if checked_at - evidence.generated_at > MAX_EVIDENCE_AGE:
        raise EvidenceError("rollout evidence is stale")

    return RolloutDecision(True, "evidence_passed", current_level, target_level)


__all__ = [
    "ALLOWED_LEVELS",
    "MAX_EVIDENCE_AGE",
    "EvidenceError",
    "RolloutDecision",
    "RolloutEvidence",
    "validate_transition",
]
