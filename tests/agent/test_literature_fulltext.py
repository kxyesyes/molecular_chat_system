import asyncio
import json

import pytest

from src.literature.fulltext import (select_passages, validate_fulltext_claims, request_fulltext_analysis,
                                     kinetic_method_evidence, kinetic_pair_evidence)
from src.target_search.remote_clients import RemoteSourceError


def _payload(pmcid='PMC456'):
    return [{'documents': [{'id': pmcid, 'passages': [
        {'offset': 10, 'infons': {'section_type': 'RESULTS', 'type': 'title_2'}, 'text': 'Kinase measurements'},
        {'offset': 100, 'infons': {'section_type': 'RESULTS', 'type': 'paragraph'},
         'text': 'We estimated erlotinib inhibition constants by enzyme kinetics.'},
        {'offset': 200, 'infons': {'section_type': 'RESULTS', 'type': 'paragraph'},
         'text': 'The L858R/T790M mutant showed an estimated inhibition constant of 41 nM.'},
        {'offset': 300, 'infons': {'section_type': 'REF', 'type': 'paragraph'},
         'text': 'A cited article mentioned T790M.'},
    ]}]}]


def test_select_passages_binds_document_and_preserves_preceding_context():
    result = select_passages(_payload(), 'PMC456', '分析 EGFR T790M 对厄洛替尼的影响。')
    assert [item['offset'] for item in result] == [100, 200]
    assert all(item['section'] == 'RESULTS' and item['heading'] == 'Kinase measurements' for item in result)
    assert 'enzyme kinetics' in result[0]['text'] and '41 nM' in result[1]['text']


def test_select_passages_rejects_document_mismatch_and_missing_identifier():
    with pytest.raises(RemoteSourceError):
        select_passages(_payload('PMC999'), 'PMC456', '分析 T790M。')
    assert select_passages(_payload(), 'PMC456', '分析 F999X。') == []


def test_passage_selection_prioritizes_relevant_headings_over_first_keyword_hits():
    paragraphs = [{'offset': i * 100, 'infons': {'section_type': 'RESULTS', 'type': 'paragraph'},
                   'text': 'EGFR variants were tested at several ATP concentrations.'} for i in range(1, 6)]
    paragraphs += [
        {'offset': 600, 'infons': {'section_type': 'RESULTS', 'type': 'title_2'},
         'text': 'ATP affinity explains inhibitor sensitivity'},
        {'offset': 700, 'infons': {'section_type': 'RESULTS', 'type': 'paragraph'},
         'text': 'EGFR variants with lower ATP affinity showed greater inhibitor sensitivity.'}]
    payload = [{'documents': [{'id': 'PMC456', 'passages': paragraphs}]}]
    result = select_passages(payload, 'PMC456', '说明 EGFR 变体的 ATP 亲和力与抑制剂敏感性的关系。')
    assert 700 in [item['offset'] for item in result]
    assert len(result) <= 4


def test_kinetic_method_evidence_requires_explicit_measurement_and_estimation():
    passage = {'offset': 100, 'section': 'RESULTS', 'heading': 'Kinetics',
        'text': ('We undertook steady state kinetic measurements of TKD activities to measure ATP constants '
                 'and inhibition constants (KI) for erlotinib using the Cheng–Prusoff equation.')}
    evidence = kinetic_method_evidence([passage])
    assert evidence['offset'] == 100 and evidence['quote'] in passage['text']
    assert 'Cheng–Prusoff equation' in evidence['quote']
    assert kinetic_method_evidence([{**passage, 'text': passage['text'].replace('kinetic measurements', 'crystal structures')}]) is None
    assert kinetic_method_evidence([{**passage, 'text': passage['text'].replace('using the Cheng–Prusoff equation', 'using direct binding')}]) is None


