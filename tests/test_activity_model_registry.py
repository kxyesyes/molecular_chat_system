from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.activity.model_registry import ActivityModelRegistry


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
    "model_format",
    "weights_sha256",
    "metrics",
}

WEIGHTS_BYTES = b"registered checkpoint"
WEIGHTS_SHA256 = hashlib.sha256(WEIGHTS_BYTES).hexdigest()


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
        "model_format": "pytorch_state_dict",
        "weights_sha256": WEIGHTS_SHA256,
        "metrics": {"rmse": 0.1},
        "name": "Display name",
        "created_at": 1234.5,
    }
    metadata.update(overrides)
    return metadata


def _write_weights(models_dir: Path, name: str = "weights.pt") -> Path:
    weights = models_dir / name
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(WEIGHTS_BYTES)
    return weights


def _process_register(models_dir: str, model_id: str) -> None:
    directory = Path(models_dir)
    weights = _write_weights(directory, f"{model_id}.pt")
    ActivityModelRegistry(directory).register(_metadata(model_id, weights.name))


def _process_select(models_dir: str, model_id: str) -> None:
    ActivityModelRegistry(models_dir).select(model_id)


def _process_delete(models_dir: str, model_id: str) -> None:
    ActivityModelRegistry(models_dir).delete(model_id)


def _process_list_repeatedly(models_dir: str) -> None:
    registry = ActivityModelRegistry(models_dir)
    for _ in range(20):
        registry.list()


def _process_hold_registry_lock(models_dir: str, ready, release) -> None:
    from src.activity import model_registry as registry_module

    lock_path = Path(models_dir).resolve() / registry_module.REGISTRY_LOCK_FILE
    with registry_module._interprocess_registry_lock(lock_path):
        ready.set()
        release.wait(10)


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
        ("endpoint", "unknown"),
        ("endpoint", "UNSPECIFIED"),
        ("units", ""),
        ("dataset_sha256", "not-a-sha256"),
        ("split_strategy", ""),
        ("random_seed", True),
        ("model_config", {}),
        ("model_format", "pickle_object"),
        ("weights_sha256", "not-a-sha256"),
        ("weights_sha256", "0" * 64),
        ("metrics", []),
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
    state = json.loads(second_instance.state_path.read_text(encoding="utf-8"))
    assert state["active_model_id"] == "persisted-model"
    assert set(state["models"]) == {"persisted-model"}
    assert {path.name for path in replace_targets} == {
        "persisted-model_info.json",
        "registry_state.json",
    }
    assert not list(models_dir.glob("*.tmp"))


def test_legacy_sidecars_migrate_once_into_atomic_state(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir, "model_rgmpnn_legacy-model.pt")
    metadata = _metadata("legacy-model", weights.name)
    metadata["best_metrics"] = dict(metadata["metrics"])
    for field in ("model_format", "weights_sha256", "metrics"):
        metadata.pop(field)
    (models_dir / "legacy-model_info.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    (models_dir / "active_model.json").write_text(
        json.dumps({"model_id": "legacy-model"}), encoding="utf-8"
    )

    registry = ActivityModelRegistry(models_dir)

    assert [item["model_id"] for item in registry.list()] == ["legacy-model"]
    assert registry.get_active_model_id() == "legacy-model"
    state = json.loads(registry.state_path.read_text(encoding="utf-8"))
    assert state["active_model_id"] == "legacy-model"
    migrated = state["models"]["legacy-model"]
    assert migrated["model_format"] == "pytorch_state_dict"
    assert migrated["weights_sha256"] == WEIGHTS_SHA256
    assert migrated["metrics"] == {"rmse": 0.1}


def test_register_state_remains_authoritative_when_sidecar_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.activity import model_registry as registry_module

    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir, "state-only.pt")
    registry = ActivityModelRegistry(models_dir)
    real_replace = registry_module.os.replace

    def fail_sidecar_replace(source, destination) -> None:
        if Path(destination).name.endswith("_info.json"):
            raise PermissionError("injected sidecar failure")
        real_replace(source, destination)

    monkeypatch.setattr(registry_module.os, "replace", fail_sidecar_replace)

    registered = registry.register(_metadata("state-only", weights.name))

    assert registered["model_id"] == "state-only"
    assert [item["model_id"] for item in registry.list()] == ["state-only"]
    assert not (models_dir / "state-only_info.json").exists()
    assert "Failed to write activity model sidecar" in caplog.text


