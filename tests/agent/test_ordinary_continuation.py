"""Semantic revision7 replay with real Session/SQLite and offline scripted calls."""
import json
from dataclasses import replace

import pytest

from src.agent.evidence import EvidenceLedger
from src.agent.contracts import RunOutcome
from test_decision_loop import setup_loop, clarify, finish, tool
from test_decision_continuation import ContinuationModel
from test_ordinary_admission_budget import context, carry, invoke, clock, admission


def resume(b, waiting, *, incoming=None, exchange=None, **kw):
    return invoke(b, incoming=incoming or carry(now=400.0, allowance=250.0, expiry=1000.0),
        exchange=exchange, continuation_id=waiting.metadata['continuation_id'], clarified_query='Please explain', **kw)


def test_revision7_checkpoint_and_resume_preserve_original_receipt_and_prompt(setup_loop, clock):
    b = setup_loop([clarify(), finish(text='Hello', kind='chat')])
    exchange = admission.AdmissionExchange()
    waiting = invoke(b, exchange=exchange)
    saved = b.store.get_run('trace-1')['metadata']['decision_continuation']['snapshot']
    assert saved['decision_protocol_revision'] == 7
    assert saved['total_model_requests'] == 2 and saved['intent_requests'] == 1
    assert exchange.checkpoint.created_at == 10.0
    assert exchange.checkpoint.remaining_seconds == 300.0
    assert 'created_at' not in repr(saved) and 'deadline' not in repr(saved)
    clock[0] = 400.0
    ctx = context()
    ctx.metadata['turn_id'] = 'new-transport-turn'
    result = resume(b, waiting, ctx=ctx)
    assert result.success, result.metadata
    assert result.metadata['total_model_requests'] == 3
    assert len(result.metadata['model_calls']) == 2
    assert result.metadata['ordinary_admission']['intent_record']['turn_id'] == 'turn-1'
    assert b.model.messages[0][0] == b.model.messages[1][0]


@pytest.mark.parametrize('fault', ['intent', 'total', 'binding', 'capability', 'revision', 'calls', 'unsafe', 'record'])
def test_tampered_semantic_snapshot_rejected_before_cas(setup_loop, clock, monkeypatch, fault):
    b = setup_loop([clarify(), finish(text='Hello', kind='chat')])
    waiting = invoke(b)
    record = b.store.get_run('trace-1')
    payload = record['metadata']['decision_continuation']
    snap = payload['snapshot']
    if fault == 'intent': snap['intent_requests'] = 0
    if fault == 'total': snap['total_model_requests'] = 1
    if fault == 'binding': snap['ordinary_admission']['binding']['history_digest'] = '0' * 64
    if fault == 'capability': snap['ordinary_admission']['binding']['capability_digest'] = '0' * 64
    if fault == 'revision': snap['decision_protocol_revision'] = 6
    if fault == 'calls': snap['model_calls'].append(dict(snap['model_calls'][0]))
    if fault == 'unsafe': snap['proposals'][0]['decision']['question'] = '该分子的 pIC50 = 7.2'
    if fault == 'record': snap['ordinary_admission']['intent_record']['turn_id'] = 'substituted'
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    claims = []
    monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    monkeypatch.setattr(b.store, 'transition_decision_continuation', lambda *a, **kw: claims.append(kw))
    clock[0] = 400.0
    result = resume(b, waiting)
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not claims and len(b.model.messages) == 1 and not b.tools[0].inputs


def test_fifteen_decisions_plus_intent_never_resets_after_clarify(setup_loop, clock):
    b = setup_loop([clarify()] * 16)
    waiting = invoke(b)
    for index in range(2, 16):
        clock[0] += 10.0
        waiting = resume(b, waiting, incoming=carry(now=clock[0], allowance=250.0, expiry=1000.0))
        assert waiting.metadata['model_requests'] == index, waiting.metadata
    assert waiting.metadata['total_model_requests'] == 16
    before = b.store.get_run('trace-1')
    result = resume(b, waiting, incoming=carry(now=clock[0], allowance=250.0, expiry=1000.0))
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert len(b.model.messages) == 15 and not b.tools[0].inputs
    assert b.store.get_run('trace-1') == before


