"""Small real FastAPI app: no models, tools, global app, or execution runtime."""
import asyncio
import inspect
import sqlite3
import threading

from fastapi import FastAPI
import httpx
import pytest

from src.web.routes.agent_run_routes import setup_agent_run_routes
from test_result_details_store import OWNER, TRACE, payload, publish, store


def app_for(store):
    app = FastAPI()
    @app.middleware("http")
    async def session_scope(request, call_next):
        # Test-only adapter for the existing trusted session scope boundary.
        request.scope["agent_session_id"] = request.headers.get("x-test-session")
        return await call_next(request)
    setup_agent_run_routes(app, store)
    return app


def test_result_details_route_is_registered_as_sync_handler(store):
    app = app_for(store)
    matches = [r for r in app.routes if r.path == "/api/agent/runs/{trace_id}/result-details/{view_id}"]
    assert len(matches) == 1, "result detail read route is not implemented"
    assert not inspect.iscoroutinefunction(matches[0].endpoint)
    assert matches[0].methods == {"GET"}


def test_owner_success_uses_standard_envelope_private_no_store_and_threadpool(store, monkeypatch):
    import src.web.routes.agent_run_routes as routes
    ref = publish(store)
    real = routes.read_result_details
    threads = []
    def track(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs)
    monkeypatch.setattr(routes, "read_result_details", track)
    async def run():
        loop_thread = threading.get_ident()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(store)),
                                    base_url="http://test") as client:
            response = await client.get(f"/api/agent/runs/{TRACE}/result-details/{ref['view_id']}",
                params={"revision": ref["revision"]}, headers={"x-test-session": OWNER})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        result = response.json()
        assert result["success"] is True and result["code"] == "OK" and result["request_id"]
        assert result["data"] == {**ref, "payload": payload()}
        assert threads and all(thread != loop_thread for thread in threads)
    asyncio.run(run())


def test_missing_foreign_invalid_and_no_session_have_identical_private_404(store):
    ref = publish(store)
    async def run():
        bodies = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(store)),
                                    base_url="http://test") as client:
            cases = [(TRACE, ref["view_id"], ref["revision"], "foreign"),
                (TRACE, ref["view_id"], ref["revision"], None),
                ("missing", ref["view_id"], ref["revision"], OWNER),
                (TRACE, "invalid", ref["revision"], OWNER),
                (TRACE, ref["view_id"], "invalid", OWNER),
                (TRACE, ref["view_id"], None, OWNER)]
            for trace, view, revision, owner in cases:
                response = await client.get(f"/api/agent/runs/{trace}/result-details/{view}",
                    params={} if revision is None else {"revision": revision},
                    headers={} if owner is None else {"x-test-session": owner})
                assert response.status_code == 404
                assert response.headers["cache-control"] == "private, no-store"
                body = response.json()
                assert body["success"] is False and body.pop("request_id")
                bodies.append(body)
        assert all(body == bodies[0] for body in bodies)
    asyncio.run(run())


def test_database_failure_is_same_non_cacheable_404_not_internal_error(store, monkeypatch):
    ref = publish(store)
    def fail():
        raise sqlite3.OperationalError("injected private database failure")
    monkeypatch.setattr(store, "_connect", fail)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(store)),
                                    base_url="http://test") as client:
            response = await client.get(f"/api/agent/runs/{TRACE}/result-details/{ref['view_id']}",
                params={"revision": ref["revision"]}, headers={"x-test-session": OWNER})
        assert response.status_code == 404
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["code"] == "RESULT_DETAILS_NOT_FOUND"
        assert "injected" not in response.text
    asyncio.run(run())
