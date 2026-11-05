import pytest

from src.agent.target_structure_followup import accepted_target_evidence, structure_followup


def observation():
    return {'tool_name': 'target_database_search', 'status': 'succeeded', 'success': True,
            'error': None, 'quality': {'validated': True, 'evidence_id': 'target-e1'},
            'provenance': {'tool_name': 'target_database_search', 'input_digest': 'input-1',
                           'demo_mode': False, 'fallback_used': False},
            'data': [{'gene_symbol': 'PDE4A', 'organism': 'Homo sapiens',
                      'recommended_structures': [{'structure_id': '2QYK', 'source': 'RCSB_PDB',
                        'structure_type': 'experimental', 'resolution': 2.1,
                        'ligand_ids': ['MG', 'NPV', 'ZN']}]}]}


@pytest.mark.parametrize('query', [
    'AQ4 是什么配体？能否直接用这个结构给厄洛替尼做重对接验证？先说明依据和还需检查的项目，不要运行对接。',
    'AQ4 是什么配体？可以用该结构对厄洛替尼进行重对接吗？请先说明还需要检查什么，暂时不要执行对接。',
])
def test_redocking_consultation_uses_owned_ligand_evidence(monkeypatch, query):
    from src.target_search.remote_clients import RcsbClient
    from src.agent.contracts.docking_intent import explicit_docking_action
    calls = []
    def component(self, ligand):
        calls.append(ligand)
        return {'id': ligand, 'name': 'ERLOTINIB', 'source_url': f'https://www.rcsb.org/ligand/{ligand}'}
    monkeypatch.setattr(RcsbClient, 'lookup_component', component)
    monkeypatch.setattr('src.agent.ligand_binding_site.lookup_ligand_binding_site', lambda *args: None)
    evidence = {'session_id': 's', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['AQ4']}]}
    history = [{'target_structure_evidence': evidence}]
    assert not explicit_docking_action(query)
    answer = structure_followup(query, history, 's')
    assert answer['status'] == 'partial'
    assert all(value in answer['content'] for value in ('AQ4', 'ERLOTINIB', '1M17', 'RMSD', '未运行对接', '质子化'))
    assert calls == ['AQ4']
    assert structure_followup(query, history, 'other')['status'] == 'partial'
    disabled = structure_followup(query, history, 's', enable_tools=False)
    assert '未启用' in disabled['content'] and calls == ['AQ4']
    missing = structure_followup(query.replace('AQ4', 'XYZ'), history, 's')
    assert '无法' in missing['content'] and calls == ['AQ4']
    assert structure_followup(query + '请执行对接。', history, 's') is None
    assert explicit_docking_action(query + '请执行对接。')


def test_target_function_evidence_requires_single_uniprot_record():
    from src.agent.target_structure_followup import accepted_target_function_evidence
    item = observation()
    item['data'] = [{'gene_symbol': 'EGFR', 'uniprot_id': 'P00533',
                     'source': 'UniProt', 'source_record_id': 'P00533',
                     'source_url': 'https://www.uniprot.org/uniprotkb/P00533/entry',
                     'function': 'Receptor tyrosine kinase.'}]
    result = {'trace_id': 'trace-1', 'tool_result_sequence': [item]}
    evidence = accepted_target_function_evidence(result, 'session-1')
    assert evidence['function'] == 'Receptor tyrosine kinase.'
    assert evidence['uniprot_id'] == 'P00533'
    item['data'][0]['target_stale'] = True
    assert accepted_target_function_evidence(result, 'session-1')['function'] is None
    item['data'][0]['target_stale'] = False
    item['data'].append(dict(item['data'][0]))
    assert accepted_target_function_evidence(result, 'session-1') is None


def test_redocking_structure_selection_preserves_requested_ligand_and_preparation(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    calls = []
    def component(self, ligand):
        calls.append(ligand)
        return None
    monkeypatch.setattr(RcsbClient, 'lookup_component', component)
    monkeypatch.setattr('src.agent.ligand_binding_site.lookup_ligand_binding_site', lambda *args: None)
    query = 'AQ4 是什么配体？能否直接用这个结构给厄洛替尼做重对接验证？先说明依据和还需检查的项目，不要运行对接。'
    evidence = {'session_id': 's', 'structures': [
        {'structure_id': '1M17', 'ligand_ids': ['AQ4', 'CL']},
        {'structure_id': '1IVO', 'ligand_ids': ['NAG']}]}
    pending = structure_followup(query, [{'target_structure_evidence': evidence}], 's')['evidence']
    history = [{'target_structure_evidence': pending}]
    mismatch = structure_followup('1IVO', history, 's')
    assert '无法' in mismatch['content'] and not calls
    selected = structure_followup('1M17', history, 's')
    assert calls == ['AQ4'] and 'RMSD' in selected['content']
    assert 'pending_redocking_query' not in selected['evidence']


def test_local_target_identity_does_not_turn_unverified_text_into_function():
    from src.agent.target_structure_followup import accepted_target_function_evidence
    item = observation()
    item['data'] = [{'gene_symbol': 'EGFR', 'uniprot_id': 'P00533',
                     'function': 'Unverified text'}]
    result = {'trace_id': 'trace-1', 'tool_result_sequence': [item]}
    evidence = accepted_target_function_evidence(result, 'session-1')
    assert evidence['uniprot_id'] == 'P00533' and evidence['function'] is None


def test_target_function_followup_uses_session_evidence_or_reports_missing():
    evidence = {'session_id': 'session-1', 'uniprot_id': 'P00533', 'gene_symbol': 'EGFR',
                'function': 'Receptor tyrosine kinase.',
                'source_url': 'https://www.uniprot.org/uniprotkb/P00533/entry'}
    query = '这个靶点的生物学功能是什么？'
    result = structure_followup(query, [{'target_function_evidence': evidence}], 'session-1')
    assert result['status'] == 'completed' and 'P00533' in result['content']
    assert 'Receptor tyrosine kinase.' in result['content']
    assert structure_followup(query, [{'target_function_evidence': evidence}], 'other')['status'] == 'partial'
    evidence['function'] = None
    assert structure_followup(query, [{'target_function_evidence': evidence}], 'session-1')['status'] == 'partial'
    assert structure_followup(query + '再预测 CCO 活性。', [{'target_function_evidence': evidence}], 'session-1') is None


def test_target_function_summary_omits_inline_identifiers_and_marks_excerpt():
    evidence = {'session_id': 'session-1', 'uniprot_id': 'P00533', 'gene_symbol': 'EGFR',
                'function': 'First function (PubMed:123, PubMed:456). Additional information.',
                'source_url': 'https://www.uniprot.org/uniprotkb/P00533/entry'}
    result = structure_followup('这个靶点的生物学功能是什么？',
                                [{'target_function_evidence': evidence}], 'session-1')
    assert 'First function.' in result['content']
    assert 'PubMed:' not in result['content'] and 'Additional information' not in result['content']
    assert '首句' in result['content']


def test_structure_followup_reuses_exact_session_evidence_without_asserting_ligand_role():
    evidence = accepted_target_evidence({'trace_id': 'trace-1', 'tool_result_sequence': [observation()]}, 'session-1')
    query = '刚才的 2QYK 分辨率是多少？有哪些共晶配体？'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial'
    assert '2QYK' in answer['content'] and '2.1' in answer['content'] and 'NPV' in answer['content']
    assert '角色' in answer['content'] and '未核实' in answer['content']
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query.replace('2QYK', '3XYZ'), [{'target_structure_evidence': evidence}], 'session-1')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


