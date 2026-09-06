"""Single-consumer lifecycle service for sandbox docking jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import stat
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator

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
from .opensandbox_client import (
    SandboxCommandResult,
    SandboxCreateError,
    SandboxDestroyError,
    SandboxHandle,
    SandboxProtocolError,
    classify_control_plane_failure,
)
from .resilience import BreakerPermit, ControlPlaneCircuitBreaker
from .safety import contains_sensitive_metadata_text, is_safe_metadata_text
from .store import ArtifactRecord, BrokerJobRecord, BrokerStore, IdempotencyConflict
from .telemetry import (
    BrokerTelemetry,
    BrokerTelemetryEvent,
    FailureClass,
    Phase,
    PhaseToken,
)
from .validation import (
    ScientificOutputError,
    StagedInput,
    ValidatedScientificOutput,
    stage_input,
    validate_scientific_output,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TRACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UUID_HEX_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_TOOL_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+_()-]{0,127}$")
_MANIFEST_MAX_BYTES = 1024 * 1024
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_REMOTE_AUTO_EXPIRY_WARNING = (
    "sandbox_auto_expires_within_configured_remote_lifetime"
)
_TOOL_FAILED_WARNING = "sandbox_tool_failed"
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_UPLOAD_SUFFIXES = {
    "receptor": frozenset({".pdb"}),
    "ligand": frozenset({".sdf", ".mol"}),
}
_RETRYABLE_DESTROY_FAILURES = frozenset(
    {
        FailureClass.CONNECTION_FAILED,
        FailureClass.SERVER_500,
        FailureClass.PROXY_502,
        FailureClass.RESOURCE_LIMIT,
        FailureClass.DESTROY_FAILED,
        FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
    }
)
_BREAKER_QUALIFYING_PROVISIONING_FAILURES = frozenset(
    {
        FailureClass.CONNECTION_FAILED,
        FailureClass.SERVER_500,
        FailureClass.PROXY_502,
        FailureClass.READINESS_TIMEOUT,
        FailureClass.CREATE_TIMEOUT,
        FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
    }
)
_FAILURE_RESULT_FIELDS = frozenset(
    {"schema_version", "status", "phase", "error_code", "warnings"}
)
_FAILURE_RESULT_CODES = {
    ("cleanup", "cleanup_failed"): BrokerErrorCode.CLEANUP_FAILED,
    ("environment_setup", "environment_unavailable"): (
        BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
    ),
    ("input_validation", "invalid_input"): BrokerErrorCode.INVALID_INPUT,
    ("internal", "internal_error"): BrokerErrorCode.COMMAND_FAILED,
    ("output_validation", "invalid_output"): (
        BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID
    ),
    ("receptor_preparation", "tool_failed"): BrokerErrorCode.COMMAND_FAILED,
    ("receptor_preparation", "tool_timeout"): BrokerErrorCode.EXECUTION_TIMEOUT,
    ("ligand_geometry_preparation", "tool_failed"): BrokerErrorCode.COMMAND_FAILED,
    ("ligand_geometry_preparation", "tool_timeout"): BrokerErrorCode.EXECUTION_TIMEOUT,
    ("ligand_preparation", "tool_failed"): BrokerErrorCode.COMMAND_FAILED,
    ("ligand_preparation", "tool_timeout"): BrokerErrorCode.EXECUTION_TIMEOUT,
    ("docking", "tool_failed"): BrokerErrorCode.COMMAND_FAILED,
    ("docking", "tool_timeout"): BrokerErrorCode.EXECUTION_TIMEOUT,
}


class BrokerFailure(Exception):
    """A stable, sanitized Broker failure safe for API mapping."""

    def __init__(self, code: BrokerErrorCode) -> None:
        if type(code) is not BrokerErrorCode:
            raise TypeError("code must be BrokerErrorCode")
        self.code = code
        super().__init__(code.value)


class StopIncomplete(RuntimeError):
    """A sanitized signal that shutdown is still completing in the background."""

    def __init__(self) -> None:
        super().__init__("sandbox broker shutdown is incomplete")


class _CreateHardDeadline(Exception):
    def __init__(
        self,
        cleanup_confirmed: bool,
        failure_class: FailureClass = FailureClass.CREATE_TIMEOUT,
    ) -> None:
        self.cleanup_confirmed = cleanup_confirmed
        self.failure_class = failure_class
        super().__init__("sandbox create hard deadline exceeded")


class _UploadCopyCancelled(Exception):
    pass


def _verified_failure_details(
    value: object,
) -> tuple[BrokerErrorCode, tuple[str, ...]]:
    if type(value) is not str or not value or len(value) > _MANIFEST_MAX_BYTES:
        return BrokerErrorCode.COMMAND_FAILED, ()

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, item in pairs:
            if key in parsed:
                raise ValueError("duplicate JSON field")
            parsed[key] = item
        return parsed

    try:
        if len(value.encode("utf-8")) > _MANIFEST_MAX_BYTES:
            return BrokerErrorCode.COMMAND_FAILED, ()
        payload = json.loads(value, object_pairs_hook=reject_duplicate_keys)
    except Exception:
        return BrokerErrorCode.COMMAND_FAILED, ()
    if (
        type(payload) is not dict
        or set(payload) != _FAILURE_RESULT_FIELDS
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or payload.get("status") != "failed"
        or type(payload.get("phase")) is not str
        or type(payload.get("error_code")) is not str
        or type(payload.get("warnings")) is not list
        or payload["warnings"] != []
    ):
        return BrokerErrorCode.COMMAND_FAILED, ()
    pair = (payload["phase"], payload["error_code"])
    code = _FAILURE_RESULT_CODES.get(pair, BrokerErrorCode.COMMAND_FAILED)
    warnings = (
        (_TOOL_FAILED_WARNING,)
        if pair[1] == "tool_failed" and pair in _FAILURE_RESULT_CODES
        else ()
    )
    return code, warnings


def _verified_failure_result(value: object) -> BrokerErrorCode:
    return _verified_failure_details(value)[0]


def _validated_idempotency_key(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or not value.strip()
        or value != value.strip()
        or "," in value
        or any(not 0x20 <= ord(character) <= 0x7E for character in value)
    ):
        raise BrokerFailure(BrokerErrorCode.INVALID_INPUT)
    return value


def _upload_suffix(upload: object, role: str) -> str:
    try:
        filename = upload.filename
        source = upload.file
        if (
            type(filename) is not str
            or not 1 <= len(filename) <= 255
            or filename in {".", ".."}
            or filename.count(".") != 1
            or filename.startswith(".")
            or any(
                character in {"/", "\\", ":"}
                or ord(character) < 32
                or ord(character) == 127
                for character in filename
            )
            or not callable(getattr(source, "read", None))
        ):
            raise ValueError
        suffix = f".{filename.rsplit('.', 1)[1].lower()}"
        if suffix not in _UPLOAD_SUFFIXES[role]:
            raise ValueError
        return suffix
    except Exception:
        raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None


def _copy_upload_file(
    source: BinaryIO,
    destination: BinaryIO,
    maximum_bytes: int,
    cancelled: threading.Event,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        if cancelled.is_set():
            raise _UploadCopyCancelled
        chunk = source.read(_UPLOAD_CHUNK_BYTES)
        if type(chunk) is not bytes or len(chunk) > _UPLOAD_CHUNK_BYTES:
            raise ValueError
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_bytes:
            raise ValueError
        view = memoryview(chunk)
        while view:
            written = destination.write(view)
            if type(written) is not int or written <= 0:
                raise OSError
            view = view[written:]
        digest.update(chunk)
    if total == 0:
        raise ValueError
    destination.flush()
    os.fsync(destination.fileno())
    destination.seek(0)
    return total, digest.hexdigest()


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
    if pure.suffix.lower() not in _UPLOAD_SUFFIXES[role]:
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


@dataclass(frozen=True)
class _DownloadedScientificOutput:
    result_text: object
    pose_paths: object
    pose_text: object


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
        *,
        telemetry: BrokerTelemetry | None = None,
        circuit_breaker: ControlPlaneCircuitBreaker | None = None,
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
        self._shutdown_task: asyncio.Task[None] | None = None
        self._job_tasks: dict[str, asyncio.Task[None]] = {}
        self._create_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cleanup_tasks: dict[str, asyncio.Task[Any]] = {}
        self._isolated_tasks: set[asyncio.Task[Any]] = set()
        self._prepared: dict[str, PreparedDockingSubmission] = {}
        self._rejected_work: set[str] = set()
        self._pending_rejections: dict[str, asyncio.Task[bool]] = {}
        self._pending_rejection_capacity = config.queue_capacity
        self._pending_rejection_reservations = 0
        self._rejection_ownership: set[str] = set()
        self._terminal_condition = asyncio.Condition()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self._recovery_generation = 0
        self._retention_lock = asyncio.Lock()
        self._cleanup_timeout_margin_seconds = 30.0
        self._create_hard_timeout_seconds = float(config.sandbox_timeout_seconds)
        self._create_retry_delay_seconds = 0.5
        self._run_cancel_grace_seconds = 5.0
        self._failure_result_read_timeout_seconds = 20.0
        self._output_validation_timeout_seconds = 60.0
        self._destroy_hard_timeout_seconds = 30.0
        self._destroy_retry_delay_seconds = 0.1
        self._rejection_retry_delay_seconds = 0.05
        if telemetry is not None and not isinstance(telemetry, BrokerTelemetry):
            raise ValueError("invalid sandbox broker telemetry")
        if circuit_breaker is not None and not isinstance(
            circuit_breaker, ControlPlaneCircuitBreaker
        ):
            raise ValueError("invalid sandbox broker circuit breaker")
        self.telemetry = telemetry if telemetry is not None else BrokerTelemetry()
        self.circuit_breaker = (
            circuit_breaker
            if circuit_breaker is not None
            else ControlPlaneCircuitBreaker()
        )
        self._terminal_observed: set[str] = set()

    def _observe(self, observation: object, default: object = None) -> object:
        try:
            if not callable(observation):
                raise TypeError
            return observation()
        except BaseException:
            try:
                logging.getLogger(__name__).warning(
                    "sandbox broker telemetry observation failed"
                )
            except BaseException:
                pass
            return default

    def _breaker_observe(self, observation: object, default: object = None) -> object:
        try:
            if not callable(observation):
                raise TypeError
            return observation()
        except BaseException:
            try:
                logging.getLogger(__name__).warning(
                    "sandbox broker circuit breaker observation failed"
                )
            except BaseException:
                pass
            return default

    def _complete_breaker_permit(
        self,
        permit: BreakerPermit,
        action: str,
        failure_class: FailureClass | None = None,
    ) -> bool:
        if type(permit) is not BreakerPermit:
            return False

        def complete(bound: bool) -> None:
            breaker = self.circuit_breaker
            if action == "success":
                callback = (
                    ControlPlaneCircuitBreaker.record_success
                    if bound
                    else breaker.record_success
                )
                callback(breaker, permit) if bound else callback(permit)
            elif action == "failure" and type(failure_class) is FailureClass:
                callback = (
                    ControlPlaneCircuitBreaker.record_failure
                    if bound
                    else breaker.record_failure
                )
                if bound:
                    callback(breaker, permit, failure_class)
                else:
                    callback(permit, failure_class)
            elif action == "release":
                callback = (
                    ControlPlaneCircuitBreaker.release
                    if bound
                    else breaker.release
                )
                callback(breaker, permit) if bound else callback(permit)
            else:
                raise ValueError("invalid circuit-breaker completion")

        self._breaker_observe(lambda: complete(False))
        if permit._completed:
            return True
        self._breaker_observe(lambda: complete(True))
        return permit._completed

    def _active_job_count(self) -> int:
        return min(1, sum(not task.done() for task in self._job_tasks.values()))

    def _breaker_state(self) -> str:
        state = self._breaker_observe(
            lambda: self.circuit_breaker.state.value,
            "open",
        )
        return state if type(state) is str else "open"

    def _refresh_runtime_metrics(self) -> None:
        self._observe(
            lambda: self.telemetry.update_runtime_gauges(
                queue_depth=self._queue.qsize(),
                active_job_count=self._active_job_count(),
                cleanup_task_count=len(self._cleanup_tasks),
                isolated_task_count=len(self._isolated_tasks),
                breaker_state=self._breaker_state(),
            )
        )

    def _emit_job_event(self, record: BrokerJobRecord, phase: Phase) -> None:
        self._observe(
            lambda: self.telemetry.emit(
                BrokerTelemetryEvent(
                    trace_id=record.trace_id,
                    job_id=record.job_id,
                    phase=phase,
                    queue_depth=self._queue.qsize(),
                    active_job_count=self._active_job_count(),
                    cleanup_status="not_started",
                    image_digest=self.config.image_digest,
                )
            )
        )

    def _start_phase(
        self,
        job_id: str,
        phase: str,
        attempt: int = 1,
    ) -> PhaseToken | None:
        try:
            record = self.store.get(job_id)
        except BaseException:
            record = None
        if record is None:
            return None
        token = self._observe(
            lambda: self.telemetry.start_phase(
                trace_id=record.trace_id,
                job_id=job_id,
                phase=phase,
                attempt=attempt,
                queue_depth=self._queue.qsize(),
                active_job_count=self._active_job_count(),
                image_digest=self.config.image_digest,
            )
        )
        return token if type(token) is PhaseToken else None

    def _finish_phase(
        self,
        token: PhaseToken | None,
        *,
        outcome: str,
        failure_class: FailureClass | None = None,
        cleanup_status: str | None = None,
        vina_version: str | None = None,
        meeko_version: str | None = None,
    ) -> None:
        if token is None:
            return
        self._observe(
            lambda: self.telemetry.finish_phase(
                token,
                outcome=outcome,
                failure_class=failure_class,
                cleanup_status=cleanup_status,
                vina_version=vina_version,
                meeko_version=meeko_version,
            )
        )

    @staticmethod
    def _classified_failure(operation: str, failure: BaseException) -> FailureClass:
        classified = getattr(failure, "failure_class", None)
        if type(classified) is FailureClass:
            return classified
        try:
            return classify_control_plane_failure(operation, failure)
        except BaseException:
            return FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE

    def _record_control_plane_failure(
        self,
        operation: str,
        failure_class: FailureClass,
    ) -> None:
        self._observe(
            lambda: self.telemetry.record_control_plane_failure(
                operation=operation,
                failure_class=failure_class,
            )
        )

    def _record_terminal_observation(
        self,
        job_id: str,
        failure_class: FailureClass = FailureClass.NONE,
    ) -> None:
        if job_id in self._terminal_observed:
            self._refresh_runtime_metrics()
            return
        try:
            record = self.store.get(job_id)
        except BaseException:
            record = None
        if record is None or record.status not in TERMINAL_STATUSES:
            self._refresh_runtime_metrics()
            return
        self._terminal_observed.add(job_id)
        status = record.status.value
        outcome = (
            "passed"
            if record.status is BrokerJobStatus.SUCCEEDED
            else "cancelled"
            if record.status is BrokerJobStatus.CANCELLED
            else "failed"
        )
        event_failure = failure_class if outcome == "failed" else None
        self._observe(
            lambda: self.telemetry.emit(
                BrokerTelemetryEvent(
                    trace_id=record.trace_id,
                    job_id=record.job_id,
                    phase=Phase.JOB_TERMINAL,
                    outcome=outcome,
                    failure_class=event_failure,
                    queue_depth=self._queue.qsize(),
                    active_job_count=self._active_job_count(),
                    cleanup_status=record.cleanup_status,
                    image_digest=self.config.image_digest,
                )
            )
        )
        self._observe(lambda: self.telemetry.record_terminal(status, failure_class))
        self._refresh_runtime_metrics()

    def diagnostics(self) -> dict[str, object]:
        self._refresh_runtime_metrics()
        telemetry = self._observe(
            self.telemetry.snapshot,
            {"schema_version": 1, "unavailable": True},
        )
        breaker = self._breaker_observe(
            self.circuit_breaker.snapshot,
            {"state": "open", "unavailable": True},
        )
        return {
            "schema_version": 1,
            "telemetry": telemetry,
            "circuit_breaker": breaker,
        }

    def prometheus_text(self) -> bytes:
        self._refresh_runtime_metrics()
        encoded = self._observe(self.telemetry.prometheus_text, b"")
        return encoded if type(encoded) is bytes else b""

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[Any]) -> None:
        try:
            task.exception()
        except BaseException:
            pass

    async def _notify_terminal(self) -> None:
        async with self._terminal_condition:
            self._terminal_condition.notify_all()

    async def start(self) -> None:
        """Start exactly one consumer; repeated calls are harmless."""

        async with self._lifecycle_lock:
            if self._shutdown_task is not None and not self._shutdown_task.done():
                raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
            if self._started and self._consumer_task is not None and not self._consumer_task.done():
                return
            self._stopping = False
            self._started = True
            self._consumer_task = asyncio.create_task(
                self._consume(), name="sandbox-broker-consumer"
            )
            self._refresh_runtime_metrics()

    async def stop(self, *, timeout: float | None = None) -> None:
        """Request cancellation and await one shared, non-cancellable shutdown."""

        if timeout is None:
            wait_seconds = (
                float(self.config.sandbox_timeout_seconds)
                + self._cleanup_timeout_margin_seconds
            )
        elif (
            type(timeout) not in (int, float)
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            raise ValueError("shutdown timeout is invalid")
        else:
            wait_seconds = float(timeout)

        async with self._lifecycle_lock:
            shutdown = self._shutdown_task
            if shutdown is not None and shutdown.done():
                try:
                    shutdown.result()
                except BaseException:
                    pass
                self._shutdown_task = None
                self._stopping = False
                if self._consumer_task is not None and self._consumer_task.done():
                    self._consumer_task = None
                shutdown = None
            if shutdown is None:
                if (
                    not self._started
                    and self._consumer_task is None
                    and not self._job_tasks
                    and not self._create_tasks
                    and not self._cleanup_tasks
                    and not self._isolated_tasks
                ):
                    drain_cleanup = getattr(self.client, "drain_cleanup", None)
                    if not callable(drain_cleanup):
                        return
                self._started = False
                self._stopping = True
                shutdown = asyncio.create_task(
                    self._shutdown(),
                    name="sandbox-broker-shutdown",
                )
                shutdown.add_done_callback(self._consume_task_exception)
                self._shutdown_task = shutdown

        incomplete = False
        try:
            await asyncio.wait_for(asyncio.shield(shutdown), timeout=wait_seconds)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            incomplete = True
        except BaseException:
            incomplete = True
        finally:
            if shutdown.done():
                async with self._lifecycle_lock:
                    if self._shutdown_task is shutdown:
                        self._shutdown_task = None
                    self._stopping = False
        if incomplete:
            raise StopIncomplete() from None

    async def _shutdown(self) -> None:
        for record in self.store.active_jobs():
            try:
                current = self.store.request_cancel(record.job_id)
                if current.status not in TERMINAL_STATUSES:
                    self._cancel_events.setdefault(
                        current.job_id, asyncio.Event()
                    ).set()
            except BaseException:
                continue

        consumer = self._consumer_task
        join_task = asyncio.create_task(
            self._queue.join(), name="sandbox-broker-queue-drain"
        )
        join_task.add_done_callback(self._consume_task_exception)
        while not join_task.done():
            watched: set[asyncio.Task[Any]] = {join_task}
            if consumer is not None:
                watched.add(consumer)
            done, _ = await asyncio.wait(
                watched,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if join_task in done:
                break
            if consumer is not None and consumer in done:
                self._consume_task_exception(consumer)
                await self._drain_unserviceable_queue()
                break
        await asyncio.shield(join_task)
        if consumer is not None and not consumer.done():
            consumer.cancel()
        if consumer is not None:
            try:
                await asyncio.shield(consumer)
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass

        tracked = {
            *self._job_tasks.values(),
            *self._create_tasks.values(),
            *self._cleanup_tasks.values(),
        }
        if tracked:
            wait_limit = (
                self._create_hard_timeout_seconds
                + self._destroy_hard_timeout_seconds
                + self._run_cancel_grace_seconds
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *(asyncio.shield(task) for task in tracked),
                        return_exceptions=True,
                    ),
                    timeout=wait_limit,
                )
            except asyncio.TimeoutError:
                for task in tracked:
                    self._consume_task_exception(task) if task.done() else None
                raise RuntimeError("broker cleanup did not finish") from None
        isolated = tuple(self._isolated_tasks)
        if isolated:
            await asyncio.gather(
                *(self._cancel_with_grace(task) for task in isolated),
                return_exceptions=True,
            )
        await self._drain_pending_rejections()
        if self._pending_rejections:
            raise RuntimeError("broker rejection persistence did not finish")
        drain_cleanup = getattr(self.client, "drain_cleanup", None)
        if callable(drain_cleanup):
            try:
                await drain_cleanup()
            except BaseException:
                raise RuntimeError("broker cleanup drain failed") from None
        self._consumer_task = None

    async def _drain_unserviceable_queue(self) -> None:
        while True:
            try:
                job_id = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                self._fail_queue_item(job_id)
                if job_id in self._rejected_work:
                    self._remove_job_tree(job_id)
                await self._notify_terminal()
            finally:
                self._prepared.pop(job_id, None)
                self._rejected_work.discard(job_id)
                self._cancel_events.pop(job_id, None)
                self._queue.task_done()
                self._refresh_runtime_metrics()

    def _fail_queue_item(self, job_id: str) -> None:
        try:
            record = self.store.get(job_id)
            if record is not None and record.status not in TERMINAL_STATUSES:
                self.store.fail(
                    job_id,
                    BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                    cleanup_status="succeeded",
                )
                self._record_terminal_observation(
                    job_id,
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
        except BaseException:
            try:
                self.store.fail(
                    job_id,
                    BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                    cleanup_status="succeeded",
                )
                self._record_terminal_observation(
                    job_id,
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
            except BaseException:
                pass

    def _accepts_new_work(self) -> bool:
        accepted = self._breaker_observe(
            self.circuit_breaker.accepts_new_work,
            False,
        )
        return accepted is True

    def _discard_new_submission(self, record: BrokerJobRecord) -> bool:
        try:
            discarded = self.store.discard_pristine_queued(record)
        except BaseException:
            return False
        if not discarded:
            return False
        self._prepared.pop(record.job_id, None)
        self._cancel_events.pop(record.job_id, None)
        self._refresh_runtime_metrics()
        return True

    def _try_reserve_pending_rejection(self) -> bool:
        if (
            self._pending_rejection_reservations
            + len(self._rejection_ownership)
            >= self._pending_rejection_capacity
        ):
            return False
        self._pending_rejection_reservations += 1
        return True

    def _release_pending_rejection_reservation(self) -> None:
        if self._pending_rejection_reservations <= 0:
            raise RuntimeError("missing pending rejection reservation")
        self._pending_rejection_reservations -= 1

    def _assign_rejection_ownership(self, job_id: str) -> None:
        if job_id in self._rejection_ownership:
            raise RuntimeError("duplicate pending rejection ownership")
        self._release_pending_rejection_reservation()
        self._rejection_ownership.add(job_id)

    def _release_rejection_ownership(self, job_id: str) -> None:
        self._rejection_ownership.discard(job_id)

    async def submit(
        self,
        prepared: PreparedDockingSubmission,
        idempotency_key: str,
    ) -> BrokerJobView:
        if type(prepared) is not PreparedDockingSubmission:
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT)
        try:
            expected_hash = canonical_submission_sha256(
                prepared.parameters,
                _safe_staged_input(prepared.receptor, "receptor"),
                _safe_staged_input(prepared.ligand, "ligand"),
            )
            if (
                prepared.canonical_input_sha256 != expected_hash
                or type(prepared.trace_id) is not str
                or _TRACE_PATTERN.fullmatch(prepared.trace_id) is None
            ):
                raise ValueError
        except Exception:
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
        checked_key = _validated_idempotency_key(idempotency_key)
        if (
            not self._started
            or self._stopping
            or self._consumer_task is None
            or self._consumer_task.done()
        ):
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
        try:
            existing = self.store.get_by_idempotency(checked_key)
        except Exception:
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
        if existing is not None:
            if existing.canonical_input_sha256 != prepared.canonical_input_sha256:
                raise BrokerFailure(BrokerErrorCode.IDEMPOTENCY_CONFLICT)
            return _record_view(existing)
        if not self._try_reserve_pending_rejection():
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
        reservation_held = True
        try:
            record, reused = self.store.create_or_get(
                checked_key,
                prepared.canonical_input_sha256,
                prepared.trace_id,
            )
        except Exception as failure:
            self._release_pending_rejection_reservation()
            if isinstance(failure, IdempotencyConflict):
                raise BrokerFailure(BrokerErrorCode.IDEMPOTENCY_CONFLICT) from None
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
        if reused:
            self._release_pending_rejection_reservation()
            return _record_view(record)
        if not self._accepts_new_work():
            if self._discard_new_submission(record):
                self._release_pending_rejection_reservation()
                self._remove_job_tree(record.job_id)
                raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
            self._assign_rejection_ownership(record.job_id)
            reservation_held = False
            return await self._enqueue_rejected(record)

        if reservation_held:
            self._release_pending_rejection_reservation()
        return await self._enqueue_created(record, prepared)

    async def _copy_upload_to_temporary(
        self,
        upload: object,
        maximum_bytes: int,
    ) -> tuple[BinaryIO, int, str]:
        temporary: BinaryIO | None = None
        copy_task: asyncio.Task[tuple[int, str]] | None = None
        cancelled = threading.Event()
        try:
            source = upload.file
            temporary = tempfile.TemporaryFile(
                mode="w+b",
                dir=self.config.state_root,
            )
            copy_task = asyncio.create_task(
                asyncio.to_thread(
                    _copy_upload_file,
                    source,
                    temporary,
                    maximum_bytes,
                    cancelled,
                ),
                name="sandbox-broker-upload-copy",
            )
            try:
                size_bytes, sha256 = await asyncio.shield(copy_task)
            except asyncio.CancelledError:
                cancelled.set()
                while not copy_task.done():
                    try:
                        await asyncio.shield(copy_task)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                raise
            return temporary, size_bytes, sha256
        except asyncio.CancelledError:
            if temporary is not None:
                temporary.close()
            raise
        except BaseException as failure:
            if temporary is not None:
                temporary.close()
            if not isinstance(failure, Exception):
                raise
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None

    async def submit_uploads(
        self,
        parameters: DockingParameters,
        receptor_upload: object,
        ligand_upload: object,
        idempotency_key: str,
    ) -> BrokerJobView:
        """Bound, hash, stage, and enqueue one multipart docking request."""

        if type(parameters) is not DockingParameters:
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT)
        checked_key = _validated_idempotency_key(idempotency_key)
        receptor_suffix = _upload_suffix(receptor_upload, "receptor")
        ligand_suffix = _upload_suffix(ligand_upload, "ligand")
        if (
            not self._started
            or self._stopping
            or self._consumer_task is None
            or self._consumer_task.done()
        ):
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)

        receptor_temporary: BinaryIO | None = None
        ligand_temporary: BinaryIO | None = None
        record: BrokerJobRecord | None = None
        created = False
        reservation_held = False
        try:
            receptor_temporary, receptor_size, receptor_sha256 = (
                await self._copy_upload_to_temporary(
                    receptor_upload,
                    self.config.receptor_max_bytes,
                )
            )
            ligand_temporary, ligand_size, ligand_sha256 = (
                await self._copy_upload_to_temporary(
                    ligand_upload,
                    self.config.ligand_max_bytes,
                )
            )
            receptor_placeholder = StagedInput(
                relative_path=f"input/receptor{receptor_suffix}",
                size_bytes=receptor_size,
                sha256=receptor_sha256,
            )
            ligand_placeholder = StagedInput(
                relative_path=f"input/ligand{ligand_suffix}",
                size_bytes=ligand_size,
                sha256=ligand_sha256,
            )
            canonical_hash = canonical_submission_sha256(
                parameters,
                receptor_placeholder,
                ligand_placeholder,
            )
            try:
                existing = self.store.get_by_idempotency(checked_key)
            except Exception:
                raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
            if existing is not None:
                if existing.canonical_input_sha256 != canonical_hash:
                    raise BrokerFailure(BrokerErrorCode.IDEMPOTENCY_CONFLICT)
                return _record_view(existing)
            if not self._try_reserve_pending_rejection():
                raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
            reservation_held = True
            try:
                record, reused = self.store.create_or_get(
                    checked_key,
                    canonical_hash,
                    uuid.uuid4().hex,
                )
            except IdempotencyConflict:
                raise BrokerFailure(BrokerErrorCode.IDEMPOTENCY_CONFLICT) from None
            except Exception:
                raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
            if reused:
                return _record_view(record)
            created = True
            if not self._accepts_new_work():
                if self._discard_new_submission(record):
                    raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
                self._assign_rejection_ownership(record.job_id)
                reservation_held = False
                return await self._enqueue_rejected(record)

            self._release_pending_rejection_reservation()
            reservation_held = False
            receptor_temporary.seek(0)
            receptor = stage_input(
                self.config.state_root,
                record.job_id,
                "receptor",
                receptor_suffix,
                receptor_temporary,
                self.config.receptor_max_bytes,
            )
            ligand_temporary.seek(0)
            ligand = stage_input(
                self.config.state_root,
                record.job_id,
                "ligand",
                ligand_suffix,
                ligand_temporary,
                self.config.ligand_max_bytes,
            )
            prepared = PreparedDockingSubmission(
                parameters=parameters,
                receptor=receptor,
                ligand=ligand,
                canonical_input_sha256=canonical_hash,
                trace_id=record.trace_id,
            )
            return await self._enqueue_created(record, prepared)
        except asyncio.CancelledError:
            if (
                created
                and record is not None
                and record.job_id not in self._rejection_ownership
            ):
                self._fail_unqueued_submission(record.job_id)
            raise
        except BrokerFailure:
            if created and record is not None:
                current = self.store.get(record.job_id)
                if current is not None and current.status is BrokerJobStatus.QUEUED:
                    self._fail_unqueued_submission(record.job_id)
                self._remove_job_tree(record.job_id)
            raise
        except Exception:
            if created and record is not None:
                self._fail_unqueued_submission(record.job_id)
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
        finally:
            if reservation_held:
                self._release_pending_rejection_reservation()
            if receptor_temporary is not None:
                receptor_temporary.close()
            if ligand_temporary is not None:
                ligand_temporary.close()

    def _fail_unqueued_submission(self, job_id: str) -> None:
        self._prepared.pop(job_id, None)
        self._cancel_events.pop(job_id, None)
        try:
            current = self.store.get(job_id)
            if current is not None and current.status not in TERMINAL_STATUSES:
                self.store.fail(
                    job_id,
                    BrokerErrorCode.INVALID_INPUT,
                    cleanup_status="succeeded",
                )
        except BaseException:
            pass
        self._remove_job_tree(job_id)
        self._refresh_runtime_metrics()

    async def _enqueue_created(
        self,
        record: BrokerJobRecord,
        prepared: PreparedDockingSubmission,
    ) -> BrokerJobView:
        if (
            type(record) is not BrokerJobRecord
            or type(prepared) is not PreparedDockingSubmission
            or record.status is not BrokerJobStatus.QUEUED
            or record.canonical_input_sha256 != prepared.canonical_input_sha256
            or record.trace_id != prepared.trace_id
        ):
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT)
        if (
            not self._started
            or self._stopping
            or self._consumer_task is None
            or self._consumer_task.done()
        ):
            self.store.fail(
                record.job_id,
                BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                cleanup_status="succeeded",
            )
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)

        self._emit_job_event(record, Phase.JOB_RECEIVED)
        self._prepared[record.job_id] = prepared
        self._cancel_events.setdefault(record.job_id, asyncio.Event())
        try:
            self._queue.put_nowait(record.job_id)
        except asyncio.QueueFull:
            self._prepared.pop(record.job_id, None)
            self._cancel_events.pop(record.job_id, None)
            terminal = self.store.fail(
                record.job_id,
                BrokerErrorCode.QUEUE_SATURATED,
                cleanup_status="succeeded",
            )
            await self._notify_terminal()
            self._record_terminal_observation(record.job_id)
            del terminal
            raise BrokerFailure(BrokerErrorCode.QUEUE_SATURATED) from None
        self._refresh_runtime_metrics()
        self._emit_job_event(record, Phase.QUEUE_ENTERED)
        return _record_view(record)

    async def _enqueue_rejected(self, record: BrokerJobRecord) -> BrokerJobView:
        if (
            type(record) is not BrokerJobRecord
            or record.status is not BrokerJobStatus.QUEUED
        ):
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT)
        if (
            not self._started
            or self._stopping
            or self._consumer_task is None
            or self._consumer_task.done()
        ):
            self.store.fail(
                record.job_id,
                BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                cleanup_status="succeeded",
            )
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)

        self._emit_job_event(record, Phase.JOB_RECEIVED)
        self._rejected_work.add(record.job_id)
        self._cancel_events.setdefault(record.job_id, asyncio.Event())
        try:
            self._queue.put_nowait(record.job_id)
        except asyncio.QueueFull:
            first_attempt = self._schedule_pending_rejection(
                record.job_id,
                attempts=1,
            )
            persisted = await asyncio.shield(first_attempt)
            if persisted:
                raise BrokerFailure(BrokerErrorCode.QUEUE_SATURATED) from None
            self._schedule_pending_rejection(record.job_id, attempts=3)
            return _record_view(record)
        self._release_rejection_ownership(record.job_id)
        self._refresh_runtime_metrics()
        self._emit_job_event(record, Phase.QUEUE_ENTERED)
        return _record_view(record)

    def _finish_pending_rejection_ownership(self, job_id: str) -> None:
        self._pending_rejections.pop(job_id, None)
        self._release_rejection_ownership(job_id)
        self._rejected_work.discard(job_id)
        self._cancel_events.pop(job_id, None)
        self._remove_job_tree(job_id)
        self._refresh_runtime_metrics()

    async def _persist_pending_rejection(
        self,
        job_id: str,
        *,
        attempts: int,
    ) -> bool:
        for attempt in range(attempts):
            try:
                current = self.store.get(job_id)
                if current is None or current.status in TERMINAL_STATUSES:
                    self._finish_pending_rejection_ownership(job_id)
                    return True
                self.store.fail(
                    job_id,
                    BrokerErrorCode.QUEUE_SATURATED,
                    cleanup_status="succeeded",
                )
            except BaseException:
                if attempt + 1 < attempts:
                    await asyncio.sleep(self._rejection_retry_delay_seconds)
                continue
            self._record_terminal_observation(job_id)
            self._finish_pending_rejection_ownership(job_id)
            await self._notify_terminal()
            return True
        return False

    def _schedule_pending_rejection(
        self,
        job_id: str,
        *,
        attempts: int = 3,
    ) -> asyncio.Task[bool]:
        if job_id not in self._rejection_ownership:
            raise RuntimeError("pending rejection has no ownership slot")
        existing = self._pending_rejections.get(job_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(
            self._persist_pending_rejection(job_id, attempts=attempts),
            name=f"sandbox-broker-rejection-{job_id}",
        )
        self._pending_rejections[job_id] = task
        task.add_done_callback(self._consume_task_exception)
        self._refresh_runtime_metrics()
        return task

    async def _drain_pending_rejections(self) -> None:
        tasks = [
            self._schedule_pending_rejection(job_id)
            for job_id in tuple(self._pending_rejections)
        ]
        if tasks:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tasks),
                return_exceptions=True,
            )

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
        if timeout is not None and (
            type(timeout) not in (int, float)
            or not math.isfinite(float(timeout))
            or timeout < 0
        ):
            raise ValueError("terminal wait timeout is invalid")
        deadline = (
            None
            if timeout is None
            else asyncio.get_running_loop().time() + float(timeout)
        )
        while True:
            record = self.get_job(job_id)
            if record.status in TERMINAL_STATUSES:
                return record
            async with self._terminal_condition:
                record = self.get_job(job_id)
                if record.status in TERMINAL_STATUSES:
                    return record
                if deadline is None:
                    await self._terminal_condition.wait()
                else:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    await asyncio.wait_for(
                        self._terminal_condition.wait(), timeout=remaining
                    )

    async def _consume(self) -> None:
        while True:
            job_id = await self._queue.get()
            self._refresh_runtime_metrics()
            current: asyncio.Task[None] | None = None
            try:
                record = self.store.get(job_id)
                if record is None or record.status in TERMINAL_STATUSES:
                    continue
                if job_id in self._rejected_work:
                    self._fail_queue_item(job_id)
                    self._remove_job_tree(job_id)
                    continue
                current = asyncio.create_task(
                    self._run_job(job_id),
                    name=f"sandbox-broker-job-{job_id}",
                )
                self._job_tasks[job_id] = current
                self._refresh_runtime_metrics()
                try:
                    await asyncio.shield(current)
                except asyncio.CancelledError:
                    if current.cancelled():
                        continue
                    while not current.done():
                        try:
                            await asyncio.shield(current)
                        except asyncio.CancelledError:
                            continue
                        except BaseException:
                            break
                    raise
                except BaseException:
                    pass
            except asyncio.CancelledError:
                raise
            except BaseException:
                self._fail_queue_item(job_id)
            finally:
                self._job_tasks.pop(job_id, None)
                self._refresh_runtime_metrics()
                self._prepared.pop(job_id, None)
                self._rejected_work.discard(job_id)
                self._cancel_events.pop(job_id, None)
                try:
                    await self._notify_terminal()
                finally:
                    self._queue.task_done()
                    self._refresh_runtime_metrics()

    async def _run_job(self, job_id: str) -> None:
        handle: SandboxHandle | None = None
        completion: _PreparedCompletion | None = None
        failure: BrokerErrorCode | None = None
        terminal_failure_class = FailureClass.NONE
        cancelled = False
        cleanup = "succeeded"
        remote_identity_unknown = False
        failure_warnings: tuple[str, ...] = ()
        permit: BreakerPermit | None = None
        try:
            if self._cancellation_requested(job_id):
                cancelled = True
                return
            self.store.transition(job_id, BrokerJobStatus.PROVISIONING)
            if self._cancellation_requested(job_id):
                cancelled = True
                return
            acquired = self._breaker_observe(self.circuit_breaker.acquire)
            permit = acquired if type(acquired) is BreakerPermit else None
            if permit is None:
                provisioning = self._start_phase(job_id, "provisioning")
                self._finish_phase(
                    provisioning,
                    outcome="failed",
                    failure_class=FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
                terminal_failure_class = (
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
                )
                failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
                return
            if self._cancellation_requested(job_id):
                provisioning = self._start_phase(job_id, "provisioning")
                self._finish_phase(provisioning, outcome="cancelled")
                if self._complete_breaker_permit(permit, "release"):
                    permit = None
                cancelled = True
                return
            try:
                created, create_interrupted = await self._create_with_retry(
                    job_id
                )
                if type(created) is not SandboxHandle:
                    raise ValueError
                handle = created
            except _CreateHardDeadline as deadline:
                remote_identity_unknown = not deadline.cleanup_confirmed
                if remote_identity_unknown:
                    failure_warnings = (_REMOTE_AUTO_EXPIRY_WARNING,)
                terminal_failure_class = deadline.failure_class
                if permit is not None:
                    if (
                        deadline.failure_class
                        in _BREAKER_QUALIFYING_PROVISIONING_FAILURES
                    ):
                        completed = self._complete_breaker_permit(
                            permit,
                            "failure",
                            deadline.failure_class,
                        )
                    else:
                        completed = self._complete_breaker_permit(
                            permit,
                            "release",
                        )
                    if completed:
                        permit = None
                failure = BrokerErrorCode.PROVISIONING_FAILED
                return
            except asyncio.CancelledError:
                raise
            except BaseException as create_failure:
                if (
                    isinstance(create_failure, SandboxProtocolError)
                    and create_failure.cleanup_confirmed is False
                ):
                    remote_identity_unknown = True
                    failure_warnings = (_REMOTE_AUTO_EXPIRY_WARNING,)
                classified = self._classified_failure("create", create_failure)
                terminal_failure_class = classified
                if permit is not None:
                    if classified in _BREAKER_QUALIFYING_PROVISIONING_FAILURES:
                        completed = self._complete_breaker_permit(
                            permit,
                            "failure",
                            classified,
                        )
                    else:
                        completed = self._complete_breaker_permit(
                            permit,
                            "release",
                        )
                    if completed:
                        permit = None
                failure = BrokerErrorCode.PROVISIONING_FAILED
                return

            if permit is not None and self._complete_breaker_permit(
                permit,
                "success",
            ):
                permit = None
            try:
                self.store.attach_sandbox(job_id, handle.sandbox_id)
            except BaseException:
                failure = BrokerErrorCode.PROVISIONING_FAILED
                return
            if create_interrupted or self._cancellation_requested(job_id):
                cancelled = True
                return

            self.store.transition(job_id, BrokerJobStatus.UPLOADING)
            if self._cancellation_requested(job_id):
                cancelled = True
                return
            upload = self._start_phase(job_id, "upload")
            try:
                await self._upload_fixed_inputs(job_id, handle)
            except asyncio.CancelledError:
                self._finish_phase(upload, outcome="cancelled")
                raise
            except BaseException as upload_failure:
                classified = self._classified_failure("upload", upload_failure)
                self._record_control_plane_failure("upload", classified)
                self._finish_phase(
                    upload,
                    outcome="failed",
                    failure_class=classified,
                )
                terminal_failure_class = classified
                failure = BrokerErrorCode.UPLOAD_FAILED
                return
            self._finish_phase(upload, outcome="passed")
            if self._cancellation_requested(job_id):
                cancelled = True
                return

            self.store.transition(job_id, BrokerJobStatus.RUNNING)
            command_phase = self._start_phase(job_id, "command")
            try:
                command, run_cancelled = await self._run_with_cancellation(
                    job_id,
                    handle,
                )
            except asyncio.TimeoutError:
                self._record_control_plane_failure(
                    "command",
                    FailureClass.COMMAND_TIMEOUT,
                )
                self._finish_phase(
                    command_phase,
                    outcome="failed",
                    failure_class=FailureClass.COMMAND_TIMEOUT,
                )
                terminal_failure_class = FailureClass.COMMAND_TIMEOUT
                failure = BrokerErrorCode.EXECUTION_TIMEOUT
                return
            except asyncio.CancelledError:
                self._finish_phase(command_phase, outcome="cancelled")
                raise
            except BaseException as command_failure:
                classified = self._classified_failure("command", command_failure)
                self._record_control_plane_failure("command", classified)
                self._finish_phase(
                    command_phase,
                    outcome="failed",
                    failure_class=classified,
                )
                terminal_failure_class = classified
                failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
                return
            if run_cancelled:
                self._finish_phase(command_phase, outcome="cancelled")
                cancelled = True
                return
            if type(command) is not SandboxCommandResult:
                self._record_control_plane_failure(
                    "command",
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
                self._finish_phase(
                    command_phase,
                    outcome="failed",
                    failure_class=FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
                terminal_failure_class = (
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
                )
                failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
                return
            if command.exit_code != 0:
                self._finish_phase(command_phase, outcome="failed")
                if self._cancellation_requested(job_id):
                    cancelled = True
                    return
                failure_result, read_cancelled = (
                    await self._read_failure_result_with_cancellation(job_id, handle)
                )
                if read_cancelled:
                    cancelled = True
                    return
                failure, verified_warnings = _verified_failure_details(failure_result)
                failure_warnings += verified_warnings
                return
            self._finish_phase(command_phase, outcome="passed")
            if self._cancellation_requested(job_id):
                cancelled = True
                return

            self.store.transition(job_id, BrokerJobStatus.VALIDATING)
            if self._cancellation_requested(job_id):
                cancelled = True
                return
            validation = self._start_phase(job_id, "validation")
            try:
                completion, validation_cancelled = (
                    await self._download_validation_with_cancellation(job_id, handle)
                )
                if validation_cancelled:
                    self._finish_phase(validation, outcome="cancelled")
                    cancelled = True
                    return
            except ScientificOutputError:
                self._finish_phase(validation, outcome="failed")
                failure = BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID
                return
            except asyncio.TimeoutError:
                self._finish_phase(validation, outcome="failed")
                failure = BrokerErrorCode.ARTIFACT_FAILED
                return
            except asyncio.CancelledError:
                self._finish_phase(validation, outcome="cancelled")
                raise
            except BaseException:
                self._finish_phase(validation, outcome="failed")
                failure = BrokerErrorCode.ARTIFACT_FAILED
                return
            if completion is None:
                self._finish_phase(validation, outcome="failed")
                failure = BrokerErrorCode.ARTIFACT_FAILED
                return
            self._finish_phase(
                validation,
                outcome="passed",
                vina_version=completion.manifest.provenance.vina_version,
                meeko_version=completion.manifest.provenance.meeko_version,
            )
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
            terminal_failure_class = FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
        finally:
            if permit is not None:
                if self._complete_breaker_permit(permit, "release"):
                    permit = None
            try:
                cleanup = await self._destroy_best_effort(
                    job_id,
                    handle,
                    remote_identity_unknown=remote_identity_unknown,
                )
            except BaseException:
                cleanup = "failed"
            try:
                record = self.store.get(job_id)
                if record is None or record.status in TERMINAL_STATUSES:
                    self._record_terminal_observation(
                        job_id,
                        terminal_failure_class,
                    )
                    return
                if cleanup != "succeeded":
                    terminal_failure_class = FailureClass.DESTROY_FAILED
                    failed_provenance = (
                        None
                        if completion is None
                        else completion.manifest.provenance.model_copy(
                            update={"cleanup_status": "failed"}
                        ).model_dump(mode="json")
                    )
                    prior_warnings = failure_warnings + (
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
                    self.store.fail(
                        job_id,
                        failure,
                        warnings=failure_warnings,
                        cleanup_status="succeeded",
                    )
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
                                cleanup_status="succeeded",
                            )
            except BaseException:
                pass
            self._record_terminal_observation(
                job_id,
                terminal_failure_class,
            )

    def _cancellation_requested(self, job_id: str) -> bool:
        event = self._cancel_events.setdefault(job_id, asyncio.Event())
        if event.is_set():
            return True
        record = self.store.get(job_id)
        if record is None:
            return True
        if record.cancel_requested:
            event.set()
            return True
        return record.status in TERMINAL_STATUSES

    async def _run_with_cancellation(
        self,
        job_id: str,
        handle: SandboxHandle,
    ) -> tuple[object | None, bool]:
        run_task = asyncio.create_task(
            self.client.run(handle),
            name=f"sandbox-broker-run-{job_id}",
        )
        cancel_wait = asyncio.create_task(
            self._cancel_events.setdefault(job_id, asyncio.Event()).wait(),
            name=f"sandbox-broker-cancel-wait-{job_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {run_task, cancel_wait},
                timeout=self.config.execution_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await self._cancel_with_grace(run_task)
                raise asyncio.TimeoutError
            if cancel_wait in done and cancel_wait.result():
                await self._cancel_with_grace(run_task)
                return None, True
            return run_task.result(), False
        finally:
            if not cancel_wait.done():
                cancel_wait.cancel()
            try:
                await cancel_wait
            except BaseException:
                pass

    async def _read_failure_result_with_cancellation(
        self,
        job_id: str,
        handle: SandboxHandle,
    ) -> tuple[str | None, bool]:
        read_task = asyncio.create_task(
            self.client.read_text(handle, "/workspace/output/result.json"),
            name=f"sandbox-broker-failure-result-{job_id}",
        )
        cancel_wait = asyncio.create_task(
            self._cancel_events.setdefault(job_id, asyncio.Event()).wait(),
            name=f"sandbox-broker-failure-cancel-{job_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {read_task, cancel_wait},
                timeout=self._failure_result_read_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done and cancel_wait.result():
                await self._cancel_with_grace(read_task)
                return None, True
            if read_task not in done:
                await self._cancel_with_grace(read_task)
                self._record_control_plane_failure(
                    "metadata",
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
                return None, False
            try:
                value = read_task.result()
            except BaseException as read_failure:
                self._record_control_plane_failure(
                    "metadata",
                    self._classified_failure("metadata", read_failure),
                )
                return None, False
            return (value if type(value) is str else None), False
        except asyncio.CancelledError:
            await self._cancel_with_grace(read_task)
            raise
        finally:
            if not cancel_wait.done():
                cancel_wait.cancel()
            try:
                await cancel_wait
            except BaseException:
                pass

    async def _download_validation_with_cancellation(
        self,
        job_id: str,
        handle: SandboxHandle,
    ) -> tuple[_PreparedCompletion | None, bool]:
        validation_task = asyncio.create_task(
            self._download_scientific_output(handle),
            name=f"sandbox-broker-validation-{job_id}",
        )
        cancel_wait = asyncio.create_task(
            self._cancel_events.setdefault(job_id, asyncio.Event()).wait(),
            name=f"sandbox-broker-validation-cancel-{job_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {validation_task, cancel_wait},
                timeout=self._output_validation_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done and cancel_wait.result():
                await self._cancel_with_grace(validation_task)
                return None, True
            if validation_task not in done:
                await self._cancel_with_grace(validation_task)
                self._record_control_plane_failure(
                    "metadata",
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
                raise asyncio.TimeoutError
            try:
                downloaded = validation_task.result()
            except BaseException as metadata_failure:
                self._record_control_plane_failure(
                    "metadata",
                    self._classified_failure("metadata", metadata_failure),
                )
                raise
            if self._cancellation_requested(job_id):
                return None, True
            return self._validate_publish_downloaded(job_id, downloaded), False
        except asyncio.CancelledError:
            await self._cancel_with_grace(validation_task)
            raise
        finally:
            if not cancel_wait.done():
                cancel_wait.cancel()
            try:
                await cancel_wait
            except BaseException:
                pass

    async def _cancel_with_grace(self, task: asyncio.Task[Any]) -> None:
        if task.done():
            self._consume_task_exception(task)
            return
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self._run_cancel_grace_seconds
            )
        except BaseException:
            pass
        if not task.done():
            self._isolated_tasks.add(task)
            self._refresh_runtime_metrics()

            def isolated_finished(completed: asyncio.Task[Any]) -> None:
                self._isolated_tasks.discard(completed)
                self._consume_task_exception(completed)
                self._refresh_runtime_metrics()

            task.add_done_callback(isolated_finished)
        else:
            self._consume_task_exception(task)

    async def _create_without_cancelling(
        self,
        job_id: str,
        deadline: float,
        attempt: int,
        provisioning: PhaseToken | None = None,
    ) -> tuple[object, bool]:
        """Run one create while retaining ownership of any late SDK result."""

        if provisioning is None:
            provisioning = self._start_phase(job_id, "provisioning", attempt)
        create_task = asyncio.create_task(
            self.client.create(job_id),
            name=f"sandbox-broker-create-{job_id}",
        )
        self._create_tasks[job_id] = create_task
        try:
            try:
                while not create_task.done():
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        create_task.cancel()
                        self._retain_late_create(job_id, create_task)
                        await asyncio.sleep(0)
                        confirmed = await self._reconcile_unknown_create(
                            job_id, deadline
                        )
                        raise _CreateHardDeadline(confirmed)
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(create_task), timeout=remaining
                        )
                    except asyncio.CancelledError:
                        create_task.cancel()
                        self._retain_late_create(job_id, create_task)
                        await asyncio.sleep(0)
                        raise
                    except asyncio.TimeoutError:
                        create_task.cancel()
                        self._retain_late_create(job_id, create_task)
                        await asyncio.sleep(0)
                        confirmed = await self._reconcile_unknown_create(
                            job_id, deadline
                        )
                        raise _CreateHardDeadline(confirmed) from None
                    except BaseException:
                        break
                result = create_task.result(), False
            finally:
                if self._create_tasks.get(job_id) is create_task:
                    del self._create_tasks[job_id]
        except asyncio.CancelledError:
            self._finish_phase(provisioning, outcome="cancelled")
            raise
        except BaseException as create_failure:
            classified = self._classified_failure(
                "create",
                create_failure,
            )
            self._finish_phase(
                provisioning,
                outcome="failed",
                failure_class=classified,
            )
            raise
        self._finish_phase(provisioning, outcome="passed")
        return result

    def _retain_late_create(
        self,
        job_id: str,
        create_task: asyncio.Task[Any],
    ) -> None:
        lifecycle = asyncio.create_task(
            self._drain_late_create(job_id, create_task),
            name=f"sandbox-broker-late-create-{job_id}",
        )
        self._create_tasks[job_id] = lifecycle

        def late_create_finished(task: asyncio.Task[Any]) -> None:
            if self._create_tasks.get(job_id) is task:
                del self._create_tasks[job_id]
            self._consume_task_exception(task)

        lifecycle.add_done_callback(late_create_finished)

    async def _drain_late_create(
        self,
        job_id: str,
        create_task: asyncio.Task[Any],
    ) -> None:
        try:
            created = await create_task
        except BaseException:
            return
        if type(created) is not SandboxHandle:
            return
        try:
            await self._destroy_once(job_id, created)
        finally:
            cleanup_task = self._cleanup_tasks.get(job_id)
            if cleanup_task is not None:
                try:
                    await asyncio.shield(cleanup_task)
                except BaseException:
                    pass

    async def _create_with_retry(self, job_id: str) -> tuple[object, bool]:
        loop = asyncio.get_running_loop()
        provisioning = self._start_phase(job_id, "provisioning", 1)
        deadline = loop.time() + self._create_hard_timeout_seconds
        try:
            return await self._create_without_cancelling(
                job_id,
                deadline,
                1,
                provisioning,
            )
        except SandboxCreateError as first_failure:
            first_class = self._classified_failure("create", first_failure)
            try:
                reconciled = await self._reconcile_unknown_create(job_id, deadline)
            finally:
                self._record_control_plane_failure("create", first_class)
            if not reconciled:
                raise _CreateHardDeadline(False, first_class) from None
            if self._cancellation_requested(job_id):
                raise asyncio.CancelledError
            remaining = deadline - loop.time()
            if remaining <= self._create_retry_delay_seconds:
                self._observe(
                    lambda: self.telemetry.record_retry("create", "exhausted")
                )
                raise _CreateHardDeadline(True, first_class) from None
            self._observe(
                lambda: self.telemetry.record_retry("create", "attempted")
            )
            await asyncio.sleep(self._create_retry_delay_seconds)
            if self._cancellation_requested(job_id):
                raise asyncio.CancelledError
            if loop.time() >= deadline:
                self._observe(
                    lambda: self.telemetry.record_retry("create", "exhausted")
                )
                raise _CreateHardDeadline(True, first_class) from None
            try:
                result = await self._create_without_cancelling(
                    job_id,
                    deadline,
                    2,
                )
            except SandboxCreateError as final_failure:
                final_class = self._classified_failure("create", final_failure)
                self._observe(
                    lambda: self.telemetry.record_retry("create", "exhausted")
                )
                try:
                    reconciled = await self._reconcile_unknown_create(
                        job_id,
                        deadline,
                    )
                finally:
                    self._record_control_plane_failure("create", final_class)
                if not reconciled:
                    raise _CreateHardDeadline(False, final_class) from None
                raise
            except _CreateHardDeadline as final_deadline:
                self._record_control_plane_failure(
                    "create",
                    final_deadline.failure_class,
                )
                self._observe(
                    lambda: self.telemetry.record_retry("create", "exhausted")
                )
                raise
            except BaseException as final_failure:
                self._record_control_plane_failure(
                    "create",
                    self._classified_failure("create", final_failure),
                )
                raise
            self._observe(
                lambda: self.telemetry.record_retry("create", "succeeded")
            )
            return result
        except _CreateHardDeadline as first_deadline:
            self._record_control_plane_failure(
                "create",
                first_deadline.failure_class,
            )
            raise
        except BaseException as first_failure:
            self._record_control_plane_failure(
                "create",
                self._classified_failure("create", first_failure),
            )
            raise

    async def _reconcile_unknown_create(
        self,
        job_id: str,
        deadline: float,
    ) -> bool:
        reconcile = getattr(self.client, "destroy_by_job_id", None)
        if not callable(reconcile):
            return False
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        task = asyncio.create_task(
            reconcile(job_id), name=f"sandbox-broker-reconcile-{job_id}"
        )
        try:
            destroyed = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=min(self._destroy_hard_timeout_seconds, remaining),
            )
            valid = type(destroyed) is int and destroyed == 0
            if not valid:
                self._record_control_plane_failure(
                    "destroy",
                    FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
                )
            return valid
        except asyncio.CancelledError:
            await self._cancel_and_isolate_reconciliation(task)
            raise
        except BaseException as reconcile_failure:
            self._record_control_plane_failure(
                "destroy",
                (
                    FailureClass.DESTROY_FAILED
                    if isinstance(reconcile_failure, asyncio.TimeoutError)
                    else self._classified_failure("destroy", reconcile_failure)
                ),
            )
            await self._cancel_and_isolate_reconciliation(task)
            return False

    async def _cancel_and_isolate_reconciliation(
        self,
        task: asyncio.Task[Any],
    ) -> None:
        if not task.done():
            task.cancel()
            self._isolated_tasks.add(task)
            self._refresh_runtime_metrics()

            def reconcile_finished(completed: asyncio.Task[Any]) -> None:
                self._isolated_tasks.discard(completed)
                self._consume_task_exception(completed)
                self._refresh_runtime_metrics()

            task.add_done_callback(reconcile_finished)
            await asyncio.sleep(0)
        else:
            self._consume_task_exception(task)

    async def cancel(self, job_id: str) -> BrokerJobView:
        """Persist and signal cancellation without cancelling lifecycle tasks."""

        try:
            record = self.store.request_cancel(job_id)
        except KeyError:
            raise KeyError("job not found") from None
        except Exception:
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE) from None
        if record.status in TERMINAL_STATUSES:
            return _record_view(record)
        self._cancel_events.setdefault(job_id, asyncio.Event()).set()
        current = self.store.get(job_id)
        if current is None:
            raise KeyError("job not found")
        return _record_view(current)

    async def recover(self) -> None:
        """Destroy persisted orphan sandboxes and fail every interrupted job."""

        observed_generation = self._recovery_generation
        async with self._recovery_lock:
            if observed_generation != self._recovery_generation:
                return
            await self._drain_pending_rejections()
            for record in self.store.cleanup_pending_jobs():
                was_terminal = record.status in TERMINAL_STATUSES
                try:
                    current = self.store.get(record.job_id)
                    if current is None or current.cleanup_status == "succeeded":
                        continue
                    was_terminal = current.status in TERMINAL_STATUSES
                    if current.sandbox_id is None:
                        continue
                    if current.cleanup_status != "in_progress":
                        self.store.record_cleanup(current.job_id, "in_progress")
                    try:
                        await self._recover_destroy(
                            current.job_id,
                            current.sandbox_id,
                        )
                    except asyncio.CancelledError:
                        raise
                    except BaseException:
                        latest = self.store.get(current.job_id)
                        if latest is not None and latest.cleanup_status != "succeeded":
                            self.store.record_cleanup(current.job_id, "failed")
                        latest = self.store.get(current.job_id)
                        if latest is not None and latest.status not in TERMINAL_STATUSES:
                            self.store.fail(
                                current.job_id,
                                BrokerErrorCode.CLEANUP_FAILED,
                                warnings=("cleanup_failed",),
                                cleanup_status="failed",
                            )
                    else:
                        self.store.record_cleanup(current.job_id, "succeeded")
                        latest = self.store.get(current.job_id)
                        if latest is not None and latest.status not in TERMINAL_STATUSES:
                            self.store.fail(
                                current.job_id,
                                BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                                cleanup_status="succeeded",
                            )
                    if not was_terminal:
                        latest = self.store.get(current.job_id)
                        self._record_terminal_observation(
                            current.job_id,
                            (
                                FailureClass.DESTROY_FAILED
                                if latest is not None
                                and latest.cleanup_status == "failed"
                                else FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
                            ),
                        )
                    await self._notify_terminal()
                except asyncio.CancelledError:
                    raise
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
                    if not was_terminal:
                        self._record_terminal_observation(
                            record.job_id,
                            FailureClass.DESTROY_FAILED,
                        )

            for record in self.store.active_jobs():
                try:
                    current = self.store.get(record.job_id)
                    if current is None or current.status in TERMINAL_STATUSES:
                        continue
                    if current.sandbox_id is not None:
                        if current.cleanup_status == "succeeded":
                            self.store.fail(
                                current.job_id,
                                BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                                cleanup_status="succeeded",
                            )
                        else:
                            self.store.fail(
                                current.job_id,
                                BrokerErrorCode.CLEANUP_FAILED,
                                warnings=("cleanup_failed",),
                                cleanup_status="failed",
                            )
                    elif current.status is BrokerJobStatus.PROVISIONING:
                        self.store.record_cleanup(current.job_id, "in_progress")
                        try:
                            await self._recover_destroy_by_job_id(current.job_id)
                        except asyncio.CancelledError:
                            raise
                        except BaseException:
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
                    else:
                        self.store.record_cleanup(current.job_id, "succeeded")
                        self.store.fail(
                            current.job_id,
                            BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                            cleanup_status="succeeded",
                        )
                    latest = self.store.get(current.job_id)
                    self._record_terminal_observation(
                        current.job_id,
                        (
                            FailureClass.DESTROY_FAILED
                            if latest is not None
                            and latest.cleanup_status == "failed"
                            else FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
                        ),
                    )
                    await self._notify_terminal()
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    continue
            self._recovery_generation += 1

    async def _recover_destroy(self, job_id: str, sandbox_id: str) -> None:
        cleanup = self._start_phase(job_id, "cleanup", 1)
        try:
            await self._destroy_by_id_hard(sandbox_id)
        except asyncio.CancelledError:
            self._finish_phase(
                cleanup,
                outcome="cancelled",
                cleanup_status="failed",
            )
            raise
        except BaseException:
            self._finish_phase(
                cleanup,
                outcome="failed",
                failure_class=FailureClass.DESTROY_FAILED,
                cleanup_status="failed",
            )
            raise
        self._finish_phase(
            cleanup,
            outcome="passed",
            cleanup_status="succeeded",
        )

    async def _recover_destroy_by_job_id(self, job_id: str) -> None:
        cleanup = self._start_phase(job_id, "cleanup", 1)
        reconcile = getattr(self.client, "destroy_by_job_id", None)
        if not callable(reconcile):
            self._finish_phase(
                cleanup,
                outcome="failed",
                failure_class=FailureClass.DESTROY_FAILED,
                cleanup_status="failed",
            )
            self._record_control_plane_failure(
                "destroy", FailureClass.DESTROY_FAILED
            )
            raise BrokerFailure(BrokerErrorCode.CLEANUP_FAILED)

        task = asyncio.create_task(
            reconcile(job_id),
            name=f"sandbox-broker-recovery-reconcile-{job_id}",
        )
        try:
            destroyed = await asyncio.wait_for(
                asyncio.shield(task), timeout=self._destroy_hard_timeout_seconds
            )
            if type(destroyed) is not int or destroyed < 0:
                raise BrokerFailure(BrokerErrorCode.CLEANUP_FAILED)
        except asyncio.CancelledError:
            self._finish_phase(
                cleanup,
                outcome="cancelled",
                cleanup_status="failed",
            )
            await self._cancel_and_isolate_reconciliation(task)
            raise
        except BaseException as destroy_failure:
            await self._cancel_and_isolate_reconciliation(task)
            classified = (
                FailureClass.DESTROY_FAILED
                if isinstance(destroy_failure, asyncio.TimeoutError)
                else self._classified_failure("destroy", destroy_failure)
            )
            self._record_control_plane_failure("destroy", classified)
            self._finish_phase(
                cleanup,
                outcome="failed",
                failure_class=classified,
                cleanup_status="failed",
            )
            raise BrokerFailure(BrokerErrorCode.CLEANUP_FAILED) from None
        self._finish_phase(
            cleanup,
            outcome="passed",
            cleanup_status="succeeded",
        )

    async def _destroy_by_id_hard(self, sandbox_id: str) -> None:
        task = asyncio.create_task(
            self.client.destroy_by_id(sandbox_id),
            name=f"sandbox-broker-recovery-cleanup-{sandbox_id}",
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self._destroy_hard_timeout_seconds
            )
        except asyncio.CancelledError:
            await self._cancel_and_isolate_reconciliation(task)
            raise
        except BaseException as destroy_failure:
            await self._cancel_and_isolate_reconciliation(task)
            classified = (
                FailureClass.DESTROY_FAILED
                if isinstance(destroy_failure, asyncio.TimeoutError)
                else self._classified_failure("destroy", destroy_failure)
            )
            self._record_control_plane_failure("destroy", classified)
            raise BrokerFailure(BrokerErrorCode.CLEANUP_FAILED) from None

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
                    self._record_terminal_observation(latest.job_id)
                    await self._notify_terminal()

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
                await self._destroy_by_id_hard(record.sandbox_id)
            except BaseException:
                self.store.record_cleanup(record.job_id, "failed")
                self.store.fail(
                    record.job_id,
                    BrokerErrorCode.CLEANUP_FAILED,
                    warnings=("cleanup_failed",),
                    cleanup_status="failed",
                )
                self._record_terminal_observation(
                    record.job_id,
                    FailureClass.DESTROY_FAILED,
                )
                await self._notify_terminal()
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
            self._record_terminal_observation(
                record.job_id,
                FailureClass.DESTROY_FAILED,
            )
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
            handle, f"/workspace/input/receptor{receptor_suffix}", receptor
        )
        await self.client.upload_text(
            handle, f"/workspace/input/ligand{ligand_suffix}", ligand
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

    async def _download_scientific_output(
        self,
        handle: SandboxHandle,
    ) -> _DownloadedScientificOutput:
        result_text = await self.client.read_text(handle, "/workspace/output/result.json")
        pose_paths = await self.client.list_files(
            handle, "/workspace/output/poses", "*.pdbqt"
        )
        if pose_paths != ["/workspace/output/poses/result.pdbqt"]:
            raise ScientificOutputError("scientific output validation failed")
        pose_text = await self.client.read_text(handle, pose_paths[0])
        return _DownloadedScientificOutput(result_text, pose_paths, pose_text)

    def _validate_publish_downloaded(
        self,
        job_id: str,
        downloaded: _DownloadedScientificOutput,
    ) -> _PreparedCompletion:
        prepared = self._prepared.get(job_id)
        if prepared is None:
            raise ScientificOutputError("scientific output validation failed")
        if (
            type(downloaded.result_text) is not str
            or downloaded.pose_paths != ["/workspace/output/poses/result.pdbqt"]
            or type(downloaded.pose_text) is not str
        ):
            raise ScientificOutputError("scientific output validation failed")
        try:
            result_bytes = downloaded.result_text.encode("utf-8", errors="strict")
            pose_bytes = downloaded.pose_text.encode("utf-8", errors="strict")
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
        return bool(
            is_safe_metadata_text(value)
            and type(value) is str
            and not contains_sensitive_metadata_text(value)
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

    async def _destroy_once(
        self,
        job_id: str,
        handle: SandboxHandle,
    ) -> str:
        cleanup_task = asyncio.create_task(self.client.destroy(handle))
        cleanup_task.set_name(f"sandbox-broker-cleanup-{job_id}")
        self._cleanup_tasks[job_id] = cleanup_task
        self._refresh_runtime_metrics()

        def cleanup_finished(task: asyncio.Task[Any]) -> None:
            if self._cleanup_tasks.get(job_id) is task:
                del self._cleanup_tasks[job_id]
            self._consume_task_exception(task)
            self._refresh_runtime_metrics()

        cleanup_task.add_done_callback(cleanup_finished)
        try:
            done, _ = await asyncio.wait(
                {cleanup_task},
                timeout=self._destroy_hard_timeout_seconds,
            )
        except asyncio.CancelledError:
            cleanup_task.cancel()
            await asyncio.sleep(0)
            # The done callback retains sole ownership of registry removal so
            # shutdown continues to drain cancellation-resistant cleanup.
            return "failed"
        if cleanup_task not in done:
            cleanup_task.cancel()
            await asyncio.sleep(0)
            self._record_control_plane_failure(
                "destroy",
                FailureClass.DESTROY_FAILED,
            )
            # Completion is unknown. Keep the task tracked until its done
            # callback runs so shutdown cannot lose ownership of it.
            return "failed"
        try:
            cleanup_task.result()
        except SandboxDestroyError as failure:
            self._record_control_plane_failure(
                "destroy",
                failure.failure_class,
            )
            if (
                failure.completion_known is True
                and failure.failure_class in _RETRYABLE_DESTROY_FAILURES
            ):
                return "retryable_failed"
            return "failed"
        except BaseException as destroy_failure:
            self._record_control_plane_failure(
                "destroy",
                self._classified_failure("destroy", destroy_failure),
            )
            return "failed"
        return "succeeded"

    async def _destroy_best_effort(
        self,
        job_id: str,
        handle: SandboxHandle | None,
        *,
        remote_identity_unknown: bool = False,
    ) -> str:
        if handle is None:
            cleanup_phase = self._start_phase(job_id, "cleanup")
            try:
                self.store.record_cleanup(
                    job_id,
                    "failed" if remote_identity_unknown else "succeeded",
                )
            except BaseException:
                self._finish_phase(
                    cleanup_phase,
                    outcome="failed",
                    failure_class=FailureClass.DESTROY_FAILED,
                    cleanup_status="failed",
                )
                return "failed"
            outcome = "failed" if remote_identity_unknown else "succeeded"
            self._finish_phase(
                cleanup_phase,
                outcome="failed" if outcome == "failed" else "passed",
                failure_class=(
                    FailureClass.DESTROY_FAILED
                    if outcome == "failed"
                    else None
                ),
                cleanup_status=outcome,
            )
            return outcome
        cleanup_tracking_active = True
        try:
            self.store.record_cleanup(job_id, "in_progress")
        except BaseException:
            # A concurrent terminal state or persistence failure must never
            # suppress destruction of a handle already returned by the SDK.
            cleanup_tracking_active = False
        cleanup_phase = self._start_phase(job_id, "cleanup", 1)
        outcome = await self._destroy_once(job_id, handle)
        self._finish_phase(
            cleanup_phase,
            outcome="passed" if outcome == "succeeded" else "failed",
            failure_class=(
                None
                if outcome == "succeeded"
                else FailureClass.DESTROY_FAILED
            ),
            cleanup_status=(
                "succeeded" if outcome == "succeeded" else "failed"
            ),
        )
        if outcome == "retryable_failed":
            self._observe(
                lambda: self.telemetry.record_retry("destroy", "attempted")
            )
            try:
                await asyncio.sleep(self._destroy_retry_delay_seconds)
            except asyncio.CancelledError:
                outcome = "failed"
            else:
                cleanup_phase = self._start_phase(job_id, "cleanup", 2)
                outcome = await self._destroy_once(job_id, handle)
                self._finish_phase(
                    cleanup_phase,
                    outcome=(
                        "passed" if outcome == "succeeded" else "failed"
                    ),
                    failure_class=(
                        None
                        if outcome == "succeeded"
                        else FailureClass.DESTROY_FAILED
                    ),
                    cleanup_status=(
                        "succeeded" if outcome == "succeeded" else "failed"
                    ),
                )
            self._observe(
                lambda: self.telemetry.record_retry(
                    "destroy",
                    "succeeded" if outcome == "succeeded" else "exhausted",
                )
            )
        cleanup_succeeded = outcome == "succeeded"
        if cleanup_tracking_active and cleanup_succeeded:
            try:
                self.store.record_cleanup(job_id, "succeeded")
            except BaseException:
                return "failed"
        elif cleanup_tracking_active:
            try:
                self.store.record_cleanup(job_id, "failed")
            except BaseException:
                pass
        return "succeeded" if cleanup_succeeded else "failed"

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
            if manifest.artifacts != expected:
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

    def open_artifact(
        self,
        job_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactRecord, Iterator[bytes]]:
        """Open only a pose allowlisted by a succeeded job's verified manifest."""

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
            record, stream = self.artifact_registry.open_registered(
                job_id,
                artifact_id,
                self.config.output_max_bytes,
            )
            if record.media_type != "chemical/x-pdbqt":
                stream.close()
                raise ValueError
            return record, stream
        except Exception:
            raise KeyError("artifact not found") from None
