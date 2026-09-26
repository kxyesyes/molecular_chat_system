"""Acceptance of sealed B1 observations, not live model/scientific validation.

RDKit and temporary RAG/reverse sources are real. ADMET/activity envelopes and
the explicitly injected target Service are synthetic, test-owned fixtures.
"""
import importlib
import importlib.util
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from src.agent.contracts import ToolResult, ObservationStatus, ToolProvenance, WorkflowArtifact
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION, B1_TOOLS
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.agent.tools.target_database_tool import TargetDatabaseTool
from test_decision_dynamic_bindings import build, decision, execute, eid, owned
from test_current_source_tool_hooks import sources, forbid_work
from test_analysis_contract import CountingTool, analysis_rows
from test_activity_tool_contract import Recorder, single_row
from test_family_activity_tool import family_row
from test_target_tool_contract import Service
from test_decision_binding_inputs import attach, admit, selections


def api():
    name = 'src.agent.harness.decision_binding_acceptance'
    assert importlib.util.find_spec(name) is not None, 'Task4A acceptance API missing'
    return importlib.import_module(name)


def requirements(**groups):
    return dict(version='2', profile=B1_PROFILE_REVISION, **groups)


def evaluate(case, **kwargs):
    return owned(case, lambda: api().evaluate_binding_acceptance(case.resolver, **kwargs))


def render(case, ids, **kwargs):
    return owned(case, lambda: api().render_binding_scientific_answer(
        case.resolver, evidence_ids=ids, **kwargs))


def target_tool(status='resolved'):
    tool = TargetDatabaseTool()
    tool._service = Service(status)
    return tool


def admet_tool(*, empty=False, smiles='CCO'):
    # Synthetic sparse adme_py fixture; no backend/model initialization.
    value = dict(prediction_method='adme_py', backend_version='synthetic-fixture')
    value.update({key: {} for key in ('physicochemical', 'solubility', 'lipophilicity',
                                    'pharmacokinetics', 'druglikeness', 'medicinal')})
    value['medicinal'] = dict(pains=None, brenk=None, zinc=None)
    if not empty:
        value['lipophilicity']['wlogp'] = 0.0
    return CountingTool('admet_predictor', dict(success=True, data=[dict(smiles=smiles, admet=value)]))


def test_independent_rag_citation_does_not_complete_successful_property(build, sources):
    rag = sources('rag')
    query = 'SMILES: CCO'
    case = build(query, tools=[PropertyCalculator(), rag.tool], requirements=requirements(
        molecular_results=[dict(tool_name='property_calculator', required_metrics=['logp'])],
        retrieval_result=dict(query=query)))
    prop = execute(case, decision())
    retrieval = execute(case, decision('rag_search'))
    assert prop.success and retrieval.success
    report = evaluate(case, evidence_ids=[eid(retrieval)])
    assert not report['satisfied'] and not report['finish_eligible']
    assert 'missing_required_citation' in report['reason_codes']
    assert report['cited_closure_ids'] == [eid(retrieval)]
    assert eid(prop) not in report['required_evidence_ids']
    assert evaluate(case, evidence_ids=[eid(prop), eid(retrieval)])['finish_eligible']


def test_progress_is_never_finish_and_empty_scientific_citations_fail(build):
    case = build(requirements=requirements(molecular_results=[dict(tool_name='property_calculator')]))
    result = execute(case, decision())
    progress = evaluate(case)
    assert progress['satisfied'] and not progress['citation_checked']
    assert not progress['finish_eligible']
    empty = evaluate(case, evidence_ids=[])
    assert empty['citation_checked'] and not empty['finish_eligible']
    assert not empty['satisfied']
    final = evaluate(case, evidence_ids=[eid(result)])
    assert final['satisfied'] and final['finish_eligible'] and final['citation_checked']
    assert final['version'] == '2' and final['profile'] == B1_PROFILE_REVISION


def test_no_obligations_no_observations_is_not_scientific_finish(build):
    assert not evaluate(build(), evidence_ids=[])['finish_eligible']


def test_earliest_qualifier_inside_closure_and_equal_smiles_not_ancestor(build):
    case = build(requirements=requirements(molecular_results=[dict(tool_name='property_calculator')]))
    earlier = execute(case, decision())
    chosen = execute(case, decision())
    later = execute(case, decision())
    report = evaluate(case, evidence_ids=[eid(later), eid(chosen)])
    assert report['finish_eligible']
    assert report['cited_closure_ids'] == [eid(chosen), eid(later)]
    assert report['required_evidence_ids'] == [eid(chosen)]
    assert eid(earlier) not in report['required_evidence_ids']


