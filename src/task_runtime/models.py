from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
import re
from typing import Any
from urllib.parse import unquote

from src.agent.persistence.redaction import redact_sensitive

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
    RECEPTOR_PREPARATION = "receptor_preparation"
    LIGAND_PREPARATION = "ligand_preparation"
    VINA_RUNNING = "vina_running"
    SCIENTIFIC_VALIDATION = "scientific_validation"
    COMPLETING = "completing"
    CANCELING = "canceling"


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
        "hash",
        "hashes",
    }
)
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
_WINDOWS_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]")
_WINDOWS_PATH_TEXT = re.compile(r"(?i)(?:[A-Z]:[\\/])[^\s;,]+")
_UNC_PATH_TEXT = re.compile(r"\\\\[^\s\\/]+[\\/][^\s;,]+")
_POSIX_PATH_TEXT = re.compile(r"(?<![A-Za-z0-9.])/(?:[^\s/;,]+/)*[^\s;,]+")
_FILE_URI_TEXT = re.compile(r"(?i)file://[^\s;,]+")
_BEARER_TEXT = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_API_KEY_TEXT = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_SMILES_TEXT = re.compile(r"(?<![A-Za-z0-9])(?:(?:Cl|Br|[BCNOFPSI])){3,}(?![A-Za-z0-9])")
_SMILES_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z0-9@+\-\[\]()=#$\\/.%]+)(?![A-Za-z0-9])"
)
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ENCODED_PATH_SEMANTICS = re.compile(r"%(?:2e|2f|5c)", re.IGNORECASE)
_SAFE_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_SAFE_DIGEST = re.compile(r"[A-Fa-f0-9]{64}\Z")
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
        _validate_runtime_code(decision["backend"], "backend")
        _validate_runtime_code(decision["reason"], "reason")
        bucket = decision["bucket"]
        if bucket is not None and (
            type(bucket) is not int or not 0 <= bucket < 100
        ):
            raise ValueError("invalid task payload bucket")
        percent = decision["percent"]
        if type(percent) is not int or not 0 <= percent <= 100:
            raise ValueError("invalid task payload percent")

    direct_keys = {"backend", "reason", "bucket", "percent"}
    if set(payload) == direct_keys:
        validate_decision(payload)
        return
    if set(payload) != {"decision", "mode", "total_bytes", "config_hash"}:
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
        object.__setattr__(
            self,
            "payload",
            _safe_runtime_metadata(self.payload, "task payload"),
        )
        _validate_submission_payload_schema(self.payload)


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
            _safe_runtime_metadata(self.details, "backend health details"),
        )


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
    message = _SMILES_TEXT.sub("[PRIVATE]", message)
    message = _SMILES_CANDIDATE.sub(_replace_smiles_candidate, message)
    return message[:TASK_WARNING_MESSAGE_MAX_LENGTH].strip()


def _replace_smiles_candidate(match: re.Match[str]) -> str:
    candidate = match.group(0)
    if candidate in {"[PATH]", "[PRIVATE]", "[REDACTED]"}:
        return candidate
    has_atom = bool(re.search(r"[BCNOFPSIcnosp]", candidate))
    has_structure = bool(re.search(r"[\d@\[\]()=#$]", candidate))
    if len(candidate) >= 4 and has_atom and has_structure:
        return "[PRIVATE]"
    return candidate


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
    if not is_digest_label and _is_private_content_key(normalized):
        raise ValueError("invalid provenance hash identifier")
    return value


def sanitize_provenance(value: Any) -> dict[str, Any]:
    snapshot = strict_json_snapshot(value or {})
    if not isinstance(snapshot, dict):
        raise ValueError("invalid provenance")
    allowed: dict[str, Any] = {}
    for key, item in snapshot.items():
        normalized = key.lower()
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
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        normalized = key.lower()
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


def is_safe_relative_artifact_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if "?" in value or "#" in value or "\\" in value:
        return False
    decoded = value
    for _ in range(8):
        if _ENCODED_PATH_SEMANTICS.search(decoded):
            return False
        try:
            next_decoded = unquote(decoded, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return False
        if next_decoded == decoded:
            break
        decoded = next_decoded
    else:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        return False
    if "?" in decoded or "#" in decoded or "\\" in decoded:
        return False
    if (
        _URI_SCHEME.match(value)
        or _URI_SCHEME.match(decoded)
        or _is_absolute_path(value)
        or _is_absolute_path(decoded)
    ):
        return False
    original_parts = value.split("/")
    decoded_parts = decoded.split("/")
    return bool(original_parts) and all(
        part not in {"", ".", ".."} for part in (*original_parts, *decoded_parts)
    )


def _is_private_content_key(value: str) -> bool:
    normalized = value.lower()
    if normalized in _PRIVATE_CONTENT_KEYS or "manifest" in normalized:
        return True
    tokens = {token for token in re.split(r"[^a-z0-9]+", normalized) if token}
    return bool(
        tokens & {"smiles", "query", "prompt", "receptor", "ligand", "input"}
    )


def _sanitize_public_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower()
    if _is_private_content_key(normalized_key):
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
    if (
        sanitize_public_task_data(snapshot) != snapshot
        or _runtime_metadata_contains_smiles(snapshot)
    ):
        raise ValueError("sensitive task payload")
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


def _sanitize_task_result_projection_value(
    value: Any,
    *,
    key: str | None = None,
) -> Any:
    normalized_key = (key or "").lower()
    if "smiles" in normalized_key:
        return _DROP
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                continue
            sanitized = _sanitize_task_result_projection_value(
                child_value,
                key=child_key,
            )
            if sanitized is not _DROP:
                result[child_key] = sanitized
        return redact_sensitive(result)
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            sanitized = _sanitize_task_result_projection_value(item)
            if sanitized is not _DROP:
                result.append(sanitized)
        return redact_sensitive(result)
    if isinstance(value, float) and not math.isfinite(value):
        return _DROP
    if isinstance(value, str):
        if _has_unsafe_result_path_semantics(value):
            return _DROP
        if _runtime_metadata_contains_smiles(value, normalized_key):
            return _DROP
        if any(marker in normalized_key for marker in ("path", "file", "uri")):
            if not is_safe_relative_artifact_path(value):
                return _DROP
        return redact_sensitive(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _DROP


def sanitize_task_result_projection(value: Any) -> Any:
    """Return the strict scientific task projection persisted by async backends."""

    sanitized = _sanitize_task_result_projection_value(value)
    return None if sanitized is _DROP else sanitized


def sanitize_public_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    allowed_keys = {"name", "type", "path", "hash", "sha256", "size", "status"}
    for artifact in value:
        if not isinstance(artifact, dict):
            continue
        public: dict[str, Any] = {}
        for key in allowed_keys:
            if key not in artifact:
                continue
            item = artifact[key]
            if key == "path":
                if is_safe_relative_artifact_path(item):
                    public[key] = item
                continue
            if key in {"hash", "sha256"}:
                try:
                    public[key] = _validate_digest(item)
                except ValueError:
                    pass
                continue
            sanitized = _sanitize_public_value(item, key=key)
            if sanitized is not _DROP:
                public[key] = sanitized
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
