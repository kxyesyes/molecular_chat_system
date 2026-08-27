"""Temporal Activities for verified docking and SQLite state projection."""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.errors import TaskErrorCode
from src.agent.persistence.redaction import contains_sensitive_text
from src.task_runtime.models import (
    TaskPhase,
    TaskStatus,
    canonical_artifact_type,
    contains_prefixed_scientific_identifier,
    contains_scientific_structure,
    sanitize_provenance,
    sanitize_public_artifacts,
)
from src.task_runtime.prometheus_metrics import TemporalWorkerMetrics
from src.task_runtime.staging import DockingInputStager, ManifestError
from src.task_runtime.store import TaskStore
from src.task_runtime.temporal.process_runner import ManagedDockingProcessRunner


DOCKING_ACTIVITY_NAME = "run_docking_activity"
PROJECTION_ACTIVITY_NAME = "project_task_activity"
VERIFY_MANIFEST_ACTIVITY_NAME = "verify_manifest_activity"

_HEARTBEAT_PHASES = (
    TaskPhase.ENVIRONMENT_CHECK.value,
    TaskPhase.INPUT_VERIFICATION.value,
    TaskPhase.RECEPTOR_PREPARATION.value,
    TaskPhase.LIGAND_PREPARATION.value,
    TaskPhase.VINA_RUNNING.value,
    TaskPhase.RESULT_PARSING.value,
    TaskPhase.SCIENTIFIC_VALIDATION.value,
    TaskPhase.ARTIFACT_COMMIT.value,
)
_CONTROLLED_PHASES = frozenset(_HEARTBEAT_PHASES)
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
_REASON_CODE_MAP = {
    "ownership_uncertain": TaskErrorCode.DOCKING_PROCESS_OWNERSHIP_UNCERTAIN.value,
    "validator_rejected": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
    "evidence_rejected": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
    "provenance_rejected": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
    "artifact_invalid": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
    "completion_invalid": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
    "pose_invalid": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
    "input_hash_mismatch": TaskErrorCode.TASK_INPUT_HASH_MISMATCH.value,
    "environment_unavailable": TaskErrorCode.DOCKING_ENVIRONMENT_UNAVAILABLE.value,
}
_SAFE_PROVENANCE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:-]{0,127}\Z")
_UUID4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_MANIFEST_LOCATOR = "input_manifest.json"


def _best_effort_metric(
    metrics: TemporalWorkerMetrics | None,
    method: str,
    *args: Any,
) -> None:
    if metrics is None:
        return
    try:
        getattr(metrics, method)(*args)
    except Exception:
        pass


def _has_strict_terminal_provenance(payload: dict[str, Any]) -> bool:
    provenance = payload.get("provenance")
    return (
        isinstance(provenance, dict)
        and provenance.get("tool") == "molecular_docking"
        and provenance.get("tool_name") == "molecular_docking"
        and provenance.get("demo_mode") is False
        and provenance.get("fallback_used") is False
    )


@dataclass
class _ProgressSlot:
    """A bounded latest-value slot shared with one synchronous worker thread."""

    _phase: str = TaskPhase.ENVIRONMENT_CHECK.value
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
    if not isinstance(task_id, str) or _UUID4.fullmatch(task_id) is None:
        raise ValueError("invalid task_id")
    if manifest_locator != _MANIFEST_LOCATOR:
        raise ValueError("invalid manifest locator")
    return task_id, manifest_locator


def _structured_error_code(result: dict[str, Any]) -> str:
    error = result.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    reason = details.get("reason") if isinstance(details, dict) else None
    if isinstance(reason, str) and reason in _REASON_CODE_MAP:
        return _REASON_CODE_MAP[reason]
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str):
        return _ERROR_CODE_MAP.get(code, TaskErrorCode.DOCKING_PROCESS_FAILED.value)
    return TaskErrorCode.DOCKING_PROCESS_FAILED.value


def _safe_artifacts(value: Any) -> list[dict[str, Any]]:
    return sanitize_public_artifacts(value)


