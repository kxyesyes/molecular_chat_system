"""Real RGNN execution on temporary, untrained synthetic test weights only."""
import hashlib
import math


def test_registered_pair_runs_real_forward_without_global_selection(tmp_path, monkeypatch):
    import torch
    from src.activity.model_registry import ActivityModelRegistry
    from src.activity.predictor import ActivityPredictor
    from src.activity.family_predictor import FamilyActivityPredictor
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
    from tests.family_model_test_support import make_package, make_pair, register_bundle

    threads = torch.get_num_threads()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    try:
        torch.set_num_threads(1)
        path = make_package(tmp_path, monkeypatch)
        registry = ActivityModelRegistry(tmp_path / "models")
        pair = ActivityPredictor().process_smiles("CCO")
        config = dict(in_channels=pair[0].x.shape[1], edge_dim=pair[0].edge_attr.shape[1],
                      channels=8, out_channels=1, num_passing_atom=2, num_passing_pool=1,
                      num_passing_rg=1, num_passing_mol=1, dropout=0.)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(71)
            def real_test_weights(registry, metadata):
                network = RGNN(**config).eval()
                weights = registry.models_dir / metadata["weights_file"]
                torch.save({"state_dict": network.state_dict()}, weights)
                metadata.update(model_config=config, weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest())
            models = make_pair(registry, path, before_register=real_test_weights)
        register_bundle(registry, path, models)
        registry.select_family_bundle("bundle-a")
        assert registry.get_active_model_id() is None

        def no_dataset_reopen(*args, **kwargs):
            raise AssertionError("Inference must use sealed evidence, not reopen datasets")

        monkeypatch.setattr("src.activity.family_dataset.load_family_dataset", no_dataset_reopen)
        # Fresh registry instance also reads the persisted family selection.
        predictor = FamilyActivityPredictor(ActivityModelRegistry(registry.models_dir))
        rows = predictor.predict(["CCO", "CC(C)((", "CCN"], target="PDE5A")
        assert [row["status"] for row in rows] == ["passed", "failed", "passed"]
        for row in (row for row in rows if row["success"]):
            assert math.isfinite(row["predicted_pIC50"])
            assert 0 <= row["activity_probability"] <= 1
            assert row["provenance"]["models"]["regression"]["model_id"] == models["regression"]["model_id"]
        assert rows[1]["predicted_pIC50"] is None
        assert predictor.predict("CCO", target="BuChE")[0]["status"] == "failed"
        assert predictor.predict("CCN", target="PDE")[0]["success"]
        # Warm-cache inference must still reverify the actual model card.
        card = registry.models_dir / models["regression"]["model_card_file"]
        card.write_bytes(card.read_bytes() + b"\n")
        failed = predictor.predict("CCO", target="PDE")[0]
        assert failed["status"] == "failed"
        assert failed["predicted_pIC50"] is None
        assert failed["errors"]["bundle"]
    finally:
        torch.set_num_threads(threads)
