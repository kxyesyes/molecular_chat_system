"""Read-only, current-rank-bound authoritative identity/structure continuation.

No coordinate download, cache mutation, molecular preparation or docking grant.
Provider transports are trusted server dependencies, never browser arguments.
"""
from copy import deepcopy
from html import escape
import re

from src.agent.harness.decision_bounds import validate_json
from src.agent.utils.validators import InputValidator
from src.target_search.authoritative_resolver import AuthoritativeTargetResolver
from src.target_search.exact_name import ExactTargetNameResolver
from src.target_search.remote_clients import (
    AlphaFoldClient, BoundedJsonClient, RcsbClient, RemoteSourceError, UniProtClient,
    _is_uniprot_accession, _resolve_organism,
)


def _failure(reason):
    return dict(status='unavailable', reason=reason, ranked_record=None, target=None,
                mapping_evidence=None, structures=[], input_ready=False, docking_started=False)


class _OneOperation:
    """All existing providers share this read's original deadline and species."""
    def __init__(self, http, organism, taxon):
        self.http, self.organism, self.taxon = http, organism, taxon
        self.deadline = http._begin_operation()
        self.accession = None

    def _begin_operation(self):
        return self.deadline

    def _remaining_time(self, source, deadline):
        return self.http._remaining_time(source, self.deadline)

    def request_json(self, source, method, url, **kwargs):
        kwargs['_deadline'] = self.deadline
        self._remaining_time(source, self.deadline)
        result = self.http.request_json(source, method, url, **kwargs)
        self._remaining_time(source, self.deadline)
        validate_json(result, max_bytes=1_000_000, reason='invalid_response')
        if source == 'RCSB_PDB' and url == RcsbClient.GRAPHQL_URL:
            self._check_structure_species(result)
        return result

    def _check_structure_species(self, payload):
        # Normalization keeps one display organism; check ALL source taxa of
        # every matching entity before that information is lost. Other complex
        # entities are not the queried target and do not replace its identity.
        for entry in payload['data']['entries']:
            for entity in entry['polymer_entities']:
                identifiers = entity.get('rcsb_polymer_entity_container_identifiers')
                references = identifiers.get('reference_sequence_identifiers') if type(identifiers) is dict else None
                if type(references) is not list:
                    # Unmapped partners are not target evidence. The existing
                    # normalizer still rejects missing mappings for requested IDs.
                    continue
                if any(type(item) is dict and item.get('database_name') == 'UniProt'
                       and item.get('database_accession') == self.accession for item in references):
                    organisms = entity['rcsb_entity_source_organism']
                    if not organisms or any(
                            item.get('ncbi_scientific_name') != self.organism
                            or type(item.get('ncbi_taxonomy_id')) is not int
                            or item['ncbi_taxonomy_id'] != self.taxon for item in organisms):
                        raise RemoteSourceError('RCSB_PDB', 'invalid_response', retryable=False)


