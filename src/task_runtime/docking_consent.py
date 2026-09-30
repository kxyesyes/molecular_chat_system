"""Closed consent values. Only durable claim and lease-local reservation authorize execution."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any

from .staging import (
    MAX_DOCKING_INPUT_BYTES,
    ManifestError,
    _normalize_config,
    _portable_name_key,
    _validate_input_name,
    _validate_task_id,
)


CONSENT_TTL_MS = 15 * 60 * 1000
PREPARATION_SECONDS = 30.0
MAX_PENDING_CONSENTS = 16
_REASONS = frozenset({
    "consent_not_found", "consent_revision_conflict", "consent_expired",
    "consent_invalid_input", "consent_not_waiting", "consent_policy_unavailable",
    "consent_owner_busy", "consent_capacity_full", "consent_identity_conflict",
    "consent_preparation_timeout", "consent_cleanup_unresolved",
    "consent_proof_mismatch", "consent_binding_mismatch", "consent_generation_mismatch",
    "consent_execution_busy", "consent_execution_unknown", "consent_persistence_unavailable",
    "consent_cancelled", "consent_execution_timeout", "consent_execution_failed",
})


class DockingConsentError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = (
            reason_code if type(reason_code) is str and reason_code in _REASONS
            else "consent_invalid_input"
        )
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class DockingConsentPolicy:
    tool_policy_digest: str
    adapter_contract_version: str
    execution_backend: str
    policy_generation: str
    runtime_generation: str
    vina_limit_seconds: int
    operation_limit_seconds: int

    def __post_init__(self) -> None:
        labels = (self.adapter_contract_version, self.policy_generation,
                  self.runtime_generation)
        if (
            type(self.tool_policy_digest) is not str
            or re.fullmatch(r"[a-f0-9]{64}", self.tool_policy_digest) is None
            or any(type(value) is not str or not value for value in labels)
            or type(self.execution_backend) is not str or self.execution_backend != "local"
            or type(self.vina_limit_seconds) is not int
            or not 0 < self.vina_limit_seconds <= 300
            or type(self.operation_limit_seconds) is not int
            or not self.vina_limit_seconds <= self.operation_limit_seconds <= 420
        ):
            raise DockingConsentError("consent_policy_unavailable")


@dataclass(frozen=True, repr=False)
class DockingConsentPreview:
    # Immutable JSON bytes prevent nested aliasing; never include the nonce in repr.
    _json: bytes = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    def __repr__(self) -> str:
        return "DockingConsentPreview(<private>)"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def validate_approval(nonce, digest, confirm) -> None:
    if confirm is not True or any(
        type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None
        for value in (nonce, digest)
    ):
        raise DockingConsentError("consent_invalid_input")


def verify_approval(row, nonce, digest) -> None:
    if row["state"] in {"REVOKED", "EXPIRED"}:
        raise DockingConsentError(
            "consent_expired" if row["state"] == "EXPIRED" else "consent_not_waiting"
        )
    if (not hmac.compare_digest(row["binding_digest"] or "", digest)
            or not hmac.compare_digest(row["approval_nonce_hash"] or "",
                                       hashlib.sha256(nonce.encode("ascii")).hexdigest())):
        raise DockingConsentError("consent_proof_mismatch")


def validate_execution_binding(row, policy, now_ms, monotonic_now, *, expected=None):
    """Revalidate trusted policy and immutable consent facts, including after claim."""
    if type(policy) is not DockingConsentPolicy:
        raise DockingConsentError("consent_policy_unavailable")
    validate_clocks(now_ms, monotonic_now)
    if (row["runtime_generation"] != policy.runtime_generation
            or row["policy_generation"] != policy.policy_generation):
        raise DockingConsentError("consent_generation_mismatch")
    if now_ms >= row["expires_at_ms"] or monotonic_now >= row["monotonic_expires"]:
        raise DockingConsentError("consent_expired")
    if row.get("operation_deadline_ms") is not None and (
        now_ms >= row["operation_deadline_ms"]
        or monotonic_now >= row["operation_monotonic_expires"]
    ):
        raise DockingConsentError("consent_execution_timeout")
    try:
        binding = json.loads(row["binding_json"])
        stored_policy = json.loads(row["policy_json"])
        if stored_policy != asdict(policy):
            raise ValueError
        digest = hashlib.sha256(b"medchat-docking-consent-v1\0" + canonical_json(binding)).hexdigest()
        if not hmac.compare_digest(digest, row["binding_digest"]):
            raise ValueError
        if expected is not None and any(row[key] != expected[key] for key in (
            "identity_json", "binding_json", "binding_digest", "approval_nonce_hash", "policy_json",
        )):
            raise ValueError
        identity = json.loads(row["identity_json"])
        if any(binding[key] != value for key, value in identity.items()):
            raise ValueError
        if any(binding[key] != value for key, value in stored_policy.items()):
            raise ValueError
    except (TypeError, ValueError, KeyError):
        raise DockingConsentError("consent_binding_mismatch") from None
    return binding


def validate_identity(identity: Any) -> dict[str, Any]:
    fields = {
        "preparation_id", "task_id", "owner_session_id", "trace_id",
        "origin_turn_id", "query_digest", "refinement_revision",
        "admission_revision", "source_refs",
    }
    if type(identity) is not dict or set(identity) != fields:
        raise DockingConsentError("consent_invalid_input")
    for key in fields - {"source_refs", "refinement_revision", "query_digest"}:
        if type(identity[key]) is not str or not identity[key]:
            raise DockingConsentError("consent_invalid_input")
    refs = identity["source_refs"]
    if (
        type(refs) is not dict or set(refs) != {"receptor_ref", "ligand_ref"}
        or any(type(value) is not str or not value for value in refs.values())
        or type(identity["refinement_revision"]) is not int
        or identity["refinement_revision"] < 0
        or type(identity["query_digest"]) is not str
        or re.fullmatch(r"[a-f0-9]{64}", identity["query_digest"]) is None
    ):
        raise DockingConsentError("consent_invalid_input")
    try:
        _validate_task_id(identity["task_id"])
        return json.loads(canonical_json(identity))
    except (ManifestError, ValueError, TypeError, UnicodeError):
        raise DockingConsentError("consent_invalid_input") from None


def validate_clocks(now_ms: int, monotonic_now: float) -> None:
    if (type(now_ms) is not int or not 0 <= now_ms < 2**63 - CONSENT_TTL_MS
            or type(monotonic_now) not in (int, float)):
        raise DockingConsentError("consent_invalid_input")
    try:
        valid = math.isfinite(monotonic_now) and monotonic_now >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise DockingConsentError("consent_invalid_input")


def validate_inputs(receptor_name, receptor_bytes, ligand_name, ligand_bytes,
                    parameters) -> dict[str, Any]:
    try:
        _validate_input_name(receptor_name)
        _validate_input_name(ligand_name)
        if _portable_name_key(receptor_name) == _portable_name_key(ligand_name):
            raise ValueError
        for content in (receptor_bytes, ligand_bytes):
            if type(content) is not bytes or not 0 < len(content) <= MAX_DOCKING_INPUT_BYTES:
                raise ValueError
        if type(parameters) is not dict or set(parameters) != {
            "center", "size", "exhaustiveness", "num_modes",
        }:
            raise ValueError
        for key in ("center", "size"):
            values = parameters[key]
            if type(values) is not list or len(values) != 3:
                raise ValueError
            if any(type(value) not in (int, float) for value in values):
                raise ValueError
        for key in ("exhaustiveness", "num_modes"):
            if type(parameters[key]) is not int:
                raise ValueError
        return _normalize_config(parameters)
    except (ManifestError, ValueError, TypeError, OverflowError):
        raise DockingConsentError("consent_invalid_input") from None


def seal_binding(identity, manifest, policy, request_digest, input_hash, now_ms):
    files = {
        key: {"name": PurePosixPath(manifest[key]["path"]).name, "size": manifest[key]["size"],
              "sha256": manifest[key]["sha256"]}
        for key in ("receptor", "ligand")
    }
    binding = {
        "schema": "DockingConsent@1", **identity, **files,
        "ligand_mode": "file", **manifest["config"], "energy_range": 3.0,
        "config_hash": manifest["config_hash"], "input_hash": input_hash,
        "request_digest": request_digest, "tool_name": "molecular_docking",
        "adapter_contract_version": policy.adapter_contract_version,
        "tool_policy_digest": policy.tool_policy_digest,
        "execution_backend": policy.execution_backend,
        "policy_generation": policy.policy_generation,
        "runtime_generation": policy.runtime_generation,
        "issued_at_ms": now_ms, "expires_at_ms": now_ms + CONSENT_TTL_MS,
        "vina_limit_seconds": policy.vina_limit_seconds,
        "operation_limit_seconds": policy.operation_limit_seconds,
    }
    digest = hashlib.sha256(
        b"medchat-docking-consent-v1\0" + canonical_json(binding)
    ).hexdigest()
    return binding, digest


def make_preview(binding, digest, nonce) -> DockingConsentPreview:
    return DockingConsentPreview(canonical_json({
        "schema": binding["schema"], "preparation_id": binding["preparation_id"],
        "task_id": binding["task_id"], "trace_id": binding["trace_id"],
        "receptor": binding["receptor"], "ligand": binding["ligand"],
        "parameters": {key: binding[key] for key in (
            "center", "size", "exhaustiveness", "num_modes", "energy_range",
        )},
        "tool_policy": {
            "label": "molecular_docking/local", "digest": binding["tool_policy_digest"],
            **{key: binding[key] for key in (
                "execution_backend", "adapter_contract_version",
                "vina_limit_seconds", "operation_limit_seconds",
            )},
        },
        "binding_digest": digest, "expires_at_ms": binding["expires_at_ms"],
        "approval_nonce": nonce,
    }))
