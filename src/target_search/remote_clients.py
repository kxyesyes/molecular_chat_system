"""Bounded clients for authoritative public target and structure sources."""

from __future__ import annotations

import json as json_module
import math
import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import requests


STABLE_USER_AGENT = "MedChat-TargetSearch/1.0"

_OFFICIAL_SOURCES = frozenset({"UniProt", "RCSB_PDB", "AlphaFold"})
_ERROR_MESSAGES = {
    "ambiguous_match": "multiple authoritative records matched",
    "connection_failure": "connection to the remote source failed",
    "http_client_error": "the remote source rejected the request",
    "invalid_identifier": "the supplied identifier is invalid",
    "invalid_response": "the remote source returned an invalid response shape",
    "malformed_json": "the remote source returned malformed JSON",
    "provider_unavailable": "the remote source is temporarily unavailable",
    "request_failure": "the remote request failed",
    "response_too_large": "the remote response exceeded the configured limit",
    "timeout": "the remote request timed out",
    "unexpected_status": "the remote source returned an unexpected status",
}

_UNIPROT_ACCESSION_RE = re.compile(
    r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
)
_GENE_SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,63}")
_ORGANISM_RE = re.compile(r"[A-Za-z][A-Za-z0-9 .()_-]{0,99}")
_PDB_ID_RE = re.compile(r"[0-9][A-Z0-9]{3}")
_ALPHAFOLD_ENTRY_RE = re.compile(r"AF-([A-Z0-9]{6}|[A-Z0-9]{10})-F[0-9]+")


class RemoteSourceError(RuntimeError):
    """A sanitized, stable failure contract for authoritative source calls."""

    def __init__(
        self,
        source: str,
        code: str,
        *,
        retryable: bool,
        status: int | None = None,
    ) -> None:
        if code not in _ERROR_MESSAGES:
            raise ValueError("unknown remote source error code")
        safe_source = source if source in _OFFICIAL_SOURCES else "Remote"
        safe_status = status if isinstance(status, int) and not isinstance(status, bool) else None
        message = f"{safe_source}: {_ERROR_MESSAGES[code]} [{code}]"
        if safe_status is not None:
            message += f" (status {safe_status})"
        super().__init__(message)
        self.source = safe_source
        self.code = code
        self.retryable = bool(retryable)
        self.status = safe_status


