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



# Landed-publication integration additions. These are desired-behavior
# regressions, not a claim that either known donor defect has been executed.
def test_revision8_codec_api_contract():
    """Explicit feature/API RED, not a reproduction of a donor behavior defect."""
    from importlib import import_module
    from importlib.util import find_spec
    name = 'src.agent.harness.decision_binding_continuation'
    assert find_spec(name) is not None, 'revision8 continuation codec API is absent'
    codec = import_module(name)
    assert codec.REVISION == 8 and codec.LIMIT == 512 * 1024
    assert callable(codec.snapshot_payload) and callable(codec.validate_continuation)
    assert callable(codec.ValidatedReplay.verify_claim)
    assert callable(codec.ValidatedReplay.verify_restored)


def _fresh_registry_reopen(loop_case, previous, tools, decisions):
    """New actual registry, model, store connection authority and event bus."""
    fresh = loop_case([], tools, mode=previous.loop.mode)
    fresh.store = SQLiteAgentStateStore(previous.store.db_path)
    fresh.bus = AgentEventBus(state_store=fresh.store)
    fresh.model = ContinuationModel(decisions)
    fresh.loop = ModelDecisionLoop(fresh.model, fresh.loop.registry, fresh.store,
        mode=previous.loop.mode, binding_profile=B1_PROFILE_REVISION,
        config_generation=previous.loop.config_generation,
        max_model_requests=previous.loop.max_model_requests,
        max_tool_attempts=previous.loop.max_tool_attempts,
        timeout_seconds=previous.loop.timeout_seconds)
    assert fresh.store is not previous.store and fresh.store.db_path == previous.store.db_path
    assert fresh.loop.registry is not previous.loop.registry
    # Both registries are owned/closed by loop_case even on assertion failure.
    return fresh


