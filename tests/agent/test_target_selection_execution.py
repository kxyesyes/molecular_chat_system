"""Offline execution guards with controlled services, not scientific acceptance."""

import pytest

from src.agent.contracts import AgentContext
from src.agent.runtime.workflow_executor import (
    PreparedWorkflow,
    WorkflowExecution,
    WorkflowExecutor,
)
from src.agent.tools.target_database_tool import TargetDatabaseTool
from src.agent.workflows import WorkflowCatalog


class ForbiddenTool:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        raise AssertionError('No tool execution without validated target evidence')


@pytest.mark.parametrize('query', [
    'Design 2 molecules for PDE5A and select top 3',
    'Design 2 molecules for PDE5A, select top 3',
    'Design 2 molecules for PDE5A and select top 3\n\t',
])
def test_selection_prepares_with_target_guard_and_generated_candidate_bindings(query):
    policy = WorkflowCatalog().require('target_driven_design')
    tools = {name: ForbiddenTool(name) for name in policy.allowed_tools}
    prepared = WorkflowExecutor().prepare(
        context=AgentContext(
            query=query,
            active_skill=policy.name, trace_id='selection-prepare',
        ),
        policy=policy, all_tools=tools,
    )

    assert isinstance(prepared, PreparedWorkflow)
    plan = prepared.compiled.plan
    assert plan.metadata['target_hint'] == 'PDE5A'
    assert plan.metadata['requested_count'] == 2
    assert plan.metadata['docking_top_n'] == 3
    steps = {step.tool_name: step for step in plan.steps}
    target = steps['target_database_search']
    generator = steps['llm_molecular_generator']
    assert target.input_data == 'PDE5A'
    assert generator.preconditions == ('target_evidence',)
    assert generator.input_binding == '$.workflow'
    assert generator.input_data['metadata']['requested_count'] == 2
    assert generator.output_key == 'molecules'
    assert target.name in prepared.compiled.dependencies[generator.name]
    for name in ('property_calculator', 'admet_predictor', 'activity_predictor'):
        step = steps[name]
        assert step.input_from == generator.output_key
        assert step.input_binding == '$.outputs.molecules'
        assert step.input_transform == 'smiles_text'
        assert generator.name in prepared.compiled.dependencies[step.name]
    ranker = steps['candidate_ranker']
    assert ranker.input_binding == '$.workflow'
    assert ranker.metadata['workflow_output_keys'] == (
        'molecules', 'properties', 'admet', 'activity',
    )
    assert generator.name in prepared.compiled.dependencies[ranker.name]
    assert all(tool.calls == [] for tool in tools.values())


@pytest.mark.parametrize('target_status', ['not_found', 'unavailable'])
def test_selection_without_target_evidence_blocks_all_scientific_tools(target_status):
    class Service:
        def __init__(self):
            self.calls = []

        def search_targets(self, query):
            self.calls.append(query)
            return {
                'status': target_status, 'results': [], 'evidence': [],
                'warnings': ['controlled service: no target evidence'],
            }

    policy = WorkflowCatalog().require('target_driven_design')
    forbidden = {name: ForbiddenTool(name) for name in policy.allowed_tools
                 if name != 'target_database_search'}
    service = Service()
    target = TargetDatabaseTool()
    target._service = service
    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query='Design 2 molecules for PDE5A and select top 3',
            active_skill=policy.name, trace_id='selection-no-evidence',
        ),
        policy=policy, all_tools={**forbidden, target.name: target},
    )

    assert service.calls == ['PDE5A']
    assert not execution.result.success
    assert execution.result.error is not None
    assert execution.result.error.code.value != 'invalid_input'
    assert [item.tool_name for item in execution.result.tool_results] == [target.name]
    assert all(tool.calls == [] for tool in forbidden.values())


@pytest.mark.parametrize('query', [
    'Design 2 molecules for PDE5A and select XYZ999',
    'Design 2 molecules for PDE5A and EGFR and select top 3',
    'Design 2 molecules not against PDE5A and select top 3',
])
def test_forced_design_selection_cannot_bypass_target_clarification(query):
    for skill in ('target_driven_design', 'molecular_design', 'hit_to_lead_optimization'):
        policy = WorkflowCatalog().require(skill)
        tools = {name: ForbiddenTool(name) for name in policy.allowed_tools}
        context = AgentContext(query=query, active_skill=skill, trace_id='selection-invalid')
        executor = WorkflowExecutor()
        for entrypoint in (executor.prepare, executor.execute):
            execution = entrypoint(context=context, policy=policy, all_tools=tools)
            assert isinstance(execution, WorkflowExecution)
            assert not execution.result.success
            assert execution.result.error.code.value == 'invalid_input'
            assert execution.result.error.details['reason'] == 'target_clarification_required'
            assert execution.plan.steps == []
            assert execution.tool_attempt_count == 0
            assert execution.result.tool_results == []
            assert all(tool.calls == [] for tool in tools.values())
