from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
import re
from typing import Any
from urllib.parse import unquote

from src.agent.persistence.redaction import (
    contains_sensitive_text,
    redact_sensitive,
    sanitize_sensitive_text,
)

from .errors import TaskErrorCode


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


class TaskPhase(str, Enum):
    RUNNING = "running"
    STAGING = "staging"
    ENVIRONMENT_CHECK = "environment_check"
    INPUT_VERIFICATION = "input_verification"
    RECEPTOR_PREPARATION = "receptor_preparation"
    LIGAND_PREPARATION = "ligand_preparation"
    VINA_RUNNING = "vina_running"
    RESULT_PARSING = "result_parsing"
    SCIENTIFIC_VALIDATION = "scientific_validation"
    ARTIFACT_COMMIT = "artifact_commit"
    COMPLETING = "completing"
    CANCELING = "canceling"


class ResultProjectionPolicy(str, Enum):
    SCIENTIFIC_STRICT = "scientific_strict"
    GENERIC_SAFE = "generic_safe"


CONFIG_WARNING_CODES = frozenset(
    {
        "invalid_task_backend",
        "invalid_temporal_canary_percent",
        "invalid_temporal_address",
        "invalid_temporal_namespace",
        "invalid_temporal_docking_queue",
        "invalid_temporal_docking_concurrency",
        "invalid_task_staging_root",
    }
)
TASK_WARNING_CODES = frozenset(
    CONFIG_WARNING_CODES
    | {item.value for item in TaskErrorCode}
    | {"projection_stale", "temporal_start_ambiguous", "TOOL_WARNING"}
)

PROVENANCE_KEYS = frozenset(
    {
        "provider",
        "model",
        "model_name",
        "tool",
        "tool_name",
        "version",
        "tool_version",
        "sdk_version",
        "backend",
        "attempt",
        "validator_status",
        "configured",
        "reason",
        "bucket",
        "percent",
        "start_outcome",
        "process_restart_recovery",
        "durable_execution",
        "demo_mode",
        "fallback_used",
        "hash",
        "hashes",
    }
)
_PROVENANCE_BOOLEAN_KEYS = frozenset({"demo_mode", "fallback_used"})
_PRIVATE_CONTENT_KEYS = frozenset(
    {
        "smiles",
        "query",
        "prompt",
        "receptor",
        "ligand",
        "input",
        "db_path",
        "model_path",
        "manifest",
        "input_manifest_path",
    }
)
_PRIVATE_SCIENTIFIC_PREFIXES = frozenset(
    {"smiles", "ligand", "receptor", "input", "query", "prompt"}
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]")
_WINDOWS_PATH_TEXT = re.compile(r"(?i)(?:[A-Z]:[\\/])[^\s;,]+")
_UNC_PATH_TEXT = re.compile(r"\\\\[^\s\\/]+[\\/][^\s;,]+")
_POSIX_PATH_TEXT = re.compile(r"(?<![A-Za-z0-9.])/(?:[^\s/;,]+/)*[^\s;,]+")
_FILE_URI_TEXT = re.compile(r"(?i)file://[^\s;,]+")
_BEARER_TEXT = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_API_KEY_TEXT = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ENCODED_PATH_SEMANTICS = re.compile(r"%(?:2e|2f|5c)", re.IGNORECASE)
_SAFE_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_SAFE_DIGEST = re.compile(r"[A-Fa-f0-9]{64}\Z")
_SAFE_DIGEST_TEXT = re.compile(
    r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{64}(?![A-Fa-f0-9])"
)
_SAFE_DIGEST_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_SCIENTIFIC_SMILES_KEYS = frozenset(
    {"smiles", "canonical_smiles", "isomeric_smiles", "best_smiles"}
)
_EXPLICIT_PROVENANCE_DIGEST_KEYS = frozenset(
    {"hash", "input_hash", "config_hash", "pose_sha256"}
)
_SENSITIVE_IDENTIFIER_MARKERS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)
_DROP = object()
STRICT_JSON_MAX_DEPTH = 64
STRICT_JSON_MAX_ITEMS = 100_000
TASK_WARNING_MESSAGE_MAX_LENGTH = 512
_SAFE_RUNTIME_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_STRICT_SMILES_VALUE = re.compile(
    r"(?:(?:Cl|Br)|[BCNOFPSIcnosp0-9@+\-\[\]()=#$\\/.%])+\Z"
)
_BACKEND_DECISION_REASONS = frozenset(
    {
        "invalid_canary_percent",
        "task_type_not_allowlisted",
        "temporal_unavailable",
        "invalid_task_id",
        "canary_selected",
        "canary_not_selected",
    }
)


def _validate_runtime_code(value: Any, name: str) -> str:
    if type(value) is not str or _SAFE_RUNTIME_CODE.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _safe_runtime_metadata(value: Any, name: str) -> dict[str, Any]:
    try:
        snapshot = validate_task_input_payload(value)
    except ValueError as exc:
        message = str(exc)
        if "JSON" in message:
            raise ValueError(f"invalid JSON {name}") from exc
        raise ValueError(f"sensitive {name}") from exc
    return snapshot


def _runtime_metadata_contains_smiles(value: Any, key: str | None = None) -> bool:
    if isinstance(value, dict):
        return any(
            _runtime_metadata_contains_smiles(child, child_key)
            for child_key, child in value.items()
        )
    if isinstance(value, list):
        return any(_runtime_metadata_contains_smiles(child) for child in value)
    if not isinstance(value, str):
        return False
    normalized_key = (key or "").lower()
    if normalized_key.endswith(("_hash", "_sha256")) and _SAFE_DIGEST.fullmatch(value):
        return False
    return (
        len(value) >= 3
        and _STRICT_SMILES_VALUE.fullmatch(value) is not None
        and re.search(r"[BCNOFPSIcnosp]", value) is not None
    )


