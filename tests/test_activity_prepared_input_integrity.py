"""Prepared training must retain the ingestion boundary's strict byte semantics."""
import csv
import io
import json

import pandas as pd
import pytest

from src.activity.model_card import load_prepared_training_data
from src.activity import dataset_contract as dc, model_card
from tests.test_activity_prepared_training_data import prepared, rehash


def corrupt_records(path, split, kind):
    manifest = json.loads(path.read_bytes())
    artifact = path.parent / manifest["artifacts"][split]["path"]
    rows = list(csv.reader(io.StringIO(artifact.read_text(encoding="utf-8"))))
    if kind == "surplus":
        rows = [rows[0], *[["synthetic-ignored-index", *row] for row in rows[1:]]]
    else:
        column = rows[0].index("reference")
        for row in rows[1:]:
            row[column] += "<NUL>synthetic-hidden-suffix"
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    artifact.write_bytes(stream.getvalue().encode().replace(b"<NUL>", b"\x00"))
    # Correct file hashes cannot compensate for a parser discarding source fields.
    rehash(path, manifest)


@pytest.mark.parametrize("split", ["train", "validation", "test"])
@pytest.mark.parametrize("kind", ["surplus", "nul"])
def test_training_loader_rejects_rehashed_parser_loss(tmp_path, split, kind):
    path = prepared(tmp_path)
    corrupt_records(path, split, kind)
    with pytest.raises(ValueError, match="record width|NUL"):
        load_prepared_training_data(path)


@pytest.mark.parametrize("kind", ["surplus", "nul"])
def test_training_loader_rejects_bad_bytes_before_dataframe_parser(tmp_path, monkeypatch, kind):
    path = prepared(tmp_path)
    corrupt_records(path, "train", kind)
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("bad bytes reached pandas"))
    with pytest.raises(ValueError, match="record width|NUL"):
        load_prepared_training_data(path)


def test_training_manifest_cannot_relabel_retained_source_target(tmp_path):
    declaration = dc.DatasetManifest(
        dataset_id="synthetic-source-identity", target_id="source-target",
        target_name="Synthetic source", task_type="regression", endpoint="pIC50",
        units="pIC50", label_transform="identity", source="synthetic", license="test-only",
        minimum_unique_molecules=3, minimum_scaffolds=3,
    )
    frame = pd.DataFrame([
        dict(smiles=prefix + ring, value=str(5 + i), units="pIC50", relation="=",
             target_id="source-target", endpoint="pIC50")
        for ring in ("c1ccccc1", "c1ccncc1", "C1CCCCC1")
        for i, prefix in enumerate(("", "C", "CC", "CCC"))
    ])
    validated = dc.validate_activity_dataset(
        frame, declaration, input_bytes=dc._csv_bytes(frame), input_format="csv",
    )
    split = dc.split_prepared_dataset(validated.accepted, ratios=(.34, .33, .33))
    path = dc.write_prepared_dataset(validated, split, declaration, tmp_path)
    manifest = load_prepared_training_data(path).manifest
    manifest["target_id"] = "other-target"
    for key in ("endpoint_key", "model_contract_key"):
        manifest[key] = manifest[key].replace("source-target", "other-target")
    manifest["input_binding_sha256"] = dc._input_binding_sha256(
        manifest["input_sha256"], manifest["validated_content_sha256"],
        manifest["endpoint_key"], manifest["model_contract_key"], manifest["input_format"],
    )
    path.write_bytes(dc._json_bytes(manifest))
    with pytest.raises(ValueError, match="source identity"):
        load_prepared_training_data(path)


@pytest.mark.parametrize("location", ["row", "replicate"])
@pytest.mark.parametrize("field,value", [("target_id", "other-target"), ("endpoint", "pKi")])
def test_training_label_evidence_checks_optional_source_identity(tmp_path, location, field, value):
    loaded = load_prepared_training_data(prepared(tmp_path))
    frame = loaded.frames["train"].copy()
    if location == "row":
        frame[field] = value
    else:
        frame["replicate_evidence"] = frame["replicate_evidence"].map(
            lambda text: json.dumps([{**item, field: value} for item in json.loads(text)])
        )
    with pytest.raises(ValueError, match="source identity"):
        model_card._validate_labels(frame, model_card._declaration(loaded.manifest))
