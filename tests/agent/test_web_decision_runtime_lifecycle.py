"""Normal application lifetime tests with in-memory HTTP protocol barriers."""
import asyncio
import json
import inspect
from types import SimpleNamespace

import pytest

from test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision, result_of


@pytest.mark.parametrize('failure', ['run', 'watch_cancel'])
def test_watcher_creation_failure_cannot_release_running_provider(actual_app, monkeypatch, failure):
    """SPEC actual-route probe, including safe rejection before provider start."""
    async def run():
        entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        children, unscheduled, snapshots = [], [], []
        create_task = asyncio.create_task

        async def respond(payload):
            entered.set()
            try:
                await release.wait()
                return chat_decision('released provider')
            finally:
                exited.set()

        def inject(coro, *args, **kwargs):
            is_run = kwargs.get('name') == 'isolated-decision-chat'
            is_watch = getattr(getattr(coro, 'cr_code', None), 'co_name', None) == 'watch_cancel'
            if (failure == 'run' and is_run) or (failure == 'watch_cancel' and is_watch):
                unscheduled.append(coro)
                raise RuntimeError('synthetic scheduling failure')
            task = create_task(coro, *args, **kwargs)
            if is_run:
                children.append(task)
            return task

        async with actual_app(mode='decision_a2', respond=respond) as b:
            from src.web import decision_runtime
            send = decision_runtime._Sender.send_text
            async def observe(self, text, **kwargs):
                if json.loads(text)['type'] in {'agent_result', 'complete'}:
                    snapshots.append(dict(provider_entered=entered.is_set(), provider_exited=exited.is_set(),
                        child_done=all(t.done() for t in children)))
                return await send(self, text, **kwargs)
            monkeypatch.setattr(decision_runtime._Sender, 'send_text', observe)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                monkeypatch.setattr(asyncio, 'create_task', inject)
                try:
                    frames = await socket.turn({'message': '你好'})
                    assert result_of(frames)['status'] == 'failed'
                    if failure == 'watch_cancel' and any(not t.done() for t in children):
                        # Preserve the old SPEC failure as a positive diagnostic:
                        # the leaked real provider starts AFTER premature terminal.
                        # Correct rollback may cancel before dispatch, so it never
                        # needs this wait. No sleep or production scheduling hook.
                        await asyncio.wait_for(entered.wait(), 3)
                    snapshots.append(dict(provider_entered=entered.is_set(), provider_exited=exited.is_set(),
                        child_done=all(t.done() for t in children),
                        readers=b.app.model_request_gate._readers,
                        active_owners=len(b.app.decision_runtime.active_owners)))
                    assert snapshots and all(s['child_done'] and (
                        not s['provider_entered'] or s['provider_exited']) for s in snapshots), snapshots
                    assert unscheduled and all(inspect.getcoroutinestate(c) == inspect.CORO_CLOSED
                                               for c in unscheduled)
                    assert b.app.model_request_gate._readers == 0
                    assert not b.app.decision_runtime.active_owners
                finally:
                    # Even RED exits retain all created tasks and close the
                    # unscheduled coroutines, with no test/production escape.
                    release.set()
                    for coro in unscheduled:
                        coro.close()
                    await asyncio.wait_for(asyncio.gather(*children, return_exceptions=True), 5)
    asyncio.run(run())


def test_cancel_blocked_provider_then_next_turn_on_same_socket(actual_app):
    async def run():
        entered, released = asyncio.Event(), asyncio.Event()
        count = 0

        async def respond(payload):
            nonlocal count
            count += 1
            if count == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    released.set()
            return chat_decision('next turn')

        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send({'type': 'chat', 'message': '你好'})
                accepted = await socket.receive()
                assert accepted['type'] == 'request_accepted'
                await asyncio.wait_for(entered.wait(), 3)
                await socket.send({'type': 'ping', 'timestamp': 0})
                for _ in range(15):
                    frame = await socket.receive()
                    if frame['type'] == 'pong':
                        break
                else:
                    assert False, 'receiver stopped during model dispatch'
                assert frame['timestamp'] == 0
                await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                frames = []
                for _ in range(20):
                    frame = await socket.receive()
                    frames.append(frame)
                    if frame['type'] == 'complete':
                        break
                assert result_of(frames)['status'] == 'cancelled'
                assert released.is_set()
                assert b.app.model_request_gate._readers == 0
                assert not b.app.decision_runtime.active_owners
                assert result_of(await socket.turn({'message': '你好'}))['final_answer'] == 'next turn'
    asyncio.run(run())


