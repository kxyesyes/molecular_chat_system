from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class TaskRecord:
    task_id: str
    task_type: str
    status: TaskStatus
    input: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    artifacts: list[dict[str, Any]] | None = None
    created_at: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TaskRecord":
        import json

        def parse_json(value: str | None, default: Any) -> Any:
            if not value:
                return default
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default

        return cls(
            task_id=row["task_id"],
            task_type=row["task_type"],
            status=TaskStatus(row["status"]),
            input=parse_json(row.get("input_json"), {}),
            result=parse_json(row.get("result_json"), None),
            error=row.get("error"),
            artifacts=parse_json(row.get("artifacts_json"), []),
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
            "result": self.result,
            "error": self.error,
            "artifacts": self.artifacts or [],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }
