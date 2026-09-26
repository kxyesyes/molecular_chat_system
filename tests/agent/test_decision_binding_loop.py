"""Actual B1 graph integration. Scripted decisions are not live-model acceptance.

RDKit and temporary retrieval/reverse producers are real; ADMET/activity and
target Service are explicitly synthetic, typed, test-owned fixtures.
"""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext, RunOutcome
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION, B1_TOOLS
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.harness.decision_bindings import B1BindingResolver
from src.agent.harness.decision_policy import (
    DecisionBoundaryError, decision_system_message, encode_observation,
)
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from test_current_source_tool_hooks import sources
from test_decision_loop import ScriptedModel, tool, finish, clarify, last_observation
from test_decision_binding_acceptance import admet_tool, target_tool, requirements
from test_activity_tool_contract import Recorder, single_row
from test_family_activity_tool import family_row
from test_decision_binding_inputs import selections


@pytest.fixture
def loop_case(tmp_path, monkeypatch):
    registries = []

    def build(decisions, tools, *, mode='native', profile=B1_PROFILE_REVISION):
        calls = {t.name: [] for t in tools}
        for t in tools:
            original = t.execute
            def counted(query, original=original, name=t.name):
                calls[name].append(deepcopy(query))
                return original(query)
            monkeypatch.setattr(t, 'execute', counted)
        registry = build_tool_registry(tools)
        registries.append(registry)
        store = SQLiteAgentStateStore(str(tmp_path / f'loop-{len(registries)}.sqlite'))
        model = ScriptedModel(decisions)
        options = {} if profile is None else {'binding_profile': profile}
        loop = ModelDecisionLoop(model, registry, store, mode=mode, **options)
        return SimpleNamespace(loop=loop, model=model, store=store, calls=calls,
                               bus=AgentEventBus(state_store=store))

    yield build
    for registry in registries:
        registry.close()


def run(case, query='SMILES: CCO', *, req=None, required=(), context=None, **kwargs):
    return asyncio.run(case.loop.run(context or AgentContext(query, 'actual-b1',
        user_id='user', session_id='owner'), request_kind='scientific',
        allowed_tools=set(case.calls), required_tools=required,
        requirements=req or requirements(), worker_owner=WorkerOwner(),
        event_bus=case.bus, **kwargs))


def finish_last(messages):
    return finish([last_observation(messages)['quality']['evidence_id']])


@pytest.mark.parametrize('name', ['rag_search', 'admet_predictor', 'reverse_target_predictor'])
def test_actual_legacy_graph_seven_tool_gap(loop_case, sources, name):
    source = sources('rag' if name == 'rag_search' else 'reverse') if name != 'admet_predictor' else None
    actual = source.tool if source else admet_tool()
    case = loop_case([tool(name), finish_last], [actual], profile=None)
    result = asyncio.run(case.loop.run(AgentContext('SMILES: CCO', 'legacy-gap'),
        request_kind='scientific', allowed_tools={name}, required_tools=set(),
        worker_owner=WorkerOwner()))
    # Initial RED demonstrated this gap. Keep default legacy authority unchanged;
    # only the explicit B1 tests below may dispatch these registered adapters.
    assert not result.success and result.metadata['stop_reason'] == 'tool_not_authorized'
    assert not case.calls[name] and len(case.model.messages) == 1


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_b1_actual_rdkit_observation_drives_next_action(loop_case, mode):
    def choose_likeness(messages):
        obs = last_observation(messages)
        assert obs['success'] and obs['data'][0]['properties']['molecular_weight'] > 46
        assert obs['task_acceptance']['finish_eligible'] is False
        return tool('drug_likeness_assessment', {'input_ref': obs['quality']['evidence_id']})
    case = loop_case([tool(), choose_likeness, finish_last],
        [PropertyCalculator(), DrugLikenessAssessment()], mode=mode)
    result = run(case, req=requirements(molecular_results=[
        dict(tool_name='property_calculator'), dict(tool_name='drug_likeness_assessment')]))
    assert result.outcome == RunOutcome.COMPLETED, result.metadata
    assert all(len(calls) == 1 for calls in case.calls.values())
    assert result.metadata['task_acceptance']['finish_eligible']
    assert 'model prose' not in result.final_answer
    assert all(r.quality['binding_proof']['profile'] == B1_PROFILE_REVISION for r in result.tool_results)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_all_seven_actual_tools_are_chosen_from_observations(loop_case, sources, mode, monkeypatch):
    rag, reverse = sources('rag'), sources('reverse')
    ids, records, proposals = {}, [], []
    original = B1BindingResolver.register_action
    def register(self, step, action):
        records.append(json.loads(action.record_json))
        return original(self, step, action)
    monkeypatch.setattr(B1BindingResolver, 'register_action', register)
    def choose(name, *, ref=None):
        def next_action(messages):
            obs = last_observation(messages)
            assert obs['success'], obs
            ids[obs['tool_name']] = obs['quality']['evidence_id']
            args = {'input_ref': ids[ref] if ref else 'user'}
            if name == 'target_database_search':
                assert obs['record_references']
                args['record_ref'] = obs['record_references'][0]['record_ref']
            return tool(name, args)
        return next_action
    def done(messages):
        obs = last_observation(messages)
        ids['rag_search'] = obs['quality']['evidence_id']
        return finish(list(ids.values()))
    query = 'target: PDE5A; SMILES: CCO'
    case = loop_case([tool(), choose('admet_predictor', ref='property_calculator'),
        choose('activity_predictor', ref='property_calculator'),
        choose('drug_likeness_assessment', ref='property_calculator'),
        choose('reverse_target_predictor', ref='property_calculator'),
        choose('target_database_search', ref='reverse_target_predictor'), choose('rag_search'), done],
        [PropertyCalculator(), DrugLikenessAssessment(), admet_tool(),
         Recorder(dict(success=True, data=[family_row()])), reverse.tool, target_tool(), rag.tool], mode=mode)
    result = run(case, query, required=B1_TOOLS)
    assert result.outcome == RunOutcome.COMPLETED, (result.metadata['stop_reason'], result.metadata['task_acceptance'])
    assert set(case.calls) == B1_TOOLS and all(len(v) == 1 for v in case.calls.values())
    assert len(result.tool_results) == 7 and result.metadata['model_requests'] == 8
    assert all(r['input_turn'] == 0 for r in records)
    assert all(r['context']['query'] == query for r in records)
    assert result.metadata['task_acceptance']['version'] == '2'


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('query', ['"\\' * 3000 + 'a' * 10384, '药' * 5461 + 'a', '<' * 16384],
                         ids=['escaped', 'utf8', 'html-sensitive'])
