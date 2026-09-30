from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import secrets
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from src.docking.adapters.base import CommandOwnershipScope

from .backends.base import (
    BackendSubmitResult,
    StartOutcome,
    TaskRuntimeBackend,
    assert_async_backend_contract,
)
from .backends.local import LocalTaskBackend, TaskIdempotencyConflictError
from .config import PROJECT_ROOT, TaskRuntimeConfig
from .docking_execution import DockingExecution, _manifest_input_hash, _input_hash
from .docking_consent import (
    PREPARATION_SECONDS, DockingConsentError, DockingConsentPolicy,
    DockingConsentPreview, make_preview, seal_binding, validate_inputs,
    canonical_json, validate_approval, verify_approval, validate_execution_binding,
)
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


@dataclass(eq=False)
class _ConsentPreparation:
    preparation_id: str
    token: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    aborted: threading.Event = field(default_factory=threading.Event, repr=False)
    worker: asyncio.Task | None = field(default=None, repr=False)
    revocation: asyncio.Task | None = field(default=None, repr=False)
    revocation_status: str = "pending"
    settlement: asyncio.Task | None = field(default=None, repr=False)
    task_id: str | None = None
    manifest_path: Path | None = None
    stage_started: bool = False
    settled: bool = False


@dataclass(eq=False, repr=False)
class _ConsentExecution:
    row: dict[str, Any] = field(repr=False)
    token: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    signal: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    backend_signal: threading.Event | None = field(default=None, repr=False)
    admission: asyncio.Task | None = field(default=None, repr=False)
    submission: asyncio.Task | None = field(default=None, repr=False)
    worker: asyncio.Task | None = field(default=None, repr=False)
    command_scope: CommandOwnershipScope | None = field(default=None, repr=False)
    physical_unresolved: str | None = None
    physical_notice: asyncio.Future | None = field(default=None, repr=False)
    cancellation: asyncio.Task | None = field(default=None, repr=False)
    settlement: asyncio.Task | None = field(default=None, repr=False)
    deadline: asyncio.Task | None = field(default=None, repr=False)
    raw_deadline: asyncio.Task | None = field(default=None, repr=False)
    raw_expires: float | None = None
    backend_owner: asyncio.Task | None = field(default=None, repr=False)
    loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    status: str = "ACTIVE"
    reason: str | None = None
    pending: bool = False
    acquired: bool = False
    claim_uncertain: bool = False
    unused: bool = False
    settled: bool = False

    def __post_init__(self):
        if self.loop is not None:
            self.physical_notice = self.loop.create_future()

    def stop(self, status, reason) -> None:
        # Never hold this lock across SQLite, a coroutine await or physical cleanup.
        with self.lock:
            if self.status != "ACTIVE":
                return
            self.status, self.reason, self.pending = status, reason, True
            if status != "SUCCEEDED":
                self.signal.set()
                if self.backend_signal is not None:
                    self.backend_signal.set()


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
        temporal_backend_factory: Callable[[], Any] | None = None,
        uuid_factory: Callable[[], Any] | None = None,
        docking_execution: DockingExecution | Any | None = None,
        docking_consent_policy: DockingConsentPolicy | None = None,
        consent_wall_time_ms: Callable[[], int] | None = None,
        consent_monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or TaskRuntimeConfig.from_env()
        self.store = store or TaskStore()
        self.stager = stager or DockingInputStager(self.config.staging_root)
        self.selector = selector or TemporalDockingSelector(self.config.canary_percent)
        self._uuid_factory = uuid_factory or uuid4
        self._temporal_backend_lock = asyncio.Lock()
        self._temporal_backend_factory = (
            temporal_backend_factory or self._build_temporal_backend
        )
        if (
            temporal_backend is None
            and self.config.backend == "temporal_canary"
        ):
            temporal_backend = self._temporal_backend_factory()
            if inspect.isawaitable(temporal_backend):
                raise TypeError(
                    "async temporal backend factory cannot initialize canary runtime"
                )
        self.temporal_backend = temporal_backend
        if temporal_backend is not None:
            assert_async_backend_contract(temporal_backend)

        self._consent_local_handler = local_backend is None
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
                consent = self._consent_executions.get(submission.task_id)
                if consent is not None and consent.acquired:
                    return await self._run_consent_execution(
                        consent, submission, cancel_event, progress_callback,
                    )
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
        self._closing = False
        self._close_lock = threading.Lock()
        self._close_future: Future[None] | None = None
        self._close_runner: asyncio.Task[None] | None = None
        self._staging_cleanup_lock = threading.Lock()
        self._last_staging_cleanup = 0.0
        self._docking_consent_policy = docking_consent_policy
        self._consent_wall_time_ms = consent_wall_time_ms or (lambda: time.time_ns() // 1_000_000)
        self._consent_monotonic = consent_monotonic or time.monotonic
        # Ownership of real I/O, not a dispatch queue. Durable capacity lives in SQLite.
        self._consent_preparations: set[_ConsentPreparation] = set()
        self._consent_executions: dict[str, _ConsentExecution] = {}

    async def approve_docking_consent(
        self, *, preparation_id: str, owner_session_id: str,
        approval_nonce: str, binding_digest: str, confirm: bool,
    ) -> dict[str, Any]:
        row = await self._load_owned_consent(owner_session_id=owner_session_id,
            preparation_id=preparation_id,
        )
        validate_approval(approval_nonce, binding_digest, confirm)
        verify_approval(row, approval_nonce, binding_digest)
        if row["state"] in {"CLAIMED", "DISPATCH_RESERVED"}:
            return await self._consent_receipt(row)
        if row["state"] != "READY":
            raise DockingConsentError("consent_not_waiting")
        if (self._closing or self._closed or not self._consent_local_handler
                or type(self.docking_execution) is not DockingExecution):
            raise DockingConsentError("consent_policy_unavailable")
        operation = self._consent_executions.get(row["task_id"])
        if operation is None:
            operation = _ConsentExecution(row, loop=asyncio.get_running_loop())
            self._consent_executions[row["task_id"]] = operation
            operation.admission = asyncio.create_task(self._admit_consent_execution(operation))
        elif operation.unused:
            raise DockingConsentError("consent_not_waiting")
        try:
            await asyncio.shield(operation.admission)
        except asyncio.CancelledError:
            self._request_consent_stop(operation, "CANCELLED", "consent_cancelled")
            raise
        current = await self._consent_row(operation)
        return await self._consent_receipt(current)

    async def get_docking_consent_view(
        self, *, task_id: str, owner_session_id: str,
    ) -> dict[str, Any]:
        row = await self._load_owned_consent(owner_session_id=owner_session_id, task_id=task_id,
        )
        return await self._consent_receipt(row)

    async def cancel_docking_consent(
        self, *, task_id: str, owner_session_id: str,
    ) -> dict[str, Any]:
        row = await self._load_owned_consent(owner_session_id=owner_session_id, task_id=task_id,
        )
        if row["view_status"] != "ACTIVE":
            return await self._consent_receipt(row)
        operation = self._consent_executions.get(task_id)
        if operation is None:
            if row["state"] != "READY":
                # Another process or an interrupted prior owner is not replayable.
                return await self._consent_receipt(row)
            operation = _ConsentExecution(row, unused=True, loop=asyncio.get_running_loop())
            self._consent_executions[task_id] = operation
        self._request_consent_stop(operation, "CANCELLED", "consent_cancelled")
        # Local signal is immediate; no claim of a completed SQL receipt here.
        return await self._consent_receipt(row)

    async def _load_owned_consent(self, **identity):
        try:
            return await asyncio.to_thread(self.store._owned_docking_consent, **identity)
        except DockingConsentError:
            raise
        except Exception:
            raise DockingConsentError("consent_persistence_unavailable") from None

    async def _consent_row(self, operation):
        return await self._load_owned_consent(
            owner_session_id=operation.row["owner_session_id"],
            preparation_id=operation.row["preparation_id"],
        )

    async def _consent_receipt(self, row):
        operation = self._consent_executions.get(row["task_id"])
        status, reason = row["view_status"], row["primary_reason"]
        pending = False
        if operation is not None and operation.status != "ACTIVE":
            status, reason, pending = operation.status, operation.reason, operation.pending
        elif operation is None and status == "ACTIVE" and row["state"] in {"CLAIMED", "DISPATCH_RESERVED"}:
            status, reason = "UNKNOWN", "consent_execution_unknown"
        policy = self._docking_consent_policy
        if status in {"ACTIVE", "SUCCEEDED"} and row["state"] in {"CLAIMED", "DISPATCH_RESERVED"} and (
            type(policy) is not DockingConsentPolicy
            or policy.runtime_generation != row["runtime_generation"]
        ):
            status, reason = "UNKNOWN", "consent_execution_unknown"
        try:
            task = await asyncio.to_thread(self.store.get, row["task_id"])
            task_status = task.status.value
        except KeyError:
            task_status = None
        except Exception:
            task_status, pending = None, True
            if status in {"ACTIVE", "SUCCEEDED"}:
                status, reason = "UNKNOWN", "consent_execution_unknown"
        return {
            "preparation_id": row["preparation_id"], "task_id": row["task_id"],
            "trace_id": json.loads(row["identity_json"])["trace_id"],
            "consent_state": row["state"], "dispatch_state": row["dispatch_state"],
            "view_status": status, "task_status": task_status,
            "cleanup_status": row["cleanup_state"], "reason_code": reason,
            "persistence_pending": pending,
        }

    def _validate_consent_row(self, operation, row):
        if operation.signal.is_set():
            raise DockingConsentError(operation.reason or "consent_cancelled")
        return validate_execution_binding(
            row, self._docking_consent_policy, self._consent_wall_time_ms(),
            self._consent_monotonic(), expected=operation.row,
        )

    def _validate_consent_manifest(self, operation):
        binding = self._validate_consent_row(operation, operation.row)
        try:
            manifest = self.stager.load_verified_locator(
                operation.row["task_id"], operation.row["manifest_locator"],
            )
            if (_manifest_input_hash(manifest) != binding["input_hash"]
                    or manifest["config_hash"] != binding["config_hash"]
                    or self._docking_request_digest(manifest) != binding["request_digest"]
                    or any(manifest["config"][key] != binding[key] for key in manifest["config"])
                    or any(Path(manifest[key]["path"]).name != binding[key]["name"]
                           for key in ("receptor", "ligand"))):
                raise ValueError
        except Exception:
            raise DockingConsentError("consent_binding_mismatch") from None

    async def _admit_consent_execution(self, operation):
        try:
            await asyncio.to_thread(self._validate_consent_manifest, operation)
            acquired = await asyncio.to_thread(
                self.store._claim_docking_consent, operation.row, operation.token,
                lambda row: self._validate_consent_row(operation, row),
                self._consent_wall_time_ms, self._consent_monotonic,
            )
        except DockingConsentError:
            if not operation.signal.is_set():
                self._consent_executions.pop(operation.row["task_id"], None)
            raise
        except Exception:
            # Read back only; never retry a possibly committed claim.
            try:
                current = await self._consent_row(operation)
            except Exception:
                operation.claim_uncertain = True
                operation.stop("UNKNOWN", "consent_execution_unknown")
                raise DockingConsentError("consent_execution_unknown") from None
            if current["execution_token"] != operation.token:
                self._consent_executions.pop(operation.row["task_id"], None)
                raise DockingConsentError("consent_persistence_unavailable") from None
            operation.acquired = True
            operation.stop("UNKNOWN", "consent_execution_unknown")
            await self._persist_consent_terminal(operation)
            raise DockingConsentError("consent_execution_unknown") from None
        if not acquired:
            self._consent_executions.pop(operation.row["task_id"], None)
            return
        operation.acquired = True
        try:
            await self._submit_claimed_consent(operation)
        except Exception:
            operation.stop("UNKNOWN", "consent_execution_unknown")
            await self._persist_consent_terminal(operation)
            if operation.settlement is None:
                operation.settlement = asyncio.create_task(self._settle_consent_execution(operation))
            raise DockingConsentError("consent_execution_unknown") from None

    async def _submit_claimed_consent(self, operation):
        current = await self._consent_row(operation)
        # Keep the original sealed facts for the later in-lease comparison.
        operation.row.update({key: current[key] for key in (
            "operation_deadline_ms", "operation_monotonic_expires",
        )})
        operation.deadline = asyncio.create_task(self._consent_deadline(operation))
        submission = TaskSubmission(
            task_id=operation.row["task_id"], task_type="docking", payload={},
            input_manifest_path=str(self.stager.resolve_manifest_locator(
                operation.row["task_id"], operation.row["manifest_locator"],
            )),
            request_digest=json.loads(operation.row["binding_json"])["request_digest"],
        )
        operation.submission = asyncio.create_task(self.local_backend.submit(submission))
        try:
            await asyncio.shield(operation.submission)
        except Exception:
            operation.stop("UNKNOWN", "consent_execution_unknown")
            await self._persist_consent_terminal(operation)
        finally:
            operation.settlement = asyncio.create_task(self._settle_consent_execution(operation))
        if operation.signal.is_set() and operation.cancellation is None:
            self._request_consent_stop(operation, operation.status, operation.reason)

    def _request_consent_stop(self, operation, status, reason):
        operation.stop(status, reason)
        if operation.status == "SUCCEEDED" or operation.settled:
            return
        if operation.cancellation is None:
            operation.cancellation = asyncio.create_task(self._cancel_consent_execution(operation))

    async def _cancel_consent_execution(self, operation):
        if operation.unused:
            try:
                row = await asyncio.to_thread(
                    self.store._terminal_docking_consent, operation.row, operation.token,
                    operation.status, operation.reason, unused=True,
                )
                if row["execution_token"] != operation.token or row["state"] != "REVOKED":
                    # A concurrent claim won: no authority to delete its stage.
                    operation.pending = True
                    return
                operation.acquired = True
                operation.pending = False
                operation.settlement = asyncio.create_task(self._settle_consent_execution(operation))
            except Exception:
                operation.pending = True
            return
        if operation.admission is not None and not operation.acquired:
            try:
                await asyncio.shield(operation.admission)
            except Exception:
                row = await self._consent_row(operation)
                if row["state"] == "READY":
                    operation.unused = True
                    await self._cancel_consent_execution(operation)
                return
        if not operation.acquired:
            return
        persisted = await self._persist_consent_terminal(operation)
        if operation.submission is None:
            return
        operation.pending = True
        # Retain actual create/submit; do not cancel the coroutine that owns it.
        if operation.submission is not None:
            try:
                await asyncio.shield(operation.submission)
            except Exception:
                pass
        try:
            await self.local_backend.cancel(operation.row["task_id"], reason="consent cancelled")
        except KeyError:
            # No task row is not evidence that a possibly committed claim is safe.
            operation.pending = True
        except Exception:
            operation.pending = True
        else:
            operation.pending = not persisted

    async def _persist_consent_terminal(self, operation):
        try:
            row = await asyncio.to_thread(
                self.store._terminal_docking_consent, operation.row, operation.token,
                operation.status, operation.reason,
            )
            confirmed = (row["execution_token"] == operation.token
                         and row["view_status"] == operation.status
                         and row["primary_reason"] == operation.reason)
        except Exception:
            confirmed = False
        operation.pending = not confirmed
        return confirmed

    async def _consent_deadline(self, operation):
        # Absolute original budget; cancellation stops feedback but never abandons
        # the owned thread/lease or extends its deadline.
        remaining = max(0, operation.row["operation_monotonic_expires"] - self._consent_monotonic())
        await asyncio.sleep(remaining)
        self._request_consent_stop(operation, "TIMED_OUT", "consent_execution_timeout")

    def _arm_consent_raw_deadline(self, operation):
        if operation.raw_deadline is None and not operation.settled:
            async def deadline():
                await asyncio.sleep(max(0, operation.raw_expires - self._consent_monotonic()))
                self._request_consent_stop(operation, "TIMED_OUT", "consent_execution_timeout")
            operation.raw_deadline = asyncio.create_task(deadline())

    def _consent_execution_guard(self, operation, inputs, phase):
        try:
            if phase in {"result", "dispatch"}:
                if operation.signal.is_set():
                    raise DockingConsentError(operation.reason or "consent_cancelled")
                if (self._consent_monotonic() >= operation.row["operation_monotonic_expires"]
                        or self._consent_wall_time_ms() >= operation.row["operation_deadline_ms"]
                        or (operation.raw_expires is not None
                            and self._consent_monotonic() >= operation.raw_expires)):
                    raise DockingConsentError("consent_execution_timeout")
                if phase == "dispatch":
                    # Reservation verification/commit may outlive the consent.
                    # Recheck at raw dispatch without restoring its spent claim.
                    binding = self._validate_consent_row(operation, operation.row)
                    operation.raw_expires = (self._consent_monotonic()
                        + binding["vina_limit_seconds"])
                    operation.loop.call_soon_threadsafe(self._arm_consent_raw_deadline, operation)
                return

            def validate(row):
                binding = self._validate_consent_row(operation, row)
                inputs.verify_integrity()
                if (_input_hash(inputs) != binding["input_hash"]
                        or inputs.config_hash != binding["config_hash"]
                        or canonical_json(dict(inputs.config)) != canonical_json(
                            {key: binding[key] for key in inputs.config})
                        or inputs.receptor_path.name != binding["receptor"]["name"]
                        or inputs.ligand_path.name != binding["ligand"]["name"]):
                    raise DockingConsentError("consent_binding_mismatch")
                return binding

            if phase == "reserve":
                try:
                    self.store._reserve_docking_dispatch(operation.row, operation.token, validate)
                except DockingConsentError:
                    raise
                except Exception:
                    raise DockingConsentError("consent_execution_unknown") from None
            else:
                row = self.store._owned_docking_consent(
                    owner_session_id=operation.row["owner_session_id"],
                    preparation_id=operation.row["preparation_id"],
                )
                validate(row)
        except DockingConsentError as exc:
            status = ("TIMED_OUT" if exc.reason_code in {"consent_expired", "consent_execution_timeout"}
                      else "UNKNOWN" if exc.reason_code == "consent_execution_unknown"
                      else "CANCELLED" if exc.reason_code == "consent_cancelled" else "FAILED")
            operation.stop(status, exc.reason_code)
            raise

    async def _run_consent_execution(self, operation, submission, cancel_event, progress_callback):
        operation.backend_owner = asyncio.current_task()
        with operation.lock:
            operation.backend_signal = cancel_event
            if operation.signal.is_set():
                cancel_event.set()
        if operation.signal.is_set():
            raise DockingConsentError(operation.reason or "consent_cancelled")
        operation.command_scope = CommandOwnershipScope(
            task_id=submission.task_id, output_root=self.docking_execution._allowed_output_root,
            cancel_event=operation.signal,
        )
        operation.worker = asyncio.create_task(asyncio.to_thread(
            self.docking_execution.run_verified, submission.task_id, submission.input_manifest_path,
            cancel_event=operation.signal, progress_callback=progress_callback,
            lease_timeout_seconds=min(300.0, max(0.001,
                operation.row["operation_monotonic_expires"] - self._consent_monotonic())),
            consent_guard=lambda inputs, phase: self._consent_execution_guard(operation, inputs, phase),
            command_scope=operation.command_scope,
            ownership_observer=lambda reason: self._observe_consent_ownership(operation, reason),
        ))
        try:
            result = await asyncio.shield(operation.worker)
            # Even a late completion-commit acknowledgement cannot pass a spent
            # operation deadline or the already chosen non-success terminal.
            self._consent_execution_guard(operation, None, "result")
            return result
        except asyncio.CancelledError:
            self._request_consent_stop(operation, "CANCELLED", "consent_cancelled")
            # Physical thread and lease remain owned even under repeated cancellation.
            try:
                await self._await_task_outcome(operation.worker)
            except Exception:
                pass
            raise
        finally:
            if operation.raw_deadline is not None:
                operation.raw_deadline.cancel()
                await asyncio.gather(operation.raw_deadline, return_exceptions=True)

    def _observe_consent_ownership(self, operation, reason):
        if reason not in {"command_ownership_unresolved", "own_job_cleanup_unresolved"}:
            raise ValueError("Invalid command ownership observation")
        with operation.lock:
            if operation.physical_unresolved is None:
                operation.physical_unresolved = reason
            operation.pending = True
        # The signal reports a real worker outcome, not task completion. Never
        # invoke loop callbacks or perform physical cleanup under operation.lock.
        def notify():
            notice = operation.physical_notice
            if notice is not None and not notice.done():
                notice.set_result(None)

        try:
            operation.loop.call_soon_threadsafe(notify)
        except RuntimeError:
            pass  # A closed loop cannot erase the retained synchronous latch.

    @staticmethod
    def _consent_commands_released(operation):
        with operation.lock:
            if operation.physical_unresolved is not None:
                return False
            scope = operation.command_scope
            dispatched = operation.raw_expires is not None
        if scope is None:
            return not dispatched
        with scope._condition:
            if not dispatched and not scope._commands and scope._own_job_cleanup is None:
                return True  # Independently fenced pre-raw rejection/reuse.
            return (scope.snapshot()["state"] == "settled"
                    and scope._own_job_cleanup_state in {"none", "done"})

    async def _await_consent_close_task(self, operation, task):
        # Race existing tasks against a one-way Future, not another owner/task.
        # Cancellation of shutdown observation must not cancel either input.
        while True:
            with operation.lock:
                if operation.physical_unresolved is not None:
                    raise DockingConsentError("consent_cleanup_unresolved")
                if task.done():
                    # Read the already-completed outcome in the same latch
                    # observation; no callback, await or cleanup under this lock.
                    return task.result()
            try:
                if operation.physical_notice is None:
                    await asyncio.shield(task)
                else:
                    await asyncio.wait((task, operation.physical_notice),
                                       return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                continue

    async def _settle_consent_execution(self, operation):
        try:
            if not operation.unused:
                # Reuse the actual LocalTaskBackend owner, not TaskStatus as a
                # physical completion signal. No mutation of backend registries.
                with self.local_backend._lock:
                    actual = self.local_backend._tasks.get(operation.row["task_id"], operation.backend_owner)
                while actual is not None:
                    try:
                        await self._await_task_outcome(actual)
                    except (Exception, asyncio.CancelledError):
                        operation.stop("UNKNOWN", "consent_execution_unknown")
                    # Done callbacks can install the existing backend recovery
                    # task. Drain that actual owner too; do not invent recovery.
                    await asyncio.sleep(0)
                    with self.local_backend._lock:
                        successor = self.local_backend._tasks.get(operation.row["task_id"])
                    if successor is actual:
                        break
                    actual = successor
                if operation.worker is not None:
                    try:
                        await self._await_task_outcome(operation.worker)
                    except (Exception, asyncio.CancelledError):
                        pass
                if operation.cancellation is not None:
                    await self._await_task_outcome(operation.cancellation)
                record = await asyncio.to_thread(self.store.get, operation.row["task_id"])
                if record.status not in TERMINAL_STATUSES:
                    operation.stop("UNKNOWN", "consent_execution_unknown")
                    await self._persist_consent_terminal(operation)
                    return
                if operation.status == "ACTIVE":
                    if record.status is TaskStatus.SUCCEEDED:
                        operation.stop("SUCCEEDED", None)
                    elif record.status is TaskStatus.CANCELED:
                        operation.stop("CANCELLED", "consent_cancelled")
                    elif record.status is TaskStatus.TIMED_OUT:
                        operation.stop("TIMED_OUT", "consent_execution_timeout")
                    else:
                        operation.stop("FAILED", "consent_execution_failed")
            if not self._consent_commands_released(operation):
                self._observe_consent_ownership(operation, "command_ownership_unresolved")
                return  # No staging deletion, capacity release or owner removal.
            if not await self._persist_consent_terminal(operation):
                return
            if operation.status != "SUCCEEDED":
                manifest = self.stager.resolve_manifest_locator(
                    operation.row["task_id"], operation.row["manifest_locator"],
                )
                removed = await asyncio.to_thread(
                    self.stager.discard_unprojected, operation.row["task_id"], manifest,
                    projection_check=lambda task_id: self.store._docking_execution_cleanup_protected(
                        task_id, operation.token,
                    ),
                )
                if not removed:
                    await asyncio.to_thread(self.store._settle_docking_execution,
                                            operation.row, operation.token, settled=False)
                    return
            operation.settled = await asyncio.to_thread(
                self.store._settle_docking_execution, operation.row, operation.token, settled=True,
            )
        except (Exception, asyncio.CancelledError):
            operation.pending = True
        finally:
            for timer in (operation.deadline, operation.raw_deadline):
                if timer is not None:
                    timer.cancel()
                    await asyncio.gather(timer, return_exceptions=True)
            if operation.settled and self._consent_executions.get(operation.row["task_id"]) is operation:
                self._consent_executions.pop(operation.row["task_id"])

    async def _close_consent_executions(self):
        operations = tuple(self._consent_executions.values())
        for operation in operations:
            if not operation.settled:
                self._request_consent_stop(operation, "CANCELLED", "consent_cancelled")
        for operation in operations:
            for name in ("admission", "cancellation", "settlement", "worker"):
                task = getattr(operation, name)
                if task is not None:
                    try:
                        await self._await_consent_close_task(operation, task)
                    except DockingConsentError as exc:
                        if exc.reason_code == "consent_cleanup_unresolved":
                            raise
                    except (Exception, asyncio.CancelledError):
                        pass
        if any((operation.acquired or operation.claim_uncertain) and not operation.settled
               for operation in operations):
            raise DockingConsentError("consent_cleanup_unresolved")

    async def prepare_docking_consent(
        self, *, preparation_id: str, owner_session_id: str, revision: int,
        receptor_name: str, receptor_bytes: bytes, ligand_name: str,
        ligand_bytes: bytes, parameters: dict[str, Any],
    ) -> DockingConsentPreview:
        if self._closed or self._closing:
            raise DockingConsentError("consent_policy_unavailable")
        policy = self._docking_consent_policy
        if type(policy) is not DockingConsentPolicy:
            raise DockingConsentError("consent_policy_unavailable")
        deadline = asyncio.get_running_loop().time() + PREPARATION_SECONDS
        config = validate_inputs(receptor_name, receptor_bytes, ligand_name,
                                 ligand_bytes, parameters)
        operation = _ConsentPreparation(preparation_id)
        self._consent_preparations.add(operation)
        operation.worker = asyncio.create_task(
            self._prepare_consent_owned(
                operation, owner_session_id, revision, policy, deadline,
                receptor_name, receptor_bytes, ligand_name, ligand_bytes, config,
            ), name="medchat-consent-prepare",
        )
        try:
            done, _ = await asyncio.wait(
                {operation.worker}, timeout=max(0.0, deadline - asyncio.get_running_loop().time()),
            )
            if not done or asyncio.get_running_loop().time() >= deadline:
                raise DockingConsentError("consent_preparation_timeout")
            if operation.aborted.is_set() or self._closing:
                raise DockingConsentError("consent_not_waiting")
            preview = operation.worker.result()
        except BaseException as exc:
            await self._abort_consent_preparation(operation)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, DockingConsentError):
                raise
            raise DockingConsentError("consent_invalid_input") from None
        self._consent_preparations.discard(operation)
        return preview

    async def _prepare_consent_owned(
        self, operation, owner_session_id, revision, policy, deadline,
        receptor_name, receptor_bytes, ligand_name, ligand_bytes, config,
    ) -> DockingConsentPreview:
        self._check_consent_preparation(operation, deadline)
        loop = asyncio.get_running_loop()

        def still_current():
            return (not operation.aborted.is_set() and loop.time() < deadline
                    and self._docking_consent_policy is policy)

        identity = await asyncio.to_thread(
            self.store._begin_docking_preparation,
            preparation_id=operation.preparation_id, owner_session_id=owner_session_id,
            revision=revision, policy=policy, token=operation.token,
            now_ms=self._consent_wall_time_ms(), monotonic_now=self._consent_monotonic(),
            still_current=still_current,
        )
        operation.task_id = identity["task_id"]
        self._check_consent_preparation(operation, deadline)
        operation.stage_started = True
        path = await asyncio.to_thread(
            self.stager.stage, operation.task_id, receptor_name, receptor_bytes,
            ligand_name, ligand_bytes, None, config,
        )
        operation.manifest_path = Path(path)
        self._check_consent_preparation(operation, deadline)
        manifest = await asyncio.to_thread(
            self.stager.load_verified_locator, operation.task_id, "input_manifest.json",
        )
        self._check_consent_preparation(operation, deadline)
        issued_ms = self._consent_wall_time_ms()
        binding, digest = seal_binding(
            identity, manifest, policy, self._docking_request_digest(manifest),
            _manifest_input_hash(manifest), issued_ms,
        )
        nonce = secrets.token_hex(32)
        preview = make_preview(binding, digest, nonce)
        await asyncio.to_thread(
            self.store._seal_docking_preparation, operation.preparation_id, operation.token,
            binding, digest, hashlib.sha256(nonce.encode("ascii")).hexdigest(),
            now_ms=issued_ms, monotonic_now=self._consent_monotonic(),
            still_current=still_current, wall_time_ms=self._consent_wall_time_ms,
            monotonic=self._consent_monotonic,
        )
        self._check_consent_preparation(operation, deadline)
        return preview

    @staticmethod
    def _check_consent_preparation(operation, deadline) -> None:
        if operation.aborted.is_set():
            raise DockingConsentError("consent_not_waiting")
        if asyncio.get_running_loop().time() >= deadline:
            raise DockingConsentError("consent_preparation_timeout")

    async def _abort_consent_preparation(self, operation) -> None:
        # This coroutine deliberately does not await I/O. Caller outcome is local;
        # only the retained receipt below can confirm a durable terminal state.
        operation.aborted.set()
        if operation.revocation is None:
            operation.revocation = asyncio.create_task(
                self._revoke_consent_preparation(operation), name="medchat-consent-revoke",
            )
        if operation.settlement is None:
            # Never cancel the to_thread wrapper: settlement owns its actual return.
            operation.settlement = asyncio.create_task(
                self._settle_consent_preparation(operation), name="medchat-consent-cleanup",
            )

    async def _revoke_consent_preparation(self, operation) -> None:
        marker = asyncio.create_task(asyncio.to_thread(
            self.store._revoke_docking_preparation, operation.preparation_id, operation.token,
        ))
        try:
            confirmed = await self._await_task_outcome(marker)
        except Exception:
            # No retry or success inference after an uncertain commit outcome.
            operation.revocation_status = "unconfirmed"
        else:
            operation.revocation_status = "confirmed" if confirmed is True else "unconfirmed"

    async def _settle_consent_preparation(self, operation) -> None:
        try:
            try:
                await self._await_task_outcome(operation.worker)
            except BaseException:
                pass
            await self._await_task_outcome(operation.revocation)
            task_id = await asyncio.to_thread(
                self.store._docking_preparation_task, operation.preparation_id, operation.token,
            )
            if task_id is None:
                # A rejected duplicate/missing/foreign request acquired no writer.
                # This proves absence of our ownership, NOT a confirmed revocation.
                operation.settled = not operation.stage_started
            elif operation.revocation_status != "confirmed":
                # Matching ownership still exists: no cleanup/capacity release
                # from an exception, zero-match or merely completed SQL wrapper.
                operation.settled = False
            elif not operation.stage_started:
                operation.settled = True
            elif operation.manifest_path is not None:
                def protected(candidate):
                    if candidate != task_id:
                        return True
                    try:
                        self.store.get(candidate)
                    except KeyError:
                        return self.store._docking_stage_protected(
                            candidate, cleanup_token=operation.token,
                        )
                    return True

                operation.settled = bool(await asyncio.to_thread(
                    self.stager.discard_unprojected, task_id, operation.manifest_path,
                    projection_check=protected,
                ))
            # An exception before stage returned supplies no safe deletion receipt.
            # Keep that reservation unresolved, not guessed absent from a pathname.
            if task_id is not None:
                await asyncio.to_thread(
                    self.store._finish_docking_cleanup, operation.preparation_id,
                    operation.token, settled=operation.settled,
                )
        except Exception:
            operation.settled = False
        if operation.settled:
            self._consent_preparations.discard(operation)

    async def _close_consent_preparations(self) -> None:
        operations = tuple(self._consent_preparations)
        for operation in operations:
            await self._abort_consent_preparation(operation)
        for operation in operations:
            await self._await_task_outcome(operation.settlement)
        if any(not operation.settled for operation in operations):
            raise DockingConsentError("consent_cleanup_unresolved")

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
        if record.backend == "temporal":
            temporal_backend = await self._temporal_control_backend()
            return await temporal_backend.get(task_id)
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
            return self.store._docking_stage_protected(task_id)
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
        if record.backend == "temporal":
            temporal_backend = await self._temporal_control_backend()
            return await temporal_backend.cancel(task_id, reason)
        return await self.local_backend.cancel(task_id, reason)

    def _build_temporal_backend(self) -> TaskRuntimeBackend:
        from .backends.temporal import TemporalTaskBackend

        return TemporalTaskBackend(
            self.store,
            address=self.config.temporal_address,
            namespace=self.config.temporal_namespace,
            task_queue=self.config.docking_queue,
        )

    async def _temporal_control_backend(self) -> TaskRuntimeBackend:
        if self._closing or self._closed:
            raise RuntimeError("task runtime is closed")
        if self.temporal_backend is not None:
            return self.temporal_backend
        async with self._temporal_backend_lock:
            if self._closing or self._closed:
                raise RuntimeError("task runtime is closed")
            if self.temporal_backend is not None:
                return self.temporal_backend
            try:
                backend = self._temporal_backend_factory()
                if inspect.isawaitable(backend):
                    backend = await backend
                assert_async_backend_contract(backend)
            except Exception as exc:
                raise TaskBackendStartError(
                    "Temporal control backend is unavailable"
                ) from exc
            if self._closing or self._closed:
                await self._close_backend(backend)
                raise RuntimeError("task runtime is closed")
            self.temporal_backend = backend
            return backend

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
                self._closing = True
                self._close_runner = asyncio.create_task(
                    self._close_once(shared),
                    name="medchat-task-runtime-close",
                )
        await asyncio.shield(asyncio.wrap_future(shared))

    async def _close_once(self, shared: Future[None]) -> None:
        try:
            await self._close_consent_preparations()
            await self._close_consent_executions()
            await self._close_backends()
        except BaseException as exc:
            with self._close_lock:
                if self._close_future is shared:
                    self._close_future = None
                    self._close_runner = None
                    self._closing = False
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
            await self._close_backend(backend)

    @staticmethod
    async def _close_backend(backend: TaskRuntimeBackend) -> None:
        closer = getattr(backend, "close", None)
        if closer is None:
            closer = getattr(backend, "shutdown", None)
        if closer is None:
            return
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