def _waiting_cancel_case(loop_case, monkeypatch, mode, stage):
    import threading
    import src.agent.harness.decision_binding_continuation as codec
    from src.agent.harness.decision_bindings import B1BindingResolver
    from src.agent.runtime.run_session import WorkflowRunSession
    from test_decision_binding_publication import (
        MARKER, TERMINALS, capture_sessions, assert_ordinary_cancelled,
        assert_sanitized, hold_real_owned_worker,
    )
    from test_worker_ownership import signalled, pending

    sessions = capture_sessions(monkeypatch)
    case = loop_case([], [PropertyCalculator()], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool(), clarify()])
    context = AgentContext('SMILES: CCO', 'actual-b1', user_id='owner', session_id='session')
    owner = WorkerOwner()
    entered, release, exited, in_snapshot, snapshot_done, committed = (
        threading.Event() for _ in range(6))
    roots, drains, snapshots, publications, attempts, checks, finishes = [], [], [], [], [], [], []
    snapshot, verify = codec.snapshot_payload, B1BindingResolver.verify_binding_closure
    transition, append = case.store.transition_decision_continuation, case.store.append_event
    settle, finish_dynamic = WorkerOwner.settle, WorkflowRunSession.finish_dynamic

    def snapshot_call(*args, **kwargs):
        in_snapshot.set()
        try:
            value = snapshot(*args, **kwargs)
            snapshots.append(deepcopy(value[0]))
            snapshot_done.set()
            return value
        finally:
            in_snapshot.clear()

    def closure(self, *args, **kwargs):
        checks.append(owner.status)
        value = verify(self, *args, **kwargs)
        should_hold = (
            stage == 'snapshot' and in_snapshot.is_set()
            or stage == 'postsnapshot' and snapshot_done.is_set() and not finishes
            or stage == 'postattempt' and committed.is_set()
        )
        if should_hold and not entered.is_set():
            hold_real_owned_worker(owner, entered, release, exited, roots)
        return value

    def publishing(*args, **kwargs):
        publications.append(deepcopy(kwargs))
        return transition(*args, **kwargs)

    def finishing(self, *args, **kwargs):
        finishes.append(deepcopy(kwargs.get('metadata')))
        return finish_dynamic(self, *args, **kwargs)

    def append_then_raise(event):
        value = append(event)
        if stage == 'postattempt' and event['event'] == 'task_partial':
            attempts.append(deepcopy(event))
            assert not sessions[0]._terminal_event_emitted
            committed.set()
            raise RuntimeError('fixture waiting terminal committed before raising')
        return value

    async def draining(self):
        drains.append(self)
        await settle(self)

    monkeypatch.setattr(codec, 'snapshot_payload', snapshot_call)
    monkeypatch.setattr(B1BindingResolver, 'verify_binding_closure', closure)
    monkeypatch.setattr(case.store, 'transition_decision_continuation', publishing)
    monkeypatch.setattr(case.store, 'append_event', append_then_raise)
    monkeypatch.setattr(WorkflowRunSession, 'finish_dynamic', finishing)
    monkeypatch.setattr(WorkerOwner, 'settle', draining)

    async def exercise():
        task = asyncio.create_task(case.loop.run(context, request_kind='scientific',
            allowed_tools=set(case.calls), required_tools=set(case.calls),
            requirements=requirements(), event_bus=case.bus, worker_owner=owner))
        try:
            await signalled(entered)
            assert sessions[0].started and not sessions[0].finished
            assert case.store.get_run(context.trace_id)['status'] == 'running'
            assert publications == []
            if stage == 'snapshot':
                assert snapshots == [] and finishes == []
            else:
                assert len(snapshots) == 1
                assert snapshots[0]['snapshot']['decision_protocol_revision'] == 8
                if stage == 'postsnapshot':
                    # The first owned closure after codec return is finish's
                    # precheck, after the loop assembled its nonce metadata.
                    assert finishes == []
                else:
                    assert finishes[0]['continuation_id'] == snapshots[0]['id']
                    assert len(attempts) == 2 and attempts[0] == attempts[1]
                    assert not sessions[0]._terminal_event_emitted
            durable_before = [e for e in case.store.get_events(context.trace_id)
                              if e['event'] in {t.value for t in TERMINALS}]
            assert len(durable_before) == (1 if stage == 'postattempt' else 0)
            for _ in range(3):
                task.cancel()
                await pending(task)
                assert owner.status == 'pending' and owner.pending_roots == 1
                assert not roots[0].finished and not exited.is_set() and not owner._sealed
                assert case.store.get_run(context.trace_id)['status'] == 'running'
                assert case.calls == {'property_calculator': ['CCO']} and len(case.model.messages) == 2
                assert publications == []
                assert [e for e in case.store.get_events(context.trace_id)
                        if e['event'] in {t.value for t in TERMINALS}] == durable_before
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert not isinstance(result, BaseException), type(result).__name__
            assert drains == [owner] and owner.status == 'settled' and owner.pending_roots == 0
            assert roots[0].finished and exited.is_set()
            assert checks and set(checks) == {'pending'}, 'no source work after sealing'
            assert publications == [], 'cancellation cannot publish a fresh waiting snapshot'
            record = case.store.get_run(context.trace_id)
            assert 'decision_continuation' not in record['metadata']
            assert 'continuation_id' not in result.metadata
            assert case.calls == {'property_calculator': ['CCO']} and len(case.model.messages) == 2
            durable = [e for e in case.store.get_events(context.trace_id)
                       if e['event'] in {t.value for t in TERMINALS}]
            assert all('continuation_id' not in e['payload'] for e in durable)
            if stage != 'postattempt':
                assert_ordinary_cancelled(case, result)
                assert finishes[-1]['waiting_for_input'] is False
                assert 'continuation_id' not in finishes[-1]
            else:
                assert_sanitized(result, reason='cancelled')
                assert len(attempts) == 2, 'no positive retry after invalidation'
                assert record['status'] == 'failed' and record['metadata'][MARKER]['reason'] == 'cancelled'
                assert durable[:-1] == durable_before
                assert durable[-1]['event'] == 'task_failed'
                assert durable[-1]['payload']['publication_stage'] == 'invalidated'
                assert durable[-1]['payload']['supersedes_terminal_attempt_id'] == (
                    durable_before[0]['payload']['terminal_attempt_id'])
                assert all(e['payload']['answer_released'] is False for e in durable)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_snapshot_owned_cancellation_discards_preterminal_nonce(loop_case, monkeypatch, mode):
    _waiting_cancel_case(loop_case, monkeypatch, mode, 'snapshot')


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_postsnapshot_preterminal_cancellation_discards_assembled_nonce(loop_case, monkeypatch, mode):
    _waiting_cancel_case(loop_case, monkeypatch, mode, 'postsnapshot')


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_postattempt_continuation_cancel_is_failure_only(loop_case, monkeypatch, mode):
    _waiting_cancel_case(loop_case, monkeypatch, mode, 'postattempt')


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('failure', ['source', 'deadline'])
@pytest.mark.parametrize('started', [False, True])
def test_postclaim_first_error_survives_start_commit_then_raise(
        loop_case, sources, monkeypatch, mode, failure, started):
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_policy import DecisionBoundaryError
    from src.agent.runtime.run_session import WorkflowRunSession
    from test_decision_binding_publication import MARKER

    now = [1000.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    source = sources('rag')
    case = loop_case([], [source.tool], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool('rag_search'), clarify()])
    context = AgentContext(source.query, 'b8-first-postclaim', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={'rag_search'})
    assert waiting.metadata['continuation_id']
    nonce = waiting.metadata['continuation_id']
    before = case.store.get_run(context.trace_id)
    reopen(case, [tool('rag_search'), lambda m: finish([last_observation(m)['quality']['evidence_id']])])
    transition, append = case.store.transition_decision_continuation, case.store.append_event
    start, restore = WorkflowRunSession.start, WorkflowRunSession.restore_observations
    invalidate, finishing = WorkflowRunSession.invalidate_dynamic_publication, WorkflowRunSession.finish_dynamic
    owned = module.settle_owned_call
    claims, observed_errors, sessions, restores, corrections, finishes, committed_events = [], [], [], [], [], [], []
    expected = 'invalid_dynamic_binding' if failure == 'source' else 'task_deadline_exceeded'

    def claim(*args, **kwargs):
        value = transition(*args, **kwargs)
        assert kwargs['claim'] is True and value is True
        claims.append(deepcopy(kwargs['replacement']))
        if failure == 'source':
            source.source.close()
        else:
            now[0] += before['metadata']['decision_continuation']['snapshot']['remaining_seconds'] + 1
        return value

    async def observe_owned(*args, **kwargs):
        try:
            return await owned(*args, **kwargs)
        except DecisionBoundaryError as exc:
            if claims:
                observed_errors.append(str(exc))
            raise

    def commit_start_event(event):
        value = append(event)
        if event['event'] == 'task_started' and not started:
            committed_events.append(deepcopy(event))
            assert sessions and not sessions[0].started
            raise RuntimeError('fixture start event committed while Session remains unstarted')
        return value

    def starting(self, **kwargs):
        # The real guarded owned call has already reported the first error.
        assert claims and observed_errors == [expected]
        assert kwargs['resume_claimed'] is True
        sessions.append(self)
        value = start(self, **kwargs)
        assert started and self.started
        raise RuntimeError('fixture real Session start completed before raising')

    def restoring(self, *args, **kwargs):
        restores.append(self)
        return restore(self, *args, **kwargs)

    def invalidating(self, *args, **kwargs):
        corrections.append(self)
        return invalidate(self, *args, **kwargs)

    def finish_call(self, *args, **kwargs):
        finishes.append(self)
        return finishing(self, *args, **kwargs)

    monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
    monkeypatch.setattr(case.store, 'append_event', commit_start_event)
    monkeypatch.setattr(module, 'settle_owned_call', observe_owned)
    monkeypatch.setattr(WorkflowRunSession, 'start', starting)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restoring)
    monkeypatch.setattr(WorkflowRunSession, 'invalidate_dynamic_publication', invalidating)
    monkeypatch.setattr(WorkflowRunSession, 'finish_dynamic', finish_call)
    result = invoke(case, context, required={'rag_search'}, continuation_id=nonce, clarified_query='继续')
    assert len(claims) == 1 and observed_errors == [expected] and len(sessions) == 1
    assert result.outcome == RunOutcome.FAILED and result.metadata['stop_reason'] == expected
    assert not result.tool_results and not result.final_answer and 'continuation_id' not in result.metadata
    assert not restores and not case.model.messages and len(case.calls['rag_search']) == 1
    record = case.store.get_run(context.trace_id)
    assert record['metadata']['decision_continuation'] == claims[0]
    assert claims[0]['id'] == nonce and claims[0]['claimed_by']
    if started:
        assert sessions[0].started and corrections
        assert record['status'] == 'failed' and record['metadata'][MARKER]['reason'] == expected
        assert result.metadata['correction_durability'] == 'confirmed'
    else:
        assert not sessions[0].started and not corrections and not finishes
        assert committed_events
        assert any(e['event'] == 'task_started' for e in case.store.get_events(context.trace_id))
        assert record['status'] == 'running' and MARKER not in record['metadata']
        assert result.metadata['correction_durability'] == 'unconfirmed'