def test_b1_query_content_limit_in_actual_rag_loop(loop_case, sources, mode, query):
    rag = sources('rag')
    case = loop_case([tool('rag_search'), finish_last], [rag.tool], mode=mode)
    result = run(case, query, req=requirements(retrieval_result=dict(query=query)))
    assert result.outcome == RunOutcome.COMPLETED, result.metadata
    assert case.calls['rag_search'] == [query]
    assert case.model.messages[0][-1]['content'] == query
    # The requirement must round-trip exactly, without changing its evidence
    # identity, and every actual model request must fit the real transport.
    from src.agent.decision_transport import _snapshot_messages
    for messages in case.model.messages:
        assert _snapshot_messages(messages)[0] == messages
    requirement_text = case.model.messages[0][0]['content'].split(
        '. Immutable result requirements (not a tool sequence): ', 1)[1]
    prompt_requirements, _ = json.JSONDecoder().raw_decode(requirement_text)
    assert prompt_requirements['retrieval_result']['query'] == query
    assert result.metadata['task_acceptance']['finish_eligible']


def test_b1_requirement_prompt_preserves_redaction_and_alias_values():
    shared = {'query': '<药>&`', 'api_key': 'synthetic-test-only'}
    payload = {'first': shared, 'alias': shared, 'query': 'password=synthetic-test-only'}
    message = decision_system_message('scientific', (), [], payload,
                                      binding_profile=B1_PROFILE_REVISION)
    encoded = message['content'].split(
        '. Immutable result requirements (not a tool sequence): ', 1)[1]
    decoded = json.loads(encoded)
    assert decoded == {'first': {'query': '<药>&`', 'api_key': '[REDACTED]'},
                       'alias': {'query': '<药>&`', 'api_key': '[REDACTED]'},
                       'query': '[REDACTED]'}
    assert payload['first'] is payload['alias'] and shared['api_key'] == 'synthetic-test-only'


@pytest.mark.parametrize('bad', ['oversize', 'alias_oversize', 'cycle', 'subclass'])
def test_b1_requirement_prompt_bounds_before_redaction(monkeypatch, bad):
    import src.agent.harness.decision_policy as policy
    def forbidden_scan(value):
        pytest.fail('unbounded/native-invalid payload reached the secret scanner')
    monkeypatch.setattr(policy, 'contains_secret_material', forbidden_scan)
    if bad == 'oversize':
        payload = {'api_key': 'a' * 32768}
    elif bad == 'alias_oversize':
        shared = {'query': 'a' * 17000}
        payload = [shared, shared]
    elif bad == 'cycle':
        payload = []
        payload.append(payload)
    else:
        class Hostile(dict):
            def items(self):
                pytest.fail('non-native mapping was traversed')
        payload = Hostile(query='x')
    with pytest.raises(DecisionBoundaryError, match='invalid_binding_requirements'):
        decision_system_message('scientific', (), [], payload,
                                binding_profile=B1_PROFILE_REVISION)


