from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from src.web.api_response import api_error, api_success

from .manager import get_task_manager


def setup_task_routes(app: FastAPI) -> None:
    @app.get("/api/tasks")
    async def list_tasks(limit: int = 20, status: str | None = None, task_type: str | None = None):
        manager = get_task_manager()
        records = manager.list(limit=limit, status=status, task_type=task_type)
        return api_success([record.to_dict() for record in records])

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        manager = get_task_manager()
        try:
            record = manager.get(task_id)
        except KeyError:
            return api_error("TASK_NOT_FOUND", "任务不存在", status_code=404)
        return api_success(record.to_dict())

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
        return api_success(record.to_dict(), message="任务已提交")
