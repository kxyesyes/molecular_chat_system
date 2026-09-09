"""Bounded v2 integration guards; synthetic artifacts are never loaded as models.

Based on 5dadb49 contracts and 36fc419 legacy-discovery isolation. Deliberately
excludes registry-v3 family bundles and imports of unported training modules.
"""
from __future__ import annotations

import hashlib
import inspect
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


@pytest.fixture(autouse=True)
def synthetic_runtime(tmp_path, monkeypatch):
    # Both relative historical paths and environment-based registry resolution
    # must stay inside this test's temporary directory, even on failure.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "models"))


@pytest.fixture
def registry(tmp_path, monkeypatch):
    instance = ActivityModelRegistry(tmp_path / "models")
    monkeypatch.setattr(trainer, "get_model_registry", lambda: instance)
    return instance


def _register_legacy(registry, readiness=None):
    weights = registry.models_dir / "legacy.pt"
    weights.write_bytes(b"SYNTHETIC LEGACY PLACEHOLDER - NEVER LOAD")
    metadata = legacy_metadata("legacy", weights.name)
    metadata.update(
        weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
        created_at=1,
    )
    if readiness is not None:
        metadata["scientific_readiness"] = readiness
    return registry.register(metadata)


def _register_prepared(registry, target="target-a", task="regression", weights_name=None):
    metadata = write_endpoint_model(registry, target_id=target)
    metadata["created_at"] = 2
    if task == "classification":
        metadata.update(
            task_type=task, endpoint="active", units="binary",
            endpoint_key=f"{target}:active:binary:classification",
            test_metrics={
                "roc_auc": .7, "pr_auc": .6, "balanced_accuracy": .6,
                "confusion_matrix": {"tn": 4, "fp": 3, "fn": 2, "tp": 6},
            },
        )
    if weights_name is not None:
        (registry.models_dir / metadata["weights_file"]).rename(
            registry.models_dir / weights_name
        )
        metadata["weights_file"] = weights_name
    write_card(registry, metadata)
    return registry.register(metadata)


@pytest.mark.parametrize("method_name", ["start_training", "_train_loop"])
def test_existing_trainer_interfaces_accept_explicit_prepared_manifest(method_name):
    method = getattr(trainer.ActivityTrainer, method_name)
    parameters = inspect.signature(method).parameters
    assert "prepared_manifest_path" in parameters, (
        f"ActivityTrainer.{method_name} must accept prepared_manifest_path"
    )
    assert parameters["prepared_manifest_path"].default is None


@pytest.mark.parametrize("target", ["target-a", "pde-family", "buche-family"])
@pytest.mark.parametrize("task", ["regression", "classification"])
@pytest.mark.parametrize("with_legacy", [False, True])
def test_prepared_registration_cannot_change_global_discovery(
    registry, target, task, with_legacy,
):
    predictor = object.__new__(ActivityPredictor)
    legacy = _register_legacy(registry) if with_legacy else None
    before = predictor._find_checkpoint()
    prepared = _register_prepared(registry, target, task)

    # The new candidate is discoverable and outranks legacy, so an empty or
    # incorrectly registered fixture cannot accidentally satisfy this guard.
    assert registry.list()[0] == prepared
    assert registry.get_active() is None
    assert trainer.get_best_model() == legacy
    assert predictor._find_checkpoint() == before
    assert trainer.get_current_model_id() == ("legacy" if with_legacy else None)
    assert trainer.get_best_model_path() == (
        str(registry.resolve_weights("legacy")) if with_legacy else None
    )


@pytest.mark.parametrize("readiness", [None, "legacy_unvalidated"])
def test_genuine_registered_legacy_remains_discoverable(registry, readiness):
    legacy = _register_legacy(registry, readiness)
    assert trainer.get_best_model() == legacy
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (
        registry.resolve_weights("legacy"), legacy,
    )


@pytest.mark.parametrize("task", ["regression", "classification"])
def test_explicit_global_selection_still_wins_in_synthetic_registry(registry, task):
    _register_legacy(registry)
    prepared = _register_prepared(registry, task=task)
    registry.select(prepared["model_id"])
    assert trainer.get_best_model() == prepared
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (
        registry.resolve_weights(prepared["model_id"]), prepared,
    )


def test_endpoint_selection_persists_without_implicit_global_selection(registry):
    # These guards fail as assertions on the existing registry class, not as
    # collection errors importing a module that the parent has yet to port.
    select = getattr(registry, "select_for_endpoint", None)
    get_active = getattr(registry, "get_active_for_endpoint", None)
    assert callable(select), "Registry v2 must support select_for_endpoint"
    assert callable(get_active), "Registry v2 must support get_active_for_endpoint"
    legacy = _register_legacy(registry)
    prepared = _register_prepared(registry, task="classification")
    key = prepared["endpoint_key"]
    assert get_active(key) is None
    select(key, prepared["model_id"])

    reloaded = ActivityModelRegistry(registry.models_dir)
    assert reloaded.get_active_for_endpoint(key) == prepared
    assert reloaded.get_active_for_endpoint("target-b:active:binary:classification") is None
    with pytest.raises(ValueError, match="endpoint_key"):
        select("target-b:active:binary:classification", prepared["model_id"])
    assert reloaded.get_active_for_endpoint(key) == prepared
    assert registry.get_active() is None
    assert trainer.get_best_model() == legacy


@pytest.mark.parametrize("historical_path", HISTORICAL_PATHS)
def test_historical_filename_cannot_reintroduce_unselected_prepared_model(
    monkeypatch, historical_path,
):
    path = Path(historical_path)
    registry = ActivityModelRegistry(path.parent)
    monkeypatch.setattr(trainer, "get_model_registry", lambda: registry)
    prepared = _register_prepared(registry, task="classification", weights_name=path.name)
    assert registry.get(prepared["model_id"]) == prepared
    # Exercise the predictor's fallback independently of trainer list filtering.
    monkeypatch.setattr(trainer, "get_best_model", lambda: None)
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (None, None)


@pytest.mark.parametrize("historical_path", HISTORICAL_PATHS)
def test_lone_unregistered_historical_checkpoint_remains_discoverable(registry, historical_path):
    path = Path(historical_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SYNTHETIC HISTORICAL PLACEHOLDER - NEVER LOAD")
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (path, None)


def test_skipping_prepared_historical_path_continues_to_genuine_legacy(monkeypatch):
    registry = ActivityModelRegistry(Path(HISTORICAL_PATHS[0]).parent)
    monkeypatch.setattr(trainer, "get_model_registry", lambda: registry)
    _register_prepared(registry, task="classification", weights_name="best_model.pt")
    legacy_path = Path(HISTORICAL_PATHS[1])
    legacy_path.write_bytes(b"SYNTHETIC HISTORICAL PLACEHOLDER - NEVER LOAD")
    assert object.__new__(ActivityPredictor)._find_checkpoint() == (legacy_path, None)
