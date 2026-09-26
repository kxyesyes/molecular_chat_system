"""Task2 admission contracts only; no Web/core execution or scientific evidence.

Initial RED observed blocked B science before adding the optional server seam,
NOT an unexpected-keyword TypeError. All tests now require the explicit seam.
All data are literal synthetic requests; no models, source assets or stores here.
"""
import importlib
import json
from dataclasses import replace

import pytest


B1 = 'ordinary-semantic-b1-v1'
RAG = '使用 RAG 检索本地分子库中与乙醇相关的分子'
ADMET = '评估 ADMET；SMILES: CCO'
REVERSE = '反向寻靶并查询返回的一个靶点；SMILES: CCO'
TOOLS = frozenset({'property_calculator', 'drug_likeness_assessment',
    'activity_predictor', 'target_database_search', 'admet_predictor',
    'reverse_target_predictor', 'rag_search'})


def api():
    return importlib.import_module('src.web.decision_request')


def server_call(function, *args, profile=B1, **kwargs):
    return function(*args, binding_profile=profile, **kwargs)


def envelope(query, *, profile=B1, **options):
    return server_call(api().validate_request_envelope,
        {'message': query, **options}, profile=profile, session_id='b1-owner',
        trace_id='b1-admission', config_generation='generation-1')


def assess(request, *, profile=B1):
    return server_call(api().assess_whole_request, request, profile=profile)


def snapshot(request, *, profile=B1, **changes):
    from src.web.ordinary_capabilities import build_capability_snapshot, ORIGINAL_FOUR
    kwargs = dict(registered_names=TOOLS if profile == B1 else ORIGINAL_FOUR,
        permitted_names=TOOLS if profile == B1 else ORIGINAL_FOUR,
        provider_descriptor={'provider': 'offline', 'model': 'test-model', 'mode': 'json'},
        semantic_profile=True, intent_capable=True, original_four_profile=profile != B1,
        scientific_tools=request.enable_tools, model_generation='generation-1',
        capability_generation='capability-1')
    if profile == B1:
        kwargs.update(binding_profile=B1, wired_names=TOOLS, rag_enabled=request.enable_rag)
    kwargs.update(changes)
    return build_capability_snapshot(**kwargs)


def proposal(kind='capability', relation='none'):
    from src.agent.contracts.ordinary_intent import OrdinaryIntent
    return OrdinaryIntent(version='1', kind=kind, history_relation=relation, unresolved=False)


def admit(request, intent=None, *, profile=B1, history=None, references=None, view=None):
    return server_call(api().prepare_with_intent, request, assess(request, profile=profile),
        intent, profile=profile, history=[] if history is None else history,
        capability_snapshot=snapshot(request, profile=profile) if view is None else view,
        intent_requests=0 if intent is None else 1, references=references)


def science(query, **options):
    request = envelope(query, **options)
    assessment = assess(request)
    assert assessment.kind == 'known_scientific', assessment
    prepared, raw = admit(request)
    assert prepared.request_kind == 'scientific'
    assert prepared.context.query == query
    assert prepared.required_tools == prepared.allowed_tools <= TOOLS
    assert prepared.requirements.version == '2'
    assert prepared.requirements.profile == B1
    binding = json.loads(raw)
    assert binding['profile_revision'] == B1
    assert binding['assessment_revision'] == 'whole-request-b-v1'
    assert binding['intent_requests'] == 0
    return prepared


def blocked(query, **options):
    request = envelope(query, **options)
    assessment = assess(request)
    assert assessment.kind == 'blocked', assessment
    # Even an affirmative intent proposal must not turn a blocked whole request
    # into partial obligations/chat. This calls admission, not an execution mock.
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, proposal('conversation'))
    return request, assessment