def test_target_citation_expands_only_sealed_reverse_property_ancestors(build, sources):
    reverse = sources('reverse')
    case = build(tools=[PropertyCalculator(), reverse.tool, target_tool()], requirements=requirements(
        molecular_results=[dict(tool_name='property_calculator')],
        reverse_result=dict(expected_smiles='CCO'), target_result=dict(input='reverse', require_resolved=True)))
    unrelated = execute(case, decision())
    prop = execute(case, decision())
    rev = execute(case, decision('reverse_target_predictor', eid(prop)))
    handle = owned(case, lambda: case.resolver.record_descriptors(eid(rev)))[0]['record_ref']
    target = execute(case, decision('target_database_search', eid(rev), record_ref=handle))
    assert target.success
    report = evaluate(case, evidence_ids=[eid(target)])
    assert report['finish_eligible']
    assert report['cited_closure_ids'] == report['required_evidence_ids'] == [eid(prop), eid(rev), eid(target)]
    assert eid(unrelated) not in report['required_evidence_ids']


@pytest.mark.parametrize('explicit', [False, True])
def test_all_seven_tools_groups_and_explicit_only(build, sources, explicit):
    rag, reverse = sources('rag'), sources('reverse')
    query = 'target: PDE5A; SMILES: CCO'
    groups = dict(molecular_results=[dict(tool_name=name) for name in
        ('property_calculator', 'drug_likeness_assessment')],
        analysis_results=[dict(tool_name='admet_predictor'), dict(tool_name='activity_predictor', target='PDE5A')],
        reverse_result=dict(expected_smiles='CCO'), retrieval_result=dict(query=query),
        target_result=dict(input='reverse', require_resolved=True))
    case = build(query, tools=[PropertyCalculator(), DrugLikenessAssessment(), admet_tool(),
        Recorder(dict(success=True, data=[family_row()])), rag.tool, reverse.tool, target_tool()],
        requirements=requirements(**({} if explicit else groups)))
    results = [execute(case, decision(name)) for name in (
        'property_calculator', 'drug_likeness_assessment', 'admet_predictor', 'activity_predictor',
        'rag_search', 'reverse_target_predictor')]
    handle = owned(case, lambda: case.resolver.record_descriptors(eid(results[-1])))[0]['record_ref']
    results.append(execute(case, decision('target_database_search', eid(results[-1]), record_ref=handle)))
    assert all(r.success for r in results)
    report = evaluate(case, required_tools=sorted(B1_TOOLS) if explicit else (), evidence_ids=[eid(r) for r in results])
    assert report['finish_eligible'] and report['satisfied']
    assert {c['tool_name'] for c in report['checks']} == B1_TOOLS
    assert report['missing_required_tools'] == []


@pytest.mark.parametrize('name', ['property_calculator', 'drug_likeness_assessment', 'admet_predictor', 'activity_predictor'])
@pytest.mark.parametrize('wrong', ['subject', 'missing', 'duplicate'])
def test_actual_action_whole_subjects_required_even_without_group(build, name, wrong):
    smiles = ('CCC', 'CCN') if wrong == 'subject' else ('CCO',) if wrong == 'missing' else ('CCO', 'CCO')
    if name in ('property_calculator', 'drug_likeness_assessment'):
        tool = CountingTool(name, dict(success=True, data=analysis_rows(name, smiles)))
    elif name == 'activity_predictor':
        tool = Recorder(dict(success=True, data=[family_row(smiles=s) for s in smiles]))
    else:
        tool = admet_tool()
        tool.raw['data'] = [dict(deepcopy(tool.raw['data'][0]), smiles=s) for s in smiles]
    case = build('target: PDE5A; SMILES: CCO; SMILES: CCN', tools=[tool])
    result = execute(case, decision(name))
    assert result.success, 'Direct wrong subjects should reach the acceptance boundary sealed'
    report = evaluate(case, required_tools=[name], evidence_ids=[eid(result)])
    assert not report['satisfied'] and not report['finish_eligible']
    assert name in report['missing_required_tools']


@pytest.mark.parametrize('require_hits', [False, True])
def test_real_empty_rag_obligation_toggle(build, sources, require_hits):
    rag = sources('rag', empty=True)
    case = build(rag.query, tools=[rag.tool], requirements=requirements(
        retrieval_result=dict(query=rag.query, require_hits=require_hits)))
    result = execute(case, decision('rag_search'))
    assert result.success and result.data == []
    assert evaluate(case, evidence_ids=[eid(result)])['finish_eligible'] is (not require_hits)


