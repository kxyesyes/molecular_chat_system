"""Focused security regressions for scientific HTTP boundaries."""

from __future__ import annotations

import math
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_docking_limits_reject_non_finite_manual_center():
    from src.web.routes import api_routes

    with pytest.raises(Exception) as error:
        api_routes._validate_docking_limits(
            center_x=math.nan,
            center_y=0.0,
            center_z=0.0,
            size_x=20.0,
            size_y=20.0,
            size_z=20.0,
            exhaustiveness=8,
            num_modes=10,
            energy_range=3.0,
        )
    assert "center" in error.value.detail


def test_completed_admet_future_does_not_leave_cancel_marker():
    from src.web.routes.admet_routes import _AdmetWorker

    support = SimpleNamespace(os=os, ThreadPoolExecutor=__import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor)
    worker = _AdmetWorker(support, isolate=False)

    class CompletedFuture:
        _admet_job_id = "already-complete"

        def done(self):
            return True

        def cancel(self):
            return False

    try:
        assert worker.cancel(CompletedFuture()) is False
        assert "already-complete" not in worker._cancelled_jobs
    finally:
        worker.close()


def test_reverse_target_internal_failure_is_not_returned_to_client(monkeypatch):
    from src.web.routes import api_routes

    fake_predictor = SimpleNamespace(
        predict=lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("secret-token /srv/private/model.bin")
        )
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.reverse_target.predictor",
        SimpleNamespace(get_predictor=lambda: fake_predictor),
    )

    app = FastAPI()

    @app.middleware("http")
    async def browser_session(request, call_next):
        request.scope["agent_session_id"] = "security-test"
        return await call_next(request)

    api_routes.setup_api_routes(app)
    with TestClient(app) as client:
        response = client.post("/api/reverse_target/predict", data={"smiles": "CC"})

    assert response.status_code == 500
    body = response.text
    assert "secret-token" not in body
    assert "/srv/private/model.bin" not in body
    assert "反向寻靶预测失败" in body


def test_pharm3d_candidate_job_loads_predictor_inside_execution_boundary(monkeypatch):
    from src.web.routes import api_routes

    calls = []

    class Predictor:
        def get_raw_similar_molecules(self, **kwargs):
            calls.append(kwargs)
            return [{"target_name": "synthetic-target"}]

    monkeypatch.setitem(
        __import__("sys").modules,
        "src.reverse_target.predictor",
        SimpleNamespace(get_predictor=lambda: Predictor()),
    )

    result = api_routes._pharm3d_candidates_job("CC", 0.4, 10, "human")

    assert result == [{"target_name": "synthetic-target"}]
    assert calls == [{
        "smiles": "CC",
        "threshold": 0.4,
        "limit": 10,
        "organism_filter": "human",
    }]
