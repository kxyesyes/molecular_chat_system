import pytest

from src.agent.contracts import AgentContext
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing import HybridSkillRouter
from src.agent.tools.target_database_tool import TargetDatabaseTool


def context(query, active_skill):
    return AgentContext(query=query, active_skill=active_skill, trace_id='target-test')


@pytest.mark.parametrize('alias', ['BuChE', 'BCHE', 'buche', '丁酰胆碱酯酶'])
def test_family_aliases_route_and_plan_the_same_design(alias):
    query = f'针对{alias}生成 5 个候选分子'
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'target_driven_design'
    plan = TaskPlanner().plan(context(query, decision.selected_skill))
    assert plan.steps[0].input_data == 'BCHE'
    assert plan.metadata['target_hint'] == 'BCHE'
    assert plan.steps[1].preconditions == ('target_evidence',)
    assert plan.metadata['requested_count'] == 5


@pytest.mark.parametrize('query', [
    '针对 PDE5A 和 EGFR 生成 5 个候选分子',
    '针对 PDE5A 和 UNKNOWN42 生成 5 个候选分子',
    '针对 UNKNOWN42 和 PDE5A 生成 5 个候选分子',
    '针对 PDE999Z 生成 5 个候选分子',
    '针对 XEGFRfoo 生成 5 个候选分子',
    '针对 PDE5A_extra 生成 5 个候选分子',
    '针对未知蛋白和 PDE5A 生成 5 个候选分子',
    '针对 PDE5A, UNKNOWN42 生成 5 个候选分子',
    '针对未知蛋白生成 5 个候选分子',
    '不要针对 BuChE，改为 PDE5A 生成 5 个候选分子',
    '针对 PDE5A 生成 5 个候选分子，不要针对 BuChE',
])
def test_unresolved_target_intent_never_silently_selects_first_or_generic_design(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None
    assert decision.requires_confirmation
    plan = TaskPlanner().plan(context(query, 'target_driven_design'))
    assert not plan.steps
    assert plan.metadata['reason'] == 'target_clarification_required'
    assert plan.metadata['message']


@pytest.mark.parametrize('query', [
    'Generate 5 molecules targeting EGFR and do not output binding energy',
    'Target EGFR and generate 5 molecules',
])
def test_non_target_qualifiers_and_action_words_do_not_block_design(query):
    assert HybridSkillRouter().decide(query).selected_skill == 'target_driven_design'
    plan = TaskPlanner().plan(context(query, 'target_driven_design'))
    assert plan.metadata['target_hint'] == 'EGFR'
    assert plan.steps


def test_forced_generic_generation_cannot_bypass_ambiguous_target_guard():
    plan = TaskPlanner().plan(context('针对 PDE5A 和 BuChE 生成 5 个候选分子', 'molecular_design'))
    assert not plan.steps
    assert plan.metadata['reason'] == 'target_clarification_required'


def test_comparison_search_retains_both_targets_without_generation():
    query = '比较 BuChE 和 PDE5A 的蛋白结构'
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'target_database_search'
    plan = TaskPlanner().plan(context(query, decision.selected_skill))
    assert [step.tool_name for step in plan.steps] == ['target_database_search']
    assert TargetDatabaseTool()._search_queries(plan.steps[0].input_data) == ['BCHE', 'PDE5A']


def test_explanation_never_becomes_generation_even_with_explicit_search_skill():
    query = '解释 BuChE 分子设计是什么'
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill not in {'target_driven_design', 'molecular_design', 'docking_simulation'}
    plan = TaskPlanner().plan(context(query, 'target_database_search'))
    assert all(step.tool_name == 'target_database_search' for step in plan.steps)


@pytest.mark.parametrize('query', [
    '你好，搜索 EGFR 蛋白结构',
    '你好，搜索 BuChE 蛋白结构',
])
def test_greeting_does_not_short_circuit_explicit_target_search(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'target_database_search'
    assert not decision.requires_confirmation


@pytest.mark.parametrize('alias', ['BuChE', 'BCHE', '丁酰胆碱酯酶'])
def test_tool_normalizes_only_explicit_alias_not_arbitrary_substrings(alias):
    tool = TargetDatabaseTool()
    assert tool._search_queries(alias) == ['BCHE']
    assert tool._search_queries('XEGFRfoo') == ['XEGFRfoo']
    assert tool._search_queries('UNKNOWN42') == ['UNKNOWN42']


def test_forced_design_with_ambiguous_target_fails_in_actual_executor():
    from src.agent.runtime.workflow_executor import WorkflowExecutor
    from src.agent.workflows import WorkflowCatalog

    result = WorkflowExecutor().execute(
        context=context('针对 PDE5A 和 BuChE 生成 5 个候选分子', 'target_driven_design'),
        policy=WorkflowCatalog().require('target_driven_design'), all_tools={},
    ).result
    assert not result.success
    assert result.error.code.value == 'invalid_input'
    assert result.error.details['reason'] == 'target_clarification_required'


def test_chat_returns_target_clarification_without_model_or_tools():
    import asyncio
    import json
    from types import SimpleNamespace
    from src.agent.router import SkillRouter
    from src.web.chat_handler import ChatHandler

    class Model:
        async def generate(self, *args, **kwargs):
            raise AssertionError('Ambiguous request must not fall through to model')

    class Socket:
        messages = []
        async def send_text(self, text):
            self.messages.append(json.loads(text))

    handler = ChatHandler(Model(), SimpleNamespace(is_initialized=False),
                          SimpleNamespace(skill_router=SkillRouter(), llm=None), {})
    socket = Socket()
    asyncio.run(handler._process_message(socket, '针对 PDE5A 和 BuChE 生成 5 个候选分子', False, True))
    terminal = [message for message in socket.messages if message['type'] == 'complete']
    assert len(terminal) == 1
    assert '单一靶点' in terminal[0]['content']
    assert len(handler.conversation_history) == 1


@pytest.mark.parametrize('target_status', ['not_found', 'unavailable'])
def test_recognized_buche_without_real_target_evidence_cannot_generate(target_status):
    from src.agent.runtime.workflow_executor import WorkflowExecutor
    from src.agent.workflows import WorkflowCatalog

    class Service:
        def __init__(self):
            self.calls = []

        def search_targets(self, query):
            self.calls.append(query)
            return {'status': target_status, 'results': [],
                    'warnings': ['no real evidence'], 'evidence': []}

    class Forbidden:
        def __init__(self, name):
            self.name = name
            self.calls = []
        def execute(self, query):
            self.calls.append(query)
            raise AssertionError('No generation/evaluation without target evidence')

    policy = WorkflowCatalog().require('target_driven_design')
    tools = {name: Forbidden(name) for name in policy.allowed_tools}
    target = TargetDatabaseTool()
    service = Service()
    target._service = service
    tools[target.name] = target
    execution = WorkflowExecutor().execute(
        context=context('针对 BuChE 生成 5 个候选分子', policy.name),
        policy=policy, all_tools=tools,
    )
    assert service.calls == ['BCHE']
    assert not execution.result.success
    assert not any(tool.calls for name, tool in tools.items() if name != target.name)


@pytest.mark.parametrize('skill', ['target_driven_design', 'molecular_design'])
def test_supervisor_run_preserves_target_clarification_error(skill):
    from src.agent.supervisor import SupervisorAgent

    result = SupervisorAgent(tools={}).run(
        '针对 PDE5A 和 BuChE 生成 5 个候选分子', skill_name=skill,
    )
    assert result['status'] == 'failed'
    assert result['result']['error']['code'] == 'invalid_input'
    assert result['result']['error']['details']['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('entry', ['planner', 'preflight', 'supervisor'])
def test_forced_optimization_clarifies_target_ambiguity(entry):
    from src.agent.runtime.workflow_executor import WorkflowExecution, WorkflowExecutor
    from src.agent.supervisor import SupervisorAgent
    from src.agent.workflows import WorkflowCatalog

    query = '针对 PDE5A 和 BuChE 优化 CCO，生成 5 个候选分子'
    skill = 'hit_to_lead_optimization'
    if entry == 'planner':
        plan = TaskPlanner().plan(context(query, skill))
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'
    elif entry == 'preflight':
        policy = WorkflowCatalog().require(skill)
        prepared = WorkflowExecutor().prepare(
            context=context(query, skill), policy=policy,
            all_tools={name: object() for name in policy.allowed_tools},
        )
        assert isinstance(prepared, WorkflowExecution)
        assert prepared.result.error.code.value == 'invalid_input'
        assert prepared.result.error.details['reason'] == 'target_clarification_required'
    else:
        result = SupervisorAgent(tools={}).run(query, skill_name=skill)
        assert result['result']['error']['code'] == 'invalid_input'
        assert result['result']['error']['details']['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('query', [
    'Generate 5 molecules for EGFR and UNKNOWN42',
    'Generate 5 molecules for EGFR, UNKNOWN42',
    'EGFR and UNKNOWN42: generate 5 molecules',
])
def test_english_target_lists_retain_unknown_identifiers(query):
    from src.agent.contracts.target_request import analyze_target_request
    from src.agent.runtime.workflow_executor import WorkflowExecutor
    from src.agent.workflows import WorkflowCatalog

    request = analyze_target_request(query)
    assert request.targets == ('EGFR',)
    assert request.unknown == ('UNKNOWN42',)
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None
    assert decision.requires_confirmation
    for skill in ('target_driven_design', 'molecular_design', 'hit_to_lead_optimization'):
        plan = TaskPlanner().plan(context(query, skill))
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'
        result = WorkflowExecutor().execute(
            context=context(query, skill), policy=WorkflowCatalog().require(skill), all_tools={},
        ).result
        assert result.error.code.value == 'invalid_input'
        assert result.error.details['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('query', [
    'Generate 5 molecules for EGFR',
    'Generate 5 molecules for EGFR and do not output binding energy',
    'Generate 5 molecules for EGFR and report properties',
    'EGFR and generate 5 molecules',
])
def test_english_target_actions_remain_generation(query):
    from src.agent.contracts.target_request import analyze_target_request

    request = analyze_target_request(query)
    assert request.targets == ('EGFR',)
    assert not request.unknown
    assert not request.needs_clarification
    assert HybridSkillRouter().decide(query).selected_skill == 'target_driven_design'
    plan = TaskPlanner().plan(context(query, 'target_driven_design'))
    assert plan.metadata['target_hint'] == 'EGFR'
    assert any(step.tool_name == 'llm_molecular_generator' for step in plan.steps)


@pytest.mark.parametrize('query', [
    '优化 CCO，生成 5 个候选分子',
    'Optimize CCO and generate 5 molecules for testing',
])
def test_nontarget_optimization_still_prepares_generation(query):
    from src.agent.runtime.workflow_executor import PreparedWorkflow, WorkflowExecutor
    from src.agent.workflows import WorkflowCatalog

    skill = 'hit_to_lead_optimization'
    plan = TaskPlanner().plan(context(query, skill))
    assert any(step.tool_name == 'llm_molecular_generator' for step in plan.steps)
    assert plan.metadata.get('reason') != 'target_clarification_required'
    policy = WorkflowCatalog().require(skill)
    assert isinstance(WorkflowExecutor().prepare(
        context=context(query, skill), policy=policy,
        all_tools={name: object() for name in policy.allowed_tools},
    ), PreparedWorkflow)


def test_generic_generation_for_purpose_is_not_target_request():
    from src.agent.contracts.target_request import analyze_target_request

    query = 'Generate 5 molecules for testing'
    request = analyze_target_request(query)
    assert not request.targets
    assert not request.unknown
    assert not request.explicit
    assert HybridSkillRouter().decide(query).selected_skill == 'molecular_design'


@pytest.mark.parametrize('unknown', ['UNKNOWN42', 'XYZ17', 'kinase99', 'ABC_27', 'NTRK'])
@pytest.mark.parametrize('template', [
    'Generate 5 molecules for {unknown} and EGFR',
    'Generate 5 molecules for EGFR and {unknown}',
    '{unknown} and EGFR: generate 5 molecules',
    'EGFR and {unknown}: generate 5 molecules',
    'Generate 5 molecules for {unknown}, EGFR',
    'Generate 5 molecules for {unknown}',
])
def test_identifier_lists_are_symmetric_and_unknown_only_clarifies(unknown, template):
    from src.agent.contracts.target_request import analyze_target_request
    from src.agent.runtime.workflow_executor import WorkflowExecutor
    from src.agent.supervisor import SupervisorAgent
    from src.agent.workflows import WorkflowCatalog

    query = template.format(unknown=unknown)
    request = analyze_target_request(query)
    assert request.unknown == (unknown,)
    assert request.targets == (('EGFR',) if 'EGFR' in query else ())
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None
    assert decision.requires_confirmation
    for skill in ('target_driven_design', 'molecular_design', 'hit_to_lead_optimization'):
        plan = TaskPlanner().plan(context(query, skill))
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'
        result = WorkflowExecutor().execute(
            context=context(query, skill), policy=WorkflowCatalog().require(skill), all_tools={},
        ).result
        assert result.error.code.value == 'invalid_input'
        assert result.error.details['reason'] == 'target_clarification_required'
        result = SupervisorAgent(tools={}).run(query, skill_name=skill)
        assert result['result']['error']['details']['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('query', [
    'Generate 5 molecules for testing',
    'Generate 5 molecules for research and report properties',
    'Generate 5 molecules and report properties',
    'Optimize CCO and generate 5 molecules for testing',
    'CCO and generate 5 molecules for testing',
    'Generate 5 molecules and report ADMET, pIC50 and docking results',
    'Generate 5 analogues of CCO and CCN',
])
def test_identifier_lists_do_not_capture_purpose_or_action_clauses(query):
    from src.agent.contracts.target_request import analyze_target_request

    request = analyze_target_request(query)
    assert not request.targets
    assert not request.unknown
    assert not request.explicit
    plan = TaskPlanner().plan(context(query, 'hit_to_lead_optimization'))
    assert any(step.tool_name == 'llm_molecular_generator' for step in plan.steps)
    assert plan.metadata.get('reason') != 'target_clarification_required'