def test_ligand_pocket_consultation_requires_owned_matching_record():
    evidence = {'session_id': 'session-1', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['AQ4']}]}
    query = '其中 1M17 有配体 AQ4，能否直接以它确定对接口袋？还需要核对哪些信息？请先不要运行对接。'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial'
    assert all(term in answer['content'] for term in ('1M17', 'AQ4', '配体', '口袋', '未运行对接'))
    assert '未核' in answer['content']
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query.replace('AQ4', 'XYZ'), [{'target_structure_evidence': evidence}], 'session-1')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None
    from src.web.web_business import WebTurnProjection
    view = WebTurnProjection(query, 'session-1', [{'target_structure_evidence': evidence}]).followup(enable_tools=True)
    assert view['status'] == 'partial' and '未运行对接' in view['content']


def test_pairwise_resolution_comparison_requires_two_owned_structure_records():
    evidence = {'session_id': 'session-1', 'structures': [
        {'structure_id': '1P0I', 'resolution': 2.0, 'target': 'BCHE', 'uniprot_id': 'P06276'},
        {'structure_id': '1P0M', 'resolution': 2.38, 'target': 'BCHE', 'uniprot_id': 'P06276'}]}
    history = [{'target_structure_evidence': evidence}]
    query = '刚才列出的 1P0I 和 1P0M，哪个分辨率更高？仅依据已返回的结构信息回答，不要假设配体结合方式。'
    answer = structure_followup(query, history, 'session-1')
    assert answer['status'] == 'completed'
    assert all(term in answer['content'] for term in ('1P0I', '1P0M', '2', '2.38', '更高'))
    assert structure_followup(query, history, 'other')['status'] == 'partial'
    assert structure_followup(query.replace('1P0M', '3XYZ'), history, 'session-1')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', history, 'session-1') is None
    evidence['structures'][1]['resolution'] = None
    assert structure_followup(query, history, 'session-1')['status'] == 'partial'
    evidence['structures'][1]['resolution'] = 2.0
    assert '相同' in structure_followup(query, history, 'session-1')['content']


def test_singular_structure_followup_requires_one_owned_record():
    evidence = accepted_target_evidence({'trace_id': 'trace-1', 'tool_result_sequence': [observation()]}, 'session-1')
    query = '刚才这个结构的分辨率是多少？配体 ID 是什么？'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert '2QYK' in answer['content'] and '2.1' in answer['content'] and 'NPV' in answer['content']
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    evidence['structures'].append({**evidence['structures'][0], 'structure_id': '1M17'})
    assert '唯一' in structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')['content']
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


def test_kinase_docking_candidate_question_uses_owned_verified_titles_only(monkeypatch):
    from src.web.web_business import WebTurnProjection
    monkeypatch.setattr('src.target_search.remote_clients.RcsbClient.lookup_titles', lambda self, ids: {}, raising=False)
    evidence = {'session_id': 'session-1', 'structures': [
        {'structure_id': '1MOX', 'target': 'EGFR', 'resolution': 2.5,
         'title': 'Extracellular domain in complex with growth factor',
         'source_url': 'https://www.rcsb.org/structure/1MOX'},
        {'structure_id': '1M17', 'target': 'EGFR', 'resolution': 2.6,
         'title': 'EGFR tyrosine kinase domain with 4-anilinoquinazoline inhibitor erlotinib',
         'source_url': 'https://www.rcsb.org/structure/1M17'},
    ]}
    query = '这些结构中哪个适合用于 EGFR 激酶抑制剂对接？为什么？'
    history = [{'target_structure_evidence': evidence}]
    answer = WebTurnProjection(query, 'session-1', history).followup(enable_tools=True)
    assert answer['status'] == 'partial' and '1M17' in answer['content']
    assert 'RCSB' in answer['content'] and '口袋' in answer['content'] and '未运行对接' in answer['content']
    assert '1M17' not in structure_followup(query, history, 'other')['content']
    assert structure_followup(query + '请执行对接。', history, 'session-1') is None
    evidence['structures'][1]['source_url'] = 'https://example.com/1M17'
    assert '1M17' not in structure_followup(query, history, 'session-1')['content']


