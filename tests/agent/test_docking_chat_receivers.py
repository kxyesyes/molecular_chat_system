"""D6 single Pi receiver, real coordinator/store; explicit synthetic execution."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from src.web.chat_handler import ChatHandler
from src.web.decision_runtime import WebDecisionRuntime, _Sender
from src.web.docking_chat_controller import DockingChatController
from tests.agent.test_web_docking_coordinator import Rig, QUERY, proposal, wire
from tests.agent.test_web_docking_refinement import response
from tests.agent.test_web_docking_consent import target_case
from tests.agent.test_chat_handler_agent_events import FakeModel
from test_web_decision_runtime import actual_app, ActualSocket, cookie_for


class Socket:
    def __init__(self, owner='owner-1'):
        self.scope = {'agent_session_id': owner}
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.frames = []
        self.close_codes = []
        self.accepts = self.readers = self.max_readers = 0

    async def accept(self):
        self.accepts += 1

    async def receive_text(self):
        self.readers += 1
        self.max_readers = max(self.max_readers, self.readers)
        try:
            value = await self.incoming.get()
            if value is None:
                raise WebSocketDisconnect()
            return value
        finally:
            self.readers -= 1

    async def send_text(self, text):
        frame = json.loads(text)
        self.frames.append(frame)
        await self.outgoing.put(frame)

    async def close(self, **kwargs):
        self.close_codes.append(kwargs.get('code'))
        await self.incoming.put(None)

    async def send_json(self, value):
        await self.send_text(json.dumps(value))

    async def send(self, payload):
        await self.incoming.put(json.dumps(payload, ensure_ascii=False))

    async def until(self, predicate):
        for _ in range(100):
            frame = await asyncio.wait_for(self.outgoing.get(), 3)
            if predicate(frame):
                return frame
        pytest.fail('expected receiver frame absent')


async def settle(runtime):
    while runtime.tasks:
        await asyncio.wait_for(asyncio.gather(*tuple(runtime.tasks)), 5)


@asynccontextmanager
async def receiver(rig, monkeypatch, *, enabled=True, transport=None):
    app = SimpleNamespace(_get_agent_tool_registry=lambda: SimpleNamespace(as_mapping=lambda: {}),
        _get_agent_state_store=lambda: rig.state, _get_scientific_references=lambda: None)
    runtime = WebDecisionRuntime(app, wire_mode='native')
    handler = ChatHandler(None, None, None, {})
    ordinary = []
    async def decision(handler, sender, turn):
        ordinary.append((sender, turn.payload['message']))
        sender.memory.append({'user': turn.payload['message'], 'assistant': 'ordinary answer',
                              'scientific_pointer': {'private': 'not model memory'}})
        await sender.send({'type': 'complete', 'content': 'ordinary answer'})
    legacy = AsyncMock(side_effect=AssertionError('retired receiver must not dispatch'))
    assert not hasattr(handler, '_process_message')
    monkeypatch.setattr(runtime, '_execute', decision)
    handler.decision_runtime = runtime
    async with rig.client(transport) as client:
        coordinator = rig.coordinator(client)
        controller = DockingChatController(runtime, coordinator, enabled=enabled)
        handler.docking_controller = controller
        socket = Socket()
        task = asyncio.create_task(handler.handle_websocket(socket))
        try:
            await socket.until(lambda f: f['type'] == 'connection_ready')
            assert socket.frames[0]['normal_chat_mode'] == 'pi'
            yield SimpleNamespace(handler=handler, runtime=runtime, controller=controller,
                coordinator=coordinator, socket=socket, task=task, ordinary=ordinary)
        finally:
            await socket.send({'type': 'ping'})
            if not task.done():
                await socket.until(lambda f: f['type'] == 'pong')
            await socket.incoming.put(None)
            await asyncio.wait_for(task, 5)
            await settle(runtime)
            legacy.assert_not_called()


def test_eligible_request_enters_existing_receiver_without_ordinary_allocation(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        async with receiver(rig, monkeypatch) as case:
            await case.socket.send({'type': 'chat', 'message': QUERY})
            frame = await case.socket.until(lambda f: f['type'] in {'request_accepted', 'complete'})
            assert frame['type'] == 'request_accepted', 'C must bypass ordinary execution'
            await case.socket.until(lambda f: f['type'] == 'complete' or f.get('segment_complete'))
            await settle(case.runtime)
            assert not case.ordinary and len(rig.calls) == 1
            root = case.coordinator._owned(frame['trace_id'], 'owner-1')
            assert root is not None and root.origin == frame['turn_id']
            assert len(case.runtime.sockets) == 1 and case.socket.accepts == case.socket.max_readers == 1
            assert any(f['type'] == 'docking_event' for f in case.socket.frames)
        assert not case.coordinator._roots and not case.runtime.tasks
    asyncio.run(run())


@pytest.mark.parametrize('enabled,message', [(False, QUERY), (True, '你好'),
    (True, '不要对接，只查结构。'), (True, QUERY + ' 然后预测活性')])
def test_disabled_and_non_c_keep_ordinary_dispatch(tmp_path, monkeypatch, enabled, message):
    rig = Rig(tmp_path)
    async def run():
        async with receiver(rig, monkeypatch, enabled=enabled) as case:
            await case.socket.send({'message': message})
            await case.socket.until(lambda f: f['type'] == 'complete')
            assert [item[1] for item in case.ordinary] == [message]
            assert not rig.calls and not case.coordinator._roots
            if enabled:
                assert isinstance(case.ordinary[0][0], _Sender)
    asyncio.run(run())


def test_only_bounded_plain_actual_history_enters_c_source(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        async with receiver(rig, monkeypatch) as case:
            for i in range(18):
                await case.socket.send({'message': f'ordinary {i}'})
                await case.socket.until(lambda f: f['type'] == 'complete')
                await settle(case.runtime)
            await case.socket.send({'message': QUERY})
            await case.socket.until(lambda f: f['type'] in {'request_accepted', 'complete'})
            await case.socket.send({'type': 'ping'})
            await case.socket.until(lambda f: f['type'] == 'pong')
            await settle(case.runtime)
            assert len(rig.calls) == 1, 'C request must reach coordinator'
            source = json.loads(json.loads(rig.calls[0].content)['messages'][-1]['content'])
            assert source['history'] == [entry for i in range(2, 18) for entry in
                ({'role': 'user', 'content': f'ordinary {i}'},
                 {'role': 'assistant', 'content': 'ordinary answer'})]
    asyncio.run(run())


def test_strict_refine_same_root_and_invalid_watch_do_not_allocate(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.output = proposal('run docking')
    async def run():
        async with receiver(rig, monkeypatch) as case:
            await case.socket.send({'message': 'run docking'})
            await case.socket.until(lambda f: f['type'] in {'request_accepted', 'complete'})
            await case.socket.send({'type': 'ping'})
            await case.socket.until(lambda f: f['type'] == 'pong')
            await settle(case.runtime)
            assert case.coordinator._roots, 'existing receiver must admit C'
            root = next(iter(case.coordinator._roots.values()))
            rig.output = response('num_modes=5', {'num_modes': 5})
            await case.socket.send(dict(type='docking_refine', request_id=root.root_id,
                                        revision=0, message='num_modes=5'))
            await case.socket.until(lambda f: f['type'] == 'docking_event' and f.get('revision') == 1)
            await settle(case.runtime)
            assert root.accepted_revision == 1 and len(rig.calls) == 2
            await case.socket.send(dict(type='docking_watch', task_id=root.task_id, unexpected=True))
            await case.socket.until(lambda f: f['type'] == 'error')
            assert not case.ordinary and len(rig.calls) == 2
    asyncio.run(run())


def test_busy_c_blocks_ordinary_and_disconnect_retains_physical_work(tmp_path, monkeypatch):
    import httpx
    rig = Rig(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def transport(request):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        try:
            async with receiver(rig, monkeypatch, transport=transport) as case:
                await case.socket.send({'message': QUERY})
                frame = await case.socket.until(lambda f: f['type'] in {'request_accepted', 'complete'})
                assert frame['type'] == 'request_accepted'
                await asyncio.wait_for(entered.wait(), 2)
                await case.socket.send({'message': 'ordinary while busy'})
                frame = await case.socket.until(lambda f: f['type'] in {'error', 'complete'})
                assert frame.get('code') == 'docking_in_progress' and not case.ordinary
                await case.socket.incoming.put(None)
                await asyncio.wait_for(case.task, 2)
                assert case.runtime.tasks and rig.gate._readers == 1
                release.set()
                await settle(case.runtime)
                assert not case.coordinator._roots and rig.gate._readers == 0
            assert frame.get('docking_request_rejected') is True
        finally:
            release.set()
    asyncio.run(run())


def test_controller_defaults_to_none():
    assert getattr(ChatHandler(None, None, None, {}), 'docking_controller', 'missing') is None


def test_missing_pi_runtime_fails_closed_without_legacy_dispatch(monkeypatch):
    handler = ChatHandler(FakeModel(), None, None, {'inference': {'stream': False}})
    legacy = AsyncMock(side_effect=AssertionError('retired receiver must not dispatch'))
    assert not hasattr(handler, '_process_message')
    async def run():
        socket = Socket()
        await socket.send({'message': 'hello', 'enable_tools': False, 'enable_rag': False})
        await handler.handle_websocket(socket)
        assert len(socket.frames) == 1
        assert socket.frames[0]['type'] == 'error'
        assert socket.frames[0]['code'] == 'pi_runtime_unavailable'
        assert socket.accepts == 1 and socket.max_readers == 0
        assert socket.close_codes == [1011]
        legacy.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['cancel', 'send'])
def test_enabled_pi_failed_ready_does_not_leak_runtime_socket(tmp_path, monkeypatch, failure):
    rig = Rig(tmp_path)
    async def run():
        app = SimpleNamespace(_get_agent_tool_registry=lambda: SimpleNamespace(as_mapping=lambda: {}),
            _get_agent_state_store=lambda: rig.state, _get_scientific_references=lambda: None)
        runtime = WebDecisionRuntime(app, wire_mode='native')
        async with rig.client() as client:
            handler = ChatHandler(None, None, None, {})
            handler.decision_runtime = runtime
            handler.docking_controller = DockingChatController(runtime, rig.coordinator(client), enabled=True)
            socket = Socket()
            entered = asyncio.Event()
            async def fail(text):
                entered.set()
                if failure == 'send':
                    raise ConnectionError('synthetic handshake failure')
                await asyncio.Event().wait()
            monkeypatch.setattr(socket, 'send_text', fail)
            task = asyncio.create_task(handler.handle_websocket(socket))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if failure == 'cancel':
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    for _ in range(100):
                        if task.done():
                            break
                        await asyncio.sleep(0)
                    assert task.done(), 'unwritable handshake must not leave a blocked receiver'
                assert not runtime.sockets
            finally:
                await socket.incoming.put(None)
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('enabled', [True, False])
def test_connection_ready_advertises_only_closed_preparation_facts(tmp_path, monkeypatch, enabled):
    async def run():
        async with receiver(Rig(tmp_path), monkeypatch, enabled=enabled) as case:
            ready = case.socket.frames[0]
            facts = ready.get('capabilities', {}).get('chat_docking')
            assert facts == ({'preparation_available': True, 'execution_authorized': False}
                             if enabled else None)
    asyncio.run(run())


@asynccontextmanager
async def source_receiver(tmp_path, target, monkeypatch, *, owner='owner-session',
                          fail_complete=False, result_status='completed'):
    """Actual handlers/turns; only agent execution is a synthetic accepted result."""
    from src.agent.contracts import AgentResult, ToolResult, ToolProvenance, RunOutcome
    from src.agent.pi_runtime.runtime import PiAgentRuntime
    from src.web import decision_runtime
    from src.web.decision_chat import _result_frame
    rig = Rig(tmp_path / 'source-rig')
    rig.state = target.store
    raw = deepcopy(target.agent)
    raw.update(final_answer='Synthetic accepted structure result',
               tools_used=['target_database_search'], status=result_status)
    if result_status != 'completed':
        raw['success'] = False
    model = FakeModel()
    handler = ChatHandler(model, None, None, {'inference': {'stream': False}})
    legacy = AsyncMock(side_effect=AssertionError('retired receiver must not dispatch'))
    assert not hasattr(handler, '_process_message')
    async def refresh():
        pass
    app = SimpleNamespace(_get_agent_tool_registry=lambda: SimpleNamespace(as_mapping=lambda: {}),
        _get_agent_state_store=lambda: target.store, _get_scientific_references=lambda: None,
        model=model, model_generation='model-1', model_request_gate=rig.gate,
        _refresh_llm_config_from_env=refresh)
    runtime = WebDecisionRuntime(app, wire_mode='native')
    handler.decision_runtime = runtime
    original_turn = decision_runtime._Turn
    monkeypatch.setattr(decision_runtime, '_Turn',
                        lambda payload, **kwargs: original_turn(payload, trace_id='target-trace'))
    observation = deepcopy(target.checkpoint['output'])
    tool = ToolResult.success_result('target_database_search', data=observation['data'],
        quality=observation['quality'], evidence=observation['evidence'],
        provenance=ToolProvenance.from_dict(observation['provenance']))
    result = AgentResult('target-trace', raw['success'], 'Synthetic accepted structure result',
        final_answer=raw['final_answer'], tool_results=[tool], outcome=RunOutcome(result_status))
    assert result.to_legacy_dict()['tool_result_sequence'] == target.agent['tool_result_sequence']
    async def bridge(sender, **kwargs):
        assert isinstance(kwargs['decision_loop'], PiAgentRuntime)
        for frame in _result_frame(handler, result, False):
            await sender.send_text(frame)
        return result
    monkeypatch.setattr(handler, 'process_decision_message', bridge)
    socket = Socket(owner)
    original_send = socket.send_text
    async def send(text):
        if fail_complete and json.loads(text)['type'] == 'complete':
            raise ConnectionError('synthetic terminal delivery failure')
        await original_send(text)
    monkeypatch.setattr(socket, 'send_text', send)
    async with rig.client() as client:
        coordinator = rig.coordinator(client)
        controller = DockingChatController(runtime, coordinator, enabled=True)
        handler.docking_controller = controller
        captures = []
        accept = controller.accept_result
        async def captured(sender, value):
            captures.append((sender, deepcopy(value), deepcopy(socket.frames)))
            return await accept(sender, value)
        monkeypatch.setattr(controller, 'accept_result', captured)
        task = asyncio.create_task(handler.handle_websocket(socket))
        try:
            await socket.until(lambda f: f['type'] == 'connection_ready')
            assert socket.frames[0]['normal_chat_mode'] == 'pi'
            yield SimpleNamespace(socket=socket, handler=handler, runtime=runtime,
                controller=controller, captures=captures, task=task, rig=rig)
        finally:
            await socket.incoming.put(None)
            await asyncio.wait_for(task, 5)
            await settle(runtime)
            legacy.assert_not_called()


@pytest.mark.parametrize('owner', ['owner-session', 'foreign'])
def test_actual_accepted_source_captured_only_after_terminal_delivery(
        tmp_path, target_case, monkeypatch, owner):
    async def run():
        async with source_receiver(tmp_path, target_case, monkeypatch, owner=owner) as case:
            await case.socket.send({'message': '解释分子量', 'enable_rag': False})
            complete = await case.socket.until(lambda f: f['type'] == 'complete')
            assert complete.get('status') in {'succeeded', 'completed'}
            await case.socket.send({'type': 'ping'})
            await case.socket.until(lambda f: f['type'] == 'pong')
            await settle(case.runtime)
            assert len(case.captures) == 1, 'actual successful result needs delivered-terminal hook'
            sender, value, delivered = case.captures[0]
            terminals = [frame for frame in delivered if frame['type'] == 'complete']
            assert len(terminals) == 1 and terminals[0]['status'] in {'completed', 'succeeded'}
            assert value['trace_id'] == 'target-trace'
            assert value['tool_result_sequence'] == target_case.agent['tool_result_sequence']
            pointer = getattr(sender, '_docking_structure', None)
            assert bool(pointer) is (owner == 'owner-session')
            if pointer:
                assert pointer['observation_id'] == 'target-observation'
            assert 'observation_id' not in json.dumps(sender.memory)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['delivery', 'failed', 'partial'])
def test_failed_or_undelivered_actual_result_never_captures_source(
        tmp_path, target_case, monkeypatch, failure):
    async def run():
        async with source_receiver(tmp_path, target_case, monkeypatch,
                fail_complete=failure == 'delivery',
                result_status='completed' if failure == 'delivery' else failure) as case:
            await case.socket.send({'message': '解释分子量', 'enable_rag': False})
            if failure == 'delivery':
                await case.socket.until(lambda f: f['type'] == 'agent_result')
                # Let the receiver/turn attempt the failing terminal send.
                for _ in range(50):
                    await asyncio.sleep(.002)
            else:
                await case.socket.until(lambda f: f['type'] == 'complete')
                await case.socket.send({'type': 'ping'})
                await case.socket.until(lambda f: f['type'] == 'pong')
            assert not case.captures
    asyncio.run(run())


def test_a2_does_not_admit_new_turn_until_accepted_source_capture_finishes(tmp_path, target_case, monkeypatch):
    async def run():
        async with source_receiver(tmp_path, target_case, monkeypatch) as case:
            entered, release = asyncio.Event(), asyncio.Event()
            accept = case.controller.accept_result
            async def held(sender, value):
                entered.set()
                await release.wait()
                return await accept(sender, value)
            monkeypatch.setattr(case.controller, 'accept_result', held)
            try:
                await case.socket.send({'message': '解释分子量', 'enable_rag': False})
                await case.socket.until(lambda f: f['type'] == 'complete')
                await asyncio.sleep(.02)
                assert entered.is_set(), 'capture must be part of the accepted turn task'
                await case.socket.send({'message': QUERY})
                frame = await case.socket.until(lambda f: f['type'] in {'error', 'request_accepted'})
                assert frame.get('code') == 'turn_in_progress'
                assert not case.rig.calls
            finally:
                release.set()
                await settle(case.runtime)
    asyncio.run(run())


def test_shutdown_disconnects_c_before_waiting_for_physical_work(tmp_path, monkeypatch):
    import httpx
    rig = Rig(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def transport(request):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with receiver(rig, monkeypatch, transport=transport) as case:
            shutdown = None
            try:
                await case.socket.send({'message': QUERY})
                await asyncio.wait_for(entered.wait(), 2)
                sender = next(iter(case.runtime.sockets))
                shutdown = asyncio.create_task(case.runtime.shutdown())
                await asyncio.sleep(.02)
                assert not sender._chat_docking.active
                assert not shutdown.done() and case.runtime.tasks
            finally:
                release.set()
                if shutdown is not None:
                    await asyncio.wait_for(shutdown, 5)
            assert rig.gate._readers == 0 and not case.coordinator._roots
            await case.socket.incoming.put(None)
            await asyncio.wait_for(case.task, 3)
    asyncio.run(run())


@pytest.mark.parametrize('owner', ['owner-1', 'foreign'])
def test_watch_through_existing_receiver_is_read_only_and_owner_bound(tmp_path, monkeypatch, owner):
    rig = Rig(tmp_path)
    async def run():
        async with receiver(rig, monkeypatch) as case:
            await case.socket.send({'message': QUERY})
            await case.socket.until(lambda f: f.get('segment_complete'))
            await settle(case.runtime)
            root = next(iter(case.coordinator._roots.values()))
            watcher = Socket(owner)
            watch_task = asyncio.create_task(case.handler.handle_websocket(watcher))
            try:
                await watcher.until(lambda f: f['type'] == 'connection_ready')
                await watcher.send({'type': 'docking_watch', 'task_id': root.task_id})
                frame = await watcher.until(lambda f: f['type'] in {'error', 'docking_event'})
                assert frame['type'] == ('docking_event' if owner == 'owner-1' else 'error')
                await settle(case.runtime)
                await watcher.send({'type': 'docking_refine', 'request_id': root.root_id,
                                    'revision': 0, 'message': 'num_modes=5'})
                assert (await watcher.until(lambda f: f['type'] == 'error'))['code'] == 'docking_unavailable'
                assert len(rig.calls) == 1
            finally:
                await watcher.incoming.put(None)
                await asyncio.wait_for(watch_task, 5)
                await settle(case.runtime)
            assert not root.closed and not case.ordinary
    asyncio.run(run())


def test_source_capture_waits_for_actual_physical_complete(tmp_path, target_case, monkeypatch):
    async def run():
        async with source_receiver(tmp_path, target_case, monkeypatch) as case:
            entered, release = asyncio.Event(), asyncio.Event()
            original = case.socket.send_text
            async def held(text):
                if json.loads(text)['type'] == 'complete':
                    entered.set()
                    await release.wait()
                await original(text)
            monkeypatch.setattr(case.socket, 'send_text', held)
            try:
                await case.socket.send({'message': '解释分子量', 'enable_rag': False})
                await asyncio.wait_for(entered.wait(), 2)
                assert not case.captures
                assert not any(f['type'] == 'complete' for f in case.socket.frames)
            finally:
                release.set()
            await case.socket.until(lambda f: f['type'] == 'complete')
            await case.socket.send({'type': 'ping'})
            await case.socket.until(lambda f: f['type'] == 'pong')
            await settle(case.runtime)
            assert len(case.captures) == 1
    asyncio.run(run())


def test_mounted_asgi_route_uses_single_receiver_and_real_controller(actual_app, tmp_path):
    rig = Rig(tmp_path / 'asgi-rig')
    async def run():
        async with actual_app() as app_case:
            app = app_case.app
            runtime = app.decision_runtime
            assert runtime is app.chat_handler.decision_runtime
            assert isinstance(runtime, WebDecisionRuntime)
            rig.state = runtime.store
            async with rig.client() as client:
                coordinator = rig.coordinator(client)
                app.chat_handler.docking_controller = DockingChatController(runtime, coordinator, enabled=True)
                try:
                    async with ActualSocket(app, await cookie_for(app)) as socket:
                        ready = await socket.ready()
                        assert ready['normal_chat_mode'] == 'pi'
                        assert ready['capabilities']['chat_docking']['execution_authorized'] is False
                        await socket.send({'message': QUERY})
                        accepted = await socket.receive()
                        assert accepted['type'] == 'request_accepted'
                        for _ in range(10):
                            event = await socket.receive()
                            if event.get('segment_complete'):
                                break
                        assert event['type'] == 'docking_event' and event['request_id'] == accepted['trace_id']
                        await settle(runtime)
                        assert len(runtime.sockets) == 1 and len(rig.calls) == 1 and not app_case.calls
                finally:
                    await runtime.shutdown()
                assert not coordinator._roots
    asyncio.run(run())


@pytest.mark.parametrize('control', ['watch', 'invalid_refine'])
def test_a2_c_controls_precede_held_ordinary_turn_guard(actual_app, tmp_path, control):
    """Mounted Pi receiver and actual turn; only provider responses are synthetic."""
    from test_web_decision_runtime import chat_decision
    rig = Rig(tmp_path / 'held-ordinary-rig')
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def held_ordinary(payload):
            entered.set()
            await release.wait()
            return chat_decision()
        async with actual_app(respond=held_ordinary) as app_case:
            app = app_case.app
            runtime = app.decision_runtime
            rig.state = runtime.store
            async with rig.client() as client:
                coordinator = rig.coordinator(client)
                app.chat_handler.docking_controller = DockingChatController(runtime, coordinator, enabled=True)
                async with ActualSocket(app, await cookie_for(app)) as socket:
                    await socket.ready()
                    await socket.send({'message': QUERY})
                    accepted = await socket.receive()
                    assert accepted['type'] == 'request_accepted'
                    for _ in range(10):
                        event = await socket.receive()
                        if event.get('segment_complete'):
                            break
                    assert event['type'] == 'docking_event' and event.get('segment_complete')
                    await settle(runtime)
                    sender, = runtime.sockets
                    root = coordinator._owned(accepted['trace_id'], sender.scope['agent_session_id'])
                    assert root is not None and not app.chat_handler.docking_controller.busy(sender)
                    try:
                        await socket.send({'message': '解释分子量', 'enable_rag': False})
                        ordinary = await socket.receive()
                        assert ordinary['type'] == 'request_accepted'
                        # Actual Pi starts before the provider can be held.
                        # Post-entry control/busy and cleanup waits stay short.
                        await asyncio.wait_for(entered.wait(), 15)
                        turn, = runtime.active_owners
                        assert not turn.task.done() and not turn.terminal_sent
                        if control == 'watch':
                            payload = {'type': 'docking_watch', 'task_id': root.task_id}
                        else:
                            payload = {'type': 'docking_refine', 'request_id': root.root_id,
                                       'revision': True, 'message': 'num_modes=5'}
                        await socket.send(payload)
                        # Ordinary execution may emit progress events while its
                        # provider is held; only the control's receipt is relevant.
                        for _ in range(50):
                            frame = await socket.receive()
                            if frame['type'] in {'error', 'docking_event'}:
                                break
                        if control == 'watch':
                            assert frame['type'] == 'docking_event', frame
                            assert frame['request_id'] == root.root_id
                        else:
                            assert frame.get('code') == 'invalid_docking_control', frame
                        assert not turn.task.done() and not turn.terminal_sent
                        assert coordinator._owned(root.root_id, root.owner) is root and not root.closed
                        # New ordinary/C chats are still new turns, not controls.
                        for message in ('解释分子量', QUERY):
                            await socket.send({'message': message})
                            for _ in range(50):
                                frame = await socket.receive()
                                if frame['type'] in {'error', 'request_accepted'}:
                                    break
                            assert frame.get('code') == 'turn_in_progress', frame
                        assert len(app_case.calls) == len(rig.calls) == 1
                        assert runtime.active_owners == {turn}
                    finally:
                        release.set()
                        await settle(runtime)
                await settle(runtime)
                assert not coordinator._roots
    asyncio.run(run())
