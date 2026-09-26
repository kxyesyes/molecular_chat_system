"""Revision8 actual-loop continuation contracts; no live model acceptance.

The store, Session, RDKit and temporary RAG/reverse sources are real. Decision
transport, target lookup and typed ADMET/activity observations are explicitly
synthetic fixtures. A fresh loop reopens the same temporary SQLite database.
"""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext, RunOutcome
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.tools.property_calculator import PropertyCalculator
from test_activity_tool_contract import Recorder
from test_current_source_tool_hooks import sources
from test_decision_binding_acceptance import admet_tool, requirements, target_tool
from test_decision_binding_loop import loop_case
from test_decision_continuation import ContinuationModel
from test_decision_loop import clarify, finish, last_observation, tool
from test_family_activity_tool import family_row


def invoke(case, context, *, required, **kwargs):
    return asyncio.run(case.loop.run(context, request_kind='scientific',
        allowed_tools=set(case.calls), required_tools=required,
        requirements=kwargs.pop('requirements', requirements()), worker_owner=kwargs.pop('worker_owner', WorkerOwner()),
        event_bus=case.bus, **kwargs))


def reopen(case, decisions):
    """No source reconstruction, Session reuse or database substitution."""
    previous = case.loop
    case.store = SQLiteAgentStateStore(case.store.db_path)
    case.bus = AgentEventBus(state_store=case.store)
    case.model = ContinuationModel(decisions)
    case.loop = ModelDecisionLoop(case.model, previous.registry, case.store,
        mode=previous.mode, binding_profile=B1_PROFILE_REVISION,
        max_model_requests=previous.max_model_requests,
        max_tool_attempts=previous.max_tool_attempts,
        timeout_seconds=previous.timeout_seconds)
    assert case.loop is not previous


def scientific_decisions(*, waiting):
    """Every downstream proposal uses IDs/handles from actual observations."""
    ids = {}

    def remember(messages):
        observation = last_observation(messages)
        assert observation['success'], observation
        ids[observation['tool_name']] = observation['quality']['evidence_id']
        return observation

    def choose(name, producer=None):
        def next_decision(messages):
            observation = remember(messages)
            arguments = {'input_ref': ids[producer] if producer else 'user'}
            if name == 'target_database_search':
                assert observation['tool_name'] == 'reverse_target_predictor'
                arguments['record_ref'] = observation['record_references'][0]['record_ref']
            return tool(name, arguments)
        return next_decision

    def end(messages):
        remember(messages)
        return clarify() if waiting else finish(list(ids.values()))

    return [tool(), choose('admet_predictor', 'property_calculator'),
        choose('activity_predictor', 'property_calculator'),
        choose('reverse_target_predictor', 'property_calculator'),
        choose('target_database_search', 'reverse_target_predictor'),
        choose('rag_search'), end]


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_sqlite_revision8_new_loop_reuses_original_scientific_chain(loop_case, sources, mode, monkeypatch):
    import src.agent.harness.decision_binding_continuation as codec
    replay_errors = []
    original = codec.validate_continuation
    def validate(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            replay_errors.append((type(exc).__name__, str(exc)))
            raise
    monkeypatch.setattr(codec, 'validate_continuation', validate)
    rag, reverse = sources('rag'), sources('reverse')
    case = loop_case([], [PropertyCalculator(), admet_tool(),
        Recorder(dict(success=True, data=[family_row()])), reverse.tool,
        target_tool(), rag.tool], mode=mode)
    case.model = case.loop.model = ContinuationModel(scientific_decisions(waiting=True))
    context = AgentContext('target: PDE5A; SMILES: CCO', 'b8-scientific-chain',
        user_id='fixture-user', session_id='fixture-session')
    original_context = deepcopy(context)
    required = set(case.calls)
    waiting = invoke(case, context, required=required)
    assert waiting.metadata['stop_reason'] == 'clarification_required', waiting.metadata
    assert len(waiting.tool_results) == 6 and all(r.success for r in waiting.tool_results)
    assert all(len(calls) == 1 for calls in case.calls.values())
    assert waiting.metadata['waiting_for_input']
    assert 'continuation_id' in waiting.metadata, 'B clarification must publish revision8, never revision6/7'
    saved = case.store.get_run(context.trace_id)
    payload = saved['metadata']['decision_continuation']
    assert saved['status'] == 'waiting_for_input'
    assert payload['id'] == waiting.metadata['continuation_id']
    assert payload['snapshot']['decision_protocol_revision'] == 8
    before = [deepcopy(result.to_legacy_dict()) for result in waiting.tool_results]
    reopen(case, scientific_decisions(waiting=False))
    resumed = invoke(case, context, required=required,
        continuation_id=payload['id'], clarified_query='继续')
    assert resumed.outcome == RunOutcome.COMPLETED, (resumed.metadata, replay_errors)
    assert resumed.metadata['model_requests'] == 14
    assert resumed.metadata['reused_decisions'] == 6
    # Target lookup reserves its configured two attempts even though the
    # dynamic wrapper executes once. Reuse must not debit or refund that slot.
    assert waiting.metadata['tool_budget_reserved'] == 7
    assert resumed.metadata['tool_budget_reserved'] == waiting.metadata['tool_budget_reserved']
    assert resumed.metadata['tool_attempt_count'] == 6
    assert [result.to_legacy_dict() for result in resumed.tool_results] == before
    assert all(len(calls) == 1 for calls in case.calls.values()), 'replay must never repeat science'
    assert case.store.get_run(context.trace_id)['status'] == 'succeeded'
    assert context == original_context


def start_property_waiting(loop_case, *, mode='native'):
    case = loop_case([], [PropertyCalculator()], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool(), clarify()])
    context = AgentContext('SMILES: CCO', 'b8-negative', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={'property_calculator'})
    assert 'continuation_id' in waiting.metadata, waiting.metadata
    reopen(case, [tool(), lambda messages: finish([last_observation(messages)['quality']['evidence_id']])])
    return case, context, waiting.metadata['continuation_id']


