"""Bounded clients for authoritative public target and structure sources."""

from __future__ import annotations

import json as json_module
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import MappingProxyType
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
    "invalid_request": "the remote request is not permitted",
    "invalid_response": "the remote source returned an invalid response shape",
    "malformed_json": "the remote source returned malformed JSON",
    "operation_timeout": "the remote operation exceeded its time limit",
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
_RCSB_POLYMER_ENTITY_ID_RE = re.compile(r"([0-9][A-Z0-9]{3})_([1-9][0-9]*)")
_SUPPORTED_ORGANISMS = {
    "homo sapiens": ("Homo sapiens", 9606),
    "human": ("Homo sapiens", 9606),
    "9606": ("Homo sapiens", 9606),
    "mus musculus": ("Mus musculus", 10090),
    "mouse": ("Mus musculus", 10090),
    "10090": ("Mus musculus", 10090),
}
_RCSB_CORE_PATH_RE = re.compile(
    r"/rest/v1/core/(?:entry/[0-9][A-Z0-9]{3}|"
    r"polymer_entity/[0-9][A-Z0-9]{3}/[1-9][0-9]*)"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _http_date_delay(
    value: Any, clock: Callable[[], datetime]
) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = clock()
    if not isinstance(now, datetime):
        raise TypeError("clock must return datetime")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()


def _is_allowlisted_request(source: str, method: str, url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or parsed.netloc.casefold() != parsed.hostname.casefold()
    ):
        return False
    host = parsed.hostname.casefold()
    if source == "UniProt":
        return (
            method == "GET"
            and host == "rest.uniprot.org"
            and parsed.path == "/uniprotkb/search"
        )
    if source == "RCSB_PDB":
        if method == "POST" and host == "search.rcsb.org":
            return parsed.path == "/rcsbsearch/v2/query"
        if method == "POST" and host == "data.rcsb.org":
            return parsed.path == "/graphql"
        return (
            method == "GET"
            and host == "data.rcsb.org"
            and _RCSB_CORE_PATH_RE.fullmatch(parsed.path) is not None
        )
    if source == "AlphaFold" and method == "GET" and host == "alphafold.ebi.ac.uk":
        prefix = "/api/prediction/"
        if not parsed.path.startswith(prefix):
            return False
        accession = parsed.path[len(prefix) :]
        return _is_uniprot_accession(accession)
    return False


class _CredentialFreeAuth(requests.auth.AuthBase):
    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        for header in ("Authorization", "Proxy-Authorization", "Cookie"):
            request.headers.pop(header, None)
        return request


_CREDENTIAL_FREE_AUTH = _CredentialFreeAuth()


class RemoteSourceError(RuntimeError):
    """A sanitized, stable failure contract for authoritative source calls."""

    __slots__ = ("source", "code", "retryable", "status_code")

    def __init__(
        self,
        source: str,
        code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        if code not in _ERROR_MESSAGES:
            raise ValueError("unknown remote source error code")
        safe_source = source if source in _OFFICIAL_SOURCES else "Remote"
        safe_status = (
            status_code
            if isinstance(status_code, int) and not isinstance(status_code, bool)
            else None
        )
        message = f"{safe_source}: {_ERROR_MESSAGES[code]} [{code}]"
        if safe_status is not None:
            message += f" (status {safe_status})"
        super().__init__(message)
        object.__setattr__(self, "source", safe_source)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "retryable", bool(retryable))
        object.__setattr__(self, "status_code", safe_status)

    @property
    def __dict__(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "source": self.source,
                "code": self.code,
                "retryable": self.retryable,
                "status_code": self.status_code,
            }
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("RemoteSourceError is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("RemoteSourceError is immutable")

    def __reduce__(self):
        return (
            _restore_remote_source_error,
            (self.source, self.code, self.retryable, self.status_code),
        )


def _restore_remote_source_error(
    source: str, code: str, retryable: bool, status_code: int | None
) -> RemoteSourceError:
    return RemoteSourceError(
        source, code, retryable=retryable, status_code=status_code
    )


def _validate_credential_free_session(session: Any) -> None:
    if getattr(session, "auth", None) is not None:
        raise ValueError("authenticated sessions are not allowed")

    headers = getattr(session, "headers", None)
    if headers is not None:
        if not isinstance(headers, Mapping):
            raise TypeError("session headers must be a mapping")
        sensitive_headers = {
            "authorization",
            "proxy-authorization",
            "cookie",
        }
        if any(str(name).casefold() in sensitive_headers for name in headers):
            raise ValueError("credential-bearing session headers are not allowed")

    cookies = getattr(session, "cookies", None)
    if cookies is not None:
        try:
            has_cookies = len(cookies) > 0
        except TypeError:
            has_cookies = bool(cookies)
        if has_cookies:
            raise ValueError("session cookies are not allowed")

    proxies = getattr(session, "proxies", None)
    if isinstance(proxies, Mapping):
        for proxy_url in proxies.values():
            if not isinstance(proxy_url, str):
                continue
            parsed = urlsplit(proxy_url)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("credential-bearing proxies are not allowed")


def _neutralize_environment_credentials(session: Any) -> None:
    try:
        session.trust_env = False
    except Exception:
        if getattr(session, "trust_env", None) is not False:
            raise ValueError("session must disable environment credentials") from None
    if getattr(session, "trust_env", None) is not False:
        raise ValueError("session must disable environment credentials")


def _revalidate_session_for_request(session: Any, source: str) -> None:
    try:
        _neutralize_environment_credentials(session)
        _validate_credential_free_session(session)
    except (TypeError, ValueError):
        raise RemoteSourceError(source, "invalid_request", retryable=False) from None


class BoundedJsonClient:
    """Small JSON transport with bounded reads and narrowly scoped retries."""

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        operation_timeout: float = 30.0,
        timeout: tuple[float, float] = (3.05, 10.0),
        max_attempts: int = 3,
        backoff_base: float = 0.25,
        backoff_ceiling: float = 2.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self._owns_session = session is None
        if session is None:
            session = requests.Session()
        if not callable(getattr(session, "request", None)):
            raise TypeError("session must provide request()")
        _neutralize_environment_credentials(session)
        _validate_credential_free_session(session)
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(monotonic):
            raise TypeError("monotonic must be callable")
        if (
            isinstance(operation_timeout, bool)
            or not isinstance(operation_timeout, (int, float))
            or not math.isfinite(float(operation_timeout))
            or operation_timeout <= 0
        ):
            raise ValueError("operation_timeout must be a positive finite number")
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
        self._clock = clock or _utc_now
        self._monotonic = monotonic
        self._operation_timeout = float(operation_timeout)
        self._timeout = (float(timeout[0]), float(timeout[1]))
        self._max_attempts = max_attempts
        self._backoff_base = float(backoff_base)
        self._backoff_ceiling = float(backoff_ceiling)
        self._max_response_bytes = max_response_bytes
        self._request_lock = threading.RLock()
        self._closed = False

    def request_json(
        self,
        source: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        _deadline: float | None = None,
    ) -> Any | None:
        deadline = self._begin_operation() if _deadline is None else _deadline
        with self._request_lock:
            if self._closed:
                raise RuntimeError("bounded JSON client is closed")
            result = self._request_json_locked(
                source, method, url, params=params, json=json, deadline=deadline
            )
            self._remaining_time(source, deadline)
            return result

    def _request_json_locked(
        self,
        source: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        deadline: float,
    ) -> Any | None:
        if source not in _OFFICIAL_SOURCES:
            raise ValueError("source must be an authoritative provider")
        normalized_method = method.upper() if isinstance(method, str) else ""
        if not _is_allowlisted_request(source, normalized_method, url):
            raise RemoteSourceError(source, "invalid_request", retryable=False)
        self._remaining_time(source, deadline)
        request_options: dict[str, Any] = {
            "headers": {
                "Accept": "application/json",
                "User-Agent": STABLE_USER_AGENT,
            },
            "auth": _CREDENTIAL_FREE_AUTH,
            "allow_redirects": False,
            "stream": True,
        }
        if params is not None:
            request_options["params"] = dict(params)
        if json is not None:
            request_options["json"] = dict(json)

        for attempt in range(1, self._max_attempts + 1):
            _revalidate_session_for_request(self._session, source)
            remaining = self._remaining_time(source, deadline)
            attempt_options = dict(request_options)
            attempt_options["timeout"] = (
                min(self._timeout[0], remaining),
                min(self._timeout[1], remaining),
            )
            try:
                response = self._session.request(
                    normalized_method, url, **attempt_options
                )
            except requests.Timeout:
                self._remaining_time(source, deadline)
                if attempt == self._max_attempts:
                    raise RemoteSourceError(
                        source, "timeout", retryable=True
                    ) from None
                self._sleep_with_deadline(
                    source, self._backoff_delay(attempt), deadline
                )
                continue
            except requests.ConnectionError:
                self._remaining_time(source, deadline)
                if attempt == self._max_attempts:
                    raise RemoteSourceError(
                        source, "connection_failure", retryable=True
                    ) from None
                self._sleep_with_deadline(
                    source, self._backoff_delay(attempt), deadline
                )
                continue
            except requests.RequestException:
                self._remaining_time(source, deadline)
                raise RemoteSourceError(
                    source, "request_failure", retryable=False
                ) from None

            retry_delay: float | None = None
            transport_error_code: str | None = None
            try:
                self._remaining_time(source, deadline)
                status = getattr(response, "status_code", None)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise RemoteSourceError(
                        source, "invalid_response", retryable=False
                    )
                if status in (204, 404):
                    return None
                if status == 429 or 500 <= status <= 599:
                    code = "provider_unavailable"
                    if attempt == self._max_attempts:
                        raise RemoteSourceError(
                            source, code, retryable=True, status_code=status
                        )
                    retry_delay = self._retry_delay(response, attempt)
                elif 400 <= status <= 499:
                    raise RemoteSourceError(
                        source,
                        "http_client_error",
                        retryable=False,
                        status_code=status,
                    )
                elif not 200 <= status <= 299:
                    raise RemoteSourceError(
                        source,
                        "unexpected_status",
                        retryable=False,
                        status_code=status,
                    )
                else:
                    raw = self._read_bounded(
                        response, source, status, deadline
                    )
                    try:
                        return json_module.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json_module.JSONDecodeError):
                        raise RemoteSourceError(
                            source,
                            "malformed_json",
                            retryable=False,
                            status_code=status,
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
                self._remaining_time(source, deadline)
                if attempt == self._max_attempts:
                    raise RemoteSourceError(
                        source, transport_error_code, retryable=True
                    ) from None
                self._sleep_with_deadline(
                    source, self._backoff_delay(attempt), deadline
                )
                continue
            if retry_delay is not None:
                self._sleep_with_deadline(source, retry_delay, deadline)

        raise AssertionError("bounded retry loop exhausted without a result")

    def close(self) -> None:
        with self._request_lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_session:
                close = getattr(self._session, "close", None)
                if callable(close):
                    close()

    def __enter__(self) -> BoundedJsonClient:
        if self._closed:
            raise RuntimeError("bounded JSON client is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _read_bounded(
        self, response: Any, source: str, status: int, deadline: float
    ) -> bytes:
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
                    status_code=status,
                )

        iter_content = getattr(response, "iter_content", None)
        if not callable(iter_content):
            raise RemoteSourceError(
                source, "invalid_response", retryable=False, status_code=status
            )
        chunks: list[bytes] = []
        total = 0
        iterator = iter(
            iter_content(chunk_size=min(8192, self._max_response_bytes + 1))
        )
        while True:
            self._remaining_time(source, deadline)
            try:
                chunk = next(iterator)
            except StopIteration:
                self._remaining_time(source, deadline)
                break
            self._remaining_time(source, deadline)
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise RemoteSourceError(
                    source, "invalid_response", retryable=False, status_code=status
                )
            total += len(chunk)
            if total > self._max_response_bytes:
                raise RemoteSourceError(
                    source,
                    "response_too_large",
                    retryable=False,
                    status_code=status,
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _begin_operation(self) -> float:
        return self._monotonic_now() + self._operation_timeout

    def _monotonic_now(self) -> float:
        value = self._monotonic()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise TypeError("monotonic must return a finite number")
        return float(value)

    def _remaining_time(self, source: str, deadline: float) -> float:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(float(deadline))
        ):
            raise TypeError("deadline must be a finite number")
        remaining = float(deadline) - self._monotonic_now()
        if remaining <= 0:
            raise RemoteSourceError(
                source, "operation_timeout", retryable=True
            )
        return remaining

    def _sleep_with_deadline(
        self, source: str, delay: float, deadline: float
    ) -> None:
        remaining = self._remaining_time(source, deadline)
        self._sleep(min(delay, remaining))

    def _backoff_delay(self, attempt: int) -> float:
        return min(self._backoff_base * (2 ** (attempt - 1)), self._backoff_ceiling)

    def _retry_delay(self, response: Any, attempt: int) -> float:
        headers = getattr(response, "headers", {})
        value = headers.get("Retry-After") if isinstance(headers, Mapping) else None
        if value is not None:
            try:
                retry_after = float(value)
            except (TypeError, ValueError):
                retry_after = _http_date_delay(value, self._clock)
            if retry_after is not None and math.isfinite(retry_after):
                return min(max(retry_after, 0.0), self._backoff_ceiling)
        return self._backoff_delay(attempt)


class _SourceClientContext:
    _http: BoundedJsonClient

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class UniProtClient(_SourceClientContext):
    SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"

    def __init__(self, http: BoundedJsonClient) -> None:
        self._http = http

    def resolve(
        self, query: str, organism: str | int = "Homo sapiens"
    ) -> dict[str, Any] | None:
        deadline = self._http._begin_operation()
        normalized_query = _validate_gene_or_accession(query, "UniProt")
        scientific_name, taxon_id = _resolve_organism(organism)
        is_accession = _is_uniprot_accession(normalized_query)
        field = "accession" if is_accession else "gene_exact"
        payload = self._http.request_json(
            "UniProt",
            "GET",
            self.SEARCH_URL,
            params={
                "query": f"({field}:{normalized_query}) AND (organism_id:{taxon_id})",
                "format": "json",
                "size": 5,
                "fields": "accession,id,gene_names,protein_name,organism_name,organism_id,reviewed",
            },
            _deadline=deadline,
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
            _, gene_names, aliases = _uniprot_genes(record)
            if is_accession:
                if accession.upper() == normalized_query:
                    _validate_uniprot_record_organism(
                        record, scientific_name, taxon_id
                    )
                    ranked.append((0, record, "exact_accession"))
            elif any(
                gene_name.casefold() == normalized_query.casefold()
                for gene_name in gene_names
            ):
                _validate_uniprot_record_organism(record, scientific_name, taxon_id)
                reviewed = _is_reviewed_uniprot_record(record)
                ranked.append(
                    (
                        0 if reviewed else 1,
                        record,
                        "exact_gene_reviewed" if reviewed else "exact_gene",
                    )
                )
            elif any(alias.casefold() == normalized_query.casefold() for alias in aliases):
                _validate_uniprot_record_organism(record, scientific_name, taxon_id)
                reviewed = _is_reviewed_uniprot_record(record)
                ranked.append(
                    (
                        2 if reviewed else 3,
                        record,
                        "exact_alias_reviewed" if reviewed else "exact_alias",
                    )
                )

        if not ranked:
            return None
        best_rank = min(item[0] for item in ranked)
        best = [item for item in ranked if item[0] == best_rank]
        unique_accessions = {item[1]["primaryAccession"].upper() for item in best}
        if len(unique_accessions) != 1:
            raise RemoteSourceError("UniProt", "ambiguous_match", retryable=False)
        _, selected, match_reason = best[0]
        return _normalize_uniprot(selected, match_reason)


class RcsbClient(_SourceClientContext):
    SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
    GRAPHQL_URL = "https://data.rcsb.org/graphql"
    _SEARCH_PAGE_LIMIT = 10
    _ENTRY_QUERY = """
query Entries($ids: [String!]!) {
  entries(entry_ids: $ids) {
    rcsb_id
    struct { title }
    exptl { method }
    rcsb_entry_info { resolution_combined nonpolymer_bound_components }
    polymer_entities {
      rcsb_id
      rcsb_polymer_entity_container_identifiers {
        entry_id
        entity_id
        reference_sequence_identifiers {
          database_name
          database_accession
        }
      }
      rcsb_entity_source_organism {
        ncbi_scientific_name
        ncbi_taxonomy_id
      }
    }
  }
}
""".strip()

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
        deadline = self._http._begin_operation()
        accession = _validate_uniprot_accession(uniprot_accession, "RCSB_PDB")
        selected_polymer_ids: dict[str, str] = {}
        start = 0
        scanned_hits = 0
        search_hit_limit = self._max_structures * self._SEARCH_PAGE_LIMIT
        while (
            len(selected_polymer_ids) < self._max_structures
            and scanned_hits < search_hit_limit
        ):
            rows = min(self._max_structures, search_hit_limit - scanned_hits)
            payload = self._http.request_json(
                "RCSB_PDB",
                "POST",
                self.SEARCH_URL,
                json=self._search_request(accession, start=start, rows=rows),
                _deadline=deadline,
            )
            self._http._remaining_time("RCSB_PDB", deadline)
            if payload is None:
                break
            if not isinstance(payload, dict) or not isinstance(
                payload.get("result_set"), list
            ):
                raise RemoteSourceError(
                    "RCSB_PDB", "invalid_response", retryable=False
                )
            total_count = payload.get("total_count")
            if total_count is not None and (
                isinstance(total_count, bool)
                or not isinstance(total_count, int)
                or total_count < 0
            ):
                raise RemoteSourceError(
                    "RCSB_PDB", "invalid_response", retryable=False
                )
            page = payload["result_set"]
            if not page:
                break
            for item in page:
                if not isinstance(item, dict) or not isinstance(
                    item.get("identifier"), str
                ):
                    raise RemoteSourceError(
                        "RCSB_PDB", "invalid_response", retryable=False
                    )
                identifier = item["identifier"].upper()
                match = _RCSB_POLYMER_ENTITY_ID_RE.fullmatch(identifier)
                if match is None:
                    raise RemoteSourceError(
                        "RCSB_PDB", "invalid_response", retryable=False
                    )
                selected_polymer_ids.setdefault(match.group(1), identifier)
                if len(selected_polymer_ids) == self._max_structures:
                    break
            scanned_hits += len(page)
            start += len(page)
            if len(page) < rows or (
                total_count is not None and start >= total_count
            ):
                break

        if not selected_polymer_ids:
            return []
        entry_ids = sorted(selected_polymer_ids)
        self._http._remaining_time("RCSB_PDB", deadline)
        details = self._http.request_json(
            "RCSB_PDB",
            "POST",
            self.GRAPHQL_URL,
            json={"query": self._ENTRY_QUERY, "variables": {"ids": entry_ids}},
            _deadline=deadline,
        )
        self._http._remaining_time("RCSB_PDB", deadline)
        if details is None or not isinstance(details, dict) or "errors" in details:
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        data = details.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)

        entries_by_id: dict[str, dict[str, Any]] = {}
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            entry_id = entry.get("rcsb_id")
            if (
                not isinstance(entry_id, str)
                or entry_id not in entry_ids
                or entry_id in entries_by_id
            ):
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            entries_by_id[entry_id] = entry
        if set(entries_by_id) != set(entry_ids):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)

        return [
            _normalize_rcsb_graphql_entry(
                entries_by_id[entry_id],
                entry_id,
                accession,
                {selected_polymer_ids[entry_id]},
            )
            for entry_id in entry_ids
        ]

    @staticmethod
    def _search_request(
        accession: str, *, start: int, rows: int
    ) -> dict[str, Any]:
        return {
            "query": {
                "type": "group",
                "logical_operator": "and",
                "nodes": [
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_name",
                            "operator": "exact_match",
                            "value": "UniProt",
                        },
                    },
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                            "operator": "exact_match",
                            "value": accession,
                        },
                    },
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": "exptl.method",
                            "operator": "exists",
                        },
                    },
                ],
            },
            "return_type": "polymer_entity",
            "request_options": {
                "paginate": {"start": start, "rows": rows},
                "sort": [
                    {"sort_by": "score", "direction": "desc"},
                    {"sort_by": "rcsb_id", "direction": "asc"},
                ],
            },
        }