@pytest.mark.parametrize('profile', [None, B1], ids=['original', 'b1'])
def test_b1_available_method_admet(profile):
    request = envelope(ADMET, profile=profile)
    assessment = assess(request, profile=profile)
    if profile is None:
        assert assessment.kind == 'blocked'
        assert assessment.reason == 'unsupported_scientific_request'
        return
    assert assessment.kind == 'known_scientific', assessment
    prepared = science(ADMET)
    assert prepared.required_tools == {'admet_predictor'}
    obligation, = prepared.requirements.analysis_results
    assert obligation.tool_name == 'admet_predictor'
    assert obligation.scope == 'available_methods'
    assert obligation.expected_smiles == ('CCO',)
    assert obligation.exact_molecule_count == 1
    assert prepared.requirements.molecular_results == ()


def test_b1_reverse_target_linkage():
    prepared = science(REVERSE)
    assert prepared.required_tools == {'reverse_target_predictor', 'target_database_search'}
    assert prepared.requirements.reverse_result.expected_smiles == 'CCO'
    target = prepared.requirements.target_result
    assert target.input == 'reverse'
    assert target.query is None
    assert target.require_resolved is False
    # Obligations contain no action ordering or invented row/target handle.
    assert set(prepared.requirements.model_dump()) == {'version', 'profile', 'forbidden_tools',
        'molecular_results', 'analysis_results', 'reverse_result', 'retrieval_result', 'target_result'}


def test_b1_local_rag_permission():
    prepared = science(RAG, enable_tools=False, enable_rag=True, rag_count=3)
    assert prepared.required_tools == {'rag_search'}
    retrieval = prepared.requirements.retrieval_result
    assert retrieval.query == RAG and retrieval.k == 3 and retrieval.require_hits is False
    assert prepared.requirements.target_result is None
    assert prepared.requirements.reverse_result is None
    assert prepared.requirements.molecular_results == prepared.requirements.analysis_results == ()
    blocked(RAG, enable_tools=True, enable_rag=False, rag_count=3)
    blocked(ADMET, enable_tools=False, enable_rag=True)


@pytest.mark.parametrize('count', [None, 3, 5, 10])
def test_b1_rag_count_not_silently_reduced(count):
    options = {} if count is None else {'rag_count': count}
    request = envelope(RAG, **options)
    assert request.rag_count == (3 if count is None else count)
    checked = server_call(api()._validated_envelope, request)
    assert checked == request and checked.rag_count == request.rag_count
    if count in (None, 3):
        assert science(RAG, **options).requirements.retrieval_result.k == 3
    else:
        _, assessment = blocked(RAG, **options)
        assert assessment.reason == 'request_clarification_required'
    # A retrieval preference/count is not itself a request to do retrieval.
    prepared, _ = admit(envelope('你好', **options))
    assert prepared.request_kind == 'chat' and not prepared.required_tools
    assert prepared.context.metadata['rag_count'] == request.rag_count


@pytest.mark.parametrize('suffix', ['，返回五个', '，返回5个', '，返回10个',
    '，必须返回三个命中', '；然后计算这些分子的 ADMET', '；搜索 PubMed 文献'])
def test_b1_rag_unconsumed_count_or_action_is_not_query_data(suffix):
    blocked(RAG + suffix, rag_count=3)


@pytest.mark.parametrize('query', [
    '评估 ADMET 和熔点；SMILES: CCO',
    '评估 ADMET；SMILES: CCO；然后对接这个分子',
    '评估 ADMET；SMILES: CCO；生成五个候选并排序',
    '评估 ADMET；SMILES: CCO；搜索并引用外部文献',
    '评估所有 ADMET 终点；SMILES: CCO',
    '评估 ADMET 和毒性；SMILES: CCO',
    '反向寻靶，阈值0.8；SMILES: CCO',
    '反向寻靶，top_k=5；SMILES: CCO',
    '反向寻靶并查询最高分靶点；SMILES: CCO',
    '反向寻靶并查询人类靶点；SMILES: CCO',
    '评估 ADMET 但不要使用工具；SMILES: CCO',
    '评估 ADMET；SMILES: CCO；焚烧这个文件',
    '评估 ADMET；SMILES: CCO；融化这个样品',
    '解释 ADMET；评估 ADMET；SMILES: CCO',
])
def test_b1_full_clause_coverage(query):
    blocked(query)


