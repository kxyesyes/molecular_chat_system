"""Temporal Activities for verified docking and SQLite state projection."""

from __future__ import annotations

import asyncio
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import ResultProjectionPolicy, TaskPhase, TaskStatus
from src.task_runtime.store import TaskStore


DOCKING_ACTIVITY_NAME = "run_docking_activity"
PROJECTION_ACTIVITY_NAME = "project_task_activity"

_CONTROLLED_PHASES = frozenset(item.value for item in TaskPhase)
_ERROR_CODE_MAP = {
    "invalid_input": TaskErrorCode.TASK_INPUT_INVALID.value,
    "invalid_output": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
    "validation_error": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
    "tool_unavailable": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value,
    "external_tool_unavailable": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value,
    "model_unavailable": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value,
    "tool_timeout": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
    "provider_error": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
    "internal_error": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
}
_SAFE_PROVENANCE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass
class _ProgressSlot:
    """A bounded latest-value slot shared with one synchronous worker thread."""

    _phase: str = TaskPhase.RUNNING.value
    _progress: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, phase: Any, progress: Any) -> None:
        if not isinstance(phase, str) or phase not in _CONTROLLED_PHASES:
            return
        if isinstance(progress, bool) or not isinstance(progress, (int, float)):
            return
        parsed = float(progress)
        if not math.isfinite(parsed):
            return
        if parsed > 1.0:
            parsed /= 100.0
        if not 0.0 <= parsed <= 1.0:
            return
        with self._lock:
            self._phase = phase
            self._progress = parsed

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"phase": self._phase, "progress": self._progress}


def _validate_docking_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict) or set(payload) != {
        "task_id",
        "manifest_locator",
    }:
        raise ValueError("invalid docking activity payload")
    task_id = payload["task_id"]
    manifest_locator = payload["manifest_locator"]
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise ValueError("invalid task_id")
    if (
        not isinstance(manifest_locator, str)
        or not manifest_locator
        or len(manifest_locator) > 4096
    ):
        raise ValueError("invalid manifest locator")
    return task_id, manifest_locator


def _structured_error_code(result: dict[str, Any]) -> str:
    error = result.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str):
        return _ERROR_CODE_MAP.get(code, TaskErrorCode.DOCKING_PROCESS_FAILED.value)
    return TaskErrorCode.DOCKING_PROCESS_FAILED.value


def _safe_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    safe: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        artifact_type = item.get("type") or item.get("artifact_type")
        if not _is_safe_relative_path(path) or not isinstance(artifact_type, str):
            continue
        record: dict[str, Any] = {"type": artifact_type, "path": path}
        metadata = item.get("metadata")
        digest = metadata.get("sha256") if isinstance(metadata, dict) else None
        if isinstance(digest, str):
            record["metadata"] = {"sha256": digest}
        safe.append(record)
    return safe


def _is_safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        return False
    parts = normalized.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _safe_provenance(value: Any, attempt: int) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {
        "tool": "molecular_docking",
        "attempt": attempt,
    }
    for key in ("tool_name", "tool_version", "model_name", "model_version"):
        candidate = source.get(key)
        if (
            isinstance(candidate, str)
            and _SAFE_PROVENANCE_IDENTIFIER.fullmatch(candidate) is not None
        ):
            result[key] = candidate
    for key in ("demo_mode", "fallback_used"):
        candidate = source.get(key)
        if isinstance(candidate, bool):
            result[key] = candidate
    return result


def _summarize_docking_result(
    result: Any,
    *,
    task_id: str,
    attempt: int,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "task_id": task_id,
            "attempt": attempt,
            "status": TaskStatus.FAILED.value,
            "error_code": TaskErrorCode.DOCKING_PROCESS_FAILED.value,
        }
    status = result.get("status")
    if status in {"cancelled", "canceled"}:
        return {
            "task_id": task_id,
            "attempt": attempt,
            "status": TaskStatus.CANCELED.value,
        }
    if result.get("success") is not True or status != "succeeded":
        return {
            "task_id": task_id,
            "attempt": attempt,
            "status": TaskStatus.FAILED.value,
            "error_code": _structured_error_code(result),
        }

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    best_pose = data.get("best_pose") if isinstance(data.get("best_pose"), dict) else {}
    pose_count = data.get("total_poses")
    best_energy = best_pose.get("binding_energy")
    pose_file = best_pose.get("pose_file") or data.get("pose_file")
    if (
        type(pose_count) is not int
        or pose_count <= 0
        or type(best_energy) not in (int, float)
        or not math.isfinite(float(best_energy))
        or not _is_safe_relative_path(pose_file)
    ):
        return {
            "task_id": task_id,
            "attempt": attempt,
            "status": TaskStatus.FAILED.value,
            "error_code": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
        }
    return {
        "task_id": task_id,
        "attempt": attempt,
        "status": TaskStatus.SUCCEEDED.value,
        "result": {
            "pose_count": pose_count,
            "best_energy": float(best_energy),
            "pose_file": pose_file,
        },
        "artifacts": _safe_artifacts(result.get("artifacts")),
        "warnings": ([{"code": "TOOL_WARNING"}] if result.get("warnings") else []),
        "provenance": _safe_provenance(result.get("provenance"), attempt),
    }


