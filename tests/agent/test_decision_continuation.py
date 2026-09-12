"""Explicit continuation contracts; no production service or external model."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from test_decision_loop import setup_loop, ScriptedModel, CountingTool, tool, clarify, finish_last
from src.agent.contracts import AgentContext
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus


class ContinuationModel(ScriptedModel):
    model_name = 'offline-continuation-model'
    def __init__(self, decisions):
        super().__init__(decisions)
        self.prefix = uuid4().hex
    async def decide(self, *a, **kw):
        response = await super().decide(*a, **kw)
        return replace(response, tool_call_id=self.prefix + response.tool_call_id)


def owned_context():
    return AgentContext('SMILES: CCO', 'owned-trace', user_id='owner', session_id='browser-1')


def start(b, decisions, **kw):
    b.model = b.loop.model = ContinuationModel(decisions)
    return invoke(b, **kw)


def invoke(b, context=None, **kw):
    return asyncio.run(b.loop.run(context or owned_context(), request_kind='scientific',
        allowed_tools=kw.pop('allowed_tools', {'property_calculator'}),
        required_tools=kw.pop('required_tools', {'property_calculator'}), event_bus=b.bus, **kw))


def fresh(b, decisions):
    b.store = SQLiteAgentStateStore(b.store.db_path)
    b.bus = AgentEventBus(state_store=b.store)
    b.model = ContinuationModel(decisions)
    b.loop = ModelDecisionLoop(b.model, b.registry, b.store,
        max_model_requests=b.loop.max_model_requests, max_tool_attempts=b.loop.max_tool_attempts,
        timeout_seconds=b.loop.timeout_seconds)


def test_reopen_store_resume_preserves_budget_evidence_and_cached_action(setup_loop):
    b = setup_loop([])
    waiting = start(b, [tool(), clarify()])
    nonce = waiting.metadata['continuation_id']
    before = waiting.tool_results[0].to_legacy_dict()
    fresh(b, [tool(), finish_last])
    result = invoke(b, continuation_id=nonce, clarified_query='SMILES: CCO')
    assert result.success, result.metadata
    assert result.trace_id == waiting.trace_id
    assert result.metadata['model_requests'] == 4
    assert result.metadata['tool_budget_reserved'] == 1
    assert len(b.tools[0].inputs) == 1
    assert result.tool_results[0].to_legacy_dict() == before
    assert b.store.get_run('owned-trace')['status'] == 'succeeded'


@pytest.mark.parametrize('change', ['owner', 'session', 'nonce', 'permissions', 'required', 'version', 'adapter_version', 'budget', 'corrupt'])
def test_invalid_resume_leaves_waiting_state_unchanged(setup_loop, change, monkeypatch):
    b = setup_loop([])
    waiting = start(b, [clarify()])
    nonce = waiting.metadata['continuation_id']
    context, options = owned_context(), {}
    if change == 'owner': context.user_id = 'different'
    if change == 'session': context.session_id = 'other'
    if change == 'nonce': nonce = 'wrong'
    if change == 'permissions': options['allowed_tools'] = {'property_calculator', 'activity_predictor'}
    if change == 'required': options['required_tools'] = set()
    if change == 'version':
        adapter = b.registry.resolve('property_calculator')
        adapter.spec = replace(adapter.spec, version='changed')
    if change == 'budget': b.loop.max_model_requests = 15
    if change == 'adapter_version': b.registry.resolve('property_calculator').adapter_version = 'changed'
    if change == 'corrupt':
        record = b.store.get_run('owned-trace')
        damaged = record['metadata']['decision_continuation']
        damaged['snapshot']['model_requests'] = 0
        # Main forbids non-CAS continuation writes. Inject a corrupt read only.
        monkeypatch.setattr(b.store, 'get_run', lambda _: record)
    before = b.store.get_run('owned-trace')
    result = invoke(b, context=context, continuation_id=nonce, clarified_query='SMILES: CCO', **options)
    assert not result.success
    assert b.store.get_run('owned-trace') == before
    assert not b.tools[0].inputs


def test_continuation_is_single_use(setup_loop):
    b = setup_loop([])
    waiting = start(b, [clarify()])
    nonce = waiting.metadata['continuation_id']
    fresh(b, [tool(), finish_last])
    assert invoke(b, continuation_id=nonce, clarified_query='SMILES: CCO').success
    before = b.store.get_run('owned-trace')
    assert not invoke(b, continuation_id=nonce, clarified_query='SMILES: CCO').success
    assert b.store.get_run('owned-trace') == before
    assert len(b.tools[0].inputs) == 1


def test_resume_does_not_reset_exhausted_model_budget(setup_loop):
    b = setup_loop([], max_model_requests=1)
    waiting = start(b, [clarify()])
    fresh(b, [tool()])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert not result.success and not b.model.messages
    assert result.metadata['stop_reason'] == 'model_budget_exhausted'
    assert not b.tools[0].inputs


def test_old_input_results_cannot_complete_revised_input(setup_loop):
    b = setup_loop([])
    waiting = start(b, [tool(), clarify()])
    from test_decision_loop import finish
    old_evidence = waiting.tool_results[0].quality['evidence_id']
    fresh(b, [finish([old_evidence])])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCN')
    assert not result.success
    assert '46.069' not in result.final_answer


def test_real_invalid_input_corrected_by_user_recomputes_rdkit(setup_loop):
    from src.agent.tools.property_calculator import PropertyCalculator
    b = setup_loop([], [PropertyCalculator()])
    context = owned_context()
    context.query = 'SMILES: CC(C)(('
    waiting = start(b, [tool(), clarify()], context=context)
    assert not waiting.tool_results[0].success
    fresh(b, [tool(), finish_last])
    result = invoke(b, context=context, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert not result.success and result.partial, result.metadata
    assert len(result.tool_results) == 2
    assert result.metadata['step_count'] == 2
    assert not result.tool_results[0].success and result.tool_results[1].success
    assert result.error is not None
    assert result.tool_results[1].data[0]['smiles'] == 'CCO'
    assert result.metadata['stop_reason'] == 'model_finished'


def test_multiple_clarifications_keep_history_and_single_use_snapshots(setup_loop):
    b = setup_loop([])
    first = start(b, [tool(), clarify()])
    fresh(b, [clarify()])
    second = invoke(b, continuation_id=first.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert second.metadata['continuation_id'] != first.metadata['continuation_id']
    fresh(b, [tool(), finish_last])
    result = invoke(b, continuation_id=second.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert result.success and len(b.tools[0].inputs) == 1
    assert result.metadata['model_requests'] == 5


def test_two_stores_cannot_claim_same_continuation(setup_loop):
    from concurrent.futures import ThreadPoolExecutor
    b = setup_loop([])
    start(b, [clarify()])
    payload = b.store.get_run('owned-trace')['metadata']['decision_continuation']
    def claim(_):
        store = SQLiteAgentStateStore(b.store.db_path)
        return store.transition_decision_continuation('owned-trace', user_id='owner', session_id='browser-1',
            expected=payload, replacement={**payload, 'claimed_by': uuid4().hex}, claim=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_publish_retry_cannot_overwrite_claimed_continuation(setup_loop):
    b = setup_loop([])
    start(b, [clarify()])
    payload = b.store.get_run('owned-trace')['metadata']['decision_continuation']
    args = dict(user_id='owner', session_id='browser-1')
    assert b.store.transition_decision_continuation('owned-trace', **args, expected=payload,
        replacement={**payload, 'claimed_by': uuid4().hex}, claim=True)
    assert not b.store.transition_decision_continuation('owned-trace', **args,
        expected=None, replacement=payload, claim=False)


def test_elapsed_execution_budget_survives_human_wait(setup_loop, monkeypatch):
    import time
    b = setup_loop([])
    real_clock = time.monotonic
    offset = [0]
    monkeypatch.setattr('src.agent.harness.decision_loop.time.monotonic', lambda: real_clock() + offset[0])
    def spend_budget(messages):
        offset[0] += 250
        return clarify()
    waiting = start(b, [spend_budget])
    remaining = b.store.get_run('owned-trace')['metadata']['decision_continuation']['snapshot']['remaining_seconds']
    assert 0 < remaining <= 50
    offset[0] += 999
    fresh(b, [tool(), finish_last])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert result.success


def test_tool_budget_is_not_reset_by_continuation(setup_loop):
    b = setup_loop([], max_tool_attempts=1)
    waiting = start(b, [tool(), clarify()])
    fresh(b, [tool(), finish_last])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCN')
    assert not result.success and len(b.tools[0].inputs) == 1
    assert result.metadata['stop_reason'] == 'tool_budget_exhausted'


@pytest.mark.parametrize('after_commit', [False, True])
def test_waiting_publication_is_idempotent(setup_loop, after_commit):
    b = setup_loop([])
    original, injected = b.store.transition_decision_continuation, []
    def fault(*a, **kw):
        if not kw['claim'] and not injected:
            injected.append(True)
            if after_commit: original(*a, **kw)
            raise OSError('private publication error')
        return original(*a, **kw)
    b.store.transition_decision_continuation = fault
    waiting = start(b, [clarify()])
    assert injected and waiting.metadata['continuation_id']
    assert b.store.get_run('owned-trace')['status'] == 'waiting_for_input'


def test_uncertain_claim_never_dispatches_or_retries(setup_loop):
    b = setup_loop([])
    waiting = start(b, [clarify()])
    fresh(b, [tool(), finish_last])
    original = b.store.transition_decision_continuation
    def fault(*a, **kw):
        original(*a, **kw)
        raise OSError('private claim error')
    b.store.transition_decision_continuation = fault
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    assert not result.success and not b.model.messages and not b.tools[0].inputs
    assert b.store.get_run('owned-trace')['status'] == 'running'


def test_retained_result_cannot_reassign_input_before_clarification(setup_loop):
    from src.agent.evidence import EvidenceLedger
    from test_decision_loop import finish
    class Source(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            return self.last
    source = Source()
    def tamper_then_clarify(messages):
        source.last.quality['request_input_digest'] = EvidenceLedger.output_digest('SMILES: CCN')
        return clarify()
    b = setup_loop([], [source])
    waiting = start(b, [tool(), tamper_then_clarify])
    if 'continuation_id' not in waiting.metadata:
        assert not waiting.success
        return
    fresh(b, [finish([source.last.quality['evidence_id']])])
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCN')
    assert not result.success
    assert '46.069' not in result.final_answer