def test_kinase_candidate_fetches_titles_for_retrieved_pdbs(monkeypatch):
    from src.web.web_business import WebTurnProjection
    requested = []
    def lookup(self, ids):
        requested.extend(ids)
        return {'1M17': 'EGFR tyrosine kinase domain with inhibitor erlotinib'}
    monkeypatch.setattr('src.target_search.remote_clients.RcsbClient.lookup_titles', lookup, raising=False)
    evidence = {'session_id': 's', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'resolution': 2.6},
        {'structure_id': '1MOX', 'target': 'EGFR', 'resolution': 2.5}]}
    query = '这些结构中哪个适合用于 EGFR 激酶抑制剂对接？为什么？'
    answer = WebTurnProjection(query, 's', [{'target_structure_evidence': evidence}]).followup(enable_tools=True)
    assert set(requested) == {'1M17', '1MOX'}
    assert '1M17' in answer['content'] and '1MOX' not in answer['content']
    assert '最适合' in answer['content'] and answer['status'] == 'partial'
    requested.clear()
    disabled = WebTurnProjection(query, 's', [{'target_structure_evidence': evidence}]).followup(enable_tools=False)
    assert not requested and '1M17' not in disabled['content']


def test_single_docking_candidate_can_be_referenced_on_next_turn(monkeypatch):
    monkeypatch.setattr('src.target_search.remote_clients.RcsbClient.lookup_titles',
                        lambda self, ids: {'1M17': 'EGFR kinase domain with inhibitor erlotinib'})
    evidence = {'session_id': 's', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'resolution': 2.6, 'ligand_ids': ['AQ4']},
        {'structure_id': '1MOX', 'target': 'EGFR', 'resolution': 2.5, 'ligand_ids': []}]}
    question = '这些结构中哪个适合用于 EGFR 激酶抑制剂对接？为什么？'
    first = structure_followup(question, [{'target_structure_evidence': evidence}], 's')
    assert first['evidence']['selected_structure_id'] == '1M17'
    follow = '刚才这个结构的分辨率是多少？配体 ID 是什么？'
    answer = structure_followup(follow, [{'target_structure_evidence': first['evidence']}], 's')
    assert '1M17' in answer['content'] and '2.6' in answer['content'] and 'AQ4' in answer['content']
    assert '未核实' in answer['content']
    assert '1M17' not in structure_followup(follow, [{'target_structure_evidence': first['evidence']}], 'other')['content']
    monkeypatch.setattr('src.target_search.remote_clients.RcsbClient.lookup_titles',
                        lambda self, ids: {key: 'EGFR kinase domain with inhibitor' for key in ids})
    multiple = structure_followup(question, [{'target_structure_evidence': evidence}], 's')
    assert 'selected_structure_id' not in multiple['evidence']
    assert '唯一' in structure_followup(follow, [{'target_structure_evidence': multiple['evidence']}], 's')['content']


