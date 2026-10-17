"""Exact-entry RCSB contracts, using only scripted HTTP responses."""

import pytest

from src.target_search.remote_clients import RcsbClient, RemoteSourceError
from tests.test_target_search_fallback import (
    _FakeRemoteResponse,
    _FakeRemoteSession,
    _ManualMonotonic,
    _bounded_remote_client,
    _rcsb_graphql_entry,
)


def _search_response(*identifiers, **extra):
    return _FakeRemoteResponse(
        payload={
            "result_set": [{"identifier": value} for value in identifiers],
            **extra,
        }
    )


def _details_response(entry=None):
    if entry is None:
        entry = _rcsb_graphql_entry("6SAM", accession="P06276", ligands=["L3H"])
    return _FakeRemoteResponse(payload={"data": {"entries": [entry]}})


def _default_payload():
    prefix = "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers"
    return {
        "query": {
            "type": "group",
            "logical_operator": "and",
            "nodes": [
                {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {
                        "attribute": f"{prefix}.database_name",
                        "operator": "exact_match",
                        "value": "UniProt",
                    },
                },
                {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {
                        "attribute": f"{prefix}.database_accession",
                        "operator": "exact_match",
                        "value": "P06276",
                    },
                },
                {
                    "type": "terminal",
                    "service": "text",
                    "parameters": {"attribute": "exptl.method", "operator": "exists"},
                },
            ],
        },
        "return_type": "polymer_entity",
        "request_options": {
            "paginate": {"start": 0, "rows": 10},
            "sort": [
                {"sort_by": "score", "direction": "desc"},
                {"sort_by": "rcsb_id", "direction": "asc"},
            ],
        },
    }


@pytest.mark.parametrize("kwargs", [{}, {"pdb_id": None}])
def test_default_search_keeps_existing_payload_and_two_call_shape(kwargs):
    session = _FakeRemoteSession(_search_response("6SAM_1"), _details_response())

    result = RcsbClient(_bounded_remote_client(session)).search("P06276", **kwargs)

    assert [item["structure_id"] for item in result] == ["6SAM"]
    assert [(method, url) for method, url, _ in session.calls] == [
        ("POST", RcsbClient.SEARCH_URL),
        ("POST", RcsbClient.GRAPHQL_URL),
    ]
    assert session.calls[0][2]["json"] == _default_payload()
    assert session.calls[1][2]["json"]["variables"] == {"ids": ["6SAM"]}


@pytest.mark.parametrize("pdb_id", ["6SAM", "6sam", "6sAm"])
def test_exact_search_appends_only_official_entry_terminal_and_normalizes(pdb_id):
    session = _FakeRemoteSession(_search_response("6sam_1"), _details_response())

    result = RcsbClient(_bounded_remote_client(session)).search("P06276", pdb_id=pdb_id)

    expected = _default_payload()
    expected["query"]["nodes"].append(
        {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "attribute": "rcsb_polymer_entity_container_identifiers.entry_id",
                "operator": "exact_match",
                "value": "6SAM",
            },
        }
    )
    assert session.calls[0][2]["json"] == expected
    assert session.calls[1][2]["json"]["variables"] == {"ids": ["6SAM"]}
    assert result == [
        {
            "structure_id": "6SAM",
            "source": "RCSB_PDB",
            "structure_type": "experimental",
            "method": "X-RAY DIFFRACTION",
            "resolution": 1.8,
            "organism": "Homo sapiens",
            "title": "Experimental structure",
            "ligand_evidence": ["L3H"],
            "download_url": "https://files.rcsb.org/download/6SAM.cif",
            "source_url": "https://www.rcsb.org/structure/6SAM",
        }
    ]
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "pdb_id",
    [
        "", "6SA", "6SAM1", "ASAM", "6SA!", "6S M", " 6SAM", "6SAM\n",
        "PDB:6SAM", "pdb_00006sam", "6SAM_1", "../6SAM", "6SAM.cif",
        "https://www.rcsb.org/structure/6SAM", "6sa\u017f", "6sa\u212a", "6a\u00df",
        6, True, [], {},
    ],
)
def test_invalid_raw_pdb_id_is_rejected_before_http(pdb_id):
    session = _FakeRemoteSession()

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session)).search("P06276", pdb_id=pdb_id)

    assert raised.value.code == "invalid_identifier"
    assert raised.value.source == "RCSB_PDB"
    assert raised.value.retryable is False
    assert session.calls == []


