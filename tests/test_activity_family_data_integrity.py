"""Family/main integration regressions using only synthetic source records.

Exercise the adapter against main's byte and identity guards, not model training.
Historical family tests cover basic preparation and descriptor-only tampering.
"""
import csv
import hashlib
import importlib
from importlib.util import find_spec
import io
import json

import pandas as pd
import pytest


def family_modules():
    # Keep a missing staged port an actionable test failure, not a collection error.
    names = ("src.activity.family_contract", "src.activity.family_dataset")
    for name in names:
        assert find_spec(name) is not None, f"Staged family data module is missing: {name}"
    return tuple(importlib.import_module(name) for name in names)


def source_bytes(rows, input_format="csv"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=list(rows[0]),
        delimiter="," if input_format == "csv" else "\t",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


@pytest.fixture
def synthetic_rows():
    values = ("4.9999999999999994", "5.0000000000000000",
              "5.0000000000000009", "4.5000000000000000")
    references = ("NA", "NULL", "", 'synthetic,\tfield\nwith "quote"')
    return [
        dict(Smiles=prefix + ring, pIC50=values[i],
             compound_id=f"{ring_index * 4 + i + 1:06d}", assay_id="000042",
             reference=references[i], target_id="pde-family", endpoint="pIC50")
        for ring_index, ring in enumerate(("c1ccccc1", "c1ccncc1", "C1CCCCC1"))
        for i, prefix in enumerate(("", "C", "CC", "CCC"))
    ]


def small_family(monkeypatch):
    contract, dataset = family_modules()
    builder = contract.build_family_manifest

    def small_manifest(family, dataset_id, task_type):
        return builder(family, dataset_id, task_type,
                       minimum_unique_molecules=3, minimum_scaffolds=3)

    # Lower size gates only; chemistry, labels, hashes and loaders stay real.
    monkeypatch.setattr(contract, "build_family_manifest", small_manifest)
    return dataset


def small_package(tmp_path, monkeypatch, rows, input_format="csv"):
    dataset = small_family(monkeypatch)
    source = tmp_path / f"synthetic.{input_format}"
    source.write_bytes(source_bytes(rows, input_format))
    report = dataset.prepare_family_dataset(
        source, family="PDE", package_id="synthetic-integrity",
        output_dir=tmp_path / "out", validate_only=False,
    )
    assert report["published"] is True
    return source, tmp_path / "out" / "synthetic-integrity" / "family_dataset.json"


@pytest.mark.parametrize("input_format", ["csv", "tsv"])
def test_family_text_survives_adapter_and_strict_prepared_loader(
    tmp_path, monkeypatch, synthetic_rows, input_format,
):
    _, dataset = family_modules()
    from src.activity.model_card import load_prepared_training_data

    raw = source_bytes(synthetic_rows, input_format)
    adapted, adapted_bytes, adapter = dataset._adapt_source(raw, input_format)
    for column in synthetic_rows[0]:
        assert adapted[column].tolist() == [row[column] for row in synthetic_rows]
    assert adapter["adapted_input_sha256"] == hashlib.sha256(adapted_bytes).hexdigest()

    source, path = small_package(tmp_path, monkeypatch, synthetic_rows, input_format)
    bundle = dataset.load_family_dataset(path)
    assert source.read_bytes() == raw
    assert (path.parent / f"source.{input_format}").read_bytes() == raw
    assert bundle["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert bundle["class_counts"] == {"inactive": 6, "active": 6}
    expected = {row["compound_id"]: row for row in synthetic_rows}
    for task, record in bundle["datasets"].items():
        loaded = load_prepared_training_data(path.parent / record["path"])
        prepared = pd.concat(loaded.frames.values(), ignore_index=True)
        assert set(prepared.compound_id) == set(expected)
        assert len(prepared) == len(expected)
        for row in prepared.to_dict("records"):
            original = expected[row["compound_id"]]
            assert row["original_value"] == original["pIC50"]
            for column in ("assay_id", "reference", "target_id", "endpoint"):
                assert row[column] == original[column]
            evidence, = json.loads(row["replicate_evidence"])
            assert evidence["original_value"] == original["pIC50"]
            for column in ("compound_id", "assay_id", "reference", "target_id", "endpoint"):
                assert evidence[column] == original[column]
            value = float(original["pIC50"])
            expected_label = float(value >= 5.0) if task == "classification" else value
            assert row["normalized_value"] == expected_label


@pytest.mark.parametrize("input_format", ["csv", "tsv"])
@pytest.mark.parametrize("kind", ["duplicate-header", "surplus", "missing", "nul"])
@pytest.mark.parametrize("validate_only", [True, False])
def test_family_rejects_lossy_source_before_pandas_and_publication(
    tmp_path, monkeypatch, input_format, kind, validate_only,
):
    _, dataset = family_modules()
    separator = "," if input_format == "csv" else "\t"
    header = ["Smiles", "pIC50", "reference"]
    row = ["CCO", "5", "synthetic"]
    if kind == "duplicate-header":
        header.append("pIC50")
        row.append("4")
    elif kind == "surplus":
        row.insert(0, "discarded-index")
    elif kind == "missing":
        row.pop()
    else:
        row[-1] += "\x00discarded-suffix"
    source = tmp_path / f"synthetic.{input_format}"
    source.write_bytes((separator.join(header) + "\n" + separator.join(row) + "\n").encode())

    def forbid_parser(*args, **kwargs):
        pytest.fail("Malformed family source reached pandas before byte validation")

    monkeypatch.setattr(pd, "read_csv", forbid_parser)
    error = {"duplicate-header": "columns", "surplus": "width",
             "missing": "width", "nul": "NUL"}[kind]
    with pytest.raises(ValueError, match=error):
        dataset.prepare_family_dataset(
            source, family="PDE", package_id="synthetic-integrity",
            output_dir=tmp_path / "out", validate_only=validate_only,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("task", ["regression", "classification"])
@pytest.mark.parametrize("field,value", [
    ("target_id", "buche-family"), ("target_id", "PDE5A"),
    ("endpoint", "pKi"), ("endpoint", "activity"), ("endpoint", ""),
])
def test_family_source_identity_rejection_precedes_duplicate_aggregation(task, field, value):
    contract, dataset = family_modules()
    from src.activity import dataset_contract as dc

    valid = dict(Smiles="CCO", pIC50="4", target_id="pde-family", endpoint="pIC50")
    invalid = dict(valid, pIC50="6")
    invalid[field] = value
    frame, snapshot, _ = dataset._adapt_source(source_bytes([valid, invalid]), "csv")
    declaration = contract.build_family_manifest(
        "PDE5A", "synthetic-identity", task,
        minimum_unique_molecules=1, minimum_scaffolds=1,
    )
    result = dc.validate_activity_dataset(
        frame, declaration, input_bytes=snapshot, input_format="csv",
    )
    assert len(result.accepted) == 1
    accepted = result.accepted.iloc[0]
    assert accepted["replicate_count"] == 1
    assert accepted["normalized_value"] == (0.0 if task == "classification" else 4.0)
    assert result.statistics["rejected_by_reason"] == {f"{field}_mismatch": 1}
    assert result.rejected[field].tolist() == [value]


@pytest.mark.parametrize("field,value", [("target_id", "buche-family"), ("endpoint", "activity")])
@pytest.mark.parametrize("validate_only", [True, False])
def test_family_prepare_cannot_override_conflicting_source_identity(
    tmp_path, monkeypatch, synthetic_rows, field, value, validate_only,
):
    dataset = small_family(monkeypatch)
    source = tmp_path / "synthetic.csv"
    source.write_bytes(source_bytes(synthetic_rows))
    # A positive control rules out unrelated size/split quality-gate failures.
    control = dataset.prepare_family_dataset(
        source, family="PDE", package_id="synthetic-integrity",
        output_dir=tmp_path / "out", validate_only=True,
    )
    assert control["ready_for_training"] is True
    rows = [{**row, field: value} for row in synthetic_rows]
    source.write_bytes(source_bytes(rows))
    with pytest.raises(ValueError, match="quality gate"):
        dataset.prepare_family_dataset(
            source, family="PDE", package_id="synthetic-integrity",
            output_dir=tmp_path / "out", validate_only=validate_only,
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("location", ["row", "replicate"])
@pytest.mark.parametrize("field,value", [("target_id", "buche-family"), ("endpoint", "activity")])
def test_family_loader_rejects_rehashed_child_source_identity(
    tmp_path, monkeypatch, synthetic_rows, location, field, value,
):
    _, dataset = family_modules()
    from src.activity import dataset_contract as dc
    from tests.test_activity_prepared_training_data import rehash

    _, path = small_package(tmp_path, monkeypatch, synthetic_rows)
    descriptor = dataset.load_family_dataset(path)
    child_record = descriptor["datasets"]["classification"]
    child = path.parent / child_record["path"]
    manifest = json.loads(child.read_bytes())
    artifact = child.parent / manifest["artifacts"]["train"]["path"]
    frame = dc.read_prepared_split(artifact)
    if location == "row":
        frame.loc[0, field] = value
    else:
        evidence = json.loads(frame.loc[0, "replicate_evidence"])
        evidence[0][field] = value
        frame.loc[0, "replicate_evidence"] = json.dumps(evidence)
    artifact.write_bytes(dc._csv_bytes(frame))
    rehash(child, manifest)
    child_record["manifest_sha256"] = hashlib.sha256(child.read_bytes()).hexdigest()
    child_record["prepared_dataset_sha256"] = manifest["prepared_dataset_sha256"]
    path.write_bytes(dc._json_bytes(descriptor))
    # Correct artifact/container hashes must not bypass main's source identity check.
    with pytest.raises(ValueError, match="source identity"):
        dataset.load_family_dataset(path)


@pytest.mark.parametrize("source_format", ["csv", "tsv"])
@pytest.mark.parametrize("task", ["classification", "regression"])
def test_family_loader_rejects_rehashed_adapter_format(
    tmp_path, monkeypatch, synthetic_rows, source_format, task,
):
    _, dataset = family_modules()
    from src.activity import dataset_contract as dc
    from src.activity.model_card import load_prepared_training_data

    _, path = small_package(tmp_path, monkeypatch, synthetic_rows, source_format)
    descriptor = dataset.load_family_dataset(path)
    record = descriptor["datasets"][task]
    child = path.parent / record["path"]
    manifest = json.loads(child.read_bytes())
    # Even TSV raw sources are adapted to CSV before the paired child writers.
    assert manifest["input_format"] == "csv"
    manifest["input_format"] = "tsv"
    manifest["input_binding_sha256"] = dc._input_binding_sha256(
        manifest["input_sha256"], manifest["validated_content_sha256"],
        manifest["endpoint_key"], manifest["model_contract_key"], "tsv",
    )
    child.write_bytes(dc._json_bytes(manifest))
    record["manifest_sha256"] = hashlib.sha256(child.read_bytes()).hexdigest()
    path.write_bytes(dc._json_bytes(descriptor))
    # The generic child loader has no raw snapshot: the family layer owns this check.
    assert load_prepared_training_data(child).manifest["input_format"] == "tsv"
    with pytest.raises(ValueError, match="provenance"):
        dataset.load_family_dataset(path)
