"""Safe protocol diagnostics contain schema coordinates, never provider values."""
import asyncio
import json

import httpx
import pytest

from src.agent.contracts.decision import DecisionProtocolError, parse_decision_json
from src.agent.openai_compatible_model import OpenAICompatibleModel


def invalid_tool():
    return {'decision': {'action': 'tool', 'tool_name': 'property_calculator',
                         'arguments': {'input_ref': 'user'}, 'purpose': 'compute'}}


def test_schema_error_identifies_missing_version_without_input():
    with pytest.raises(DecisionProtocolError) as caught:
        parse_decision_json(json.dumps(invalid_tool()))
    assert caught.value.code == 'invalid_decision_schema'
    assert caught.value.schema_issues == [{'path': 'decision.tool.version', 'type': 'missing'}]
    assert str(caught.value) == 'invalid_decision_schema'


def test_diagnostics_mask_unknown_field_names_values_and_validation_context():
    payload = invalid_tool()
    payload['decision'].update(version='private-value-marker')
    payload['decision']['private-key-marker'] = 'private-content-marker'
    with pytest.raises(DecisionProtocolError) as caught:
        parse_decision_json(json.dumps(payload))
    issues = caught.value.schema_issues
    assert {'path': 'decision.tool.version', 'type': 'literal_error'} in issues
    assert {'path': 'decision.tool.*', 'type': 'extra_forbidden'} in issues
    assert 'private-' not in repr(issues)


def test_transport_retains_only_safe_schema_issues():
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(invalid_tool()),
                'reasoning_content': 'private-reasoning-marker'}}]}))) as client:
            model = OpenAICompatibleModel('test-key', 'test-model', 'https://example.invalid/v1', client=client)
            response = await model.decide([{'role': 'user', 'content': 'test'}], mode='json')
            assert not response.success
            assert response.error.details['schema_issues'] == [
                {'path': 'decision.tool.version', 'type': 'missing'}]
            assert response.metadata['request_attempts'] == 1
            assert 'private-' not in repr(response)
    asyncio.run(execute())