def test_legacy_requirement_prompt_and_scientific_observation_bounds_unchanged():
    payload = {'query': '<药>&`'}
    message = decision_system_message('scientific', (), [], payload)
    assert message['content'].endswith(encode_observation(payload))
    assert '\\u003c' in message['content'] and '<药>' not in message['content']
    for encode in (encode_observation,
                   lambda value: decision_system_message('scientific', (), [], value)):
        with pytest.raises(DecisionBoundaryError, match='observation_too_large'):
            encode({'query': '<' * 16384})


@pytest.mark.parametrize('profile', ['unknown', True])
def test_requirement_prompt_rejects_unknown_binding_profile(profile):
    with pytest.raises(DecisionBoundaryError, match='invalid_binding_profile'):
        decision_system_message('scientific', (), [], {}, binding_profile=profile)


@pytest.mark.parametrize('scientific,rag', [(False, True), (True, False), (True, True), (False, False), (1, True), (True, 1)])
def test_independent_strict_capabilities(loop_case, sources, scientific, rag):
    source = sources('rag')
    case = loop_case([tool('rag_search'), finish_last], [source.tool])
    query = source.query
    ctx = AgentContext(query, 'actual-b1', metadata={'capabilities': {'scientific_tools': scientific, 'rag': rag}})
    result = run(case, context=ctx, req=requirements(retrieval_result=dict(query=query)))
    expected = type(scientific) is bool and type(rag) is bool and rag
    assert result.success is expected
    assert len(case.calls['rag_search']) == int(expected)
    if not expected:
        assert not case.model.messages


def test_b1_chat_has_zero_tools_and_no_citations(loop_case, sources):
    source = sources('rag')
    case = loop_case([finish(text='你好', kind='chat')], [source.tool, PropertyCalculator()])
    result = asyncio.run(case.loop.run(AgentContext('你好', 'b1-chat',
        metadata={'capabilities': {'scientific_tools': True, 'rag': True}}),
        request_kind='chat', allowed_tools=B1_TOOLS, required_tools=(),
        requirements=requirements(), worker_owner=WorkerOwner()))
    assert result.success and result.final_answer == '你好'
    assert not result.tool_results and not any(case.calls.values())
    assert 'Tool catalog: []' in case.model.messages[0][0]['content']


@pytest.mark.parametrize('profile', ['unknown', 'ordinary-semantic-b2-v1', True, {}])
def test_unknown_profile_never_downgrades(loop_case, profile):
    with pytest.raises(ValueError, match='invalid_binding_profile'):
        loop_case([], [], profile=profile)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_initial_clarification_never_mints_legacy_nonce(loop_case, mode):
    case = loop_case([clarify()], [PropertyCalculator()], mode=mode)
    result = run(case, '请计算性质', required={'property_calculator'})
    assert result.metadata['waiting_for_input']
    assert result.metadata['stop_reason'] == 'clarification_required'
    assert 'continuation_id' not in result.metadata and not any(case.calls.values())
    rejected = run(case, continuation_id='legacy-nonce', clarified_query='SMILES: CCO')
    assert rejected.metadata['stop_reason'] == 'continuation_rejected'
    assert len(case.model.messages) == 1


@pytest.mark.parametrize('mutation', ['input', 'metadata', 'extra_metadata', 'tool', 'record', 'context'])
def test_session_owned_copy_is_authenticated_before_physical_dispatch(loop_case, monkeypatch, mutation):
    original = WorkflowRunSession.append_step
    snapshots = []
    def append(self, step):
        snapshots.append(deepcopy(step))
        original(self, step)
        actual = self.steps[-1]
        assert actual is not step
        if mutation == 'input':
            actual.input_data['query'] = 'CCN'
        elif mutation == 'metadata':
            actual.metadata['operation_key'] = 'forged'
        elif mutation == 'extra_metadata':
            actual.metadata['round'] = 999
        elif mutation == 'tool':
            object.__setattr__(actual, 'tool_name', 'drug_likeness_assessment')
        elif mutation == 'context':
            self.context.metadata['browser'] = 'unadmitted'
    monkeypatch.setattr(WorkflowRunSession, 'append_step', append)
    if mutation == 'record':
        register = B1BindingResolver.register_action
        def tamper(self, step, action):
            metadata = register(self, step, action)
            value = json.loads(self._records[step])
            value['arguments'] = {'input_ref': 'evidence-forged'}
            self._records[step] = json.dumps(value)
            return metadata
        monkeypatch.setattr(B1BindingResolver, 'register_action', tamper)
    case = loop_case([tool(), finish_last], [PropertyCalculator(), DrugLikenessAssessment()])
    result = run(case)
    assert not result.success and not any(case.calls.values()), result.metadata
    assert result.metadata['stop_reason'] == 'invalid_dynamic_binding'
    assert len(case.model.messages) == 1


