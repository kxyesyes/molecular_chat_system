from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.activity.predictor import ActivityPredictor


def _model_metadata(
    *,
    task_type: str = "regression",
    endpoint: str = "pIC50",
    units: str = "log10(mol/L)",
) -> dict[str, Any]:
    return {
        "model_id": "contract-model",
        "weights_sha256": "a" * 64,
        "task_type": task_type,
        "endpoint": endpoint,
        "units": units,
    }


def _fake_loaded_predictor(
    metadata: dict[str, Any] | None,
    raw_outputs: list[float],
) -> tuple[ActivityPredictor, Any]:
    torch = pytest.importorskip("torch")
    data_module = pytest.importorskip("torch_geometric.data")

    class FakeModel:
        def __init__(self, outputs: list[float]):
            self.outputs = list(outputs)
            self.forward_calls = 0

        def __call__(self, atom_batch, _rg_batch):
            self.forward_calls += 1
            values = self.outputs[: atom_batch.num_graphs]
            return torch.tensor(values, dtype=torch.float32).reshape(-1, 1), None

    predictor = ActivityPredictor()
    predictor._loaded = True
    predictor.demo_mode = False
    predictor.device = torch.device("cpu")
    predictor.current_model_path = "data/activity/models/contract-model.pt"
    predictor.current_model_metadata = metadata
    predictor.model = FakeModel(raw_outputs)

    def process_smiles(_smiles: str):
        atom_data = data_module.Data(x=torch.ones((1, 1), dtype=torch.float32))
        rg_data = data_module.Data(x=torch.ones((1, 1), dtype=torch.float32))
        return atom_data, rg_data

    predictor.process_smiles = process_smiles
    return predictor, predictor.model


def test_regression_prediction_preserves_registered_endpoint_schema() -> None:
    predictor, model = _fake_loaded_predictor(_model_metadata(), [6.2])

    result = predictor.predict("CCO")[0]

    assert model.forward_calls == 1
    assert result == {
        "smiles": "CCO",
        "success": True,
        "task_type": "regression",
        "endpoint": "pIC50",
        "value": pytest.approx(6.2),
        "units": "log10(mol/L)",
    }
    assert {"activity_score", "confidence", "class"}.isdisjoint(result)


def test_classification_logit_is_sigmoid_probability_without_invented_fields() -> None:
    metadata = _model_metadata(
        task_type="classification",
        endpoint="AURKA_active",
        units="probability",
    )
    predictor, model = _fake_loaded_predictor(metadata, [0.0])

    result = predictor.predict("CCO")[0]

    assert model.forward_calls == 1
    assert result == {
        "smiles": "CCO",
        "success": True,
        "task_type": "classification",
        "endpoint": "AURKA_active",
        "probability": pytest.approx(0.5),
        "units": "probability",
    }
    assert {"pIC50", "activity_score", "confidence", "class"}.isdisjoint(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", ""),
        ("endpoint", "unknown"),
        ("endpoint", "UNSPECIFIED"),
        ("task_type", "ranking"),
        ("units", None),
    ],
)
def test_invalid_scientific_metadata_fails_before_model_forward(
    field: str,
    value: Any,
) -> None:
    metadata = _model_metadata()
    metadata[field] = value
    predictor, model = _fake_loaded_predictor(metadata, [8.8])

    result = predictor.predict("CCO")[0]

    assert model.forward_calls == 0
    assert result["success"] is False
    assert "metadata" in result["error"].lower()
    assert {
        "value",
        "probability",
        "pIC50",
        "activity_score",
        "confidence",
        "class",
    }.isdisjoint(result)


def test_unavailable_weights_remain_structured_failure_without_simulated_value() -> None:
    predictor = ActivityPredictor()
    predictor._loaded = True
    predictor.demo_mode = True
    predictor.current_model_path = None
    predictor.current_model_metadata = None

    results = predictor.predict(["CCO", "CCN"])

    assert [item["smiles"] for item in results] == ["CCO", "CCN"]
    assert all(item["success"] is False for item in results)
    for item in results:
        assert {
            "value",
            "probability",
            "pIC50",
            "activity_score",
            "confidence",
            "class",
        }.isdisjoint(item)


def test_checkpoint_without_metadata_is_rejected_before_torch_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.activity import predictor as predictor_module

    checkpoint = tmp_path / "unregistered.pt"
    checkpoint.write_bytes(b"not-real-weights")
    predictor = ActivityPredictor()
    predictor.has_torch = True
    predictor._loaded = False
    load_calls: list[Path] = []
    monkeypatch.setattr(
        predictor,
        "_find_checkpoint",
        lambda: (checkpoint, None),
    )

    def record_torch_load(_torch, path: Path, _device):
        load_calls.append(path)
        return {"state_dict": {}}

    monkeypatch.setattr(predictor_module, "_safe_torch_load", record_torch_load)

    predictor.load()
    result = predictor.predict("CCO")[0]

    assert load_calls == []
    assert predictor.current_model_metadata is None
    assert result["success"] is False
    assert "metadata" in result["error"].lower()


