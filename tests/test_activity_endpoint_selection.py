import copy
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.activity import model_registry as module
from src.activity.model_registry import ActivityModelRegistry
from tests.activity_test_support import (
    endpoint_metadata, legacy_metadata, register_endpoint_model, write_card, write_endpoint_model,
)


def test_endpoint_isolation_and_no_global_fallback(tmp_path):
    registry = ActivityModelRegistry(tmp_path)
    a = register_endpoint_model(registry)
    b = register_endpoint_model(registry, "model-b", "target-b")
    registry.select(b["model_id"])
    assert registry.get_active_for_endpoint(a["endpoint_key"]) is None
    for item in (a, b):
        registry.select_for_endpoint(item["endpoint_key"], item["model_id"])
        assert registry.get_active_for_endpoint(item["endpoint_key"]) == item
    with pytest.raises(ValueError, match="endpoint_key"):
        registry.select_for_endpoint(b["endpoint_key"], a["model_id"])
    with pytest.raises(ValueError):
        registry.select_for_endpoint(a["endpoint_key"].replace("pic50:regression", "nm:regression"), a["model_id"])
    assert registry.get_active_for_endpoint("unknown") is None


@pytest.mark.parametrize("field,value", [
    ("random_seed", 99), ("model_config", {"channels": 999}),
    ("model_format", "unrecognized"), ("metrics", {"rmse": 999.0}),
])
def test_card_training_provenance_must_match_registered_metadata(tmp_path, field, value):
    registry = ActivityModelRegistry(tmp_path)
    item = write_endpoint_model(registry)
    write_card(registry, item, **{field: value})
    with pytest.raises(ValueError, match="card"):
        registry.register(item)


@pytest.mark.parametrize("suffix", [".", " "])
def test_weight_alias_cannot_take_ownership_of_another_models_card(tmp_path, suffix):
    registry = ActivityModelRegistry(tmp_path)
    owner = register_endpoint_model(registry)
    registry.select_for_endpoint(owner["endpoint_key"], owner["model_id"])
    card = tmp_path / owner["model_card_file"]
    before = card.read_bytes()
    alias = legacy_metadata("alias-model", card.name + suffix)
    alias["weights_sha256"] = owner["model_card_sha256"]
    with pytest.raises(ValueError):
        registry.register(alias)
    assert card.read_bytes() == before
    assert registry.get_active_for_endpoint(owner["endpoint_key"]) == owner


def test_resolved_weight_alias_cannot_share_a_models_card(tmp_path, monkeypatch):
    from pathlib import Path

    registry = ActivityModelRegistry(tmp_path)
    owner = register_endpoint_model(registry)
    registry.select_for_endpoint(owner["endpoint_key"], owner["model_id"])
    card = tmp_path / owner["model_card_file"]
    before = card.read_bytes()
    alias_path = tmp_path / "MODEL_~1.JSO"
    original_resolve = Path.resolve

    def resolve_alias(path, *args, **kwargs):
        if path == alias_path:
            return original_resolve(card, *args, **kwargs)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)
    alias = legacy_metadata("alias-model", alias_path.name)
    alias["weights_sha256"] = owner["model_card_sha256"]
    with pytest.raises(ValueError, match="collision"):
        registry.register(alias)
    assert card.read_bytes() == before
    assert registry.get_active_for_endpoint(owner["endpoint_key"]) == owner


def test_v1_migration_preserves_global_without_inference(tmp_path):
    registry = ActivityModelRegistry(tmp_path)
    item = write_endpoint_model(registry)
    legacy = legacy_metadata("legacy", item["weights_file"])
    legacy["weights_sha256"] = item["weights_sha256"]
    state = dict(version=1, models={"legacy": legacy}, active_model_id="legacy")
    registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    registry = ActivityModelRegistry(tmp_path)
    migrated = json.loads(registry.state_path.read_text())
    assert migrated["version"] == 3
    assert migrated["family_bundles"] == migrated["active_family_bundles"] == {}
    assert migrated["active_models_by_endpoint"] == {}
    assert registry.get_active()["model_id"] == "legacy"
    assert registry.get_active_for_endpoint(item["endpoint_key"]) is None
    with pytest.raises(ValueError):
        registry.select_for_endpoint(item["endpoint_key"], "legacy")


