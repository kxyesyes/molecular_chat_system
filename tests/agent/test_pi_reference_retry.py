"""Reference retries run through real Pi/Web and RDKit; model replies are offline."""
import asyncio
import threading
from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4

import pytest

from src.web.decision_runtime import _Sender, _Turn
from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, PlannedWebModel
from tests.agent.test_scientific_reference_execution import Socket, Tool, confirmed
from tests.agent.test_scientific_reference_resilience import QUERY, _handler, _tools, _execution_traces


async def _turn(handler, sender, pointer, query=QUERY):
    runtime = handler.decision_runtime
    sender._runtime = runtime
    turn = _Turn(dict(message=query, enable_tools=True, enable_rag=False, reference=pointer))
    handler.web_turns.append(turn)
    turn.task = asyncio.current_task()
    runtime.active_owners.add(turn)
    await runtime._execute(handler, sender, turn)
    assert turn.worker_owner.pending_roots == 0
    return turn


def test_same_socket_history_growth_does_not_repeat_successful_science(tmp_path):
    store, _, pointer = confirmed(tmp_path)
    tools = _tools()
    handler = _handler(store, tools)
    socket = Socket()
    sender = _Sender(socket)

    async def run():
        first = await _turn(handler, sender, pointer)
        assert first.reference_claim and first.reference_claim['admitted'] and first.reference_claim['verified']
        assert socket.messages[-1]['status'] == 'partial'
        # History changes are model context, not a new scientific request.
        sender.memory = [{'user': 'earlier question', 'assistant': 'earlier reply'}]
        second = await _turn(handler, sender, pointer)
        assert second.trace_id == first.trace_id, socket.messages[-1]

    asyncio.run(run())
    assert tools['property_calculator'].calls == ['CCN']
    assert len(_execution_traces(store)) == 1
    completions = [frame for frame in socket.messages if frame['type'] == 'complete']
    assert len(completions) == 2 and all(frame['status'] == 'partial' for frame in completions)


def test_duplicate_during_cancelled_physical_work_cannot_dispatch(tmp_path):
    store, _, pointer = confirmed(tmp_path)
    entered, release, exited = (threading.Event() for _ in range(3))

    class BlockingProperty(Tool):
        def execute(self, value):
            result = super().execute(value)
            entered.set()
            try:
                assert release.wait(15), 'test did not release physical worker'
                return result
            finally:
                exited.set()

    tools = _tools()
    tools['property_calculator'] = BlockingProperty('property_calculator')
    first_handler = _handler(store, tools)
    second_tools = _tools()
    second_handler = _handler(store, second_tools)

    async def run():
        first = asyncio.create_task(_turn(first_handler, _Sender(Socket()), pointer))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 8), 9)
            first.cancel()
            await asyncio.sleep(0)
            duplicate_socket = Socket()
            await _turn(second_handler, _Sender(duplicate_socket), pointer)
            assert second_tools['property_calculator'].calls == []
            assert second_tools['drug_likeness_assessment'].calls == []
            complete = [frame for frame in duplicate_socket.messages if frame['type'] == 'complete']
            assert len(complete) == 1 and complete[0]['status'] == 'rejected'
            assert not exited.is_set() and not first.done()
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(first, return_exceptions=True), 10)

    asyncio.run(run())


