import pandas as pd
import pytest
import numpy as np
from rdkit import Chem

from src.reverse_target.data_quality import (
    apply_activity_quality_flags,
    deduplicate_activity_rows,
    normalize_activity_value,
    quality_filter_for_training,
)
from src.reverse_target.generate_fingerprints import FingerprintGenerator
from src.reverse_target.predictor import ReverseTargetPredictor


def _rows():
    return pd.DataFrame(
        [
            {
                "molecule_chembl_id": "CHEMBL1",
                "canonical_smiles": "CCO",
                "target_name": "Target A",
                "target_chembl_id": "CHEMBL_T1",
                "standard_type": "IC50",
                "standard_value": 2,
                "standard_units": "uM",
                "standard_relation": "=",
                "organism": "Homo sapiens",
                "assay_id": 10,
                "document_chembl_id": "CHEMBL_DOC1",
                "assay_confidence_score": 9,
            },
            {
                "molecule_chembl_id": "CHEMBL1",
                "canonical_smiles": "CCO",
                "target_name": "Target A",
                "target_chembl_id": "CHEMBL_T1",
                "standard_type": "IC50",
                "standard_value": 2,
                "standard_units": "uM",
                "standard_relation": "=",
                "organism": "Mus musculus",
                "assay_id": 11,
                "document_chembl_id": "CHEMBL_DOC2",
                "assay_confidence_score": 9,
            },
            {
                "molecule_chembl_id": "CHEMBL2",
                "canonical_smiles": "CCC",
                "target_name": "Target B",
                "target_chembl_id": "CHEMBL_T2",
                "standard_type": "IC50",
                "standard_value": 20,
                "standard_units": "mystery-unit",
                "standard_relation": "=",
                "organism": "Homo sapiens",
                "assay_id": 12,
                "document_chembl_id": "CHEMBL_DOC3",
                "assay_confidence_score": 9,
            },
            {
                "molecule_chembl_id": "CHEMBL3",
                "canonical_smiles": "CCN",
                "target_name": "Target C",
                "target_chembl_id": "CHEMBL_T3",
                "standard_type": "IC50",
                "standard_value": 20,
                "standard_units": "nM",
                "standard_relation": ">",
                "organism": "Homo sapiens",
                "assay_id": 13,
                "document_chembl_id": "CHEMBL_DOC4",
                "assay_confidence_score": 9,
            },
        ]
    )


@pytest.mark.parametrize(
    "value,unit,expected,status",
    [
        (2, "uM", 2000.0, "confirmed"),
        (2, "µM", 2000.0, "confirmed"),
        (2, "nM", 2.0, "confirmed"),
        (2, "unknown", None, "unknown"),
        (2, None, None, "unknown"),
        (float("nan"), "nM", None, "invalid"),
    ],
)
def test_activity_units_are_explicit_and_unknown_units_are_not_nanomolar(
    value, unit, expected, status
):
    normalized = normalize_activity_value(value, unit)
    assert normalized["value_nm"] == expected
    assert normalized["status"] == status


def test_quality_flags_preserve_provenance_and_only_confirmed_equalities_are_trainable():
    flagged = apply_activity_quality_flags(_rows())

    assert flagged.loc[0, "standard_value_nm"] == 2000.0
    assert flagged.loc[0, "unit_status"] == "confirmed"
    assert bool(flagged.loc[0, "quality_eligible"]) is True
    assert flagged.loc[0, "assay_id"] == 10
    assert flagged.loc[0, "document_chembl_id"] == "CHEMBL_DOC1"
    assert bool(flagged.loc[2, "quality_eligible"]) is False
    assert flagged.loc[2, "unit_status"] == "unknown"
    assert bool(flagged.loc[3, "quality_eligible"]) is False
    assert flagged.loc[3, "quality_reason"] == "non_exact_relation"


def test_deduplication_does_not_merge_species_assays_or_publications():
    duplicated = pd.concat([_rows().iloc[[0]], _rows().iloc[[0]]], ignore_index=True)
    deduped = deduplicate_activity_rows(duplicated)
    assert len(deduped) == 1

    flagged = apply_activity_quality_flags(_rows())
    assert len(deduplicate_activity_rows(flagged)) == len(flagged)


def test_training_filter_excludes_unverified_rows_but_audit_frame_keeps_them():
    flagged = apply_activity_quality_flags(_rows())
    training = quality_filter_for_training(flagged)
    assert list(training["molecule_chembl_id"]) == ["CHEMBL1", "CHEMBL1"]
    assert set(flagged["molecule_chembl_id"]) == {"CHEMBL1", "CHEMBL2", "CHEMBL3"}


def test_fingerprint_metadata_binds_source_and_row_provenance(tmp_path):
    source = tmp_path / "training.tsv"
    frame = pd.DataFrame(
        {
            "molecule_chembl_id": ["CHEMBL1"],
            "canonical_smiles": ["CCO"],
            "target_name": ["Target A"],
            "standard_type": ["IC50"],
            "standard_value": [100.0],
            "organism": ["Homo sapiens"],
        }
    )
    frame.to_csv(source, sep="\t", index=False)
    generator = FingerprintGenerator(source)
    valid, morgan, maccs = generator.process_molecules(frame)
    metadata = generator.save_fingerprints(valid, morgan, maccs)
    assert metadata["source_sha256"]
    assert metadata["fingerprint_params"] == {"morgan_radius": 2, "morgan_bits": 2048, "maccs_bits": 166}
    assert metadata["row_mapping"] == [0]
    assert (tmp_path / "fingerprint_manifest.json").exists()


def test_prediction_exposes_similarity_as_rank_evidence_not_activity_confidence(tmp_path):
    frame = pd.DataFrame(
        {
            "molecule_chembl_id": ["CHEMBL1"],
            "canonical_smiles": ["CCO"],
            "target_name": ["Target A"],
            "target_chembl_id": ["CHEMBL_T1"],
            "target_uniprot_id": ["P12345"],
            "taxon_id": [9606],
            "standard_type": ["IC50"],
            "standard_value": [100.0],
            "standard_units": ["nM"],
            "standard_relation": ["="],
            "organism": ["Homo sapiens"],
            "assay_id": [10],
            "document_chembl_id": ["CHEMBL_DOC1"],
            "quality_eligible": [True],
        }
    )
    frame.to_csv(tmp_path / "training.tsv", sep="\t", index=False)
    generator = FingerprintGenerator(tmp_path / "training.tsv")
    predictor = ReverseTargetPredictor(tmp_path)
    mol = Chem.MolFromSmiles("CCO")
    morgan = generator.generate_morgan_fingerprint(mol)
    maccs = generator.generate_maccs_fingerprint(mol)
    predictor.df = frame
    predictor.morgan_fps = np.stack([morgan])
    predictor.maccs_fps = np.stack([maccs])
    predictor.morgan_popcounts = predictor.morgan_fps.sum(axis=1)
    predictor.maccs_popcounts = predictor.maccs_fps.sum(axis=1)
    predictor._loaded = True

    result = predictor.predict("CCO", threshold=0.0, combine_by_target=False)
    assert result[0]["score_semantics"] == "2d_structure_similarity_rank_only"
    assert result[0]["similarity_metric"] == "weighted_morgan_maccs_tanimoto"
    assert result[0]["target_chembl_id"] == "CHEMBL_T1"
    assert result[0]["target_uniprot_id"] == "P12345"
    assert result[0]["evidence"]["type"] == "similarity_neighbor"
    assert "confidence" not in result[0]
