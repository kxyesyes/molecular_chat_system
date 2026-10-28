"""Pure Pi projection contracts; synthetic observations are not scientific acceptance."""
import asyncio
from copy import deepcopy
import importlib
import json

import pytest

from src.agent.contracts import AgentErrorCode, AgentExecutionError
from src.agent.contracts.decision import (
    ClarifyDecision, DecisionProtocolError, FinishDecision, ToolDecision,
)
from src.agent.decision_transport import DecisionResponse, _snapshot_messages


def test_module_provides_public_protocol_api():
    try:
        module = importlib.import_module('src.agent.pi_runtime.provider')
    except ModuleNotFoundError:
        pytest.fail('Pi provider adapter has not been implemented')
    for name in ('ModelProviderAdapter', 'project_messages', 'decision_tool_call', 'tool_definitions'):
        assert callable(getattr(module, name, None))


@pytest.fixture
def adapter():
    try:
        return importlib.import_module('src.agent.pi_runtime.provider')
    except ModuleNotFoundError:
        pytest.fail('Pi provider adapter has not been implemented')


def decision():
    return ToolDecision(version='1', action='tool', tool_name='property_calculator',
                        arguments={'input_ref': 'user'}, purpose='Compute supplied inputs')


def pair(proposal=None, *, call_id='call-1', name=None, content='{"status":"failed"}'):
    proposal = proposal or decision()
    name = name or {'tool': getattr(proposal, 'tool_name', None),
                    'finish': 'finish_task', 'clarify': 'clarify_task'}[proposal.action]
    return [
        {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': call_id,
         'name': name, 'arguments': {'decision': proposal.model_dump()}}],
         'api': 'medchat', 'provider': 'medchat', 'model': 'test',
         'stopReason': 'toolUse', 'timestamp': 1},
        {'role': 'toolResult', 'toolCallId': call_id, 'toolName': name,
         'content': [{'type': 'text', 'text': content}], 'isError': True, 'timestamp': 2},
    ]


