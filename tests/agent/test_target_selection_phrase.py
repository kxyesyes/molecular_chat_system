"""Bounded candidate selection must not be mistaken for a second target."""

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts import target_request
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing import HybridSkillRouter


@pytest.mark.parametrize('target, canonical', [
    ('PDE', 'PDE'), ('pde-family', 'PDE'), ('PDE5A', 'PDE5A'),
    ('BuChE', 'BCHE'), ('丁酰胆碱酯酶', 'BCHE'),
])
@pytest.mark.parametrize('phrase', [
    '以 {target} 为靶点设计三个候选分子',
    '以 {target} 为靶点 设计三个候选分子',
    '以{target}为靶点\t设计三个候选分子',
    '以 {target} 为 靶点\u3000设计三个候选分子',
    '以\t{target}\t为\t靶点 \t设计三个候选分子',
    '以 {target} 为靶点\n设计三个候选分子',
    '请以 {target} 为靶点，设计三个候选分子。',
    '以 {target} 为靶点, 设计三个候选分子',
    '以 {target} 为靶点 \t生成三个候选分子',
    '以“{target}”为靶点设计三个候选分子',
    '以（{target}）为靶点，设计三个候选分子',
    '以 "{target}" 为靶点 设计三个候选分子',
    "以 '{target}' 为靶点 设计三个候选分子",
    '以‘{target}’为靶点 设计三个候选分子',
    '以 ( {target} ) 为靶点 设计三个候选分子',
    '以 {target} 为靶点 设计 3 个类药候选分子；评估 ADMET 和 {target} 活性',
    '基于 {target} 设计三个候选分子',
    '针对 {target} 设计三个候选分子',
])
def test_target_role_equivalents_route_and_plan(phrase, target, canonical):
    query = phrase.format(target=target)
    request = target_request.analyze_target_request(query)
    assert request.targets == (canonical,)
    assert request.unknown == ()
    assert not request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'target_driven_design'
    assert not decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill=decision.selected_skill, trace_id='target-role-test',
    ))
    assert plan.metadata['target_hint'] == canonical
    assert plan.metadata['requested_count'] == 3
    assert plan.steps[0].tool_name == 'target_database_search'
    assert plan.steps[0].input_data == canonical
    assert plan.steps[1].input_data['query'] == query
    assert plan.steps[1].input_data['metadata']['requested_count'] == 3