def test_sender_deadline_includes_serialization_lock(monkeypatch):
    from src.web import decision_runtime
    async def run():
        monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.03)
        sent = []
        async def send_text(text):
            sent.append(text)
        sender = decision_runtime._Sender(SimpleNamespace(scope={}, send_text=send_text))
        await sender.lock.acquire()
        task = asyncio.create_task(sender.send_text('{}'))
        try:
            done, _ = await asyncio.wait({task}, timeout=0.2)
            assert done, 'waiting for send lock escaped the total deadline'
            with pytest.raises(asyncio.TimeoutError):
                task.result()
            assert not sender.writable and not sent
        finally:
            sender.lock.release()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['accept-send', 'task-create'])
def test_failed_turn_admission_leaves_no_orphan_owner(actual_app, monkeypatch, failure):
    from src.web import decision_runtime
    async def run():
        async with actual_app(mode='decision_a2') as b:
            sender = decision_runtime._Sender.send_text
            create_task = asyncio.create_task
            hit = asyncio.Event()
            async def fail_send(self, text, **kwargs):
                if json.loads(text)['type'] == 'request_accepted':
                    hit.set()
                    raise ConnectionError('synthetic transport failure')
                return await sender(self, text, **kwargs)
            def fail_create(coro, **kwargs):
                if getattr(coro, 'cr_code', None) is decision_runtime.WebDecisionRuntime._execute.__code__:
                    hit.set()
                    raise RuntimeError('synthetic scheduling failure')
                return create_task(coro, **kwargs)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                if failure == 'accept-send':
                    monkeypatch.setattr(decision_runtime._Sender, 'send_text', fail_send)
                else:
                    monkeypatch.setattr(asyncio, 'create_task', fail_create)
                await socket.send({'message': '你好'})
                await asyncio.wait_for(hit.wait(), 3)
            assert not b.app.decision_runtime.active_owners
            assert not b.app.decision_runtime.tasks
            assert b.app.model_request_gate._readers == 0
            assert b.calls == []
    asyncio.run(run())


def test_ws_switch_waits_for_captured_model_and_uses_new_epoch_next_turn(actual_app, monkeypatch):
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    import httpx
    from test_web_decision_runtime import protocol_response

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(payload):
            entered.set()
            await release.wait()
            return chat_decision('old epoch')
        async with actual_app(mode='decision_a2', respond=respond) as b:
            old_epoch = b.app.model_generation
            closed = []
            async def close():
                closed.append(True)
            monkeypatch.setattr(b.model, 'close', close)
            new_calls = []
            def reply(request):
                new_calls.append(True)
                return protocol_response(chat_decision('new epoch'))
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                new_model = OpenAICompatibleModel('synthetic-rotated-key', 'protocol-only',
                                                  'https://example.invalid/v1', client=client)
                monkeypatch.setattr(b.app, '_create_model_from_llm_config', lambda config: new_model)
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    await socket.ready()
                    await socket.send({'message': '你好'})
                    await asyncio.wait_for(entered.wait(), 3)
                    switch = asyncio.create_task(b.app._replace_llm_config(dict(b.app.active_llm_config)))
                    try:
                        scheduled = asyncio.Event()
                        asyncio.get_running_loop().call_soon(scheduled.set)
                        await scheduled.wait()
                        assert b.app.model_request_gate._writers == 1
                        assert not switch.done() and not closed
                        assert b.app.model_generation == old_epoch
                        assert b.app.model_request_gate._readers == 1
                    finally:
                        release.set()
                    frames = []
                    for _ in range(20):
                        frame = await socket.receive()
                        frames.append(frame)
                        if frame['type'] == 'complete':
                            break
                    assert result_of(frames)['final_answer'] == 'old epoch'
                    await asyncio.wait_for(switch, 3)
                    assert len(closed) == 1 and b.app.model_generation != old_epoch
                    assert result_of(await socket.turn({'message': '你好'}))['final_answer'] == 'new epoch'
                    assert len(new_calls) == 1
    asyncio.run(run())
