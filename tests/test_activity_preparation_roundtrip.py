"""Synthetic E/Z regressions for generic data preparation, without family adapters."""
import io

import pandas as pd
import pytest
from rdkit import Chem

from src.activity import dataset_contract as dc


# Reviewed scaffold regression from test_activity_family_dataset.py; only the
# generic chemistry contract is exercised, with unrelated synthetic molecules.
ALKENE_PAIR = ("C1CC1/C(C)=C/C1CC1", "C1CC1/C(C)=C\\C1CC1")


def test_scaffold_stereo_survives_parent_roundtrip_without_mutating_parent():
    identities = []
    for smiles in ALKENE_PAIR:
        canonical, molecule, reason = dc._canonical_parent(smiles)
        assert reason is None
        assert canonical is not None and molecule is not None
        before = [(bond.GetBondDir(), bond.GetStereo()) for bond in molecule.GetBonds()]
        scaffold = dc._scaffold_identity(molecule)
        assert scaffold == dc._scaffold_identity(Chem.MolFromSmiles(canonical))
        assert before == [(bond.GetBondDir(), bond.GetStereo()) for bond in molecule.GetBonds()]
        dc._verify_prepared_chemistry(pd.DataFrame([dict(
            canonical_smiles=canonical, original_smiles=smiles, scaffold_smiles=scaffold)]))
        identities.append(scaffold)
    assert identities[0] != identities[1]


@pytest.mark.parametrize("input_format,separator", [("csv", ","), ("tsv", "\t")])
def test_generic_preparation_preserves_ez_scaffolds_after_publication(tmp_path, input_format, separator):
    # Three distinct scaffold groups make all three partitions feasible. Values
    # and IDs deliberately avoid the independent precision/leading-zero bugs.
    frame = pd.DataFrame({
        "smiles": [*ALKENE_PAIR, "c1ccccc1"],
        "value": ["4.8"] * 3,
        "units": ["pIC50"] * 3,
        "relation": ["="] * 3,
        "compound_id": ["synthetic-ez-a", "synthetic-ez-b", "synthetic-ring"],
    })
    source = frame.to_csv(index=False, sep=separator).encode("utf-8")
    frame = pd.read_csv(io.BytesIO(source), sep=separator, dtype=str, keep_default_na=False)
    declaration = dc.DatasetManifest(
        dataset_id="synthetic-ez-roundtrip", target_id="synthetic-target",
        target_name="Synthetic target", task_type="regression", endpoint="pIC50",
        units="pIC50", label_transform="identity", source="synthetic-test",
        license="test-only", minimum_unique_molecules=3, minimum_scaffolds=3,
    )
    result = dc.validate_activity_dataset(
        frame, declaration, input_bytes=source, input_format=input_format,
    )
    assert result.ready_for_training
    assert result.rejected.empty
    assert result.statistics["unique_molecules"] == 3
    assert result.statistics["unique_scaffolds"] == 3
    expected = result.accepted.set_index("compound_id").sort_index()
    assert expected.loc["synthetic-ez-a", "canonical_smiles"] != expected.loc[
        "synthetic-ez-b", "canonical_smiles"]
    assert expected.loc["synthetic-ez-a", "scaffold_smiles"] != expected.loc[
        "synthetic-ez-b", "scaffold_smiles"]

    split = dc.split_prepared_dataset(result.accepted)
    path = dc.write_prepared_dataset(result, split, declaration, tmp_path / "prepared")
    frames = [dc.read_prepared_split(path.parent / f"{name}.csv")
              for name in ("train", "validation", "test")]
    assert all(len(part) == 1 for part in frames)
    for part in frames:
        assert dc._verify_prepared_chemistry(part) == part["scaffold_smiles"].tolist()
    published = pd.concat(frames).set_index("compound_id").sort_index()
    assert published.index.tolist() == expected.index.tolist()
    for column in ("original_smiles", "canonical_smiles", "scaffold_smiles", "original_value",
                   "normalized_value", "replicate_evidence"):
        assert published[column].tolist() == expected[column].tolist()
