"""Explicit approval HTTP over real sessions/SQLite and synthetic C2 only."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from src.web.agent_session import COOKIE_NAME
from tests.agent.test_web_docking_http import app_for, cookies, ORIGIN, PATH
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case


def approval(case):
    return dict(approval_nonce=case.proof['approval_nonce'],
                binding_digest=case.proof['binding_digest'], confirm=True)


def approve_path(case):
    return PATH + '/' + case.root.preparation_id + '/approve'


@pytest.mark.parametrize('streamed', [False, True])
def test_http_approval_uses_owned_context_once_and_exposes_no_success(tmp_path, target_case, monkeypatch, streamed):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                if streamed:
                    async def chunks():
                        yield json.dumps(approval(case)).encode()
                    response = await client.post(approve_path(case), content=chunks(), headers={
                        'Origin': ORIGIN, 'Content-Type': 'application/json'})
                else:
                    response = await client.post(approve_path(case), json=approval(case), headers={'Origin': ORIGIN})
                assert response.status_code in {200, 202}, response.text
                assert response.headers['cache-control'] == 'private, no-store'
                operation = case.context._execution
                await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
                repeat = await client.post(approve_path(case), json=approval(case), headers={'Origin': ORIGIN})
                assert repeat.status_code == 200 and repeat.json()['phase'] == 'RESULT_PENDING'
                assert case.raw == [True]
                for value in (case.proof['approval_nonce'], 'binding_energy', 'artifact_id', str(tmp_path)):
                    assert value not in response.text + repeat.text
    asyncio.run(run())


def test_http_approval_auth_and_origin_precede_claim(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, foreign = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                for headers in ({}, {'Origin': 'null'}, {'Origin': 'https://foreign.invalid'},
                                [('Origin', ORIGIN), ('Origin', ORIGIN)]):
                    assert (await client.post(approve_path(case), json=approval(case), headers=headers)).status_code == 403
                stranger = await client.post(approve_path(case), json=approval(case), headers={
                    'Origin': ORIGIN, 'Cookie': COOKIE_NAME + '=' + foreign})
                missing = await client.post(PATH + '/missing/approve', json=approval(case), headers={'Origin': ORIGIN})
                assert stranger.status_code == missing.status_code == 404
                assert stranger.json() == missing.json()
                # The middleware refreshed the foreign request's cookie; restore
                # the owner before testing the owner's mismatched proof.
                client.cookies = cookies(token)
                bad = await client.post(approve_path(case), json=dict(approval(case), approval_nonce='0' * 64),
                    headers={'Origin': ORIGIN})
                assert bad.status_code == 409
            app, _, _ = app_for(case, tmp_path, middleware=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                assert (await client.post(approve_path(case), json=approval(case), headers={'Origin': ORIGIN})).status_code == 401
            assert case.context._execution is None and not case.raw
    asyncio.run(run())


def test_http_approval_requires_exact_native_closed_body(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            values = [{}, [], dict(approval(case), confirm=1), dict(approval(case), confirm=False),
                      dict(approval(case), backend='local'), dict(approval(case), owner=case.root.owner),
                      dict(approval(case), parameters={}), dict(approval(case), approval_nonce='x'),
                      dict(approval(case), binding_digest=None)]
            raw_values = [json.dumps(value) for value in values] + ['{', 'NaN',
                json.dumps(approval(case))[:-1] + ',"confirm":true}']
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                for raw in raw_values:
                    response = await client.post(approve_path(case), content=raw,
                        headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
                    assert response.status_code == 422
                assert (await client.post(approve_path(case) + '?backend=local', json=approval(case),
                    headers={'Origin': ORIGIN})).status_code == 422
                assert (await client.post(approve_path(case), content=' ' * 4097,
                    headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})).status_code == 413
                assert (await client.post(approve_path(case), content=json.dumps(approval(case)),
                    headers={'Origin': ORIGIN, 'Content-Type': 'text/plain'})).status_code == 415
            assert case.context._execution is None and not case.raw
    asyncio.run(run())


def test_disabled_http_approval_has_no_execution_path(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            for enabled in (False, None, 1):
                app, token, _ = app_for(case, tmp_path, enabled=enabled)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                        cookies=cookies(token)) as client:
                    assert (await client.post(approve_path(case), json=approval(case), headers={'Origin': ORIGIN})).status_code == 404
            assert case.context._execution is None and not case.raw
    asyncio.run(run())


@pytest.mark.parametrize('change,status', [('expired', 410), ('revoked', 409), ('policy', 503)])
def test_http_approval_reports_closed_own_consent_failures(tmp_path, target_case, monkeypatch, change, status):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            if change == 'expired':
                case.rig.wall += 900000
                case.rig.mono += 900
            elif change == 'revoked':
                with sqlite3.connect(case.rig.tasks.db_path) as db:
                    db.execute("UPDATE docking_consents SET state='REVOKED'")
            else:
                case.runtime._docking_consent_policy = None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.post(approve_path(case), json=approval(case), headers={'Origin': ORIGIN})
                if case.runtime._consent_preparations:
                    case.unresolved = True
                assert response.status_code == status, response.text
                assert 'nonce' not in response.text and str(tmp_path) not in response.text
            assert not case.raw and case.context._execution is None
    asyncio.run(run())


def test_http_claimed_but_uncertain_approval_is_not_reported_as_unused_proof_conflict(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            original = case.rig.tasks._claim_docking_consent
            def claim(*args, **kwargs):
                acquired = original(*args, **kwargs)
                case.rig.context['model_generation'] = 'after-claim-invalidated'
                return acquired
            monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            case.unresolved = True
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.post(approve_path(case), json=approval(case), headers={'Origin': ORIGIN})
                assert response.status_code == 503 and response.json()['cleanup_pending'] is True
                repeated = await client.post(approve_path(case), json=approval(case), headers={'Origin': ORIGIN})
                assert repeated.status_code == 503 and repeated.json()['cleanup_pending'] is True
                assert repeated.json() == response.json()
                bad = await client.post(approve_path(case), json=dict(approval(case), approval_nonce='0' * 64),
                    headers={'Origin': ORIGIN})
                assert bad.status_code == 409
            operation = case.context._execution
            assert operation.acquired and operation.status == 'UNKNOWN' and operation.submission is None
            assert case.coordinator._roots[case.root.root_id] is case.root and not case.raw
    asyncio.run(run())


@pytest.mark.parametrize('framing', ['length_and_transfer', 'duplicate_transfer', 'unsupported_transfer'])
def test_approval_rejects_ambiguous_transfer_framing_before_claim(tmp_path, target_case, monkeypatch, framing):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            raw = json.dumps(approval(case)).encode()
            headers = [('Origin', ORIGIN), ('Content-Type', 'application/json')]
            if framing == 'length_and_transfer':
                headers += [('Content-Length', str(len(raw))), ('Transfer-Encoding', 'chunked')]
            elif framing == 'duplicate_transfer':
                headers += [('Transfer-Encoding', 'chunked'), ('Transfer-Encoding', 'gzip')]
            else:
                headers += [('Transfer-Encoding', 'gzip')]
            async def chunks():
                yield raw
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.post(approve_path(case), content=chunks(), headers=headers)
                assert response.status_code == 422
            assert case.context._execution is None and not case.raw
    asyncio.run(run())
