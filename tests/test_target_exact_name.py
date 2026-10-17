"""Offline R4a contracts; all provider records below are SYNTHETIC fixtures.

They use UniProt JSON field shapes, not measured/verified biological mappings.
No fixture is acceptance evidence for an application or scientific workflow.
"""

from copy import deepcopy
import hashlib
import importlib
import json
import math

import pytest
import requests

from src.target_search.remote_clients import BoundedJsonClient, RemoteSourceError


NAME = "Synthetic binding protein alpha"
ACCESSIONS = ["P12345", "Q12345", "O12345", "P23456", "Q23456", "O23456"]
RESULT_KEYS = {"status", "target", "mapping_evidence", "candidates", "reason"}
EVIDENCE_KEYS = {
    "schema_version", "mapping_revision", "query_name", "matched_name",
    "matched_field", "organism", "taxon_id", "source", "source_record_id",
    "source_url", "entry_version", "response_digest",
}
CANDIDATE_KEYS = {
    "uniprot_id", "protein_name", "organism", "taxon_id", "source",
    "source_record_id", "source_url", "matched_name",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("exact-name tests must never access a network")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked)


def resolver_type():
    # Import INSIDE test calls: missing implementation is a call-phase RED,
    # never a collection failure or an importorskip disguised as success.
    return importlib.import_module("src.target_search.exact_name").ExactTargetNameResolver


def record(accession=ACCESSIONS[0], name=NAME, *, mouse=False):
    return {
        "entryType": "UniProtKB reviewed (Swiss-Prot)",
        "primaryAccession": accession,
        "uniProtkbId": "SYNTHETIC_FIXTURE",
        "organism": {
            "scientificName": "Mus musculus" if mouse else "Homo sapiens",
            "taxonId": 10090 if mouse else 9606,
            "lineage": ["Eukaryota"],
        },
        "proteinDescription": {
            "recommendedName": {"fullName": {"value": name}},
            "alternativeNames": [{
                "fullName": {"value": "Synthetic alternate protein"},
                "shortNames": [{"value": "Synthetic protein short name"}],
            }],
        },
        "genes": [{"geneName": {"value": "SYNTH1"},
                   "synonyms": [{"value": "SYNTH_ALIAS"}]}],
        "entryAudit": {
            "firstPublicDate": "2000-01-01",
            "lastAnnotationUpdateDate": "2025-01-01",
            "lastSequenceUpdateDate": "2000-01-01",
            "entryVersion": 17,
            "sequenceVersion": 1,
        },
        "sequence": {"value": "MTEST", "length": 5},
    }


class BorrowedHttp:
    """Small borrowed transport seam, including the real deadline contract."""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.requests = []
        self.begins = 0
        self.checks = []
        self.now = 10.0
        self.deadline = 40.0
        self.closed = 0

    def _begin_operation(self):
        self.begins += 1
        return self.deadline

    def _remaining_time(self, source, deadline):
        self.checks.append((source, deadline))
        assert source == "UniProt" and deadline == self.deadline
        if self.now >= deadline:
            raise RemoteSourceError(source, "operation_timeout", retryable=True)
        return deadline - self.now

    def request_json(self, source, method, url, **kwargs):
        self.requests.append((source, method, url, kwargs))
        if self.error is not None:
            raise self.error
        return self.payload

    def close(self):
        self.closed += 1


def resolve_records(records, name=NAME, organism="human"):
    http = BorrowedHttp({"results": records})
    result = resolver_type()(http).resolve(name, organism)
    assert_contract(result)
    return result, http


def assert_native(value):
    if type(value) is dict:
        assert all(type(key) is str for key in value)
        for item in value.values():
            assert_native(item)
    elif type(value) is list:
        for item in value:
            assert_native(item)
    else:
        assert type(value) in (str, int, float, bool, type(None))
        if type(value) is float:
            assert math.isfinite(value)


