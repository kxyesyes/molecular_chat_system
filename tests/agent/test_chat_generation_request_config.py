"""Request settings through actual Pi/Web and the offline HTTP model boundary."""
import asyncio

import pytest

from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('repair', [False, True])
def test_pi_generation_and_schema_repair_preserve_request_settings(actual_app, stream, repair):
    attempts = []
    async def respond(payload):
        attempts.append(payload)
        if repair and len(attempts) == 1:
            return {'version':'1', 'action':'not-a-decision'}
        return chat_decision('offline request-settings response')

    async def run():
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            bundle.app.chat_handler.config = {'inference': {
                'stream':stream, 'max_tokens':600, 'external_max_tokens':600}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message':'你好', 'enable_tools':False,
                                            'enable_rag':False, 'temperature':0.23})
                assert len(attempts) == (2 if repair else 1)
                assert all(payload['temperature'] == 0.23 for payload in attempts)
                assert all(payload['max_tokens'] == 600 for payload in attempts)
                complete = [frame for frame in frames if frame['type'] == 'complete']
                assert len(complete) == 1 and complete[0]['status'] == 'completed'
                assert complete[0]['content'] == 'offline request-settings response'
                assert not any(frame['type'] in {'message', 'stream'} for frame in frames)
                assert bundle.app.agent_state_store.get_tool_executions(complete[0]['trace_id']) == []
    asyncio.run(run())


@pytest.mark.parametrize('value', [True, None, '600', -1, 0, 8193])
def test_preparation_rejects_invalid_model_budget_before_compilation(value):
    from src.web.web_request import prepare_web_request
    from src.web.decision_request import DecisionAdmissionError
    with pytest.raises(DecisionAdmissionError, match='invalid_decision_model_options'):
        prepare_web_request({'message':'你好'}, session_id='owner', trace_id='options',
                            max_output_tokens=value)


@pytest.mark.parametrize('value', [True, None, '0.7', -0.01, 2.01, float('nan'), float('inf')])
def test_model_transport_rejects_invalid_temperature_before_http(value):
    import httpx
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    calls = []
    def forbidden(request):
        calls.append(request)
        raise AssertionError('invalid temperature dispatched')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            model = OpenAICompatibleModel('synthetic-key', 'offline', 'https://example.invalid/v1', client=client)
            result = await model.decide([{'role':'user', 'content':'hello'}], temperature=value)
            assert not result.success and result.metadata['request_attempts'] == 0
            assert result.error.details['reason'] == 'invalid_decision_options'
    asyncio.run(run())
    assert not calls
