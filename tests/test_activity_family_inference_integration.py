"""Real RGNN execution on temporary, untrained synthetic test weights only."""
import hashlib
import math
import pytest


@pytest.mark.parametrize("entrypoint", ["predictor", "api", "agent"])
def test_registered_pair_runs_real_forward_without_global_selection(tmp_path, monkeypatch, entrypoint):
    import torch
    from src.activity.model_registry import ActivityModelRegistry
    from src.activity.predictor import ActivityPredictor
    from src.activity.family_predictor import FamilyActivityPredictor
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
    from tests.family_model_test_support import make_package, make_pair, register_bundle

    threads = torch.get_num_threads()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    try:
        torch.set_num_threads(1)
        path = make_package(tmp_path, monkeypatch)
        registry = ActivityModelRegistry(tmp_path / "models")
        pair = ActivityPredictor().process_smiles("CCO")
        config = dict(in_channels=pair[0].x.shape[1], edge_dim=pair[0].edge_attr.shape[1],
                      channels=8, out_channels=1, num_passing_atom=2, num_passing_pool=1,
                      num_passing_rg=1, num_passing_mol=1, dropout=0.)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(71)
            def real_test_weights(registry, metadata):
                network = RGNN(**config).eval()
                weights = registry.models_dir / metadata["weights_file"]
                torch.save({"state_dict": network.state_dict()}, weights)
                metadata.update(model_config=config, weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest())
            models = make_pair(registry, path, before_register=real_test_weights)
        register_bundle(registry, path, models)
        registry.select_family_bundle("bundle-a")
        assert registry.get_active_model_id() is None
        # Fresh registry instance also reads the persisted family selection.
        predictor = FamilyActivityPredictor(ActivityModelRegistry(registry.models_dir))
        if entrypoint == "api":
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from src.web.routes.api_routes import setup_api_routes
            monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(registry.models_dir))
            app = FastAPI()
            setup_api_routes(app)
            with TestClient(app) as client:
                response = client.post("/api/activity/batch_predict", data={"target": "PDE5A"},
                    files={"file": ("synthetic.smi", b"CCO\nCC(C)((\nCCN\n", "text/plain")})
            assert response.status_code == 200
            assert response.json()["status"] == "partial"
            assert response.json()["success"] is False
            rows = response.json()["results"]
        elif entrypoint == "agent":
            from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
            from src.agent.tools.base_tool import execute_tool_compat
            monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(registry.models_dir))
            result = execute_tool_compat(ActivityPredictorTool(),
                {"smiles": ["CCO", "CCN"], "target": "PDE5A"})
            assert result.success
            assert result.evidence
            assert result.quality["prediction_status"] == "passed"
            # The invalid Agent input is a separate request because its parser
            # rejects malformed batches before invoking scientific tools.
            invalid = execute_tool_compat(ActivityPredictorTool(),
                {"smiles": "CC(C)((", "target": "PDE5A"})
            assert not invalid.success
            assert not invalid.data
            rows = result.data
        else:
            rows = predictor.predict(["CCO", "CC(C)((", "CCN"], target="PDE5A")
        assert [row["status"] for row in rows] == (
            ["passed", "passed"] if entrypoint == "agent" else ["passed", "failed", "passed"])
        for row in (row for row in rows if row["success"]):
            assert math.isfinite(row["predicted_pIC50"])
            assert 0 <= row["activity_probability"] <= 1
            assert row["provenance"]["models"]["regression"]["model_id"] == models["regression"]["model_id"]
        if entrypoint != "agent":
            assert rows[1]["predicted_pIC50"] is None
        assert predictor.predict("CCO", target="BuChE")[0]["status"] == "failed"
    finally:
        torch.set_num_threads(threads)
