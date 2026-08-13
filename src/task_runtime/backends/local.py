from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..errors import TaskErrorCode
from ..models import (
    BackendHealth,
    TaskPhase,
    TaskRecord,
    TaskStatus,
    TaskSubmission,
    normalize_task_warnings,
    sanitize_provenance,
    sanitize_public_artifacts,
    sanitize_public_result,
    sanitize_task_message,
    strict_json_snapshot,
)
from ..store import TERMINAL_STATUSES, TaskStore
from .base import BackendSubmitResult, StartOutcome


ProgressCallback = Callable[..., None]
TaskHandler = Callable[
    [TaskSubmission, threading.Event, ProgressCallback],
    Awaitable[dict[str, Any]],
]

_AGENT_ERROR_MAP = {
    "invalid_input": TaskErrorCode.TASK_INPUT_INVALID,
    "validation_error": TaskErrorCode.TASK_INPUT_INVALID,
    "invalid_output": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
    "empty_result": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
    "tool_unavailable": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE,
    "external_tool_unavailable": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE,
    "model_unavailable": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE,
    "tool_timeout": TaskErrorCode.DOCKING_PROCESS_FAILED,
    "provider_error": TaskErrorCode.DOCKING_PROCESS_FAILED,
    "internal_error": TaskErrorCode.DOCKING_PROCESS_FAILED,
}