@pytest.mark.parametrize('query,known', [
    ('1MOX 有 EGFR 的激酶域吗？能用它对接 ATP 竞争性抑制剂吗？', '1MOX'),
    ('1MOX 中有哪些配体？它们是什么，能确认是抑制剂吗？', 'AQ4'),
    ('AQ4 是什么？它是 EGFR 抑制剂吗？', 'AQ4'),
])
def test_structure_verification_respects_disabled_tools(query, known, monkeypatch):
    from src.target_search.remote_clients import BoundedJsonClient
    from src.web.web_business import WebTurnProjection

    def forbidden(*args, **kwargs):
        pytest.fail('disabled tools opened a remote client')

    monkeypatch.setattr(BoundedJsonClient, '__init__', forbidden)
    evidence = {'session_id': 's', 'structures': [{'structure_id': '1MOX',
        'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['AQ4']}]}
    projection = WebTurnProjection(query, 's', [{'target_structure_evidence': evidence}])
    answer = projection.followup(enable_tools=False)
    assert answer['status'] == 'partial' and known in answer['content']
    assert '工具未启用' in answer['content']


def test_rcsb_title_lookup_keeps_only_matching_safe_records():
    from src.target_search.remote_clients import RcsbClient
    class Http:
        def request_json(self, source, method, url, **kwargs):
            return {'data': {'entries': [
                {'rcsb_id': '1M17', 'struct': {'title': 'EGFR kinase with inhibitor'}},
                {'rcsb_id': '9ABC', 'struct': {'title': 'unrequested'}},
                {'rcsb_id': '1MOX', 'struct': {'title': '<unsafe>'}},
            ]}}
    assert RcsbClient(Http()).lookup_titles(['1M17', '1MOX']) == {'1M17': 'EGFR kinase with inhibitor'}


@pytest.mark.parametrize('query', [
    '刚才列出的结构中，哪个分辨率最高？能直接用于分子对接吗？',
    '刚才这些结构里分辨率最高的是哪个？它是否因此一定最适合对接？',
])
def test_best_resolution_followup_uses_only_owned_retrieved_structures(query):
    from src.web.scientific_references import scientific_intent
    from src.web.web_business import WebTurnProjection
    item = observation()
    item['data'][0]['recommended_structures'].append({
        'structure_id': '1M17', 'source': 'RCSB_PDB', 'structure_type': 'experimental',
        'resolution': 2.6, 'ligand_ids': []})
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial'
    assert '2QYK' in answer['content'] and '2.1' in answer['content']
    assert '检索' in answer['content'] and '不能' in answer['content']
    projection = WebTurnProjection(query, 'session-1', [{'target_structure_evidence': evidence}])
    assert '2QYK' in projection.followup(enable_tools=True)['content']
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert not scientific_intent(query)
    assert scientific_intent(query + '请执行分子对接。')


def test_specific_domain_suitability_question_does_not_execute_docking():
    from src.web.web_business import WebTurnProjection
    item = observation()
    item['data'][0]['gene_symbol'] = 'EGFR'
    item['data'][0]['recommended_structures'][0]['structure_id'] = '1MOX'
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    query = '1MOX 包含 EGFR 激酶结构域吗？它适合作为激酶抑制剂对接的起点吗？'
    answer = WebTurnProjection(query, 'session-1', [{'target_structure_evidence': evidence}]).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert '1MOX' in answer['content'] and '未' in answer['content']
    assert '结构域' in answer['content'] and '对接' in answer['content']
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


@pytest.mark.parametrize('query', [
    '1MOX 有 EGFR 的激酶域吗？能用它对接 ATP 竞争性抑制剂吗？',
    '1MOX 是否包含 EGFR 的激酶结构域？它能用于激酶抑制剂对接吗？',
])
def test_domain_question_natural_wording_uses_owned_evidence(monkeypatch, query):
    from src.web.web_business import WebTurnProjection
    from src.agent.contracts.docking_intent import explicit_docking_action
    monkeypatch.setattr('src.agent.kinase_domain_evidence.kinase_domain_ranges',
                        lambda pdb, accession: ([(25, 525)], (712, 979)))
    evidence = {'session_id': 's', 'structures': [{'structure_id': '1MOX', 'target': 'EGFR', 'uniprot_id': 'P00533'}]}
    history = [{'target_structure_evidence': evidence}]
    answer = WebTurnProjection(query, 's', history).followup(enable_tools=True)
    assert answer is not None and '25–525' in answer['content'] and '不适合' in answer['content']
    assert not explicit_docking_action(query)
    assert structure_followup(query + '请实际运行对接。', history, 's') is None
    assert explicit_docking_action(query + '请实际运行对接。')
    assert structure_followup(query, history, 'other')['status'] == 'partial'


@pytest.mark.parametrize('entity_length,expected', [(501, True), (600, False)])
def test_domain_followup_compares_authoritative_ranges(monkeypatch, entity_length, expected):
    from src.target_search.remote_clients import BoundedJsonClient
    def request(self, source, method, url, **kwargs):
        if source == 'UniProt':
            return {'results': [{'primaryAccession': 'P00533', 'features': [
                {'type': 'Domain', 'description': 'Protein kinase', 'location': {
                    'start': {'value': 712, 'modifier': 'EXACT'}, 'end': {'value': 979, 'modifier': 'EXACT'}}}]}]}
        if url.endswith('/entry/1MOX'):
            return {'rcsb_id': '1MOX', 'rcsb_entry_container_identifiers': {'polymer_entity_ids': ['1', '2']}}
        if url.endswith('/1MOX/1'):
            return {'rcsb_id': '1MOX_1', 'entity_poly': {'rcsb_sample_sequence_length': entity_length}, 'rcsb_polymer_entity_container_identifiers': {'reference_sequence_identifiers': [{'database_name': 'UniProt', 'database_accession': 'P00533'}]}, 'rcsb_polymer_entity_align': [{'reference_database_name': 'UniProt',
                'reference_database_accession': 'P00533', 'aligned_regions': [{'entity_beg_seq_id': 1, 'ref_beg_seq_id': 25, 'length': 501}]}]}
        return {'rcsb_id': '1MOX_2', 'rcsb_polymer_entity_container_identifiers': {'reference_sequence_identifiers': [{'database_name': 'UniProt', 'database_accession': 'P01135'}]}, 'rcsb_polymer_entity_align': []}
    monkeypatch.setattr(BoundedJsonClient, 'request_json', request)
    evidence = {'session_id': 's', 'structures': [{'structure_id': '1MOX', 'target': 'EGFR', 'uniprot_id': 'P00533'}]}
    answer = structure_followup('1MOX 包含 EGFR 激酶结构域吗？它适合作为激酶抑制剂对接的起点吗？',
                                [{'target_structure_evidence': evidence}], 's')
    if expected:
        assert '25–525' in answer['content'] and '712–979' in answer['content']
        assert '不适合' in answer['content'] and '未运行对接' in answer['content']
        assert 'https://www.rcsb.org/structure/1MOX' in answer['content']
    else:
        assert answer['status'] == 'partial' and '未核验' in answer['content']


def test_domain_followup_reports_positive_coverage_despite_unmapped_tag(monkeypatch):
    from src.target_search.remote_clients import BoundedJsonClient
    def request(self, source, method, url, **kwargs):
        if source == 'UniProt':
            return {'results': [{'primaryAccession': 'P00533', 'features': [{'type': 'Domain',
                'description': 'Protein kinase', 'location': {'start': {'value': 712, 'modifier': 'EXACT'},
                'end': {'value': 979, 'modifier': 'EXACT'}}}]}]}
        if url.endswith('/entry/1M17'):
            return {'rcsb_id': '1M17', 'rcsb_entry_container_identifiers': {'polymer_entity_ids': ['1']}}
        return {'rcsb_id': '1M17_1', 'entity_poly': {'rcsb_sample_sequence_length': 333},
                'rcsb_polymer_entity_container_identifiers': {'reference_sequence_identifiers': [
                    {'database_name': 'UniProt', 'database_accession': 'P00533'}]},
                'rcsb_polymer_entity_align': [{'reference_database_name': 'UniProt',
                    'reference_database_accession': 'P00533', 'aligned_regions': [
                        {'entity_beg_seq_id': 6, 'ref_beg_seq_id': 695, 'length': 328}]}]}
    monkeypatch.setattr(BoundedJsonClient, 'request_json', request)
    evidence = {'session_id': 's', 'structures': [{'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533'}]}
    answer = structure_followup('1M17 是否包含 EGFR 的激酶结构域？它能用于激酶抑制剂对接吗？',
                                [{'target_structure_evidence': evidence}], 's')
    assert '695–1022' in answer['content'] and '712–979' in answer['content']
    assert '覆盖' in answer['content'] and '口袋' in answer['content']
    assert answer['status'] == 'partial' and '未运行对接' in answer['content']


def test_domain_selection_resolves_ligand_followup_without_claiming_binding_site(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    monkeypatch.setattr('src.agent.kinase_domain_evidence.kinase_domain_ranges',
                        lambda pdb, accession: ([(695, 1022)], (712, 979)))
    monkeypatch.setattr('src.agent.ligand_binding_site.lookup_ligand_binding_site', lambda *args: None)
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': 'ERLOTINIB', 'source_url': f'https://www.rcsb.org/ligand/{ligand}'})
    evidence = {'session_id': 's', 'selected_structure_id': '1MOX', 'component': {'id': 'old'},
                'mechanism': {'action': 'INHIBITOR'}, 'structures': [
                    {'structure_id': '1MOX', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['NAG']},
                    {'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['AQ4']}]}
    domain = structure_followup('1M17 是否包含 EGFR 的激酶结构域？它能用于激酶抑制剂对接吗？',
                                [{'target_structure_evidence': evidence}], 's')
    saved = domain['evidence']
    assert saved['selected_structure_id'] == '1M17' and 'component' not in saved and 'mechanism' not in saved
    query = '这个结构的共晶配体是什么？它结合在哪里？'
    answer = structure_followup(query, [{'target_structure_evidence': saved}], 's')
    assert answer['status'] == 'partial' and all(value in answer['content'] for value in ('1M17', 'AQ4', 'ERLOTINIB', '未核实'))
    assert 'NAG' not in answer['content']
    assert structure_followup(query, [{'target_structure_evidence': saved}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请运行对接。', [{'target_structure_evidence': saved}], 's') is None


def test_ligand_followup_uses_unique_retrieved_structure(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: None)
    monkeypatch.setattr('src.agent.ligand_binding_site.lookup_ligand_binding_site', lambda *args: None)
    evidence = {'session_id': 's', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['AQ4']}]}
    query = '这个结构的共晶配体是什么？它结合在哪里？'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 's')
    assert '1M17' in answer['content'] and 'AQ4' in answer['content'] and '未核实' in answer['content']
    assert answer['evidence']['selected_structure_id'] == '1M17'
    assert '无法唯一' in structure_followup(query, [{'target_structure_evidence': {
        **evidence, 'structures': evidence['structures'] * 2}}], 's')['content']
    assert '无法唯一' in structure_followup(query, [{'target_structure_evidence': {
        **evidence, 'selected_structure_id': '2QYK'}}], 's')['content']
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 's') is None


def test_ligand_site_clarification_resumes_only_owned_listed_structure(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: None)
    monkeypatch.setattr('src.agent.ligand_binding_site.lookup_ligand_binding_site', lambda *args: None)
    evidence = {'session_id': 's', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['AQ4']},
        {'structure_id': '1IVO', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['NAG']}]}
    query = '这个结构的共晶配体是什么？它结合在哪里？'
    clarification = structure_followup(query, [{'target_structure_evidence': evidence}], 's')
    assert '提供结构编号' in clarification['content']
    history = [{'target_structure_evidence': clarification['evidence']}]
    answer = structure_followup('1M17', history, 's')
    assert '1M17' in answer['content'] and 'AQ4' in answer['content'] and '未核实' in answer['content']
    assert 'NAG' not in answer['content'] and answer['evidence']['selected_structure_id'] == '1M17'
    assert '无法' in structure_followup('2QYK', history, 's')['content']
    assert structure_followup('1M17', history, 'other') is None
    assert structure_followup('1M17，然后执行对接。', history, 's') is None


def test_ligand_followup_reports_verified_residue_positions(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': 'ERLOTINIB', 'source_url': f'https://www.rcsb.org/ligand/{ligand}'})
    monkeypatch.setattr('src.agent.ligand_binding_site.lookup_ligand_binding_site', lambda *args: {
        'chain': 'A', 'entity': '1', 'ranges': [(29, 29), (101, 105)],
        'alignments': [{'entity_start': 6, 'reference_start': 695, 'length': 328}],
        'source_url': 'https://www.ebi.ac.uk/pdbe/api/pdb/entry/binding_sites/1m17/1'})
    evidence = {'session_id': 's', 'selected_structure_id': '1M17', 'structures': [
        {'structure_id': '1M17', 'target': 'EGFR', 'uniprot_id': 'P00533', 'ligand_ids': ['AQ4']}]}
    answer = structure_followup('这个结构的共晶配体是什么？它结合在哪里？',
                                [{'target_structure_evidence': evidence}], 's')
    assert all(value in answer['content'] for value in ('AQ4', 'PDBe', '29', '101–105', 'A'))
    assert '实体序列' in answer['content'] and 'ATP 口袋' not in answer['content']
    query = '这些位点换成 EGFR 的 UniProt 标准序列编号分别是什么？'
    translated = structure_followup(query, [{'target_structure_evidence': answer['evidence']}], 's')
    assert all(value in translated['content'] for value in ('P00533', '29→718', '101→790', '105→794'))
    assert structure_followup(query, [{'target_structure_evidence': answer['evidence']}], 'other')['status'] == 'partial'
    assert structure_followup(query.replace('EGFR', 'PDE4A'), [{'target_structure_evidence': answer['evidence']}], 's')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': answer['evidence']}], 's') is None


def test_docking_structure_choice_uses_prior_receptors_without_claiming_best():
    item = observation()
    item['data'][0]['recommended_structures'].append({
        'structure_id': '1M17', 'source': 'RCSB_PDB', 'structure_type': 'experimental',
        'resolution': 2.6, 'ligand_ids': []})
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    query = '这些结构里哪个最适合对接？请说明选择依据和还需要核实的信息。'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial'
    assert all(term in answer['content'] for term in ('2QYK', '2.1', '不能', '口袋', '配体'))
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


def test_atp_site_receptor_choice_uses_matching_owned_target_records():
    item = observation()
    item['data'][0]['gene_symbol'] = 'EGFR'
    item['data'][0]['recommended_structures'][0]['structure_id'] = '1M17'
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    query = '这些结构中哪一个适合做 EGFR 酪氨酸激酶 ATP 位点小分子对接？为什么？'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial'
    assert all(value in answer['content'] for value in ('1M17', 'EGFR', 'ATP', '未', '配体'))
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    mismatch = structure_followup(query.replace('EGFR', 'PDE4A'), [{'target_structure_evidence': evidence}], 'session-1')
    assert mismatch['status'] == 'partial' and '不一致' in mismatch['content']
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


def test_natural_atp_site_choice_uses_previous_structure_records_in_web_projection():
    from src.web.web_business import WebTurnProjection
    item = observation()
    item['data'][0]['gene_symbol'] = 'EGFR'
    item['data'][0]['recommended_structures'][0]['structure_id'] = '1M17'
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    history = [{'target_structure_evidence': evidence}]
    query = '这些结构中哪个适合用于 EGFR 的 ATP 位点小分子对接？选择前还需要检查什么？'
    answer = WebTurnProjection(query, 'session-1', history).followup(enable_tools=True)
    assert answer['status'] == 'partial'
    assert all(word in answer['content'] for word in ('1M17', 'EGFR', 'ATP', '口袋', '配体', '未执行对接'))
    assert structure_followup(query, history, 'other')['status'] == 'partial'
    assert '不一致' in structure_followup(query.replace('EGFR', 'PDE4A'), history, 'session-1')['content']
    assert structure_followup(query + '请执行对接。', history, 'session-1') is None


def test_named_structure_ligands_use_owned_records_and_component_names(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': 'component ' + ligand,
        'source_url': 'https://www.rcsb.org/ligand/' + ligand})
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [observation()]}, 'session-1')
    query = '2QYK 中有哪些配体？它们是什么，能确认是抑制剂吗？'
    result = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert result['status'] == 'partial'
    assert 'NPV' in result['content'] and 'component NPV' in result['content']
    assert '不能' in result['content'] and '抑制剂' in result['content']
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


def test_named_structure_ligands_checks_verified_target_mechanism(monkeypatch):
    from src.target_search.remote_clients import RcsbClient, ChemblClient
    key = 'AAKJLRGGTJKAMG-UHFFFAOYSA-N'
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': 'component ' + ligand, 'inchikey': key if ligand == 'NPV' else None,
        'source_url': 'https://www.rcsb.org/ligand/' + ligand})
    calls = []
    def mechanism(self, inchikey, accession):
        calls.append((inchikey, accession))
        return {'name': 'EXAMPLE', 'action': 'INHIBITOR',
                'source_url': 'https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL553/'}
    monkeypatch.setattr(ChemblClient, 'lookup_mechanism', mechanism)
    item = observation()
    item['data'][0]['uniprot_id'] = 'P00533'
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    query = '2QYK 中有哪些配体？它们是什么，能确认是抑制剂吗？'
    result = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert calls == [(key, 'P00533')]
    assert 'NPV' in result['content'] and 'EXAMPLE' in result['content']
    assert '抑制剂' in result['content'] and result['status'] == 'partial'
    retained = result['evidence']
    assert retained['selected_structure_id'] == '2QYK'
    assert retained['component']['id'] == 'NPV'
    assert retained['mechanism']['inchikey'] == key
    followup = structure_followup('那么这个结构可以作为 PDE4A 抑制剂对接的起点吗？',
                                  [{'target_structure_evidence': retained}], 'session-1')
    assert followup['status'] == 'completed' and '2QYK' in followup['content']
    assert '验证' in followup['content'] and '未' in followup['content']