def _validate_submission_payload_schema(payload: dict[str, Any]) -> None:
    if not payload:
        return

    def validate_decision(decision: Any) -> None:
        if not isinstance(decision, dict) or set(decision) != {
            "backend",
            "reason",
            "bucket",
            "percent",
        }:
            raise ValueError("invalid task payload schema")
        if decision["backend"] not in {"local", "temporal"}:
            raise ValueError("invalid task payload backend")
        if decision["reason"] not in _BACKEND_DECISION_REASONS:
            raise ValueError("invalid task payload reason")
        bucket = decision["bucket"]
        if bucket is not None and (
            type(bucket) is not int or not 0 <= bucket < 100
        ):
            raise ValueError("invalid task payload bucket")
        percent = decision["percent"]
        if type(percent) is not int or not 0 <= percent <= 100:
            raise ValueError("invalid task payload percent")

    generic_keys = {"payload_digest", "field_count"}
    if set(payload) == generic_keys:
        if (
            type(payload["payload_digest"]) is not str
            or _SAFE_DIGEST.fullmatch(payload["payload_digest"]) is None
            or type(payload["field_count"]) is not int
            or payload["field_count"] < 0
        ):
            raise ValueError("invalid generic task payload")
        return
    if set(payload) != {
        "decision",
        "mode",
        "total_bytes",
        "config_hash",
        "request_digest",
    }:
        raise ValueError("invalid task payload schema")
    validate_decision(payload["decision"])
    if payload["mode"] not in {"file", "smiles"}:
        raise ValueError("invalid task payload mode")
    if type(payload["total_bytes"]) is not int or payload["total_bytes"] < 0:
        raise ValueError("invalid task payload total_bytes")
    if (
        type(payload["config_hash"]) is not str
        or _SAFE_DIGEST.fullmatch(payload["config_hash"]) is None
    ):
        raise ValueError("invalid task payload config_hash")
    if (
        type(payload["request_digest"]) is not str
        or _SAFE_DIGEST.fullmatch(payload["request_digest"]) is None
    ):
        raise ValueError("invalid task payload request_digest")


@dataclass(frozen=True)
class TaskWarning:
    code: str
    message: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class TaskSubmission:
    """Internal immutable hand-off; public metadata excludes scientific inputs."""

    task_id: str
    task_type: str
    payload: dict[str, Any] = field(repr=False)
    input_manifest_path: str = field(repr=False)
    idempotency_key: str | None = field(default=None, repr=False)
    request_digest: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_runtime_code(self.task_id, "task_id")
        _validate_runtime_code(self.task_type, "task_type")
        if (
            type(self.input_manifest_path) is not str
            or not self.input_manifest_path
            or self.input_manifest_path != self.input_manifest_path.strip()
            or len(self.input_manifest_path) > 4096
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.input_manifest_path
            )
        ):
            raise ValueError("invalid input_manifest_path")
        if self.idempotency_key is not None:
            _validate_runtime_code(self.idempotency_key, "idempotency_key")
        if self.request_digest is not None and (
            type(self.request_digest) is not str
            or _SAFE_DIGEST.fullmatch(self.request_digest) is None
        ):
            raise ValueError("invalid request_digest")
        object.__setattr__(
            self,
            "payload",
            _safe_runtime_metadata(self.payload, "task payload"),
        )
        _validate_submission_payload_schema(self.payload)
        payload_request_digest = self.payload.get("request_digest")
        if payload_request_digest is not None and (
            self.request_digest is None
            or payload_request_digest != self.request_digest
        ):
            raise ValueError("request_digest mismatch")


