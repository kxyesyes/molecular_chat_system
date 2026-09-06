#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent wrapper for target database search."""

from typing import Any, Dict
import logging
import threading

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
        self._service_lock = threading.RLock()

    def _get_service(self):
        """Lazy-load TargetSearchService."""
        with self._service_lock:
            if self._service is None:
                try:
                    from src.target_search.service import TargetSearchService

                    self._service = TargetSearchService()
                    logger.info("TargetSearchService loaded")
                except Exception:
                    logger.error("Failed to load TargetSearchService")
                    raise
            return self._service

    def close(self) -> None:
        with self._service_lock:
            service = self._service
            self._service = None
        if service is not None:
            close = getattr(service, "close", None)
            if callable(close):
                close()

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
            lookup_path: list[str] = []
            evidence: list[dict[str, Any]] = []
            service_statuses: list[str] = []
            service_retryabilities: list[bool] = []
            result["warnings"] = []
            for search_query in search_queries:
                search_result = service.search_targets(search_query)
                searched.append(search_query)
                service_results = search_result.get("results", [])
                service_status = search_result.get("status")
                if not isinstance(service_status, str):
                    service_status = "resolved" if service_results else "not_found"
                service_statuses.append(service_status)
                if isinstance(search_result.get("retryable"), bool):
                    service_retryabilities.append(search_result["retryable"])
                for service_evidence in search_result.get("evidence", []):
                    normalized_evidence = self._service_evidence(service_evidence)
                    if (
                        normalized_evidence is not None
                        and normalized_evidence not in evidence
                    ):
                        evidence.append(normalized_evidence)
                for warning in search_result.get("warnings", []):
                    if isinstance(warning, str) and warning not in result.setdefault(
                        "warnings", []
                    ):
                        result["warnings"].append(warning)
                for step in search_result.get("lookup_path", []):
                    if isinstance(step, str) and step not in lookup_path:
                        lookup_path.append(step)
                for target in service_results:
                    if isinstance(target, dict):
                        target = {**target, "source_query": search_query}
                        target_id = target.get("target_id", target.get("id"))
                        recommended_structures = [
                            dict(structure)
                            for structure in target.get("recommended_structures", [])
                            if isinstance(structure, dict)
                        ]
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
                            except Exception:
                                result.setdefault("warnings", []).append(
                                    "Could not load structures for target "
                                    f"{target_id}."
                                )
                        target["recommended_structures"] = recommended_structures
                        for provenance in self._target_evidence(target):
                            if provenance not in evidence:
                                evidence.append(provenance)
                    targets.append(target)

            result["lookup_path"] = lookup_path
            result["evidence"] = evidence
            result["quality"] = {
                "status": "complete",
                "service_statuses": service_statuses,
            }

            if not targets:
                if any(status == "ambiguous" for status in service_statuses):
                    result["status"] = "ambiguous"
                    result["quality"]["status"] = "partial"
                    result["message"] = "Target search requires clarification."
                    result["error"] = {
                        "code": "validation_error",
                        "message": result["message"],
                    }
                    result["formatted"] = (
                        "## Target database search results\n\n"
                        f"Query keywords: `{', '.join(searched)}`\n\n"
                        "The authoritative lookup was ambiguous. Please provide a "
                        "gene symbol, UniProt accession, or organism to clarify the target."
                    )
                    return result
                if any(
                    status in {"unavailable", "partial"}
                    for status in service_statuses
                ):
                    result["status"] = "unavailable"
                    result["quality"]["status"] = "partial"
                    result["quality"]["retryable"] = (
                        any(service_retryabilities)
                        if service_retryabilities
                        else True
                    )
                    result["message"] = "Target search is temporarily unavailable."
                    result["error"] = {
                        "code": "provider_error",
                        "message": result["message"],
                    }
                    result["formatted"] = (
                        "## Target database search results\n\n"
                        f"Query keywords: `{', '.join(searched)}`\n\n"
                        "The authoritative lookup could not be completed because a "
                        "provider is unavailable. This is not a conclusive zero-match result."
                    )
                    return result
                result["success"] = True
                result["status"] = "not_found"
                result["message"] = "No matching targets found"
                authoritative_checked = any(
                    step in {"UniProt", "RCSB_PDB", "AlphaFold", "authoritative"}
                    for step in lookup_path
                )
                no_match_text = (
                    "No matching target was found in the local database or the "
                    "authoritative sources checked."
                    if authoritative_checked
                    else "No matching target records were found in the local database."
                )
                result["formatted"] = (
                    "## Target database search results\n\n"
                    f"Query keywords: `{', '.join(searched)}`\n\n"
                    f"{no_match_text}"
                )
                return result

            partial = any(
                status in {"ambiguous", "unavailable", "partial"}
                for status in service_statuses
            ) or any(
                isinstance(target, dict) and bool(target.get("stale"))
                for target in targets
            )
            if partial:
                result["status"] = "partial"
                result["quality"]["status"] = "partial"
                warning = "partial_authoritative_results"
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
            else:
                result["status"] = "resolved"

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
                structure_evidence_status = target.get("structure_evidence_status")
                structures_unavailable = structure_evidence_status == "unavailable"
                has_exp = (
                    "unknown"
                    if structures_unavailable
                    else "yes"
                    if target.get("has_experimental_structure")
                    else "no"
                )
                has_af = (
                    "unknown"
                    if structures_unavailable
                    else "yes"
                    if target.get("has_alphafold_structure")
                    else "no"
                )
                match_reason = target.get("match_reason", "")
                recommended_structures = target.get("recommended_structures", [])

                lines.append(f"### {index}. {gene} ({protein})")
                lines.append(f"- UniProt: {uniprot}")
                lines.append(f"- Organism: {organism}")
                lines.append(
                    f"- Structure count: {'unknown' if structures_unavailable else struct_count}"
                )
                if structure_evidence_status:
                    lines.append(
                        f"- Structure evidence: {structure_evidence_status}"
                    )
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
                elif structures_unavailable:
                    lines.append("- Recommended structures: unavailable")
                else:
                    lines.append("- Recommended structures: none recorded")
                lines.append("")

            result["success"] = True
            result["data"] = targets
            result["formatted"] = "\n".join(lines)
            result["message"] = (
                f"Found {len(targets)} matching targets; results are partial."
                if partial
                else f"Found {len(targets)} matching targets"
            )

        except Exception:
            logger.error("Target database search failed")
            result["message"] = "Target search is temporarily unavailable."
            result["status"] = "unavailable"
            result["quality"] = {"status": "partial", "service_statuses": []}
            result["error"] = {
                "code": "provider_error",
                "message": result["message"],
            }

        return result

    @staticmethod
    def _service_evidence(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or not isinstance(value.get("source"), str):
            return None
        allowed_fields = (
            "source",
            "id",
            "source_record_id",
            "url",
            "retrieved_at",
            "expires_at",
            "stale",
            "status",
        )
        return {
            field: value[field]
            for field in allowed_fields
            if field in value
            and (
                value[field] is None
                or isinstance(value[field], (str, bool, int, float))
            )
        }

    @staticmethod
    def _target_evidence(target: dict[str, Any]) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        source = target.get("source")
        record_id = target.get("source_record_id")
        if isinstance(source, str) and isinstance(record_id, str):
            evidence.append(
                {
                    "source": source,
                    "id": record_id,
                    "url": target.get("source_url"),
                    "retrieved_at": target.get("target_retrieved_at", target.get("retrieved_at")),
                    "expires_at": target.get("target_expires_at", target.get("expires_at")),
                    "stale": bool(target.get("target_stale", target.get("stale"))),
                }
            )
        for structure in target.get("recommended_structures") or []:
            if not isinstance(structure, dict):
                continue
            structure_source = structure.get("source")
            structure_id = structure.get("structure_id")
            if not isinstance(structure_source, str) or not isinstance(
                structure_id, str
            ):
                continue
            evidence.append(
                {
                    "source": structure_source,
                    "id": structure_id,
                    "url": structure.get("source_url"),
                    "retrieved_at": structure.get(
                        "retrieved_at", target.get("structures_retrieved_at")
                    ),
                    "expires_at": structure.get(
                        "expires_at", target.get("structures_expires_at")
                    ),
                    "stale": bool(
                        structure.get(
                            "stale",
                            target.get("structures_stale", target.get("stale")),
                        )
                    ),
                }
            )
        return evidence

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