@pytest.mark.parametrize('boundary', ['planning_started', 'planning_completed', 'tool_started',
                                     'tool_completed', 'dispatch_persist', 'observed_persist', 'terminal_persist', 'model'])
def test_current_source_expiry_stops_next_action_and_answer(loop_case, sources, monkeypatch, boundary):
    source = sources('rag')
    state = {'observed': False, 'expired': False}
    def expire():
        if not state['expired']:
            source.source.close()
            state['expired'] = True
    def next_action(messages):
        assert last_observation(messages)['success']
        if boundary == 'model':
            expire()
        return finish_last(messages) if boundary == 'terminal_persist' else tool()
    case = loop_case([tool('rag_search'), next_action, finish_last], [source.tool, PropertyCalculator()])
    def callback(event):
        if event.event.value == 'tool_completed' and event.tool == 'rag_search':
            state['observed'] = True
        if state['observed'] and event.event.value == boundary:
            expire()
    case.bus.on_event = callback
    update = case.store.update_run_metadata
    def persist(trace, metadata):
        value = update(trace, metadata)
        phase = metadata.get('decision_loop', {}).get('phase')
        if state['observed'] and phase == boundary.removesuffix('_persist'):
            expire()
        return value
    monkeypatch.setattr(case.store, 'update_run_metadata', persist)
    result = run(case, 'SMILES: CCO')
    assert state['expired'] and not result.success, result.metadata
    assert len(case.calls['rag_search']) == 1 and not case.calls['property_calculator']
    assert len(case.model.messages) <= 2
    assert '"observations"' not in result.final_answer


@pytest.mark.parametrize('phase', ['validated', 'reserved', 'worker'])
def test_adapter_guard_denial_latches_first_reason_after_normalization(loop_case, monkeypatch, phase):
    original = B1BindingResolver.guard_dispatch
    checks, sessions = [], []
    # Session guard, adapter after-validation, reservation, submitted worker.
    fail_at = {'validated': 2, 'reserved': 3, 'worker': 4}[phase]
    def guard(self, step, input_data):
        original(self, step, input_data)
        sessions.append(self.session)
        checks.append(1)
        if len(checks) == fail_at:
            raise DecisionBoundaryError('scientific_reference_unavailable')
        # The source remains valid after the transient boundary failure.
    monkeypatch.setattr(B1BindingResolver, 'guard_dispatch', guard)
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    result = run(case)
    assert not result.success and not case.calls['property_calculator']
    assert len(checks) == fail_at and len(case.model.messages) == 1
    assert result.metadata['stop_reason'] == 'scientific_reference_unavailable'
    assert result.metadata['tool_attempt_count'] == 1
    assert result.metadata['tool_budget_reserved'] >= 1


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_action_identity_reuses_without_scientific_reexecution(loop_case, sources, mode):
    source = sources('rag')
    case = loop_case([tool('rag_search'), tool('rag_search'), finish_last], [source.tool], mode=mode)
    result = run(case, source.query, required={'rag_search'})
    assert result.success and len(case.calls['rag_search']) == 1
    assert result.metadata['reused_decisions'] == 1 and len(result.tool_results) == 1


@pytest.mark.parametrize('repeat', [False, True])
def test_failed_optional_tool_is_partial_and_never_reexecuted(loop_case, repeat):
    from src.agent.contracts import ToolResult, AgentErrorCode
    from test_analysis_contract import CountingTool
    unavailable = CountingTool('admet_predictor', ToolResult.error_result(
        'admet_predictor', AgentErrorCode.MODEL_UNAVAILABLE, 'synthetic unavailable fixture'))
    decisions = [tool(), tool('admet_predictor')]
    decisions += [tool('admet_predictor')] if repeat else [finish_last]
    case = loop_case(decisions, [PropertyCalculator(), unavailable])
    result = run(case, required={'property_calculator'})
    assert not result.success and len(case.calls['admet_predictor']) == 1
    if repeat:
        assert result.metadata['stop_reason'] == 'previous_action_not_usable'
    else:
        assert result.outcome == RunOutcome.PARTIAL
        assert 'missing_required_citation' in result.metadata['task_acceptance']['reason_codes']


