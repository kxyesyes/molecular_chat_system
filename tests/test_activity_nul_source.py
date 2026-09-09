"""NUL bytes must not be truncated into apparently valid scientific evidence."""
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import prepare_activity_dataset as cli
from src.activity import dataset_contract as dc
from tests.test_activity_dataset_blockers import manifest, source_frame


ERROR = "NUL bytes are not allowed in activity source"


def raw_source(separator, contaminated="all"):
    frame = source_frame(compound_id="000123", target_id="pde-family", endpoint="pIC50")
    columns = ("value", "compound_id", "target_id") if contaminated == "all" else (contaminated,)
    for name in columns:
        if name != "header":
            if name not in frame:
                frame[name] = "synthetic"
            frame[name] += "<synthetic-NUL>discarded-synthetic-suffix"
    # Inject after CSV serialization: the writer itself may refuse actual NUL.
    data = frame.to_csv(index=False, sep=separator).encode("utf-8").replace(b"<synthetic-NUL>", b"\x00")
    if contaminated == "header":
        data = data.replace(b"compound_id", b"compound_id\x00discarded-synthetic-suffix", 1)
    assert b"\x00" in data  # Actual NUL, not the textual four-character escape.
    return data


@pytest.fixture(params=[("csv", ","), ("tsv", "\t")])
def input_case(request, tmp_path):
    input_format, separator = request.param
    data = raw_source(separator)
    path = tmp_path / f"synthetic.{input_format}"
    path.write_bytes(data)
    declaration = manifest()
    manifest_path = tmp_path / "manifest.json"
    fields = {name: getattr(declaration, name) for name in (
        "dataset_id", "target_id", "target_name", "task_type", "endpoint", "units",
        "label_transform", "source", "license", "minimum_unique_molecules", "minimum_scaffolds",
    )}
    manifest_path.write_text(json.dumps(fields), encoding="utf-8")
    args = SimpleNamespace(input=path, manifest=manifest_path, output_dir=tmp_path / "prepared",
                           prepare=True, validate_only=False)
    return input_format, separator, data, args


def test_cli_run_rejects_nul_before_preparation(input_case):
    _, _, _, args = input_case
    with pytest.raises(ValueError, match=f"^{ERROR}$"):
        cli._run(args)
    assert not args.output_dir.exists()


@pytest.mark.parametrize("mode", ["--prepare", "--validate-only"])
def test_cli_nul_failure_is_fixed_and_non_sensitive(input_case, tmp_path, mode):
    _, _, _, args = input_case
    script = Path(cli.__file__).resolve()
    result = subprocess.run(
        [sys.executable, "-B", str(script), "--input", str(args.input),
         "--manifest", str(args.manifest), "--output-dir", str(args.output_dir), mode],
        cwd=tmp_path, capture_output=True, timeout=60,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "error_code": "contract_rejected", "status": "failed", "ready_for_training": False,
    }
    assert result.stderr == b""
    assert not args.output_dir.exists()


@pytest.mark.parametrize("source_kind", ["bytes", "path"])
def test_direct_snapshot_cannot_certify_nul_truncation(input_case, source_kind):
    input_format, separator, data, args = input_case
    # Reproduce the real C-parser loss, rather than manufacturing an unrelated frame.
    truncated = pd.read_csv(io.BytesIO(data), sep=separator, dtype=str, keep_default_na=False)
    assert set(truncated["value"]) == {"4.8"}
    assert set(truncated["compound_id"]) == {"000123"}
    assert set(truncated["target_id"]) == {"pde-family"}
    kwargs = ({"input_bytes": data, "input_format": input_format} if source_kind == "bytes"
              else {"input_path": args.input})
    with pytest.raises(ValueError, match=f"^{ERROR}$"):
        dc.validate_activity_dataset(truncated, manifest(), **kwargs)


def test_snapshot_acquisition_itself_rejects_nul(input_case):
    input_format, _, data, _ = input_case
    with pytest.raises(ValueError, match=f"^{ERROR}$"):
        dc._source_snapshot(input_bytes=data, input_path=None, input_format=input_format)


@pytest.mark.parametrize("column", ["value", "compound_id", "target_id", "endpoint", "header", "extra"])
def test_snapshot_rejects_nul_anywhere_before_pandas_parses(input_case, monkeypatch, column):
    input_format, separator, _, _ = input_case
    data = raw_source(separator, column)
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("NUL reached pandas parser"))
    with pytest.raises(ValueError, match=f"^{ERROR}$"):
        dc._verify_source_snapshot_matches_frame(data, source_frame(), input_format)


def test_cli_rejects_nul_before_any_csv_parser(input_case, monkeypatch):
    _, _, _, args = input_case
    monkeypatch.setattr(cli.csv, "reader", lambda *a, **k: pytest.fail("NUL reached CSV parser"))
    with pytest.raises(ValueError, match=f"^{ERROR}$"):
        cli._run(args)
    assert not args.output_dir.exists()