@pytest.mark.parametrize('empty', [False, True])
def test_sparse_admet_presence_not_unknown_as_low_risk(build, empty):
    case = build(tools=[admet_tool(empty=empty)])
    result = execute(case, decision('admet_predictor'))
    assert result.success
    report = evaluate(case, required_tools=['admet_predictor'], evidence_ids=[eid(result)])
    assert report['finish_eligible'] is (not empty)
    text = render(case, [eid(result)], required_tools=['admet_predictor'])
    assert 'low risk' not in text.lower()
    if not empty:
        assert '"pains": null' in text and 'adme_py' in text


@pytest.mark.parametrize('require_resolved', [False, True])
def test_target_not_found_execution_only(build, require_resolved):
    case = build('EGFR', tools=[target_tool('not_found')], requirements=requirements(
        target_result=dict(input='user', query='EGFR', require_resolved=require_resolved)))
    result = execute(case, decision('target_database_search'))
    assert result.success and result.data is None
    assert result.quality['lookup_status'] == 'not_found'
    assert evaluate(case, evidence_ids=[eid(result)])['finish_eligible'] is (not require_resolved)


@pytest.mark.parametrize('kind', ['family_alias', 'conflicting_family', 'single_unproven'])
def test_activity_target_bound_provenance(build, kind):
    row = single_row() if kind == 'single_unproven' else family_row()
    if kind == 'conflicting_family':
        row['family_id'] = 'buche-family'
        for model in row['provenance']['models'].values():
            model['target_id'] = 'buche-family'
    case = build('target: PDE; SMILES: CCO', tools=[Recorder(dict(success=True, data=[row]))])
    result = execute(case, decision('activity_predictor'))
    assert result.success
    report = evaluate(case, required_tools=['activity_predictor'], evidence_ids=[eid(result)])
    assert report['finish_eligible'] is (kind == 'family_alias')
    if kind == 'single_unproven':
        assert 'insufficient_target_evidence' in report['reason_codes']


def test_family_disagreement_cited_raw_data_is_partial_never_success(build):
    row = family_row(success=False, status='partial', execution_status='passed',
                     predicted_pIC50=6.0, classification_regression_consistent=False)
    # Real family producer emits a canonical ToolResult with no error. A legacy
    # false dict instead acquires a structured failure in execute_tool_compat.
    raw = ToolResult('activity_predictor', False, 'synthetic family review',
                     status=ObservationStatus.PARTIAL, data=[row])
    case = build('target: PDE5A; SMILES: CCO', tools=[Recorder(raw)])
    result = execute(case, decision('activity_predictor'))
    from src.agent.harness.decision_policy import family_review_observation
    assert family_review_observation(result) and result.error is None
    report = evaluate(case, required_tools=['activity_predictor'], evidence_ids=[eid(result)])
    assert not report['finish_eligible'] and not report['satisfied']
    text = render(case, [eid(result)], required_tools=['activity_predictor'])
    assert 'partial' in text and '"predicted_pIC50": 6.0' in text
    assert '"scientific_usable": false' in text
    assert 'predicted_pIC50' not in render(case, [], required_tools=['activity_predictor'])


@pytest.mark.parametrize('kind', ['rag', 'reverse'])
@pytest.mark.parametrize('change', ['none', 'close', 'replacement'])
def test_checks_never_rerun_and_stale_sources_fail_closed(build, sources, monkeypatch, kind, change):
    source = sources(kind)
    query = source.query if kind == 'rag' else 'SMILES: CCO'
    case = build(query, tools=[source.tool])
    result = execute(case, decision(source.tool.name))
    forbidden = forbid_work(source, monkeypatch)
    before = deepcopy(result)
    for _ in range(2):
        assert evaluate(case, required_tools=[source.tool.name], evidence_ids=[eid(result)])['finish_eligible']
    if change == 'close':
        source.source.close() if kind == 'rag' else source.source.close_strict()
    elif change == 'replacement':
        replacement = sources(kind)
        setattr(source.tool, 'rag_system' if kind == 'rag' else '_predictor', replacement.source)
    if change != 'none':
        with pytest.raises((ValueError, DecisionBoundaryError)):
            evaluate(case, evidence_ids=[eid(result)])
        with pytest.raises((ValueError, DecisionBoundaryError)):
            render(case, [eid(result)])
    assert result == before and forbidden == [] and len(case.calls[source.tool.name]) == 1


