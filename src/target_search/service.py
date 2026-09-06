"""Service layer for the local target-search demo."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

from .authoritative_resolver import AuthoritativeTargetResolver, TargetResolution
from .cache import CachedEvidence, TargetCacheRepository
from .database import (
    absolute_from_project,
    get_cache_dir,
    get_connection,
    get_db_path,
    get_target_db_dir,
    init_db,
    relative_to_project,
    resolve_project_root,
)
from .downloader import StructureDownloader
from .seed import seed_database
from .schemas import as_bool, split_list
from .scoring import calculate_structure_score


PDE_CATALYTIC_TARGETS = {
    "PDE1A", "PDE1B", "PDE1C", "PDE2A", "PDE3A", "PDE3B", "PDE4A", "PDE4B", "PDE4C", "PDE4D",
    "PDE5A", "PDE6A", "PDE6B", "PDE6C", "PDE7A", "PDE7B", "PDE8A", "PDE8B", "PDE9A", "PDE10A", "PDE11A",
}
PDE_REGULATORY_TARGETS = {"PDE6D", "PDE6G", "PDE6H"}
_SEED_LOCKS: dict[Path, threading.RLock] = {}
_SEED_LOCKS_GUARD = threading.Lock()
_TARGET_SEED_FILENAMES = (
    "seed_targets.csv",
    "common_targets.csv",
    "pde_targets.csv",
)
_UNIPROT_ACCESSION_RE = re.compile(
    r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
)
_RCSB_STRUCTURE_ID_RE = re.compile(r"[0-9][A-Z0-9]{3}")
_ORGANISM_ALIASES = {
    "homo sapiens": "Homo sapiens",
    "human": "Homo sapiens",
    "9606": "Homo sapiens",
    "mus musculus": "Mus musculus",
    "mouse": "Mus musculus",
    "10090": "Mus musculus",
}
def _seed_lock(db_path: Path) -> threading.RLock:
    resolved = db_path.resolve()
    with _SEED_LOCKS_GUARD:
        return _SEED_LOCKS.setdefault(resolved, threading.RLock())


class _InterprocessSeedLock:
    def __init__(self, db_path: Path) -> None:
        resolved = db_path.resolve()
        self._lock_path = resolved.with_name(f"{resolved.name}.seed.lock")
        self._stream = None

    def __enter__(self) -> "_InterprocessSeedLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self._lock_path.open("a+b", buffering=0)
        self._stream.seek(0, os.SEEK_END)
        if self._stream.tell() == 0:
            self._stream.write(b"\0")
        self._stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._stream.close()
            self._stream = None
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._stream is None:
            return False
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None
        return False


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    return value


class TargetSearchService:
    def __init__(
        self,
        project_root: Optional[Path | str] = None,
        resolver: Optional[AuthoritativeTargetResolver] = None,
        cache: Optional[TargetCacheRepository] = None,
        auto_seed: bool = True,
        organism: str | int = "Homo sapiens",
    ):
        if not isinstance(auto_seed, bool):
            raise TypeError("auto_seed must be a boolean")
        self.project_root = resolve_project_root(project_root)
        init_db(self.project_root)
        self._resolver = resolver
        self._owns_resolver = resolver is None
        self._resolver_lock = threading.RLock()
        self._closed = False
        self.cache = cache or TargetCacheRepository(self.project_root)
        self.downloader = StructureDownloader(self.project_root, cache=self.cache)
        self.auto_seed = auto_seed
        self.organism = organism

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
            return self._search_payload(query, [], status="not_found")

        local_results = self._search_local(query)
        if local_results:
            results = self._search_local(
                query,
                target_type=target_type,
                source=source,
                has_experimental=has_experimental,
                docking_recommended=docking_recommended,
                has_ligand=has_ligand,
            )
            return self._search_payload(
                query,
                results,
                status="resolved",
                lookup_path=["local"],
            )

        seeded = self._maybe_auto_seed()
        local_results = self._search_local(query)
        if local_results:
            results = self._search_local(
                query,
                target_type=target_type,
                source=source,
                has_experimental=has_experimental,
                docking_recommended=docking_recommended,
                has_ligand=has_ligand,
            )
            return self._search_payload(
                query,
                results,
                status="resolved",
                lookup_path=["local_seed", "local"] if seeded else ["local"],
            )

        return self._search_authoritative(
            query,
            target_type=target_type,
            source=source,
            has_experimental=has_experimental,
            docking_recommended=docking_recommended,
            has_ligand=has_ligand,
        )

    def _search_local(
        self,
        query: str,
        target_type: Optional[str] = None,
        source: Optional[str] = None,
        has_experimental: Optional[bool] = None,
        docking_recommended: Optional[bool] = None,
        has_ligand: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
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
            return [self._format_search_row(row, query) for row in exact_rows]

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
        return [self._format_search_row(row, query) for row in rows]

    def _maybe_auto_seed(self) -> bool:
        if not self.auto_seed or self._target_count() != 0:
            return False
        seed_dir = get_target_db_dir(self.project_root)
        if not any((seed_dir / filename).is_file() for filename in _TARGET_SEED_FILENAMES):
            return False
        db_path = get_db_path(self.project_root).resolve()
        lock = _seed_lock(db_path)
        with lock, _InterprocessSeedLock(db_path):
            if self._target_count() != 0:
                return False
            if not any(
                (seed_dir / filename).is_file()
                for filename in _TARGET_SEED_FILENAMES
            ):
                return False
            seed_database(self.project_root)
            return True

    def _target_count(self) -> int:
        conn = get_connection(self.project_root)
        try:
            row = conn.execute("SELECT COUNT(*) AS count FROM targets").fetchone()
            return int(row["count"])
        finally:
            conn.close()

    def _search_authoritative(
        self,
        query: str,
        *,
        target_type: Optional[str],
        source: Optional[str],
        has_experimental: Optional[bool],
        docking_recommended: Optional[bool],
        has_ligand: Optional[bool],
    ) -> dict[str, Any]:
        target_key = self._target_cache_key(query)
        target_cached = self._read_target_cache(target_key)
        target_fresh = target_cached is not None and not target_cached.stale
        target_state = (
            "fresh" if target_fresh else "expired" if target_cached else "miss"
        )
        structures_cached: Optional[CachedEvidence] = None
        structures_state = "miss"

        if target_cached is not None:
            accession = str(target_cached.payload.get("uniprot_id") or "")
            if accession:
                structures_cached = self._read_structures_cache(
                    accession,
                    organism=str(target_cached.payload["organism"]),
                )
                structures_state = (
                    "fresh"
                    if structures_cached is not None and not structures_cached.stale
                    else "expired"
                    if structures_cached is not None
                    else "miss"
                )

        if target_fresh and structures_cached is not None and not structures_cached.stale:
            record = self._record_from_cache(target_cached, structures_cached)
            return self._authoritative_success_payload(
                query,
                record,
                lookup_path=["local", "cache:target", "cache:structures"],
                cache={"target": "fresh", "structures": "fresh"},
                filters={
                    "target_type": target_type,
                    "source": source,
                    "has_experimental": has_experimental,
                    "docking_recommended": docking_recommended,
                    "has_ligand": has_ligand,
                },
            )

        resolve_query = (
            str(target_cached.payload["uniprot_id"])
            if target_fresh and target_cached is not None
            else query
        )
        resolution = self._resolve_safely(resolve_query)
        lookup_path = ["local", *resolution.lookup_path]

        if resolution.status == "resolved":
            try:
                normalized_target = self._normalize_target_evidence(resolution.target)
                normalized_structures = self._normalize_structure_evidence(
                    resolution.structures,
                    accession=normalized_target["uniprot_id"],
                    organism=normalized_target["organism"],
                )
            except (KeyError, TypeError, ValueError):
                return self._search_payload(
                    query,
                    [],
                    status="unavailable",
                    warnings=["authoritative_normalization_failed"],
                    lookup_path=lookup_path,
                    cache={
                        "target": target_state,
                        "structures": structures_state,
                    },
                    retryable=False,
                )

            if target_fresh and target_cached is not None:
                if normalized_target["uniprot_id"] != target_cached.payload["uniprot_id"]:
                    return self._search_payload(
                        query,
                        [],
                        status="unavailable",
                        warnings=["authoritative_identity_mismatch"],
                        lookup_path=lookup_path,
                        cache={
                            "target": "fresh",
                            "structures": structures_state,
                        },
                        retryable=False,
                    )
                target_evidence = target_cached
                target_state = "fresh"
            else:
                target_evidence = self.cache.upsert(
                    target_key,
                    "target",
                    normalized_target["source"],
                    normalized_target["source_record_id"],
                    normalized_target,
                )
                target_state = "refreshed"

            accession = str(target_evidence.payload["uniprot_id"])
            structures_evidence = self.cache.upsert(
                self._structures_cache_key(accession),
                "structures",
                self._structure_cache_source(normalized_structures),
                accession,
                {"structures": normalized_structures},
            )
            record = self._record_from_cache(target_evidence, structures_evidence)
            return self._authoritative_success_payload(
                query,
                record,
                warnings=list(resolution.warnings),
                lookup_path=lookup_path,
                cache={"target": target_state, "structures": "refreshed"},
                filters={
                    "target_type": target_type,
                    "source": source,
                    "has_experimental": has_experimental,
                    "docking_recommended": docking_recommended,
                    "has_ligand": has_ligand,
                },
            )

        if self._resolution_is_retryable(resolution) and target_cached is not None:
            if structures_cached is None:
                record = self._target_only_record(target_cached)
                warnings = [
                    *resolution.warnings,
                    "authoritative_structures_unavailable",
                ]
                if target_cached.stale:
                    warnings.append("stale_authoritative_cache")
                return self._authoritative_success_payload(
                    query,
                    record,
                    status="partial",
                    warnings=warnings,
                    lookup_path=lookup_path,
                    cache={
                        "target": "stale" if target_cached.stale else "fresh",
                        "structures": "unavailable",
                    },
                    filters={
                        "target_type": target_type,
                        "source": source,
                        "has_experimental": has_experimental,
                        "docking_recommended": docking_recommended,
                        "has_ligand": has_ligand,
                    },
                )
            record = self._record_from_cache(target_cached, structures_cached)
            warnings = [*resolution.warnings, "stale_authoritative_cache"]
            return self._authoritative_success_payload(
                query,
                record,
                warnings=warnings,
                lookup_path=lookup_path,
                cache={
                    "target": "stale" if target_cached.stale else "fresh",
                    "structures": (
                        "stale" if structures_cached.stale else "fresh"
                    ),
                },
                filters={
                    "target_type": target_type,
                    "source": source,
                    "has_experimental": has_experimental,
                    "docking_recommended": docking_recommended,
                    "has_ligand": has_ligand,
                },
            )

        return self._search_payload(
            query,
            [],
            status=resolution.status,
            warnings=list(resolution.warnings),
            lookup_path=lookup_path,
            cache={"target": target_state, "structures": structures_state},
            retryable=resolution.retryable,
        )

    def _resolve_safely(self, query: str) -> TargetResolution:
        try:
            return self._get_resolver().resolve(query, self.organism)
        except Exception:
            return TargetResolution(
                status="unavailable",
                warnings=("authoritative_lookup_failed",),
                lookup_path=("authoritative",),
                retryable=False,
                source="Remote",
                code="request_failure",
            )

    def _get_resolver(self) -> AuthoritativeTargetResolver:
        with self._resolver_lock:
            if self._closed:
                raise RuntimeError("TargetSearchService is closed")
            if self._resolver is None:
                self._resolver = AuthoritativeTargetResolver()
            return self._resolver

    def close(self) -> None:
        resolver = None
        with self._resolver_lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_resolver:
                resolver = self._resolver
            self._resolver = None
        if resolver is not None:
            resolver.close()

    def __enter__(self) -> "TargetSearchService":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @staticmethod
    def _resolution_is_retryable(resolution: TargetResolution) -> bool:
        return resolution.status == "unavailable" and resolution.retryable

    def _target_cache_key(self, query: str) -> str:
        identity = (
            f"{str(self.organism).strip().casefold()}\0"
            f"{query.strip().casefold()}"
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"target:{digest}"

    @staticmethod
    def _structures_cache_key(accession: str) -> str:
        return f"structures:{accession.strip().upper()}"

    def _read_target_cache(self, cache_key: str) -> Optional[CachedEvidence]:
        evidence = self.cache.get(cache_key, allow_stale=True)
        if evidence is None:
            return None
        try:
            normalized = self._normalize_target_evidence(evidence.payload)
            if (
                evidence.cache_key != cache_key
                or evidence.record_type != "target"
                or evidence.source != normalized["source"]
                or evidence.source_record_id != normalized["source_record_id"]
                or _json_copy(evidence.payload) != normalized
            ):
                raise ValueError("cached target provenance is invalid")
        except (KeyError, TypeError, ValueError):
            accessions = {
                str(evidence.source_record_id).strip().upper(),
                str(evidence.payload.get("uniprot_id") or "").strip().upper(),
                str(evidence.payload.get("source_record_id") or "").strip().upper(),
            }
            self.cache.discard(
                cache_key,
                expected_generation_id=evidence.generation_id,
            )
            for accession in accessions:
                if _UNIPROT_ACCESSION_RE.fullmatch(accession):
                    structures_key = self._structures_cache_key(accession)
                    structures = self.cache.get(structures_key, allow_stale=True)
                    if structures is not None:
                        self.cache.discard(
                            structures_key,
                            expected_generation_id=structures.generation_id,
                        )
            return None
        return evidence

    def _read_structures_cache(
        self,
        accession: str,
        *,
        organism: str,
    ) -> Optional[CachedEvidence]:
        cache_key = self._structures_cache_key(accession)
        evidence = self.cache.get(cache_key, allow_stale=True)
        if evidence is None:
            return None
        try:
            payload = _json_copy(evidence.payload)
            if set(payload) != {"structures"}:
                raise ValueError("cached structures payload is invalid")
            normalized = self._normalize_structure_evidence(
                tuple(payload["structures"]),
                accession=accession,
                organism=organism,
            )
            expected_source = self._structure_cache_source(normalized)
            if (
                evidence.cache_key != cache_key
                or evidence.record_type != "structures"
                or evidence.source != expected_source
                or evidence.source_record_id != accession
                or payload != {"structures": normalized}
            ):
                raise ValueError("cached structure provenance is invalid")
        except (KeyError, TypeError, ValueError):
            self.cache.discard(
                cache_key,
                expected_generation_id=evidence.generation_id,
            )
            return None
        return evidence

    def _normalize_target_evidence(
        self, target: Optional[Mapping[str, Any]]
    ) -> dict[str, Any]:
        if target is None:
            raise ValueError("resolved evidence requires a target")
        required = (
            "gene_symbol",
            "uniprot_id",
            "source",
            "source_record_id",
            "source_url",
        )
        if any(not isinstance(target.get(key), str) or not target[key].strip() for key in required):
            raise ValueError("target evidence is incomplete")
        aliases = target.get("aliases") or []
        if not isinstance(aliases, (list, tuple)) or any(
            not isinstance(item, str) for item in aliases
        ):
            raise ValueError("target aliases are invalid")
        accession = target["uniprot_id"].strip().upper()
        source_record_id = target["source_record_id"].strip().upper()
        source = target["source"].strip()
        organism = self._optional_text(target.get("organism"))
        expected_organism = self._canonical_organism(self.organism)
        if (
            source != "UniProt"
            or _UNIPROT_ACCESSION_RE.fullmatch(accession) is None
            or source_record_id != accession
            or target["source_url"].strip()
            != f"https://www.uniprot.org/uniprotkb/{accession}/entry"
            or organism is None
            or self._canonical_organism(organism) != expected_organism
        ):
            raise ValueError("target provenance is invalid")
        return {
            "gene_symbol": target["gene_symbol"].strip(),
            "protein_name": self._optional_text(target.get("protein_name")),
            "uniprot_id": accession,
            "organism": organism,
            "target_type": self._optional_text(target.get("target_type")),
            "disease_keywords": [],
            "aliases": sorted({item.strip() for item in aliases if item.strip()}),
            "source": source,
            "source_record_id": source_record_id,
            "source_url": f"https://www.uniprot.org/uniprotkb/{accession}/entry",
            "match_reason": self._optional_text(target.get("match_reason")) or "authoritative",
        }

    def _normalize_structure_evidence(
        self,
        structures: tuple[Mapping[str, Any], ...],
        *,
        accession: str,
        organism: Optional[str],
    ) -> list[dict[str, Any]]:
        normalized = []
        for structure in structures:
            structure_id = structure.get("structure_id")
            source = structure.get("source")
            structure_type = structure.get("structure_type")
            source_url = structure.get("source_url")
            if any(
                not isinstance(value, str) or not value.strip()
                for value in (structure_id, source, structure_type, source_url)
            ):
                raise ValueError("structure evidence is incomplete")
            normalized_id = structure_id.strip().upper()
            normalized_source = source.strip()
            normalized_type = structure_type.strip().casefold()
            normalized_source_url = source_url.strip()
            normalized_download_url = self._optional_text(
                structure.get("download_url")
            )
            structure_organism = self._optional_text(structure.get("organism"))
            if structure_organism is not None and (
                organism is None
                or self._canonical_organism(structure_organism)
                != self._canonical_organism(organism)
            ):
                raise ValueError("structure organism is invalid")
            if normalized_source == "RCSB_PDB":
                if (
                    normalized_type != "experimental"
                    or _RCSB_STRUCTURE_ID_RE.fullmatch(normalized_id) is None
                    or normalized_source_url
                    != f"https://www.rcsb.org/structure/{normalized_id}"
                    or normalized_download_url
                    != f"https://files.rcsb.org/download/{normalized_id}.cif"
                ):
                    raise ValueError("RCSB provenance is invalid")
            elif normalized_source == "AlphaFold":
                expected_model_id = f"AF-{accession}-F1"
                model_id = self._optional_text(structure.get("model_id"))
                linked_accession = self._optional_text(structure.get("uniprot_id"))
                official_download = re.fullmatch(
                    rf"https://alphafold\.ebi\.ac\.uk/files/{re.escape(expected_model_id)}-model_v[1-9][0-9]*\.(?:cif|pdb)",
                    normalized_download_url or "",
                )
                if (
                    normalized_type != "predicted"
                    or normalized_id != expected_model_id
                    or model_id != expected_model_id
                    or linked_accession != accession
                    or normalized_source_url
                    != f"https://alphafold.ebi.ac.uk/entry/{accession}"
                    or official_download is None
                ):
                    raise ValueError("AlphaFold provenance is invalid")
            else:
                raise ValueError("structure source is invalid")
            ligand_values = structure.get("ligand_evidence", structure.get("ligand_ids", [])) or []
            if not isinstance(ligand_values, (list, tuple)) or any(
                not isinstance(item, str) for item in ligand_values
            ):
                raise ValueError("structure ligand evidence is invalid")
            is_experimental = normalized_type == "experimental"
            payload = {
                "id": None,
                "structure_id": normalized_id,
                "source": normalized_source,
                "structure_type": normalized_type,
                "method": self._optional_text(structure.get("method")),
                "resolution": structure.get("resolution"),
                "chain_ids": [],
                "ligand_ids": sorted(
                    {item.strip() for item in ligand_values if item.strip()}
                ),
                "organism": structure_organism,
                "title": self._optional_text(structure.get("title")),
                "file_format": "cif",
                "download_url": normalized_download_url,
                "source_url": normalized_source_url,
                "is_downloaded": False,
                "docking_recommended": is_experimental,
                "is_preferred": False,
                "quality_note": None,
            }
            payload["score"] = calculate_structure_score(payload)
            normalized.append(payload)
        normalized.sort(
            key=lambda item: (-item["score"], item["source"], item["structure_id"])
        )
        for index, payload in enumerate(normalized):
            payload["is_preferred"] = index == 0
            payload["score"] = calculate_structure_score(payload)
            payload["recommendation_reasons"] = self._structure_recommendation_reasons(payload)
            payload["warnings"] = self._structure_warnings(payload)
            payload["recommendation_level"] = self._recommendation_level(payload)
            payload["docking_grade"] = self._docking_grade(payload)
            payload["docking_grade_label"] = self._docking_grade_label(
                payload["docking_grade"]
            )
        return normalized

    @staticmethod
    def _optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("text evidence must be a string")
        return value.strip() or None

    @staticmethod
    def _canonical_organism(value: Any) -> str:
        if isinstance(value, bool) or value is None:
            raise ValueError("organism evidence is invalid")
        normalized = str(value).strip().casefold()
        if normalized not in _ORGANISM_ALIASES:
            raise ValueError("organism evidence is invalid")
        return _ORGANISM_ALIASES[normalized]

    @staticmethod
    def _structure_cache_source(structures: list[dict[str, Any]]) -> str:
        sources = {item["source"] for item in structures}
        return next(iter(sources)) if len(sources) == 1 else "Authoritative"

    def _record_from_cache(
        self, target: CachedEvidence, structures: CachedEvidence
    ) -> dict[str, Any]:
        target_payload = dict(target.payload)
        structure_records = [
            _json_copy(item) for item in structures.payload.get("structures", [])
        ]
        for structure_record in structure_records:
            structure_record.update(
                {
                    "retrieved_at": structures.retrieved_at.isoformat(),
                    "expires_at": structures.expires_at.isoformat(),
                    "stale": structures.stale,
                }
            )
        experimental_count = sum(
            item.get("structure_type") == "experimental" for item in structure_records
        )
        alphafold_count = sum(
            item.get("source") == "AlphaFold" for item in structure_records
        )
        return {
            "target_id": None,
            **_json_copy(target_payload),
            "structure_count": len(structure_records),
            "downloaded_structure_count": 0,
            "experimental_structure_count": experimental_count,
            "alphafold_structure_count": alphafold_count,
            "has_experimental_structure": experimental_count > 0,
            "has_alphafold_structure": alphafold_count > 0,
            "recommended_structures": structure_records,
            "retrieved_at": target.retrieved_at.isoformat(),
            "expires_at": target.expires_at.isoformat(),
            "target_retrieved_at": target.retrieved_at.isoformat(),
            "target_expires_at": target.expires_at.isoformat(),
            "structures_retrieved_at": structures.retrieved_at.isoformat(),
            "structures_expires_at": structures.expires_at.isoformat(),
            "target_stale": target.stale,
            "structures_stale": structures.stale,
            "stale": target.stale or structures.stale,
            "structure_evidence_status": (
                "stale" if structures.stale else "fresh"
            ),
        }

    @staticmethod
    def _target_only_record(target: CachedEvidence) -> dict[str, Any]:
        return {
            "target_id": None,
            **_json_copy(target.payload),
            "structure_count": None,
            "downloaded_structure_count": None,
            "experimental_structure_count": None,
            "alphafold_structure_count": None,
            "has_experimental_structure": None,
            "has_alphafold_structure": None,
            "recommended_structures": [],
            "retrieved_at": target.retrieved_at.isoformat(),
            "expires_at": target.expires_at.isoformat(),
            "target_retrieved_at": target.retrieved_at.isoformat(),
            "target_expires_at": target.expires_at.isoformat(),
            "structures_retrieved_at": None,
            "structures_expires_at": None,
            "target_stale": target.stale,
            "structures_stale": None,
            "stale": target.stale,
            "structure_evidence_status": "unavailable",
        }

    def _authoritative_success_payload(
        self,
        query: str,
        record: dict[str, Any],
        *,
        status: str = "resolved",
        lookup_path: list[str],
        cache: dict[str, str],
        filters: dict[str, Any],
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        results = [record] if self._remote_record_matches(record, **filters) else []
        return self._search_payload(
            query,
            results,
            status=status,
            warnings=warnings,
            lookup_path=lookup_path,
            cache=cache,
        )

    @staticmethod
    def _remote_record_matches(
        record: Mapping[str, Any],
        *,
        target_type: Optional[str],
        source: Optional[str],
        has_experimental: Optional[bool],
        docking_recommended: Optional[bool],
        has_ligand: Optional[bool],
    ) -> bool:
        structures = record.get("recommended_structures") or []
        if record.get("structure_evidence_status") == "unavailable" and any(
            value is not None
            for value in (
                source,
                has_experimental,
                docking_recommended,
                has_ligand,
            )
        ):
            return False
        if target_type and str(record.get("target_type") or "").casefold() != target_type.casefold():
            return False
        if source and not any(
            str(item.get("source") or "").casefold() == source.casefold()
            for item in structures
        ):
            return False
        if has_experimental is not None and bool(
            record.get("has_experimental_structure")
        ) != has_experimental:
            return False
        if docking_recommended is not None and any(
            bool(item.get("docking_recommended")) for item in structures
        ) != docking_recommended:
            return False
        if has_ligand is not None and any(
            bool(item.get("ligand_ids")) for item in structures
        ) != has_ligand:
            return False
        return True

    @staticmethod
    def _search_payload(
        query: str,
        results: list[dict[str, Any]],
        *,
        status: str,
        warnings: Optional[list[str]] = None,
        lookup_path: Optional[list[str]] = None,
        cache: Optional[dict[str, str]] = None,
        retryable: Optional[bool] = None,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "results": results,
            "warnings": list(dict.fromkeys(warnings or [])),
            "lookup_path": list(lookup_path or []),
            "status": status,
            "cache": cache or {"target": "none", "structures": "none"},
        }
        if retryable is not None:
            payload["retryable"] = retryable
        return payload

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

    def get_fallback_health(self) -> dict[str, Any]:
        """Expose bounded operational counts without machine paths or cache payloads."""
        conn = get_connection(self.project_root)
        try:
            local_target_count = int(
                conn.execute("SELECT COUNT(*) AS count FROM targets").fetchone()["count"]
            )
            local_structure_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM target_structures"
                ).fetchone()["count"]
            )
        finally:
            conn.close()
        cache_health = self.cache.get_health_summary()
        seed_dir = get_target_db_dir(self.project_root)
        return {
            "local_target_count": local_target_count,
            "local_structure_count": local_structure_count,
            "seed_available": any(
                (seed_dir / filename).is_file()
                for filename in _TARGET_SEED_FILENAMES
            ),
            "cache_entry_count": cache_health["cache_entry_count"],
            "stale_entry_count": cache_health["stale_entry_count"],
            "last_refresh_errors": cache_health["last_refresh_errors"],
        }

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
