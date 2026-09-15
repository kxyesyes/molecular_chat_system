"""Isolated ChatHandler entry: actual loop/RDKit, explicit decision doubles."""
import asyncio
import json
from dataclasses import replace

import pytest

from test_decision_loop import setup_loop, tool, finish, finish_last, clarify
from test_decision_inputs import downstream
from test_decision_requirements import requirements
from src.agent.contracts import AgentContext
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.web.chat_handler import ChatHandler


class ForbiddenLegacy:
    def __getattr__(self, name):
        raise AssertionError('legacy service must not be called: ' + name)


class Socket:
    def __init__(self):
        self.messages = []
    async def send_text(self, text):
        self.messages.append(json.loads(text))


def context(query='SMILES: CCO\nSMILES: CCN'):
    return AgentContext(query, 'isolated-chat', user_id='test-owner', session_id='test-session')


def handler():
    return ChatHandler(ForbiddenLegacy(), ForbiddenLegacy(), ForbiddenLegacy(), {})


async def invoke(b, ws, ctx=None, **kw):
    return await handler().process_decision_message(ws, context=ctx or context(), decision_loop=b.loop,
        request_kind=kw.pop('request_kind', 'scientific'),
        allowed_tools={'property_calculator', 'drug_likeness_assessment'},
        required_tools=kw.pop('required_tools', {'property_calculator'}), **kw)


def terminal(ws):
    messages = [m for m in ws.messages if m['type'] == 'complete']
    assert len(messages) == 1
    return messages[0]


def test_chat_calls_model_without_tools_or_legacy_reply(setup_loop):
    b = setup_loop([finish(text='这是主模型本轮的回答', kind='chat')])
    ws = Socket()
    result = asyncio.run(invoke(b, ws, context('你好'), request_kind='chat', required_tools=set()))
    assert result.success and len(b.model.messages) == 1 and not b.tools[0].inputs
    assert terminal(ws)['content'] == '这是主模型本轮的回答'
    assert terminal(ws)['status'] == 'completed'


def test_rdkit_batch_direct_output_preserves_evidence_and_events(setup_loop):
    b = setup_loop([tool(), downstream, finish_last], [PropertyCalculator(), DrugLikenessAssessment()])
    ws = Socket()
    result = asyncio.run(invoke(b, ws, requirements=requirements(2, expected_smiles=['CCO', 'CCN'])))
    assert result.success and len(b.model.messages) == 3
    assert terminal(ws)['content'] == result.final_answer
    assert terminal(ws)['trace_id'] == context().trace_id
    envelope = next(m for m in ws.messages if m['type'] == 'agent_result')
    assert len(envelope['tool_result_sequence']) == 2
    assert all(len(t['data']) == 2 for t in envelope['tool_result_sequence'])
    assert envelope['metadata']['task_acceptance']['satisfied']
    assert envelope['tool_result_sequence'][1]['quality']['input_evidence_ids']
    assert ws.messages[0]['type'] == 'agent_event' and ws.messages[-1]['type'] == 'complete'
    assert any(m.get('event', {}).get('event') == 'tool_completed' for m in ws.messages)


