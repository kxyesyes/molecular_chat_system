from src.web.web_business import WebTurnProjection


def _review_response(messages):
    import json
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    data = json.loads(messages[-1]['content'])
    if 'candidates' not in data:
        return None
    value = {'assessments': [{'index': item['index'], 'category': 'direct'} for item in data['candidates']]}
    return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
        text=json.dumps(value), evidence_ids=[]), None, 'review', {})


def test_explicit_literature_keywords_return_linked_records(monkeypatch):
    from src.literature.europe_pmc import EuropePMCClient
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query: {'hit_count': 1, 'articles': [
        {'pmid': '18227510', 'title': 'ATP affinity', 'year': '2008', 'abstract': None,
         'publication_types': ['Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/18227510/'}]})
    result = WebTurnProjection('检索文献：EGFR T790M ATP', 's', []).followup(enable_tools=True)
    assert 'ATP affinity' in result['content'] and 'https://pubmed.ncbi.nlm.nih.gov/18227510/' in result['content']
    assert '2008' in result['content'] and '不代表' in result['content']


def test_literature_search_respects_tool_permission_and_reports_failure(monkeypatch):
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import RemoteSourceError
    def fail(self, query):
        raise RemoteSourceError('EuropePMC', 'timeout', retryable=True)
    monkeypatch.setattr(EuropePMCClient, 'search', fail)
    query = '检索文献：EGFR T790M ATP'
    disabled = WebTurnProjection(query, 's', []).followup(enable_tools=False)
    assert '未' in disabled['content'] and disabled['status'] != 'completed'
    failure = WebTurnProjection(query, 's', []).followup(enable_tools=True)
    assert '暂不可用' in failure['content'] and failure['status'] != 'completed'


