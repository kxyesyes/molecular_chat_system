"""Family transaction contracts using only temporary synthetic artifacts."""
import json

import pytest

from src.activity.model_registry import ActivityModelRegistry
from tests.family_model_test_support import make_package, make_pair, register_bundle


def test_family_bundle_registry_api_exists():
    for name in ("register_family_bundle", "select_family_bundle", "get_active_family_bundle"):
        assert callable(getattr(ActivityModelRegistry, name, None)), name


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = make_package(tmp_path, monkeypatch)
    registry = ActivityModelRegistry(tmp_path / "models")
    return registry, path, make_pair(registry, path)


def test_register_select_restart_and_detached_snapshot(setup):
    registry, path, models = setup
    record = register_bundle(registry, path, models)
    assert registry.get_active_family_bundle("PDE5A") is None
    assert registry.get_active() is None
    registry.select_family_bundle("bundle-a")
    active = ActivityModelRegistry(registry.models_dir).get_active_family_bundle("PDE4")
    descriptor = json.loads(path.read_bytes())
    assert active["bundle_id"] == "bundle-a"
    assert active["family_id"] == "pde-family"
    assert active["models"] == models
    for key in ("source_sha256", "assignment_sha256", "scope"):
        assert active[key] == descriptor[key]
    assert active["label_threshold"] == 5
    assert active["probability_threshold"] == .5
    record["scope"].clear()
    active["models"]["classification"]["model_config"].clear()
    assert registry.get_active_family_bundle("PDE")["models"] == models
    assert registry.get_active_family_bundle("BuChE") is None
    assert json.loads(registry.state_path.read_bytes())["version"] == 3


def test_duplicate_bundle_rejected_without_write(setup):
    registry, path, models = setup
    register_bundle(registry, path, models)
    before = registry.state_path.read_bytes()
    with pytest.raises(ValueError):
        register_bundle(registry, path, models)
    assert registry.state_path.read_bytes() == before


@pytest.mark.parametrize("field", ["source_sha256", "dataset_sha256", "prepared_dataset_sha256", "model_contract_key"])
@pytest.mark.parametrize("location", ["metadata", "card"])
def test_registration_binds_both_metadata_and_actual_card(tmp_path, monkeypatch, field, location):
    path = make_package(tmp_path, monkeypatch)
    registry = ActivityModelRegistry(tmp_path / "models")
    changes = {"classification": {field: "0" * 64}}
    # Main rejects all one-sided card conflicts at single-model registration.
    # Coherent-but-wrong metadata/card provenance must still fail at bundle binding.
    if location == "card":
        with pytest.raises(ValueError):
            make_pair(registry, path, card_overrides=changes)
        assert registry.list() == []
        return
    models = make_pair(registry, path, **{("overrides" if location == "metadata" else "card_overrides"): changes})
    before = registry.state_path.read_bytes()
    with pytest.raises(ValueError):
        register_bundle(registry, path, models)
    assert registry.state_path.read_bytes() == before


@pytest.mark.parametrize("kind", ["swapped", "same", "cross-family", "legacy"])
def test_invalid_pair_rejected(setup, tmp_path, monkeypatch, kind):
    registry, path, models = setup
    if kind == "swapped":
        models = dict(classification=models["regression"], regression=models["classification"])
    elif kind == "same":
        models["regression"] = models["classification"]
    elif kind == "cross-family":
        other = make_package(tmp_path, monkeypatch, "BuChE", "synthetic-buche")
        models["regression"] = make_pair(registry, other, "other")["regression"]
    else:
        from tests.activity_test_support import legacy_metadata
        item = legacy_metadata("legacy", "legacy.pt")
        (registry.models_dir / "legacy.pt").write_bytes(b"synthetic")
        item["weights_sha256"] = registry._sha256_file(registry.models_dir / "legacy.pt")
        models["classification"] = registry.register(item)
    with pytest.raises(ValueError):
        register_bundle(registry, path, models)


