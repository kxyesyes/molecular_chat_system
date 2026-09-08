"""Requirements gate tests; model responses are explicit test doubles."""
import asyncio
import json
from copy import deepcopy

import pytest

from test_decision_loop import setup_loop, CountingTool, tool, finish_last, clarify, run
from test_decision_continuation import start, invoke, fresh
from src.agent.contracts import ToolResult, RunOutcome


def requirements(count=1, metrics=('qed',), **kw):
    return {'version': '1', 'molecular_results': [{'tool_name': 'property_calculator',
        'exact_molecule_count': count, 'required_metrics': list(metrics), **kw}]}


def row(smiles='CCO', **kw):
    return {'smiles': smiles, 'properties': {'molecular_weight': 46.069, 'qed': 0.4, **kw}}


class RowsTool(CountingTool):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows
    def execute(self, query):
        self.inputs.append(query)
        return ToolResult.success_result(self.name, deepcopy(self.rows))


def test_complete_batch_has_structured_acceptance_and_preserves_values(setup_loop):
    b = setup_loop([tool(), finish_last], [RowsTool([row()])])
    result = run(b, requirements=requirements(metrics=('qed', 'molecular_weight'), expected_smiles=['OCC']))
    assert result.success, result.metadata['task_acceptance']
    report = result.metadata['task_acceptance']
    assert report['satisfied'] and report['checks'][0]['valid_unique_count'] == 1
    assert report['checks'][0]['evidence_ids'] == [result.tool_results[0].quality['evidence_id']]
    assert b.store.get_run('trace-test')['metadata']['task_acceptance'] == report
    assert b.bus.events[-1].payload['metadata']['task_acceptance'] == report
    assert 'task_acceptance' in json.loads(b.model.messages[-1][-1]['content'])


@pytest.mark.parametrize('rows, count, metrics, reason', [
    ([row()], 2, ('qed',), 'molecule_count_mismatch'),
    ([row(), row('OCC')], 2, ('qed',), 'duplicate_smiles'),
    ([row(), row('CC(C)((')], 2, ('qed',), 'invalid_smiles'),
    ([row()], 1, ('logp',), 'missing_or_invalid_metric'),
    ([row(qed=True)], 1, ('qed',), 'missing_or_invalid_metric'),
    ([row(qed='0.5')], 1, ('qed',), 'missing_or_invalid_metric'),
    ([row(qed=2)], 1, ('qed',), 'missing_or_invalid_metric'),
    ([row(hbd=1.5)], 1, ('hbd',), 'missing_or_invalid_metric'),
    ([row(), row('CCN')], 1, ('qed',), 'molecule_count_mismatch'),
])
def test_tool_success_does_not_mean_task_success(setup_loop, rows, count, metrics, reason):
    b = setup_loop([tool(), finish_last], [RowsTool(rows)])
    result = run(b, requirements=requirements(count, metrics))
    assert not result.success and result.outcome in (RunOutcome.PARTIAL, RunOutcome.FAILED)
    report = result.metadata['task_acceptance']
    assert not report['satisfied'] and reason in report['checks'][0]['reason_codes']


def test_valid_wrong_molecule_does_not_satisfy_subjects(setup_loop):
    b = setup_loop([tool(), finish_last], [RowsTool([row('CCN')])])
    result = run(b, requirements=requirements(expected_smiles=['CCO']))
    assert not result.success
    assert 'subject_mismatch' in result.metadata['task_acceptance']['checks'][0]['reason_codes']


def test_forbidden_tool_is_blocked_before_dispatch(setup_loop):
    b = setup_loop([tool('drug_likeness_assessment')],
                   [RowsTool([row()]), CountingTool('drug_likeness_assessment')])
    req = requirements()
    req['forbidden_tools'] = ['drug_likeness_assessment']
    result = run(b, requirements=req)
    assert not result.success and not any(t.inputs for t in b.tools)
    assert result.metadata['stop_reason'] == 'tool_forbidden_by_task'