@pytest.mark.parametrize('query,tools_enabled', [
    ('解释 logP\n对接这个分子', False),
    ('计算 CCO 的分子量和熔点', True),
    ('计算性质（禁用 property_calculator）；SMILES: CCO', True),
    ('分析两种化合物的性质；SMILES: CCO', True),
])
@pytest.mark.parametrize('profile', [None, B1], ids=['original', 'b1'])
def test_b1_preserves_four_parent_a1_counterexamples(query, tools_enabled, profile):
    request = envelope(query, profile=profile, enable_tools=tools_enabled)
    assert assess(request, profile=profile).kind == 'blocked'
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, proposal('conversation'), profile=profile)


@pytest.mark.parametrize('subjects', [('CCO',), ('OCC', 'CCN')])
def test_b1_same_subject_obligations(subjects):
    query = '评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: ' + '; '.join(subjects)
    prepared = science(query)
    assert prepared.required_tools == {'property_calculator', 'drug_likeness_assessment',
        'admet_predictor', 'activity_predictor'}
    groups = (*prepared.requirements.molecular_results, *prepared.requirements.analysis_results)
    assert len(groups) == 4
    for group in groups:
        assert group.expected_smiles == subjects
        assert group.exact_molecule_count == len(subjects)
    indexed = {group.tool_name: group for group in groups}
    assert indexed['activity_predictor'].target == 'PDE5A'
    assert indexed['admet_predictor'].scope == 'available_methods'
    assert indexed['property_calculator'].required_metrics == ('molecular_weight',)
    assert indexed['drug_likeness_assessment'].required_metrics == ('lipinski_compliant',)


@pytest.mark.parametrize('query', [
    '评估 CCO 的 ADMET 和 CCN 的类药性',
    '评估两个分子的 ADMET；SMILES: CCO',
    '评估 ADMET；SMILES: CCO; OCC',
    '评估 ADMET；SMILES: CCO broken',
    '评估 ADMET；SMILES: CC(C)((',
    '评估 PDE5A 和 BuChE 活性；SMILES: CCO',
    '评估 UNKNOWN 活性；SMILES: CCO',
    '反向寻靶；SMILES: CCO; CCN',
    '评估 CCO 的 ADMET 和刚才分子的类药性',
])
def test_b1_divergent_or_invalid_subject_target_count_rejected(query):
    blocked(query)


class NoReferenceResolution:
    def resolve(self, *args, **kwargs):
        pytest.fail('ordinary/retrieval/target-only request must not resolve molecular reference')


@pytest.mark.parametrize('query,kind,history', [
    ('你好，我想了解一下这个系统能做什么？', 'capability', []),
    ('你好，我只是想了解这个系统能做什么。请不要调用任何科研计算工具。', 'capability', []),
    ('能再用更简单的话说一遍吗？', 'conversation',
        [{'user': '你好', 'assistant': '你好，很高兴与你交流。'}]),
])
def test_b1_ordinary_empty_authority(query, kind, history):
    request = envelope(query, enable_tools=False, enable_rag=True, rag_count=10,
        reference={'untrusted': 'unused'})
    assert assess(request).kind == 'semantic_candidate'
    prepared, raw = admit(request, proposal(kind, 'prior_ordinary_turn' if history else 'none'),
        history=history, references=NoReferenceResolution())
    assert prepared.request_kind == 'chat'
    assert prepared.required_tools == prepared.allowed_tools == frozenset()
    assert prepared.requirements.version == '2'
    assert prepared.requirements.molecular_results == prepared.requirements.analysis_results == ()
    assert prepared.requirements.reverse_result is None
    assert prepared.requirements.target_result is None
    assert prepared.requirements.retrieval_result is None
    assert prepared.context.memory == [] and prepared.context.resolved_molecule is None
    assert json.loads(raw)['intent_requests'] == 1
    assert prepared.context.user_id == prepared.context.session_id == 'b1-owner'


@pytest.mark.parametrize('kind', ['scientific_execution', 'retrieval'])
def test_b1_intent_cannot_invent_obligations(kind):
    request = envelope('能用通俗的话说说这个平台的用途吗？')
    assert assess(request).kind == 'semantic_candidate'
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, proposal(kind))