@pytest.mark.parametrize("damage", ["weights", "card", "evidence", "threshold", "scope", "metadata"])
def test_bad_bundle_cannot_half_switch_and_active_read_raises(setup, damage):
    registry, path, models = setup
    register_bundle(registry, path, models)
    registry.select_family_bundle("bundle-a")
    other = make_pair(registry, path, "other")
    register_bundle(registry, path, other, "bundle-b")
    if damage in {"weights", "card"}:
        field = "weights_file" if damage == "weights" else "model_card_file"
        (registry.models_dir / other["regression"][field]).write_bytes(b"damaged")
    else:
        state = json.loads(registry.state_path.read_bytes())
        bundle = state["family_bundles"]["bundle-b"]
        if damage == "evidence":
            bundle["dataset_evidence"]["source_sha256"] = "0" * 64
        elif damage == "threshold":
            bundle["label_threshold"] = 6
        elif damage == "scope":
            bundle["scope"]["species"] = "human"
        else:
            bundle["models"]["regression"]["weights_sha256"] = "0" * 64
        registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    before = registry.state_path.read_bytes()
    with pytest.raises(ValueError):
        registry.select_family_bundle("bundle-b")
    assert registry.state_path.read_bytes() == before
    state = json.loads(before)
    assert state["active_family_bundles"] == {"pde-family": "bundle-a"}
    state["active_family_bundles"]["pde-family"] = "bundle-b"
    registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        registry.get_active_family_bundle("PDE")


def test_selection_and_deletion_each_write_once_and_preserve_other_family(setup, tmp_path, monkeypatch):
    registry, path, models = setup
    register_bundle(registry, path, models)
    register_bundle(registry, path, models, "dependent")
    other_path = make_package(tmp_path, monkeypatch, "BuChE", "synthetic-buche")
    other = make_pair(registry, other_path, "other")
    register_bundle(registry, other_path, other, "other")
    registry.select_family_bundle("other")
    registry.select_for_endpoint(models["classification"]["endpoint_key"], models["classification"]["model_id"])
    writes = []
    original = registry._atomic_write_json_unlocked

    def write(destination, payload):
        writes.append(destination)
        return original(destination, payload)

    monkeypatch.setattr(registry, "_atomic_write_json_unlocked", write)
    registry.select_family_bundle("bundle-a")
    assert writes == [registry.state_path]
    writes.clear()
    registry.delete(models["classification"]["model_id"])
    assert writes == [registry.state_path]
    assert registry.get_active_family_bundle("PDE") is None
    assert registry.get_active_family_bundle("BChE")["models"] == other
    state = json.loads(registry.state_path.read_bytes())
    assert set(state["family_bundles"]) == {"other"}
    assert state["active_models_by_endpoint"] == {}
    assert registry.get(models["regression"]["model_id"])


@pytest.mark.parametrize("version", [1, 2])
def test_migration_keeps_legacy_and_endpoint_selections(setup, version):
    registry, path, models = setup
    item = models["regression"]
    registry.select(item["model_id"])
    registry.select_for_endpoint(item["endpoint_key"], item["model_id"])
    state = json.loads(registry.state_path.read_bytes())
    state.update(version=version)
    state.pop("family_bundles", None)
    state.pop("active_family_bundles", None)
    if version == 1:
        state.pop("active_models_by_endpoint")
    registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    registry = ActivityModelRegistry(registry.models_dir)
    migrated = json.loads(registry.state_path.read_bytes())
    assert migrated["version"] == 3
    assert migrated["family_bundles"] == migrated["active_family_bundles"] == {}
    assert registry.get_active() == item
    assert registry.get_active_for_endpoint(item["endpoint_key"]) == (item if version == 2 else None)


def test_atomic_write_failure_retains_previous_selection(setup, monkeypatch):
    registry, path, models = setup
    register_bundle(registry, path, models)
    before = registry.state_path.read_bytes()

    def fail(*args):
        raise RuntimeError("synthetic disk failure")

    monkeypatch.setattr(registry, "_atomic_write_json_unlocked", fail)
    with pytest.raises(RuntimeError, match="disk failure"):
        registry.select_family_bundle("bundle-a")
    assert registry.state_path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"label_threshold": 6}, {"probability_threshold": .7},
    {"probability_threshold": True}, {"source_sha256": None},
    {"target_id": "pde5a", "endpoint_key": "pde5a:activity:probability:classification"},
    {"endpoint": "active", "endpoint_key": "pde-family:active:probability:classification"},
    {"units": "binary", "label_transform": "identity",
     "endpoint_key": "pde-family:activity:binary:classification"},
])
def test_coherent_endpoint_metadata_cannot_override_family_contract(tmp_path, monkeypatch, change):
    path = make_package(tmp_path, monkeypatch)
    registry = ActivityModelRegistry(tmp_path / "models")
    models = make_pair(registry, path, overrides={"classification": change})
    with pytest.raises(ValueError):
        register_bundle(registry, path, models)


