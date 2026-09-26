"""Analytical role parsing only; no model/scientific readiness claim."""
import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts import target_request as boundary
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing import HybridSkillRouter


EXACT_QUERY = '评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: CCO; CCN'


@pytest.mark.parametrize('query', [
    pytest.param(EXACT_QUERY, id='exact-reproducer'),
    '评估 QED 和 ADMET 和 PDE5A 活性',
    '评估 ADMET 和 QED 和 PDE5A 活性',
    '评估 LOGP 和 ADMET 和 PDE5A 活性',
    '评估 ADMET 和 LogP 和 PDE5A 活性',
    '评估 PDE5A 活性和 QED 和 ADMET',
    '评估 PDE5A 活性和 ADMET 和 QED',
    '请预测 ADME、PDE5A 的活性。',
    'Please ASSESS QED and admet and pde5a activity!',
    'calculate molecular weight, properties, ADMET, PDE5A potency',
    'compute drug-likeness and ADME and PDE5A pIC50',
    'analyse drug likeness and ADMET and PDE5A IC50',
    'analyze Lipinski and ADMET and PDE5A activity',
    'evaluate\tTPSA\tand\tHBD and HBA and ADMET and PDE5A activity',
    '预测分子性质及分子属性与理化性质并性质、属性，成药性和ADMET和PDE5A活性',
    'unrelated prose; 评估 QED 和 ADMET 和 PDE5A 活性\nSMILES: CCO; CCN',
])
def test_complete_analysis_retains_target_without_analytical_unknown(query):
    original = query.encode('utf-8')
    request = boundary.analyze_target_request(query)
    assert query.encode('utf-8') == original
    assert request.targets == ('PDE5A',)
    assert request.unknown == ()
    assert not request.qualified
    assert not request.needs_clarification
    # A successful exemption need not manufacture explicit-label authority.
    assert not request.explicit


@pytest.mark.parametrize('label', ['target:', 'against ', 'for '])
@pytest.mark.parametrize('prefix', ['', '评估 QED 和 ADMET 和 PDE5A 活性；'])
def test_explicit_admet_occurrence_is_never_exempted(label, prefix):
    query = prefix + label + 'ADMET and PDE5A'
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == ('ADMET',)
    assert request.explicit and request.needs_clarification
    assert 'admet' not in boundary._ACTION_WORDS


@pytest.mark.parametrize('clause, unknown, targets, qualified', [
    ('评估 UNKNOWN42 和 PDE5A 活性', ('UNKNOWN42',), ('PDE5A',), False),
    ('评估 PDE5A 和 UNKNOWN42 活性', ('UNKNOWN42',), ('PDE5A',), False),
    ('评估 ADMET2 和 PDE5A 活性', ('ADMET2',), ('PDE5A',), False),
    ('评估 PDE5A 和 ADMET2 活性', ('ADMET2',), ('PDE5A',), False),
    ('评估 ADMET 和 PDE5A 活性 和 PDE4A 活性', (), ('PDE5A', 'PDE4A'), False),
    ('评估 ADMET 和 PDE5A 活性 和 BuChE 活性', (), ('PDE5A', 'BCHE'), False),
    ('评估 ADMET 和 PDE5A 活性；target: UNKNOWN42', ('UNKNOWN42',), ('PDE5A',), False),
    ('评估 ADMET 和 PDE5A 活性；not against PDE5A', (), ('PDE5A',), True),
    ('评估 ADMET 和 PDE5A 活性；switch target: PDE4A', (), ('PDE5A', 'PDE4A'), True),
    ('评估 ADMET 和 PDE5A 活性；selective', (), ('PDE5A',), True),
])
def test_ambiguity_survives_parser_router_and_planner(clause, unknown, targets, qualified):
    # Generation intent is explicit because Router's existing ambiguity guard
    # is a generation guard, not a promise to reject every analytical query.
    query = clause + '; Design 2 molecules'
    request = boundary.analyze_target_request(query)
    assert request.targets == targets
    assert request.unknown == unknown
    assert request.qualified is qualified
    assert request.needs_clarification
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill is None and decision.requires_confirmation
    for skill in ('target_driven_design', 'molecular_design', 'hit_to_lead_optimization'):
        context = AgentContext(query=query, active_skill=skill, trace_id='analysis-role-test')
        plan = TaskPlanner().plan(context)
        assert context.query == query
        assert not plan.steps
        assert plan.metadata['reason'] == 'target_clarification_required'