@pytest.mark.parametrize('mode', ['foreign', 'forged', 'tampered', 'owner', 'requirements'])
def test_untrusted_closure_fails_closed(build, mode):
    case = build()
    result = execute(case, decision())
    ids = [eid(result)]
    if mode == 'foreign':
        other = build(session_id='foreign')
        ids = [eid(execute(other, decision()))]
    elif mode == 'forged':
        ids = ['evidence-forged']
    elif mode == 'tampered':
        result.data[0]['properties']['logp'] = 123.0
    elif mode == 'owner':
        case.session.context = replace(case.context, session_id='foreign')
    else:
        case.resolver.requirements = case.resolver.requirements.model_copy(update={'forbidden_tools': ('property_calculator',)})
    with pytest.raises((ValueError, DecisionBoundaryError)):
        evaluate(case, evidence_ids=ids)


@pytest.mark.parametrize('argument,value', [
    ('required_tools', None), ('required_tools', 'property_calculator'),
    ('required_tools', ['property_calculator'] * 33), ('required_tools', ['property_calculator'] * 2),
    ('required_tools', ['unknown']), ('required_tools', [True]), ('required_tools', ['x' * 100000]),
    ('evidence_ids', ()), ('evidence_ids', {}), ('evidence_ids', [True]),
    ('evidence_ids', ['x'] * 129), ('evidence_ids', ['x', 'x']),
    ('evidence_ids', ['bad/id']), ('evidence_ids', ['x' * 129]),
])
def test_invalid_native_arguments_rejected_before_resolver_work(build, monkeypatch, argument, value):
    case = build()
    monkeypatch.setattr(case.resolver, 'verify_binding_closure', lambda *a, **k: pytest.fail('invalid input reached resolver'))
    with pytest.raises((ValueError, DecisionBoundaryError)):
        evaluate(case, **{argument: value})


def test_actual_resolver_type_required(build):
    case = build()
    with pytest.raises((ValueError, DecisionBoundaryError)):
        api().evaluate_binding_acceptance(object())
    class Foreign(type(case.resolver)):
        pass
    with pytest.raises((ValueError, DecisionBoundaryError)):
        api().evaluate_binding_acceptance(object.__new__(Foreign))


def test_report_native_detached_bounded_and_render_excludes_uncited_numbers(build, sources):
    rag = sources('rag')
    case = build(tools=[PropertyCalculator(), rag.tool])
    prop = execute(case, decision())
    retrieved = execute(case, decision('rag_search'))
    ids = [eid(retrieved)]
    before = deepcopy(case.session.results)
    report = evaluate(case, required_tools=['property_calculator'], evidence_ids=ids)
    assert len(json.dumps(report, ensure_ascii=False).encode()) <= 65536
    assert json.loads(json.dumps(report)) == report
    report['checks'].clear()
    report['cited_closure_ids'].append('forged')
    again = evaluate(case, required_tools=['property_calculator'], evidence_ids=ids)
    assert again['checks'] and again['cited_closure_ids'] == ids
    text = render(case, ids)
    assert str(prop.data[0]['properties']['molecular_weight']) not in text
    assert eid(prop) not in text and 'raw_result' not in text
    assert case.session.results == before


def test_earliest_qualifying_cited_observation_skips_not_found(build):
    # Honest not_found then resolved from the test-owned service, same input.
    lookup = target_tool('not_found')
    case = build('EGFR', tools=[lookup], requirements=requirements(
        target_result=dict(input='user', query='EGFR', require_resolved=True)))
    earlier = execute(case, decision('target_database_search'))
    lookup._service.status = 'resolved'
    later = execute(case, decision('target_database_search'))
    assert earlier.success and later.success
    report = evaluate(case, evidence_ids=[eid(earlier), eid(later)])
    assert report['satisfied'] and report['finish_eligible']
    assert report['required_evidence_ids'] == [eid(later)]


def test_rdkit_rules_admet_available_methods_is_not_model_evidence(build):
    rows = analysis_rows('admet_predictor')  # actual deterministic RDKit rules
    tool = CountingTool('admet_predictor', dict(success=True, data=rows))
    case = build(tools=[tool], requirements=requirements(analysis_results=[dict(tool_name=tool.name)]))
    result = execute(case, decision(tool.name))
    assert evaluate(case, evidence_ids=[eid(result)])['finish_eligible']
    text = render(case, [eid(result)])
    assert 'rdkit_rules' in text and 'backend_version' in text
    assert '"pains": null' in text