def history():
    return [{'role': 'system', 'content': [{'type': 'text', 'text': 'Scientific '},
                                         {'type': 'text', 'text': 'constraints.'}]},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'SMILES: CCO'}]},
            *pair()]


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_web_outbound_compaction_preserves_every_json_token_and_audit(adapter, mode):
    observation = r'{ "score": -0.000, "label": "a b \"x\"", "source": "\u003csource\u003e", "missing": null, "warnings": ["not validated"] }'
    assert json.loads(observation)['label'] == 'a b "x"'
    # A normal JSON-looking user input must not be mistaken for an observation.
    user = '{ "ordinary": "keep this spacing" }'
    messages = history()
    messages[1]['content'] = user
    messages[-1]['content'][0]['text'] = observation
    before = deepcopy(messages)
    seen = []

    class Model:
        async def decide(self, projected, **kwargs):
            seen.append(projected)
            return DecisionResponse(decision(), None, 'next-call', {'request_attempts': 1})

    response = asyncio.run(adapter.ModelProviderAdapter(Model(), mode).request(
        messages, 2, compact_observations=True))
    assert response.success
    outbound = seen[0][-1]['content']
    assert len(outbound) < len(observation)
    assert json.loads(outbound) == json.loads(observation)
    assert '-0.000' in outbound and r'\u003csource\u003e' in outbound
    assert 'a b ' in outbound and 'not validated' in outbound
    assert seen[0][1]['content'] == user
    assert messages == before
    assert adapter.project_messages(messages, mode=mode)[-1]['content'] == observation


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('observation', ['not JSON', '{"value":1,"value":2}', '{"value":NaN}'])
def test_web_compaction_rejects_invalid_observation_without_dispatch(adapter, mode, observation):
    messages = history()
    messages[-1]['content'][0]['text'] = observation

    class Model:
        async def decide(self, *args, **kwargs):
            pytest.fail('invalid observation must not reach the model')

    response = asyncio.run(adapter.ModelProviderAdapter(Model(), mode).request(
        messages, 2, compact_observations=True))
    assert response.error.code is AgentErrorCode.INVALID_INPUT
    assert response.metadata['request_attempts'] == 0


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_compaction_does_not_omit_observations_to_fit_budget(adapter, mode):
    from src.agent.decision_transport import _budget_messages
    messages = history()
    messages[-1]['content'][0]['text'] = json.dumps({
        'source': 'synthetic contract fixture', 'values': list(range(120)),
        'warning': 'not an experimental result'})
    before = deepcopy(messages)
    compact = adapter.project_messages(messages, mode=mode, compact_observations=True)
    cost = sum(len(m.get('content') or '') + sum(len(c['function']['arguments'])
        for c in m.get('tool_calls', [])) for m in compact)
    budget = {'max_chars': cost, 'history_max_chars': 0, 'history': []}
    assert _budget_messages(compact, budget) == compact
    with pytest.raises(DecisionProtocolError, match='decision_input_budget_exceeded'):
        _budget_messages(compact, {**budget, 'max_chars': cost - 1})
    assert messages == before


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('depth', [17, 32])
def test_compaction_keeps_observation_encoder_depth_contract(adapter, mode, depth):
    from src.agent.scientific_session.decision_policy import encode_observation
    value = 0
    for _ in range(depth):
        value = {'nested': value}
    observation = encode_observation(value)
    messages = history()
    messages[-1]['content'][0]['text'] = observation
    assert adapter.project_messages(messages, mode=mode)[-1]['content'] == observation
    projected = adapter.project_messages(messages, mode=mode, compact_observations=True)
    assert json.loads(projected[-1]['content']) == value


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_compaction_keeps_observation_depth_bound(adapter, mode):
    value = 0
    for _ in range(33):
        value = {'nested': value}
    messages = history()
    messages[-1]['content'][0]['text'] = json.dumps(value)
    with pytest.raises(DecisionProtocolError, match='decision_too_deep'):
        adapter.project_messages(messages, mode=mode, compact_observations=True)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_pi_internal_entries_do_not_consume_domain_message_budget(adapter, mode):
    trusted = {'role': 'system', 'content': 'Trusted'}
    messages = []
    for _ in range(20):
        messages.extend([{'role': 'user', 'content': 'hello'},
                         {'role': 'assistant', 'content': 'hello'}])
    proposal = ClarifyDecision(version='1', action='clarify', question='Input?', missing_fields=['smiles'])
    for index in range(7):
        messages.extend([{'role': 'system', 'content': '', 'sections': {}},
                         {'role': 'user', 'content': 'SMILES: CCO'},
                         *pair(proposal, call_id=f'clarify-{index}')])
    assert len(messages) > 64
    projected = adapter.project_messages(messages, mode=mode, system_message=trusted)
    assert len(projected) == 54
    assert projected == _snapshot_messages(projected)[0]


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_projects_exact_decisions_and_observations_without_mutation(adapter, mode):
    messages = history()
    observation = json.dumps({'success': False, 'status': 'partial', 'warnings': ['missing model'],
                             'provenance': {'source': 'synthetic-fixture'},
                             'evidence': {'evidence_id': 'obs-1'},
                             'data': {'smiles': 'CCO', 'score': None}})
    messages[-1]['content'][0]['text'] = observation
    before = deepcopy(messages)
    projected = adapter.project_messages(messages, mode=mode)
    assert projected[:2] == [{'role': 'system', 'content': 'Scientific constraints.'},
                             {'role': 'user', 'content': 'SMILES: CCO'}]
    if mode == 'native':
        call = projected[2]['tool_calls'][0]
        assert call['id'] == 'call-1' and call['function']['name'] == 'agent_decision'
        assert json.loads(call['function']['arguments']) == {'decision': decision().model_dump()}
        assert projected[3] == {'role': 'tool', 'tool_call_id': 'call-1', 'content': observation}
    else:
        assert json.loads(projected[2]['content']) == {'decision': decision().model_dump()}
        assert projected[3] == {'role': 'user', 'content': observation}
    assert _snapshot_messages(projected)[0] == projected
    projected[0]['content'] = 'changed'
    assert messages == before


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_trusted_system_replacement_is_exact_and_keeps_history(adapter, mode):
    messages = history()
    messages.insert(1, {'role': 'system', 'content': 'Pi generated catalog'})
    trusted = {'role': 'system', 'content': 'Exact server constraints.\nNo invented science.'}
    result = adapter.project_messages(messages, mode=mode, system_message=trusted)
    assert result[0] == trusted and result[0] is not trusted
    assert len(result) == 4
    assert result[1:] == adapter.project_messages(history(), mode=mode)[1:]
    assert adapter.project_messages([{'role': 'user', 'content': 'hello'}],
                                    mode=mode, system_message=trusted)[0] == trusted