class BoundedJsonClient:
    """Small JSON transport with bounded reads and narrowly scoped retries."""

    def __init__(
        self,
        session: requests.Session,
        *,
        sleep: Callable[[float], None] = time.sleep,
        timeout: tuple[float, float] = (3.05, 10.0),
        max_attempts: int = 3,
        backoff_base: float = 0.25,
        backoff_ceiling: float = 2.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        if not callable(getattr(session, "request", None)):
            raise TypeError("session must provide request()")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if (
            not isinstance(timeout, tuple)
            or len(timeout) != 2
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
                for value in timeout
            )
        ):
            raise ValueError("timeout must contain two positive finite numbers")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes < 1
        ):
            raise ValueError("max_response_bytes must be a positive integer")
        for name, value in (
            ("backoff_base", backoff_base),
            ("backoff_ceiling", backoff_ceiling),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative finite number")

        self._session = session
        self._sleep = sleep
        self._timeout = (float(timeout[0]), float(timeout[1]))
        self._max_attempts = max_attempts
        self._backoff_base = float(backoff_base)
        self._backoff_ceiling = float(backoff_ceiling)
        self._max_response_bytes = max_response_bytes

    def request_json(
        self,
        source: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any | None:
        if source not in _OFFICIAL_SOURCES:
            raise ValueError("source must be an authoritative provider")
        request_options: dict[str, Any] = {
            "headers": {
                "Accept": "application/json",
                "User-Agent": STABLE_USER_AGENT,
            },
            "timeout": self._timeout,
            "stream": True,
        }
        if params is not None:
            request_options["params"] = dict(params)
        if json is not None:
            request_options["json"] = dict(json)

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.request(method, url, **request_options)
            except requests.Timeout:
                if attempt == self._max_attempts:
                    raise RemoteSourceError(
                        source, "timeout", retryable=True
                    ) from None
                self._sleep(self._backoff_delay(attempt))
                continue
            except requests.ConnectionError:
                if attempt == self._max_attempts:
                    raise RemoteSourceError(
                        source, "connection_failure", retryable=True
                    ) from None
                self._sleep(self._backoff_delay(attempt))
                continue
            except requests.RequestException:
                raise RemoteSourceError(
                    source, "request_failure", retryable=False
                ) from None

            retry_delay: float | None = None
            transport_error_code: str | None = None
            try:
                status = getattr(response, "status_code", None)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise RemoteSourceError(
                        source, "invalid_response", retryable=False
                    )
                if status == 404:
                    return None
                if status == 429 or 500 <= status <= 599:
                    code = "provider_unavailable"
                    if attempt == self._max_attempts:
                        raise RemoteSourceError(
                            source, code, retryable=True, status=status
                        )
                    retry_delay = self._retry_delay(response, attempt, status)
                elif 400 <= status <= 499:
                    raise RemoteSourceError(
                        source,
                        "http_client_error",
                        retryable=False,
                        status=status,
                    )
                elif not 200 <= status <= 299:
                    raise RemoteSourceError(
                        source,
                        "unexpected_status",
                        retryable=False,
                        status=status,
                    )
                else:
                    raw = self._read_bounded(response, source, status)
                    try:
                        return json_module.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json_module.JSONDecodeError):
                        raise RemoteSourceError(
                            source,
                            "malformed_json",
                            retryable=False,
                            status=status,
                        ) from None
            except requests.Timeout:
                transport_error_code = "timeout"
            except requests.ConnectionError:
                transport_error_code = "connection_failure"
            except requests.RequestException:
                raise RemoteSourceError(
                    source, "request_failure", retryable=False
                ) from None
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

            if transport_error_code is not None:
                if attempt == self._max_attempts:
                    raise RemoteSourceError(
                        source, transport_error_code, retryable=True
                    ) from None
                self._sleep(self._backoff_delay(attempt))
                continue
            if retry_delay is not None:
                self._sleep(retry_delay)

        raise AssertionError("bounded retry loop exhausted without a result")

    def _read_bounded(self, response: Any, source: str, status: int) -> bytes:
        headers = getattr(response, "headers", {})
        content_length = None
        if isinstance(headers, Mapping):
            content_length = headers.get("Content-Length")
        if isinstance(content_length, str) and content_length.strip().isdigit():
            if int(content_length.strip()) > self._max_response_bytes:
                raise RemoteSourceError(
                    source,
                    "response_too_large",
                    retryable=False,
                    status=status,
                )

        iter_content = getattr(response, "iter_content", None)
        if not callable(iter_content):
            raise RemoteSourceError(source, "invalid_response", retryable=False, status=status)
        chunks: list[bytes] = []
        total = 0
        for chunk in iter_content(chunk_size=min(8192, self._max_response_bytes + 1)):
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise RemoteSourceError(
                    source, "invalid_response", retryable=False, status=status
                )
            total += len(chunk)
            if total > self._max_response_bytes:
                raise RemoteSourceError(
                    source,
                    "response_too_large",
                    retryable=False,
                    status=status,
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _backoff_delay(self, attempt: int) -> float:
        return min(self._backoff_base * (2 ** (attempt - 1)), self._backoff_ceiling)

    def _retry_delay(self, response: Any, attempt: int, status: int) -> float:
        if status == 429:
            headers = getattr(response, "headers", {})
            value = headers.get("Retry-After") if isinstance(headers, Mapping) else None
            try:
                retry_after = float(value)
            except (TypeError, ValueError):
                retry_after = -1.0
            if math.isfinite(retry_after) and retry_after >= 0:
                return min(retry_after, self._backoff_ceiling)
        return self._backoff_delay(attempt)


class UniProtClient:
    SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"

    def __init__(self, http: BoundedJsonClient) -> None:
        self._http = http

    def resolve(
        self, query: str, organism: str = "Homo sapiens"
    ) -> dict[str, Any] | None:
        normalized_query = _validate_gene_or_accession(query, "UniProt")
        normalized_organism = _validate_organism(organism)
        is_accession = _is_uniprot_accession(normalized_query)
        field = "accession" if is_accession else "gene_exact"
        if normalized_organism.casefold() == "homo sapiens":
            organism_filter = "organism_id:9606"
        else:
            organism_filter = f'organism_name:"{normalized_organism}"'
        payload = self._http.request_json(
            "UniProt",
            "GET",
            self.SEARCH_URL,
            params={
                "query": f"({field}:{normalized_query}) AND ({organism_filter})",
                "format": "json",
                "size": 5,
                "fields": "accession,id,gene_names,protein_name,organism_name",
            },
        )
        if payload is None:
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
        results = payload["results"]
        if len(results) > 5:
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)

        ranked: list[tuple[int, dict[str, Any], str]] = []
        for record in results:
            if not isinstance(record, dict):
                raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
            accession = record.get("primaryAccession")
            if not isinstance(accession, str) or not _is_uniprot_accession(accession.upper()):
                raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
            gene_name, aliases = _uniprot_genes(record)
            if is_accession:
                if accession.upper() == normalized_query:
                    ranked.append((0, record, "exact_accession"))
            elif gene_name is not None and gene_name.casefold() == normalized_query.casefold():
                ranked.append((0, record, "exact_gene"))
            elif any(alias.casefold() == normalized_query.casefold() for alias in aliases):
                ranked.append((1, record, "exact_alias"))

        if not ranked:
            return None
        best_rank = min(item[0] for item in ranked)
        best = [item for item in ranked if item[0] == best_rank]
        unique_accessions = {item[1]["primaryAccession"].upper() for item in best}
        if len(unique_accessions) != 1:
            raise RemoteSourceError("UniProt", "ambiguous_match", retryable=False)
        _, selected, match_reason = best[0]
        return _normalize_uniprot(selected, match_reason)


class RcsbClient:
    SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
    ENTRY_URL = "https://data.rcsb.org/rest/v1/core/entry/{entry_id}"

    def __init__(self, http: BoundedJsonClient, *, max_structures: int = 10) -> None:
        if (
            isinstance(max_structures, bool)
            or not isinstance(max_structures, int)
            or not 1 <= max_structures <= 100
        ):
            raise ValueError("max_structures must be an integer from 1 to 100")
        self._http = http
        self._max_structures = max_structures

    def search(self, uniprot_accession: str) -> list[dict[str, Any]]:
        accession = _validate_uniprot_accession(uniprot_accession, "RCSB_PDB")
        payload = self._http.request_json(
            "RCSB_PDB",
            "POST",
            self.SEARCH_URL,
            json={
                "query": {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {
                        "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                        "operator": "exact_match",
                        "value": accession,
                    },
                },
                "return_type": "entry",
                "request_options": {
                    "paginate": {"start": 0, "rows": self._max_structures}
                },
            },
        )
        if payload is None:
            return []
        if not isinstance(payload, dict) or not isinstance(payload.get("result_set"), list):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)

        identifiers: set[str] = set()
        for item in payload["result_set"]:
            if not isinstance(item, dict) or not isinstance(item.get("identifier"), str):
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            identifier = item["identifier"].upper()
            if _PDB_ID_RE.fullmatch(identifier) is None:
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            identifiers.add(identifier)

        normalized: list[dict[str, Any]] = []
        for entry_id in sorted(identifiers)[: self._max_structures]:
            entry = self._http.request_json(
                "RCSB_PDB",
                "GET",
                self.ENTRY_URL.format(entry_id=entry_id),
            )
            if entry is None:
                continue
            normalized.append(_normalize_rcsb_entry(entry, entry_id))
        return normalized