def spy_writes(case, monkeypatch):
    writes = []
    for name in ('start_run', 'start_unowned_run', 'claim_workflow_run', 'update_run_status',
                 'update_run_metadata', 'transition_decision_continuation', 'append_event',
                 'save_checkpoint', 'record_tool_execution', 'register_artifact'):
        original = getattr(case.store, name)
        def wrapped(*args, name=name, original=original, **kwargs):
            writes.append(name)
            return original(*args, **kwargs)
        monkeypatch.setattr(case.store, name, wrapped)
    return writes


def resign(payload):
    from src.agent.evidence import EvidenceLedger
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})


@pytest.mark.parametrize('mutation', ['revision6', 'revision7', 'revision_bool', 'revision_float',
    'checksum', 'missing_field', 'unknown_field', 'whole_budget', 'actions13', 'models17', 'models_exhausted',
    'counter_bool', 'credit_bool', 'credit_nan', 'credit_inf', 'reserve_policy', 'credit_arithmetic', 'credit_root',
    'original_owner', 'journal_query', 'record_input', 'record_prefix', 'record_action', 'record_role',
    'proof_role', 'proof_requirement', 'proof_unknown', 'own_source', 'final_key', 'foreign_evidence',
    'producer_version', 'producer_digest', 'demo', 'fallback', 'seal', 'model_observation',
    'proposal_turn', 'proposal_tool', 'model_round', 'reuse_count', 'reserved_count', 'acceptance', 'marker',
    'metadata_calls', 'metadata_acceptance', 'metadata_query', 'call_unknown', 'call_missing', 'call_native',
    'envelope_dict_type', 'observation_dict_type', 'results_tuple'])
def test_revision8_preclaim_tampering_is_readonly(loop_case, monkeypatch, mutation):
    case, context, nonce = start_property_waiting(loop_case)
    original_get = case.store.get_run
    before = original_get(context.trace_id)
    events = case.store.get_events(context.trace_id)
    damaged = deepcopy(before)
    payload = damaged['metadata']['decision_continuation']
    saved = payload['snapshot']
    raw = saved['results'][0]
    quality = raw['quality']
    action = saved['binding_records'][quality['step_id']]
    proof = quality['binding_proof']
    if mutation.startswith('revision'):
        saved['decision_protocol_revision'] = {'revision6': 6, 'revision7': 7,
            'revision_bool': True, 'revision_float': 8.0}[mutation]
    elif mutation == 'checksum': payload['checksum'] = '0' * 64
    elif mutation == 'missing_field': del saved['binding_input_journal']
    elif mutation == 'unknown_field': saved['authority'] = 'model'
    elif mutation == 'whole_budget': saved['messages'][0]['content'] = 'x' * (512 * 1024)
    elif mutation == 'actions13': saved['tool_attempt_count'] = 13
    elif mutation == 'models17': saved['model_requests'] = 17
    elif mutation == 'models_exhausted': saved['model_requests'] = case.loop.max_model_requests
    elif mutation == 'counter_bool': saved['protocol_repairs'] = False
    elif mutation == 'credit_bool': saved['remaining_seconds'] = True
    elif mutation == 'credit_nan': saved['remaining_seconds'] = float('nan')
    elif mutation == 'credit_inf': saved['remaining_seconds'] = float('inf')
    elif mutation == 'reserve_policy': saved['finalization_reserve_seconds'] = 1
    elif mutation == 'credit_arithmetic': saved['remaining_seconds'] -= 1
    elif mutation == 'credit_root': saved['pre_finalization_remaining_seconds'] = 301
    elif mutation == 'original_owner': saved['binding_input_journal']['original']['user_id'] = 'foreign'
    elif mutation == 'journal_query': saved['binding_input_journal']['entries'][0]['query'] = 'SMILES: CCC'
    elif mutation == 'record_input': action['input_data']['query'] = 'CCC'
    elif mutation == 'record_prefix': action['input_prefix_sha256'] = '0' * 64
    elif mutation == 'record_action': action['action_sha256'] = '0' * 64
    elif mutation == 'record_role': action['proof']['roles'] = [{'evidence_id': 'evidence-foreign'}]
    elif mutation == 'proof_role': proof['roles'] = [{'evidence_id': 'evidence-foreign'}]
    elif mutation == 'proof_requirement': proof['requirements_sha256'] = '0' * 64
    elif mutation == 'proof_unknown': proof['arbitrary'] = True
    elif mutation == 'own_source': proof['own_source'] = {'kind': 'rag'}
    elif mutation == 'final_key': quality['operation_key'] = '0' * 64
    elif mutation == 'foreign_evidence': quality['evidence_id'] = 'evidence-foreign'
    elif mutation == 'producer_version': raw['provenance']['tool_version'] = 'foreign'
    elif mutation == 'producer_digest': raw['provenance']['output_digest'] = '0' * 64
    elif mutation == 'demo': raw['provenance']['demo_mode'] = True
    elif mutation == 'fallback': raw['provenance']['fallback_used'] = True
    elif mutation == 'seal': saved['observation_seals'][quality['evidence_id']] = '{}'
    elif mutation == 'model_observation':
        value = json.loads(saved['messages'][-1]['content'])
        value['data'][0]['smiles'] = 'CCC'
        saved['messages'][-1]['content'] = json.dumps(value)
    elif mutation == 'proposal_turn': saved['proposals'][0]['input_turn'] = 2
    elif mutation == 'proposal_tool': saved['proposals'][0]['decision']['tool_name'] = 'rag_search'
    elif mutation == 'model_round': saved['model_calls'][0]['round'] = True
    elif mutation == 'reuse_count': saved['reused_decisions'] += 1
    elif mutation == 'reserved_count': saved['tool_budget_reserved'] += 1
    elif mutation == 'acceptance': saved['task_acceptance']['satisfied'] = False
    elif mutation == 'marker': damaged['metadata']['decision_publication_invalidation'] = {}
    elif mutation == 'metadata_calls': damaged['metadata']['decision_loop']['model_requests'] += 1
    elif mutation == 'metadata_acceptance': damaged['metadata']['task_acceptance']['satisfied'] = False
    elif mutation == 'metadata_query': damaged['query'] = 'SMILES: CCC'
    elif mutation.startswith('call_'):
        call = saved['model_calls'][0]
        if mutation == 'call_unknown': call['authority'] = 'model'
        elif mutation == 'call_missing': del call['usage']
        else: call['request_attempts'] = True
        damaged['metadata']['decision_loop']['model_calls'] = deepcopy(saved['model_calls'])
    elif mutation in ('envelope_dict_type', 'observation_dict_type'):
        class ForeignDict(dict):
            pass
        if mutation == 'envelope_dict_type':
            payload = damaged['metadata']['decision_continuation'] = ForeignDict(payload)
        else:
            saved['results'][0] = ForeignDict(raw)
    elif mutation == 'results_tuple': saved['results'] = tuple(saved['results'])
    if mutation not in ('checksum', 'credit_nan', 'credit_inf',
                        'envelope_dict_type', 'observation_dict_type', 'results_tuple'):
        resign(payload)
    monkeypatch.setattr(case.store, 'get_run', lambda trace: deepcopy(damaged))
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    assert not writes and not case.bus.events and not case.model.messages
    assert len(case.calls['property_calculator']) == 1
    assert original_get(context.trace_id) == before and case.store.get_events(context.trace_id) == events