@pytest.mark.parametrize('query,tool', [('预测活性', 'activity_predictor'),
    ('反向寻靶', 'reverse_target_predictor')])
def test_b1_missing_input_keeps_original_obligation(query, tool):
    prepared = science(query)
    assert prepared.required_tools == {tool}
    assert prepared.context.resolved_molecule is None
    if tool == 'activity_predictor':
        group, = prepared.requirements.analysis_results
        assert group.expected_smiles == () and group.exact_molecule_count is None
        assert group.target is None
    else:
        assert prepared.requirements.reverse_result is None


def test_b1_direct_target_is_authored_not_reverse_or_rag():
    query = '查询 EGFR 的靶点结构'
    prepared = science(query)
    assert prepared.required_tools == {'target_database_search'}
    target = prepared.requirements.target_result
    assert target.input == 'user' and target.query in (query, 'EGFR')
    assert target.require_resolved is False
    assert prepared.requirements.reverse_result is None
    assert prepared.requirements.retrieval_result is None


@pytest.mark.parametrize('field,value', [
    ('binding_profile', B1), ('profile', B1), ('admission_carry', {}),
    ('budget', {'max_model_requests': 16}), ('owner', 'other'),
    ('requirements', {'version': '2', 'profile': B1}), ('history', []),
    ('allowed_tools', ['rag_search']), ('capability_snapshot', {}),
    ('intent', {'kind': 'retrieval'}), ('session_id', 'other'),
])
def test_b1_browser_cannot_select_profile(field, value):
    with pytest.raises(api().DecisionAdmissionError, match='untrusted_request_field'):
        server_call(api().validate_request_envelope, {'message': RAG, field: value},
            session_id='b1-owner', trace_id='b1-admission', config_generation='generation-1')


@pytest.mark.parametrize('field', ['enable_tools', 'enable_rag'])
@pytest.mark.parametrize('value', [0, 1, 'true', None, []])
def test_b1_permission_flags_are_strict_booleans(field, value):
    with pytest.raises(api().DecisionAdmissionError, match='invalid_options'):
        envelope(RAG, **{field: value})


@pytest.mark.parametrize('profile', ['', 'b1', 'ordinary-semantic-b2-v1', True, {}])
def test_b1_server_profile_is_closed(profile):
    with pytest.raises(api().DecisionAdmissionError):
        envelope('你好', profile=profile)


def test_b1_snapshot_cannot_activate_profile_or_grant_disabled_permission():
    request = envelope('你好', profile=None)
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, profile=None, view=snapshot(request))
    request = envelope(ADMET)
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, view=snapshot(request, profile=None))
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, view=snapshot(request, permitted_names=TOOLS - {'admet_predictor'}))


@pytest.mark.parametrize('query', ['a' * 16384, '界' * 5461 + 'a',
    '"' * 8192 + 'a', '界' * 4000 + '"' * 3000],
    ids=['ascii-exact', 'unicode-exact', 'escaped', 'unicode-escaped'])
def test_b1_content_bound_not_serialized_query_bound(query):
    assert len(query.encode('utf-8')) <= 16384
    assert len(json.dumps(query, ensure_ascii=False).encode('utf-8')) > 16384
    assert len(json.dumps({'message': query}, ensure_ascii=False).encode('utf-8')) <= 24576
    request = envelope(query)
    assert request.query == query
    assert server_call(api()._validated_envelope, request) == request
    # Assessment revalidation must use the same bound without truncation. Full
    # intent transport and carry/digest-size composition belong to Tasks3/4.
    assessment = assess(request)
    from src.agent.evidence.ledger import EvidenceLedger
    assert assessment.query_digest == EvidenceLedger.output_digest(query)
    with pytest.raises(api().DecisionAdmissionError):
        envelope(query, profile=None)


@pytest.mark.parametrize('query', ['a' * 16385, '界' * 5461 + 'ab'], ids=['ascii', 'unicode'])
def test_b1_content_one_byte_over_rejected(query):
    assert len(query.encode('utf-8')) == 16385
    with pytest.raises(api().DecisionAdmissionError):
        envelope(query)


