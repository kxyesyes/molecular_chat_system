"""Public legacy shape tests; synthetic canonical results, not scientific claims."""
from copy import deepcopy
import inspect

import pytest

import src.agent.agent_executor as module
from src.agent.contracts import AgentContext, AgentErrorCode, AgentResult, ObservationStatus, ToolResult
from src.agent.contracts.domain import WorkflowArtifact
from src.agent.contracts.scientific import ToolProvenance
from src.agent.supervisor import SupervisorAgent


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(module, 'get_core_tools', lambda: [])
    monkeypatch.setattr(module, 'get_optional_tool', lambda name: (_ for _ in ()).throw(RuntimeError('unavailable')))
    return module.MolecularAgent()


@pytest.mark.parametrize('method,key', [('execute', 'tool_results'), ('execute_tools', 'results')])
@pytest.mark.parametrize('status', list(ObservationStatus))
def test_canonical_result_shape_preserves_status_and_evidence(agent, monkeypatch, method, key, status):
    success = status in {ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL}
    if success:
        tool = ToolResult.success_result('activity_predictor', {'synthetic': True}, 'synthetic', status=status)
    else:
        tool = ToolResult.error_result('activity_predictor', AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
                                      'unavailable', status=status)
    tool.warnings = ['fixture warning']
    tool.evidence = [{'source': 'synthetic-test'}]
    tool.quality = {'step_id': 'prediction', 'fixture': True}
    tool.artifacts = [WorkflowArtifact('test', 'synthetic/result.json', 'synthetic artifact')]
    tool.provenance = ToolProvenance(tool_name='activity_predictor', model_name='synthetic-test')
    result = AgentResult.from_tool_results('synthetic-trace', 'activity_prediction', [tool], message='result')
    expected = deepcopy(result.to_legacy_dict())
    calls = []
    events = [{'event': 'tool_started', 'tool': 'activity_predictor'}, {'event': 'task_completed'}]

    def execute(self, query, **kwargs):
        calls.append((query, kwargs))
        # Supervisor's legacy chat envelope treats partial as success. Adapter must not.
        return {'success': result.success or result.partial, 'agent_result': result,
                'trace_id': result.trace_id, 'agent_events': events,
                'workflow_plan': {'steps': ['activity_predictor']}}

    monkeypatch.setattr(SupervisorAgent, 'execute', execute)
    output = getattr(agent, method)('Predict activity for CCO')
    assert len(calls) == 1
    assert output['success'] == result.success
    assert output['partial'] == result.partial
    assert output['status'] == expected['status']
    assert output[key] == expected['tool_result_sequence']
    assert output['error'] == expected['error']
    assert output['warnings'] == expected['warnings']
    assert output['evidence'] == expected['evidence']
    assert output['artifacts'] == expected['artifacts']
    assert output['trace_id'] == 'synthetic-trace'
    assert output['agent_events'] == events
    assert output['used_tools'] == ['activity_predictor']


def test_repeated_observations_and_partial_error_are_not_filtered(agent, monkeypatch):
    first = ToolResult.success_result('property_calculator', {'synthetic': 1}, 'first', quality={'step_id': 'first'})
    second = ToolResult.error_result('property_calculator', AgentErrorCode.TOOL_TIMEOUT, 'timed out',
                                     quality={'step_id': 'second'}, warnings=['timed out'])
    result = AgentResult.from_tool_results('repeat', 'admet_assessment', [first, second], final_answer='partial answer')
    monkeypatch.setattr(SupervisorAgent, 'execute', lambda *a, **k: {
        'agent_result': result, 'trace_id': 'repeat', 'agent_events': [
            {'event': 'tool_started', 'tool': 'property_calculator'},
            {'event': 'tool_started', 'tool': 'property_calculator'},
        ]})
    output = agent.execute('计算 CCO 的理化性质')
    assert output['response'] == 'partial answer'
    assert output['success'] is False and output['partial'] is True
    assert output['used_tools'] == ['property_calculator', 'property_calculator']
    assert [item['step_id'] for item in output['tool_results']] == ['first', 'second']
    assert output['tool_results'][1]['error']['message'] == 'timed out'


