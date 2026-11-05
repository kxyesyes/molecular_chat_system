import asyncio
import json

import pytest

from src.literature.research import build_search_query, request_search_query, validate_research_claims, request_research_synthesis, select_research_sources
from src.literature.summary_evidence import prepare_abstract


QUESTION = '请检索原始研究文献，分析 EGFR T790M 对厄洛替尼直接结合亲和力和 ATP 竞争的影响。'


def _review_response(messages):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    try:
        data = json.loads(messages[-1]['content'])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or 'candidates' not in data:
        return None
    value = {'assessments': [{'index': item['index'], 'category': 'direct'} for item in data['candidates']]}
    return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
        text=json.dumps(value), evidence_ids=[]), None, 'review', {})


def test_search_proposal_preserves_explicit_identifiers_and_bounds_fields():
    proposal = {'terms': ['EGFR', 'T790M', 'ATP', 'erlotinib'], 'title_terms': ['T790M', 'ATP']}
    assert build_search_query(QUESTION, proposal) == '"EGFR" AND TITLE:"T790M" AND TITLE:"ATP" AND "erlotinib"'
    assert build_search_query(QUESTION, {'terms': ['EGFR', 'ATP'], 'title_terms': []}) is None
    assert build_search_query(QUESTION, {'terms': ['EGFR', 'T790M', 'ATP', 'foo OR bar'], 'title_terms': []}) is None
    assert build_search_query(QUESTION, {'terms': ['EGFR', 'T790M', 'ATP'], 'title_terms': ['unlisted']}) is None


def test_search_proposal_accepts_safe_phrases_without_losing_identifiers():
    proposal = {'terms': ['EGFR T790M', 'ATP affinity', 'erlotinib'], 'title_terms': ['EGFR T790M']}
    assert build_search_query(QUESTION, proposal) == 'TITLE:"EGFR T790M" AND "ATP affinity" AND "erlotinib"'


def test_requested_citation_fields_are_not_scientific_search_anchors():
    question = '检索 EGFR T790M 文献，列出 PMID 和 DOI。'
    proposal = {'terms': ['EGFR', 'T790M', 'erlotinib'], 'title_terms': []}
    assert build_search_query(question, proposal) == '"EGFR" AND "T790M" AND "erlotinib"'
    assert build_search_query(question, {'terms': ['EGFR'], 'title_terms': []}) is None


def test_search_query_uses_configured_model_but_rejects_tool_actions():
    from src.agent.contracts.decision import FinishDecision, ToolDecision
    from src.agent.decision_transport import DecisionResponse
    class Model:
        def __init__(self, tool=False): self.tool = tool
        async def decide(self, messages, **kwargs):
            assert QUESTION in messages[-1]['content']
            if self.tool:
                decision = ToolDecision(version='1', action='tool', tool_name='molecular_docking',
                                        arguments={'input_ref': 'user'}, purpose='unexpected')
            else:
                decision = FinishDecision(version='1', action='finish', response_kind='chat',
                    text=json.dumps({'terms': ['EGFR', 'T790M', 'ATP'], 'title_terms': ['T790M']}), evidence_ids=[])
            return DecisionResponse(decision, None, 'research-1', {})
    assert 'TITLE:"T790M"' in asyncio.run(request_search_query(Model(), 'native', QUESTION))
    assert asyncio.run(request_search_query(Model(tool=True), 'native', QUESTION)) is None


def test_research_claims_bind_each_quote_and_number_to_its_source():
    first = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
                              'abstract': 'The mutant increases ATP affinity in this assay.'})
    second = prepare_abstract({'pmid': '456', 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/',
                               'abstract': 'Direct binding was measured in a biochemical assay.'})
    sources = [first, second]
    good = {'claims': [{'pmid': '123', 'text': '该实验中突变体 ATP 亲和力增加。',
                        'quote': 'The mutant increases ATP affinity'},
                       {'pmid': '456', 'text': '研究测量了直接结合。',
                        'quote': 'Direct binding was measured'}]}
    assert validate_research_claims(json.dumps(good, ensure_ascii=False), sources, QUESTION) == good['claims']
    good['claims'][1]['quote'] = 'The mutant increases ATP affinity'
    assert validate_research_claims(json.dumps(good, ensure_ascii=False), sources, QUESTION) is None
    good['claims'][1]['quote'] = 'Direct binding was measured'
    good['claims'][1]['text'] = '亲和力提高 100 倍。'
    assert validate_research_claims(json.dumps(good, ensure_ascii=False), sources, QUESTION) is None