@pytest.mark.parametrize('proposal,name', [
    (decision(), 'property_calculator'),
    (FinishDecision(version='1', action='finish', response_kind='scientific',
                    text='Proposal only, not verified', evidence_ids=['obs-1']), 'finish_task'),
    (ClarifyDecision(version='1', action='clarify', question='Input?',
                     missing_fields=['smiles']), 'clarify_task'),
])
def test_decision_tool_call_uses_actual_name_and_full_envelope(adapter, proposal, name):
    assert adapter.decision_tool_call(proposal, 'call-1') == {
        'toolCall': {'id': 'call-1', 'name': name,
                     'arguments': {'decision': proposal.model_dump()}}}


@pytest.mark.parametrize('call_id', ['', 'bad id', 'a\n', 'x' * 129, None, 42])
def test_invalid_call_ids_are_rejected_without_echo(adapter, call_id):
    with pytest.raises(DecisionProtocolError):
        adapter.decision_tool_call(decision(), call_id)


@pytest.mark.parametrize('name', ['protocol_feedback', 'finish_task', 'clarify_task'])
def test_model_domain_proposal_cannot_impersonate_internal_or_terminal_tool(adapter, name):
    proposal = decision().model_copy(update={'tool_name': name})
    with pytest.raises(DecisionProtocolError):
        adapter.decision_tool_call(proposal, 'call-1')
    with pytest.raises(DecisionProtocolError):
        adapter.project_messages(pair(proposal), mode='native')


def test_constructed_model_is_revalidated_and_sensitive_errors_are_not_echoed(adapter):
    proposal = decision().model_copy(update={'purpose': '', 'tool_name': 'private-invalid-name'})
    with pytest.raises(DecisionProtocolError) as caught:
        adapter.decision_tool_call(proposal, 'call-1')
    assert 'private-invalid-name' not in str(caught.value)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_clarify_history_has_fixed_marker_only_after_new_user(adapter, mode):
    clarify = ClarifyDecision(version='1', action='clarify', question='private-question',
                              missing_fields=['smiles'])
    prefix = [{'role': 'user', 'content': 'Calculate properties'}]
    terminal = prefix + pair(clarify, content='private-terminal-result')
    assert adapter.project_messages(terminal, mode=mode) == prefix
    resumed = terminal + [{'role': 'user', 'content': 'SMILES: CCO'}]
    assert adapter.project_messages(resumed, mode=mode) == [*prefix,
        {'role': 'assistant', 'content': '需要用户补充完整输入；尚未完成任务。'},
        {'role': 'user', 'content': 'SMILES: CCO'}]


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_resumed_pi_system_update_does_not_replace_clarification_turn(adapter, mode):
    clarify = ClarifyDecision(version='1', action='clarify', question='Input?', missing_fields=['smiles'])
    trusted = {'role': 'system', 'content': 'Trusted'}
    messages = [{'role': 'user', 'content': 'Calculate properties'},
                *pair(clarify), official_system(adapter), {'role': 'user', 'content': 'SMILES: CCO'}]
    assert adapter.project_messages(messages, mode=mode, system_message=trusted) == [trusted,
        {'role': 'user', 'content': 'Calculate properties'},
        {'role': 'assistant', 'content': '需要用户补充完整输入；尚未完成任务。'},
        {'role': 'user', 'content': 'SMILES: CCO'}]


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_finish_preserves_scientific_proposal_without_certifying_it(adapter, mode):
    proposal = FinishDecision(version='1', action='finish', response_kind='scientific',
                              text='Not a verified scientific answer', evidence_ids=['obs-1'])
    result = adapter.project_messages(pair(proposal), mode=mode)
    raw = result[0]['tool_calls'][0]['function']['arguments'] if mode == 'native' else result[0]['content']
    assert json.loads(raw) == {'decision': proposal.model_dump()}
    assert result[1]['content'] == '{"status":"failed"}'


