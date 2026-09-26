"""Empty-science-state B1 integration, not Web success or live prediction.

Real typed adapters and owned resolver/Session/journal boundaries are used.
ADMET and activity envelopes are synthetic, NONEXECUTING fixtures. No action
is registered, dispatched, sealed, or accepted as scientific evidence here.
"""
import json
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.binding_requirements import required_binding_tools
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.contracts.target_request import analyze_target_request
from src.agent.harness.decision_binding_inputs import BindingInputJournal
from src.agent.harness.decision_bindings import prepare_binding_requirements
from src.agent.tooling.activity_contract import ActivityToolAdapter
from src.agent.tooling.analysis_contract import AnalysisToolAdapter
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.agent.tools.property_calculator import PropertyCalculator
from test_activity_tool_contract import Recorder, single_row
from test_decision_binding_acceptance import admet_tool
from test_decision_binding_inputs import attach
from test_decision_dynamic_bindings import build, decision, owned


TASK6_QUERY = '评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: CCO; CCN'
SUBJECTS = ('CCO', 'CCN')
TOOLS = ('property_calculator', 'drug_likeness_assessment',
         'admet_predictor', 'activity_predictor')


def four_whole_batch_requirements():
    # Explicit current-schema obligations, not requirements inferred by a model.
    return dict(version='2', profile=B1_PROFILE_REVISION,
        molecular_results=[
            dict(tool_name='property_calculator', exact_molecule_count=2,
                 expected_smiles=list(SUBJECTS), required_metrics=['molecular_weight']),
            dict(tool_name='drug_likeness_assessment', exact_molecule_count=2,
                 expected_smiles=list(SUBJECTS), required_metrics=['qed']),
        ],
        analysis_results=[
            dict(tool_name='admet_predictor', exact_molecule_count=2,
                 expected_smiles=list(SUBJECTS), scope='available_methods'),
            dict(tool_name='activity_predictor', exact_molecule_count=2,
                 expected_smiles=list(SUBJECTS), target='PDE5A'),
        ])


def assert_zero_science(case, admet, activity):
    assert case.session.results == []
    assert case.session.ledger.to_list() == []
    assert case.session.steps == []
    assert case.session.tool_attempt_count == 0
    assert dict(case.session._decision_observation_seals) == {}
    assert case.resolver.export_records() == {}
    assert case.calls == {name: [] for name in TOOLS}
    assert admet.inputs == []
    assert activity.inputs == []


