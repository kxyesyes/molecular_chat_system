from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Request

from src.agent.supervisor import SupervisorAgent
from src.task_runtime import get_task_manager
from src.web.api_response import api_error, api_success


def setup_agent_workflow_routes(
    app: FastAPI,
    supervisor_factory: Callable[[], SupervisorAgent] | None = None,
) -> None:
    create_supervisor = supervisor_factory or SupervisorAgent

    @app.post("/api/agent/workflows/plan")
    async def plan_workflow(request: Request):
        payload: dict[str, Any] = await request.json()
        query = str(payload.get("query") or "").strip()
        if not query:
            return api_error("QUERY_REQUIRED", "请输入任务目标", status_code=422)

        supervisor = create_supervisor()
        plan = supervisor.plan(
            query=query,
            skill_name=payload.get("skill_name"),
            metadata=payload.get("metadata") or {},
        )
        return api_success(plan, message="工作流计划已生成")

    @app.post("/api/agent/workflows/run")
    async def run_workflow(request: Request):
        payload: dict[str, Any] = await request.json()
        query = str(payload.get("query") or "").strip()
        if not query:
            return api_error("QUERY_REQUIRED", "请输入任务目标", status_code=422)

        def handler(task_payload: dict[str, Any]) -> dict[str, Any]:
            supervisor = create_supervisor()
            return supervisor.run(
                query=str(task_payload.get("query") or ""),
                skill_name=task_payload.get("skill_name"),
                metadata=task_payload.get("metadata") or {},
            )

        record = get_task_manager().submit(
            task_type="agent_workflow",
            payload={
                "query": query,
                "skill_name": payload.get("skill_name"),
                "metadata": payload.get("metadata") or {},
            },
            handler=handler,
        )
        return api_success(record.to_public_dict(), message="Agent 工作流任务已提交")
