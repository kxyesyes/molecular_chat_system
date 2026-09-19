"""Synthetic package/card fixtures; no training or scientific performance claims."""
import hashlib
import json

from src.activity import family_contract as fc
from src.activity.family_dataset import prepare_family_dataset, load_family_dataset
from tests.activity_test_support import write_endpoint_model, write_card


def make_package(tmp_path, monkeypatch, family="PDE", package_id="synthetic-pde"):
    original = fc.build_family_manifest

    def small(family, dataset_id, task_type, **kwargs):
        return original(family, dataset_id, task_type,
                        minimum_unique_molecules=3, minimum_scaffolds=3)

    monkeypatch.setattr(fc, "build_family_manifest", small)
    source = tmp_path / (package_id + ".csv")
    rows = ["Smiles,pIC50"]
    for ring in ("c1ccccc1", "c1ccncc1", "C1CCCCC1"):
        rows.extend(f"{prefix}{ring},{value}" for prefix, value in
                    zip(("", "C", "CC", "CCC"), (4, 4.5, 5, 6)))
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")
    prepare_family_dataset(source, family=family, package_id=package_id,
                           output_dir=tmp_path / "datasets", validate_only=False)
    return tmp_path / "datasets" / package_id / "family_dataset.json"


def make_pair(registry, path, prefix="pair", overrides=None, card_overrides=None,
              before_register=None):
    """Optionally replace synthetic artifacts/config before card sealing."""
    descriptor = load_family_dataset(path)
    models = {}
    for task in ("classification", "regression"):
        child = descriptor["datasets"][task]
        manifest = json.loads((path.parent / child["path"]).read_bytes())
        item = write_endpoint_model(registry, f"{prefix}-{task}", descriptor["family_id"])
        item.update({key: manifest[key] for key in
                     ("target_id", "target_name", "task_type", "endpoint_key", "label_transform", "model_contract_key")})
        item.update(endpoint=manifest["output_endpoint"], units=manifest["output_units"],
                    source=manifest["source"], license=manifest["license"],
                    source_sha256=manifest["input_sha256"], dataset_sha256=child["manifest_sha256"],
                    prepared_dataset_sha256=child["prepared_dataset_sha256"],
                    dataset_split_seed=manifest["split"]["seed"], split_counts=manifest["split"]["counts"],
                    split_scaffold_counts=manifest["split"]["scaffold_counts"],
                    limitations=["Synthetic contract test only; not a scientific model."])
        if task == "classification":
            counts = descriptor["split_class_counts"]["test"]
            item["test_metrics"] = dict(roc_auc=.5, pr_auc=.5, balanced_accuracy=.5,
                                       confusion_matrix=dict(tn=counts["inactive"], fp=0,
                                                             fn=counts["active"], tp=0))
        item.update((overrides or {}).get(task, {}))
        if before_register is not None:
            before_register(registry, item)
        write_card(registry, item, **(card_overrides or {}).get(task, {}))
        models[task] = registry.register(item)
    return models


def register_bundle(registry, path, models, bundle_id="bundle-a"):
    return registry.register_family_bundle(
        bundle_id=bundle_id, family_dataset_path=path,
        classification_model_id=models["classification"]["model_id"],
        regression_model_id=models["regression"]["model_id"])


def make_forward_bundle(tmp_path, monkeypatch, *, family, bundle_id, constant_outputs=None):
    """Register untrained, real CPU RGNN weights under the caller's pytest tmp_path.

    Never accepts a source model directory or selects a global/family model.
    Multiple families/bundles can share this temporary registry. Metrics remain
    synthetic contract fixtures, not scientific performance measurements.
    """
    import torch
    from rdkit.Chem.SaltRemover import SaltRemover
    from src.activity.model_registry import ActivityModelRegistry
    from src.activity.predictor import ActivityPredictor
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN

    family_id = fc.resolve_activity_family(family)
    bundle_id = ActivityModelRegistry._validate_model_id(bundle_id)
    prefix = f"{family_id}-{bundle_id}"
    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.device("cpu"), torch.random.fork_rng(devices=[]):
            # fork_rng(devices=[]) restores CPU only; never seed/queue accelerators.
            torch.random.default_generator.manual_seed(71)
            path = make_package(tmp_path, monkeypatch, family, package_id=prefix)
            registry = ActivityModelRegistry(tmp_path / "models")
            # process_smiles only needs the salt remover. Bypass initialization
            # and all checkpoint discovery; reuse the scientific featurizer.
            features = ActivityPredictor.__new__(ActivityPredictor)
            features.remover = SaltRemover()
            pair = features.process_smiles("CCO")
            config = dict(in_channels=pair[0].x.shape[1], edge_dim=pair[0].edge_attr.shape[1],
                          channels=8, out_channels=1, num_passing_atom=2, num_passing_pool=1,
                          num_passing_rg=1, num_passing_mol=1, dropout=0.)

            def real_test_weights(registry, metadata):
                network = RGNN(**config).to(torch.device("cpu")).eval()
                if constant_outputs is not None:
                    # Deliberately untrained test heads: classification is a raw logit,
                    # regression a raw value. The full real graph forward still runs.
                    with torch.no_grad():
                        network.lin2.weight.zero_()
                        network.lin2.bias.fill_(constant_outputs[metadata["task_type"]])
                weights = registry.models_dir / metadata["weights_file"]
                torch.save({"state_dict": network.state_dict()}, weights)
                metadata.update(model_config=config, random_seed=71,
                                weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest())

            models = make_pair(registry, path, prefix=prefix, before_register=real_test_weights)
            register_bundle(registry, path, models, bundle_id=bundle_id)
        return registry, models
    finally:
        torch.set_num_threads(threads)
