"""T02 outcome preservation composed with T04 default ownership and aliases."""
from copy import deepcopy

import pytest

from src.agent.contracts import ToolResult, ObservationStatus, WorkflowArtifact
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.agent.tooling import build_tool_registry


@pytest.mark.parametrize('legacy_name', [False, True])
@pytest.mark.parametrize('entry', ['execute', 'run'])
@pytest.mark.parametrize('partial', [False, True])
def test_default_rag_owner_keeps_outcome_and_evidence_across_entrypoints(
    tmp_path, legacy_name, entry, partial,
):
    observation = ToolResult(
        'rag_search', True, 'synthetic retrieval',
        data=[{'SMILES': 'CCO', 'source': 'synthetic-only',
               'source_index': 0, 'similarity_score': 0.75,
               'provenance': {
                   'source_path': 'synthetic.csv', 'source_sha256': 'a' * 64,
                   'index_sha256': 'b' * 64, 'embedding_model': 'offline-test',
                   'manifest_schema_version': 2, 'builder_version': '1',
                   'vector_label': 0,
               }}],
        status=ObservationStatus.PARTIAL if partial else ObservationStatus.SUCCEEDED,
        warnings=['incomplete coverage'] if partial else [],
        evidence=[{'source': 'synthetic-only'}],
        artifacts=[WorkflowArtifact('report', 'fixture.txt', 'fixture')],
    )

    class Tool:
        name = 'rag_database_search' if legacy_name else 'rag_search'
        calls = 0

        def execute(self, query):
            self.calls += 1
            return deepcopy(observation)

    tool = Tool()
    registry = build_tool_registry([tool])
    store = SQLiteAgentStateStore(tmp_path / 'state.sqlite')
    supervisor = SupervisorAgent(
        tools={tool.name: tool}, tool_registry=registry,
        specialists=build_default_specialists(), state_store=store,
    )
    try:
        assert registry.resolve('rag_search') is registry.resolve('rag_database_search')
        if entry == 'execute':
            response = supervisor.execute('检索知识库中的乙醇', active_skill='rag_search')
            result = response['agent_result'].to_legacy_dict()
        else:
            response = supervisor.run('检索知识库中的乙醇', skill_name='rag_search')
            result = response['result']
            assert response['delegations'][0]['agent_name'] == 'rag'
        assert tool.calls == 1
        assert result['status'] == ('partial' if partial else 'completed')
        assert result['warnings'] == observation.warnings
        assert result['evidence'] == observation.evidence
        assert result['artifacts'] == [item.to_dict() for item in observation.artifacts]
        assert result['tool_result_sequence'][0]['data'] == observation.data
        assert store.get_run(response['trace_id'])['status'] == ('partial' if partial else 'succeeded')
        execution = store.get_tool_executions(response['trace_id'])
        assert len(execution) == 1
        assert execution[0]['status'] == ('partial' if partial else 'succeeded')
    finally:
        registry.close()


def test_health_telemetry_does_not_raise_or_publish_invalid_raw_status():
    observation = ToolResult('property_calculator', True, 'fixture')
    observation.status = 'synthetic-private-provider-text'

    class Tool:
        name = 'property_calculator'
        def execute(self, query):
            return observation

    registry = build_tool_registry([Tool()])
    try:
        adapter = registry.resolve('property_calculator')
        result = adapter.execute({'query': 'CCO'})
        assert result is observation  # downstream validation still sees raw input
        health = adapter.health()
        assert health['last_execution_status'] == 'failed'
        assert health['last_error_code'] == 'invalid_output'
        assert 'synthetic-private-provider-text' not in str(health)
    finally:
        registry.close()


def test_request_local_alias_view_preserves_capability_denial_and_shared_mapping():
    from src.agent.contracts import AgentContext

    tool = object()
    supervisor = SupervisorAgent(tools={'rag_database_search': tool})
    enabled = AgentContext('query', 'trace-enabled')
    disabled = AgentContext('query', 'trace-disabled', metadata={'capabilities': {'rag': False}})
    assert supervisor._request_tools(enabled)['rag_search'] is tool
    assert supervisor._request_tools(disabled) == {}
    assert supervisor.tools == {'rag_database_search': tool}


def test_explicit_canonical_raw_tool_keeps_existing_precedence():
    from src.agent.contracts import AgentContext

    canonical, legacy = object(), object()
    supervisor = SupervisorAgent(tools={'rag_database_search': legacy, 'rag_search': canonical})
    tools = supervisor._request_tools(AgentContext('query', 'trace-both'))
    assert tools['rag_search'] is canonical
    assert tools['rag_database_search'] is legacy