def test_conflict_with_required_tools_rejected_before_model_or_trace(setup_loop):
    b = setup_loop([tool()])
    result = run(b, requirements={'version': '1', 'forbidden_tools': ['property_calculator']})
    assert not result.success and not b.model.messages and not b.tools[0].inputs
    assert b.store.get_run('trace-test') is None


@pytest.mark.parametrize('change', ['count', 'metrics', 'forbidden', 'remove'])
def test_continuation_cannot_weaken_or_change_requirements(setup_loop, change):
    b = setup_loop([])
    req = requirements(2)
    waiting = start(b, [clarify()], requirements=req)
    fresh(b, [tool(), finish_last])
    before = b.store.get_run('owned-trace')
    modified = deepcopy(req)
    if change == 'count': modified['molecular_results'][0]['exact_molecule_count'] = 1
    if change == 'metrics': modified['molecular_results'][0]['required_metrics'] = []
    if change == 'forbidden': modified['forbidden_tools'] = ['molecular_docking']
    if change == 'remove': modified = None
    result = invoke(b, requirements=modified, continuation_id=waiting.metadata['continuation_id'],
                    clarified_query='SMILES: CCO')
    assert not result.success and b.store.get_run('owned-trace') == before
    assert not b.model.messages and not b.tools[0].inputs


def test_unchanged_requirements_resume_and_real_rdkit(setup_loop):
    from src.agent.tools.property_calculator import PropertyCalculator
    b = setup_loop([], [PropertyCalculator()])
    req = requirements(metrics=('molecular_weight', 'logp', 'qed', 'tpsa', 'hbd', 'hba'))
    waiting = start(b, [clarify()], requirements=req)
    fresh(b, [tool(), finish_last])
    result = invoke(b, requirements=req, continuation_id=waiting.metadata['continuation_id'],
                    clarified_query='SMILES: CCO')
    assert result.success and result.metadata['task_acceptance']['satisfied']


def test_caller_mutation_cannot_relax_frozen_requirement(setup_loop):
    req = requirements(2)
    def relax_then_finish(messages):
        req['molecular_results'][0]['exact_molecule_count'] = 1
        return finish_last(messages)
    b = setup_loop([tool(), relax_then_finish], [RowsTool([row()])])
    result = run(b, requirements=req)
    assert not result.success
    assert result.metadata['task_requirements']['molecular_results'][0]['exact_molecule_count'] == 2


def test_cannot_join_metrics_across_observations(setup_loop):
    from test_decision_loop import last_observation
    class SplitTool(RowsTool):
        def execute(self, query):
            self.rows = [{'smiles': 'CCO', 'properties': {'qed': 0.4} if not self.inputs
                          else {'molecular_weight': 46.069}}]
            return super().execute(query)
    def bound(messages):
        return tool(arguments={'input_ref': last_observation(messages)['quality']['evidence_id']})
    b = setup_loop([tool(), bound, finish_last], [SplitTool([])])
    result = run(b, requirements=requirements(metrics=('qed', 'molecular_weight')))
    assert not result.success and len(b.tools[0].inputs) == 2
    assert 'missing_or_invalid_metric' in result.metadata['task_acceptance']['checks'][0]['reason_codes']


def test_lipinski_false_is_a_real_result_not_missing_metric(setup_loop):
    source = RowsTool([{'smiles': 'CCO', 'assessment': {'lipinski_rule_of_five': {'compliance': False}}}])
    source.name = 'drug_likeness_assessment'
    b = setup_loop([tool(source.name), finish_last], [source])
    req = {'version': '1', 'molecular_results': [{'tool_name': source.name,
            'required_metrics': ['lipinski_compliant'], 'exact_molecule_count': 1}]}
    result = run(b, requirements=req, required_tools={source.name})
    assert result.success and result.tool_results[0].data[0]['assessment']['lipinski_rule_of_five']['compliance'] is False