class AlphaFoldClient(_SourceClientContext):
    PREDICTION_URL = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"

    def __init__(self, http: BoundedJsonClient) -> None:
        self._http = http

    def lookup(self, accession: str) -> dict[str, Any] | None:
        deadline = self._http._begin_operation()
        normalized_accession = _validate_uniprot_accession(accession, "AlphaFold")
        payload = self._http.request_json(
            "AlphaFold",
            "GET",
            self.PREDICTION_URL.format(accession=normalized_accession),
            _deadline=deadline,
        )
        if payload is None:
            return None
        if not isinstance(payload, list):
            raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
        if not payload:
            return None

        matching: list[tuple[int, dict[str, Any]]] = []
        for record in payload:
            if not isinstance(record, dict):
                raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
            record_accession = record.get("uniprotAccession")
            if not isinstance(record_accession, str):
                raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
            if record_accession.strip().upper() != normalized_accession:
                continue
            version = _validate_alphafold_record(record, normalized_accession)
            matching.append((version, record))
        if not matching:
            return None
        latest_version = max(version for version, _ in matching)
        latest = [record for version, record in matching if version == latest_version]
        selected = sorted(latest, key=_alphafold_record_sort_key)[0]
        return _normalize_alphafold(selected, normalized_accession, latest_version)


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


