"""Task4 offline admission; proposals never authorize science or invent results."""
import importlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from test_decision_loop import setup_loop, tool, finish_last


CAPABILITY_CASES = (
    ('REAL-010', '你好，我想了解一下这个系统能做什么？'),
    ('DIVERSE-015', '你好，我只是想了解这个系统能做什么。请不要调用任何科研计算工具。'),
)


def api():
    module = importlib.import_module('src.web.decision_request')
    for name in ('validate_request_envelope', 'assess_whole_request', 'prepare_with_intent'):
        assert callable(getattr(module, name, None)), 'Task4 missing API: ' + name
    return module


def envelope(query, **options):
    return api().validate_request_envelope({'message': query, **options},
        session_id='session-1', trace_id='trace-1', config_generation='generation-1')


@pytest.mark.parametrize('case_id,query', CAPABILITY_CASES)
def test_full_capability_is_only_a_candidate(case_id, query):
    request = envelope(query)
    assessment = api().assess_whole_request(request)
    assert assessment.kind == 'semantic_candidate', case_id
    assert request.query == query
    assert not isinstance(request, api().PreparedDecision)


def intent(kind='capability', **changes):
    from src.agent.contracts.ordinary_intent import OrdinaryIntent
    return OrdinaryIntent(version='1', kind=kind,
        history_relation=changes.get('history_relation', 'none'),
        unresolved=changes.get('unresolved', False))


def snapshot(**changes):
    from src.web.ordinary_capabilities import build_capability_snapshot, ORIGINAL_FOUR
    args = dict(registered_names=ORIGINAL_FOUR,
        provider_descriptor={'provider': 'offline', 'model': 'test-model', 'mode': 'json'},
        semantic_profile=True, intent_capable=True, original_four_profile=True,
        scientific_tools=True, permitted_names=ORIGINAL_FOUR,
        model_generation='generation-1', capability_generation='capability-1')
    args.update(changes)
    return build_capability_snapshot(**args)


def test_current_snapshot_round_trip_before_preparation():
    from src.agent.contracts.ordinary_admission import capability_digest, parse_capability_snapshot
    cap = snapshot()
    assert parse_capability_snapshot(cap.model_dump_json()) == cap
    assert capability_digest(cap)
    assert api()._current_capability_snapshot(cap, envelope('你好')) == cap


def test_assessment_revision_is_accepted_by_existing_binding_contract():
    from src.agent.contracts.ordinary_admission import build_admission_binding
    binding = build_admission_binding(snapshot(), query='hello', history=[],
        assessment_revision=api().ASSESSMENT_REVISION, intent_kind='known_chat', intent_requests=0)
    assert binding['assessment_revision'] == api().ASSESSMENT_REVISION


def admit(request, proposal, **changes):
    args = dict(history=[], capability_snapshot=snapshot(), intent_requests=1)
    args.update(changes)
    return api().prepare_with_intent(request, api().assess_whole_request(request), proposal, **args)


@pytest.mark.parametrize('query', [
    '能用通俗的语言说说这个平台的用途吗？',
    '我想听听药物研发这个领域的一般背景。',
    '聊聊为什么科学家重视可重复性。',
    'What makes a scientific explanation understandable?',
    'Could you describe molecular docking in simple terms?',
    '请介绍分子对接与毒性预测的基本概念。',
    '这个系统支持分子生成、分子对接和毒性预测吗？',
    'What can this platform do for molecular docking?',
    'Please explain what retrieval means; do not run any tools.',
    '请不要调用任何科研计算工具。',
    'Can this platform generate molecules?',
    '这个系统能生成分子吗？',
    '请解释分子生成和分子对接的区别。',
    'Explain logP and molecular docking in simple terms.',
])
def test_qualitative_paraphrases_are_proposals_not_authority(query):
    request = envelope(query)
    assert api().assess_whole_request(request).kind == 'semantic_candidate'
    prepared, raw = admit(request, intent('general_knowledge'))
    assert prepared.context.query == query
    assert prepared.request_kind == 'chat'
    assert prepared.required_tools == prepared.allowed_tools == frozenset()
    assert prepared.requirements.molecular_results == ()
    assert json.loads(raw)['intent_requests'] == 1