def test_kinetic_pair_evidence_requires_explicit_groups_values_and_order():
    passage = {'offset': 200, 'section': 'RESULTS', 'heading': 'Kinetics', 'text': (
        'Equivalent experiments with L858R- and L858R/T790M-mutated TKDs (Supplementary Fig. 3a, b) '
        'gave KI values of 4.7 ± 2.3 and 41 ± 14 nM respectively, indicating reduced affinity.')}
    result = kinetic_pair_evidence([passage], '分析 T790M。')
    assert result['offset'] == 200
    assert result['groups'] == [('L858R', '4.7 ± 2.3', 'nM'), ('L858R/T790M', '41 ± 14', 'nM')]
    assert kinetic_pair_evidence([{**passage, 'text': passage['text'].replace('respectively', 'approximately')}], '分析 T790M。') is None
    assert kinetic_pair_evidence([passage], '分析 F999X。') is None


def test_fulltext_claims_require_exact_passage_quote_and_supported_numbers():
    passages = select_passages(_payload(), 'PMC456', '分析 T790M。')
    claim = {'offset': 200, 'text': '该突变体的估算抑制常数为41 nM。',
             'quote': 'L858R/T790M mutant showed an estimated inhibition constant of 41 nM'}
    data = {'claims': [claim]}
    assert validate_fulltext_claims(json.dumps(data, ensure_ascii=False), passages, '分析 T790M。') == data['claims']
    claim['text'] = '该突变体的估算抑制常数为99 nM。'
    assert validate_fulltext_claims(json.dumps(data, ensure_ascii=False), passages, '分析 T790M。') is None
    claim['text'] = '该突变体的估算抑制常数为41 nM。'
    claim['quote'] = 'mutant showed an estimated inhibition constant of 41 nM'
    assert validate_fulltext_claims(json.dumps(data, ensure_ascii=False), passages, '分析 T790M。') is None
    claim['quote'] = 'L858R/T790M mutant showed an estimated inhibition constant of 41 nM'
    claim['offset'] = 100
    assert validate_fulltext_claims(json.dumps(data, ensure_ascii=False), passages, '分析 T790M。') is None


def test_fulltext_claims_preserve_verified_claims_and_original_whitespace():
    passages = [{'offset': 1, 'section': 'RESULTS', 'heading': 'Test',
                 'text': 'The measured inhibition constant was 4.7\u2009±\u20092.3 nM in this experiment.'}]
    valid = {'offset': 1, 'text': '抑制常数为4.7 ± 2.3 nM。',
             'quote': 'constant was 4.7 ± 2.3 nM in this experiment'}
    bad = {'offset': 1, 'text': '抑制常数为99 nM。', 'quote': 'constant was 4.7 ± 2.3 nM in this experiment'}
    actual = validate_fulltext_claims(json.dumps({'claims': [valid, bad]}, ensure_ascii=False), passages, '分析结果。')
    assert len(actual) == 1 and actual[0]['text'] == valid['text']
    assert actual[0]['quote'] in passages[0]['text'] and '\u2009' in actual[0]['quote']


def test_fulltext_quote_normalizes_dash_only_and_returns_source_characters():
    passages = [{'offset': 1, 'section': 'RESULTS', 'heading': 'Methods',
                 'text': 'Inhibition constants were estimated using the Cheng–Prusoff equation.'}]
    claim = {'offset': 1, 'text': '抑制常数通过酶动力学估算。',
             'quote': 'constants were estimated using the Cheng-Prusoff equation'}
    result = validate_fulltext_claims(json.dumps({'claims': [claim]}, ensure_ascii=False), passages, '论文中的 Ki 如何得到？')
    assert result and result[0]['quote'] in passages[0]['text']
    assert 'Cheng–Prusoff' in result[0]['quote']
    claim['quote'] = 'constants were measured directly using the Cheng-Prusoff equation'
    assert validate_fulltext_claims(json.dumps({'claims': [claim]}, ensure_ascii=False), passages, '论文中的 Ki 如何得到？') is None


