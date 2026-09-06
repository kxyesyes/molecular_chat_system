"""Repair SQLite task projections from Temporal workflow snapshots only."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from src.task_runtime.errors import TaskErrorCode
from src.task_runtime.models import TaskPhase, TaskRecord, TaskStatus
from src.task_runtime.store import TERMINAL_STATUSES, TaskStore


_SNAPSHOT_STATUSES = {
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.CANCEL_REQUESTED.value,
    TaskStatus.SUCCEEDED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELED.value,
    TaskStatus.TIMED_OUT.value,
}


def repair_temporal_projection(
    store: TaskStore,
    task_id: str,
    snapshot: Any,
    *,
    execution_status: str,
) -> TaskRecord:
    """CAS-apply one safe lifecycle snapshot; never execute scientific work."""

    if not isinstance(store, TaskStore):
        raise TypeError("store must be a TaskStore")
    if not isinstance(snapshot, dict) or snapshot.get("task_id") != task_id:
        raise ValueError("invalid Temporal task snapshot")
    status_text = snapshot.get("status")
    if status_text not in _SNAPSHOT_STATUSES:
        raise ValueError("invalid Temporal task status")
    if type(execution_status) is not str or not execution_status:
        raise ValueError("invalid Temporal execution status")

    record = store.get(task_id)
    if record.backend != "temporal" or record.status in TERMINAL_STATUSES:
        return record
    phase = _safe_phase(snapshot.get("phase"))
    attempt = _safe_attempt(snapshot.get("attempt"))
    target = TaskStatus(status_text)
    _validate_execution_status(target, execution_status)
    terminal_projection = (
        _terminal_projection(snapshot, target)
        if target in TERMINAL_STATUSES
        else None
    )

    if target is TaskStatus.QUEUED:
        if phase is not None and record.phase != phase:
            store.annotate_nonterminal(task_id, phase=phase)
    elif target is TaskStatus.RUNNING:
        store.project_temporal_running(
            task_id,
            phase=phase,
            attempt=attempt,
        )
    elif target is TaskStatus.CANCEL_REQUESTED:
        store.request_cancel(task_id)
    elif target is TaskStatus.CANCELED:
        if record.status is TaskStatus.QUEUED:
            store.project_temporal_running(
                task_id,
                phase=phase,
                attempt=attempt,
            )
        store.request_cancel(task_id)
        store.project_temporal_terminal(
            task_id,
            TaskStatus.CANCELED,
            error_code=terminal_projection.get("error_code"),
        )
    else:
        if record.status is TaskStatus.QUEUED:
            store.project_temporal_running(
                task_id,
                phase=phase,
                attempt=attempt,
            )
        current = store.get(task_id)
        if current.status in {TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED}:
            if current.status is TaskStatus.RUNNING:
                store.project_temporal_running(
                    task_id,
                    phase=phase,
                    attempt=attempt,
                )
            store.project_temporal_terminal(
                task_id,
                target,
                result=terminal_projection.get("result"),
                artifacts=terminal_projection.get("artifacts"),
                error_code=terminal_projection.get("error_code"),
                warnings=terminal_projection.get("warnings"),
                provenance=terminal_projection.get("provenance"),
            )
    repaired = store.get(task_id)
    if repaired.status is not target:
        raise RuntimeError("Temporal projection conflict")
    return repaired


async def reconcile_temporal_tasks(
    store: TaskStore,
    backend: Any,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Scan temporal nonterminal rows and invoke only backend reconciliation."""

    requested = max(1, min(1000, int(limit)))
    scanned = repaired = stale = 0
    after_task_id: str | None = None
    while scanned < requested:
        records = await asyncio.to_thread(
            store.list_temporal_reconcilable,
            limit=min(200, requested - scanned),
            after_task_id=after_task_id,
        )
        if not records:
            break
        after_task_id = records[-1].task_id
        for record in records:
            scanned += 1
            try:
                await backend.reconcile(record.task_id)
            except Exception:
                stale += 1
                await asyncio.to_thread(
                    store.mark_projection_stale_once,
                    record.task_id,
                )
            else:
                repaired += 1
            if scanned >= requested:
                break
    return {"scanned": scanned, "repaired": repaired, "stale": stale}


def _safe_phase(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return TaskPhase(value).value
    except (TypeError, ValueError):
        raise ValueError("invalid Temporal task phase") from None


def _safe_attempt(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("invalid Temporal task attempt")
    return value


def _safe_error_code(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return TaskErrorCode(value).value
    except (TypeError, ValueError):
        return TaskErrorCode.TASK_PROJECTION_FAILED.value


def _terminal_projection(
    snapshot: dict[str, Any],
    target: TaskStatus,
) -> dict[str, Any]:
    projection = snapshot.get("terminal_projection")
    if not isinstance(projection, dict) or projection.get("status") != target.value:
        raise ValueError("invalid Temporal terminal projection")
    if target is TaskStatus.SUCCEEDED:
        required = {"result", "artifacts", "warnings", "provenance"}
        if not required.issubset(projection):
            raise ValueError("incomplete Temporal terminal projection")
        if not isinstance(projection.get("result"), dict):
            raise ValueError("invalid Temporal terminal projection result")
    safe = {
        key: projection[key]
        for key in (
            "result",
            "artifacts",
            "warnings",
            "provenance",
        )
        if key in projection
    }
    safe["error_code"] = _safe_error_code(
        projection.get("error_code", snapshot.get("error_code"))
    )
    return safe


def _validate_execution_status(target: TaskStatus, execution_status: str) -> None:
    normalized = execution_status.upper()
    if target is TaskStatus.CANCELED:
        expected = {"CANCELED"}
    elif target in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
    }:
        expected = {"COMPLETED"}
    else:
        expected = {"RUNNING"}
    if normalized not in expected:
        raise ValueError("Temporal snapshot execution status mismatch")


async def _main(limit: int) -> dict[str, int]:
    from src.task_runtime.backends.temporal import TemporalTaskBackend
    from src.task_runtime.config import TaskRuntimeConfig

    config = TaskRuntimeConfig.from_env()
    store = TaskStore()
    backend = TemporalTaskBackend(
        store,
        address=config.temporal_address,
        namespace=config.temporal_namespace,
        task_queue=config.docking_queue,
    )
    return await reconcile_temporal_tasks(store, backend, limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair Temporal task projections")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    summary = asyncio.run(_main(args.limit))
    print(
        "temporal_reconciliation "
        f"scanned={summary['scanned']} "
        f"repaired={summary['repaired']} stale={summary['stale']}"
    )


if __name__ == "__main__":
    main()


__all__ = ["reconcile_temporal_tasks", "repair_temporal_projection"]