@pytest.mark.parametrize('count', [2, 5, 6])
def test_comparison_search_respects_service_cap_without_silent_omission(count):
    targets = ['EGFR', 'BRAF', 'KRAS', 'JAK2', 'ALK', 'MET'][:count]
    query = 'Compare protein structures of ' + ', '.join(targets[:-1]) + ' and ' + targets[-1]

    class Service:
        def __init__(self):
            self.calls = []

        def search_targets(self, target):
            self.calls.append(target)
            return {'status': 'resolved', 'results': [{'gene_symbol': target}]}

    service = Service()
    tool = TargetDatabaseTool()
    tool._service = service
    plan = TaskPlanner().plan(context(query, 'target_database_search'))
    results = [tool.execute(step.input_data) for step in plan.steps]
    if count > 5:
        assert service.calls == []
        assert not results
        assert plan.metadata['reason'] == 'target_clarification_required'
        assert '5' in plan.metadata['message']
        assert plan.metadata['target_candidates'] == targets
    else:
        assert service.calls == targets
        assert len(results) == 1
        assert results[0]['success']
        assert results[0]['status'] == 'resolved'
        assert results[0]['quality']['status'] == 'complete'


@pytest.mark.parametrize('count', [16, 32, 64])
@pytest.mark.parametrize('qualified_unknown', [False, True])
def test_target_list_scan_has_linear_operation_bound(monkeypatch, count, qualified_unknown):
    from src.agent.contracts import target_request as module

    class CountingPattern:
        def __init__(self, pattern):
            self.pattern = pattern
            self.calls = 0

        def match(self, *args, **kwargs):
            self.calls += 1
            return self.pattern.match(*args, **kwargs)

    counter = CountingPattern(module._FOLLOWING)
    monkeypatch.setattr(module, '_FOLLOWING', counter)
    members = ['EGFR'] * count
    if qualified_unknown:
        members[0] = 'UNKNOWN42'
        members[-1] = 'XYZ17'
    query = 'Generate 5 molecules for ' + ' and '.join(members)
    if qualified_unknown:
        query += ', not against BRAF'
    request = module.analyze_target_request(query)
    assert request.targets == (('EGFR', 'BRAF') if qualified_unknown else ('EGFR',))
    if qualified_unknown:
        assert 'UNKNOWN42' in request.unknown
        assert 'XYZ17' in request.unknown
        assert request.qualified
        assert request.needs_clarification
    else:
        assert not request.unknown
        assert not request.needs_clarification
    assert counter.calls <= 8 * count + 32, counter.calls


