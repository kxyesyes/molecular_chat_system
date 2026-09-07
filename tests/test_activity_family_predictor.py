"""Synthetic inference contract tests, not scientific model performance."""
import copy
import hashlib
import importlib

import pytest


def module():
    return importlib.import_module("src.activity.family_predictor")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    payload = b"SYNTHETIC CONTRACT ONLY"
    weights = tmp_path / "weights.pt"
    weights.write_bytes(payload)
    calls = []
    bundle = dict(bundle_id="pde-v1", family_id="pde-family", source_sha256="a" * 64,
                  assignment_sha256="b" * 64, scope={"species": "mixed"},
                  label_threshold=5., probability_threshold=.5, models={})
    for task in ("classification", "regression"):
        bundle["models"][task] = dict(model_id=task, task_type=task,
            target_id="pde-family", scientific_readiness="endpoint_ready",
            endpoint="activity" if task == "classification" else "pIC50",
            units="probability" if task == "classification" else "pIC50",
            weights_sha256=hashlib.sha256(payload).hexdigest(), model_card_sha256="c" * 64,
            demo_mode=False, fallback_used=False)
    class Registry:
        def get_active_family_bundle(self, family):
            calls.append(("resolve", family))
            return copy.deepcopy(bundle) if family == "pde-family" else None
        def resolve_weights(self, model_id):
            return weights
    class Stage:
        def __init__(self, metadata, content):
            self.task = metadata["task_type"]
            calls.append(("load", self.task))
        def predict(self, smiles):
            calls.append((self.task, list(smiles)))
            key = "probability" if self.task == "classification" else "value"
            return [dict(smiles=smi, success=True, task_type=self.task,
                         endpoint="activity" if self.task == "classification" else "pIC50",
                         units="probability" if self.task == "classification" else "pIC50",
                         **{key: .2 if self.task == "classification" else 6.1}) for smi in smiles]
    monkeypatch.setattr(module(), "_PinnedPredictor", Stage)
    predictor = module().FamilyActivityPredictor(Registry())
    return predictor, calls, bundle, weights, Stage


def test_public_api():
    assert callable(module().FamilyActivityPredictor)


def test_inactive_still_regresses_and_contradiction_not_clipped(harness):
    predictor, calls, _, _, _ = harness
    row = predictor.predict("CCO", target="PDE5A")[0]
    assert row["success"] is True
    assert row["activity_class"] == "无活性"
    assert row["activity_probability"] == .2
    assert row["predicted_pIC50"] == 6.1
    assert row["classification_regression_consistent"] is False
    assert row["warnings"]
    assert [task for task, _ in calls if task in {"classification", "regression"}] == ["classification", "regression"]
    assert row["provenance"]["models"]["classification"]["model_id"] == "classification"


def test_invalid_input_never_loads_or_calls_models(harness):
    predictor, calls, *_ = harness
    rows = predictor.predict(["CC(C)((", "CCO.CCC", "", None], target="PDE5A")
    assert not calls
    assert all(r["status"] == "failed" and r["predicted_pIC50"] is None for r in rows)


@pytest.mark.parametrize("target", ["AChE", "PDE BuChE", "", None, "BuChE"])
def test_no_unknown_or_cross_family_fallback(harness, target):
    predictor, calls, *_ = harness
    row = predictor.predict("CCO", target=target)[0]
    assert row["status"] == "failed"
    assert row["predicted_pIC50"] is None
    assert not any(task in {"classification", "regression", "load"} for task, _ in calls)


@pytest.mark.parametrize("stage,status", [("classification", "failed"), ("regression", "partial")])
def test_stage_failure(harness, monkeypatch, stage, status):
    predictor, calls, _, _, Stage = harness
    original = Stage.predict
    def predict(self, smiles):
        if self.task == stage:
            return [dict(smiles=s, success=False, error="synthetic failure") for s in smiles]
        return original(self, smiles)
    monkeypatch.setattr(Stage, "predict", predict)
    row = predictor.predict("CCO", target="PDE")[0]
    assert row["status"] == status
    assert row["success"] is False
    assert row["predicted_pIC50"] is None
    assert row["errors"][stage]
    if stage == "classification":
        assert not any(t == "regression" for t, _ in calls)
    else:
        assert row["activity_probability"] == .2


@pytest.mark.parametrize("change", ["nan", "bool", "range", "reorder", "count", "demo", "units"])
def test_bad_stage_output_rejected(harness, monkeypatch, change):
    predictor, _, _, _, Stage = harness
    original = Stage.predict
    def bad(self, smiles):
        rows = original(self, smiles)
        if self.task != "classification":
            return rows
        if change == "count": return []
        if change == "nan": rows[0]["probability"] = float("nan")
        if change == "bool": rows[0]["probability"] = True
        if change == "range": rows[0]["probability"] = 1.2
        if change == "reorder": rows[0]["smiles"] = "CCC"
        if change == "demo": rows[0]["demo_mode"] = True
        if change == "units": rows[0]["units"] = "pIC50"
        return rows
    monkeypatch.setattr(Stage, "predict", bad)
    row = predictor.predict("CCO", target="PDE")[0]
    assert row["status"] == "failed"
    assert row["predicted_pIC50"] is None


def test_cache_reused_but_corrupt_weights_never_hidden(harness):
    predictor, calls, _, weights, _ = harness
    assert predictor.predict("CCO", target="PDE")[0]["success"]
    assert predictor.predict("CCN", target="PDE5A")[0]["success"]
    assert len([t for t, _ in calls if t == "load"]) == 2
    weights.write_bytes(b"CORRUPTED")
    assert predictor.predict("CCO", target="PDE")[0]["status"] == "failed"