@pytest.mark.parametrize('change', ['owner', 'session', 'nonce', 'config', 'requirements', 'status'])
def test_revision8_owner_and_current_config_bind_before_claim(loop_case, monkeypatch, change):
    case, context, nonce = start_property_waiting(loop_case)
    before = case.store.get_run(context.trace_id)
    options = {}
    if change == 'owner': context.user_id = 'foreign'
    elif change == 'session': context.session_id = 'foreign'
    elif change == 'nonce': nonce = '0' * 32
    elif change == 'config': case.loop.config_generation = 'changed'
    elif change == 'requirements': options['requirements'] = requirements(forbidden_tools=['rag_search'])
    elif change == 'status':
        original = case.store.get_run
        monkeypatch.setattr(case.store, 'get_run', lambda trace: {**original(trace), 'status': 'running'})
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续', **options)
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not writes and not case.model.messages and not case.bus.events
    assert before['metadata']['decision_continuation']['id'] == nonce or change == 'nonce'


@pytest.mark.parametrize('where', ['read', 'source', 'claim', 'start', 'restore'])
def test_saved_credit_charges_all_restore_boundaries(loop_case, monkeypatch, where):
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_bindings import B1BindingResolver
    from src.agent.runtime.run_session import WorkflowRunSession
    now = [1000.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    case, context, nonce = start_property_waiting(loop_case)
    before = case.store.get_run(context.trace_id)
    credit = before['metadata']['decision_continuation']['snapshot']['remaining_seconds']
    hook = {'read': (case.store, 'get_run'), 'source': (B1BindingResolver, 'verify_binding_closure'),
        'claim': (case.store, 'transition_decision_continuation'), 'start': (WorkflowRunSession, 'start'),
        'restore': (WorkflowRunSession, 'restore_observations')}[where]
    original = getattr(*hook)
    advanced = []
    def wrapped(*args, **kwargs):
        value = original(*args, **kwargs)
        if not advanced:
            now[0] += credit + 1
            advanced.append(True)
        return value
    monkeypatch.setattr(*hook, wrapped)
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert advanced and not case.model.messages and len(case.calls['property_calculator']) == 1
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    if where in ('read', 'source'):
        assert not writes and not case.bus.events
        assert case.store.get_run(context.trace_id) == before
    else:
        assert result.outcome == RunOutcome.FAILED
        record = case.store.get_run(context.trace_id)
        assert record['status'] == 'failed' and 'decision_publication_invalidation' in record['metadata']


@pytest.mark.parametrize('committed', [False, True])
def test_false_or_uncertain_claim_never_installs_live_projection(loop_case, monkeypatch, committed):
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case)
    before = case.store.get_run(context.trace_id)
    original = case.store.transition_decision_continuation
    starts = []
    start = WorkflowRunSession.start
    def track_start(self, **kwargs):
        starts.append(self)
        return start(self, **kwargs)
    def claim(*args, **kwargs):
        assert kwargs['claim'] is True
        if committed:
            assert original(*args, **kwargs) is True
            raise RuntimeError('fixture CAS committed before raise')
        return False
    monkeypatch.setattr(WorkflowRunSession, 'start', track_start)
    monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not starts and not case.bus.events and not case.model.messages
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    record = case.store.get_run(context.trace_id)
    if committed:
        assert record['status'] == 'running'
        assert 'claimed_by' in record['metadata']['decision_continuation']
    else:
        assert record == before