@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('history', ['property', 'no_action'])
def test_sqlite_reopen_fresh_registry_property_or_no_action(loop_case, mode, history):
    from test_decision_binding_loop import finish_last
    case = loop_case([], [PropertyCalculator()], mode=mode)
    case.model = case.loop.model = ContinuationModel(
        [tool(), clarify()] if history == 'property' else [clarify()])
    context = AgentContext('SMILES: CCO' if history == 'property' else '请计算性质',
        'b8-fresh-registry', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={'property_calculator'})
    assert waiting.metadata['continuation_id']
    before = case.store.get_run(context.trace_id)['metadata']['decision_continuation']
    previous = [deepcopy(r.to_legacy_dict()) for r in waiting.tool_results]
    fresh = _fresh_registry_reopen(loop_case, case, [PropertyCalculator()], [tool(), finish_last])
    result = invoke(fresh, context, required={'property_calculator'},
        continuation_id=before['id'], clarified_query='继续' if history == 'property' else 'SMILES: CCO')
    assert result.outcome == RunOutcome.COMPLETED, result.metadata
    assert result.metadata['tool_attempt_count'] == 1
    assert len(result.tool_results) == 1 and result.tool_results[0].success
    assert len(case.calls['property_calculator']) == (1 if history == 'property' else 0)
    assert fresh.calls['property_calculator'] == ([] if history == 'property' else ['CCO'])
    if history == 'property':
        assert [r.to_legacy_dict() for r in result.tool_results] == previous
    else:
        assert before['snapshot']['input_queries'] == ['请计算性质']
        assert before['snapshot']['results'] == [] and before['snapshot']['tool_attempt_count'] == 0
        assert result.tool_results[0].data[0]['smiles'] == 'CCO'
    saved = fresh.store.get_run(context.trace_id)
    assert saved['status'] == 'succeeded'
    assert saved['metadata']['decision_continuation']['id'] == before['id']
    assert saved['metadata']['decision_continuation']['claimed_by']