def test_prediction_preserves_input_order_and_duplicate_smiles() -> None:
    predictor, _model = _fake_loaded_predictor(_model_metadata(), [6.1, 6.9, 7.3])

    results = predictor.predict(["CCO", "CCO", "CCN"])

    assert [item["smiles"] for item in results] == ["CCO", "CCO", "CCN"]
    assert [item["value"] for item in results] == pytest.approx([6.1, 6.9, 7.3])


def _scaffold(smiles: str) -> str:
    from rdkit.Chem.Scaffolds import MurckoScaffold

    return MurckoScaffold.MurckoScaffoldSmiles(
        smiles=smiles,
        includeChirality=False,
    )


def test_scaffold_split_is_default_stable_and_has_no_scaffold_leakage() -> None:
    from src.activity import trainer

    smiles = [
        "c1ccccc1",
        "Cc1ccccc1",
        "c1ccncc1",
        "Cc1ccncc1",
        "C1CCCCC1",
        "CC1CCCCC1",
    ]

    first = trainer._split_indices(smiles, random_seed=17, validation_fraction=0.34)
    second = trainer._split_indices(smiles, random_seed=17, validation_fraction=0.34)

    assert first == second
    assert first["requested_strategy"] == "scaffold"
    assert first["actual_strategy"] == "scaffold"
    assert first["random_seed"] == 17
    assert first["warnings"] == []
    train_scaffolds = {_scaffold(smiles[index]) for index in first["train_indices"]}
    val_scaffolds = {_scaffold(smiles[index]) for index in first["val_indices"]}
    assert train_scaffolds
    assert val_scaffolds
    assert train_scaffolds.isdisjoint(val_scaffolds)

    signature = inspect.signature(trainer.ActivityTrainer.start_training)
    assert signature.parameters["split_strategy"].default == "scaffold"
    assert signature.parameters["random_seed"].default == 42


def test_random_split_remains_explicit_and_seed_stable() -> None:
    from src.activity import trainer

    smiles = ["CCO", "CCN", "CCC", "CCCl", "CCBr", "CCF"]
    first = trainer._split_indices(
        smiles,
        split_strategy="random",
        random_seed=23,
        validation_fraction=0.34,
    )
    second = trainer._split_indices(
        smiles,
        split_strategy="random",
        random_seed=23,
        validation_fraction=0.34,
    )

    assert first == second
    assert first["requested_strategy"] == "random"
    assert first["actual_strategy"] == "random"
    assert sorted(first["train_indices"] + first["val_indices"]) == list(range(6))


@pytest.mark.parametrize(
    "smiles",
    [
        ["CCO"],
        ["c1ccccc1", "Cc1ccccc1", "Oc1ccccc1"],
    ],
)
def test_scaffold_split_fails_clearly_for_insufficient_groups(
    smiles: list[str],
) -> None:
    from src.activity import trainer

    with pytest.raises(ValueError, match="scaffold|samples"):
        trainer._split_indices(smiles, split_strategy="scaffold", random_seed=42)


