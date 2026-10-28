"""T06-C Pi/full-Web terminals and isolated histories; scientific fixtures are offline."""
import asyncio
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.rag_search_tool import RAGSearchTool
from src.web.chat_handler import ChatHandler
from src.web.decision_runtime import _Sender, _Turn
from src.web.rag_presentation import rag_info_molecule
from tests.agent.pi_web_support import (
    PlannedWebModel, WebSocket, cleanup_web_handlers, make_web_handler,
)
from tests.agent.test_rag_tool_contract import InjectedService, record as rag_record
from tests.agent.test_web_decision_runtime import (
    actual_app, ActualSocket, cookie_for, chat_decision, result_of,
)


class Rag:
    # Original pure-projection fixtures stay independent of owned retrieval.
    rows = [
        {"SMILES": "CCO", "similarity_score": .98765, "mw": 46.069,
         "source": "test-source", "unused": None, "source_index": 2,
         "provenance": {"vector_label": 1, "source_sha256": "a" * 64}},
        {"SMILES": "C", "similarity_score": .5, "source_index": 0,
         "provenance": {"vector_label": 0, "source_sha256": "a" * 64}},
    ]


class History(list):
    """Count admission appends, including duplicates hidden by the length cap."""
    def __init__(self, records=()):
        super().__init__(records)
        self.appended = []

    def append(self, entry):
        self.appended.append(entry)
        super().append(entry)


def seeded_history(count=23):
    return History({"user": str(i), "assistant": str(i), "extra": [i]}
                   for i in range(count))


class Model(PlannedWebModel):
    def __init__(self, *, fail=False):
        super().__init__()
        self.fail = fail

    async def decide(self, messages, **kwargs):
        if self.fail:
            self.messages.append(deepcopy(messages))
            raise RuntimeError('synthetic decision provider failure')
        return await super().decide(messages, **kwargs)

    async def stream_generate(self, *args, **kwargs):
        pytest.fail('Pi must not invoke retired partial-prose streaming')


class Properties(PropertyCalculator):
    def __init__(self, *, fail=False):
        super().__init__()
        self.fail, self.calls = fail, []

    def execute(self, query):
        self.calls.append(deepcopy(query))
        if self.fail:
            return ToolResult.error_result(self.name, code=AgentErrorCode.TOOL_UNAVAILABLE,
                                           message='synthetic property failure')
        return super().execute(query)


def local_case(tmp_path, *, tools=(), model=None, scientific=None, plain=(), owner='local-owner'):
    handler = make_web_handler(tools, SQLiteAgentStateStore(tmp_path / 'local.sqlite'),
                               model=model or Model())
    compiled = handler.decision_runtime._execute_compiled_scientific_plan
    async def observe_compiled(*args, **kwargs):
        result = await compiled(*args, **kwargs)
        handler.web_results.append(result)
        return result
    handler.decision_runtime._execute_compiled_scientific_plan = observe_compiled
    sender = _Sender(WebSocket(owner))
    sender._runtime = handler.decision_runtime
    sender.scientific_memory = scientific if scientific is not None else History()
    sender.memory = list(plain)
    return handler, sender


async def invoke(handler, sender, query, **options):
    """Run the actual Pi/Web turn and observe its delivered terminal."""
    start = len(sender.websocket.messages)
    payload = dict(message=query, enable_tools=True, enable_rag=False)
    payload.update(options)
    turn = _Turn(payload)
    turn.task = asyncio.current_task()
    runtime = handler.decision_runtime
    runtime.active_owners.add(turn)
    await runtime._execute(handler, sender, turn)
    assert turn.worker_owner.pending_roots == 0
    assert runtime.application.model_request_gate._readers == 0
    assert turn not in runtime.active_owners
    frames = sender.websocket.messages[start:]
    complete = [frame for frame in frames if frame['type'] == 'complete']
    assert len(complete) == 1 and turn.terminal_sent, frames
    assert not any(frame['type'] == 'stream' for frame in frames)
    return complete[0], frames


