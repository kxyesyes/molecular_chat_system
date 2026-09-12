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


@pytest.mark.parametrize("stage", ["classification", "regression"])
@pytest.mark.parametrize("change", [
    "nan", "inf", "-inf", "bool", "string", "missing", "reorder", "count",
    "container", "row_type", "success_type", "task", "endpoint", "demo",
    "fallback", "units", "exception",
])
def test_bad_stage_output_rejected(harness, monkeypatch, stage, change):
    predictor, _, _, _, Stage = harness
    original = Stage.predict
    def bad(self, smiles):
        rows = original(self, smiles)
        if self.task != stage:
            return rows
        key = "probability" if stage == "classification" else "value"
        if change == "count": return []
        if change == "container": return tuple(rows)
        if change == "row_type": return [None]
        if change == "exception": raise RuntimeError("PRIVATE_SENTINEL")
        if change in {"nan", "inf", "-inf"}: rows[0][key] = float(change)
        if change == "bool": rows[0][key] = True
        if change == "string": rows[0][key] = "0.2"
        if change == "missing": rows[0].pop(key)
        if change == "reorder": rows[0]["smiles"] = "CCC"
        if change == "success_type": rows[0]["success"] = 1
        if change == "task": rows[0]["task_type"] = "ranking"
        if change == "endpoint": rows[0]["endpoint"] = "unknown"
        if change == "demo": rows[0]["demo_mode"] = True
        if change == "fallback": rows[0]["fallback_used"] = True
        if change == "units": rows[0]["units"] = "unknown"
        return rows
    monkeypatch.setattr(Stage, "predict", bad)
    row = predictor.predict("CCO", target="PDE")[0]
    assert row["status"] == ("failed" if stage == "classification" else "partial")
    assert row["success"] is False
    assert row["predicted_pIC50"] is None
    assert row["errors"][stage]
    assert row["activity_probability"] == (None if stage == "classification" else .2)
    assert row["provenance"]["bundle_id"] == "pde-v1"
    assert "PRIVATE_SENTINEL" not in str(row)


@pytest.mark.parametrize("probability", [-.1, 1.2])
def test_out_of_range_probability_never_regresses(harness, monkeypatch, probability):
    predictor, calls, _, _, Stage = harness
    original = Stage.predict

    def bad(self, smiles):
        rows = original(self, smiles)
        for row in rows:
            row["probability"] = probability
        return rows

    monkeypatch.setattr(Stage, "predict", bad)
    row = predictor.predict("CCO", target="PDE")[0]
    assert row["status"] == "failed"
    assert row["activity_probability"] is None
    assert not any(task == "regression" for task, _ in calls)


def test_cache_reused_but_corrupt_weights_never_hidden(harness):
    predictor, calls, _, weights, _ = harness
    assert predictor.predict("CCO", target="PDE")[0]["success"]
    assert predictor.predict("CCN", target="PDE5A")[0]["success"]
    assert len([t for t, _ in calls if t == "load"]) == 2
    assert len([t for t, _ in calls if t == "resolve"]) == 2
    weights.write_bytes(b"CORRUPTED")
    assert predictor.predict("CCO", target="PDE")[0]["status"] == "failed"
    assert not predictor._cache


def test_bundle_change_invalidates_pair_and_keeps_result_snapshot(harness):
    predictor, calls, bundle, _, _ = harness
    first = predictor.predict("CCO", target="PDE")[0]
    bundle["bundle_id"] = "pde-v2"
    second = predictor.predict("CCO", target="PDE")[0]
    assert first["bundle_id"] == "pde-v1"
    assert second["bundle_id"] == "pde-v2"
    assert len([t for t, _ in calls if t == "load"]) == 4


def test_request_pins_one_bundle_even_when_selection_changes_mid_forward(harness, monkeypatch):
    predictor, calls, bundle, _, Stage = harness
    original = Stage.predict

    def switch(self, smiles):
        if self.task == "classification":
            bundle["bundle_id"] = "pde-v2"
        return original(self, smiles)

    monkeypatch.setattr(Stage, "predict", switch)
    rows = predictor.predict(["OCC", "CCN"], target="PDE5A")
    assert all(row["success"] and row["bundle_id"] == "pde-v1" for row in rows)
    assert all(row["provenance"]["bundle_id"] == "pde-v1" for row in rows)
    assert [row["smiles"] for row in rows] == ["OCC", "CCN"]
    assert calls.count(("resolve", "pde-family")) == 1
    assert ("regression", ["CCO", "CCN"]) in calls
    assert predictor.predict("CCO", target="PDE")[0]["bundle_id"] == "pde-v2"


