"""Physical HTTP ownership boundaries; all scientific execution is synthetic."""
import asyncio
from dataclasses import replace
import threading

import httpx
import pytest
from starlette.requests import Request

from tests.agent.test_web_docking_http import app_for, cookies, ORIGIN
from tests.agent.test_web_docking_control_http import task_path, revoke_path
from tests.agent.test_web_docking_execution import web_approve
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case


def direct_control(app, case, receive):
    route = next((route for route in app.routes
                  if route.path == '/api/agent/docking/preparations/{preparation_id}/revoke'), None)
    assert route is not None, 'explicit revoke control is missing'
    scope = dict(type='http', method='POST', scheme='https', path=revoke_path(case),
        query_string=b'', server=('localhost', 443),
        headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode())],
        agent_session_id=case.root.owner)
    return route.endpoint(preparation_id=case.root.preparation_id,
                          request=Request(scope, receive))


@pytest.mark.parametrize('disconnect', [False, True])
def test_late_empty_body_cannot_revoke_after_feedback_closes(tmp_path, target_case, monkeypatch, disconnect):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, _, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            async def receive():
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass  # A physical read which cannot be cancelled yet.
                return dict(type='http.request', body=b'', more_body=False)
            call = asyncio.create_task(direct_control(app, case, receive))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if disconnect:
                    call.cancel()
                done, _ = await asyncio.wait({call}, timeout=6.5)
                assert done, 'HTTP feedback drained an uncooperative receive'
                if disconnect:
                    assert call.cancelled()
                else:
                    # An HTTPException or a response are both transport outcomes;
                    # neither may later promote the timed-out control to mutation.
                    try:
                        response = call.result()
                    except Exception as exc:
                        assert getattr(exc, 'status_code', None) in {408, 503}
                    else:
                        assert response.status_code in {408, 503}
                assert not case.root.closed
                assert any(not task.done() for task in case.rig.gate._background)
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert not case.root.closed and case.context._execution is None and not case.raw
    asyncio.run(run())


@pytest.mark.parametrize('headers', [
    [('Transfer-Encoding', 'chunked'), ('Content-Length', '0')],
    [('Transfer-Encoding', 'chunked'), ('Transfer-Encoding', 'chunked')],
    [('Content-Length', '0'), ('Content-Length', '0')],
    [('Content-Encoding', 'identity')],
])
def test_control_framing_rejected_without_mutation(tmp_path, target_case, monkeypatch, headers):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url=ORIGIN, cookies=cookies(token)) as client:
                for path in (revoke_path(case), task_path(case) + '/cancel'):
                    response = await client.post(path, headers=[('Origin', ORIGIN)] + headers)
                    assert response.status_code in {415, 422}
            assert not case.root.closed and case.context._execution is None and not case.raw
    asyncio.run(run())


def test_absent_live_owner_is_observation_only(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            root = case.coordinator._roots.pop(case.root.root_id)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        base_url=ORIGIN, cookies=cookies(token)) as client:
                    response = await client.get(task_path(case))
                    assert response.status_code == 200
                    view = response.json()
                    assert view['execution_authorized'] is False and view['input_ready'] is False
                    assert view['phase'] not in {'READY', 'SUCCEEDED', 'RESULT_PENDING'}
                    for private in ('approval_nonce', 'binding_json', 'manifest_locator', 'source_preview'):
                        assert private not in response.text
                    for path in (revoke_path(case), task_path(case) + '/cancel'):
                        response = await client.post(path, headers={'Origin': ORIGIN})
                        assert response.status_code in {409, 503}
                assert case.root.root_id not in case.coordinator._roots
                assert not root.closed and case.context._execution is None and not case.raw
            finally:
                case.coordinator._roots[root.root_id] = root
    asyncio.run(run())


def test_slow_durable_owner_lookup_remains_owned_without_science(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            root = case.coordinator._roots.pop(case.root.root_id)
            entered, release = threading.Event(), threading.Event()
            original = case.runtime.store._owned_docking_consent
            def held(**kwargs):
                entered.set()
                assert release.wait(20), 'synthetic owner-read barrier unreleased'
                return original(**kwargs)
            monkeypatch.setattr(case.runtime.store, '_owned_docking_consent', held)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        base_url=ORIGIN, cookies=cookies(token)) as client:
                    call = asyncio.create_task(client.get(task_path(case)))
                    assert await asyncio.to_thread(entered.wait, 3)
                    done, _ = await asyncio.wait({call}, timeout=6.5)
                    assert done and call.result().status_code == 503
                    assert any(not task.done() for task in case.rig.gate._background)
                    assert not root.closed and not case.raw
            finally:
                release.set()
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                case.coordinator._roots[root.root_id] = root
    asyncio.run(run())


