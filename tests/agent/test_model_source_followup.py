from copy import deepcopy

import pytest

from src.admet.backend import ADMETAIBackend
from src.agent.candidate_evidence_followup import accepted_candidate_evidence
from src.web.web_business import WebTurnProjection
from tests.agent.test_candidate_evidence_followup import observation
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for


QUESTION = ('请核查刚才这个 hERG 模型的训练标签定义和原始文献，给出可访问来源。'
            '找不到就说未核实，不要凭经验补 IC50 截点，也不要重新预测。')
URL = 'https://tdcommons.ai/single_pred_tasks/tox/#herg-blockers'


def evidence():
    class Row:
        def to_dict(self):
            return {'hERG': .1}
    backend = ADMETAIBackend(model=object(), weights_id='test', endpoint_info={'hERG': {
        'task_type': 'classification', 'category': 'Toxicity', 'unit': 'probability', 'dataset_url': URL}})
    row = backend._row_from_prediction('CCO', 'm1', Row())
    return accepted_candidate_evidence({'trace_id': 't', 'tool_result_sequence': [
        observation('admet_predictor', [row], 'e')]}, '预测 CCO 的 hERG。', 'owner')


def test_prediction_keeps_model_to_dataset_link():
    record = evidence()['candidates'][0]['admet']
    assert record['model_version'] == '1.4.0'
    assert record['endpoints']['hERG']['dataset_url'] == URL


def test_source_followup_uses_owned_record_and_preserves_predictions(monkeypatch):
    from src.literature import model_sources
    calls = []
    def verify():
        calls.append(True)
        return {'content': '标签已核实；IC50 截点未核实。', 'status': 'partial',
                'source_record': {'dataset_url': URL}}
    monkeypatch.setattr(model_sources, 'verify_herg_sources', verify)
    prior = evidence()
    before = deepcopy(prior)
    view = WebTurnProjection(QUESTION, 'owner', [{'candidate_evidence': prior}])
    result = view.followup(enable_tools=True)
    assert calls == [True] and 'IC50 截点未核实' in result['content']
    assert prior == before == view.entry['candidate_evidence']
    assert view.entry['model_source_evidence']['session_id'] == 'owner'


@pytest.mark.parametrize('case', ['foreign', 'disabled', 'no_link', 'no_history'])
def test_no_source_claim_without_permission_or_bound_model(case, monkeypatch):
    from src.literature import model_sources
    monkeypatch.setattr(model_sources, 'verify_herg_sources', lambda: pytest.fail('must not fetch'))
    prior = evidence()
    if case == 'no_link':
        prior['candidates'][0]['admet']['endpoints']['hERG'].pop('dataset_url', None)
    result = WebTurnProjection(QUESTION, 'foreign' if case == 'foreign' else 'owner',
        [] if case == 'no_history' else [{'candidate_evidence': prior}]).followup(enable_tools=case != 'disabled')
    assert result['status'] != 'completed' and '未核实' in result['content']


def test_source_request_does_not_swallow_additional_science():
    result = WebTurnProjection(QUESTION + '再计算 CCO 的分子量。', 'owner',
        [{'candidate_evidence': evidence()}]).followup(enable_tools=True)
    assert result is None


@pytest.mark.parametrize('ending', ['', '？'])
def test_source_question_punctuation_is_optional(ending):
    question = QUESTION.split('。')[0] + ending
    result = WebTurnProjection(question, 'owner', []).followup(enable_tools=True)
    assert '未核实' in result['content']


def test_dataset_parser_excludes_neighbor_dataset_cutoffs():
    from src.literature.model_sources import parse_herg_section
    page = '''<h3 id="herg-blockers">hERG blockers</h3>
    <p>Task Description: Binary classification. Given a drug SMILES string,
    predict whether it blocks (1) or not blocks (0).</p>
    <a href="https://pubs.acs.org/doi/abs/10.1021/acs.molpharmaceut.6b00471?tracking=x">Reference</a>
    <h3 id="herg-central">hERG Central</h3><p>IC50 less than 10µM</p>'''
    record = parse_herg_section(page)
    assert record['label_definition'] == '1=blocker; 0=non-blocker'
    assert record['doi'] == '10.1021/acs.molpharmaceut.6b00471'
    assert '10µM' not in record['section_text'] and 'tracking' not in str(record)
    assert parse_herg_section(page.replace('blocks (1)', 'different labels')) is None


def test_verification_records_source_without_claiming_fulltext(monkeypatch):
    from src.literature import model_sources
    monkeypatch.setattr(model_sources, '_fetch_dataset_page', lambda: '''
        <h3 id="herg-blockers">hERG blockers</h3><p>Binary classification.
        blocks (1) or not blocks (0)</p>
        <a href="https://pubs.acs.org/doi/abs/10.1021/acs.molpharmaceut.6b00471">Reference</a>''')
    monkeypatch.setattr(model_sources.EuropePMCClient, 'search', lambda *args, **kwargs: {
        'articles': [{'doi': None}, {'doi': '10.1021/acs.molpharmaceut.6b00471',
        'title': 'Reference title', 'year': '2016', 'pmid': '123',
        'url': 'https://pubmed.ncbi.nlm.nih.gov/123/'}]})
    result = model_sources.verify_herg_sources()
    assert 'Reference title' in result['content'] and '未核实' in result['content']
    assert result['source_record']['fulltext_status'] == 'not_retrieved'


def test_source_record_survives_full_web_turn(actual_app, monkeypatch):
    import asyncio
    from src.literature import model_sources
    monkeypatch.setattr(model_sources, 'verify_herg_sources', lambda: {
        'content': '标签已核实；IC50 截点未核实。', 'status': 'partial',
        'source_record': {'dataset_url': URL}})
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                prior = evidence()
                prior['session_id'] = sender.scope['agent_session_id']
                sender.scientific_memory[:] = [{'candidate_evidence': deepcopy(prior)}]
                frames = await socket.turn({'message': QUESTION, 'enable_rag': False})
                assert frames[-1]['content'] == '标签已核实；IC50 截点未核实。'
                assert sender.scientific_memory[-1]['candidate_evidence'] == prior
                assert sender.scientific_memory[-1]['model_source_evidence']['dataset_url'] == URL
                assert not bundle.calls and not bundle.protocol_errors
    asyncio.run(run())