@pytest.mark.parametrize('query, unknown, qualified', [
    ('以 XYZ999 为靶点设计三个候选分子', 'XYZ999', False),
    ('以 XYZ999 为靶点 设计三个 PDE 候选分子', 'XYZ999', False),
    ('以 未知激酶 为靶点，设计三个 PDE 候选分子', '未知激酶', False),
    ('以 ADMET 为靶点 设计三个 PDE 候选分子', 'ADMET', False),
    ('以 设计 为靶点 设计三个 PDE 候选分子', '设计', False),
    ('以 任意 PDE 说明 为靶点设计三个候选分子', '任意 PDE 说明', False),
    ('以 PDE5A_extra 为靶点设计三个 PDE 候选分子', 'PDE5A_extra', False),
    ('靶点 设计三个候选分子；基于 PDE 设计三个分子', '设计三个', False),
    ('以 PDE 为靶点 XYZ999，设计三个候选分子', 'XYZ999', False),
    ('以 PDE 和 XYZ999 为靶点设计三个候选分子', 'XYZ999', False),
    ('以 XYZ999 和 PDE 为靶点设计三个候选分子', 'XYZ999', False),
    ('以 PDE 和 EGFR 为靶点设计三个候选分子', None, False),
    ('以 PDE 为靶点设计三个候选分子；靶点：XYZ999', 'XYZ999', False),
    ('以 PDE 为靶点：XYZ999，设计三个候选分子', 'XYZ999', False),
    ('不是以 PDE 为靶点 设计三个候选分子', None, True),
    ('不以 PDE 为靶点 设计三个候选分子', None, True),
    ('不要以 PDE 为靶点设计三个候选分子', None, True),
    ('别以 PDE 为靶点\t设计三个候选分子', None, True),
    ('勿以 PDE 为靶点，设计三个候选分子', None, True),
    ('不再以 PDE 为靶点 设计三个候选分子', None, True),
    ('不能以 PDE 为靶点 设计三个候选分子', None, True),
    ('不再\t以 PDE 为靶点设计三个候选分子', None, True),
    ('不能 以 PDE 为靶点，设计三个候选分子', None, True),
    ('以 PDE 为靶点 设计三个选择性候选分子', None, True),
    ('以 PDE 为靶点改为 EGFR，设计三个候选分子', None, True),
    ('以 PDE 为靶点 设计三个候选分子，之后改用 XYZ999', 'XYZ999', True),
    ('以 PDE 为靶点设计三个候选分子；之后改用 XYZ999', 'XYZ999', True),
    ('以 PDE 为靶点 设计三个候选分子；以 XYZ999 为靶点', 'XYZ999', False),
    ('不要针对 PDE 设计三个候选分子', None, True),
])
def test_target_role_preserves_conservative_guards(query, unknown, qualified):
    request = target_request.analyze_target_request(query)
    if unknown is not None:
        assert unknown in request.unknown
    assert request.qualified is qualified
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None
    assert decision.requires_confirmation
    for skill in ('target_driven_design', 'molecular_design', 'hit_to_lead_optimization'):
        plan = TaskPlanner().plan(AgentContext(
            query=query, active_skill=skill, trace_id='target-role-test',
        ))
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('separator', ['', ' ', '\t', '，'])
@pytest.mark.parametrize('body', [
    '设计三个 XYZ999 抑制剂候选分子',
    '设计三个 未知激酶 抑制剂候选分子',
    '设计三个候选分子并改用 XYZ999',
    '设计三个候选分子 arbitrary prose',
])
def test_target_role_action_prefix_does_not_hide_unknown_body(separator, body):
    query = f'以 PDE 为靶点{separator}{body}'
    request = target_request.analyze_target_request(query)
    assert request.unknown
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None and decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill='target_driven_design', trace_id='role-body-test',
    ))
    assert not plan.steps
    assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('body', [
    '“XYZ999”', '（XYZ999）', '"XYZ999"', '(未知激酶)',
    '“PDE）', '（PDE”', '“PDE', 'PDE）', '（（PDE））',
    '“PDE 和 XYZ999”', '（PDE 说明）',
])
def test_target_role_wrappers_only_unwrap_one_balanced_known_target(body):
    request = target_request.analyze_target_request(
        f'以{body}为靶点 设计三个候选分子；预测 PDE 活性',
    )
    assert body in request.unknown
    assert request.needs_clarification


def test_target_role_negation_does_not_blanket_reject_unrelated_negative_prose():
    request = target_request.analyze_target_request(
        '不再保存报告，不能导出文件；以 PDE 为靶点 设计三个候选分子',
    )
    assert request.unknown == ()
    assert not request.qualified
    assert not request.needs_clarification


@pytest.mark.parametrize('negation', ['不', '不要', '不能', '别', '勿'])
@pytest.mark.parametrize('again', ['', '再', ' 再\t'])
@pytest.mark.parametrize('space', [' ', '\t'])
def test_target_role_composed_negation_grammar(negation, again, space):
    query = f'{negation}{again}以 PDE 为靶点{space}设计三个候选分子'
    request = target_request.analyze_target_request(query)
    assert request.qualified
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None and decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill='target_driven_design', trace_id='role-negation-test',
    ))
    assert not plan.steps
    assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('separator', ['，', ', ', '；', '\n'])
@pytest.mark.parametrize('connector', ['且', '并', '同时'])
@pytest.mark.parametrize('target', ['PDE', 'EGFR', 'XYZ999'])
def test_target_role_tail_target_clauses_keep_identity(separator, connector, target):
    query = f'以 PDE 为靶点 设计三个候选分子{separator}{connector}抑制 {target}'
    request = target_request.analyze_target_request(query)
    assert request.targets == (('PDE', 'EGFR') if target == 'EGFR' else ('PDE',))
    assert request.unknown == (('XYZ999',) if target == 'XYZ999' else ())
    assert not request.qualified
    assert request.needs_clarification is (target != 'PDE')
    decision = HybridSkillRouter().decide(query)
    if target == 'PDE':
        assert decision.selected_skill == 'target_driven_design'
        assert not decision.requires_confirmation
    else:
        assert decision.selected_skill is None and decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill='target_driven_design', trace_id='role-tail-test',
    ))
    if target == 'PDE':
        assert plan.metadata['target_hint'] == 'PDE'
        assert plan.metadata['requested_count'] == 3
    else:
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('switch', ['转而使用', '转而\t使用', '改用'])
@pytest.mark.parametrize('target', ['PDE', 'EGFR', 'XYZ999'])
def test_target_role_tail_switch_always_needs_clarification(switch, target):
    query = f'以 PDE 为靶点\t设计三个候选分子，{switch} {target}'
    request = target_request.analyze_target_request(query)
    assert request.qualified
    assert request.unknown == (('XYZ999',) if target == 'XYZ999' else ())
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None and decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill='target_driven_design', trace_id='role-tail-test',
    ))
    assert not plan.steps
    assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('tail', ['且抑制 XYZ999', '转向 XYZ999', '未识别条件 XYZ999'])
