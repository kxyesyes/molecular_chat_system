"""Bounded, exact UniProt protein-name mapping; no gene/LLM translation.

Mapping evidence describes a fresh server-side provider read, NOT a bearer
capability. Callers must still bind owner, trace and the current reverse receipt
before accepting evidence or authorizing any downstream scientific execution.
This sidecar does not cache, persist, page, fetch structures or authorize tools.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any

from .remote_clients import (
    BoundedJsonClient,
    RemoteSourceError,
    UniProtClient,
    _is_uniprot_accession,
    _normalize_uniprot,
    _resolve_organism,
    _validate_uniprot_record_organism,
)


_CANDIDATE_LIMIT = 5
_MAPPING_REVISION = "uniprot-exact-protein-name/v1"
_RECORD_FAILURES = frozenset({
    "invalid_response", "invalid_entry_version", "conflicting_records",
})
_REMOTE_REASONS = {
    "operation_timeout": "operation_timeout",
    "timeout": "operation_timeout",
    "response_too_large": "response_too_large",
    "invalid_response": "invalid_response",
    "malformed_json": "invalid_response",
}


class _InvalidRecord(ValueError):
    def __init__(self, reason: str = "invalid_response") -> None:
        self.reason = reason if reason in _RECORD_FAILURES else "invalid_response"
        super().__init__(self.reason)


def _result(
    status: str,
    reason: str,
    *,
    target: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "target": target,
        "mapping_evidence": evidence,
        "candidates": candidates if candidates is not None else [],
        "reason": reason,
    }


def _has_controls(value: str) -> bool:
    # Include invisible format controls and invalid surrogate code points.
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)


def _validate_native_json(value: Any) -> None:
    """Reject Python coercions (including subclasses), NaN and non-JSON shapes."""
    kind = type(value)
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise _InvalidRecord()
            _validate_native_json(item)
    elif kind is list:
        for item in value:
            _validate_native_json(item)
    elif kind is float:
        if not math.isfinite(value):
            raise _InvalidRecord()
    elif kind not in (str, int, bool, type(None)):
        raise _InvalidRecord()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _response_digest(payload: dict[str, Any]) -> str:
    # Canonical object keys/whitespace; preserve arrays, text and provider values.
    # Bind the entire bounded response, without returning the raw records.
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _protein_names(record: dict[str, Any]) -> list[tuple[str, str]]:
    """Root protein names only: never genes, component names, or inferred aliases."""
    description = record.get("proteinDescription")
    if type(description) is not dict:
        raise _InvalidRecord()
    names: list[tuple[str, str]] = []

    def add_name(item: Any, path: str) -> None:
        if type(item) is not dict:
            raise _InvalidRecord()
        value = item.get("value")
        if type(value) is not str or not value.strip() or _has_controls(value):
            raise _InvalidRecord()
        names.append((value, "proteinDescription." + path))

    def add_group(group: Any, path: str) -> None:
        if type(group) is not dict:
            raise _InvalidRecord()
        add_name(group.get("fullName"), path + ".fullName")
        short_names = group.get("shortNames", [])
        if type(short_names) is not list:
            raise _InvalidRecord()
        for index, short_name in enumerate(short_names):
            add_name(short_name, f"{path}.shortNames[{index}]")

    if "recommendedName" in description:
        add_group(description["recommendedName"], "recommendedName")
    for field in ("alternativeNames", "submissionNames"):
        groups = description.get(field, [])
        if type(groups) is not list:
            raise _InvalidRecord()
        for index, group in enumerate(groups):
            add_group(group, f"{field}[{index}]")
    if not names:
        raise _InvalidRecord()
    return names


def _entry_version(record: dict[str, Any]) -> int:
    audit = record.get("entryAudit")
    version = audit.get("entryVersion") if type(audit) is dict else None
    if type(version) is not int or version <= 0:
        raise _InvalidRecord("invalid_entry_version")
    return version


class ExactTargetNameResolver:
    """Resolve a literal protein name within a single bounded UniProt response.

    ``resolve(name, organism)`` returns exactly status/target/mapping_evidence/
    candidates/reason. Non-resolved responses have null target and evidence.
    Candidates are provider identities for inspection, not execution authority.

    Evidence v1 has exactly schema_version, mapping_revision, query_name,
    matched_name, matched_field, organism, taxon_id, source, source_record_id,
    source_url, entry_version and response_digest. query_name is outer-trimmed;
    matched_name preserves provider spelling/whitespace. entry_version is the
    positive native integer entryAudit.entryVersion, never a local substitute.
    The SHA-256 digest uses UTF-8 canonical JSON of the complete bounded payload
    (sorted object keys, compact separators, unchanged array order).

    Reasons are fixed enums: exact_name, no_exact_match, multiple_exact_matches,
    candidate_limit, invalid_name, invalid_organism, invalid_response,
    invalid_entry_version, conflicting_records, operation_timeout,
    response_too_large, provider_unavailable, closed.

    The default transport owns its session with existing byte/time limits.
    Injected transports are borrowed and must honor BoundedJsonClient's bounded
    request/deadline contract. close() never closes a borrowed transport.
    """

    def __init__(self, http: BoundedJsonClient | None = None) -> None:
        self._owns_http = http is None
        self._http = BoundedJsonClient() if http is None else http
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_http:
                self._http.close()

    def resolve(self, name: str, organism: str | int) -> dict[str, Any]:
        if self._closed:
            return _result("unavailable", "closed")
        try:
            deadline = self._http._begin_operation()
            if (type(name) is not str or not 1 <= len(name) <= 512
                    or not name.strip() or _has_controls(name)):
                return _result("unavailable", "invalid_name")
            query_name = name.strip()
            try:
                scientific_name, taxon_id = _resolve_organism(organism)
            except RemoteSourceError:
                return _result("unavailable", "invalid_organism")
            self._http._remaining_time("UniProt", deadline)
            escaped = query_name.replace("\\", "\\\\").replace('"', '\\"')
            payload = self._http.request_json(
                "UniProt", "GET", UniProtClient.SEARCH_URL,
                params={
                    "query": f'(protein_name:"{escaped}") AND (organism_id:{taxon_id})',
                    "format": "json",
                    "size": _CANDIDATE_LIMIT,
                    # No fields projection: entryAudit is required for evidence.
                },
                _deadline=deadline,
            )
            self._http._remaining_time("UniProt", deadline)
            result = self._interpret(payload, query_name, scientific_name, taxon_id, deadline)
            self._http._remaining_time("UniProt", deadline)
            return result
        except _InvalidRecord as exc:
            return _result("unavailable", exc.reason)
        except RemoteSourceError as exc:
            return _result("unavailable", _REMOTE_REASONS.get(exc.code, "provider_unavailable"))
        except (TypeError, ValueError, KeyError, RecursionError):
            return _result("unavailable", "invalid_response")
        except Exception:
            # Never propagate/log provider errors, URLs, payloads or credentials.
            return _result("unavailable", "provider_unavailable")

    def _interpret(
        self, payload: Any, query_name: str, scientific_name: str,
        taxon_id: int, deadline: float,
    ) -> dict[str, Any]:
        if type(payload) is not dict or type(payload.get("results")) is not list:
            raise _InvalidRecord()
        records = payload["results"]
        if len(records) > _CANDIDATE_LIMIT:
            raise _InvalidRecord()
        _validate_native_json(payload)
        self._http._remaining_time("UniProt", deadline)
        candidates: list[dict[str, Any]] = []
        exact: list[tuple[dict[str, Any], str, str, int]] = []
        seen: dict[str, str] = {}
        for record in records:
            self._http._remaining_time("UniProt", deadline)
            if type(record) is not dict:
                raise _InvalidRecord()
            accession = record.get("primaryAccession")
            # Validate the original alphabet before case conversion: Unicode
            # expansions (e.g. sharp s -> SS) must not invent a record identity.
            if (type(accession) is not str or not accession.isascii()
                    or not _is_uniprot_accession(accession.upper())):
                raise _InvalidRecord()
            _validate_uniprot_record_organism(record, scientific_name, taxon_id)
            names = _protein_names(record)
            version = _entry_version(record)
            if "entryType" in record and type(record["entryType"]) is not str:
                raise _InvalidRecord()
            self._http._remaining_time("UniProt", deadline)
            target = _normalize_uniprot(record, "exact_protein_name")
            self._http._remaining_time("UniProt", deadline)
            canonical = _canonical_json(record)
            identity = target["uniprot_id"]
            if identity in seen:
                if seen[identity] != canonical:
                    raise _InvalidRecord("conflicting_records")
                continue
            seen[identity] = canonical
            matched = next(((value, path) for value, path in names
                            if value.strip().casefold() == query_name.casefold()), None)
            candidates.append({
                "uniprot_id": identity,
                "protein_name": target["protein_name"],
                "organism": target["organism"],
                "taxon_id": taxon_id,
                "source": target["source"],
                "source_record_id": target["source_record_id"],
                "source_url": target["source_url"],
                "matched_name": matched[0] if matched else None,
            })
            if matched is not None:
                exact.append((target, matched[0], matched[1], version))
        self._http._remaining_time("UniProt", deadline)
        if len(records) == _CANDIDATE_LIMIT:
            return _result("ambiguous", "candidate_limit", candidates=candidates)
        if not exact:
            return _result("not_found", "no_exact_match", candidates=candidates)
        if len(exact) > 1:
            return _result("ambiguous", "multiple_exact_matches", candidates=candidates)
        target, matched_name, matched_field, version = exact[0]
        digest = _response_digest(payload)
        self._http._remaining_time("UniProt", deadline)
        return _result("resolved", "exact_name", target=target, candidates=candidates,
                       evidence={
                           "schema_version": 1,
                           "mapping_revision": _MAPPING_REVISION,
                           "query_name": query_name,
                           "matched_name": matched_name,
                           "matched_field": matched_field,
                           "organism": scientific_name,
                           "taxon_id": taxon_id,
                           "source": target["source"],
                           "source_record_id": target["source_record_id"],
                           "source_url": target["source_url"],
                           "entry_version": version,
                           "response_digest": digest,
                       })


__all__ = ["ExactTargetNameResolver"]