def test_register_rejects_duplicate_weights_file_across_model_ids(
    tmp_path: Path,
) -> None:
    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir, "shared.pt")
    registry = ActivityModelRegistry(models_dir)
    registry.register(_metadata("first-model", weights.name))

    with pytest.raises(ValueError):
        registry.register(_metadata("second-model", weights.name))

    assert [item["model_id"] for item in registry.list()] == ["first-model"]


def test_delete_commits_logical_removal_before_best_effort_file_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir, "active.pt")
    registry = ActivityModelRegistry(models_dir)
    registry.register(_metadata("active-model", weights.name))
    registry.select("active-model")
    sidecar = models_dir / "active-model_info.json"
    legacy_active = models_dir / "active_model.json"
    legacy_active.write_text(
        json.dumps({"model_id": "active-model"}), encoding="utf-8"
    )
    cleanup_names = {weights.name, sidecar.name, legacy_active.name}
    real_unlink = Path.unlink

    def fail_physical_cleanup(path: Path, *args, **kwargs) -> None:
        if path.parent == models_dir.resolve() and path.name in cleanup_names:
            raise PermissionError("injected cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_physical_cleanup)

    assert registry.delete("active-model") is True
    assert weights.exists()
    assert sidecar.exists()
    assert legacy_active.exists()
    assert "Failed to clean activity model artifact" in caplog.text

    second_instance = ActivityModelRegistry(models_dir)
    assert second_instance.list() == []
    assert second_instance.get_active_model_id() is None
    with pytest.raises(ValueError):
        second_instance.get("active-model")


def test_threaded_registry_transactions_preserve_unrelated_models(
    tmp_path: Path,
) -> None:
    models_dir = tmp_path / "models"
    model_ids = [f"thread-{index}" for index in range(12)]

    def register(model_id: str) -> None:
        weights = _write_weights(models_dir, f"{model_id}.pt")
        ActivityModelRegistry(models_dir).register(_metadata(model_id, weights.name))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(register, model_ids))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda model_id: ActivityModelRegistry(models_dir).select(model_id),
                model_ids,
            )
        )

    deleted = set(model_ids[::2])

    def read_repeatedly() -> None:
        registry = ActivityModelRegistry(models_dir)
        for _ in range(30):
            registry.list()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(read_repeatedly) for _ in range(3)]
        futures.extend(
            executor.submit(ActivityModelRegistry(models_dir).delete, model_id)
            for model_id in deleted
        )
        for future in futures:
            future.result()

    registry = ActivityModelRegistry(models_dir)
    remaining = {item["model_id"] for item in registry.list()}
    assert remaining == set(model_ids) - deleted
    assert registry.get_active_model_id() in remaining | {None}
    state = json.loads(registry.state_path.read_text(encoding="utf-8"))
    assert set(state["models"]) == remaining