@pytest.mark.parametrize('mode', [
    'chat', 'tool_failure', 'invalid_molecule', 'scientific', 'model_failure', 'invalid_count',
])
def test_each_terminal_path_appends_one_scientific_marker_and_only_eligible_plain_pair(tmp_path, mode):
    properties = Properties(fail=mode == 'tool_failure')
    model = Model(fail=mode == 'model_failure')
    history = seeded_history()
    before = list(history)
    plain = [{'user': 'earlier plain question', 'assistant': 'earlier plain answer'}]
    handler, sender = local_case(tmp_path, tools=[properties], model=model,
                                scientific=history, plain=plain)
    query = ('请分析这个 SMILES 的成药性：CC(C)((。' if mode == 'invalid_molecule' else
             '只计算 CCO 的分子量和 logP' if mode in {'tool_failure', 'scientific'} else 'hello')
    options = {'mol_count': 0} if mode == 'invalid_count' else {}
    complete, frames = asyncio.run(invoke(handler, sender, query, **options))

    assert sender.scientific_memory is history
    assert len(history) == 20 and len(history.appended) == 1
    assert all(after is old for after, old in zip(history[:-1], before[4:]))
    # Admission revokes old authority immediately. Only terminal delivery can
    # replace this marker; neither list carries the retired mixed-history shape.
    assert history.appended[0] == {'user': query, 'agent_used': False}
    assert history[-1]['user'] == query
    assert handler.conversation_history == []
    assert all(set(pair) == {'user', 'assistant'} for pair in sender.memory)
    if mode == 'chat':
        assert complete['status'] == 'completed'
        assert sender.memory == plain + [{'user': query, 'assistant': complete['content']}]
        assert len(model.messages) == 1 and properties.calls == []
        assert history[-1] == {'user': query, 'agent_used': False}
    else:
        assert sender.memory == plain, 'scientific/rejected/failed content is not a plain chat pair'
    if mode == 'scientific':
        assert complete['status'] == 'completed'
        assert properties.calls == ['CCO'] and model.messages == []
        assert history[-1]['agent_used'] is True and history[-1]['property_evidence']
        assert '分子量' in complete['content']
    if mode == 'tool_failure':
        # A compiled one-step plan with no usable output is a failed request.
        assert complete['status'] == 'failed'
        assert len(properties.calls) == 1
        assert history[-1] == {'user': query, 'agent_used': False}
        result, = handler.web_results
        assert not result.success and not result.partial
        assert result.tool_results[0].error.code is AgentErrorCode.TOOL_UNAVAILABLE
        assert 'property_evidence' not in history[-1]
        public, = [frame for frame in frames if frame['type'] == 'agent_result']
        assert public['tool_result_sequence'][0]['error']['code'] == 'tool_unavailable'
    if mode == 'model_failure':
        assert complete['status'] == 'failed' and len(model.messages) == 1
        assert properties.calls == [] and history[-1]['agent_used'] is False
    if mode in {'invalid_count', 'invalid_molecule'}:
        assert complete['status'] == 'rejected'
        assert model.messages == [] and properties.calls == []
        assert history[-1] == {'user': query, 'agent_used': False}
    if mode == 'invalid_molecule':
        assert 'SMILES' in complete['content']
    assert not model.errors


class CountedService(InjectedService):
    """A strict synthetic producer honors k without changing source identity."""
    def _synthetic_envelope(self, query, k):
        from src.rag.receipt import canonical_digest
        envelope = super()._synthetic_envelope(query, k)
        envelope['records'] = deepcopy(envelope['records'][:k])
        count = len(envelope['records'])
        envelope['receipt']['result_sha256'] = canonical_digest(envelope['records'])
        envelope['receipt']['diagnostics'].update(effective_k=count, score_count=count,
                                                   label_count=count, accepted_count=count)
        return envelope


