"""Offline acceptance plumbing tests; all configuration and weights are synthetic."""
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


def explicit_config(tmp_path):
    return {
        "MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE": "1",
        "MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR": str(tmp_path / "not-created"),
        "MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID": "synthetic-pde",
        "MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID": "synthetic-buche",
    }


def test_disabled_gate_does_not_read_other_configuration(monkeypatch):
    from tests.family_real_acceptance_support import read_config

    class Disabled(dict):
        def get(self, key, default=None):
            assert key == "MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE"
            return "0"

    def forbidden(*args, **kwargs):
        raise AssertionError("Configuration must not touch the filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(Path, "open", forbidden)
        assert read_config(Disabled()) is None
        assert read_config({}) is None


@pytest.mark.parametrize("gate", ["", "0", 0, "true", "yes", 1, True, None, " 1", "1 ", "1"])
def test_only_exact_string_one_enables_configuration(tmp_path, gate):
    from tests.family_real_acceptance_support import read_config

    env = explicit_config(tmp_path)
    env["MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE"] = gate
    assert (read_config(env) is not None) is (gate == "1")


def test_enabled_requires_both_explicit_bundle_ids():
    from tests.family_real_acceptance_support import read_config

    with pytest.raises(ValueError, match="^invalid_configuration$"):
        read_config({"MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE": "1"})


@pytest.mark.parametrize("missing", ["MODELS_DIR", "PDE_BUNDLE_ID", "BUCHE_BUNDLE_ID"])
def test_enabled_requires_each_field(tmp_path, missing):
    from tests.family_real_acceptance_support import read_config

    env = explicit_config(tmp_path)
    del env["MEDCHAT_FAMILY_ACCEPTANCE_" + missing]
    with pytest.raises(ValueError, match="^invalid_configuration$"):
        read_config(env)


def test_enabled_rejects_same_bundle_id(tmp_path):
    from tests.family_real_acceptance_support import read_config

    env = explicit_config(tmp_path)
    env["MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID"] = env["MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID"]
    with pytest.raises(ValueError, match="^invalid_configuration$"):
        read_config(env)


@pytest.mark.parametrize("field", ["PDE_BUNDLE_ID", "BUCHE_BUNDLE_ID"])
@pytest.mark.parametrize("bundle_id", [None, 1, "", "../escape", "a/b", "a\\b", "a.b", " a", "a ", "-a", "a_", "a\n", "a" * 129])
def test_enabled_rejects_invalid_bundle_ids(tmp_path, field, bundle_id):
    from tests.family_real_acceptance_support import read_config

    env = explicit_config(tmp_path)
    env["MEDCHAT_FAMILY_ACCEPTANCE_" + field] = bundle_id
    with pytest.raises(ValueError, match="^invalid_configuration$"):
        read_config(env)


@pytest.mark.parametrize("source", [None, 1, "", "models", "../models", "C:models", r"\\server\share\models", "//server/share/models", r"\\?\C:\models", r"\\.\C:\models", "bad\x00path"])
def test_enabled_rejects_nonlocal_or_relative_source(tmp_path, source):
    from tests.family_real_acceptance_support import read_config

    env = explicit_config(tmp_path)
    env["MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR"] = source
    with pytest.raises(ValueError, match="^invalid_configuration$"):
        read_config(env)


@pytest.mark.parametrize("bundle_id", ["a", "A_0-b", "a" * 128])
def test_enabled_is_pure_frozen_and_does_not_disclose_config(tmp_path, monkeypatch, bundle_id):
    from src.activity.model_registry import ActivityModelRegistry
    from tests.family_real_acceptance_support import AcceptanceConfig, read_config

    env = explicit_config(tmp_path)
    env["MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID"] = bundle_id
    before = env.copy()
    validate = ActivityModelRegistry._validate_model_id
    validated = []

    def lexical(value):
        validated.append(value)
        return validate(value)

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure config cannot construct a registry or read files")

    with monkeypatch.context() as patch:
        patch.setattr(ActivityModelRegistry, "__init__", forbidden)
        patch.setattr(ActivityModelRegistry, "_validate_model_id", staticmethod(lexical))
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "resolve", forbidden)
        config = read_config(env)
    assert isinstance(config, AcceptanceConfig)
    assert config.source == Path(env["MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR"])
    assert config.pde_bundle_id == bundle_id
    assert config.buche_bundle_id == "synthetic-buche"
    assert validated == [bundle_id, "synthetic-buche"]
    assert env == before
    assert AcceptanceConfig.__dataclass_params__.repr is False
    assert str(config.source) not in repr(config)
    assert "synthetic-buche" not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.pde_bundle_id = "changed"


