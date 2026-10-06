"""Synthetic regression for leakage introduced by actual graph preprocessing."""
from types import SimpleNamespace

import pandas as pd
import pytest
from rdkit import Chem

from src.activity import dataset_contract as dc, prepared_training, predictor
from src.activity.model_card import load_prepared_training_data


@pytest.mark.parametrize("seed", [2, 3])
def test_split_keeps_neutralized_scaffolds_disjoint_before_training(tmp_path, seed):
    frame = pd.DataFrame([
        dict(smiles=prefix + ring, value=float(5 + i), units="pIC50", relation="=")
        for ring in ("c1ccncc1", "c1cc[nH+]cc1", "c1ccccc1", "C1CCCCC1")
        for i, prefix in enumerate(("", "C", "CC", "CCC"))
    ])
    declaration = dc.DatasetManifest(
        dataset_id="synthetic-feature-leak", target_id="synthetic-target",
        target_name="Synthetic", task_type="regression", endpoint="pIC50",
        units="pIC50", label_transform="identity", source="synthetic-only",
        license="test-only", minimum_unique_molecules=3, minimum_scaffolds=3,
    )
    validated = dc.validate_activity_dataset(
        frame, declaration, input_bytes=dc._csv_bytes(frame), input_format="csv",
    )
    split = dc.split_prepared_dataset(validated.accepted, seed=seed, ratios=(.34, .33, .33))
    path = dc.write_prepared_dataset(validated, split, declaration, tmp_path / "prepared")
    loaded = load_prepared_training_data(path)
    groups = [set(part.canonical_smiles) for part in loaded.frames.values()]
    assert all(not left & right for i, left in enumerate(groups) for right in groups[i+1:])
    engine = predictor.ActivityPredictor()  # Featurization only; no load/predict.
    owners = {}
    for split_name, split_frame in loaded.frames.items():
        for smiles in split_frame["canonical_smiles"]:
            atoms, _ = engine.process_smiles(smiles)
            prepared_training._claim_feature_identity(atoms, split_name, owners)
    assert path.exists()


def _synthetic_validation(rings):
    frame = pd.DataFrame([
        dict(smiles=prefix + ring, value=float(5 + i), units="pIC50", relation="=")
        for ring in rings for i, prefix in enumerate(("", "C", "CC", "CCC"))
    ])
    declaration = dc.DatasetManifest(
        dataset_id="synthetic-legacy", target_id="synthetic-target",
        target_name="Synthetic", task_type="regression", endpoint="pIC50",
        units="pIC50", label_transform="identity", source="synthetic-only",
        license="test-only", minimum_unique_molecules=3, minimum_scaffolds=3,
    )
    validated = dc.validate_activity_dataset(
        frame, declaration, input_bytes=dc._csv_bytes(frame), input_format="csv",
    )
    return validated, declaration


def test_legacy_snapshot_still_loads_but_leaked_graphs_never_reach_training(tmp_path, monkeypatch):
    validated, declaration = _synthetic_validation(
        ("c1ccncc1", "c1cc[nH+]cc1", "C1CCCCC1"))
    split = dc._split_prepared_dataset(
        validated.accepted, seed=2, ratios=(.34, .33, .33),
        algorithm="deterministic_scaffold_greedy_v2",
    )
    path = dc.write_prepared_dataset(validated, split, declaration, tmp_path)
    loaded = load_prepared_training_data(path)
    assert loaded.manifest["split"]["algorithm"] == "deterministic_scaffold_greedy_v2"
    assert sum(map(len, loaded.frames.values())) == 12
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(predictor, "get_predictor", predictor.ActivityPredictor)
    monkeypatch.setattr(prepared_training, "fit_prepared", lambda *a, **k: pytest.fail(
        "leaked inputs reached training"))
    with pytest.raises(ValueError, match="Featurized .* overlap"):
        prepared_training.run_prepared_training(
            SimpleNamespace(has_torch=True, status={}, device="cpu"), str(path),
            epochs=1, lr=.001, batch_size=4, dropout=0., num_layers=2,
            hidden_size=8, weight_decay=0., patience=1, loss_metric="MSE",
            lr_scheduler="Cosine", random_seed=42,
        )
    assert not list((tmp_path / "models").glob("*.pt"))


def test_split_rejects_insufficient_feature_groups_without_dropping_molecules():
    validated, _ = _synthetic_validation(("c1ccncc1", "c1cc[nH+]cc1", "C1CCCCC1"))
    original = validated.accepted.copy(deep=True)
    with pytest.raises(ValueError, match="three distinct"):
        dc.split_prepared_dataset(validated.accepted)
    pd.testing.assert_frame_equal(original, validated.accepted)


def test_stereo_scaffolds_share_split_but_retain_source_identity(tmp_path):
    validated, declaration = _synthetic_validation((
        "C1CC1/C(C)=C/C1CC1", "C1CC1/C(C)=C\\C1CC1", "c1ccccc1", "c1ccncc1",
    ))
    original = validated.accepted.copy(deep=True)
    split = dc.split_prepared_dataset(validated.accepted, ratios=(.34, .33, .33))
    reordered = dc.split_prepared_dataset(
        validated.accepted.sample(frac=1, random_state=8), ratios=(.34, .33, .33))
    assert split.assignments == reordered.assignments
    path = dc.write_prepared_dataset(validated, split, declaration, tmp_path)
    loaded = load_prepared_training_data(path)
    owners = {}
    engine = predictor.ActivityPredictor()
    for name, frame in loaded.frames.items():
        for smiles in frame.canonical_smiles:
            atoms, _ = engine.process_smiles(smiles)
            prepared_training._claim_feature_identity(atoms, name, owners)
    assert len(validated.accepted.scaffold_smiles.unique()) == 4
    pd.testing.assert_frame_equal(original, validated.accepted)


def test_graph_identity_describes_the_molecule_after_neutralization():
    engine = predictor.ActivityPredictor()
    atoms, _ = engine.process_smiles("c1cc[nH+]cc1")
    assert getattr(atoms, "feature_smiles", None) == Chem.MolToSmiles(Chem.MolFromSmiles("c1ccncc1"))


@pytest.mark.parametrize("left,right", [("train", "validation"), ("train", "test"), ("validation", "test")])
def test_feature_scaffold_check_covers_every_split_pair(left, right):
    owners = {}
    prepared_training._claim_feature_identity(SimpleNamespace(feature_smiles="Cc1ccncc1"), left, owners)
    # Different molecule, same actual scaffold: raw molecule deduplication is insufficient.
    with pytest.raises(ValueError, match="Featurized scaffold overlap"):
        prepared_training._claim_feature_identity(SimpleNamespace(feature_smiles="CCc1ccncc1"), right, owners)


def test_feature_guard_allows_within_split_scaffolds_and_disjoint_splits():
    owners = {}
    for smiles in ("c1ccncc1", "Cc1ccncc1"):
        prepared_training._claim_feature_identity(SimpleNamespace(feature_smiles=smiles), "train", owners)
    prepared_training._claim_feature_identity(SimpleNamespace(feature_smiles="C1CCCCC1"), "test", owners)


@pytest.mark.parametrize("smiles", [None, "", "invalid("])
def test_missing_or_invalid_feature_identity_fails_closed(smiles):
    with pytest.raises(ValueError, match="identity is unavailable"):
        prepared_training._claim_feature_identity(SimpleNamespace(feature_smiles=smiles), "train", {})