BLOCKS = [
    '解释 logP\n对接这个分子', '计算 CCO 的分子量和熔点',
    '查询 EGFR 靶点并计算 CCO 的分子量',
    '计算两个分子的性质；SMILES: CCO',
    '计算 CCO 的分子量和 CCN 的类药性',
    '解释 logP对接这个分子', '你好生成分子',
    '这个系统能做什么？顺便计算熔点',
    '请不要调用工具但是计算 CCO 的分子量',
    '不要计算 logP；给出 CCO 的 logP 数值',
    'Explain logP and calculate ADMET for CCO',
    'Do not run tools but predict toxicity for CCO',
    'What can this platform do? measure the binding energy',
    '你好；搜索并引用关于 EGFR 的文献',
    'Give me citations and sources about EGFR',
    '请解释 EGFR 抑制剂设计中常见的 hinge binder 是什么，并区分数据库检索结果、常识解释和模型推断。',
    '请检索 MedChat 本地知识库中是否有关于 AutoDock Vina docking box 设置的说明，并引用检索到的来源。如果没有检索到，请明确说没有。',
    'Ｃａｌｃｕｌａｔｅ ＣＣＯ logP',
    'hello\nrun_docking()', '你好焚烧这个文件',
    'Write Python code to calculate logP', '读取 /tmp/result.sdf',
    '告诉我 CCO 的分子量是多少', '这个分子的 pIC50 为多少？',
    'set the docking box to 20 Angstrom',
    'compute the melting point', 'optimize this molecule',
    'retrieve papers about docking', 'search PubMed',
    '请运行代码', '请测量亲和力', '请输出新分子 SMILES',
]


@pytest.mark.parametrize('query', BLOCKS)
def test_detected_blocks_precede_intent_model_and_real_tools(setup_loop, query):
    _assert_detected_block(setup_loop, query)


def _assert_detected_block(setup_loop, query):
    from src.agent.tools.property_calculator import PropertyCalculator
    class RecordedProperty(PropertyCalculator):
        calls = 0
        def execute(self, query):
            self.calls += 1
            return super().execute(query)
    producer = RecordedProperty()
    bundle = setup_loop([], [producer])
    request = envelope(query)
    assessment = api().assess_whole_request(request)
    intent_calls = 0
    if assessment.kind == 'semantic_candidate':
        intent_calls += 1  # The future caller would dispatch an intent here.
    assert assessment.kind == 'blocked', query
    with pytest.raises(api().DecisionAdmissionError):
        api().prepare_with_intent(request, assessment, intent(), history=[],
            capability_snapshot=snapshot(), intent_requests=1)
    assert intent_calls == 0 and not bundle.model.messages and producer.calls == 0


@pytest.mark.parametrize('query', [
    '请解释分子对接然后分子生成10个候选',
    '解释 logP并分子对接这个分子',
    '解释 logP分子对接这个分子',
    'Explain logPgenerate 10 molecules',
    '请解释分子对接顺便分子生成候选',
])
def test_nominal_suffix_still_demands_execution(setup_loop, query):
    _assert_detected_block(setup_loop, query)


def test_full_scan_inspects_suffix_and_never_truncates():
    request = envelope('a' * 15000 + '对接这个分子')
    assert api().assess_whole_request(request).kind == 'blocked'
    assert api().assess_whole_request(envelope('你好。' * 1500 + '运行代码')).kind == 'blocked'


@pytest.mark.parametrize('query', ['你好', 'Explain logP', '解释分子生成的概念'])
def test_known_chat_has_zero_intent_requests(query):
    request = envelope(query)
    assert api().assess_whole_request(request).kind == 'known_chat'
    prepared, raw = admit(request, None, intent_requests=0)
    assert prepared.request_kind == 'chat' and not prepared.required_tools
    assert json.loads(raw)['intent_kind'] == 'known_chat'
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, intent(), intent_requests=1)


def test_known_science_cannot_be_overridden_and_uses_real_rdkit(setup_loop):
    import asyncio
    from src.agent.tools.property_calculator import PropertyCalculator
    query = '计算 CCO 和 CCN 的分子量'
    request = envelope(query)
    assert api().assess_whole_request(request).kind == 'known_scientific'
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, intent('conversation'))
    prepared, raw = admit(request, None, intent_requests=0)
    legacy = api().prepare_decision_request({'message': query}, session_id='session-1',
        trace_id='trace-1', config_generation='generation-1')
    assert prepared == legacy
    assert json.loads(raw)['intent_kind'] == 'known_scientific'
    requirement, = prepared.requirements.molecular_results
    assert requirement.expected_smiles == ('CCO', 'CCN')
    assert requirement.exact_molecule_count == 2
    bundle = setup_loop([tool('property_calculator'), finish_last], [PropertyCalculator()])
    result = asyncio.run(bundle.loop.run(prepared.context, request_kind=prepared.request_kind,
        allowed_tools=prepared.allowed_tools, required_tools=prepared.required_tools,
        requirements=prepared.requirements))
    assert result.success and result.metadata['task_acceptance']['satisfied']