@pytest.mark.parametrize('args', [{'input_ref': 'evidence-forged'}, {'input_ref': 'user', 'smiles': 'CCN'},
                                  {'input_ref': 'user', 'query': 'invented'}, {'input_ref': 'user', 'k': 1}])
def test_model_cannot_forge_tool_inputs(loop_case, args):
    case = loop_case([tool(arguments=args)], [PropertyCalculator()])
    result = run(case)
    assert not result.success and not any(case.calls.values())


@pytest.mark.parametrize('group', [dict(analysis_results=[dict(tool_name='admet_predictor')]),
    dict(reverse_result=dict(expected_smiles='CCO')), dict(retrieval_result=dict(query='SMILES: CCO')),
    dict(target_result=dict(input='reverse')), dict(molecular_results=[dict(tool_name='property_calculator')])])
def test_all_requirement_groups_need_actual_registry_authority(loop_case, group):
    case = loop_case([], [])
    result = asyncio.run(case.loop.run(AgentContext('SMILES: CCO', 'absent'),
        request_kind='scientific', allowed_tools=B1_TOOLS, required_tools=(),
        requirements=requirements(**group), worker_owner=WorkerOwner()))
    assert not result.success and not case.model.messages
    assert result.metadata['stop_reason'] == 'task_requirements_not_authorized'


@pytest.mark.parametrize('invalidity', ['expired', 'foreign'])
def test_consumed_selected_reference_rejects_before_model(loop_case, selections, monkeypatch, invalidity):
    store, selected = selections
    case = loop_case([tool()], [PropertyCalculator()])
    case.loop.store = store
    case.bus = AgentEventBus(state_store=store)
    if invalidity == 'expired':
        store.update_run_status(selected[0].trace_id, 'failed')
    ctx = AgentContext('计算所选分子的性质', 'selected-loop', session_id='foreign' if invalidity == 'foreign' else 'owner',
                       resolved_molecule=selected[0])
    result = run(case, context=ctx, req=requirements(molecular_results=[dict(tool_name='property_calculator')]))
    assert not result.success and not case.model.messages and not any(case.calls.values())


@pytest.mark.parametrize('bad', [None, {'version': '1'}, {}])
def test_b1_requires_explicit_v2(loop_case, bad):
    case = loop_case([], [])
    result = asyncio.run(case.loop.run(AgentContext('hello', 'no-v2'), request_kind='scientific',
        allowed_tools=(), required_tools=(), requirements=bad, worker_owner=WorkerOwner()))
    assert not result.success and not case.model.messages


def test_b1_requires_worker_owner(loop_case):
    case = loop_case([], [])
    result = asyncio.run(case.loop.run(AgentContext('hello', 'no-owner'), request_kind='scientific',
        allowed_tools=(), required_tools=(), requirements=requirements()))
    assert result.metadata['stop_reason'] == 'invalid_binding_admission'
    assert not case.model.messages


@pytest.mark.parametrize('mutation', ['query_over', 'context_over', 'hostile_tools', 'non_owner'])
def test_native_admission_bounded_before_callbacks_or_copies(loop_case, mutation):
    hooks = []
    class Hostile:
        def __hash__(self):
            hooks.append('hash')
            return 1
        def __deepcopy__(self, memo):
            hooks.append('copy')
            return self
    case = loop_case([], [])
    ctx = AgentContext('hello', 'bounded')
    allowed, owner = [], WorkerOwner()
    if mutation == 'query_over':
        ctx.query = '药' * 5462
    elif mutation == 'context_over':
        ctx.metadata = {'oversize': 'a' * 65536}
    elif mutation == 'hostile_tools':
        allowed = [Hostile()]
    else:
        owner = Hostile()
    result = asyncio.run(case.loop.run(ctx, request_kind='scientific', allowed_tools=allowed,
        required_tools=(), requirements=requirements(), worker_owner=owner))
    assert not result.success and not hooks and not case.model.messages


def test_original_admission_frozen_before_catalog_callbacks(loop_case, monkeypatch):
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    import src.agent.harness.decision_loop as module
    catalog = module.authorized_catalog
    def mutate(registry, context, *args, **kwargs):
        result = catalog(registry, context, *args, **kwargs)
        context.query = 'SMILES: CCN'
        return result
    monkeypatch.setattr(module, 'authorized_catalog', mutate)
    result = run(case)
    assert not result.success and not case.model.messages and not any(case.calls.values())


