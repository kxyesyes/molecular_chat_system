"""Bounded property-ranking admission; all assertions use synthetic inputs."""
import pytest
from src.agent.contracts import AgentContext
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.scientific_session.web_requirements import compile_web_requirements
from src.web.scientific_obligations import ScientificObligationCompiler


QUERY = '针对 EGFR 生成 2 个候选分子，计算分子性质，并根据性质进行排序。'


def test_target_generation_property_ranking_uses_only_requested_evidence():
    assert not ScientificObligationCompiler.is_profile_candidate(QUERY)
    context = AgentContext(QUERY, 'fixture-property-rank', metadata={
        'capabilities': {'scientific_tools': True, 'rag': False}})
    plan = compile_web_requirements(context, allowed_tools=WEB_TOOLS)
    assert plan.workflow_name == 'target_driven_design'
    assert [step.tool_name for step in plan.steps] == [
        'target_database_search', 'llm_molecular_generator', 'property_calculator', 'candidate_ranker']
    ranking = plan.steps[-1]
    assert tuple(ranking.metadata['workflow_output_keys']) == ('molecules', 'properties')
    assert tuple(ranking.metadata['workflow_optional_output_keys']) == ()
    assert tuple(ranking.metadata['required_evidence']) == ('properties',)
    assert 'required_evidence' in ranking.metadata['workflow_metadata_keys']
    assert ranking.metadata['ranking_scope'] == 'properties_only'


def test_property_ranking_covers_every_requested_candidate():
    query = QUERY.replace('2 个', '6 个')
    context = AgentContext(query, 'fixture-six', metadata={'capabilities': {'scientific_tools': True, 'rag': False}})
    plan = compile_web_requirements(context, allowed_tools=WEB_TOOLS)
    assert plan.steps[-1].metadata['docking_top_n'] == 6


def test_property_ranking_allows_omitted_comma():
    query = QUERY.replace('性质，并根据', '性质并根据')
    assert not ScientificObligationCompiler.is_profile_candidate(query)
    context = AgentContext(query, 'fixture-punctuation', metadata={'capabilities': {'scientific_tools': True, 'rag': False}})
    assert compile_web_requirements(context, allowed_tools=WEB_TOOLS).workflow_name == 'target_driven_design'


def test_unknown_metric_does_not_bypass_the_property_guard():
    query = QUERY.replace('计算分子性质', '计算分子性质和 logD')
    assert ScientificObligationCompiler.is_profile_candidate(query)
    assert 'metric:logd' in ScientificObligationCompiler().compile(query).missing_capabilities


def test_explicit_admet_is_not_silently_removed():
    query = QUERY.replace('计算分子性质', '计算分子性质和 ADMET')
    assert ScientificObligationCompiler.is_profile_candidate(query)


@pytest.mark.parametrize('query', [
    QUERY + ' 另外预测 ADMET。',
    QUERY.replace('计算分子性质', '计算分子性质和 logD'),
    QUERY.replace('根据性质进行排序', '根据 QED 降序排序'),
    QUERY.replace('根据性质进行排序', '根据性质排序，再进行对接'),
    QUERY.replace('计算分子性质', '不要计算分子性质'),
    QUERY.replace('EGFR', 'EGFR 和 PDE5A'),
])
def test_unreviewed_or_additional_requirements_do_not_use_narrow_route(query):
    from src.agent.contracts.property_ranking_request import property_only_design_request
    assert not property_only_design_request(query)