@pytest.mark.parametrize(
    "identifiers", [("1ABC_1",), ("6SAM_1", "1ABC_1"), ("6SAM_1", "6SAM_0")]
)
@pytest.mark.parametrize("max_structures", [1, 10])
def test_exact_search_rejects_wrong_or_malformed_hits_before_details(
    identifiers, max_structures
):
    session = _FakeRemoteSession(_search_response(*identifiers))

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session), max_structures=max_structures).search(
            "P06276", pdb_id="6SAM"
        )

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert len(session.calls) == 1


@pytest.mark.parametrize("response", [_search_response(), _FakeRemoteResponse(204)])
def test_exact_search_empty_response_does_not_fetch_details(response):
    session = _FakeRemoteSession(response)

    assert RcsbClient(_bounded_remote_client(session)).search(
        "P06276", pdb_id="6SAM"
    ) == []
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "entry",
    [
        _rcsb_graphql_entry("1ABC", accession="P06276"),
        _rcsb_graphql_entry("6SAM", "2", accession="P06276"),
        _rcsb_graphql_entry("6SAM", accession="P00533"),
        _rcsb_graphql_entry("6SAM", accession="P06276", database_name="PDB"),
        _rcsb_graphql_entry("6SAM", accession="P06276", method=None),
        _rcsb_graphql_entry("6SAM", accession="P06276", organism=None),
    ],
)
def test_exact_search_keeps_actual_polymer_and_experiment_validation(entry):
    session = _FakeRemoteSession(_search_response("6SAM_1"), _details_response(entry))

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session)).search("P06276", pdb_id="6SAM")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert len(session.calls) == 2


def test_exact_search_retains_bounded_paging_and_constraint_on_every_page():
    page = lambda: _search_response("6SAM_1", "6SAM_2", total_count=100)
    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    entry["polymer_entities"].extend(
        _rcsb_graphql_entry("6SAM", "2", accession="P06276")["polymer_entities"]
    )
    session = _FakeRemoteSession(*(page() for _ in range(10)), _details_response(entry))

    result = RcsbClient(_bounded_remote_client(session), max_structures=2).search(
        "P06276", pdb_id="6SAM"
    )

    assert [item["structure_id"] for item in result] == ["6SAM"]
    assert len(session.calls) == 11
    for index, (_, url, options) in enumerate(session.calls[:-1]):
        assert url == RcsbClient.SEARCH_URL
        assert options["json"]["request_options"]["paginate"] == {
            "start": index * 2, "rows": 2
        }
        assert options["json"]["query"]["nodes"][-1]["parameters"] == {
            "attribute": "rcsb_polymer_entity_container_identifiers.entry_id",
            "operator": "exact_match",
            "value": "6SAM",
        }


def test_exact_search_retains_shared_operation_deadline():
    clock = _ManualMonotonic()

    class SlowSearchResponse(_FakeRemoteResponse):
        def close(self):
            super().close()
            clock.advance(0.6)

    session = _FakeRemoteSession(
        SlowSearchResponse(payload={"result_set": [{"identifier": "6SAM_1"}]})
    )
    http = _bounded_remote_client(session, monotonic=clock, operation_timeout=0.5)

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(http).search("P06276", pdb_id="6SAM")

    assert raised.value.code == "operation_timeout"
    assert raised.value.retryable is True
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "methods",
    [
        ["UNKNOWN METHOD"],
        ["THEORETICAL MODEL"],
        ["AB INITIO MODEL"],
        ["OTHER"],
        ["X-RAY DIFFRACTION", "UNKNOWN METHOD"],
        ["THEORETICAL MODEL", "X-RAY DIFFRACTION"],
        ["x-ray diffraction"],
    ],
)
def test_exact_search_rejects_unknown_or_nonexperimental_method_rows(methods):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    entry["exptl"] = [{"method": method} for method in methods]
    session = _FakeRemoteSession(_search_response("6SAM_1"), _details_response(entry))

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session)).search("P06276", pdb_id="6SAM")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "method",
    [
        "ELECTRON CRYSTALLOGRAPHY", "ELECTRON MICROSCOPY", "EPR",
        "FIBER DIFFRACTION", "FLUORESCENCE TRANSFER", "INFRARED SPECTROSCOPY",
        "NEUTRON DIFFRACTION", "POWDER DIFFRACTION", "SOLID-STATE NMR",
        "SOLUTION NMR", "SOLUTION SCATTERING", "X-RAY DIFFRACTION",
    ],
)
def test_exact_search_accepts_dictionary_experimental_method_rows(method):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276", method=method)
    entry["exptl"].append({"method": "SOLUTION SCATTERING"})
    session = _FakeRemoteSession(_search_response("6SAM_1"), _details_response(entry))

    result = RcsbClient(_bounded_remote_client(session)).search("P06276", pdb_id="6SAM")

    assert result[0]["method"] == method


