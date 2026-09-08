"""Synthetic-only regressions for the held activity integration blockers."""
import io
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from src.activity import dataset_contract as dc


def manifest(**overrides):
    fields = dict(
        dataset_id="synthetic-blockers", target_id="pde-family",
        target_name="Synthetic PDE family", task_type="regression",
        endpoint="pIC50", units="pIC50", label_transform="identity",
        source="synthetic-test", license="test-only",
        minimum_unique_molecules=3, minimum_scaffolds=3,
    )
    fields.update(overrides)
    return dc.DatasetManifest(**fields)


def source_frame(**columns):
    frame = pd.DataFrame({
        "smiles": [prefix + ring for ring in ("c1ccccc1", "c1ccncc1", "C1CCCCC1")
                   for prefix in ("", "C", "CC", "CCC")],
        "value": "4.8", "units": "pIC50", "relation": "=",
    })
    for name, value in columns.items():
        frame[name] = value
    return frame


@pytest.mark.parametrize("identity,reason", [
    ({"target_id": "buche-family"}, "target_id_mismatch"),
    ({"endpoint": "pKi"}, "endpoint_mismatch"),
    ({"target_id": "buche-family", "endpoint": "pKi"}, "target_id_mismatch"),
    ({"target_id": ""}, "target_id_mismatch"),
    ({"endpoint": None}, "endpoint_mismatch"),
])
def test_source_identity_conflicts_are_rejected(identity, reason):
    frame = source_frame(compound_id="000123", reference="synthetic-evidence", **identity)
    result = dc.validate_activity_dataset(frame, manifest())
    assert result.accepted.empty
    assert not result.ready_for_training
    assert result.statistics["rejected_by_reason"] == {reason: 12}
    for name in (*identity, "compound_id", "reference"):
        assert result.rejected[name].tolist() == [dc._evidence_text(frame.iloc[0][name])] * 12


@pytest.mark.parametrize("identity", [
    {}, {"target_id": "pde-family"}, {"endpoint": "pIC50"},
    {"target_id": " PDE-FAMILY ", "endpoint": " pic50 "},
])
def test_declared_or_matching_source_identity_survives_publication(tmp_path, identity):
    frame = source_frame(**identity)
    declaration = manifest()
    result = dc.validate_activity_dataset(
        frame, declaration, input_bytes=frame.to_csv(index=False).encode(), input_format="csv",
    )
    assert result.ready_for_training
    assert result.rejected.empty
    split = dc.split_prepared_dataset(result.accepted)
    path = dc.write_prepared_dataset(result, split, declaration, tmp_path)
    published = pd.concat([dc.read_prepared_split(path.parent / f"{name}.csv")
                           for name in ("train", "validation", "test")])
    for name, value in identity.items():
        assert set(published[name]) == {value}
        assert all(json.loads(evidence)[0][name] == value
                   for evidence in published["replicate_evidence"])
    if not identity:
        assert "target_id" not in published and "endpoint" not in published


def test_source_endpoint_is_checked_before_output_transform():
    declaration = manifest(endpoint="IC50", units="nM", label_transform="molar_to_pactivity",
                           output_endpoint="pIC50", output_units="pIC50")
    good = dc.validate_activity_dataset(source_frame(endpoint="IC50", units="nM"), declaration)
    bad = dc.validate_activity_dataset(source_frame(endpoint="pIC50", units="nM"), declaration)
    assert good.ready_for_training
    assert bad.accepted.empty
    assert bad.statistics["rejected_by_reason"] == {"endpoint_mismatch": 12}


@pytest.mark.parametrize("failure", ["invalid_smiles", "conflicting_duplicate_labels"])
def test_rejected_rows_keep_available_identity_and_evidence(tmp_path, failure):
    frame = source_frame(target_id="pde-family", endpoint="pIC50", compound_id="000123")
    declaration = manifest(task_type="classification", endpoint="active", units="binary")
    frame["endpoint"], frame["units"], frame["value"] = "active", "binary", "0"
    if failure == "invalid_smiles":
        frame.loc[0, "smiles"] = "not-a-molecule"
    else:
        extra = frame.iloc[[0]].copy()
        extra["value"] = "1"
        frame = pd.concat([frame, extra], ignore_index=True)
    result = dc.validate_activity_dataset(
        frame, declaration, input_bytes=frame.to_csv(index=False).encode(), input_format="csv",
    )
    assert set(result.rejected["rejection_reason"]) == {failure}
    rejected_path = tmp_path / "rejected.csv"
    dc.write_prepared_dataset(result, dc.split_prepared_dataset(result.accepted), declaration,
                              tmp_path, rejected_output_path=rejected_path)
    rejected = pd.read_csv(rejected_path, dtype=str, keep_default_na=False)
    assert set(rejected["target_id"]) == {"pde-family"}
    assert set(rejected["endpoint"]) == {"active"}
    assert set(rejected["compound_id"]) == {"000123"}


