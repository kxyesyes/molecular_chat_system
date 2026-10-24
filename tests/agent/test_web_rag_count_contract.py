"""Request-local retrieval count, real receipt checks and no shared mutation."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentErrorCode
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.rag_search_tool import RAGSearchTool
from test_rag_tool_contract import InjectedService


def test_structured_rag_count_survives_adapter_and_current_source_checks():
    service = InjectedService()
    tool = RAGSearchTool(service)
    registry = build_tool_registry([tool])
    request = {'query': 'synthetic retrieval query', 'k': 5}
    try:
        result = registry.resolve('rag_search').execute({'query': request})
        assert result.success, result.to_legacy_dict()
        assert service.calls == [(request['query'], 5)]
        source = tool.validate_current_observation(result.data, result.evidence, input_data=request)
        assert source['kind'] == 'rag'
        with pytest.raises(ValueError):
            tool.validate_current_observation(result.data, result.evidence,
                                              input_data={**request, 'k': 3})
        default = registry.resolve('rag_search').execute('next synthetic query')
        assert default.success
        assert service.calls[-1] == ('next synthetic query', 3)
    finally:
        registry.close()


@pytest.mark.parametrize('payload', [
    {'query': 'q', 'k': True}, {'query': 'q', 'k': 0}, {'query': 'q', 'k': 51},
    {'query': 'q', 'k': '5'}, {'query': 'q'}, {'k': 5},
    {'query': 'q', 'k': 5, 'url': 'https://untrusted.invalid'},
])
def test_malformed_structured_count_is_rejected_before_retrieval(payload):
    service = InjectedService()
    registry = build_tool_registry([RAGSearchTool(service)])
    try:
        result = registry.resolve('rag_search').execute({'query': deepcopy(payload)})
        assert result.error.code is AgentErrorCode.INVALID_INPUT
        assert service.calls == []
    finally:
        registry.close()