def test_fulltext_recovers_comparison_excerpt_only_from_original_sentence():
    text = ('Equivalent experiments with L858R- and L858R/T790M-mutated TKDs gave KI values of '
            '4.7 ± 2.3 and 41 ± 14 nM respectively, indicating reduced drug binding affinity in the mutant '
            'in this particular biochemical measurement under the reported experimental conditions.')
    passage = {'offset': 1, 'section': 'RESULTS', 'heading': 'Kinetics', 'text': text}
    claim = {'offset': 1, 'text': 'L858R 为4.7 ± 2.3 nM，L858R/T790M 为41 ± 14 nM。',
             'quote': 'The model changed and extended the original quote so it is not an exact contiguous '
                      'quotation and it is far longer than twenty five words, which should not be displayed.'}
    result = validate_fulltext_claims(json.dumps({'claims': [claim]}, ensure_ascii=False), [passage], '分析 T790M。')
    assert len(result) == 1
    excerpt = result[0]['quote']
    assert text.startswith(excerpt) and len(excerpt.split()) <= 25
    assert all(value in excerpt for value in ('L858R-', 'T790M', '4.7', '2.3', '41', '14', 'respectively'))
    assert excerpt.endswith('respectively,')


def test_fulltext_analysis_returns_source_bound_model_interpretation():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    passages = select_passages(_payload(), 'PMC456', '分析 T790M。')
    class Model:
        def __init__(self): self.calls = 0
        async def decide(self, messages, **kwargs):
            self.calls += 1
            data = json.loads(messages[-1]['content'])
            if 'claims' in data:
                assert data['passages'][1]['offset'] == 200
                value = {'assessments': [{'index': 0, 'category': 'direct'}]}
            else:
                assert 'exactly one' in messages[0]['content']
                value = {'claims': [{'offset': 200, 'text': '该突变体的估算抑制常数为41 nM。',
                                     'quote': 'L858R/T790M mutant showed an estimated inhibition constant of 41 nM'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'fulltext', {})
    model = Model()
    answer = asyncio.run(request_fulltext_analysis(model, 'native', '分析 T790M。', 'PMC456', passages))
    assert model.calls == 2 and '41 nM' in answer
    assert 'https://pmc.ncbi.nlm.nih.gov/articles/PMC456/' in answer
    assert '模型' in answer and '核对' in answer


@pytest.mark.parametrize('repaired', [True, False])
def test_fulltext_extraction_repairs_schema_once_with_same_sources(repaired):
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    passages = select_passages(_payload(), 'PMC456', '分析 T790M。')
    class Model:
        def __init__(self): self.calls = []
        async def decide(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            data = json.loads(messages[-1]['content'])
            assert data['passages'] == passages
            if len(self.calls) == 1 or not repaired:
                return DecisionResponse(None, AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'rejected',
                    {'reason': 'invalid_decision_schema', 'schema_issues': [
                        {'path': 'decision.finish.evidence_ids', 'type': 'missing'}]}), None, {})
            if 'claims' in data:
                value = {'assessments': [{'index': 0, 'category': 'direct'}]}
            else:
                assert 'invalid_decision_schema' in messages[0]['content']
                assert kwargs['timeout_seconds'] <= self.calls[0][1]['timeout_seconds']
                value = {'claims': [{'offset': 200, 'text': '该突变体的估算抑制常数为41 nM。',
                         'quote': 'L858R/T790M mutant showed an estimated inhibition constant of 41 nM'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'fulltext', {})
    model = Model()
    answer = asyncio.run(request_fulltext_analysis(model, 'native', '分析 T790M。', 'PMC456', passages))
    assert len(model.calls) == (3 if repaired else 2)
    assert ('41 nM' in answer) if repaired else answer is None


def test_fulltext_analysis_does_not_display_model_misattributed_numeric_claim():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    passages = [{'offset': 1, 'section': 'RESULTS', 'heading': 'Enzyme kinetics',
                 'text': 'L858R and L858R/T790M enzymes gave KI values of 4.7 and 41 nM respectively.'}]
    incorrect = 'L858R/T790M has both KI values of 4.7 and 41 nM.'
    class Model:
        async def decide(self, messages, **kwargs):
            data = json.loads(messages[-1]['content'])
            value = ({'assessments': [{'index': 0, 'category': 'direct'}]} if 'claims' in data else
                     {'claims': [{'offset': 1, 'text': incorrect,
                                  'quote': 'L858R and L858R/T790M enzymes gave KI values of 4.7 and 41 nM respectively'}]})
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'fulltext', {})
    answer = asyncio.run(request_fulltext_analysis(Model(), 'native', '分析 T790M。', 'PMC456', passages))
    assert incorrect not in answer
    assert 'L858R and L858R/T790M enzymes gave KI values' in answer
    assert '模型' in answer and '原文片段' in answer
    assert 'respectively…' in answer


def test_web_fulltext_request_retrieves_open_article_and_uses_configured_model(monkeypatch, tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import BoundedJsonClient
    from pi_web_support import make_web_handler, run_web_turn
    query = '请分析 PMID 123 的全文，说明 T790M 对抑制常数的影响。'
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, value, **kwargs: {'hit_count': 1, 'articles': [
        {'pmid': '123', 'pmcid': 'PMC456', 'open_access': True, 'title': 'Study', 'year': '2022'}]})
    def request(self, source, method, url, **kwargs):
        assert source == 'PMC_BioC' and url.endswith('/PMC456/unicode')
        return _payload()
    monkeypatch.setattr(BoundedJsonClient, 'request_json', request)
    class Model:
        async def decide(self, messages, **kwargs):
            data = json.loads(messages[-1]['content'])
            assert data['question'] == query
            value = ({'assessments': [{'index': 0, 'category': 'direct'}]} if 'claims' in data else
                     {'claims': [{'offset': 200, 'text': '估算抑制常数为41 nM。',
                                  'quote': 'L858R/T790M mutant showed an estimated inhibition constant of 41 nM'}]})
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'fulltext', {})
    history = []
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'fulltext.sqlite'), model=Model())
    result, _ = run_web_turn(handler, query, history=history, session='s')
    assert result['status'] == 'partial' and '41 nM' in result['content']
    assert 'https://pmc.ncbi.nlm.nih.gov/articles/PMC456/' in result['content']
    assert history[-1]['literature_fulltext']['pmcid'] == 'PMC456'