@pytest.mark.parametrize('mode', ['native', 'json'])
@pytest.mark.parametrize('kind', ['rag', 'reverse'])
def test_sqlite_reopen_new_source_generation_rejects_preclaim(loop_case, sources, monkeypatch, mode, kind):
    from pathlib import Path
    from hashlib import sha256
    from src.agent.runtime.run_session import WorkflowRunSession
    from src.agent.tools.rag_search_tool import RAGSearchTool
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.rag.service import RAGSystem
    from src.reverse_target.predictor import ReverseTargetPredictor
    from test_current_source_tool_hooks import forbid_work, provider_methods
    from tests.test_reverse_target_invocation_receipts import close_fixture_mmaps

    original = sources(kind)
    name = original.tool.name
    case = loop_case([], [original.tool], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool(name), clarify()])
    context = AgentContext(original.query, 'b8-new-generation', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={name})
    assert waiting.metadata['continuation_id']
    saved, events = case.store.get_run(context.trace_id), case.store.get_events(context.trace_id)
    # Reopen EXACT SAME persisted fixture bytes/paths with an independent
    # producer, not a copied receipt or a changed configuration.
    if kind == 'rag':
        directory = Path(original.source.csv_path).parent
        current = RAGSystem(deepcopy(original.source.config))
    else:
        directory = original.source.data_dir
        current = ReverseTargetPredictor(directory)
    source_files = sorted(p for p in directory.iterdir() if p.is_file())
    before_files = {p.name: sha256(p.read_bytes()).hexdigest() for p in source_files}
    assert before_files
    try:
        if kind == 'rag':
            asyncio.run(current.initialize())
            assert current.is_initialized and current.index_status == 'loaded'
            old_projection = original.source.capture_retrieval_eligibility()
            new_projection = current.capture_retrieval_eligibility()
            old_bytes = original.source._generation_source_projection(original.source._generation)
            new_bytes = current._generation_source_projection(current._generation)
            assert {k: v for k, v in old_bytes.items() if k != 'generation_id'} == {
                k: v for k, v in new_bytes.items() if k != 'generation_id'}
            current_tool = RAGSearchTool(current)
        else:
            current.initialize_strict()
            old_projection = original.source.capture_prediction_source()
            new_projection = current.capture_prediction_source()
            # The complete producer identity includes a fresh generation UUID;
            # content equality must not require equal generation-bound hashes.
            with original.source._strict_lock:
                old_descriptor = deepcopy(original.source._strict_source.descriptor)
            with current._strict_lock:
                new_descriptor = deepcopy(current._strict_source.descriptor)
            for descriptor, projection in ((old_descriptor, old_projection), (new_descriptor, new_projection)):
                encoded = json.dumps(descriptor, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')
                assert sha256(encoded).hexdigest() == projection.source_sha256
                assert descriptor['generation_id'] == projection.generation_id
                assert descriptor['cache_origins'] == {'morgan': 'verified_cache', 'maccs': 'verified_cache'}
            old_content = {k: v for k, v in old_descriptor.items() if k != 'generation_id'}
            new_content = {k: v for k, v in new_descriptor.items() if k != 'generation_id'}
            differences = sorted(k for k in old_content.keys() | new_content.keys()
                if k not in old_content or k not in new_content or old_content[k] != new_content[k])
            assert not differences, ('reverse_descriptor_mismatch', differences)
            assert old_projection.source_sha256 != new_projection.source_sha256
            current_tool = ReverseTargetTool(current)
        assert current is not original.source
        assert old_projection.generation_id != new_projection.generation_id
        assert old_projection.configuration_sha256 == new_projection.configuration_sha256
        assert {p.name: sha256(p.read_bytes()).hexdigest() for p in source_files} == before_files
        fresh = _fresh_registry_reopen(loop_case, case, [current_tool], [tool(name), clarify()])
        current_case = SimpleNamespace(kind=kind, source=current, tool=current_tool)
        forbidden = forbid_work(current_case, monkeypatch)
        writes = spy_writes(fresh, monkeypatch)
        hooks, validations, starts, restores = [], [], [], []
        for method in provider_methods(current_case):
            actual = getattr(current, method)
            def observed(*args, _actual=actual, _name=method, **kwargs):
                hooks.append(_name)
                return _actual(*args, **kwargs)
            monkeypatch.setattr(current, method, observed)
        validate = current_tool.validate_current_observation
        def validate_observation(*args, **kwargs):
            validations.append(True)
            return validate(*args, **kwargs)
        monkeypatch.setattr(current_tool, 'validate_current_observation', validate_observation)
        start, restore = WorkflowRunSession.start, WorkflowRunSession.restore_observations
        def starting(self, *args, **kwargs):
            starts.append(self)
            return start(self, *args, **kwargs)
        def restoring(self, *args, **kwargs):
            restores.append(self)
            return restore(self, *args, **kwargs)
        monkeypatch.setattr(WorkflowRunSession, 'start', starting)
        monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restoring)
        http_count = len(original.requests)
        result = invoke(fresh, context, required={name},
            continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
        assert result.metadata['stop_reason'] == 'continuation_rejected'
        assert result.outcome == RunOutcome.REJECTED and not result.tool_results
        assert validations and hooks, 'reject at current source authority, not unrelated configuration/setup'
        assert writes == [] and starts == [] and restores == []
        assert fresh.bus.events == [] and fresh.model.messages == [] and fresh.calls[name] == []
        assert forbidden == [] and len(original.requests) == http_count
        assert len(case.calls[name]) == 1
        assert fresh.store.get_run(context.trace_id) == saved
        assert fresh.store.get_events(context.trace_id) == events
        assert {p.name: sha256(p.read_bytes()).hexdigest() for p in source_files} == before_files
    finally:
        if kind == 'rag':
            current.close()
        else:
            current.close_strict()
            close_fixture_mmaps(current)


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_two_revision8_claimants_have_one_sqlite_winner(loop_case, monkeypatch, mode):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from src.agent.runtime.run_session import WorkflowRunSession
    from test_decision_binding_loop import finish_last

    first = loop_case([], [PropertyCalculator()], mode=mode)
    first.model = first.loop.model = ContinuationModel([clarify()])
    context = AgentContext('请计算性质', 'b8-two-claimants', user_id='owner', session_id='session')
    waiting = invoke(first, context, required={'property_calculator'})
    assert waiting.metadata['continuation_id'] and first.calls['property_calculator'] == []
    before = first.store.get_run(context.trace_id)['metadata']['decision_continuation']
    claimants = [_fresh_registry_reopen(loop_case, first, [PropertyCalculator()], [tool(), finish_last])
                 for _ in range(2)]
    owners = [WorkerOwner(), WorkerOwner()]
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    claims, starts, restores = [], [], []
    writes = [spy_writes(case, monkeypatch) for case in claimants]
    start, restore = WorkflowRunSession.start, WorkflowRunSession.restore_observations

    def index(session):
        return next(i for i, case in enumerate(claimants) if session.orchestrator.state_store is case.store)

    def starting(self, *args, **kwargs):
        with lock:
            starts.append(index(self))
        return start(self, *args, **kwargs)

    def restoring(self, *args, **kwargs):
        with lock:
            restores.append(index(self))
        return restore(self, *args, **kwargs)

    monkeypatch.setattr(WorkflowRunSession, 'start', starting)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restoring)
    for i, case in enumerate(claimants):
        actual = case.store.transition_decision_continuation
        def race(*args, _index=i, _actual=actual, **kwargs):
            assert kwargs['claim'] is True and kwargs['expected'] == before
            # Each caller already passed private replay and its final preclaim
            # checks. Separate threads/event loops make this real CAS rendezvous
            # safe; never block two coroutines on one loop with this barrier.
            barrier.wait(timeout=10)
            value = _actual(*args, **kwargs)
            with lock:
                claims.append((_index, value, deepcopy(kwargs['replacement'])))
            return value
        monkeypatch.setattr(case.store, 'transition_decision_continuation', race)

    # The executor context joins both calls on every exit; a missing participant
    # breaks the bounded barrier, not a permanent blocked task/owned root.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, case, deepcopy(context), required={'property_calculator'},
                   continuation_id=before['id'], clarified_query='SMILES: CCO', worker_owner=owners[i])
                   for i, case in enumerate(claimants)]
        results = [future.result(timeout=60) for future in futures]
    assert len(claims) == 2 and sorted(value for _, value, _ in claims) == [False, True]
    winner, payload = next((i, payload) for i, ok, payload in claims if ok)
    loser = 1 - winner
    assert results[winner].outcome == RunOutcome.COMPLETED, results[winner].metadata
    assert results[loser].outcome == RunOutcome.REJECTED
    assert results[loser].metadata['stop_reason'] == 'continuation_rejected'
    assert not results[loser].tool_results and 'continuation_id' not in results[loser].metadata
    assert starts == [winner] and restores == [winner]
    assert claimants[winner].calls['property_calculator'] == ['CCO']
    assert len(claimants[winner].model.messages) == 2
    assert claimants[loser].calls['property_calculator'] == []
    assert claimants[loser].model.messages == [] and claimants[loser].bus.events == []
    assert writes[loser] == ['transition_decision_continuation']
    assert writes[winner].count('transition_decision_continuation') == 1
    saved = first.store.get_run(context.trace_id)
    assert saved['status'] == 'succeeded'
    assert saved['metadata']['decision_continuation'] == payload
    assert payload['id'] == before['id'] and payload['claimed_by']
    assert all(owner.status == 'settled' and owner.pending_roots == 0 for owner in owners)