@pytest.mark.parametrize("mapping", [None, [], {"unknown": "absent"}, {"target-b:pic50:pic50:regression": "model-a"}, {"target-a:pic50:pic50:regression": []}])
def test_invalid_state_mapping_rejected(tmp_path, mapping):
    registry = ActivityModelRegistry(tmp_path)
    register_endpoint_model(registry)
    state = json.loads(registry.state_path.read_text())
    state["active_models_by_endpoint"] = mapping
    registry.state_path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        ActivityModelRegistry(tmp_path)


@pytest.mark.parametrize("field", ["weights_file", "model_card_file"])
@pytest.mark.parametrize("corruption", ["change", "remove"])
def test_selected_corruption_is_unavailable(tmp_path, field, corruption):
    registry = ActivityModelRegistry(tmp_path)
    a = register_endpoint_model(registry)
    b = register_endpoint_model(registry, "model-b", "target-b")
    registry.select(b["model_id"])
    registry.select_for_endpoint(a["endpoint_key"], a["model_id"])
    path = tmp_path / a[field]
    if corruption == "change":
        path.write_bytes(b"CORRUPTED SYNTHETIC ARTIFACT")
    else:
        path.unlink()
    assert ActivityModelRegistry(tmp_path).get_active_for_endpoint(a["endpoint_key"]) is None
    assert registry.get_active() == b
    with pytest.raises(ValueError):
        registry.select_for_endpoint(a["endpoint_key"], a["model_id"])


def test_pure_validator_normalizes_without_mutation_or_registry(tmp_path, monkeypatch):
    validate = getattr(module, "validate_endpoint_metadata", None)
    assert callable(validate)
    metadata = endpoint_metadata()
    metadata.update(target_id="  TARGET-A  ", endpoint="pic50", task_type=" Regression ",
                    endpoint_key="TARGET-A:PIC50:PIC50:REGRESSION", prepared_dataset_sha256="B" * 64)
    original = copy.deepcopy(metadata)
    monkeypatch.setattr(ActivityModelRegistry, "__init__", lambda *a: pytest.fail("registry construction"))
    result = validate(metadata)
    assert result["endpoint_key"] == "target-a:pic50:pic50:regression"
    assert result["endpoint"] == "pIC50"
    assert result["prepared_dataset_sha256"] == "b" * 64
    result["split_counts"]["train"] = 1
    assert metadata == original
    assert list(tmp_path.iterdir()) == []
    assert validate({"scientific_readiness": "legacy_unvalidated"}) == {"scientific_readiness": "legacy_unvalidated"}


def test_trainer_extension_contract_and_legacy_deep_copy():
    fields = getattr(module, "ENDPOINT_METADATA_FIELDS", None)
    assert isinstance(fields, frozenset)
    assert set(endpoint_metadata()) - set(legacy_metadata("a", "a.pt")) <= fields
    assert not fields.intersection(legacy_metadata("a", "a.pt"))
    original = {"metrics": {"rmse": 1}, "endpoint": " Unspecified "}
    result = module.validate_endpoint_metadata(original)
    assert result == original
    result["metrics"]["rmse"] = 2
    assert original["metrics"]["rmse"] == 1


@pytest.mark.parametrize("bad", [None, "missing", "range", "bool", "single-class", "total"])
def test_classification_metrics_contract(bad):
    validate = getattr(module, "validate_endpoint_metadata", None)
    assert callable(validate)
    item = endpoint_metadata()
    item.update(task_type="classification", endpoint="active", units="binary",
                endpoint_key="target-a:active:binary:classification",
                test_metrics={"roc_auc": .7, "pr_auc": .6, "balanced_accuracy": .6,
                              "confusion_matrix": {"tn": 4, "fp": 3, "fn": 2, "tp": 6}})
    if bad is None:
        assert validate(item)["task_type"] == "classification"
        return
    if bad == "missing":
        del item["test_metrics"]["roc_auc"]
    elif bad == "range":
        item["test_metrics"]["pr_auc"] = 2
    elif bad == "bool":
        item["test_metrics"]["roc_auc"] = True
    elif bad == "single-class":
        item["test_metrics"]["confusion_matrix"] = {"tn": 0, "fp": 0, "fn": 5, "tp": 10}
    else:
        item["test_metrics"]["confusion_matrix"]["tp"] = 7
    with pytest.raises(ValueError):
        validate(item)