def test_false_lipinski_metric_is_present_not_missing(build):
    tool = DrugLikenessAssessment()
    case = build('SMILES: ' + 'C' * 45, tools=[tool], requirements=requirements(molecular_results=[dict(
        tool_name=tool.name, required_metrics=['lipinski_compliant', 'qed', 'logp'])]))
    result = execute(case, decision(tool.name))
    assert result.success and result.data[0]['assessment']['lipinski_rule_of_five']['compliance'] is False
    assert evaluate(case, evidence_ids=[eid(result)])['finish_eligible']


@pytest.mark.parametrize('mode', ['missing_metric', 'nonfinite', 'demo', 'fallback', 'unavailable', 'error'])
def test_invalid_scientific_states_never_pass_explicit_tools(build, mode):
    raw = dict(success=True, data=analysis_rows('property_calculator'))
    if mode == 'missing_metric':
        del raw['data'][0]['properties']['logp']
    elif mode == 'nonfinite':
        raw['data'][0]['properties']['logp'] = float('nan')
    elif mode in ('demo', 'fallback'):
        raw['provenance'] = dict(tool_name='property_calculator',
            demo_mode=mode == 'demo', fallback_used=mode == 'fallback')
    elif mode == 'unavailable':
        raw.update(success=False, status='unavailable', data=None)
    else:
        raw.update(success=False, data=None, error='synthetic failure')
    case = build(tools=[CountingTool('property_calculator', raw)])
    result = execute(case, decision())
    report = evaluate(case, required_tools=['property_calculator'], evidence_ids=[eid(result)])
    assert not report['satisfied'] and not report['finish_eligible']
    assert '"data"' not in render(case, [eid(result)])


def test_optional_failure_not_hidden_by_success_citation(build):
    failed = CountingTool('drug_likeness_assessment', dict(success=False, error='fixture', data=None))
    case = build(tools=[PropertyCalculator(), failed])
    success = execute(case, decision())
    failure = execute(case, decision(failed.name))
    report = evaluate(case, required_tools=['property_calculator'], evidence_ids=[eid(success)])
    assert report['satisfied'] and not report['finish_eligible']
    blocks = json.loads(render(case, [eid(success)]))['observations']
    diagnostic = next(b for b in blocks if b['evidence_id'] == eid(failure))
    assert 'data' not in diagnostic and diagnostic['scientific_usable'] is False


def test_forbidden_tool_cannot_dispatch_or_satisfy_explicit_obligation(build):
    case = build(requirements=requirements(forbidden_tools=['drug_likeness_assessment']))
    with pytest.raises((ValueError, DecisionBoundaryError)):
        execute(case, decision('drug_likeness_assessment'))
    assert case.calls['drug_likeness_assessment'] == []
    result = execute(case, decision())
    report = evaluate(case, required_tools=['drug_likeness_assessment'], evidence_ids=[eid(result)])
    assert not report['satisfied'] and not report['finish_eligible']
    assert report['executed_forbidden_tools'] == []


def test_journal_current_head_and_expired_selection_checked(build, selections):
    store, selected = selections
    case = attach(build('计算性质', store=store, selected=selected[0]))
    result = execute(case, decision())
    assert evaluate(case, required_tools=['property_calculator'], evidence_ids=[eid(result)])['finish_eligible']
    store.update_run_status(selected[0].trace_id, 'failed')
    with pytest.raises((ValueError, DecisionBoundaryError)):
        evaluate(case, evidence_ids=[eid(result)])
    with pytest.raises((ValueError, DecisionBoundaryError)):
        render(case, [eid(result)])


def test_current_admitted_head_cannot_change_original_subject(build):
    case = attach(build('计算性质'))
    admit(case, 'SMILES: CCO')
    result = execute(case, decision())
    admit(case, 'target: PDE5A')
    assert evaluate(case, required_tools=['property_calculator'], evidence_ids=[eid(result)])['finish_eligible']
    admit(case, 'SMILES: CCC')
    with pytest.raises((ValueError, DecisionBoundaryError)):
        evaluate(case, evidence_ids=[eid(result)])


@pytest.mark.parametrize('argument', ['required_tools', 'evidence_ids'])
def test_native_container_subclasses_never_invoke_hooks(build, argument):
    class Hostile(list):
        def __len__(self):
            pytest.fail('non-native length executed')
        def __iter__(self):
            pytest.fail('non-native iterator executed')
    with pytest.raises((ValueError, DecisionBoundaryError)):
        evaluate(build(), **{argument: Hostile()})