def _is_safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        return False
    parts = normalized.split("/")
    return all(
        part not in {"", ".", ".."}
        and not contains_sensitive_text(part)
        and not contains_scientific_structure(part.rsplit(".", 1)[0])
        and not contains_prefixed_scientific_identifier(part)
        for part in parts
    )


def _safe_provenance(value: Any, attempt: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source = value
    if (
        source.get("tool_name") != "molecular_docking"
        or source.get("demo_mode") is not False
        or source.get("fallback_used") is not False
    ):
        return None
    result: dict[str, Any] = {
        "tool": "molecular_docking",
        "attempt": attempt,
    }
    for key in ("tool_name", "tool_version", "model_name", "model_version"):
        candidate = source.get(key)
        if candidate is None:
            continue
        if not isinstance(candidate, str):
            return None
        compact_candidate = re.sub(r"\s+", "", candidate)
        if (
            _SAFE_PROVENANCE_IDENTIFIER.fullmatch(candidate) is None
            or contains_sensitive_text(candidate)
            or contains_scientific_structure(compact_candidate)
            or contains_prefixed_scientific_identifier(compact_candidate)
        ):
            return None
        result[key] = candidate
    for key in ("demo_mode", "fallback_used"):
        candidate = source.get(key)
        if isinstance(candidate, bool):
            result[key] = candidate
    try:
        sanitized = sanitize_provenance(result)
    except ValueError:
        return None
    if any(
        key in result and key not in sanitized
        for key in ("tool", "tool_name", "tool_version", "model_name", "model_version")
    ):
        return None
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
    artifacts = _safe_artifacts(result.get("artifacts"))
    raw_artifacts = result.get("artifacts")
    artifacts_valid = isinstance(raw_artifacts, list) and len(artifacts) == len(
        raw_artifacts
    )
    if artifacts_valid:
        for source, projected in zip(raw_artifacts, artifacts):
            artifact_type = canonical_artifact_type(source)
            if (
                not isinstance(source, dict)
                or artifact_type is None
                or projected.get("path") != source.get("path")
                or canonical_artifact_type(projected) != artifact_type
            ):
                artifacts_valid = False
                break
    if artifacts_valid:
        pose_artifacts = [
            artifact
            for artifact in raw_artifacts
            if canonical_artifact_type(artifact) == "docking_pose"
        ]
        artifacts_valid = (
            len(pose_artifacts) == 1
            and pose_artifacts[0].get("path") == pose_file
        )
    if not artifacts_valid:
        return {
            "task_id": task_id,
            "attempt": attempt,
            "status": TaskStatus.FAILED.value,
            "error_code": TaskErrorCode.DOCKING_ARTIFACT_INVALID.value,
        }
    provenance = _safe_provenance(result.get("provenance"), attempt)
    if provenance is None:
        return {
            "task_id": task_id,
            "attempt": attempt,
            "status": TaskStatus.FAILED.value,
            "error_code": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
        }
    execution_contract = _safe_execution_contract(result.get("quality"))
    if execution_contract is None:
        return {
            "task_id": task_id,
            "attempt": attempt,
            "status": TaskStatus.FAILED.value,
            "error_code": TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
        }
    return {
        "task_id": task_id,
        "attempt": attempt,
        "status": TaskStatus.SUCCEEDED.value,
        "result": {
            "pose_count": pose_count,
            "best_energy": float(best_energy),
            "pose_file": pose_file,
            **execution_contract,
        },
        "artifacts": artifacts,
        "warnings": ([{"code": "TOOL_WARNING"}] if result.get("warnings") else []),
        "provenance": provenance,
    }


def _safe_execution_contract(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or value.get("execution_backend") is None:
        return {}
    digest = value.get("sandbox_image_digest")
    if (
        value.get("execution_backend") != "opensandbox"
        or value.get("secure_runtime") != "gvisor"
        or type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or value.get("cleanup_status") != "succeeded"
    ):
        return None
    return {
        "execution_backend": "opensandbox",
        "secure_runtime": "gvisor",
        "sandbox_image_digest": digest,
        "cleanup_status": "succeeded",
    }


async def _run_docking_in_test_thread(
    docking_execution: DockingExecution,
    payload: dict[str, Any],
    *,
    attempt: int,
    heartbeat: Callable[[dict[str, Any]], None],
    heartbeat_interval: float = 1.0,
    heartbeat_projection: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run an injected test executor and cooperatively join it on cancellation."""

    task_id, manifest_locator = _validate_docking_payload(payload)
    if type(attempt) is not int or attempt <= 0:
        raise ValueError("invalid activity attempt")
    if not isinstance(heartbeat_interval, (int, float)) or heartbeat_interval <= 0:
        raise ValueError("invalid heartbeat interval")
    cancel_event = threading.Event()
    progress = _ProgressSlot()
    started_at = time.monotonic()

    work = asyncio.create_task(
        asyncio.to_thread(
            docking_execution.run_verified_locator,
            task_id,
            manifest_locator,
            attempt=attempt,
            progress_callback=progress.update,
            cancel_event=cancel_event,
        )
    )

    async def flush_heartbeat() -> None:
        heartbeat_payload = {
            **progress.snapshot(),
            "attempt": attempt,
            "elapsed_time": max(0.0, time.monotonic() - started_at),
        }
        heartbeat(heartbeat_payload)
        if heartbeat_projection is not None:
            try:
                projected = heartbeat_projection(dict(heartbeat_payload))
                if inspect.isawaitable(projected):
                    await projected
            except Exception:
                pass

    try:
        while not work.done():
            await flush_heartbeat()
            await asyncio.sleep(float(heartbeat_interval))
        raw_result = await work
        outcome = _summarize_docking_result(
            raw_result,
            task_id=task_id,
            attempt=attempt,
        )
        if outcome.get("status") == TaskStatus.SUCCEEDED.value:
            progress.update(TaskPhase.ARTIFACT_COMMIT.value, 1.0)
        await flush_heartbeat()
        return outcome
    except asyncio.CancelledError:
        cancel_event.set()
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if work.done() and not work.cancelled():
            try:
                work.result()
            except Exception:
                pass
        raise


async def _run_docking_in_process(
    process_runner: ManagedDockingProcessRunner,
    payload: dict[str, Any],
    *,
    attempt: int,
    heartbeat: Callable[[dict[str, Any]], None],
    heartbeat_interval: float = 1.0,
    heartbeat_projection: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run docking behind an owned process while emitting bounded heartbeats."""

    task_id, _ = _validate_docking_payload(payload)
    if type(attempt) is not int or attempt <= 0:
        raise ValueError("invalid activity attempt")
    if not isinstance(heartbeat_interval, (int, float)) or heartbeat_interval <= 0:
        raise ValueError("invalid heartbeat interval")
    progress = _ProgressSlot()
    started_at = time.monotonic()
    work = asyncio.create_task(
        process_runner.run(
            payload,
            attempt=attempt,
            progress_callback=progress.update,
        )
    )

    async def flush_heartbeat() -> None:
        heartbeat_payload = {
            **progress.snapshot(),
            "attempt": attempt,
            "elapsed_time": max(0.0, time.monotonic() - started_at),
        }
        heartbeat(heartbeat_payload)
        if heartbeat_projection is not None:
            try:
                projected = heartbeat_projection(dict(heartbeat_payload))
                if inspect.isawaitable(projected):
                    await projected
            except Exception:
                pass

    try:
        while not work.done():
            await flush_heartbeat()
            await asyncio.sleep(float(heartbeat_interval))
        raw_result = await work
        outcome = _summarize_docking_result(
            raw_result,
            task_id=task_id,
            attempt=attempt,
        )
        if outcome.get("status") == TaskStatus.SUCCEEDED.value:
            progress.update(TaskPhase.ARTIFACT_COMMIT.value, 1.0)
        await flush_heartbeat()
        return outcome
    except asyncio.CancelledError:
        work.cancel()
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not work.cancelled():
            error = work.exception()
            if isinstance(error, ApplicationError):
                raise error
        raise


async def _to_thread_completion_safe(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Wait for one sync state mutation even if its Activity is canceled."""

    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
        return work.result()


class TemporalDockingActivities:
    """Bound Activity set with explicit worker-owned dependencies."""

    def __init__(
        self,
        store: TaskStore,
        docking_execution: DockingExecution,
        *,
        stager: DockingInputStager | None = None,
        process_runner: ManagedDockingProcessRunner | None = None,
        metrics: TemporalWorkerMetrics | None = None,
    ) -> None:
        if (
            process_runner is None
            and getattr(docking_execution, "raw_executor", None)
            is DockingExecution._execute_production_tool
        ):
            raise ValueError(
                "production Temporal docking requires a managed process runner"
            )
        self.store = store
        self.docking_execution = docking_execution
        self.stager = stager or getattr(docking_execution, "_stager", None)
        self.process_runner = process_runner
        self.metrics = metrics

    @activity.defn(name=VERIFY_MANIFEST_ACTIVITY_NAME)
    async def verify_manifest_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._verify_manifest_activity(payload)
        except asyncio.CancelledError:
            raise
        except ApplicationError:
            raise
        except Exception:
            raise ApplicationError(
                "Docking input manifest verification is unavailable",
                type="MANIFEST_TRANSIENT",
                non_retryable=False,
            ) from None

    async def _verify_manifest_activity(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, manifest_locator = _validate_docking_payload(payload)
        if self.stager is None:
            raise ApplicationError(
                "manifest stager is unavailable",
                type=TaskErrorCode.TASK_BACKEND_UNAVAILABLE.value,
                non_retryable=True,
            )
        try:
            await asyncio.to_thread(
                self.stager.load_verified_locator,
                task_id,
                manifest_locator,
            )
        except ManifestError as exc:
            if exc.reason_code == "manifest_integrity_failed":
                error_type = TaskErrorCode.TASK_INPUT_HASH_MISMATCH.value
                non_retryable = True
            elif exc.reason_code in {"manifest_busy", "manifest_io_error"}:
                error_type = "MANIFEST_TRANSIENT"
                non_retryable = False
            else:
                error_type = TaskErrorCode.TASK_INPUT_INVALID.value
                non_retryable = True
            raise ApplicationError(
                "Docking input manifest verification failed",
                type=error_type,
                non_retryable=non_retryable,
            ) from None
        return {"verified": True}

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def run_docking_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._run_docking_activity(
                payload,
                attempt=activity.info().attempt,
                heartbeat=activity.heartbeat,
                worker_shutdown_waiter=activity.wait_for_worker_shutdown,
            )
        except asyncio.CancelledError:
            raise
        except ApplicationError:
            raise
        except Exception:
            raise ApplicationError(
                "Docking process failed",
                type=TaskErrorCode.DOCKING_PROCESS_FAILED.value,
                non_retryable=True,
            ) from None

    async def _run_docking_activity(
        self,
        payload: dict[str, Any],
        *,
        attempt: int,
        heartbeat: Callable[[dict[str, Any]], None],
        worker_shutdown_waiter: Callable[[], Any],
        heartbeat_interval: float = 1.0,
    ) -> dict[str, Any]:
        task_id, _ = _validate_docking_payload(payload)
        if type(attempt) is not int or attempt <= 0:
            raise ValueError("invalid activity attempt")
        started_at = time.monotonic()
        run_task: asyncio.Task | None = None
        shutdown_task: asyncio.Task | None = None
        try:
            _best_effort_metric(self.metrics, "activity_started", "docking")
            _best_effort_metric(
                self.metrics,
                "docking_process_attempt",
                "docking",
                attempt,
            )
            docking_call = (
                _run_docking_in_process(
                    self.process_runner,
                    payload,
                    attempt=attempt,
                    heartbeat=heartbeat,
                    heartbeat_interval=heartbeat_interval,
                    heartbeat_projection=lambda update: self._project_live_heartbeat(
                        task_id,
                        update,
                    ),
                )
                if self.process_runner is not None
                else _run_docking_in_test_thread(
                    self.docking_execution,
                    payload,
                    attempt=attempt,
                    heartbeat=heartbeat,
                    heartbeat_interval=heartbeat_interval,
                    heartbeat_projection=lambda update: self._project_live_heartbeat(
                        task_id,
                        update,
                    ),
                )
            )
            run_task = asyncio.create_task(docking_call)
            shutdown_task = asyncio.create_task(worker_shutdown_waiter())
            done, _ = await asyncio.wait(
                {run_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_task in done and not run_task.done():
                run_task.cancel()
            return await asyncio.shield(run_task)
        except asyncio.CancelledError:
            if run_task is not None and not run_task.done():
                run_task.cancel()
            while run_task is not None and not run_task.done():
                try:
                    await asyncio.shield(run_task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if run_task is not None and not run_task.cancelled():
                error = run_task.exception()
                if isinstance(error, ApplicationError):
                    raise error
            raise
        finally:
            if shutdown_task is not None:
                shutdown_task.cancel()
                await asyncio.gather(shutdown_task, return_exceptions=True)
            _best_effort_metric(
                self.metrics,
                "activity_finished",
                "docking",
                max(0.0, time.monotonic() - started_at),
            )

    async def _project_live_heartbeat(
        self,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self.store.heartbeat,
            task_id,
            phase=payload["phase"],
            progress=payload["progress"],
            attempt=payload["attempt"],
        )

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def project_task_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._project_task_activity(payload)
        except asyncio.CancelledError:
            raise
        except ApplicationError:
            raise
        except Exception:
            raise ApplicationError(
                "Task projection failed",
                type=TaskErrorCode.TASK_PROJECTION_FAILED.value,
                non_retryable=False,
            ) from None

    async def _project_task_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        if operation == "inspect_terminal":
            record = await _to_thread_completion_safe(self.store.get, task_id)
            if record.status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELED,
                TaskStatus.TIMED_OUT,
            }:
                response: dict[str, Any] = {
                    "terminal": True,
                    "status": record.status.value,
                }
                if record.error_code is not None:
                    response["error_code"] = record.error_code
                return response
            return {"terminal": False, "status": record.status.value}
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
        if status is TaskStatus.SUCCEEDED and not _has_strict_terminal_provenance(
            payload
        ):
            _best_effort_metric(
                self.metrics,
                "provenance_validation_failure",
            )
            raise ApplicationError(
                "Scientific provenance validation failed",
                type=TaskErrorCode.SCIENTIFIC_VALIDATION_FAILED.value,
                non_retryable=True,
            ) from None
        error_code = payload.get("error_code")
        applied = await _to_thread_completion_safe(
            self.store.project_temporal_terminal,
            payload["task_id"],
            status,
            result=payload.get("result"),
            artifacts=payload.get("artifacts"),
            error_code=error_code,
            warnings=payload.get("warnings"),
            provenance=payload.get("provenance"),
        )
        if applied:
            if (
                status is TaskStatus.FAILED
                and error_code == TaskErrorCode.DOCKING_ARTIFACT_INVALID.value
            ):
                _best_effort_metric(
                    self.metrics,
                    "artifact_validation_failure",
                )
            _best_effort_metric(
                self.metrics,
                "terminal_projected",
                "docking",
                status.value,
            )
            return {"applied": True, "status": status.value}
        record = await _to_thread_completion_safe(
            self.store.get,
            payload["task_id"],
        )
        if record.status is status:
            return {"applied": False, "idempotent": True, "status": status.value}
        _best_effort_metric(
            self.metrics,
            "terminal_invariant_violation",
        )
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
    "VERIFY_MANIFEST_ACTIVITY_NAME",
    "TemporalDockingActivities",
]