def test_named_structure_ligands_does_not_select_among_multiple_mechanisms(monkeypatch):
    from src.target_search.remote_clients import RcsbClient, ChemblClient
    key = 'AAKJLRGGTJKAMG-UHFFFAOYSA-N'
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': ligand, 'inchikey': key,
        'source_url': 'https://www.rcsb.org/ligand/' + ligand})
    monkeypatch.setattr(ChemblClient, 'lookup_mechanism', lambda self, inchikey, accession: {
        'name': 'EXAMPLE', 'action': 'INHIBITOR',
        'source_url': 'https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL553/'})
    item = observation(); item['data'][0]['uniprot_id'] = 'P00533'
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    result = structure_followup('2QYK 中有哪些配体？它们是什么，能确认是抑制剂吗？',
                                [{'target_structure_evidence': evidence}], 'session-1')
    assert 'component' not in result['evidence'] and 'mechanism' not in result['evidence']


def test_unvalidated_structure_observation_cannot_be_reused():
    item = observation()
    item['quality']['validated'] = False
    assert accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1') is None


def test_ligand_list_followup_uses_only_owned_experimental_records():
    item = observation()
    item['data'][0]['recommended_structures'].append({
        'structure_id': '1M17', 'source': 'RCSB_PDB', 'structure_type': 'experimental',
        'resolution': 2.6, 'ligand_ids': []})
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    query = '这些结构中哪些含有配体？'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert '2QYK' in answer['content'] and 'NPV' in answer['content']
    assert '未记录不代表不存在' in answer['content'] and '角色' in answer['content']
    assert answer['evidence'] == evidence
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


