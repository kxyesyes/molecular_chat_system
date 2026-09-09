"""V2 card consistency using tmp-only synthetic artifacts, never loaded weights."""
import copy
import hashlib
import json

import pytest

from src.activity.model_registry import ActivityModelRegistry
from tests.activity_test_support import write_card, write_endpoint_model


PROVENANCE = {
    "source": "Synthetic source A",
    "license": "Synthetic license A",
    "source_sha256": "d" * 64,
    "training_code": {"files": {"trainer.py": "e" * 64}, "source_sha256": "f" * 64},
    "data_quality_summary": {"counts": {"accepted_rows": 100}, "warnings": []},
    "prepared": {"manifest_sha256": "d" * 64},
    "model_contract_key": "synthetic-contract",
    "dataset_split_seed": 42,
    "requested_split_strategy": "scaffold",
    "test_prediction_summary": {"sample_count": 15, "mean": .5},
    "samples": 100,
    "best_epoch": 2,
    "epochs_completed": 4,
    "created_at": 1234.5,
    "split_warnings": [],
}


@pytest.fixture
def registry(tmp_path):
    instance = ActivityModelRegistry(tmp_path)
    existing = instance.register(write_endpoint_model(instance, "existing"))
    instance.select(existing["model_id"])
    instance.select_for_endpoint(existing["endpoint_key"], existing["model_id"])
    return instance


def _rewrite_card(registry, metadata, card):
    path = registry.models_dir / metadata["model_card_file"]
    content = json.dumps(card).encode("utf-8")
    path.write_bytes(content)
    metadata["model_card_sha256"] = hashlib.sha256(content).hexdigest()


def _card(registry, metadata):
    return json.loads((registry.models_dir / metadata["model_card_file"]).read_bytes())


def _assert_rejected_without_state_change(registry, metadata):
    before = registry.state_path.read_bytes()
    sidecar = registry.models_dir / "existing_info.json"
    before_sidecar = sidecar.read_bytes()
    original = copy.deepcopy(metadata)
    with pytest.raises(ValueError, match="card|metrics|JSON serializable"):
        registry.register(metadata)
    assert registry.state_path.read_bytes() == before
    assert sidecar.read_bytes() == before_sidecar
    assert metadata == original
    assert not (registry.models_dir / f"{metadata['model_id']}_info.json").exists()
    assert registry.get_active()["model_id"] == "existing"
    assert registry.get_active_for_endpoint(metadata["endpoint_key"])["model_id"] == "existing"


