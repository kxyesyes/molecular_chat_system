"""Whole ASGI control ownership, using synthetic science and real sessions."""
import asyncio
import json
import threading
import time

import httpx
import pytest

from src.web.agent_session import COOKIE_NAME
from tests.agent.test_web_docking_http import app_for, body, cookies, ORIGIN, PATH
from tests.agent.test_web_docking_control_http import task_path
from tests.agent.test_web_docking_approval_http import approval, approve_path
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case
from tests.task_runtime.test_chat_docking_input_context import authenticated_rig


@pytest.mark.parametrize('disconnect', [False, True])
def test_control_auth_wait_is_bounded_owned_and_cannot_cancel_late(tmp_path, target_case, monkeypatch, disconnect):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            store = app.state.agent_session_store
            entered, release = threading.Event(), threading.Event()
            original = store.resolve_and_touch
            def held(value):
                entered.set()
                assert release.wait(15), 'session read barrier was not released'
                return original(value)
            monkeypatch.setattr(store, 'resolve_and_touch', held)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                call = asyncio.create_task(client.post(task_path(case) + '/cancel', headers={'Origin': ORIGIN}))
                try:
                    assert await asyncio.to_thread(entered.wait, 2)
                    if disconnect:
                        call.cancel()
                    done, _ = await asyncio.wait({call}, timeout=1 if disconnect else 6.5)
                    assert done, 'control deadline did not include session authentication'
                    if disconnect:
                        assert call.cancelled()
                    else:
                        assert call.result().status_code in {408, 503}
                    assert not case.root.closed and not case.raw
                    assert any(not task.done() for task in case.rig.gate._background), \
                        'physical session read lost ownership when HTTP returned'
                finally:
                    release.set()
                    await asyncio.gather(call, return_exceptions=True)
                    await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert not case.root.closed and case.context._execution is None and not case.raw
    asyncio.run(run())


def test_undelivered_prepare_nonce_cannot_remain_approvable(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch, capture_context=False) as case:
            app, token, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            sends = []
            async def send(message):
                sends.append(message['type'])
                if len(sends) == 1:
                    assert message['status'] == 201
                    entered.set()
                    while not release.is_set():
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            pass
            async def receive():
                return dict(type='http.request', body=json.dumps(body(case)).encode(), more_body=False)
            scope = dict(type='http', method='POST', scheme='https', path=PATH,
                raw_path=PATH.encode(), query_string=b'', server=('localhost', 443),
                http_version='1.1', client=('127.0.0.1', 1234),
                headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode()),
                    (b'content-type', b'application/json'),
                    (b'cookie', (COOKIE_NAME + '=' + token).encode())])
            call = asyncio.create_task(app(scope, receive, send))
            try:
                await asyncio.wait_for(entered.wait(), 8)
                assert case.root.phase == 'READY'
                call.cancel()
                done, _ = await asyncio.wait({call}, timeout=1)
                assert done, 'prepare delivery drained a cancellation-resistant send'
                assert call.cancelled()
                assert case.root.closed and case.root.phase != 'READY'
                assert any(not task.done() for task in case.rig.gate._background)
                assert not case.calls
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] != 'READY' and row['approval_nonce_hash'] is None
            assert sends == ['http.response.start'] and not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('disconnect', [False, True])
def test_http_send_is_retained_without_late_followup_frames(tmp_path, target_case, monkeypatch, disconnect):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            sends = []
            async def send(message):
                sends.append(message['type'])
                if len(sends) == 1:
                    entered.set()
                    while not release.is_set():
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            pass
            async def receive():
                return dict(type='http.request', body=b'', more_body=False)
            scope = dict(type='http', method='GET', scheme='https', path=task_path(case),
                raw_path=task_path(case).encode(), query_string=b'', server=('localhost', 443),
                http_version='1.1', client=('127.0.0.1', 1234),
                headers=[(b'host', b'localhost'), (b'cookie', (COOKIE_NAME + '=' + token).encode())])
            call = asyncio.create_task(app(scope, receive, send))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if disconnect:
                    call.cancel()
                done, _ = await asyncio.wait({call}, timeout=1 if disconnect else 6.5)
                assert done, 'control HTTP drained a cancellation-resistant physical send'
                if disconnect:
                    assert call.cancelled()
                else:
                    call.result()
                assert any(not task.done() for task in case.rig.gate._background)
                assert sends == ['http.response.start']
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert sends == ['http.response.start'], 'closed feedback started an additional frame'
            assert case.context._execution is None and not case.raw
    asyncio.run(run())


def test_auth_and_body_share_one_control_budget(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            store = app.state.agent_session_store
            original = store.resolve_and_touch
            def delayed(value):
                time.sleep(1.4)
                return original(value)
            monkeypatch.setattr(store, 'resolve_and_touch', delayed)
            entered, release = asyncio.Event(), asyncio.Event()
            messages = []
            async def receive():
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
                return dict(type='http.request', body=b'', more_body=False)
            async def send(message):
                messages.append(message)
            path = task_path(case) + '/cancel'
            scope = dict(type='http', method='POST', scheme='https', path=path,
                raw_path=path.encode(), query_string=b'', server=('localhost', 443),
                http_version='1.1', client=('127.0.0.1', 1234),
                headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode()),
                    (b'cookie', (COOKIE_NAME + '=' + token).encode())])
            call = asyncio.create_task(app(scope, receive, send))
            try:
                done, _ = await asyncio.wait({call}, timeout=5.8)
                assert entered.is_set()
                assert done, 'route restarted the control budget after session authentication'
                call.result()
                starts = [m for m in messages if m['type'] == 'http.response.start']
                assert len(starts) == 1 and starts[0]['status'] in {408, 503}
                assert not case.root.closed
                assert any(not task.done() for task in case.rig.gate._background)
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert not case.root.closed and case.context._execution is None and not case.raw
    asyncio.run(run())


def test_whole_http_approval_loss_does_not_cancel_accepted_c2(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            original = case.runtime._admit_consent_execution
            async def held(operation):
                entered.set()
                await release.wait()
                return await original(operation)
            monkeypatch.setattr(case.runtime, '_admit_consent_execution', held)
            operation = None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                call = asyncio.create_task(client.post(approve_path(case), json=approval(case),
                    headers={'Origin': ORIGIN}))
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    operation = case.context._execution
                    assert operation is not None
                    call.cancel()
                    done, _ = await asyncio.wait({call}, timeout=1)
                    assert done and call.cancelled()
                    assert not operation.signal.is_set() and not case.root.closed
                finally:
                    release.set()
                    await asyncio.gather(call, return_exceptions=True)
                    await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                if operation.settlement is not None:
                    await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
                assert case.raw == [True] and operation.status == 'SUCCEEDED' and operation.settled
    asyncio.run(run())