@pytest.mark.parametrize("family", ["PDE", "BuChE"])
def test_forward_fixture_is_temporary_and_has_no_global_selection(tmp_path, monkeypatch, family):
    import torch
    from src.activity.predictor import ActivityPredictor
    from tests.family_model_test_support import make_forward_bundle

    def no_ambient_predictor(*args, **kwargs):
        raise AssertionError("Weight construction must not initialize/load an ambient predictor")

    monkeypatch.setattr(ActivityPredictor, "__init__", no_ambient_predictor)
    monkeypatch.setattr(ActivityPredictor, "load", no_ambient_predictor)
    monkeypatch.setattr(ActivityPredictor, "_find_checkpoint", no_ambient_predictor)
    rng = torch.get_rng_state().clone()
    threads = torch.get_num_threads()
    registry, models = make_forward_bundle(
        tmp_path, monkeypatch, family=family, bundle_id="synthetic-" + family.lower())
    assert torch.equal(torch.get_rng_state(), rng)
    assert torch.get_num_threads() == threads
    assert registry.models_dir.is_relative_to(tmp_path)
    assert registry.get_active_model_id() is None
    assert registry.get_active_family_bundle(family) is None
    assert set(models) == {"classification", "regression"}
    for metadata in models.values():
        assert metadata["model_config"]["channels"] == 8
        assert metadata["random_seed"] == 71
        weights = torch.load(registry.models_dir / metadata["weights_file"], map_location="cpu", weights_only=True)
        assert weights["state_dict"]
        assert all(tensor.device.type == "cpu" for tensor in weights["state_dict"].values())


def test_forward_fixtures_can_share_temporary_registry_without_collisions(tmp_path, monkeypatch):
    import torch
    from tests.family_model_test_support import make_forward_bundle

    pairs = []
    for family, bundle_id in [("PDE", "pde-first"), ("BuChE", "buche-first"), ("PDE", "pde-second")]:
        registry, models = make_forward_bundle(tmp_path, monkeypatch, family=family, bundle_id=bundle_id)
        pairs.append(models)
    assert len(registry.list()) == 6
    assert len(list((tmp_path / "datasets").glob("*/family_dataset.json"))) == 3
    assert pairs[0]["regression"]["target_id"] != pairs[1]["regression"]["target_id"]
    # torch.save archives include the filename; compare seeded tensors, not zip bytes.
    states = [torch.load(registry.models_dir / pair["regression"]["weights_file"],
                         map_location="cpu", weights_only=True)["state_dict"]
              for pair in (pairs[0], pairs[2])]
    assert states[0].keys() == states[1].keys()
    assert all(torch.equal(states[0][key], states[1][key]) for key in states[0])
    assert registry.get_active_model_id() is None


def test_forward_fixture_restores_rng_and_threads_on_failure(tmp_path, monkeypatch):
    import torch
    from tests.family_model_test_support import make_forward_bundle

    rng = torch.get_rng_state().clone()
    threads = torch.get_num_threads()

    def fail_save(*args, **kwargs):
        raise RuntimeError("synthetic save failure")

    monkeypatch.setattr(torch, "save", fail_save)
    with pytest.raises(RuntimeError, match="synthetic save failure"):
        make_forward_bundle(tmp_path, monkeypatch, family="PDE", bundle_id="synthetic-pde")
    assert torch.equal(torch.get_rng_state(), rng)
    assert torch.get_num_threads() == threads


@pytest.fixture
def accelerator_seed_calls(monkeypatch):
    """Observe forbidden seeds without touching accelerator state or lazy queues."""
    import torch

    calls = []

    def record_seed(*args, **kwargs):
        calls.append(args)

    def no_initialization(*args, **kwargs):
        pytest.fail("Synthetic CPU tests must not initialize accelerators")

    for backend in (torch.cuda, torch.mps, torch.xpu):
        monkeypatch.setattr(backend, "_is_in_bad_fork", lambda: False)
        for name in ("manual_seed", "manual_seed_all", "seed", "seed_all"):
            if hasattr(backend, name):
                monkeypatch.setattr(backend, name, record_seed)
        for name in ("init", "_lazy_init", "_lazy_call"):
            if hasattr(backend, name):
                monkeypatch.setattr(backend, name, no_initialization)
    monkeypatch.setattr(torch.random, "_seed_custom_device", record_seed)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    return calls


@pytest.mark.parametrize("fail_save", [False, True], ids=["success", "save-exception"])
def test_forward_fixture_never_seeds_accelerators(
        tmp_path, monkeypatch, accelerator_seed_calls, fail_save):
    import torch
    from tests.family_model_test_support import make_forward_bundle

    rng = torch.get_rng_state().clone()
    threads = torch.get_num_threads()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic save failure")

    if fail_save:
        monkeypatch.setattr(torch, "save", fail)
        with pytest.raises(RuntimeError, match="synthetic save failure"):
            make_forward_bundle(tmp_path, monkeypatch, family="PDE", bundle_id="cpu-only")
    else:
        registry, models = make_forward_bundle(
            tmp_path, monkeypatch, family="PDE", bundle_id="cpu-only")
        assert set(models) == {"classification", "regression"}
        assert registry.get_active_model_id() is None
    assert torch.equal(torch.get_rng_state(), rng)
    assert torch.get_num_threads() == threads
    assert accelerator_seed_calls == []