@pytest.mark.parametrize(
    "source_pairs",
    [
        [("Homo sapiens", 9606), ("Mus musculus", 10090)],
        [("Mus musculus", 10090), ("Homo sapiens", 9606)],
        [("Homo sapiens", 10090)],
        [("Mus musculus", 9606)],
        [("Rattus norvegicus", 10116)],
        [("Homo sapiens", 9606), ("Homo sapiens", 10090)],
        [("Homo sapiens", 9606), ("Rattus norvegicus", 10116)],
    ],
    ids=["human-mouse", "mouse-human", "human-wrong-id", "mouse-wrong-id",
         "unsupported-pair", "valid-then-conflicting", "valid-then-unsupported"],
)
def test_exact_search_rejects_mixed_conflicting_or_unsupported_species(source_pairs):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    entry["polymer_entities"][0]["rcsb_entity_source_organism"] = [
        {"ncbi_scientific_name": name, "ncbi_taxonomy_id": taxonomy_id}
        for name, taxonomy_id in source_pairs
    ]
    session = _FakeRemoteSession(_search_response("6SAM_1"), _details_response(entry))

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session)).search("P06276", pdb_id="6SAM")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert len(session.calls) == 2


@pytest.mark.parametrize("pair", [("Homo sapiens", 9606), ("Mus musculus", 10090)])
def test_exact_search_accepts_consistent_supported_species_without_human_default(pair):
    name, taxonomy_id = pair
    entry = _rcsb_graphql_entry(
        "6SAM", accession="P06276", organism=name, taxonomy_id=taxonomy_id
    )
    entry["polymer_entities"][0]["rcsb_entity_source_organism"].append(
        {"ncbi_scientific_name": f" {name} ", "ncbi_taxonomy_id": taxonomy_id}
    )
    session = _FakeRemoteSession(_search_response("6SAM_1"), _details_response(entry))

    result = RcsbClient(_bounded_remote_client(session)).search("P06276", pdb_id="6SAM")

    assert result[0]["organism"] == name


@pytest.mark.parametrize("kwargs", [{}, {"pdb_id": None}])
def test_default_search_preserves_legacy_unknown_method_and_species_reduction(kwargs):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276", method="UNKNOWN METHOD")
    entry["polymer_entities"][0]["rcsb_entity_source_organism"] = [
        {"ncbi_scientific_name": "Homo sapiens", "ncbi_taxonomy_id": 10090},
        {"ncbi_scientific_name": "Mus musculus", "ncbi_taxonomy_id": 9606},
        {"ncbi_scientific_name": "Rattus norvegicus", "ncbi_taxonomy_id": 10116},
    ]
    session = _FakeRemoteSession(_search_response("6SAM_1"), _details_response(entry))

    result = RcsbClient(_bounded_remote_client(session)).search("P06276", **kwargs)

    assert result[0]["method"] == "UNKNOWN METHOD"
    assert result[0]["organism"] == "Homo sapiens"
    assert session.calls[0][2]["json"] == _default_payload()