def test_multiprocess_registry_transactions_do_not_lose_state(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    model_ids = [f"process-{index}" for index in range(4)]
    context = multiprocessing.get_context("spawn")

    register_processes = [
        context.Process(target=_process_register, args=(str(models_dir), model_id))
        for model_id in model_ids
    ]
    for process in register_processes:
        process.start()
    for process in register_processes:
        process.join(20)
        assert process.exitcode == 0

    select_processes = [
        context.Process(target=_process_select, args=(str(models_dir), model_id))
        for model_id in model_ids
    ]
    for process in select_processes:
        process.start()
    for process in select_processes:
        process.join(20)
        assert process.exitcode == 0

    deleted = set(model_ids[:2])
    mutation_processes = [
        context.Process(target=_process_delete, args=(str(models_dir), model_id))
        for model_id in deleted
    ] + [
        context.Process(target=_process_list_repeatedly, args=(str(models_dir),))
        for _ in range(2)
    ]
    for process in mutation_processes:
        process.start()
    for process in mutation_processes:
        process.join(20)
        assert process.exitcode == 0

    registry = ActivityModelRegistry(models_dir)
    remaining = {item["model_id"] for item in registry.list()}
    assert remaining == set(model_ids) - deleted
    assert registry.get_active_model_id() in remaining | {None}
    state = json.loads(registry.state_path.read_text(encoding="utf-8"))
    assert set(state["models"]) == remaining


def test_interprocess_lock_timeout_is_finite_and_does_not_leak_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    registry = ActivityModelRegistry(models_dir)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_process_hold_registry_lock,
        args=(str(models_dir), ready, release),
    )
    holder.start()
    assert ready.wait(10)
    monkeypatch.setenv("MEDCHAT_ACTIVITY_REGISTRY_LOCK_TIMEOUT_SECONDS", "0.2")
    started = time.monotonic()
    elapsed = None
    try:
        with pytest.raises(TimeoutError) as exc_info:
            registry.list()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(10)

    assert holder.exitcode == 0
    assert elapsed is not None and elapsed < 2
    assert str(tmp_path) not in str(exc_info.value)


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


def test_trainer_metadata_uses_real_dataset_and_weights_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.activity import trainer

    dataset = tmp_path / "training.csv"
    dataset.write_bytes(b"smiles,pIC50\nCCO,5.1\n")
    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir, "job.pt")
    monkeypatch.setattr(trainer, "MODELS_DIR", models_dir)
    model_config = {"in_channels": 32, "edge_dim": 8, "channels": 64}

    metadata = trainer._build_model_metadata(
        model_id="job-1234",
        weights_file=weights.name,
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
    assert metadata["model_format"] == "pytorch_state_dict"
    assert metadata["weights_sha256"] == hashlib.sha256(weights.read_bytes()).hexdigest()
    assert metadata["metrics"] == {"rmse": 0.1}


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
    app = FastAPI()
    setup_api_routes(app)
    client = TestClient(app)

    listed = client.get("/api/activity/models")
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
        response = client.post("/api/activity/models/switch", json=payload)
        assert response.status_code in {400, 422}
        assert str(tmp_path) not in response.text
        assert ActivityModelRegistry(models_dir).get_active_model_id() == "first-model"

    switched = client.post(
        "/api/activity/models/switch",
        json={"model_id": "second-model"},
    )
    assert switched.status_code == 200
    assert ActivityModelRegistry(models_dir).get_active_model_id() == "second-model"
    assert dummy_predictor._loaded is False
    assert client.get("/api/activity/models").json()["current_model"] == "second-model"


def test_activity_model_delete_api_invalidates_shared_predictor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.activity import predictor as predictor_module
    from src.activity import trainer
    from src.web.routes.api_routes import setup_api_routes

    models_dir = tmp_path / "models"
    weights = _write_weights(models_dir, "delete.pt")
    monkeypatch.setattr(trainer, "MODELS_DIR", models_dir)
    registry = ActivityModelRegistry(models_dir)
    registry.register(_metadata("delete-model", weights.name))
    registry.select("delete-model")

    dummy_predictor = SimpleNamespace(_loaded=True)

    def invalidate() -> None:
        dummy_predictor._loaded = False

    dummy_predictor.invalidate = Mock(side_effect=invalidate)
    monkeypatch.setattr(predictor_module, "_predictor", dummy_predictor)
    app = FastAPI()
    setup_api_routes(app)
    client = TestClient(app)

    response = client.delete("/api/activity/models/delete-model")

    assert response.status_code == 200
    dummy_predictor.invalidate.assert_called_once_with()
    assert dummy_predictor._loaded is False
    assert ActivityModelRegistry(models_dir).get_active_model_id() is None
    assert ActivityModelRegistry(models_dir).list() == []


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
    assert "model_id: item.weights_file" not in source
    assert "`/api/activity/models/${modelId}`" in source
