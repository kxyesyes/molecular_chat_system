"""Owner-only HTTP observation/revocation/cancel; synthetic C2, never Vina."""
import asyncio
import threading

import httpx

from src.web.agent_session import COOKIE_NAME
from tests.agent.test_web_docking_http import app_for, cookies, ORIGIN, PATH
from tests.agent.test_web_docking_execution import web_approve
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case


def task_path(case):
    return '/api/agent/docking/tasks/' + case.root.task_id


def revoke_path(case):
    return PATH + '/' + case.root.preparation_id + '/revoke'


def test_owned_status_is_read_only_and_unused_revoke_has_no_raw_call(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.get(task_path(case))
                assert response.status_code == 200 and response.json()['phase'] == 'READY'
                assert 'approval_nonce' not in response.text and case.context._execution is None
                response = await client.post(revoke_path(case), headers={'Origin': ORIGIN})
                assert response.status_code in {200, 202}
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                assert not case.raw and case.context._execution is None
                status = await client.get(task_path(case))
                assert status.status_code == 200
                assert status.json()['phase'] in {'CLOSED', 'CANCELLED'}
                assert not status.json().get('cleanup_pending', False)
                assert 'approval_nonce' not in status.text
    asyncio.run(run())


def test_controls_require_owner_origin_and_empty_body_before_mutation(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, foreign = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                for path in (revoke_path(case), task_path(case) + '/cancel'):
                    for headers in ({}, {'Origin': 'null'}, {'Origin': 'https://foreign.invalid'},
                                    [('Origin', ORIGIN), ('Origin', ORIGIN)]):
                        assert (await client.post(path, headers=headers)).status_code == 403
                    assert (await client.post(path, json={}, headers={'Origin': ORIGIN})).status_code == 422
                    assert (await client.post(path + '?owner=x', headers={'Origin': ORIGIN})).status_code == 422
                    response = await client.post(path, headers={'Origin': ORIGIN, 'Cookie': COOKIE_NAME + '=' + foreign})
                    assert response.status_code == 404
                    client.cookies = cookies(token)
                foreign_status = await client.get(task_path(case), headers={'Cookie': COOKIE_NAME + '=' + foreign})
                unknown_status = await client.get('/api/agent/docking/tasks/missing')
                assert foreign_status.status_code == unknown_status.status_code == 404
                assert foreign_status.json() == unknown_status.json()
            assert not case.root.closed and not case.raw and case.context._execution is None
    asyncio.run(run())


def test_http_cancel_signals_live_owner_and_returns_before_physical_worker(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            def held_raw():
                entered.set()
                assert release.wait(20), 'synthetic worker barrier unreleased'
            case.raw_hook = held_raw
            await web_approve(case)
            assert await asyncio.to_thread(entered.wait, 8)
            operation = case.context._execution
            app, token, _ = app_for(case, tmp_path)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                        cookies=cookies(token)) as client:
                    revoked = await client.post(revoke_path(case), headers={'Origin': ORIGIN})
                    assert revoked.status_code == 409 and not operation.signal.is_set()
                    response = await asyncio.wait_for(client.post(task_path(case) + '/cancel',
                        headers={'Origin': ORIGIN}), 7)
                    assert response.status_code == 202 and response.json()['cleanup_pending'] is True
                    assert operation.signal.is_set() and not operation.settled
                    assert case.coordinator._roots[case.root.root_id] is case.root
                    assert 'binding_energy' not in response.text
            finally:
                release.set()
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert operation.settled and operation.status == 'CANCELLED'
    asyncio.run(run())
