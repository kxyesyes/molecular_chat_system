"""Fail-closed rollout gates for the Temporal production canary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
import secrets
from types import MappingProxyType


ALLOWED_LEVELS = (0, 5, 10, 25)
MAX_EVIDENCE_AGE = timedelta(hours=1)

MAX_EVIDENCE_DEPTH = 32
MAX_EVIDENCE_NODES = 100_000
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024

_COMMON_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "stage",
        "status",
        "current_level",
        "generated_at",
        "gates",
    }
)
# Planned report sections stay explicit here. Add future report fields to the
# relevant stage allowlist so unknown top-level data continues to fail closed.
_OBSERVATION_REPORT_FIELDS = frozenset(
    {
        "window_start",
        "window_end",
        "sample_count",
        "counts",
        "rates",
        "latency_seconds",
        "error_code_distribution",
        "worker_health",
        "infrastructure",
        "blocking_alerts",
        "backup_verified",
        "tasks",
    }
)
_PREFLIGHT_REPORT_FIELDS = frozenset(
    {
        "deployment",
        "contract",
        "real",
        "infrastructure",
        "blocking_alerts",
        "backup",
    }
)
_STAGE_EVIDENCE_FIELDS = {
    "observation": _COMMON_EVIDENCE_FIELDS | _OBSERVATION_REPORT_FIELDS,
    "preflight": _COMMON_EVIDENCE_FIELDS | _PREFLIGHT_REPORT_FIELDS,
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STAGES = frozenset({"preflight", "observation"})
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))
_PROVENANCE_KEY = secrets.token_bytes(32)
_CANONICAL_PAYLOAD_ATTRIBUTE = "_validated_canonical_payload"
_CONTENT_SEAL_ATTRIBUTE = "_validated_content_seal"
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
            _validate_top_level_shape(payload)
            _validate_json_structure(payload)
            canonical = _canonical_payload(payload)
        except EvidenceError:
            raise
        except (MemoryError, OverflowError, RecursionError):
            raise EvidenceError("invalid evidence payload") from None

        actual_sha256 = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise EvidenceError("evidence hash mismatch")

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
        _seal_validated_evidence(evidence, canonical)
        return evidence


def _validate_top_level_shape(payload: dict[object, object]) -> None:
    if not all(type(key) is str for key in payload):
        raise EvidenceError("invalid evidence payload")
    if not _COMMON_EVIDENCE_FIELDS.issubset(payload):
        raise EvidenceError("invalid evidence payload")
    stage = payload.get("stage")
    if type(stage) is not str or stage not in _STAGE_EVIDENCE_FIELDS:
        raise EvidenceError("invalid evidence payload")
    if not set(payload).issubset(_STAGE_EVIDENCE_FIELDS[stage]):
        raise EvidenceError("invalid evidence payload")


def _validate_json_structure(payload: object) -> None:
    frames: list[tuple[object, int, int]] = []
    active_containers: set[int] = set()
    nodes = 1
    raw_string_bytes = 0
    value = payload
    depth = 0

    while True:
        if nodes > MAX_EVIDENCE_NODES or depth > MAX_EVIDENCE_DEPTH:
            raise EvidenceError("invalid evidence payload")

        value_type = type(value)
        if value_type is dict:
            child_count = len(value)
            direct_nodes = child_count * 2
            if direct_nodes > MAX_EVIDENCE_NODES - nodes:
                raise EvidenceError("invalid evidence payload")
            if child_count and depth >= MAX_EVIDENCE_DEPTH:
                raise EvidenceError("invalid evidence payload")
            identity = id(value)
            if identity in active_containers:
                raise EvidenceError("invalid evidence payload")
            for key in value:
                if type(key) is not str:
                    raise EvidenceError("invalid evidence payload")
                try:
                    raw_string_bytes += len(key.encode("utf-8"))
                except (MemoryError, UnicodeError):
                    raise EvidenceError("invalid evidence payload") from None
                if raw_string_bytes > MAX_EVIDENCE_BYTES:
                    raise EvidenceError("invalid evidence payload")
            nodes += direct_nodes
            active_containers.add(identity)
            frames.append((iter(value.values()), depth + 1, identity))
        elif value_type is list:
            child_count = len(value)
            if child_count > MAX_EVIDENCE_NODES - nodes:
                raise EvidenceError("invalid evidence payload")
            if child_count and depth >= MAX_EVIDENCE_DEPTH:
                raise EvidenceError("invalid evidence payload")
            identity = id(value)
            if identity in active_containers:
                raise EvidenceError("invalid evidence payload")
            nodes += child_count
            active_containers.add(identity)
            frames.append((iter(value), depth + 1, identity))
        elif value_type is str:
            try:
                raw_string_bytes += len(value.encode("utf-8"))
            except (MemoryError, UnicodeError):
                raise EvidenceError("invalid evidence payload") from None
            if raw_string_bytes > MAX_EVIDENCE_BYTES:
                raise EvidenceError("invalid evidence payload")
        elif value is None or value_type in {bool, int}:
            pass
        elif value_type is float and math.isfinite(value):
            pass
        else:
            raise EvidenceError("invalid evidence payload")

        while frames:
            iterator, child_depth, container_id = frames[-1]
            try:
                value = next(iterator)
                depth = child_depth
                break
            except StopIteration:
                frames.pop()
                active_containers.remove(container_id)
        else:
            return


def _canonical_payload(payload: object) -> bytes:
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (
        MemoryError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise EvidenceError("invalid evidence payload") from None
    if len(canonical) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("invalid evidence payload")
    return canonical


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
    canonical_report: bytes,
) -> None:
    public_snapshot = _canonical_json(_validated_public_snapshot(evidence))
    seal = hmac.new(
        _PROVENANCE_KEY,
        _provenance_message(canonical_report, public_snapshot),
        hashlib.sha256,
    ).hexdigest()
    object.__setattr__(evidence, _CANONICAL_PAYLOAD_ATTRIBUTE, canonical_report)
    object.__setattr__(evidence, _CONTENT_SEAL_ATTRIBUTE, seal)


def _payload_matches_public_evidence(
    payload: dict[object, object],
    evidence: RolloutEvidence,
) -> bool:
    generated_at = payload.get("generated_at")
    if type(generated_at) is not str:
        return False
    try:
        parsed_generated_at = datetime.fromisoformat(generated_at)
    except ValueError:
        return False
    if (
        parsed_generated_at.tzinfo is None
        or parsed_generated_at.utcoffset() is None
        or parsed_generated_at.isoformat() != evidence.generated_at.isoformat()
    ):
        return False
    gates = payload.get("gates")
    return (
        type(payload.get("schema_version")) is int
        and payload["schema_version"] == evidence.schema_version
        and type(payload.get("stage")) is str
        and payload["stage"] == evidence.stage
        and type(payload.get("status")) is str
        and payload["status"] == evidence.status
        and type(payload.get("current_level")) is int
        and payload["current_level"] == evidence.current_level
        and type(gates) is dict
        and gates == dict(evidence.gates)
    )


def _verify_evidence_provenance(evidence: RolloutEvidence) -> None:
    public_snapshot = _canonical_json(_validated_public_snapshot(evidence))
    canonical_report = getattr(evidence, _CANONICAL_PAYLOAD_ATTRIBUTE, None)
    content_seal = getattr(evidence, _CONTENT_SEAL_ATTRIBUTE, None)
    if (
        type(canonical_report) is not bytes
        or len(canonical_report) > MAX_EVIDENCE_BYTES
        or type(content_seal) is not str
        or _SHA256.fullmatch(content_seal) is None
    ):
        raise EvidenceError("rollout evidence is not trusted")

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

    try:
        payload = json.loads(canonical_report)
        if type(payload) is not dict:
            raise EvidenceError("rollout evidence is not trusted")
        _validate_top_level_shape(payload)
        _validate_json_structure(payload)
    except EvidenceError:
        raise EvidenceError("rollout evidence is not trusted") from None
    except (
        json.JSONDecodeError,
        MemoryError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise EvidenceError("rollout evidence is not trusted") from None
    if not _payload_matches_public_evidence(payload, evidence):
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
