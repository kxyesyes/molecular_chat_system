#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent wrapper for target database search."""

from typing import Any, Dict
import logging

from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class TargetDatabaseTool(BaseMolecularTool):
    """Search local target records by gene, protein name, UniProt ID, or upstream target records."""

    def __init__(self):
        super().__init__(
            name="target_database_search",
            description=(
                "Search the local target database by gene symbol, protein name, "
                "UniProt ID, disease keyword, or reverse-target evidence records."
            ),
        )
        self._service = None

    def _get_service(self):
        """Lazy-load TargetSearchService."""
        if self._service is None:
            try:
                from src.target_search.service import TargetSearchService

                self._service = TargetSearchService()
                logger.info("TargetSearchService loaded")
            except Exception as e:
                logger.error(f"Failed to load TargetSearchService: {e}")
                raise
        return self._service

    def should_use(self, query: str) -> bool:
        query_lower = query.lower()
        keywords = [
            "pde",
            "egfr",
            "kras",
            "braf",
            "uniprot",
            "alphafold",
            "pdb",
            "structure",
            "protein",
            "target database",
            "target search",
            "docking",
            "结构",
            "蛋白",
            "基因",
            "靶点",
            "晶体结构",
            "靶点数据库",
        ]
        return any(keyword in query_lower for keyword in keywords)

    def execute(self, query: Any) -> Dict[str, Any]:
        result = self._create_base_result(query)
        search_queries = self._search_queries(query)
        if not search_queries:
            result["message"] = "No target search keyword was found."
            result["reasoning"] = (
                "Target database search requires a target name, gene symbol, "
                "UniProt ID, or reverse-target evidence records."
            )
            return result

        logger.info("Target database search queries: %s", search_queries)

        try:
            service = self._get_service()
            targets: list[Any] = []
            searched: list[str] = []
            for search_query in search_queries:
                search_result = service.search_targets(search_query)
                searched.append(search_query)
                for target in search_result.get("results", []):
                    if isinstance(target, dict):
                        target = {**target, "source_query": search_query}
                        target_id = target.get("target_id", target.get("id"))
                        recommended_structures: list[dict[str, Any]] = []
                        if target_id not in (None, ""):
                            try:
                                structure_payload = service.get_target_structures(
                                    int(target_id)
                                )
                                structures = structure_payload.get("structures", [])
                                recommended_structures = [
                                    dict(structure)
                                    for structure in structures[:3]
                                    if isinstance(structure, dict)
                                ]
                            except Exception as exc:
                                result.setdefault("warnings", []).append(
                                    "Could not load structures for target "
                                    f"{target_id}: {exc}"
                                )
                        target["recommended_structures"] = recommended_structures
                    targets.append(target)

            if not targets:
                result["success"] = True
                result["message"] = "No matching targets found"
                result["formatted"] = (
                    "## Target database search results\n\n"
                    f"Query keywords: `{', '.join(searched)}`\n\n"
                    "No matching target records were found in the database."
                )
                return result

            lines = [
                "## Target database search results",
                "",
                f"Query keywords: `{', '.join(searched)}`",
                f"Matched targets: {len(targets)}",
                "",
            ]

            for index, target in enumerate(targets, 1):
                if not isinstance(target, dict):
                    lines.append(f"### {index}. {target}")
                    lines.append("")
                    continue
                gene = target.get("gene_symbol", "N/A")
                protein = target.get("protein_name", "N/A")
                uniprot = target.get("uniprot_id", "N/A")
                organism = target.get("organism", "N/A")
                struct_count = target.get("structure_count", 0)
                has_exp = "yes" if target.get("has_experimental_structure") else "no"
                has_af = "yes" if target.get("has_alphafold_structure") else "no"
                match_reason = target.get("match_reason", "")
                recommended_structures = target.get("recommended_structures", [])

                lines.append(f"### {index}. {gene} ({protein})")
                lines.append(f"- UniProt: {uniprot}")
                lines.append(f"- Organism: {organism}")
                lines.append(f"- Structure count: {struct_count}")
                lines.append(
                    f"- Experimental structure: {has_exp} | AlphaFold: {has_af}"
                )
                lines.append(f"- Match source: {match_reason}")
                if recommended_structures:
                    lines.append("- Recommended structures:")
                    for structure in recommended_structures:
                        structure_id = structure.get("structure_id", "N/A")
                        source = structure.get("source", "N/A")
                        recommendation = structure.get(
                            "recommendation_level", "unrated"
                        )
                        lines.append(
                            f"  - {structure_id} ({source}; {recommendation})"
                        )
                else:
                    lines.append("- Recommended structures: none recorded")
                lines.append("")

            result["success"] = True
            result["data"] = targets
            result["formatted"] = "\n".join(lines)
            result["message"] = f"Found {len(targets)} matching targets"

        except Exception as e:
            logger.error(f"Target database search failed: {e}", exc_info=True)
            result["message"] = f"Search failed: {str(e)}"

        return result

    def _search_queries(self, query: Any) -> list[str]:
        queries: list[str] = []

        def add(value: Any) -> None:
            if value is None:
                return
            text = str(value).strip()
            if text and text not in queries:
                queries.append(text)

        def collect(value: Any) -> None:
            if isinstance(value, str):
                add(value)
                return
            if isinstance(value, dict):
                for key in (
                    "gene_symbol",
                    "target_gene",
                    "uniprot_id",
                    "target_name",
                    "protein_name",
                ):
                    if value.get(key):
                        add(value[key])
                        return
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)

        collect(query)
        return queries[:5]
