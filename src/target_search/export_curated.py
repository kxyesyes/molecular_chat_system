"""Export curated target subsets from SQLite into rebuildable CSV seeds."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Optional

from .database import get_connection, get_target_db_dir, relative_to_project, resolve_project_root


PDE_WHERE = "upper(t.gene_symbol) LIKE 'PDE%' OR upper(t.protein_name) LIKE '%PHOSPHODIESTERASE%'"
COMMON_WHERE = f"NOT ({PDE_WHERE})"

TARGET_EXPORT_HEADERS = [
    "gene_symbol", "protein_name", "uniprot_id", "organism", "target_type",
    "description", "function_summary", "disease_keywords", "chembl_target_id",
    "aliases", "pathway", "known_drugs", "representative_ligands", "external_links",
]

STRUCTURE_EXPORT_HEADERS = [
    "gene_symbol", "structure_id", "source", "structure_type", "method", "resolution",
    "chain_ids", "ligand_ids", "organism", "title", "file_format", "local_file_path",
    "download_url", "docking_recommended", "is_preferred", "is_downloaded", "quality_note",
]


def export_pde_csvs(project_root: Optional[Path | str] = None) -> dict:
    """Write pde_targets.csv and pde_structures.csv from the current SQLite database."""
    return _export_csvs("pde", PDE_WHERE, project_root)


def export_common_csvs(project_root: Optional[Path | str] = None) -> dict:
    """Write common_targets.csv and common_structures.csv from non-PDE targets."""
    return _export_csvs("common", COMMON_WHERE, project_root)


def _export_csvs(prefix: str, where_clause: str, project_root: Optional[Path | str] = None) -> dict:
    root = resolve_project_root(project_root)
    db_dir = get_target_db_dir(root)
    db_dir.mkdir(parents=True, exist_ok=True)
    target_csv = db_dir / f"{prefix}_targets.csv"
    structure_csv = db_dir / f"{prefix}_structures.csv"

    conn = get_connection(root)
    try:
        target_rows = conn.execute(
            f"""
            SELECT
                t.id,
                t.gene_symbol,
                t.protein_name,
                t.uniprot_id,
                t.organism,
                t.target_type,
                t.description,
                t.function_summary,
                t.disease_keywords,
                t.chembl_target_id,
                t.pathway,
                t.known_drugs,
                t.representative_ligands,
                t.external_links
            FROM targets t
            WHERE {where_clause}
            ORDER BY t.gene_symbol
            """
        ).fetchall()
        structure_rows = conn.execute(
            f"""
            SELECT
                t.gene_symbol,
                s.structure_id,
                s.source,
                s.structure_type,
                s.method,
                s.resolution,
                s.chain_ids,
                s.ligand_ids,
                s.organism,
                s.title,
                s.file_format,
                s.local_file_path,
                s.download_url,
                s.docking_recommended,
                s.is_preferred,
                s.is_downloaded,
                s.quality_note
            FROM target_structures s
            JOIN targets t ON t.id = s.target_id
            WHERE {where_clause}
            ORDER BY t.gene_symbol, s.source, s.structure_id
            """
        ).fetchall()
        aliases = _aliases_by_target_id(conn, [row["id"] for row in target_rows])
    finally:
        conn.close()

    with target_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_EXPORT_HEADERS)
        writer.writeheader()
        for row in target_rows:
            writer.writerow({
                "gene_symbol": row["gene_symbol"],
                "protein_name": row["protein_name"] or "",
                "uniprot_id": row["uniprot_id"] or "",
                "organism": row["organism"] or "Homo sapiens",
                "target_type": row["target_type"] or "",
                "description": row["description"] or "",
                "function_summary": row["function_summary"] or "",
                "disease_keywords": row["disease_keywords"] or "",
                "chembl_target_id": row["chembl_target_id"] or "",
                "aliases": ";".join(aliases.get(row["id"], [])),
                "pathway": row["pathway"] or "",
                "known_drugs": row["known_drugs"] or "",
                "representative_ligands": row["representative_ligands"] or "",
                "external_links": row["external_links"] or "",
            })

    with structure_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=STRUCTURE_EXPORT_HEADERS)
        writer.writeheader()
        for row in structure_rows:
            writer.writerow({
                key: "" if row[key] is None else row[key]
                for key in STRUCTURE_EXPORT_HEADERS
            })

    return {
        "target_csv": relative_to_project(target_csv, root),
        "structure_csv": relative_to_project(structure_csv, root),
        "target_count": len(target_rows),
        "structure_count": len(structure_rows),
    }


def _aliases_by_target_id(conn, target_ids: list[int]) -> dict[int, list[str]]:
    if not target_ids:
        return {}
    placeholders = ",".join("?" for _ in target_ids)
    rows = conn.execute(
        f"""
        SELECT target_id, alias
        FROM target_aliases
        WHERE target_id IN ({placeholders})
        ORDER BY target_id, alias
        """,
        target_ids,
    ).fetchall()
    aliases: dict[int, list[str]] = {}
    for row in rows:
        aliases.setdefault(row["target_id"], []).append(row["alias"])
    return aliases


def main() -> None:
    parser = argparse.ArgumentParser(description="Export curated target CSVs from the local SQLite database.")
    parser.add_argument("--family", choices=["pde", "common"], default="pde")
    parser.add_argument("--project-root", default=None)
    args = parser.parse_args()
    if args.family == "pde":
        print(export_pde_csvs(args.project_root))
    if args.family == "common":
        print(export_common_csvs(args.project_root))


if __name__ == "__main__":
    main()