def _resolve_organism(value: str | int) -> tuple[str, int]:
    if isinstance(value, bool):
        raise RemoteSourceError("UniProt", "invalid_identifier", retryable=False)
    if isinstance(value, int):
        key = str(value)
    elif isinstance(value, str):
        key = " ".join(value.strip().split()).casefold()
    else:
        raise RemoteSourceError("UniProt", "invalid_identifier", retryable=False)
    resolved = _SUPPORTED_ORGANISMS.get(key)
    if resolved is None:
        raise RemoteSourceError("UniProt", "invalid_identifier", retryable=False)
    return resolved


def _uniprot_genes(
    record: dict[str, Any]
) -> tuple[str | None, list[str], list[str]]:
    genes = record.get("genes", [])
    if not isinstance(genes, list):
        raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
    primary: str | None = None
    gene_names: set[str] = set()
    aliases: set[str] = set()
    for gene in genes:
        if not isinstance(gene, dict):
            raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
        gene_name = gene.get("geneName")
        if gene_name is not None:
            if not isinstance(gene_name, dict) or not isinstance(gene_name.get("value"), str):
                raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
            value = gene_name["value"].strip()
            if value:
                gene_names.add(value)
                if primary is None:
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
        aliases.update(
            value for value in gene_names if value.casefold() != primary.casefold()
        )
        aliases = {
            alias for alias in aliases if alias.casefold() != primary.casefold()
        }
    ordered_names = sorted(gene_names, key=lambda item: (item.casefold(), item))
    ordered_aliases = sorted(aliases, key=lambda item: (item.casefold(), item))
    return primary, ordered_names, ordered_aliases