def resolve_ranked_structures(boundary, context, *, session_id, http=None):
    """Resolve rank one from owned evidence; never accept a client target string.

    A supplied transport is borrowed. The default is request-owned and always
    closed. Source revalidation brackets the complete provider read. Unmapped
    source identifiers are not silently replaced by a name search.
    """
    owned = http is None
    try:
        ranked = boundary.resolve(context, session_id=session_id, rank=1)
        if ranked is None:
            return _failure('rank_source_unavailable')
        row = ranked['record']
        organism, taxon = _resolve_organism(row['organism'])
        # The normalized legacy record may retain a name key even when the
        # raw producer supplied target_uniprot_id. Never discard that identity.
        supplied = [row[key] for key in ('target_chembl_id', 'target_uniprot_id')
                    if row.get(key) not in (None, '')]
        identifier = supplied[0] if supplied else row['target_identifier']
        is_name = not supplied and re.fullmatch(r'name-sha256:[0-9a-f]{64}', identifier) is not None
        if (len(supplied) > 1 or not is_name and not (type(identifier) is str
                and identifier.isascii() and _is_uniprot_accession(identifier.upper()))):
            return _failure('identifier_mapping_required')
        if http is None:
            http = BoundedJsonClient()
        operation = _OneOperation(http, organism, taxon)
        mapping = None
        if is_name:
            mapped = ExactTargetNameResolver(operation).resolve(row['target_name'], organism)
            if mapped['status'] != 'resolved':
                return _failure('exact_mapping_unavailable')
            accession = mapped['target']['uniprot_id']
            mapping = mapped['mapping_evidence']
        else:
            accession = identifier.upper()
        operation.accession = accession
        # Reuse the existing authoritative target-search path with explicit
        # species, not a shared Human-default service's mutable configuration.
        resolver = AuthoritativeTargetResolver(UniProtClient(operation),
            RcsbClient(operation), AlphaFoldClient(operation))
        resolved = resolver.resolve(accession, organism).to_dict()
        operation._remaining_time('UniProt', operation.deadline)
        if resolved['status'] != 'resolved':
            return _failure('structure_provider_unavailable')
        target, structures = resolved['target'], resolved['structures']
        if (target['uniprot_id'] != accession or target['organism'] != organism
                or any(item.get('organism') != organism for item in structures)):
            return _failure('identity_mismatch')
        if boundary.resolve(context, session_id=session_id, rank=1) != ranked:
            return _failure('rank_source_unavailable')
        output = dict(status='resolved', reason='authoritative', ranked_record=ranked,
            target=target, mapping_evidence=mapping, structures=structures,
            input_ready=False, docking_started=False)
        validate_json(output, max_bytes=128 * 1024, reason='invalid_response')
        operation._remaining_time('UniProt', operation.deadline)
        return deepcopy(output)
    except Exception:
        return _failure('ranked_structure_unavailable')
    finally:
        if owned and http is not None:
            try:
                http.close()
            except Exception:
                # Cleanup is part of this read's success boundary. Never let a
                # transport exception escape to the legacy socket/log handler,
                # or publish an otherwise successful rank context after it.
                return _failure('ranked_structure_unavailable')


def _fresh_molecule_reverse_request(message):
    # This is only routing admission, not SMILES validation or tool consent.
    # Bind an affirmative reverse action to its molecule, not an arbitrary
    # ligand mentioned elsewhere in a reference to a previous ranking.
    # In particular, ASCII dots belong to disconnected SMILES, not clauses.
    clauses = re.split(r'[，,;；。!?！？\n]|\b(?:and|but|then)\b|并且|然后', message, flags=re.I)
    chinese_prefix = r'\s*(?:请\s*)?(?:重新\s*)?'
    chinese_action = r'(?:反向寻靶|反向靶点预测|反向预测靶点)'
    # Keep bare 并 inside the whole command: splitting it would also admit
    # 并非 negation or a display clause referring to a previous ranking.
    chinese_display = (r'(?:\s*并\s*(?:列出|展示|显示)\s*'
                       r'(?:排名第[一1]|第[一1]名)的(?:候选)?靶点)?\s*')
    for clause in clauses:
        for molecule in InputValidator().extract_potential_smiles_candidates(clause):
            token = re.escape(molecule)
            label = r'(?:SMILES\s*[:：]\s*)?'
            # A whole affirmative command, not a prefix of a negated action
            # or a description of a previous result for this same molecule.
            if (re.fullmatch(chinese_prefix + r'对\s*' + label + token
                             + r'\s*(?:做|进行|运行)\s*' + chinese_action + chinese_display, clause, re.I)
                    or re.fullmatch(chinese_prefix + r'(?:(?:做|进行|运行)\s*)?'
                                    + chinese_action + r'\s*(?:对\s*)?' + label + token + chinese_display, clause, re.I)
                    or re.fullmatch(r'\s*(?:please\s+)?(?:(?:run|perform|rerun)\s+)?'
                                    r'reverse[ -]target(?:ing)?\s+(?:prediction\s+)?(?:for\s+)?'
                                    + label + token + r'\s*', clause, re.I)):
                return True
    return False


def reverse_rank_routing_query(message):
    """Keep fresh display qualifiers out of the legacy rank-reference gate.

    The original query must still reach execution/history, with all molecular
    validation, negation and capabilities intact. This view is for routing only.
    """
    if not _fresh_molecule_reverse_request(message):
        return message
    return re.sub(r'((?:列出|展示|显示)\s*)(?:排名第[一1]|第[一1]名)的((?:候选)?靶点)',
                  r'\1\2', message)


