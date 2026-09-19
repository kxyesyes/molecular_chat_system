"""Real RGNN execution on temporary, untrained synthetic test weights only."""
import math
from pathlib import Path
import urllib.request

import pytest


@pytest.fixture
def offline_chain(tmp_path, monkeypatch):
    """Construct only temporary synthetic assets; never accept ambient/source models."""
    import httpx
    import pandas as pd
    import requests
    import torch
    from src.activity import predictor as legacy
    from tests.family_model_test_support import make_forward_bundle

    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("Offline chain forbids global fallback, dataset reopening and network")

    threads = torch.get_num_threads()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(legacy, "get_predictor", forbidden)
    monkeypatch.setattr(legacy.ActivityPredictor, "load", forbidden)
    monkeypatch.setattr(legacy.ActivityPredictor, "_find_checkpoint", forbidden)
    # TestClient's in-process ASGI transport remains available; real HTTP does not.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(requests.Session, "send", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            registry, models = make_forward_bundle(
                tmp_path, monkeypatch, family="PDE", bundle_id="bundle-a")
            assert registry.models_dir.is_relative_to(tmp_path)
            monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(registry.models_dir))
            monkeypatch.setenv("MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE", "0")
            registry.select_family_bundle("bundle-a")
            assert registry.get_active_model_id() is None

            monkeypatch.setattr("src.activity.family_dataset.load_family_dataset", forbidden)
            monkeypatch.setattr(pd, "read_csv", forbidden)
            path_open = Path.open

            def no_csv_open(path, *args, **kwargs):
                if path.suffix.lower() in {".csv", ".tsv"}:
                    forbidden()
                return path_open(path, *args, **kwargs)

            monkeypatch.setattr(Path, "open", no_csv_open)
            yield registry, models
    finally:
        torch.set_num_threads(threads)
        assert not forbidden_calls, "An offline isolation guard was reached (even if swallowed)"


@pytest.mark.parametrize("entrypoint", ["predictor", "predict", "batch_predict"])
def test_registered_pair_runs_real_forward_without_global_selection(offline_chain, entrypoint):
    from src.activity.model_registry import ActivityModelRegistry
    from src.activity.family_predictor import FamilyActivityPredictor

    registry, models = offline_chain
    # Fresh registry instance also reads the persisted family selection.
    predictor = FamilyActivityPredictor(ActivityModelRegistry(registry.models_dir))

    if entrypoint != "predictor":
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.web.routes.api_routes import setup_api_routes

        app = FastAPI()
        setup_api_routes(app)

    def predict(smiles, *, target):
        if entrypoint == "predictor":
            return predictor.predict(smiles, target=target)
        inputs = [smiles] if isinstance(smiles, str) else list(smiles)
        with TestClient(app) as client:
            if entrypoint == "predict":
                responses = [client.post("/api/activity/predict", data={
                    "smiles": value, "target": target}) for value in inputs]
            else:
                responses = [client.post("/api/activity/batch_predict", data={"target": target},
                    files={"file": ("synthetic.smi", "\n".join(inputs).encode(), "text/plain")})]
        results = []
        for response in responses:
            assert response.status_code == 200
            body = response.json()
            statuses = [row["status"] for row in body["results"]]
            expected = ("passed" if statuses and all(s == "passed" for s in statuses)
                        else "partial" if any(s in {"passed", "partial"} for s in statuses)
                        else "failed")
            assert body["status"] == expected
            assert body["success"] is (expected == "passed")
            results.extend(body["results"])
        return results

    rows = predict(["CCO", "CC(C)((", "CCN"], target="PDE5A")
    assert [row["status"] for row in rows] == ["passed", "failed", "passed"]
    for row in (row for row in rows if row["success"]):
        assert math.isfinite(row["predicted_pIC50"])
        assert 0 <= row["activity_probability"] <= 1
        assert row["provenance"]["models"]["regression"]["model_id"] == models["regression"]["model_id"]
    assert rows[1]["predicted_pIC50"] is None
    assert predict("CCO", target="BuChE")[0]["status"] == "failed"
    assert predict("CCN", target="PDE")[0]["success"]
    if entrypoint == "predictor":
        assert predictor._cache
        for _, pair in predictor._cache.values():
            assert all(stage.device.type == "cpu" for stage in pair.values())
    # Warm-cache inference must still reverify the actual model card.
    card = registry.models_dir / models["regression"]["model_card_file"]
    card.write_bytes(card.read_bytes() + b"\n")
    failed = predict("CCO", target="PDE")[0]
    assert failed["status"] == "failed"
    assert failed["predicted_pIC50"] is None
    assert failed["errors"]["bundle"]