def test_repeated_checks_do_not_calculate_or_mutate(build, monkeypatch):
    prop, likeness = PropertyCalculator(), DrugLikenessAssessment()
    case = build(tools=[prop, likeness])
    first = execute(case, decision())
    second = execute(case, decision(likeness.name, eid(first)))
    before = deepcopy(case.session.results)
    def forbidden(*a, **kw):
        pytest.fail('acceptance ran a scientific calculation')
    monkeypatch.setattr(prop, 'calculate_properties', forbidden)
    monkeypatch.setattr(likeness, 'assess_drug_likeness', forbidden)
    for _ in range(2):
        assert evaluate(case, required_tools=[prop.name, likeness.name], evidence_ids=[eid(second)])['finish_eligible']
        render(case, [eid(second)])
    assert case.session.results == before


@pytest.mark.parametrize('extra', [dict(target_id='PDE5A'),
    dict(target_id='PDE5A', scientific_readiness='endpoint_ready')])
def test_legacy_extra_target_id_cannot_upgrade_missing_model_target_proof(build, extra):
    row = single_row()
    row['model_provenance'].update(extra)
    case = build('target: PDE5A; SMILES: CCO', tools=[Recorder(dict(success=True, data=[row]))])
    result = execute(case, decision('activity_predictor'))
    assert result.success  # the typed legacy boundary intentionally allows extras
    report = evaluate(case, required_tools=['activity_predictor'], evidence_ids=[eid(result)])
    assert not report['satisfied'] and not report['finish_eligible']
    assert 'insufficient_target_evidence' in report['reason_codes']
    block = json.loads(render(case, [eid(result)]))['observations'][0]
    assert 'data' not in block and block['scientific_usable'] is False


@pytest.mark.parametrize('mode', ['complete', 'target_mismatch', 'missing_card_digest', 'wrong_endpoint_key'])
def test_single_model_reuses_complete_endpoint_target_contract_without_assets(build, monkeypatch, mode):
    from tests.activity_test_support import endpoint_metadata
    from src.activity import model_registry, predictor
    metadata = endpoint_metadata('synthetic-acceptance-model', target_id='pde5a')
    if mode == 'target_mismatch':
        metadata = endpoint_metadata('synthetic-acceptance-model', target_id='bche')
    elif mode == 'missing_card_digest':
        del metadata['model_card_sha256']
    elif mode == 'wrong_endpoint_key':
        metadata['endpoint_key'] = 'bche:pic50:pic50:regression'
    row = single_row(endpoint='pIC50', units='pIC50', model_provenance=metadata)
    case = build('target: PDE5A; SMILES: CCO', tools=[Recorder(dict(success=True, data=[row]))])
    result = execute(case, decision('activity_predictor'))
    assert result.success
    def forbidden(*a, **kw):
        pytest.fail('acceptance tried to initialize model/registry or load weights')
    monkeypatch.setattr(model_registry.ActivityModelRegistry, '__init__', forbidden)
    monkeypatch.setattr(predictor.ActivityPredictor, '__init__', forbidden)
    validated = []
    validate = model_registry.validate_endpoint_metadata
    def counted(value):
        validated.append(True)
        return validate(value)
    monkeypatch.setattr(model_registry, 'validate_endpoint_metadata', counted)
    report = evaluate(case, required_tools=['activity_predictor'], evidence_ids=[eid(result)])
    assert report['finish_eligible'] is (mode == 'complete')
    assert validated, 'existing endpoint proof validator must own the target metadata checks'
    if mode == 'target_mismatch':
        assert 'activity_target_mismatch' in report['reason_codes']
    elif mode != 'complete':
        assert 'insufficient_target_evidence' in report['reason_codes']