@dataclass(frozen=True)
class BackendHealth:
    """Safe health projection suitable for API and event serialization."""

    backend: str
    available: bool
    message: str
    details: dict[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        _validate_runtime_code(self.backend, "backend")
        if type(self.available) is not bool:
            raise ValueError("invalid backend availability")
        if (
            type(self.message) is not str
            or not self.message
            or self.message != self.message.strip()
            or len(self.message) > TASK_WARNING_MESSAGE_MAX_LENGTH
            or sanitize_task_message(self.message) != self.message
        ):
            raise ValueError("sensitive backend health message")
        object.__setattr__(
            self,
            "details",
            _safe_health_details(self.details),
        )


def _safe_health_details(value: Any) -> dict[str, Any]:
    try:
        snapshot = strict_json_snapshot(value)
    except ValueError as exc:
        raise ValueError("invalid JSON backend health details") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("invalid backend health details")
    allowed = {
        "configured",
        "durable_execution",
        "process_restart_recovery",
        "running",
        "shutdown_pending",
    }
    if not set(snapshot) <= allowed:
        raise ValueError("sensitive backend health details")
    for key, item in snapshot.items():
        if key == "running":
            if type(item) is not int or item < 0:
                raise ValueError("invalid backend health details")
        elif type(item) is not bool:
            raise ValueError("invalid backend health details")
    return snapshot


def strict_json_snapshot(value: Any) -> Any:
    """Return a detached JSON-compatible value and reject non-finite data."""

    active_containers: set[int] = set()
    item_count = 0

    def visit(item: Any, depth: int) -> Any:
        nonlocal item_count
        item_count += 1
        if item_count > STRICT_JSON_MAX_ITEMS:
            raise ValueError("invalid JSON size limit exceeded")
        if depth > STRICT_JSON_MAX_DEPTH:
            raise ValueError("invalid JSON depth limit exceeded")
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("invalid JSON non-finite number")
            return item
        if isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in active_containers:
                raise ValueError("invalid JSON cyclic container")
            active_containers.add(identity)
            try:
                if isinstance(item, dict):
                    result: dict[str, Any] = {}
                    for key, child in item.items():
                        if not isinstance(key, str):
                            raise ValueError("invalid JSON object key")
                        result[key] = visit(child, depth + 1)
                    return result
                return [visit(child, depth + 1) for child in item]
            finally:
                active_containers.remove(identity)
        raise ValueError("invalid JSON value")

    try:
        return visit(value, 0)
    except RecursionError as exc:
        raise ValueError("invalid JSON recursion limit exceeded") from exc


def strict_json_roundtrip(value: Any) -> Any:
    snapshot = strict_json_snapshot(value)
    encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    return json.loads(encoded)


def sanitize_task_message(value: str) -> str:
    message = value
    for pattern in (_BEARER_TEXT, _API_KEY_TEXT):
        message = pattern.sub("[REDACTED]", message)
    for pattern in (_FILE_URI_TEXT, _WINDOWS_PATH_TEXT, _UNC_PATH_TEXT, _POSIX_PATH_TEXT):
        message = pattern.sub("[PATH]", message)
    message = _redact_scientific_structures(message)
    return message[:TASK_WARNING_MESSAGE_MAX_LENGTH].strip()


def _normalize_warning(value: Any, *, strict: bool) -> dict[str, str] | None:
    if isinstance(value, TaskErrorCode):
        value = value.value
    if isinstance(value, TaskWarning):
        raw: dict[str, Any] = {
            "code": value.code,
            "message": value.message,
            "source": value.source,
        }
    elif isinstance(value, dict):
        raw = value
    elif isinstance(value, str):
        if value in TASK_WARNING_CODES:
            return {"code": value}
        message = sanitize_task_message(value)
        if not message:
            if strict:
                raise ValueError("invalid warning message")
            return None
        return {"code": "TOOL_WARNING", "message": message}
    else:
        if strict:
            raise ValueError("invalid warning")
        return None

    code = raw.get("code")
    if isinstance(code, TaskErrorCode):
        code = code.value
    if not isinstance(code, str) or code not in TASK_WARNING_CODES:
        if strict:
            raise ValueError("invalid warning code")
        return None
    normalized = {"code": code}
    message = raw.get("message")
    if message is not None:
        if not isinstance(message, str):
            if strict:
                raise ValueError("invalid warning message")
            return normalized
        safe_message = sanitize_task_message(message)
        if safe_message:
            normalized["message"] = safe_message
    source = raw.get("source")
    if source is not None:
        if not isinstance(source, str) or not _SAFE_SOURCE.fullmatch(source):
            if strict:
                raise ValueError("invalid warning source")
        else:
            normalized["source"] = source
    return normalized


def normalize_task_warnings(values: Any, *, strict: bool = True) -> list[dict[str, str]]:
    if not isinstance(values, (list, tuple)):
        if strict:
            raise ValueError("invalid warnings")
        return []
    normalized: list[dict[str, str]] = []
    for value in values:
        warning = _normalize_warning(value, strict=strict)
        if warning is not None:
            normalized.append(warning)
    return normalized


def normalize_warning_codes(values: Any) -> list[str]:
    return [item["code"] for item in normalize_task_warnings(values)]


def _is_hash_key(value: str) -> bool:
    return value in {"hash", "input_hash", "config_hash", "pose_sha256"} or value.endswith(
        ("_hash", "_sha256")
    )


def _validate_digest(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_DIGEST.fullmatch(value):
        raise ValueError("invalid provenance digest")
    return value.lower()


def _validate_digest_identifier(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_DIGEST_IDENTIFIER.fullmatch(value):
        raise ValueError("invalid provenance hash identifier")
    normalized = value.lower()
    if (
        value in {".", ".."}
        or value.startswith(".")
        or value.endswith(".")
        or ".." in value
    ):
        raise ValueError("invalid provenance hash identifier")
    tokens = {token for token in re.split(r"[^a-z0-9]+", normalized) if token}
    if "key" in tokens or tokens & _SENSITIVE_IDENTIFIER_MARKERS or any(
        marker in normalized for marker in _SENSITIVE_IDENTIFIER_MARKERS
    ):
        raise ValueError("invalid provenance hash identifier")
    if normalized in _EXPLICIT_PROVENANCE_DIGEST_KEYS:
        return value
    if any(marker in normalized for marker in ("path", "file", "uri", "manifest")):
        raise ValueError("invalid provenance hash identifier")
    is_digest_label = normalized.endswith(("_hash", "_sha256", "-hash", "-sha256"))
    if not is_digest_label and _is_private_content_key(value):
        raise ValueError("invalid provenance hash identifier")
    return value


def sanitize_provenance(value: Any) -> dict[str, Any]:
    snapshot = strict_json_snapshot(value or {})
    if not isinstance(snapshot, dict):
        raise ValueError("invalid provenance")
    allowed: dict[str, Any] = {}
    seen_normalized_keys: set[str] = set()
    for key, item in snapshot.items():
        normalized = key.lower()
        if normalized in seen_normalized_keys:
            raise ValueError("invalid provenance duplicate key")
        seen_normalized_keys.add(normalized)
        if normalized in _PROVENANCE_BOOLEAN_KEYS:
            if type(item) is not bool:
                raise ValueError("invalid provenance boolean")
            allowed[normalized] = item
            continue
        if _is_hash_key(normalized):
            safe_key = _validate_digest_identifier(normalized)
            allowed[safe_key] = _validate_digest(item)
            continue
        if normalized == "hashes":
            if not isinstance(item, dict):
                raise ValueError("invalid provenance hashes")
            allowed[normalized] = {
                _validate_digest_identifier(digest_key): _validate_digest(digest)
                for digest_key, digest in item.items()
            }
            continue
        if normalized not in PROVENANCE_KEYS and not (
            normalized.endswith("_hash") or normalized.endswith("_sha256")
        ):
            continue
        sanitized = _sanitize_public_value(item, key=normalized)
        if sanitized is not _DROP:
            allowed[normalized] = sanitized
    return redact_sensitive(allowed)


def sanitize_public_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed: dict[str, Any] = {}
    normalized_counts: dict[str, int] = {}
    for key in value:
        if isinstance(key, str):
            normalized = key.lower()
            normalized_counts[normalized] = normalized_counts.get(normalized, 0) + 1
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        normalized = key.lower()
        if normalized_counts.get(normalized, 0) != 1:
            continue
        if normalized in _PROVENANCE_BOOLEAN_KEYS:
            if type(item) is bool:
                allowed[normalized] = item
            continue
        if _is_hash_key(normalized):
            try:
                safe_key = _validate_digest_identifier(normalized)
                allowed[safe_key] = _validate_digest(item)
            except ValueError:
                pass
            continue
        if normalized == "hashes" and isinstance(item, dict):
            hashes: dict[str, str] = {}
            for digest_key, digest in item.items():
                try:
                    safe_key = _validate_digest_identifier(digest_key)
                    hashes[safe_key] = _validate_digest(digest)
                except ValueError:
                    continue
            if hashes:
                allowed[normalized] = hashes
            continue
        if normalized not in PROVENANCE_KEYS and not (
            normalized.endswith("_hash") or normalized.endswith("_sha256")
        ):
            continue
        sanitized = _sanitize_public_value(item, key=normalized)
        if sanitized is not _DROP:
            allowed[normalized] = sanitized
    return redact_sensitive(allowed)


def _is_absolute_path(value: str) -> bool:
    return (
        bool(_WINDOWS_ABSOLUTE_PATH.search(value))
        or value.startswith("/")
        or value.startswith("\\\\")
    )


def _decode_relative_artifact_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    if "?" in value or "#" in value or "\\" in value:
        return None
    decoded = value
    for _ in range(8):
        if _ENCODED_PATH_SEMANTICS.search(decoded):
            return None
        try:
            next_decoded = unquote(decoded, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        if next_decoded == decoded:
            break
        decoded = next_decoded
    else:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        return None
    if "?" in decoded or "#" in decoded or "\\" in decoded:
        return None
    if (
        _URI_SCHEME.match(value)
        or _URI_SCHEME.match(decoded)
        or _is_absolute_path(value)
        or _is_absolute_path(decoded)
    ):
        return None
    original_parts = value.split("/")
    decoded_parts = decoded.split("/")
    if not original_parts or any(
        part in {"", ".", ".."} for part in (*original_parts, *decoded_parts)
    ):
        return None
    return decoded


def is_safe_relative_artifact_path(value: Any) -> bool:
    return _decode_relative_artifact_path(value) is not None


def _is_private_content_key(value: str) -> bool:
    normalized = value.lower()
    if normalized in _PRIVATE_CONTENT_KEYS or "manifest" in normalized:
        return True
    if contains_prefixed_scientific_identifier(value):
        return True
    tokens = {token for token in re.split(r"[^a-z0-9]+", normalized) if token}
    return bool(
        tokens & {"smiles", "query", "prompt", "receptor", "ligand", "input"}
    )


def _sanitize_public_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower()
    if _is_private_content_key(key or ""):
        return _DROP
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                continue
            sanitized = _sanitize_public_value(child_value, key=child_key)
            if sanitized is not _DROP:
                result[child_key] = sanitized
        return redact_sensitive(result)
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            sanitized = _sanitize_public_value(item)
            if sanitized is not _DROP:
                result.append(sanitized)
        return redact_sensitive(result)
    if isinstance(value, float) and not math.isfinite(value):
        return _DROP
    if isinstance(value, str):
        if (
            _is_absolute_path(value)
            or _FILE_URI_TEXT.search(value)
            or _WINDOWS_PATH_TEXT.search(value)
            or _UNC_PATH_TEXT.search(value)
            or _POSIX_PATH_TEXT.search(value)
            or any(part in {".", ".."} for part in re.split(r"[\\/]", value))
        ):
            return _DROP
    if value is None or isinstance(value, (str, bool, int, float)):
        return redact_sensitive(value)
    return _DROP


def sanitize_public_task_data(value: Any) -> Any:
    sanitized = _sanitize_public_value(value)
    return {} if sanitized is _DROP else sanitized


def validate_task_input_payload(value: Any) -> dict[str, Any]:
    try:
        snapshot = strict_json_snapshot(value)
    except ValueError as exc:
        raise ValueError("invalid JSON task payload") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("invalid JSON task payload")
    try:
        _validate_submission_payload_schema(snapshot)
    except ValueError as exc:
        raise ValueError("invalid task payload schema") from exc
    return snapshot


def _has_unsafe_result_path_semantics(value: str) -> bool:
    candidate = value
    for _ in range(8):
        if (
            _is_absolute_path(candidate)
            or _FILE_URI_TEXT.search(candidate)
            or _WINDOWS_PATH_TEXT.search(candidate)
            or _UNC_PATH_TEXT.search(candidate)
            or _POSIX_PATH_TEXT.search(candidate)
            or _ENCODED_PATH_SEMANTICS.search(candidate)
            or any(
                part in {"", ".", ".."} for part in re.split(r"[\\/]", candidate)
            )
        ):
            return True
        try:
            decoded = unquote(candidate, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return True
        if decoded == candidate:
            return False
        candidate = decoded
    return True


def _sanitize_public_result_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower()
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                continue
            sanitized = _sanitize_public_result_value(child_value, key=child_key)
            if sanitized is not _DROP:
                result[child_key] = sanitized
        return redact_sensitive(result)
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            sanitized = _sanitize_public_result_value(item)
            if sanitized is not _DROP:
                result.append(sanitized)
        return redact_sensitive(result)
    if isinstance(value, float) and not math.isfinite(value):
        return _DROP
    if isinstance(value, str):
        if _has_unsafe_result_path_semantics(value):
            return _DROP
        if normalized_key in _SCIENTIFIC_SMILES_KEYS:
            return redact_sensitive(value)
        if _runtime_metadata_contains_smiles(value, normalized_key):
            return _DROP
        if any(marker in normalized_key for marker in ("path", "file", "uri")):
            if not is_safe_relative_artifact_path(value):
                return _DROP
        return redact_sensitive(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _DROP


def sanitize_public_result(value: Any) -> Any:
    sanitized = _sanitize_public_result_value(value)
    return None if sanitized is _DROP else sanitized


_RESULT_STATUS_VALUES = frozenset(
    {"succeeded", "failed", "cancelled", "canceled", "timed_out"}
)
_RESULT_ENUM_VALUES = frozenset(
    {
        "validated",
        "passed",
        "rejected",
        "available",
        "unavailable",
        "committed",
        "committed_ambiguous",
        "strict",
        "real",
        "succeeded",
        "completed",
        "uncertain",
        "reusable",
        "opensandbox",
        "gvisor",
    }
)

RESULT_PROJECTION_MAX_DEPTH = 8
RESULT_PROJECTION_MAX_ITEMS = 256
RESULT_PROJECTION_MAX_TEXT_LENGTH = 512
_SAFE_GENERIC_RESULT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
_SAFE_ARTIFACT_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}\Z")
_ELEMENT_SYMBOLS = frozenset(
    (
        "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe "
        "Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In "
        "Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf "
        "Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm "
        "Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
    ).split()
    + ["b", "c", "n", "o", "p", "s"]
)
_AROMATIC_ATOMS = frozenset({"b", "c", "n", "o", "p", "s"})
_ORGANIC_SMILES_ATOMS = frozenset(
    {"B", "C", "N", "O", "P", "S", "F", "I", "Cl", "Br"}
)
_SMILES_STRUCTURE_CHARACTERS = frozenset("@+-[]()=#$%.\\/:")
_ARTIFACT_TYPES = frozenset(
    {"docking_pose", "file", "generic_file", "log", "pdbqt", "pose", "report"}
)
_ARTIFACT_STATUSES = frozenset(
    {"available", "completed", "failed", "partial", "ready", "succeeded", "verified"}
)


def canonical_artifact_type(value: Any) -> str | None:
    """Resolve legacy/current artifact type aliases without precedence ambiguity."""

    if not isinstance(value, dict):
        return None
    current = value.get("artifact_type")
    legacy = value.get("type")
    if current is not None and legacy is not None and current != legacy:
        return None
    candidate = current if current is not None else legacy
    return candidate if isinstance(candidate, str) and candidate in _ARTIFACT_TYPES else None


_PROJECTION_CONFLICT = object()
_RESULT_RESERVED_KEYS = frozenset(
    {"artifacts", "error", "error_code", "evidence", "provenance", "warnings"}
)
_STRICT_NUMERIC_RESULT_KEYS = frozenset(
    {
        "attempt",
        "best_energy",
        "binding_energy",
        "elapsed",
        "elapsed_ms",
        "energy",
        "pose_count",
        "progress",
        "score",
        "total_poses",
    }
)
_STRICT_BOOLEAN_RESULT_KEYS = frozenset(
    {"success", "reused_completion", "real_execution", "validated"}
)
_STRICT_DIGEST_RESULT_KEYS = frozenset(
    {
        "config_hash",
        "digest",
        "hash",
        "input_hash",
        "output_hash",
        "pose_sha256",
        "sha256",
        "sandbox_image_digest",
    }
)
_STRICT_ENUM_RESULT_KEYS = frozenset(
    {
        "authority_state",
        "completion_authority",
        "completion_durability",
        "execution_status",
        "quality",
        "validator_status",
        "execution_backend",
        "secure_runtime",
        "cleanup_status",
    }
)
_STRICT_RESULT_OBJECT_SCHEMAS: dict[str, frozenset[str]] = {
    "root": frozenset(
        {
            "success",
            "status",
            "reused_completion",
            "attempt",
            "best_energy",
            "binding_energy",
            "elapsed",
            "elapsed_ms",
            "energy",
            "pose_count",
            "progress",
            "score",
            "total_poses",
            "completion",
            "data",
            "quality",
            "validator_status",
            "execution_backend",
            "secure_runtime",
            "sandbox_image_digest",
            "cleanup_status",
        }
    ),
    "completion": frozenset(
        {
            "attempt",
            "best_energy",
            "binding_energy",
            "elapsed",
            "elapsed_ms",
            "pose_count",
            "progress",
            "total_poses",
            "config_hash",
            "digest",
            "hash",
            "input_hash",
            "output_hash",
            "pose_sha256",
            "quality",
            "sha256",
            "validator_status",
        }
    ),
    "data": frozenset(
        {
            "attempt",
            "best_energy",
            "binding_energy",
            "elapsed",
            "elapsed_ms",
            "pose_count",
            "progress",
            "score",
            "total_poses",
            "best_pose",
        }
    ),
    "best_pose": frozenset(
        {"best_energy", "binding_energy", "pose_count", "score"}
    ),
    "quality": frozenset(
        {
            "attempt",
            "authority_state",
            "completion_authority",
            "completion_durability",
            "execution_status",
            "progress",
            "real_execution",
            "score",
            "validated",
            "validator_status",
        }
    ),
}


def _sanitize_scientific_result_object(
    value: Any,
    *,
    schema_name: str,
    depth: int,
    budget: list[int],
) -> Any:
    if value is None and schema_name == "completion":
        return None
    if not isinstance(value, dict) or depth > RESULT_PROJECTION_MAX_DEPTH:
        return _DROP
    allowed_keys = _STRICT_RESULT_OBJECT_SCHEMAS[schema_name]
    seen_keys: set[str] = set()
    for raw_key in value:
        if not isinstance(raw_key, str):
            continue
        canonical = raw_key.lower()
        if canonical not in allowed_keys:
            continue
        if canonical in seen_keys:
            return _PROJECTION_CONFLICT
        seen_keys.add(canonical)
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if budget[0] >= RESULT_PROJECTION_MAX_ITEMS:
            break
        budget[0] += 1
        if not isinstance(raw_key, str):
            continue
        key = raw_key
        if key not in allowed_keys:
            continue
        sanitized: Any = _DROP
        if key in _STRICT_BOOLEAN_RESULT_KEYS:
            if type(raw_value) is bool:
                sanitized = raw_value
        elif key in _STRICT_NUMERIC_RESULT_KEYS:
            if type(raw_value) in {int, float} and (
                not isinstance(raw_value, float) or math.isfinite(raw_value)
            ):
                sanitized = raw_value
        elif key in _STRICT_DIGEST_RESULT_KEYS:
            if isinstance(raw_value, str) and _SAFE_DIGEST.fullmatch(raw_value):
                sanitized = raw_value.lower()
        elif key == "status":
            if isinstance(raw_value, str):
                lowered = raw_value.strip().lower()
                if lowered in _RESULT_STATUS_VALUES:
                    sanitized = lowered
        elif key in _STRICT_ENUM_RESULT_KEYS:
            if key == "quality" and isinstance(raw_value, dict):
                sanitized = _sanitize_scientific_result_object(
                    raw_value,
                    schema_name="quality",
                    depth=depth + 1,
                    budget=budget,
                )
                if sanitized is _PROJECTION_CONFLICT:
                    return _PROJECTION_CONFLICT
            elif isinstance(raw_value, str):
                lowered = raw_value.strip().lower()
                if lowered in _RESULT_ENUM_VALUES:
                    sanitized = lowered
        elif key in {"completion", "data", "best_pose"}:
            sanitized = _sanitize_scientific_result_object(
                raw_value,
                schema_name=key,
                depth=depth + 1,
                budget=budget,
            )
            if sanitized is _PROJECTION_CONFLICT:
                return _PROJECTION_CONFLICT
        if sanitized is not _DROP:
            result[key] = sanitized
    return result


def _is_safe_generic_result_key(value: Any) -> bool:
    if not isinstance(value, str) or _SAFE_GENERIC_RESULT_KEY.fullmatch(value) is None:
        return False
    normalized = value.lower()
    if normalized in _RESULT_RESERVED_KEYS or _is_private_content_key(value):
        return False
    if any(marker in normalized for marker in _SENSITIVE_IDENTIFIER_MARKERS):
        return False
    if any(marker in normalized for marker in ("manifest", "path", "uri")):
        return False
    if ".." in value or contains_sensitive_text(value):
        return False
    return not contains_scientific_structure(value)


def _is_ascii_alphanumeric(value: str) -> bool:
    return value.isascii() and value.isalnum()


def _atom_token_at(value: str, index: int) -> tuple[int, bool, bool] | None:
    character = value[index]
    if character in _AROMATIC_ATOMS:
        return index + 1, True, True
    if not character.isupper():
        return None
    if index + 1 < len(value) and value[index + 1].islower():
        symbol = value[index : index + 2]
        if symbol in _ELEMENT_SYMBOLS:
            return index + 2, False, symbol in _ORGANIC_SMILES_ATOMS
    if character in _ELEMENT_SYMBOLS:
        return index + 1, False, character in _ORGANIC_SMILES_ATOMS
    return None


def _scan_scientific_structure(value: str, start: int) -> int | None:
    atom = _atom_token_at(value, start)
    if atom is None:
        return None
    cursor, aromatic, organic = atom
    atom_count = 1
    has_aromatic = aromatic
    all_organic = organic
    has_explicit_structure = False
    qualified_end: int | None = None
    while True:
        if cursor == len(value) or not _is_ascii_alphanumeric(value[cursor]):
            pure_chain = (
                atom_count >= 3 and all_organic and not has_aromatic
            )
            structured = atom_count >= 2 and has_explicit_structure
            if pure_chain or structured:
                qualified_end = cursor
        if cursor >= len(value):
            break
        while cursor < len(value) and (
            value[cursor].isdigit()
            or value[cursor] in _SMILES_STRUCTURE_CHARACTERS
        ):
            has_explicit_structure = True
            cursor += 1
        if cursor == len(value):
            if atom_count >= 2 and has_explicit_structure:
                qualified_end = cursor
            break
        atom = _atom_token_at(value, cursor)
        if atom is None:
            if (
                atom_count >= 2
                and has_explicit_structure
                and not _is_ascii_alphanumeric(value[cursor])
            ):
                qualified_end = cursor
            break
        cursor, aromatic, organic = atom
        atom_count += 1
        has_aromatic = has_aromatic or aromatic
        all_organic = all_organic and organic
    return qualified_end


def _scientific_structure_spans(value: str) -> list[tuple[int, int]]:
    protected = _SAFE_DIGEST_TEXT.sub(
        lambda match: "x" * len(match.group(0)),
        value,
    )
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(protected):
        if index > 0 and _is_ascii_alphanumeric(protected[index - 1]):
            index += 1
            continue
        end = _scan_scientific_structure(protected, index)
        if end is None:
            index += 1
            continue
        spans.append((index, end))
        index = end
    return spans


def contains_scientific_structure(value: Any) -> bool:
    """Return whether text contains a reliable molecular atomic subsequence."""

    return isinstance(value, str) and bool(_scientific_structure_spans(value))


def contains_prefixed_scientific_identifier(value: Any) -> bool:
    """Return whether an identifier prefixes a scientific suffix with private semantics."""

    if not isinstance(value, str):
        return False
    normalized = value.lower()
    for index in range(len(normalized)):
        for prefix in _PRIVATE_SCIENTIFIC_PREFIXES:
            if not normalized.startswith(prefix, index):
                continue
            suffix_start = index + len(prefix)
            if contains_scientific_structure(value[suffix_start:]):
                return True
    return False


def _redact_scientific_structures(value: str) -> str:
    spans = _scientific_structure_spans(value)
    if not spans:
        return value
    result: list[str] = []
    previous_end = 0
    for start, end in spans:
        result.extend((value[previous_end:start], "[PRIVATE]"))
        previous_end = end
    result.append(value[previous_end:])
    return "".join(result)


def _sanitize_generic_result_text(value: str) -> str:
    sanitized, changed = sanitize_sensitive_text(
        value,
        max_chars=RESULT_PROJECTION_MAX_TEXT_LENGTH,
    )
    if changed:
        marker = (
            "[PATH]"
            if any(
                pattern.search(value)
                for pattern in (
                    _FILE_URI_TEXT,
                    _WINDOWS_PATH_TEXT,
                    _UNC_PATH_TEXT,
                    _POSIX_PATH_TEXT,
                )
            )
            else "[REDACTED]"
        )
        sanitized = sanitized.replace("[redacted]", marker)
    if contains_scientific_structure(sanitized):
        return "[PRIVATE]"
    return sanitized.strip()


def _sanitize_generic_result_value(
    value: Any,
    *,
    key: str | None,
    depth: int,
    budget: list[int],
) -> Any:
    if depth > RESULT_PROJECTION_MAX_DEPTH:
        return _DROP
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if budget[0] >= RESULT_PROJECTION_MAX_ITEMS:
                break
            budget[0] += 1
            if not _is_safe_generic_result_key(child_key):
                continue
            sanitized = _sanitize_generic_result_value(
                child_value,
                key=child_key,
                depth=depth + 1,
                budget=budget,
            )
            if sanitized is not _DROP:
                result[child_key] = sanitized
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            if budget[0] >= RESULT_PROJECTION_MAX_ITEMS:
                break
            budget[0] += 1
            sanitized = _sanitize_generic_result_value(
                item,
                key=None,
                depth=depth + 1,
                budget=budget,
            )
            if sanitized is not _DROP:
                result.append(sanitized)
        return result
    if isinstance(value, float) and not math.isfinite(value):
        return _DROP
    if isinstance(value, str):
        sanitized = _sanitize_generic_result_text(value)
        if not sanitized:
            return _DROP
        if key is not None and key.lower() == "status":
            lowered = sanitized.strip().lower()
            if lowered in _RESULT_STATUS_VALUES:
                return lowered
        return sanitized
    if value is None or type(value) in {bool, int, float}:
        return value
    return _DROP


def sanitize_task_result_projection(value: Any) -> Any:
    """Return the strict scientific task projection persisted by async backends."""

    sanitized = _sanitize_scientific_result_object(
        value,
        schema_name="root",
        depth=0,
        budget=[0],
    )
    return (
        None
        if sanitized is _DROP or sanitized is _PROJECTION_CONFLICT
        else sanitized
    )


def sanitize_generic_task_result_projection(value: Any) -> Any:
    """Return a bounded, redacted projection for legacy non-scientific tasks."""

    sanitized = _sanitize_generic_result_value(
        value,
        key=None,
        depth=0,
        budget=[0],
    )
    return None if sanitized is _DROP else sanitized


def project_task_result(value: Any, policy: ResultProjectionPolicy) -> Any:
    if not isinstance(policy, ResultProjectionPolicy):
        raise ValueError("invalid result projection policy")
    if policy is ResultProjectionPolicy.SCIENTIFIC_STRICT:
        return sanitize_task_result_projection(value)
    return sanitize_generic_task_result_projection(value)


def sanitize_public_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    allowed_keys = (
        "artifact_type",
        "name",
        "type",
        "path",
        "hash",
        "sha256",
        "size",
        "status",
    )
    for artifact in value:
        if not isinstance(artifact, dict):
            continue
        has_type_alias = "artifact_type" in artifact or "type" in artifact
        if has_type_alias and canonical_artifact_type(artifact) is None:
            continue
        path = artifact.get("path")
        decoded_path = (
            _decode_relative_artifact_path(path) if "path" in artifact else None
        )
        if decoded_path is not None:
            path_is_sensitive = False
            for segment in decoded_path.split("/"):
                basename = segment.rsplit(".", 1)[0]
                if any(
                    contains_sensitive_text(candidate)
                    or contains_scientific_structure(candidate)
                    or contains_prefixed_scientific_identifier(candidate)
                    for candidate in {segment, basename}
                ):
                    path_is_sensitive = True
                    break
            if path_is_sensitive:
                continue
        invalid_identity = False
        for identity_key in ("artifact_type", "name", "type", "status"):
            if identity_key not in artifact:
                continue
            item = artifact[identity_key]
            if not isinstance(item, str):
                invalid_identity = True
                break
            if identity_key in {"artifact_type", "type"}:
                valid = item in _ARTIFACT_TYPES
            elif identity_key == "status":
                valid = item in _ARTIFACT_STATUSES
            else:
                valid = (
                    _SAFE_ARTIFACT_CODE.fullmatch(item) is not None
                    and not contains_sensitive_text(item)
                    and not contains_scientific_structure(item)
                    and not contains_prefixed_scientific_identifier(item)
                )
            if not valid:
                invalid_identity = True
                break
        if invalid_identity:
            continue
        public: dict[str, Any] = {}
        for key in allowed_keys:
            if key not in artifact:
                continue
            item = artifact[key]
            if key == "path":
                if decoded_path is not None:
                    public[key] = item
                continue
            if key in {"hash", "sha256"}:
                try:
                    public[key] = _validate_digest(item)
                except ValueError:
                    pass
                continue
            if key in {"artifact_type", "name", "type", "status"}:
                public[key] = item
                continue
            if key == "size":
                if type(item) in {int, float} and item >= 0 and (
                    not isinstance(item, float) or math.isfinite(item)
                ):
                    public[key] = item
        metadata = artifact.get("metadata")
        if "sha256" not in public and isinstance(metadata, dict):
            try:
                public["sha256"] = _validate_digest(metadata.get("sha256"))
            except ValueError:
                pass
        result.append(public)
    return result


@dataclass
class TaskRecord:
    task_id: str
    task_type: str
    status: TaskStatus
    input: dict[str, Any]
    backend: str = "local"
    external_workflow_id: str | None = None
    phase: str | None = None
    progress: float = 0.0
    attempt: int = 0
    heartbeat_at: str | None = None
    error_code: str | None = None
    warnings: list[Any] | None = None
    input_manifest_path: str | None = None
    provenance: dict[str, Any] | None = None
    result: Any = None
    error: str | None = None
    artifacts: list[dict[str, Any]] | None = None
    created_at: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TaskRecord":
        import json

        def parse_json(
            value: Any, default: Any, expected_type: type | None
        ) -> Any:
            if not value:
                return default
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
            except (json.JSONDecodeError, TypeError, ValueError):
                return default
            if expected_type is None or isinstance(parsed, expected_type):
                return parsed
            return default

        try:
            status = TaskStatus(row.get("status", TaskStatus.QUEUED.value))
        except (TypeError, ValueError):
            status = TaskStatus.QUEUED

        def parse_progress(value: Any) -> float:
            if isinstance(value, bool):
                return 0.0
            try:
                parsed = float(value or 0.0)
            except (TypeError, ValueError, OverflowError):
                return 0.0
            if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
                return 0.0
            return parsed

        def parse_attempt(value: Any) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return 0
            return value

        def parse_error_code(value: Any) -> str | None:
            if value is None:
                return None
            try:
                return TaskErrorCode(value).value
            except (TypeError, ValueError):
                return None

        return cls(
            task_id=row["task_id"],
            task_type=row["task_type"],
            status=status,
            input=parse_json(row.get("input_json"), {}, dict),
            backend=row.get("backend") or "local",
            external_workflow_id=row.get("external_workflow_id"),
            phase=row.get("phase"),
            progress=parse_progress(row.get("progress")),
            attempt=parse_attempt(row.get("attempt")),
            heartbeat_at=row.get("heartbeat_at"),
            error_code=parse_error_code(row.get("error_code")),
            warnings=parse_json(row.get("warnings_json"), [], list),
            input_manifest_path=row.get("input_manifest_path"),
            provenance=parse_json(row.get("provenance_json"), {}, dict),
            result=parse_json(row.get("result_json"), None, None),
            error=row.get("error"),
            artifacts=parse_json(row.get("artifacts_json"), [], list),
            created_at=row.get("created_at"),
            started_at=row.get("started_at"),
            updated_at=row.get("updated_at"),
            finished_at=row.get("finished_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "input": self.input,
            "backend": self.backend,
            "external_workflow_id": self.external_workflow_id,
            "phase": self.phase,
            "progress": self.progress,
            "attempt": self.attempt,
            "heartbeat_at": self.heartbeat_at,
            "error_code": self.error_code,
            "warnings": self.warnings or [],
            "input_manifest_path": self.input_manifest_path,
            "provenance": self.provenance or {},
            "result": self.result,
            "error": self.error,
            "artifacts": self.artifacts or [],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        public = self.to_dict()
        for key in (
            "input",
            "input_manifest_path",
            "provenance",
            "warnings",
            "artifacts",
            "result",
            "error",
        ):
            public.pop(key, None)
        public = sanitize_public_task_data(public)
        public["provenance"] = sanitize_public_provenance(self.provenance or {})
        public["warnings"] = normalize_task_warnings(
            self.warnings or [], strict=False
        )
        public["artifacts"] = sanitize_public_artifacts(self.artifacts or [])
        public["result"] = sanitize_public_result(self.result)
        if isinstance(self.error, str):
            public["error"] = sanitize_task_message(self.error)
        else:
            public["error"] = None
        return public


@dataclass(frozen=True)
class TaskEvent:
    event_id: str
    task_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    is_terminal: bool
    created_at: str