def test_preclaim_projection_is_unstarted_and_live_restore_has_fresh_seals(loop_case, monkeypatch):
    from src.agent.runtime.run_session import WorkflowRunSession, SessionLifecycleError
    from src.agent.orchestrators.base import WorkflowStep
    from src.agent.harness.decision_bindings import B1BindingResolver
    case, context, nonce = start_property_waiting(loop_case)
    projections, live = [], []
    original_verify, original_restore = B1BindingResolver.verify_binding_closure, WorkflowRunSession.restore_observations
    def verify(self, *args, **kwargs):
        if self.session.results and not self.session.started:
            projection = self.session
            projections.append(projection)
            assert not projection.finished and not projection.steps
            with pytest.raises(SessionLifecycleError):
                projection.append_step(WorkflowStep('forbidden', 'property_calculator', input_data='CCO'))
            with pytest.raises(SessionLifecycleError):
                projection.execute_step(0)
        return original_verify(self, *args, **kwargs)
    def restore(self, *args, **kwargs):
        assert self.started and kwargs['binding_proofs']
        value = original_restore(self, *args, **kwargs)
        live.append(self)
        assert not self._decision_observation_seals
        return value
    monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', verify)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restore)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result.outcome == RunOutcome.COMPLETED, result.metadata
    assert projections and len(live) == 1
    assert all(p is not live[0] and p._decision_observation_seals is not live[0]._decision_observation_seals
               and p.results[0] is not live[0].results[0] for p in projections)
    assert len(case.calls['property_calculator']) == 1


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_live_restore_warning_mutation_cannot_acquire_fresh_authority(loop_case, monkeypatch, mode):
    import src.agent.harness.decision_loop as module
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case, mode=mode)
    original = WorkflowRunSession.restore_observations
    seal = module.seal_observation
    restored, live_seals = [], []
    fabricated = 'SYNTHETIC ATTACK: experimental validation established efficacy'
    def changed(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        self.results[0].warnings.append(fabricated)
        restored.append(self)
        return value
    def capture(result, session):
        if session in restored:
            live_seals.append(result)
        return seal(result, session)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', changed)
    monkeypatch.setattr(module, 'seal_observation', capture)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert restored and result.outcome == RunOutcome.FAILED
    assert not live_seals and not restored[0]._decision_observation_seals
    assert not case.model.messages and len(case.calls['property_calculator']) == 1
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    record = case.store.get_run(context.trace_id)
    assert record['status'] == 'failed' and 'decision_publication_invalidation' in record['metadata']
    assert fabricated not in json.dumps(result.to_legacy_dict(), ensure_ascii=False)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_rejected_live_quality_cannot_break_sanitized_failure_correction(loop_case, monkeypatch, mode):
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case, mode=mode)
    restore = WorkflowRunSession.restore_observations
    restored = []
    def changed(self, *args, **kwargs):
        value = restore(self, *args, **kwargs)
        self.results[0].quality = None
        restored.append(self)
        return value
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', changed)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result is not None, 'rejected observations must not prevent a failure result'
    assert result.outcome == RunOutcome.FAILED and restored
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    assert not case.model.messages and len(case.calls['property_calculator']) == 1
    assert not restored[0]._decision_observation_seals
    record = case.store.get_run(context.trace_id)
    marker = record['metadata']['decision_publication_invalidation']
    assert record['status'] == 'failed'
    assert result.metadata['stop_reason'] == marker['reason'] == 'continuation_restore_failed'
    assert marker['boundary'] == 'continuation_restore'
    assert marker['invalidated_evidence_ids'] == []
    corrections = [event for event in case.store.get_events(context.trace_id)
        if (event.get('payload') or {}).get('publication_stage') == 'invalidated']
    assert len(corrections) == 1 and corrections[0]['payload']['reason'] == marker['reason']


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('committed', [False, True])
def test_failure_fallback_precedes_reentry_and_retries_one_frozen_request(loop_case, monkeypatch, mode, committed):
    import src.agent.harness.decision_loop as module
    from src.agent.contracts import AgentResult
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case, mode=mode)
    restore, invalidate = WorkflowRunSession.restore_observations, module._BindingFinalization.invalidate
    correct = WorkflowRunSession.invalidate_dynamic_publication
    active, requests, reentries = [], [], []
    def changed(self, *args, **kwargs):
        value = restore(self, *args, **kwargs)
        self.results[0].quality = None
        return value
    def entered(self, reason, boundary):
        if not active:
            # Exercise a previously positive private candidate, not an
            # externally authorized answer or fabricated scientific evidence.
            self.candidate = AgentResult(context.trace_id, True, 'SYNTHETIC stale candidate',
                final_answer='SYNTHETIC stale candidate', outcome=RunOutcome.COMPLETED)
            active.append(self)
        return invalidate(self, reason, boundary)
    def corrected(self, **kwargs):
        requests.append(deepcopy(kwargs))
        candidate = active[0].candidate
        assert candidate.outcome == RunOutcome.FAILED and not candidate.final_answer and not candidate.tool_results
        assert candidate.metadata['stop_reason'] == 'continuation_restore_failed'
        assert candidate.metadata['correction_durability'] == 'unconfirmed'
        reentries.append(active[0].invalidate('another_failure', 'reentrant_callback'))
        assert reentries[-1].metadata['stop_reason'] == 'continuation_restore_failed'
        active[0].counters['model_requests'] = 16
        if len(requests) == 1:
            if committed:
                correct(self, **kwargs)
            # The next attempt must decode the original private request, not
            # reuse a callback-owned dict (nor the now-changed live counters).
            kwargs['reason'] = 'mutated_callback_reason'
            kwargs['counters']['model_requests'] = 16
            raise RuntimeError('fixture correction callback failed')
        return correct(self, **kwargs)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', changed)
    monkeypatch.setattr(module._BindingFinalization, 'invalidate', entered)
    monkeypatch.setattr(WorkflowRunSession, 'invalidate_dynamic_publication', corrected)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result is not None and result.outcome == RunOutcome.FAILED
    assert len(requests) == (1 if committed else 2) and all(request == requests[0] for request in requests)
    assert reentries and all(r.outcome == RunOutcome.FAILED for r in reentries)
    assert not result.final_answer and not result.tool_results and 'continuation_id' not in result.metadata
    assert 'SYNTHETIC stale candidate' not in json.dumps(result.to_legacy_dict(), ensure_ascii=False)
    assert result.metadata['stop_reason'] == 'continuation_restore_failed'
    assert not case.model.messages and len(case.calls['property_calculator']) == 1
    record = case.store.get_run(context.trace_id)
    assert record['status'] == 'failed'
    marker = record['metadata']['decision_publication_invalidation']
    assert marker['reason'] == result.metadata['stop_reason'] and marker['boundary'] == 'continuation_restore'
    assert marker['counters']['model_requests'] == 2
    assert result.metadata['correction_durability'] == 'confirmed'
    corrections = [event for event in case.store.get_events(context.trace_id)
        if (event.get('payload') or {}).get('publication_stage') == 'invalidated']
    assert len(corrections) == 1
    assert corrections[0]['payload']['reason'] == marker['reason']
    assert corrections[0]['payload']['counters'] == marker['counters']


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('mutation', ['drop', 'duplicate', 'order', 'list_type', 'result_type', 'native_bool'])
def test_live_restore_complete_sequence_is_compared_before_any_seal(loop_case, monkeypatch, mode, mutation):
    from dataclasses import replace
    import src.agent.harness.decision_loop as module
    from src.agent.contracts import ToolResult
    from src.agent.runtime.run_session import WorkflowRunSession
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    case = loop_case([], [PropertyCalculator(), DrugLikenessAssessment()], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool(), tool('drug_likeness_assessment'), clarify()])
    context = AgentContext('SMILES: CCO', 'b8-live-copy', user_id='owner', session_id='session')
    first = invoke(case, context, required=set(case.calls))
    reopen(case, [tool(), clarify()])
    original, seal = WorkflowRunSession.restore_observations, module.seal_observation
    restored, live_seals = [], []
    def changed(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        if mutation == 'drop': self.results.pop()
        elif mutation == 'duplicate': self.results.append(deepcopy(self.results[0]))
        elif mutation == 'order': self.results.reverse()
        elif mutation == 'list_type': self.results = tuple(self.results)
        elif mutation == 'result_type':
            class ForeignResult(ToolResult):
                pass
            self.results[0] = ForeignResult(**vars(self.results[0]))
        else: self.results[0].provenance = replace(self.results[0].provenance, demo_mode=0)
        restored.append(self)
        return value
    def capture(result, session):
        if session in restored:
            live_seals.append(result)
        return seal(result, session)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', changed)
    monkeypatch.setattr(module, 'seal_observation', capture)
    result = invoke(case, context, required=set(case.calls),
        continuation_id=first.metadata['continuation_id'], clarified_query='继续')
    assert restored and result.outcome == RunOutcome.FAILED
    assert not live_seals and not restored[0]._decision_observation_seals
    assert not case.model.messages and all(len(calls) == 1 for calls in case.calls.values())
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    record = case.store.get_run(context.trace_id)
    assert record['status'] == 'failed' and 'decision_publication_invalidation' in record['metadata']


@pytest.mark.parametrize('where', ['before', 'claim', 'start', 'restore'])
def test_current_source_change_never_releases_old_science(loop_case, sources, monkeypatch, where):
    from src.agent.runtime.run_session import WorkflowRunSession
    source = sources('rag')
    case = loop_case([], [source.tool])
    case.model = case.loop.model = ContinuationModel([tool('rag_search'), clarify()])
    context = AgentContext(source.query, 'b8-source', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={'rag_search'})
    nonce = waiting.metadata['continuation_id']
    before = case.store.get_run(context.trace_id)
    reopen(case, [tool('rag_search'), lambda m: finish([last_observation(m)['quality']['evidence_id']])])
    if where == 'before':
        source.source.close()
    else:
        owner, name = {'claim': (case.store, 'transition_decision_continuation'),
            'start': (WorkflowRunSession, 'start'), 'restore': (WorkflowRunSession, 'restore_observations')}[where]
        original = getattr(owner, name)
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            source.source.close()
            return value
        monkeypatch.setattr(owner, name, changed)
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'rag_search'}, continuation_id=nonce, clarified_query='继续')
    assert not case.model.messages and len(case.calls['rag_search']) == 1
    assert not result.final_answer and not result.tool_results and 'continuation_id' not in result.metadata
    record = case.store.get_run(context.trace_id)
    if where == 'before':
        assert record == before and not writes and not case.bus.events
    else:
        assert result.outcome == RunOutcome.FAILED and record['status'] == 'failed'
        assert 'decision_publication_invalidation' in record['metadata']


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_live_comparison_and_fresh_sealing_have_no_scheduling_gap(loop_case, monkeypatch, mode):
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_binding_continuation import ValidatedReplay
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case, mode=mode)
    restore, compare, seal = WorkflowRunSession.restore_observations, ValidatedReplay.verify_restored, module.seal_observation
    loop, restored, events = [], [], []
    def restored_live(self, *args, **kwargs):
        value = restore(self, *args, **kwargs)
        loop.append(asyncio.get_running_loop())
        restored.append(self)
        return value
    def compared(self, results):
        value = compare(self, results)
        events.append('compared')
        loop[0].call_soon_threadsafe(events.append, 'yielded')
        return value
    def sealed(result, session):
        if session in restored:
            events.append('sealed')
        return seal(result, session)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restored_live)
    monkeypatch.setattr(ValidatedReplay, 'verify_restored', compared)
    monkeypatch.setattr(module, 'seal_observation', sealed)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result.outcome == RunOutcome.COMPLETED
    assert events == ['compared', 'sealed', 'yielded']
    assert len(case.calls['property_calculator']) == 1


