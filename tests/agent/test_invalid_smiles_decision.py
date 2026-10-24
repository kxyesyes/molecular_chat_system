"""Prompt and wire regressions; provider fixtures are not real LLM acceptance."""
import asyncio
import json

import httpx
import pytest

from src.agent.contracts import AgentContext, RunOutcome
from src.agent.contracts.decision import DecisionProtocolError, parse_decision_json
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.scientific_session.binding_policy import WEB_PROFILE
from src.agent.scientific_session.decision_policy import decision_system_message
from test_decision_loop import setup_loop, tool, clarify, finish, finish_last, ScriptedModel


@pytest.mark.parametrize('profile', [None, B1_PROFILE_REVISION, WEB_PROFILE])
def test_scientific_failure_guidance_does_not_consume_ordinary_chat_budget(profile):
    text = decision_system_message('chat', set(), [], {}, binding_profile=profile)['content']
    assert 'Let authorized tools validate supplied molecular input' not in text
    assert 'If required input is missing, choose clarify' not in text
    assert 'Use scientific finish with observed evidence IDs for scientific requests; ' in text
    assert 'Use chat finish only for chat requests. Request kind: chat.' in text


@pytest.mark.parametrize('profile', [None, B1_PROFILE_REVISION, WEB_PROFILE])
def test_initial_prompt_explains_failure_and_evidence_before_schema_repair(profile):
    text = decision_system_message('scientific', {'property_calculator'}, [], {},
                                   binding_profile=profile)['content']
    assert 'scientific evidence_ids must be nonempty, unique IDs' in text
    assert 'If required input is missing, choose clarify' in text
    assert 'Do not reject supplied SMILES based on your own assessment' in text
    assert 'If a tool rejects the input and leaves no usable or review-only evidence, choose clarify' in text
    assert 'Let authorized tools validate supplied molecular input' in text
    assert 'Use chat finish only for chat requests' in text
    assert 'text must be nonempty' in text


@pytest.mark.parametrize('profile', [None, B1_PROFILE_REVISION, WEB_PROFILE])
def test_prompt_preserves_server_authorized_review_without_claiming_success(profile):
    text = decision_system_message('scientific', {'activity_predictor'}, [], {},
                                   binding_profile=profile)['content']
    assert 'server-authorized family-disagreement review observations' in text
    assert 'review-only completion, never scientific success' in text


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_invalid_rdkit_observation_clarifies_without_protocol_repair(setup_loop, mode):
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    from src.agent.tools.property_calculator import PropertyCalculator

    bundle = setup_loop([], [PropertyCalculator()], mode=mode)
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        decision = tool() if len(calls) == 1 else clarify()
        assert len(calls) <= 2, 'clarification must not require schema repair'
        if mode == 'native':
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': f'call-{len(calls)}', 'type': 'function', 'function': {
                    'name': 'agent_decision',
                    'arguments': json.dumps({'decision': decision.model_dump()})}}]}
            reason = 'tool_calls'
        else:
            message = {'role': 'assistant', 'content': json.dumps({'decision': decision.model_dump()})}
            reason = 'stop'
        return httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': message}]})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            bundle.loop.model = OpenAICompatibleModel('fake-test-key', 'protocol-test',
                                                      'https://example.invalid', client=client)
            return await bundle.loop.run(AgentContext('SMILES: CC(C)((', 'invalid-wire'),
                request_kind='scientific', allowed_tools={'property_calculator'},
                required_tools={'property_calculator'}, event_bus=bundle.bus)

    result = asyncio.run(exercise())
    assert len(calls) == 2
    initial = '\n'.join(m['content'] for m in calls[0]['messages'] if m['role'] == 'system')
    assert 'If a tool rejects the input and leaves no usable or review-only evidence, choose clarify' in initial
    assert not result.success and result.outcome == RunOutcome.REJECTED
    assert result.metadata['stop_reason'] == 'clarification_required'
    assert result.metadata['protocol_repairs'] == 0
    assert all(call['success'] for call in result.metadata['model_calls'])
    assert len(result.tool_results) == 1
    observation = result.tool_results[0]
    assert not observation.success and not observation.data and not observation.artifacts
    assert observation.error.message == 'SMILES 无效或缺失；请提供完整结构，使用换行或分号分隔，不会提取片段替代。'
    assert not result.artifacts


def test_reproduced_empty_scientific_finish_remains_invalid():
    raw = json.dumps({'decision': {'version': '1', 'action': 'finish',
        'response_kind': 'scientific', 'text': 'Input was invalid.', 'evidence_ids': []}})
    with pytest.raises(DecisionProtocolError) as caught:
        parse_decision_json(raw)
    assert caught.value.code == 'invalid_decision_schema'
    assert caught.value.schema_issues == [{'path': 'decision.finish', 'type': 'value_error'}]


def test_repaired_schema_failure_still_disqualifies_invalid_input_acceptance():
    from scripts.run_decision_chat_acceptance import run_acceptance
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.decision_transport import DecisionResponse

    class SchemaFailureModel(ScriptedModel):
        injected = False

        async def decide(self, messages, **kwargs):
            if len(self.messages) == 7 and not self.injected:
                self.injected = True
                return DecisionResponse(None, AgentExecutionError(AgentErrorCode.INVALID_OUTPUT,
                    'Decision rejected', {'reason': 'invalid_decision_schema',
                    'schema_issues': [{'path': 'decision.finish', 'type': 'value_error'}]}), None, {})
            return await super().decide(messages, **kwargs)

    model = SchemaFailureModel([finish(kind='chat'), tool(), finish_last,
                                clarify(), tool(), finish_last, tool(), clarify()])
    report = asyncio.run(run_acceptance(model))
    case = report['cases'][-1]
    assert model.injected
    assert case['stop_reason'] == 'clarification_required'
    assert case['status'] == 'failed'
    assert case['checks']['expected_rejection'] is False
    assert case['checks']['no_fabricated_properties'] is True
    assert [call['success'] for call in case['model_calls']] == [True, False, True]
