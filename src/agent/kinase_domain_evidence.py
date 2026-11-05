"""Compare RCSB sequence alignments with UniProt kinase-domain annotations."""
from src.target_search.remote_clients import BoundedJsonClient, RemoteSourceError, _is_uniprot_accession


def kinase_domain_ranges(pdb, accession):
    if not isinstance(accession, str) or not _is_uniprot_accession(accession):
        return None
    try:
        with BoundedJsonClient(operation_timeout=25, timeout=(3, 7), max_attempts=2) as http:
            deadline = http._begin_operation()
            payload = http.request_json('UniProt', 'GET', 'https://rest.uniprot.org/uniprotkb/search',
                params={'query': f'accession:{accession}', 'format': 'json', 'size': 2,
                        'fields': 'accession,ft_domain'}, _deadline=deadline)
            records = payload.get('results') if isinstance(payload, dict) else None
            if not isinstance(records, list) or len(records) != 1 or records[0].get('primaryAccession') != accession:
                return None
            features = records[0].get('features', [])
            domains = []
            for feature in features:
                if feature.get('type') != 'Domain' or feature.get('description') != 'Protein kinase':
                    continue
                location = feature.get('location', {})
                start, end = location.get('start', {}), location.get('end', {})
                if (start.get('modifier') != 'EXACT' or end.get('modifier') != 'EXACT'
                        or type(start.get('value')) is not int or type(end.get('value')) is not int
                        or not 0 < start['value'] <= end['value']):
                    return None
                domains.append((start['value'], end['value']))
            if len(domains) != 1:
                return None
            base = 'https://data.rcsb.org/rest/v1/core/'
            entry = http.request_json('RCSB_PDB', 'GET', base + 'entry/' + pdb, _deadline=deadline)
            identifiers = entry.get('rcsb_entry_container_identifiers', {})
            entities = identifiers.get('polymer_entity_ids')
            if entry.get('rcsb_id') != pdb or not isinstance(entities, list) or not 0 < len(entities) <= 20:
                return None
            ranges = []
            complete = True
            for entity in entities:
                if not isinstance(entity, str) or not entity.isdigit() or entity.startswith('0'):
                    return None
                data = http.request_json('RCSB_PDB', 'GET', base + f'polymer_entity/{pdb}/{entity}', _deadline=deadline)
                if data.get('rcsb_id') != f'{pdb}_{entity}':
                    return None
                refs = data.get('rcsb_polymer_entity_container_identifiers', {}).get('reference_sequence_identifiers')
                if not isinstance(refs, list):
                    return None
                if not any(ref.get('database_name') == 'UniProt' and ref.get('database_accession') == accession for ref in refs):
                    continue
                alignments = data.get('rcsb_polymer_entity_align') or []
                matching = [a for a in alignments if a.get('reference_database_name') == 'UniProt'
                            and a.get('reference_database_accession') == accession]
                if len(matching) != 1 or not matching[0].get('aligned_regions'):
                    return None
                length = data.get('entity_poly', {}).get('rcsb_sample_sequence_length')
                if type(length) is not int or length < 1:
                    return None
                entity_ranges = []
                for region in matching[0]['aligned_regions']:
                    start, span, entity_start = region.get('ref_beg_seq_id'), region.get('length'), region.get('entity_beg_seq_id')
                    if (type(start) is not int or type(span) is not int or type(entity_start) is not int
                            or start < 1 or span < 1 or entity_start < 1):
                        return None
                    ranges.append((start, start + span - 1))
                    entity_ranges.append((entity_start, entity_start + span - 1))
                end = 0
                for start, stop in sorted(entity_ranges):
                    if start > end + 1:
                        complete = False
                    end = max(end, stop)
                if end > length:
                    return None
                if end < length:
                    complete = False
            if not complete and not any(start <= domains[0][1] and end >= domains[0][0]
                                        for start, end in ranges):
                return None
            return (ranges, domains[0]) if ranges else None
    except RemoteSourceError:
        return None
