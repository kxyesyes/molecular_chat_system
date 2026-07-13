from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.activity.model_registry import ActivityModelRegistry


ADMIN_TOKEN = "activity-registry-admin-token"
REQUIRED_FIELDS = {
    "model_id",
    "weights_file",
    "task_type",
    "endpoint",
    "units",
    "dataset_sha256",
    "split_strategy",
    "random_seed",
    "model_config",
}


def _metadata(model_id: str, weights_file: str, **overrides) -> dict:
    metadata = {
        "model_id": model_id,
        "weights_file": weights_file,
        "task_type": "regression",
        "endpoint": "pIC50",
        "units": "unspecified",
        "dataset_sha256": "a" * 64,
        "split_strategy": "random",
        "random_seed": 42,
        "model_config": {
            "in_channels": 32,
            "edge_dim": 8,
            "channels": 64,
        },
        "name": "Display name",
        "created_at": 1234.5,
    }
    metadata.update(overrides)
    return metadata


def _write_weights(models_dir: Path, name: str = "weights.pt") -> Path:
    weights = models_dir / name
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"registered checkpoint")
    return weights


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../outside.pt",
        "../../outside.pt",
        "nested/../weights.pt",
        "nested\\..\\weights.pt",
    ],
)
def test_register_rejects_traversal_and_basename_tricks(
    tmp_path: Path, unsafe_name: str
) -> None:
    models_dir = tmp_path / "models"
    _write_weights(models_dir)
    registry = ActivityModelRegistry(models_dir)

    with pytest.raises(ValueError):
        registry.register(_metadata("safe-model", unsafe_name))


def test_register_rejects_absolute_missing_and_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    outside = tmp_path / "outside.pt"
    outside.write_bytes(b"outside")
    registry = ActivityModelRegistry(models_dir)

    with pytest.raises(ValueError):
        registry.register(_metadata("absolute", str(outside.resolve())))
    with pytest.raises(ValueError):
        registry.register(_metadata("missing", "missing.pt"))

    link = models_dir / "escape.pt"
    try:
        link.symlink_to(outside)
    except OSError:
        link.write_bytes(b"simulated link")
        outside_resolved = outside.resolve(strict=True)
        real_resolve = Path.resolve

        def simulated_symlink_resolve(path: Path, strict: bool = False) -> Path:
            if path == link:
                return outside_resolved
            return real_resolve(path, strict=strict)

        monkeypatch.setattr(Path, "resolve", simulated_symlink_resolve)

    with pytest.raises(ValueError):
        registry.register(_metadata("symlink", link.name))


@pytest.mark.parametrize("missing_field", sorted(REQUIRED_FIELDS))
def test_register_requires_complete_scientific_metadata(
    tmp_path: Path, missing_field: str
) -> None:
    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir)
    metadata = _metadata("complete-model", weights.name)
    metadata.pop(missing_field)

    with pytest.raises(ValueError):
        ActivityModelRegistry(models_dir).register(metadata)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("model_id", "../../model"),
        ("model_id", "model.pt"),
        ("model_id", " model "),
        ("task_type", "ranking"),
        ("endpoint", ""),
        ("units", ""),
        ("dataset_sha256", "not-a-sha256"),
        ("split_strategy", ""),
        ("random_seed", True),
        ("model_config", {}),
    ],
)
def test_register_validates_field_values(
    tmp_path: Path, field: str, invalid_value: object
) -> None:
    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir)
    metadata = _metadata("valid-model", weights.name)
    metadata[field] = invalid_value

    with pytest.raises(ValueError):
        ActivityModelRegistry(models_dir).register(metadata)


def test_register_and_select_use_atomic_files_and_persist_across_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.activity import model_registry as registry_module

    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir)
    registry = ActivityModelRegistry(models_dir)
    replace_targets: list[Path] = []
    real_replace = registry_module.os.replace

    def recording_replace(source, target) -> None:
        replace_targets.append(Path(target))
        real_replace(source, target)

    monkeypatch.setattr(registry_module.os, "replace", recording_replace)
    registered = registry.register(_metadata("persisted-model", weights.name))
    registry.select("persisted-model")

    second_instance = ActivityModelRegistry(models_dir)
    assert REQUIRED_FIELDS <= registered.keys()
    assert second_instance.get_active_model_id() == "persisted-model"
    assert second_instance.get_active()["model_id"] == "persisted-model"
    assert {path.name for path in replace_targets} == {
        "persisted-model_info.json",
        "active_model.json",
    }
    assert not list(models_dir.glob("*.tmp"))


def test_select_accepts_only_registered_model_id_and_preserves_active_on_error(
    tmp_path: Path,
) -> None:
    models_dir = tmp_path / "models"
    first = _write_weights(models_dir, "first.pt")
    _write_weights(models_dir, "unregistered.pt")
    registry = ActivityModelRegistry(models_dir)
    registry.register(_metadata("first-model", first.name))
    registry.select("first-model")

    for candidate in (
        "unregistered",
        "unregistered.pt",
        "../../unregistered.pt",
        str((models_dir / "unregistered.pt").resolve()),
    ):
        with pytest.raises(ValueError):
            registry.select(candidate)
        assert ActivityModelRegistry(models_dir).get_active_model_id() == "first-model"


