"""Synthetic package contracts; never evidence of real activity performance."""
import hashlib
import importlib
import json
from pathlib import Path

import pandas as pd
import pytest


def module():
    return importlib.import_module("src.activity.family_dataset")


def test_family_dataset_public_api_exists():
    assert callable(module().prepare_family_dataset)
    assert callable(module().load_family_dataset)


@pytest.mark.parametrize("row", ["EXTRA,CCO,6", "CCO", "CCO,6,", ""])
def test_adapter_rejects_malformed_row_width(row):
    with pytest.raises(ValueError, match="CSV row"):
        module()._adapt_source(("Smiles,pIC50\n" + row + "\n").encode(), "csv")


def test_scaffold_stereo_survives_parent_roundtrip_without_mutating_parent():
    from rdkit import Chem
    from src.activity import dataset_contract as dc
    identities = []
    # Synthetic cyclopropyl alkene pair, unrelated to the private input data.
    for smiles in ("C1CC1/C(C)=C/C1CC1", "C1CC1/C(C)=C\\C1CC1"):
        canonical, molecule, reason = dc._canonical_parent(smiles)
        assert reason is None
        before = [(bond.GetBondDir(), bond.GetStereo()) for bond in molecule.GetBonds()]
        scaffold = dc._scaffold_identity(molecule)
        assert scaffold == dc._scaffold_identity(Chem.MolFromSmiles(canonical))
        assert before == [(bond.GetBondDir(), bond.GetStereo()) for bond in molecule.GetBonds()]
        dc._verify_prepared_chemistry(pd.DataFrame([dict(
            canonical_smiles=canonical, original_smiles=smiles, scaffold_smiles=scaffold)]))
        identities.append(scaffold)
    assert identities[0] != identities[1]


@pytest.fixture
def source(tmp_path, monkeypatch):
    from src.activity import family_contract
    real_builder = family_contract.build_family_manifest
    def small_builder(family, dataset_id, task_type):
        return real_builder(family, dataset_id, task_type,
                            minimum_unique_molecules=3, minimum_scaffolds=3)
    monkeypatch.setattr(family_contract, "build_family_manifest", small_builder)
    rows = []
    for ring in ("c1ccccc1", "c1ccncc1", "C1CCCCC1"):
        for i, prefix in enumerate(("", "C", "CC", "CCC")):
            rows.append({"Smiles": prefix + ring, "pIC50": [4., 4.5, 5., 6.][i]})
    path = tmp_path / "private.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def prepare(source, tmp_path, **kwargs):
    return module().prepare_family_dataset(source, family="PDE5A", package_id="synthetic-pde",
                                          output_dir=tmp_path / "out", **kwargs)


def test_validate_only_has_no_writes_and_both_classes(source, tmp_path):
    result = prepare(source, tmp_path, validate_only=True)
    assert result["ready_for_training"] is True
    assert result["family_id"] == "pde-family"
    assert result["label_threshold"] == 5.0
    assert result["statistics"]["unique_molecules"] == 12
    assert result["class_counts"] == {"inactive": 6, "active": 6}
    assert not (tmp_path / "out").exists()
    assert "Smiles" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_package_is_same_source_same_split_no_renormalization(source, tmp_path):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = prepare(source, tmp_path, validate_only=False)
    path = tmp_path / "out" / "synthetic-pde" / "family_dataset.json"
    bundle = module().load_family_dataset(path)
    assert result["status"] == "passed"
    assert bundle["source_sha256"] == digest
    assert bundle["input_adapter"]["added_columns"] == {"units": "pIC50", "relation": "="}
    assert bundle["input_adapter"]["adapted_input_sha256"] != digest
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert bundle["scope"]["species"] == "mixed"
    assert bundle["chemistry"]["scaffold_policy"] == "murcko-isomeric-bond-directions-v1"
    assert bundle["chemistry"]["rdkit_version"]
    from src.activity.model_card import load_prepared_training_data
    loaded = {task: load_prepared_training_data(path.parent / record["path"])
              for task, record in bundle["datasets"].items()}
    for name in ("train", "validation", "test"):
        reg = loaded["regression"].frames[name].set_index("canonical_smiles").sort_index()
        cls = loaded["classification"].frames[name].set_index("canonical_smiles").sort_index()
        assert reg.index.tolist() == cls.index.tolist()
        assert cls.normalized_value.tolist() == (reg.normalized_value >= 5).astype(float).tolist()
    with pytest.raises(FileExistsError):
        prepare(source, tmp_path, validate_only=False)
    assert module().load_family_dataset(path) == bundle


def test_duplicate_values_aggregate_before_classification(source, tmp_path):
    frame = pd.read_csv(source)
    row = frame.iloc[0].copy()
    row["pIC50"] = 8.
    pd.concat([frame, row.to_frame().T], ignore_index=True).to_csv(source, index=False)
    result = prepare(source, tmp_path, validate_only=True)
    assert result["class_counts"] == {"inactive": 5, "active": 7}
    assert result["duplicate_summary"]["groups_crossing_threshold"] == 1


