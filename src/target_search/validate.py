"""Validation helpers for the curated local target database."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from .database import absolute_from_project, get_cache_dir, get_connection, get_db_path, relative_to_project
from .service import PDE_CATALYTIC_TARGETS, PDE_REGULATORY_TARGETS, TargetSearchService


EXPECTED_PDE_TARGETS = frozenset(PDE_CATALYTIC_TARGETS | PDE_REGULATORY_TARGETS)
REQUIRED_TABLES = ("targets", "target_aliases", "target_structures")


def validate_target_database(
    project_root: Optional[Path | str] = None,
    expected_pde_targets: Optional[Iterable[str]] = None,
    require_cache: bool = False,
) -> dict:
    """Return a structured validation report for the target-search database."""

    expected_pde = _normalize_gene_set(expected_pde_targets or EXPECTED_PDE_TARGETS)
    db_path = get_db_path(project_root)
    cache_dir = get_cache_dir(project_root)

    report = {
        "status": "ok",
        "database_exists": db_path.exists(),
        "cache_dir_exists": cache_dir.exists(),
        "database_file_path": relative_to_project(db_path, project_root),
        "cache_dir_path": relative_to_project(cache_dir, project_root),
        "tables": {table: False for table in REQUIRED_TABLES},
        "summary": {
            "target_count": 0,
            "structure_count": 0,
            "alias_count": 0,
            "targets_without_structures_count": 0,
        },
        "pde_coverage": {
            "expected_count": len(expected_pde),
            "present_expected_count": 0,
            "present_expected_genes": [],
            "missing_expected_genes": sorted(expected_pde),
            "extra_pde_like_genes": [],
        },
        "cache": {
            "cached_count": 0,
            "missing_count": 0,
            "coverage": 0.0,
            "missing_files": [],
        },
        "structure_quality": {
            "docking_grade_summary": {},
            "source_summary": {},
            "docking_recommended_count": 0,
            "preferred_count": 0,
        },
        "issues": [],
        "warnings": [],
    }

    if not db_path.exists():
        report["status"] = "error"
        report["issues"].append("target_database.sqlite 不存在，请先运行 python -m src.target_search.seed")
        return report

    conn = get_connection(project_root)
    try:
        existing_tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        report["tables"] = {table: table in existing_tables for table in REQUIRED_TABLES}
        missing_tables = [table for table, exists in report["tables"].items() if not exists]
        if missing_tables:
            report["issues"].append(f"缺少数据表：{', '.join(missing_tables)}")
            report["status"] = "error"
            return report

        targets = conn.execute("SELECT id, gene_symbol FROM targets ORDER BY gene_symbol").fetchall()
        structures = conn.execute(
            """
            SELECT s.*, t.gene_symbol
            FROM target_structures s
            JOIN targets t ON t.id = s.target_id
            ORDER BY t.gene_symbol, s.structure_id
            """
        ).fetchall()
        alias_count = conn.execute("SELECT COUNT(*) AS count FROM target_aliases").fetchone()["count"]
        targets_without_structures = conn.execute(
            """
            SELECT t.gene_symbol
            FROM targets t
            LEFT JOIN target_structures s ON s.target_id = t.id
            GROUP BY t.id, t.gene_symbol
            HAVING COUNT(s.id) = 0
            ORDER BY t.gene_symbol
            """
        ).fetchall()
    finally:
        conn.close()

    report["summary"] = {
        "target_count": len(targets),
        "structure_count": len(structures),
        "alias_count": int(alias_count or 0),
        "targets_without_structures_count": len(targets_without_structures),
    }
    if not targets:
        report["issues"].append("targets 表为空")
    if not structures:
        report["issues"].append("target_structures 表为空")
    if targets_without_structures:
        report["warnings"].append(
            f"{len(targets_without_structures)} 个靶点没有结构记录"
        )
        report["targets_without_structures"] = [row["gene_symbol"] for row in targets_without_structures[:50]]

    present_pde_like = {row["gene_symbol"].upper() for row in targets if row["gene_symbol"].upper().startswith("PDE")}
    present_expected = sorted(present_pde_like & expected_pde)
    missing_expected = sorted(expected_pde - present_pde_like)
    report["pde_coverage"] = {
        "expected_count": len(expected_pde),
        "present_expected_count": len(present_expected),
        "present_expected_genes": present_expected,
        "missing_expected_genes": missing_expected,
        "extra_pde_like_genes": sorted(present_pde_like - expected_pde),
    }
    if missing_expected:
        report["warnings"].append(f"PDE 预期靶点缺失：{', '.join(missing_expected)}")

    cached_count = 0
    missing_files = []
    for row in structures:
        local_path = row.get("local_file_path") or ""
        if local_path and absolute_from_project(local_path, project_root).exists():
            cached_count += 1
        else:
            missing_files.append(
                {
                    "structure_db_id": row["id"],
                    "gene_symbol": row["gene_symbol"],
                    "structure_id": row["structure_id"],
                    "source": row["source"],
                    "local_file_path": local_path,
                }
            )
    missing_count = len(missing_files)
    report["cache"] = {
        "cached_count": cached_count,
        "missing_count": missing_count,
        "coverage": round(cached_count / len(structures), 4) if structures else 0.0,
        "missing_files": missing_files[:100],
    }
    if missing_count:
        message = f"{missing_count} 个结构文件尚未缓存到本地"
        if require_cache:
            report["issues"].append(message)
        else:
            report["warnings"].append(message)

    quality = _summarize_structure_quality(structures, project_root)
    report["structure_quality"] = quality

    if report["issues"]:
        report["status"] = "error"
    elif report["warnings"]:
        report["status"] = "warning"
    else:
        report["status"] = "ok"

    return report


def _summarize_structure_quality(structures: list[dict], project_root: Optional[Path | str]) -> dict:
    service = TargetSearchService(project_root=project_root)
    grade_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    docking_recommended_count = 0
    preferred_count = 0

    for row in structures:
        formatted = service._format_structure(row)
        grade_counts[formatted["docking_grade"]] += 1
        source_counts[formatted["source"]] += 1
        if formatted["docking_recommended"]:
            docking_recommended_count += 1
        if formatted["is_preferred"]:
            preferred_count += 1

    return {
        "docking_grade_summary": dict(sorted(grade_counts.items())),
        "source_summary": dict(sorted(source_counts.items())),
        "docking_recommended_count": docking_recommended_count,
        "preferred_count": preferred_count,
    }


def _normalize_gene_set(values: Iterable[str]) -> set[str]:
    return {str(value).strip().upper() for value in values if str(value).strip()}