def test_forward_fixture_preserves_seed71_weights_and_real_forward(
        tmp_path, monkeypatch, accelerator_seed_calls):
    import torch
    from rdkit.Chem.SaltRemover import SaltRemover
    from torch_geometric.data import Batch
    from src.activity.family_predictor import FamilyActivityPredictor
    from src.activity.predictor import ActivityPredictor
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
    from tests.family_model_test_support import make_forward_bundle

    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            registry, models = make_forward_bundle(
                tmp_path, monkeypatch, family="PDE", bundle_id="seed-reference")
            registry.select_family_bundle("seed-reference")
            rows = FamilyActivityPredictor(registry).predict(["CCO", "CCN"], target="PDE")
            assert all(row["success"] for row in rows)

            # Independent CPU reference for the original seed71 network sequence.
            # This test also runs before the fix, with accelerator seeds intercepted.
            torch.random.default_generator.manual_seed(71)
            references = {task: RGNN(**models[task]["model_config"]).cpu().eval()
                          for task in ("classification", "regression")}
            features = ActivityPredictor.__new__(ActivityPredictor)
            features.remover = SaltRemover()
            pairs = [features.process_smiles(smiles) for smiles in ("CCO", "CCN")]
            for task, network in references.items():
                state = torch.load(registry.models_dir / models[task]["weights_file"],
                                   map_location="cpu", weights_only=True)["state_dict"]
                assert state.keys() == network.state_dict().keys()
                assert all(torch.equal(state[key], value)
                           for key, value in network.state_dict().items())
                with torch.no_grad():
                    output, _ = network(Batch.from_data_list([pair[0] for pair in pairs]),
                                        Batch.from_data_list([pair[1] for pair in pairs]))
                    if task == "classification":
                        output = torch.sigmoid(output)
                key = "activity_probability" if task == "classification" else "predicted_pIC50"
                assert [row[key] for row in rows] == output.reshape(-1).tolist()
    finally:
        torch.set_num_threads(threads)


