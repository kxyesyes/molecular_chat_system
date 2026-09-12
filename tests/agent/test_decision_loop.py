"""Isolated controller contracts; scripted models are not real LLM acceptance."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult, RunOutcome
from src.agent.contracts.decision import ToolDecision, FinishDecision, ClarifyDecision
from src.agent.decision_transport import DecisionResponse
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import WorkflowRunSession, SessionLifecycleError
from src.agent.tooling.factory import build_tool_registry


def tool(name='property_calculator', arguments=None):
    return ToolDecision(version='1', action='tool', tool_name=name,
                        arguments=arguments or {'input_ref': 'user'}, purpose='compute')


def finish(ids=(), text='model prose must not become scientific facts', kind='scientific'):
    return FinishDecision(version='1', action='finish', response_kind=kind,
                          text=text, evidence_ids=list(ids))


def clarify():
    return ClarifyDecision(version='1', action='clarify', question='Please provide input',
                           missing_fields=['smiles'])


class ScriptedModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.messages = []

    async def decide(self, messages, **kwargs):
        self.messages.append(json.loads(json.dumps(messages)))
        decision = next(self.decisions)
        if callable(decision):
            decision = decision(messages)
        return DecisionResponse(decision, None, f'call-{len(self.messages)}',
                                {'model': 'scripted-test', 'request_attempts': 1})


class CountingTool:
    description = 'Explicit contract test double'
    version = 'test-1'
    timeout_seconds = 0.5

    def __init__(self, name='property_calculator', fail=False, demo=False):
        self.name, self.fail, self.demo = name, fail, demo
        self.inputs = []

    def execute(self, query):
        self.inputs.append(query)
        if self.fail:
            return ToolResult.error_result(self.name, AgentErrorCode.MODEL_UNAVAILABLE,
                                          'Test dependency unavailable', warnings=['test-warning'])
        return ToolResult.success_result(self.name, {'smiles': 'CCO', 'molecular_weight': 46.069},
                                         quality={'demo_mode': self.demo}, warnings=['test-warning'])


def last_observation(messages):
    return json.loads(messages[-1]['content'])


def finish_last(messages):
    return finish([last_observation(messages)['quality']['evidence_id']])


@pytest.fixture
def setup_loop(tmp_path):
    registries = []

    def build(decisions, tools=None, **kwargs):
        from src.agent.harness.decision_loop import ModelDecisionLoop
        supplied = tools if tools is not None else [CountingTool()]
        registry = build_tool_registry(supplied)
        registries.append(registry)
        store = SQLiteAgentStateStore(tmp_path / f'state-{len(registries)}.sqlite')
        bus = AgentEventBus(state_store=store)
        model = ScriptedModel(decisions)
        return SimpleNamespace(loop=ModelDecisionLoop(model, registry, store, **kwargs),
                               registry=registry, store=store, bus=bus, model=model, tools=supplied)
    yield build
    for registry in registries:
        registry.close()


def run(bundle, **kwargs):
    return asyncio.run(bundle.loop.run(AgentContext('SMILES: CCO', 'trace-test'),
        allowed_tools={'property_calculator', 'drug_likeness_assessment'},
        required_tools=kwargs.pop('required_tools', {'property_calculator'}),
        request_kind=kwargs.pop('request_kind', 'scientific'), event_bus=bundle.bus, **kwargs))


def test_dynamic_session_uses_existing_validation_ledger_and_keeps_running():
    source = CountingTool()
    registry = build_tool_registry([source])
    try:
        session = WorkflowRunSession(WorkflowOrchestrator(), AgentContext('CCO', 'one'),
                                     [], registry.as_mapping(), dynamic=True)
        session.start()
        session.append_step(WorkflowStep('round-1', source.name, {'query': 'CCO'}, required=False))
        advance = session.execute_step(0)
        assert not advance.terminal
        assert session.results[0].quality['evidence_id']
        assert session.results[0].provenance.input_digest
        result = session.finish_dynamic('observed')
        assert result.final_answer == 'observed'
        with pytest.raises(SessionLifecycleError):
            session.append_step(WorkflowStep('late', source.name))
    finally:
        registry.close()


def test_exclusive_start_never_overwrites_trace_across_store_instances(tmp_path):
    one = SQLiteAgentStateStore(tmp_path / 'state.sqlite')
    two = SQLiteAgentStateStore(tmp_path / 'state.sqlite')
    one.start_run({'trace_id': 'same', 'user_id': 'owner'}, exclusive=True)
    with pytest.raises(Exception):
        two.start_run({'trace_id': 'same', 'user_id': 'intruder'}, exclusive=True)
    assert one.get_run('same')['user_id'] == 'owner'


def test_loop_uses_model_observations_not_planner(setup_loop, monkeypatch):
    from src.agent.planning.task_planner import TaskPlanner
    monkeypatch.setattr(TaskPlanner, 'plan', lambda *a, **k: pytest.fail('static planner called'))
    b = setup_loop([tool(), finish_last])
    result = run(b)
    assert result.success
    assert result.metadata['backend'] == 'model_decision_loop'
    assert len(b.model.messages) == 2
    assert b.tools[0].inputs == ['SMILES: CCO']
    assert '46.069' in result.final_answer
    assert 'model prose' not in result.final_answer
    events = [e.event.value for e in b.bus.events]
    assert events.count('task_started') == events.count('task_completed') == 1
    assert events.count('planning_started') == events.count('planning_completed') == 2
    assert all(e.progress is None for e in b.bus.events if e.event.value.startswith('tool_'))


@pytest.mark.parametrize('failed', [False, True])
def test_next_action_changes_with_observation(setup_loop, failed):
    def branch(messages):
        return clarify() if not last_observation(messages)['success'] else tool('drug_likeness_assessment')
    b = setup_loop([tool(), branch, finish_last],
                   [CountingTool(fail=failed), CountingTool('drug_likeness_assessment')])
    result = run(b)
    assert len(b.tools[1].inputs) == (0 if failed else 1)
    assert result.success is (not failed)
    if failed:
        assert result.metadata['waiting_for_input']
        assert b.store.get_run('trace-test')['status'] == 'waiting_for_input'


@pytest.mark.parametrize('bad_tool,arguments', [
    ('run_docking', None), ('llm_molecular_generator', None),
    ('property_calculator', {'query': 'invented SMILES'}),
    ('property_calculator', {'input_ref': 'other-trace'}),
    ('property_calculator', {'input_ref': 'user', 'smiles': 'CCN'}),
])
def test_gate_rejects_unknown_tools_and_model_invented_inputs(setup_loop, bad_tool, arguments):
    b = setup_loop([tool(bad_tool, arguments)])
    result = run(b)
    assert not result.success
    assert b.tools[0].inputs == []


def test_unknown_evidence_and_unfulfilled_obligation_fail(setup_loop):
    b = setup_loop([tool(), finish(['evidence-foreign'])])
    assert not run(b).success
    b = setup_loop([tool(), finish_last])
    assert not run(b, required_tools={'property_calculator', 'activity_predictor'}).success


def test_demo_cannot_be_promoted_to_scientific_success(setup_loop):
    b = setup_loop([tool(), finish_last], [CountingTool(demo=True)])
    result = run(b)
    assert not result.success
    assert '46.069' not in result.final_answer


def test_duplicate_action_reuses_observation_without_reexecuting(setup_loop):
    b = setup_loop([tool(), tool(), finish_last])
    result = run(b)
    assert result.success
    assert len(b.tools[0].inputs) == 1
    assert result.metadata['reused_decisions'] == 1


def test_budget_and_existing_trace_do_not_replay(setup_loop):
    b = setup_loop([tool(), tool()], max_model_requests=1)
    result = run(b)
    assert not result.success
    assert result.metadata['stop_reason'] == 'model_budget_exhausted'
    before = b.store.get_run('trace-test')
    again = run(b)
    assert not again.success
    assert len(b.tools[0].inputs) == 1
    assert b.store.get_run('trace-test') == before


def test_chat_calls_model_but_never_scientific_tools(setup_loop):
    b = setup_loop([finish(text='你好，有什么可以帮助你？', kind='chat')])
    result = run(b, request_kind='chat', required_tools=set())
    assert result.success and '你好' in result.final_answer
    assert len(b.model.messages) == 1 and not b.tools[0].inputs
    b = setup_loop([tool()])
    assert not run(b, request_kind='chat', required_tools=set()).success
    assert not b.tools[0].inputs


def test_scientific_task_cannot_escape_as_chat_finish(setup_loop):
    b = setup_loop([finish(text='pIC50=9.9 docking -10 kcal/mol', kind='chat')])
    result = run(b)
    assert not result.success
    assert '9.9' not in result.final_answer


def test_model_cancellation_records_one_terminal(setup_loop):
    b = setup_loop([])
    async def cancel(*a, **kw):
        raise asyncio.CancelledError()
    b.model.decide = cancel
    result = run(b)
    assert result.outcome == RunOutcome.CANCELLED
    assert [e.event.value for e in b.bus.events].count('task_cancelled') == 1
    assert not b.tools[0].inputs


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_real_rdkit_through_existing_http_model_protocol(setup_loop, mode):
    import httpx
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    from src.agent.tools.property_calculator import PropertyCalculator
    b = setup_loop([], [PropertyCalculator()], mode=mode)
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        decision = tool() if len(calls) == 1 else finish_last(payload['messages'])
        if mode == 'native':
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': f'call-{len(calls)}', 'type': 'function', 'function': {
                    'name': 'agent_decision',
                    'arguments': json.dumps({'decision': decision.model_dump()})}}]}
            reason = 'tool_calls'
        else:
            message = {'role': 'assistant', 'content': json.dumps({'decision': decision.model_dump()})}
            reason = 'stop'
        return httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': message}],
                                         'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            b.loop.model = OpenAICompatibleModel('fake-test-key', 'protocol-test',
                                                'https://example.invalid', client=client)
            return await b.loop.run(AgentContext('SMILES: CCO', 'trace-rdkit'),
                request_kind='scientific', allowed_tools={'property_calculator'},
                required_tools={'property_calculator'}, event_bus=b.bus)
    result = asyncio.run(exercise())
    assert result.success, result.metadata
    assert len(calls) == 2
    assert result.tool_results[0].quality['validated']
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    displayed = json.loads(result.final_answer.removeprefix('```json\n').removesuffix('\n```'))
    assert displayed['data'][0]['properties']['molecular_weight'] == pytest.approx(
        Descriptors.MolWt(Chem.MolFromSmiles('CCO')), abs=0.01)
    rounds = result.metadata['model_calls']
    assert [r['model'] for r in rounds] == ['protocol-test', 'protocol-test']
    assert rounds[1]['usage']['total'] == 30
    assert rounds[1]['usage_unit'] == 'tokens'
    assert b.store.get_run('trace-rdkit')['metadata']['decision_loop']['model_calls'] == rounds


def test_failed_observation_warnings_are_persisted(setup_loop):
    b = setup_loop([tool(), clarify()], [CountingTool(fail=True)])
    run(b)
    execution = b.store.get_tool_executions('trace-test')[0]
    assert execution['output']['warnings']
    assert execution['output']['provenance']['input_digest']


def test_invalid_real_smiles_never_yields_properties(setup_loop):
    from src.agent.tools.property_calculator import PropertyCalculator
    b = setup_loop([tool(), clarify()], [PropertyCalculator()])
    result = asyncio.run(b.loop.run(AgentContext('SMILES: CC(C)((', 'invalid'),
        request_kind='scientific', allowed_tools={'property_calculator'},
        required_tools={'property_calculator'}, event_bus=b.bus))
    assert not result.success
    assert not any(r.success for r in result.tool_results)
    assert '46.069' not in result.final_answer


def test_tool_budget_reserves_internal_retries(setup_loop):
    b = setup_loop([tool()], max_tool_attempts=1)
    adapter = b.registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, retry_policy=replace(adapter.spec.retry_policy, max_attempts=2))
    result = run(b)
    assert result.metadata['stop_reason'] == 'tool_budget_exhausted'
    assert not b.tools[0].inputs


def test_deadline_stops_before_another_model_request(setup_loop):
    import time
    class SlowTool(CountingTool):
        def execute(self, query):
            time.sleep(0.1)
            return super().execute(query)
    b = setup_loop([tool(), finish_last], [SlowTool()], timeout_seconds=0.04)
    result = run(b)
    assert not result.success
    assert len(b.model.messages) <= 1
    assert result.metadata['stop_reason'] in {'tool_execution_state_unconfirmed', 'task_deadline_exceeded'}


def test_model_cannot_drop_capability_restriction(setup_loop):
    b = setup_loop([tool()])
    result = asyncio.run(b.loop.run(AgentContext('CCO', 'disabled',
        metadata={'capabilities': {'scientific_tools': False}}),
        request_kind='scientific', allowed_tools={'property_calculator'},
        required_tools={'property_calculator'}, event_bus=b.bus))
    assert not result.success and not b.tools[0].inputs


def test_native_call_id_is_validated_before_dispatch(setup_loop):
    b = setup_loop([])
    async def missing_id(*a, **kw):
        return DecisionResponse(tool(), None, None, {})
    b.model.decide = missing_id
    result = run(b)
    assert not result.success
    assert b.tools[0].inputs == []


def test_tool_configuration_cannot_change_after_authorization(setup_loop):
    b = setup_loop([])
    adapter = b.registry.resolve('property_calculator')
    async def change_spec(*a, **kw):
        adapter.spec = replace(adapter.spec, side_effects='filesystem')
        return DecisionResponse(tool(), None, 'call-1', {})
    b.model.decide = change_spec
    result = run(b)
    assert not result.success and not b.tools[0].inputs


def test_tool_events_and_provenance_have_dispatch_identity(setup_loop):
    b = setup_loop([tool(), finish_last])
    result = run(b)
    assert result.tool_results[0].provenance.tool_version == 'test-1'
    for event in b.bus.events:
        if event.event.value in {'tool_started', 'tool_completed'}:
            assert event.payload['decision_id']
            assert event.payload['tool_call_id'] == 'call-1'
            assert event.payload['round'] == 1


def test_mismatched_tool_identity_is_not_trusted(setup_loop):
    class ImpersonatingTool(CountingTool):
        def execute(self, query):
            result = super().execute(query)
            result.tool_name = 'activity_predictor'
            return result
    b = setup_loop([tool(), clarify()], [ImpersonatingTool()])
    result = run(b)
    assert not result.tool_results[0].success
    assert result.tool_results[0].tool_name == 'property_calculator'


@pytest.mark.parametrize('event_name', ['task_started', 'tool_started', 'tool_completed', 'task_completed'])
def test_broken_notification_callback_cannot_break_authoritative_run(setup_loop, event_name):
    b = setup_loop([tool(), finish_last])
    deliveries = []
    def broken(event):
        deliveries.append(event.event.value)
        if event.event.value == event_name:
            raise RuntimeError('private notification failure')
    b.bus.on_event = broken
    result = run(b)
    assert result.success
    assert len(b.tools[0].inputs) == 1
    assert deliveries.count(event_name) == 1
    assert b.store.get_run('trace-test')['status'] == 'succeeded'
    assert 'private notification' not in repr(result)


def test_double_cancellation_during_tool_settles_before_terminal(setup_loop):
    import threading
    entered, release = threading.Event(), threading.Event()
    class Blocked(CountingTool):
        timeout_seconds = 2
        def execute(self, query):
            entered.set()
            assert release.wait(2)
            return super().execute(query)
    b = setup_loop([tool(), finish_last], [Blocked()])
    async def exercise():
        task = asyncio.create_task(b.loop.run(AgentContext('CCO', 'cancel-twice'),
            request_kind='scientific', allowed_tools={'property_calculator'},
            required_tools={'property_calculator'}, event_bus=b.bus))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            release.set()
            return await task
        finally:
            release.set()
    result = asyncio.run(exercise())
    assert result.outcome == RunOutcome.CANCELLED
    assert b.store.get_run('cancel-twice')['status'] == 'cancelled'
    assert b.bus.events[-1].event.value == 'task_cancelled'
    assert len(b.model.messages) == 1


@pytest.mark.parametrize('permanent', [False, True])
def test_execution_record_failure_never_reexecutes_tool(setup_loop, permanent):
    b = setup_loop([tool(), finish_last])
    original = b.store.record_tool_execution
    calls = []
    def fail_after_commit(record):
        calls.append(record['id'])
        original(record)
        if permanent or len(calls) == 1:
            raise OSError('private storage failure')
    b.store.record_tool_execution = fail_after_commit
    result = run(b)
    assert len(b.tools[0].inputs) == 1
    assert len(set(calls)) == 1
    assert result.success is (not permanent)
    assert b.store.get_run('trace-test')['status'] != 'running'
    assert b.bus.events[-1].event.value.startswith('task_')
    assert 'private storage failure' not in repr(result)


def test_adapter_cannot_retry_after_outer_deadline(setup_loop):
    import threading
    entered, release = threading.Event(), threading.Event()
    class LateFailure(CountingTool):
        timeout_seconds = 2
        def execute(self, query):
            self.inputs.append(query)
            entered.set()
            assert release.wait(2)
            return ToolResult.error_result(self.name, AgentErrorCode.PROVIDER_ERROR, 'test provider error')
    source = LateFailure()
    b = setup_loop([tool(), clarify()], [source], timeout_seconds=0.5)
    adapter = b.registry.resolve(source.name)
    adapter.spec = replace(adapter.spec, retry_policy=replace(adapter.spec.retry_policy,
        max_attempts=2, retryable_error_codes={'provider_error'}))
    async def exercise():
        result = await b.loop.run(AgentContext('CCO', 'late-retry'),
            request_kind='scientific', allowed_tools={source.name}, required_tools={source.name}, event_bus=b.bus)
        release.set()
        await asyncio.sleep(0.1)
        return result
    try:
        result = asyncio.run(exercise())
        assert not result.success
        assert len(source.inputs) <= 1
    finally:
        release.set()


@pytest.mark.parametrize('failure', ['metadata', 'status_before', 'status_after', 'waiting_status'])
def test_terminal_persistence_recovers_without_restarting_graph(setup_loop, failure):
    b = setup_loop([tool(), clarify() if failure == 'waiting_status' else finish_last])
    metadata_write, status_write = b.store.update_run_metadata, b.store.update_run_status
    injected = []
    def write_metadata(trace, metadata):
        metadata_write(trace, metadata)
        if failure == 'metadata' and metadata['decision_loop']['phase'] == 'terminal' and not injected:
            injected.append(True)
            raise OSError('private finalization error')
    def write_status(trace, status):
        match = failure.startswith('status_') or failure == 'waiting_status' and status == 'waiting_for_input'
        if match and not injected:
            injected.append(True)
            if failure != 'status_before':
                status_write(trace, status)
            raise OSError('private finalization error')
        return status_write(trace, status)
    b.store.update_run_metadata = write_metadata
    b.store.update_run_status = write_status
    result = run(b)
    assert injected
    assert result.success is (failure != 'waiting_status')
    assert len(b.tools[0].inputs) == 1 and len(b.model.messages) == 2
    assert sum(e.event.value in {'task_completed', 'task_partial', 'task_rejected', 'task_failed'}
               for e in b.bus.events) == 1
    assert b.store.get_run('trace-test')['status'] == ('waiting_for_input' if failure == 'waiting_status' else 'succeeded')