@pytest.mark.parametrize("mode", [True, False])
def test_single_class_is_not_ready_and_publishes_nothing(source, tmp_path, mode):
    frame = pd.read_csv(source)
    frame["pIC50"] = 6.
    frame.to_csv(source, index=False)
    with pytest.raises(ValueError, match="both classes"):
        prepare(source, tmp_path, validate_only=mode)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("field,value", [
    ("label_threshold", 6), ("family_id", "buche-family"),
    ("source_sha256", "0" * 64), ("assignment_sha256", "0" * 64),
    ("aggregation", "mean"), ("schema_version", True),
])
def test_metadata_tampering_rejected(source, tmp_path, field, value):
    prepare(source, tmp_path, validate_only=False)
    path = tmp_path / "out" / "synthetic-pde" / "family_dataset.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        module().load_family_dataset(path)


def test_child_failure_leaves_no_half_package(source, tmp_path, monkeypatch):
    from src.activity import dataset_contract as dc
    real = dc.write_prepared_dataset
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return real(*args, **kwargs)
    monkeypatch.setattr(dc, "write_prepared_dataset", fail_second)
    with pytest.raises(OSError, match="synthetic failure"):
        prepare(source, tmp_path, validate_only=False)
    assert list((tmp_path / "out").iterdir()) == []
    assert source.exists()


def test_paths_and_private_columns_not_reported(source, tmp_path):
    frame = pd.read_csv(source)
    frame["private_notes"] = "PRIVATE_SENTINEL_DO_NOT_LOG"
    frame.to_csv(source, index=False)
    result = prepare(source, tmp_path, validate_only=False)
    assert "PRIVATE_SENTINEL" not in json.dumps(result)
    for file in (tmp_path / "out").rglob("*"):
        if file.is_file() and file.name != "source.csv":
            assert "PRIVATE_SENTINEL" not in file.read_text(encoding="utf-8")
    assert (tmp_path / "out" / "synthetic-pde" / "source.csv").read_bytes() == source.read_bytes()


@pytest.mark.parametrize("package_id", ["../escape", "NUL", "package.", "package ", "x/y"])
def test_unsafe_package_ids_rejected_without_writes(source, tmp_path, package_id):
    with pytest.raises(ValueError):
        module().prepare_family_dataset(source, family="PDE", package_id=package_id,
                                        output_dir=tmp_path / "out", validate_only=False)
    assert not (tmp_path / "out").exists()


def test_source_decimal_text_just_below_threshold_is_not_rounded_up(source, tmp_path):
    frame = pd.read_csv(source, dtype=str)
    frame.loc[0, "pIC50"] = "4.9999999999999994"
    frame.to_csv(source, index=False)
    adapted, _, _ = module()._adapt_source(source.read_bytes(), "csv")
    assert adapted.loc[0, "pIC50"] == "4.9999999999999994"
    report = prepare(source, tmp_path, validate_only=False)
    assert report["class_counts"] == {"inactive": 6, "active": 6}
    module().load_family_dataset(tmp_path / "out" / "synthetic-pde" / "family_dataset.json")


def test_rehashed_rejection_accounting_must_match_raw_source(source, tmp_path):
    from tests.test_activity_prepared_training_data import rehash
    from src.activity import dataset_contract as dc
    prepare(source, tmp_path, validate_only=False)
    path = tmp_path / "out" / "synthetic-pde" / "family_dataset.json"
    descriptor = json.loads(path.read_bytes())
    child = path.parent / descriptor["datasets"]["regression"]["path"]
    manifest = json.loads(child.read_bytes())
    quality_path = child.parent / "quality_report.json"
    quality = json.loads(quality_path.read_bytes())
    quality["counts"]["input_rows"] += 1
    quality["counts"]["rejected_rows"] += 1
    quality["rejection_reason_counts"]["invalid_smiles"] = 1
    quality_path.write_bytes(dc._json_bytes(quality))
    rehash(child, manifest)
    record = descriptor["datasets"]["regression"]
    record["manifest_sha256"] = hashlib.sha256(child.read_bytes()).hexdigest()
    record["prepared_dataset_sha256"] = manifest["prepared_dataset_sha256"]
    path.write_bytes(dc._json_bytes(descriptor))
    with pytest.raises(ValueError, match="quality"):
        module().load_family_dataset(path)


@pytest.mark.parametrize("validate_only", [True, False])
def test_oversized_aggregated_evidence_rejected_before_readiness_or_writes(
    source, tmp_path, validate_only,
):
    import csv

    frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    frame["reference"] = ""
    # Every source cell is below the parser limit. Aggregation alone crosses it.
    frame.loc[0, "reference"] = "S" * (csv.field_size_limit() // 2)
    frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    frame.to_csv(source, index=False)
    module()._adapt_source(source.read_bytes(), "csv")
    with pytest.raises(ValueError, match="source records"):
        prepare(source, tmp_path, validate_only=validate_only)
    assert not (tmp_path / "out").exists()