class AlphaFoldClient:
    PREDICTION_URL = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"

    def __init__(self, http: BoundedJsonClient) -> None:
        self._http = http

    def lookup(self, accession: str) -> dict[str, Any] | None:
        normalized_accession = _validate_uniprot_accession(accession, "AlphaFold")
        payload = self._http.request_json(
            "AlphaFold",
            "GET",
            self.PREDICTION_URL.format(accession=normalized_accession),
        )
        if payload is None:
            return None
        if not isinstance(payload, list):
            raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
        if not payload:
            return None

        matching: list[dict[str, Any]] = []
        for record in payload:
            if not isinstance(record, dict):
                raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
            record_accession = record.get("uniprotAccession")
            entry_id = record.get("entryId")
            if not isinstance(record_accession, str) or not isinstance(entry_id, str):
                raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
            entry_match = _ALPHAFOLD_ENTRY_RE.fullmatch(entry_id.upper())
            if entry_match is None or entry_match.group(1) != record_accession.upper():
                raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
            if record_accession.upper() == normalized_accession:
                matching.append(record)
        if not matching:
            return None
        if len(matching) != 1:
            raise RemoteSourceError("AlphaFold", "ambiguous_match", retryable=False)
        return _normalize_alphafold(matching[0], normalized_accession)