@pytest.mark.parametrize("field", PROVENANCE)
@pytest.mark.parametrize("mutation", ["conflict", "card_only", "metadata_only"])
def test_optional_provenance_cannot_conflict_or_disappear(registry, field, mutation):
    metadata = write_endpoint_model(registry)
    metadata[field] = copy.deepcopy(PROVENANCE[field])
    write_card(registry, metadata)
    card = _card(registry, metadata)
    if mutation == "conflict":
        card[field] = "Contradictory synthetic claim"
    elif mutation == "card_only":
        del metadata[field]
    else:
        del card[field]
    _rewrite_card(registry, metadata, card)
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("field", ["training_code", "data_quality_summary", "prepared"])
def test_nested_provenance_conflict(registry, field):
    metadata = write_endpoint_model(registry)
    metadata[field] = copy.deepcopy(PROVENANCE[field])
    write_card(registry, metadata)
    card = _card(registry, metadata)
    if field == "training_code":
        card[field]["files"]["trainer.py"] = "0" * 64
    elif field == "data_quality_summary":
        card[field]["counts"]["accepted_rows"] = 99
    else:
        card[field]["manifest_sha256"] = "0" * 64
    _rewrite_card(registry, metadata, card)
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("alias", ["validation_metrics", "best_metrics"])
@pytest.mark.parametrize("side", ["card", "metadata", "both"])
def test_validation_metric_alias_must_agree_with_metrics(registry, alias, side):
    metadata = write_endpoint_model(registry)
    if side in {"metadata", "both"}:
        metadata[alias] = {"rmse": 0.}
    write_card(registry, metadata)
    card = _card(registry, metadata)
    card[alias] = {"rmse": 0. if side in {"card", "both"} else .8}
    _rewrite_card(registry, metadata, card)
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("alias", ["validation_metrics", "best_metrics"])
def test_metadata_metric_alias_cannot_disappear_from_card(registry, alias):
    metadata = write_endpoint_model(registry)
    metadata[alias] = copy.deepcopy(metadata["metrics"])
    write_card(registry, metadata)
    card = _card(registry, metadata)
    del card[alias]
    _rewrite_card(registry, metadata, card)
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("metrics", [
    {"rmse": True}, {"rmse": "0.8"}, {"rmse": None}, {"rmse": []},
    {"rmse": -.1}, {"mae": -.1}, {"r2": 1.1},
    {"val_loss": "0.2"}, {"roc_auc": 1.1}, {"pr_auc": -.1},
    {"balanced_accuracy": 2.}, {"confusion_matrix": {"tn": 15}},
])
def test_matching_but_illegal_validation_metrics_rejected(registry, metrics):
    metadata = write_endpoint_model(registry)
    metadata.update(metrics=metrics, best_metrics=copy.deepcopy(metrics))
    write_card(registry, metadata, validation_metrics=copy.deepcopy(metrics))
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("invalid", [True, "0.8", None, [], float("nan"), float("inf")])
def test_card_only_validation_metrics_require_finite_numbers(registry, invalid):
    metadata = write_endpoint_model(registry)
    # True == 1.0 in Python: equality alone must not legitimize a boolean metric.
    metadata["metrics"] = {"rmse": 1. if invalid is True else .8}
    write_card(registry, metadata, validation_metrics={"rmse": invalid})
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("mutation", ["conflict", "metadata_only"])
def test_metadata_limitations_cannot_conflict_or_disappear(registry, mutation):
    metadata = write_endpoint_model(registry)
    metadata["limitations"] = ["Synthetic data only."]
    write_card(registry, metadata)
    card = _card(registry, metadata)
    if mutation == "conflict":
        card["limitations"] = []
    else:
        del card["limitations"]
    _rewrite_card(registry, metadata, card)
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("field,value", [
    ("model_card_file", "another-card.json"), ("model_card_sha256", "0" * 64),
])
def test_optional_card_artifact_claim_cannot_contradict_record(registry, field, value):
    metadata = write_endpoint_model(registry)
    write_card(registry, metadata, **{field: value})
    _assert_rejected_without_state_change(registry, metadata)


@pytest.mark.parametrize("style", ["minimal_v2", "builder", "publisher", "metadata_alias"])
@pytest.mark.parametrize("task", ["regression", "classification"])
def test_consistent_cards_register_and_select(registry, style, task):
    metadata = write_endpoint_model(registry)
    if task == "classification":
        metadata.update(task_type=task, endpoint="active", units="binary",
                        endpoint_key="target-a:active:binary:classification",
                        metrics={"roc_auc": .7, "pr_auc": .6, "balanced_accuracy": .6,
                                 "confusion_matrix": {"tn": 4, "fp": 3, "fn": 2, "tp": 6}})
        metadata["test_metrics"] = copy.deepcopy(metadata["metrics"])
    if style in {"publisher", "metadata_alias"}:
        metadata.update(copy.deepcopy(PROVENANCE))
        metadata["best_metrics"] = copy.deepcopy(metadata["metrics"])
    if style == "metadata_alias":
        metadata["validation_metrics"] = copy.deepcopy(metadata["metrics"])
        metadata["limitations"] = ["Synthetic contract test only."]
    write_card(registry, metadata)
    if style != "minimal_v2":
        from src.activity.model_card import build_model_card

        # Match publication ordering: card is built before the record gets paths/hash.
        inputs = {k: v for k, v in metadata.items()
                  if k not in {"model_card_file", "model_card_sha256"}}
        card = build_model_card(inputs, metadata["metrics"], metadata["test_metrics"],
                                ["Synthetic contract test only."])
        _rewrite_card(registry, metadata, card)
    original = copy.deepcopy(metadata)
    assert registry.register(metadata) == original
    registry.select_for_endpoint(metadata["endpoint_key"], metadata["model_id"])
    reloaded = ActivityModelRegistry(registry.models_dir)
    assert reloaded.get_active_for_endpoint(metadata["endpoint_key"]) == original
    assert reloaded.get_active()["model_id"] == "existing"
    assert metadata == original