def test_resolution_and_inhibitor_query_uses_verified_structure_without_claiming_global_ranking():
    item = observation()
    item['data'][0]['gene_symbol'] = 'EGFR'
    item['data'][0]['uniprot_id'] = 'P00533'
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    evidence.update(selected_structure_id='2QYK',
                    component={'id': 'NPV', 'inchikey': 'TEST-KEY'},
                    mechanism={'inchikey': 'TEST-KEY', 'uniprot_id': 'P00533', 'action': 'INHIBITOR'})
    query = '这些结构中分辨率最高且含有已知 EGFR 抑制剂的是哪个？'
    result = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert result['status'] == 'partial'
    assert all(term in result['content'] for term in ('2QYK', 'NPV', '分辨率', '无法', '未'))
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None
    evidence['mechanism']['uniprot_id'] = 'Q00000'
    result = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert 'NPV' not in result['content'] and '无法' in result['content']


def test_structure_selection_question_reuses_records_without_claiming_ligand_role():
    evidence = accepted_target_evidence({'trace_id': 'trace-1', 'tool_result_sequence': [observation()]}, 'session-1')
    query = '这些结构中哪一个含有激酶抑制剂、适合作为对接起点？请说明判断依据。'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial'
    assert '2QYK' in answer['content'] and 'NPV' in answer['content']
    assert '未核实' in answer['content'] and '抑制剂' in answer['content']
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请运行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None