def test_web_fulltext_respects_tool_permission_and_unavailable_open_access(monkeypatch, tmp_path):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    calls = []
    def search(self, query, **kwargs):
        calls.append(query)
        return {'hit_count': 1, 'articles': [{'pmid': '123', 'pmcid': None, 'open_access': False}]}
    monkeypatch.setattr(EuropePMCClient, 'search', search)
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'not-open.sqlite'))
    query = '请分析 PMID 123 的全文，说明 T790M 的实验结果。'
    disabled, _ = run_web_turn(handler, query, enable_tools=False)
    assert '未启用' in disabled['content'] and not calls
    unavailable, _ = run_web_turn(handler, query)
    assert len(calls) == 1 and '开放全文' in unavailable['content']
    assert unavailable['status'] == 'partial'


@pytest.mark.parametrize('question', [
    '请获取 PMID 27379394 的开放全文，核对其中 hERG 阳性样本的 IC50 标准。如果全文不可用，请明确说明未核实，不要根据摘要推断阈值。',
    '查阅 PMID 27379394 的全文；我想知道论文中的 hERG 标签依据什么实验标准。不要重新预测。',
    '请检索 PMID 27379394 的论文全文，解释其中的实验方法？',
])
def test_natural_fulltext_request_is_not_activity_prediction(question):
    from src.web.web_business import WebTurnProjection
    answer = WebTurnProjection(question, 'owner', []).followup(enable_tools=True)
    assert answer['fulltext_pmid'] == '27379394'
    assert answer['fulltext_question'] == question
    disabled = WebTurnProjection(question, 'owner', []).followup(enable_tools=False)
    assert '未启用' in disabled['content'] and 'fulltext_pmid' not in disabled


@pytest.mark.parametrize('extra', ['再预测 CCO 的活性。', '然后计算 CCO 的分子量。', '执行分子对接。',
                                  '请帮我预测 CCO 的活性。', '并比较PMID 123 的结果。'])