def safe_feedback():
    from src.agent.scientific_session.decision_policy import schema_correction
    return schema_correction(DecisionResponse(None, AgentExecutionError(
        AgentErrorCode.INVALID_OUTPUT, 'private-error', {
            'reason': 'invalid_decision_schema',
            'schema_issues': [{'path': 'decision.tool.version', 'type': 'missing'},
                              {'path': 'private-field', 'type': 'private-error'}],
        }), None, {'request_attempts': 1}))


def feedback_pair():
    return [
        {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'repair-1',
          'name': 'protocol_feedback', 'arguments': {'feedback': safe_feedback()}}]},
        {'role': 'toolResult', 'toolCallId': 'repair-1', 'toolName': 'protocol_feedback',
         'content': [{'type': 'text', 'text': 'private-raw-model-marker'}], 'isError': True},
    ]


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_internal_feedback_pair_becomes_safe_system_without_raw_model_content(adapter, mode):
    result = adapter.project_messages(history() + feedback_pair(), mode=mode, include_feedback=True)
    assert [result[0], *result[2:]] == adapter.project_messages(history(), mode=mode)
    assert result[1]['role'] == 'system'
    assert 'invalid_decision_schema' in result[1]['content']
    assert result[1]['content'] == safe_feedback()
    assert 'decision.tool.version' in result[1]['content']
    assert 'private-raw-model-marker' not in repr(result)
    assert _snapshot_messages(result)[0] == result


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('malformed', ['arbitrary', 'unsafe_issues', 'extra_issue_fields', 'trailing_text'])
def test_feedback_must_be_reconstructible_safe_schema_correction(adapter, mode, malformed):
    messages = history() + feedback_pair()
    prefix = safe_feedback().split('Safe schema issues: ')[0] + 'Safe schema issues: '
    values = {
        'arbitrary': 'private-raw-model-marker',
        'unsafe_issues': prefix + '[{"path":"private-raw-model-marker","type":"missing"}]',
        'extra_issue_fields': prefix + '[{"path":"decision","type":"missing","raw":"private-raw-model-marker"}]',
        'trailing_text': safe_feedback() + ' private-raw-model-marker',
    }
    messages[-2]['content'][0]['arguments']['feedback'] = values[malformed]
    for include in (False, True):
        with pytest.raises(DecisionProtocolError) as caught:
            adapter.project_messages(messages, mode=mode, include_feedback=include)
        assert 'private-raw-model-marker' not in str(caught.value)


