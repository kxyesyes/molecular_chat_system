"""Service layer for the local target-search demo."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .database import (
    absolute_from_project,
    get_cache_dir,
    get_connection,
    get_db_path,
    init_db,
    relative_to_project,
    resolve_project_root,
)
from .downloader import StructureDownloader
from .schemas import as_bool, split_list
from .scoring import calculate_structure_score


PDE_CATALYTIC_TARGETS = {
    "PDE1A", "PDE1B", "PDE1C", "PDE2A", "PDE3A", "PDE3B", "PDE4A", "PDE4B", "PDE4C", "PDE4D",
    "PDE5A", "PDE6A", "PDE6B", "PDE6C", "PDE7A", "PDE7B", "PDE8A", "PDE8B", "PDE9A", "PDE10A", "PDE11A",
}
PDE_REGULATORY_TARGETS = {"PDE6D", "PDE6G", "PDE6H"}


class TargetSearchService:
    def __init__(self, project_root: Optional[Path | str] = None):
        self.project_root = resolve_project_root(project_root)
        init_db(self.project_root)
        self.downloader = StructureDownloader(self.project_root)

    def search_targets(
        self,
        query: str,
        target_type: Optional[str] = None,
        source: Optional[str] = None,
        has_experimental: Optional[bool] = None,
        docking_recommended: Optional[bool] = None,
        has_ligand: Optional[bool] = None,
    ) -> dict:
        query = (query or "").strip()
        if not query:
            return {"query": query, "results": []}

        like = f"%{query.lower()}%"
        filter_clause, filter_params = self._filter_clause(
            target_type=target_type,
            source=source,
            has_experimental=has_experimental,
            docking_recommended=docking_recommended,
            has_ligand=has_ligand,
        )
        exact_where = """
            (
                lower(t.gene_symbol) = lower(?)
                OR lower(t.uniprot_id) = lower(?)
                OR lower(a.alias) = lower(?)
            )
        """
        exact_sql = self._search_sql(exact_where + filter_clause)
        conn = get_connection(self.project_root)
        try:
            exact_rows = conn.execute(
                exact_sql,
                (query, query, query, like, like, query, query, query, *filter_params, query, query, query, like),
            ).fetchall()
        finally:
            conn.close()

        if exact_rows:
            return {"query": query, "results": [self._format_search_row(row, query) for row in exact_rows]}

        fuzzy_where = """
            (
                lower(t.gene_symbol) LIKE ?
                OR lower(t.protein_name) LIKE ?
                OR lower(t.uniprot_id) LIKE ?
                OR lower(a.alias) LIKE ?
                OR lower(t.disease_keywords) LIKE ?
            )
        """
        sql = self._search_sql(fuzzy_where + filter_clause)
        conn = get_connection(self.project_root)
        try:
            rows = conn.execute(
                sql,
                (query, query, query, like, like, like, like, like, like, like, *filter_params, query, query, query, like),
            ).fetchall()
        finally:
            conn.close()

        rows = [row for row in rows if self._is_relevant_fuzzy_row(row, query)]
        return {"query": query, "results": [self._format_search_row(row, query) for row in rows]}

    def _search_sql(self, where_clause: str) -> str:
        return f"""
            SELECT DISTINCT
                t.id AS target_id,
                t.gene_symbol,
                t.protein_name,
                t.uniprot_id,
                t.organism,
                t.target_type,
                t.disease_keywords,
                (
                    SELECT COUNT(*) FROM target_structures s WHERE s.target_id = t.id
                ) AS structure_count,
                (
                    SELECT COUNT(*) FROM target_structures s WHERE s.target_id = t.id AND s.is_downloaded = 1
                ) AS downloaded_structure_count,
                EXISTS(
                    SELECT 1 FROM target_structures s WHERE s.target_id = t.id AND s.structure_type = 'experimental'
                ) AS has_experimental_structure,
                EXISTS(
                    SELECT 1 FROM target_structures s WHERE s.target_id = t.id AND s.source = 'AlphaFold'
                ) AS has_alphafold_structure,
                CASE
                    WHEN lower(t.gene_symbol) = lower(?) THEN 'gene_symbol'
                    WHEN lower(t.uniprot_id) = lower(?) THEN 'uniprot_id'
                    WHEN lower(a.alias) = lower(?) THEN 'alias'
                    WHEN lower(t.disease_keywords) LIKE ? THEN 'disease'
                    WHEN lower(t.protein_name) LIKE ? THEN 'protein_name'
                    ELSE 'keyword'
                END AS match_reason,
                (
                    SELECT GROUP_CONCAT(aa.alias, ';')
                    FROM target_aliases aa
                    WHERE aa.target_id = t.id
                ) AS aliases
            FROM targets t
            LEFT JOIN target_aliases a ON a.target_id = t.id
            WHERE {where_clause}
            ORDER BY
                CASE
                    WHEN lower(t.gene_symbol) = lower(?) THEN 0
                    WHEN lower(a.alias) = lower(?) THEN 1
                    WHEN lower(t.uniprot_id) = lower(?) THEN 2
                    WHEN lower(t.disease_keywords) LIKE ? THEN 3
                    ELSE 4
                END,
                t.gene_symbol
        """

    def _filter_clause(
        self,
        target_type: Optional[str] = None,
        source: Optional[str] = None,
        has_experimental: Optional[bool] = None,
        docking_recommended: Optional[bool] = None,
        has_ligand: Optional[bool] = None,
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if target_type:
            clauses.append("lower(t.target_type) = lower(?)")
            params.append(target_type)
        if source:
            clauses.append("EXISTS(SELECT 1 FROM target_structures fs WHERE fs.target_id = t.id AND lower(fs.source) = lower(?))")
            params.append(source)
        if has_experimental is not None:
            operator = "EXISTS" if has_experimental else "NOT EXISTS"
            clauses.append(f"{operator}(SELECT 1 FROM target_structures fs WHERE fs.target_id = t.id AND fs.structure_type = 'experimental')")
        if docking_recommended is not None:
            operator = "EXISTS" if docking_recommended else "NOT EXISTS"
            clauses.append(f"{operator}(SELECT 1 FROM target_structures fs WHERE fs.target_id = t.id AND fs.docking_recommended = 1)")
        if has_ligand is not None:
            operator = "EXISTS" if has_ligand else "NOT EXISTS"
            clauses.append(f"{operator}(SELECT 1 FROM target_structures fs WHERE fs.target_id = t.id AND COALESCE(fs.ligand_ids, '') != '')")
        if not clauses:
            return "", []
        return " AND " + " AND ".join(clauses), params

    def _format_search_row(self, row: dict, query: str) -> dict:
        query_lower = query.lower()
        match_reason = row.get("match_reason") or "keyword"
        if match_reason == "keyword":
            if query_lower in str(row.get("disease_keywords") or "").lower():
                match_reason = "disease"
            elif query_lower in str(row.get("protein_name") or "").lower():
                match_reason = "protein_name"
        return {
            **row,
            "has_experimental_structure": as_bool(row["has_experimental_structure"]),
            "has_alphafold_structure": as_bool(row["has_alphafold_structure"]),
            "match_reason": match_reason,
        }

    def _is_relevant_fuzzy_row(self, row: dict, query: str) -> bool:
        """Avoid accidental substring hits for very short biomedical abbreviations."""
        query_lower = query.lower().strip()
        if len(query_lower) > 3:
            return True

        aliases = split_list(row.get("aliases"))
        disease_terms = split_list(row.get("disease_keywords"))
        gene = str(row.get("gene_symbol") or "").lower()
        uniprot = str(row.get("uniprot_id") or "").lower()
        protein_tokens = self._tokenize_text(row.get("protein_name"))

        if gene.startswith(query_lower) or uniprot.startswith(query_lower):
            return True
        if any(alias.lower().startswith(query_lower) for alias in aliases):
            return True
        if any(term.lower() == query_lower or term.lower().startswith(f"{query_lower} ") for term in disease_terms):
            return True
        if query_lower in protein_tokens:
            return True
        return False

    def _tokenize_text(self, value: Optional[str]) -> set[str]:
        text = str(value or "").lower()
        for char in "(),;:/[]{}'\"+-":
            text = text.replace(char, " ")
        return {token for token in text.split() if token}

    def get_target_detail(self, target_id: int) -> dict:
        target = self._get_target(target_id)
        structures = self.get_target_structures(target_id)["structures"]
        return {
            "target_id": target["id"],
            "gene_symbol": target["gene_symbol"],
            "protein_name": target["protein_name"],
            "uniprot_id": target["uniprot_id"],
            "organism": target["organism"],
            "target_type": target["target_type"],
            "description": target["description"],
            "function_summary": target["function_summary"],
            "pathway": target.get("pathway"),
            "known_drugs": split_list(target.get("known_drugs")),
            "representative_ligands": split_list(target.get("representative_ligands")),
            "external_links": self._parse_external_links(target.get("external_links")),
            "disease_keywords": split_list(target["disease_keywords"]),
            "chembl_target_id": target["chembl_target_id"],
            "structures": structures,
        }

    def get_target_structures(self, target_id: int) -> dict:
        target = self._get_target(target_id)
        conn = get_connection(self.project_root)
        try:
            rows = conn.execute(
                "SELECT * FROM target_structures WHERE target_id = ?",
                (target_id,),
            ).fetchall()
        finally:
            conn.close()

        structures = [self._format_structure(row) for row in rows]
        structures.sort(key=lambda item: item["score"], reverse=True)
        return {
            "target_id": target_id,
            "gene_symbol": target["gene_symbol"],
            "structures": structures,
        }

    def get_pde_overview(self, top_structures_per_target: int = 3) -> dict:
        """Return a family-level PDE overview for the target-search page."""
        conn = get_connection(self.project_root)
        try:
            targets = conn.execute(
                """
                SELECT t.*
                FROM targets t
                WHERE upper(t.gene_symbol) LIKE 'PDE%'
                   OR upper(t.protein_name) LIKE '%PHOSPHODIESTERASE%'
                ORDER BY t.gene_symbol
                """
            ).fetchall()
        finally:
            conn.close()

        items = []
        families: dict[str, dict] = {}
        grade_counts = {"A": 0, "B": 0, "C": 0}
        for target in targets:
            structures_payload = self.get_target_structures(target["id"])
            structures = structures_payload["structures"]
            top_structures = structures[:max(top_structures_per_target, 0)]
            best = structures[0] if structures else None
            target_class = self._pde_target_class(target["gene_symbol"])
            family = self._pde_family(target["gene_symbol"])
            if best:
                grade_counts[best["docking_grade"]] = grade_counts.get(best["docking_grade"], 0) + 1
            family_bucket = families.setdefault(
                family,
                {
                    "family": family,
                    "target_count": 0,
                    "structure_count": 0,
                    "experimental_structure_count": 0,
                    "alphafold_structure_count": 0,
                    "top_grade_count": {"A": 0, "B": 0, "C": 0},
                },
            )
            family_bucket["target_count"] += 1
            family_bucket["structure_count"] += len(structures)
            family_bucket["experimental_structure_count"] += sum(1 for item in structures if item["structure_type"] == "experimental")
            family_bucket["alphafold_structure_count"] += sum(1 for item in structures if item["source"] == "AlphaFold")
            if best:
                family_bucket["top_grade_count"][best["docking_grade"]] += 1

            items.append(
                {
                    "target_id": target["id"],
                    "gene_symbol": target["gene_symbol"],
                    "protein_name": target["protein_name"],
                    "uniprot_id": target["uniprot_id"],
                    "target_type": target["target_type"],
                    "pde_family": family,
                    "pde_target_class": target_class,
                    "structure_count": len(structures),
                    "experimental_structure_count": sum(1 for item in structures if item["structure_type"] == "experimental"),
                    "alphafold_structure_count": sum(1 for item in structures if item["source"] == "AlphaFold"),
                    "best_structure": best,
                    "top_structures": top_structures,
                }
            )

        return {
            "family": "PDE",
            "target_count": len(items),
            "structure_count": sum(item["structure_count"] for item in items),
            "docking_grade_summary": grade_counts,
            "families": sorted(families.values(), key=lambda item: item["family"]),
            "targets": items,
        }

    def prepare_structure_file(self, structure_db_id: int, requested_format: Optional[str] = None) -> dict:
        structure = self._get_structure(structure_db_id)
        return self.downloader.prepare_structure_file(structure, requested_format)

    def preflight_structure(self, structure_db_id: int, requested_format: Optional[str] = None) -> dict:
        """Check structure cache and docking suitability without downloading files."""
        row = self._get_structure(structure_db_id)
        structure = self._format_structure(row)
        file_format = (requested_format or structure.get("file_format") or "cif").lower()
        local_file_path = self.downloader._local_path_for_format(row, file_format)
        absolute_path = absolute_from_project(local_file_path, self.project_root)
        file_exists = absolute_path.exists()
        file_size = absolute_path.stat().st_size if file_exists else None
        source = str(structure.get("source") or "")
        docking_grade = structure["docking_grade"]
        warnings = list(structure.get("warnings") or [])

        if not file_exists:
            warnings.append("本地缓存文件不存在，下载时将尝试远程获取并写入缓存。")
        if source == "AlphaFold":
            warnings.append("AlphaFold 预测结构仅建议作为参考，不建议作为 docking 首选。")
        if docking_grade == "C":
            warnings.append("C 级结构不建议直接作为首选 docking 输入。")

        can_download = file_exists or source in {"RCSB_PDB", "AlphaFold"} or bool(structure.get("download_url"))
        suitable_for_direct_docking = docking_grade in {"A", "B"} and source != "AlphaFold"
        needs_pdbqt_conversion = file_format not in {"pdbqt"}

        return {
            "structure_db_id": structure["id"],
            "structure_id": structure["structure_id"],
            "source": source,
            "structure_type": structure["structure_type"],
            "file_format": file_format,
            "local_file_path": relative_to_project(absolute_path, self.project_root),
            "local_file_exists": file_exists,
            "cache_status": "available" if file_exists else "missing",
            "file_size_bytes": file_size,
            "download_url": structure.get("download_url"),
            "can_download": can_download,
            "docking_recommended": structure["docking_recommended"],
            "docking_grade": docking_grade,
            "docking_grade_label": structure["docking_grade_label"],
            "recommendation_level": structure["recommendation_level"],
            "score": structure["score"],
            "has_ligand": bool(structure["ligand_ids"]),
            "ligand_ids": structure["ligand_ids"],
            "chain_ids": structure["chain_ids"],
            "needs_pdbqt_conversion": needs_pdbqt_conversion,
            "suitable_for_direct_docking": suitable_for_direct_docking,
            "warnings": list(dict.fromkeys(warnings)),
            "recommendation_reasons": structure.get("recommendation_reasons") or [],
        }

    def send_to_docking(self, structure_db_id: int) -> dict:
        prepared = self.prepare_structure_file(structure_db_id, requested_format=None)
        return {
            "status": "success",
            "message": "结构文件已准备好，后续可接入分子对接模块",
            "protein_file": prepared["local_file_path"],
        }

    def get_database_stats(self) -> dict:
        db_path = get_db_path(self.project_root)
        cache_dir = get_cache_dir(self.project_root)
        conn = get_connection(self.project_root)
        try:
            target_count = conn.execute("SELECT COUNT(*) AS count FROM targets").fetchone()["count"]
            structure_count = conn.execute("SELECT COUNT(*) AS count FROM target_structures").fetchone()["count"]
            docking_count = conn.execute(
                "SELECT COUNT(*) AS count FROM target_structures WHERE docking_recommended = 1"
            ).fetchone()["count"]
            source_counts = {
                row["source"]: row["count"]
                for row in conn.execute(
                    "SELECT source, COUNT(*) AS count FROM target_structures GROUP BY source ORDER BY source"
                ).fetchall()
            }
            structures = conn.execute("SELECT local_file_path FROM target_structures").fetchall()
            pde_stats = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT t.id) AS target_count,
                    COUNT(s.id) AS structure_count
                FROM targets t
                LEFT JOIN target_structures s ON s.target_id = t.id
                WHERE upper(t.gene_symbol) LIKE 'PDE%'
                   OR upper(t.protein_name) LIKE '%PHOSPHODIESTERASE%'
                """
            ).fetchone()
            pde_structures = conn.execute(
                """
                SELECT s.local_file_path
                FROM target_structures s
                JOIN targets t ON t.id = s.target_id
                WHERE upper(t.gene_symbol) LIKE 'PDE%'
                   OR upper(t.protein_name) LIKE '%PHOSPHODIESTERASE%'
                """
            ).fetchall()
            alphafold_structures = conn.execute(
                "SELECT local_file_path FROM target_structures WHERE source = 'AlphaFold'"
            ).fetchall()
        finally:
            conn.close()

        cached_count = sum(
            1
            for row in structures
            if row.get("local_file_path")
            and absolute_from_project(row["local_file_path"], self.project_root).exists()
        )
        pde_cached_count = sum(
            1
            for row in pde_structures
            if row.get("local_file_path")
            and absolute_from_project(row["local_file_path"], self.project_root).exists()
        )
        alphafold_cached_count = sum(
            1
            for row in alphafold_structures
            if row.get("local_file_path")
            and absolute_from_project(row["local_file_path"], self.project_root).exists()
        )
        pde_structure_count = int(pde_stats["structure_count"] or 0)
        return {
            "database_exists": db_path.exists(),
            "cache_dir_exists": cache_dir.exists(),
            "database_file_path": relative_to_project(db_path, self.project_root),
            "cache_dir_path": relative_to_project(cache_dir, self.project_root),
            "target_count": target_count,
            "structure_count": structure_count,
            "cached_structure_count": cached_count,
            "missing_cache_count": max(structure_count - cached_count, 0),
            "source_counts": source_counts,
            "docking_recommended_count": docking_count,
            "pde_target_count": int(pde_stats["target_count"] or 0),
            "pde_structure_count": pde_structure_count,
            "pde_cached_structure_count": pde_cached_count,
            "pde_cache_coverage": round(pde_cached_count / pde_structure_count, 4) if pde_structure_count else 0.0,
            "alphafold_structure_count": len(alphafold_structures),
            "alphafold_cached_structure_count": alphafold_cached_count,
        }

    def get_database_health(self) -> dict:
        db_path = get_db_path(self.project_root)
        cache_dir = get_cache_dir(self.project_root)
        expected_tables = ["targets", "target_aliases", "target_structures"]
        notes = [
            "AlphaFold structures use local cache first and can fall back to online AlphaFold DB downloads."
        ]

        if not db_path.exists():
            return {
                "status": "error",
                "database_exists": False,
                "cache_dir_exists": cache_dir.exists(),
                "database_file_path": relative_to_project(db_path, self.project_root),
                "cache_dir_path": relative_to_project(cache_dir, self.project_root),
                "tables": {name: False for name in expected_tables},
                "target_count": 0,
                "structure_count": 0,
                "cached_structure_count": 0,
                "missing_cache_count": 0,
                "missing_cache_files": [],
                "notes": notes,
            }

        conn = get_connection(self.project_root)
        try:
            existing_tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            table_state = {name: name in existing_tables for name in expected_tables}
            target_count = (
                conn.execute("SELECT COUNT(*) AS count FROM targets").fetchone()["count"]
                if table_state["targets"]
                else 0
            )
            structure_count = (
                conn.execute("SELECT COUNT(*) AS count FROM target_structures").fetchone()["count"]
                if table_state["target_structures"]
                else 0
            )
            structures = (
                conn.execute(
                    """
                    SELECT s.id, s.structure_id, s.source, s.local_file_path, t.gene_symbol
                    FROM target_structures s
                    JOIN targets t ON t.id = s.target_id
                    ORDER BY t.gene_symbol, s.structure_id
                    """
                ).fetchall()
                if table_state["target_structures"] and table_state["targets"]
                else []
            )
        finally:
            conn.close()

        cached_count = 0
        missing_files = []
        for row in structures:
            local_path = row.get("local_file_path") or ""
            if absolute_from_project(local_path, self.project_root).exists():
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

        status = "ok"
        if not all(table_state.values()) or target_count == 0 or structure_count == 0:
            status = "error"
        elif missing_files:
            status = "warning"

        return {
            "status": status,
            "database_exists": db_path.exists(),
            "cache_dir_exists": cache_dir.exists(),
            "database_file_path": relative_to_project(db_path, self.project_root),
            "cache_dir_path": relative_to_project(cache_dir, self.project_root),
            "tables": table_state,
            "target_count": target_count,
            "structure_count": structure_count,
            "cached_structure_count": cached_count,
            "missing_cache_count": len(missing_files),
            "missing_cache_files": missing_files[:50],
            "notes": notes,
        }

    def _get_target(self, target_id: int) -> dict:
        conn = get_connection(self.project_root)
        try:
            row = conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            raise ValueError(f"Target not found: {target_id}")
        return row

    def _get_structure(self, structure_db_id: int) -> dict:
        conn = get_connection(self.project_root)
        try:
            row = conn.execute("SELECT * FROM target_structures WHERE id = ?", (structure_db_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            raise ValueError(f"Structure not found: {structure_db_id}")
        return row

    def _format_structure(self, row: dict) -> dict:
        absolute = absolute_from_project(row.get("local_file_path") or "", self.project_root)
        is_downloaded = as_bool(row["is_downloaded"]) or absolute.exists()
        payload = {
            "id": row["id"],
            "structure_id": row["structure_id"],
            "source": row["source"],
            "structure_type": row["structure_type"],
            "method": row["method"],
            "resolution": row["resolution"],
            "chain_ids": split_list(row["chain_ids"]),
            "ligand_ids": split_list(row["ligand_ids"]),
            "organism": row["organism"],
            "title": row["title"],
            "file_format": row["file_format"],
            "local_file_path": row["local_file_path"],
            "download_url": row["download_url"],
            "is_downloaded": is_downloaded,
            "docking_recommended": as_bool(row["docking_recommended"]),
            "is_preferred": as_bool(row["is_preferred"]),
            "quality_note": row["quality_note"],
        }
        payload["score"] = calculate_structure_score({**row, **payload})
        payload["recommendation_reasons"] = self._structure_recommendation_reasons(payload)
        payload["warnings"] = self._structure_warnings(payload)
        payload["recommendation_level"] = self._recommendation_level(payload)
        payload["docking_grade"] = self._docking_grade(payload)
        payload["docking_grade_label"] = self._docking_grade_label(payload["docking_grade"])
        return payload

    def _parse_external_links(self, value: Optional[str]) -> list[dict[str, str]]:
        links = []
        for item in split_list(value):
            if "|" not in item:
                continue
            label, url = item.split("|", 1)
            links.append({"label": label, "url": url})
        return links

    def _structure_recommendation_reasons(self, structure: dict) -> list[str]:
        reasons = []
        if structure["structure_type"] == "experimental":
            reasons.append("实验结构")
        if structure["source"] == "AlphaFold" or structure["structure_type"] == "predicted":
            reasons.append("预测结构")
        if structure["resolution"] is not None and float(structure["resolution"]) <= 2.5:
            reasons.append("分辨率 <= 2.5 A")
        if structure["ligand_ids"]:
            reasons.append("含配体")
        if str(structure["organism"]).lower() == "homo sapiens":
            reasons.append("人源")
        if structure["docking_recommended"]:
            reasons.append("推荐 docking")
        return reasons or ["Demo 结构索引"]

    def _structure_warnings(self, structure: dict) -> list[str]:
        warnings = []
        if structure["source"] == "AlphaFold":
            warnings.append("AlphaFold 预测结构不优先用于 docking")
        if not structure["ligand_ids"] and structure["structure_type"] == "experimental":
            warnings.append("未记录共晶配体")
        return warnings

    def _recommendation_level(self, structure: dict) -> str:
        if structure["score"] >= 80:
            return "High"
        if structure["score"] >= 50:
            return "Medium"
        return "Reference only"

    def _docking_grade(self, structure: dict) -> str:
        """A/B/C display grade for practical docking triage."""
        if structure["source"] == "AlphaFold" or structure["structure_type"] == "predicted":
            return "C"

        has_good_resolution = False
        try:
            has_good_resolution = structure["resolution"] is not None and float(structure["resolution"]) <= 2.5
        except (TypeError, ValueError):
            has_good_resolution = False

        if (
            structure["structure_type"] == "experimental"
            and str(structure["organism"]).lower() == "homo sapiens"
            and has_good_resolution
            and bool(structure["ligand_ids"])
            and structure["docking_recommended"]
        ):
            return "A"
        if structure["structure_type"] == "experimental":
            return "B"
        return "C"

    def _docking_grade_label(self, grade: str) -> str:
        return {
            "A": "A 级：优先 docking",
            "B": "B 级：可参考筛选",
            "C": "C 级：参考结构",
        }.get(grade, "参考结构")

    def _pde_family(self, gene_symbol: str) -> str:
        gene = gene_symbol.upper()
        for number in ("10", "11", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
            if gene.startswith(f"PDE{number}"):
                return f"PDE{number}"
        return "PDE"

    def _pde_target_class(self, gene_symbol: str) -> str:
        gene = gene_symbol.upper()
        if gene in PDE_REGULATORY_TARGETS:
            return "regulatory/accessory PDE-related protein"
        if gene in PDE_CATALYTIC_TARGETS:
            return "catalytic PDE enzyme"
        return "PDE family target"
