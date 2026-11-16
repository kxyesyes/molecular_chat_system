"""Shared target identity projection; complete structure evidence stays in results."""

import math
import re


def target_resolution_limit(query):
    """Select records by resolution only when the user requests an ordering."""
    from src.agent.contracts.generation_request import parse_chinese_integer
    from src.agent.planning.request_parsing import positive_intent_text
    ranked = r'分辨率\s*(?:最高|最好|最优|从高到低)|(?:最高|最好|最优)\s*(?:的)?\s*分辨率|\b(?:highest|best)[ -]resolution\b'
    for clause in re.split(r'[，,。；;!?！？\n]', positive_intent_text(query)):
        if not re.search(ranked, clause, re.I) or re.search(r'(?:不|别|勿|无需)\s*(?:用|要|必|再)?\s*按|\b(?:not|without)\b', clause, re.I):
            continue
        count = re.search(r'(?:前\s*|\btop\s+)([0-9一二两三四五六七八九十百]+)(?!\d)|'
                          r'([0-9一二两三四五六七八九十百]+)\s*(?:个|条|项)\s*(?:分辨率|PDB|实验|结构)|'
                          r'\b(one|two|three|four|five|six|seven|eight|nine|ten|\d{1,3})\s+(?:highest|best)[ -]resolution\b',
                          clause, re.I)
        if count:
            token = next(x for x in count.groups() if x).lower()
            words = 'one two three four five six seven eight nine ten'.split()
            if token in words:
                return words.index(token) + 1
            if len(token) > 8:
                return 21  # Only the distinction "more than 20" affects this display.
            value = int(token) if token.isdigit() else parse_chinese_integer(token)
            return value if value and value > 0 else None
        return 1 if re.search(r'哪(?:个|一)|最高(?:的)?\s*(?:一个)?(?:实验)?结构|\bstructure\b', clause, re.I) else 20
    return None


def resolution_preview(structures, limit):
    comparable = [s for s in structures if isinstance(s, dict) and s.get('structure_type') == 'experimental'
                  and type(s.get('resolution')) in (int, float) and math.isfinite(s['resolution'])
                  and s['resolution'] > 0 and s.get('source') == 'RCSB_PDB'
                  and isinstance(s.get('structure_id'), str) and re.fullmatch(r'[0-9][A-Za-z0-9]{3}', s['structure_id'])]
    return sorted(comparable, key=lambda s: s['resolution'])[:limit], len(comparable)


def target_planning_data(data):
    """Keep target identity for Web planning, not its presentation payload."""
    if not isinstance(data, list):
        return data
    fields = (
        "target_id", "gene_symbol", "target_gene", "target_identifier",
        "target_chembl_id", "uniprot_id", "target_name", "protein_name",
        "organism", "target_type", "match_reason", "source_record_id",
        "source", "source_query", "structure_count",
        "downloaded_structure_count", "experimental_structure_count",
        "alphafold_structure_count", "has_experimental_structure",
        "has_alphafold_structure", "structure_evidence_status",
    )
    return [{key: row[key] for key in fields if key in row}
            if isinstance(row, dict) else row for row in data]