@pytest.mark.parametrize('enable_tools', [True, False])
@pytest.mark.parametrize('count', [1, 2])
def test_rag_capability_paths_preserve_raw_receipt_and_cap_accepted_cards(tmp_path, enable_tools, count):
    rows = []
    for index, source in enumerate(Rag.rows):
        row = rag_record()
        row.update(deepcopy(source), source_index=index)
        row['provenance'] = {**rag_record()['provenance'], 'vector_label': index}
        rows.append(row)
    service = CountedService(rows)
    before = deepcopy(service.rows)
    handler, sender = local_case(tmp_path, tools=[RAGSearchTool(service)])
    query = '检索知识库中相关分子'
    complete, frames = asyncio.run(invoke(handler, sender, query,
        enable_tools=enable_tools, enable_rag=True, rag_count=count))
    assert service.calls == [(query, count)]
    assert service.rows == before
    assert complete['status'] == 'completed'
    result, = handler.web_results
    observation, = result.tool_results
    assert observation.data == before[:count] and len(observation.data) == count
    assert observation.evidence[0]['retrieval_receipt']['diagnostics']['requested_k'] == count
    packet, = [frame for frame in frames if frame['type'] == 'rag_info']
    assert set(packet) == {'type', 'molecules', 'message'}
    assert len(packet['molecules']) == count, 'same immutable request cap with science tools on or off'
    approved, = [block for block in json.loads(result.final_answer)['observations']
                  if block['tool'] == 'rag_search']
    assert packet['molecules'] == [rag_info_molecule(row) for row in approved['data'][:count]]
    card = packet['molecules'][0]
    assert card['smiles'] == 'CCO' and card['similarity'] == .988
    assert card['source_index'] == before[0]['source_index']
    assert card['provenance'] == approved['data'][0]['provenance']
    assert card['provenance']['source_sha256'] == before[0]['provenance']['source_sha256']
    assert 'source_index' not in card['properties'] and 'provenance' not in card['properties']
    # Arbitrary CSV values survive in the immutable observation, not the
    # accepted scientific card's closed field set.
    assert 'mw' not in card['properties'] and 'source' not in card['properties']
    public, = [frame for frame in frames if frame['type'] == 'agent_result']
    assert public['metadata']['rag_requested'] is True
    assert public['metadata']['retrieval_performed'] is True
    assert len(sender.scientific_memory) == len(sender.scientific_memory.appended) == 1
    assert sender.memory == [] and handler.conversation_history == []
    assert len(handler.model.messages) == 1
    checkpoint = handler.decision_runtime.store.latest_checkpoint(result.trace_id)
    assert checkpoint['output']['data'] == before[:count]


@pytest.mark.parametrize("size", [0, 19, 20, 23])
def test_history_helper_preserves_record_fields_objects_and_list_identity(size):
    history = seeded_history(size)
    alias = history
    before = list(history)
    record = {"user": "new", "error": {"code": "invalid_input"}, "extra": ["source"]}
    snapshot = deepcopy(record)
    ChatHandler._append_history(history, record)
    assert history is alias
    assert history == (before + [snapshot])[-20:]
    assert history[-1] is record and record == snapshot
    assert history.appended == [record]
    assert all(after is old for after, old in zip(history[:-1], before[-19:]))
    assert history[-1]["error"] is record["error"]
    assert history[-1]["extra"] is record["extra"]


@pytest.mark.parametrize("empty", [False, True])
def test_rag_payload_helper_uses_shared_projection_without_mutating_records(empty):
    rows = [] if empty else deepcopy(Rag.rows) + [{
        "SMILES": "CC", "nested": {"labels": ["source", 2]},
        "missing": float("nan"), "provenance": {"source": "fixture"},
    }]
    before = json.dumps(rows, sort_keys=True)
    result = ChatHandler._rag_info_payload(rows, "caller-owned count and wording")
    assert result == {
        "type": "rag_info", "molecules": [rag_info_molecule(row) for row in rows],
        "message": "caller-owned count and wording",
    }
    assert json.dumps(rows, sort_keys=True) == before
    if rows:
        assert result["molecules"][0]["provenance"] is rows[0]["provenance"]
        assert "missing" not in result["molecules"][-1]["properties"]
        assert json.loads(result["molecules"][-1]["properties"]["nested"]) == {"labels": ["source", 2]}