@pytest.mark.parametrize('name', sorted(B1_TOOLS))
def test_renderer_preserves_cited_typed_provenance_artifacts_and_warnings(build, sources, monkeypatch, name):
    if name == 'property_calculator':
        tool, query = PropertyCalculator(), 'SMILES: CCO'
    elif name == 'drug_likeness_assessment':
        tool, query = DrugLikenessAssessment(), 'SMILES: CCO'
    elif name == 'admet_predictor':
        tool, query = admet_tool(), 'SMILES: CCO'
    elif name == 'activity_predictor':
        tool, query = Recorder(dict(success=True, data=[family_row()])), 'target: PDE5A; SMILES: CCO'
    elif name == 'target_database_search':
        tool, query = target_tool(), 'EGFR'
    else:
        source = sources('rag' if name == 'rag_search' else 'reverse')
        tool = source.tool
        query = source.query if name == 'rag_search' else 'SMILES: CCO'
    original = tool.execute
    provenance = ToolProvenance(name, model_name='synthetic-contract-source', model_version='fixture-v1')
    artifact = WorkflowArtifact('report', 'reports/synthetic-observation.json', 'Synthetic report',
        'application/json', metadata={'raw_result': {'provisional_value': 987654.321}, 'debug': 'PRIVATE-DEBUG'})
    def annotated(payload):
        raw = original(payload)
        assert type(raw) is dict
        raw['provenance'] = provenance.to_dict()
        raw['artifacts'] = [artifact.to_dict()]
        raw['warnings'] = ['synthetic scientific scope warning']
        return raw
    monkeypatch.setattr(tool, 'execute', annotated)
    case = build(query, tools=[tool])
    result = execute(case, decision(name))
    assert result.success
    before = deepcopy(result)
    text = render(case, [eid(result)], required_tools=[name])
    block = json.loads(text)['observations'][0]
    assert block['provenance'] == result.provenance.to_dict()
    assert block['artifacts'] == [dict(artifact_type='report', path=artifact.path,
                                     label=artifact.label, mime_type=artifact.mime_type)]
    assert 'synthetic scientific scope warning' in block['warnings']
    assert 'PRIVATE-DEBUG' not in text and '987654.321' not in text and 'raw_result' not in text
    assert len(text.encode('utf-8')) <= 65536 and result == before


def test_renderer_preserves_safe_uncited_failure_warnings_not_provisional_claims(build):
    failure = CountingTool('drug_likeness_assessment', dict(success=False, error='diagnostic fixture',
        warnings=['partial_authoritative_results', 'Predicted provisional logP = 987654.321',
                  '暂定预测值为九点九', 'unrecognized diagnostic text'],
        artifacts=[dict(artifact_type='report', path='reports/uncited-provisional.json',
                        label='provisional 987654.321')], data=None))
    case = build(tools=[PropertyCalculator(), failure])
    prop = execute(case, decision())
    failed = execute(case, decision(failure.name))
    before = deepcopy(case.session.results)
    text = render(case, [eid(prop)])
    block = next(b for b in json.loads(text)['observations'] if b['evidence_id'] == eid(failed))
    assert 'partial_authoritative_results' in block['warnings']
    assert block['warning_details_omitted'] >= 3
    assert '987654.321' not in text and '九点九' not in text
    assert 'unrecognized diagnostic text' not in text
    assert 'data' not in block and block['artifacts'] == []
    assert block['provenance']['tool_name'] == failure.name
    assert case.session.results == before


def test_cited_family_review_preserves_provenance_artifact_and_stage_diagnostics(build):
    conflict = family_row(success=False, status='partial', execution_status='passed',
                         predicted_pIC50=6.0, classification_regression_consistent=False)
    partial = family_row(smiles='CCN', success=False, status='partial', execution_status='partial',
                        predicted_pIC50=None, classification_regression_consistent=None,
                        errors={'regression': 'synthetic stage unavailable'})
    artifact = WorkflowArtifact('report', 'reports/family-review.json', 'Synthetic review', 'application/json')
    raw = ToolResult('activity_predictor', False, 'synthetic review', status=ObservationStatus.PARTIAL,
        data=[conflict, partial], warnings=['synthetic family limitation'], artifacts=[artifact])
    case = build('target: PDE5A; SMILES: CCO; SMILES: CCN', tools=[Recorder(raw)])
    result = execute(case, decision('activity_predictor'))
    block = json.loads(render(case, [eid(result)], required_tools=['activity_predictor']))['observations'][0]
    assert block['scientific_usable'] is False and block['status'] == 'partial'
    assert block['provenance'] == result.provenance.to_dict()
    assert block['artifacts'][0]['path'] == artifact.path
    assert 'synthetic family limitation' in block['warnings']
    assert block['data'][1]['errors'] == partial['errors']
    assert block['data'][0]['warnings'] == conflict['warnings']