@pytest.mark.parametrize("field", ["demo_mode", "fallback_used"])
def test_rejects_demo_or_fallback_in_registry_evidence(setup, field):
    registry, path, models = setup
    state = json.loads(registry.state_path.read_bytes())
    state["models"][models["classification"]["model_id"]][field] = True
    registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        register_bundle(registry, path, models)


@pytest.mark.parametrize("mapping", [None, [], {"pde-family": "missing"}, {"buche-family": "bundle-a"}])
def test_corrupt_mapping_raises_instead_of_falling_back(setup, mapping):
    registry, path, models = setup
    register_bundle(registry, path, models)
    state = json.loads(registry.state_path.read_bytes())
    state["active_family_bundles"] = mapping
    registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        registry.get_active_family_bundle("PDE")


def test_rehashed_model_changes_do_not_replace_pinned_weights(setup):
    registry, path, models = setup
    register_bundle(registry, path, models)
    registry.select_family_bundle("bundle-a")
    state = json.loads(registry.state_path.read_bytes())
    item = state["models"][models["regression"]["model_id"]]
    weights = registry.models_dir / item["weights_file"]
    weights.write_bytes(b"different synthetic contract bytes")
    item["weights_sha256"] = registry._sha256_file(weights)
    from tests.activity_test_support import write_card
    write_card(registry, item)
    registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    # The model is independently valid but is no longer the registered pair.
    assert registry.get(item["model_id"]) == item
    with pytest.raises(ValueError, match="pinned"):
        registry.get_active_family_bundle("PDE")


def test_concurrent_delete_and_select_preserve_other_family(setup, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    registry, path, models = setup
    register_bundle(registry, path, models)
    other_path = make_package(tmp_path, monkeypatch, "BuChE", "synthetic-buche")
    other = make_pair(registry, other_path, "other")
    register_bundle(registry, other_path, other, "other")

    def select():
        try:
            ActivityModelRegistry(registry.models_dir).select_family_bundle("bundle-a")
        except ValueError:  # Deletion can legitimately win the transaction lock.
            assert registry.get_active_family_bundle("PDE") is None

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(select),
                   pool.submit(registry.delete, models["classification"]["model_id"]),
                   pool.submit(registry.select_family_bundle, "other")]
        for future in futures:
            future.result()
    assert registry.get_active_family_bundle("PDE") is None
    assert registry.get_active_family_bundle("BuChE")["models"] == other


def test_unknown_or_ambiguous_family_never_uses_global_selection(setup):
    registry, path, models = setup
    registry.select(models["regression"]["model_id"])
    for family in ("unknown", "PDE BuChE", None):
        with pytest.raises(ValueError):
            registry.get_active_family_bundle(family)
    assert registry.get_active_family_bundle("PDE") is None


def test_pair_fixture_callback_updates_weights_and_card_before_registration(tmp_path, monkeypatch):
    path = make_package(tmp_path, monkeypatch)
    registry = ActivityModelRegistry(tmp_path / "models")
    calls = []

    def before_register(actual_registry, item):
        assert actual_registry is registry
        calls.append(item["task_type"])
        weights = registry.models_dir / item["weights_file"]
        weights.write_bytes(b"replacement synthetic contract bytes")
        item["weights_sha256"] = registry._sha256_file(weights)
        item["model_config"] = {"channels": 32}

    models = make_pair(registry, path, before_register=before_register)
    assert calls == ["classification", "regression"]
    for item in models.values():
        assert item["model_config"] == {"channels": 32}
        assert registry.get(item["model_id"]) == item
        card = json.loads((registry.models_dir / item["model_card_file"]).read_bytes())
        assert card["weights_sha256"] == item["weights_sha256"]
        assert card["model_config"] == item["model_config"]
    register_bundle(registry, path, models)


def test_training_seed_is_independent_of_dataset_split_seed(tmp_path, monkeypatch):
    path = make_package(tmp_path, monkeypatch)
    registry = ActivityModelRegistry(tmp_path / "models")
    models = make_pair(registry, path, overrides={
        "classification": {"random_seed": 123}, "regression": {"random_seed": 456}})
    register_bundle(registry, path, models)
    registry.select_family_bundle("bundle-a")
    assert registry.get_active_family_bundle("PDE")["models"] == models


