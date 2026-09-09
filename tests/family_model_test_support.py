"""Synthetic package/card fixtures; no training or scientific performance claims."""
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