def _unstarted_postclaim_drain_case(loop_case, sources, monkeypatch, *, interrupt):
    """Real failed postclaim/start and owned drain; never grant Session authority."""
    import threading
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_policy import DecisionBoundaryError
    from src.agent.runtime.run_session import WorkflowRunSession
    from test_decision_binding_publication import MARKER, start_real_owned_worker
    from test_worker_ownership import signalled, pending

    source = sources('rag')
    case = loop_case([], [source.tool])
    case.model = case.loop.model = ContinuationModel([tool('rag_search'), clarify()])
    context = AgentContext(source.query, 'b8-unstarted-drain', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={'rag_search'})
    assert waiting.metadata['continuation_id']
    nonce = waiting.metadata['continuation_id']
    reopen(case, [tool('rag_search'), clarify()])
    owner = WorkerOwner()
    entered, release, exited, drain_entered = (threading.Event() for _ in range(4))
    roots, workers, claims, observed_errors, sessions = [], [], [], [], []
    returns, drains, drain_errors, lifecycle = [], [], [], []
    transition, append = case.store.transition_decision_continuation, case.store.append_event
    start, settle, owned, actual_run = (
        WorkflowRunSession.start, WorkerOwner.settle, module.settle_owned_call, case.loop._run)
    cleanup_error = RuntimeError('synthetic post-drain receipt failure')

    def claim(*args, **kwargs):
        value = transition(*args, **kwargs)
        assert kwargs['claim'] is True and value is True
        claims.append(deepcopy(kwargs['replacement']))
        source.source.close()  # Actual freshness failure, not an injected parser error.
        return value

    async def observe_owned(*args, **kwargs):
        try:
            return await owned(*args, **kwargs)
        except DecisionBoundaryError as exc:
            if claims:
                observed_errors.append(str(exc))
            raise

    def start_event_then_raise(event):
        value = append(event)  # Commit the real start event while _started is false.
        if event['event'] == 'task_started':
            assert sessions and not sessions[0].started
            if not workers:
                start_real_owned_worker(owner, entered, release, exited, roots, workers)
            raise RuntimeError('synthetic committed start event, unstarted Session')
        return value

    def starting(self, **kwargs):
        assert kwargs['resume_claimed'] is True
        assert observed_errors == ['invalid_dynamic_binding']
        sessions.append(self)
        return start(self, **kwargs)

    async def returned_before_drain(*args, **kwargs):
        value = await actual_run(*args, **kwargs)
        finalization = kwargs['_finalization']
        assert finalization.session is None and not sessions[0].started
        assert value.outcome == RunOutcome.FAILED
        assert value.metadata['stop_reason'] == 'invalid_dynamic_binding'
        assert value.metadata['correction_durability'] == 'unconfirmed'
        returns.append((value, finalization, case.store.get_run(context.trace_id),
                        case.store.get_events(context.trace_id)))
        return value  # Observe the real inner return; do not supply a fallback.

    async def draining(self):
        assert self is owner and len(returns) == 1
        drains.append(self)
        drain_entered.set()
        try:
            await settle(self)  # Actual retained root/drain including cancellation.
        except asyncio.CancelledError as exc:
            drain_errors.append(exc)
            raise
        if interrupt == 'error':
            # Fault only the receipt AFTER the real owner has physically drained.
            # This is not evidence of an actual OS join failure or fake settlement.
            assert self.status == 'settled' and self.pending_roots == 0
            drain_errors.append(cleanup_error)
            raise cleanup_error

    monkeypatch.setattr(case.store, 'transition_decision_continuation', claim)
    monkeypatch.setattr(case.store, 'append_event', start_event_then_raise)
    monkeypatch.setattr(module, 'settle_owned_call', observe_owned)
    monkeypatch.setattr(WorkflowRunSession, 'start', starting)
    monkeypatch.setattr(case.loop, '_run', returned_before_drain)
    monkeypatch.setattr(WorkerOwner, 'settle', draining)
    for name in ('restore_observations', 'finish', 'finish_dynamic', 'invalidate_dynamic_publication'):
        original = getattr(WorkflowRunSession, name)
        def observed(self, *args, _name=name, _original=original, **kwargs):
            lifecycle.append((_name, self))
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(WorkflowRunSession, name, observed)

    async def exercise():
        task = asyncio.create_task(case.loop.run(context, request_kind='scientific',
            allowed_tools={'rag_search'}, required_tools={'rag_search'},
            requirements=requirements(), event_bus=case.bus, worker_owner=owner,
            continuation_id=nonce, clarified_query='继续'))
        try:
            await signalled(entered)
            await signalled(drain_entered)
            assert len(returns) == len(sessions) == len(claims) == 1
            fallback, finalization, record, history = returns[0]
            frozen = deepcopy(fallback.to_legacy_dict())
            assert record['status'] == 'running' and MARKER not in record['metadata']
            assert record['metadata']['decision_continuation'] == claims[0]
            assert claims[0]['id'] == nonce and claims[0]['claimed_by']
            assert not sessions[0].started and finalization.session is None
            assert lifecycle == [] and case.model.messages == [] and len(case.calls['rag_search']) == 1
            for _ in range(3 if interrupt == 'cancel' else 1):
                if interrupt == 'cancel':
                    task.cancel()
                await pending(task)
                assert owner._sealed and owner.status == 'pending' and owner.pending_roots == 1
                assert not roots[0].finished and not exited.is_set()
                assert not sessions[0].started and finalization.session is None
                assert case.store.get_run(context.trace_id) == record
                assert case.store.get_events(context.trace_id) == history
                assert lifecycle == []
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            # These physical/lifecycle checks precede the decisive regression
            # assertion, so rescue cannot turn a lost primary failure green.
            assert drains == [owner] and owner.status == 'settled' and owner.pending_roots == 0
            assert roots[0].finished and exited.is_set() and len(drain_errors) == 1
            if interrupt == 'cancel':
                assert isinstance(drain_errors[0], asyncio.CancelledError)
            else:
                assert drain_errors[0] is cleanup_error
            assert not sessions[0].started and finalization.session is None and lifecycle == []
            assert observed_errors == ['invalid_dynamic_binding']
            assert case.store.get_run(context.trace_id) == record
            assert case.store.get_events(context.trace_id) == history
            assert case.model.messages == [] and len(case.calls['rag_search']) == 1
            assert not isinstance(result, BaseException), (
                'unstarted postclaim FAILED was lost during owner drain', type(result).__name__)
            assert result.trace_id == context.trace_id and result.error == fallback.error
            assert result.metadata['failed_boundary'] == frozen['metadata']['failed_boundary']
            assert result.outcome == RunOutcome.FAILED
            assert result.metadata['stop_reason'] == 'invalid_dynamic_binding'
            assert result.metadata['correction_durability'] == 'unconfirmed'
            assert not result.final_answer and not result.tool_results and 'continuation_id' not in result.metadata
        finally:
            release.set()
            await asyncio.gather(task, *workers, return_exceptions=True)

    asyncio.run(exercise())


