from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
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
        return redact_sensitive(public)


@dataclass(frozen=True)
class TaskEvent:
    event_id: str
    task_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    is_terminal: bool
    created_at: str
