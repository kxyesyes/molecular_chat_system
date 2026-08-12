from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import re
from typing import Any

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
    PREPARING = "preparing"
    DOCKING = "docking"
    VINA = "vina"
    VALIDATING = "validating"
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
    | {"projection_stale"}
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
_DROP = object()


def strict_json_snapshot(value: Any) -> Any:
    """Return a detached JSON-compatible value and reject non-finite data."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("invalid JSON non-finite number")
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid JSON object key")
            result[key] = strict_json_snapshot(item)
        return result
    if isinstance(value, (list, tuple)):
        return [strict_json_snapshot(item) for item in value]
    raise ValueError("invalid JSON value")


def strict_json_roundtrip(value: Any) -> Any:
    snapshot = strict_json_snapshot(value)
    encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    return json.loads(encoded)


def normalize_warning_codes(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("invalid warning codes")
    normalized: list[str] = []
    for value in values:
        code = value.value if isinstance(value, TaskErrorCode) else value
        if not isinstance(code, str) or code not in TASK_WARNING_CODES:
            raise ValueError("invalid warning code")
        normalized.append(code)
    return normalized


def sanitize_provenance(value: Any) -> dict[str, Any]:
    snapshot = strict_json_snapshot(value or {})
    if not isinstance(snapshot, dict):
        raise ValueError("invalid provenance")
    allowed: dict[str, Any] = {}
    for key, item in snapshot.items():
        normalized = key.lower()
        if normalized not in PROVENANCE_KEYS and not (
            normalized.endswith("_hash") or normalized.endswith("_sha256")
        ):
            continue
        sanitized = _sanitize_public_value(item, key=normalized)
        if sanitized is not _DROP:
            allowed[key] = sanitized
    return redact_sensitive(allowed)


def sanitize_public_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        normalized = key.lower()
        if normalized not in PROVENANCE_KEYS and not (
            normalized.endswith("_hash") or normalized.endswith("_sha256")
        ):
            continue
        sanitized = _sanitize_public_value(item, key=normalized)
        if sanitized is not _DROP:
            allowed[key] = sanitized
    return redact_sensitive(allowed)


def _is_absolute_path(value: str) -> bool:
    return (
        bool(_WINDOWS_ABSOLUTE_PATH.search(value))
        or value.startswith("/")
        or value.startswith("\\\\")
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
    if isinstance(value, str) and _is_absolute_path(value):
        return _DROP
    if value is None or isinstance(value, (str, bool, int, float)):
        return redact_sensitive(value)
    return _DROP


def sanitize_public_task_data(value: Any) -> Any:
    sanitized = _sanitize_public_value(value)
    return {} if sanitized is _DROP else sanitized


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
        public.pop("input", None)
        public.pop("input_manifest_path", None)
        public["provenance"] = sanitize_public_provenance(self.provenance or {})
        return sanitize_public_task_data(public)


@dataclass(frozen=True)
class TaskEvent:
    event_id: str
    task_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    is_terminal: bool
    created_at: str