def test_synthesis_uses_selected_source_and_server_owned_url():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    source = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
                               'abstract': 'The mutant increases ATP affinity in this assay.'})
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            assert 'untrusted' in messages[0]['content'].lower()
            assert QUESTION in messages[-1]['content']
            text = json.dumps({'claims': [{'pmid': '123', 'text': '该实验观察到 ATP 亲和力增加。',
                'quote': 'The mutant increases ATP affinity'}]}, ensure_ascii=False)
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=text, evidence_ids=[]), None, 'research-2', {})
    answer = asyncio.run(request_research_synthesis(Model(), 'native', QUESTION, [source]))
    assert 'pubmed.ncbi.nlm.nih.gov/123/' in answer and '核对' in answer
    assert '该实验观察到' not in answer
    assert 'The mutant increases ATP affinity' in answer
    assert '尚未形成经核对的研究结论' in answer


def test_synthesis_offers_complete_short_sentences_for_relevant_selection():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    long_sentence = ' '.join(['Background'] * 27) + ' describes prior research.'
    short_sentence = 'The study compared kinase activity across exon 19 variants.'
    source = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
                               'abstract': long_sentence + ' ' + short_sentence})
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            data = json.loads(messages[-1]['content'])['sources'][0]
            eligible = [item['text'] for item in data.get('sentences', []) if item['displayable']]
            quote = ('study compared kinase activity across exon 19 variants' if short_sentence in eligible
                     else 'Background Background Background Background')
            value = {'claims': [{'pmid': '123', 'text': '研究比较了外显子19变体。', 'quote': quote}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    answer = asyncio.run(request_research_synthesis(Model(), 'native', QUESTION, [source]))
    assert short_sentence in answer
    assert long_sentence not in answer


def test_synthesis_can_show_short_excerpt_from_long_relevant_sentence():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    sentence = ('The crystal structure of the kinase domain from the epidermal growth factor receptor '
        'was determined in the presence and absence of a selective inhibitor using an experimental '
        'method that allowed the authors to compare the resulting structural conformations.')
    source = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
                               'abstract': sentence})
    class Model:
        async def decide(self, messages, **kwargs):
            data = json.loads(messages[-1]['content'])
            if 'candidates' in data:
                assert data['candidates'][0]['sentence'] == sentence
                assert data['candidates'][0]['excerpt'] == 'crystal structure of the kinase domain'
                assert data['sources'][0]['abstract'] == sentence
                assert 'excerpt itself' in messages[0]['content']
                value = {'assessments': [{'index': 0, 'category': 'direct'}]}
            else:
                assert data['sources'][0]['sentences'][0]['displayable']
                value = {'claims': [{'pmid': '123', 'text': '研究测定了激酶结构。',
                                     'quote': 'crystal structure of the kinase domain'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    answer = asyncio.run(request_research_synthesis(Model(), 'native', '分析 EGFR 结构。', [source]))
    assert '摘录' in answer and 'crystal structure of the kinase domain' in answer
    assert sentence not in answer
    assert '直接相关原句' not in answer
    assert '需结合原文' in answer


def test_synthesis_distinguishes_other_drug_background_from_direct_evidence():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    source = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
        'abstract': 'The mutant retained nanomolar binding to gefitinib. ATP affinity increased in the mutant kinase.'})
    question = '分析 erlotinib 的直接结合亲和力以及 ATP 竞争。'
    class Model:
        def __init__(self): self.calls = []
        async def decide(self, messages, **kwargs):
            data = json.loads(messages[-1]['content'])
            self.calls.append(data)
            if 'candidates' in data:
                assert data['question'] == question
                assert data['candidates'][0]['sentence'] == 'The mutant retained nanomolar binding to gefitinib.'
                assert data['sources'][0]['abstract'] == source['abstract']
                value = {'assessments': [{'index': 0, 'category': 'context'},
                                         {'index': 1, 'category': 'direct'}]}
            else:
                value = {'claims': [{'pmid': '123', 'text': '吉非替尼结合。',
                                     'quote': 'mutant retained nanomolar binding to gefitinib'},
                                    {'pmid': '123', 'text': 'ATP 亲和力增加。',
                                     'quote': 'ATP affinity increased in the mutant kinase'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    model = Model()
    answer = asyncio.run(request_research_synthesis(model, 'native', question, [source]))
    assert len(model.calls) == 2
    assert '直接相关原句' in answer and '背景原句' in answer
    assert answer.index('ATP affinity increased') < answer.index('背景原句')
    assert answer.index('gefitinib') > answer.index('背景原句')


@pytest.mark.parametrize('assessment', [{'index': 999, 'category': 'direct'},
                                        {'index': 0, 'category': ['direct']}])
def test_synthesis_does_not_expose_unclassified_sentences(assessment):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    source = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
                               'abstract': 'The mutant binds gefitinib in a biochemical assay.'})
    class Model:
        async def decide(self, messages, **kwargs):
            data = json.loads(messages[-1]['content'])
            value = ({'assessments': [assessment]} if 'candidates' in data else
                     {'claims': [{'pmid': '123', 'text': '药物结合。',
                                  'quote': 'mutant binds gefitinib in a biochemical assay'}]})
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    answer = asyncio.run(request_research_synthesis(Model(), 'native', QUESTION, [source]))
    assert 'The mutant binds gefitinib' not in answer
    assert '复核不可用' in answer and 'pubmed.ncbi.nlm.nih.gov/123/' in answer


def test_synthesis_preserves_qualifying_sentence_and_marks_long_sentence_excerpt():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    first = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
        'abstract': 'Previous experiments were inconclusive. We propose that the treatment may reduce tumor growth in this model.'})
    second = prepare_abstract({'pmid': '456', 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/',
        'abstract': ' '.join(['Evidence'] * 27) + ' suggests a possible effect.'})
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            value = {'claims': [
                {'pmid': '123', 'text': '研究观察到作用。', 'quote': 'treatment may reduce tumor growth'},
                {'pmid': '456', 'text': '研究发现作用。', 'quote': 'suggests a possible effect'},
            ]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    answer = asyncio.run(request_research_synthesis(Model(), 'native', QUESTION, [first, second]))
    assert 'We propose that the treatment may reduce tumor growth in this model.' in answer
    assert '摘录：suggests a possible effect' in answer
    assert second['abstract'] not in answer
    assert 'pubmed.ncbi.nlm.nih.gov/456/' in answer
    assert '完整原文' in answer


def test_synthesis_lists_each_paper_once_when_multiple_excerpts_are_too_long():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    abstract = ' '.join(['Evidence'] * 27) + ' suggests a possible effect.'
    source = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
                               'abstract': abstract})
    class Model:
        async def decide(self, messages, **kwargs):
            value = {'claims': [
                {'pmid': '123', 'text': '第一项。', 'quote': 'suggests a possible effect'},
                {'pmid': '123', 'text': '第二项。', 'quote': 'Evidence Evidence Evidence Evidence'},
            ]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    answer = asyncio.run(request_research_synthesis(Model(), 'native', QUESTION, [source]))
    assert answer.count('https://pubmed.ncbi.nlm.nih.gov/123/') == 1


def test_synthesis_does_not_split_sentence_at_common_abbreviation():
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    sentence = 'Studies including variants, e.g. EGFR T790M, may show reduced inhibitor sensitivity.'
    source = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/', 'abstract': sentence})
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            value = {'claims': [{'pmid': '123', 'text': '一种可能的结果。',
                                 'quote': 'EGFR T790M, may show reduced inhibitor sensitivity'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    answer = asyncio.run(request_research_synthesis(Model(), 'native', QUESTION, [source]))
    assert sentence in answer


def test_primary_research_selection_excludes_reviews_and_unlabeled_records():
    def entry(pmid, types):
        return {'pmid': pmid, 'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
                'abstract': 'The experiment reported an observed result.', 'publication_types': types}
    articles = [entry('123', ['Journal Article']), entry('456', ['research-article', 'Review']),
                entry('789', ['research-article', 'Journal Article'])]
    assert [source['pmid'] for source in select_research_sources(articles, primary_only=True)] == ['789']


def test_natural_research_request_is_read_only_and_preserves_full_question():
    from src.web.web_business import WebTurnProjection
    query = '请检索原始研究文献，分析 EGFR T790M 对厄洛替尼直接结合亲和力和 ATP 竞争的影响，提供可核验的论文来源。'
    result = WebTurnProjection(query, 's', []).followup(enable_tools=True)
    assert result['research_question'] == query and result['primary_only'] is True
    disabled = WebTurnProjection(query, 's', []).followup(enable_tools=False)
    assert disabled['status'] != 'completed' and not disabled.get('research_question')
    mixed = query + '请计算 CCO 的 LogP。'
    assert WebTurnProjection(mixed, 's', []).followup(enable_tools=True) is None


def test_subject_first_literature_request_preserves_question_and_requires_tools():
    from src.web.web_business import WebTurnProjection
    query = ('请检索 EGFR 抑制剂厄洛替尼的晶体结构研究文献，列出论文题目、年份、PMID 和来源，'
             '并概括摘要支持的主要结论；没有核验的实验数值请明确说明。')
    result = WebTurnProjection(query, 's', []).followup(enable_tools=True)
    assert result['research_question'] == query and result['primary_only'] is False
    assert not WebTurnProjection(query, 's', []).followup(enable_tools=False).get('research_question')
    assert WebTurnProjection(query + '再预测 CCO 的活性。', 's', []).followup(enable_tools=True) is None


@pytest.mark.parametrize('query', [
    '请检索 EGFR 抑制剂厄洛替尼的晶体结构相关文献，并总结摘要中的主要发现，附上 PMID。',
    '查找 EGFR 耐药相关论文并概括摘要中的主要结论。',
    '搜索 EGFR T790M 原始研究文献，分析摘要中的实验结果，并提供来源。',
    '查询 KRAS 相关文献，总结摘要的主要发现，列出论文题目和年份。',
])
def test_natural_literature_requests_use_research_pipeline(query):
    from src.web.web_business import WebTurnProjection
    result = WebTurnProjection(query, 's', []).followup(enable_tools=True)
    assert result['research_question'] == query
    assert result['primary_only'] == ('原始' in query)
    assert not WebTurnProjection(query, 's', []).followup(enable_tools=False).get('research_question')
    assert WebTurnProjection(query + '再预测 CCO 的活性。', 's', []).followup(enable_tools=True) is None


def test_literature_summary_does_not_swallow_inline_calculation():
    from src.web.web_business import WebTurnProjection
    query = '请检索 EGFR 相关文献，并总结摘要中的主要发现并预测 CCO 的活性。'
    assert WebTurnProjection(query, 's', []).followup(enable_tools=True) is None


def test_web_runtime_research_uses_configured_model_and_verified_source(monkeypatch, tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    class Model:
        def __init__(self): self.calls = []
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            self.calls.append(messages)
            if len(self.calls) == 1:
                value = {'terms': ['EGFR', 'T790M', 'ATP', 'erlotinib'], 'title_terms': ['T790M', 'ATP']}
            else:
                value = {'claims': [{'pmid': '123', 'text': '该实验观察到 ATP 亲和力增加。',
                                     'quote': 'The mutant increases ATP affinity'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research-call', {})
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query, **kwargs: {'hit_count': 1, 'articles': [
        {'pmid': '123', 'title': 'Kinase study', 'year': '2008',
         'abstract': 'The mutant increases ATP affinity in this assay.',
         'publication_types': ['research-article', 'Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/'}]})
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'research.sqlite'), model=model)
    query = '请检索原始研究文献，分析 EGFR T790M 对厄洛替尼直接结合亲和力和 ATP 竞争的影响，提供可核验的论文来源。'
    complete, _ = run_web_turn(handler, query)
    assert len(model.calls) == 3
    assert 'TITLE:' in complete['content'] and 'pubmed.ncbi.nlm.nih.gov/123/' in complete['content']
    assert 'The mutant increases ATP affinity' in complete['content'] and complete['status'] == 'partial'
    assert '该实验观察到' not in complete['content']
    assert 'Kinase study' in complete['content']
    assert 'PMID 123' in complete['content']


def test_primary_search_filters_before_limit_and_does_not_stop_at_first_candidate(monkeypatch, tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    queries = []
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            if len(messages) and 'sources' in messages[-1]['content']:
                data = json.loads(messages[-1]['content'])
                pmid = data['sources'][0]['pmid']
                result = {'claims': [{'pmid': pmid, 'text': '有实验结果。',
                                      'quote': 'The mutant increases ATP affinity'}]}
            else:
                result = {'terms': ['EGFR', 'T790M', 'ATP'], 'title_terms': ['T790M']}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(result, ensure_ascii=False), evidence_ids=[]), None, 'research', {})
    def search(self, query, **kwargs):
        queries.append(query)
        pmid = '456' if query.endswith('sort_cited:y') else '123'
        articles = [{'pmid': pmid, 'title': 'Study', 'year': '2008',
            'abstract': 'The mutant increases ATP affinity in this assay.',
            'publication_types': ['research-article', 'Journal Article'],
            'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'}]
        if pmid == '456':
            articles.append({'pmid': '789', 'title': 'Another relevant candidate', 'year': '2022',
                'abstract': 'The experiment used a separate biochemical assay.',
                'publication_types': ['research-article', 'Journal Article'],
                'url': 'https://pubmed.ncbi.nlm.nih.gov/789/'})
        return {'hit_count': len(articles), 'articles': articles}
    monkeypatch.setattr(EuropePMCClient, 'search', search)
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'primary-filter.sqlite'), model=Model())
    complete, _ = run_web_turn(handler, '请检索原始研究文献，分析 EGFR T790M 对 ATP 竞争的影响。')
    assert len(queries) == 2
    assert all('PUB_TYPE:research-article NOT PUB_TYPE:review' in q for q in queries)
    assert 'TITLE_ABS:"EGFR"' in queries[1] and 'TITLE_ABS:"T790M"' in queries[1]
    assert 'pubmed.ncbi.nlm.nih.gov/456/' in complete['content']
    assert 'Another relevant candidate' in complete['content']
    assert 'pubmed.ncbi.nlm.nih.gov/789/' in complete['content']
    assert '未逐篇核验' in complete['content']


def test_web_runtime_research_reports_remote_failure_without_fabricated_claim(monkeypatch, tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import RemoteSourceError
    from pi_web_support import make_web_handler, run_web_turn
    class Model:
        def __init__(self): self.calls = 0
        async def decide(self, messages, **kwargs):
            self.calls += 1
            text = json.dumps({'terms': ['EGFR', 'T790M', 'ATP'], 'title_terms': ['T790M']})
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=text, evidence_ids=[]), None, 'research-call', {})
    def fail(self, query, **kwargs):
        raise RemoteSourceError('EuropePMC', 'timeout', retryable=True)
    monkeypatch.setattr(EuropePMCClient, 'search', fail)
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'failure.sqlite'), model=model)
    query = '请检索原始研究文献，分析 EGFR T790M 对 ATP 竞争的影响。'
    complete, _ = run_web_turn(handler, query)
    assert model.calls == 1 and complete['status'] == 'partial'
    assert '暂不可用' in complete['content'] and '未生成' in complete['content']