def test_ligand_identity_followup_cites_owned_structure_title(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': '[Example] chemical name',
        'source_url': f'https://www.rcsb.org/ligand/{ligand}'})
    item = observation()
    structure = item['data'][0]['recommended_structures'][0]
    structure.update(source_url='https://www.rcsb.org/structure/2QYK',
                     title='Example kinase domain with inhibitor example', ligand_ids=['NPV'])
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    answer = structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？',
                                [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial'
    assert 'Example kinase domain with inhibitor example' in answer['content']
    assert 'https://www.rcsb.org/structure/2QYK' in answer['content']
    assert '［Example］ chemical name' in answer['content']
    assert 'https://www.rcsb.org/ligand/NPV' in answer['content']
    assert '作用' in answer['content'] and '尚未' in answer['content']
    assert structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？', [], 'session-1')['status'] == 'partial'
    assert 'Example kinase' not in structure_followup('NPV 是什么？它是 EGFR 抑制剂吗？',
                                            [{'target_structure_evidence': evidence}], 'session-1')['content']
    assert 'Example kinase' not in structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？',
                                            [{'target_structure_evidence': evidence}], 'other')['content']
    structure['source_url'] = 'https://www.rcsb.org/structure/1M17'
    wrong_source = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    assert 'title' not in wrong_source['structures'][0]
    assert structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？再预测 CCO 的活性。',
                              [{'target_structure_evidence': evidence}], 'session-1') is None


def test_ligand_followup_refreshes_uncited_local_structure_from_rcsb(monkeypatch):
    from src.target_search.remote_clients import RcsbClient
    calls = []
    def search(self, accession, *, pdb_id):
        calls.append((accession, pdb_id))
        return [{'structure_id': '2QYK', 'source_url': 'https://www.rcsb.org/structure/2QYK',
                 'title': 'Verified structure title', 'ligand_evidence': ['NPV']}]
    monkeypatch.setattr(RcsbClient, 'search', search)
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': 'Example component', 'source_url': 'https://www.rcsb.org/ligand/NPV'})
    item = observation()
    item['data'][0]['uniprot_id'] = 'P00533'
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    query = 'NPV 是什么？它是 PDE4A 抑制剂吗？'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert calls == [('P00533', '2QYK')]
    assert 'Verified structure title' in answer['content'] and 'Example component' in answer['content']
    assert answer['status'] == 'partial' and '尚未独立核实' in answer['content']
    assert answer['evidence']['structures'][0]['source_url'] == 'https://www.rcsb.org/structure/2QYK'
    assert answer['evidence']['selected_structure_id'] == '2QYK'

    monkeypatch.setattr(RcsbClient, 'search', lambda self, accession, *, pdb_id: [{
        'structure_id': '2QYK', 'source_url': 'https://www.rcsb.org/structure/2QYK',
        'title': 'wrong ligand', 'ligand_evidence': ['OTHER']}])
    rejected = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert rejected['status'] == 'partial' and 'wrong ligand' not in rejected['content']


def test_ligand_identity_reports_remote_failure(monkeypatch):
    from src.target_search.remote_clients import RcsbClient, RemoteSourceError
    def unavailable(self, ligand):
        raise RemoteSourceError('RCSB_PDB', 'timeout', retryable=True)
    monkeypatch.setattr(RcsbClient, 'lookup_component', unavailable)
    item = observation()
    item['data'][0]['recommended_structures'][0].update(
        source_url='https://www.rcsb.org/structure/2QYK', title='Example title')
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    answer = structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？',
                                [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial' and '查询暂不可用' in answer['content']
    assert 'component' not in answer['evidence']


def test_ligand_lookup_recovers_from_transient_source_timeout(monkeypatch):
    import requests
    from tests.test_target_search_fallback import _FakeRemoteResponse, _FakeRemoteSession
    from src.target_search import remote_clients
    session = _FakeRemoteSession(requests.Timeout('transient'), _FakeRemoteResponse(payload={
        'data': {'chem_comp': {'chem_comp': {'id': 'NPV', 'name': 'Example compound'},
            'rcsb_chem_comp_container_identifiers': {'comp_id': 'NPV'}}}}))
    monkeypatch.setattr(remote_clients.requests, 'Session', lambda: session)
    item = observation()
    item['data'][0]['recommended_structures'][0].update(
        source_url='https://www.rcsb.org/structure/2QYK', title='Example title')
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    answer = structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？',
                                [{'target_structure_evidence': evidence}], 'session-1')
    assert 'Example compound' in answer['content']
    assert len(session.calls) == 2