@pytest.mark.parametrize("location", ["metadata", "card"])
def test_split_seed_must_match_in_metadata_and_card(tmp_path, monkeypatch, location):
    path = make_package(tmp_path, monkeypatch)
    registry = ActivityModelRegistry(tmp_path / "models")
    overrides = {"classification": {"dataset_split_seed": 999}}
    if location == "card":
        with pytest.raises(ValueError, match="dataset_split_seed"):
            make_pair(registry, path, card_overrides=overrides)
        assert registry.list() == []
        return
    models = make_pair(registry, path, **{
        "overrides" if location == "metadata" else "card_overrides": overrides})
    with pytest.raises(ValueError, match="dataset_split_seed"):
        register_bundle(registry, path, models)


def test_selection_and_read_do_not_reopen_training_source(setup, monkeypatch):
    from src.activity import family_dataset

    registry, path, models = setup
    record = register_bundle(registry, path, models)
    path.unlink()
    (path.parent / "source.csv").unlink()

    def forbidden(*args, **kwargs):
        raise AssertionError("Serving must not reload training source")

    monkeypatch.setattr(family_dataset, "load_family_dataset", forbidden)
    registry.select_family_bundle("bundle-a")
    active = ActivityModelRegistry(registry.models_dir).get_active_family_bundle("PDE")
    assert active["models"] == models
    assert "family_dataset_path" not in active
    assert "family_dataset_path" not in record
    assert str(path) not in registry.state_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("location", ["metadata", "card"])
@pytest.mark.parametrize("field,value", [("source", "different-source"), ("license", "redistribution-allowed")])
def test_source_and_license_are_bound_to_verified_scope(tmp_path, monkeypatch, location, field, value):
    path = make_package(tmp_path, monkeypatch)
    registry = ActivityModelRegistry(tmp_path / "models")
    changes = {"classification": {field: value}}
    if location == "card":
        with pytest.raises(ValueError, match=field):
            make_pair(registry, path, card_overrides=changes)
        assert registry.list() == []
        return
    models = make_pair(registry, path, **{
        "overrides" if location == "metadata" else "card_overrides": changes})
    with pytest.raises(ValueError, match=field):
        register_bundle(registry, path, models)


def test_dataset_validation_runs_outside_registry_transaction(setup, monkeypatch):
    from contextlib import contextmanager
    from src.activity import family_models

    registry, path, models = setup
    locked = False
    events = []
    transaction = registry._transaction
    load = family_models.load_bundle_dataset
    build = registry._build_family_bundle_unlocked
    write = registry._atomic_write_json_unlocked

    @contextmanager
    def tracked_transaction():
        nonlocal locked
        with transaction():
            locked = True
            try:
                yield
            finally:
                locked = False

    def tracked_load(path):
        assert not locked, "Slow dataset verification must not hold registry transaction"
        events.append("dataset")
        return load(path)

    def tracked_build(*args):
        assert locked
        events.append("models")
        return build(*args)

    def tracked_write(*args):
        assert locked
        events.append("write")
        return write(*args)

    monkeypatch.setattr(registry, "_transaction", tracked_transaction)
    monkeypatch.setattr(family_models, "load_bundle_dataset", tracked_load)
    monkeypatch.setattr(registry, "_build_family_bundle_unlocked", tracked_build)
    monkeypatch.setattr(registry, "_atomic_write_json_unlocked", tracked_write)
    register_bundle(registry, path, models)
    assert events == ["dataset", "models", "write"]


@pytest.mark.parametrize("intervening", ["selection", "delete", "weights", "card", "duplicate"])
def test_registration_rechecks_latest_state_after_dataset_validation(setup, monkeypatch, intervening):
    from src.activity import family_models

    registry, path, models = setup
    load = family_models.load_bundle_dataset
    after_change = []

    def load_with_intervening_change(path):
        evidence = load(path)
        if intervening == "selection":
            registry.select(models["regression"]["model_id"])
        elif intervening == "delete":
            registry.delete(models["classification"]["model_id"])
        elif intervening in {"weights", "card"}:
            field = "weights_file" if intervening == "weights" else "model_card_file"
            (registry.models_dir / models["regression"][field]).write_bytes(b"corrupt synthetic artifact")
        else:
            # Simulate another registration completing while validation is in progress.
            with monkeypatch.context() as patch:
                patch.setattr(family_models, "load_bundle_dataset", lambda path: evidence)
                register_bundle(registry, path, models)
        after_change.append(registry.state_path.read_bytes())
        return evidence

    monkeypatch.setattr(family_models, "load_bundle_dataset", load_with_intervening_change)
    if intervening == "selection":
        register_bundle(registry, path, models)
        assert registry.get_active()["model_id"] == models["regression"]["model_id"]
    else:
        with pytest.raises(ValueError):
            register_bundle(registry, path, models)
        assert registry.state_path.read_bytes() == after_change[0]


