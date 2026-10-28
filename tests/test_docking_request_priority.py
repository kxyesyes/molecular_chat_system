"""Docking I/O can yield prewarm CPU without changing ASGI behavior."""
import asyncio
from contextlib import contextmanager

import pytest


@pytest.mark.parametrize('path', ['/api/docking/tasks', '/api/docking/result/id/summary',
                                  '/api/docking/pose_sdf/id', '/api/tasks', '/api/tasks/id'])
def test_priority_covers_receive_and_full_response(path):
    from src.web.foreground_io import DockingRequestPriorityMiddleware
    active = []
    @contextmanager
    def priority():
        active.append(True)
        try:
            yield
        finally:
            active.pop()
    async def receive():
        assert active == [True]
        return {'type': 'http.request', 'body': b'input'}
    messages = []
    async def send(message):
        assert active == [True]
        messages.append(message)
    async def app(scope, recv, emit):
        assert await recv() == {'type': 'http.request', 'body': b'input'}
        await emit({'type': 'http.response.start', 'status': 202, 'headers': []})
        await asyncio.sleep(0)
        await emit({'type': 'http.response.body', 'body': b'result'})
    asyncio.run(DockingRequestPriorityMiddleware(app, foreground_io=priority)(
        {'type': 'http', 'path': path}, receive, send))
    assert not active
    assert messages[-1]['body'] == b'result'


@pytest.mark.parametrize('scope', [{'type': 'http', 'path': '/api/tasks-other'},
                                  {'type': 'http', 'path': '/'},
                                  {'type': 'websocket', 'path': '/api/tasks/id'},
                                  {'type': 'lifespan'}])
def test_other_scopes_do_not_acquire_priority(scope):
    from src.web.foreground_io import DockingRequestPriorityMiddleware
    def priority():
        pytest.fail('unrelated scope acquired priority')
    called = []
    async def app(*args):
        called.append(args)
    asyncio.run(DockingRequestPriorityMiddleware(app, foreground_io=priority)(scope, None, None))
    assert called == [(scope, None, None)]


def test_overlaps_failure_and_cancellation_release_only_their_own_priority():
    from src.web.foreground_io import DockingRequestPriorityMiddleware
    count = 0
    @contextmanager
    def priority():
        nonlocal count
        count += 1
        try:
            yield
        finally:
            count -= 1
    async def scenario():
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        async def app(scope, receive, send):
            entered[scope['id']].set()
            await release.wait()
            raise OSError('expected')
        middleware = DockingRequestPriorityMiddleware(app, foreground_io=priority)
        tasks = [asyncio.create_task(middleware(
            {'type': 'http', 'path': '/api/tasks/id', 'id': i}, None, None)) for i in range(2)]
        await asyncio.gather(*(event.wait() for event in entered))
        assert count == 2
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert count == 1
        release.set()
        with pytest.raises(OSError):
            await tasks[1]
        assert count == 0
    asyncio.run(scenario())