def test_tools_disabled_is_blocked_not_reclassified():
    request = envelope('计算 CCO 的分子量', enable_tools=False)
    assessment = api().assess_whole_request(request)
    assert assessment.kind == 'blocked' and assessment.reason == 'scientific_tools_disabled'
    with pytest.raises(api().DecisionAdmissionError, match='scientific_tools_disabled'):
        admit(request, intent())


@pytest.mark.parametrize('kind', ['scientific_execution', 'retrieval'])
def test_model_science_proposal_reenters_original_science_and_cannot_fall_back(kind, monkeypatch):
    request = envelope(CAPABILITY_CASES[0][1], enable_tools=False, enable_rag=False)
    module = api()
    original = module._classify
    calls = []
    def recorded(query):
        calls.append(query)
        return original(query)
    monkeypatch.setattr(module, '_classify', recorded)
    with pytest.raises(module.DecisionAdmissionError, match='request_clarification_required'):
        admit(request, intent(kind))
    assert calls and all(query == request.query for query in calls)


@pytest.mark.parametrize('kind,unresolved', [('mixed', False), ('uncertain', False), ('capability', True)])
def test_ambiguous_proposals_clarify(kind, unresolved):
    with pytest.raises(api().DecisionAdmissionError, match='request_clarification_required'):
        admit(envelope(CAPABILITY_CASES[0][1]), intent(kind, unresolved=unresolved))


@pytest.mark.parametrize('bad', [None, [], {'kind': 'capability'}, 'capability'])
def test_proposals_require_strict_task1_instances(bad):
    with pytest.raises(api().DecisionAdmissionError):
        admit(envelope(CAPABILITY_CASES[0][1]), bad)


def test_constructed_invalid_intent_is_revalidated():
    from src.agent.contracts.ordinary_intent import OrdinaryIntent
    bad = OrdinaryIntent.model_construct(version='1', kind='capability',
        history_relation='none', unresolved=False, allowed_tools=['run_docking'])
    # model_construct ignores extras: retain a malicious field explicitly.
    bad.__dict__['allowed_tools'] = ['run_docking']
    with pytest.raises(api().DecisionAdmissionError):
        admit(envelope(CAPABILITY_CASES[0][1]), bad)


@pytest.mark.parametrize('history', [None, (), [{}], [{'user': 'hello', 'assistant': 1}],
    [{'user': 'hi', 'assistant': 'ok'}] * 21,
    [{'user': 'a' * 16400, 'assistant': 'ok'}],
    [{'user': 'hello', 'assistant': 'api_key=sk-' + 'x' * 40}]])
def test_existing_history_admission_bounds_not_task3_digest_bounds(history):
    with pytest.raises(api().DecisionAdmissionError):
        admit(envelope('再讲得浅显一些。'), intent('follow_up', history_relation='prior_ordinary_turn'),
            history=history)


def test_follow_up_requires_actual_history_and_does_not_assign_context_memory():
    request = envelope('再讲得浅显一些。')
    proposal = intent('follow_up', history_relation='prior_ordinary_turn')
    with pytest.raises(api().DecisionAdmissionError):
        admit(request, proposal)
    with pytest.raises(TypeError):
        api().prepare_with_intent(request, api().assess_whole_request(request), proposal,
            capability_snapshot=snapshot(), intent_requests=1)
    history = [{'user': '你好', 'assistant': '你好。'}]
    prepared, raw = admit(request, proposal, history=history)
    assert prepared.context.memory == []
    from src.agent.contracts.ordinary_admission import build_admission_binding
    assert json.loads(raw) == build_admission_binding(snapshot(), query=request.query, history=history,
        assessment_revision=api().ASSESSMENT_REVISION, intent_kind='follow_up', intent_requests=1)
    history[0]['user'] = 'changed'
    assert prepared.context.memory == []


@pytest.mark.parametrize('field', ['history', 'profile', 'permissions', 'binding',
    'capability_snapshot', 'allowed_tools', 'intent_requests', 'intent', 'assessment'])
def test_client_authority_fields_fail_before_assessment(field):
    with pytest.raises(api().DecisionAdmissionError, match='untrusted_request_field'):
        envelope('你好', **{field: {}})


@pytest.mark.parametrize('changes', [
    {'model_generation': 'stale'}, {'semantic_profile': False}, {'intent_capable': False},
])
def test_current_capability_view_is_required(changes):
    with pytest.raises(api().DecisionAdmissionError):
        admit(envelope(CAPABILITY_CASES[0][1]), intent(), capability_snapshot=snapshot(**changes))


