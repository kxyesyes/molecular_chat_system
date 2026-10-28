"""Real local demo asset route contract."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.requests import Request

from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
from src.web.routes import docking_routes
from src.web.routes.docking_routes import setup_docking_routes


if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _app(tmp_path):
    app = FastAPI()
    app.add_middleware(
        AgentSessionMiddleware,
        store=AgentSessionStore(tmp_path / "sessions.sqlite"),
    )
    setup_docking_routes(
        app,
        docking_service=SimpleNamespace(work_dir=str(tmp_path / "work")),
        task_runtime=None,
    )
    return app


def _request(app):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/docking/demo/pde4a-apo",
        "raw_path": b"/api/docking/demo/pde4a-apo",
        "query_string": b"",
        "headers": [],
        "scheme": "https",
        "server": ("localhost", 443),
        "client": ("127.0.0.1", 1),
        "root_path": "",
        "app": app,
        "agent_session_id": "demo-test-session",
    }
    return Request(scope)


def _endpoint(app):
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/api/docking/demo/pde4a-apo"
    )


def test_pde4a_apo_demo_returns_the_registered_local_6fg5_asset(tmp_path):
    app = _app(tmp_path)
    response = _endpoint(app)(_request(app))

    assert response.media_type == "chemical/x-pdb"
    assert b"6FG5" in response.body[:1200]
    assert b"ATOM  " in response.body


def test_pde4a_apo_demo_does_not_fallback_when_registered_asset_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        docking_routes,
        "DEMO_PDE4A_APO_PATH",
        Path(tmp_path) / "missing-QXQO-or-6FG5.pdb",
    )
    app = _app(tmp_path)
    try:
        _endpoint(app)(_request(app))
    except Exception as error:
        response = error
    else:
        raise AssertionError("missing demo asset should be rejected")

    assert getattr(response, "status_code", None) == 404
    assert "示例" in str(response.detail)