@pytest.mark.parametrize('damage', ['data', 'proof', 'source', 'false-success', 'invalid-json'])
def test_damaged_successful_checkpoint_is_not_repaired_or_redispatched(tmp_path, damage):
    store, _, pointer = confirmed(tmp_path)
    first_tools = _tools()
    first_handler = _handler(store, first_tools)
    first = asyncio.run(_turn(first_handler, _Sender(Socket()), pointer))
    checkpoints = store.get_tool_executions(first.trace_id)
    step_id = next(item['step_id'] for item in checkpoints if item['tool_name'] == 'property_calculator')
    checkpoint = deepcopy(store.latest_checkpoint(first.trace_id, step_id))
    assert checkpoint['status'] == 'succeeded'
    checkpoint['id'] = uuid4().hex
    if damage == 'data':
        checkpoint['output']['data'][0]['properties']['molecular_weight'] += 1
    elif damage == 'proof':
        checkpoint['output']['quality'].pop('binding_proof')
    elif damage == 'source':
        checkpoint['output']['quality']['binding_proof']['request_input_digest'] = '0' * 64
    elif damage == 'false-success':
        checkpoint['output']['success'] = False
    store.save_checkpoint(checkpoint)
    if damage == 'invalid-json':
        with store._connect() as connection:
            connection.execute('UPDATE agent_checkpoints SET output_json=? WHERE id=?',
                               ('{broken-json', checkpoint['id']))
    second_tools = _tools()
    second_handler = _handler(store, second_tools)
    socket = Socket()
    second = asyncio.run(_turn(second_handler, _Sender(socket), pointer))
    assert second.trace_id == first.trace_id
    assert not any(tool.calls for tool in second_tools.values())
    assert socket.messages[-1]['status'] == 'failed'
    third_socket = Socket()
    asyncio.run(_turn(second_handler, _Sender(third_socket), pointer))
    assert third_socket.messages[-1]['status'] == 'rejected'
    assert not any(tool.calls for tool in second_tools.values())


def test_reference_waiting_resume_uses_original_nonce_and_releases_after_drain(tmp_path, monkeypatch):
    from src.agent.contracts.decision import ClarifyDecision
    from src.agent.decision_transport import DecisionResponse
    from src.agent.scientific_session import decision_binding_continuation as continuation
    rejected = []
    validate = continuation.validate_continuation

    def audit(*args, **kwargs):
        try:
            return validate(*args, **kwargs)
        except ValueError as exc:
            rejected.append(str(exc))
            raise

    monkeypatch.setattr(continuation, 'validate_continuation', audit)

    class WaitOnce(PlannedWebModel):
        asked = False

        async def decide(self, messages, **kwargs):
            if not self.asked and any(message.get('role') == 'tool' for message in messages):
                self.asked = True
                self.messages.append(deepcopy(messages))
                decision = ClarifyDecision(version='1', action='clarify', question='继续？', missing_fields=['confirmation'])
                return DecisionResponse(decision, None, 'wait-reference', {'request_attempts': 1})
            return await super().decide(messages, **kwargs)

    store, _, pointer = confirmed(tmp_path)
    tools = _tools()
    handler = make_web_handler(tools, store, model=WaitOnce())
    socket, sender = Socket(), None
    sender = _Sender(socket)

    async def run():
        first = await _turn(handler, sender, pointer)
        assert socket.messages[-1]['status'] == 'waiting_for_input'
        waiting = sender.waiting
        assert waiting is not None
        # A plain duplicate cannot bypass the durable continuation nonce.
        duplicate = Socket()
        await _turn(handler, _Sender(duplicate), pointer)
        assert duplicate.messages[-1]['status'] == 'rejected'
        restored = handler.decision_runtime._restore_durable_waiting(
            sender, waiting.context.trace_id, waiting.continuation_id)
        assert restored is not None
        turn = _Turn(dict(message='继续'), trace_id=waiting.context.trace_id, waiting=restored)
        turn.task = asyncio.current_task()
        handler.web_turns.append(turn)
        handler.decision_runtime.active_owners.add(turn)
        await handler.decision_runtime._execute(handler, sender, turn)
        assert turn.reference_claim['admitted'] and turn.reference_claim['verified'], (
            socket.messages[-1], handler.web_results[-1].metadata, store.get_run(first.trace_id)['status'], rejected)
        assert socket.messages[-1]['status'] == 'partial', socket.messages[-1]
        assert tools['property_calculator'].calls == ['CCN']
        assert tools['drug_likeness_assessment'].calls == ['CCN']
        # Now a new turn can retry the failed optional action, not the property.
        retry = await _turn(handler, _Sender(Socket()), pointer)
        assert retry.trace_id == first.trace_id
        assert tools['property_calculator'].calls == ['CCN']

    asyncio.run(run())