def official_system(adapter):
    return {'role': 'system', 'content': '',
            'sections': {'preamble': 'generated-instruction-marker',
                         'cwd': '<cwd>\nC:/synthetic/runtime/pi\n</cwd>'},
            'timestamp': 1, 'toolsAdded': adapter.tool_definitions({'property_calculator'})}


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('seeded', [False, True])
def test_official_pi_system_and_runtime_metadata_use_exact_trusted_replacement(adapter, mode, seeded):
    memory = ([{'role': 'user', 'content': 'Previous question', 'timestamp': 1},
               {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Previous answer'}],
                'durationMs': 2, 'thinkingLevel': 'off', 'timestamp': 2}]
              if seeded else [])
    calls = pair()
    calls[0].update(durationMs=3.1, thinkingLevel='off')
    calls[1].update(durationMs=2.0, usage={'input': 1, 'output': 2}, details={'status': 'failed'})
    messages = [*memory, official_system(adapter), {'role': 'user', 'content': 'SMILES: CCO'}, *calls]
    trusted = {'role': 'system', 'content': 'Exact trusted constraints'}
    result = adapter.project_messages(messages, mode=mode, system_message=trusted)
    expected = [trusted]
    if seeded:
        expected.extend([{'role': 'user', 'content': 'Previous question'},
                         {'role': 'assistant', 'content': 'Previous answer'}])
    expected.extend(adapter.project_messages(history(), mode=mode)[1:])
    assert result == expected
    assert 'generated-instruction-marker' not in repr(result) and '<cwd>' not in repr(result)
    with pytest.raises(DecisionProtocolError):
        adapter.project_messages(messages, mode=mode)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_official_pi_first_request_reaches_model_once(adapter, mode):
    class Model:
        calls = 0

        async def decide(self, messages, **kwargs):
            self.calls += 1
            assert messages == [{'role': 'system', 'content': 'Trusted'},
                                {'role': 'user', 'content': 'Hello'}]
            return DecisionResponse(decision(), None, 'call-1', {'request_attempts': 1})

    model = Model()
    response = asyncio.run(adapter.ModelProviderAdapter(model, mode).request(
        [official_system(adapter), {'role': 'user', 'content': 'Hello', 'timestamp': 2}],
        2, system_message={'role': 'system', 'content': 'Trusted'}))
    assert response.success and model.calls == 1


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_feedback_is_excluded_from_audit_by_default_but_still_validated(adapter, mode):
    messages = history() + feedback_pair()
    assert adapter.project_messages(messages, mode=mode) == adapter.project_messages(history(), mode=mode)
    messages[-1]['toolCallId'] = 'wrong-id'
    with pytest.raises(DecisionProtocolError):
        adapter.project_messages(messages, mode=mode)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_request_replaces_system_and_includes_safe_feedback_in_prefix(adapter, mode):
    trusted = {'role': 'system', 'content': 'Exact trusted scientific constraints'}
    messages = history() + feedback_pair()
    response = DecisionResponse(decision(), None, 'call-2', {'request_attempts': 1})

    class Model:
        def __init__(self):
            self.calls = []

        async def decide(self, projected, **kwargs):
            self.calls.append((projected, kwargs))
            return response

    model = Model()
    result = asyncio.run(adapter.ModelProviderAdapter(model, mode).request(
        messages, timeout_seconds=3, system_message=trusted))
    assert result is response and len(model.calls) == 1
    projected, options = model.calls[0]
    assert projected[0] == trusted
    assert projected[1]['role'] == 'system' and 'invalid_decision_schema' in projected[1]['content']
    assert projected[2:] == adapter.project_messages(history(), mode=mode)[1:]
    assert 'private-raw-model-marker' not in repr(projected)
    assert options == {'mode': mode, 'timeout_seconds': 3}
    assert projected == adapter.project_messages(messages, mode=mode,
                                                 system_message=trusted, include_feedback=True)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_plain_assistant_memory_and_text_only_blocks_are_preserved(adapter, mode):
    messages = [{'role': 'user', 'content': 'Previous question'},
                {'role': 'assistant', 'content': 'Previous answer'},
                {'role': 'user', 'content': 'Next question'},
                {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Answer '},
                                                 {'type': 'text', 'text': 'continued'}]}]
    assert adapter.project_messages(messages, mode=mode) == [*messages[:3],
        {'role': 'assistant', 'content': 'Answer continued'}]


