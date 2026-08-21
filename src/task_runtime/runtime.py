from __future__ import annotations

import asyncio
from concurrent.futures import Future
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import threading
import time
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
_IDEMPOTENT_REUSE_DISCARDED_MESSAGE = (
    "existing idempotent task reused; staged input discarded"
)
_STAGING_CLEANUP_INTERVAL_SECONDS = 300.0
_STAGING_ORPHAN_TTL_SECONDS = 24 * 60 * 60.0
_STAGING_CLEANUP_LIMIT = 16


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
        if (
            temporal_backend is None
            and self.config.backend == "temporal_canary"
        ):
            from .backends.temporal import TemporalTaskBackend

            temporal_backend = TemporalTaskBackend(
                self.store,
                address=self.config.temporal_address,
                namespace=self.config.temporal_namespace,
                task_queue=self.config.docking_queue,
            )
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
        self._close_lock = threading.Lock()
        self._close_future: Future[None] | None = None
        self._close_runner: asyncio.Task[None] | None = None
        self._staging_cleanup_lock = threading.Lock()
        self._last_staging_cleanup = 0.0

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
        await self._maybe_cleanup_staging()
        task_id = self._new_task_id()
        config_snapshot = strict_json_snapshot(config)
        if not isinstance(config_snapshot, dict):
            raise ValueError("invalid docking config")
        manifest_path, verified_manifest, request_digest = await self._stage_verified(
            task_id=task_id,
            receptor_name=receptor_name,
            receptor_bytes=receptor_bytes,
            ligand_name=ligand_name,
            ligand_bytes=ligand_bytes,
            smiles=smiles,
            config=config_snapshot,
        )
        try:
            if idempotency_key is not None:
                existing = await self._global_idempotency_result(
                    idempotency_key,
                    request_digest,
                    task_type="docking",
                )
                if existing is not None:
                    discarded = await self._discard_staging(
                        task_id,
                        manifest_path,
                    )
                    return BackendSubmitResult(
                        existing.task_id,
                        existing.backend,
                        existing.outcome,
                        (
                            _IDEMPOTENT_REUSE_DISCARDED_MESSAGE
                            if discarded
                            else _IDEMPOTENT_REUSE_MESSAGE
                        ),
                    )

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
        except asyncio.CancelledError:
            await self._discard_staging_after_cancellation(task_id, manifest_path)
            raise
        except Exception:
            await self._discard_staging(task_id, manifest_path)
            raise

        if decision.backend == "local":
            try:
                local_result = await self.local_backend.submit(submission)
            except asyncio.CancelledError:
                await self._discard_staging_after_cancellation(
                    task_id,
                    manifest_path,
                )
                raise
            except TaskIdempotencyConflictError:
                recovered = await self._recover_idempotency_race(
                    submission,
                    task_id,
                    manifest_path,
                )
                if recovered is not None:
                    return recovered
                raise
            except Exception as exc:
                await self._project_start_failure(
                    submission,
                    backend="local",
                    error_code=TaskErrorCode.TASK_BACKEND_UNAVAILABLE,
                )
                raise TaskBackendStartError("Local start failed") from exc
            try:
                return await self._validated_local_result(submission, local_result)
            except TaskBackendStartError:
                await self._discard_staging(task_id, manifest_path)
                raise

        if decision.backend != "temporal" or self.temporal_backend is None:
            await self._mark_temporal_ambiguous(submission)
            raise TaskBackendStartError("Temporal start was unavailable")

        try:
            temporal_result = await self.temporal_backend.submit(submission)
        except asyncio.CancelledError:
            await self._mark_temporal_ambiguous_after_cancellation(submission)
            raise
        except TaskIdempotencyConflictError:
            recovered = await self._recover_idempotency_race(
                submission,
                task_id,
                manifest_path,
            )
            if recovered is not None:
                return recovered
            raise
        except Exception as exc:
            await self._mark_temporal_ambiguous(submission)
            raise TaskBackendStartError(
                "Temporal start was not confirmed; local fallback suppressed"
            ) from exc

        if (
            not isinstance(temporal_result, BackendSubmitResult)
            or temporal_result.backend != "temporal"
            or (
                temporal_result.task_id != task_id
                and idempotency_key is None
            )
        ):
            await self._mark_temporal_ambiguous(submission)
            raise TaskBackendStartError(
                "Temporal start returned an ambiguous result; local fallback suppressed"
            )
        if temporal_result.outcome is StartOutcome.ACCEPTED:
            try:
                validated = await self._validated_temporal_result(
                    submission,
                    temporal_result,
                )
                if validated.task_id != task_id:
                    discarded = await self._discard_staging(task_id, manifest_path)
                    return BackendSubmitResult(
                        validated.task_id,
                        validated.backend,
                        validated.outcome,
                        (
                            _IDEMPOTENT_REUSE_DISCARDED_MESSAGE
                            if discarded
                            else _IDEMPOTENT_REUSE_MESSAGE
                        ),
                    )
                return validated
            except asyncio.CancelledError:
                await self._mark_temporal_ambiguous_after_cancellation(submission)
                raise
            except TaskBackendStartError:
                if temporal_result.task_id == task_id:
                    await self._mark_temporal_ambiguous(submission)
                else:
                    await self._discard_staging(task_id, manifest_path)
                raise
        if temporal_result.outcome is StartOutcome.REJECTED:
            if await self._projection_exists(task_id):
                await self._mark_temporal_ambiguous(submission)
                raise TaskBackendStartError(
                    "Temporal start rejection conflicted with an existing projection"
                )
            try:
                local_result = await self.local_backend.submit(submission)
            except asyncio.CancelledError:
                await self._discard_staging_after_cancellation(
                    task_id,
                    manifest_path,
                )
                raise
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
                await self._discard_staging(task_id, manifest_path)
                raise

        if temporal_result.task_id != task_id and idempotency_key is not None:
            try:
                await self._validated_temporal_result(submission, temporal_result)
            except TaskBackendStartError:
                await self._discard_staging(task_id, manifest_path)
                raise
            discarded = await self._discard_staging(task_id, manifest_path)
            return BackendSubmitResult(
                temporal_result.task_id,
                temporal_result.backend,
                StartOutcome.AMBIGUOUS,
                (
                    _IDEMPOTENT_REUSE_DISCARDED_MESSAGE
                    if discarded
                    else _IDEMPOTENT_REUSE_MESSAGE
                ),
            )
        await self._mark_temporal_ambiguous(submission)
        raise TaskBackendStartError(
            "Temporal start was ambiguous; local fallback suppressed"
        )

    async def get(self, task_id: str) -> TaskRecord:
        record = await asyncio.to_thread(self.store.get, task_id)
        if record.backend == "temporal" and self.temporal_backend is not None:
            return await self.temporal_backend.get(task_id)
        return record

    async def events(self, task_id: str):
        await asyncio.to_thread(self.store.get, task_id)
        return await asyncio.to_thread(self.store.events, task_id)

    async def _stage_verified(
        self,
        *,
        task_id: str,
        receptor_name: str,
        receptor_bytes: bytes,
        ligand_name: str | None,
        ligand_bytes: bytes | None,
        smiles: str | None,
        config: Mapping[str, Any],
    ) -> tuple[Path, dict[str, Any], str]:
        manifest_path: Path | None = None
        stage_task = asyncio.create_task(
            asyncio.to_thread(
                self.stager.stage,
                task_id,
                receptor_name,
                receptor_bytes,
                ligand_name,
                ligand_bytes,
                smiles,
                config,
            ),
            name=f"medchat-stage-{task_id}",
        )
        try:
            try:
                staged_path = await asyncio.shield(stage_task)
            except asyncio.CancelledError:
                try:
                    staged_path = await self._await_task_outcome(stage_task)
                    manifest_path = Path(staged_path)
                except Exception:
                    pass
                raise
            manifest_path = Path(staged_path)
            verify_task = asyncio.create_task(
                asyncio.to_thread(
                    self.stager.load_verified,
                    task_id,
                    manifest_path,
                ),
                name=f"medchat-verify-stage-{task_id}",
            )
            try:
                verified = await asyncio.shield(verify_task)
            except asyncio.CancelledError:
                try:
                    await self._await_task_outcome(verify_task)
                except Exception:
                    pass
                raise
            if not isinstance(verified, dict):
                raise ValueError("invalid verified manifest")
            request_digest = self._docking_request_digest(verified)
            return manifest_path, verified, request_digest
        except asyncio.CancelledError:
            if manifest_path is not None:
                await self._discard_staging_after_cancellation(
                    task_id,
                    manifest_path,
                )
            raise
        except Exception:
            if manifest_path is not None:
                await self._discard_staging(task_id, manifest_path)
            raise

    @staticmethod
    async def _await_task_outcome(task: asyncio.Task[Any]) -> Any:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    async def _discard_staging_after_cancellation(
        self,
        task_id: str,
        manifest_path: Path,
    ) -> None:
        cleanup = asyncio.create_task(
            self._discard_staging(task_id, manifest_path),
            name=f"medchat-discard-stage-{task_id}",
        )
        try:
            await self._await_task_outcome(cleanup)
        except Exception:
            pass

    async def _mark_temporal_ambiguous_after_cancellation(
        self,
        submission: TaskSubmission,
    ) -> None:
        marker = asyncio.create_task(
            self._mark_temporal_ambiguous(submission),
            name=f"medchat-mark-temporal-ambiguous-{submission.task_id}",
        )
        try:
            await self._await_task_outcome(marker)
        except Exception:
            pass

    async def _discard_staging(
        self,
        task_id: str,
        manifest_path: Path,
    ) -> bool:
        discard = getattr(self.stager, "discard_unprojected", None)
        if discard is None:
            return False
        try:
            return bool(
                await asyncio.to_thread(
                    discard,
                    task_id,
                    manifest_path,
                    projection_check=self._projection_exists_sync,
                )
            )
        except Exception:
            return False

    def _projection_exists_sync(self, task_id: str) -> bool:
        try:
            self.store.get(task_id)
        except KeyError:
            return False
        return True

    async def cleanup_staging_once(
        self,
        *,
        cutoff_epoch: float | None = None,
        limit: int = _STAGING_CLEANUP_LIMIT,
    ) -> list[str]:
        cleanup = getattr(self.stager, "cleanup_unprojected_expired", None)
        if cleanup is None:
            return []
        cutoff = (
            time.time() - _STAGING_ORPHAN_TTL_SECONDS
            if cutoff_epoch is None
            else cutoff_epoch
        )
        return await asyncio.to_thread(
            cleanup,
            cutoff,
            projection_check=self._projection_exists_sync,
            limit=limit,
        )

    async def _maybe_cleanup_staging(self) -> None:
        now = time.monotonic()
        with self._staging_cleanup_lock:
            if now - self._last_staging_cleanup < _STAGING_CLEANUP_INTERVAL_SECONDS:
                return
            self._last_staging_cleanup = now
        try:
            await self.cleanup_staging_once()
        except Exception:
            return

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
        with self._close_lock:
            if self._closed:
                return
            shared = self._close_future
            if shared is None:
                shared = Future()
                self._close_future = shared
                self._close_runner = asyncio.create_task(
                    self._close_once(shared),
                    name="medchat-task-runtime-close",
                )
        await asyncio.shield(asyncio.wrap_future(shared))

    async def _close_once(self, shared: Future[None]) -> None:
        try:
            await self._close_backends()
        except BaseException as exc:
            with self._close_lock:
                if self._close_future is shared:
                    self._close_future = None
                    self._close_runner = None
            if not shared.done():
                shared.set_exception(exc)
            return
        with self._close_lock:
            self._closed = True
            if self._close_future is shared:
                self._close_runner = None
        if not shared.done():
            shared.set_result(None)

    async def _close_backends(self) -> None:
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

    async def _recover_idempotency_race(
        self,
        submission: TaskSubmission,
        loser_task_id: str,
        manifest_path: Path,
    ) -> BackendSubmitResult | None:
        """Resolve a backend create race against the global idempotency authority."""

        if submission.idempotency_key is None or submission.request_digest is None:
            await self._discard_staging(loser_task_id, manifest_path)
            return None
        try:
            authority = await self._global_idempotency_result(
                submission.idempotency_key,
                submission.request_digest,
                task_type=submission.task_type,
            )
            discarded = await self._discard_staging(
                loser_task_id,
                manifest_path,
            )
        except asyncio.CancelledError:
            await self._discard_staging_after_cancellation(
                loser_task_id,
                manifest_path,
            )
            raise
        except Exception:
            await self._discard_staging(loser_task_id, manifest_path)
            raise
        if authority is None:
            return None
        return BackendSubmitResult(
            authority.task_id,
            authority.backend,
            authority.outcome,
            (
                _IDEMPOTENT_REUSE_DISCARDED_MESSAGE
                if discarded
                else _IDEMPOTENT_REUSE_MESSAGE
            ),
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
            outcome = StartOutcome.ACCEPTED
            if (
                record.backend == "temporal"
                and (record.provenance or {}).get("start_outcome") != "accepted"
            ):
                outcome = StartOutcome.AMBIGUOUS
            return BackendSubmitResult(
                task_id=record.task_id,
                backend=record.backend,
                outcome=outcome,
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

    async def _validated_temporal_result(
        self,
        submission: TaskSubmission,
        result: BackendSubmitResult,
    ) -> BackendSubmitResult:
        try:
            record, stored_request_digest = await asyncio.to_thread(
                self._task_authority,
                result.task_id,
            )
        except Exception as exc:
            raise TaskBackendStartError(
                "Temporal accepted start has no matching authority"
            ) from exc
        if (
            record.task_id != result.task_id
            or record.task_type != submission.task_type
            or record.backend != "temporal"
            or stored_request_digest != submission.request_digest
        ):
            raise TaskBackendStartError(
                "Temporal accepted start has conflicting authority"
            )
        if submission.idempotency_key is not None:
            digest = LocalTaskBackend.idempotency_digest(
                submission.idempotency_key
            )
            try:
                authority, authority_request_digest = await asyncio.to_thread(
                    self.store.get_idempotency_authority,
                    digest,
                )
            except Exception as exc:
                raise TaskBackendStartError(
                    "Temporal accepted start has no matching authority"
                ) from exc
            if (
                authority.task_id != result.task_id
                or authority.task_type != submission.task_type
                or authority.backend != "temporal"
                or authority_request_digest != submission.request_digest
            ):
                raise TaskBackendStartError(
                    "Temporal accepted start has conflicting authority"
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
                if submission.idempotency_key is None:
                    record = await asyncio.to_thread(
                        self.store.get,
                        submission.task_id,
                    )
                else:
                    digest = self._submission_idempotency_digest(submission)
                    record, stored_request_digest = await asyncio.to_thread(
                        self.store.get_idempotency_authority,
                        digest,
                    )
                    if (
                        record.backend != "temporal"
                        or record.task_type != submission.task_type
                        or stored_request_digest != submission.request_digest
                    ):
                        raise TaskIdempotencyConflictError(
                            "idempotency authority conflict"
                        )

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
                    external_workflow_id=(
                        f"medchat-docking-{submission.task_id}"
                    ),
                    phase=TaskPhase.STAGING,
                    warnings=warning,
                    provenance=provenance,
                    idempotency_digest=self._submission_idempotency_digest(submission),
                    submission_digest=submission.request_digest,
                )
            except sqlite3.IntegrityError:
                record = await asyncio.to_thread(self.store.get, submission.task_id)
        if record.backend != "temporal":
            raise TaskBackendStartError(
                "Temporal ambiguity conflicts with existing task authority"
            )
        await asyncio.to_thread(
            self.store.mark_temporal_start_outcome,
            submission.task_id,
            "ambiguous",
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
_RUNTIME_RESET_FUTURE: Future[None] | None = None
_RUNTIME_RESET_RUNNER: asyncio.Task[None] | None = None
_RUNTIME_BINDING_COUNT = 0
_RUNTIME_POISONED = False


class TaskRuntimeBinding:
    """Bind the process singleton to an application lifecycle."""

    _IDLE = "idle"
    _ACTIVE = "active"
    _CLOSING = "closing"
    _POISONED = "poisoned"

    def __init__(self, *, factory=None, shutdown=None) -> None:
        self._factory = factory or acquire_task_runtime
        self._shutdown = shutdown or release_task_runtime
        self._runtime = None
        self._state = self._IDLE
        self._lock = threading.Lock()
        self._close_future: Future[None] | None = None
        self._close_runner: asyncio.Task[None] | None = None

    def start(self):
        with self._lock:
            if self._state == self._ACTIVE:
                return self._runtime
            if self._state in {self._CLOSING, self._POISONED}:
                raise RuntimeError("task runtime unavailable")
            runtime = self._factory()
            self._runtime = runtime
            self._state = self._ACTIVE
            return self._runtime

    def __call__(self):
        return self.start()

    def install(self, app, logger) -> None:
        async def startup() -> None:
            try:
                self.start()
            except Exception:
                logger.warning("Durable task runtime initialization failed")

        async def shutdown() -> None:
            try:
                await self.close()
            except Exception:
                logger.warning("Durable task runtime shutdown failed")

        app.router.add_event_handler("startup", startup)
        app.router.add_event_handler("shutdown", shutdown)

    async def close(self) -> None:
        with self._lock:
            if self._state == self._IDLE:
                return
            runtime = self._runtime
            shared = self._close_future
            if self._state != self._CLOSING or shared is None:
                shared = Future()
                self._close_future = shared
                self._state = self._CLOSING
                self._close_runner = asyncio.create_task(
                    self._close_once(runtime, shared),
                    name="medchat-task-runtime-binding-close",
                )
        await asyncio.shield(asyncio.wrap_future(shared))

    async def _close_once(self, runtime, shared: Future[None]) -> None:
        try:
            await self._shutdown(runtime)
        except BaseException as exc:
            with self._lock:
                if self._close_future is shared:
                    self._state = self._POISONED
                    self._close_future = None
                    self._close_runner = None
            if not shared.done():
                shared.set_exception(exc)
            return
        with self._lock:
            if self._close_future is shared and self._runtime is runtime:
                self._runtime = None
                self._state = self._IDLE
                self._close_future = None
                self._close_runner = None
        if not shared.done():
            shared.set_result(None)


def get_task_runtime() -> TaskRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME_RESET_FUTURE is not None or _RUNTIME_POISONED:
            raise RuntimeError("task runtime unavailable")
        if _RUNTIME is None:
            _RUNTIME = TaskRuntime()
        return _RUNTIME


def acquire_task_runtime() -> TaskRuntime:
    global _RUNTIME, _RUNTIME_BINDING_COUNT
    with _RUNTIME_LOCK:
        if _RUNTIME_RESET_FUTURE is not None or _RUNTIME_POISONED:
            raise RuntimeError("task runtime unavailable")
        if _RUNTIME is None:
            _RUNTIME = TaskRuntime()
        _RUNTIME_BINDING_COUNT += 1
        return _RUNTIME


async def release_task_runtime(expected_runtime: TaskRuntime) -> None:
    global _RUNTIME_BINDING_COUNT, _RUNTIME_RESET_FUTURE, _RUNTIME_RESET_RUNNER
    with _RUNTIME_LOCK:
        if _RUNTIME is not expected_runtime:
            return
        shared = _RUNTIME_RESET_FUTURE
        if shared is None:
            if _RUNTIME_BINDING_COUNT > 1:
                _RUNTIME_BINDING_COUNT -= 1
                return
            if _RUNTIME_BINDING_COUNT <= 0:
                return
            shared = Future()
            _RUNTIME_RESET_FUTURE = shared
            _RUNTIME_RESET_RUNNER = asyncio.create_task(
                _reset_runtime_once(expected_runtime, shared),
                name="medchat-task-runtime-release",
            )
    await asyncio.shield(asyncio.wrap_future(shared))


async def shutdown_task_runtime() -> None:
    global _RUNTIME_RESET_FUTURE, _RUNTIME_RESET_RUNNER
    with _RUNTIME_LOCK:
        runtime = _RUNTIME
        if runtime is None:
            return
        shared = _RUNTIME_RESET_FUTURE
        if shared is None:
            shared = Future()
            _RUNTIME_RESET_FUTURE = shared
            _RUNTIME_RESET_RUNNER = asyncio.create_task(
                _reset_runtime_once(runtime, shared),
                name="medchat-task-runtime-reset",
            )
    await asyncio.shield(asyncio.wrap_future(shared))


async def reset_task_runtime_for_tests() -> None:
    await shutdown_task_runtime()


async def _reset_runtime_once(
    runtime: TaskRuntime,
    shared: Future[None],
) -> None:
    global _RUNTIME, _RUNTIME_BINDING_COUNT, _RUNTIME_POISONED
    global _RUNTIME_RESET_FUTURE, _RUNTIME_RESET_RUNNER
    try:
        await runtime.close()
    except BaseException as exc:
        with _RUNTIME_LOCK:
            if _RUNTIME_RESET_FUTURE is shared:
                _RUNTIME_RESET_FUTURE = None
                _RUNTIME_RESET_RUNNER = None
                if _RUNTIME is runtime:
                    _RUNTIME_POISONED = True
        if not shared.done():
            shared.set_exception(exc)
        return
    with _RUNTIME_LOCK:
        if _RUNTIME is runtime:
            _RUNTIME = None
            _RUNTIME_BINDING_COUNT = 0
            _RUNTIME_POISONED = False
        if _RUNTIME_RESET_FUTURE is shared:
            _RUNTIME_RESET_FUTURE = None
            _RUNTIME_RESET_RUNNER = None
    if not shared.done():
        shared.set_result(None)
