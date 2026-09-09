"""CLI uses generated synthetic CSVs, never the user's private datasets."""
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_family_activity_dataset.py"


@pytest.fixture
def dataset(tmp_path):
    rows = []
    for ring_size in range(3, 15):
        ring = "C1" + "C" * (ring_size - 1) + "1"
        for i in range(12):
            rows.append({"Smiles": "C" * i + ring, "pIC50": 4. + (i % 2) * 2})
    path = tmp_path / "synthetic.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def run(*args):
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                          cwd=ROOT, capture_output=True, encoding="utf-8", timeout=120)


def test_validate_then_publish_and_reject_overwrite(dataset, tmp_path):
    output = tmp_path / "prepared"
    args = ["--input", dataset, "--family", "PDE5A", "--package-id", "synthetic-pde", "--output-dir", output]
    check = run(*args, "--validate-only")
    assert check.returncode == 0, check.stdout + check.stderr
    assert not output.exists()
    assert json.loads(check.stdout)["ready_for_training"]
    create = run(*args, "--prepare")
    assert create.returncode == 0, create.stdout + create.stderr
    report = json.loads(create.stdout)
    assert report["published"] is True
    assert str(tmp_path) not in create.stdout
    assert create.stderr == ""
    repeated = run(*args, "--prepare")
    assert repeated.returncode == 2
    assert json.loads(repeated.stdout)["status"] == "failed"


@pytest.mark.parametrize("kind", ["bad-family", "missing-column", "single-class", "bad-smiles"])
def test_failure_reports_no_private_rows(dataset, tmp_path, kind):
    frame = pd.read_csv(dataset)
    family = "PDE"
    if kind == "bad-family":
        family = "PRIVATE_IDENTIFIER"
    elif kind == "missing-column":
        frame = frame.rename(columns={"Smiles": "PRIVATE_COLUMN"})
    elif kind == "single-class":
        frame["pIC50"] = 6.
    else:
        frame["Smiles"] = "PRIVATE_MOLECULE_INVALID"
    frame.to_csv(dataset, index=False)
    result = run("--input", dataset, "--family", family, "--package-id", "synthetic",
                 "--output-dir", tmp_path / "out", "--validate-only")
    assert result.returncode == 2
    assert json.loads(result.stdout)["ready_for_training"] is False
    assert "PRIVATE_" not in result.stdout + result.stderr
    assert result.stderr == ""
    assert str(tmp_path) not in result.stdout


def test_bad_arguments_are_structured():
    result = run("--unknown", "PRIVATE_VALUE")
    assert result.returncode == 2
    assert json.loads(result.stdout)["error_code"] == "invalid_arguments"
    assert "PRIVATE_VALUE" not in result.stdout + result.stderr


def test_malformed_row_rejected_without_private_index_leak(dataset, tmp_path):
    dataset.write_text(dataset.read_text() + "PRIVATE_VALUE,CCO,6\n", encoding="utf-8")
    result = run("--input", dataset, "--family", "PDE", "--package-id", "synthetic",
                 "--output-dir", tmp_path / "out", "--prepare")
    assert result.returncode == 2
    assert json.loads(result.stdout)["published"] is False
    assert "PRIVATE_VALUE" not in result.stdout + result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode", ["--validate-only", "--prepare"])
def test_aggregated_evidence_limit_is_a_structured_preflight_failure(dataset, tmp_path, mode):
    import csv

    frame = pd.read_csv(dataset, dtype=str, keep_default_na=False)
    frame["reference"] = ""
    frame.loc[0, "reference"] = "S" * (csv.field_size_limit() // 2)
    pd.concat([frame, frame.iloc[[0]]], ignore_index=True).to_csv(dataset, index=False)
    result = run("--input", dataset, "--family", "PDE", "--package-id", "synthetic",
                 "--output-dir", tmp_path / "out", mode)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report == {"error_code": "contract_rejected", "status": "failed",
                      "ready_for_training": False, "published": False}
    assert result.stderr == ""
    assert not (tmp_path / "out").exists()
