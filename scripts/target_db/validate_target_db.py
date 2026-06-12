"""CLI validation for the local target-search database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.target_search.validate import EXPECTED_PDE_TARGETS, validate_target_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MedChat target-search database and structure cache.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT), help="Project root directory.")
    parser.add_argument("--expected-pde", default="", help="Comma-separated PDE gene symbols to require.")
    parser.add_argument("--require-cache", action="store_true", help="Treat missing local structure files as errors.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero for warning or error status.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    expected_pde = _parse_expected_pde(args.expected_pde)
    report = validate_target_database(
        project_root=args.project_root,
        expected_pde_targets=expected_pde,
        require_cache=args.require_cache,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human_report(report)

    if report["status"] == "error":
        return 1
    if args.strict and report["status"] != "ok":
        return 1
    return 0


def _parse_expected_pde(value: str) -> set[str]:
    if not value.strip():
        return set(EXPECTED_PDE_TARGETS)
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def _print_human_report(report: dict) -> None:
    print("MedChat target database validation")
    print(f"Status: {report['status']}")
    print(f"Database: {report['database_file_path']}")
    print(f"Cache: {report['cache_dir_path']}")
    print(
        "Summary: "
        f"{report['summary']['target_count']} targets, "
        f"{report['summary']['structure_count']} structures, "
        f"{report['cache']['cached_count']} cached, "
        f"{report['cache']['missing_count']} missing cache"
    )
    pde = report["pde_coverage"]
    print(
        "PDE coverage: "
        f"{pde['present_expected_count']}/{pde['expected_count']} expected targets present"
    )
    if pde["missing_expected_genes"]:
        print(f"Missing PDE targets: {', '.join(pde['missing_expected_genes'])}")
    print(f"Structure sources: {report['structure_quality']['source_summary']}")
    print(f"Docking grades: {report['structure_quality']['docking_grade_summary']}")

    if report["issues"]:
        print("\nIssues:")
        for item in report["issues"]:
            print(f"- {item}")
    if report["warnings"]:
        print("\nWarnings:")
        for item in report["warnings"]:
            print(f"- {item}")


if __name__ == "__main__":
    raise SystemExit(main())
