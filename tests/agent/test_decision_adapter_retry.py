"""Request-local retry override; default adapter semantics remain unchanged."""
from dataclasses import replace

import pytest

from test_decision_loop import CountingTool
from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.tooling.factory import build_tool_registry
from src.agent.tooling.spec import RetryPolicy


@pytest.fixture
def adapter():
    tool = CountingTool()
    def execute(query):
        tool.inputs.append(query)
        return ToolResult.error_result(tool.name, AgentErrorCode.PROVIDER_ERROR, 'synthetic')
    tool.execute = execute
    registry = build_tool_registry([tool])
    value = registry.resolve(tool.name)
    value.spec = replace(value.spec, retry_policy=RetryPolicy(
        max_attempts=3, retryable_error_codes={'provider_error'}))
    yield value, tool
    registry.close()


@pytest.mark.parametrize('options,count', [({}, 3), ({'allow_retry': True}, 3),
                                          ({'allow_retry': False}, 1)])
def test_retry_override_preserves_default_and_one_attempt(adapter, options, count):
    value, tool = adapter
    result = value.execute({'query': 'CCO'}, **options)
    assert not result.success
    assert len(tool.inputs) == count
    assert value.spec.retry_policy.max_attempts == 3


@pytest.mark.parametrize('invalid', [None, 0, 1, '', 'false', [], {}])
def test_retry_override_rejects_non_bool_before_dispatch(adapter, invalid):
    value, tool = adapter
    with pytest.raises(TypeError, match='allow_retry must be a bool'):
        value.execute({'query': 'CCO'}, allow_retry=invalid)
    assert not tool.inputs