def _is_uniprot_accession(value: str) -> bool:
    return _UNIPROT_ACCESSION_RE.fullmatch(value) is not None


def _validate_uniprot_accession(value: str, source: str) -> str:
    if not isinstance(value, str):
        raise RemoteSourceError(source, "invalid_identifier", retryable=False)
    normalized = value.strip().upper()
    if not _is_uniprot_accession(normalized):
        raise RemoteSourceError(source, "invalid_identifier", retryable=False)
    return normalized


def _validate_gene_or_accession(value: str, source: str) -> str:
    if not isinstance(value, str):
        raise RemoteSourceError(source, "invalid_identifier", retryable=False)
    normalized = value.strip().upper()
    if not normalized or (
        not _is_uniprot_accession(normalized)
        and _GENE_SYMBOL_RE.fullmatch(normalized) is None
    ):
        raise RemoteSourceError(source, "invalid_identifier", retryable=False)
    return normalized


def _validate_organism(value: str) -> str:
    if not isinstance(value, str):
        raise RemoteSourceError("UniProt", "invalid_identifier", retryable=False)
    normalized = " ".join(value.strip().split())
    if _ORGANISM_RE.fullmatch(normalized) is None:
        raise RemoteSourceError("UniProt", "invalid_identifier", retryable=False)
    return normalized


def _uniprot_genes(record: dict[str, Any]) -> tuple[str | None, list[str]]:
    genes = record.get("genes", [])
    if not isinstance(genes, list):
        raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
    primary: str | None = None
    aliases: set[str] = set()
    for gene in genes:
        if not isinstance(gene, dict):
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
        gene_name = gene.get("geneName")
        if gene_name is not None:
            if not isinstance(gene_name, dict) or not isinstance(gene_name.get("value"), str):
                raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
            value = gene_name["value"].strip()
            if value and primary is None:
                primary = value
        synonyms = gene.get("synonyms", [])
        if not isinstance(synonyms, list):
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
        for synonym in synonyms:
            if not isinstance(synonym, dict) or not isinstance(synonym.get("value"), str):
                raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
            value = synonym["value"].strip()
            if value:
                aliases.add(value)
    if primary is not None:
        aliases = {alias for alias in aliases if alias.casefold() != primary.casefold()}
    return primary, sorted(aliases, key=lambda item: (item.casefold(), item))


def _normalize_uniprot(record: dict[str, Any], match_reason: str) -> dict[str, Any]:
    accession = record["primaryAccession"].upper()
    gene_name, aliases = _uniprot_genes(record)
    protein_name = None
    description = record.get("proteinDescription")
    if description is not None:
        if not isinstance(description, dict):
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
        recommended = description.get("recommendedName")
        if recommended is not None:
            if not isinstance(recommended, dict):
                raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
            full_name = recommended.get("fullName")
            if full_name is not None:
                if not isinstance(full_name, dict) or not isinstance(full_name.get("value"), str):
                    raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
                protein_name = full_name["value"].strip() or None
    organism_name = None
    organism = record.get("organism")
    if organism is not None:
        if not isinstance(organism, dict):
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
        scientific_name = organism.get("scientificName")
        if scientific_name is not None and not isinstance(scientific_name, str):
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
        organism_name = scientific_name.strip() if scientific_name else None
    return {
        "gene_symbol": gene_name,
        "protein_name": protein_name,
        "uniprot_id": accession,
        "organism": organism_name,
        "aliases": aliases,
        "source": "UniProt",
        "source_record_id": accession,
        "source_url": f"https://www.uniprot.org/uniprotkb/{accession}/entry",
        "match_reason": match_reason,
    }