@pytest.mark.parametrize('query', [
    'ADMET 和 PDE5A 活性',  # No action.
    '提示 评估 ADMET 和 PDE5A 活性',  # No forward search past unknown text.
    '请 please assess ADMET and PDE5A activity',  # At most one politeness prefix.
    'assessments ADMET and PDE5A activity',
    '评估 QED 和 PDE5A 活性',  # Required ADME(T) absent.
    '评估 ADMET 和 PDE5A',  # Required activity noun absent.
    '评估 ADMET PDE5A 活性',  # Whitespace is not a join.
    '评估 ADMET 和和 PDE5A 活性',
    '评估 ADMET 和 PDE5A 活性 和',
    '评估 ADMET 和 PDE5A 活性 extra',
    '评估 ADMET 和 PDE5A 活性。。',
    '评估 ADMET2 和 PDE5A 活性',
    '评估 ADMET 和 UNKNOWN42 活性',
    '评估 ADMET 和 PDE5AX 活性',
    '评估 ADMET 和 PDE5A_extra 活性',
    'assess ADMET and PDE5A activityXYZ',
    'assess ADMET androgen PDE5A activity',
    'assess ADMET or PDE5A activity',
    '评估 ADMET / PDE5A 活性',
    '评估 ADMET 和 target: PDE5A 活性',
    'assess ADMET and for PDE5A activity',
    'assess ADMET and against PDE5A activity',
    '评估 ADMET 和 PDE5A 活性 SMILES: CCO',
    '评估 ADMET 和 PDE5A 活性，SMILES: CCO',
    '评估 ADMET 和 PDE5A 活性\nextra',  # Tested separately below: first clause valid.
])
def test_incomplete_or_unknown_text_cannot_create_full_query_interval(query):
    intervals = boundary._analysis_clause_intervals(query)
    if query.endswith('\nextra'):
        assert intervals == ((0, query.index('\n')),)
    else:
        assert intervals == ()


@pytest.mark.parametrize('separator', [';', '；', '\n', '\r\n'])
def test_only_complete_clause_span_is_published(separator):
    clause = ' 请评估 QED 和 ADMET 和 PDE5A 活性！\t'
    prefix = 'SMILES: CCO' + separator
    query = prefix + clause + separator + 'target: ADMET'
    assert boundary._analysis_clause_intervals(query) == ((len(prefix), len(prefix) + len(clause)),)
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == ('ADMET',)
    assert request.explicit and request.needs_clarification


@pytest.mark.parametrize('query, unknown', [
    ('评估 ADMET 和 PDE5A', ('ADMET',)),
    ('评估 QED 和 ADMET 和 PDE5A 活性 extra', ('QED', 'ADMET')),
    ('评估 ADMET 和 PDE5A 活性，SMILES: CCO', ('ADMET',)),
    ('评估 ADMET2 和 PDE5A 活性', ('ADMET2',)),
])
def test_declined_clause_retains_original_anchored_list_unknowns(query, unknown):
    assert boundary._analysis_clause_intervals(query) == ()
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == unknown
    assert request.needs_clarification


@pytest.mark.parametrize('count', [16, 32, 64])
@pytest.mark.parametrize('trailing_unknown', [False, True])
def test_scanner_and_interval_cursor_have_linear_operation_growth(monkeypatch, count, trailing_unknown):
    counters = dict(items=0, intervals=0, reads=0)
    original_item = boundary._analysis_item
    original_intervals = boundary._analysis_clause_intervals

    def counted_item(*args):
        counters['items'] += 1
        return original_item(*args)

    class CountedIntervals:
        def __init__(self, values):
            self.values = values

        def __len__(self):
            return len(self.values)

        def __getitem__(self, index):
            counters['reads'] += 1
            return self.values[index]

    def counted_intervals(query):
        counters['intervals'] += 1
        return CountedIntervals(original_intervals(query))

    monkeypatch.setattr(boundary, '_analysis_item', counted_item)
    monkeypatch.setattr(boundary, '_analysis_clause_intervals', counted_intervals)
    long_clause = '评估 ' + ' 和 '.join(['QED'] * count + ['ADMET', 'PDE5A 活性'])
    if trailing_unknown:
        long_clause += ' extra'
    query = ';'.join(['评估 QED 和 ADMET 和 PDE5A 活性'] * count + [long_clause])
    request = boundary.analyze_target_request(query)
    assert request.targets == ('PDE5A',)
    assert request.unknown == (('QED', 'ADMET') if trailing_unknown else ())
    assert counters['intervals'] == 1
    assert counters['items'] == 4 * count + 2
    token_count = sum(1 for _ in boundary._IDENTIFIER.finditer(query))
    assert counters['reads'] <= 4 * token_count + 2 * (count + 1) + 8
