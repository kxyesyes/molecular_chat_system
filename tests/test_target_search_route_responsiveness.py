"""Route contracts without downloads, databases, or production structures."""
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.target_search import routes
from src.target_search.downloader import StructureDownloadError


def test_send_to_docking_preparation_runs_off_event_loop(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    thread_ids = []

    def prepare(structure_id):
        thread_ids.append(threading.get_ident())
        started.set()
        assert release.wait(2), "preparation was not released"
        return {"status": "success", "structure_db_id": structure_id}

    monkeypatch.setattr(routes, "TargetSearchService", lambda: SimpleNamespace(send_to_docking=prepare))
    app = FastAPI()
    routes.setup_target_search_routes(app)

    async def exercise():
        loop_thread = threading.get_ident()
        # Safety timer prevents the original blocking implementation hanging the test.
        safety = threading.Timer(1, release.set)
        safety.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                pending = asyncio.create_task(client.post("/api/target-db/structures/7/send-to-docking"))
                assert await asyncio.to_thread(started.wait, 2)
                responsive = not release.is_set()
                release.set()
                response = await pending
            assert responsive, "structure preparation blocked other event-loop work"
            assert thread_ids == [thread_ids[0]] and thread_ids[0] != loop_thread
            assert response.status_code == 200
            assert response.json() == {"status": "success", "structure_db_id": 7}
        finally:
            release.set()
            safety.cancel()
            safety.join()

    asyncio.run(exercise())


@pytest.mark.parametrize("error, expected_status", [(ValueError, 404), (StructureDownloadError, 502), (RuntimeError, 500)])
def test_send_to_docking_preserves_error_mapping(monkeypatch, error, expected_status):
    def fail(_structure_id):
        raise error("controlled failure")
    monkeypatch.setattr(routes, "TargetSearchService", lambda: SimpleNamespace(send_to_docking=fail))
    app = FastAPI()
    routes.setup_target_search_routes(app)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/target-db/structures/7/send-to-docking")
        assert response.status_code == expected_status
        if expected_status == 502:
            assert response.json() == {"status": "error", "message": "controlled failure", "structure_db_id": 7}
        else:
            assert response.json() == {"detail": "controlled failure"}
    asyncio.run(exercise())