@pytest.mark.parametrize('tail', [20, 30])
def test_fixed_tail_reservation_charges_next_segment_without_refund(loop_case, monkeypatch, tail):
    import src.agent.harness.decision_loop as module
    now = [1000.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    case = loop_case([], [PropertyCalculator()])
    case.loop.timeout_seconds = 100
    case.model = case.loop.model = ContinuationModel([clarify()])
    context = AgentContext('SMILES: CCO', 'b8-tail', user_id='owner', session_id='session')
    original = WorkerOwner.settle
    drains = []
    async def settle(self):
        await original(self)
        drains.append(self)
        if len(drains) == 1:
            now[0] += tail
    monkeypatch.setattr(WorkerOwner, 'settle', settle)
    result = invoke(case, context, required={'property_calculator'})
    record = case.store.get_run(context.trace_id)
    saved = record['metadata']['decision_continuation']['snapshot']
    assert saved['pre_finalization_remaining_seconds'] == 100
    assert saved['finalization_reserve_seconds'] == 25 and saved['remaining_seconds'] == 75
    assert not any(case.calls.values()) and len(drains) == 1
    if tail == 30:
        assert result.outcome == RunOutcome.FAILED and record['status'] == 'failed'
        assert not result.final_answer and not result.tool_results and 'continuation_id' not in result.metadata
        assert 'decision_publication_invalidation' in record['metadata']
    else:
        assert record['status'] == 'waiting_for_input'
        reopen(case, [clarify()])
        resumed = invoke(case, context, required={'property_calculator'},
            continuation_id=result.metadata['continuation_id'], clarified_query='继续')
        assert resumed.metadata['waiting_for_input'], resumed.metadata
        second = case.store.get_run(context.trace_id)['metadata']['decision_continuation']['snapshot']
        assert second['pre_finalization_remaining_seconds'] <= 75
        assert second['remaining_seconds'] <= 56.25


@pytest.mark.parametrize('reply', ['SMILES: CCC', 'SMILES: CCO; SMILES: CCC', '靶点: EGFR'])
def test_resume_cannot_reset_original_subject_target_or_count(loop_case, reply):
    case = loop_case([], [PropertyCalculator()])
    case.model = case.loop.model = ContinuationModel([tool(), clarify()])
    context = AgentContext('SMILES: CCO; target: PDE5A', 'b8-conflict', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={'property_calculator'})
    reopen(case, [tool()])
    result = invoke(case, context, required={'property_calculator'},
        continuation_id=waiting.metadata['continuation_id'], clarified_query=reply)
    assert result.outcome == RunOutcome.FAILED
    assert not case.model.messages and len(case.calls['property_calculator']) == 1
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata


@pytest.mark.parametrize('where', ['read', 'claim', 'start'])
def test_restore_rechecks_configuration_changed_inside_callback(loop_case, monkeypatch, where):
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case)
    before = case.store.get_run(context.trace_id)
    owner, name = {'read': (case.store, 'get_run'), 'claim': (case.store, 'transition_decision_continuation'),
                   'start': (WorkflowRunSession, 'start')}[where]
    original = getattr(owner, name)
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        case.loop.config_generation = 'callback-generation'
        return value
    monkeypatch.setattr(owner, name, changed)
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert not case.model.messages and len(case.calls['property_calculator']) == 1
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    if where == 'read':
        assert not writes and case.store.get_run(context.trace_id) == before
    else:
        assert result.outcome == RunOutcome.FAILED and case.store.get_run(context.trace_id)['status'] == 'failed'


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_actual_loop_restores_proofless_preparation_diagnostic_without_authority(loop_case, sources, monkeypatch, mode):
    from src.agent.harness.decision_bindings import B1BindingResolver
    original = B1BindingResolver.prepare_observation
    preparations = []
    def prepare(self, result, step):
        preparations.append(step.tool_name)
        original(self, result, step)
        if step.tool_name == 'property_calculator':
            # Real Session post-hook validation creates its exact rollback
            # envelope. No monkeypatched result, proof or lifecycle flags.
            step.metadata['invalid_fixture'] = object()
    monkeypatch.setattr(B1BindingResolver, 'prepare_observation', prepare)
    source = sources('rag')
    case = loop_case([], [PropertyCalculator(), source.tool], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool(), tool('rag_search'), clarify()])
    context = AgentContext('SMILES: CCO', 'b8-diagnostic', user_id='owner', session_id='session')
    first = invoke(case, context, required={'rag_search'})
    assert first.metadata['waiting_for_input'] and 'continuation_id' in first.metadata, first.metadata
    diagnostic = first.tool_results[0]
    assert diagnostic.message == 'Observation preparation failed' and not diagnostic.success
    assert 'binding_proof' not in diagnostic.quality and diagnostic.data is None
    before = deepcopy(diagnostic.to_legacy_dict())
    reopen(case, [tool('rag_search'), clarify()])
    second = invoke(case, context, required={'rag_search'},
        continuation_id=first.metadata['continuation_id'], clarified_query='继续')
    assert second.metadata['waiting_for_input'] and 'continuation_id' in second.metadata, second.metadata
    assert second.tool_results[0].to_legacy_dict() == before
    assert preparations.count('property_calculator') == 1
    assert all(len(calls) == 1 for calls in case.calls.values())


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_revision8_preserves_native_query_content_boundary(loop_case, mode):
    case = loop_case([], [], mode=mode)
    case.model = case.loop.model = ContinuationModel([clarify()])
    context = AgentContext('"' * 10000, 'b8-content', user_id='owner', session_id='session')
    first = invoke(case, context, required=set())
    assert 'continuation_id' in first.metadata
    reopen(case, [clarify()])
    second = invoke(case, context, required=set(), continuation_id=first.metadata['continuation_id'],
        clarified_query='"' * 10000)
    assert 'continuation_id' in second.metadata, second.metadata


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('details_kind', ['false', 'zero', 'list', 'dict_subclass', 'none', 'dict'])
def test_live_diagnostic_error_details_are_native_before_serialization(loop_case, monkeypatch, mode, details_kind):
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_bindings import B1BindingResolver
    from src.agent.runtime.run_session import WorkflowRunSession
    prepare = B1BindingResolver.prepare_observation
    def fail_preparation(self, result, step):
        prepare(self, result, step)
        step.metadata['invalid_fixture'] = object()
    monkeypatch.setattr(B1BindingResolver, 'prepare_observation', fail_preparation)
    case = loop_case([], [PropertyCalculator()], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool(), clarify()])
    context = AgentContext('SMILES: CCO', 'b8-error-details', user_id='owner', session_id='session')
    first = invoke(case, context, required=set())
    assert first.tool_results[0].message == 'Observation preparation failed'
    reopen(case, [clarify()])
    restore, seal = WorkflowRunSession.restore_observations, module.seal_observation
    restored, live_seals = [], []
    class ForeignDict(dict):
        pass
    def changed(self, *args, **kwargs):
        value = restore(self, *args, **kwargs)
        assert self.results[0].error.details == {}
        self.results[0].error.details = {'false': False, 'zero': 0, 'list': [],
            'dict_subclass': ForeignDict(), 'none': None, 'dict': {}}[details_kind]
        restored.append(self)
        return value
    def capture(result, session):
        if session in restored:
            live_seals.append(result)
        return seal(result, session)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', changed)
    monkeypatch.setattr(module, 'seal_observation', capture)
    result = invoke(case, context, required=set(), continuation_id=first.metadata['continuation_id'], clarified_query='继续')
    assert restored and len(case.calls['property_calculator']) == 1
    if details_kind in ('none', 'dict'):
        assert result.metadata['waiting_for_input'] and 'continuation_id' in result.metadata
        assert len(live_seals) == 1 and len(case.model.messages) == 1
    else:
        assert result.outcome == RunOutcome.FAILED and not live_seals
        assert not restored[0]._decision_observation_seals and not case.model.messages
        assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
        assert case.store.get_run(context.trace_id)['status'] == 'failed'