@pytest.mark.parametrize('stream', [False, True])
def test_scientific_terminal_never_invokes_retired_prose_summary_or_stream(tmp_path, stream):
    properties = Properties()
    history = seeded_history()
    before = list(history)
    handler, sender = local_case(tmp_path, tools=[properties], scientific=history)
    handler.config.update(inference={'stream': stream},
                          agent={'summarize_workflow_results': True})
    complete, frames = asyncio.run(invoke(handler, sender, '只计算 CCO 的分子量和 logP'))
    assert complete['status'] == 'completed'
    assert len(history) == 20 and len(history.appended) == 1
    assert all(after is old for after, old in zip(history[:-1], before[4:]))
    assert history[-1]['property_evidence'] and history[-1]['agent_used'] is True
    assert sender.memory == [] and handler.conversation_history == []
    assert properties.calls == ['CCO'] and handler.model.messages == []
    assert not any(frame['type'] == 'stream' for frame in frames)
    public, = [frame for frame in frames if frame['type'] == 'agent_result']
    assert public['display_answer'] == complete['content']
    assert public['final_answer'] == handler.web_results[-1].final_answer


@pytest.mark.parametrize('stream', [False, True])
def test_input_rejection_changes_only_scientific_marker_not_plain_pairs(tmp_path, stream):
    # Parent owns budget enforcement; this test covers only its history effect.
    properties = Properties()
    history = seeded_history()
    before = list(history)
    plain = [{'user': 'retained plain question', 'assistant': 'retained plain answer'}]
    handler, sender = local_case(tmp_path, tools=[properties], scientific=history, plain=plain)
    handler.config['inference'] = {'stream': stream, 'input_max_chars': 6000}
    complete, frames = asyncio.run(invoke(handler, sender, '问' * 7000, enable_rag=True))
    assert complete['status'] == 'rejected'
    assert len([frame for frame in frames if frame['type'] == 'complete']) == 1
    assert len(history) == 20 and len(history.appended) == 1
    assert all(after is old for after, old in zip(history[:-1], before[4:]))
    assert history[-1] == {'user': '问' * 7000, 'agent_used': False}
    assert sender.memory == plain and handler.conversation_history == []
    assert properties.calls == [] and handler.model.messages == []


def test_explicit_socket_histories_and_handler_default_remain_independent(tmp_path):
    handler, first = local_case(tmp_path)
    second = _Sender(WebSocket('local-owner'))
    second._runtime = handler.decision_runtime
    second.scientific_memory = History()
    default = handler.conversation_history

    async def run():
        await invoke(handler, first, 'first private question', enable_tools=False)
        await invoke(handler, second, 'second private question', enable_tools=False)
        assert len(first.scientific_memory) == len(second.scientific_memory) == 1
        assert first.scientific_memory[0] is not second.scientific_memory[0]
        assert len(first.memory) == len(second.memory) == 1
        assert 'first private question' not in json.dumps(handler.model.messages[1])
        assert default is handler.conversation_history and default == []
        await invoke(handler, first, 'next question', enable_tools=False)
        assert len(first.memory) == 2 and len(second.memory) == 1
        messages = [m for m in handler.model.messages[-1] if m['role'] != 'system']
        assert messages[0] == {'role': 'user', 'content': 'first private question'}
        assert messages[1] == {'role': 'assistant', 'content': first.memory[0]['assistant']}
        assert messages[-1] == {'role': 'user', 'content': 'next question'}
        assert 'second private question' not in json.dumps(messages)
    asyncio.run(run())
    other, _ = local_case(tmp_path / 'other')
    assert other.conversation_history == [] and other.conversation_history is not default