@pytest.mark.parametrize('count', [2, 6])
def test_actual_web_plan_ranks_only_bound_property_rows(tmp_path, count):
    from copy import deepcopy
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.candidate_ranker import CandidateRanker
    from tests.agent.test_target_driven_design_workflow import FakeTool
    from tests.agent.pi_web_support import make_web_handler, run_web_turn

    class Target(FakeTool):
        def execute(self, query):
            value = super().execute(query)
            value['data'][0].update(gene_symbol='EGFR', source_record_id='LOCAL_EGFR')
            return value

    class RecordingRanker(CandidateRanker):
        def execute(self, payload):
            self.last_payload = deepcopy(payload)
            return super().execute(payload)

    ranker = RecordingRanker()
    tools = [Target('target_database_search'), FakeTool('llm_molecular_generator'),
             PropertyCalculator(), ranker]
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / 'property-rank.sqlite'))
    query = QUERY.replace('2 个', f'{count} 个')
    complete, _ = run_web_turn(handler, query)
    result = handler.web_results[-1]
    assert not result.metadata.get('publication_invalidated'), (
        result.metadata.get('stop_reason'), result.metadata.get('failed_boundary'))
    assert complete['status'] in ('completed', 'partial')
    rank_check = next(check for check in result.metadata['task_acceptance']['checks']
                      if check['tool_name'] == 'candidate_ranker')
    assert rank_check['passed'] is True, rank_check
    ranking = next(item for item in result.tool_results if item.tool_name == 'candidate_ranker')
    assert len(ranking.data['ranked_candidates']) == count
    assert len(ranking.data['top_candidates']) == count
    for row in ranking.data['ranked_candidates']:
        assert row['ranking_evidence']['admet_score'] is None
        assert row['ranking_evidence']['activity_score'] is None
        assert row['ranking_evidence']['weights_used']['properties'] == .5
    assert '候选排序' in complete['content']
    assert 'QED' in complete['content'] and 'LogP' in complete['content']
    assert str(ranking.data['ranked_candidates'][0]['score']) in complete['content']
    assert ranking.data['ranked_candidates'][-1]['canonical_smiles'] in complete['content']
    from src.web.result_summary import prepare_summary
    summary, _ = prepare_summary(handler, result)
    assert 'ADMET evidence was absent' not in summary['display_answer']
    assert 'Activity evidence was absent' not in summary['display_answer']
    assert '缺少可用的 ADMET 证据' not in summary['display_answer']
    assert '缺少可用的活性证据' not in summary['display_answer']
    from src.agent.scientific_session.web_acceptance import web_observation_reasons
    bad = deepcopy(ranking)
    bad.data['top_candidates'][0]['score'] = 0.99
    bad.data['ranked_candidates'][0]['score'] = 0.99
    reasons = web_observation_reasons(bad, {'input_data': {'query': ranker.last_payload}},
                                      None, request_query=query)
    assert 'ranking_evidence_mismatch' in reasons
    reordered = deepcopy(ranking)
    reordered.data['ranked_candidates'].reverse()
    for index, row in enumerate(reordered.data['ranked_candidates'], 1):
        row['rank'] = index
    reordered.data['top_candidates'] = deepcopy(reordered.data['ranked_candidates'])
    reasons = web_observation_reasons(reordered, {'input_data': {'query': ranker.last_payload}},
                                      None, request_query=query)
    assert 'ranking_evidence_mismatch' in reasons
    wrong_scope = deepcopy(ranker.last_payload)
    wrong_scope['outputs']['admet'] = []
    assert ranker.execute(wrong_scope)['success'] is False


def test_invalid_ranking_never_appears_in_web_answer():
    import json
    from types import SimpleNamespace
    from src.web.decision_chat import web_scientific_display
    block = {'tool': 'candidate_ranker', 'status': 'succeeded', 'evidence_id': 'fixture',
             'scientific_usable': False, 'reason_codes': ['ranking_evidence_mismatch'],
             'data': {'top_candidates': [{'rank': 1, 'canonical_smiles': 'CCO', 'score': .99}]}}
    result = SimpleNamespace(metadata={'binding_profile': 'medchat-web-v1'}, outcome=SimpleNamespace(value='partial'),
        final_answer=json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [block]}))
    text = web_scientific_display(result)
    assert '0.99' not in text and '排序结果未通过校验' in text
    block['reason_codes'] = ['invalid_scientific_observation']
    block['data'] = None
    result.final_answer = json.dumps({'acceptance': {'profile': 'medchat-web-v1'}, 'observations': [block]})
    assert '排序结果未通过校验' in web_scientific_display(result)
