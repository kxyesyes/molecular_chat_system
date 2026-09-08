"""Checkpoint selection only: synthetic bytes are never loaded as models."""
import hashlib
from pathlib import Path

import pytest

from src.activity import trainer
from src.activity.model_registry import ActivityModelRegistry
from src.activity.predictor import ActivityPredictor
from tests.activity_test_support import legacy_metadata, write_card, write_endpoint_model


HISTORICAL_PATHS = (
    "data/activity/rg_mpnn/best_model.pt",
    "data/activity/rg_mpnn/model.pt",
    "RG-MPNN-main/vis/AURKA/AURKA.pt",
)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = ActivityModelRegistry(tmp_path / "models")
    monkeypatch.setattr(trainer, "get_model_registry", lambda: registry)
    return registry


def register_legacy(registry, readiness=None):
    weights = registry.models_dir / "legacy.pt"
    weights.write_bytes(b"SYNTHETIC NON-MODEL LEGACY PLACEHOLDER")
    metadata = legacy_metadata("legacy", weights.name)
    metadata.update(weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(), created_at=1)
    if readiness is not None:
        metadata["scientific_readiness"] = readiness
    return registry.register(metadata)


def register_prepared(registry, target, task, weights_name=None):
    metadata = write_endpoint_model(registry, target_id=target)
    metadata["created_at"] = 2
    if task == "classification":
        metadata.update(task_type=task, endpoint="active", units="binary",
                        endpoint_key=f"{target}:active:binary:classification",
                        test_metrics={"roc_auc": .7, "pr_auc": .6, "balanced_accuracy": .6,
                                      "confusion_matrix": {"tn": 4, "fp": 3, "fn": 2, "tp": 6}})
    if weights_name:
        (registry.models_dir / metadata["weights_file"]).rename(registry.models_dir / weights_name)
        metadata["weights_file"] = weights_name
    write_card(registry, metadata)
    return registry.register(metadata)


@pytest.mark.parametrize("target", ["target-a", "pde-family", "buche-family"])
@pytest.mark.parametrize("task", ["regression", "classification"])
@pytest.mark.parametrize("with_legacy", [False, True])
def test_prepared_registration_does_not_change_unselected_discovery(registry, target, task, with_legacy):
    predictor = object.__new__(ActivityPredictor)
    legacy = register_legacy(registry) if with_legacy else None
    before = predictor._find_checkpoint()
    prepared = register_prepared(registry, target, task)
    assert registry.list()[0] == prepared  # Ensure the repro really exercises list priority.
    assert registry.get_active() is None
    assert trainer.get_best_model() == legacy
    assert predictor._find_checkpoint() == before
    assert trainer.get_current_model_id() == ("legacy" if with_legacy else None)
    assert trainer.get_best_model_path() == (str(registry.resolve_weights("legacy")) if with_legacy else None)


@pytest.mark.parametrize("readiness", [None, "legacy_unvalidated"])
def test_historical_legacy_metadata_remains_discoverable(registry, readiness):
    legacy = register_legacy(registry, readiness)
    assert trainer.get_best_model() == legacy
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (registry.resolve_weights("legacy"), legacy)


@pytest.mark.parametrize("task", ["regression", "classification"])
def test_explicit_global_selection_still_wins_in_temporary_registry(registry, task):
    register_legacy(registry)
    prepared = register_prepared(registry, "pde-family", task)
    registry.select(prepared["model_id"])
    assert trainer.get_best_model() == prepared
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (
        registry.resolve_weights(prepared["model_id"]), prepared,
    )


@pytest.mark.parametrize("historical_path", HISTORICAL_PATHS)
def test_hardcoded_fallback_cannot_reintroduce_unselected_prepared_model(tmp_path, monkeypatch, historical_path):
    monkeypatch.chdir(tmp_path)
    path = Path(historical_path)
    registry = ActivityModelRegistry(path.parent)
    monkeypatch.setattr(trainer, "get_model_registry", lambda: registry)
    register_prepared(registry, "pde-family", "classification", path.name)
    # Isolate the hardcoded fallback from registry choice, so list filtering
    # alone cannot satisfy this regression.
    monkeypatch.setattr(trainer, "get_best_model", lambda: None)
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (None, None)


@pytest.mark.parametrize("historical_path", HISTORICAL_PATHS)
def test_unregistered_historical_paths_remain_discoverable(registry, historical_path):
    path = Path(historical_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SYNTHETIC HISTORICAL FILE - NEVER LOADED")
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (path, None)


def test_skipping_prepared_hardcoded_path_continues_historical_search(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = ActivityModelRegistry(Path(HISTORICAL_PATHS[0]).parent)
    monkeypatch.setattr(trainer, "get_model_registry", lambda: registry)
    register_prepared(registry, "pde-family", "classification", "best_model.pt")
    legacy_path = Path(HISTORICAL_PATHS[1])
    legacy_path.write_bytes(b"SYNTHETIC HISTORICAL FILE - NEVER LOADED")
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (legacy_path, None)


def test_endpoint_selection_is_not_implicit_global_selection(registry):
    legacy = register_legacy(registry)
    prepared = register_prepared(registry, "pde-family", "classification")
    registry.select_for_endpoint(prepared["endpoint_key"], prepared["model_id"])
    assert registry.get_active() is None
    assert trainer.get_best_model() == legacy


def test_registering_real_family_bundle_keeps_legacy_choice(registry, tmp_path, monkeypatch):
    from tests.family_model_test_support import make_package, make_pair, register_bundle

    legacy = register_legacy(registry)
    predictor = object.__new__(ActivityPredictor)
    before = predictor._find_checkpoint()
    path = make_package(tmp_path, monkeypatch)
    pair = make_pair(registry, path, overrides={
        task: {"created_at": 2} for task in ("regression", "classification")
    })
    register_bundle(registry, path, pair)
    assert registry.get_active() is None
    assert registry.get_active_family_bundle("pde-family") is None
    assert registry.list()[0]["scientific_readiness"] == "endpoint_ready"
    assert trainer.get_best_model() == legacy
    assert predictor._find_checkpoint() == before
