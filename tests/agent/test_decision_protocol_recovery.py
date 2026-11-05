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


def ambiguous_calls(count=2):
    return DecisionResponse(None, AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'private-provider-text',
        {'reason': 'ambiguous_decision_response'}), None,
        {'mode': 'native', 'request_attempts': 1, 'native_response_shape': {
            'call_count': count, 'content_present': False,
            'function_name_valid': True, 'call_id_valid': True}})


def malformed_native_json():
    return DecisionResponse(None, AgentExecutionError(AgentErrorCode.INVALID_OUTPUT,
        'private-provider-text', {'reason': 'invalid_decision_json'}), None,
        {'mode': 'native', 'request_attempts': 1, 'native_response_shape': {
            'call_count': 1, 'content_present': False,
            'function_name_valid': True, 'call_id_valid': True}})


@pytest.mark.parametrize('after_tool', [False, True])
def test_single_native_json_failure_gets_one_new_proposal_without_replaying_content(setup_loop, after_tool):
    actions = ([tool(), malformed_native_json(), finish_last] if after_tool
               else [malformed_native_json(), tool(), finish_last])
    b = configured(setup_loop, actions, mode='native')
    result = run(b)
    assert result.success, result.metadata
    assert len(b.tools[0].inputs) == 1
    assert result.metadata['protocol_repairs'] == 1
    assert result.metadata['model_requests'] == 3
    assert 'private-provider-text' not in repr(b.model.messages)
    assert any('invalid_decision_json' in m['content'] for messages in b.model.messages
               for m in messages if m['role'] == 'system')


@pytest.mark.parametrize('change', [
    {'call_count': 0}, {'call_count': 2}, {'call_count': True},
    {'content_present': True}, {'function_name_valid': False}, {'call_id_valid': False},
])
def test_json_failure_without_verified_single_native_call_stops(setup_loop, change):
    response = malformed_native_json()
    response.metadata['native_response_shape'].update(change)
    b = configured(setup_loop, [response, tool()], mode='native')
    result = run(b)
    assert not result.success and result.metadata['protocol_repairs'] == 0
    assert len(b.model.messages) == 1 and not b.tools[0].inputs


def test_native_json_correction_shares_existing_attempt_and_model_budgets(setup_loop):
    repeated = configured(setup_loop, [malformed_native_json(), invalid(), tool()], mode='native')
    result = run(repeated)
    assert not result.success and result.metadata['protocol_repairs'] == 1
    assert len(repeated.model.messages) == 2 and not repeated.tools[0].inputs
    exhausted = configured(setup_loop, [malformed_native_json(), tool()], mode='native', max_model_requests=1)
    result = run(exhausted)
    assert result.metadata['stop_reason'] == 'model_budget_exhausted'
    assert len(exhausted.model.messages) == 1 and not exhausted.tools[0].inputs


def test_multiple_native_calls_recover_without_executing_rejected_proposals(setup_loop):
    b = configured(setup_loop, [ambiguous_calls(), tool(), finish_last], mode='native')
    result = run(b)
    assert result.success, result.metadata
    assert len(b.tools[0].inputs) == 1
    assert result.metadata['protocol_repairs'] == 1
    assert 'private-provider-text' not in repr(b.model.messages)
    assert any('exactly one' in m['content'] for messages in b.model.messages
               for m in messages if m['role'] == 'system')


@pytest.mark.parametrize('change', [
    {'call_count': 1}, {'call_count': True}, {'content_present': True},
    {'function_name_valid': False}, {'call_id_valid': False},
])
def test_ambiguous_response_without_verified_multiple_call_shape_is_not_retried(setup_loop, change):
    response = ambiguous_calls()
    response.metadata['native_response_shape'].update(change)
    b = configured(setup_loop, [response, tool()], mode='native')
    result = run(b)
    assert not result.success and result.metadata['protocol_repairs'] == 0
    assert len(b.model.messages) == 1 and not b.tools[0].inputs


def test_multiple_call_repair_survives_clarification_resume(setup_loop):
    b = configured(setup_loop, [ambiguous_calls(), clarify(), tool(), finish_last], mode='native')
    context = AgentContext('Please provide properties', 'multi-repair', user_id='owner', session_id='session')
    options = dict(request_kind='scientific', allowed_tools={'property_calculator'},
                   required_tools={'property_calculator'})
    waiting = asyncio.run(b.loop.run(context, **options))
    assert waiting.metadata.get('waiting_for_input'), waiting.metadata
    result = asyncio.run(b.loop.run(context, **options,
        continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO'))
    assert result.success, result.metadata
    assert result.metadata['protocol_repairs'] == 1
    assert len(b.tools[0].inputs) == 1


def test_multiple_call_repair_is_bounded_by_one_attempt_and_model_budget(setup_loop):
    repeated = configured(setup_loop, [ambiguous_calls(), ambiguous_calls(), tool()], mode='native')
    result = run(repeated)
    assert not result.success and result.metadata['protocol_repairs'] == 1
    assert len(repeated.model.messages) == 2 and not repeated.tools[0].inputs
    exhausted = configured(setup_loop, [ambiguous_calls(), tool()], mode='native', max_model_requests=1)
    result = run(exhausted)
    assert result.metadata['stop_reason'] == 'model_budget_exhausted'
    assert len(exhausted.model.messages) == 1 and not exhausted.tools[0].inputs


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
@pytest.mark.parametrize('failure', [invalid, malformed_native_json])
def test_schema_correction_does_not_relax_scientific_boundaries(setup_loop, proposal, stop_reason, failure):
    b = configured(setup_loop, [failure(), proposal, tool()], mode='native')
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
    from src.agent.scientific_session import controller as decision_loop
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
    from types import SimpleNamespace
    from src.agent.scientific_session import controller
    # Bound the model transaction under test, not the separate Node startup
    # handshake (which has its own readiness deadline and no model calls).
    monkeypatch.setattr(controller, 'asyncio', SimpleNamespace(
        wait_for=bounded_wait, CancelledError=asyncio.CancelledError,
        TimeoutError=asyncio.TimeoutError))
    result = run(b)
    assert not result.success and not b.tools[0].inputs
    assert result.metadata['model_requests'] == 2
    assert result.metadata['protocol_repairs'] == 1
    assert result.metadata['stop_reason'] == 'task_deadline_exceeded'