def test_b1_json_envelope_boundary_is_still_24_kib():
    payload = {'message': '你好', 'client_id': ''}
    overhead = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    options = {'client_id': 'a' * (24576 - overhead)}
    assert envelope('你好', **options).query == '你好'
    options['client_id'] += 'a'
    with pytest.raises(api().DecisionAdmissionError):
        envelope('你好', **options)


def test_b1_forged_envelope_revalidated():
    request = envelope(RAG, rag_count=3)
    for forged in (replace(request, enable_rag=1), replace(request, rag_count=True),
                   replace(request, query='a' * 16385)):
        with pytest.raises(api().DecisionAdmissionError):
            assess(forged)


def test_b1_history_pair_and_context_limits_remain_unchanged():
    request = envelope('能再说说吗？')
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, proposal('conversation', 'prior_ordinary_turn'),
            history=[{'user': 'a' * 16385, 'assistant': 'ok'}])
    from src.agent.contracts import AgentContext
    from src.agent.harness.decision_bounds import context_value
    from src.agent.harness.decision_policy import DecisionBoundaryError
    context = AgentContext('你好', 'boundary', metadata={'padding': 'x' * 65536})
    with pytest.raises(DecisionBoundaryError, match='invalid_context'):
        context_value(context, query_content_bytes=True)


def test_b1_prepared_context_cannot_mutate_obligations():
    prepared = science(ADMET)
    exposed = prepared.context
    exposed.query = 'SMILES: CCN'
    exposed.metadata['capabilities']['scientific_tools'] = False
    assert prepared.context.query == ADMET
    assert prepared.context.metadata['capabilities']['scientific_tools'] is True
    assert prepared.requirements.analysis_results[0].expected_smiles == ('CCO',)


@pytest.mark.parametrize('query', ['ADME mystery_endpoint', 'reverse target prediction mystery_endpoint'])
def test_b1_unconsumed_b_obligation_cannot_downgrade_to_intent(query):
    blocked(query)


@pytest.mark.parametrize('query', ['评估 ADME；SMILES: CCO', '评估可用方法 ADMET；SMILES: CCO',
    'assess ADMET; SMILES: CCO'])
def test_b1_available_methods_surface_preserves_whole_structure(query):
    prepared = science(query)
    assert prepared.required_tools == {'admet_predictor'}
    group, = prepared.requirements.analysis_results
    assert group.scope == 'available_methods' and group.expected_smiles == ('CCO',)


def test_b1_explicit_hit_obligation_does_not_invent_numeric_guarantee():
    query = RAG + '并要求有命中'
    prepared = science(query)
    assert prepared.requirements.retrieval_result.require_hits is True
    assert prepared.requirements.retrieval_result.query == query
    assert prepared.requirements.retrieval_result.k == 3


@pytest.mark.parametrize('query', [RAG, '查询 EGFR 的靶点结构'])
def test_b1_nonmolecular_input_does_not_consume_incidental_reference(query):
    request = envelope(query, reference={'id': 'unowned'}, selection={'id': 'unowned'})
    prepared, _ = admit(request, references=NoReferenceResolution())
    assert prepared.context.resolved_molecule is None


def test_b1_invalid_consumed_reference_uses_existing_owner_service(tmp_path):
    from src.web.scientific_references import ScientificReferenceService
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    service = ScientificReferenceService(SQLiteAgentStateStore(str(tmp_path / 'reference.sqlite')))
    request = envelope('评估这个分子的 ADMET', reference={'id': 'unowned'})
    assert assess(request).kind == 'known_scientific'
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, references=service)


@pytest.mark.parametrize('query', ['计算性质和类药性，包含 logP 和分子量；SMILES: OCC; CCN',
    '预测 PDE5A 活性；SMILES: CCO', '查询 EGFR 的靶点结构', '你好'])
def test_b1_direct_preparation_and_semantic_preparation_agree(query):
    request = envelope(query)
    prepared, _ = admit(request)
    direct = server_call(api().prepare_decision_request, {'message': query},
        session_id=request.session_id, trace_id=request.trace_id, config_generation=request.config_generation)
    assert direct == prepared
    assert direct.requirements.version == '2'
    old = server_call(api().prepare_decision_request, {'message': query}, profile=None,
        session_id=request.session_id, trace_id=request.trace_id, config_generation=request.config_generation)
    assert old.requirements.version == '1'
    assert old.required_tools == prepared.required_tools