@pytest.mark.parametrize('method', ['execute', 'execute_tools'])
@pytest.mark.parametrize('count', [None, 3])
def test_delegate_once_without_turning_none_into_explicit_count(agent, monkeypatch, method, count):
    seen = []
    def execute(self, query, **kwargs):
        seen.append(kwargs)
        result = AgentResult('forward', False, 'unavailable')
        return {'agent_result': result, 'trace_id': result.trace_id, 'agent_events': []}
    monkeypatch.setattr(SupervisorAgent, 'execute', execute)
    getattr(agent, method)('Generate 7 molecules', temperature=0.23, mol_count=count)
    assert len(seen) == 1
    assert seen[0]['temperature'] == 0.23
    assert ('mol_count' in seen[0]) == (count is not None)
    if count is not None:
        assert seen[0]['mol_count'] == count


@pytest.mark.parametrize('method', ['execute', 'execute_tools'])
@pytest.mark.parametrize('count', [0, False, 11, 3.5, '3'])
def test_preflight_rejects_count_before_delegate_or_optional_loading(agent, monkeypatch, method, count):
    def forbidden(*a, **k):
        pytest.fail('invalid count crossed preflight')
    monkeypatch.setattr(SupervisorAgent, 'execute', forbidden)
    monkeypatch.setattr(module, 'get_optional_tool', forbidden)
    output = getattr(agent, method)('Predict activity for CCO', mol_count=count)
    assert output['success'] is False
    assert output['error']['code'] == 'invalid_input'
    assert output['used_tools'] == []


def test_greeting_does_not_use_eager_tool_predicates_or_optional_factories(agent, monkeypatch):
    class Trap:
        name = 'property_calculator'
        def should_use(self, query):
            pytest.fail('legacy per-tool predicate used')
        def execute(self, query):
            pytest.fail('greeting executed scientific tool')
    agent.core_tools = [Trap()]
    monkeypatch.setattr(module, 'get_optional_tool', lambda *a: pytest.fail('greeting loaded optional tool'))
    assert agent.should_use_tools('你好') is False
    assert agent.execute('你好')['success'] is False


def test_public_signatures_remain_compatible():
    assert str(inspect.signature(module.MolecularAgent)) == '(llm=None)'
    for method in ['execute', 'execute_tools']:
        parameters = inspect.signature(getattr(module.MolecularAgent, method)).parameters
        assert list(parameters) == ['self', 'query', 'temperature', 'mol_count']
        assert parameters['temperature'].default == 0.7
        assert parameters['mol_count'].default is None


def test_existing_rag_alias_does_not_load_a_second_tool(agent, monkeypatch):
    class BorrowedRag:
        name = 'rag_database_search'
    borrowed = BorrowedRag()
    agent.core_tools = [borrowed]
    loads = []
    monkeypatch.setattr(module, 'get_optional_tool', lambda name: loads.append(name))
    seen = []
    def execute(self, query, **kwargs):
        seen.append(self._request_tools(AgentContext(
            query=query, trace_id='alias', active_skill='rag_search')))
        result = AgentResult('alias', False, 'synthetic')
        return {'agent_result': result, 'trace_id': 'alias', 'agent_events': []}
    monkeypatch.setattr(SupervisorAgent, 'execute', execute)
    agent.execute('请检索本地知识库中关于分子设计的资料')
    assert len(seen) == 1
    assert loads == []
    assert seen[0]['rag_search'] is borrowed


def test_optional_loading_is_plan_scoped_cached_and_request_local(agent, monkeypatch):
    class Activity:
        name = 'activity_predictor'
    tool = Activity()
    loads, snapshots = [], []
    def load(name):
        loads.append(name)
        return tool
    def execute(self, query, **kwargs):
        snapshots.append(self.tools)
        result = AgentResult('load', False, 'synthetic')
        return {'agent_result': result, 'trace_id': 'load', 'agent_events': []}
    monkeypatch.setattr(module, 'get_optional_tool', load)
    monkeypatch.setattr(SupervisorAgent, 'execute', execute)
    for _ in range(2):
        agent.execute('Predict activity for CCO')
    assert loads == ['ActivityPredictorTool']
    assert snapshots[0] is not snapshots[1]
    assert snapshots[0]['activity_predictor'] is snapshots[1]['activity_predictor'] is tool
    assert agent.core_tools == []


def test_cancellation_propagates_without_retry_or_closing_borrowed_tools(agent, monkeypatch):
    calls = []
    def execute(*a, **k):
        calls.append(1)
        raise KeyboardInterrupt('cancelled')
    monkeypatch.setattr(SupervisorAgent, 'execute', execute)
    with pytest.raises(KeyboardInterrupt):
        agent.execute('Predict activity for CCO')
    assert calls == [1]