def test_bundle_change_invalidates_pair_and_keeps_result_snapshot(harness):
    predictor, calls, bundle, _, _ = harness
    first = predictor.predict("CCO", target="PDE")[0]
    bundle["bundle_id"] = "pde-v2"
    second = predictor.predict("CCO", target="PDE")[0]
    assert first["bundle_id"] == "pde-v1"
    assert second["bundle_id"] == "pde-v2"
    assert len([t for t, _ in calls if t == "load"]) == 4


def test_provenance_does_not_expose_private_paths_or_extra_fields(harness):
    predictor, _, bundle, *_ = harness
    bundle["family_dataset_path"] = "D:/PRIVATE_PATH/source.csv"
    bundle["models"]["regression"]["private_extra"] = "PRIVATE_SENTINEL"
    row = predictor.predict("CCO", target="PDE")[0]
    import json
    assert "PRIVATE_" not in json.dumps(row)
    assert row["provenance"]["source_sha256"] == bundle["source_sha256"]


def test_exact_probability_boundary_and_mixed_batch(harness, monkeypatch):
    predictor, _, _, _, Stage = harness
    original = Stage.predict
    def boundary(self, smiles):
        rows = original(self, smiles)
        for row in rows:
            row["probability" if self.task == "classification" else "value"] = .5 if self.task == "classification" else 5.
        return rows
    monkeypatch.setattr(Stage, "predict", boundary)
    rows = predictor.predict(["CCO", "invalid", "CCN"], target="PDE")
    assert [row["status"] for row in rows] == ["passed", "failed", "passed"]
    assert rows[0]["activity_class"] == "有活性"
    assert rows[0]["classification_regression_consistent"] is True


def test_stage_warnings_preserved(harness, monkeypatch):
    predictor, _, _, _, Stage = harness
    original = Stage.predict
    def warned(self, smiles):
        rows = original(self, smiles)
        for row in rows:
            row["warnings"] = ["synthetic-domain-warning"]
        return rows
    monkeypatch.setattr(Stage, "predict", warned)
    row = predictor.predict("CCO", target="PDE")[0]
    assert "[classification] synthetic-domain-warning" in row["warnings"]
    assert "[regression] synthetic-domain-warning" in row["warnings"]


@pytest.mark.parametrize("logit", [float("inf"), float("-inf"), float("nan"), 1000., -1000.])
def test_nonfinite_logit_cannot_be_hidden_by_sigmoid(harness, monkeypatch, logit):
    import math
    from tests.test_activity_prediction_contract import _fake_loaded_predictor
    predictor, calls, _, _, Stage = harness
    original = Stage.predict
    def logits(self, smiles):
        if self.task != "classification":
            return original(self, smiles)
        core, _ = _fake_loaded_predictor(dict(model_id="synthetic", weights_sha256="a" * 64,
            task_type="classification", endpoint="activity", units="probability"), [logit] * len(smiles))
        return core.predict(smiles)
    monkeypatch.setattr(Stage, "predict", logits)
    row = predictor.predict("CCO", target="PDE")[0]
    if math.isfinite(logit):
        assert row["success"] is True
    else:
        assert row["status"] == "failed"
        assert row["activity_probability"] is None
        assert row["predicted_pIC50"] is None
        assert not any(t == "regression" for t, _ in calls)


def test_pinned_rg_nn_forward_on_synthetic_weights_only(monkeypatch):
    import io
    import torch
    from torch_geometric.data import Batch
    from src.activity.predictor import ActivityPredictor
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
    monkeypatch.setattr(ActivityPredictor, "_find_checkpoint", lambda _: pytest.fail("legacy lookup"))
    helper = ActivityPredictor()
    helper.device = torch.device("cpu")
    pairs = [helper.process_smiles(s) for s in ("CCO", "CCN")]
    assert all(pair is not None for pair in pairs)
    atoms = Batch.from_data_list([p[0] for p in pairs])
    reduced = Batch.from_data_list([p[1] for p in pairs])
    config = dict(in_channels=atoms.x.shape[1], edge_dim=atoms.edge_attr.shape[1], channels=8,
                  out_channels=1, num_passing_atom=2, num_passing_pool=1,
                  num_passing_rg=1, num_passing_mol=1, dropout=0.)
    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(17)
            network = RGNN(**config).eval()
        stream = io.BytesIO()
        torch.save({"state_dict": network.state_dict()}, stream)
        data = stream.getvalue()
        with torch.no_grad():
            reference, _ = network(atoms, reduced)
        for task in ("classification", "regression"):
            metadata = dict(model_id="synthetic-only", task_type=task,
                endpoint="activity" if task == "classification" else "pIC50",
                units="probability" if task == "classification" else "pIC50",
                weights_sha256=hashlib.sha256(data).hexdigest(), model_config=config)
            stage = module()._PinnedPredictor(metadata, data)
            stage.device = torch.device("cpu")
            rows = stage.predict(["CCO", "CCN"])
            assert all(row["success"] for row in rows)
            expected = torch.sigmoid(reference) if task == "classification" else reference
            assert [r["probability" if task == "classification" else "value"] for r in rows] == pytest.approx(expected.reshape(-1).tolist())
    finally:
        torch.set_num_threads(threads)