def test_clarification_can_resume_same_owner_and_trace(setup_loop):
    b = setup_loop([clarify(), tool(), finish_last], [PropertyCalculator()])
    ctx = context('请计算分子性质')
    async def scenario():
        ws = Socket()
        waiting = await invoke(b, ws, ctx)
        assert terminal(ws)['status'] == 'waiting_for_input'
        assert terminal(ws)['continuation_id'] == waiting.metadata['continuation_id']
        other = Socket()
        denied = await invoke(b, other, replace(ctx, user_id='other'),
                              continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
        assert not denied.success and terminal(other)['status'] == 'rejected'
        resumed_ws = Socket()
        resumed = await invoke(b, resumed_ws, ctx, continuation_id=waiting.metadata['continuation_id'],
                               clarified_query='SMILES: CCO')
        assert resumed.success and terminal(resumed_ws)['status'] == 'completed'
    asyncio.run(scenario())


def test_disabled_tools_cannot_be_reenabled_by_entry(setup_loop):
    b = setup_loop([tool()])
    ws = Socket()
    ctx = context()
    ctx.metadata = {'capabilities': {'scientific_tools': False, 'rag': False}}
    result = asyncio.run(invoke(b, ws, ctx))
    assert not result.success and not b.tools[0].inputs
    assert terminal(ws)['status'] in {'failed', 'rejected'}


def test_invalid_input_never_becomes_visualization_or_success(setup_loop):
    b = setup_loop([tool(), finish_last], [PropertyCalculator()])
    ws = Socket()
    result = asyncio.run(invoke(b, ws, context('SMILES: CC(C)((')))
    assert not result.success
    assert terminal(ws)['status'] != 'completed'
    assert not any(m['type'] == 'molecular_generation' for m in ws.messages)


def test_unexpected_error_completes_without_leaking_details():
    class BrokenLoop:
        store = None
        async def run(self, *a, **kw):
            raise RuntimeError('api_key=synthetic-private-error')
    class Bundle:
        loop = BrokenLoop()
    ws = Socket()
    result = asyncio.run(invoke(Bundle(), ws))
    assert not result.success and terminal(ws)['status'] == 'failed'
    assert 'synthetic-private-error' not in json.dumps(ws.messages)


def test_events_are_delivered_before_model_finishes(setup_loop):
    async def scenario():
        released = asyncio.Event()
        b = setup_loop([])
        class Model:
            async def decide(self, *args, **kwargs):
                await asyncio.wait_for(released.wait(), 2)
                from src.agent.decision_transport import DecisionResponse
                return DecisionResponse(finish(text='live', kind='chat'), None, 'live', {})
        b.loop.model = Model()
        class LiveSocket(Socket):
            async def send_text(self, text):
                await super().send_text(text)
                released.set()
        ws = LiveSocket()
        result = await invoke(b, ws, request_kind='chat', required_tools=set())
        assert result.success and terminal(ws)['content'] == 'live'
    asyncio.run(scenario())


def test_disconnect_cancels_inflight_model_without_fallback(setup_loop):
    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        b = setup_loop([])
        class Model:
            async def decide(self, *a, **kw):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
        b.loop.model = Model()
        class Disconnected(Socket):
            async def send_text(self, text):
                await started.wait()
                raise ConnectionError('disconnected')
        with pytest.raises(ConnectionError):
            await invoke(b, Disconnected(), request_kind='chat', required_tools=set())
        assert cancelled.is_set()
        assert b.store.get_run(context().trace_id)['status'] == 'cancelled'
    asyncio.run(scenario())


@pytest.mark.parametrize('identity', ['user_id', 'session_id'])
def test_identity_is_required_before_model_dispatch(setup_loop, identity):
    b = setup_loop([])
    with pytest.raises(ValueError):
        asyncio.run(invoke(b, Socket(), replace(context(), **{identity: None})))
    assert not b.model.messages


def test_partial_is_not_relabelled_failed_or_completed(setup_loop):
    b = setup_loop([tool(), finish_last], [PropertyCalculator()])
    ws = Socket()
    result = asyncio.run(invoke(b, ws, context('SMILES: CCO'), requirements=requirements(2)))
    assert not result.success and terminal(ws)['status'] == 'partial'
    assert 'CCO' in terminal(ws)['content']


def test_long_display_has_explicit_truncation_marker(setup_loop):
    from src.agent.contracts import AgentResult, RunOutcome
    class ResultLoop:
        store = None
        async def run(self, *a, **kw):
            return AgentResult('large', True, 'done', final_answer='A' * 20000,
                               outcome=RunOutcome.COMPLETED)
    class Bundle:
        loop = ResultLoop()
    ws = Socket()
    result = asyncio.run(invoke(Bundle(), ws))
    assert terminal(ws)['display_redacted_or_truncated']
    assert len(result.final_answer) == 20000


def test_redacted_credential_value_sets_display_marker():
    from src.agent.contracts import AgentResult, RunOutcome
    class ResultLoop:
        store = None
        async def run(self, *a, **kw):
            return AgentResult('private', True, 'done', final_answer='sk-' + 'synthetic-test-only-key',
                               outcome=RunOutcome.COMPLETED)
    class Bundle:
        loop = ResultLoop()
    ws = Socket()
    asyncio.run(invoke(Bundle(), ws))
    assert 'synthetic-test-only-key' not in json.dumps(ws.messages)
    assert terminal(ws)['display_redacted_or_truncated']


@pytest.mark.parametrize('bad_key', ['sk-' + 'synthetic-review-only-key', 'D:/private/diagnostic'])
def test_sensitive_keys_at_depth_are_not_sent(bad_key):
    from src.agent.contracts import AgentResult, RunOutcome
    class Loop:
        store = None
        async def run(self, *a, **kw):
            nested = {bad_key: 'value'}
            for _ in range(8):
                nested = {'details': nested}
            return AgentResult('keys', False, 'failed', outcome=RunOutcome.FAILED, metadata=nested)
    class Bundle:
        loop = Loop()
    ws = Socket()
    asyncio.run(invoke(Bundle(), ws))
    assert bad_key not in json.dumps(ws.messages)
    assert terminal(ws)['display_redacted_or_truncated']


@pytest.mark.parametrize('number', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_diagnostic_does_not_drop_completion(number):
    from src.agent.contracts import AgentResult, RunOutcome
    class Loop:
        store = None
        async def run(self, *a, **kw):
            return AgentResult('nonfinite', False, 'failed', outcome=RunOutcome.FAILED,
                               metadata={'diagnostic': number})
    class Bundle:
        loop = Loop()
    ws = Socket()
    asyncio.run(invoke(Bundle(), ws))
    assert terminal(ws)['status'] == 'failed'
    assert terminal(ws)['display_redacted_or_truncated']
    json.dumps(ws.messages, allow_nan=False)


def test_streamed_event_truncation_is_explicit():
    from src.agent.contracts import AgentResult, RunOutcome
    from src.agent.runtime.task_state import TaskEventType
    class Loop:
        store = None
        async def run(self, ctx, *, event_bus, **kw):
            event_bus.emit(ctx.trace_id, TaskEventType.TASK_STARTED, 'A' * 1000)
            return AgentResult(ctx.trace_id, True, 'done', final_answer='done', outcome=RunOutcome.COMPLETED)
    class Bundle:
        loop = Loop()
    ws = Socket()
    asyncio.run(invoke(Bundle(), ws))
    assert ws.messages[0]['display_redacted_or_truncated']
    assert terminal(ws)['display_redacted_or_truncated']