def test_fulltext_request_does_not_swallow_new_scientific_operation(extra):
    from src.web.web_business import WebTurnProjection
    question = '请获取 PMID 27379394 的开放全文，核对其中 hERG 的 IC50 标准。' + extra
    assert WebTurnProjection(question, 'owner', []).followup(enable_tools=True) is None


@pytest.mark.parametrize('failure', [False, True])
def test_fulltext_lookup_distinguishes_missing_access_and_service_failure(monkeypatch, tmp_path, failure):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import RemoteSourceError
    from pi_web_support import make_web_handler, run_web_turn
    calls = []
    def search(self, query, **kwargs):
        calls.append(query)
        if failure:
            raise RemoteSourceError('EuropePMC', 'timeout', retryable=True)
        return {'hit_count': 1, 'articles': [{'pmid': '27379394', 'pmcid': None, 'open_access': False}]}
    monkeypatch.setattr(EuropePMCClient, 'search', search)
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'fulltext-status.sqlite'))
    history = []
    result, _ = run_web_turn(handler,
        '请获取 PMID 27379394 的开放全文，核对其中 hERG 阳性样本的 IC50 标准。'
        '如果全文不可用，请明确说明未核实，不要根据摘要推断阈值。', history=history, session='s')
    assert calls == ['EXT_ID:27379394 AND SRC:MED']
    assert result['status'] == 'partial' and '未核实' in result['content']
    assert ('暂不可用' in result['content']) == failure
    assert ('未提供' in result['content']) != failure
    lookup = history[-1]['literature_fulltext_lookup']
    assert lookup['status'] == ('retrieval_failed' if failure else 'no_open_fulltext')
    assert lookup['session_id'] == 's' and lookup['pmid'] == '27379394'
    assert 'literature_fulltext' not in history[-1]


def test_web_fulltext_rejects_mismatched_bioc_document(monkeypatch, tmp_path):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import BoundedJsonClient
    from pi_web_support import make_web_handler, run_web_turn
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query, **kwargs: {'hit_count': 1, 'articles': [
        {'pmid': '123', 'pmcid': 'PMC456', 'open_access': True}]})
    monkeypatch.setattr(BoundedJsonClient, 'request_json', lambda self, *args, **kwargs: _payload('PMC999'))
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'wrong-document.sqlite'))
    result, _ = run_web_turn(handler, '请分析 PMID 123 的全文，说明 T790M 的实验结果。')
    assert result['status'] == 'partial' and '未核对正文' in result['content']
    assert '41 nM' not in result['content'] and 'PMC999' not in result['content']


def test_fulltext_followup_reuses_owned_context_and_checks_source_hashes(monkeypatch, tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import BoundedJsonClient
    from pi_web_support import make_web_handler, run_web_turn
    question = '请分析 PMID 123 的全文，说明 T790M 对抑制常数的影响。'
    followup = '这两个数值分别对应哪个突变？论文中的 Ki 是直接测量的 Kd 吗？'
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, value, **kwargs: {'hit_count': 1, 'articles': [
        {'pmid': '123', 'pmcid': 'PMC456', 'open_access': True}]})
    state = {'changed': False}
    def request(self, source, method, url, **kwargs):
        payload = _payload()
        if state['changed']:
            payload[0]['documents'][0]['passages'][2]['text'] += ' Updated.'
        return payload
    monkeypatch.setattr(BoundedJsonClient, 'request_json', request)
    class Model:
        def __init__(self): self.calls = []
        async def decide(self, messages, **kwargs):
            data = json.loads(messages[-1]['content'])
            self.calls.append(data)
            value = ({'assessments': [{'index': 0, 'category': 'direct'}]} if 'claims' in data else
                     {'claims': [{'offset': 200, 'text': '估算抑制常数为41 nM。',
                                  'quote': 'L858R/T790M mutant showed an estimated inhibition constant of 41 nM'}]})
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'fulltext', {})
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'followup-fulltext.sqlite'), model=model)
    history = []
    first, _ = run_web_turn(handler, question, history=history, session='s')
    assert '41 nM' in first['content']
    second, _ = run_web_turn(handler, followup, history=history, session='s')
    assert second['status'] == 'partial' and 'PMC456' in second['content']
    assert question in model.calls[-2]['question'] and followup in model.calls[-2]['question']
    assert 'SMILES' not in second['content']
    before = len(model.calls)
    state['changed'] = True
    changed, _ = run_web_turn(handler, followup, history=history, session='s')
    assert '正文已变化' in changed['content'] and len(model.calls) == before


