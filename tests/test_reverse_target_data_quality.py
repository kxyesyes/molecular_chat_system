import json
from urllib.parse import parse_qs, unquote, urlsplit

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


def test_target_links_use_source_identifiers_on_both_prediction_paths(tmp_path):
    from src.reverse_target.predictor import _aggregate_by_target

    frame = _rows().iloc[:1].assign(target_chembl_id="CHEMBL203", target_uniprot_id="P00533")
    file = tmp_path / "training.tsv"
    frame.assign(organism="").to_csv(file, sep="\t", index=False)
    predictor = ReverseTargetPredictor(tmp_path)
    predictor.df = pd.read_csv(file, sep="\t")
    morgan, maccs = predictor.compute_query_fingerprints("CCO")
    predictor.morgan_fps, predictor.maccs_fps = morgan[None, :], maccs[None, :]
    predictor.morgan_popcounts, predictor.maccs_popcounts = np.array([morgan.sum()]), np.array([maccs.sum()])
    predictor._loaded = True
    for row in [predictor.predict("CCO")[0], _aggregate_by_target(predictor.get_raw_similar_molecules("CCO"))[0]]:
        assert row["chembl_search_url"].endswith("/explore/target/CHEMBL203")
        assert row["uniprot_search_url"] == "https://www.uniprot.org/uniprotkb/P00533/entry"
        assert row["organism"] == ""
        json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("species,taxon,suffix", [
    ("Mus musculus", 10090.0, " AND organism_id:10090"),
    ("Mus musculus", None, ' AND organism_name:"Mus musculus"'),
    ("", None, ""), ("Unknown", "invalid", ""),
])
def test_missing_target_ids_link_to_explicit_name_search(tmp_path, species, taxon, suffix):
    from src.reverse_target.predictor import _aggregate_by_target

    name = "A/B & C"
    fields = dict(target_name=name, organism=species, taxon_id=taxon,
                  target_chembl_id="invalid", target_uniprot_id="not an accession")
    predictor = ReverseTargetPredictor(tmp_path)
    for row in [predictor._similarity_record(pd.DataFrame([fields]), 0, .5, .5, .5),
                _aggregate_by_target([fields])[0]]:
        path = urlsplit(row["chembl_search_url"]).path
        assert path.startswith("/chembl/web_components/search_results/")
        assert unquote(path.rsplit("/", 1)[1]) == name
        query = parse_qs(urlsplit(row["uniprot_search_url"]).query)
        assert query == {"query": [f'"{name}"' + suffix]}


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


@pytest.mark.parametrize("source", ["sqlite", "api"])
def test_training_export_applies_nm_range_and_labels_converted_values(source, tmp_path, monkeypatch):
    from src.reverse_target.extract_clean_data import ChEMBLDataExtractor
    from src.reverse_target.fetch_chembl_api import ChEMBLAPIFetcher

    observations = pd.DataFrame([
        {**_rows().iloc[0].to_dict(), "standard_value": value, "standard_units": unit}
        for value, unit in [(2, "uM"), (200_000_000, "pM"), (0.1, "M")]
    ])
    if source == "sqlite":
        monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(tmp_path))
        db_path = tmp_path / "fixture.db"
        db_path.touch()
        importer = ChEMBLDataExtractor(db_path)
        importer.format_training_data(importer.clean_data(observations))
    else:
        importer = ChEMBLAPIFetcher(output_dir=tmp_path)
        observations = observations.rename(columns={"target_name": "target_pref_name",
                                                    "organism": "target_organism"})
        importer.save_training_data(importer.clean_data(observations))

    exported = pd.read_csv(tmp_path / "chembl_training_data.tsv", sep="\t")
    assert list(zip(exported.standard_value, exported.standard_units)) == [
        (2000, "nM"), (200_000, "nM")
    ]
    audit = pd.read_csv(tmp_path / "chembl_quality_audit.tsv", sep="\t")
    assert list(zip(audit.standard_value, audit.standard_units)) == [
        (2, "uM"), (200_000_000, "pM"), (0.1, "M")
    ]
    assert not audit.iloc[-1].quality_eligible


