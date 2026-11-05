import pytest

from src.target_search.remote_clients import RcsbClient, RemoteSourceError
from src.web.web_business import WebTurnProjection


class Http:
    def __init__(self, entry):
        self.entry = entry

    def request_json(self, source, method, url, **kwargs):
        assert source == 'RCSB_PDB'
        assert kwargs['json']['variables']['ids'] == ['6FG5']
        return {'data': {'entries': [self.entry]}}


def test_entry_origin_returns_each_polymer_species():
    entry = {'rcsb_id': '6FG5', 'polymer_entities': [
        {'rcsb_id': '6FG5_1', 'rcsb_entity_source_organism': [
            {'ncbi_scientific_name': 'Schistosoma mansoni', 'ncbi_taxonomy_id': 6183}]},
        {'rcsb_id': '6FG5_2', 'rcsb_entity_source_organism': None},
    ]}
    assert RcsbClient(Http(entry)).lookup_entry_origins('6fg5') == [
        {'entity_id': '6FG5_1', 'organisms': [('Schistosoma mansoni', 6183)]},
        {'entity_id': '6FG5_2', 'organisms': []},
    ]


def test_entry_origin_rejects_mismatched_entry():
    with pytest.raises(RemoteSourceError):
        RcsbClient(Http({'rcsb_id': '1M17', 'polymer_entities': []})).lookup_entry_origins('6FG5')


@pytest.mark.parametrize('query', [
    '查询 PDB 6FG5 的结构来源和物种。',
    '我想用 PDB 6FG5 的 PDE4A 结构与罗氟司特做对接。这个结构是人源的吗？对接分数可以直接说明它对人体 PDE4A 的活性吗？先核对结构来源并解释，不要运行对接。',
])
def test_origin_consultation_uses_rcsb_without_docking(monkeypatch, query):
    monkeypatch.setattr(RcsbClient, 'lookup_entry_origins', lambda self, pdb: [
        {'entity_id': pdb + '_1', 'organisms': [('Schistosoma mansoni', 6183)]}])
    result = WebTurnProjection(query, 'session', []).followup(enable_tools=True)
    assert 'Schistosoma mansoni' in result['content']
    assert 'https://www.rcsb.org/structure/6FG5' in result['content']
    if '对接' in query:
        assert '不能' in result['content'] and '未执行对接' in result['content']


def test_origin_then_incomplete_docking_reports_both(monkeypatch):
    monkeypatch.setattr(RcsbClient, 'lookup_entry_origins', lambda self, pdb: [
        {'entity_id': pdb + '_1', 'organisms': [('Schistosoma mansoni', 6183)]}])
    query = '查询 PDB 6FG5 的结构来源和物种。然后执行对接。'
    result = WebTurnProjection(query, 'session', []).followup(enable_tools=True)
    assert result['status'] == 'partial'
    assert all(text in result['content'] for text in ('Schistosoma mansoni', '分子对接页面', '配体', '盒', '未执行对接'))


def test_origin_docking_with_other_actions_is_not_swallowed():
    query = '查询 PDB 6FG5 的结构来源和物种。然后执行对接。再计算 CCO 的 logP。'
    assert WebTurnProjection(query, 'session', []).followup(enable_tools=True) is None


def test_origin_query_never_uses_disabled_tools(monkeypatch):
    def forbidden(*args):
        raise AssertionError('disabled tools must not run')
    monkeypatch.setattr(RcsbClient, 'lookup_entry_origins', forbidden)
    result = WebTurnProjection('查询 PDB 6FG5 的结构来源和物种。', 'session', []).followup(enable_tools=False)
    assert result['status'] == 'not_calculated'
    mixed = WebTurnProjection('查询 PDB 6FG5 的结构来源和物种。然后执行对接。', 'session', []).followup(enable_tools=False)
    assert '未查询' in mixed['content'] and '未执行对接' in mixed['content']


def test_mixed_or_missing_origins_are_not_called_human(monkeypatch):
    monkeypatch.setattr(RcsbClient, 'lookup_entry_origins', lambda self, pdb: [
        {'entity_id': pdb + '_1', 'organisms': [('Homo sapiens', 9606)]},
        {'entity_id': pdb + '_2', 'organisms': []},
    ])
    query = ('我想用 PDB 1M17 的 EGFR 结构与罗氟司特做对接。这个结构是人源的吗？'
             '对接分数可以直接说明它对人体 EGFR 的活性吗？先核对结构来源并解释，不要运行对接。')
    answer = WebTurnProjection(query, 'session', []).followup(enable_tools=True)['content']
    assert '未提供物种信息' in answer and '结合具体链确认' in answer
    assert '来源均为人' not in answer


@pytest.mark.parametrize('query', [
    'PDB 6FG5 是哪个物种的蛋白？',
    '6FG5 结构来自什么物种？',
    '请问 PDB 6FG5 是人源的吗？',
])
def test_natural_origin_questions_use_rcsb(monkeypatch, query):
    monkeypatch.setattr(RcsbClient, 'lookup_entry_origins', lambda self, pdb: [
        {'entity_id': pdb + '_1', 'organisms': [('Schistosoma mansoni', 6183)]}])
    result = WebTurnProjection(query, 's', []).followup(enable_tools=True)
    assert 'Schistosoma mansoni' in result['content']
    if '人源' in query:
        assert '不是人' in result['content']


def test_human_origin_followup_is_bound_to_last_session_evidence(monkeypatch):
    monkeypatch.setattr(RcsbClient, 'lookup_entry_origins', lambda self, pdb: [
        {'entity_id': pdb + '_1', 'organisms': [('Schistosoma mansoni', 6183)]}])
    first = WebTurnProjection('查询 PDB 6FG5 的结构来源和物种。', 's', [])
    first.followup(enable_tools=True)
    follow = WebTurnProjection('它是人源的吗？', 's', [first.entry])
    assert '不是人' in follow.followup(enable_tools=True)['content']
    assert WebTurnProjection('它是人源的吗？', 'other', [first.entry]).followup(enable_tools=True) is None
    assert WebTurnProjection('它是人源的吗？然后对接。', 's', [first.entry]).followup(enable_tools=True) is None