@pytest.mark.parametrize("name", ["CON", "NUL.json", "COM1.json", "lpt9", "card.json\x00"])
def test_pure_validator_rejects_windows_device_basenames(name):
    item = endpoint_metadata()
    item["model_card_file"] = name
    with pytest.raises(ValueError):
        module.validate_endpoint_metadata(item)


@pytest.mark.parametrize("endpoint,units,transform,task", [
    ("pIC50", "pIC50", "molar_to_pactivity", "regression"),
    ("pKi", "pKi", "molar_to_pactivity", "regression"),
    ("active", "probability", "binary_threshold", "classification"),
    ("IC50", "μM", "identity", "regression"),
])
def test_output_identity_matches_dataset_manifest(tmp_path, endpoint, units, transform, task):
    from src.activity.dataset_contract import DatasetManifest

    kwargs = dict(dataset_id="synthetic", target_id="target-a", target_name="Controlled target-a",
                  source="synthetic contract fixture", license="test-only", task_type=task,
                  endpoint=endpoint, units=units, label_transform=transform)
    if transform == "molar_to_pactivity":
        kwargs.update(endpoint={"pIC50": "IC50", "pKi": "Ki"}[endpoint], units="nM",
                      output_endpoint=endpoint, output_units=units)
    elif transform == "binary_threshold":
        kwargs.update(endpoint="pIC50", units="pIC50", output_endpoint=endpoint,
                      output_units=units, classification_threshold=6,
                      classification_direction="greater_or_equal")
    manifest = DatasetManifest(**kwargs)
    registry = ActivityModelRegistry(tmp_path)
    item = write_endpoint_model(registry)
    item.update(endpoint=endpoint, units=units, label_transform=transform, task_type=task,
                endpoint_key=manifest.endpoint_key)
    if task == "classification":
        item["test_metrics"] = {"roc_auc": .7, "pr_auc": .6, "balanced_accuracy": .6,
                                "confusion_matrix": {"tn": 4, "fp": 3, "fn": 2, "tp": 6}}
    write_card(registry, item)
    registered = registry.register(item)
    registry.select_for_endpoint(manifest.endpoint_key, item["model_id"])
    assert registry.get_active_for_endpoint(manifest.endpoint_key) == registered


@pytest.mark.parametrize("kind", ["invalid-json", "duplicate-key", "missing-flag", "wrong-weights-name", "hardlink"])
def test_card_io_validation_rejects_bad_artifacts(tmp_path, kind):
    registry = ActivityModelRegistry(tmp_path)
    item = write_endpoint_model(registry)
    path = tmp_path / item["model_card_file"]
    if kind == "invalid-json":
        path.write_bytes(b"\xff")
    elif kind == "duplicate-key":
        path.write_text(path.read_text()[:-1] + ', "demo_mode": false}')
    elif kind == "missing-flag":
        payload = json.loads(path.read_text())
        del payload["fallback_used"]
        path.write_text(json.dumps(payload))
    elif kind == "wrong-weights-name":
        write_card(registry, item, weights_file="other.pt")
    else:
        (tmp_path / "shared.json").hardlink_to(path)
    item["model_card_sha256"] = module.hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        registry.register(item)


@pytest.mark.parametrize("field", list(endpoint_metadata().keys() - legacy_metadata("a", "a.pt").keys() - {"demo_mode", "fallback_used"}))
def test_partial_endpoint_claim_rejected(tmp_path, field):
    registry = ActivityModelRegistry(tmp_path)
    metadata = write_endpoint_model(registry)
    del metadata[field]
    with pytest.raises(ValueError):
        registry.register(metadata)


