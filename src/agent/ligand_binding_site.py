"""Retrieve ligand-site annotations for an explicitly identified protein entity."""
import re

from src.target_search.remote_clients import BoundedJsonClient, RemoteSourceError, _is_uniprot_accession


def lookup_ligand_binding_site(pdb, accession, ligand):
    if (not isinstance(pdb, str) or not re.fullmatch(r'[0-9][A-Z0-9]{3}', pdb)
            or not isinstance(accession, str) or not _is_uniprot_accession(accession)
            or not isinstance(ligand, str) or not re.fullmatch(r'[A-Z0-9]{1,12}', ligand)):
        return None
    try:
        with BoundedJsonClient(operation_timeout=20, timeout=(3, 7), max_attempts=2) as http:
            deadline = http._begin_operation()
            base = 'https://data.rcsb.org/rest/v1/core/'
            entry = http.request_json('RCSB_PDB', 'GET', base + 'entry/' + pdb, _deadline=deadline)
            if not isinstance(entry, dict) or entry.get('rcsb_id') != pdb:
                return None
            identifiers = entry.get('rcsb_entry_container_identifiers')
            entities = identifiers.get('polymer_entity_ids') if isinstance(identifiers, dict) else None
            if not isinstance(entities, list) or not 0 < len(entities) <= 20:
                return None
            matching = []
            for entity in entities:
                if not isinstance(entity, str) or not re.fullmatch(r'[1-9][0-9]*', entity):
                    return None
                record = http.request_json('RCSB_PDB', 'GET', base + f'polymer_entity/{pdb}/{entity}', _deadline=deadline)
                if not isinstance(record, dict) or record.get('rcsb_id') != f'{pdb}_{entity}':
                    return None
                identifiers = record.get('rcsb_polymer_entity_container_identifiers')
                refs = identifiers.get('reference_sequence_identifiers') if isinstance(identifiers, dict) else None
                if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
                    return None
                if any(ref.get('database_name') == 'UniProt' and ref.get('database_accession') == accession for ref in refs):
                    matching.append((entity, record))
            if len(matching) != 1:
                return None
            entity, entity_record = matching[0]
            url = f'https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/{pdb.lower()}/{entity}'
            payload = http.request_json('PDBe', 'GET', url, _deadline=deadline)
            record = payload.get(pdb.lower()) if isinstance(payload, dict) else None
            if not isinstance(record, dict) or record.get('dataType') != 'LIGAND BINDING SITES':
                return None
            length, data = record.get('length'), record.get('data')
            if type(length) is not int or length < 1 or not isinstance(data, list):
                return None
            sites = [site for site in data if isinstance(site, dict) and site.get('accession') == ligand
                     and site.get('dataType') == 'Ligand']
            if len(sites) != 1:
                return None
            site = sites[0]
            extra, residues = site.get('additionalData'), site.get('residues')
            if not isinstance(extra, dict) or extra.get('entityId') != int(entity) or not isinstance(residues, list) or not 0 < len(residues) <= 100:
                return None
            chain = extra.get('bestChainId')
            if not isinstance(chain, str) or not re.fullmatch(r'[A-Za-z0-9]{1,8}', chain):
                return None
            ranges, instances = [], set()
            for residue in residues:
                if not isinstance(residue, dict) or residue.get('indexType') != 'PDB':
                    return None
                start, end, extra = residue.get('startIndex'), residue.get('endIndex'), residue.get('additionalData')
                if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= length
                        or not isinstance(extra, dict) or not isinstance(extra.get('boundMoleculeId'), str)
                        or not extra['boundMoleculeId']):
                    return None
                ranges.append((start, end))
                instances.add(extra['boundMoleculeId'])
            if len(instances) != 1:
                return None
            alignments = []
            for alignment in entity_record.get('rcsb_polymer_entity_align') or []:
                if (not isinstance(alignment, dict) or alignment.get('reference_database_name') != 'UniProt'
                        or alignment.get('reference_database_accession') != accession):
                    continue
                for region in alignment.get('aligned_regions') or []:
                    if not isinstance(region, dict):
                        continue
                    start, reference, span = region.get('entity_beg_seq_id'), region.get('ref_beg_seq_id'), region.get('length')
                    if (type(start) is int and type(reference) is int and type(span) is int
                            and start > 0 and reference > 0 and span > 0 and start + span - 1 <= length):
                        alignments.append({'entity_start': start, 'reference_start': reference, 'length': span})
            return {'chain': chain, 'entity': entity, 'ranges': ranges, 'source_url': url, 'alignments': alignments}
    except RemoteSourceError:
        return None


def map_site_to_uniprot(site):
    mapped, unknown = [], []
    if sum(end - start + 1 for start, end in site['ranges']) > 1000:
        return mapped, unknown
    for start, end in site['ranges']:
        for position in range(start, end + 1):
            matches = [region['reference_start'] + position - region['entity_start']
                       for region in site['alignments']
                       if region['entity_start'] <= position < region['entity_start'] + region['length']]
            if len(matches) == 1:
                mapped.append((position, matches[0]))
            else:
                unknown.append(position)
    return mapped, unknown
