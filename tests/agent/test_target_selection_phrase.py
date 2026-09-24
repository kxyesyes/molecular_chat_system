"""Bounded candidate selection must not be mistaken for a second target."""

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts import target_request
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing import HybridSkillRouter


def assert_design(query, top_n=3):
    request = target_request.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == ()
    assert not request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'target_driven_design'
    assert not decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill=decision.selected_skill, trace_id='selection-test',
    ))
    assert plan.metadata == {
        'target_hint': 'PDE5A', 'requested_count': 2, 'docking_top_n': top_n,
    }
    assert [step.tool_name for step in plan.steps] == [
        'target_database_search', 'llm_molecular_generator', 'property_calculator',
        'admet_predictor', 'activity_predictor', 'candidate_ranker',
    ]
    assert plan.steps[0].input_data == 'PDE5A'
    assert plan.steps[1].preconditions == ('target_evidence',)
    assert plan.steps[1].input_data['metadata']['requested_count'] == 2
    assert plan.steps[1].input_data['query'] == query
    for step in plan.steps[2:5]:
        assert step.input_binding == '$.outputs.molecules'
    assert plan.steps[-1].metadata['docking_top_n'] == top_n


@pytest.mark.parametrize('connector', [' and ', ', ', '，', '\tAnD\t'])
@pytest.mark.parametrize('phrase', [
    'select top 3', 'SELECT TOP 3', 'Select Top 3 candidates',
    'select\ttop\t3 molecules',
])
def test_terminal_selection_routes_and_plans(connector, phrase):
    assert_design('Design 2 molecules for PDE5A' + connector + phrase)


@pytest.mark.parametrize('count', range(1, 101))
def test_selection_preserves_top_n_without_changing_generation_count(count):
    assert_design(f'Design 2 molecules for PDE5A and select top {count}', count)


@pytest.mark.parametrize('ending', ['.', '!', '?', '。', '！', '？', ' \t', ' \t! \t', '\n', '!\r\n'])
def test_selection_allows_single_terminal_punctuation(ending):
    assert_design('Design 2 molecules for PDE5A and select top 3 candidates' + ending)


@pytest.mark.parametrize('ending', ['\u3000', '\u00a0', '\u2028'])
def test_terminal_unicode_whitespace_agrees_with_router_trimming(ending):
    assert_design('Design 2 molecules for PDE5A and select top 3' + ending)


@pytest.mark.parametrize('suffix', [
    ' and select XYZ999', ' and select EGFR', ' and select top 3XYZ999',
    ' and select top 3 and XYZ999', ' and select top 3 candidates for XYZ999',
    ' and select top 3 molecules and EGFR', ' and select top 3 extra',
    ' and select top 0', ' and select top 101', ' and select top 01',
    ' and select top 3.5', ' and select top -3', ' and select top +3',
    ' and select top 3e0', ' and select top ３', ' and select top 3..',
    pytest.param(' and select top ' + '9' * 10000, id='overlong-number'),
    pytest.param(' and select top 3' + ' ' * 10000 + 'XYZ999', id='long-tail-with-unknown'),
    ' or select top 3', '/ select top 3', '、select top 3',
    ' and\nselect top 3', '\nand select top 3', ' and select\ntop 3',
    ' and select top\n3', ' and select top 3\nand extra',
])
def test_unbounded_or_invalid_selection_still_clarifies(suffix):
    query = 'Design 2 molecules for PDE5A' + suffix
    assert target_request.analyze_target_request(query).needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None
    assert decision.requires_confirmation
    for skill in ('target_driven_design', 'molecular_design', 'hit_to_lead_optimization'):
        plan = TaskPlanner().plan(AgentContext(
            query=query, active_skill=skill, trace_id='selection-test',
        ))
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('prefix, unknown, targets, qualified', [
    ('Design 2 molecules for XYZ999 and PDE5A', ('XYZ999',), ('PDE5A',), False),
    ('Design 2 molecules for PDE5A and XYZ999', ('XYZ999',), ('PDE5A',), False),
    ('Design 2 molecules for PDE5A and EGFR', (), ('PDE5A', 'EGFR'), False),
    ('Design 2 selective molecules for PDE5A', (), ('PDE5A',), True),
    ('Design 2 molecules not against PDE5A', (), ('PDE5A',), True),
    ('Instead design 2 molecules for PDE5A', (), ('PDE5A',), True),
])
def test_selection_preserves_original_target_constraints(prefix, unknown, targets, qualified):
    query = prefix + ' and select top 3'
    request = target_request.analyze_target_request(query)
    assert request.unknown == unknown
    assert request.targets == targets
    assert request.qualified is qualified
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None
    assert decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill='target_driven_design', trace_id='selection-test',
    ))
    assert not plan.steps
    assert plan.metadata['reason'] == 'target_clarification_required'


def test_select_is_not_a_global_action_word():
    assert 'select' not in target_request._ACTION_WORDS
    request = target_request.analyze_target_request(
        'Design 2 molecules for PDE5A and select XYZ999',
    )
    assert 'select' in request.unknown
    assert request.needs_clarification


@pytest.mark.parametrize('count', [16, 32, 64])
def test_selection_scan_retains_linear_operation_bound(monkeypatch, count):
    class CountingPattern:
        def __init__(self, pattern):
            self.pattern = pattern
            self.calls = 0

        def match(self, *args, **kwargs):
            self.calls += 1
            return self.pattern.match(*args, **kwargs)

    counter = CountingPattern(target_request._FOLLOWING)
    monkeypatch.setattr(target_request, '_FOLLOWING', counter)
    query = 'Design 2 molecules for ' + ' and '.join(['PDE5A'] * count)
    query += ' and select top 3'
    request = target_request.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert not request.needs_clarification
    assert counter.calls <= 8 * count + 32