def test_training_metadata_records_requested_and_actual_split_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.activity import trainer

    dataset = tmp_path / "training.csv"
    dataset.write_bytes(b"smiles,label\nCCO,1\n")
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    weights = models_dir / "job.pt"
    weights.write_bytes(b"fake-test-weights")
    monkeypatch.setattr(trainer, "MODELS_DIR", models_dir)

    metadata = trainer._build_model_metadata(
        model_id="job-contract",
        weights_file=weights.name,
        task_type="classification",
        target_column="label",
        endpoint="AURKA_active",
        units="probability",
        file_path=str(dataset),
        samples=1,
        best_metrics={"auc_pr": 0.7},
        model_config={"in_channels": 4, "edge_dim": 2, "channels": 8},
        requested_split_strategy="scaffold",
        actual_split_strategy="random_fallback",
        random_seed=29,
        split_warnings=["Only one scaffold; used an explicit random fallback."],
        created_at=100.0,
    )

    assert metadata["endpoint"] == "AURKA_active"
    assert metadata["units"] == "probability"
    assert metadata["requested_split_strategy"] == "scaffold"
    assert metadata["split_strategy"] == "random_fallback"
    assert metadata["random_seed"] == 29
    assert metadata["split_warnings"] == [
        "Only one scaffold; used an explicit random fallback."
    ]
    assert metadata["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "prediction,expected_token",
    [
        (
            {
                "smiles": "CCO",
                "success": True,
                "task_type": "regression",
                "endpoint": "AURKA_potency",
                "value": 6.2,
                "units": "log10(mol/L)",
            },
            "6.2000",
        ),
        (
            {
                "smiles": "CCO",
                "success": True,
                "task_type": "classification",
                "endpoint": "AURKA_active",
                "probability": 0.5,
                "units": "probability",
            },
            "0.5000",
        ),
    ],
)
def test_agent_tool_formats_task_schema_and_exposes_complete_provenance(
    prediction: dict[str, Any],
    expected_token: str,
) -> None:
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool

    metadata = _model_metadata(
        task_type=prediction["task_type"],
        endpoint=prediction["endpoint"],
        units=prediction["units"],
    )
    fake_predictor = SimpleNamespace(
        predict=lambda _smiles: [dict(prediction)],
        current_model_metadata=metadata,
        current_model_path="data/activity/models/contract-model.pt",
        demo_mode=False,
    )
    tool = ActivityPredictorTool()
    tool._predictor = fake_predictor

    result = tool.execute("predict activity for CCO")

    assert result["success"] is True
    assert prediction["endpoint"] in result["formatted"]
    assert prediction["units"] in result["formatted"]
    assert expected_token in result["formatted"]
    assert "High" not in result["formatted"]
    assert "Medium" not in result["formatted"]
    assert "Low" not in result["formatted"]
    assert result["quality"]["model_provenance"] == {
        "model_id": "contract-model",
        "weights_sha256": "a" * 64,
        "endpoint": prediction["endpoint"],
        "units": prediction["units"],
        "task_type": prediction["task_type"],
        "demo_mode": False,
    }
    assert result["data"][0]["model_provenance"] == result["quality"][
        "model_provenance"
    ]


def test_activity_api_uses_task8_threadpool_for_cold_start_and_prediction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.activity import predictor as predictor_module
    from src.web.routes import api_routes

    state = {"inside_threadpool": False, "invocations": 0}

    class FakePredictor:
        def predict(self, smiles):
            values = [smiles] if isinstance(smiles, str) else list(smiles)
            return [
                {
                    "smiles": value,
                    "success": False,
                    "error": "test model unavailable",
                }
                for value in values
            ]

    def get_fake_predictor():
        assert state["inside_threadpool"] is True
        return FakePredictor()

    async def fake_invoke(func, *args, **kwargs):
        state["invocations"] += 1
        state["inside_threadpool"] = True
        try:
            return func(*args, **kwargs)
        finally:
            state["inside_threadpool"] = False

    monkeypatch.setattr(predictor_module, "get_predictor", get_fake_predictor)
    monkeypatch.setattr(api_routes, "_invoke_in_threadpool", fake_invoke)
    app = FastAPI()
    api_routes.setup_api_routes(app)
    client = TestClient(app)

    direct = client.post("/api/activity/predict", data={"smiles": "CCO"})
    batch = client.post(
        "/api/activity/batch_predict",
        files={"file": ("batch.smi", b"CCO\nCCO\nCCN\n", "text/plain")},
    )

    assert direct.status_code == 200
    assert batch.status_code == 200
    assert state["invocations"] == 2
    assert [item["smiles"] for item in batch.json()["results"]] == [
        "CCO",
        "CCO",
        "CCN",
    ]


def test_training_api_and_frontend_forward_split_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.activity import trainer
    from src.web.routes import api_routes

    captured: dict[str, Any] = {}

    def fake_submit_training_job(**kwargs):
        captured.update(kwargs)
        os.unlink(kwargs["file_path"])
        return "split-job"

    monkeypatch.setattr(trainer, "submit_training_job", fake_submit_training_job)
    app = FastAPI()
    api_routes.setup_api_routes(app)
    client = TestClient(app)

    response = client.post(
        "/api/activity/train",
        files={"file": ("train.csv", b"smiles,label\nCCO,1\n", "text/csv")},
        data={
            "target_column": "label",
            "task_type": "classification",
            "split_strategy": "random",
            "random_seed": "31",
        },
    )

    assert response.status_code == 200
    assert captured["split_strategy"] == "random"
    assert captured["random_seed"] == 31

    source = (
        Path(__file__).parents[1]
        / "src/web/static/js/activity_prediction/main.js"
    ).read_text(encoding="utf-8")
    assert 'formData.append("split_strategy"' in source
    assert 'document.getElementById("splitStrategy").value' in source