@pytest.mark.parametrize("change", [
    {"target_id": "target-b"}, {"endpoint_key": "target-a:pic50:nm:regression"},
    {"units": "pic50"}, {"units": "nM"}, {"label_transform": "unknown"},
    {"scientific_readiness": "legacy_unvalidated"}, {"demo_mode": True}, {"fallback_used": 0},
    {"split_counts": {"train": True, "validation": 15, "test": 15}},
    {"split_scaffold_counts": {"train": 71, "validation": 10, "test": 10}},
    {"test_metrics": {"rmse": -1, "mae": 1, "r2": .5}},
    {"test_metrics": {"rmse": 1, "mae": 1, "r2": float("nan")}},
    {"test_metrics": {"rmse": 1}}, {"prepared_dataset_sha256": "x" * 64},
    {"model_card_file": "../outside.json"}, {"model_card_file": "C:card.json"},
    {"model_card_file": "registry_state.json"}, {"model_card_file": "registry_state.lock"},
    {"model_card_file": "model-a_info.json"},
])
def test_invalid_endpoint_metadata_rejected(tmp_path, change):
    registry = ActivityModelRegistry(tmp_path)
    metadata = write_endpoint_model(registry)
    metadata.update(change)
    with pytest.raises(ValueError):
        registry.register(metadata)


@pytest.mark.parametrize("change", [
    {"target_id": "target-b"}, {"target_name": "Other target"},
    {"endpoint_key": "target-b:pic50:pic50:regression"}, {"task_type": "classification"},
    {"units": "nM"}, {"output_units": "nM"}, {"weights_sha256": "d" * 64},
    {"prepared_dataset_sha256": "d" * 64}, {"demo_mode": True}, {"fallback_used": True},
])
def test_matching_card_hash_does_not_hide_inconsistent_content(tmp_path, change):
    registry = ActivityModelRegistry(tmp_path)
    metadata = write_endpoint_model(registry)
    write_card(registry, metadata, **change)
    with pytest.raises(ValueError):
        registry.register(metadata)


def test_cards_cannot_share_or_collide_with_other_artifacts(tmp_path):
    registry = ActivityModelRegistry(tmp_path)
    a = register_endpoint_model(registry)
    b = write_endpoint_model(registry, "model-b")
    for name in (a["model_card_file"], a["weights_file"], "model-a_info.json"):
        candidate = dict(b, model_card_file=name)
        candidate["model_card_sha256"] = module.hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        with pytest.raises(ValueError):
            registry.register(candidate)
    # Reverse collision: registering a legacy weight at an existing card path.
    legacy = legacy_metadata("legacy", a["model_card_file"])
    legacy["weights_sha256"] = a["model_card_sha256"]
    with pytest.raises(ValueError):
        registry.register(legacy)


def _select_or_delete(directory, model_id, key, delete=False):
    registry = ActivityModelRegistry(directory)
    if delete:
        registry.delete(model_id)
    else:
        try:
            registry.select_for_endpoint(key, model_id)
        except ValueError:  # deletion may win the same-model race
            assert registry.get_active_for_endpoint(key) is None


@pytest.mark.parametrize("processes", [False, True])
def test_concurrent_selection_deletion_preserves_other_endpoints(tmp_path, processes):
    registry = ActivityModelRegistry(tmp_path)
    a = register_endpoint_model(registry)
    b = register_endpoint_model(registry, "model-b", "target-b")
    registry.select(a["model_id"])
    registry.select_for_endpoint(a["endpoint_key"], a["model_id"])
    args = [(str(tmp_path), a["model_id"], a["endpoint_key"], True),
            (str(tmp_path), a["model_id"], a["endpoint_key"]),
            (str(tmp_path), b["model_id"], b["endpoint_key"])]
    if processes:
        context = multiprocessing.get_context("spawn")
        workers = [context.Process(target=_select_or_delete, args=arg) for arg in args]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(30)
            assert worker.exitcode == 0
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_select_or_delete, *arg) for arg in args]
            for future in futures:
                future.result()
    assert registry.get_active_for_endpoint(a["endpoint_key"]) is None
    assert registry.get_active_for_endpoint(b["endpoint_key"]) == b
    assert registry.get_active() is None
    assert not (tmp_path / a["model_card_file"]).exists()
    assert (tmp_path / b["model_card_file"]).exists()