def assert_contract(result):
    assert type(result) is dict and set(result) == RESULT_KEYS
    assert result["status"] in {"resolved", "not_found", "ambiguous", "unavailable"}
    assert type(result["candidates"]) is list and len(result["candidates"]) <= 5
    for candidate in result["candidates"]:
        assert set(candidate) == CANDIDATE_KEYS
    if result["status"] != "resolved":
        assert result["target"] is result["mapping_evidence"] is None
    assert_native(result)
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_exact_primary_is_resolved_with_closed_versioned_evidence_and_shared_deadline():
    source = record()
    result, http = resolve_records([source])
    assert result["status"] == "resolved"
    assert result["reason"] == "exact_name"
    from src.target_search.remote_clients import _normalize_uniprot

    assert result["target"] == _normalize_uniprot(source, "exact_protein_name")
    evidence = result["mapping_evidence"]
    assert set(evidence) == EVIDENCE_KEYS
    assert evidence == {
        "schema_version": 1,
        "mapping_revision": "uniprot-exact-protein-name/v1",
        "query_name": NAME,
        "matched_name": NAME,
        "matched_field": "proteinDescription.recommendedName.fullName",
        "organism": "Homo sapiens",
        "taxon_id": 9606,
        "source": "UniProt",
        "source_record_id": ACCESSIONS[0],
        "source_url": f"https://www.uniprot.org/uniprotkb/{ACCESSIONS[0]}/entry",
        "entry_version": 17,
        "response_digest": "sha256:" + hashlib.sha256(json.dumps(
            {"results": [source]}, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest(),
    }
    assert http.begins == 1 and len(http.checks) >= 4
    assert http.requests == [("UniProt", "GET",
        "https://rest.uniprot.org/uniprotkb/search", {
            "params": {"query": f'(protein_name:"{NAME}") AND (organism_id:9606)',
                       "format": "json", "size": 5},
            "_deadline": http.deadline,
        })]
    assert "sequence" not in json.dumps(result)


@pytest.mark.parametrize("query,path", [
    ("Synthetic alternate protein", "alternativeNames[0].fullName"),
    ("Synthetic protein short name", "alternativeNames[0].shortNames[0]"),
    ("Synthetic recommended short", "recommendedName.shortNames[0]"),
    ("Synthetic submitted protein", "submissionNames[0].fullName"),
])
def test_exact_provider_name_fields(query, path):
    source = record()
    source["proteinDescription"]["recommendedName"]["shortNames"] = [
        {"value": "Synthetic recommended short"}]
    source["proteinDescription"]["submissionNames"] = [
        {"fullName": {"value": "Synthetic submitted protein"}}]
    result, _ = resolve_records([source], query)
    assert result["status"] == "resolved"
    assert result["mapping_evidence"]["matched_name"] == query
    assert result["mapping_evidence"]["matched_field"] == "proteinDescription." + path


def test_case_and_outer_trim_only_preserve_actual_provider_spelling():
    result, _ = resolve_records([record(name="  " + NAME + "  ")], " " + NAME.upper() + " ")
    assert result["status"] == "resolved"
    assert result["mapping_evidence"]["query_name"] == NAME.upper()
    assert result["mapping_evidence"]["matched_name"] == "  " + NAME + "  "


@pytest.mark.parametrize("query", [
    "alpha protein binding Synthetic", "Synthetic binding protein", "SYNTH1",
    "SYNTH_ALIAS", ACCESSIONS[0], "Synthetic  binding protein alpha",
])
def test_no_fuzzy_reordering_gene_alias_or_accession_translation(query):
    result, _ = resolve_records([record()], query)
    assert result["status"] == "not_found" and result["reason"] == "no_exact_match"
    assert result["candidates"][0]["uniprot_id"] == ACCESSIONS[0]
    assert result["candidates"][0]["matched_name"] is None


@pytest.mark.parametrize("organism,mouse,taxon", [
    ("human", False, 9606), (9606, False, 9606), ("Homo sapiens", False, 9606),
    ("mouse", True, 10090), (10090, True, 10090), ("Mus musculus", True, 10090),
])
def test_supported_species_are_real_taxon_constraints(organism, mouse, taxon):
    result, http = resolve_records([record(mouse=mouse)], organism=organism)
    assert result["status"] == "resolved"
    assert result["mapping_evidence"]["taxon_id"] == taxon
    assert f"(organism_id:{taxon})" in http.requests[0][3]["params"]["query"]


@pytest.mark.parametrize("query", [NAME, "unmatched"])
def test_every_record_must_match_requested_species_even_if_name_is_not_exact(query):
    result, _ = resolve_records([record(mouse=True)], query)
    assert result["status"] == "unavailable" and result["reason"] == "invalid_response"
    assert result["candidates"] == []


def test_multiple_exact_accessions_never_prefer_reviewed():
    second = record(ACCESSIONS[1])
    second["entryType"] = "UniProtKB unreviewed (TrEMBL)"
    result, _ = resolve_records([record(), second])
    assert result["status"] == "ambiguous"
    assert result["reason"] == "multiple_exact_matches"
    assert {c["uniprot_id"] for c in result["candidates"]} == set(ACCESSIONS[:2])


@pytest.mark.parametrize("exact_count", [0, 1, 5])
def test_saturation_is_ambiguous_even_with_zero_or_one_exact_match(exact_count):
    records = [record(a, NAME if i < exact_count else "Synthetic other")
               for i, a in enumerate(ACCESSIONS[:5])]
    result, http = resolve_records(records)
    assert result["status"] == "ambiguous" and result["reason"] == "candidate_limit"
    assert len(result["candidates"]) == 5 and len(http.requests) == 1


def test_oversized_results_are_rejected_not_truncated():
    result, _ = resolve_records([record(a) for a in ACCESSIONS])
    assert result["status"] == "unavailable" and result["reason"] == "invalid_response"
    assert result["candidates"] == []


def test_identical_duplicate_accession_is_deduplicated_but_still_counts_for_saturation():
    result, _ = resolve_records([record(), record()])
    assert result["status"] == "resolved" and len(result["candidates"]) == 1
    saturated, _ = resolve_records([record() for _ in range(5)])
    assert saturated["reason"] == "candidate_limit"


@pytest.mark.parametrize("field,value", [
    ("entryType", "UniProtKB unreviewed (TrEMBL)"),
    ("sequence", {"value": "OTHER", "length": 5}),
    ("entryAudit", {"entryVersion": 18}),
    ("proteinDescription", {"recommendedName": {"fullName": {"value": "Other"}}}),
])
def test_conflicting_duplicate_accession_is_unavailable(field, value):
    changed = record()
    changed[field] = value
    result, _ = resolve_records([record(), changed])
    assert result["status"] == "unavailable" and result["reason"] == "conflicting_records"
    assert result["candidates"] == []


class StringSubclass(str):
    pass


@pytest.mark.parametrize("name", [
    None, 1, True, [], {}, StringSubclass(NAME), "", "   ", "x" * 513,
    " " * 512 + "x", "x\n", "\tx", "x\x00y", "x\x7fy", "x\x85y",
    "x\u200by", "x\ud800y",
])
def test_invalid_native_name_is_rejected_before_request(name):
    http = BorrowedHttp({"results": [record()]})
    result = resolver_type()(http).resolve(name, "human")
    assert_contract(result)
    assert result["status"] == "unavailable" and result["reason"] == "invalid_name"
    assert http.requests == []


@pytest.mark.parametrize("name", ["x", "x" * 512, "Synthetic β protein"])
def test_valid_name_length_boundaries_and_unicode(name):
    result, _ = resolve_records([record(name=name)], name)
    assert result["status"] == "resolved"


@pytest.mark.parametrize("organism", [None, True, [], {}, "rat", "9606 OR *", 42])
def test_unsupported_organism_does_not_make_request(organism):
    http = BorrowedHttp({"results": [record()]})
    result = resolver_type()(http).resolve(NAME, organism)
    assert_contract(result)
    assert result["reason"] == "invalid_organism" and http.requests == []


def test_query_escapes_quotes_and_backslashes_without_changing_literal_match():
    name = 'Synthetic \\" protein") OR (organism_id:10090) OR (protein_name:"*'
    result, http = resolve_records([record(name=name)], name)
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    assert result["status"] == "resolved"
    assert http.requests[0][3]["params"]["query"] == (
        f'(protein_name:"{escaped}") AND (organism_id:9606)')


@pytest.mark.parametrize("payload", [None, [], {}, {"results": None},
    {"results": {}}, {"results": [None]}, {"results": ["bad"]}])
def test_malformed_envelope_never_becomes_not_found(payload):
    result = resolver_type()(BorrowedHttp(payload)).resolve(NAME, "human")
    assert_contract(result)
    assert result["status"] == "unavailable" and result["reason"] == "invalid_response"


def test_empty_results_are_not_found():
    result, _ = resolve_records([])
    assert result["status"] == "not_found" and result["candidates"] == []


@pytest.mark.parametrize("accession,folded", [
    ("P1\u00df45", "P1SS45"),  # sharp s expands to two ASCII letters
    ("P1\u017f345", "P1S345"),  # long s
    ("P1\u0131345", "P1I345"),  # dotless i
    ("P1\ufb0045", "P1FF45"),  # ff ligature
    ("P1\ufb034", "P1FFI4"),   # ffi ligature
    ("P1\ufb0645", "P1ST45"),  # st ligature
])
@pytest.mark.parametrize("exact_name", [True, False])
def test_unicode_accession_is_rejected_before_ascii_case_normalization(
    accession, folded, exact_name,
):
    assert not accession.isascii() and accession.upper() == folded
    bad = record(accession, NAME if exact_name else "Synthetic other protein")
    records = [bad] if exact_name else [record(), bad]
    result, _ = resolve_records(records)
    assert result == {
        "status": "unavailable", "target": None, "mapping_evidence": None,
        "candidates": [], "reason": "invalid_response",
    }
    assert folded not in json.dumps(result)


@pytest.mark.parametrize("accession,canonical", [
    ("p12345", "P12345"), ("q9y6k9", "Q9Y6K9"), ("a0a024rbg1", "A0A024RBG1"),
])
def test_ascii_lowercase_accession_still_resolves_to_canonical_identity(accession, canonical):
    result, _ = resolve_records([record(accession)])
    assert result["status"] == "resolved"
    assert result["target"]["uniprot_id"] == canonical
    for identity in [result["target"], result["mapping_evidence"], *result["candidates"]]:
        assert identity["source_record_id"] == canonical
        assert identity["source_url"] == f"https://www.uniprot.org/uniprotkb/{canonical}/entry"


@pytest.mark.parametrize("field,value", [
    ("primaryAccession", None), ("primaryAccession", True),
    ("primaryAccession", StringSubclass("P12345")),
    ("primaryAccession", "P12345/evil"), ("primaryAccession", " P12345 "),
    ("organism", {"scientificName": "Homo sapiens", "taxonId": True}),
    ("organism", {"scientificName": "human", "taxonId": 9606}),
    ("organism", None), ("proteinDescription", None), ("proteinDescription", {}),
    ("proteinDescription", {"recommendedName": {"fullName": {"value": 1}}}),
    ("proteinDescription", {"recommendedName": {"fullName": {"value": ""}}}),
    ("proteinDescription", {"alternativeNames": [None]}),
    ("proteinDescription", {"recommendedName": {"fullName": {"value": NAME},
                                               "shortNames": {"value": "bad"}}}),
    ("genes", [None]), ("genes", [{"geneName": {"value": 123}}]),
    ("entryType", []), ("unrelated", float("nan")),
    ("unrelated", ("tuple",)), ("unrelated", {1: "not a string key"}),
])
def test_all_returned_record_shapes_are_validated(field, value):
    bad = record(ACCESSIONS[1], "not an exact name")
    bad[field] = value
    result, _ = resolve_records([record(), bad])
    assert result["status"] == "unavailable" and result["reason"] == "invalid_response"
    assert result["candidates"] == []


@pytest.mark.parametrize("version", [None, True, False, 0, -1, 17.0, "17"])
def test_entry_version_is_real_positive_native_int_or_fails_closed(version):
    source = record()
    source["entryAudit"]["entryVersion"] = version
    result, _ = resolve_records([source])
    assert result["status"] == "unavailable" and result["reason"] == "invalid_entry_version"


@pytest.mark.parametrize("audit", [None, {}, [], {"sequenceVersion": 1}])
def test_missing_entry_audit_or_version_is_not_invented(audit):
    source = record()
    if audit is None:
        del source["entryAudit"]
    else:
        source["entryAudit"] = audit
    result, _ = resolve_records([source])
    assert result["status"] == "unavailable" and result["reason"] == "invalid_entry_version"


def test_canonical_digest_ignores_key_order_but_binds_full_response():
    original = record()
    shuffled = {key: original[key] for key in reversed(list(original))}
    first, _ = resolve_records([original])
    second, _ = resolve_records([shuffled])
    changed = deepcopy(original)
    changed["sequence"]["value"] = "OTHER"
    third, _ = resolve_records([changed])
    assert first["mapping_evidence"]["response_digest"] == second["mapping_evidence"]["response_digest"]
    assert first["mapping_evidence"]["response_digest"] != third["mapping_evidence"]["response_digest"]


@pytest.mark.parametrize("error,reason", [
    (RuntimeError("synthetic-secret https://invalid.example/private"), "provider_unavailable"),
    (RemoteSourceError("UniProt", "operation_timeout", retryable=True), "operation_timeout"),
    (RemoteSourceError("UniProt", "response_too_large", retryable=False), "response_too_large"),
    (RemoteSourceError("UniProt", "malformed_json", retryable=False), "invalid_response"),
])
def test_transport_errors_are_fixed_enums_without_exception_text(error, reason):
    result = resolver_type()(BorrowedHttp(error=error)).resolve(NAME, "human")
    assert_contract(result)
    assert result["status"] == "unavailable" and result["reason"] == reason
    assert "synthetic-secret" not in json.dumps(result)
    assert "invalid.example" not in json.dumps(result)


def test_close_is_idempotent_and_borrowed_http_is_not_closed():
    http = BorrowedHttp({"results": [record()]})
    resolver = resolver_type()(http)
    resolver.close()
    resolver.close()
    assert http.closed == 0
    result = resolver.resolve(NAME, "human")
    assert_contract(result)
    assert result["reason"] == "closed" and http.requests == []


def test_default_http_is_owned_and_closed_once(monkeypatch):
    cls = resolver_type()
    module = importlib.import_module(cls.__module__)
    http = BorrowedHttp({"results": [record()]})
    monkeypatch.setattr(module, "BoundedJsonClient", lambda: http)
    resolver = cls()
    assert resolver.resolve(NAME, "human")["status"] == "resolved"
    resolver.close()
    resolver.close()
    assert http.closed == 1


@pytest.mark.parametrize("phase", ["request", "validation", "normalization", "digest"])
def test_original_deadline_is_checked_after_every_processing_phase(monkeypatch, phase):
    cls = resolver_type()
    module = importlib.import_module(cls.__module__)
    http = BorrowedHttp({"results": [record()]})
    if phase == "request":
        original = http.request_json

        def late(*args, **kwargs):
            value = original(*args, **kwargs)
            http.now = http.deadline
            return value

        http.request_json = late
    else:
        function = {"validation": "_validate_uniprot_record_organism",
                    "normalization": "_normalize_uniprot", "digest": "_response_digest"}[phase]
        original = getattr(module, function)

        def late(*args, **kwargs):
            value = original(*args, **kwargs)
            http.now = http.deadline
            return value

        monkeypatch.setattr(module, function, late)
    result = cls(http).resolve(NAME, "human")
    assert_contract(result)
    assert result["status"] == "unavailable" and result["reason"] == "operation_timeout"
    assert result["candidates"] == [] and http.begins == 1


class SyntheticResponse:
    status_code = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, body, tick=None):
        self.body = body
        self.tick = tick
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            if self.tick:
                self.tick()
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class SyntheticSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_real_bounded_transport_seam_keeps_official_get_shape_and_byte_budget():
    body = json.dumps({"results": [record()]}).encode()
    response = SyntheticResponse(body)
    session = SyntheticSession(response)
    http = BoundedJsonClient(session=session, max_attempts=1)
    result = resolver_type()(http).resolve(NAME, "human")
    assert_contract(result)
    assert result["status"] == "resolved" and response.closed
    method, url, options = session.calls[0]
    assert method == "GET" and url == "https://rest.uniprot.org/uniprotkb/search"
    assert options["stream"] is True and options["allow_redirects"] is False
    assert options["params"]["size"] == 5 and "fields" not in options["params"]
    oversized = resolver_type()(BoundedJsonClient(
        session=SyntheticSession(SyntheticResponse(body)),
        max_response_bytes=32, max_attempts=1,
    )).resolve(NAME, "human")
    assert oversized["reason"] == "response_too_large"


def test_real_bounded_transport_stream_consumes_original_deadline():
    now = [0.0]

    def tick():
        now[0] += 2.0

    response = SyntheticResponse(json.dumps({"results": [record()]}).encode(), tick)
    http = BoundedJsonClient(session=SyntheticSession(response), max_attempts=1,
                             operation_timeout=1.0, monotonic=lambda: now[0])
    result = resolver_type()(http).resolve(NAME, "human")
    assert_contract(result)
    assert result["reason"] == "operation_timeout" and response.closed
