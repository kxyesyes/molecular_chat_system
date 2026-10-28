"""Stable Web action identity through real Pi/RDKit and waiting replay.

Model responses are scripted, not live-model acceptance. Reference-claim tests
only verify forwarding; the parent-owned session validates the claim itself.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.evidence import EvidenceLedger
from src.agent.pi_runtime.runtime import PiAgentRuntime
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.scientific_session import controller
from src.agent.scientific_session.decision_policy import DecisionBoundaryError
from src.agent.scientific_session.web_bindings import WebBindingResolver
from src.agent.scientific_session.web_requirements import compile_web_requirements
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from tests.agent.test_decision_loop import setup_loop, tool, clarify, finish_last


WEB = 'medchat-web-v1'
ALLOWED = {'property_calculator'}


def make_case(setup_loop, profile, *, mode='native'):
    allowed = ALLOWED | {'drug_likeness_assessment'} if profile == WEB else ALLOWED
    tools = [PropertyCalculator()]
    context = AgentContext(
        '计算 CCO 的理化性质和类药性，不要 ADMET 或活性' if profile == WEB else 'SMILES: CCO',
        'stable-action-context', user_id='owner', session_id='session',
        active_skill='admet_assessment' if profile == WEB else None,
    )
    if profile == WEB:
        requirements = compile_web_requirements(context, allowed_tools=allowed)
        step, pending = requirements.steps
        proposal = tool(arguments={'step_ref': step.name})
        # Pause while the compiled drug-likeness obligation is still pending.
        final = tool(pending.tool_name, {'step_ref': pending.name})
        tools.append(DrugLikenessAssessment())
    else:
        requirements = (dict(version='2', profile=profile,
            molecular_results=[dict(tool_name='property_calculator', required_metrics=['logp'])])
            if profile is not None else None)
        proposal = tool()
        final = finish_last
    case = setup_loop([proposal, clarify(), proposal, final],
                      tools, binding_profile=profile, mode=mode)

    def run(**kwargs):
        owner = WorkerOwner()
        result = asyncio.run(controller.ScientificSession(case.loop).run(
            context, request_kind='scientific', allowed_tools=allowed, required_tools=allowed,
            requirements=requirements, event_bus=case.bus, worker_owner=owner, **kwargs))
        assert owner.status == 'settled' and owner.pending_roots == 0
        return result

    return case, context, proposal, run


@pytest.mark.parametrize('profile', [WEB, B1_PROFILE_REVISION])
def test_same_context_action_identity_does_not_follow_web_model_uuid(setup_loop, monkeypatch, profile):
    steps = []
    append = WorkflowRunSession.append_step

    def record_step(session, step):
        steps.append(deepcopy(step))
        return append(session, step)

    monkeypatch.setattr(WorkflowRunSession, 'append_step', record_step)
    seen = []
    for offset in (100, 200):
        identities = iter(f'{value:032x}' for value in range(offset, offset + 10))
        monkeypatch.setattr(controller, 'uuid4', lambda: SimpleNamespace(hex=next(identities)))
        case, context, proposal, run = make_case(setup_loop, profile)
        waiting = run()
        assert waiting.metadata.get('waiting_for_input'), waiting.metadata
        snapshot = case.store.get_run(context.trace_id)['metadata']['decision_continuation']['snapshot']
        result, = waiting.tool_results
        step = steps[-1]
        model_id = snapshot['model_calls'][0]['decision_id']
        assert model_id == f'{offset:032x}' == step.metadata['decision_id']
        assert snapshot['proposals'][0]['decision_id'] == model_id
        assert snapshot['proposals'][0]['decision'] == proposal.model_dump()
        assert result.success and result.data[0]['smiles'] == 'CCO'
        action = result.quality['binding_proof']['action_sha256']
        expected = 'web-' + action if profile == WEB else model_id
        assert step.name == step.output_key == expected
        assert result.quality['step_id'] == result.quality['output_key'] == expected
        assert set(snapshot['binding_records']) == {expected}
        assert snapshot['binding_records'][expected]['action_sha256'] == action
        seen.append((step.name, model_id, action))
    assert seen[0][1] != seen[1][1]
    assert seen[0][2] == seen[1][2]
    assert (seen[0][0] == seen[1][0]) is (profile == WEB)


@pytest.mark.parametrize('profile,revision', [(WEB, 13), (B1_PROFILE_REVISION, 11)])
@pytest.mark.parametrize('mode', ['native', 'json'])
def test_waiting_replay_uses_profile_step_identity(setup_loop, profile, revision, mode):
    case, context, _, run = make_case(setup_loop, profile, mode=mode)
    waiting = run()
    assert waiting.metadata.get('waiting_for_input'), waiting.metadata
    snapshot = case.store.get_run(context.trace_id)['metadata']['decision_continuation']['snapshot']
    assert snapshot['decision_protocol_revision'] == revision
    before = waiting.tool_results[0].to_legacy_dict()
    previous = case.loop
    case.loop = PiAgentRuntime(case.model, previous.registry, case.store,
                              mode=mode, binding_profile=profile)
    resumed = run(continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
    assert resumed.success, resumed.metadata
    assert resumed.metadata['reused_decisions'] == 1
    assert resumed.tool_results[0].to_legacy_dict() == before
    executions = case.store.get_tool_executions(context.trace_id)
    assert len(executions) == (2 if profile == WEB else 1)
    assert sum(item['tool_name'] == 'property_calculator' for item in executions) == 1
    assert len(case.model.messages) == 4


def test_web_revision12_is_rejected_without_deleting_waiting_data(setup_loop, monkeypatch):
    from src.agent.scientific_session import decision_binding_continuation as codec
    snapshot_payload = codec.snapshot_payload

    def old_snapshot(*args, **kwargs):
        payload, deadline = snapshot_payload(*args, **kwargs)
        payload['snapshot']['decision_protocol_revision'] = 12
        payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
        return payload, deadline

    # Publish a correctly checksummed old-version fixture through the real CAS;
    # do not bypass the store's continuation-write protection.
    monkeypatch.setattr(codec, 'snapshot_payload', old_snapshot)
    case, context, _, run = make_case(setup_loop, WEB)
    waiting = run()
    assert waiting.metadata.get('waiting_for_input'), waiting.metadata
    monkeypatch.setattr(codec, 'snapshot_payload', snapshot_payload)
    before = case.store.get_run(context.trace_id)
    assert before['metadata']['decision_continuation']['snapshot']['decision_protocol_revision'] == 12
    receipts = case.store.get_tool_executions(context.trace_id)
    checkpoint = case.store.latest_checkpoint(context.trace_id)
    events = case.store.get_events(context.trace_id)
    monkeypatch.setattr(case.store, 'transition_decision_continuation',
                        lambda *a, **k: pytest.fail('old Web revision must not reach claim'))
    rejected = run(continuation_id=waiting.metadata['continuation_id'], clarified_query='继续')
    assert rejected.metadata['stop_reason'] == 'continuation_rejected'
    assert case.store.get_run(context.trace_id) == before
    assert case.store.get_tool_executions(context.trace_id) == receipts
    assert case.store.latest_checkpoint(context.trace_id) == checkpoint
    assert case.store.get_events(context.trace_id) == events
    assert len(case.model.messages) == 2


@pytest.mark.parametrize('resuming', [False, True])
def test_web_reference_claim_is_verified_only_after_owner_release(setup_loop, monkeypatch, resuming):
    claim = dict(receipt={'fixture': 'protocol-only'}, admitted=False, verified=False, resuming=resuming)
    received = []
    start = WorkflowRunSession.start
    settle = WorkerOwner.settle
    finalizations = []
    make_finalization = controller._BindingFinalization
    case, _, _, run = make_case(setup_loop, WEB)
    original_waiting = run() if resuming else None

    def capture_finalization():
        value = make_finalization()
        finalizations.append(value)
        return value

    async def settle_before_verification(owner):
        assert claim['verified'] is False
        await settle(owner)
        assert owner.status == 'settled' and claim['verified'] is False

    def receive(session, *, resume_claimed=False, reference_claim=None):
        received.append((reference_claim, resume_claimed))
        # Protocol receiver only: this test must not pretend to authorize or
        # consume a durable claim. Its actual validation belongs to RunSession.
        result = start(session, resume_claimed=resume_claimed)
        reference_claim['admitted'] = True
        return result

    monkeypatch.setattr(WorkflowRunSession, 'start', receive)
    monkeypatch.setattr(WorkerOwner, 'settle', settle_before_verification)
    monkeypatch.setattr(controller, '_BindingFinalization', capture_finalization)
    options = dict(continuation_id=original_waiting.metadata['continuation_id'], clarified_query='继续') if resuming else {}
    waiting = run(reference_claim=claim, **options)
    assert waiting.success if resuming else waiting.metadata.get('waiting_for_input'), waiting.metadata
    assert len(case.model.messages) == (4 if resuming else 2)
    assert received == [(claim, resuming)] and received[0][0] is claim
    assert claim['verified'] is True and claim['admitted'] is True
    finalization, = finalizations
    assert finalization.phase == 'released' and finalization.failure is None
    assert not finalization.session.publication_invalidated


@pytest.mark.parametrize('profile', [B1_PROFILE_REVISION, None])
def test_reference_claim_cannot_acquire_nonweb_authority(setup_loop, monkeypatch, profile):
    claim = dict(receipt={}, admitted=False, verified=False, resuming=False)
    case, _, _, run = make_case(setup_loop, profile)
    monkeypatch.setattr(WorkflowRunSession, 'start',
                        lambda *a, **k: pytest.fail('non-Web claim must not reach session start'))
    with pytest.raises(DecisionBoundaryError, match='invalid_reference_claim'):
        run(reference_claim=claim)
    assert not case.model.messages and not claim['admitted'] and not claim['verified']


def test_reference_resume_marker_cannot_replace_continuation_nonce(setup_loop, monkeypatch):
    claim = dict(receipt={}, admitted=False, verified=False, resuming=True)
    case, _, _, run = make_case(setup_loop, WEB)
    called = []

    def start(*args, **kwargs):
        called.append(kwargs)
        raise ValueError('test refuses unexpected session start')

    monkeypatch.setattr(WorkflowRunSession, 'start', start)
    result = run(reference_claim=claim)
    assert called == [], 'a local carrier flag is not a successful continuation CAS'
    assert not result.success and result.metadata['stop_reason'] == 'invalid_reference_claim'
    assert not case.model.messages and not claim['admitted'] and not claim['verified']


@pytest.mark.parametrize('fault', ['owner-drain', 'expired', 'publication', 'no-session'])
def test_reference_claim_failure_never_marks_verified(setup_loop, monkeypatch, fault):
    claim = dict(receipt={}, admitted=False, verified=False, resuming=False)
    finalizations = []
    make_finalization = controller._BindingFinalization
    start, settle = WorkflowRunSession.start, WorkerOwner.settle

    def capture_finalization():
        value = make_finalization()
        finalizations.append(value)
        return value

    def receive(session, *, resume_claimed=False, reference_claim=None):
        if fault == 'no-session':
            raise RuntimeError('fixture start failure')
        result = start(session)
        reference_claim['admitted'] = True
        return result

    async def drain(owner):
        await settle(owner)
        finalization, = finalizations
        assert claim['verified'] is False
        if fault == 'owner-drain':
            raise RuntimeError('fixture drain failure')
        if fault == 'expired':
            finalization.deadline = finalization.clock() - 1
        if fault == 'publication':
            finalization.session.invalidate_dynamic_publication(
                reason='invalid_dynamic_binding', boundary='fixture_owner_drain')

    monkeypatch.setattr(controller, '_BindingFinalization', capture_finalization)
    monkeypatch.setattr(WorkflowRunSession, 'start', receive)
    monkeypatch.setattr(WorkerOwner, 'settle', drain)
    _, _, _, run = make_case(setup_loop, WEB)
    if fault == 'no-session':
        with pytest.raises(RuntimeError, match='fixture start failure'):
            run(reference_claim=claim)
        assert finalizations[0].session is None
    else:
        result = run(reference_claim=claim)
        assert result.metadata['publication_invalidated'] is True
        assert finalizations[0].failure is not None
    assert claim['verified'] is False


@pytest.mark.parametrize('fault', ['none', 'validator', 'configuration'])
def test_web_checkpoint_validation_is_guarded(setup_loop, monkeypatch, fault):
    case, _, _, run = make_case(setup_loop, WEB)
    constructor, append = WorkflowRunSession.__init__, WorkflowRunSession.append_step
    callbacks, invoked = [], []
    result, checkpoint = object(), {'fixture': 'checkpoint-protocol-only'}

    def receive(session, *args, **kwargs):
        callbacks.append(kwargs.get('checkpoint_validate'))
        return constructor(session, *args, **kwargs)

    def validate(resolver, observed, step, saved):
        assert observed is result and saved is checkpoint
        invoked.append(step.name)
        if fault == 'validator':
            raise DecisionBoundaryError('invalid_dynamic_binding')
        if fault == 'configuration':
            case.loop.config_generation = 'changed-during-checkpoint-validation'
        return True

    def validate_before_append(session, step):
        callback = callbacks[-1]
        assert callable(callback), 'Web checkpoint validator was not connected'
        assert callback(result, step, checkpoint) is True
        return append(session, step)

    monkeypatch.setattr(WorkflowRunSession, '__init__', receive)
    monkeypatch.setattr(WorkflowRunSession, 'append_step', validate_before_append)
    monkeypatch.setattr(WebBindingResolver, 'validate_checkpoint', validate, raising=False)
    outcome = run()
    assert len(invoked) == 1
    if fault == 'none':
        assert outcome.metadata.get('waiting_for_input'), outcome.metadata
    else:
        assert not outcome.success
        assert outcome.metadata['publication_invalidated'] is True
        assert outcome.metadata['stop_reason'] == (
            'tool_configuration_changed' if fault == 'configuration' else 'invalid_dynamic_binding')
