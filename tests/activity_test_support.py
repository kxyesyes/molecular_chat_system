"""Synthetic registry contract fixtures; never usable scientific checkpoints."""
import hashlib
import json


def legacy_metadata(model_id, weights_file):
    return dict(model_id=model_id, weights_file=weights_file, task_type="regression",
                endpoint="pIC50", units="pIC50", dataset_sha256="a" * 64,
                split_strategy="scaffold", random_seed=42, model_config={"channels": 16},
                model_format="pytorch_state_dict", weights_sha256="0" * 64,
                metrics={"rmse": 0.8})


def endpoint_metadata(model_id="model-a", target_id="target-a"):
    return dict(legacy_metadata(model_id, f"{model_id}.pt"), target_id=target_id,
                target_name=f"Controlled {target_id}",
                endpoint_key=f"{target_id}:pic50:pic50:regression",
                label_transform="identity", prepared_dataset_sha256="b" * 64,
                split_counts={"train": 70, "validation": 15, "test": 15},
                split_scaffold_counts={"train": 50, "validation": 10, "test": 10},
                test_metrics={"rmse": 0.8, "mae": 0.6, "r2": 0.5},
                model_card_file=f"{model_id}_model_card.json", model_card_sha256="c" * 64,
                scientific_readiness="endpoint_ready", demo_mode=False, fallback_used=False)


def write_endpoint_model(registry, model_id="model-a", target_id="target-a"):
    metadata = endpoint_metadata(model_id, target_id)
    weights = registry.models_dir / metadata["weights_file"]
    weights.write_bytes(b"SYNTHETIC CONTRACT TEST ONLY - NOT TORCH WEIGHTS")
    metadata["weights_sha256"] = hashlib.sha256(weights.read_bytes()).hexdigest()
    write_card(registry, metadata)
    return metadata


def write_card(registry, metadata, **overrides):
    card = {k: v for k, v in metadata.items() if k not in {"model_card_sha256", "model_card_file"}}
    card.update(overrides)
    path = registry.models_dir / metadata["model_card_file"]
    path.write_text(json.dumps(card), encoding="utf-8")
    metadata["model_card_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def register_endpoint_model(registry, model_id="model-a", target_id="target-a"):
    return registry.register(write_endpoint_model(registry, model_id, target_id))
