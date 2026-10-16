"""Prepare-only HTTP: real sessions/SQLite/C1, synthetic source, no Vina."""
import asyncio
import importlib
import json
import sqlite3

import httpx
import pytest
from fastapi import FastAPI

from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore, COOKIE_NAME
from tests.task_runtime.test_chat_docking_input_context import authenticated_rig, target_case


PATH = '/api/agent/docking/preparations'
ORIGIN = 'https://localhost'


def api():
    name = 'src.web.routes.docking_consent_routes'
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail('prepare-only HTTP route is absent', pytrace=False)
    return module.setup_docking_consent_routes


def app_for(case, tmp_path, *, enabled=True, middleware=True):
    store = AgentSessionStore(tmp_path / 'sessions.sqlite')
    token, _ = store.issue()
    with store._connect() as db:
        db.execute('UPDATE agent_sessions SET session_id=? WHERE token_digest=?',
                   (case.root.owner, store._digest(token)))
    foreign, _ = store.issue()
    app = FastAPI()
    if middleware:
        app.add_middleware(AgentSessionMiddleware, store=store)
    api()(app, coordinator=case.coordinator, task_runtime=case.runtime, enabled=enabled)
    return app, token, foreign


def body(case):
    return dict(root=case.root.root_id, revision=case.root.accepted_revision,
        receptor_ref=case.root.accepted_slots['receptor_ref']['value'],
        ligand_ref=case.root.accepted_slots['ligand_ref']['value'])


def cookies(token):
    # A hostless test cookie plus the response's host cookie would create two
    # same-name cookies; the real middleware correctly rejects that ambiguity.
    jar = httpx.Cookies()
    jar.set(COOKIE_NAME, token, domain='localhost.local', path='/')
    return jar