def test_real_rdkit_two_molecule_properties_and_drug_likeness(setup_loop):
    from src.agent.contracts import AgentContext
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    b = setup_loop([tool(), tool('drug_likeness_assessment'), finish_last],
                   [PropertyCalculator(), DrugLikenessAssessment()])
    subjects = ['CC(=O)Oc1ccccc1C(=O)O', 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C']
    criteria = requirements(2, ('molecular_weight', 'logp', 'qed', 'tpsa', 'hbd', 'hba'), expected_smiles=subjects)
    extra = deepcopy(criteria['molecular_results'][0])
    extra['tool_name'] = 'drug_likeness_assessment'
    extra['required_metrics'].append('lipinski_compliant')
    criteria['molecular_results'].append(extra)
    result = asyncio.run(b.loop.run(AgentContext('\n'.join('SMILES: ' + s for s in subjects), 'rdkit-batch'),
        request_kind='scientific', allowed_tools={t.name for t in b.tools}, required_tools=set(),
        requirements=criteria, event_bus=b.bus))
    assert result.success, result.metadata['task_acceptance']
    assert all(c['passed'] and c['valid_unique_count'] == 2 for c in result.metadata['task_acceptance']['checks'])


def test_short_smiles_batch_meets_exact_subject_requirements(setup_loop):
    from src.agent.contracts import AgentContext
    from src.agent.tools.property_calculator import PropertyCalculator
    b = setup_loop([tool(), finish_last], [PropertyCalculator()])
    result = asyncio.run(b.loop.run(AgentContext('SMILES: CCO\nSMILES: CCN', 'short-smiles'),
        request_kind='scientific', allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
        requirements=requirements(2, expected_smiles=['CCO', 'CCN']), event_bus=b.bus))
    assert result.success, result.metadata
    check = result.metadata['task_acceptance']['checks'][0]
    assert check['valid_unique_count'] == 2 and check['passed']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), True, '0.2', None])
def test_non_numeric_or_nonfinite_metric_is_not_evidence(value):
    from src.agent.harness.decision_requirements import _valid_metric
    assert not _valid_metric('qed', value)


@pytest.mark.parametrize('subjects', [['CC(C)(('], ['CCO', 'OCC']])
def test_bad_subject_configuration_does_not_start_a_run(setup_loop, subjects):
    b = setup_loop([tool()])
    result = run(b, requirements=requirements(len(subjects), expected_smiles=subjects))
    assert not result.success and not b.model.messages and b.store.get_run('trace-test') is None


def test_missing_model_is_not_accepted(setup_loop):
    b = setup_loop([tool(), clarify()], [CountingTool(fail=True)])
    result = run(b, requirements=requirements())
    assert not result.success and not result.metadata['task_acceptance']['satisfied']
    assert result.metadata['task_acceptance']['checks'][0]['reason_codes'] == ['no_usable_observation']


def test_terminal_acceptance_error_cannot_keep_success(setup_loop, monkeypatch):
    import src.agent.harness.decision_loop as module
    original, calls = module.evaluate_requirements, []
    def break_final(*args, **kw):
        calls.append(True)
        if len(calls) >= 4: raise RuntimeError('test final check failed')
        return original(*args, **kw)
    monkeypatch.setattr(module, 'evaluate_requirements', break_final)
    result = run(setup_loop([tool(), finish_last], [RowsTool([row()])]), requirements=requirements())
    assert not result.success
    assert not result.metadata['task_acceptance']['satisfied']


@pytest.mark.parametrize('blocked', ['capability', 'registry', 'owner'])
def test_requirements_must_match_effective_authorization(setup_loop, blocked):
    from dataclasses import replace
    from src.agent.contracts import AgentContext
    b = setup_loop([clarify()], [] if blocked == 'registry' else None)
    context = AgentContext('SMILES: CCO', 'effective-authorization')
    if blocked == 'capability': context.metadata = {'capabilities': {'scientific_tools': False}}
    if blocked == 'owner':
        adapter = b.registry.resolve('property_calculator')
        adapter.spec = replace(adapter.spec, owner_agents={'not-the-authorized-owner'})
    result = asyncio.run(b.loop.run(context, request_kind='scientific',
        allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
        requirements=requirements(), event_bus=b.bus))
    assert not result.success and not b.model.messages
    assert not any(t.inputs for t in b.tools)
    assert b.store.get_run(context.trace_id) is None
