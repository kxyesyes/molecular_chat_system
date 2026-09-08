"""Synthetic engineering tests, not evidence of scientific model performance."""
import inspect

import pytest

from src.activity import trainer


def test_prepared_path_is_explicit_and_legacy_metadata_is_unvalidated(tmp_path, monkeypatch):
    assert "prepared_manifest_path" in inspect.signature(trainer.ActivityTrainer.start_training).parameters
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path))
    (tmp_path / "source.csv").write_text("synthetic", encoding="utf-8")
    (tmp_path / "model.pt").write_bytes(b"synthetic-not-a-model")
    metadata = trainer._build_model_metadata(
        model_id="test", weights_file="model.pt", task_type="regression",
        target_column="value", file_path=str(tmp_path / "source.csv"), samples=10,
        best_metrics={}, model_config={},
    )
    assert metadata["scientific_readiness"] == "legacy_unvalidated"


def test_metrics_have_explicit_binary_semantics():
    from src.activity.prepared_training import calculate_metrics
    metrics = calculate_metrics("classification", [0, 1, 0, 1], [-2, 2, 2, -2])
    assert metrics["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 1, "tp": 1}
    assert metrics["balanced_accuracy"] == 0.5
    assert set(metrics) == {"roc_auc", "pr_auc", "balanced_accuracy", "confusion_matrix"}


@pytest.mark.parametrize("task,labels,predictions", [
    ("classification", [1, 1], [0., 1.]),
    ("classification", [0, 7], [0., 1.]),
    ("regression", [1], [1.]),
    ("regression", [1, 2], [float("nan"), 1.]),
    ("regression", [1, 2], [float("inf"), 1.]),
    ("other", [1, 2], [1., 2.]),
])
def test_metrics_reject_undefined_or_nonfinite_values(task, labels, predictions):
    from src.activity.prepared_training import calculate_metrics
    with pytest.raises(ValueError):
        calculate_metrics(task, labels, predictions)


def test_epochs_select_validation_then_evaluate_test_once(monkeypatch):
    import torch
    from src.activity import prepared_training as prepared
    model = torch.nn.Linear(1, 1)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    calls = []
    def train_epoch(*args):
        with torch.no_grad():
            model.weight.add_(1)
        calls.append("train")
        return 1.0
    def evaluate(model_arg, loader, *args, prediction_summary=None):
        calls.append(loader)
        if loader == "validation":
            # First epoch is best; patience=2 must stop after epoch three.
            epoch = calls.count("validation")
            if epoch == 1:
                saved.append(model.weight.detach().clone())
            return float(epoch), {"rmse": float(epoch), "mae": 1., "r2": 0.}
        assert torch.equal(model.weight, saved[0])
        prediction_summary.update(sample_count=2)
        return 9.0, {"rmse": 9., "mae": 9., "r2": -1.}
    saved = []
    monkeypatch.setattr(prepared, "train_epoch", train_epoch)
    monkeypatch.setattr(prepared, "evaluate", evaluate)
    result = prepared.fit_prepared(
        model, optimizer, {"train": "train", "validation": "validation", "test": "test"},
        torch.nn.MSELoss(), task_type="regression", device="cpu", epochs=10, patience=2,
    )
    assert calls == ["train", "validation"] * 3 + ["test"]
    assert result["best_epoch"] == 1
    assert result["validation_metrics"]["rmse"] == 1.
    assert result["test_metrics"]["rmse"] == 9.
    assert result["epochs_completed"] == 3
    assert result["test_prediction_summary"]["sample_count"] == 2


def test_prepared_dispatch_does_not_run_legacy_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "dataset_manifest.json"
    path.write_text("synthetic", encoding="utf-8")
    job = "prepared-dispatch-test"
    trainer.training_jobs[job] = {"logs": []}
    instance = trainer.ActivityTrainer(job)
    from src.activity import prepared_training
    def run(owner, manifest, **kwargs):
        assert manifest == str(path)
        raise ValueError("rejected synthetic fixture")
    monkeypatch.setattr(prepared_training, "run_prepared_training", run)
    instance._train_loop(str(path), "value", "regression", 1, .001, 2, .2,
                         1, 8, 0., 1, "MSE", "Cosine", "scaffold", 42,
                         prepared_manifest_path=str(path))
    assert path.exists()
    assert instance.status["state"] == "failed"
    assert "rejected synthetic" in instance.status["error"]
    trainer.training_jobs.pop(job)


@pytest.mark.parametrize("fail_registration", [False, True])
def test_publication_writes_verified_card_or_cleans_assets(tmp_path, monkeypatch, fail_registration):
    import json
    import torch
    from src.activity import prepared_training as module
    from src.activity.model_card import load_prepared_training_data
    from tests.test_activity_prepared_training_data import prepared
    path = prepared(tmp_path / "prepared")
    loaded = load_prepared_training_data(path)
    models = tmp_path / "models"
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(models))
    result = dict(best_epoch=1, epochs_completed=2,
                  validation_metrics={"rmse": .7, "mae": .6, "r2": .5},
                  test_metrics={"rmse": .9, "mae": .8, "r2": .3})
    if fail_registration:
        def reject(*args):
            raise ValueError("registration rejected")
        monkeypatch.setattr(trainer, "save_model_info", reject)
        with pytest.raises(ValueError, match="registration rejected"):
            module.publish_model("test", torch.nn.Linear(1, 1), loaded, {"channels": 1}, result, 42)
        assert list(models.iterdir()) == []
    else:
        metadata = module.publish_model("test", torch.nn.Linear(1, 1), loaded, {"channels": 1}, result, 42)
        registered = trainer.get_model_registry().get(metadata["model_id"])
        assert registered["scientific_readiness"] == "endpoint_ready"
        card = json.loads((models / metadata["model_card_file"]).read_text(encoding="utf-8"))
        assert card["validation_metrics"] == result["validation_metrics"]
        assert card["test_metrics"] == result["test_metrics"]
        assert card["source"] == "synthetic-test-only"
        assert card["data_quality_summary"] == loaded.quality_report
        assert len(card["training_code"]["source_sha256"]) == 64
        assert "src/activity/prepared_training.py" in card["training_code"]["files"]
        assert trainer.get_model_registry().get_active_for_endpoint(metadata["endpoint_key"]) is None
    assert path.exists()