@pytest.mark.parametrize('scientific', [False, True])
def test_histories_publish_only_after_terminal_delivery(tmp_path, monkeypatch, scientific):
    properties = Properties()
    handler, sender = local_case(tmp_path, tools=[properties], plain=[
        {'user': 'earlier plain question', 'assistant': 'earlier plain answer'}])

    async def run():
        await invoke(handler, sender, '只计算 CCO 的分子量和 logP')
        prior = sender.scientific_memory[-1]
        assert prior['property_evidence']
        snapshot, plain = deepcopy(prior), deepcopy(sender.memory)
        entered, release = asyncio.Event(), asyncio.Event()
        send = sender.websocket.send_text
        start = len(sender.websocket.messages)
        async def held(text):
            if json.loads(text)['type'] == 'complete':
                entered.set()
                await release.wait()
            await send(text)
        monkeypatch.setattr(sender.websocket, 'send_text', held)
        query = '只计算 CCN 的分子量和 logP' if scientific else 'hello'
        task = asyncio.create_task(invoke(handler, sender, query))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            assert sender.memory == plain
            assert sender.scientific_memory[-1] == {'user': query, 'agent_used': False}
            assert sender.scientific_memory[-1] is sender.scientific_memory.appended[-1]
            assert sender.scientific_memory[-2] is prior and prior == snapshot
            assert not any(frame['type'] == 'complete'
                           for frame in sender.websocket.messages[start:])
        finally:
            release.set()
            complete, _ = await task
        assert complete['status'] == 'completed'
        assert sender.scientific_memory[-2] is prior and prior == snapshot
        assert len(sender.scientific_memory.appended) == 2
        if scientific:
            assert sender.memory == plain
            assert sender.scientific_memory[-1]['property_evidence']['smiles'] == 'CCN'
        else:
            assert sender.memory == plain + [{'user': query, 'assistant': complete['content']}]
            assert sender.scientific_memory[-1] == {'user': query, 'agent_used': False}
    asyncio.run(run())


def test_actual_websocket_connections_do_not_share_history(actual_app, monkeypatch):
    """Even the same authenticated owner gets fresh per-connection Pi history."""
    first_query, second_query = 'Explain logP', '解释分子生成的概念'
    private_answer = 'Connection one private answer'

    async def respond(payload):
        return chat_decision(private_answer if payload['messages'][-1]['content'] == first_query
                             else 'Connection two answer')

    async def run():
        async with actual_app(respond=respond) as bundle:
            app = bundle.app
            handler = app.chat_handler
            default = handler.conversation_history
            forbidden = Mock(side_effect=AssertionError('isolated chat dispatched a tool'))
            monkeypatch.setattr(app.agent_system, 'execute', forbidden)
            assert not hasattr(handler, '_process_message')
            for adapter in app.agent_tool_registry.as_mapping().values():
                monkeypatch.setattr(adapter, 'execute', forbidden)
            cookie = await cookie_for(app)
            for query in (first_query, second_query):
                async with ActualSocket(app, cookie) as socket:
                    assert (await socket.ready())['normal_chat_mode'] == 'pi'
                    result = result_of(await socket.turn({
                        'message': query, 'enable_rag': False, 'enable_tools': False}))
                    assert result['success'] and result['status'] == 'completed'
                    assert result['tool_result_sequence'] == []
                    assert app.agent_state_store.get_tool_executions(result['trace_id']) == []
            assert len(bundle.calls) == 2
            for call, query in zip(bundle.calls, (first_query, second_query)):
                assert [m for m in call['messages'] if m['role'] != 'system'] == [
                    {'role': 'user', 'content': query}]
            assert first_query not in json.dumps(bundle.calls[1], ensure_ascii=False)
            assert private_answer not in json.dumps(bundle.calls[1], ensure_ascii=False)
            assert default is handler.conversation_history and default == []
            assert not forbidden.called and not bundle.rag.retrieve.called
    asyncio.run(run())
