"""Owned task projections; no browser access without durable ownership authority.

Filter owners before paging, exclude cross-database ID collisions and refill
bounded pages. Durable event reads authorize without triggering a refresh.
"""

from __future__ import annotations

import asyncio
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


def _session_id(request: Request) -> str:
    session_id = request.scope.get("agent_session_id")
    if type(session_id) is not str or not session_id.strip():
        raise HTTPException(401, "Browser session required")
    return session_id


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

    def manager_task_record(manager, task_id: str):
        try:
            record = manager.get(task_id)
        except KeyError:
            return None
        return record

    def same_database(first, second):
        return first is second or first.db_path.samefile(second.db_path)

    def owned_store_list(store, other_store, limit, status, task_type, session_id,
                         *, exclude_task_type=None):
        records = []
        offset = 0
        while offset < _ADAPTER_SCAN_LIMIT:
            page = store.list(
                limit=_ADAPTER_PAGE_SIZE, offset=offset, status=status, task_type=task_type,
                exclude_task_type=exclude_task_type,
                require_owner=True, owner_session_id=session_id,
            )
            for record in page:
                if other_store is not None:
                    try:
                        other_store.get(record.task_id)
                    except KeyError:
                        pass
                    else:
                        continue
                records.append(record)
            if len(records) >= limit or len(page) < _ADAPTER_PAGE_SIZE:
                return records[:limit]
            offset += len(page)
        raise HTTPException(503, "Task runtime pagination unavailable")

    async def runtime_list(
        request: Request,
        limit: int,
        status: str | None,
        task_type: str | None,
    ):
        session_id = _session_id(request)
        limit = max(1, min(200, limit))
        task_type = task_type or None
        manager = get_task_manager()
        runtime = current_task_runtime()
        store = getattr(runtime, "store", None)
        if runtime is not None and not isinstance(store, TaskStore):
            raise HTTPException(503, "Task ownership storage unavailable")
        separate = store is not None and not await asyncio.to_thread(same_database, store, manager.store)
        records = await asyncio.to_thread(
            owned_store_list, manager.store, store if separate else None,
            limit, status, task_type, session_id,
        )
        if separate and task_type != "agent_workflow":
            records.extend(await asyncio.to_thread(
                owned_store_list, store, manager.store, limit, status, task_type, session_id,
                exclude_task_type="agent_workflow",
            ))
        unique = {record.task_id: record for record in records}
        return sorted(
            unique.values(),
            key=lambda record: (record.updated_at or "", record.task_id),
            reverse=True,
        )[:limit]

    async def runtime_get(request: Request, task_id: str, *, refresh: bool = True):
        session_id = _session_id(request)
        manager = get_task_manager()
        runtime = current_task_runtime()
        manager_record = await asyncio.to_thread(manager_task_record, manager, task_id)
        if manager_record is not None:
            runtime_store = getattr(runtime, "store", None)
            if isinstance(runtime_store, TaskStore) and not await asyncio.to_thread(
                same_database, runtime_store, manager.store
            ):
                try:
                    await asyncio.to_thread(runtime_store.get, task_id)
                except KeyError:
                    pass
                else:
                    # The ID exists in both stores. Never let a caller borrow
                    # the source selected by a collision.
                    raise KeyError(task_id)
            agent_record = await asyncio.to_thread(
                manager.store.get_owned, task_id, session_id
            )
            # Return the source together with the authorized record. Events and
            # cancel must use that same source, not re-resolve by a colliding ID.
            return agent_record, None, manager
        if runtime is not None:
            store = getattr(runtime, "store", None)
            if isinstance(store, TaskStore):
                projection = await asyncio.to_thread(store.get_owned, task_id, session_id)
                if projection.task_type == "agent_workflow":
                    # A Temporal get can refresh/mutate the projection. Reject
                    # unknown Agent records before invoking any backend action.
                    raise KeyError(task_id)
                if not refresh:
                    return projection, runtime, manager
            else:
                raise HTTPException(503, "Task ownership storage unavailable")
            record = await runtime.get(task_id)
        else:
            record = await asyncio.to_thread(manager.store.get_owned, task_id, session_id)
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
                events = await asyncio.to_thread(
                    manager.store.events, task_id,
                    owner_session_id=_session_id(request), require_owner=True,
                )
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
                    owner_session_id=_session_id(request),
                )
        except KeyError:
            return api_error("TASK_NOT_FOUND", "Task not found", status_code=404)
        return api_success(
            record.to_public_dict(),
            message="Cancellation requested",
        )

    @app.post("/api/tasks/demo")
    async def submit_demo_task(request: Request):
        session_id = _session_id(request)
        payload: dict[str, Any] = await request.json()

        def handler(task_payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "message": "demo task completed",
                "echo": task_payload,
                "artifacts": [],
            }

        record = get_task_manager().submit("demo", payload, handler, owner_session_id=session_id)
        return api_success(record.to_public_dict(), message="Task submitted")