def _normalize_rcsb_entry(entry: Any, requested_id: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    returned_id = entry.get("rcsb_id")
    if returned_id is not None and (
        not isinstance(returned_id, str) or returned_id.upper() != requested_id
    ):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)

    title = None
    struct = entry.get("struct")
    if struct is not None:
        if not isinstance(struct, dict):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        title_value = struct.get("title")
        if title_value is not None and not isinstance(title_value, str):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        title = title_value.strip() if title_value else None

    method = None
    experiments = entry.get("exptl")
    if experiments is not None:
        if not isinstance(experiments, list):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        for experiment in experiments:
            if not isinstance(experiment, dict):
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            value = experiment.get("method")
            if value is not None and not isinstance(value, str):
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            if value:
                method = value.strip()
                break

    info = entry.get("rcsb_entry_info", {})
    if not isinstance(info, dict):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    resolution = _optional_resolution(info)
    organism = _optional_first_string(
        info, ("source_organism_names", "organism_names"), "RCSB_PDB"
    )
    ligands = _optional_string_list(
        info, "nonpolymer_bound_components", "RCSB_PDB"
    )
    return {
        "structure_id": requested_id,
        "source": "RCSB_PDB",
        "structure_type": "experimental",
        "method": method,
        "resolution": resolution,
        "organism": organism,
        "title": title,
        "ligand_evidence": ligands,
        "download_url": f"https://files.rcsb.org/download/{requested_id}.cif",
        "source_url": f"https://www.rcsb.org/structure/{requested_id}",
    }


def _optional_resolution(info: dict[str, Any]) -> float | None:
    values = info.get("resolution_combined")
    if values is None:
        return None
    if not isinstance(values, list):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    for value in values:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and value > 0
        ):
            return float(value)
    return None


def _optional_first_string(
    mapping: dict[str, Any], keys: tuple[str, ...], source: str
) -> str | None:
    for key in keys:
        values = mapping.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise RemoteSourceError(source, "invalid_response", retryable=False)
        normalized = sorted(
            {value.strip() for value in values if value.strip()},
            key=lambda item: (item.casefold(), item),
        )
        return normalized[0] if normalized else None
    return None


def _optional_string_list(
    mapping: dict[str, Any], key: str, source: str
) -> list[str]:
    values = mapping.get(key)
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise RemoteSourceError(source, "invalid_response", retryable=False)
    return sorted({value.strip() for value in values if value.strip()})


def _normalize_alphafold(
    record: dict[str, Any], accession: str
) -> dict[str, Any]:
    entry_id = record["entryId"].upper()
    pdb_url = _optional_alphafold_url(record, "pdbUrl")
    cif_url = _optional_alphafold_url(record, "cifUrl")
    title = _optional_text(record, "uniprotDescription", "AlphaFold")
    organism = _optional_text(record, "organismScientificName", "AlphaFold")
    return {
        "structure_id": entry_id,
        "model_id": entry_id,
        "uniprot_id": accession,
        "source": "AlphaFold",
        "structure_type": "predicted",
        "resolution": None,
        "title": title,
        "organism": organism,
        "pdb_url": pdb_url,
        "cif_url": cif_url,
        "download_url": cif_url or pdb_url,
        "source_url": f"https://alphafold.ebi.ac.uk/entry/{accession}",
    }


def _optional_text(mapping: dict[str, Any], key: str, source: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RemoteSourceError(source, "invalid_response", retryable=False)
    return value.strip() or None


def _optional_alphafold_url(record: dict[str, Any], key: str) -> str | None:
    value = _optional_text(record, key, "AlphaFold")
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != "alphafold.ebi.ac.uk":
        raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
    return value


__all__ = [
    "AlphaFoldClient",
    "BoundedJsonClient",
    "RcsbClient",
    "RemoteSourceError",
    "STABLE_USER_AGENT",
    "UniProtClient",
]