def test_owner_prepare_uses_frozen_server_bytes_and_nonce_is_not_recoverable(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch, capture_context=False) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.post(PATH, json=body(case), headers={'Origin': ORIGIN})
                assert response.status_code == 201, response.text
                payload = response.json()
                assert payload['phase'] == 'READY' and payload['execution_authorized'] is False
                assert payload['consent_preview']['approval_nonce']
                assert response.headers['cache-control'] == 'private, no-store'
                repeat = await client.post(PATH, json=body(case), headers={'Origin': ORIGIN})
                assert repeat.status_code == 200 and 'approval_nonce' not in repeat.text
                assert case.stages == [case.root.task_id] and not case.calls
                assert (await client.get(PATH)).status_code == 405
                await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_prepare_requires_exact_same_origin_and_owned_existing_root(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            app, token, foreign = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                for headers in ({}, {'Origin': 'https://foreign.invalid'}, {'Origin': 'null'},
                                [('Origin', ORIGIN), ('Origin', ORIGIN)]):
                    assert (await client.post(PATH, json=body(case), headers=headers)).status_code == 403
                stranger = await client.post(PATH, json=body(case), headers={
                    'Origin': ORIGIN, 'Cookie': COOKIE_NAME + '=' + foreign})
                missing = await client.post(PATH, json=dict(body(case), root='unknown'), headers={'Origin': ORIGIN})
                assert stranger.status_code == missing.status_code == 404
                assert stranger.json() == missing.json()
            app, _, _ = app_for(case, tmp_path, middleware=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                assert (await client.post(PATH, json=body(case), headers={'Origin': ORIGIN})).status_code == 401
            assert not case.stages and not case.calls and not case.context._claimed
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_prepare_body_is_closed_duplicate_free_native_and_bounded(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                invalid = [dict(body(case), **{key: value}) for key, value in
                    [('revision', True), ('revision', 1.0), ('revision', -1), ('root', '../x'),
                     ('root', None), ('root', 'x' * 129), ('receptor_ref', '/private/file.pdb'),
                     ('ligand_ref', 'https://example.invalid/file'), ('owner', 'owner-session'),
                     ('parameters', {}), ('receptor_file', 'bytes'), ('backend', 'local')]]
                invalid += [{}, [], dict(root='root-1')]
                raw_values = [json.dumps(value) for value in invalid] + [
                    '{"root":"root-1","root":"root-1"}', '{', 'NaN',
                    json.dumps(body(case))[:-1] + ',"revision":NaN}']
                for raw in raw_values:
                    response = await client.post(PATH, content=raw,
                        headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
                    assert response.status_code == 422, (raw, response.status_code)
                assert (await client.post(PATH, content='x' * 4097,
                    headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})).status_code == 413
                assert (await client.post(PATH, content=json.dumps(body(case)),
                    headers={'Origin': ORIGIN, 'Content-Type': 'text/plain'})).status_code == 415
            assert not case.stages and not case.calls and not case.context._claimed
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_disabled_registration_has_no_prepare_path(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            for flag in (False, None, 1):
                app, token, _ = app_for(case, tmp_path, enabled=flag)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                        cookies=cookies(token)) as client:
                    assert (await client.post(PATH, json=body(case), headers={'Origin': ORIGIN})).status_code == 404
            assert not case.stages and not case.calls
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_streaming_body_rejects_oversize_before_consuming_remaining_chunks(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            consumed = []
            async def chunks():
                consumed.append(1)
                yield b' ' * 3000
                consumed.append(2)
                yield b' ' * 2000
                consumed.append(3)
                yield b'never-needed'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.post(PATH, content=chunks(), headers={
                    'Origin': ORIGIN, 'Content-Type': 'application/json'})
                assert response.status_code == 413
            assert consumed == [1, 2] and not case.stages and not case.context._claimed
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('framing', ['length_mismatch', 'content_encoding'])
def test_ambiguous_body_framing_is_rejected_before_staging(tmp_path, target_case, monkeypatch, framing):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            headers = {'Origin': ORIGIN, 'Content-Type': 'application/json'}
            headers.update({'Content-Length': '0'} if framing == 'length_mismatch'
                           else {'Content-Encoding': 'gzip'})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.post(PATH, content=json.dumps(body(case)), headers=headers)
                assert response.status_code in {415, 422}, response.status_code
            assert not case.stages and not case.context._claimed
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_query_parameters_cannot_expand_closed_prepare_contract(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                response = await client.post(PATH + '?owner=other', json=body(case), headers={'Origin': ORIGIN})
                assert response.status_code == 422
            assert not case.stages and not case.context._claimed
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_unavailable_session_storage_and_new_anonymous_session_cannot_prepare(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as anonymous:
                response = await anonymous.post(PATH, json=body(case), headers={'Origin': ORIGIN})
                assert response.status_code == 404
            def unavailable(*args):
                raise sqlite3.OperationalError('synthetic session storage failure')
            with monkeypatch.context() as patch:
                patch.setattr(AgentSessionStore, 'resolve_and_touch', unavailable)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                        cookies=cookies(token)) as client:
                    response = await client.post(PATH, json=body(case), headers={'Origin': ORIGIN})
                    assert response.status_code == 503
                    assert 'synthetic' not in response.text
            assert not case.stages and not case.context._claimed
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_stale_revision_or_refs_cannot_stage_and_do_not_replace_current_root(tmp_path, target_case, monkeypatch):
    api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            app, token, _ = app_for(case, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                for patch in ({'revision': case.root.accepted_revision + 1}, {'receptor_ref': 'sha256:' + '0' * 64},
                              {'ligand_ref': 'sha256:' + '1' * 64}):
                    response = await client.post(PATH, json=dict(body(case), **patch), headers={'Origin': ORIGIN})
                    assert response.status_code == 409
                assert not case.context._claimed and not case.stages
                assert case.coordinator._roots['root-1'] is case.root
                response = await client.post(PATH, json=body(case), headers={'Origin': ORIGIN})
                assert response.status_code == 201
            assert case.stages == [case.root.task_id] and not case.calls
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())