def test_unstarted_postclaim_failure_survives_real_drain_cancellation(loop_case, sources, monkeypatch):
    _unstarted_postclaim_drain_case(loop_case, sources, monkeypatch, interrupt='cancel')


def test_unstarted_postclaim_failure_survives_post_drain_error(loop_case, sources, monkeypatch):
    _unstarted_postclaim_drain_case(loop_case, sources, monkeypatch, interrupt='error')


def _resumed_source_close_case(loop_case, sources, monkeypatch, *, mode, boundary):
    import src.agent.harness.decision_loop as module
    from src.agent.harness.decision_binding_continuation import ValidatedReplay
    from src.agent.harness.decision_policy import DecisionBoundaryError
    from src.agent.runtime.run_session import WorkflowRunSession
    from test_decision_binding_publication import MARKER, TERMINALS, assert_sanitized

    source = sources('rag')
    case = loop_case([], [source.tool], mode=mode)
    case.model = case.loop.model = ContinuationModel([tool('rag_search'), clarify()])
    context = AgentContext(source.query, 'b8-resumed-source-close', user_id='owner', session_id='session')
    waiting = invoke(case, context, required={'rag_search'})
    nonce = waiting.metadata['continuation_id']
    initial = deepcopy(case.store.get_run(context.trace_id)['metadata']['decision_continuation'])
    initial_history = deepcopy(case.store.get_events(context.trace_id))
    assert initial['id'] == nonce and 'claimed_by' not in initial
    assert len(initial['snapshot']['results']) == 1 and len(case.calls['rag_search']) == 1
    http_count = len(source.requests)
    reopen(case, [clarify()])
    owner = WorkerOwner()
    start, restore = WorkflowRunSession.start, WorkflowRunSession.restore_observations
    check, compare = ValidatedReplay.verify_claim, ValidatedReplay.verify_restored
    seal, owned, settle = module.seal_observation, module.settle_owned_call, WorkerOwner.settle
    transition = case.store.transition_decision_continuation
    phases, sessions, claim_checks, completed_checks, restores, comparisons, seals = [], [], [], [], [], [], []
    claims, publications, closed, errors, drains = [], [], [], [], []

    def starting(self, *args, **kwargs):
        value = start(self, *args, **kwargs)
        sessions.append(self)
        phases.append('started')
        return value

    def checked(self, payload):
        value = check(self, payload)  # Actual source/store verification, not a supplied success.
        claim_checks.append(self)
        return value

    async def observed_owned(*args, **kwargs):
        count = len(claim_checks)
        try:
            value = await owned(*args, **kwargs)
        except DecisionBoundaryError as exc:
            if closed and kwargs.get('worker_owner') is owner:
                errors.append(str(exc))
                phases.append('owned_source_rejected')
            raise
        if kwargs.get('worker_owner') is owner and sessions and len(claim_checks) > count:
            completed_checks.append(len(claim_checks))
            phases.append('prechecked')
        return value

    def close_source():
        assert not closed
        source.source.close()  # Close the real attached generation; never mock validation failure.
        closed.append(True)
        phases.append('closed')

    def restoring(self, *args, **kwargs):
        assert self is sessions[0] and self.started and self._resume_claimed
        assert completed_checks and phases[-1] == 'prechecked'
        assert len(claims) == 1 and claims[0]['replacement']['claimed_by']
        phases.append('restore_entered')
        if boundary == 'restore':
            close_source()  # AFTER successful owned precheck, BEFORE delegating the real restore.
        value = restore(self, *args, **kwargs)
        restores.append(deepcopy([r.to_legacy_dict() for r in self.results]))
        phases.append('restored')
        return value

    def compared(self, results):
        value = compare(self, results)
        if sessions and results is sessions[0].results:
            comparisons.append(True)
            phases.append('compared')
        return value

    def sealed(result, session):
        value = seal(result, session)
        if sessions and session is sessions[0]:
            seals.append(result.quality['evidence_id'])
            phases.append('sealed')
        return value

    def transitioned(*args, **kwargs):
        value = transition(*args, **kwargs)  # Real SQLite CAS must actually commit first.
        assert value is True
        record = deepcopy(case.store.get_run(context.trace_id))
        entry = dict(expected=deepcopy(kwargs['expected']), replacement=deepcopy(kwargs['replacement']),
                     record=record, history=deepcopy(case.store.get_events(context.trace_id)))
        if kwargs['claim']:
            claims.append(entry)
            phases.append('claimed')
        else:
            assert boundary == 'waiting_cas' and not closed
            assert restores and comparisons == [True] and len(seals) == 1
            assert record['status'] == 'waiting_for_input'
            assert record['metadata']['decision_continuation'] == entry['replacement']
            publications.append(entry)
            phases.append('published')
            close_source()  # Resumed waiting publication: close only AFTER the successful new CAS.
        return value

    async def drained(self):
        try:
            return await settle(self)  # Keep real cleanup/join, including failure paths.
        finally:
            drains.append(self)

    monkeypatch.setattr(WorkflowRunSession, 'start', starting)
    monkeypatch.setattr(WorkflowRunSession, 'restore_observations', restoring)
    monkeypatch.setattr(ValidatedReplay, 'verify_claim', checked)
    monkeypatch.setattr(ValidatedReplay, 'verify_restored', compared)
    monkeypatch.setattr(module, 'seal_observation', sealed)
    monkeypatch.setattr(module, 'settle_owned_call', observed_owned)
    monkeypatch.setattr(case.store, 'transition_decision_continuation', transitioned)
    monkeypatch.setattr(WorkerOwner, 'settle', drained)
    result = invoke(case, context, required={'rag_search'}, worker_owner=owner,
                    continuation_id=nonce, clarified_query='继续')
    assert drains == [owner] and owner.status == 'settled' and owner.pending_roots == 0
    assert closed == [True] and errors and set(errors) == {'invalid_dynamic_binding'}
    assert len(sessions) == len(restores) == len(claims) == 1 and completed_checks
    assert claims[0]['expected'] == initial
    consumed = claims[0]['replacement']
    assert consumed['id'] == nonce and consumed['claimed_by']
    assert {k: v for k, v in consumed.items() if k != 'claimed_by'} == initial
    assert claims[0]['record']['status'] == 'running'
    assert claims[0]['record']['metadata']['decision_continuation'] == consumed
    assert_sanitized(result, reason='invalid_dynamic_binding')
    saved, history = case.store.get_run(context.trace_id), case.store.get_events(context.trace_id)
    assert saved['status'] == 'failed' and saved['metadata'][MARKER]['reason'] == 'invalid_dynamic_binding'
    assert history[:len(initial_history)] == initial_history
    terminals = [event for event in case.bus.events if event.event in TERMINALS]
    assert terminals and terminals[-1].payload['publication_stage'] == 'invalidated'
    assert sum(event.payload.get('publication_stage') == 'invalidated' for event in terminals) == 1
    assert all(event.payload.get('answer_released') is False for event in terminals)
    assert len(case.calls['rag_search']) == 1 and len(source.requests) == http_count
    if boundary == 'restore':
        assert phases.index('prechecked') < phases.index('closed') < phases.index('restored')
        assert phases.index('restored') < phases.index('owned_source_rejected')
        assert not comparisons and not seals and not publications and not case.model.messages
        assert saved['metadata']['decision_continuation'] == consumed
        assert result.metadata['failed_boundary'] == 'continuation_restore'
        rejected_nonces = [nonce]
    else:
        assert comparisons == [True] and len(seals) == 1 and len(publications) == 1
        assert phases.index('restored') < phases.index('compared') < phases.index('sealed')
        assert phases.index('sealed') < phases.index('published') < phases.index('closed')
        assert phases.index('closed') < phases.index('owned_source_rejected')
        published = publications[0]
        assert published['expected'] == consumed
        replacement = published['replacement']
        assert replacement['id'] != nonce and 'claimed_by' not in replacement
        assert replacement['snapshot']['decision_protocol_revision'] == 8
        assert replacement['snapshot']['results'] == initial['snapshot']['results']
        assert replacement['snapshot']['input_queries'] == initial['snapshot']['input_queries'] + ['继续']
        assert saved['metadata']['decision_continuation'] == replacement
        assert history[:len(published['history'])] == published['history']
        assert len(case.model.messages) == 1 and result.metadata['failed_boundary'] == 'waiting_publication'
        assert all(set(event.payload) == {'terminal_attempt_id', 'observed_outcome',
            'publication_stage', 'answer_released'} for event in terminals[:-1])
        rejected_nonces = [nonce, replacement['id']]
    # Neither the consumed input nor a committed-then-invalidated new nonce can
    # reopen science. Rejection must not rewrite the frozen corrected history.
    model_count, phase_count, event_count = len(case.model.messages), len(phases), len(case.bus.events)
    for rejected_nonce in rejected_nonces:
        rejected_owner = WorkerOwner()
        rejected = invoke(case, context, required={'rag_search'}, worker_owner=rejected_owner,
                          continuation_id=rejected_nonce, clarified_query='继续')
        assert rejected.outcome == RunOutcome.REJECTED
        assert rejected.metadata['stop_reason'] == 'continuation_rejected'
        assert not rejected.tool_results and not rejected.final_answer and 'continuation_id' not in rejected.metadata
        assert drains.count(rejected_owner) == 1 and rejected_owner.status == 'settled'
        assert rejected_owner.pending_roots == 0
        assert case.store.get_run(context.trace_id) == saved and case.store.get_events(context.trace_id) == history
        assert len(case.model.messages) == model_count and len(case.bus.events) == event_count
        assert len(phases) == phase_count and len(claims) == len(restores) == 1
        assert len(case.calls['rag_search']) == 1 and len(source.requests) == http_count


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_source_close_before_real_restore_is_failure_only(loop_case, sources, monkeypatch, mode):
    _resumed_source_close_case(loop_case, sources, monkeypatch, mode=mode, boundary='restore')


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_source_close_after_resumed_waiting_cas_is_failure_only(loop_case, sources, monkeypatch, mode):
    _resumed_source_close_case(loop_case, sources, monkeypatch, mode=mode, boundary='waiting_cas')