@pytest.mark.parametrize('kind', ['target', 'rag'])
@pytest.mark.parametrize('require_result', [False, True])
def test_empty_lookup_vs_required_resolved_or_hits(loop_case, sources, kind, require_result):
    if kind == 'target':
        actual, query = target_tool('not_found'), 'EGFR'
        req = requirements(target_result=dict(input='user', query=query, require_resolved=require_result))
    else:
        source = sources('rag', empty=True)
        actual, query = source.tool, source.query
        req = requirements(retrieval_result=dict(query=query, require_hits=require_result))
    case = loop_case([tool(actual.name), finish_last], [actual])
    result = run(case, query, req=req)
    assert result.success is (not require_result)
    assert result.metadata['task_acceptance']['finish_eligible'] is (not require_result)


@pytest.mark.parametrize('empty', [False, True])
def test_typed_sparse_admet_never_infers_unknown_alerts(loop_case, empty):
    case = loop_case([tool('admet_predictor'), finish_last], [admet_tool(empty=empty)])
    result = run(case, required={'admet_predictor'})
    assert result.success is (not empty)
    assert 'low risk' not in result.final_answer.lower()
    if not empty:
        assert '"pains": null' in result.final_answer


def test_family_disagreement_is_review_not_completion(loop_case):
    from src.agent.contracts import ToolResult, ObservationStatus
    row = family_row(success=False, status='partial', execution_status='passed',
        predicted_pIC50=6.0, classification_regression_consistent=False)
    raw = ToolResult('activity_predictor', False, 'labelled synthetic review fixture',
        data=[row], status=ObservationStatus.PARTIAL)
    case = loop_case([tool('activity_predictor'), finish_last], [Recorder(raw)])
    result = run(case, 'target: PDE5A; SMILES: CCO', required={'activity_predictor'})
    assert result.outcome == RunOutcome.PARTIAL
    assert 'requires review' in result.final_answer
    assert not result.metadata['task_acceptance']['finish_eligible']


def test_uncited_success_does_not_satisfy_required_science(loop_case, sources):
    source = sources('rag')
    case = loop_case([tool(), tool('rag_search'), finish_last], [PropertyCalculator(), source.tool])
    result = run(case, required={'property_calculator'})
    assert result.outcome == RunOutcome.PARTIAL
    assert 'missing_required_citation' in result.metadata['task_acceptance']['reason_codes']
    assert 'molecular_weight' not in result.final_answer


def test_legacy_nonce_helpers_explicitly_reject_b_profile():
    from src.agent.harness.decision_continuation import claim_continuation, snapshot_payload
    loop = SimpleNamespace(binding_profile=B1_PROFILE_REVISION)
    session = SimpleNamespace(_decision_binding_profile=B1_PROFILE_REVISION)
    with pytest.raises(DecisionBoundaryError, match='continuation_rejected'):
        claim_continuation(loop, session, 'fingerprint', 'nonce', 'reply')
    with pytest.raises(DecisionBoundaryError, match='continuation_rejected'):
        snapshot_payload(SimpleNamespace(), session, 'fingerprint')


@pytest.mark.parametrize('where', ['before', 'after'])
def test_source_resolution_stays_owned_with_deadline_on_both_sides(loop_case, sources, monkeypatch, where):
    from src.agent.runtime.worker_ownership import _binding
    import src.agent.harness.decision_loop as module
    source = sources('rag')
    timer = [100.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: timer[0]))
    validate = source.tool.validate_current_observation
    calls = []
    def current(*args, **kwargs):
        scope = _binding.get()
        assert scope is not None and not scope[0].owner._sealed
        calls.append(1)
        result = validate(*args, **kwargs)
        if where == 'after':
            timer[0] += 301
        return result
    monkeypatch.setattr(source.tool, 'validate_current_observation', current)
    def next_action(messages):
        if where == 'before':
            timer[0] += 301
        return finish_last(messages)
    case = loop_case([tool('rag_search'), next_action], [source.tool])
    result = run(case, source.query)
    assert not result.success and calls
    assert result.metadata['stop_reason'] == 'task_deadline_exceeded'
    assert len(case.calls['rag_search']) == 1


