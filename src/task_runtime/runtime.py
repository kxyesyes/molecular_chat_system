from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4

from .backends.base import (
    BackendSubmitResult,
    StartOutcome,
    TaskRuntimeBackend,
    assert_async_backend_contract,
)
from .backends.local import LocalTaskBackend, TaskIdempotencyConflictError
from .config import PROJECT_ROOT, TaskRuntimeConfig
from .docking_execution import DockingExecution
from .errors import TaskErrorCode
from .models import (
    BackendHealth,
    TaskPhase,
    TaskRecord,
    TaskStatus,
    TaskSubmission,
    strict_json_snapshot,
)
from .selector import BackendDecision, TemporalDockingSelector
from .staging import DockingInputStager
from .store import TERMINAL_STATUSES, TaskStore


class TaskBackendStartError(RuntimeError):
    """A backend start was not safely accepted and must not be retried blindly."""


_IDEMPOTENT_REUSE_MESSAGE = (
    "existing idempotent task reused; staged input retained"
)


class TaskRuntime:
    """Async backend façade for safely staged scientific tasks."""

    def __init__(
        self,
        *,
        config: TaskRuntimeConfig | Any | None = None,
        store: TaskStore | None = None,
        stager: DockingInputStager | Any | None = None,
        selector: TemporalDockingSelector | Any | None = None,
        local_backend: TaskRuntimeBackend | None = None,
        temporal_backend: TaskRuntimeBackend | None = None,
        uuid_factory: Callable[[], Any] | None = None,
        docking_execution: DockingExecution | Any | None = None,
    ) -> None:
        self.config = config or TaskRuntimeConfig.from_env()
        self.store = store or TaskStore()
        self.stager = stager or DockingInputStager(self.config.staging_root)
        self.selector = selector or TemporalDockingSelector(self.config.canary_percent)
        self._uuid_factory = uuid_factory or uuid4
        self.temporal_backend = temporal_backend
        if temporal_backend is not None:
            assert_async_backend_contract(temporal_backend)

        if local_backend is None:
            self.docking_execution = docking_execution or DockingExecution(
                self.config.staging_root,
                allowed_output_root=PROJECT_ROOT / "temp_docking",
            )

            async def run_docking(
                submission: TaskSubmission,
                cancel_event: threading.Event,
                progress_callback,
            ) -> dict[str, Any]:
                return await asyncio.to_thread(
                    self.docking_execution.run_verified,
                    submission.task_id,
                    submission.input_manifest_path,
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                )

            local_backend = LocalTaskBackend(self.store, {"docking": run_docking})
        else:
            self.docking_execution = docking_execution
            assert_async_backend_contract(local_backend)
        self.local_backend = local_backend
        self._closed = False

    async def submit_docking(
        self,
        *,
        receptor_name: str,
        receptor_bytes: bytes,
        ligand_name: str | None,
        ligand_bytes: bytes | None,
        smiles: str | None,
        config: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> BackendSubmitResult:
        if self._closed:
            raise RuntimeError("task runtime is closed")
        task_id = self._new_task_id()
        config_snapshot = strict_json_snapshot(config)
        if not isinstance(config_snapshot, dict):
            raise ValueError("invalid docking config")
        manifest_path = await asyncio.to_thread(
            self.stager.stage,
            task_id,
            receptor_name,
            receptor_bytes,
            ligand_name,
            ligand_bytes,
            smiles,
            config_snapshot,
        )
        verified_manifest = await asyncio.to_thread(
            self.stager.load_verified,
            task_id,
            manifest_path,
        )
        request_digest = self._docking_request_digest(verified_manifest)

        if idempotency_key is not None:
            existing = await self._global_idempotency_result(
                idempotency_key,
                request_digest,
                task_type="docking",
            )
            if existing is not None:
                return existing

        temporal_health = await self._temporal_health()
        decision = self.selector.select(
            "docking",
            task_id,
            temporal_health.available,
        )
        decision_payload = self._decision_payload(decision)
        payload = {
            "decision": decision_payload,
            "mode": "file" if ligand_bytes is not None else "smiles",
            "total_bytes": len(receptor_bytes) + (len(ligand_bytes) if ligand_bytes else 0),
            "config_hash": verified_manifest["config_hash"],
            "request_digest": request_digest,
        }
        submission = TaskSubmission(
            task_id=task_id,
            task_type="docking",
            payload=payload,
            input_manifest_path=str(manifest_path),
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

        if decision.backend == "local":
            try:
                local_result = await self.local_backend.submit(submission)
            except TaskIdempotencyConflictError:
                raise
            except Exception as exc:
                await self._project_start_failure(
                    submission,
                    backend="local",
                    error_code=TaskErrorCode.TASK_BACKEND_UNAVAILABLE,
                )
                raise TaskBackendStartError("Local start failed") from exc
            return await self._validated_local_result(submission, local_result)

        if decision.backend != "temporal" or self.temporal_backend is None:
            await self._mark_temporal_ambiguous(submission)
            raise TaskBackendStartError("Temporal start was unavailable")

        try:
            temporal_result = await self.temporal_backend.submit(submission)
        except Exception as exc:
            await self._mark_temporal_ambiguous(submission)
            raise TaskBackendStartError(
                "Temporal start was not confirmed; local fallback suppressed"
            ) from exc

        if (
            not isinstance(temporal_result, BackendSubmitResult)
            or temporal_result.task_id != task_id
            or temporal_result.backend != "temporal"
        ):
            await self._mark_temporal_ambiguous(submission)
            raise TaskBackendStartError(
                "Temporal start returned an ambiguous result; local fallback suppressed"
            )
        if temporal_result.outcome is StartOutcome.ACCEPTED:
            return temporal_result
        if temporal_result.outcome is StartOutcome.REJECTED:
            if await self._projection_exists(task_id):
                await self._mark_temporal_ambiguous(submission)
                raise TaskBackendStartError(
                    "Temporal start rejection conflicted with an existing projection"
                )
            try:
                local_result = await self.local_backend.submit(submission)
            except TaskIdempotencyConflictError:
                raise
            except Exception as exc:
                await self._project_start_failure(
                    submission,
                    backend="local",
                    error_code=TaskErrorCode.TASK_BACKEND_UNAVAILABLE,
                )
                raise TaskBackendStartError("Local fallback start failed") from exc
            try:
                return await self._validated_local_result(
                    submission,
                    local_result,
                )
            except TaskBackendStartError as exc:
                if await self._projection_exists(task_id):
                    await self._mark_temporal_ambiguous(submission)
                    raise TaskBackendStartError(
                        "Temporal fallback projection has conflicting authority"
                    ) from exc
                raise

        await self._mark_temporal_ambiguous(submission)
        raise TaskBackendStartError(
            "Temporal start was ambiguous; local fallback suppressed"
        )

    async def get(self, task_id: str) -> TaskRecord:
        return await asyncio.to_thread(self.store.get, task_id)

    @staticmethod
    def _docking_request_digest(manifest: Mapping[str, Any]) -> str:
        try:
            authority = {
                "config_hash": manifest["config_hash"],
                "ligand_mode": manifest["ligand_mode"],
                "ligand_sha256": manifest["ligand"]["sha256"],
                "receptor_sha256": manifest["receptor"]["sha256"],
            }
        except (KeyError, TypeError) as exc:
            raise ValueError("verified manifest missing digest authority") from exc
        for key in ("config_hash", "ligand_sha256", "receptor_sha256"):
            value = authority[key]
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("verified manifest has invalid digest authority")
        if authority["ligand_mode"] not in {"file", "smiles"}:
            raise ValueError("verified manifest has invalid ligand mode")
        canonical = json.dumps(
            authority,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(
            b"medchat-task-submission-v1\x00" + canonical
        ).hexdigest()

    async def list(
        self,
        limit: int = 20,
        status: str | TaskStatus | None = None,
        task_type: str | None = None,
    ) -> list[TaskRecord]:
        return await asyncio.to_thread(
            self.store.list,
            limit=limit,
            status=status,
            task_type=task_type,
        )

    async def cancel(self, task_id: str, reason: str | None = None) -> TaskRecord:
        record = await asyncio.to_thread(self.store.get, task_id)
        if record.backend == "temporal" and self.temporal_backend is not None:
            return await self.temporal_backend.cancel(task_id, reason)
        return await self.local_backend.cancel(task_id, reason)

    async def health(self) -> dict[str, BackendHealth]:
        return {
            "local": await self.local_backend.health(),
            "temporal": await self._temporal_health(),
        }

    async def close(self) -> None:
        if self._closed:
            return
        seen: set[int] = set()
        for backend in (self.local_backend, self.temporal_backend):
            if backend is None or id(backend) in seen:
                continue
            seen.add(id(backend))
            closer = getattr(backend, "close", None)
            if closer is None:
                closer = getattr(backend, "shutdown", None)
            if closer is None:
                continue
            result = closer()
            if inspect.isawaitable(result):
                await result
        self._closed = True

    async def _temporal_health(self) -> BackendHealth:
        if self.temporal_backend is None:
            return BackendHealth(
                "temporal",
                False,
                "not configured",
                {"running": 0},
            )
        try:
            health = await self.temporal_backend.health()
        except Exception:
            return BackendHealth(
                "temporal",
                False,
                "health unavailable",
                {"running": 0},
            )
        if not isinstance(health, BackendHealth) or health.backend != "temporal":
            return BackendHealth(
                "temporal",
                False,
                "health invalid",
                {"running": 0},
            )
        return health

    async def _global_idempotency_result(
        self,
        idempotency_key: str,
        request_digest: str,
        *,
        task_type: str,
    ) -> BackendSubmitResult | None:
        digest = LocalTaskBackend.idempotency_digest(idempotency_key)
        try:
            record, stored_request_digest = await asyncio.to_thread(
                self.store.get_idempotency_authority,
                digest,
            )
        except KeyError:
            return None
        except Exception as exc:
            raise TaskIdempotencyConflictError(
                "idempotency authority conflict"
            ) from exc
        return self._authority_result(
            record,
            stored_request_digest,
            request_digest=request_digest,
            task_type=task_type,
            allowed_backends={"local", "temporal"},
        )

    @staticmethod
    def _authority_result(
        record: TaskRecord,
        stored_request_digest: str | None,
        *,
        request_digest: str,
        task_type: str,
        allowed_backends: set[str],
    ) -> BackendSubmitResult:
        if stored_request_digest is None:
            raise TaskIdempotencyConflictError(
                "idempotency legacy authority conflict"
            )
        if stored_request_digest != request_digest:
            raise TaskIdempotencyConflictError("idempotency request conflict")
        if (
            not isinstance(record, TaskRecord)
            or record.task_type != task_type
            or record.backend not in allowed_backends
            or not isinstance(record.status, TaskStatus)
        ):
            raise TaskIdempotencyConflictError(
                "idempotency authority conflict"
            )
        try:
            return BackendSubmitResult(
                task_id=record.task_id,
                backend=record.backend,
                outcome=StartOutcome.ACCEPTED,
                message=_IDEMPOTENT_REUSE_MESSAGE,
            )
        except ValueError as exc:
            raise TaskIdempotencyConflictError(
                "idempotency authority conflict"
            ) from exc

    async def _validated_local_result(
        self,
        submission: TaskSubmission,
        result: BackendSubmitResult,
    ) -> BackendSubmitResult:
        if (
            not isinstance(result, BackendSubmitResult)
            or result.backend != "local"
            or result.outcome is not StartOutcome.ACCEPTED
        ):
            raise TaskBackendStartError(
                "Local start result has no matching authority"
            )

        if submission.idempotency_key is not None:
            digest = LocalTaskBackend.idempotency_digest(
                submission.idempotency_key
            )
            try:
                record, stored_request_digest = await asyncio.to_thread(
                    self.store.get_idempotency_authority,
                    digest,
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise TaskBackendStartError(
                    "Local start result has no matching authority"
                ) from exc
            try:
                authority = self._authority_result(
                    record,
                    stored_request_digest,
                    request_digest=submission.request_digest or "",
                    task_type=submission.task_type,
                    allowed_backends={"local"},
                )
            except TaskIdempotencyConflictError as exc:
                raise TaskBackendStartError(
                    "Local start result has conflicting authority"
                ) from exc
            if (
                authority.task_id != result.task_id
                or authority.backend != result.backend
            ):
                raise TaskBackendStartError(
                    "Local start result has conflicting authority"
                )
            return result

        if result.task_id != submission.task_id:
            raise TaskBackendStartError(
                "Local start result has no matching authority"
            )
        try:
            record, stored_request_digest = await asyncio.to_thread(
                self._task_authority,
                result.task_id,
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise TaskBackendStartError(
                "Local start result has no matching authority"
            ) from exc
        if (
            record.task_type != submission.task_type
            or record.backend != "local"
            or stored_request_digest != submission.request_digest
        ):
            raise TaskBackendStartError(
                "Local start result has conflicting authority"
            )
        return result

    def _task_authority(
        self,
        task_id: str,
    ) -> tuple[TaskRecord, str | None]:
        return self.store.get(task_id), self.store.get_submission_digest(task_id)

    async def _project_start_failure(
        self,
        submission: TaskSubmission,
        *,
        backend: str,
        error_code: TaskErrorCode,
    ) -> TaskRecord:
        try:
            record = await asyncio.to_thread(self.store.get, submission.task_id)
        except KeyError:
            try:
                record = await asyncio.to_thread(
                    self.store.create,
                    submission.task_id,
                    submission.task_type,
                    submission.payload,
                    backend=backend,
                    provenance=submission.payload["decision"],
                    idempotency_digest=self._submission_idempotency_digest(submission),
                    submission_digest=submission.request_digest,
                )
            except sqlite3.IntegrityError:
                record = await asyncio.to_thread(self.store.get, submission.task_id)

        if record.status is TaskStatus.QUEUED:
            await asyncio.to_thread(
                self.store.claim_running,
                submission.task_id,
                phase=TaskPhase.STAGING,
                attempt=0,
            )
            record = await asyncio.to_thread(self.store.get, submission.task_id)
        if record.status is TaskStatus.RUNNING:
            await asyncio.to_thread(
                self.store.finish,
                submission.task_id,
                TaskStatus.FAILED,
                error=(
                    "Temporal start failed"
                    if backend == "temporal"
                    else "Task execution backend unavailable"
                ),
                error_code=error_code,
                provenance=submission.payload["decision"],
            )
        elif record.status is TaskStatus.CANCEL_REQUESTED:
            await asyncio.to_thread(
                self.store.finish,
                submission.task_id,
                TaskStatus.CANCELED,
            )
        return await asyncio.to_thread(self.store.get, submission.task_id)

    async def _mark_temporal_ambiguous(
        self, submission: TaskSubmission
    ) -> TaskRecord:
        decision = submission.payload.get("decision", {})
        provenance = {
            **(decision if isinstance(decision, dict) else {}),
            "start_outcome": "ambiguous",
        }
        warning = [{"code": "temporal_start_ambiguous"}]
        try:
            record = await asyncio.to_thread(self.store.get, submission.task_id)
        except KeyError:
            try:
                return await asyncio.to_thread(
                    self.store.create,
                    submission.task_id,
                    submission.task_type,
                    submission.payload,
                    backend="temporal",
                    phase=TaskPhase.STAGING,
                    warnings=warning,
                    provenance=provenance,
                    idempotency_digest=self._submission_idempotency_digest(submission),
                    submission_digest=submission.request_digest,
                )
            except sqlite3.IntegrityError:
                record = await asyncio.to_thread(self.store.get, submission.task_id)
        await asyncio.to_thread(
            self.store.annotate_nonterminal,
            submission.task_id,
            phase=TaskPhase.STAGING,
            warnings=warning,
            provenance=provenance,
        )
        return await asyncio.to_thread(self.store.get, submission.task_id)

    async def _projection_exists(self, task_id: str) -> bool:
        try:
            await asyncio.to_thread(self.store.get, task_id)
        except KeyError:
            return False
        return True

    @staticmethod
    def _submission_idempotency_digest(
        submission: TaskSubmission,
    ) -> str | None:
        if submission.idempotency_key is None:
            return None
        return LocalTaskBackend.idempotency_digest(submission.idempotency_key)

    def _new_task_id(self) -> str:
        value = self._uuid_factory()
        task_id = str(value)
        if not task_id or len(task_id) > 128:
            raise ValueError("invalid generated task_id")
        return task_id

    @staticmethod
    def _decision_payload(decision: Any) -> dict[str, Any]:
        if not isinstance(decision, BackendDecision):
            raise ValueError("invalid backend decision")
        payload: dict[str, Any] = {
            "backend": decision.backend,
            "reason": decision.reason,
            "bucket": decision.bucket,
            "percent": decision.percent,
        }
        return payload


_RUNTIME: TaskRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def get_task_runtime() -> TaskRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = TaskRuntime()
        return _RUNTIME


async def reset_task_runtime_for_tests() -> None:
    global _RUNTIME
    with _RUNTIME_LOCK:
        runtime = _RUNTIME
        _RUNTIME = None
    if runtime is not None:
        await runtime.close()