def test_list_skips_invalid_records_and_delete_only_removes_registered_model(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    models_dir = tmp_path / "models"
    valid_weights = _write_weights(models_dir, "valid.pt")
    outside = tmp_path / "outside.pt"
    outside.write_bytes(b"outside")
    registry = ActivityModelRegistry(models_dir)
    registry.register(_metadata("valid-model", valid_weights.name))
    registry.select("valid-model")

    (models_dir / "broken_info.json").write_text("{not-json", encoding="utf-8")
    (models_dir / "ghost_info.json").write_text(
        json.dumps(_metadata("ghost", "missing.pt")), encoding="utf-8"
    )
    (models_dir / "outside_info.json").write_text(
        json.dumps(_metadata("outside", str(outside.resolve()))), encoding="utf-8"
    )

    assert [item["model_id"] for item in registry.list()] == ["valid-model"]
    assert "Skipping invalid activity model registry record" in caplog.text

    with pytest.raises(ValueError):
        registry.delete("outside")
    assert outside.exists()

    assert registry.delete("valid-model") is True
    assert not valid_weights.exists()
    assert not (models_dir / "valid-model_info.json").exists()
    assert ActivityModelRegistry(models_dir).get_active_model_id() is None


def test_trainer_metadata_uses_real_dataset_hash_and_unspecified_units(
    tmp_path: Path,
) -> None:
    from src.activity import trainer

    dataset = tmp_path / "training.csv"
    dataset.write_bytes(b"smiles,pIC50\nCCO,5.1\n")
    model_config = {"in_channels": 32, "edge_dim": 8, "channels": 64}

    metadata = trainer._build_model_metadata(
        model_id="job-1234",
        weights_file="job.pt",
        task_type="regression",
        target_column="pIC50",
        file_path=str(dataset),
        samples=1,
        best_metrics={"rmse": 0.1},
        model_config=model_config,
        created_at=100.0,
    )

    assert metadata["endpoint"] == "pIC50"
    assert metadata["units"] == "unspecified"
    assert metadata["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert metadata["split_strategy"] == "random"
    assert metadata["random_seed"] == 42
    assert metadata["model_config"] == model_config


def test_predictor_resolves_custom_checkpoint_and_metadata_from_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.activity import predictor as predictor_module
    from src.activity import trainer

    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir, "opaque-checkpoint.pt")
    monkeypatch.setattr(trainer, "MODELS_DIR", models_dir)
    registry = ActivityModelRegistry(models_dir)
    expected = registry.register(_metadata("registered-model", weights.name))
    registry.select("registered-model")

    predictor = object.__new__(predictor_module.ActivityPredictor)
    checkpoint, metadata = predictor._find_checkpoint()

    assert checkpoint == weights.resolve()
    assert metadata == expected


def test_safe_torch_load_uses_weights_only_when_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.activity import predictor as predictor_module

    checkpoint = tmp_path / "model.pt"
    load = Mock(return_value={"state_dict": {}})
    torch_module = SimpleNamespace(load=load)
    supported_signature = inspect.Signature(
        [
            inspect.Parameter("path", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter(
                "map_location", inspect.Parameter.KEYWORD_ONLY, default=None
            ),
            inspect.Parameter(
                "weights_only", inspect.Parameter.KEYWORD_ONLY, default=False
            ),
        ]
    )
    monkeypatch.setattr(
        predictor_module.inspect, "signature", lambda _callable: supported_signature
    )

    result = predictor_module._safe_torch_load(torch_module, checkpoint, "cpu")

    assert result == {"state_dict": {}}
    load.assert_called_once_with(checkpoint, map_location="cpu", weights_only=True)


def test_activity_model_api_uses_model_ids_and_rejects_path_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.activity import predictor as predictor_module
    from src.activity import trainer
    from src.web.routes.api_routes import setup_api_routes

    models_dir = tmp_path / "models"
    first = _write_weights(models_dir, "first.pt")
    second = _write_weights(models_dir, "second.pt")
    monkeypatch.setattr(trainer, "MODELS_DIR", models_dir)
    registry = ActivityModelRegistry(models_dir)
    registry.register(_metadata("first-model", first.name, created_at=1.0))
    registry.register(_metadata("second-model", second.name, created_at=2.0))
    registry.select("first-model")

    dummy_predictor = SimpleNamespace(_loaded=True)
    monkeypatch.setattr(predictor_module, "_predictor", dummy_predictor)
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ADMIN_TOKEN)
    app = FastAPI()
    setup_api_routes(app)
    client = TestClient(app)
    headers = {"X-MedChat-Admin-Token": ADMIN_TOKEN}

    listed = client.get("/api/activity/models", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["current_model"] == "first-model"

    invalid_payloads = [
        {"model_file": "second.pt"},
        {"path": "../../second.pt"},
        {"model_id": "../../second-model"},
        {"model_id": str(second.resolve())},
        {"model_id": "second-model", "model_file": "second.pt"},
    ]
    for payload in invalid_payloads:
        response = client.post(
            "/api/activity/models/switch", headers=headers, json=payload
        )
        assert response.status_code in {400, 422}
        assert str(tmp_path) not in response.text
        assert ActivityModelRegistry(models_dir).get_active_model_id() == "first-model"

    switched = client.post(
        "/api/activity/models/switch",
        headers=headers,
        json={"model_id": "second-model"},
    )
    assert switched.status_code == 200
    assert ActivityModelRegistry(models_dir).get_active_model_id() == "second-model"
    assert dummy_predictor._loaded is False
    assert client.get("/api/activity/models", headers=headers).json()[
        "current_model"
    ] == "second-model"


def test_activity_model_frontend_uses_model_id_protocol() -> None:
    source = (
        Path(__file__).parents[1]
        / "src/web/static/js/activity_prediction/model_manager.js"
    ).read_text(encoding="utf-8")

    assert "opt.value = model.model_id" in source
    assert "data.current_model === model.model_id" in source
    assert "JSON.stringify({ model_id:" in source
    assert "opt.textContent = model.name" in source
    assert "model.model_id === selector.value" in source
    assert "model.weights_file === selector.value" not in source
    assert "JSON.stringify({ model_file:" not in source
    assert "`/api/activity/models/${modelId}`" in source