def test_duplicate_cancel_shares_physical_cleanup_owner(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            def held_raw():
                entered.set()
                assert release.wait(25), 'synthetic worker barrier unreleased'
            case.raw_hook = held_raw
            await web_approve(case)
            assert await asyncio.to_thread(entered.wait, 8)
            execution = case.context._execution
            original = case.coordinator._close_execution
            closes = []
            async def counted(root):
                closes.append(root)
                return await original(root)
            monkeypatch.setattr(case.coordinator, '_close_execution', counted)
            app, token, _ = app_for(case, tmp_path)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        base_url=ORIGIN, cookies=cookies(token)) as client:
                    responses = await asyncio.wait_for(asyncio.gather(*(
                        client.post(task_path(case) + '/cancel', headers={'Origin': ORIGIN})
                        for _ in range(6))), 7)
                    assert all(response.status_code == 202 for response in responses)
                    assert all(response.json()['cleanup_pending'] is True for response in responses)
                    assert closes == [case.root]
                    assert execution.signal.is_set() and not execution.settled
                    assert case.coordinator._roots[case.root.root_id] is case.root
                    assert len(case.raw) == 1
            finally:
                release.set()
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert execution.settled and execution.status == 'CANCELLED'
            assert closes == [case.root]
    asyncio.run(run())


def test_cancel_does_not_inherit_racing_revoke_rejection(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, _, _ = app_for(case, tmp_path)
            release = threading.Event()
            def raw():
                assert release.wait(20), 'synthetic worker barrier unreleased'
            case.raw_hook = raw
            body_ready = asyncio.Event()
            async def interleaved_approval():
                await body_ready.wait()
                # Body completion wakes both controls before their scheduled
                # physical close starts. This is ordinary event-loop ordering.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                return await web_approve(case)
            approval_task = asyncio.create_task(interleaved_approval())

            async def invoke(revoke):
                pattern = ('/api/agent/docking/preparations/{preparation_id}/revoke'
                           if revoke else '/api/agent/docking/tasks/{task_id}/cancel')
                endpoint = next(route.endpoint for route in app.routes if route.path == pattern)
                async def receive():
                    body_ready.set()
                    return dict(type='http.request', body=b'', more_body=False)
                request = Request(dict(type='http', method='POST', scheme='https',
                    path=pattern, query_string=b'', server=('localhost', 443),
                    headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode())],
                    agent_session_id=case.root.owner), receive)
                kwargs = ({'preparation_id': case.root.preparation_id} if revoke
                          else {'task_id': case.root.task_id})
                try:
                    return (await endpoint(request=request, **kwargs)).status_code
                except Exception as exc:
                    return getattr(exc, 'status_code', None)
            try:
                outcomes = await asyncio.gather(invoke(True), invoke(False))
                assert outcomes[0] in {200, 202, 409}
                assert outcomes[1] in {200, 202}, 'valid cancel inherited revoke-only rejection'
                assert case.root.closed
                if case.context._execution is not None:
                    assert case.context._execution.signal.is_set()
            finally:
                release.set()
                await asyncio.gather(approval_task, return_exceptions=True)
                await case.coordinator.close_root(root_id=case.root.root_id,
                                                 owner_session_id=case.root.owner)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
    asyncio.run(run())


def test_queued_close_drains_captured_root_not_replacement(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, _, _ = app_for(case, tmp_path)
            body_ready = asyncio.Event()
            replacement = replace(case.root, closed=False, task=None, input_context=None,
                preparation_owners=[], issuer=None, draft=None, draft_attempted=False,
                close_task=None, close_state='not_started', durable=False)
            async def change_mapping():
                await body_ready.wait()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert case.root.closed, 'control admission did not stop the original root'
                # Ordinary begin() never replaces a retained root. Deliberately
                # replace its locator to verify queued cleanup uses its owner.
                case.coordinator._roots[case.root.root_id] = replacement
            changer = asyncio.create_task(change_mapping())
            async def receive():
                body_ready.set()
                return dict(type='http.request', body=b'', more_body=False)
            try:
                response = await direct_control(app, case, receive)
                await changer
                assert response.status_code in {200, 202}
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                assert not replacement.closed, 'cleanup stopped a replacement root'
                assert case.root.close_state == 'confirmed'
                assert case.root.input_context is None
                assert case.coordinator._roots[case.root.root_id] is replacement
                assert not case.raw
            finally:
                await asyncio.gather(changer, return_exceptions=True)
                case.coordinator._roots[case.root.root_id] = case.root
                await case.coordinator.close_root(root_id=case.root.root_id,
                                                 owner_session_id=case.root.owner)
    asyncio.run(run())