def test_fulltext_followup_rejects_foreign_session_and_disabled_tools():
    from src.web.web_business import WebTurnProjection
    history = [{'user': '请分析 PMID 123 的全文，说明 T790M 的实验结果。',
                'literature_fulltext': {'session_id': 'a', 'pmid': '123', 'pmcid': 'PMC456', 'passages': [
                    {'offset': 1, 'section': 'RESULTS', 'heading': '', 'sha256': 'a' * 64}]}}]
    query = '这两个数值分别对应哪个突变？论文中的 Ki 是直接测量的 Kd 吗？'
    foreign = WebTurnProjection(query, 'b', history).followup(enable_tools=True)
    assert foreign['status'] == 'partial' and not foreign.get('fulltext_pmid')
    disabled = WebTurnProjection(query, 'a', history).followup(enable_tools=False)
    assert '未启用' in disabled['content'] and not disabled.get('fulltext_pmid')
    assert WebTurnProjection(query + '请执行对接。', 'a', history).followup(enable_tools=True) is None


def test_generic_number_followup_without_paper_context_is_not_intercepted():
    from src.web.web_business import WebTurnProjection
    assert WebTurnProjection('这两个数值分别是多少？', 'a', []).followup(enable_tools=True) is None


def test_fulltext_followup_reports_source_backed_ki_method(monkeypatch, tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import BoundedJsonClient
    from pi_web_support import make_web_handler, run_web_turn
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query, **kwargs: {'hit_count': 1, 'articles': [
        {'pmid': '123', 'pmcid': 'PMC456', 'open_access': True}]})
    def request(self, source, method, url, **kwargs):
        payload = _payload()
        payload[0]['documents'][0]['passages'][1]['text'] = (
            'We undertook steady state kinetic measurements of TKD activities to measure ATP constants '
            'and inhibition constants (KI) for erlotinib using the Cheng–Prusoff equation.')
        payload[0]['documents'][0]['passages'][2]['text'] = (
            'Equivalent experiments with L858R- and L858R/T790M-mutated TKDs gave KI values of '
            '4.7 ± 2.3 and 41 ± 14 nM respectively, indicating reduced affinity.')
        return payload
    monkeypatch.setattr(BoundedJsonClient, 'request_json', request)
    class Model:
        def __init__(self): self.calls = 0
        async def decide(self, messages, **kwargs):
            self.calls += 1
            data = json.loads(messages[-1]['content'])
            value = ({'assessments': [{'index': 0, 'category': 'direct'}]} if 'claims' in data else
                     {'claims': [{'offset': 200, 'text': '估算抑制常数为41 nM。',
                                  'quote': 'L858R- and L858R/T790M-mutated TKDs gave KI values of 4.7 ± 2.3 and 41 ± 14 nM respectively'}]})
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'fulltext', {})
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'method-followup.sqlite'), model=model)
    history = []
    run_web_turn(handler, '请分析 PMID 123 的全文，说明 T790M 的实验结果。', history=history, session='s')
    before = model.calls
    result, _ = run_web_turn(handler, '这两个数值分别对应哪个突变？论文中的 Ki 是直接测量的 Kd 吗？',
                             history=history, session='s')
    assert '稳态酶动力学' in result['content'] and 'Cheng–Prusoff' in result['content']
    assert '不应当作直接测得的 Kd' in result['content']
    assert 'L858R：4.7 ± 2.3 nM' in result['content']
    assert 'L858R/T790M：41 ± 14 nM' in result['content']
    assert 'https://pmc.ncbi.nlm.nih.gov/articles/PMC456/' in result['content']
    assert model.calls == before and result['status'] == 'partial'