@pytest.mark.parametrize('mutation', ['future_parent', 'record_handle', 'selection_digest'])
def test_replay_rebuilds_reverse_handles_and_rejects_future_observations(loop_case, sources, monkeypatch, mutation):
    source = sources('reverse')
    case = loop_case([], [source.tool, target_tool()])
    def lookup(messages):
        observed = last_observation(messages)
        return tool('target_database_search', {'input_ref': observed['quality']['evidence_id'],
            'record_ref': observed['record_references'][0]['record_ref']})
    case.model = case.loop.model = ContinuationModel([tool('reverse_target_predictor'), lookup, clarify()])
    context = AgentContext('SMILES: CCO', 'b8-order', user_id='owner', session_id='session')
    first = invoke(case, context, required=set(case.calls))
    reopen(case, [clarify()])
    original_get = case.store.get_run
    before = original_get(context.trace_id)
    damaged = deepcopy(before)
    saved = damaged['metadata']['decision_continuation']['snapshot']
    target = saved['results'][1]
    record = saved['binding_records'][target['quality']['step_id']]
    if mutation == 'record_handle':
        record['arguments']['record_ref'] = 'record-' + '0' * 64
        saved['proposals'][1]['decision']['arguments']['record_ref'] = record['arguments']['record_ref']
    elif mutation == 'selection_digest':
        record['proof']['roles'][0]['selection_sha256'] = '0' * 64
    else:
        saved['results'][:2] = reversed(saved['results'][:2])
        saved['proposals'][:2] = reversed(saved['proposals'][:2])
        saved['model_calls'][:2] = reversed(saved['model_calls'][:2])
        for index, call in enumerate(saved['model_calls'], 1):
            call['round'] = index
        saved['messages'][2:6] = saved['messages'][4:6] + saved['messages'][2:4]
        damaged['metadata']['decision_loop']['model_calls'] = deepcopy(saved['model_calls'])
    resign(damaged['metadata']['decision_continuation'])
    monkeypatch.setattr(case.store, 'get_run', lambda trace: deepcopy(damaged))
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required=set(case.calls),
        continuation_id=first.metadata['continuation_id'], clarified_query='继续')
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not writes and not case.bus.events and not case.model.messages
    assert all(len(calls) == 1 for calls in case.calls.values())
    assert original_get(context.trace_id) == before


