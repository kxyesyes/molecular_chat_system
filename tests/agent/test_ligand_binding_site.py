import pytest

from src.agent.ligand_binding_site import lookup_ligand_binding_site, map_site_to_uniprot
from src.target_search.remote_clients import BoundedJsonClient, _is_allowlisted_request


@pytest.mark.parametrize('variant', ['ok', 'wrong_accession', 'ambiguous_instances'])
def test_binding_site_requires_target_and_unique_ligand_instance(monkeypatch, variant):
    def request(self, source, method, url, **kwargs):
        if source == 'RCSB_PDB' and '/entry/' in url:
            return {'rcsb_id': '1M17', 'rcsb_entry_container_identifiers': {'polymer_entity_ids': ['1']}}
        if source == 'RCSB_PDB':
            return {'rcsb_id': '1M17_1', 'rcsb_polymer_entity_container_identifiers': {
                'reference_sequence_identifiers': [{'database_name': 'UniProt', 'database_accession':
                    'Q00001' if variant == 'wrong_accession' else 'P00533'}]}}
        residues = [{'startIndex': 29, 'endIndex': 29, 'indexType': 'PDB',
                     'additionalData': {'boundMoleculeId': 'bm1'}},
                    {'startIndex': 101, 'endIndex': 105, 'indexType': 'PDB',
                     'additionalData': {'boundMoleculeId': 'bm2' if variant == 'ambiguous_instances' else 'bm1'}}]
        return {'1m17': {'length': 333, 'dataType': 'LIGAND BINDING SITES', 'data': [
            {'accession': 'AQ4', 'dataType': 'Ligand', 'residues': residues,
             'additionalData': {'bestChainId': 'A', 'entityId': 1}}]}}
    monkeypatch.setattr(BoundedJsonClient, 'request_json', request)
    result = lookup_ligand_binding_site('1M17', 'P00533', 'AQ4')
    if variant == 'ok':
        assert result['chain'] == 'A' and result['ranges'] == [(29, 29), (101, 105)]
    else:
        assert result is None


def test_pdbe_allowlist_is_narrow():
    assert _is_allowlisted_request('PDBe', 'GET', 'https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/1m17/1')
    assert not _is_allowlisted_request('PDBe', 'GET', 'https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/1m17/1/other')
    assert not _is_allowlisted_request('PDBe', 'POST', 'https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/1m17/1')


def test_uniprot_mapping_uses_each_aligned_region_and_keeps_gaps_unknown():
    site = {'ranges': [(29, 29), (101, 105), (200, 200)], 'alignments': [
        {'entity_start': 6, 'reference_start': 695, 'length': 100},
        {'entity_start': 102, 'reference_start': 900, 'length': 4}]}
    mapped, unknown = map_site_to_uniprot(site)
    assert mapped == [(29, 718), (101, 790)]
    assert unknown == [102, 103, 104, 105, 200]