def test_exact_normalizer_checks_species_across_all_requested_polymer_ids():
    from src.target_search.remote_clients import _normalize_rcsb_graphql_entry

    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    entry["polymer_entities"].extend(
        _rcsb_graphql_entry(
            "6SAM", "2", accession="P06276", organism="Mus musculus", taxonomy_id=10090
        )["polymer_entities"]
    )

    with pytest.raises(RemoteSourceError) as raised:
        _normalize_rcsb_graphql_entry(
            entry, "6SAM", "P06276", {"6SAM_1", "6SAM_2"}, _strict_exact=True
        )

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


def test_shared_experimental_methods_constant_matches_dictionary_non_theoretical_values():
    from src.target_search import remote_clients

    methods = getattr(remote_clients, "_EXPERIMENTAL_METHODS", None)
    assert isinstance(methods, frozenset)
    assert methods == {
        "ELECTRON CRYSTALLOGRAPHY", "ELECTRON MICROSCOPY", "EPR",
        "FIBER DIFFRACTION", "FLUORESCENCE TRANSFER", "INFRARED SPECTROSCOPY",
        "NEUTRON DIFFRACTION", "POWDER DIFFRACTION", "SOLID-STATE NMR",
        "SOLUTION NMR", "SOLUTION SCATTERING", "X-RAY DIFFRACTION",
    }


def _two_entity_search_session(entry, *, split_pages):
    if split_pages:
        pages = [
            _search_response("6SAM_1", total_count=2),
            _search_response("6SAM_2", total_count=2),
        ]
    else:
        pages = [_search_response("6SAM_1", "6SAM_2", total_count=2)]
    return _FakeRemoteSession(*pages, _details_response(entry))


@pytest.mark.parametrize("split_pages", [False, True])
def test_exact_search_rejects_mixed_species_across_all_matched_entities(split_pages):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    entry["polymer_entities"].extend(
        _rcsb_graphql_entry(
            "6SAM", "2", accession="P06276", organism="Mus musculus", taxonomy_id=10090
        )["polymer_entities"]
    )
    session = _two_entity_search_session(entry, split_pages=split_pages)

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(
            _bounded_remote_client(session), max_structures=1 if split_pages else 10
        ).search("P06276", pdb_id="6SAM")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert len(session.calls) == (3 if split_pages else 2)


@pytest.mark.parametrize("split_pages", [False, True])
def test_exact_search_rejects_missing_second_matched_entity(split_pages):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    session = _two_entity_search_session(entry, split_pages=split_pages)

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(
            _bounded_remote_client(session), max_structures=1 if split_pages else 10
        ).search("P06276", pdb_id="6SAM")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert len(session.calls) == (3 if split_pages else 2)


@pytest.mark.parametrize("split_pages", [False, True])
def test_exact_search_accepts_two_consistent_matched_entities(split_pages):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    entry["polymer_entities"].extend(
        _rcsb_graphql_entry("6SAM", "2", accession="P06276")["polymer_entities"]
    )
    session = _two_entity_search_session(entry, split_pages=split_pages)

    result = RcsbClient(
        _bounded_remote_client(session), max_structures=1 if split_pages else 10
    ).search("P06276", pdb_id="6SAM")

    assert [item["structure_id"] for item in result] == ["6SAM"]
    assert result[0]["organism"] == "Homo sapiens"
    assert len(session.calls) == (3 if split_pages else 2)
    assert session.calls[-1][2]["json"]["variables"] == {"ids": ["6SAM"]}


@pytest.mark.parametrize("kwargs", [{}, {"pdb_id": None}])
def test_default_search_still_validates_only_first_matched_entity(kwargs):
    entry = _rcsb_graphql_entry("6SAM", accession="P06276")
    entry["polymer_entities"].extend(
        _rcsb_graphql_entry(
            "6SAM", "2", accession="P00533", organism="Mus musculus", taxonomy_id=10090
        )["polymer_entities"]
    )
    session = _two_entity_search_session(entry, split_pages=False)

    result = RcsbClient(_bounded_remote_client(session)).search("P06276", **kwargs)

    assert result[0]["organism"] == "Homo sapiens"
    assert len(session.calls) == 2
    assert session.calls[0][2]["json"] == _default_payload()