@pytest.mark.parametrize('query,count', [(RAG, 5), (RAG, 10), (RAG + '，返回五个', 3)])
def test_b1_rag_count_rejection_explains_supported_choice_without_echo(query, count):
    request = envelope(query, rag_count=count)
    with pytest.raises(api().DecisionAdmissionError) as error:
        admit(request)
    assert error.value.code == 'request_clarification_required'
    assert '3' in getattr(error.value, 'notice', '')
    assert RAG not in error.value.notice
    assert request.rag_count == count


@pytest.mark.parametrize('query', ['Explain ADME in simple terms.',
    'Explain reverse target prediction in simple terms.', '请介绍 ADMET 的基本概念。'])
def test_b1_descriptive_scope_stays_ordinary(query):
    request = envelope(query, enable_tools=False, enable_rag=True)
    assert assess(request).kind == 'semantic_candidate'
    prepared, _ = admit(request, proposal('general_knowledge'))
    assert prepared.request_kind == 'chat'
    assert prepared.allowed_tools == prepared.required_tools == frozenset()


@pytest.mark.parametrize('query', [RAG, ADMET])
def test_b1_known_science_cannot_be_overridden_by_intent(query):
    request = envelope(query)
    assert assess(request).kind == 'known_scientific'
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, proposal('conversation'))


@pytest.mark.parametrize('query', [
    '评估 ADMET 和 PDE5A 活性和 PDE4A 活性；SMILES: CCO',
    '评估 ADMET 和 PDE4A 活性和 PDE5A 活性；SMILES: CCO',
    '评估 PDE5A 活性和 PDE4A 活性；SMILES: CCO',
], ids=['carson-exact', 'reverse-mention-order', 'activity-only'])
def test_b1_distinct_authored_activity_identities_not_collapsed_to_family(query):
    request = envelope(query)
    assessment = assess(request)
    if assessment.kind != 'blocked':
        prepared, _ = admit(request)
        pytest.fail('Expected whole-request rejection; admitted targets=' + repr(tuple(
            item.target for item in prepared.requirements.analysis_results
            if item.tool_name == 'activity_predictor')))
    assert assessment.reason == 'request_clarification_required'
    with pytest.raises(api().DecisionAdmissionError):
        admit(request)
    with pytest.raises(api().DecisionAdmissionError):
        server_call(api().prepare_decision_request, {'message': query},
            session_id=request.session_id, trace_id=request.trace_id,
            config_generation=request.config_generation)


@pytest.mark.parametrize('query', ['什么是反向寻靶？',
    'Explain reverse target search in simple terms.'], ids=['carson-zh', 'carson-en'])
def test_b1_complete_reverse_descriptions_have_empty_authority(query):
    request = envelope(query, enable_tools=False, enable_rag=True)
    assessment = assess(request)
    assert assessment.kind == 'semantic_candidate', assessment
    prepared, _ = admit(request, proposal('general_knowledge'), references=NoReferenceResolution())
    assert prepared.request_kind == 'chat'
    assert prepared.required_tools == prepared.allowed_tools == frozenset()
    assert prepared.requirements.molecular_results == prepared.requirements.analysis_results == ()
    assert prepared.requirements.reverse_result is None
    assert prepared.requirements.target_result is None
    assert prepared.requirements.retrieval_result is None


@pytest.mark.parametrize('query', [
    '什么是反向寻靶？评估 ADMET；SMILES: CCO',
    '什么是反向寻靶并计算熔点',
    'Explain reverse target search in simple terms. Calculate ADMET for CCO.',
    'Explain reverse target search in simple terms and calculate melting point',
    'Explain reverse target search in simple terms. mystery_endpoint',
], ids=['zh-clause', 'zh-unseparated', 'en-clause', 'en-unseparated', 'en-unknown-suffix'])
def test_b1_reverse_description_does_not_consume_extra_obligations(query):
    blocked(query)