def test_cited_family_conflict_with_unsupported_sibling_preserves_empty_provenance(build, monkeypatch):
    from src.activity import prediction_service
    from src.activity.family_predictor import FamilyActivityPredictor
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool

    # Real family canonicalization, row construction, service/tool and Session;
    # only model stages/pair metadata are synthetic. No registry/assets loaded.
    bundle = deepcopy(family_row()['provenance'])
    bundle.update(family_id='pde-family', assignment_sha256='e' * 64,
                  scope='synthetic-test-only', label_threshold=5.0, probability_threshold=0.5)
    calls = []

    class Stage:
        def __init__(self, task):
            self.task = task

        def predict(self, smiles):
            calls.append((self.task, list(smiles)))
            classification = self.task == 'classification'
            return [dict(smiles=s, success=True, task_type=self.task,
                         endpoint='activity' if classification else 'pIC50',
                         units='probability' if classification else 'pIC50',
                         **({'probability': 0.0} if classification else {'value': 6.0}))
                    for s in smiles]

    predictor = FamilyActivityPredictor(None)
    monkeypatch.setattr(predictor, '_pair', lambda family: (bundle, {
        task: Stage(task) for task in ('classification', 'regression')}))
    monkeypatch.setattr(prediction_service, 'get_family_predictor', lambda: predictor)
    case = build('target: PDE5A; SMILES: CCO; SMILES: C', tools=[ActivityPredictorTool()])
    result = execute(case, decision('activity_predictor'))
    assert calls == [('classification', ['CCO']), ('regression', ['CCO'])]
    assert result.status is ObservationStatus.PARTIAL and result.error is None
    conflict, rejected = result.data
    assert conflict['classification_regression_consistent'] is False
    assert rejected['errors'] == {'input': 'unsupported_structure'}
    assert rejected['status'] == 'failed' and rejected['provenance'] == {}
    before = deepcopy(case.session.results)
    report = evaluate(case, required_tools=['activity_predictor'], evidence_ids=[eid(result)])
    assert not report['satisfied'] and not report['finish_eligible']

    rendered = json.loads(render(case, [eid(result)], required_tools=['activity_predictor']))
    block = rendered['observations'][0]
    assert not rendered['acceptance']['finish_eligible'] and not block['scientific_usable']
    assert block['status'] == 'partial' and 'review_message' in block
    assert block['provenance'] == result.provenance.to_dict()
    shown, failed = block['data']
    assert shown['activity_probability'] == 0.0 and shown['predicted_pIC50'] == 6.0
    assert shown['warnings'] == conflict['warnings']
    assert set(shown['provenance']['models']) == {'classification', 'regression'}
    assert failed['smiles'] == 'C' and failed['status'] == failed['execution_status'] == 'failed'
    assert failed['provenance'] == {} and failed['errors'] == rejected['errors']
    assert failed['activity_probability'] is None and failed['predicted_pIC50'] is None
    assert case.session.results == before
    assert calls == [('classification', ['CCO']), ('regression', ['CCO'])]


def test_single_model_untyped_family_extras_do_not_become_display_diagnostics(build):
    from tests.activity_test_support import endpoint_metadata
    row = single_row(endpoint='pIC50', units='pIC50',
        model_provenance=endpoint_metadata('synthetic-render-model', target_id='pde5a'),
        errors=['OPAQUE-ROW-DEBUG'], provenance={'raw_result': 'OPAQUE-ROW-DEBUG'},
        warnings=['OPAQUE-ROW-DEBUG'], requested_target='OPAQUE-ROW-DEBUG', bundle_id='OPAQUE-ROW-DEBUG')
    case = build('target: PDE5A; SMILES: CCO', tools=[Recorder(dict(success=True, data=[row]))])
    result = execute(case, decision('activity_predictor'))
    assert evaluate(case, required_tools=['activity_predictor'], evidence_ids=[eid(result)])['finish_eligible']
    text = render(case, [eid(result)])
    assert 'OPAQUE-ROW-DEBUG' not in text
    projected = json.loads(text)['observations'][0]['data'][0]
    assert 'errors' not in projected and 'warnings' not in projected and 'provenance' not in projected
    assert projected['model_provenance']['scientific_readiness'] == 'endpoint_ready'


def test_optional_untyped_family_source_metadata_never_copies_debug_containers(build):
    row = family_row()
    # The existing family domain contract validates model hashes, not this
    # optional source extension. It must not be a raw-debug rendering tunnel.
    row['provenance']['source_sha256'] = {'raw_result': 'OPAQUE-SOURCE-DEBUG'}
    case = build('target: PDE5A; SMILES: CCO', tools=[Recorder(dict(success=True, data=[row]))])
    result = execute(case, decision('activity_predictor'))
    assert result.success
    text = render(case, [eid(result)])
    assert 'OPAQUE-SOURCE-DEBUG' not in text and 'raw_result' not in text
    assert 'source_sha256' not in json.loads(text)['observations'][0]['data'][0]['provenance']