@pytest.mark.parametrize('where', ['claim', 'start', 'restore'])
def test_marker_introduced_inside_claim_or_restore_callback_blocks_dispatch(loop_case, monkeypatch, where):
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case)
    owner, name = {'claim': (case.store, 'transition_decision_continuation'),
        'start': (WorkflowRunSession, 'start'), 'restore': (WorkflowRunSession, 'restore_observations')}[where]
    original = getattr(owner, name)
    def invalidated(*args, **kwargs):
        value = original(*args, **kwargs)
        case.store.update_run_metadata(context.trace_id, {'decision_publication_invalidation': {'reason': 'fixture'}})
        return value
    monkeypatch.setattr(owner, name, invalidated)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert not case.model.messages and len(case.calls['property_calculator']) == 1
    assert result.outcome == RunOutcome.FAILED and case.store.get_run(context.trace_id)['status'] == 'failed'
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata


def test_marker_introduced_by_final_preclaim_source_check_leaves_nonce_unchanged(loop_case, monkeypatch):
    from src.agent.harness.decision_binding_continuation import ValidatedReplay
    case, context, nonce = start_property_waiting(loop_case)
    before = case.store.get_run(context.trace_id)
    update = case.store.update_run_metadata
    verify = ValidatedReplay.verify_current
    def invalidated(self):
        verify(self)
        update(context.trace_id, {'decision_publication_invalidation': {'reason': 'fixture'}})
    monkeypatch.setattr(ValidatedReplay, 'verify_current', invalidated)
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert not writes and not case.bus.events and not case.model.messages
    after = case.store.get_run(context.trace_id)
    assert after['status'] == 'waiting_for_input'
    assert after['metadata']['decision_continuation'] == before['metadata']['decision_continuation']
    assert result.metadata['stop_reason'] == 'continuation_rejected'


