"""Validate private CSV/TSV activity measurements or publish verified splits.

Except for --help, stdout is one UTF-8 JSON report. Exit codes: 0 ready, 2 contract rejection,
1 runtime failure. Source rows and local paths are never included in reports.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class ArgumentFailure(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's message may echo private arguments.
        raise ArgumentFailure("invalid_arguments")


def _arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    return parser.parse_args(argv)


def _run(args):
    import pandas as pd
    from rdkit import rdBase
    from src.activity.dataset_contract import (
        _validate_source_records,
        load_dataset_manifest, split_prepared_dataset,
        validate_activity_dataset, write_prepared_dataset,
    )

    manifest = load_dataset_manifest(args.manifest)
    input_format = {".csv": "csv", ".tsv": "tsv"}.get(args.input.suffix.lower())
    if input_format is None:
        raise ValueError("unsupported_input_format")
    source_bytes = args.input.read_bytes()
    _validate_source_records(source_bytes, input_format)
    delimiter = "\t" if input_format == "tsv" else ","
    frame = pd.read_csv(io.BytesIO(source_bytes), encoding="utf-8",
                        keep_default_na=False, sep=delimiter, dtype=str)
    # RDKit parse errors include the input molecule; keep them out of stderr.
    with rdBase.BlockLogs():
        result = validate_activity_dataset(frame, manifest, input_bytes=source_bytes,
                                           input_format=input_format)
        report = {
            "status": "passed" if result.ready_for_training else "failed",
            "ready_for_training": result.ready_for_training,
            "statistics": {
                key: result.statistics[key] for key in (
                    "input_rows", "accepted_rows", "rejected_rows", "unique_molecules",
                    "unique_scaffolds", "rejected_by_reason", "duplicate_rows_collapsed",
                )
            },
            "warning_count": len(result.warnings),
        }
        if not result.ready_for_training:
            report["error_code"] = "quality_gate_rejected"
            return 2, report
        # Also prove that three nonempty partitions are feasible in validate-only.
        split = split_prepared_dataset(result.accepted)
        if args.prepare:
            manifest_path = write_prepared_dataset(result, split, manifest, args.output_dir)
            report["artifact_directory"] = manifest_path.parent.name
    return 0, report


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    try:
        args = _arguments(argv)
        # Library diagnostics cannot pollute the report or disclose source rows.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code, report = _run(args)
    except ArgumentFailure:
        code, report = 2, {"error_code": "invalid_arguments"}
    except (ValueError, FileExistsError, UnicodeError, csv.Error):
        code, report = 2, {"error_code": "contract_rejected"}
    except Exception:
        code, report = 1, {"error_code": "runtime_failure"}
    if code:
        report.update(status="failed", ready_for_training=False)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
