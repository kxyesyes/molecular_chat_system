"""Family metadata tests use synthetic in-memory labels only."""

from importlib import import_module
from importlib.util import find_spec

import pandas as pd
import pytest

from src.activity.dataset_contract import DatasetManifest, validate_activity_dataset


def test_family_contract_module_exists():
    assert find_spec("src.activity.family_contract") is not None


@pytest.fixture
def contract():
    return import_module("src.activity.family_contract")


@pytest.mark.parametrize("alias", [
    "PDE", "pde-family", " PDE5A ", "pde4d", "PDE1", "PDE11A",
    "PDE6G", "target: PDE5A (human)", "PDE4D / PDE5A",
])
def test_resolves_explicit_pde_identifiers(contract, alias):
    assert contract.resolve_activity_family(alias) == "pde-family"


@pytest.mark.parametrize("alias", [
    "BuChE", "BChE", "buche-family", "BUCHE-FAMILY", "丁酰胆碱酯酶",
    "target: BChE (human)", "BuChE / BChE",
])
def test_resolves_explicit_buche_identifiers(contract, alias):
    assert contract.resolve_activity_family(alias) == "buche-family"


@pytest.mark.parametrize("text", [
    "", " ", "AChE", "acetylcholinesterase", "P54750", "unknown",
    "XPDE5A", "PDE5AX", "123PDE", "PDE123", "PDE0", "PDE12",
    "PDE5B", "PDE4Z", "PDE01A", "PDEgarbage", "PDE-like", "PDE-familyish",
    "x-pde-family", "pde-family-x", "foo_PDE5A", "PDE5A_foo",
    "xBuChE", "BuChEx", "BChE2", "buche-family123", "abc丁酰胆碱酯酶123",
    None, True, 5, [],
])
def test_rejects_unknown_and_embedded_identifiers(contract, text):
    with pytest.raises(ValueError, match="Unknown"):
        contract.resolve_activity_family(text)


@pytest.mark.parametrize("text", [
    "PDE 和 BuChE", "PDE5A/BChE", "buche-family, pde-family",
    "丁酰胆碱酯酶; PDE4D",
])
def test_rejects_ambiguous_families(contract, text):
    with pytest.raises(ValueError, match="Ambiguous"):
        contract.resolve_activity_family(text)


@pytest.mark.parametrize("family,family_id", [("PDE5A", "pde-family"), ("BChE", "buche-family")])
@pytest.mark.parametrize("task_type", ["classification", "regression"])
def test_manifest_fixed_scientific_metadata(contract, family, family_id, task_type):
    manifest = contract.build_family_manifest(family, "synthetic-v1", task_type)
    assert isinstance(manifest, DatasetManifest)
    assert manifest.dataset_id == "synthetic-v1"
    assert manifest.target_id == family_id
    assert manifest.target_name == family_id
    assert manifest.task_type == task_type
    assert manifest.smiles_column == "Smiles"
    assert manifest.value_column == "pIC50"
    assert manifest.endpoint == manifest.units == "pIC50"
    assert manifest.source == "user-provided"
    assert manifest.license == "project-training-only"
    assert manifest.duplicate_strategy == "median"
    assert manifest.minimum_unique_molecules == 100
    assert manifest.minimum_scaffolds == 10
    if task_type == "classification":
        assert manifest.label_transform == "binary_threshold"
        assert manifest.classification_threshold == 5.0
        assert manifest.classification_direction == "greater_or_equal"
        assert manifest.output_endpoint == "activity"
        assert manifest.output_units == "probability"
    else:
        assert manifest.label_transform == "identity"
        assert manifest.classification_threshold is None
        assert manifest.classification_direction is None
        assert manifest.output_endpoint is None
        assert manifest.output_units is None


def test_threshold_constants_are_distinct(contract):
    assert contract.LABEL_THRESHOLD == 5.0
    assert contract.PROBABILITY_THRESHOLD == 0.5


@pytest.mark.parametrize("task_type", ["classification", "regression"])
def test_small_test_minimums_are_explicit_keywords(contract, task_type):
    manifest = contract.build_family_manifest(
        "PDE", "small-test", task_type,
        minimum_unique_molecules=3, minimum_scaffolds=1,
    )
    assert manifest.minimum_unique_molecules == 3
    assert manifest.minimum_scaffolds == 1
    with pytest.raises(TypeError):
        contract.build_family_manifest("PDE", "small-test", task_type, 3, 1)


@pytest.mark.parametrize("field", ["minimum_unique_molecules", "minimum_scaffolds"])
@pytest.mark.parametrize("value", [True, 0, -1, 1.5, float("nan"), float("inf")])
def test_manifest_retains_minimum_validation(contract, field, value):
    with pytest.raises(ValueError, match=field):
        contract.build_family_manifest("PDE", "invalid-test", "regression", **{field: value})


@pytest.mark.parametrize("task_type", ["unknown", "", None, True])
def test_manifest_rejects_invalid_task_type(contract, task_type):
    with pytest.raises(ValueError, match="task_type"):
        contract.build_family_manifest("PDE", "invalid-test", task_type)


@pytest.mark.parametrize("family", ["AChE", "PDE and BuChE"])
def test_manifest_does_not_default_unknown_or_ambiguous_family(contract, family):
    with pytest.raises(ValueError):
        contract.build_family_manifest(family, "invalid-test", "regression")


@pytest.mark.parametrize("task_type,expected", [
    ("classification", [0.0, 1.0, 1.0]),
    ("regression", [4.999, 5.0, 5.001]),
])
def test_threshold_boundary_and_identity_with_existing_validator(contract, task_type, expected):
    manifest = contract.build_family_manifest(
        "PDE", "boundary-test", task_type,
        minimum_unique_molecules=1, minimum_scaffolds=1,
    )
    frame = pd.DataFrame({
        "Smiles": ["CCO", "CCN", "CCC"], "pIC50": [4.999, 5.0, 5.001],
        "relation": "=", "units": "pIC50",
    })
    result = validate_activity_dataset(frame, manifest)
    actual = result.accepted.set_index("canonical_smiles")["normalized_value"]
    assert [actual[key] for key in ["CCO", "CCN", "CCC"]] == expected


@pytest.mark.parametrize("task_type", ["classification", "regression"])
@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), float("-inf")])
def test_invalid_python_labels_are_rejected_by_existing_validator(contract, task_type, value):
    manifest = contract.build_family_manifest("BuChE", "invalid-label-test", task_type)
    frame = pd.DataFrame({
        # Preserve Python bool; numpy.bool_ handling belongs to dataset_contract.
        "Smiles": ["CC"], "pIC50": pd.Series([value], dtype=object),
        "relation": "=", "units": "pIC50",
    })
    result = validate_activity_dataset(frame, manifest)
    assert result.accepted.empty
    assert len(result.rejected) == 1
    assert not result.ready_for_training


@pytest.mark.parametrize("task_type,expected", [("classification", 1.0), ("regression", 5.0)])
def test_duplicate_median_precedes_classification(contract, task_type, expected):
    manifest = contract.build_family_manifest("PDE", "median-test", task_type)
    frame = pd.DataFrame({
        "Smiles": ["CC", "CC"], "pIC50": [4.0, 6.0],
        "relation": "=", "units": "pIC50",
    })
    result = validate_activity_dataset(frame, manifest)
    assert len(result.accepted) == 1
    assert result.accepted.iloc[0]["normalized_value"] == expected
    assert result.accepted.iloc[0]["replicate_count"] == 2