def _is_reviewed_uniprot_record(record: dict[str, Any]) -> bool:
    entry_type = record.get("entryType")
    if entry_type is None:
        return False
    if not isinstance(entry_type, str):
        raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
    return entry_type == "UniProtKB reviewed (Swiss-Prot)"


def _validate_uniprot_record_organism(
    record: dict[str, Any], expected_name: str, expected_taxon_id: int
) -> None:
    organism = record.get("organism")
    if not isinstance(organism, dict):
        raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
    scientific_name = organism.get("scientificName")
    taxon_id = organism.get("taxonId")
    if (
        not isinstance(scientific_name, str)
        or isinstance(taxon_id, bool)
        or not isinstance(taxon_id, int)
        or taxon_id <= 0
    ):
        raise RemoteSourceError("UniProt", "invalid_response", retryable=False)
    if scientific_name != expected_name or taxon_id != expected_taxon_id:
        raise RemoteSourceError("UniProt", "invalid_response", retryable=False)


def _normalize_uniprot(record: dict[str, Any], match_reason: str) -> dict[str, Any]:
    accession = record["primaryAccession"].upper()
    gene_name, _, aliases = _uniprot_genes(record)
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


def _normalize_rcsb_graphql_entry(
    entry: Any,
    requested_id: str,
    requested_accession: str,
    requested_polymer_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    returned_id = entry.get("rcsb_id")
    if not isinstance(returned_id, str) or returned_id != requested_id:
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
    if not isinstance(experiments, list) or not experiments:
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    for experiment in experiments:
        if not isinstance(experiment, dict):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        value = experiment.get("method")
        if not isinstance(value, str) or not value.strip():
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        if method is None:
            method = value.strip()

    polymer_entities = entry.get("polymer_entities")
    if not isinstance(polymer_entities, list):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    matched_polymer_ids: set[str] = set()
    organisms: set[str] = set()
    for entity in polymer_entities:
        if not isinstance(entity, dict):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        polymer_id = entity.get("rcsb_id")
        if not isinstance(polymer_id, str):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        if polymer_id not in requested_polymer_ids:
            continue
        match = _RCSB_POLYMER_ENTITY_ID_RE.fullmatch(polymer_id)
        identifiers = entity.get("rcsb_polymer_entity_container_identifiers")
        if match is None or not isinstance(identifiers, dict):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        if (
            identifiers.get("entry_id") != requested_id
            or identifiers.get("entity_id") != match.group(2)
        ):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        references = identifiers.get("reference_sequence_identifiers")
        if not isinstance(references, list) or not references:
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        if any(
            not isinstance(reference, dict)
            or not isinstance(reference.get("database_name"), str)
            or not reference["database_name"].strip()
            or not isinstance(reference.get("database_accession"), str)
            or not reference["database_accession"].strip()
            for reference in references
        ) or not any(
            reference["database_name"] == "UniProt"
            and reference["database_accession"] == requested_accession
            for reference in references
        ):
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        sources = entity.get("rcsb_entity_source_organism")
        if not isinstance(sources, list) or not sources:
            raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
        for source in sources:
            if not isinstance(source, dict):
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            scientific_name = source.get("ncbi_scientific_name")
            taxonomy_id = source.get("ncbi_taxonomy_id")
            if (
                not isinstance(scientific_name, str)
                or not scientific_name.strip()
                or isinstance(taxonomy_id, bool)
                or not isinstance(taxonomy_id, int)
                or taxonomy_id <= 0
            ):
                raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
            organisms.add(scientific_name.strip())
        matched_polymer_ids.add(polymer_id)
    if matched_polymer_ids != requested_polymer_ids or not organisms:
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)

    info = entry.get("rcsb_entry_info", {})
    if not isinstance(info, dict):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    resolution = _optional_resolution(info)
    ligands = _optional_string_list(
        info, "nonpolymer_bound_components", "RCSB_PDB"
    )
    return {
        "structure_id": requested_id,
        "source": "RCSB_PDB",
        "structure_type": "experimental",
        "method": method,
        "resolution": resolution,
        "organism": sorted(organisms, key=lambda item: (item.casefold(), item))[0],
        "title": title,
        "ligand_evidence": ligands,
        "download_url": f"https://files.rcsb.org/download/{requested_id}.cif",
        "source_url": f"https://www.rcsb.org/structure/{requested_id}",
    }