@pytest.mark.parametrize('query', [
    pytest.param(TASK6_QUERY, id='exact-task6'),
    pytest.param(
        '评估分子量和类药性和 QED 和 ADMET 和 PDE5A 活性；SMILES: CCO; CCN',
        id='qed-before-admet'),
    pytest.param(
        '评估分子量和类药性和 ADMET 和 QED 和 PDE5A 活性；SMILES: CCO; CCN',
        id='admet-before-qed'),
])
def test_empty_binding_closure_then_four_whole_batch_inputs(build, query):
    # These fixture outputs must never be consumed; only adapter construction
    # and input binding are in scope. Property/likeness are real tool instances.
    admet = admet_tool()
    activity = Recorder(dict(success=True, data=[single_row(smiles=s) for s in SUBJECTS]))
    requirements = four_whole_batch_requirements()
    original_requirements = deepcopy(requirements)
    case = attach(build(query, tools=[PropertyCalculator(), DrugLikenessAssessment(),
                                     admet, activity], requirements=requirements))
    assert isinstance(case.journal, BindingInputJournal)
    assert all(isinstance(case.adapters[name], AnalysisToolAdapter) for name in TOOLS[:3])
    assert isinstance(case.adapters['activity_predictor'], ActivityToolAdapter)
    assert required_binding_tools(case.requirements) == frozenset(TOOLS)
    groups = (*case.requirements.molecular_results, *case.requirements.analysis_results)
    assert len(groups) == 4
    assert all(group.exact_molecule_count == 2 and group.expected_smiles == SUBJECTS
               for group in groups)
    prepared_requirements = case.requirements.model_dump()
    initial_journal = case.journal.export()
    assert initial_journal['entries'] == [dict(input_turn=0, query=query, resolved_molecule=None)]
    assert initial_journal['original']['query'] == query
    assert case.journal.head_turn == 0
    assert case.resolver._issued == set()
    assert_zero_science(case, admet, activity)

    try:
        # First behavioral check: there are no observations to pre-seed closure.
        # Old target_request.py misreads ADMET/QED as unknown target-list members;
        # _journal_inputs then raises invalid_dynamic_binding at this assertion.
        assert owned(case, lambda: case.resolver.verify_binding_closure([])) == []
        assert case.resolver._issued == set()
        actions = {}
        for name in TOOLS:
            assert_zero_science(case, admet, activity)
            action = owned(case, lambda: case.resolver.resolve(decision(name)))
            actions[name] = action.input_data
            record = json.loads(action.record_json)
            assert record['tool_name'] == name
            assert record['context']['query'] == query
            assert record['input_turn'] == 0
            assert record['input_prefix_sha256'] == case.journal.prefix_digest(0)
            assert record['proof']['roles'] == []
            assert record['proof']['own_source'] is None
            # Resolving issues an input record; it does not register or seal it.
            assert_zero_science(case, admet, activity)
        assert actions == {
            'property_calculator': {'query': 'CCO\nCCN'},
            'drug_likeness_assessment': {'query': 'CCO\nCCN'},
            'admet_predictor': {'query': 'CCO\nCCN'},
            'activity_predictor': {'query': {
                'query': query, 'smiles': list(SUBJECTS), 'target': 'PDE5A'}},
        }
        assert owned(case, lambda: case.resolver.verify_binding_closure([])) == []
    finally:
        # Also enforce zero dispatch and unchanged whole intent on the RED path.
        assert_zero_science(case, admet, activity)
        assert case.context.query == case.session.context.query == query
        assert case.journal.context().query == query
        assert case.journal.export() == initial_journal
        assert case.requirements.model_dump() == prepared_requirements
        assert requirements == original_requirements


@pytest.mark.parametrize('query,targets,unknown,reject_preparation', [
    pytest.param(
        '评估 ADMET 和 PDE5A 活性；target: UNKNOWN42；SMILES: CCO; CCN',
        ('PDE5A',), 'UNKNOWN42', True, id='explicit-unknown'),
    pytest.param(
        '评估 ADMET 和 PDE5A 活性；target: ADMET；SMILES: CCO; CCN',
        ('PDE5A',), 'ADMET', True, id='explicit-admet-is-not-a-metric-exemption'),
    pytest.param(
        '评估 ADMET 和 PDE5A 活性；target: BuChE；SMILES: CCO; CCN',
        ('PDE5A', 'BCHE'), None, True, id='explicit-cross-family-targets'),
    pytest.param(
        '评估 ADMET 和 PDE5A 活性；target: PDE4A；SMILES: CCO; CCN',
        ('PDE5A', 'PDE4A'), None, False, id='explicit-same-family-targets'),
])
def test_explicit_unknown_and_multiple_target_guards_preserve_whole_intent(
        query, targets, unknown, reject_preparation):
    context = AgentContext(query, 'analysis-clause-target-guard', session_id='owner')
    journal = BindingInputJournal(context)
    initial = journal.export()
    request = analyze_target_request(query)
    assert request.targets == targets
    assert request.explicit
    assert request.needs_clarification
    if unknown is not None:
        assert unknown in request.unknown
    requirements = four_whole_batch_requirements()
    original_requirements = deepcopy(requirements)
    if reject_preparation:
        # Existing activity preparation rejects unknown/cross-family targets.
        # The same-family parser guard above does not promise this behavior.
        with pytest.raises(ValueError, match='^invalid_binding_requirements$'):
            prepare_binding_requirements(requirements, context=context,
                request_kind='scientific', allowed_tools=set(TOOLS), required_tools=set())
    # Journal admission bounds/preserves input; it is not a universal semantic
    # ambiguity validator. Do not assert that journal construction must reject.
    assert context.query == journal.context().query == query
    assert journal.export() == initial
    assert initial['entries'] == [dict(input_turn=0, query=query, resolved_molecule=None)]
    assert initial['original']['query'] == query
    assert requirements == original_requirements