@pytest.mark.parametrize('flag', [None, 0, 'true', {}])
def test_feedback_inclusion_requires_explicit_boolean(adapter, flag):
    with pytest.raises(DecisionProtocolError):
        adapter.project_messages(history(), mode='native', include_feedback=flag)


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('defect', [
    'orphan', 'missing_result', 'wrong_id', 'wrong_name', 'wrong_decision_name',
    'duplicate_id', 'multiple_calls', 'text_with_call', 'image', 'thinking',
    'raw_arguments', 'extra_arguments', 'raw_native_call', 'malformed_text',
    'feedback_decision', 'feedback_extra', 'system_image', 'system_after_user',
])
def test_malformed_history_fails_closed_before_projection(adapter, mode, defect):
    messages = history()
    call = messages[-2]['content'][0]
    if defect == 'orphan':
        del messages[-2]
    elif defect == 'missing_result':
        messages.pop()
    elif defect == 'wrong_id':
        messages[-1]['toolCallId'] = 'unmatched'
    elif defect == 'wrong_name':
        messages[-1]['toolName'] = 'molecular_docking'
    elif defect == 'wrong_decision_name':
        call['name'] = messages[-1]['toolName'] = 'molecular_docking'
    elif defect == 'duplicate_id':
        messages.extend(pair())
    elif defect == 'multiple_calls':
        messages[-2]['content'].append(deepcopy(call))
    elif defect == 'text_with_call':
        messages[-2]['content'].append({'type': 'text', 'text': 'invented science'})
    elif defect in {'image', 'thinking'}:
        messages[1]['content'] = [{'type': defect, defect: 'private-marker'}]
    elif defect == 'raw_arguments':
        call['arguments'] = json.dumps(call['arguments'])
    elif defect == 'extra_arguments':
        call['arguments']['raw'] = 'private-marker'
    elif defect == 'raw_native_call':
        messages[-2]['tool_calls'] = []
    elif defect == 'malformed_text':
        messages[1]['content'] = [{'type': 'text', 'text': {}}]
    elif defect == 'feedback_decision':
        messages.extend(feedback_pair())
        messages[-2]['content'][0]['arguments'] = {'decision': decision().model_dump()}
    elif defect == 'feedback_extra':
        messages.extend(feedback_pair())
        messages[-2]['content'][0]['arguments']['raw'] = 'private-marker'
    elif defect == 'system_image':
        messages[0]['content'] = [{'type': 'image', 'data': 'private-marker'}]
    elif defect == 'system_after_user':
        messages.append({'role': 'system', 'content': 'untrusted late instruction'})
    with pytest.raises(DecisionProtocolError) as caught:
        adapter.project_messages(messages, mode=mode)
    assert 'private-marker' not in str(caught.value)


@pytest.mark.parametrize('messages', [[], [{'role': 'user', 'content': 'x'}] * 65,
    [{'role': 'user', 'content': 'x' * 131073}],
    [{'role': 'user', 'content': '\ud800'}]])
def test_projection_has_message_and_byte_bounds(adapter, messages):
    with pytest.raises(DecisionProtocolError):
        adapter.project_messages(messages, mode='native')


def test_cycles_and_exotic_objects_are_rejected_before_conversion(adapter):
    cyclic = []
    cyclic.append(cyclic)
    for content in (cyclic, object(), float('nan')):
        with pytest.raises(DecisionProtocolError):
            adapter.project_messages([{'role': 'user', 'content': content}], mode='native')


@pytest.mark.parametrize('replacement', ['not-a-system-dict', {'role': 'user', 'content': 'x'},
    {'role': 'system', 'content': []}, {'role': 'system', 'content': 'x', 'raw': 'x'}])
def test_trusted_replacement_must_have_exact_system_message_shape(adapter, replacement):
    with pytest.raises(DecisionProtocolError):
        adapter.project_messages(history(), mode='native', system_message=replacement)