async def _run_docking_in_thread(
    docking_execution: DockingExecution,
    payload: dict[str, Any],
    *,
    attempt: int,
    heartbeat: Callable[[dict[str, Any]], None],
    heartbeat_interval: float = 1.0,
) -> dict[str, Any]:
    """Run one verified docking call and cooperatively join it on cancellation."""

    task_id, manifest_locator = _validate_docking_payload(payload)
    if type(attempt) is not int or attempt <= 0:
        raise ValueError("invalid activity attempt")
    if not isinstance(heartbeat_interval, (int, float)) or heartbeat_interval <= 0:
        raise ValueError("invalid heartbeat interval")
    cancel_event = threading.Event()
    progress = _ProgressSlot()

    work = asyncio.create_task(
        asyncio.to_thread(
            docking_execution.run_verified,
            task_id,
            manifest_locator,
            attempt=attempt,
            progress_callback=progress.update,
            cancel_event=cancel_event,
        )
    )
    try:
        while not work.done():
            heartbeat({**progress.snapshot(), "attempt": attempt})
            await asyncio.sleep(float(heartbeat_interval))
        raw_result = await work
        return _summarize_docking_result(raw_result, task_id=task_id, attempt=attempt)
    except asyncio.CancelledError:
        cancel_event.set()
        try:
            await asyncio.shield(work)
        except Exception:
            pass
        raise


class TemporalDockingActivities:
    """Bound Activity set with explicit worker-owned dependencies."""

    def __init__(self, store: TaskStore, docking_execution: DockingExecution) -> None:
        self.store = store
        self.docking_execution = docking_execution

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def run_docking_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await _run_docking_in_thread(
            self.docking_execution,
            payload,
            attempt=activity.info().attempt,
            heartbeat=activity.heartbeat,
        )

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def project_task_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RuntimeError("invalid projection payload")
        operation = payload.get("operation")
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError("invalid projection task")
        if operation == "running":
            applied = await asyncio.to_thread(
                self.store.claim_running,
                task_id,
                phase=payload.get("phase", TaskPhase.RUNNING.value),
                attempt=payload.get("attempt"),
            )
            if applied:
                return {"applied": True, "status": TaskStatus.RUNNING.value}
            return await self._idempotent_or_conflict(task_id, TaskStatus.RUNNING)
        if operation == "heartbeat":
            applied = await asyncio.to_thread(
                self.store.heartbeat,
                task_id,
                phase=payload.get("phase"),
                progress=payload.get("progress"),
                attempt=payload.get("attempt"),
            )
            if applied:
                return {"applied": True, "status": TaskStatus.RUNNING.value}
            return await self._idempotent_or_conflict(task_id, TaskStatus.RUNNING)
        if operation == "cancel_requested":
            record = await asyncio.to_thread(self.store.request_cancel, task_id)
            if record.status is not TaskStatus.CANCEL_REQUESTED:
                raise _projection_conflict()
            return {"applied": True, "status": TaskStatus.CANCEL_REQUESTED.value}
        if operation == "terminal":
            return await self._project_terminal(payload)
        raise RuntimeError("invalid projection operation")

    async def _idempotent_or_conflict(
        self,
        task_id: str,
        expected: TaskStatus,
    ) -> dict[str, Any]:
        record = await asyncio.to_thread(self.store.get, task_id)
        if record.status is expected:
            return {"applied": False, "idempotent": True, "status": expected.value}
        raise _projection_conflict()

    async def _project_terminal(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            status = TaskStatus(payload.get("status"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("invalid terminal projection") from exc
        if status not in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.TIMED_OUT,
        }:
            raise RuntimeError("invalid terminal projection")
        error_code = payload.get("error_code")
        applied = await asyncio.to_thread(
            self.store.finish,
            payload["task_id"],
            status,
            result=payload.get("result"),
            artifacts=payload.get("artifacts"),
            error_code=error_code,
            warnings=payload.get("warnings"),
            provenance=payload.get("provenance"),
            projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
        )
        if applied:
            return {"applied": True, "status": status.value}
        record = await asyncio.to_thread(self.store.get, payload["task_id"])
        if record.status is status:
            return {"applied": False, "idempotent": True, "status": status.value}
        raise _projection_conflict()


def _projection_conflict() -> ApplicationError:
    return ApplicationError(
        "Task projection conflict",
        type="TASK_PROJECTION_CONFLICT",
        non_retryable=True,
    )


__all__ = [
    "DOCKING_ACTIVITY_NAME",
    "PROJECTION_ACTIVITY_NAME",
    "TemporalDockingActivities",
]