@pytest.mark.parametrize("task", ["regression", "classification"])
def test_real_rg_mpnn_one_epoch_on_synthetic_fixture_only(tmp_path, monkeypatch, task):
    """Real network/optimizer integration, NOT evaluation of scientific accuracy."""
    import torch
    from tests.test_activity_prepared_training_data import prepared
    path = prepared(tmp_path / "prepared", task=task)
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "models"))
    job = "synthetic-one-epoch"
    trainer.training_jobs[job] = {"logs": [], "warnings": []}
    instance = trainer.ActivityTrainer(job)
    instance.device = torch.device("cpu")
    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        from src.activity.prepared_training import run_prepared_training
        run_prepared_training(instance, str(path), epochs=1, lr=.001, batch_size=4,
                              dropout=0., num_layers=2, hidden_size=8, weight_decay=0.,
                              patience=1, loss_metric="MSE", lr_scheduler="Cosine", random_seed=42)
        assert instance.status["state"] == "completed", instance.status.get("error")
        record = trainer.get_model_registry().get(instance.status["model_id"])
        assert record["task_type"] == task
        assert record["split_strategy"] == "scaffold"
        assert record["split_counts"] == {"train": 4, "validation": 4, "test": 4}
        assert record["demo_mode"] is False
        assert record["test_prediction_summary"]["sample_count"] == 4
        assert record["test_prediction_summary"]["output_kind"] == ("probability" if task == "classification" else "endpoint_value")
        assert path.exists()
    finally:
        torch.set_num_threads(threads)
        trainer.training_jobs.pop(job)


def test_unsupported_single_layer_rejected_before_featurization(tmp_path, monkeypatch):
    from tests.test_activity_prepared_training_data import prepared
    from src.activity.prepared_training import run_prepared_training
    from types import SimpleNamespace
    path = prepared(tmp_path)
    with pytest.raises(ValueError, match="num_layers must be at least 2"):
        run_prepared_training(SimpleNamespace(has_torch=True), str(path), epochs=1,
                              lr=.001, batch_size=4, dropout=0., num_layers=1,
                              hidden_size=8, weight_decay=0., patience=1,
                              loss_metric="MSE", lr_scheduler="Cosine", random_seed=42)


def test_seed_controls_nonzero_dropout_not_ambient_rng(tmp_path, monkeypatch):
    import torch
    from tests.test_activity_prepared_training_data import prepared
    from src.activity.prepared_training import run_prepared_training
    path = prepared(tmp_path / "prepared")
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "models"))
    threads = torch.get_num_threads()
    records = []
    try:
        torch.set_num_threads(1)
        for ambient_seed in (11, 22):
            torch.manual_seed(ambient_seed)
            before = torch.random.get_rng_state().clone()
            job = f"synthetic-repeat-{ambient_seed}"
            trainer.training_jobs[job] = {"logs": [], "warnings": []}
            owner = trainer.ActivityTrainer(job)
            owner.device = torch.device("cpu")
            try:
                run_prepared_training(owner, str(path), epochs=2, lr=.001, batch_size=4,
                                      dropout=.2, num_layers=2, hidden_size=8, weight_decay=0.,
                                      patience=1, loss_metric="MSE", lr_scheduler="Cosine", random_seed=42)
                records.append(trainer.get_model_registry().get(owner.status["model_id"]))
                assert torch.equal(before, torch.random.get_rng_state())
            finally:
                trainer.training_jobs.pop(job)
        states = [torch.load(trainer.get_activity_models_dir() / r["weights_file"], weights_only=True)["state_dict"] for r in records]
        assert all(torch.equal(states[0][key], states[1][key]) for key in states[0])
        assert records[0]["test_metrics"] == records[1]["test_metrics"]
    finally:
        torch.set_num_threads(threads)


def test_prepared_featurization_failure_never_drops_rows_or_publishes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests.test_activity_prepared_training_data import prepared
    from src.activity import predictor
    from src.activity.prepared_training import run_prepared_training
    path = prepared(tmp_path / "prepared")
    models = tmp_path / "models"
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(models))
    monkeypatch.setattr(predictor, "get_predictor", lambda: SimpleNamespace(process_smiles=lambda _: None))
    with pytest.raises(ValueError, match="no rows may be silently dropped"):
        run_prepared_training(SimpleNamespace(has_torch=True, status={}), str(path), epochs=1,
                              lr=.001, batch_size=4, dropout=0., num_layers=2,
                              hidden_size=8, weight_decay=0., patience=1,
                              loss_metric="MSE", lr_scheduler="Cosine", random_seed=42)
    assert path.exists()
    assert not models.exists()


def test_submission_forwards_prepared_path_without_starting_a_thread(monkeypatch):
    captured = {}
    class Thread:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def start(self):
            captured["started"] = True
    monkeypatch.setattr(trainer.threading, "Thread", Thread)
    job = trainer.submit_training_job(prepared_manifest_path="synthetic/dataset_manifest.json")
    try:
        assert captured["started"]
        assert captured["kwargs"]["prepared_manifest_path"] == "synthetic/dataset_manifest.json"
        assert trainer.get_job_status(job)["state"] == "pending"
    finally:
        trainer.training_jobs.pop(job)
