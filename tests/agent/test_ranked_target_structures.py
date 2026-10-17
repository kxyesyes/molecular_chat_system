"""Synthetic provider payloads exercise real bounded resolvers, never biology."""
from copy import deepcopy
import importlib
import importlib.util

import pytest
import requests

from src.target_search.remote_clients import RemoteSourceError
from tests.agent.test_reverse_target_rank_followup import ranked, service
from tests.test_target_exact_name import record
from tests.test_target_search_fallback import _rcsb_graphql_entry


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send',
        lambda *a, **kw: pytest.fail('only synthetic transport responses are allowed'))


class Http:
    def __init__(self):
        self.calls, self.begins, self.closed = [], 0, 0
        self.now = 0
        self.records = [record(name='Synthetic A')]
        self.entry = _rcsb_graphql_entry('1ABC', accession='P12345')
        self.after = None

    def _begin_operation(self):
        self.begins += 1
        return self.now + 30

    def _remaining_time(self, source, deadline):
        if self.now >= deadline:
            raise RemoteSourceError(source, 'operation_timeout', retryable=True)
        return deadline - self.now

    def request_json(self, source, method, url, **kwargs):
        self._remaining_time(source, kwargs['_deadline'])
        self.calls.append((source, url, deepcopy(kwargs)))
        if source == 'UniProt':
            result = {'results': deepcopy(self.records)}
        elif 'graphql' in url:
            result = {'data': {'entries': [deepcopy(self.entry)]}}
        else:
            result = {'total_count': 1, 'result_set': [{'identifier': '1ABC_1'}]}
        if self.after:
            self.after()
        return result

    def close(self):
        self.closed += 1


def resolve(case, http, *, session='owner-session'):
    module = 'src.web.ranked_target_structures'
    assert importlib.util.find_spec(module), 'rank-bound structure resolver is missing'
    boundary = service(case)
    context = boundary.capture(case.agent, session_id='owner-session')
    return importlib.import_module(module).resolve_ranked_structures(
        boundary, context, session_id=session, http=http)


def test_name_key_maps_exact_species_and_structure_without_science_execution(ranked, monkeypatch):
    http = Http()
    for method in ('predict_with_receipt', 'initialize_strict', 'load'):
        monkeypatch.setattr(ranked.producer, method, lambda *a, **kw: pytest.fail('unexpected science execution'))
    result = resolve(ranked, http)
    assert result['status'] == 'resolved'
    assert result['ranked_record']['record'] == ranked.data[0]
    assert result['target']['uniprot_id'] == 'P12345'
    assert result['mapping_evidence']['entry_version'] == 17
    assert result['structures'][0]['structure_id'] == '1ABC'
    assert result['input_ready'] is False and result['docking_started'] is False
    assert http.begins == 1 and http.closed == 0
    assert all(call[2]['_deadline'] == 30 for call in http.calls)
    assert 'protein_name:' in http.calls[0][2]['params']['query']
    assert 'name-sha256' not in str(http.calls)


@pytest.mark.parametrize('mutation', ['none', 'ambiguous', 'wrong_species', 'wrong_name'])
def test_nonunique_mapping_never_reaches_structure_provider(ranked, mutation):
    http = Http()
    if mutation == 'none': http.records = []
    elif mutation == 'ambiguous': http.records.append(record(accession='Q12345', name='Synthetic A'))
    elif mutation == 'wrong_species': http.records = [record(name='Synthetic A', mouse=True)]
    else: http.records = [record(name='Similar but different')]
    result = resolve(ranked, http)
    assert result['status'] != 'resolved'
    assert result['structures'] == [] and result['target'] is None
    assert len(http.calls) == 1 and http.closed == 0


@pytest.mark.parametrize('mutation', ['wrong_accession', 'wrong_species', 'wrong_taxon', 'mixed_species'])
def test_invalid_structure_binding_cannot_publish(ranked, mutation):
    http = Http()
    entity = http.entry['polymer_entities'][0]
    if mutation == 'wrong_accession':
        entity['rcsb_polymer_entity_container_identifiers']['reference_sequence_identifiers'][0]['database_accession'] = 'Q12345'
    elif mutation == 'wrong_species':
        entity['rcsb_entity_source_organism'][0]['ncbi_scientific_name'] = 'Mus musculus'
    elif mutation == 'wrong_taxon':
        entity['rcsb_entity_source_organism'][0]['ncbi_taxonomy_id'] = 10090
    else:
        entity['rcsb_entity_source_organism'].append({'ncbi_scientific_name': 'Mus musculus', 'ncbi_taxonomy_id': 10090})
    result = resolve(ranked, http)
    assert result['status'] != 'resolved' and result['structures'] == []


def test_cross_owner_does_not_call_provider(ranked):
    http = Http()
    result = resolve(ranked, http, session='foreign')
    assert result['status'] != 'resolved' and not http.calls


def test_retirement_during_provider_lookup_does_not_publish(ranked):
    http = Http()
    http.after = ranked.producer.close_strict
    result = resolve(ranked, http)
    assert result['status'] != 'resolved' and result['structures'] == []


def test_all_provider_operations_keep_original_deadline(ranked):
    http = Http()
    http.after = lambda: setattr(http, 'now', http.now + 11)
    result = resolve(ranked, http)
    assert result['status'] != 'resolved' and result['structures'] == []
    assert http.begins == 1


@pytest.mark.parametrize('ranked', [{'count': 2, 'organism': 'Mouse'}], indirect=True)
def test_mouse_rank_keeps_species_during_mapping_and_structure_lookup(ranked):
    http = Http()
    http.records = [record(name='Synthetic A', mouse=True)]
    http.entry = _rcsb_graphql_entry('1ABC', accession='P12345', organism='Mus musculus', taxonomy_id=10090)
    result = resolve(ranked, http)
    assert result['status'] == 'resolved'
    assert result['target']['organism'] == 'Mus musculus'
    assert all('10090' in call[2]['params']['query'] for call in http.calls if call[0] == 'UniProt')


@pytest.mark.parametrize('ranked', [{'count': 1, 'identifier': 'P12345'}], indirect=True)
def test_existing_accession_is_not_discarded_for_name_guess(ranked):
    http = Http()
    result = resolve(ranked, http)
    assert result['status'] == 'resolved'
    assert result['mapping_evidence'] is None
    assert 'accession:P12345' in http.calls[0][2]['params']['query']
    assert 'protein_name:' not in str(http.calls)


@pytest.mark.parametrize('ranked', [{'count': 1, 'identifier': value} for value in
    ['CHEMBL1234', 'P12345-2', 'name-sha256:wrong', 'P1ß45']], indirect=True)
def test_unsupported_existing_identifier_requires_mapping_not_name_fallback(ranked):
    http = Http()
    result = resolve(ranked, http)
    assert result['status'] != 'resolved' and http.calls == []
    assert result['reason'] == 'identifier_mapping_required'


def test_unmapped_partner_entity_does_not_invalidate_verified_target(ranked):
    http = Http()
    partner = _rcsb_graphql_entry('1ABC', entity_id='2')['polymer_entities'][0]
    partner['rcsb_polymer_entity_container_identifiers']['reference_sequence_identifiers'] = None
    http.entry['polymer_entities'].append(partner)
    result = resolve(ranked, http)
    assert result['status'] == 'resolved' and result['structures'][0]['structure_id'] == '1ABC'