def rank_followup_kind(message):
    if _fresh_molecule_reverse_request(message):
        return None
    if (re.search(r'反向寻靶|反向靶点预测|reverse.target', message, re.I)
            and not re.search(r'排名第|排行第|第[一二三四五六七八九十0-9]+(?:名|个).*?靶点|\brank(?:ed)?\s*(?:one|first|1)\b|\btop\s*1\b', message, re.I)):
        # A fresh ranking request still goes through the normal molecular-input
        # and negation/routing checks. Merely saying "排名" is not a reference.
        return None
    if not (re.search(r'排名|排行|第(?:一|1)名|\brank(?:ed)?\b|\btop\s*1\b', message, re.I)
            and re.search(r'靶点|结构|对接|target|structure|dock', message, re.I)):
        return None
    compact = re.sub(r'\s+', '', message)
    if re.fullmatch(r'(?:请)?(?:查找|查询|搜索)(?:排名第[一1]|第[一1]名)的靶点结构'
                    r'(?:[，,]?并说明(?:能否|是否可以)继续对接)?[。.!！]?', compact):
        return 'first'
    return 'clarify'


def _text(value):
    value = escape(str(value))
    return ''.join(f'&#{ord(c)};' if c in '|`*_[\\]#' else ' ' if c in '\r\n' else c for c in value)


def ranked_structure_followup(message, history, *, session_id, enable_tools, boundary):
    """Socket-local continuation, not generic conversation or a consent route."""
    kind = rank_followup_kind(message)
    if kind is None:
        return None
    unavailable = dict(status='partial', content='排名证据不可用：缺少同会话最后选定、已接受且仍有效的排名。需要先在本会话重新运行反向寻靶。', context=None)
    if kind != 'first':
        return dict(status='partial', content='请明确要引用的排名、靶点及物种；当前仅支持查询刚才排名第一的靶点结构。结构查询不授权对接。', context=None)
    if not enable_tools:
        return dict(status='partial', content='工具已关闭，未查询排名靶点结构，也未启动对接。', context=None)
    if boundary is None or type(history) is not list or not history or type(history[-1]) is not dict:
        return unavailable
    context = history[-1].get('reverse_target_evidence')
    result = resolve_ranked_structures(boundary, context, session_id=session_id)
    if result['status'] != 'resolved':
        if result['reason'] == 'rank_source_unavailable':
            return unavailable
        return dict(status='partial', content='排名证据对应的靶点/物种尚不能唯一核实，或权威结构来源暂不可用。请明确靶点标识并核对物种；未启动对接。', context=None)
    target, ranked = result['target'], result['ranked_record']
    accession = target['uniprot_id']
    lines = [f"原排名第 1：{_text(ranked['record']['target_name'])}；物种：{_text(target['organism'])}。",
        f'已核实标识：[UniProt {accession}](https://www.uniprot.org/uniprotkb/{accession}/entry)。',
        '这只是原二维结构相似度排名的结构检索续接，不是实验靶点确认或活性结论。', '']
    mapping = result['mapping_evidence']
    if mapping is not None:
        lines.extend([f"名称映射来源：UniProt，entry version {_text(mapping['entry_version'])}；精确匹配 {_text(mapping['matched_name'])}。",
                      '映射响应摘要：' + _text(mapping['response_digest']), ''])
    if result['structures']:
        lines.extend(['| 结构 | 来源/类型 | 方法 | 分辨率 Å |', '|---|---|---|---|'])
        for structure in result['structures']:
            label = _text(structure['structure_id'])
            source_label = 'RCSB PDB' if structure['source'] == 'RCSB_PDB' else structure['source']
            lines.append(f"| [{label}]({structure['source_url']}) | {_text(source_label)} / {_text(structure['structure_type'])} | "
                         f"{_text(structure.get('method') or '未提供')} | {_text(structure.get('resolution') if structure.get('resolution') is not None else '未提供')} |")
    else:
        lines.append('权威来源此次未返回可用结构；不能据此宣称已获得受体。')
    lines.extend(['', '对接状态：未启动。结构条目不等于已准备的受体或口袋；仍需核实链/构象与化学完整性、参考配体和口袋、配体三维结构及当前任务审批。',
                  '原始 trace：' + _text(ranked['context']['trace_id']),
                  '原始证据摘要：' + _text(ranked['context']['result_digest'])])
    content = '\n'.join(lines)
    if boundary.resolve(context, session_id=session_id, rank=1) != ranked:
        return unavailable
    return dict(status='succeeded', content=content, context=deepcopy(context))
