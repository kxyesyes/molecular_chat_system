"""Validate or publish paired family datasets; stdout is one redacted UTF-8 JSON."""
from __future__ import annotations

import contextlib
import csv
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prepare_activity_dataset import ArgumentFailure, Parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument("--family", required=True)
        parser.add_argument("--package-id", required=True)
        parser.add_argument("--output-dir", type=Path, required=True)
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--validate-only", action="store_true")
        mode.add_argument("--prepare", action="store_true")
        args = parser.parse_args(argv)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from src.activity.family_dataset import prepare_family_dataset
            report = prepare_family_dataset(args.input, family=args.family, package_id=args.package_id,
                                            output_dir=args.output_dir, validate_only=args.validate_only)
        code = 0
    except ArgumentFailure:
        code, report = 2, {"error_code": "invalid_arguments"}
    except (ValueError, FileExistsError, UnicodeError, csv.Error):
        code, report = 2, {"error_code": "contract_rejected"}
    except Exception:
        code, report = 1, {"error_code": "runtime_failure"}
    if code:
        report.update(status="failed", ready_for_training=False, published=False)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
