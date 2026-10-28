"""Admission obligations; real Pi/registry execution, scripted model only."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.scientific_session.web_requirements import (
    compile_web_requirements, prepare_web_requirements, required_web_tools,
)
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.rag_search_tool import RAGSearchTool
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.test_rag_tool_contract import InjectedService, record


def context(query, *, rag=True, tools=True, **kwargs):
    return AgentContext(query, 'obligation-test', session_id='owner',
        metadata={'capabilities': {'rag': rag, 'scientific_tools': tools}}, **kwargs)


@pytest.mark.parametrize('query', [
    '计算阿司匹林的分子性质', 'Calculate molecular properties of aspirin',
    '计算这个分子的分子量', '预测候选的 ADMET',
])
def test_unbound_property_obligation_needs_current_molecule(query):
    ctx = context(query,
                  memory=[{'user': 'CCO', 'assistant': 'earlier molecular answer'}])
    before = deepcopy(ctx)
    # Unrouted English wording already fails closed at whole-request coverage.
    reason = ('uncovered_web_obligation' if query.startswith('Calculate ')
              else 'molecular_input_required')
    with pytest.raises(ValueError, match=reason):
        compile_web_requirements(ctx, allowed_tools=WEB_TOOLS)
    assert ctx == before  # Old conversation text cannot supply molecular authority.


def test_preselected_domain_plan_retains_missing_input_clarification_contract():
    ctx = context('计算理化性质和类药性', active_skill='admet_assessment')
    req = compile_web_requirements(ctx, allowed_tools=WEB_TOOLS)
    assert required_web_tools(req) == {'property_calculator', 'drug_likeness_assessment'}
    assert req.request['query'] == ctx.query


@pytest.mark.parametrize('query', ['CCO', '  CCN  ', 'c1ccccc1', '[Na+].[Cl-]'])
@pytest.mark.parametrize('tools', [False, True])
def test_bare_valid_structure_is_exact_required_retrieval(query, tools):
    ctx = context(query, tools=tools)
    before = deepcopy(ctx)
    req = compile_web_requirements(ctx, allowed_tools=WEB_TOOLS)
    assert req.workflow_name == 'rag_search'
    assert required_web_tools(req) == {'rag_search'}
    assert len(req.steps) == 1 and req.steps[0].required
    assert req.steps[0].input_data == query and req.request['query'] == query
    assert prepare_web_requirements(req, context=ctx, request_kind='scientific',
        allowed_tools={'rag_search'}, required_tools={'rag_search'}) == req
    assert ctx == before


@pytest.mark.parametrize('tools', [False, True])
def test_disabled_rag_does_not_create_bare_structure_obligation(tools):
    req = compile_web_requirements(context('CCO', rag=False, tools=tools), allowed_tools=WEB_TOOLS)
    assert req.workflow_name == 'ordinary_chat' and not required_web_tools(req)


@pytest.mark.parametrize('query', ['请检索关于 C++17 的开发说明', '请检索关于 S9+ 的普通说明'])
def test_plain_knowledge_lookup_keeps_retrieval_obligation_and_permissions(query):
    req = compile_web_requirements(context(query), allowed_tools=WEB_TOOLS)
    assert req.workflow_name == 'rag_search'
    assert required_web_tools(req) == {'rag_search'}
    assert req.steps[0].input_data == query
    with pytest.raises(ValueError):
        compile_web_requirements(context(query, rag=False), allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize('suffix', ['；预测量子轨道能量', '；发送邮件', '并计算分子量', '；删除文件'])
def test_plain_lookup_prefix_cannot_hide_independent_actions(suffix):
    with pytest.raises(ValueError):
        compile_web_requirements(context('请检索关于 C++17 的开发说明' + suffix), allowed_tools=WEB_TOOLS)


def test_enabled_retrieval_cannot_be_silently_omitted_when_not_authorized():
    with pytest.raises(ValueError, match='task_requirements_not_authorized'):
        compile_web_requirements(context('CCO'), allowed_tools=WEB_TOOLS - {'rag_search'})


@pytest.mark.parametrize('query', ['你好', '什么是药物设计', '什么是ADMET', '解释分子量', 'Explain logP'])
def test_known_concepts_remain_chat_even_with_molecule_in_history(query):
    req = compile_web_requirements(context(query,
        memory=[{'user': 'CCO', 'assistant': 'previous molecule'}]), allowed_tools=WEB_TOOLS)
    assert req.workflow_name == 'ordinary_chat' and not req.steps
    assert req.request['query'] == query


@pytest.mark.parametrize('query', [
    'CCO；计算分子量并预测量子轨道能量',
    'CCO；发送邮件', '什么是ADMET；计算阿司匹林的分子性质',
])
def test_bare_or_concept_prefix_cannot_hide_uncovered_current_obligations(query):
    with pytest.raises(ValueError):
        compile_web_requirements(context(query), allowed_tools=WEB_TOOLS)


def test_explicit_property_and_generated_property_obligations_are_preserved():
    for query, tools in [
        ('只计算 CCO 的分子量和 logP，不要 ADMET 或活性', {'property_calculator'}),
        ('生成 3 个候选分子，并计算理化性质', {'llm_molecular_generator', 'property_calculator'}),
    ]:
        ctx = context(query, active_skill='molecular_design' if 'llm_molecular_generator' in tools else None)
        req = compile_web_requirements(ctx, allowed_tools=WEB_TOOLS)
        assert required_web_tools(req) == tools and req.request['query'] == query
        if 'llm_molecular_generator' in tools:
            assert req.dependencies['properties'] == ('molecular_design',)


def test_confirmed_server_molecule_remains_valid_without_rewriting_question():
    query = '只计算刚才第二个分子的分子量和 logP，不要 ADMET 或活性'
    selected = ResolvedScientificMolecule('source', 'presentation', 'a' * 64,
                                          'observation', 'candidate', 'CCN')
    req = compile_web_requirements(context(query, resolved_molecule=selected), allowed_tools=WEB_TOOLS)
    assert required_web_tools(req) == {'property_calculator'}
    assert req.steps[0].input_data == 'CCN' and req.request['query'] == query


@pytest.mark.parametrize('tools', [False, True])
def test_actual_pi_bare_structure_retrieves_once_with_original_query(tmp_path, tools):
    row = record()
    row.update(SMILES='CCO', similarity_score=0.91)
    service = InjectedService([row])
    handler = make_web_handler([RAGSearchTool(service)], SQLiteAgentStateStore(tmp_path / 'rag.sqlite'))
    terminal, frames = run_web_turn(handler, 'CCO', enable_rag=True, enable_tools=tools)
    assert terminal['status'] == 'completed'
    assert service.calls == [('CCO', 5)]
    result = next(frame for frame in frames if frame['type'] == 'agent_result')
    assert [item['tool_name'] for item in result['tool_result_sequence']] == ['rag_search']
    assert result['metadata']['retrieval_performed']
    cards = [frame for frame in frames if frame['type'] == 'rag_info']
    assert len(cards) == 1 and cards[0]['molecules'][0]['smiles'] == 'CCO'
    assert cards[0]['molecules'][0]['provenance']['source_sha256'] == 'a' * 64
    assert len(handler.model.messages) == 1
    assert result['metadata']['task_acceptance']['finish_eligible'] is True


@pytest.mark.parametrize('rag', [False, True])
@pytest.mark.parametrize('query', ['计算阿司匹林的分子性质', '只计算阿司匹林的分子量，不要 ADMET 或活性'])
def test_actual_pi_missing_molecule_stops_before_model_or_tool(tmp_path, rag, query):
    class ForbiddenProperties(PropertyCalculator):
        def execute(self, *args, **kwargs):
            pytest.fail('unresolved molecule reached scientific execution')

    handler = make_web_handler([ForbiddenProperties()], SQLiteAgentStateStore(tmp_path / 'missing.sqlite'))
    terminal, frames = run_web_turn(handler, query, enable_rag=rag)
    assert terminal['status'] == 'rejected'
    assert terminal['error']['code'] == 'invalid_input'
    assert terminal['error']['details'] == {'reason': 'molecular_input_required'}
    assert 'SMILES' in terminal['content'] and '提供' in terminal['content']
    assert '未调用模型或科学工具' in terminal['content']
    result = next(frame for frame in frames if frame['type'] == 'agent_result')
    assert result['error'] == terminal['error']
    assert result['metadata']['stop_reason'] == 'molecular_input_required'
    assert handler.model.messages == []
    assert not any(frame['type'] == 'rag_info' or (frame['type'] == 'agent_event'
        and frame['event'].get('event') == 'tool_started') for frame in frames)


def test_preparation_preserves_exact_missing_molecule_reason():
    from src.web.decision_request import DecisionAdmissionError
    from src.web.web_request import prepare_web_request

    with pytest.raises(DecisionAdmissionError) as caught:
        prepare_web_request({'message': '计算阿司匹林的分子性质'}, session_id='owner', trace_id='missing')
    assert caught.value.code == 'molecular_input_required'


@pytest.mark.parametrize('failure', [
    ValueError('unrecognized-private-diagnostic'),
    ValueError('molecular_input_required: private-diagnostic'),
    ValueError('molecular_input_required', 'private-diagnostic'),
    TypeError('molecular_input_required'),
])
def test_reason_forwarding_is_closed_and_never_publishes_other_exception_text(monkeypatch, failure):
    import src.web.web_request as admission
    from src.web.decision_request import DecisionAdmissionError

    def fail_compile(*args, **kwargs):
        raise failure

    monkeypatch.setattr(admission, 'compile_web_requirements', fail_compile)
    with pytest.raises(DecisionAdmissionError) as caught:
        admission.prepare_web_request({'message': '你好'}, session_id='owner', trace_id='closed-reason')
    assert caught.value.code == 'request_clarification_required'
    assert str(caught.value) == 'request_clarification_required'