@pytest.mark.parametrize('change', ['owner', 'idempotent', 'side_effects', 'forbidden', 'not_allowed'])
def test_b1_catalog_cannot_expand_actual_adapter_authority(loop_case, change):
    from dataclasses import replace
    case = loop_case([], [PropertyCalculator()])
    adapter = case.loop.registry.resolve('property_calculator')
    req, allowed = requirements(), {'property_calculator'}
    if change == 'owner':
        adapter.spec = replace(adapter.spec, owner_agents=frozenset({'other'}))
    elif change == 'idempotent':
        adapter.spec = replace(adapter.spec, idempotent=False)
    elif change == 'side_effects':
        adapter.spec = replace(adapter.spec, side_effects='write')
    elif change == 'forbidden':
        req = requirements(forbidden_tools=['property_calculator'])
    else:
        allowed = set()
    result = asyncio.run(case.loop.run(AgentContext('SMILES: CCO', 'catalog'),
        request_kind='scientific', allowed_tools=allowed, required_tools={'property_calculator'},
        requirements=req, worker_owner=WorkerOwner()))
    assert not result.success and not case.model.messages and not any(case.calls.values())


@pytest.mark.parametrize('unexpected', [False, True, 0, {}])
def test_actual_guard_unexpected_return_is_latched_before_next_model(loop_case, monkeypatch, unexpected):
    original = B1BindingResolver.guard_dispatch
    def returning(self, step, input_data):
        original(self, step, input_data)
        return unexpected
    monkeypatch.setattr(B1BindingResolver, 'guard_dispatch', returning)
    case = loop_case([tool(), finish_last], [PropertyCalculator()])
    result = run(case)
    assert not result.success and not any(case.calls.values())
    assert len(case.model.messages) == 1
    assert result.metadata['stop_reason'] == 'invalid_dynamic_binding'


def test_default_legacy_clock_order_unchanged(loop_case, monkeypatch):
    import src.agent.harness.decision_loop as module
    calls = []
    def now():
        calls.append(1)
        return 100.0
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=now))
    prepare = module.prepare_requirements
    def check(*args, **kwargs):
        assert calls == [], 'B root deadline must not move the original legacy clock boundary'
        return prepare(*args, **kwargs)
    monkeypatch.setattr(module, 'prepare_requirements', check)
    case = loop_case([finish(text='你好', kind='chat')], [], profile=None)
    result = asyncio.run(case.loop.run(AgentContext('你好', 'legacy-clock'), request_kind='chat',
        allowed_tools=(), required_tools=()))
    assert result.success


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_reverse_ancestor_expiry_prevents_target_consumer(loop_case, sources, mode):
    source = sources('reverse')
    def reverse(messages):
        return tool('reverse_target_predictor', {'input_ref': last_observation(messages)['quality']['evidence_id']})
    def target(messages):
        obs = last_observation(messages)
        assert obs['success'] and obs['record_references']
        proposal = tool('target_database_search', {'input_ref': obs['quality']['evidence_id'],
            'record_ref': obs['record_references'][0]['record_ref']})
        source.source.close_strict()
        return proposal
    case = loop_case([tool(), reverse, target], [PropertyCalculator(), source.tool, target_tool()], mode=mode)
    result = run(case)
    assert not result.success
    assert len(case.calls['property_calculator']) == len(case.calls['reverse_target_predictor']) == 1
    assert not case.calls['target_database_search'] and len(case.model.messages) == 3


def test_preparation_deadline_keeps_boundary_reason(loop_case, monkeypatch):
    import src.agent.harness.decision_loop as module
    timer = [100.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: timer[0]))
    prepare = module.prepare_binding_requirements
    def delayed(*args, **kwargs):
        result = prepare(*args, **kwargs)
        timer[0] += 301
        return result
    monkeypatch.setattr(module, 'prepare_binding_requirements', delayed)
    case = loop_case([], [PropertyCalculator()])
    result = run(case)
    assert result.metadata['stop_reason'] == 'task_deadline_exceeded'
    assert not case.model.messages and not any(case.calls.values())