def test_reference_request_identity_excludes_only_transport_trace_and_history(tmp_path):
    from src.agent.pi_runtime.runtime import PiAgentRuntime
    from src.agent.scientific_session.decision_bounds import context_value
    from src.agent.scientific_session.web_requirements import compile_web_requirements
    from src.web.web_request import prepare_web_request
    from src.web.reference_retry import request_identity

    store, references, pointer = confirmed(tmp_path)
    handler = _handler(store, _tools())
    runtime = PiAgentRuntime(handler.model, handler.agent_system.tool_registry, store,
        binding_profile='medchat-web-v1', config_generation='generation-1')
    payload = dict(message=QUERY, enable_tools=True, enable_rag=False, reference=pointer)
    prepared = prepare_web_request(payload, session_id='owner', trace_id='first', references=references)
    key = request_identity(runtime, prepared)

    def changed(context):
        return replace(prepared, _context_json=json.dumps(context_value(context)),
            requirements=compile_web_requirements(context, allowed_tools=prepared.allowed_tools))

    context = prepared.context
    assert request_identity(runtime, changed(replace(context, trace_id='second',
        memory=[{'user': 'old question', 'assistant': 'old response'}]))) == key
    assert request_identity(runtime, changed(replace(context, temperature=0.31))) != key
    selected = replace(context.resolved_molecule, trace_id='different-source')
    assert request_identity(runtime, changed(replace(context, resolved_molecule=selected))) != key
    runtime.config_generation = 'generation-2'
    assert request_identity(runtime, prepared) != key


def test_web_prompt_canonicalizes_semantically_identical_persisted_requirements():
    from src.agent.scientific_session.decision_policy import decision_system_message
    requirements = {'request': {'query': 'CCN', 'memory': [],
        'metadata': {'rag_count': 3, 'capabilities': {'scientific_tools': True, 'rag': False}}},
        'profile': 'medchat-web-v1', 'steps': []}
    restored = json.loads(json.dumps(requirements, sort_keys=True))
    first = decision_system_message('scientific', (), [], requirements, binding_profile='medchat-web-v1')
    second = decision_system_message('scientific', (), [], restored, binding_profile='medchat-web-v1')
    assert first == second


def test_replayed_reference_generation_retires_old_presentation_without_reexecuting(tmp_path):
    from tests.agent.test_scientific_reference_web import pointer as event_pointer
    store, references, source_pointer = confirmed(tmp_path)
    source_view = references.get(source_pointer, session_id='owner')
    tools = _tools()
    tools['llm_molecular_generator'] = Tool('llm_molecular_generator')
    handler = _handler(store, tools)
    query = '基于刚才第二个分子生成3个分子'
    first_socket = Socket()
    first = asyncio.run(_turn(handler, _Sender(first_socket), source_pointer, query))
    assert first_socket.messages[-1]['status'] == 'partial', first_socket.messages[-1]
    old_event = next(frame for frame in first_socket.messages if frame['type'] == 'molecule_candidates')
    old_pointer = event_pointer(old_event)
    assert old_pointer['trace_id'] == first.trace_id != source_pointer['trace_id']
    assert references.confirm(old_event['reference'], session_id='owner')
    old_view = references.restore(old_pointer, session_id='owner')
    assert old_view
    original_run = store.get_run(first.trace_id)
    assert len(tools['llm_molecular_generator'].calls) == 1

    second_socket = Socket()
    second = asyncio.run(_turn(handler, _Sender(second_socket), source_pointer, query))
    assert second_socket.messages[-1]['status'] == 'partial', second_socket.messages[-1]
    assert second.trace_id == first.trace_id
    assert len(tools['llm_molecular_generator'].calls) == 1
    assert references.restore(old_pointer, session_id='owner') is None
    new_event = next(frame for frame in second_socket.messages if frame['type'] == 'molecule_candidates')
    new_pointer = event_pointer(new_event)
    assert new_pointer != old_pointer
    new_view = references.get(new_pointer, session_id='owner')
    assert new_view is None  # Unconfirmed presentations grant no input authority.
    assert references.confirm(new_event['reference'], session_id='owner')
    assert references.restore(new_pointer, session_id='owner')
    assert references.get(source_pointer, session_id='owner') == source_view
    with store._connect() as connection:
        archived_rows = connection.execute(
            'SELECT prior_run_json FROM agent_reference_run_history WHERE trace_id=?',
            (first.trace_id,)).fetchall()
    assert len(archived_rows) == 1
    archived = json.loads(archived_rows[0][0])
    archived['metadata'] = json.loads(archived.pop('metadata_json'))
    assert archived == original_run  # Including the old confirmed bit and fixed TTL.