@pytest.mark.parametrize('field,value', [('profile_revision', 'forged'),
    ('catalog_revision', 'forged'), ('features', ())])
def test_wrong_current_profile_or_catalog_is_rejected(field, value):
    cap = snapshot().model_copy(update={field: value})
    with pytest.raises(api().DecisionAdmissionError):
        admit(envelope(CAPABILITY_CASES[0][1]), intent(), capability_snapshot=cap)


@pytest.mark.parametrize('count', [0, 2, True, -1])
def test_candidate_needs_one_actual_intent_request(count):
    with pytest.raises(api().DecisionAdmissionError):
        admit(envelope(CAPABILITY_CASES[0][1]), intent(), intent_requests=count)


def test_envelope_frozen_detached_plain_hints_and_ordinary_options():
    hints = {'selection_id': 'choice-1', 'nested': ['a']}
    request = envelope(CAPABILITY_CASES[0][1], selection=hints, reference={'id': 'ref-1'},
        temperature=0.2, mol_count=3, rag_count=2, enable_tools=False, enable_rag=False)
    hints['nested'].append('changed')
    assert request.selection == {'selection_id': 'choice-1', 'nested': ['a']}
    request.selection['nested'].append('detached')
    assert request.selection['nested'] == ['a']
    with pytest.raises(FrozenInstanceError):
        request.query = 'changed'
    prepared, raw = admit(request, intent())
    context = prepared.context
    assert context.query == request.query and context.session_id == 'session-1'
    assert context.trace_id == 'trace-1' and context.temperature == 0.2 and context.mol_count == 3
    assert context.metadata == {'capabilities': {'scientific_tools': False, 'rag': False}, 'rag_count': 2}
    assert context.resolved_molecule is None and prepared.config_generation == 'generation-1'
    assert json.loads(raw)['capability_generation'] == 'capability-1'


def test_assessment_is_frozen_and_cannot_be_synthesized_to_bypass_block():
    good = envelope(CAPABILITY_CASES[0][1])
    assessment = api().assess_whole_request(good)
    with pytest.raises(FrozenInstanceError):
        assessment.kind = 'known_chat'
    blocked = envelope('对接这个分子')
    bad_digest = api().assess_whole_request(blocked).query_digest
    for candidate in [assessment, replace(assessment, query_digest=bad_digest)]:
        with pytest.raises(api().DecisionAdmissionError):
            api().prepare_with_intent(blocked, candidate, intent(), history=[],
                capability_snapshot=snapshot(), intent_requests=1)
    with pytest.raises(api().DecisionAdmissionError):
        api().assess_whole_request(replace(good, query=''))


def test_envelope_revalidation_keeps_original_payload_size_allowance():
    payload = {'message': 'ordinary question', 'reference': ''}
    overhead = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    payload['reference'] = 'a' * (24 * 1024 - overhead)
    request = api().validate_request_envelope(payload, session_id='session-1',
        trace_id='trace-1', config_generation='generation-1')
    assert api().assess_whole_request(request).kind == 'semantic_candidate'


def test_science_reference_resolution_still_uses_existing_owner_service(tmp_path):
    from src.web.scientific_references import ScientificReferenceService
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    # No synthetic resolved value: an empty real owner store must not grant one.
    service = ScientificReferenceService(SQLiteAgentStateStore(str(tmp_path / 'reference.sqlite')))
    request = envelope('计算这个分子的分子量', reference={'id': 'unowned'})
    assessment = api().assess_whole_request(request)
    assert assessment.kind == 'known_scientific'
    with pytest.raises(api().DecisionAdmissionError):
        api().prepare_with_intent(request, assessment, None, history=[],
            capability_snapshot=snapshot(), intent_requests=0, references=service)


@pytest.mark.parametrize('options', [{'enable_tools': 1}, {'mol_count': True}, {'mol_count': 11},
    {'rag_count': 0}, {'temperature': float('nan')}, {'type': 'resume'}, {'reference': object()}])
def test_factoring_keeps_legacy_validation_codes(options):
    module = api()
    payload = {'message': '你好', **options}
    args = dict(session_id='session-1', trace_id='trace-1', config_generation='generation-1')
    with pytest.raises(module.DecisionAdmissionError) as old:
        module.prepare_decision_request(payload, **args)
    with pytest.raises(module.DecisionAdmissionError) as new:
        module.validate_request_envelope(payload, **args)
    assert new.value.code == old.value.code
