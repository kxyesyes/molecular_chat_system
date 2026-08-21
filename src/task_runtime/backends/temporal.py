"""Temporal task backend with duplicate-safe start and SQLite projection."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Awaitable, Callable

from ..models import BackendHealth, TaskPhase, TaskRecord, TaskStatus, TaskSubmission
from ..store import TERMINAL_STATUSES, TaskStore
from ..temporal.reconcile import repair_temporal_projection
from .base import BackendSubmitResult, StartOutcome
from .local import LocalTaskBackend, TaskIdempotencyConflictError


ClientFactory = Callable[[str, str], Awaitable[Any]]
_WORKFLOW_TYPE = "medchat-docking-workflow"
_MANIFEST_LOCATOR = "input_manifest.json"
_START_REJECTED = "TEMPORAL_START_FAILED"
_START_AMBIGUOUS = "TEMPORAL_START_AMBIGUOUS"


@dataclass(frozen=True)
class TemporalStartOutcome:
    accepted: bool
    workflow_id: str | None
    error_code: str | None

    def __post_init__(self) -> None:
        if type(self.accepted) is not bool:
            raise ValueError("invalid Temporal accepted state")
        if self.accepted:
            if not isinstance(self.workflow_id, str) or not self.workflow_id:
                raise ValueError("accepted Temporal start requires workflow id")
            if self.error_code is not None:
                raise ValueError("accepted Temporal start cannot have error code")
        elif self.workflow_id is not None:
            raise ValueError("unaccepted Temporal start cannot have workflow id")
        elif self.error_code not in {_START_REJECTED, _START_AMBIGUOUS}:
            raise ValueError("invalid Temporal start error code")


class TemporalTaskBackend:
    """Async Temporal adapter; scientific execution remains in Activities."""

    def __init__(
        self,
        store: TaskStore,
        *,
        address: str,
        namespace: str,
        task_queue: str,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not isinstance(store, TaskStore):
            raise TypeError("store must be a TaskStore")
        for name, value in (
            ("address", address),
            ("namespace", namespace),
            ("task_queue", task_queue),
        ):
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"invalid Temporal {name}")
        self.store = store
        self._address = address
        self._namespace = namespace
        self._task_queue = task_queue
        self._client_factory = client_factory
        self._client: Any = None
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._connect_lock:
            if self._client is not None:
                return self._client
            if self._client_factory is None:
                from temporalio.client import Client

                client = await Client.connect(
                    self._address,
                    namespace=self._namespace,
                )
            else:
                client = await self._client_factory(
                    self._address,
                    self._namespace,
                )
            if client is None:
                raise RuntimeError("Temporal client unavailable")
            self._client = client
            return client

    async def submit(self, submission: TaskSubmission) -> BackendSubmitResult:
        self._validate_submission(submission)
        workflow_id = self._workflow_id(submission.task_id)
        try:
            client = await self.connect()
        except asyncio.CancelledError:
            raise
        except Exception:
            return BackendSubmitResult(
                submission.task_id,
                "temporal",
                StartOutcome.REJECTED,
                "Temporal connection was not established",
            )
        authority, owns_reservation = await asyncio.to_thread(
            self._reserve_authority,
            submission,
            workflow_id,
        )
        authority_task_id = authority.task_id
        authority_workflow_id = authority.external_workflow_id
        if authority_workflow_id != self._workflow_id(authority_task_id):
            raise ValueError("conflicting Temporal task authority")
        if (
            not owns_reservation
            and (authority.provenance or {}).get("start_outcome") == "accepted"
        ):
            return BackendSubmitResult(
                authority_task_id,
                "temporal",
                StartOutcome.ACCEPTED,
                "existing Temporal workflow reused",
            )
        if not owns_reservation:
            exists = await self._workflow_exists(client, authority_workflow_id)
            if exists is True:
                await asyncio.to_thread(
                    self._mark_start_outcome,
                    authority_task_id,
                    "accepted",
                )
                return BackendSubmitResult(
                    authority_task_id,
                    "temporal",
                    StartOutcome.ACCEPTED,
                    "existing Temporal workflow reused",
                )
            await asyncio.to_thread(
                self._mark_start_outcome,
                authority_task_id,
                "ambiguous",
            )
            return BackendSubmitResult(
                authority_task_id,
                "temporal",
                StartOutcome.AMBIGUOUS,
                "Temporal workflow start is ambiguous",
            )

        start = await self._start_workflow(client, submission, workflow_id)
        if start.accepted:
            await asyncio.to_thread(
                self._mark_start_outcome,
                submission.task_id,
                "accepted",
            )
            return BackendSubmitResult(
                submission.task_id,
                "temporal",
                StartOutcome.ACCEPTED,
                "Temporal workflow accepted",
            )
        await asyncio.to_thread(
            self._mark_start_outcome,
            submission.task_id,
            "ambiguous",
        )
        return BackendSubmitResult(
            submission.task_id,
            "temporal",
            StartOutcome.AMBIGUOUS,
            "Temporal workflow start is ambiguous",
        )

    async def _start_workflow(
        self,
        client: Any,
        submission: TaskSubmission,
        workflow_id: str,
    ) -> TemporalStartOutcome:
        from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

        try:
            await client.start_workflow(
                _WORKFLOW_TYPE,
                {
                    "task_id": submission.task_id,
                    "manifest_locator": _MANIFEST_LOCATOR,
                },
                id=workflow_id,
                task_queue=self._task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                request_eager_start=False,
                request_id=self._start_request_id(submission.task_id),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            exists = await self._workflow_exists(client, workflow_id)
            if exists is True:
                return TemporalStartOutcome(True, workflow_id, None)
            return TemporalStartOutcome(
                False,
                None,
                _START_AMBIGUOUS,
            )
        return TemporalStartOutcome(True, workflow_id, None)

    async def _workflow_exists(self, client: Any, workflow_id: str) -> bool | None:
        try:
            handle = client.get_workflow_handle(workflow_id)
            await handle.describe()
        except Exception as exc:
            if type(exc).__name__ in {
                "WorkflowNotFoundError",
                "RPCNotFoundError",
                "NotFoundError",
            }:
                return False
            return None
        return True

    async def get(self, task_id: str) -> TaskRecord:
        record = await asyncio.to_thread(self.store.get, task_id)
        if record.backend != "temporal" or record.status in TERMINAL_STATUSES:
            return record
        try:
            return await self.reconcile(task_id)
        except Exception:
            await asyncio.to_thread(
                self.store.mark_projection_stale_once,
                task_id,
            )
            return await asyncio.to_thread(self.store.get, task_id)

    async def reconcile(self, task_id: str) -> TaskRecord:
        record = await asyncio.to_thread(self.store.get, task_id)
        if record.backend != "temporal" or record.status in TERMINAL_STATUSES:
            return record
        workflow_id = record.external_workflow_id or self._workflow_id(task_id)
        client = await self.connect()
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        execution_status = _execution_status_name(description)
        if (
            record.status is TaskStatus.CANCEL_REQUESTED
            and execution_status == "RUNNING"
        ):
            await handle.cancel(
                reason="MedChat durable cancellation reconciliation",
                rpc_timeout=timedelta(seconds=10),
            )
            return await asyncio.to_thread(self.store.get, task_id)
        snapshot = await handle.query("task_snapshot")
        return await asyncio.to_thread(
            repair_temporal_projection,
            self.store,
            task_id,
            snapshot,
            execution_status=execution_status,
        )

    async def list(
        self,
        limit: int = 20,
        status: str | TaskStatus | None = None,
        task_type: str | None = None,
    ) -> list[TaskRecord]:
        records = await asyncio.to_thread(
            self.store.list,
            limit=200,
            status=status,
            task_type=task_type,
        )
        requested = max(1, min(200, int(limit)))
        return [item for item in records if item.backend == "temporal"][:requested]

    async def cancel(self, task_id: str, reason: str | None = None) -> TaskRecord:
        authority = await asyncio.to_thread(self.store.get, task_id)
        expected_workflow_id = self._workflow_id(task_id)
        if (
            authority.backend != "temporal"
            or authority.external_workflow_id != expected_workflow_id
        ):
            raise ValueError("conflicting Temporal task authority")
        record, caller_cancel, projection_error = await _complete_despite_cancellation(
            asyncio.to_thread(
                self.store.request_cancel,
                task_id,
                reason=reason,
            )
        )
        if projection_error is not None:
            raise projection_error
        if record.status in TERMINAL_STATUSES:
            if caller_cancel is not None:
                raise caller_cancel
            return record
        workflow_id = record.external_workflow_id or self._workflow_id(task_id)

        async def send_cancel() -> None:
            client = await self.connect()
            await client.get_workflow_handle(workflow_id).cancel(
                reason="MedChat task cancellation requested",
                rpc_timeout=timedelta(seconds=10),
            )

        try:
            _, rpc_cancel, rpc_error = await _complete_despite_cancellation(
                send_cancel()
            )
            caller_cancel = caller_cancel or rpc_cancel
            if rpc_error is not None:
                raise rpc_error
        except Exception:
            _, stale_cancel, _ = await _complete_despite_cancellation(
                asyncio.to_thread(
                    self.store.mark_projection_stale_once,
                    task_id,
                )
            )
            caller_cancel = caller_cancel or stale_cancel
        final, final_cancel, final_error = await _complete_despite_cancellation(
            asyncio.to_thread(self.store.get, task_id)
        )
        caller_cancel = caller_cancel or final_cancel
        if final_error is not None:
            raise final_error
        if caller_cancel is not None:
            raise caller_cancel
        return final

    async def health(self) -> BackendHealth:
        try:
            client = await self.connect()
            service_client = getattr(client, "service_client", None)
            check_health = getattr(service_client, "check_health", None)
            if check_health is None or await check_health() is not True:
                raise RuntimeError("Temporal service is not healthy")
            worker = await asyncio.to_thread(
                self.store.worker_health,
                "temporal",
                self._task_queue,
            )
        except Exception:
            return BackendHealth(
                "temporal",
                False,
                "health unavailable",
                {
                    "configured": True,
                    "durable_execution": True,
                    "process_restart_recovery": True,
                    "running": 0,
                },
            )
        available = worker.get("available") is True
        return BackendHealth(
            "temporal",
            available,
            "available" if available else "worker unavailable",
            {
                "configured": True,
                "durable_execution": True,
                "process_restart_recovery": True,
                "running": 0,
            },
        )

    async def close(self) -> None:
        self._client = None

    def _reserve_authority(
        self,
        submission: TaskSubmission,
        workflow_id: str,
    ) -> tuple[TaskRecord, bool]:
        decision = submission.payload.get("decision", {})
        provenance = {
            **(decision if isinstance(decision, dict) else {}),
            "start_outcome": "pending",
        }
        try:
            return self.store.create(
                submission.task_id,
                submission.task_type,
                submission.payload,
                backend="temporal",
                external_workflow_id=workflow_id,
                input_manifest_path=submission.input_manifest_path,
                provenance=provenance,
                phase=TaskPhase.STAGING,
                idempotency_digest=(
                    LocalTaskBackend.idempotency_digest(submission.idempotency_key)
                    if submission.idempotency_key is not None
                    else None
                ),
                submission_digest=submission.request_digest,
            ), True
        except sqlite3.IntegrityError:
            existing = self._existing_authority(submission)
            if existing is None and submission.idempotency_key is not None:
                digest = LocalTaskBackend.idempotency_digest(
                    submission.idempotency_key
                )
                try:
                    existing, request_digest = self.store.get_idempotency_authority(
                        digest
                    )
                except KeyError:
                    existing = None
                else:
                    if (
                        existing.backend != "temporal"
                        or existing.task_type != submission.task_type
                        or request_digest != submission.request_digest
                        or existing.external_workflow_id
                        != self._workflow_id(existing.task_id)
                    ):
                        raise TaskIdempotencyConflictError(
                            "idempotency authority conflict"
                        )
            if existing is None:
                raise
            return existing, False

    def _mark_start_outcome(self, task_id: str, outcome: str) -> TaskRecord:
        if outcome not in {"accepted", "ambiguous"}:
            raise ValueError("invalid Temporal start outcome")
        self.store.mark_temporal_start_outcome(
            task_id,
            outcome,
        )
        return self.store.get(task_id)

    def _existing_authority(self, submission: TaskSubmission) -> TaskRecord | None:
        try:
            record = self.store.get(submission.task_id)
        except KeyError:
            return None
        if (
            record.backend != "temporal"
            or record.task_type != submission.task_type
            or record.external_workflow_id != self._workflow_id(submission.task_id)
            or self.store.get_submission_digest(submission.task_id)
            != submission.request_digest
        ):
            raise ValueError("conflicting Temporal task authority")
        return record

    @staticmethod
    def _validate_submission(submission: TaskSubmission) -> None:
        if not isinstance(submission, TaskSubmission):
            raise TypeError("submission must be a TaskSubmission")
        if submission.task_type != "docking":
            raise ValueError("Temporal backend only accepts docking tasks")
        if submission.request_digest is None:
            raise ValueError("Temporal submission requires request digest")

    @staticmethod
    def _workflow_id(task_id: str) -> str:
        return f"medchat-docking-{task_id}"

    @staticmethod
    def _start_request_id(task_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"medchat-temporal-start:{task_id}"))


def _execution_status_name(description: Any) -> str:
    status = getattr(description, "status", None)
    name = getattr(status, "name", status)
    return str(name).upper() if name is not None else "UNKNOWN"


async def _complete_despite_cancellation(
    awaitable: Awaitable[Any],
) -> tuple[Any, asyncio.CancelledError | None, Exception | None]:
    """Finish one authority-changing operation before propagating cancellation."""

    operation = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(operation), cancellation, None
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
            if operation.done():
                try:
                    return operation.result(), cancellation, None
                except Exception as error:
                    return None, cancellation, error
        except Exception as error:
            return None, cancellation, error


__all__ = ["TemporalStartOutcome", "TemporalTaskBackend"]
