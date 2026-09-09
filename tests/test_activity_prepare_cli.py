"""Real subprocess contracts for the private activity-data preparation CLI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare_activity_dataset.py"


def inputs(tmp_path: Path, *, tsv: bool = False, invalid: bool = False):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "dataset_id": "synthetic-cli-v1", "target_id": "P16499",
        "target_name": "PDE5A", "task_type": "regression",
        "endpoint": "pIC50", "units": "pIC50", "label_transform": "identity",
        "source": "synthetic-contract-test", "license": "test-only",
        "minimum_unique_molecules": 3, "minimum_scaffolds": 3,
    }), encoding="utf-8")
    path = tmp_path / ("private.tsv" if tsv else "private.csv")
    rows = "smiles,value,units,relation,reference\n"
    if invalid:
        rows += "CC(C)((,7.0,pIC50,=,private-reference\n"
    else:
        rows += "c1ccccc1,7.0,pIC50,=,private-reference\n"
        rows += "c1ccncc1,6.5,pIC50,=,private-reference\n"
        rows += "C1CCCCC1,6.0,pIC50,=,private-reference\n"
    path.write_text(rows.replace(",", "\t") if tsv else rows, encoding="utf-8")
    return path, manifest, tmp_path / "prepared"


def invoke(tmp_path, path, manifest, output, *flags, encoding="utf-8"):
    env = {**os.environ, "PYTHONIOENCODING": encoding,
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([
        sys.executable, str(SCRIPT), "--input", str(path),
        "--manifest", str(manifest), "--output-dir", str(output), *flags,
    ], cwd=tmp_path, env=env, capture_output=True, timeout=60)
    payload = json.loads(result.stdout.decode("utf-8"))
    combined = result.stdout.decode("utf-8") + result.stderr.decode("utf-8", errors="replace")
    assert "private-reference" not in combined
    assert "CC(C)((" not in combined
    assert str(tmp_path) not in combined
    return result, payload


@pytest.mark.parametrize("tsv", [False, True])
@pytest.mark.parametrize("encoding", ["utf-8", "gbk:strict"])
def test_validate_only_is_readonly_and_direct_invocation_works(tmp_path, tsv, encoding):
    path, manifest, output = inputs(tmp_path, tsv=tsv)
    result, payload = invoke(tmp_path, path, manifest, output, "--validate-only", encoding=encoding)
    assert result.returncode == 0
    assert payload["ready_for_training"] is True
    assert payload["statistics"]["accepted_rows"] == 3
    assert not output.exists()


@pytest.mark.parametrize("tsv", [False, True])
def test_prepare_writes_verified_artifacts_without_disclosing_rows(tmp_path, tsv):
    path, manifest, output = inputs(tmp_path, tsv=tsv)
    result, payload = invoke(tmp_path, path, manifest, output, "--prepare")
    assert result.returncode == 0
    assert payload["artifact_directory"] == "synthetic-cli-v1"
    directory = output / "synthetic-cli-v1"
    metadata = json.loads((directory / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert metadata["input_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("train.csv", "validation.csv", "test.csv", "quality_report.json"):
        assert (directory / name).is_file()
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    second, _ = invoke(tmp_path, path, manifest, output, "--prepare")
    assert second.returncode == 2
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize("mode", ["--validate-only", "--prepare"])
def test_invalid_input_rejects_without_writing_or_leaking(tmp_path, mode):
    path, manifest, output = inputs(tmp_path, invalid=True)
    result, payload = invoke(tmp_path, path, manifest, output, mode)
    assert result.returncode == 2
    assert payload["ready_for_training"] is False
    assert payload["statistics"]["rejected_by_reason"]["invalid_smiles"] == 1
    assert not output.exists()


@pytest.mark.parametrize("flags", [(), ("--prepare", "--validate-only")])
def test_mode_required_and_mutually_exclusive(tmp_path, flags):
    path, manifest, output = inputs(tmp_path)
    result, payload = invoke(tmp_path, path, manifest, output, *flags)
    assert result.returncode == 2
    assert payload["error_code"] == "invalid_arguments"
    assert not output.exists()


def test_missing_input_reports_runtime_failure_without_path(tmp_path):
    path, manifest, output = inputs(tmp_path)
    result, payload = invoke(tmp_path, path.with_name("missing.csv"), manifest, output, "--prepare")
    assert result.returncode == 1
    assert payload["ready_for_training"] is False
    assert not output.exists()


def test_bad_manifest_is_contract_failure(tmp_path):
    path, manifest, output = inputs(tmp_path)
    manifest.write_text('{"private-reference": "invalid"}', encoding="utf-8")
    result, payload = invoke(tmp_path, path, manifest, output, "--prepare")
    assert result.returncode == 2
    assert payload["error_code"] == "contract_rejected"
    assert not output.exists()


def test_duplicate_columns_are_rejected_before_pandas_can_rename_them(tmp_path):
    path, manifest, output = inputs(tmp_path)
    path.write_text("smiles,value,units,relation,value\nCCO,1,pIC50,=,2\n", encoding="utf-8")
    result, payload = invoke(tmp_path, path, manifest, output, "--prepare")
    assert result.returncode == 2
    assert payload["error_code"] == "contract_rejected"
    assert not output.exists()


def test_three_way_feasibility_checked_even_with_lower_manifest_gate(tmp_path):
    path, manifest, output = inputs(tmp_path)
    path.write_text("smiles,value,units,relation\nCCO,1,pIC50,=\n", encoding="utf-8")
    declaration = json.loads(manifest.read_text(encoding="utf-8"))
    declaration.update(minimum_unique_molecules=1, minimum_scaffolds=1)
    manifest.write_text(json.dumps(declaration), encoding="utf-8")
    result, payload = invoke(tmp_path, path, manifest, output, "--validate-only")
    assert result.returncode == 2
    assert payload["ready_for_training"] is False
    assert not output.exists()


@pytest.mark.parametrize("filename,ignored", [
    ("data/activity/raw/private.csv", True),
    ("data/activity/prepared/example/train.csv", True),
    ("data/activity/models/weights.pth", True),
    ("outputs/activity/report.json", True),
    ("data/activity/raw/README.md", False),
    ("data/activity/prepared/README.md", False),
    ("data/activity/models/README.md", True),
])
def test_activity_assets_ignored_but_documentation_trackable(filename, ignored):
    result = subprocess.run(["git", "check-ignore", "--no-index", "-q", filename],
                            cwd=ROOT, capture_output=True)
    assert result.returncode == (0 if ignored else 1)