def test_native_proposal_arguments_are_bounded_before_model_dump(loop_case):
    from src.agent.contracts.decision import ToolDecision
    hooks = []
    class NativeLooking(dict):
        def __deepcopy__(self, memo):
            hooks.append('copy')
            return self
    proposal = ToolDecision.model_construct(version='1', action='tool',
        tool_name='property_calculator', arguments=NativeLooking(input_ref='user'), purpose='fixture')
    case = loop_case([proposal, finish_last], [PropertyCalculator()])
    result = run(case)
    assert not result.success and not case.calls['property_calculator']
    assert not hooks and len(case.model.messages) == 1


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('boundary', ['tail_acceptance', 'tail_boundary'])
def test_tail_cancellation_drains_owned_worker_and_persists_one_cancelled_terminal(
        loop_case, monkeypatch, mode, boundary):
    """Cancel real owned validation after the graph, never a mocked cancellation."""
    import threading
    from langgraph.graph import StateGraph
    from src.agent.runtime.worker_ownership import _binding
    from test_worker_ownership import signalled, pending
    import src.agent.harness.decision_loop as module

    graph_done, terminal_phase = threading.Event(), threading.Event()
    entered, release, exited = (threading.Event() for _ in range(3))
    roots = []
    owner = WorkerOwner()
    trace = 'tail-cancel-' + boundary + '-' + mode
    case = loop_case([tool(), finish_last], [PropertyCalculator()], mode=mode)
    terminal_names = {'task_completed', 'task_failed', 'task_cancelled', 'task_rejected'}

    # Forward to the real compiled graph; mark only its actual successful return.
    compile_graph = StateGraph.compile
    def compile_and_mark(self, *args, **kwargs):
        compiled = compile_graph(self, *args, **kwargs)
        invoke = compiled.ainvoke
        async def invoke_and_mark(*args, **kwargs):
            result = await invoke(*args, **kwargs)
            graph_done.set()
            return result
        monkeypatch.setattr(compiled, 'ainvoke', invoke_and_mark)
        return compiled
    monkeypatch.setattr(StateGraph, 'compile', compile_and_mark)

    persist = case.store.update_run_metadata
    def persist_and_mark(trace_id, metadata):
        result = persist(trace_id, metadata)
        if metadata.get('decision_loop', {}).get('phase') == 'terminal':
            assert graph_done.is_set()
            terminal_phase.set()
        return result
    monkeypatch.setattr(case.store, 'update_run_metadata', persist_and_mark)

    def block_owned_validation():
        scope = _binding.get()
        assert scope is not None and scope[0].owner is owner
        assert threading.current_thread() is not threading.main_thread()
        assert not owner._sealed and scope[0].started and not scope[0].finished
        roots.append(scope[0])
        entered.set()
        try:
            assert release.wait(5), 'test must release the real owned validator'
        finally:
            exited.set()

    evaluate = module.evaluate_binding_acceptance
    def evaluate_and_block(*args, **kwargs):
        report = evaluate(*args, **kwargs)
        if boundary == 'tail_acceptance' and graph_done.is_set() and not entered.is_set():
            block_owned_validation()
        return report
    monkeypatch.setattr(module, 'evaluate_binding_acceptance', evaluate_and_block)

    closure = B1BindingResolver.verify_binding_closure
    def closure_and_block(self, *args, **kwargs):
        result = closure(self, *args, **kwargs)
        if boundary == 'tail_boundary' and terminal_phase.is_set() and not entered.is_set():
            block_owned_validation()
        return result
    monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', closure_and_block)

    async def exercise():
        task = asyncio.create_task(case.loop.run(AgentContext('SMILES: CCO', trace,
            user_id='user', session_id='owner'), request_kind='scientific',
            allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
            requirements=requirements(), worker_owner=owner, event_bus=case.bus))

        async def still_owned():
            await pending(task)
            assert owner.status == 'pending' and owner.pending_roots == 1
            assert not owner._sealed and not exited.is_set() and not roots[0].finished
            assert case.store.get_run(trace)['status'] == 'running'
            assert not [e for e in case.bus.events if e.event.value in terminal_names]
            assert not [e for e in case.store.get_events(trace) if e['event'] in terminal_names]
            assert case.calls == {'property_calculator': ['CCO']}
            assert len(case.model.messages) == 2

        try:
            await signalled(entered)
            assert graph_done.is_set() and len(roots) == 1
            assert terminal_phase.is_set() is (boundary == 'tail_boundary')
            await still_owned()
            task.cancel()
            await still_owned()
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert exited.is_set() and roots[0].finished
            assert owner.status == 'settled' and owner.pending_roots == 0
            assert case.calls == {'property_calculator': ['CCO']}
            assert len(case.model.messages) == 2
            memory_terminal = [e.event.value for e in case.bus.events if e.event.value in terminal_names]
            stored_terminal = [e['event'] for e in case.store.get_events(trace) if e['event'] in terminal_names]
            status = case.store.get_run(trace)['status']
            assert status == 'cancelled', (status, type(result).__name__, memory_terminal, stored_terminal)
            assert memory_terminal == stored_terminal == ['task_cancelled']
            assert not isinstance(result, BaseException)
            assert result.outcome == RunOutcome.CANCELLED and not result.success
            assert result.metadata['stop_reason'] == 'cancelled'
            assert result.metadata['tool_attempt_count'] == 1
        finally:
            # Assertion failures must not abandon the blocked worker or run task.
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
