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
