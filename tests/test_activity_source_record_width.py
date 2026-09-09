"""Reject CSV/TSV index inference and implicit padding without losing quoted data."""
import csv
import io

import pandas as pd
import pytest

from src.activity import dataset_contract as dc
from tests.test_activity_prepare_cli import inputs, invoke


def malformed_source(separator, kind):
    header = ["smiles", "value", "units", "relation", "reference"]
    rows = []
    for smiles in ("c1ccccc1", "c1ccncc1", "C1CCCCC1"):
        row = [smiles, "7", "pIC50", "=", "synthetic-reference"]
        rows.append(["CCO", *row] if kind == "surplus" else row[:-1])
    return ("\n".join(separator.join(row) for row in [header, *rows]) + "\n").encode()


@pytest.mark.parametrize("tsv", [False, True])
@pytest.mark.parametrize("kind", ["surplus", "missing"])
@pytest.mark.parametrize("mode", ["--validate-only", "--prepare"])
def test_cli_rejects_wrong_record_width_without_publication(tmp_path, tsv, kind, mode):
    path, manifest, output = inputs(tmp_path, tsv=tsv)
    path.write_bytes(malformed_source("\t" if tsv else ",", kind))
    process, report = invoke(tmp_path, path, manifest, output, mode)
    assert process.returncode == 2
    assert report == {"error_code": "contract_rejected", "status": "failed",
                      "ready_for_training": False}
    assert process.stderr == b""
    assert not output.exists()


@pytest.mark.parametrize("input_format,separator", [("csv", ","), ("tsv", "\t")])
@pytest.mark.parametrize("kind", ["surplus", "missing"])
def test_snapshot_cannot_certify_inferred_index_or_padded_columns(input_format, separator, kind):
    source = malformed_source(separator, kind)
    lossy = pd.read_csv(io.BytesIO(source), sep=separator, dtype=str, keep_default_na=False)
    if kind == "surplus":
        assert lossy.index.tolist() == ["CCO"] * 3
    else:
        assert lossy["reference"].tolist() == [""] * 3
    with pytest.raises(ValueError, match="record width"):
        dc._verify_source_snapshot_matches_frame(source, lossy, input_format)


@pytest.mark.parametrize("input_format,separator", [("csv", ","), ("tsv", "\t")])
@pytest.mark.parametrize("kind", ["surplus", "missing"])
def test_width_check_happens_before_pandas(input_format, separator, kind, monkeypatch):
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("bad width reached pandas"))
    with pytest.raises(ValueError, match="record width"):
        dc._verify_source_snapshot_matches_frame(
            malformed_source(separator, kind), pd.DataFrame(), input_format,
        )


@pytest.mark.parametrize("tsv", [False, True])
def test_quoted_multiline_delimiters_empty_fields_and_bom_survive_preparation(tmp_path, tsv):
    path, manifest, output = inputs(tmp_path, tsv=tsv)
    separator = "\t" if tsv else ","
    text = io.StringIO(newline="")
    writer = csv.writer(text, delimiter=separator)
    writer.writerow(["smiles", "value", "units", "relation", "reference"])
    evidence = [f'quoted{separator}field\nwith "quote"', "", "last"]
    for smiles, reference in zip(("c1ccccc1", "c1ccncc1", "C1CCCCC1"), evidence):
        writer.writerow([smiles, "7", "pIC50", "=", reference])
    # Empty physical lines remain harmless; quoted newlines are one CSV record.
    path.write_bytes((text.getvalue() + "\r\n").encode("utf-8-sig"))
    process, report = invoke(tmp_path, path, manifest, output, "--prepare")
    assert process.returncode == 0
    assert report["statistics"]["accepted_rows"] == 3
    published = pd.concat([dc.read_prepared_split(output / "synthetic-cli-v1" / f"{name}.csv")
                           for name in ("train", "validation", "test")])
    assert set(published["reference"]) == set(evidence)