@pytest.mark.parametrize('entry', ['executor', 'supervisor'])
def test_comparison_cap_message_reaches_actual_entrypoints(monkeypatch, entry):
    from src.agent.runtime.workflow_executor import WorkflowExecutor
    from src.agent.supervisor import SupervisorAgent
    from src.agent.workflows import WorkflowCatalog

    query = 'Compare protein structures of EGFR, BRAF, KRAS, JAK2, ALK and MET'
    calls = []
    tool = TargetDatabaseTool()

    def forbidden(query):
        calls.append(query)
        raise AssertionError('Oversized comparison must stop before tool execution')

    monkeypatch.setattr(tool, 'execute', forbidden)
    tools = {tool.name: tool}
    if entry == 'executor':
        result = WorkflowExecutor().execute(
            context=context(query, tool.name),
            policy=WorkflowCatalog().require(tool.name), all_tools=tools,
        ).result
        messages = [result.message, result.error.message]
        assert result.error.code.value == 'invalid_input'
    else:
        result = SupervisorAgent(tools=tools).run(query, skill_name=tool.name)['result']
        messages = [result['message'], result['error']['message']]
        assert result['error']['code'] == 'invalid_input'
    assert calls == []
    for message in messages:
        assert '5' in message
        assert '单一靶点' not in message


@pytest.mark.parametrize('message', ['missing', None, '', '   ', {}, 5])
def test_target_clarification_message_has_safe_fallback(message):
    from src.agent.runtime.workflow_executor import WorkflowExecutor
    from src.agent.workflows import WorkflowCatalog

    class Planner(TaskPlanner):
        def plan(self, context):
            plan = super().plan(context)
            if message == 'missing':
                plan.metadata.pop('message', None)
            else:
                plan.metadata['message'] = message
            return plan

    result = WorkflowExecutor(planner=Planner()).execute(
        context=context('Generate 5 molecules for EGFR and UNKNOWN42', 'molecular_design'),
        policy=WorkflowCatalog().require('molecular_design'), all_tools={},
    ).result
    assert result.message == '请明确本次使用的单一靶点标识后重试。'
    assert result.error.message == result.message
    assert result.error.code.value == 'invalid_input'