@pytest.mark.parametrize('expired', ['ttl', 'cap', 'after-cas'])
def test_expiry_checked_immediately_before_and_after_claim(setup_loop, clock, monkeypatch, expired):
    b = setup_loop([clarify(), finish(text='Hello', kind='chat')])
    waiting = invoke(b)
    clock[0] = 400.0
    incoming = carry(now=400.0, allowance=10.0, expiry=400.0 if expired == 'ttl' else 1000.0)
    if expired == 'cap': clock[0] = 410.0
    original, claims = b.store.transition_decision_continuation, []
    def transition(*a, **kw):
        if kw['claim']:
            claims.append(kw)
            if expired == 'after-cas': clock[0] = 411.0
        return original(*a, **kw)
    monkeypatch.setattr(b.store, 'transition_decision_continuation', transition)
    result = resume(b, waiting, incoming=incoming)
    assert len(claims) == (1 if expired == 'after-cas' else 0)
    assert len(b.model.messages) == 1 and not result.success
    assert result.metadata['stop_reason'] == ('task_deadline_exceeded' if expired == 'after-cas' else 'continuation_rejected')


@pytest.mark.parametrize('fail', [False, True])
def test_checkpoint_only_after_actual_publication_and_delivery_costs_credit(setup_loop, clock, monkeypatch, fail):
    b = setup_loop([clarify()])
    exchange = admission.AdmissionExchange()
    original = b.store.transition_decision_continuation
    def transition(*a, **kw):
        assert exchange.checkpoint is None
        clock[0] = 30.0
        if fail: raise OSError('offline publication fault')
        return original(*a, **kw)
    monkeypatch.setattr(b.store, 'transition_decision_continuation', transition)
    incoming = carry()
    if fail:
        with pytest.raises(OSError): invoke(b, incoming=incoming, exchange=exchange)
        assert exchange.checkpoint is None
    else:
        invoke(b, incoming=incoming, exchange=exchange)
        checkpoint = exchange.checkpoint
        assert checkpoint.created_at == 10.0 and checkpoint.remaining_seconds == 300.0
        assert admission.settled_waiting_credit(incoming.segment, now=clock[0],
            snapshot_remaining=checkpoint.remaining_seconds) == 280.0


def test_every_stored_clarification_including_nonterminal_is_regated(setup_loop, clock, monkeypatch):
    b = setup_loop([clarify(), clarify(), finish(text='Hello', kind='chat')])
    first = invoke(b)
    clock[0] = 400.0
    second = resume(b, first)
    record = b.store.get_run('trace-1')
    payload = record['metadata']['decision_continuation']
    payload['snapshot']['proposals'][0]['decision']['question'] = '该分子的 pIC50 = 7.2'
    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
    claims = []
    monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    monkeypatch.setattr(b.store, 'transition_decision_continuation', lambda *a, **kw: claims.append(kw))
    result = resume(b, second)
    assert result.metadata['stop_reason'] == 'continuation_rejected'
    assert not claims and len(b.model.messages) == 2


def test_scientific_revision7_preserves_twelve_reservation_ceiling_and_evidence(setup_loop, clock):
    b = setup_loop([tool(), clarify(), tool()])
    adapter = b.registry.resolve('property_calculator')
    adapter.spec = replace(adapter.spec, retry_policy=replace(adapter.spec.retry_policy, max_attempts=12))
    ctx = context('SMILES: CCO')
    incoming = carry(ctx, intent=0, scientific=True)
    waiting = invoke(b, ctx=ctx, scientific=True, incoming=incoming)
    assert waiting.metadata['tool_budget_reserved'] == 12
    evidence = waiting.tool_results[0].to_legacy_dict()
    clock[0] = 400.0
    result = invoke(b, ctx=ctx, scientific=True,
        incoming=carry(ctx, intent=0, scientific=True, now=400.0, allowance=250.0, expiry=1000.0),
        continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCN')
    assert result.metadata['stop_reason'] == 'tool_budget_exhausted'
    assert result.metadata['tool_budget_reserved'] == 12 and len(b.tools[0].inputs) == 1
    assert result.tool_results[0].to_legacy_dict() == evidence
    assert 'Frozen ordinary capabilities' not in repr(b.model.messages)


@pytest.mark.parametrize('field', ['model_generation', 'capability_generation'])
def test_current_server_carry_generation_cannot_be_chosen_by_snapshot(setup_loop, clock, field):
    b = setup_loop([clarify()])
    waiting = invoke(b)
    incoming = carry(now=400.0, allowance=250.0, expiry=1000.0)
    cap = incoming.capability_snapshot().model_copy(update={field: 'new-generation'})
    binding = incoming.binding()
    binding[field] = 'new-generation'
    binding['capability_digest'] = admission.capability_digest(cap)
    record = incoming.intent_record()
    record[field] = 'new-generation'
    incoming = replace(incoming, binding_json=json.dumps(binding), capability_json=cap.model_dump_json(),
                       intent_record_json=json.dumps(record))
    clock[0] = 400.0
    result = resume(b, waiting, incoming=incoming)
    assert result.metadata['stop_reason'] == 'continuation_rejected' and len(b.model.messages) == 1
