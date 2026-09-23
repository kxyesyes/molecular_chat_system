"""Shared task projections; only manager-owned Agent records are browser-visible.

Storeless adapters can add a keyword ``offset`` to ``list`` without changing the
production runtime API. Paged results must be globally ordered by descending
(updated_at, task_id); advance by actual row count until enough visible rows or
an empty page. Legacy single-page results are sorted in full before truncation.
Requests use at most 200 rows and scan at most 10,000 candidates. Filtered legacy
underfill, repeated/invalid pages, or scan exhaustion return 503, not an uncertain
partial result. Durable event reads check projection authority without refresh.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request

from src.web.api_response import api_error, api_success

from .manager import get_task_manager
from .models import TaskStatus, sanitize_task_message
from .store import TaskStore


_TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELED,
    TaskStatus.TIMED_OUT,
}
_ADAPTER_PAGE_SIZE = 200
_ADAPTER_SCAN_LIMIT = 10_000


def setup_task_routes(app: FastAPI, task_runtime=None) -> None:
    def current_task_runtime():
        if task_runtime is None:
            return None
        try:
            return task_runtime() if callable(task_runtime) else task_runtime
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Task runtime unavailable",
            ) from exc

    def manager_agent_record(manager, task_id: str):
        try:
            record = manager.get(task_id)
        except KeyError:
            return None
        return record if record.task_type == "agent_workflow" else None

    def non_agent_records(manager, records):
        # Manager Agent IDs reserve their namespace, even for another session.
        # Never substitute a colliding runtime row for a hidden manager task.
        return [
            record for record in records
            if record.task_type != "agent_workflow"
            and manager_agent_record(manager, record.task_id) is None
        ]

    def runtime_store_list(manager, store, limit, status, task_type):
        # The durable runtime lists SQLite projections directly. Filter in SQL
        # before paging, then refill pages when cross-store ID collisions occur.
        records = []
        offset = 0
        while len(records) < limit:
            page = store.list(
                limit=200, offset=offset, status=status, task_type=task_type,
                exclude_task_type="agent_workflow",
            )
            records.extend(non_agent_records(manager, page))
            if len(page) < 200:
                break
            offset += len(page)
        return records[:limit]

    async def runtime_adapter_list(manager, runtime, limit, status, task_type):
        """Read bounded adapter projections under the module's paging contract."""
        try:
            offset_parameter = inspect.signature(runtime.list).parameters.get("offset")
        except (TypeError, ValueError):
            offset_parameter = None
        can_page = offset_parameter is not None and offset_parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY,
        )
        records = []
        seen = set()
        offset = 0
        while offset < _ADAPTER_SCAN_LIMIT:
            kwargs = {"offset": offset} if can_page else {}
            page = await runtime.list(
                limit=_ADAPTER_PAGE_SIZE, status=status, task_type=task_type, **kwargs
            )
            if not page:
                return records[:limit]
            ids = {record.task_id for record in page}
            if ids & seen or len(ids) != len(page) or len(page) > _ADAPTER_PAGE_SIZE:
                raise HTTPException(503, "Task runtime pagination unavailable")
            seen.update(ids)
            visible = await asyncio.to_thread(non_agent_records, manager, page)
            records.extend(visible)
            records.sort(
                key=lambda record: (record.updated_at or "", record.task_id),
                reverse=True,
            )
            if len(records) >= limit:
                return records[:limit]
            if not can_page:
                if len(visible) != len(page):
                    raise HTTPException(503, "Task runtime pagination unavailable")
                return records
            offset += len(page)
        raise HTTPException(503, "Task runtime pagination unavailable")

    async def runtime_list(
        request: Request,
        limit: int,
        status: str | None,
        task_type: str | None,
    ):
        limit = max(1, min(200, limit))
        task_type = task_type or None
        manager = get_task_manager()
        runtime = current_task_runtime()
        records = []
        if runtime is None or task_type in (None, "agent_workflow"):
            records = await asyncio.to_thread(
                manager.store.list,
                limit=limit, status=status,
                task_type="agent_workflow" if runtime is not None else task_type,
                enforce_agent_ownership=True,
                agent_session_id=request.scope.get("agent_session_id"),
            )
        if runtime is not None and task_type != "agent_workflow":
            store = getattr(runtime, "store", None)
            if isinstance(store, TaskStore):
                other_records = await asyncio.to_thread(
                    runtime_store_list, manager, store, limit, status, task_type
                )
            else:
                other_records = await runtime_adapter_list(
                    manager, runtime, limit, status, task_type
                )
            records.extend(other_records)
        unique = {record.task_id: record for record in records}
        return sorted(
            unique.values(),
            key=lambda record: (record.updated_at or "", record.task_id),
            reverse=True,
        )[:limit]

    async def runtime_get(request: Request, task_id: str, *, refresh: bool = True):
        manager = get_task_manager()
        agent_record = await asyncio.to_thread(manager_agent_record, manager, task_id)
        if agent_record is not None:
            session_id = request.scope.get("agent_session_id")
            owner = await asyncio.to_thread(manager.store.get_agent_owner, task_id)
            if not session_id or not owner or session_id != owner:
                raise KeyError(task_id)
            # Return the source together with the authorized record. Events and
            # cancel must use that same source, not re-resolve by a colliding ID.
            return agent_record, None, manager
        runtime = current_task_runtime()
        if runtime is not None:
            store = getattr(runtime, "store", None)
            if isinstance(store, TaskStore):
                projection = await asyncio.to_thread(store.get, task_id)
                if projection.task_type == "agent_workflow":
                    # A Temporal get can refresh/mutate the projection. Reject
                    # unknown Agent records before invoking any backend action.
                    raise KeyError(task_id)
                if not refresh:
                    return projection, runtime, manager
            record = await runtime.get(task_id)
        else:
            record = await asyncio.to_thread(manager.get, task_id)
        if record.task_type == "agent_workflow":
            raise KeyError(task_id)
        return record, runtime, manager

    @app.get("/api/tasks")
    async def list_tasks(
        request: Request,
        limit: int = 20,
        status: str | None = None,
        task_type: str | None = None,
    ):
        records = await runtime_list(request, limit, status, task_type)
        return api_success([record.to_public_dict() for record in records])

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str, request: Request):
        try:
            record, _, _ = await runtime_get(request, task_id)
        except KeyError:
            return api_error("TASK_NOT_FOUND", "Task not found", status_code=404)
        return api_success(record.to_public_dict())

    @app.get("/api/tasks/{task_id}/events")
    async def get_task_events(task_id: str, request: Request):
        try:
            _, runtime, manager = await runtime_get(request, task_id, refresh=False)
            if runtime is not None:
                events = await runtime.events(task_id)
            else:
                events = await asyncio.to_thread(manager.store.events, task_id)
        except KeyError:
            return api_error("TASK_NOT_FOUND", "Task not found", status_code=404)
        return api_success(
            [
                {
                    "event_id": event.event_id,
                    "task_id": event.task_id,
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "is_terminal": event.is_terminal,
                    "created_at": event.created_at,
                }
                for event in events
            ]
        )

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str,
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
    ):
        try:
            existing, runtime, manager = await runtime_get(request, task_id)
            raw_reason = payload.get("reason")
            if raw_reason is not None and not isinstance(raw_reason, str):
                return api_error(
                    "INVALID_CANCEL_REASON",
                    "Invalid cancellation reason",
                    status_code=422,
                )
            reason = sanitize_task_message(raw_reason or "user request")[:256]
            if existing.status in _TERMINAL_STATUSES:
                return api_success(existing.to_public_dict())
            if runtime is not None:
                record = await runtime.cancel(task_id, reason)
            else:
                record = await asyncio.to_thread(
                    manager.store.request_cancel,
                    task_id,
                    reason=reason,
                )
        except KeyError:
            return api_error("TASK_NOT_FOUND", "Task not found", status_code=404)
        return api_success(
            record.to_public_dict(),
            message="Cancellation requested",
        )

    @app.post("/api/tasks/demo")
    async def submit_demo_task(request: Request):
        payload: dict[str, Any] = await request.json()

        def handler(task_payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "message": "demo task completed",
                "echo": task_payload,
                "artifacts": [],
            }

        record = get_task_manager().submit("demo", payload, handler)
        return api_success(record.to_public_dict(), message="Task submitted")
