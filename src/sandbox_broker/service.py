"""Single-consumer lifecycle service for sandbox docking jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from src.task_runtime.config import _stat_identity
from src.task_runtime.secure_io import read_file_snapshot

from .artifacts import ArtifactRegistry
from .config import BrokerConfig
from .models import (
    TERMINAL_STATUSES,
    BrokerErrorCode,
    BrokerJobStatus,
    BrokerProvenance,
    DockingManifest,
    DockingParameters,
)
from .opensandbox_client import SandboxCommandResult, SandboxHandle
from .store import ArtifactRecord, BrokerJobRecord, BrokerStore
from .validation import (
    ScientificOutputError,
    StagedInput,
    ValidatedScientificOutput,
    validate_scientific_output,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TRACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UUID_HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_TOOL_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+_()-]{0,127}$")
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?:api[_ -]?key|token|password|secret|credential|bearer\s+)"
)
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]")
_UNC_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:\\\\|//)[^\\/\s]+[\\/]"
)
_POSIX_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])/(?!/)(?=$|[A-Za-z0-9._~-])"
)
_MANIFEST_MAX_BYTES = 1024 * 1024
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class BrokerFailure(Exception):
    """A stable, sanitized Broker failure safe for API mapping."""

    def __init__(self, code: BrokerErrorCode) -> None:
        if type(code) is not BrokerErrorCode:
            raise TypeError("code must be BrokerErrorCode")
        self.code = code
        super().__init__(code.value)


def _safe_staged_input(value: object, role: str) -> StagedInput:
    if type(value) is not StagedInput:
        raise ValueError
    relative = value.relative_path
    if type(relative) is not str or not relative or "\\" in relative or ":" in relative:
        raise ValueError
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or str(pure) != relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
        or len(pure.parts) < 2
        or pure.name != f"{role}{pure.suffix}"
    ):
        raise ValueError
    approved = {
        "receptor": frozenset({".pdb", ".pdbqt"}),
        "ligand": frozenset({".sdf", ".mol", ".pdb", ".pdbqt"}),
    }
    if pure.suffix.lower() not in approved[role]:
        raise ValueError
    if type(value.size_bytes) is not int or value.size_bytes <= 0:
        raise ValueError
    if type(value.sha256) is not str or _SHA256_PATTERN.fullmatch(value.sha256) is None:
        raise ValueError
    return value


def canonical_submission_sha256(
    parameters: DockingParameters,
    receptor: StagedInput,
    ligand: StagedInput,
) -> str:
    """Hash only the bounded scientific request, never host paths."""

    if type(parameters) is not DockingParameters:
        raise ValueError("prepared submission is invalid")
    try:
        checked_receptor = _safe_staged_input(receptor, "receptor")
        checked_ligand = _safe_staged_input(ligand, "ligand")
        payload = {
            "ligand": {
                "sha256": checked_ligand.sha256,
                "size_bytes": checked_ligand.size_bytes,
                "suffix": PurePosixPath(checked_ligand.relative_path).suffix.lower(),
            },
            "parameters": parameters.model_dump(mode="json"),
            "receptor": {
                "sha256": checked_receptor.sha256,
                "size_bytes": checked_receptor.size_bytes,
                "suffix": PurePosixPath(checked_receptor.relative_path).suffix.lower(),
            },
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except Exception:
        raise ValueError("prepared submission is invalid") from None
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PreparedDockingSubmission:
    """Host-path-free immutable metadata for an already staged request."""

    parameters: DockingParameters
    receptor: StagedInput
    ligand: StagedInput
    canonical_input_sha256: str
    trace_id: str

    def __post_init__(self) -> None:
        try:
            expected = canonical_submission_sha256(
                self.parameters,
                self.receptor,
                self.ligand,
            )
            if (
                type(self.canonical_input_sha256) is not str
                or self.canonical_input_sha256 != expected
                or type(self.trace_id) is not str
                or _TRACE_PATTERN.fullmatch(self.trace_id) is None
            ):
                raise ValueError
        except Exception:
            raise ValueError("prepared submission is invalid") from None


@dataclass(frozen=True)
class BrokerJobView:
    """Deeply immutable public job snapshot."""

    job_id: str
    trace_id: str
    status: BrokerJobStatus
    phase: str
    sandbox_id: str | None
    error_code: str | None
    warnings: tuple[str, ...] = field(repr=False)
    provenance: BrokerProvenance | None = field(repr=False)
    cancel_requested: bool
    cleanup_status: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class _PreparedCompletion:
    manifest: DockingManifest


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _record_view(record: BrokerJobRecord) -> BrokerJobView:
    try:
        provenance = (
            None
            if record.provenance is None
            else BrokerProvenance.model_validate(record.provenance)
        )
        return BrokerJobView(
            job_id=record.job_id,
            trace_id=record.trace_id,
            status=record.status,
            phase=record.phase,
            sandbox_id=record.sandbox_id,
            error_code=record.error_code,
            warnings=tuple(record.warnings),
            provenance=provenance,
            cancel_requested=record.cancel_requested,
            cleanup_status=record.cleanup_status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
    except Exception:
        raise RuntimeError("persisted broker job is invalid") from None


class SandboxBrokerService:
    """Own the one-consumer queue and the complete remote lifecycle."""

    def __init__(
        self,
        config: BrokerConfig,
        store: BrokerStore,
        client: object,
        artifact_registry: ArtifactRegistry,
    ) -> None:
        if (
            type(config) is not BrokerConfig
            or not isinstance(store, BrokerStore)
            or not isinstance(artifact_registry, ArtifactRegistry)
        ):
            raise ValueError("invalid sandbox broker service configuration")
        self.config = config
        self.store = store
        self.client = client
        self.artifact_registry = artifact_registry
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=config.queue_capacity)
        self._started = False
        self._stopping = False
        self._consumer_task: asyncio.Task[None] | None = None
        self._job_tasks: dict[str, asyncio.Task[None]] = {}
        self._create_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cleanup_tasks: dict[str, asyncio.Task[Any]] = {}
        self._prepared: dict[str, PreparedDockingSubmission] = {}
        self._terminal_events: dict[str, asyncio.Event] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self._retention_lock = asyncio.Lock()
        self._stop_timeout_seconds = 5.0

    async def start(self) -> None:
        """Start exactly one consumer; repeated calls are harmless."""

        async with self._lifecycle_lock:
            if self._started and self._consumer_task is not None and not self._consumer_task.done():
                return
            self._stopping = False
            self._started = True
            self._consumer_task = asyncio.create_task(
                self._consume(), name="sandbox-broker-consumer"
            )

    async def stop(self) -> None:
        """Stop the consumer and coordinate any active job cleanup."""

        async with self._lifecycle_lock:
            if not self._started and self._consumer_task is None:
                return
            self._started = False
            self._stopping = True
            consumer = self._consumer_task
            self._consumer_task = None
            if consumer is not None and not consumer.done():
                consumer.cancel()
        if consumer is not None:
            done, pending = await asyncio.wait(
                {consumer}, timeout=self._stop_timeout_seconds
            )
            if pending:
                self._terminalize_stop_timeout()
                for task in tuple(self._cleanup_tasks.values()):
                    if not task.done():
                        task.cancel()
                for task in tuple(self._job_tasks.values()):
                    if not task.done():
                        task.cancel()
                consumer.cancel()
                tracked = {
                    consumer,
                    *self._job_tasks.values(),
                    *self._create_tasks.values(),
                    *self._cleanup_tasks.values(),
                }
                if tracked:
                    await asyncio.wait(
                        tracked,
                        timeout=self._stop_timeout_seconds,
                    )
            if consumer.done():
                try:
                    consumer.result()
                except BaseException:
                    pass
        self._fail_unstarted_queued_jobs()
        self._stopping = False

    def _terminalize_stop_timeout(self) -> None:
        for record in self.store.active_jobs():
            try:
                create_task = self._create_tasks.get(record.job_id)
                create_result_unknown = (
                    create_task is not None and not create_task.done()
                )
                if record.sandbox_id is None and not create_result_unknown:
                    self.store.record_cleanup(record.job_id, "succeeded")
                    self.store.fail(
                        record.job_id,
                        BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                        cleanup_status="succeeded",
                    )
                else:
                    self.store.fail(
                        record.job_id,
                        BrokerErrorCode.CLEANUP_FAILED,
                        warnings=("cleanup_failed",),
                        cleanup_status="failed",
                    )
                self._terminal_events.setdefault(record.job_id, asyncio.Event()).set()
            except BaseException:
                pass

    def _fail_unstarted_queued_jobs(self) -> None:
        for record in self.store.active_jobs():
            if record.status is not BrokerJobStatus.QUEUED:
                continue
            try:
                self.store.record_cleanup(record.job_id, "succeeded")
                self.store.fail(
                    record.job_id,
                    BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                    cleanup_status="succeeded",
                )
                self._prepared.pop(record.job_id, None)
                self._terminal_events.setdefault(record.job_id, asyncio.Event()).set()
            except BaseException:
                pass

    async def submit(
        self,
        prepared: PreparedDockingSubmission,
        idempotency_key: str,
    ) -> BrokerJobView:
        if type(prepared) is not PreparedDockingSubmission:
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT)
        if type(idempotency_key) is not str or not idempotency_key.strip():
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT)
        if (
            not self._started
            or self._stopping
            or self._consumer_task is None
            or self._consumer_task.done()
        ):
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
        try:
            record, reused = self.store.create_or_get(
                idempotency_key,
                prepared.canonical_input_sha256,
                prepared.trace_id,
            )
        except Exception as exc:
            from .store import IdempotencyConflict

            if isinstance(exc, IdempotencyConflict):
                raise BrokerFailure(BrokerErrorCode.IDEMPOTENCY_CONFLICT) from None
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
        if reused:
            return _record_view(record)

        self._prepared[record.job_id] = prepared
        self._terminal_events.setdefault(record.job_id, asyncio.Event())
        try:
            self._queue.put_nowait(record.job_id)
        except asyncio.QueueFull:
            self._prepared.pop(record.job_id, None)
            terminal = self.store.fail(
                record.job_id,
                BrokerErrorCode.QUEUE_SATURATED,
                cleanup_status="succeeded",
            )
            self._terminal_events[record.job_id].set()
            del terminal
            raise BrokerFailure(BrokerErrorCode.QUEUE_SATURATED) from None
        return _record_view(record)

    def get_job(self, job_id: str) -> BrokerJobView:
        try:
            record = self.store.get(job_id)
        except Exception:
            raise KeyError("job not found") from None
        if record is None:
            raise KeyError("job not found")
        return _record_view(record)

    async def wait_terminal(
        self,
        job_id: str,
        *,
        timeout: float | None = None,
    ) -> BrokerJobView:
        record = self.get_job(job_id)
        if record.status in TERMINAL_STATUSES:
            return record
        event = self._terminal_events.setdefault(job_id, asyncio.Event())
        if timeout is None:
            await event.wait()
        else:
            if type(timeout) not in (int, float) or not math.isfinite(float(timeout)) or timeout < 0:
                raise ValueError("terminal wait timeout is invalid")
            await asyncio.wait_for(event.wait(), float(timeout))
        return self.get_job(job_id)

    async def _consume(self) -> None:
        while True:
            job_id = await self._queue.get()
            current: asyncio.Task[None] | None = None
            try:
                record = self.store.get(job_id)
                if record is None or record.status in TERMINAL_STATUSES:
                    continue
                current = asyncio.create_task(
                    self._run_job(job_id),
                    name=f"sandbox-broker-job-{job_id}",
                )
                self._job_tasks[job_id] = current
                try:
                    await asyncio.shield(current)
                except asyncio.CancelledError:
                    current.cancel()
                    try:
                        await asyncio.shield(current)
                    except BaseException:
                        pass
                    raise
            finally:
                self._job_tasks.pop(job_id, None)
                self._prepared.pop(job_id, None)
                self._terminal_events.setdefault(job_id, asyncio.Event()).set()
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        handle: SandboxHandle | None = None
        completion: _PreparedCompletion | None = None
        failure: BrokerErrorCode | None = None
        cancelled = False
        cleanup = "succeeded"
        try:
            self.store.transition(job_id, BrokerJobStatus.PROVISIONING)
            try:
                created, create_interrupted = await self._create_without_cancelling(
                    job_id
                )
                if type(created) is not SandboxHandle:
                    raise ValueError
                handle = created
                self.store.attach_sandbox(job_id, handle.sandbox_id)
                if create_interrupted:
                    cancelled = True
                    return
            except asyncio.CancelledError:
                raise
            except BaseException:
                failure = BrokerErrorCode.PROVISIONING_FAILED
                return

            self.store.transition(job_id, BrokerJobStatus.UPLOADING)
            try:
                await self._upload_fixed_inputs(job_id, handle)
            except asyncio.CancelledError:
                raise
            except BaseException:
                failure = BrokerErrorCode.UPLOAD_FAILED
                return

            self.store.transition(job_id, BrokerJobStatus.RUNNING)
            try:
                command = await asyncio.wait_for(
                    self.client.run(handle),
                    timeout=self.config.execution_timeout_seconds,
                )
            except asyncio.TimeoutError:
                failure = BrokerErrorCode.EXECUTION_TIMEOUT
                return
            if type(command) is not SandboxCommandResult:
                failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
                return
            if command.exit_code != 0:
                failure = BrokerErrorCode.COMMAND_FAILED
                return

            self.store.transition(job_id, BrokerJobStatus.VALIDATING)
            try:
                completion = await self._download_validate_publish(job_id, handle)
            except ScientificOutputError:
                failure = BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID
                return
            except asyncio.CancelledError:
                raise
            except BaseException:
                failure = BrokerErrorCode.ARTIFACT_FAILED
                return
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
        finally:
            cleanup = await self._destroy_best_effort(job_id, handle)
            try:
                record = self.store.get(job_id)
                if record is None or record.status in TERMINAL_STATUSES:
                    return
                if cleanup != "succeeded":
                    failed_provenance = (
                        None
                        if completion is None
                        else completion.manifest.provenance.model_copy(
                            update={"cleanup_status": "failed"}
                        ).model_dump(mode="json")
                    )
                    prior_warnings = (
                        ()
                        if completion is None
                        else tuple(completion.manifest.warnings)
                    )
                    self.store.fail(
                        job_id,
                        BrokerErrorCode.CLEANUP_FAILED,
                        warnings=prior_warnings + ("cleanup_failed",),
                        provenance=failed_provenance,
                        cleanup_status="failed",
                    )
                elif cancelled or record.cancel_requested:
                    self.store.cancel(job_id, cleanup_status="succeeded")
                elif failure is not None:
                    self.store.fail(job_id, failure, cleanup_status="succeeded")
                elif completion is None:
                    self.store.fail(
                        job_id,
                        BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                        cleanup_status="succeeded",
                    )
                else:
                    succeeded_provenance = completion.manifest.provenance.model_copy(
                        update={"cleanup_status": "succeeded"}
                    )
                    final_manifest = completion.manifest.model_copy(
                        update={"provenance": succeeded_provenance}
                    )
                    try:
                        self._publish_manifest(job_id, final_manifest)
                        self.store.complete(
                            job_id,
                            warnings=tuple(final_manifest.warnings),
                            provenance=succeeded_provenance.model_dump(mode="json"),
                        )
                    except BaseException:
                        latest = self.store.get(job_id)
                        if latest is not None and latest.status not in TERMINAL_STATUSES:
                            self.store.fail(
                                job_id,
                                BrokerErrorCode.ARTIFACT_FAILED,
                                warnings=(),
                                provenance=succeeded_provenance.model_dump(mode="json"),
                                cleanup_status="succeeded",
                            )
            except BaseException:
                pass

    async def _create_without_cancelling(
        self,
        job_id: str,
    ) -> tuple[object, bool]:
        """Let the bounded SDK create finish even when its caller is cancelled."""

        create_task = asyncio.create_task(
            self.client.create(job_id),
            name=f"sandbox-broker-create-{job_id}",
        )
        self._create_tasks[job_id] = create_task
        interrupted = False
        try:
            while not create_task.done():
                try:
                    await asyncio.shield(create_task)
                except asyncio.CancelledError:
                    interrupted = True
                    continue
                except BaseException:
                    break
            return create_task.result(), interrupted
        finally:
            if self._create_tasks.get(job_id) is create_task:
                del self._create_tasks[job_id]

    async def cancel(self, job_id: str) -> BrokerJobView:
        """Request cancellation once and coordinate the active execution task."""

        try:
            record = self.store.request_cancel(job_id)
        except KeyError:
            raise KeyError("job not found") from None
        except Exception:
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE) from None
        if record.status in TERMINAL_STATUSES:
            return _record_view(record)
        task = self._job_tasks.get(job_id)
        if record.status is BrokerJobStatus.QUEUED and task is None:
            try:
                self.store.record_cleanup(job_id, "succeeded")
                terminal = self.store.cancel(job_id, cleanup_status="succeeded")
            except Exception:
                raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE) from None
            self._prepared.pop(job_id, None)
            self._terminal_events.setdefault(job_id, asyncio.Event()).set()
            return _record_view(terminal)
        if task is not None and not task.done():
            task.cancel()
        current = self.store.get(job_id)
        if current is None:
            raise KeyError("job not found")
        return _record_view(current)

    async def recover(self) -> None:
        """Destroy persisted orphan sandboxes and fail every interrupted job."""

        async with self._recovery_lock:
            for record in self.store.active_jobs():
                try:
                    current = self.store.get(record.job_id)
                    if current is None or current.status in TERMINAL_STATUSES:
                        continue
                    if current.sandbox_id is None:
                        self.store.record_cleanup(current.job_id, "succeeded")
                        self.store.fail(
                            current.job_id,
                            BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                            cleanup_status="succeeded",
                        )
                        self._terminal_events.setdefault(
                            current.job_id, asyncio.Event()
                        ).set()
                        continue
                    if current.cleanup_status != "succeeded":
                        self.store.record_cleanup(current.job_id, "in_progress")
                    try:
                        await self.client.destroy_by_id(current.sandbox_id)
                    except BaseException:
                        latest = self.store.get(current.job_id)
                        if latest is not None and latest.cleanup_status != "succeeded":
                            self.store.record_cleanup(current.job_id, "failed")
                        self.store.fail(
                            current.job_id,
                            BrokerErrorCode.CLEANUP_FAILED,
                            warnings=("cleanup_failed",),
                            cleanup_status="failed",
                        )
                    else:
                        self.store.record_cleanup(current.job_id, "succeeded")
                        self.store.fail(
                            current.job_id,
                            BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                            cleanup_status="succeeded",
                        )
                    self._terminal_events.setdefault(
                        current.job_id, asyncio.Event()
                    ).set()
                except BaseException:
                    current = self.store.get(record.job_id)
                    if current is not None and current.status not in TERMINAL_STATUSES:
                        try:
                            self.store.fail(
                                current.job_id,
                                BrokerErrorCode.CLEANUP_FAILED,
                                warnings=("cleanup_failed",),
                                cleanup_status="failed",
                            )
                        except BaseException:
                            pass

    async def expire(self, now: float) -> None:
        """Apply bounded file and audit retention without following aliases."""

        if (
            type(now) not in (int, float)
            or not math.isfinite(float(now))
            or float(now) < 0
        ):
            raise ValueError("now must be a finite epoch")
        epoch = float(now)
        artifact_cutoff = epoch - self.config.artifact_retention_seconds
        audit_cutoff = epoch - self.config.audit_retention_seconds
        async with self._retention_lock:
            try:
                artifact_candidates = self.store.jobs_updated_before(artifact_cutoff)
            except Exception:
                raise ValueError("retention failed") from None
            for candidate in artifact_candidates:
                current = self.store.get(candidate.job_id)
                if current is None:
                    continue
                if current.status not in TERMINAL_STATUSES:
                    cleanup = await self._expire_active_cleanup(current)
                    if cleanup != "succeeded":
                        continue
                if not self._remove_job_tree(current.job_id):
                    continue
                latest = self.store.get(current.job_id)
                if latest is not None and latest.status not in TERMINAL_STATUSES:
                    try:
                        self.store.expire_active(
                            latest.job_id,
                            cleanup_status="succeeded",
                        )
                    except Exception:
                        continue
                    self._terminal_events.setdefault(
                        latest.job_id, asyncio.Event()
                    ).set()

            try:
                audit_candidates = self.store.jobs_updated_before(audit_cutoff)
            except Exception:
                raise ValueError("retention failed") from None
            for candidate in audit_candidates:
                if candidate.status not in TERMINAL_STATUSES:
                    continue
                if not self._remove_job_tree(candidate.job_id):
                    continue
                try:
                    self.store.delete_terminal_audit(
                        candidate.job_id,
                        cutoff=audit_cutoff,
                    )
                except Exception:
                    continue

    async def _expire_active_cleanup(self, record: BrokerJobRecord) -> str:
        try:
            if record.sandbox_id is None:
                self.store.record_cleanup(record.job_id, "succeeded")
                return "succeeded"
            if record.cleanup_status == "succeeded":
                return "succeeded"
            self.store.record_cleanup(record.job_id, "in_progress")
            try:
                await self.client.destroy_by_id(record.sandbox_id)
            except BaseException:
                self.store.record_cleanup(record.job_id, "failed")
                self.store.fail(
                    record.job_id,
                    BrokerErrorCode.CLEANUP_FAILED,
                    warnings=("cleanup_failed",),
                    cleanup_status="failed",
                )
                self._terminal_events.setdefault(record.job_id, asyncio.Event()).set()
                return "failed"
            self.store.record_cleanup(record.job_id, "succeeded")
            return "succeeded"
        except BaseException:
            current = self.store.get(record.job_id)
            if current is not None and current.status not in TERMINAL_STATUSES:
                try:
                    self.store.fail(
                        record.job_id,
                        BrokerErrorCode.CLEANUP_FAILED,
                        warnings=("cleanup_failed",),
                        cleanup_status="failed",
                    )
                except BaseException:
                    pass
            return "failed"

    def _remove_job_tree(self, job_id: str) -> bool:
        """Remove only a fully inspected real job tree; aliases fail closed."""

        try:
            if type(job_id) is not str or _UUID_HEX_PATTERN.fullmatch(job_id) is None:
                raise ValueError
            root = self.config.state_root.resolve(strict=True)
            jobs = root / "jobs"
            try:
                jobs_metadata = jobs.lstat()
            except FileNotFoundError:
                return True
            if (
                not stat.S_ISDIR(jobs_metadata.st_mode)
                or stat.S_ISLNK(jobs_metadata.st_mode)
                or _is_reparse(jobs_metadata)
            ):
                raise ValueError
            job_root = jobs / job_id
            try:
                root_metadata = job_root.lstat()
            except FileNotFoundError:
                return True
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or stat.S_ISLNK(root_metadata.st_mode)
                or _is_reparse(root_metadata)
            ):
                raise ValueError

            files: list[tuple[Path, tuple[int, ...]]] = []
            directories: list[tuple[Path, tuple[int, ...]]] = []

            def inspect(directory: Path) -> None:
                metadata = directory.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or _is_reparse(metadata)
                ):
                    raise ValueError
                directories.append((directory, _stat_identity(metadata)))
                with os.scandir(directory) as entries:
                    ordered = sorted(list(entries), key=lambda item: item.name)
                for entry in ordered:
                    path = Path(entry.path)
                    item = path.lstat()
                    if entry.is_symlink() or _is_reparse(item):
                        raise ValueError
                    if stat.S_ISDIR(item.st_mode):
                        inspect(path)
                    elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
                        files.append((path, _stat_identity(item)))
                    else:
                        raise ValueError

            inspect(job_root)
            if _stat_identity(jobs.lstat()) != _stat_identity(jobs_metadata):
                raise ValueError
            if _stat_identity(job_root.lstat()) != _stat_identity(root_metadata):
                raise ValueError

            for path, identity in files:
                current = path.lstat()
                if (
                    not stat.S_ISREG(current.st_mode)
                    or stat.S_ISLNK(current.st_mode)
                    or _is_reparse(current)
                    or current.st_nlink != 1
                    or _stat_identity(current) != identity
                ):
                    raise ValueError
                path.unlink()
            for path, identity in reversed(directories):
                current = path.lstat()
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or stat.S_ISLNK(current.st_mode)
                    or _is_reparse(current)
                    or _stat_identity(current) != identity
                ):
                    raise ValueError
                path.rmdir()
            return True
        except FileNotFoundError:
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def _snapshot_input(self, staged: StagedInput, maximum_bytes: int) -> bytes:
        try:
            root = self.config.state_root.resolve(strict=True)
            relative = PurePosixPath(staged.relative_path)
            projected = root.joinpath(*relative.parts)
            resolved_parent = projected.parent.resolve(strict=True)
            if root != resolved_parent and root not in resolved_parent.parents:
                raise ValueError
            before = projected.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or _is_reparse(before)
                or before.st_nlink != 1
                or before.st_size != staged.size_bytes
                or staged.size_bytes > maximum_bytes
            ):
                raise ValueError
            snapshot = read_file_snapshot(projected, maximum_bytes)
            after = projected.lstat()
            if (
                _stat_identity(before) != _stat_identity(after)
                or snapshot.sha256 != staged.sha256
                or len(snapshot.content) != staged.size_bytes
            ):
                raise ValueError
            return snapshot.content
        except Exception:
            raise BrokerFailure(BrokerErrorCode.UPLOAD_FAILED) from None

    async def _upload_fixed_inputs(self, job_id: str, handle: SandboxHandle) -> None:
        prepared = self._prepared.get(job_id)
        if prepared is None:
            raise BrokerFailure(BrokerErrorCode.UPLOAD_FAILED)
        receptor = self._snapshot_input(prepared.receptor, self.config.receptor_max_bytes)
        ligand = self._snapshot_input(prepared.ligand, self.config.ligand_max_bytes)
        try:
            receptor_text = receptor.decode("utf-8", errors="strict")
            ligand_text = ligand.decode("utf-8", errors="strict")
            receptor_suffix = PurePosixPath(prepared.receptor.relative_path).suffix.lower()
            ligand_suffix = PurePosixPath(prepared.ligand.relative_path).suffix.lower()
            request = json.dumps(
                {
                    "schema_version": 1,
                    "parameters": prepared.parameters.model_dump(mode="json"),
                    "receptor_path": f"/workspace/input/receptor{receptor_suffix}",
                    "ligand_path": f"/workspace/input/ligand{ligand_suffix}",
                    "receptor_sha256": prepared.receptor.sha256,
                    "ligand_sha256": prepared.ligand.sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except Exception:
            raise BrokerFailure(BrokerErrorCode.UPLOAD_FAILED) from None
        await self.client.upload_text(
            handle, f"/workspace/input/receptor{receptor_suffix}", receptor_text
        )
        await self.client.upload_text(
            handle, f"/workspace/input/ligand{ligand_suffix}", ligand_text
        )
        await self.client.upload_text(handle, "/workspace/input/request.json", request)

    def _output_directory(self, job_id: str) -> Path:
        try:
            if _UUID_HEX_PATTERN.fullmatch(job_id) is None:
                raise ValueError
            root = self.config.state_root.resolve(strict=True)
            current = root
            for name in ("jobs", job_id, "output"):
                candidate = current / name
                try:
                    candidate.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                metadata = candidate.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or _is_reparse(metadata)
                ):
                    raise ValueError
                current = candidate
            return current
        except Exception:
            raise BrokerFailure(BrokerErrorCode.ARTIFACT_FAILED) from None

    @staticmethod
    def _atomic_output(path: Path, content: bytes, maximum_bytes: int) -> None:
        part = path.parent / f".{path.name}.part"
        try:
            if type(content) is not bytes or len(content) > maximum_bytes:
                raise ValueError
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(part, flags, 0o600)
            try:
                view = memoryview(content)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError
                    view = view[written:]
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ValueError
            finally:
                os.close(descriptor)
            os.replace(part, path)
            final = path.lstat()
            if (
                not stat.S_ISREG(final.st_mode)
                or stat.S_ISLNK(final.st_mode)
                or _is_reparse(final)
                or final.st_nlink != 1
                or final.st_size != len(content)
            ):
                raise ValueError
        except Exception:
            raise BrokerFailure(BrokerErrorCode.ARTIFACT_FAILED) from None
        finally:
            try:
                part.unlink()
            except (FileNotFoundError, OSError):
                pass

    async def _download_validate_publish(
        self,
        job_id: str,
        handle: SandboxHandle,
    ) -> _PreparedCompletion:
        prepared = self._prepared.get(job_id)
        if prepared is None:
            raise ScientificOutputError("scientific output validation failed")
        result_text = await self.client.read_text(handle, "/workspace/output/result.json")
        pose_paths = await self.client.list_files(
            handle, "/workspace/output/poses", "*.pdbqt"
        )
        if pose_paths != ["/workspace/output/poses/result.pdbqt"]:
            raise ScientificOutputError("scientific output validation failed")
        pose_text = await self.client.read_text(handle, pose_paths[0])
        try:
            result_bytes = result_text.encode("utf-8", errors="strict")
            pose_bytes = pose_text.encode("utf-8", errors="strict")
        except Exception:
            raise ScientificOutputError("scientific output validation failed") from None
        if len(result_bytes) + len(pose_bytes) > self.config.output_max_bytes:
            raise ScientificOutputError("scientific output validation failed")
        output = self._output_directory(job_id)
        poses = output / "poses"
        try:
            poses.mkdir(mode=0o700)
        except FileExistsError:
            pass
        pose_dir_metadata = poses.lstat()
        if (
            not stat.S_ISDIR(pose_dir_metadata.st_mode)
            or stat.S_ISLNK(pose_dir_metadata.st_mode)
            or _is_reparse(pose_dir_metadata)
        ):
            raise ScientificOutputError("scientific output validation failed")
        self._atomic_output(output / "result.json", result_bytes, self.config.output_max_bytes)
        self._atomic_output(poses / "result.pdbqt", pose_bytes, self.config.output_max_bytes)
        validated = validate_scientific_output(
            output,
            prepared.receptor.sha256,
            prepared.ligand.sha256,
            self.config.output_max_bytes,
        )
        return self._publish_validated(job_id, prepared, validated, output)

    def _publish_validated(
        self,
        job_id: str,
        prepared: PreparedDockingSubmission,
        validated: ValidatedScientificOutput,
        output: Path,
    ) -> _PreparedCompletion:
        record = self.store.get(job_id)
        if record is None or record.sandbox_id is None:
            raise BrokerFailure(BrokerErrorCode.ARTIFACT_FAILED)
        if (
            _TOOL_VERSION_PATTERN.fullmatch(validated.vina_version) is None
            or _TOOL_VERSION_PATTERN.fullmatch(validated.meeko_version) is None
            or any(not self._safe_warning(item) for item in validated.warnings)
        ):
            raise ScientificOutputError("scientific output validation failed")
        pose_records: list[ArtifactRecord] = []
        for index, relative in enumerate(validated.pose_files, start=1):
            snapshot = read_file_snapshot(
                output.joinpath(*PurePosixPath(relative).parts),
                self.config.output_max_bytes,
            )
            pose_records.append(
                self.artifact_registry.publish(
                    job_id,
                    f"pose-{index}",
                    snapshot.content,
                    "chemical/x-pdbqt",
                )
            )
        provenance = BrokerProvenance(
            sandbox_id=record.sandbox_id,
            image_uri=self.config.image_uri,
            image_digest=self.config.image_digest,
            secure_runtime="gvisor",
            vina_version=validated.vina_version,
            meeko_version=validated.meeko_version,
            receptor_sha256=prepared.receptor.sha256,
            ligand_sha256=prepared.ligand.sha256,
            cleanup_status="not_started",
            demo_mode=False,
            fallback_used=False,
        )
        artifacts = [
            {
                "artifact_id": item.artifact_id,
                "media_type": item.media_type,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in pose_records
        ]
        manifest = DockingManifest(
            job_id=job_id,
            trace_id=record.trace_id,
            pose_count=validated.pose_count,
            best_energy=validated.best_energy,
            artifacts=artifacts,
            warnings=list(validated.warnings),
            provenance=provenance,
        )
        return _PreparedCompletion(manifest)

    @staticmethod
    def _safe_warning(value: object) -> bool:
        if type(value) is not str:
            return False
        normalized = _ANSI_ESCAPE_PATTERN.sub("", value)
        normalized = _CONTROL_CHARACTER_PATTERN.sub("", normalized)
        return bool(
            value.strip()
            and normalized == value
            and _SENSITIVE_TEXT_PATTERN.search(normalized) is None
            and _WINDOWS_PATH_PATTERN.search(normalized) is None
            and _UNC_PATH_PATTERN.search(normalized) is None
            and _POSIX_ABSOLUTE_PATH_PATTERN.search(normalized) is None
        )

    def _publish_manifest(
        self,
        job_id: str,
        manifest: DockingManifest,
    ) -> ArtifactRecord:
        content = json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        return self.artifact_registry.publish(
            job_id, "manifest", content, "application/json"
        )

    async def _destroy_best_effort(
        self,
        job_id: str,
        handle: SandboxHandle | None,
    ) -> str:
        if handle is None:
            try:
                self.store.record_cleanup(job_id, "succeeded")
            except BaseException:
                return "failed"
            return "succeeded"
        cleanup_tracking_active = True
        try:
            self.store.record_cleanup(job_id, "in_progress")
        except BaseException:
            # A bounded stop may already have terminalized an unknown create as
            # cleanup-failed. The raw handle must still be destroyed when it
            # eventually arrives, even though terminal metadata is immutable.
            cleanup_tracking_active = False
        cleanup_task = asyncio.create_task(self.client.destroy(handle))
        cleanup_task.set_name(f"sandbox-broker-cleanup-{job_id}")
        self._cleanup_tasks[job_id] = cleanup_task

        def cleanup_finished(task: asyncio.Task[Any]) -> None:
            if self._cleanup_tasks.get(job_id) is task:
                del self._cleanup_tasks[job_id]
            try:
                task.exception()
            except BaseException:
                pass

        cleanup_task.add_done_callback(cleanup_finished)
        try:
            await asyncio.shield(cleanup_task)
        except BaseException:
            if cleanup_tracking_active:
                try:
                    self.store.record_cleanup(job_id, "failed")
                except BaseException:
                    pass
            return "failed"
        if cleanup_tracking_active:
            try:
                self.store.record_cleanup(job_id, "succeeded")
            except BaseException:
                return "failed"
        return "succeeded"

    def get_manifest(self, job_id: str) -> DockingManifest:
        try:
            record = self.store.get(job_id)
            if record is None or record.status is not BrokerJobStatus.SUCCEEDED:
                raise KeyError
            manifests = [
                artifact
                for artifact in self.store.list_artifacts(job_id)
                if artifact.media_type == "application/json"
            ]
            if len(manifests) != 1:
                raise ValueError
            content = self.artifact_registry.read_registered(
                job_id, manifests[0].artifact_id, _MANIFEST_MAX_BYTES
            )
            payload = json.loads(content.decode("ascii"))
            manifest = DockingManifest.model_validate(payload)
            if (
                manifest.job_id != record.job_id
                or manifest.trace_id != record.trace_id
                or tuple(manifest.warnings) != tuple(record.warnings)
                or record.provenance is None
            ):
                raise ValueError
            persisted_provenance = BrokerProvenance.model_validate(record.provenance)
            if manifest.provenance != persisted_provenance:
                raise ValueError
            pose_records = [
                artifact
                for artifact in self.store.list_artifacts(job_id)
                if artifact.media_type != "application/json"
            ]
            expected = [
                {
                    "artifact_id": item.artifact_id,
                    "media_type": item.media_type,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in pose_records
            ]
            if manifest.artifacts != expected or manifest.pose_count != len(expected):
                raise ValueError
            return manifest
        except Exception:
            raise KeyError("manifest not found") from None

    def read_artifact(self, job_id: str, artifact_id: str) -> bytes:
        if type(artifact_id) is not str:
            raise KeyError("artifact not found")
        manifest = self.get_manifest(job_id)
        allowed = {
            item.get("artifact_id")
            for item in manifest.artifacts
            if type(item) is dict
        }
        if artifact_id not in allowed:
            raise KeyError("artifact not found")
        try:
            return self.artifact_registry.read_registered(
                job_id, artifact_id, self.config.output_max_bytes
            )
        except Exception:
            raise KeyError("artifact not found") from None