@pytest.mark.parametrize("damage", ["null-digest", "wrong-digest", "snapshot", "rehashed-snapshot", "duplicate-key", "nonfinite"])
def test_descriptor_snapshot_independently_binds_digest_and_evidence(setup, damage):
    import hashlib

    registry, path, models = setup
    register_bundle(registry, path, models)
    registry.select_family_bundle("bundle-a")
    state = json.loads(registry.state_path.read_bytes())
    record = state["family_bundles"]["bundle-a"]
    if damage == "null-digest":
        record["family_dataset_sha256"] = None
    elif damage == "wrong-digest":
        record["family_dataset_sha256"] = "0" * 64
    else:
        snapshot = "{}"
        if damage == "duplicate-key":
            snapshot = '{"family_id":"pde-family","family_id":"pde-family"}'
        elif damage == "nonfinite":
            snapshot = '{"value":NaN}'
        record["family_dataset_snapshot"] = snapshot
        if damage != "snapshot":
            record["family_dataset_sha256"] = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    registry.state_path.write_text(json.dumps(state), encoding="utf-8")
    before = registry.state_path.read_bytes()
    with pytest.raises(ValueError):
        registry.select_family_bundle("bundle-a")
    assert registry.state_path.read_bytes() == before
    with pytest.raises(ValueError):
        registry.get_active_family_bundle("PDE")
    assert registry.state_path.read_bytes() == before


def test_descriptor_snapshot_preserves_original_utf8_bytes_without_source_dependency(setup):
    import hashlib

    registry, path, models = setup
    raw = (json.dumps(json.loads(path.read_bytes()), ensure_ascii=False, indent=3) + " \r\n").encode("utf-8")
    path.write_bytes(raw)
    record = register_bundle(registry, path, models)
    assert record["family_dataset_snapshot"].encode("utf-8") == raw
    assert record["family_dataset_sha256"] == hashlib.sha256(raw).hexdigest()
    path.unlink()
    registry.select_family_bundle("bundle-a")
    assert registry.get_active_family_bundle("PDE")["models"] == models


@pytest.mark.parametrize("operation", ["healthy-read", "model-delete"])
def test_paused_dataset_validation_does_not_block_live_registry(setup, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from src.activity import family_models

    registry, path, models = setup
    register_bundle(registry, path, models)
    registry.select_family_bundle("bundle-a")
    entered, release = Event(), Event()
    load = family_models.load_bundle_dataset

    def paused_load(path):
        entered.set()
        assert release.wait(10), "Dataset validation was not released"
        return load(path)

    monkeypatch.setattr(family_models, "load_bundle_dataset", paused_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        registration = pool.submit(register_bundle, registry, path, models, "bundle-b")
        try:
            assert entered.wait(5), "Registration did not reach dataset validation"
            if operation == "healthy-read":
                healthy = pool.submit(registry.get_active_family_bundle, "PDE")
                assert healthy.result(timeout=3)["bundle_id"] == "bundle-a"
            else:
                deletion = pool.submit(registry.delete, models["classification"]["model_id"])
                assert deletion.result(timeout=3)
                after_delete = registry.state_path.read_bytes()
        finally:
            release.set()
        if operation == "healthy-read":
            assert registration.result(timeout=10)["bundle_id"] == "bundle-b"
        else:
            with pytest.raises(ValueError, match="not registered"):
                registration.result(timeout=10)
            assert registry.state_path.read_bytes() == after_delete


def test_concurrent_same_bundle_registration_has_one_atomic_winner(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from src.activity import family_models

    registry, path, models = setup
    barrier = Barrier(2)
    load = family_models.load_bundle_dataset
    write = registry._atomic_write_json_unlocked
    writes = []

    def synchronized_load(path):
        evidence = load(path)
        barrier.wait(timeout=10)
        return evidence

    def tracked_write(destination, payload):
        writes.append(destination)
        return write(destination, payload)

    def register():
        try:
            return register_bundle(registry, path, models)
        except ValueError as error:
            assert "already registered" in str(error)
            return None

    monkeypatch.setattr(family_models, "load_bundle_dataset", synchronized_load)
    monkeypatch.setattr(registry, "_atomic_write_json_unlocked", tracked_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(register) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert writes == [registry.state_path]
    assert set(json.loads(registry.state_path.read_bytes())["family_bundles"]) == {"bundle-a"}
