from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from ..errors import TaskErrorCode
from ..models import (
    BackendHealth,
    ResultProjectionPolicy,
    TaskPhase,
    TaskRecord,
    TaskStatus,
    TaskSubmission,
    normalize_task_warnings,
    sanitize_provenance,
    sanitize_public_artifacts,
    sanitize_task_result_projection,
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
T = TypeVar("T")

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
_LOCAL_PROVENANCE = {
    "backend": "local",
    "process_restart_recovery": False,
    "durable_execution": False,
}
_MAX_PROGRESS_WARNINGS = 32


class TaskIdempotencyConflictError(ValueError):
    """A durable idempotency authority does not match this submission."""


@dataclass
class _ProgressUpdate:
    phase: TaskPhase
    progress: float
    attempt: int | None
    warnings: list[Any] | None
    provenance: dict[str, Any] | None


class _ProgressSlot:
    """One coalescing slot; callbacks never create unbounded asyncio tasks."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.event = asyncio.Event()
        self.lock = threading.Lock()
        self.latest: _ProgressUpdate | None = None
        self.attempt: int | None = None
        self.warnings: list[Any] = []
        self.invalid_warnings: Any = None
        self.provenance: dict[str, Any] = {}
        self.invalid_provenance: Any = None
        self.stopping = False

    def put(self, update: _ProgressUpdate) -> None:
        with self.lock:
            if self.stopping:
                return
            if update.attempt is not None:
                self.attempt = update.attempt
            if update.warnings is not None:
                try:
                    incoming_warnings = normalize_task_warnings(update.warnings)
                except ValueError:
                    self.invalid_warnings = update.warnings
                else:
                    for warning in incoming_warnings:
                        if warning in self.warnings:
                            continue
                        if len(self.warnings) >= _MAX_PROGRESS_WARNINGS:
                            break
                        self.warnings.append(warning)
            if update.provenance is not None:
                try:
                    incoming_provenance = sanitize_provenance(update.provenance)
                except ValueError:
                    self.invalid_provenance = update.provenance
                else:
                    self.provenance.update(incoming_provenance)
            self.latest = _ProgressUpdate(
                update.phase,
                update.progress,
                self.attempt,
                (
                    self.invalid_warnings
                    if self.invalid_warnings is not None
                    else (list(self.warnings) if self.warnings else None)
                ),
                (
                    self.invalid_provenance
                    if self.invalid_provenance is not None
                    else (dict(self.provenance) if self.provenance else None)
                ),
            )
        self.loop.call_soon_threadsafe(self.event.set)

    def take(self) -> tuple[_ProgressUpdate | None, bool]:
        with self.lock:
            update = self.latest
            self.latest = None
            return update, self.stopping

    def stop(self) -> None:
        with self.lock:
            self.stopping = True
        self.loop.call_soon_threadsafe(self.event.set)

    def size(self) -> int:
        with self.lock:
            return int(self.latest is not None)


class LocalTaskBackend:
    """Async local runner with durable projection and cooperative cancellation."""

    progress_queue_capacity = 1

    def __init__(
        self,
        store: TaskStore,
        handlers: Mapping[str, TaskHandler],
        *,
        shutdown_timeout: float | None = 30.0,
    ) -> None:
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
        if shutdown_timeout is not None and (
            isinstance(shutdown_timeout, bool)
            or not isinstance(shutdown_timeout, (int, float))
            or shutdown_timeout <= 0
        ):
            raise ValueError("invalid shutdown timeout")
        self.store = store
        self._handlers = checked
        self._shutdown_timeout = (
            None if shutdown_timeout is None else float(shutdown_timeout)
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._progress_slots: dict[str, _ProgressSlot] = {}
        self._submitting: dict[str, asyncio.Event] = {}
        self._lock = threading.RLock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._closing = False
        self._shutdown_pending = False

    @property
    def background_task_count(self) -> int:
        with self._lock:
            return sum(not task.done() for task in self._tasks.values())

    @property
    def progress_queue_size(self) -> int:
        with self._lock:
            return sum(slot.size() for slot in self._progress_slots.values())

    async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
        return await self._dispatch(lambda: self._submit_owner(submission))

    async def _submit_owner(self, submission: TaskSubmission) -> BackendSubmitResult:
        if not isinstance(submission, TaskSubmission):
            raise TypeError("submission must be a TaskSubmission")
        handler = self._handlers.get(submission.task_type)
        if handler is None:
            raise ValueError(f"unknown task_type: {submission.task_type}")
        owns_submission = False
        pending_submission: asyncio.Event | None = None
        active_task = False
        with self._lock:
            if self._closed or self._closing:
                raise RuntimeError("local backend is closed")
            active_task = submission.task_id in self._tasks
            pending_submission = self._submitting.get(submission.task_id)
            if not active_task and pending_submission is None:
                pending_submission = asyncio.Event()
                self._submitting[submission.task_id] = pending_submission
                owns_submission = True

        if not owns_submission:
            if not active_task and pending_submission is not None:
                await pending_submission.wait()
            try:
                existing, existing_request_digest = await asyncio.to_thread(
                    self._task_authority,
                    submission.task_id,
                )
            except KeyError as exc:
                raise RuntimeError("prior task submission did not create a projection") from exc
            return self._existing_task_result(
                existing,
                existing_request_digest,
                submission,
                submission.request_digest
                or self.submission_digest(submission.task_type, submission.payload),
            )

        digest = (
            self.idempotency_digest(submission.idempotency_key)
            if submission.idempotency_key is not None
            else None
        )
        request_digest = submission.request_digest or self.submission_digest(
            submission.task_type,
            submission.payload,
        )
        try:
            if digest is not None:
                try:
                    prior, prior_request_digest = await asyncio.to_thread(
                        self.store.get_idempotency_authority, digest
                    )
                except KeyError:
                    pass
                else:
                    return self._idempotent_result(
                        prior,
                        prior_request_digest,
                        submission,
                        request_digest,
                    )
            try:
                existing, existing_request_digest = await asyncio.to_thread(
                    self._task_authority,
                    submission.task_id,
                )
            except KeyError:
                pass
            else:
                return self._existing_task_result(
                    existing,
                    existing_request_digest,
                    submission,
                    request_digest,
                )

            raw_provenance = submission.payload.get("decision", submission.payload)
            provenance = self._merge_provenance(
                raw_provenance if isinstance(raw_provenance, dict) else {}
            )
            create_task = asyncio.create_task(
                asyncio.to_thread(
                    self.store.create,
                    submission.task_id,
                    submission.task_type,
                    submission.payload,
                    backend="local",
                    provenance=provenance,
                    idempotency_digest=digest,
                    submission_digest=request_digest,
                ),
                name=f"medchat-create-{submission.task_id}",
            )
            try:
                await asyncio.shield(create_task)
            except asyncio.CancelledError:
                created = False
                try:
                    await self._await_task_outcome(create_task)
                    created = True
                except Exception:
                    pass
                if created:
                    await self._cancel_unstarted_submission(submission.task_id)
                raise
            except sqlite3.IntegrityError:
                if digest is not None:
                    try:
                        prior, prior_request_digest = await asyncio.to_thread(
                            self.store.get_idempotency_authority, digest
                        )
                    except KeyError:
                        pass
                    else:
                        return self._idempotent_result(
                            prior,
                            prior_request_digest,
                            submission,
                            request_digest,
                        )
                try:
                    existing, existing_request_digest = await asyncio.to_thread(
                        self._task_authority,
                        submission.task_id,
                    )
                except KeyError:
                    raise
                return self._existing_task_result(
                    existing,
                    existing_request_digest,
                    submission,
                    request_digest,
                )

            cancel_event = threading.Event()
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self._run(submission, handler, cancel_event),
                name=f"medchat-local-{submission.task_id}",
            )
            with self._lock:
                self._cancel_events[submission.task_id] = cancel_event
                self._tasks[submission.task_id] = task
            task.add_done_callback(
                lambda completed, task_id=submission.task_id: self._task_done(
                    task_id, completed
                )
            )
            return self._accepted(submission.task_id, "local task scheduled")
        finally:
            if owns_submission:
                with self._lock:
                    completed_submission = self._submitting.pop(
                        submission.task_id,
                        None,
                    )
                if completed_submission is not None:
                    completed_submission.set()

    @staticmethod
    async def _await_task_outcome(task: asyncio.Task[T]) -> T:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    async def _cancel_unstarted_submission(self, task_id: str) -> None:
        try:
            record = await asyncio.to_thread(
                self.store.request_cancel,
                task_id,
                reason="submission canceled",
            )
        except KeyError:
            return
        if record.status is TaskStatus.CANCEL_REQUESTED:
            await asyncio.to_thread(
                self.store.finish,
                task_id,
                TaskStatus.CANCELED,
                projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
            )

    async def get(self, task_id: str) -> TaskRecord:
        return await self._dispatch(
            lambda: asyncio.to_thread(self.store.get, task_id)
        )

    async def list(
        self,
        limit: int = 20,
        status: str | TaskStatus | None = None,
        task_type: str | None = None,
    ) -> list[TaskRecord]:
        return await self._dispatch(
            lambda: asyncio.to_thread(
                self.store.list,
                limit=limit,
                status=status,
                task_type=task_type,
            )
        )

    async def cancel(self, task_id: str, reason: str | None = None) -> TaskRecord:
        return await self._dispatch(lambda: self._cancel_owner(task_id, reason))

    async def _cancel_owner(
        self,
        task_id: str,
        reason: str | None,
    ) -> TaskRecord:
        record = await asyncio.to_thread(
            self.store.request_cancel,
            task_id,
            reason=reason,
        )
        with self._lock:
            cancel_event = self._cancel_events.get(task_id)
            if cancel_event is not None:
                cancel_event.set()
        return record

    async def health(self) -> BackendHealth:
        with self._lock:
            available = not self._closed and not self._closing
            running = sum(not task.done() for task in self._tasks.values())
            shutdown_pending = self._shutdown_pending
        return BackendHealth(
            backend="local",
            available=available,
            message=(
                "shutdown pending"
                if shutdown_pending
                else ("available" if available else "closed")
            ),
            details={
                "running": running,
                "shutdown_pending": shutdown_pending,
                "process_restart_recovery": False,
                "durable_execution": False,
            },
        )

    async def close(self) -> None:
        await self._dispatch(self._close_owner, allow_closed_owner=False)

    shutdown = close

    async def _close_owner(self) -> None:
        with self._lock:
            if self._closed and not self._tasks and not self._submitting:
                return
            self._closing = True
            submissions = list(self._submitting.values())

        if submissions:
            submission_waiter = asyncio.gather(
                *(event.wait() for event in submissions),
                return_exceptions=True,
            )
            try:
                if self._shutdown_timeout is None:
                    await submission_waiter
                else:
                    await asyncio.wait_for(
                        asyncio.shield(submission_waiter),
                        timeout=self._shutdown_timeout,
                    )
            except asyncio.TimeoutError:
                with self._lock:
                    self._shutdown_pending = True
                raise TimeoutError("local backend shutdown pending") from None

        with self._lock:
            pending = list(self._tasks.items())

        for task_id, task in pending:
            if task.done():
                continue
            try:
                await asyncio.to_thread(
                    self.store.request_cancel,
                    task_id,
                    reason="backend shutdown",
                )
            except KeyError:
                pass
            with self._lock:
                cancel_event = self._cancel_events.get(task_id)
                if cancel_event is not None:
                    cancel_event.set()

        active = [task for _, task in pending if not task.done()]
        if active:
            gathering = asyncio.gather(*active, return_exceptions=True)
            try:
                if self._shutdown_timeout is None:
                    await gathering
                else:
                    await asyncio.wait_for(
                        asyncio.shield(gathering),
                        timeout=self._shutdown_timeout,
                    )
            except asyncio.TimeoutError:
                with self._lock:
                    self._shutdown_pending = True
                raise TimeoutError("local backend shutdown pending") from None

        with self._lock:
            self._shutdown_pending = False
            self._closed = True
            self._closing = False
            self._tasks.clear()
            self._cancel_events.clear()
            self._progress_slots.clear()

    async def _run(
        self,
        submission: TaskSubmission,
        handler: TaskHandler,
        cancel_event: threading.Event,
    ) -> None:
        task_id = submission.task_id
        raw_decision = submission.payload.get("decision", submission.payload)
        base_provenance = self._merge_provenance(
            raw_decision if isinstance(raw_decision, dict) else {}
        )
        slot: _ProgressSlot | None = None
        projector: asyncio.Task[None] | None = None
        projection_failure_detected = False
        try:
            claimed = await asyncio.to_thread(
                self.store.claim_running,
                task_id,
                phase=TaskPhase.RUNNING,
                attempt=1,
            )
            if not claimed:
                await self._finish_requested_cancellation(task_id)
                return
            if cancel_event.is_set() or await self._is_cancel_requested(task_id):
                await self._ensure_canceled(task_id)
                return

            slot = _ProgressSlot(asyncio.get_running_loop())
            with self._lock:
                self._progress_slots[task_id] = slot
            projector = asyncio.create_task(
                self._project_progress(task_id, slot, base_provenance),
                name=f"medchat-progress-{task_id}",
            )
            result = await handler(
                submission,
                cancel_event,
                self._progress_callback(slot),
            )
            slot.stop()
            try:
                await projector
            except Exception:
                projector = None
                projection_failure_detected = True
                await self._finish_projection_failure(
                    task_id,
                    cancel_event,
                    base_provenance,
                )
                return
            else:
                projector = None
            await self._project_result(task_id, result, cancel_event, base_provenance)
        except asyncio.CancelledError:
            cancel_event.set()
            if slot is not None:
                slot.stop()
            if projector is not None:
                try:
                    await asyncio.shield(projector)
                except Exception:
                    projector = None
                    await self._finish_projection_failure(
                        task_id,
                        cancel_event,
                        base_provenance,
                    )
            await self._ensure_canceled(task_id)
            raise
        except Exception as exc:
            if projection_failure_detected:
                raise
            if slot is not None:
                slot.stop()
            if projector is not None:
                try:
                    await projector
                except Exception:
                    projector = None
                    await self._finish_projection_failure(
                        task_id,
                        cancel_event,
                        base_provenance,
                    )
                    return
            if cancel_event.is_set() or await self._is_cancel_requested(task_id):
                await self._ensure_canceled(task_id)
            else:
                await self._finish_failed(
                    task_id,
                    error=sanitize_task_message(str(exc) or "Task handler failed"),
                    error_code=TaskErrorCode.DOCKING_PROCESS_FAILED,
                    provenance=base_provenance,
                )
        finally:
            with self._lock:
                self._progress_slots.pop(task_id, None)

    async def _finish_projection_failure(
        self,
        task_id: str,
        cancel_event: threading.Event,
        provenance: dict[str, Any],
    ) -> None:
        for _ in range(3):
            try:
                record = await asyncio.to_thread(self.store.get, task_id)
            except KeyError:
                return
            if record.status in TERMINAL_STATUSES:
                return
            if cancel_event.is_set() and record.status is TaskStatus.RUNNING:
                record = await asyncio.to_thread(
                    self.store.request_cancel,
                    task_id,
                    reason="projection failed after cancellation",
                )
                if record.status in TERMINAL_STATUSES:
                    return
            if record.status is TaskStatus.CANCEL_REQUESTED:
                finished = await asyncio.to_thread(
                    self.store.finish,
                    task_id,
                    TaskStatus.CANCELED,
                    projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
                )
            elif record.status is TaskStatus.RUNNING:
                finished = await asyncio.to_thread(
                    self.store.finish,
                    task_id,
                    TaskStatus.FAILED,
                    error="Task state projection failed",
                    error_code=TaskErrorCode.TASK_PROJECTION_FAILED,
                    provenance=provenance,
                    projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
                )
            else:
                return
            if finished:
                return
        raise RuntimeError("projection terminal reconciliation incomplete")

    async def _project_progress(
        self,
        task_id: str,
        slot: _ProgressSlot,
        base_provenance: dict[str, Any],
    ) -> None:
        while True:
            await slot.event.wait()
            slot.event.clear()
            update, stopping = slot.take()
            if update is not None:
                provenance = (
                    self._merge_provenance(update.provenance, base_provenance)
                    if update.provenance is not None
                    else None
                )
                await asyncio.to_thread(
                    self.store.heartbeat,
                    task_id,
                    phase=update.phase,
                    progress=update.progress,
                    attempt=update.attempt,
                    warnings=update.warnings,
                    provenance=provenance,
                )
            if stopping and slot.size() == 0:
                return

    async def _project_result(
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
            if cancel_event.is_set() or await self._is_cancel_requested(task_id):
                await self._ensure_canceled(task_id)
                return
            await self._finish_failed(
                task_id,
                error="Scientific task result validation failed",
                error_code=TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
                provenance=base_provenance,
            )
            return

        raw_code = result.get("error_code")
        status_text = str(result.get("status", "")).strip().lower()
        safe_result = sanitize_task_result_projection(result)
        if not self._terminal_projection_matches_result(
            result,
            safe_result,
            status_text,
        ):
            await self._finish_failed(
                task_id,
                error="Scientific task result validation failed",
                error_code=TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
                provenance=base_provenance,
            )
            return
        if (
            cancel_event.is_set()
            or await self._is_cancel_requested(task_id)
            or raw_code == "cancelled"
            or status_text in {"cancelled", "canceled"}
        ):
            await self._ensure_canceled(task_id)
            return

        artifacts = sanitize_public_artifacts(result.get("artifacts", []))
        try:
            warnings = (
                normalize_task_warnings(result["warnings"])
                if "warnings" in result
                else None
            )
            provenance = (
                self._merge_provenance(result["provenance"], base_provenance)
                if "provenance" in result
                else None
            )
        except ValueError:
            await self._finish_failed(
                task_id,
                error="Scientific task result validation failed",
                error_code=TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED,
                provenance=base_provenance,
            )
            return

        if result["success"] is True and status_text != "failed":
            finished = await asyncio.to_thread(
                self.store.finish,
                task_id,
                TaskStatus.SUCCEEDED,
                result=safe_result,
                artifacts=artifacts,
                warnings=warnings,
                provenance=provenance,
                projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
            )
            if not finished:
                await self._finish_requested_cancellation(task_id)
            return

        await self._finish_failed(
            task_id,
            result=safe_result,
            error="Task failed",
            artifacts=artifacts,
            error_code=self._map_error_code(raw_code),
            warnings=warnings,
            provenance=provenance,
        )

    @staticmethod
    def _terminal_projection_matches_result(
        raw_result: dict[str, Any],
        projected_result: Any,
        status_text: str,
    ) -> bool:
        if not isinstance(projected_result, dict):
            return False
        raw_success = raw_result.get("success")
        if projected_result.get("success") is not raw_success:
            return False
        if "status" in raw_result:
            if not isinstance(raw_result["status"], str):
                return False
            if projected_result.get("status") != status_text:
                return False
        elif "status" in projected_result:
            return False
        if raw_success is True:
            return status_text in {"", "succeeded"}
        return status_text in {"", "failed", "cancelled", "canceled"}

    def _progress_callback(self, slot: _ProgressSlot) -> ProgressCallback:
        def progress_callback(
            phase: TaskPhase | str,
            progress: float | int,
            *,
            attempt: int | None = None,
            warnings: list[Any] | None = None,
            provenance: dict[str, Any] | None = None,
        ) -> None:
            canonical_phase = phase if isinstance(phase, TaskPhase) else TaskPhase(phase)
            if isinstance(progress, bool) or type(progress) not in {int, float}:
                raise ValueError("invalid progress")
            canonical_progress = float(progress)
            if 1 < canonical_progress <= 100:
                canonical_progress /= 100.0
            if not 0 <= canonical_progress <= 1:
                raise ValueError("invalid progress")
            slot.put(
                _ProgressUpdate(
                    canonical_phase,
                    canonical_progress,
                    attempt,
                    warnings,
                    provenance,
                )
            )

        return progress_callback

    async def _finish_failed(
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
        finished = await asyncio.to_thread(
            self.store.finish,
            task_id,
            TaskStatus.FAILED,
            result=result,
            error=error,
            artifacts=artifacts,
            error_code=error_code,
            warnings=warnings,
            provenance=provenance,
            projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
        )
        if not finished:
            await self._finish_requested_cancellation(task_id)

    async def _is_cancel_requested(self, task_id: str) -> bool:
        try:
            record = await asyncio.to_thread(self.store.get, task_id)
        except KeyError:
            return False
        return record.status is TaskStatus.CANCEL_REQUESTED

    async def _ensure_canceled(self, task_id: str) -> None:
        try:
            record = await asyncio.to_thread(self.store.get, task_id)
        except KeyError:
            return
        if record.status is TaskStatus.RUNNING:
            record = await asyncio.to_thread(
                self.store.request_cancel,
                task_id,
                reason="handler canceled",
            )
        if record.status is TaskStatus.CANCEL_REQUESTED:
            await asyncio.to_thread(
                self.store.finish,
                task_id,
                TaskStatus.CANCELED,
                projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
            )

    async def _finish_requested_cancellation(self, task_id: str) -> None:
        if await self._is_cancel_requested(task_id):
            await asyncio.to_thread(
                self.store.finish,
                task_id,
                TaskStatus.CANCELED,
                projection_policy=ResultProjectionPolicy.SCIENTIFIC_STRICT,
            )

    def _task_done(self, task_id: str, task: asyncio.Task[None]) -> None:
        failure: BaseException | None = None
        try:
            failure = task.exception()
        except asyncio.CancelledError:
            failure = None
        if failure is not None:
            recovery = task.get_loop().create_task(
                self._recover_unexpected_task_failure(task_id),
                name=f"medchat-recover-{task_id}",
            )
            with self._lock:
                if self._tasks.get(task_id) is task:
                    self._tasks[task_id] = recovery
            recovery.add_done_callback(
                lambda completed, recovered_task_id=task_id: self._recovery_done(
                    recovered_task_id,
                    completed,
                )
            )
            return
        with self._lock:
            if self._tasks.get(task_id) is task:
                self._tasks.pop(task_id, None)
                self._cancel_events.pop(task_id, None)
                self._progress_slots.pop(task_id, None)

    async def _recover_unexpected_task_failure(self, task_id: str) -> None:
        with self._lock:
            cancel_event = self._cancel_events.get(task_id)
        await self._finish_projection_failure(
            task_id,
            cancel_event or threading.Event(),
            dict(_LOCAL_PROVENANCE),
        )

    def _recovery_done(
        self,
        task_id: str,
        task: asyncio.Task[None],
    ) -> None:
        failed = False
        try:
            failed = task.exception() is not None
        except asyncio.CancelledError:
            failed = True
        with self._lock:
            if failed:
                self._shutdown_pending = True
            if self._tasks.get(task_id) is task:
                self._tasks.pop(task_id, None)
                self._cancel_events.pop(task_id, None)
                self._progress_slots.pop(task_id, None)

    async def _dispatch(
        self,
        factory: Callable[[], Awaitable[T]],
        *,
        allow_closed_owner: bool = True,
    ) -> T:
        loop = asyncio.get_running_loop()
        with self._lock:
            owner = self._owner_loop
            if owner is None:
                self._owner_loop = loop
                owner = loop
            elif owner.is_closed():
                if self._tasks:
                    self._shutdown_pending = True
                    raise RuntimeError("owner loop closed with workers still running")
                if allow_closed_owner:
                    self._owner_loop = loop
                    owner = loop
                else:
                    self._owner_loop = loop
                    owner = loop
        if owner is loop:
            return await factory()
        if not owner.is_running():
            with self._lock:
                if self._tasks:
                    self._shutdown_pending = True
            raise RuntimeError("owner loop is not running")
        concurrent_future = asyncio.run_coroutine_threadsafe(factory(), owner)
        return await asyncio.wrap_future(concurrent_future)

    @staticmethod
    def _accepted(task_id: str, message: str) -> BackendSubmitResult:
        return BackendSubmitResult(
            task_id=task_id,
            backend="local",
            outcome=StartOutcome.ACCEPTED,
            message=message,
        )

    @classmethod
    def _existing_task_result(
        cls,
        record: TaskRecord,
        stored_request_digest: str | None,
        submission: TaskSubmission,
        request_digest: str,
    ) -> BackendSubmitResult:
        if record.task_type != submission.task_type or record.backend != "local":
            raise ValueError("task id conflict")
        if stored_request_digest is None or stored_request_digest != request_digest:
            raise ValueError("task id request conflict")
        return cls._accepted(submission.task_id, "existing task projection")

    def _task_authority(self, task_id: str) -> tuple[TaskRecord, str | None]:
        return self.store.get(task_id), self.store.get_submission_digest(task_id)

    @staticmethod
    def idempotency_digest(key: str) -> str:
        if type(key) is not str or not key:
            raise ValueError("invalid idempotency key")
        return hashlib.sha256(
            b"MedChatTaskIdempotency@1\x00" + key.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def submission_digest(task_type: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"payload": payload, "task_type": task_type},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(
            b"medchat-local-task-submission-v1\x00" + canonical
        ).hexdigest()

    @classmethod
    def _idempotent_result(
        cls,
        record: TaskRecord,
        stored_request_digest: str | None,
        submission: TaskSubmission,
        request_digest: str,
    ) -> BackendSubmitResult:
        if record.task_type != submission.task_type or record.backend != "local":
            raise TaskIdempotencyConflictError("idempotency authority conflict")
        if stored_request_digest is None:
            raise TaskIdempotencyConflictError(
                "idempotency legacy authority conflict"
            )
        if stored_request_digest != request_digest:
            raise TaskIdempotencyConflictError("idempotency request conflict")
        return cls._accepted(record.task_id, "duplicate idempotency key")

    @staticmethod
    def _merge_provenance(
        value: Any,
        base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        incoming = sanitize_provenance(value or {})
        safe_base = sanitize_provenance(base or {})
        return {**incoming, **safe_base, **_LOCAL_PROVENANCE}

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
