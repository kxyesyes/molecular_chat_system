"""Operational runner tests use synthetic observations only."""
import importlib.util
from pathlib import Path
import pytest


def module():
    path = Path(__file__).parents[1] / "scripts/train_family_activity_models.py"
    assert path.exists(), "Frozen family training runner missing"
    spec = importlib.util.spec_from_file_location("family_training_run", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_frozen_training_configuration():
    assert module().OPTIONS == dict(epochs=50, patience=12, batch_size=32, lr=.001,
        num_layers=5, hidden_size=256, dropout=.2, weight_decay=.0001,
        lr_scheduler="Cosine", loss_metric="MSE", random_seed=42)


def test_baselines_use_only_training_values():
    runner = module()
    r = runner.baseline_metrics("regression", [2., 4.], [1., 5.])
    assert r["rmse"] == 2.
    assert r["r2"] == 0.
    c = runner.baseline_metrics("classification", [0., 1., 1.], [0., 0., 1., 1.])
    assert c["roc_auc"] == .5
    assert c["pr_auc"] == .5
    assert c["balanced_accuracy"] == .5
    assert c["confusion_matrix"] == dict(tn=0, fp=2, fn=0, tp=2)


@pytest.mark.parametrize("states,expected", [
    ([], "failed"), (["completed"] * 4, "completed"),
    (["failed"] * 4, "failed"), (["completed", "failed"], "partial")])
def test_run_status_never_hides_failed_jobs(states, expected):
    assert module().aggregate_status(states) == expected


@pytest.mark.parametrize("bundle_fails", [False, True])
def test_serial_run_records_failure_without_activation_or_retry(tmp_path, monkeypatch, bundle_fails):
    import logging
    import os
    from types import SimpleNamespace
    import pandas as pd
    import torch
    from src.activity import family_dataset, model_card, model_registry
    runner = module()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda _i: "synthetic-device")
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(family_dataset, "load_family_dataset", lambda _p: {
        "datasets": {t: {"path": t + ".json"} for t in runner.TASKS}})
    monkeypatch.setattr(model_card, "load_prepared_training_data", lambda _p: SimpleNamespace(
        frames={k: pd.DataFrame({"normalized_value": [0., 1.]}) for k in ("train", "test")}))
    bundles, calls = [], []
    class Registry:
        def __init__(self, path):
            assert path == tmp_path / "data/activity/models/synthetic"
        def register_family_bundle(self, **kwargs):
            if bundle_fails and kwargs["bundle_id"].endswith("pde-family"):
                raise RuntimeError("synthetic registration failure")
            bundles.append(kwargs)
            return kwargs
        def select_family_bundle(self, *_args):
            pytest.fail("Must not activate")
    monkeypatch.setattr(model_registry, "ActivityModelRegistry", Registry)
    def train(path, job_id, progress):
        calls.append(job_id)
        progress(2, 1, {"val_loss": 1.}, best_epoch=1)
        if len(calls) == 2 and not bundle_fails:
            raise RuntimeError("private-fixture-must-not-be-printed")
        return dict(state="completed", model_id=job_id, best_epoch=1,
                    epochs_completed=1, best_metrics={}, test_metrics={})
    monkeypatch.setattr(runner, "train_job", train)
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", "original-fixture-directory")
    original_logging = logging.root.manager.disable
    report = runner.run("synthetic", root=tmp_path)
    assert calls == [f"synthetic-{family}-{task}" for family in runner.FAMILIES for task in runner.TASKS]
    assert report["status"] == "partial"
    assert len(bundles) == 1 and bundles[0]["bundle_id"].endswith("buche-family")
    assert report["activated"] is False
    if bundle_fails:
        assert report["bundle_errors"][0]["family"] == "pde-family"
    assert "private-fixture" not in str(report)
    assert os.environ["ACTIVITY_MODEL_DIR"] == "original-fixture-directory"
    assert logging.root.manager.disable == original_logging
    with pytest.raises(FileExistsError):
        runner.run("synthetic", root=tmp_path)
    assert len(calls) == 4


def test_poor_metrics_are_not_scientific_success():
    assess = module().assess_quality
    assert assess("regression", dict(rmse=3., mae=2., r2=-1.),
                  dict(rmse=2., mae=1., r2=0.))["status"] == "does_not_improve_baseline"
    assert assess("classification", dict(roc_auc=.8, pr_auc=.8, balanced_accuracy=.5),
                  dict(roc_auc=.5, pr_auc=.6, balanced_accuracy=.5))["status"] == "mixed"
    assert assess("regression", {}, {})["status"] == "unavailable"