@pytest.mark.parametrize("fail_save", [False, True], ids=["success", "save-exception"])
def test_forward_fixture_constructs_on_cpu_under_meta_context(
        tmp_path, monkeypatch, accelerator_seed_calls, fail_save):
    import torch
    from src.activity.predictor import ActivityPredictor
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
    from tests.family_model_test_support import make_forward_bundle

    caller_device = torch.empty(0).device
    with torch.device("cpu"):
        registry, models = make_forward_bundle(
            tmp_path, monkeypatch, family="PDE", bundle_id="cpu-reference")
        expected = [torch.load(registry.models_dir / models[task]["weights_file"],
                               map_location="cpu", weights_only=True)["state_dict"]
                    for task in ("classification", "regression")]

    process_smiles, initialize, save = ActivityPredictor.process_smiles, RGNN.__init__, torch.save
    calls = dict(features=0, networks=0, saves=0)

    def cpu_features(self, smiles):
        pair = process_smiles(self, smiles)
        assert pair is not None, "Features must construct successfully under ambient meta"
        assert all(value.device.type == "cpu" for data in pair
                   for value in data.to_dict().values() if torch.is_tensor(value))
        calls["features"] += 1
        return pair

    def cpu_network(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        assert all(value.device.type == "cpu"
                   for value in (*self.parameters(), *self.buffers()))
        calls["networks"] += 1

    def cpu_save(payload, path):
        state = payload["state_dict"]
        reference = expected[calls["saves"]]
        assert state.keys() == reference.keys()
        assert all(value.device.type == "cpu" and torch.equal(value, reference[key])
                   for key, value in state.items())
        calls["saves"] += 1
        if fail_save and calls["saves"] == 2:
            raise RuntimeError("synthetic meta-context save failure")
        return save(payload, path)

    monkeypatch.setattr(ActivityPredictor, "process_smiles", cpu_features)
    monkeypatch.setattr(RGNN, "__init__", cpu_network)
    monkeypatch.setattr(torch, "save", cpu_save)
    rng, threads = torch.get_rng_state().clone(), torch.get_num_threads()
    with torch.device("meta"):
        try:
            if fail_save:
                with pytest.raises(RuntimeError, match="synthetic meta-context save failure"):
                    make_forward_bundle(tmp_path, monkeypatch, family="PDE", bundle_id="meta-caller")
            else:
                make_forward_bundle(tmp_path, monkeypatch, family="PDE", bundle_id="meta-caller")
        finally:
            assert torch.empty(0).device.type == "meta"
            assert torch.equal(torch.get_rng_state(), rng)
            assert torch.get_num_threads() == threads
    assert torch.empty(0).device == caller_device
    assert calls == dict(features=1, networks=2, saves=2)
    assert accelerator_seed_calls == []


# Task 2 uses tiny sealed contract artifacts; no network or weight loading.
@pytest.fixture(scope="module")
def sealed_snapshot_files(tmp_path_factory):
    """Seal tiny synthetic records once; each negative case gets private bytes."""
    from src.activity.model_registry import ActivityModelRegistry
    from tests.family_model_test_support import make_package, make_pair, register_bundle

    root = tmp_path_factory.mktemp("sealed-snapshot-seed")
    with pytest.MonkeyPatch.context() as patch:
        package = make_package(root, patch)
        registry = ActivityModelRegistry(root / "source")
        selected = make_pair(registry, package, prefix="older")
        register_bundle(registry, package, selected, "older-bundle")
        newer = make_pair(registry, package, prefix="newer")
        register_bundle(registry, package, newer, "newer-bundle")
        registry.select_family_bundle("newer-bundle")
    files = {registry.state_path.name: registry.state_path.read_bytes()}
    for model in (*selected.values(), *newer.values()):
        for field in ("weights_file", "model_card_file"):
            files[model[field]] = (registry.models_dir / model[field]).read_bytes()
    return files


@pytest.fixture
def snapshot_source(tmp_path, sealed_snapshot_files):
    import json
    from src.activity.model_registry import REGISTRY_STATE_FILE
    from tests.family_real_acceptance_support import AcceptanceConfig

    source = tmp_path / "source"
    source.mkdir()
    for name, content in sealed_snapshot_files.items():
        (source / name).write_bytes(content)
    config = AcceptanceConfig(source, "older-bundle", "buche-bundle")
    state = json.loads(sealed_snapshot_files[REGISTRY_STATE_FILE])
    selected = {task: state["models"]["older-" + task] for task in ("classification", "regression")}
    return config, state, selected


def save_source_state(config, state):
    import json
    from src.activity.model_registry import REGISTRY_STATE_FILE

    (config.source / REGISTRY_STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


def snapshot_api():
    from tests import family_real_acceptance_support as support

    assert callable(getattr(support, "snapshot_family", None)), "Task2 snapshot_family is missing"
    assert callable(getattr(support, "verify_source", None)), "Task2 verify_source is missing"
    return support


def test_snapshot_pins_older_bundle_and_never_opens_unselected_assets(
        snapshot_source, tmp_path, monkeypatch):
    import builtins
    import hashlib
    import io
    import json
    import os
    from src.activity.model_registry import ActivityModelRegistry, REGISTRY_STATE_FILE

    support = snapshot_api()
    config, state, selected = snapshot_source
    destination = tmp_path / "snapshot"
    names = {REGISTRY_STATE_FILE} | {item[field] for item in selected.values()
                                   for field in ("weights_file", "model_card_file")}
    before = {name: hashlib.sha256((config.source / name).read_bytes()).hexdigest() for name in names}
    original_init, original_select = ActivityModelRegistry.__init__, ActivityModelRegistry.select_family_bundle
    initial_states, selections = [], []

    def initialize(self, directory):
        assert Path(directory) == destination, "Must never construct the source registry"
        assert {p.name for p in destination.iterdir()} == names
        initial_states.append(json.loads((destination / REGISTRY_STATE_FILE).read_bytes()))
        original_init(self, directory)

    def select(self, bundle_id):
        selections.append(bundle_id)
        return original_select(self, bundle_id)

    def guard_open(original, low_level=False):
        def guarded(file, mode="r", *args, **kwargs):
            if not isinstance(file, int):
                path = Path(file)
                assert path.suffix.lower() != ".csv", "Sealed data must not be reopened"
                if path.is_relative_to(config.source):
                    assert path.parent == config.source and path.name in names
                    if low_level:
                        assert not mode & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                    else:
                        assert not any(flag in mode for flag in "wax+")
            return original(file, mode, *args, **kwargs)
        return guarded

    def no_scan(*args, **kwargs):
        raise AssertionError("Source discovery is forbidden")

    original_scandir, original_listdir = os.scandir, os.listdir

    def guard_scan(original):
        def guarded(path):
            assert not Path(path).is_relative_to(config.source), "Source scans are forbidden"
            return original(path)
        return guarded

    with monkeypatch.context() as patch:
        patch.setattr(ActivityModelRegistry, "__init__", initialize)
        patch.setattr(ActivityModelRegistry, "select_family_bundle", select)
        patch.setattr(ActivityModelRegistry, "register_family_bundle", no_scan)
        patch.setattr(builtins, "open", guard_open(builtins.open))
        patch.setattr(io, "open", guard_open(io.open))
        patch.setattr(os, "open", guard_open(os.open, True))
        patch.setattr(os, "scandir", guard_scan(original_scandir))
        patch.setattr(os, "listdir", guard_scan(original_listdir))
        patch.setattr(Path, "glob", no_scan)
        snapshot = support.snapshot_family(config, "pde-family", destination)
        assert support.verify_source(config, snapshot) is True
    assert snapshot.bundle_id == "older-bundle"
    assert snapshot.family_id == "pde-family"
    assert snapshot.models_dir == destination
    for task in ("classification", "regression"):
        for key in ("model_id", "weights_sha256", "model_card_sha256"):
            assert snapshot.expected_models[task][key] == selected[task][key]
    assert initial_states == [dict(version=3, models={m["model_id"]: m for m in selected.values()},
                                   active_model_id=None, active_models_by_endpoint={},
                                   family_bundles={"older-bundle": state["family_bundles"]["older-bundle"]},
                                   active_family_bundles={})]
    assert selections == ["older-bundle"]
    assert json.loads((config.source / REGISTRY_STATE_FILE).read_bytes()) == state
    assert before == {name: hashlib.sha256((config.source / name).read_bytes()).hexdigest() for name in names}
    assert set(snapshot.source_digests) == {"registry", "classification.weights", "classification.card",
                                            "regression.weights", "regression.card"}
    assert snapshot.source_digests["registry"] == before[REGISTRY_STATE_FILE]
    assert support.FamilySnapshot.__dataclass_params__.repr is False
    with pytest.raises(FrozenInstanceError):
        snapshot.bundle_id = "newer-bundle"


@pytest.mark.parametrize("fault,code", [
    ("wrong-family", "bundle_mismatch"), ("missing-stage", "bundle_mismatch"),
    ("duplicate-model", "bundle_mismatch"), ("unknown-bundle", "bundle_mismatch"),
    ("model-identity", "bundle_mismatch"), ("unbound-model", "bundle_mismatch"),
    ("version", "invalid_registry"), ("bool-version", "invalid_registry"),
    ("bad-hash", "asset_digest_mismatch"), ("sealed-record", "bundle_mismatch"),
    ("sealed-data", "bundle_mismatch"), ("missing-record", "bundle_mismatch"),
])
def test_snapshot_rejects_bad_selection(snapshot_source, tmp_path, fault, code):
    support = snapshot_api()
    config, state, selected = snapshot_source
    bundle = state["family_bundles"]["older-bundle"]
    model = state["models"][selected["classification"]["model_id"]]
    if fault == "wrong-family":
        bundle["family_id"] = "buche-family"
    elif fault == "missing-stage":
        del bundle["models"]["regression"]
    elif fault == "duplicate-model":
        bundle["models"]["regression"] = bundle["models"]["classification"]
    elif fault == "unknown-bundle":
        del state["family_bundles"]["older-bundle"]
    elif fault == "model-identity":
        model["model_id"] = "not-the-key"
    elif fault == "unbound-model":
        model["weights_file"] = "newer-classification.pt"
    elif fault == "missing-record":
        del state["models"][model["model_id"]]
    elif fault in ("version", "bool-version"):
        state["version"] = 99 if fault == "version" else True
    elif fault == "bad-hash":
        (config.source / model["weights_file"]).write_bytes(b"corrupt")
    elif fault == "sealed-record":
        bundle["assignment_sha256"] = "0" * 64
    elif fault == "sealed-data":
        bundle["family_dataset_snapshot"] += " "
    save_source_state(config, state)
    with pytest.raises(ValueError, match=f"^{code}$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


@pytest.mark.parametrize("field", ["weights_file", "model_card_file"])
@pytest.mark.parametrize("name", ["../escape", r"sub\escape", "/escape", "C:escape", "x:stream",
                                  "NUL.pt", "COM1", "registry_state.json", "REGISTRY_STATE.LOCK",
                                  "older-regression_info.json", "x. "])
def test_snapshot_rejects_asset_paths_before_writes(snapshot_source, tmp_path, field, name):
    support = snapshot_api()
    config, state, selected = snapshot_source
    model = state["models"][selected["classification"]["model_id"]]
    model[field] = name
    state["family_bundles"]["older-bundle"]["models"]["classification"][field] = name
    save_source_state(config, state)
    destination = tmp_path / "copy"
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", destination)
    assert not destination.exists()


@pytest.mark.parametrize("case_only", [False, True])
def test_snapshot_rejects_shared_asset_names(snapshot_source, tmp_path, case_only):
    support = snapshot_api()
    config, state, selected = snapshot_source
    name = selected["classification"]["weights_file"]
    name = name.upper() if case_only else name
    state["models"][selected["regression"]["model_id"]]["weights_file"] = name
    state["family_bundles"]["older-bundle"]["models"]["regression"]["weights_file"] = name
    save_source_state(config, state)
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


@pytest.mark.parametrize("asset", ["registry", "card", "weights"])
@pytest.mark.parametrize("fault", ["directory", "oversize"])
def test_snapshot_asset_types_and_limits(snapshot_source, tmp_path, monkeypatch, asset, fault):
    from src.activity.model_registry import REGISTRY_STATE_FILE

    support = snapshot_api()
    config, _, selected = snapshot_source
    name = {"registry": REGISTRY_STATE_FILE, "card": selected["classification"]["model_card_file"],
            "weights": selected["classification"]["weights_file"]}[asset]
    path = config.source / name
    if fault == "directory":
        path.unlink()
        path.mkdir()
    else:
        monkeypatch.setattr(support, {"registry": "REGISTRY_LIMIT", "card": "CARD_LIMIT",
                                     "weights": "WEIGHTS_LIMIT"}[asset], path.stat().st_size - 1)
    with pytest.raises(ValueError, match="^" + ("unsafe_source" if fault == "directory" else "asset_limit_exceeded") + "$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


@pytest.mark.parametrize("asset", ["registry", "card"])
@pytest.mark.parametrize("fault", ["duplicate", "nan", "infinity", "overflow", "depth", "invalid"])
def test_snapshot_strict_json(snapshot_source, tmp_path, asset, fault):
    import hashlib
    from src.activity.model_registry import REGISTRY_STATE_FILE

    support = snapshot_api()
    config, state, selected = snapshot_source
    path = config.source / (REGISTRY_STATE_FILE if asset == "registry" else selected["classification"]["model_card_file"])
    additions = {"duplicate": '"extra":1,"extra":2', "nan": '"extra":NaN',
                 "infinity": '"extra":Infinity', "overflow": '"extra":1e999',
                 "depth": '"extra":' + '[' * 33 + '0' + ']' * 33}
    content = b"not json" if fault == "invalid" else ("{" + additions[fault] + ",").encode() + path.read_bytes()[1:]
    path.write_bytes(content)
    if asset == "card":
        digest = hashlib.sha256(content).hexdigest()
        state["models"][selected["classification"]["model_id"]]["model_card_sha256"] = digest
        state["family_bundles"]["older-bundle"]["models"]["classification"]["model_card_sha256"] = digest
        save_source_state(config, state)
    with pytest.raises(ValueError, match="^invalid_registry$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


@pytest.mark.parametrize("location", ["same", "inside", "above"])
def test_snapshot_rejects_overlap_before_writes(snapshot_source, location):
    support = snapshot_api()
    config, _, _ = snapshot_source
    destination = {"same": config.source, "inside": config.source / "nested", "above": config.source.parent}[location]
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", destination)
    assert not (config.source / "nested").exists()


@pytest.mark.parametrize("location", ["root", "ancestor", "leaf"])
def test_snapshot_rejects_symlinks(snapshot_source, tmp_path, location):
    import os
    from dataclasses import replace

    support = snapshot_api()
    config, _, selected = snapshot_source
    link = tmp_path / "link"
    target = config.source if location == "root" else config.source.parent
    if location == "leaf":
        link = config.source / selected["classification"]["weights_file"]
        target = tmp_path / "moved-weight"
        link.rename(target)
    try:
        link.symlink_to(target, target_is_directory=location != "leaf")
    except OSError:
        if os.name != "nt":
            raise
        pytest.skip("Windows symlink creation privilege unavailable; reparse test runs separately")
    if location != "leaf":
        config = replace(config, source=link if location == "root" else link / "source")
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


@pytest.mark.parametrize("location", ["root", "ancestor", "leaf"])
def test_snapshot_rejects_reparse_attributes(snapshot_source, tmp_path, monkeypatch, location):
    from types import SimpleNamespace

    support = snapshot_api()
    config, _, selected = snapshot_source
    target = {"root": config.source, "ancestor": config.source.parent,
              "leaf": config.source / selected["classification"]["weights_file"]}[location]
    original = Path.lstat

    def reparse(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == target:
            fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            fields["st_file_attributes"] = 0x400
            return SimpleNamespace(**fields)
        return value

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


@pytest.mark.parametrize("asset", ["registry", "classification.weights", "classification.card", "regression.weights", "regression.card"])
def test_verify_source_detects_each_changed_asset(snapshot_source, tmp_path, asset):
    from src.activity.model_registry import REGISTRY_STATE_FILE

    support = snapshot_api()
    config, _, selected = snapshot_source
    snapshot = support.snapshot_family(config, "pde-family", tmp_path / "copy")
    if asset == "registry":
        name = REGISTRY_STATE_FILE
    else:
        task, kind = asset.split(".")
        name = selected[task]["weights_file" if kind == "weights" else "model_card_file"]
    with (config.source / name).open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(ValueError, match="^source_changed$"):
        support.verify_source(config, snapshot)


@pytest.mark.parametrize("field,value", [("bundle_id", "newer-bundle"), ("family_id", "buche-family"),
                                         ("expected_models", {}), ("source_digests", {})])
def test_verify_source_rejects_snapshot_identity_tampering(snapshot_source, tmp_path, field, value):
    from dataclasses import replace

    support = snapshot_api()
    config, _, _ = snapshot_source
    snapshot = support.snapshot_family(config, "pde-family", tmp_path / "copy")
    with pytest.raises(ValueError, match="^(bundle_mismatch|source_changed)$"):
        support.verify_source(config, replace(snapshot, **{field: value}))


@pytest.mark.parametrize("when", ["before-verify", "after-registry-read", "during-asset-reads"])
def test_verify_source_never_follows_redirected_bundle_assets(snapshot_source, tmp_path, monkeypatch, when):
    import builtins
    from copy import deepcopy
    import io
    import os
    from src.activity.model_registry import REGISTRY_STATE_FILE

    support = snapshot_api()
    config, state, selected = snapshot_source
    snapshot = support.snapshot_family(config, "pde-family", tmp_path / "copy")
    redirected = deepcopy(state["family_bundles"]["newer-bundle"])
    redirected["bundle_id"] = snapshot.bundle_id
    pinned_names = {item[field] for item in selected.values()
                    for field in ("weights_file", "model_card_file")}
    newer_names = {item[field] for item in redirected["models"].values()
                   for field in ("weights_file", "model_card_file")}
    assert pinned_names.isdisjoint(newer_names)
    forbidden_opens, pinned_opens, mutations = [], [], []

    def redirect():
        state["family_bundles"][snapshot.bundle_id] = redirected
        save_source_state(config, state)
        mutations.append(True)

    def guard_open(original):
        def guarded(file, *args, **kwargs):
            if not isinstance(file, int):
                path = Path(file)
                if path.is_relative_to(config.source):
                    if path.name in newer_names:
                        forbidden_opens.append(path.name)
                        raise AssertionError("verify_source must not open redirected assets")
                    if path.name in pinned_names:
                        pinned_opens.append(path.name)
                    assert path.parent == config.source
                    assert path.name in pinned_names | {REGISTRY_STATE_FILE}
            return original(file, *args, **kwargs)
        return guarded

    bounded_file = support._bounded_file

    def read_then_redirect(path, *args, **kwargs):
        result = bounded_file(path, *args, **kwargs)
        if not mutations and ((when == "after-registry-read" and path.name == REGISTRY_STATE_FILE)
                              or (when == "during-asset-reads" and path.name in pinned_names)):
            redirect()
        return result

    if when == "before-verify":
        redirect()
    monkeypatch.setattr(builtins, "open", guard_open(builtins.open))
    monkeypatch.setattr(io, "open", guard_open(io.open))
    monkeypatch.setattr(os, "open", guard_open(os.open))
    monkeypatch.setattr(support, "_bounded_file", read_then_redirect)
    with pytest.raises(ValueError, match="^source_changed$"):
        support.verify_source(config, snapshot)
    assert mutations == [True]
    assert forbidden_opens == []
    if when == "before-verify":
        assert pinned_opens == [], "A changed registry must fail before any asset open"


def test_snapshot_limits_are_fixed():
    support = snapshot_api()
    assert (support.REGISTRY_LIMIT, support.CARD_LIMIT, support.WEIGHTS_LIMIT,
            support.READ_CHUNK, support.JSON_DEPTH) == (16 << 20, 2 << 20, 512 << 20, 1 << 20, 32)


@pytest.mark.parametrize("phase", ["during-copy", "after-copy", "final"])
def test_snapshot_rechecks_source_through_completion(snapshot_source, tmp_path, monkeypatch, phase):
    import os
    from src.activity.model_registry import ActivityModelRegistry

    support = snapshot_api()
    config, _, selected = snapshot_source
    target = config.source / selected["classification"]["weights_file"]
    original_open, original_init, original_select = os.open, ActivityModelRegistry.__init__, ActivityModelRegistry.select_family_bundle
    mutated = []

    def mutate():
        if not mutated:
            with target.open("ab") as handle:
                handle.write(b"changed")
            mutated.append(True)

    def opening(path, flags, *args, **kwargs):
        # A destination exclusive asset creation occurs after the baseline hashes.
        if phase == "during-copy" and Path(path).parent == tmp_path / "copy" and flags & os.O_EXCL:
            mutate()
        return original_open(path, flags, *args, **kwargs)

    def initialize(self, directory):
        if phase == "after-copy":
            mutate()
        original_init(self, directory)

    def select(self, bundle_id):
        result = original_select(self, bundle_id)
        if phase == "final":
            mutate()
        return result

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(ActivityModelRegistry, "__init__", initialize)
    monkeypatch.setattr(ActivityModelRegistry, "select_family_bundle", select)
    with pytest.raises(ValueError, match="^source_changed$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")
    assert mutated


@pytest.mark.parametrize("task", ["classification", "regression"])
def test_snapshot_final_recheck_rejects_registry_change_during_asset_reads(
        snapshot_source, tmp_path, monkeypatch, task):
    from src.activity.model_registry import ActivityModelRegistry, REGISTRY_STATE_FILE

    support = snapshot_api()
    config, _, selected = snapshot_source
    registry_path = config.source / REGISTRY_STATE_FILE
    asset_names = [selected[stage][field] for stage in ("classification", "regression")
                   for field in ("weights_file", "model_card_file")]
    target = selected[task]["weights_file"]
    original_select = ActivityModelRegistry.select_family_bundle
    original_read = support._bounded_file
    final_pass, changed, final_reads = [], [], []

    def select_then_arm(self, bundle_id):
        result = original_select(self, bundle_id)
        final_pass.append(True)
        return result

    def read_then_change_registry(path, *args, **kwargs):
        assert path.parent == config.source
        assert path.name in {REGISTRY_STATE_FILE, *asset_names}
        result = original_read(path, *args, **kwargs)
        if final_pass:
            final_reads.append(path.name)
            if path.name == target and not changed:
                with registry_path.open("ab") as handle:
                    handle.write(b"\n")
                changed.append(True)
        return result

    monkeypatch.setattr(ActivityModelRegistry, "select_family_bundle", select_then_arm)
    monkeypatch.setattr(support, "_bounded_file", read_then_change_registry)
    with pytest.raises(ValueError, match="^source_changed$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")
    assert changed == [True]
    assert final_reads == [REGISTRY_STATE_FILE, *asset_names, REGISTRY_STATE_FILE]


@pytest.mark.parametrize("fault", ["identity", "growth"])
def test_snapshot_checks_open_handle_before_read(snapshot_source, tmp_path, monkeypatch, fault):
    import os
    from types import SimpleNamespace

    support = snapshot_api()
    config, _, _ = snapshot_source
    original = os.fstat

    def changed(fd):
        value = original(fd)
        fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
        if fault == "identity":
            fields["st_ino"] += 1
        else:
            fields["st_size"] = support.REGISTRY_LIMIT + 1
        return SimpleNamespace(**fields)

    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(ValueError, match="^(source_changed|asset_limit_exceeded)$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


def test_snapshot_hashes_all_five_sources_before_after_copy_and_final(
        snapshot_source, tmp_path, monkeypatch):
    import os
    from src.activity.model_registry import ActivityModelRegistry, REGISTRY_STATE_FILE

    support = snapshot_api()
    config, _, selected = snapshot_source
    names = {REGISTRY_STATE_FILE} | {m[k] for m in selected.values() for k in ("weights_file", "model_card_file")}
    counts = dict.fromkeys(names, 0)
    checkpoints = []
    original_open, original_init = os.open, ActivityModelRegistry.__init__

    def opening(path, flags, *args, **kwargs):
        path = Path(path)
        if path.parent == config.source and path.name in counts:
            counts[path.name] += 1
        if path.parent == tmp_path / "copy" and flags & os.O_EXCL and not checkpoints:
            checkpoints.append(counts.copy())
        return original_open(path, flags, *args, **kwargs)

    def initialize(self, directory):
        checkpoints.append(counts.copy())
        original_init(self, directory)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(ActivityModelRegistry, "__init__", initialize)
    support.snapshot_family(config, "pde-family", tmp_path / "copy")
    assert len(checkpoints) == 2
    assert all(value >= 1 for value in checkpoints[0].values())
    assert all(checkpoints[1][name] > checkpoints[0][name] for name in names)
    assert all(counts[name] > checkpoints[1][name] for name in names)


@pytest.mark.parametrize("source", ["relative", "../models", r"C:models", r"\\server\share\models",
                                     r"\\?\C:\models", r"\\.\C:\models", "parent", "ads", "device"])
def test_snapshot_rejects_nonlocal_source_without_open(tmp_path, monkeypatch, source):
    import os

    support = snapshot_api()
    source = {"parent": tmp_path / "x" / ".." / "models", "ads": tmp_path / "x:stream",
              "device": tmp_path / "NUL"}.get(source, Path(source))

    def forbidden(*args, **kwargs):
        raise AssertionError("Unsafe source must fail before any open")

    monkeypatch.setattr(os, "open", forbidden)
    config = support.AcceptanceConfig(source, "pde", "buche")
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")
    assert not (tmp_path / "copy").exists()


def test_snapshot_never_overwrites_existing_destination(snapshot_source, tmp_path):
    support = snapshot_api()
    config, _, _ = snapshot_source
    destination = tmp_path / "copy"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_bytes(b"untouched")
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", destination)
    assert sentinel.read_bytes() == b"untouched"


def test_snapshot_rejects_directory_identity_alias_before_writes(snapshot_source, tmp_path, monkeypatch):
    """Lexically different paths can identify the same directory (e.g. 8.3 names)."""
    support = snapshot_api()
    config, _, _ = snapshot_source
    alias = tmp_path / "source-alias"
    alias.mkdir()
    destination = alias / "copy"
    original = Path.lstat
    source_info = original(config.source)

    def alias_stat(path, *args, **kwargs):
        return source_info if path == alias else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", alias_stat)
    with pytest.raises(ValueError, match="^unsafe_source$"):
        support.snapshot_family(config, "pde-family", destination)
    assert not destination.exists()


def test_snapshot_buche_uses_only_explicit_buche_id(tmp_path, monkeypatch):
    from src.activity.model_registry import ActivityModelRegistry
    from tests.family_model_test_support import make_package, make_pair, register_bundle

    support = snapshot_api()
    package = make_package(tmp_path, monkeypatch, "BuChE", "synthetic-buche")
    registry = ActivityModelRegistry(tmp_path / "source")
    models = make_pair(registry, package)
    register_bundle(registry, package, models, "explicit-buche")
    config = support.AcceptanceConfig(registry.models_dir, "absent-pde", "explicit-buche")
    snapshot = support.snapshot_family(config, "buche-family", tmp_path / "copy")
    assert snapshot.bundle_id == "explicit-buche"
    assert snapshot.expected_models == models
    assert support.verify_source(config, snapshot)


@pytest.mark.parametrize("field,value", [("model_id", "other"), ("weights_sha256", "0" * 64),
                                         ("source_sha256", "0" * 64), ("random_seed", True)])
def test_snapshot_copy_registry_independently_validates_actual_card(snapshot_source, tmp_path, field, value):
    import hashlib
    import json

    support = snapshot_api()
    config, state, selected = snapshot_source
    model = selected["classification"]
    path = config.source / model["model_card_file"]
    card = json.loads(path.read_bytes())
    card[field] = value
    path.write_text(json.dumps(card), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # Both registry records coherently claim the new bytes; digest checks alone
    # cannot catch the discrepancy between the actual card and model metadata.
    state["models"][model["model_id"]]["model_card_sha256"] = digest
    state["family_bundles"]["older-bundle"]["models"]["classification"]["model_card_sha256"] = digest
    save_source_state(config, state)
    with pytest.raises(ValueError, match="^bundle_mismatch$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")


@pytest.mark.parametrize("phase", ["stream", "post-read"])
def test_snapshot_checks_stream_growth_and_post_read_identity(snapshot_source, tmp_path, monkeypatch, phase):
    import os
    from types import SimpleNamespace

    support = snapshot_api()
    config, _, _ = snapshot_source
    original_fdopen, original_fstat = os.fdopen, os.fstat
    stats, reads = [], []

    def fstat(fd):
        value = original_fstat(fd)
        stats.append(fd)
        if phase == "post-read" and len(stats) == 2:
            fields = {key: getattr(value, key) for key in dir(value) if key.startswith("st_")}
            fields["st_mtime_ns"] += 1
            return SimpleNamespace(**fields)
        return value

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self, size):
            reads.append(size)
            assert size == 1 << 20, "Source reads must be bounded 1 MiB binary chunks"
            return b"x" * size if phase == "stream" else self.handle.read(size)

    def fdopen(fd, mode):
        return Reader(original_fdopen(fd, mode))

    monkeypatch.setattr(os, "fstat", fstat)
    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(ValueError, match="^" + ("asset_limit_exceeded" if phase == "stream" else "source_changed") + "$"):
        support.snapshot_family(config, "pde-family", tmp_path / "copy")
    assert reads


def test_strict_json_depth32_and_escaped_strings_are_accepted():
    import json

    support = snapshot_api()
    # Root object + 31 arrays is exactly depth 32. Strings are not containers.
    content = ('{"nested":' + '[' * 31 + '0' + ']' * 31
               + ',"text":' + json.dumps('\\"' + '[' * 40) + '}').encode()
    assert support._strict_json(content)["text"] == '\\"' + '[' * 40


@pytest.mark.parametrize("entrypoint", ["snapshot_family", "verify_source"])
def test_snapshot_public_errors_do_not_expose_missing_dependency(tmp_path, monkeypatch, entrypoint):
    import builtins

    support = snapshot_api()
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "src.activity.family_models":
            raise ImportError("private dependency installation path")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    config = support.AcceptanceConfig(tmp_path / "source", "pde", "buche")
    with pytest.raises(ValueError, match="^dependency_unavailable$"):
        if entrypoint == "snapshot_family":
            support.snapshot_family(config, "pde-family", tmp_path / "copy")
        else:
            support.verify_source(config, support.FamilySnapshot(tmp_path / "copy", "pde-family", "pde", {}, {}))
