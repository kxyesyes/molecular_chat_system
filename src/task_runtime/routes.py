from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request

from src.web.api_response import api_error, api_success

from .manager import get_task_manager
from .models import TaskStatus, sanitize_task_message


_TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELED,
    TaskStatus.TIMED_OUT,
}


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

    async def runtime_list(
        limit: int,
        status: str | None,
        task_type: str | None,
    ):
        runtime = current_task_runtime()
        if runtime is not None:
            return await runtime.list(
                limit=limit,
                status=status,
                task_type=task_type,
            )
        return await asyncio.to_thread(
            get_task_manager().list,
            limit=limit,
            status=status,
            task_type=task_type,
        )

    async def runtime_get(task_id: str):
        runtime = current_task_runtime()
        if runtime is not None:
            return await runtime.get(task_id)
        return await asyncio.to_thread(get_task_manager().get, task_id)

    @app.get("/api/tasks")
    async def list_tasks(
        limit: int = 20,
        status: str | None = None,
        task_type: str | None = None,
    ):
        records = await runtime_list(limit, status, task_type)
        return api_success([record.to_public_dict() for record in records])

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        try:
            record = await runtime_get(task_id)
        except KeyError:
            return api_error("TASK_NOT_FOUND", "Task not found", status_code=404)
        return api_success(record.to_public_dict())

    @app.get("/api/tasks/{task_id}/events")
    async def get_task_events(task_id: str):
        try:
            runtime = current_task_runtime()
            if runtime is not None:
                events = await runtime.events(task_id)
            else:
                manager = get_task_manager()
                await asyncio.to_thread(manager.get, task_id)
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
        payload: dict[str, Any] = Body(default_factory=dict),
    ):
        raw_reason = payload.get("reason")
        if raw_reason is not None and not isinstance(raw_reason, str):
            return api_error(
                "INVALID_CANCEL_REASON",
                "Invalid cancellation reason",
                status_code=422,
            )
        reason = sanitize_task_message(raw_reason or "user request")[:256]
        try:
            existing = await runtime_get(task_id)
            if existing.status in _TERMINAL_STATUSES:
                return api_success(existing.to_public_dict())
            runtime = current_task_runtime()
            if runtime is not None:
                record = await runtime.cancel(task_id, reason)
            else:
                manager = get_task_manager()
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