def _optional_resolution(info: dict[str, Any]) -> float | None:
    values = info.get("resolution_combined")
    if values is None:
        return None
    if not isinstance(values, list) or not values:
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
        for value in values
    ):
        raise RemoteSourceError("RCSB_PDB", "invalid_response", retryable=False)
    return float(values[0])


def _optional_string_list(
    mapping: dict[str, Any], key: str, source: str
) -> list[str]:
    values = mapping.get(key)
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise RemoteSourceError(source, "invalid_response", retryable=False)
    return sorted({value.strip() for value in values if value.strip()})


def _validate_alphafold_record(record: dict[str, Any], accession: str) -> int:
    expected_id = f"AF-{accession}-F1"
    entry_id = record.get("entryId")
    model_entity_id = record.get("modelEntityId")
    version = record.get("latestVersion")
    if (
        not isinstance(entry_id, str)
        or entry_id.upper() != expected_id
        or not isinstance(model_entity_id, str)
        or model_entity_id.upper() != expected_id
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version <= 0
    ):
        raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
    pdb_url = _optional_alphafold_url(record, "pdbUrl", expected_id, "pdb", version)
    cif_url = _optional_alphafold_url(record, "cifUrl", expected_id, "cif", version)
    if pdb_url is None and cif_url is None:
        raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
    return version


