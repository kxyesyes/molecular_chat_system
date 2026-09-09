"""Metadata hand-off tests; synthetic bytes are not scientifically trained models."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from src.activity import trainer


@pytest.fixture
def builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    models = tmp_path / "models"
    models.mkdir()
    source = tmp_path / "prepared-manifest.json"
    source.write_bytes(b'{"synthetic_contract_fixture":true}')
    weights = models / "synthetic.pt"
    weights.write_bytes(b"synthetic-metadata-contract-only")
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(models))
    kwargs = dict(
        model_id="synthetic-model", weights_file=weights.name,
        task_type="regression", target_column="normalized_value",
        file_path=str(source), samples=100, best_metrics={"rmse": 0.8},
        model_config={"in_channels": 8, "edge_dim": 4, "channels": 16},
        endpoint="pIC50", units="pIC50", requested_split_strategy="scaffold",
        actual_split_strategy="scaffold", random_seed=42, created_at=100.0,
    )
    return kwargs, source, weights


def endpoint_metadata():
    return {
        "target_id": "target-a", "target_name": "Synthetic target A",
        "endpoint_key": "target-a:pic50:pic50:regression",
        "label_transform": "identity", "prepared_dataset_sha256": "b" * 64,
        "split_counts": {"train": 70, "validation": 15, "test": 15},
        "split_scaffold_counts": {"train": 50, "validation": 10, "test": 10},
        "test_metrics": {"rmse": 0.8, "mae": 0.6, "r2": 0.5},
        "model_card_file": "synthetic-card.json", "model_card_sha256": "c" * 64,
        "scientific_readiness": "endpoint_ready",
    }


def test_prepared_builder_keeps_real_file_digests_and_endpoint_identity(builder):
    kwargs, source, weights = builder
    extension = endpoint_metadata()
    result = trainer._build_model_metadata(**kwargs, prepared_metadata=extension)
    assert result["endpoint_key"] == extension["endpoint_key"]
    assert result["prepared_dataset_sha256"] == extension["prepared_dataset_sha256"]
    assert result["dataset_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["weights_sha256"] == hashlib.sha256(weights.read_bytes()).hexdigest()
    assert result["scientific_readiness"] == "endpoint_ready"
    assert not (weights.parent / "registry_state.json").exists()
    extension["test_metrics"]["rmse"] = 900.0
    assert result["test_metrics"]["rmse"] == 0.8


@pytest.mark.parametrize("field", list(endpoint_metadata()))
def test_prepared_builder_rejects_missing_required_extension_field(builder, field):
    kwargs, _, _ = builder
    extension = endpoint_metadata()
    del extension[field]
    with pytest.raises(ValueError):
        trainer._build_model_metadata(**kwargs, prepared_metadata=extension)


@pytest.mark.parametrize("extension", [{}, [], "prepared"])
def test_explicit_prepared_input_cannot_silently_become_legacy(builder, extension):
    kwargs, _, _ = builder
    with pytest.raises(ValueError):
        trainer._build_model_metadata(**kwargs, prepared_metadata=extension)


@pytest.mark.parametrize("field,value", [
    ("weights_sha256", "0" * 64), ("model_id", "another-model"),
    ("units", "probability"), ("private_notes", "private"),
])
def test_prepared_extension_cannot_override_base_or_inject_fields(builder, field, value):
    kwargs, _, _ = builder
    extension = {**endpoint_metadata(), field: value}
    with pytest.raises(ValueError):
        trainer._build_model_metadata(**kwargs, prepared_metadata=extension)


@pytest.mark.parametrize("field,value", [
    ("target_id", "target-b"), ("endpoint_key", "target-a:pki:pki:regression"),
    ("split_counts", {"train": 70, "validation": 15, "test": 0}),
    ("test_metrics", {"rmse": float("nan"), "mae": 0.3, "r2": 0.5}),
    ("scientific_readiness", "demo"),
])
def test_prepared_builder_uses_registry_scientific_contract(builder, field, value):
    kwargs, _, _ = builder
    extension = copy.deepcopy(endpoint_metadata())
    extension[field] = value
    with pytest.raises(ValueError):
        trainer._build_model_metadata(**kwargs, prepared_metadata=extension)


def test_legacy_builder_does_not_claim_endpoint_readiness(builder):
    kwargs, _, _ = builder
    result = trainer._build_model_metadata(**kwargs)
    assert "endpoint_key" not in result
    assert result["scientific_readiness"] == "legacy_unvalidated"


def test_prepared_counts_must_match_actual_training_sample_count(builder):
    kwargs, _, _ = builder
    kwargs["samples"] = 99
    with pytest.raises(ValueError, match="samples"):
        trainer._build_model_metadata(**kwargs, prepared_metadata=endpoint_metadata())


def test_transformed_output_metadata_does_not_reinterpret_source_units(builder):
    kwargs, _, _ = builder
    extension = endpoint_metadata()
    extension["label_transform"] = "molar_to_pactivity"
    result = trainer._build_model_metadata(**kwargs, prepared_metadata=extension)
    assert result["endpoint"] == "pIC50"
    assert result["units"] == "pIC50"
    assert result["label_transform"] == "molar_to_pactivity"


def test_prepared_endpoint_cannot_claim_random_split_as_scientifically_ready(builder):
    kwargs, _, _ = builder
    kwargs.update(requested_split_strategy="random", actual_split_strategy="random")
    with pytest.raises(ValueError, match="split_strategy"):
        trainer._build_model_metadata(**kwargs, prepared_metadata=endpoint_metadata())
