"""One budgeted schema correction; authorization and evidence never get repaired."""
import asyncio

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, AgentExecutionError
from src.agent.decision_transport import DecisionResponse, _snapshot_messages
from test_decision_loop import setup_loop, run, tool, finish, finish_last, clarify


def invalid(reason='invalid_decision_schema', code=AgentErrorCode.INVALID_OUTPUT):
    return DecisionResponse(None, AgentExecutionError(code, 'private-provider-text', {
        'reason': reason, 'schema_issues': [{'path': 'decision.tool.version', 'type': 'missing'},
                                          {'path': 'private-field', 'type': 'private-error'}]}),
        None, {'request_attempts': 1})


class ProtocolModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.messages = []

    async def decide(self, messages, **kwargs):
        _snapshot_messages(messages)  # Native tool-call history must remain paired.
        self.messages.append(messages)
        action = next(self.actions)
        if isinstance(action, DecisionResponse):
            return action
        if callable(action):
            action = action(messages)
        return DecisionResponse(action, None, f'call-{len(self.messages)}', {'request_attempts': 1})


def configured(setup_loop, actions, **kwargs):
    b = setup_loop([], **kwargs)
    b.model = b.loop.model = ProtocolModel(actions)
    return b


@pytest.mark.parametrize('mode', ['json', 'native'])
def test_one_schema_correction_before_tool_or_after_observation(setup_loop, mode):
    for actions in ([invalid(), tool(), finish_last], [tool(), invalid(), finish_last]):
        b = configured(setup_loop, actions, mode=mode)
        result = run(b)
        assert result.success
        assert len(b.tools[0].inputs) == 1
        assert result.metadata['model_requests'] == 3
        assert result.metadata['protocol_repairs'] == 1
        assert len(result.metadata['model_calls']) == 3
        assert 'private-' not in repr(b.model.messages)
        assert 'private-' not in repr(result.metadata)
        assert any('invalid_decision_schema' in m['content'] for messages in b.model.messages
                   for m in messages if m['role'] == 'system')


def test_second_schema_failure_stops_without_tool_or_fallback(setup_loop):
    b = configured(setup_loop, [invalid(), invalid(), tool()])
    result = run(b)
    assert not result.success
    assert result.metadata['model_requests'] == 2
    assert result.metadata['protocol_repairs'] == 1
    assert result.metadata['stop_reason'] == 'model_decision_unavailable'
    assert not b.tools[0].inputs


def test_correction_cannot_exceed_model_budget(setup_loop):
    b = configured(setup_loop, [invalid(), tool()], max_model_requests=1)
    result = run(b)
    assert not result.success and len(b.model.messages) == 1
    assert result.metadata['stop_reason'] == 'model_budget_exhausted'
    assert not b.tools[0].inputs


@pytest.mark.parametrize('reason,code', [
    ('decision_http_error', AgentErrorCode.PROVIDER_ERROR),
    ('decision_timeout', AgentErrorCode.PROVIDER_ERROR),
    ('decision_refused', AgentErrorCode.INVALID_OUTPUT),
    ('invalid_decision_json', AgentErrorCode.INVALID_OUTPUT),
    ('invalid_function_call', AgentErrorCode.INVALID_OUTPUT),
    ('invalid_decision_schema', AgentErrorCode.INVALID_INPUT),
])
def test_only_schema_output_failure_is_correctable(setup_loop, reason, code):
    b = configured(setup_loop, [invalid(reason, code), tool()])
    result = run(b)
    assert not result.success and len(b.model.messages) == 1
    assert result.metadata['protocol_repairs'] == 0
    assert not b.tools[0].inputs


@pytest.mark.parametrize('proposal,stop_reason', [
    (tool('run_docking'), 'tool_not_authorized'),
    (tool(arguments={'query': 'CCN'}), 'untrusted_tool_input'),
    (finish(['fabricated-evidence']), 'evidence_not_usable_in_this_trace'),
])
def test_schema_correction_does_not_relax_scientific_boundaries(setup_loop, proposal, stop_reason):
    b = configured(setup_loop, [invalid(), proposal, tool()])
    result = run(b)
    assert not result.success
    assert result.metadata['stop_reason'] == stop_reason
    assert not b.tools[0].inputs and len(b.model.messages) == 2


def test_clarification_resume_cannot_reset_correction_allowance(setup_loop):
    b = configured(setup_loop, [invalid(), clarify(), invalid(), tool()])
    context = AgentContext('Please provide properties', 'repair-resume', user_id='owner', session_id='session')
    options = dict(request_kind='scientific', allowed_tools={'property_calculator'},
                   required_tools={'property_calculator'})
    waiting = asyncio.run(b.loop.run(context, **options))
    assert waiting.metadata['waiting_for_input']
    assert waiting.metadata['protocol_repairs'] == 1
    result = asyncio.run(b.loop.run(context, **options,
        continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO'))
    assert not result.success
    assert result.metadata['protocol_repairs'] == 1
    assert result.metadata['model_requests'] == 3
    assert len(b.model.messages) == 3 and not b.tools[0].inputs


def test_correction_feedback_applies_only_to_the_retry_not_future_turns(setup_loop):
    b = configured(setup_loop, [invalid(), tool(), finish_last])
    assert run(b).success
    assert 'invalid_decision_schema' in repr(b.model.messages[1])
    assert 'invalid_decision_schema' not in repr(b.model.messages[2])


@pytest.mark.parametrize('cancel', [False, True])
def test_deadline_or_cancellation_during_correction_never_dispatches_tools(setup_loop, cancel, monkeypatch):
    from types import SimpleNamespace
    from src.agent.harness import decision_loop
    now = [0.0]
    monkeypatch.setattr(decision_loop, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    b = configured(setup_loop, [invalid()], timeout_seconds=5)
    original = b.model.decide
    async def decide(messages, **kwargs):
        if not b.model.messages:
            result = await original(messages, **kwargs)
            if not cancel:
                now[0] = 6.0
            return result
        assert cancel, 'correction deadline was ignored'
        raise asyncio.CancelledError()
    b.model.decide = decide
    result = run(b)
    assert not result.success and not b.tools[0].inputs
    assert result.metadata['protocol_repairs'] == 1
    assert result.metadata['model_requests'] == (2 if cancel else 1)
    assert result.metadata['stop_reason'] == ('cancelled' if cancel else 'task_deadline_exceeded')


def test_wait_for_timeout_during_correction_is_reported_as_timeout(setup_loop, monkeypatch):
    b = configured(setup_loop, [invalid()])
    original_decide, real_wait_for = b.model.decide, asyncio.wait_for
    async def decide(messages, **kwargs):
        if not b.model.messages:
            return await original_decide(messages, **kwargs)
        await asyncio.sleep(60)
    async def bounded_wait(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=min(timeout, 0.05))
    b.model.decide = decide
    monkeypatch.setattr(asyncio, 'wait_for', bounded_wait)
    result = run(b)
    assert not result.success and not b.tools[0].inputs
    assert result.metadata['model_requests'] == 2
    assert result.metadata['protocol_repairs'] == 1
    assert result.metadata['stop_reason'] == 'task_deadline_exceeded'