def _alphafold_record_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    pdb_url = record.get("pdbUrl")
    cif_url = record.get("cifUrl")
    return (
        -(int(isinstance(pdb_url, str)) + int(isinstance(cif_url, str))),
        str(cif_url or ""),
        str(pdb_url or ""),
        str(record.get("uniprotDescription") or ""),
        str(record.get("organismScientificName") or ""),
    )


def _normalize_alphafold(
    record: dict[str, Any], accession: str, version: int
) -> dict[str, Any]:
    entry_id = record["entryId"].upper()
    model_id = record["modelEntityId"].upper()
    pdb_url = _optional_alphafold_url(record, "pdbUrl", entry_id, "pdb", version)
    cif_url = _optional_alphafold_url(record, "cifUrl", entry_id, "cif", version)
    if pdb_url is None and cif_url is None:
        raise RemoteSourceError("AlphaFold", "invalid_response", retryable=False)
    title = _optional_text(record, "uniprotDescription", "AlphaFold")
    organism = _optional_text(record, "organismScientificName", "AlphaFold")
    return {
        "structure_id": entry_id,
        "model_id": model_id,
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


def _optional_alphafold_url(
    record: dict[str, Any],
    key: str,
    entry_id: str,
    extension: str,
    version: int,
) -> str | None:
    value = _optional_text(record, key, "AlphaFold")
    if value is None:
        return None
    parsed = urlsplit(value)
    expected_path = re.compile(
        rf"/files/{re.escape(entry_id)}-model_v{version}\.{extension}"
    )
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.hostname != "alphafold.ebi.ac.uk"
        or parsed.netloc != parsed.hostname
        or parsed.query
        or parsed.fragment
        or expected_path.fullmatch(parsed.path) is None
    ):
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