class LocalTaskBackend:
    """Async façade over in-process handlers with durable task projection."""

    def __init__(self, store: TaskStore, handlers: Mapping[str, TaskHandler]) -> None:
        if not isinstance(store, TaskStore):
            raise TypeError("store must be a TaskStore")
        if not isinstance(handlers, Mapping):
            raise TypeError("handlers must be a mapping")
        checked: dict[str, TaskHandler] = {}
        for task_type, handler in handlers.items():
            if type(task_type) is not str or not task_type or not callable(handler):
                raise ValueError("invalid handler registry")
            if not asyncio.iscoroutinefunction(handler):
                raise TypeError("local task handlers must be async")
            checked[task_type] = handler
        self.store = store
        self._handlers = checked
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = threading.RLock()
        self._closed = False

    @property
    def background_task_count(self) -> int:
        with self._lock:
            return sum(not task.done() for task in self._tasks.values())

    async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
        if not isinstance(submission, TaskSubmission):
            raise TypeError("submission must be a TaskSubmission")
        handler = self._handlers.get(submission.task_type)
        if handler is None:
            raise ValueError(f"unknown task_type: {submission.task_type}")

        with self._lock:
            if self._closed:
                raise RuntimeError("local backend is closed")
            if submission.idempotency_key is not None:
                prior_task_id = self._idempotency.get(submission.idempotency_key)
                if prior_task_id is not None:
                    return self._accepted(prior_task_id, "duplicate idempotency key")
            if submission.task_id in self._tasks:
                return self._accepted(submission.task_id, "duplicate task id")
            try:
                self.store.get(submission.task_id)
            except KeyError:
                pass
            else:
                if submission.idempotency_key is not None:
                    self._idempotency[submission.idempotency_key] = submission.task_id
                return self._accepted(submission.task_id, "existing task projection")

            provenance = submission.payload.get("decision", submission.payload)
            if not isinstance(provenance, dict):
                provenance = {}
            try:
                self.store.create(
                    submission.task_id,
                    submission.task_type,
                    submission.payload,
                    backend="local",
                    provenance=provenance,
                )
            except sqlite3.IntegrityError:
                return self._accepted(submission.task_id, "existing task projection")

            cancel_event = threading.Event()
            task = asyncio.get_running_loop().create_task(
                self._run(submission, handler, cancel_event),
                name=f"medchat-local-{submission.task_id}",
            )
            self._cancel_events[submission.task_id] = cancel_event
            self._tasks[submission.task_id] = task
            if submission.idempotency_key is not None:
                self._idempotency[submission.idempotency_key] = submission.task_id
            task.add_done_callback(
                lambda completed, task_id=submission.task_id: self._task_done(
                    task_id, completed
                )
            )
        return self._accepted(submission.task_id, "local task scheduled")

    async def get(self, task_id: str) -> TaskRecord:
        return self.store.get(task_id)

    async def list(
        self,
        limit: int = 20,
        status: str | TaskStatus | None = None,
        task_type: str | None = None,
    ) -> list[TaskRecord]:
        return self.store.list(limit=limit, status=status, task_type=task_type)

    async def cancel(self, task_id: str, reason: str | None = None) -> TaskRecord:
        record = self.store.request_cancel(task_id, reason=reason)
        with self._lock:
            cancel_event = self._cancel_events.get(task_id)
            if cancel_event is not None:
                cancel_event.set()
        return record

    async def health(self) -> BackendHealth:
        with self._lock:
            available = not self._closed
            running = sum(not task.done() for task in self._tasks.values())
        return BackendHealth(
            backend="local",
            available=available,
            message="available" if available else "closed",
            details={"running": running},
        )

    async def close(self) -> None:
        with self._lock:
            if self._closed and not self._tasks:
                return
            self._closed = True
            pending = list(self._tasks.items())

        for task_id, task in pending:
            if task.done():
                continue
            try:
                record = self.store.request_cancel(task_id, reason="backend shutdown")
            except KeyError:
                record = None
            with self._lock:
                cancel_event = self._cancel_events.get(task_id)
                if cancel_event is not None:
                    cancel_event.set()
            if record is not None and record.status not in TERMINAL_STATUSES:
                task.cancel()

        if pending:
            await asyncio.gather(
                *(task for _, task in pending),
                return_exceptions=True,
            )
        for task_id, _ in pending:
            self._finish_requested_cancellation(task_id)
        with self._lock:
            self._tasks.clear()
            self._cancel_events.clear()

    shutdown = close

    async def _run(
        self,
        submission: TaskSubmission,
        handler: TaskHandler,
        cancel_event: threading.Event,
    ) -> None:
        task_id = submission.task_id
        raw_decision = submission.payload.get("decision", submission.payload)
        base_provenance = sanitize_provenance(
            raw_decision if isinstance(raw_decision, dict) else {}
        )
        try:
            if not self.store.claim_running(
                task_id,
                phase=TaskPhase.RUNNING,
                attempt=1,
            ):
                self._finish_requested_cancellation(task_id)
                return
            if cancel_event.is_set() or self._is_cancel_requested(task_id):
                self._ensure_canceled(task_id)
                return

            result = await handler(
                submission,
                cancel_event,
                self._progress_callback(task_id, base_provenance),
            )
            self._project_result(task_id, result, cancel_event, base_provenance)
        except asyncio.CancelledError:
            cancel_event.set()
            self._ensure_canceled(task_id)
            raise
        except Exception as exc:
            if cancel_event.is_set() or self._is_cancel_requested(task_id):
                self._ensure_canceled(task_id)
            else:
                self._finish_failed(
                    task_id,
                    error=sanitize_task_message(str(exc) or "Task handler failed"),
                    error_code=TaskErrorCode.DOCKING_PROCESS_FAILED,
                )

    def _project_result(
        self,
        task_id: str,
        raw_result: Any,
        cancel_event: threading.Event,
        base_provenance: dict[str, Any],
    ) -> None:
        try:
            result = strict_json_snapshot(raw_result)
        except ValueError:
            result = None
        if not isinstance(result, dict) or type(result.get("success")) is not bool:
            if cancel_event.is_set() or self._is_cancel_requested(task_id):
                self._ensure_canceled(task_id)
                return
            self._finish_failed(
                task_id,
                error="Scientific task result validation failed",
                error_code=TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
            )
            return

        raw_code = result.get("error_code")
        status_text = str(result.get("status", "")).strip().lower()
        if (
            cancel_event.is_set()
            or self._is_cancel_requested(task_id)
            or raw_code == "cancelled"
            or status_text in {"cancelled", "canceled"}
        ):
            self._ensure_canceled(task_id)
            return

        safe_result = sanitize_public_result(result)
        if not isinstance(safe_result, dict):
            self._finish_failed(
                task_id,
                error="Scientific task result validation failed",
                error_code=TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
            )
            return
        artifacts = sanitize_public_artifacts(result.get("artifacts", []))
        try:
            warnings = (
                normalize_task_warnings(result["warnings"])
                if "warnings" in result
                else None
            )
            provenance = (
                {
                    **sanitize_provenance(result["provenance"]),
                    **base_provenance,
                }
                if "provenance" in result
                else None
            )
        except ValueError:
            self._finish_failed(
                task_id,
                error="Scientific task result validation failed",
                error_code=TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
            )
            return

        if result["success"] is True and status_text != "failed":
            finished = self.store.finish(
                task_id,
                TaskStatus.SUCCEEDED,
                result=safe_result,
                artifacts=artifacts,
                warnings=warnings,
                provenance=provenance,
            )
            if not finished:
                self._finish_requested_cancellation(task_id)
            return

        error = result.get("error") or result.get("message") or "Task returned a failed result"
        self._finish_failed(
            task_id,
            result=safe_result,
            error=sanitize_task_message(str(error)),
            artifacts=artifacts,
            error_code=self._map_error_code(raw_code),
            warnings=warnings,
            provenance=provenance,
        )

    def _progress_callback(
        self,
        task_id: str,
        base_provenance: dict[str, Any],
    ) -> ProgressCallback:
        def progress_callback(
            phase: TaskPhase | str,
            progress: float | int,
            *,
            attempt: int | None = None,
            warnings: list[Any] | None = None,
            provenance: dict[str, Any] | None = None,
        ) -> None:
            canonical_phase = phase if isinstance(phase, TaskPhase) else TaskPhase(phase)
            canonical_progress: float
            if (
                type(progress) in {int, float}
                and not isinstance(progress, bool)
                and 1 < float(progress) <= 100
            ):
                canonical_progress = float(progress) / 100.0
            else:
                canonical_progress = float(progress)
            merged_provenance = None
            if provenance is not None:
                merged_provenance = {
                    **sanitize_provenance(provenance),
                    **base_provenance,
                }
            self.store.heartbeat(
                task_id,
                phase=canonical_phase,
                progress=canonical_progress,
                attempt=attempt,
                warnings=warnings,
                provenance=merged_provenance,
            )

        return progress_callback

    def _finish_failed(
        self,
        task_id: str,
        *,
        result: Any = None,
        error: str,
        artifacts: list[dict[str, Any]] | None = None,
        error_code: TaskErrorCode,
        warnings: list[Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        finished = self.store.finish(
            task_id,
            TaskStatus.FAILED,
            result=result,
            error=error,
            artifacts=artifacts,
            error_code=error_code,
            warnings=warnings,
            provenance=provenance,
        )
        if not finished:
            self._finish_requested_cancellation(task_id)

    def _is_cancel_requested(self, task_id: str) -> bool:
        try:
            return self.store.get(task_id).status is TaskStatus.CANCEL_REQUESTED
        except KeyError:
            return False

    def _ensure_canceled(self, task_id: str) -> None:
        try:
            record = self.store.get(task_id)
        except KeyError:
            return
        if record.status is TaskStatus.RUNNING:
            record = self.store.request_cancel(task_id, reason="handler canceled")
        if record.status is TaskStatus.CANCEL_REQUESTED:
            self.store.finish(task_id, TaskStatus.CANCELED)

    def _finish_requested_cancellation(self, task_id: str) -> None:
        if self._is_cancel_requested(task_id):
            self.store.finish(task_id, TaskStatus.CANCELED)

    def _task_done(self, task_id: str, task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass
        with self._lock:
            if self._tasks.get(task_id) is task:
                self._tasks.pop(task_id, None)
                self._cancel_events.pop(task_id, None)

    @staticmethod
    def _accepted(task_id: str, message: str) -> BackendSubmitResult:
        return BackendSubmitResult(
            task_id=task_id,
            backend="local",
            outcome=StartOutcome.ACCEPTED,
            message=message,
        )

    @staticmethod
    def _map_error_code(value: Any) -> TaskErrorCode:
        if isinstance(value, TaskErrorCode):
            return value
        if isinstance(value, str):
            try:
                return TaskErrorCode(value)
            except ValueError:
                return _AGENT_ERROR_MAP.get(
                    value.strip().lower(),
                    TaskErrorCode.DOCKING_PROCESS_FAILED,
                )
        return TaskErrorCode.DOCKING_PROCESS_FAILED
