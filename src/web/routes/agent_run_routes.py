"""Owner-scoped, read-only Agent run recovery and audit endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from src.agent.persistence.redaction import redact_sensitive
from src.web.api_response import api_error, api_success


def _session(request: Request) -> str | None:
    value = request.scope.get("agent_session_id")
    return value if isinstance(value, str) and value else None


def _owned_run(store, trace_id: str, session_id: str) -> dict[str, Any] | None:
    record = store.get_run(trace_id)
    if not isinstance(record, dict):
        return None
    if record.get("session_id") != session_id or record.get("user_id") != session_id:
        return None
    return record


def _public_run(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata")
    safe_metadata = {}
    if isinstance(metadata, dict):
        # Continuation snapshots contain scientific observations and are used
        # by the WebSocket CAS boundary; never expose them as a refresh DTO.
        for key in ("web_request", "decision_loop", "ordinary_admission"):
            if key in metadata:
                safe_metadata[key] = redact_sensitive(metadata[key])
    return {
        "trace_id": record.get("trace_id"),
        "status": record.get("status"),
        "skill_name": record.get("skill_name"),
        "workflow_version": record.get("workflow_version"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "metadata": safe_metadata,
    }


def _public_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    return [redact_sensitive(event) for event in events if isinstance(event, dict)]


def setup_agent_run_routes(app: FastAPI, store) -> None:
    @app.get("/api/agent/runs/{trace_id}")
    async def get_agent_run(trace_id: str, request: Request):
        session_id = _session(request)
        if not session_id:
            return api_error("RUN_NOT_FOUND", "运行记录不存在", status_code=404)
        record = _owned_run(store, trace_id, session_id)
        if record is None:
            return api_error("RUN_NOT_FOUND", "运行记录不存在", status_code=404)
        return api_success(_public_run(record), message="Agent 运行快照")

    @app.get("/api/agent/runs/{trace_id}/events")
    async def get_agent_run_events(trace_id: str, request: Request):
        session_id = _session(request)
        if not session_id:
            return api_error("RUN_NOT_FOUND", "运行记录不存在", status_code=404)
        record = _owned_run(store, trace_id, session_id)
        if record is None:
            return api_error("RUN_NOT_FOUND", "运行记录不存在", status_code=404)
        return api_success({
            "trace_id": trace_id,
            "events": _public_events(store.get_events(trace_id)),
        }, message="Agent 运行事件")
