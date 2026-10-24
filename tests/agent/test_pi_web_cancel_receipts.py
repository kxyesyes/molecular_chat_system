"""Real Pi/Web cancellation with RDKit work, never live-model acceptance."""
import asyncio
import threading
from copy import deepcopy
from types import MappingProxyType

import pytest

from src.agent.persistence import scientific_references as references
from src.agent.contracts import AgentContext
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.runtime.task_state import TaskEventType
from src.agent.scientific_session.decision_inputs import seal_observation, verify_observation_integrity
from src.agent.tooling.factory import build_tool_registry
from tests.agent.pi_web_support import cleanup_web_handlers
from tests.agent.test_scientific_reference_execution import Tool, confirmed
from tests.agent.test_scientific_reference_resilience import _chat, _handler, _tools, _execution_traces


@pytest.mark.parametrize('fault', ['none', 'invalid-output', 'expired-source'])
def test_cancelled_web_turn_retains_only_verified_completed_receipt(tmp_path, monkeypatch, fault):
    clock = [1000.0]
    monkeypatch.setattr(references.time, 'time', lambda: clock[0])
    store, service, pointer = confirmed(tmp_path)
    entered, release, exited = (threading.Event() for _ in range(3))

    class BlockingProperty(Tool):
        def execute(self, value):
            raw = super().execute(value)
            if fault == 'invalid-output':
                raw['data'][0]['properties']['molecular_weight'] = 'not-a-number'
            entered.set()
            try:
                assert release.wait(10), 'test did not release scientific work'
                return raw
            finally:
                exited.set()

    tools = _tools()
    tools['property_calculator'] = BlockingProperty('property_calculator')
    handler = _handler(store, tools)

    async def run():
        task = asyncio.create_task(_chat(handler, pointer))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 10), 11)
            if fault == 'expired-source':
                clock[0] += 86401
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not exited.is_set()
            assert handler.decision_runtime.application.model_request_gate._readers == 1
            release.set()
            socket = await asyncio.wait_for(task, 10)
            assert exited.is_set()
            assert handler.web_turns[-1].worker_owner.pending_roots == 0
            complete = [frame for frame in socket.messages if frame['type'] == 'complete']
            assert len(complete) == 1 and complete[0]['status'] == 'cancelled'
            assert not any(frame['type'] == 'agent_event'
                           and frame['event'].get('event') == 'tool_completed'
                           and frame['event'].get('tool') == 'property_calculator'
                           for frame in socket.messages)
            assert not any(frame['type'] == 'molecule_candidates' for frame in socket.messages)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 10)

    asyncio.run(run())
    assert tools['property_calculator'].calls == ['CCN']
    assert tools['drug_likeness_assessment'].calls == []
    trace, = _execution_traces(store)
    run_record = store.get_run(trace[0])
    executions = store.get_tool_executions(trace[0])
    if fault == 'none':
        assert run_record['status'] == 'cancelled'
        assert len(executions) == 1 and executions[0]['status'] == 'succeeded'
        result = executions[0]['output']
        assert result['success'] is True and result['status'] == 'succeeded'
        assert result['data'][0]['smiles'] == 'CCN'
        assert result['quality']['binding_proof']['profile'] == 'medchat-web-v1'
        checkpoint = store.latest_checkpoint(trace[0])
        assert checkpoint['status'] == 'succeeded'
        assert checkpoint['output'] == result
        assert service.restore(pointer, session_id='owner')
    else:
        assert run_record['status'] in {'cancelled', 'failed'}
        assert not any(execution['status'] == 'succeeded' for execution in executions)
        checkpoint = store.latest_checkpoint(trace[0])
        assert checkpoint['status'] != 'succeeded'
        if fault == 'invalid-output':
            assert executions, 'invalid output must still settle its execution journal'
            assert executions[0]['output']['data'] is None


@pytest.mark.parametrize('boundary', [
    'capture', 'execution-commit', 'checkpoint-commit', 'warning', 'commit-then-raise',
])
def test_late_web_cancellation_keeps_sealed_receipt_without_later_events(tmp_path, monkeypatch, boundary):
    """Session lifecycle coverage; the test above covers authenticated Pi/Web wiring."""
    cancel = threading.Event()
    store = SQLiteAgentStateStore(tmp_path / 'late-cancel.sqlite')
    events, captured = [], []

    class PropertyWithWarning(Tool):
        def execute(self, value):
            raw = super().execute(value)
            raw['warnings'] = ['RDKit calculation; not experimental validation']
            return raw

    tool = PropertyWithWarning('property_calculator')
    registry = build_tool_registry([tool])

    def publish(event):
        events.append(event)
        if boundary == 'warning' and event.event is TaskEventType.VALIDATION_WARNING:
            cancel.set()

    def capture(result):
        seal_observation(result, session)
        captured.append(deepcopy(result.to_legacy_dict()))
        if boundary == 'capture':
            cancel.set()

    session = WorkflowRunSession(
        WorkflowOrchestrator(state_store=store, event_bus=AgentEventBus(on_event=publish)),
        AgentContext('CCN', 'late-cancel', session_id='owner'), [],
        {tool.name: registry.resolve(tool.name)}, dynamic=True,
        observation_capture=capture, cancel_event=cancel,
    )
    session._decision_binding_profile = 'medchat-web-v1'
    session._decision_observation_seals = MappingProxyType({})
    session.start()
    session.append_step(WorkflowStep('property', tool.name, {'query': 'CCN'}, output_key='property',
        metadata=dict(request_input_digest='request', input_evidence_ids=[], operation_key='property')))
    if boundary in {'execution-commit', 'commit-then-raise'}:
        record = store.record_tool_execution
        attempts = []

        def record_then_cancel(value):
            recorded = record(value)
            cancel.set()
            attempts.append(recorded)
            if boundary == 'commit-then-raise' and len(attempts) == 1:
                raise RuntimeError('injected acknowledgement failure after commit')
            return recorded

        monkeypatch.setattr(store, 'record_tool_execution', record_then_cancel)
    elif boundary == 'checkpoint-commit':
        save = store.save_checkpoint

        def checkpoint_then_cancel(value):
            saved = save(value)
            if value['status'] == 'succeeded':
                cancel.set()
            return saved

        monkeypatch.setattr(store, 'save_checkpoint', checkpoint_then_cancel)
    try:
        if boundary == 'commit-then-raise':
            with pytest.raises(RuntimeError, match='acknowledgement failure'):
                session.execute_step(0)
        session.execute_step(0)
        assert cancel.is_set(), 'cancellation boundary was not exercised'
        assert tool.calls == ['CCN']
        result, = session.results
        verify_observation_integrity(result, session)
        receipt, = store.get_tool_executions(session.context.trace_id)
        assert receipt['status'] == 'succeeded' and result.success
        assert receipt['output'] == captured[0] == result.to_legacy_dict()
        assert store.latest_checkpoint(session.context.trace_id)['output'] == captured[0]
        assert not any(event.event in {TaskEventType.TOOL_COMPLETED, TaskEventType.TOOL_FAILED}
                       for event in events)
        warnings = [event for event in events if event.event is TaskEventType.VALIDATION_WARNING]
        assert len(warnings) == int(boundary == 'warning')
    finally:
        registry.close()