def test_web_runtime_broadens_title_when_first_abstract_is_irrelevant(monkeypatch, tmp_path):
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    class Model:
        def __init__(self): self.calls = 0
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            self.calls += 1
            if self.calls == 1:
                value = {'terms': ['EGFR', 'T790M', 'ATP'], 'title_terms': ['T790M']}
            elif self.calls == 2:
                value = {'claims': []}
            else:
                value = {'claims': [{'pmid': '456', 'text': '该实验观察到 ATP 亲和力增加。',
                                     'quote': 'The mutant increases ATP affinity'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'research-call', {})
    queries = []
    def search(self, query, **kwargs):
        queries.append(query)
        pmid = '456' if query.endswith('sort_cited:y') else '123'
        abstract = ('The mutant increases ATP affinity in this assay.' if pmid == '456'
                    else 'This article investigates an unrelated resistance pathway.')
        return {'hit_count': 1, 'articles': [{'pmid': pmid, 'title': 'Study', 'year': '2008',
            'abstract': abstract, 'publication_types': ['research-article', 'Journal Article'],
            'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'}]}
    monkeypatch.setattr(EuropePMCClient, 'search', search)
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'broaden.sqlite'), model=model)
    query = '请检索原始研究文献，分析 EGFR T790M 对 ATP 竞争的影响。'
    complete, _ = run_web_turn(handler, query)
    assert len(queries) == 2 and queries[1].endswith('sort_cited:y') and 'TITLE:' not in queries[1]
    assert 'pubmed.ncbi.nlm.nih.gov/456/' in complete['content'] and '被引' in complete['content']
    assert model.calls == 3 and complete['status'] == 'partial'
