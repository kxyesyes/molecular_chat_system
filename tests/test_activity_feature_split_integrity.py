"""Synthetic regression for leakage introduced by actual graph preprocessing."""
from types import SimpleNamespace

import pandas as pd
import pytest
from rdkit import Chem

from src.activity import dataset_contract as dc, prepared_training, predictor
from src.activity.model_card import load_prepared_training_data


@pytest.mark.parametrize("seed", [2, 3])
def test_neutralized_scaffolds_cannot_leak_into_held_out_inputs(tmp_path, monkeypatch, seed):
    frame = pd.DataFrame([
        dict(smiles=prefix + ring, value=float(5 + i), units="pIC50", relation="=")
        for ring in ("c1ccncc1", "c1cc[nH+]cc1", "C1CCCCC1")
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
    monkeypatch.setenv("ACTIVITY_MODEL_DIR", str(tmp_path / "models"))
    engine = predictor.ActivityPredictor()  # Featurization only; no load/predict.
    monkeypatch.setattr(predictor, "get_predictor", lambda: engine)
    monkeypatch.setattr(prepared_training, "fit_prepared", lambda *a, **k: pytest.fail(
        "leaked feature inputs reached optimization"))
    with pytest.raises(ValueError, match="Featurized .* overlap"):
        prepared_training.run_prepared_training(
            SimpleNamespace(has_torch=True, status={}, device="cpu"), str(path),
            epochs=1, lr=.001, batch_size=4, dropout=0., num_layers=2,
            hidden_size=8, weight_decay=0., patience=1, loss_metric="MSE",
            lr_scheduler="Cosine", random_seed=42,
        )
    assert path.exists()
    assert not list((tmp_path / "models").glob("*.pt"))


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