def test_literature_title_that_looks_like_path_does_not_hide_all_results(monkeypatch):
    from src.literature.europe_pmc import EuropePMCClient
    from src.system.scientific_public_data import public_scientific_data
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query: {'hit_count': 2, 'articles': [
        {'pmid': '123', 'title': 'EGFR /T790M/C797S inhibitors', 'year': '2026', 'abstract': None,
         'publication_types': ['Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/'},
        {'pmid': '456', 'title': 'Kinase affinity study', 'year': '2025', 'abstract': None,
         'publication_types': ['Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/'}]})
    content = WebTurnProjection('检索文献：EGFR T790M ATP', 's', []).followup(enable_tools=True)['content']
    assert 'Kinase affinity study' in content and '123' in content
    assert not public_scientific_data(content)[1]


def test_open_ended_literature_request_explains_current_keyword_capability():
    from src.web.agent_result_presentation import failure_claim_risk
    query = '请给我支持 EGFR T790M 结论的论文来源。'
    answer = WebTurnProjection(query, 's', []).followup(enable_tools=True)
    assert answer['status'] == 'not_calculated'
    assert '检索文献：' in answer['content'] and '未执行' in answer['content']
    assert '总结 PMID' in answer['content'] and '摘要' in answer['content']
    assert not failure_claim_risk(answer['content'])


def test_literature_ordinal_uses_only_owned_search_results(monkeypatch):
    from src.literature.europe_pmc import EuropePMCClient
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query: {'hit_count': 2, 'articles': [
        {'pmid': '123', 'title': 'First study', 'year': '2025', 'abstract': 'First abstract.',
         'publication_types': ['Review'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/'},
        {'pmid': '456', 'title': 'Second study', 'year': '2024',
         'abstract': 'This study investigates kinase interactions in a cellular model.',
         'publication_types': ['Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/'}]})
    projection = WebTurnProjection('检索文献：EGFR kinase', 'session-a', [])
    result = projection.followup(enable_tools=True)
    assert '1.' in result['content'] and '2.' in result['content']
    query = '第二篇是什么类型的文章？它的摘要研究了什么？'
    history = [projection.entry]
    answer = WebTurnProjection(query, 'session-a', history).followup(enable_tools=True)
    assert 'Second study' in answer['content'] and '456' in answer['content']
    assert 'Journal Article' in answer['content'] and 'This study investigates' in answer['content']
    assert '摘要摘录' in answer['content'] and '未自动总结' in answer['content']
    assert answer['summary_source']['pmid'] == '456'
    assert answer['summary_source']['abstract'] == 'This study investigates kinase interactions in a cellular model.'
    assert 'First study' not in answer['content']
    foreign = WebTurnProjection(query, 'session-b', history).followup(enable_tools=True)
    assert '456' not in foreign['content'] and foreign['status'] == 'partial'
    assert WebTurnProjection(query + '请执行对接。', 'session-a', history).followup(enable_tools=True) is None


def test_research_pmid_followup_is_bound_to_the_owned_selected_abstract():
    from src.literature.summary_evidence import prepare_abstract
    source = prepare_abstract({'pmid': '456', 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/',
                               'abstract': 'The study measured kinase binding in a biochemical assay.'})
    history = [{'literature_research': {'session_id': 'session-a',
        'attempts': [{'sources': [source]}]}}]
    query = '请概括 PMID 456 的摘要，说明是否测量 T790M 直接结合亲和力。'
    projection = WebTurnProjection(query, 'session-a', history)
    result = projection.followup(enable_tools=True)
    assert result['research_summary_source'] == source
    assert '摘要中未出现 T790M' in result['content']
    assert '不能据此判断全文' in result['content']
    assert result['source_research_question'] == query
    assert projection.entry['literature_research'] == history[0]['literature_research']
    foreign = WebTurnProjection(query, 'session-b', history).followup(enable_tools=True)
    assert foreign['status'] == 'partial' and not foreign.get('research_summary_source')
    missing = WebTurnProjection(query.replace('456', '789'), 'session-a', history).followup(enable_tools=True)
    assert missing['status'] == 'partial' and not missing.get('research_summary_source')
    assert WebTurnProjection(query + '请执行对接。', 'session-a', history).followup(enable_tools=True) is None
    mixed = '请概括 PMID 456 的摘要，并计算 CCO 的 LogP。'
    assert WebTurnProjection(mixed, 'session-a', history).followup(enable_tools=True) is None


def test_research_ordinal_followup_uses_visible_owned_candidate_order():
    from src.literature.summary_evidence import prepare_abstract
    first = prepare_abstract({'pmid': '123', 'url': 'https://pubmed.ncbi.nlm.nih.gov/123/',
                              'abstract': 'The first study examined a formulation.'})
    second = prepare_abstract({'pmid': '456', 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/',
                               'abstract': 'The second study measured kinase binding.'})
    evidence = {'session_id': 'session-a', 'attempts': [{'sources': [first, second]}],
                'candidates': [{'pmid': '123', 'title': 'First', 'year': '2022'},
                               {'pmid': '456', 'title': 'Second', 'year': '2002'}]}
    history = [{'literature_research': evidence}]
    projection = WebTurnProjection('第二篇论文的摘要说了什么？', 'session-a', history)
    result = projection.followup(enable_tools=True)
    assert result['research_summary_source'] == second
    assert 'Second' in result['content'] and 'PMID 456' in result['content']
    assert projection.entry['literature_research'] == evidence
    foreign = WebTurnProjection('第二篇论文的摘要说了什么？', 'session-b', history).followup(enable_tools=True)
    assert not foreign.get('research_summary_source') and '缺少' in foreign['content']
    missing = WebTurnProjection('第三篇论文的摘要说了什么？', 'session-a', history).followup(enable_tools=True)
    assert not missing.get('research_summary_source') and '没有' in missing['content']


def test_research_pmid_followup_uses_configured_model_and_bound_question(tmp_path):
    import json
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.summary_evidence import prepare_abstract
    from pi_web_support import make_web_handler, run_web_turn
    source = prepare_abstract({'pmid': '456', 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/',
                               'abstract': 'The study measured kinase binding in a biochemical assay.'})
    question = '请概括 PMID 456 的摘要，说明是否测量 T790M 直接结合亲和力。'
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            data = json.loads(messages[-1]['content'])
            assert data['question'] == question and data['sources'][0]['pmid'] == '456'
            value = {'claims': [{'pmid': '456', 'text': '研究测量结合。',
                                 'quote': 'study measured kinase binding'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'follow-up', {})
    history = [{'literature_research': {'session_id': 's', 'attempts': [{'sources': [source]}]}}]
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'research-followup.sqlite'), model=Model())
    complete, _ = run_web_turn(handler, question, history=history, session='s')
    assert 'pubmed.ncbi.nlm.nih.gov/456/' in complete['content']
    assert 'The study measured kinase binding in a biochemical assay.' in complete['content']
    assert '摘要中未出现 T790M' in complete['content']
    assert complete['status'] == 'partial'


def test_direct_pmid_search_retrieves_exact_record_and_uses_configured_model(monkeypatch, tmp_path):
    import json
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    query = '请概括 PMID 456 的摘要，说明是否测量 T790M 直接结合亲和力。'
    searches = []
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review: return review
            data = json.loads(messages[-1]['content'])
            assert data['question'] == query and data['sources'][0]['pmid'] == '456'
            value = {'claims': [{'pmid': '456', 'text': '研究测量结合。',
                                 'quote': 'study measured kinase binding'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'follow-up', {})
    def search(self, search_query, **kwargs):
        searches.append(search_query)
        return {'hit_count': 1, 'articles': [{'pmid': '456', 'title': 'A study', 'year': '2022',
            'abstract': 'The study measured kinase binding in a biochemical assay.',
            'publication_types': ['Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/'}]}
    monkeypatch.setattr(EuropePMCClient, 'search', search)
    history = []
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'direct-pmid.sqlite'), model=Model())
    complete, _ = run_web_turn(handler, query, history=history, session='s')
    assert searches == ['EXT_ID:456 AND SRC:MED']
    assert 'pubmed.ncbi.nlm.nih.gov/456/' in complete['content']
    assert 'A study' in complete['content'] and '2022' in complete['content']
    assert '摘要中未出现 T790M' in complete['content']
    assert history[-1]['literature_research']['attempts'][0]['sources'][0]['pmid'] == '456'
    assert complete['status'] == 'partial'


def test_composite_pmid_question_keeps_metadata_and_summary_obligations(monkeypatch, tmp_path):
    import json
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    query = ('请查询 PMID 456 的论文题目、DOI 和摘要，并概括摘要对 EGFR 与拉帕替尼结合的主要发现；'
             '没有核验的信息请明确说明。')
    searches = []
    def search(self, value, **kwargs):
        searches.append(value)
        return {'hit_count': 1, 'articles': [{'pmid': '456', 'title': 'EGFR binding study', 'year': '2022',
            'doi': '10.1000/example.1', 'abstract': 'The study measured EGFR binding in a biochemical assay.',
            'publication_types': ['Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/'}]}
    class Model:
        async def decide(self, messages, **kwargs):
            review = _review_response(messages)
            if review:
                return review
            data = json.loads(messages[-1]['content'])
            assert data['question'] == query and data['sources'][0]['pmid'] == '456'
            value = {'claims': [{'pmid': '456', 'text': '研究测量结合。',
                                 'quote': 'study measured EGFR binding'}]}
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=json.dumps(value, ensure_ascii=False), evidence_ids=[]), None, 'follow-up', {})
    monkeypatch.setattr(EuropePMCClient, 'search', search)
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'composite-pmid.sqlite'), model=Model())
    complete, _ = run_web_turn(handler, query)
    assert searches == ['EXT_ID:456 AND SRC:MED']
    assert 'EGFR binding study' in complete['content'] and '10.1000/example.1' in complete['content']
    assert 'pubmed.ncbi.nlm.nih.gov/456/' in complete['content']
    assert complete['status'] == 'partial'
    assert not (WebTurnProjection(query + '预测 CCO 的活性。', 's', []).followup(enable_tools=True) or {}).get('research_lookup_pmid')


def test_composite_pmid_reports_missing_abstract_and_doi_without_losing_title(monkeypatch, tmp_path):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, *args, **kwargs: {
        'hit_count': 1, 'articles': [{'pmid': '456', 'title': 'A study', 'year': '2022',
            'doi': None, 'abstract': None, 'publication_types': [], 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/'}]})
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'composite-missing.sqlite'))
    query = '请查询 PMID 456 的论文题目、DOI 和摘要，并概括摘要对 EGFR 的主要发现。'
    complete, _ = run_web_turn(handler, query)
    assert complete['status'] == 'partial'
    assert 'A study' in complete['content'] and '未找到' in complete['content'] and 'DOI' in complete['content']


def test_direct_pmid_search_respects_tool_permission_and_rejects_other_pmid(monkeypatch, tmp_path):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    searches = []
    def search(self, search_query, **kwargs):
        searches.append(search_query)
        return {'hit_count': 1, 'articles': [{'pmid': '789', 'title': 'Another study', 'year': '2022',
            'abstract': 'This is an unrelated scientific abstract.', 'publication_types': ['Journal Article'],
            'url': 'https://pubmed.ncbi.nlm.nih.gov/789/'}]}
    monkeypatch.setattr(EuropePMCClient, 'search', search)
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'direct-pmid-mismatch.sqlite'))
    query = '请概括 PMID 456 的摘要。'
    disabled, _ = run_web_turn(handler, query, enable_tools=False)
    assert not searches and '未启用' in disabled['content']
    result, _ = run_web_turn(handler, query)
    assert len(searches) == 1 and '789' not in result['content']
    assert result['status'] == 'partial' and '未找到' in result['content']


