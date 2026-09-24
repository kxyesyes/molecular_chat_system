"""Pure selection/parsing boundaries; existing snapshots cover complete plans."""
from importlib import import_module

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.generation_request import GenerationRequestError
from src.agent.planning import WorkflowPlan
from src.agent.planning.task_planner import TaskPlanner, WorkflowPlan as FacadePlan


@pytest.mark.parametrize('skill,design,expected,calls', [
    ('admet_assessment', True, 'admet_assessment', 0),
    ('comprehensive_evaluation', True, 'comprehensive_evaluation', 0),
    ('comprehensive_evaluation_skill', True, 'comprehensive_evaluation', 0),
    ('target_driven_design', False, 'target_driven_design', 0),
    ('target_database_search', True, 'target_driven_design', 1),
    ('target_database_search', False, 'target_database_search', 1),
    ('hit_to_lead_optimization', True, 'hit_to_lead_optimization', 0),
    ('molecular_design', False, 'molecular_design', 0),
    ('docking_simulation', True, 'docking_simulation', 0),
    ('activity_prediction', True, 'activity_prediction', 0),
    ('reverse_target_prediction', True, 'reverse_target_prediction', 0),
    ('rag_search', True, 'rag_search', 0),
    ('unknown', True, None, 0),
    ('', True, None, 0),
])
def test_selection_precedence_and_lazy_predicate(skill, design, expected, calls):
    module = import_module('src.agent.planning.workflow_selection')
    observed = []

    def predicate(query):
        observed.append(query)
        return design

    assert module.select_workflow(skill, 'query', looks_like_design=predicate) == expected
    assert observed == ['query'] * calls


def test_facade_uses_selection_and_keeps_plan_identity(monkeypatch):
    module = import_module('src.agent.planning.workflow_selection')
    monkeypatch.setattr(module, 'select_workflow', lambda *args, **kwargs: 'admet_assessment')
    result = TaskPlanner().plan(AgentContext(query='CCO', trace_id='select', active_skill='unknown'))
    assert result.workflow_name == 'admet_assessment'
    assert type(result) is WorkflowPlan is FacadePlan


@pytest.mark.parametrize('metadata,expected', [({}, 3), ({'requested_count': 2}, 2)])
def test_count_metadata_precedence_and_callback(metadata, expected):
    module = import_module('src.agent.planning.request_parsing')
    calls = []

    def extract(query, *, default):
        calls.append((query, default))
        return 3

    assert module.requested_count('query', metadata, default=7, extract_count=extract) == expected
    assert calls == ([] if metadata else [('query', 7)])


@pytest.mark.parametrize('value', [None, False, 0, '2', -1, 101])
def test_invalid_present_metadata_does_not_fall_back(value):
    module = import_module('src.agent.planning.request_parsing')

    def forbidden(*args, **kwargs):
        pytest.fail('Metadata presence must not fall back to prompt parsing')

    with pytest.raises(GenerationRequestError):
        module.requested_count('Generate 2 molecules', {'requested_count': value},
                               default=7, extract_count=forbidden)


@pytest.mark.parametrize('helper,function,args', [
    ('_looks_like_design', 'looks_like_design', ('query',)),
    ('_extract_target_hint', 'extract_target_hint', ('query',)),
    ('_looks_like_unauthorized_tool_request', 'looks_like_unauthorized_tool_request', ('query',)),
    ('_extract_requested_count', 'extract_requested_count', ('query', 3)),
    ('_extract_top_n', 'extract_top_n', ('query', 3)),
    ('_wants_admet', 'wants_admet', ('query',)),
])
def test_legacy_helpers_delegate_without_new_state(monkeypatch, helper, function, args):
    module = import_module('src.agent.planning.request_parsing')
    sentinel = object()
    seen = []

    def replacement(*values, **kwargs):
        seen.append((values, kwargs))
        return sentinel

    monkeypatch.setattr(module, function, replacement)
    assert getattr(TaskPlanner(), helper)(*args) is sentinel
    assert len(seen) == 1
    assert seen[0][0][0] == 'query'


@pytest.mark.parametrize('skill,query', [
    ('molecular_design', '生成候选分子'),
    ('target_driven_design', '针对 PDE5A 设计候选分子'),
    ('hit_to_lead_optimization', '优化这个分子 SMILES: CCO'),
])
def test_count_extraction_override_survives_metadata_helper(skill, query):
    class CustomPlanner(TaskPlanner):
        @classmethod
        def _extract_requested_count(cls, query, default):
            return 7

    plan = CustomPlanner().plan(AgentContext(query=query, trace_id='override', active_skill=skill))
    assert plan.metadata['requested_count'] == 7


def test_unknown_skill_name_and_reason_remain_unchanged():
    result = TaskPlanner().plan(AgentContext(query='query', trace_id='unknown', active_skill='future_skill'))
    assert result.workflow_name == 'future_skill'
    assert result.steps == []
    assert result.metadata == {'reason': 'no deterministic workflow matched'}