def test_regression_only_receives_successful_classification_rows_in_order(harness, monkeypatch):
    predictor, calls, _, _, Stage = harness
    original = Stage.predict

    def mixed(self, smiles):
        rows = original(self, smiles)
        if self.task == "classification":
            rows[1].update(success=False, error="synthetic failure")
        return rows

    monkeypatch.setattr(Stage, "predict", mixed)
    rows = predictor.predict(["CCO", "CCN", "CCO"], target="PDE")
    assert [row["status"] for row in rows] == ["passed", "failed", "passed"]
    assert ("regression", ["CCO", "CCO"]) in calls
    assert rows[1]["predicted_pIC50"] is None
    assert rows[0]["activity_class"] == rows[2]["activity_class"] == "无活性"


def test_cache_holds_only_latest_pair_for_each_controlled_family(harness, monkeypatch):
    predictor, calls, bundle, _, _ = harness
    other = copy.deepcopy(bundle)
    other.update(family_id="buche-family", bundle_id="buche-v1")
    for model in other["models"].values():
        model["target_id"] = "buche-family"

    def resolve(family):
        calls.append(("resolve", family))
        return copy.deepcopy(bundle if family == "pde-family" else other)

    monkeypatch.setattr(predictor.registry, "get_active_family_bundle", resolve)
    for version in range(3):
        bundle["bundle_id"] = f"pde-v{version}"
        assert predictor.predict("CCO", target="PDE")[0]["success"]
        assert predictor.predict("CCO", target="BuChE")[0]["success"]
        assert set(predictor._cache) == {"pde-family", "buche-family"}
    assert len([task for task, _ in calls if task == "load"]) == 8


@pytest.mark.parametrize("field,value", [
    ("family_id", "buche-family"), ("label_threshold", 6.),
    ("probability_threshold", .6), ("models", {}),
])
def test_invalid_bundle_never_loads_a_stage(harness, field, value):
    predictor, calls, bundle, _, _ = harness
    bundle[field] = value
    row = predictor.predict("CCO", target="PDE")[0]
    assert row["status"] == "failed"
    assert row["errors"]["bundle"]
    assert not any(task == "load" for task, _ in calls)


def test_registry_revalidation_failure_is_not_hidden_by_cache(harness, monkeypatch):
    predictor, calls, *_ = harness
    assert predictor.predict("CCO", target="PDE")[0]["success"]
    calls.clear()

    def unavailable(_family):
        raise ValueError("PRIVATE_SENTINEL")

    monkeypatch.setattr(predictor.registry, "get_active_family_bundle", unavailable)
    row = predictor.predict("CCO", target="PDE")[0]
    assert row["status"] == "failed"
    assert row["errors"]["bundle"]
    assert not predictor._cache
    assert not calls
    assert "PRIVATE_SENTINEL" not in str(row)


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


@pytest.mark.parametrize("error", [TypeError, RuntimeError])
def test_pinned_loader_never_retries_without_weights_only(monkeypatch, error):
    import io
    import torch
    from src.activity.predictor import ActivityPredictor

    payload = b"SYNTHETIC RESTRICTED-LOADER CONTRACT"
    calls = []
    monkeypatch.setattr(ActivityPredictor, "_find_checkpoint", lambda _: pytest.fail("legacy lookup"))

    def restricted(stream, *, map_location, weights_only):
        assert isinstance(stream, io.BytesIO)
        assert stream.read() == payload
        assert weights_only is True
        calls.append(weights_only)
        raise error("synthetic restricted-load failure")

    monkeypatch.setattr(torch, "load", restricted)
    stage = module()._PinnedPredictor(
        {"weights_sha256": hashlib.sha256(payload).hexdigest()}, payload)
    with pytest.raises(error, match="restricted-load failure"):
        stage.load()
    assert calls == [True]
    assert not stage._loaded
    assert stage.model is None


def test_pinned_loader_checks_digest_before_deserialization(monkeypatch):
    import torch

    monkeypatch.setattr(torch, "load", lambda *a, **k: pytest.fail("unverified deserialization"))
    stage = module()._PinnedPredictor({"weights_sha256": "a" * 64}, b"CORRUPTED")
    with pytest.raises(ValueError, match="digest mismatch"):
        stage.load()
    assert not stage._loaded


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