def test_claim_payload_copy_time_is_charged_immediately_before_cas(loop_case, monkeypatch):
    import src.agent.harness.decision_loop as module
    now = [1000.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    case, context, nonce = start_property_waiting(loop_case)
    before = case.store.get_run(context.trace_id)
    remaining = before['metadata']['decision_continuation']['snapshot']['remaining_seconds']
    original_copy = module.deepcopy
    charged = []
    def delayed_copy(value, *args, **kwargs):
        result = original_copy(value, *args, **kwargs)
        if type(value) is dict and value.get('id') == nonce and 'snapshot' in value and not charged:
            now[0] += remaining + 1
            charged.append(True)
        return result
    monkeypatch.setattr(module, 'deepcopy', delayed_copy)
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert charged and result.metadata['stop_reason'] == 'continuation_rejected'
    assert not writes and not case.bus.events and not case.model.messages
    assert case.store.get_run(context.trace_id) == before
    assert len(case.calls['property_calculator']) == 1


def test_claim_envelope_byte_limit_includes_claimed_by_before_cas(loop_case, monkeypatch):
    import src.agent.harness.decision_binding_continuation as codec
    case, context, nonce = start_property_waiting(loop_case)
    before = case.store.get_run(context.trace_id)
    payload = before['metadata']['decision_continuation']
    assert codec.LIMIT == 512 * 1024
    # Scale just the byte ceiling to the real valid envelope's exact size.
    # All native fields, original history, SQLite and CAS are unchanged. This
    # isolates the additional claim bytes; whole512KiB overflow is tested above.
    monkeypatch.setattr(codec, 'LIMIT', len(codec._wire(payload).encode('utf-8')))
    writes = spy_writes(case, monkeypatch)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not writes and not case.bus.events and not case.model.messages
    assert case.store.get_run(context.trace_id) == before
    assert len(case.calls['property_calculator']) == 1


@pytest.mark.parametrize('raises', [False, True])
def test_waiting_publication_callback_spends_fixed_tail_even_when_commit_raises(loop_case, monkeypatch, raises):
    import src.agent.harness.decision_loop as module
    now = [1000.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    case = loop_case([], [PropertyCalculator()])
    case.loop.timeout_seconds = 100
    case.model = case.loop.model = ContinuationModel([tool(), clarify()])
    context = AgentContext('SMILES: CCO', 'b8-publish-tail', user_id='owner', session_id='session')
    publish = case.store.transition_decision_continuation
    publications = []
    def delayed(*args, **kwargs):
        value = publish(*args, **kwargs)
        assert kwargs['claim'] is False
        publications.append(deepcopy(kwargs['replacement']))
        now[0] += 30
        if raises:
            raise RuntimeError('fixture committed waiting callback')
        return value
    monkeypatch.setattr(case.store, 'transition_decision_continuation', delayed)
    result = invoke(case, context, required={'property_calculator'})
    assert len(publications) == 1 and len(case.calls['property_calculator']) == 1
    record = case.store.get_run(context.trace_id)
    assert record['metadata']['decision_continuation'] == publications[0]
    assert publications[0]['snapshot']['remaining_seconds'] == 75
    assert record['status'] == 'failed' and 'decision_publication_invalidation' in record['metadata']
    assert result.outcome == RunOutcome.FAILED and not result.tool_results and not result.final_answer
    assert 'continuation_id' not in result.metadata


@pytest.mark.parametrize('boundary', ['start', 'restore'])
@pytest.mark.parametrize('installed', [False, True])
def test_confirmed_claim_callback_raises_after_install_is_failure_only(loop_case, monkeypatch, boundary, installed):
    from src.agent.runtime.run_session import WorkflowRunSession
    case, context, nonce = start_property_waiting(loop_case)
    name = 'start' if boundary == 'start' else 'restore_observations'
    original = getattr(WorkflowRunSession, name)
    def committed(self, *args, **kwargs):
        if installed:
            original(self, *args, **kwargs)
        raise RuntimeError('fixture committed lifecycle callback')
    monkeypatch.setattr(WorkflowRunSession, name, committed)
    result = invoke(case, context, required={'property_calculator'}, continuation_id=nonce, clarified_query='继续')
    assert result.outcome == RunOutcome.FAILED
    if boundary == 'start' and not installed:
        # Confirmed CAS but no started Session: no invented correction authority
        # or retry of a lifecycle callback with unknown side effects.
        assert case.store.get_run(context.trace_id)['status'] == 'running'
        assert result.metadata['correction_durability'] == 'unconfirmed'
    else:
        assert case.store.get_run(context.trace_id)['status'] == 'failed'
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    assert not case.model.messages and len(case.calls['property_calculator']) == 1


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_sqlite_revision8_no_action_smiles_then_target_keeps_original_journal(loop_case, mode):
    case = loop_case([], [PropertyCalculator(),
        Recorder(dict(success=True, data=[family_row()]))], mode=mode)
    case.model = case.loop.model = ContinuationModel([clarify()])
    context = AgentContext('综合评价', 'b8-human-inputs',
        user_id='fixture-user', session_id='fixture-session',
        active_skill='fixture-skill', model_name='fixture-model',
        temperature=0.25, metadata={'fixture': 'synthetic-offline'})
    original_context = deepcopy(context)
    required = set(case.calls)
    first = invoke(case, context, required=required)
    assert first.metadata['waiting_for_input'] and not any(case.calls.values())
    assert 'continuation_id' in first.metadata, 'no-action B clarification needs an authenticated journal'
    reopen(case, [clarify()])
    second = invoke(case, context, required=required,
        continuation_id=first.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert second.metadata['waiting_for_input'] and not any(case.calls.values())
    assert second.metadata['continuation_id'] != first.metadata['continuation_id']
    snapshot = case.store.get_run(context.trace_id)['metadata']['decision_continuation']['snapshot']
    assert snapshot['decision_protocol_revision'] == 8
    assert snapshot['input_queries'] == ['综合评价', 'SMILES: CCO']
    assert snapshot['results'] == [] and snapshot['tool_attempt_count'] == 0

    def activity(messages):
        observation = last_observation(messages)
        assert observation['success'] and observation['data'][0]['smiles'] == 'CCO'
        return tool('activity_predictor', {'input_ref': observation['quality']['evidence_id']})

    def done(messages):
        return finish([last_observation(messages)['quality']['evidence_id']])

    reopen(case, [tool(), activity, done])
    result = invoke(case, context, required=required,
        continuation_id=second.metadata['continuation_id'], clarified_query='靶点: PDE5A')
    assert result.outcome == RunOutcome.COMPLETED, result.metadata
    assert result.metadata['model_requests'] == 5
    assert result.metadata['tool_attempt_count'] == 2
    assert all(len(calls) == 1 for calls in case.calls.values())
    assert context == original_context