def test_direct_pmid_search_reports_remote_failure(monkeypatch, tmp_path):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from src.target_search.remote_clients import RemoteSourceError
    from pi_web_support import make_web_handler, run_web_turn
    def fail(self, query, **kwargs):
        raise RemoteSourceError('EuropePMC', 'timeout', retryable=True)
    monkeypatch.setattr(EuropePMCClient, 'search', fail)
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'direct-pmid-failure.sqlite'))
    result, _ = run_web_turn(handler, '请概括 PMID 456 的摘要。')
    assert result['status'] == 'partial' and '暂不可用' in result['content']
    assert 'pubmed.ncbi.nlm.nih.gov/456/' not in result['content']


def test_web_runtime_uses_configured_model_for_selected_abstract(monkeypatch, tmp_path):
    import json
    from src.agent.contracts.decision import FinishDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.literature.europe_pmc import EuropePMCClient
    from pi_web_support import make_web_handler, run_web_turn
    class Model:
        def __init__(self): self.calls = []
        async def decide(self, messages, **kwargs):
            self.calls.append(messages)
            text = json.dumps({'claims': [{'text': '研究考察了激酶相互作用。',
                'quote': 'This study investigates kinase interactions'}]}, ensure_ascii=False)
            return DecisionResponse(FinishDecision(version='1', action='finish', response_kind='chat',
                text=text, evidence_ids=[]), None, 'literature-1', {})
    monkeypatch.setattr(EuropePMCClient, 'search', lambda self, query: {'hit_count': 1, 'articles': [
        {'pmid': '456', 'title': 'A kinase study', 'year': '2024',
         'abstract': 'This study investigates kinase interactions in a cellular model.',
         'publication_types': ['Journal Article'], 'url': 'https://pubmed.ncbi.nlm.nih.gov/456/'}]})
    model = Model()
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'literature.sqlite'), model=model)
    history = []
    run_web_turn(handler, '检索文献：EGFR kinase', history=history, session='s')
    complete, _ = run_web_turn(handler, '第一篇是什么类型的文章？它的摘要研究了什么？',
                               history=history, session='s')
    assert len(model.calls) == 1
    assert '研究考察了激酶相互作用' in complete['content'] and 'pubmed.ncbi.nlm.nih.gov/456/' in complete['content']
    assert '核对' in complete['content']