def test_tool_definitions_are_ref_free_detached_and_authorized_only(adapter):
    names = {'property_calculator', 'molecular_docking'}
    definitions = adapter.tool_definitions(names)
    assert [item['name'] for item in definitions] == [
        'molecular_docking', 'property_calculator', 'finish_task', 'clarify_task', 'protocol_feedback']
    assert '$ref' not in json.dumps(definitions)
    for item in definitions[:-1]:
        assert item['parameters'] == {'type': 'object', 'properties': {'decision': {'type': 'object'}},
                                      'required': ['decision'], 'additionalProperties': False}
    assert definitions[-1]['parameters'] == {
        'type': 'object', 'properties': {'feedback': {'type': 'string'}},
        'required': ['feedback'], 'additionalProperties': False}
    definitions[0]['parameters']['properties'].clear()
    assert adapter.tool_definitions(names)[0]['parameters']['properties']
    assert names == {'property_calculator', 'molecular_docking'}


@pytest.mark.parametrize('names', ['property_calculator', {'bad-name'}, {'protocol_feedback'},
                                  {'finish_task'}, {None}, ['x'] * 129])
def test_invalid_tool_catalog_is_rejected(adapter, names):
    with pytest.raises(DecisionProtocolError):
        adapter.tool_definitions(names)


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('success', [True, False])
def test_request_is_exactly_one_existing_decide_transaction(adapter, mode, success):
    response = DecisionResponse(decision() if success else None,
        None if success else AgentExecutionError(AgentErrorCode.INVALID_OUTPUT, 'Rejected',
                                                {'reason': 'invalid_decision_schema'}),
        'call-2' if mode == 'native' and success else None, {'request_attempts': 1})

    class Model:
        def __init__(self):
            self.calls = []

        async def decide(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return response

    model = Model()
    result = asyncio.run(adapter.ModelProviderAdapter(model, mode).request(history(), 4.5))
    assert result is response
    assert model.calls == [(adapter.project_messages(history(), mode=mode),
                            {'mode': mode, 'timeout_seconds': 4.5})]


def test_request_preserves_cancellation_without_retry(adapter):
    class Model:
        calls = 0

        async def decide(self, messages, **kwargs):
            self.calls += 1
            raise asyncio.CancelledError()

    model = Model()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(adapter.ModelProviderAdapter(model, 'native').request(history(), 1))
    assert model.calls == 1


def test_request_rejects_model_proposed_internal_feedback(adapter):
    class Model:
        async def decide(self, messages, **kwargs):
            return DecisionResponse(decision().model_copy(update={'tool_name': 'protocol_feedback'}),
                                    None, 'call-2', {'request_attempts': 1})

    with pytest.raises(DecisionProtocolError):
        asyncio.run(adapter.ModelProviderAdapter(Model(), 'native').request(history(), 1))


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_feedback_expanded_input_limit_is_classified_without_model_dispatch(adapter, mode):
    messages = [{'role': 'system', 'content': 'Trusted'},
                *[{'role': 'user', 'content': f'bounded-{i}'} for i in range(63)],
                *feedback_pair()]
    assert len(adapter.project_messages(messages, mode=mode)) == 64
    class Model:
        async def decide(self, *args, **kwargs):
            pytest.fail('Oversized projection must not reach the model')
    response = asyncio.run(adapter.ModelProviderAdapter(Model(), mode).request(messages, 1))
    assert not response.success
    assert response.error.code == AgentErrorCode.INVALID_INPUT
    assert response.metadata['request_attempts'] == 0


@pytest.mark.parametrize('mode', ['auto', None, {}, 1])
def test_invalid_mode_fails_closed(adapter, mode):
    with pytest.raises(DecisionProtocolError):
        adapter.project_messages(history(), mode=mode)
    with pytest.raises(DecisionProtocolError):
        adapter.ModelProviderAdapter(object(), mode)