def test_ligand_identity_uses_verified_chembl_mechanism(monkeypatch):
    from src.target_search.remote_clients import RcsbClient, ChemblClient
    monkeypatch.setattr(RcsbClient, 'lookup_component', lambda self, ligand: {
        'id': ligand, 'name': 'Example chemical name', 'inchikey': 'AAKJLRGGTJKAMG-UHFFFAOYSA-N',
        'source_url': f'https://www.rcsb.org/ligand/{ligand}'})
    monkeypatch.setattr(ChemblClient, 'lookup_mechanism', lambda self, key, accession: {
        'molecule_id': 'CHEMBL553', 'name': 'ERLOTINIB', 'target_id': 'CHEMBL203',
        'action': 'INHIBITOR', 'source_url': 'https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL553/'})
    item = observation()
    item['data'][0]['uniprot_id'] = 'P00533'
    item['data'][0]['recommended_structures'][0].update(
        source_url='https://www.rcsb.org/structure/2QYK', title='Example title')
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    answer = structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？',
                                [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'completed'
    assert 'ERLOTINIB' in answer['content'] and '抑制剂' in answer['content']
    assert 'chembl' in answer['content'].lower()


def test_ligand_retry_reuses_same_session_component(monkeypatch):
    from src.target_search.remote_clients import RcsbClient, ChemblClient
    def forbidden(self, ligand):
        raise AssertionError('validated component should be reused')
    monkeypatch.setattr(RcsbClient, 'lookup_component', forbidden)
    monkeypatch.setattr(ChemblClient, 'lookup_mechanism', lambda self, key, accession: None)
    item = observation()
    item['data'][0]['uniprot_id'] = 'P00533'
    item['data'][0]['recommended_structures'][0].update(
        source_url='https://www.rcsb.org/structure/2QYK', title='Example title')
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    evidence['component'] = {'id': 'NPV', 'name': 'Example', 'inchikey': 'AAKJLRGGTJKAMG-UHFFFAOYSA-N',
                             'source_url': 'https://www.rcsb.org/ligand/NPV'}
    answer = structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？',
                                [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'partial' and 'Example' in answer['content']


def test_ligand_mechanism_recovers_from_transient_chembl_timeout(monkeypatch):
    import requests
    from tests.test_target_search_fallback import _FakeRemoteResponse, _FakeRemoteSession
    from src.target_search import remote_clients
    key = 'AAKJLRGGTJKAMG-UHFFFAOYSA-N'
    session = _FakeRemoteSession(requests.Timeout('transient'),
        _FakeRemoteResponse(payload={'page_meta': {'total_count': 1}, 'molecules': [{
            'molecule_chembl_id': 'CHEMBL553', 'pref_name': 'ERLOTINIB',
            'molecule_structures': {'standard_inchi_key': key}}]}),
        _FakeRemoteResponse(payload={'page_meta': {'total_count': 1}, 'mechanisms': [{
            'parent_molecule_chembl_id': 'CHEMBL553', 'target_chembl_id': 'CHEMBL203', 'action_type': 'INHIBITOR'}]}),
        _FakeRemoteResponse(payload={'target_chembl_id': 'CHEMBL203', 'target_type': 'SINGLE PROTEIN',
            'organism': 'Homo sapiens', 'target_components': [{'accession': 'P00533'}]}))
    monkeypatch.setattr(remote_clients.requests, 'Session', lambda: session)
    item = observation()
    item['data'][0]['uniprot_id'] = 'P00533'
    item['data'][0]['recommended_structures'][0].update(
        source_url='https://www.rcsb.org/structure/2QYK', title='Example title')
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    evidence['component'] = {'id': 'NPV', 'name': 'Example', 'inchikey': key,
                             'source_url': 'https://www.rcsb.org/ligand/NPV'}
    answer = structure_followup('NPV 是什么？它是 PDE4A 抑制剂吗？',
                                [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'completed' and len(session.calls) == 4


def test_docking_start_followup_uses_verified_structure_and_mechanism():
    item = observation()
    item['data'][0]['uniprot_id'] = 'P00533'
    item['data'][0]['recommended_structures'][0].update(
        source_url='https://www.rcsb.org/structure/2QYK', title='Example kinase inhibitor complex')
    evidence = accepted_target_evidence({'trace_id': 't', 'tool_result_sequence': [item]}, 'session-1')
    evidence['component'] = {'id': 'NPV', 'name': 'Example', 'inchikey': 'AAKJLRGGTJKAMG-UHFFFAOYSA-N',
                             'source_url': 'https://www.rcsb.org/ligand/NPV'}
    evidence['mechanism'] = {'molecule_id': 'CHEMBL553', 'name': 'Example', 'target_id': 'CHEMBL203',
                             'action': 'INHIBITOR', 'inchikey': evidence['component']['inchikey'],
                             'uniprot_id': 'P00533',
                             'source_url': 'https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL553/'}
    query = '那么这个结构可以作为 PDE4A 抑制剂对接的起点吗？'
    answer = structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')
    assert answer['status'] == 'completed' and '2QYK' in answer['content']
    assert '准备' in answer['content'] and '验证' in answer['content']
    assert '未' in answer['content'] and '对接' in answer['content']
    evidence['structures'].append({'structure_id': '1M17', 'target': 'PDE4A',
                                   'uniprot_id': 'P00533', 'ligand_ids': ['OTHER']})
    evidence['selected_structure_id'] = '2QYK'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')['status'] == 'completed'
    evidence['selected_structure_id'] = '1M17'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')['status'] == 'partial'
    evidence['selected_structure_id'] = '2QYK'
    assert structure_followup(query, [], 'session-1')['status'] == 'partial'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'other')['status'] == 'partial'
    assert structure_followup(query + '请执行对接。', [{'target_structure_evidence': evidence}], 'session-1') is None
    evidence['mechanism']['uniprot_id'] = 'P06276'
    assert structure_followup(query, [{'target_structure_evidence': evidence}], 'session-1')['status'] == 'partial'