def test_activity_api_resumes_at_requested_offset_for_exact_page_budget(tmp_path, monkeypatch):
    from src.reverse_target import fetch_chembl_api

    importer = fetch_chembl_api.ChEMBLAPIFetcher(output_dir=tmp_path)
    pages = []

    class Page:
        def __init__(self, count):
            self.count = count

        def raise_for_status(self):
            pass

        def json(self):
            return {"activities": [{}] * self.count}

    def get(url, *, params, timeout):
        pages.append((params["offset"], params["limit"]))
        return Page(params["limit"])

    monkeypatch.setattr(importer.session, "get", get)
    monkeypatch.setattr(fetch_chembl_api.time, "sleep", lambda seconds: None)
    importer.fetch_activities(offset=1200, limit=1400)
    assert pages == [(1200, 1000), (2200, 400)]


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

    predictor.df = frame.assign(standard_units="uM", standard_relation="<")
    candidate, = predictor.get_raw_similar_molecules("CCO")
    assert (candidate["standard_value"], candidate["standard_units"], candidate["standard_relation"]) == (
        100.0, "uM", "<"
    )
    predictor.df = frame.drop(columns=["standard_units", "standard_relation"])
    legacy_candidate, = predictor.get_raw_similar_molecules("CCO")
    assert "standard_units" not in legacy_candidate and "standard_relation" not in legacy_candidate

    predictor.df = frame.assign(standard_value=2000, standard_units="uM", unit_status="confirmed")
    historical, = predictor.get_raw_similar_molecules("CCO")
    assert historical["measurement_status"] == "unit_verification_required"
    assert historical["standard_value"] is None
    assert "standard_units" not in historical and "standard_relation" not in historical

    predictor.df = predictor.df.assign(standard_value=2, standard_value_nm=2000)
    original, = predictor.get_raw_similar_molecules("CCO")
    assert (original["standard_value"], original["standard_units"]) == (2.0, "uM")


@pytest.mark.parametrize("compact", [False, True])
def test_similarity_record_preserves_explicit_quality_booleans(compact, tmp_path):
    from src.reverse_target.compact_source import CompactTable

    if compact:
        table = CompactTable({"quality_eligible": np.array([0, 1], dtype=np.uint32)},
                             {"quality_eligible": ("True", "False")}, 2)
    else:
        table = pd.DataFrame({"quality_eligible": [True, False]})
    predictor = ReverseTargetPredictor(tmp_path)
    for row, expected in enumerate([True, False]):
        record = predictor._similarity_record(table, row, 0.5, 0.5, 0.5)
        assert record["evidence"]["quality_eligible"] is expected


@pytest.mark.parametrize("fields", [{}, {"quality_eligible": float("nan")},
                                   {"quality_eligible": ""}, {"quality_eligible": "unknown"}])
def test_unknown_quality_does_not_become_an_explicit_pass(tmp_path, fields):
    predictor = ReverseTargetPredictor(tmp_path)
    frame = pd.DataFrame([{"target_name": "Fixture", **fields}])
    record = predictor._similarity_record(frame, 0, 0.5, 0.5, 0.5)
    assert record["evidence"]["quality_eligible"] is None


def test_api_activity_export_preserves_public_assay_ids_without_merging_observations(tmp_path):
    from src.reverse_target.fetch_chembl_api import ChEMBLAPIFetcher

    importer = ChEMBLAPIFetcher(output_dir=tmp_path)
    observation = _rows().iloc[0].drop("assay_id").to_dict()
    observations = pd.DataFrame([
        {**observation, "assay_chembl_id": public, "assay_id": numeric}
        for public, numeric in [("CHEMBL101", None), ("CHEMBL102", None),
                                ("CHEMBL103", 1), ("CHEMBL103", 2), ("CHEMBL101", None)]
    ]).rename(columns={"target_name": "target_pref_name", "organism": "target_organism"})
    training = importer.save_training_data(importer.clean_data(observations))
    assert list(training.assay_chembl_id) == ["CHEMBL101", "CHEMBL102", "CHEMBL103", "CHEMBL103"]
    assert list(training.assay_id.dropna()) == [1, 2]