def test_conflicting_identity_does_not_contaminate_matching_replicates():
    frame = source_frame(target_id="pde-family", endpoint="pIC50")
    conflict = frame.iloc[[0]].copy()
    conflict["target_id"], conflict["value"] = "buche-family", "9.0"
    result = dc.validate_activity_dataset(pd.concat([frame, conflict], ignore_index=True), manifest())
    assert result.statistics["rejected_rows"] == 1
    assert result.statistics["accepted_rows"] == 12
    assert set(result.accepted["normalized_value"]) == {4.8}
    assert set(result.accepted["replicate_count"]) == {1}


def threshold_manifest(**overrides):
    return manifest(task_type="classification", label_transform="binary_threshold",
                    classification_threshold=5, classification_direction="greater_or_equal",
                    output_endpoint="active", output_units="probability", **overrides)


@pytest.mark.parametrize("input_format,separator", [("csv", ","), ("tsv", "\t")])
@pytest.mark.parametrize("value,compound_id", [
    ("4.9999999999999994", "123"),  # Isolate rounding from ID corruption.
    ("5", "000123"),               # Isolate ID corruption from rounding.
    ("4.9999999999999994", "000123"),
    ("5.0000", "123"),             # Numeric equality cannot prove lexical identity.
    ("5e0", "123"),
])
def test_source_snapshot_rejects_lossy_inferred_parse(input_format, separator, value, compound_id):
    source = ("smiles,value,units,relation,compound_id\n"
              f"CCO,{value},pIC50,=,{compound_id}\n").replace(",", separator).encode()
    inferred = pd.read_csv(io.BytesIO(source), sep=separator, keep_default_na=False)
    with pytest.raises(ValueError, match="source snapshot.*dataframe"):
        dc.validate_activity_dataset(inferred, threshold_manifest(),
                                     input_bytes=source, input_format=input_format)


@pytest.mark.parametrize("input_format,separator", [("csv", ","), ("tsv", "\t")])
def test_exact_source_text_preserves_threshold_and_ids(input_format, separator):
    source = ("smiles,value,units,relation,compound_id\n"
              "CCO,4.9999999999999994,pIC50,=,000123\n"
              "c1ccccc1,5,pIC50,=,000124\n"
              "c1ccncc1,5.0000,pIC50,=,000125\n").replace(",", separator).encode()
    frame = pd.read_csv(io.BytesIO(source), sep=separator, keep_default_na=False, dtype=str)
    declaration = threshold_manifest()
    result = dc.validate_activity_dataset(frame, declaration, input_bytes=source, input_format=input_format)
    assert declaration.classification_threshold == 5
    assert result.ready_for_training
    assert result.accepted["normalized_value"].tolist() == [0, 1, 1]
    assert result.accepted["original_value"].tolist() == frame["value"].tolist()
    assert result.accepted["compound_id"].tolist() == frame["compound_id"].tolist()


@pytest.mark.parametrize("input_format,separator", [("csv", ","), ("tsv", "\t")])
def test_generic_cli_preserves_source_lexemes_in_published_artifacts(tmp_path, input_format, separator):
    source = ("smiles,value,units,relation,compound_id,assay_id,reference\n"
              "CCO,4.9999999999999994,pIC50,=,000123,000007,00123\n"
              "c1ccccc1,5,pIC50,=,000124,000008,00124\n"
              "c1ccncc1,5.0000,pIC50,=,000125,000009,00125\n")
    path = tmp_path / f"synthetic.{input_format}"
    path.write_text(source.replace(",", separator), encoding="utf-8")
    declaration = threshold_manifest()
    manifest_path = tmp_path / "manifest.json"
    # Do not serialize the dataclass's derived defaults: the strict manifest
    # loader distinguishes omitted optional declarations from null fields.
    fields = {name: getattr(declaration, name) for name in (
        "dataset_id", "target_id", "target_name", "task_type", "endpoint", "units",
        "label_transform", "source", "license", "minimum_unique_molecules", "minimum_scaffolds",
        "classification_threshold", "classification_direction", "output_endpoint", "output_units",
    )}
    manifest_path.write_text(json.dumps(fields), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_activity_dataset.py"
    result = subprocess.run(
        [sys.executable, "-B", str(script), "--input", str(path), "--manifest", str(manifest_path),
         "--output-dir", str(tmp_path / "prepared"), "--prepare"],
        cwd=tmp_path, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout.decode("utf-8") + result.stderr.decode("utf-8")
    assert json.loads(result.stdout)["ready_for_training"] is True
    directory = tmp_path / "prepared" / declaration.dataset_id
    published = pd.concat([dc.read_prepared_split(directory / f"{name}.csv")
                           for name in ("train", "validation", "test")]).set_index("original_smiles")
    original = pd.read_csv(io.StringIO(source), dtype=str, keep_default_na=False).set_index("smiles")
    for smiles, row in original.iterrows():
        actual = published.loc[smiles]
        assert actual["original_value"] == row["value"]
        assert actual["normalized_value"] == (0 if smiles == "CCO" else 1)
        for name in ("compound_id", "assay_id", "reference"):
            assert actual[name] == row[name]
            assert json.loads(actual["replicate_evidence"])[0][name] == row[name]