def test_target_role_repeated_role_cannot_hide_a_later_unknown_tail(tail):
    query = f'以 PDE 为靶点 设计三个候选分子；以 PDE 为靶点，{tail}'
    request = target_request.analyze_target_request(query)
    assert request.targets == ('PDE',)
    assert request.unknown
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None and decision.requires_confirmation


@pytest.mark.parametrize('separator', ['，', '; ', '\n'])
@pytest.mark.parametrize('connector', ['', '且', '并', '同时'])
@pytest.mark.parametrize('analysis', [
    '计算分子性质',
    '预测 ADMET',
    '评估分子量和类药性和 ADMET 和 PDE 活性',
    '计算性质及 ADMET，预测 PDE 活性',
    '预测 ADMET；计算 QED；评估 PDE 活性',
])
def test_target_role_complete_tail_preserves_analytical_steps(separator, connector, analysis):
    query = f'以 PDE 为靶点 设计三个候选分子{separator}{connector}{analysis}'
    request = target_request.analyze_target_request(query)
    assert request.targets == ('PDE',)
    assert request.unknown == ()
    assert not request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'target_driven_design'
    assert not decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill='target_driven_design', trace_id='role-tail-test',
    ))
    assert plan.metadata['target_hint'] == 'PDE'
    assert plan.metadata['requested_count'] == 3


@pytest.mark.parametrize('prefix', ['', '计算性质，', '预测 ADMET；', '且抑制 PDE，'])
@pytest.mark.parametrize('tail', [
    '阻断 XYZ999', '转向 XYZ999', '未识别条件 XYZ999',
    '且抑制 未知激酶', '计算 UNKNOWN42 活性', '预测 ADMET 和 XYZ999 活性',
    '评估 PDE 活性 extra', '同时抑制 XYZ999 后预测 ADMET',
    '且', '并', '同时', '预测 ADMET 并', '计算性质和',
])
def test_target_role_complete_tail_does_not_drop_unrecognized_remainder(prefix, tail):
    query = f'以 PDE 为靶点 设计三个候选分子，{prefix}{tail}'
    request = target_request.analyze_target_request(query)
    assert request.unknown
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None and decision.requires_confirmation
    plan = TaskPlanner().plan(AgentContext(
        query=query, active_skill='target_driven_design', trace_id='role-tail-test',
    ))
    assert not plan.steps
    assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('count', [16, 32, 64])
@pytest.mark.parametrize('unknown_tail', [False, True])
def test_target_role_complete_tail_scans_each_clause_with_linear_work(monkeypatch, count, unknown_tail):
    calls = 0
    original = target_request._analysis_item

    def counted_item(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(target_request, '_analysis_item', counted_item)
    query = '以 PDE 为靶点 设计三个候选分子'
    query += '，计算分子性质和 ADMET 和 PDE 活性' * count
    if unknown_tail:
        query += '，不认识的动作 UNKNOWN42'
    request = target_request.analyze_target_request(query)
    assert request.targets == ('PDE',)
    assert request.needs_clarification is unknown_tail
    assert bool(request.unknown) is unknown_tail
    assert 3 * count <= calls <= 8 * count + 8


@pytest.mark.parametrize('count', [16, 32, 64])
def test_target_role_scan_keeps_repeated_and_long_unknown_bodies(count):
    # Each incomplete head ends at the next head; no overlapping body rescans.
    prefix = '以未知 ' * count
    unknown = 'UNKNOWN' + '9' * (count * 100)
    request = target_request.analyze_target_request(
        prefix + f'以 {unknown} 为靶点 设计三个候选分子；预测 PDE 活性',
    )
    assert request.targets == ('PDE',)
    assert request.unknown == (unknown,)
    assert request.explicit and request.needs_clarification


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
