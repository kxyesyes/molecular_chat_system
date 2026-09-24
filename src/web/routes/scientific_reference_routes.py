"""Small protected protocol: uniform failures, bounded input, worker storage."""
import json

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from src.web.api_response import api_error, api_success


def setup_scientific_reference_routes(app, service):
    async def handle(request, operation):
        failure = lambda: api_error("REFERENCE_UNAVAILABLE", "科研引用不可用，请重新选择候选。", status_code=404)
        session = request.scope.get("agent_session_id")
        if type(session) is not str or not session:
            return failure()
        try:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 16384:
                    return failure()
                body.extend(chunk)
            payload = json.loads(body)
            result = await run_in_threadpool(operation, payload, session_id=session)
            if not result:
                return failure()
            return api_success({"confirmed": True} if result is True else result)
        except Exception:
            return failure()

    @app.post("/api/agent/workflows/references/confirm")
    async def confirm(request: Request):
        return await handle(request, service.confirm)

    @app.post("/api/agent/workflows/references/restore")
    async def restore(request: Request):
        return await handle(request, service.restore)
