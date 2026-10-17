"""D4 owned HTTP bytes. Actual C2/SQLite, SYNTHETIC -7.2 pose, NOT Vina."""
import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import threading
import time
from copy import copy
from dataclasses import replace, FrozenInstanceError
from contextlib import asynccontextmanager

import httpx
import pytest

from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case
from tests.agent.test_docking_publication_candidate import settle, publication_api
from tests.agent.test_docking_publication_receipt import publish
from tests.agent.test_docking_publication_receipt import checkpoint
from tests.agent.test_web_docking_http import app_for, cookies, ORIGIN
from tests.agent.test_web_docking_sources import invalidate_source
from tests.agent.test_web_docking_consent import republish
from src.web.agent_session import COOKIE_NAME


@asynccontextmanager
async def client_for(case, tmp_path, *, foreign=False):
    app, token, other = app_for(case, tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url=ORIGIN, cookies=cookies(other if foreign else token)) as client:
        yield client


async def get(client, case, **kwargs):
    return await client.get(path(case), headers={'Origin': ORIGIN}, **kwargs)


def artifacts_api():
    name = 'src.web.docking_artifacts'
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail('D4 owned artifact callable is absent', pytrace=False)


def path(case, artifact='pose'):
    return f'/api/agent/docking/tasks/{case.root.task_id}/artifacts/{artifact}'


def test_owned_artifact_callable():
    assert callable(getattr(artifacts_api(), 'docking_artifact_response', None))


@pytest.mark.parametrize('elapsed', [0, 421])
def test_http_exact_pose_and_non_sliding_postpublication_observation(tmp_path, target_case, monkeypatch, elapsed):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            assert outcome.status == 'SUCCEEDED'
            state = case.root.publication
            app, token, _ = app_for(case, tmp_path)
            case.rig.wall += elapsed * 1000
            case.rig.mono += elapsed
            if elapsed:
                with pytest.raises(publication_api().DockingPublicationUnavailable):
                    await publication_api().capture_docking_publication_candidate(case.coordinator,
                        root_id=case.root.root_id, owner_session_id=case.root.owner)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url=ORIGIN, cookies=cookies(token)) as client:
                for _ in range(2):
                    response = await client.get(path(case), headers={'Origin': ORIGIN})
                    assert response.status_code == 200, response.text
                    assert len(response.content) == outcome.projection.pose_size
                    assert hashlib.sha256(response.content).hexdigest() == outcome.projection.pose_sha256
                    assert response.headers['content-type'] == 'application/octet-stream'
                    assert response.headers['content-disposition'] == 'attachment; filename="pose.pdbqt"'
                    assert response.headers['cache-control'] == 'private, no-store'
                    assert response.headers['x-content-type-options'] == 'nosniff'
                    assert int(response.headers['content-length']) == len(response.content)
                assert await publish(case) is outcome
                assert state.grant is case.root.publication.grant
                assert (await client.get(path(case, 'extra-1'), headers={'Origin': ORIGIN})).status_code == 404
            assert case.raw == [True] and case.rig.gate._readers == 0
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['grant_copy', 'grant_replace', 'state', 'outcome', 'candidate',
    'context', 'root', 'unacknowledged', 'missing_grant', 'restarted', 'latch'])
def test_self_consistent_clones_are_not_grant_authority(tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            state = case.root.publication
            if mutation == 'grant_copy': state.grant = copy(state.grant)
            elif mutation == 'grant_replace': state.grant = replace(state.grant)
            elif mutation == 'state': case.root.publication = copy(state)
            elif mutation == 'outcome': state.outcome = replace(outcome)
            elif mutation == 'candidate': state.candidate = replace(state.candidate)
            elif mutation == 'context':
                cloned = copy(case.context)
                case.root.input_context = state.context = cloned
            elif mutation == 'root':
                cloned = copy(case.root)
                case.coordinator._roots[case.root.root_id] = cloned
                case.context._root = cloned
            elif mutation == 'unacknowledged': state.acknowledged = False
            elif mutation == 'missing_grant': state.grant = None
            elif mutation == 'restarted': case.coordinator._roots.clear()
            elif mutation == 'latch': state._latch = ()
            reads = []
            original = case.runtime._docking_private_output.completions.read_verified_artifact
            def read(*args, **kwargs):
                reads.append(True)
                return original(*args, **kwargs)
            monkeypatch.setattr(case.runtime._docking_private_output.completions, 'read_verified_artifact', read)
            async with client_for(case, tmp_path) as client:
                assert (await get(client, case)).status_code == 404
            assert not reads and outcome.status == 'SUCCEEDED'
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['ready', 'settled', 'candidate', 'foreign'])
def test_no_grant_before_confirmed_publication_and_no_foreign_io(tmp_path, target_case, monkeypatch, stage):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            if stage != 'ready': await settle(case)
            if stage == 'candidate':
                await publication_api().capture_docking_publication_candidate(case.coordinator,
                    root_id=case.root.root_id, owner_session_id=case.root.owner)
            if stage == 'foreign': await publish(case)
            def forbidden(*args, **kwargs):
                pytest.fail('ineligible HTTP request reached physical read')
            monkeypatch.setattr(case.runtime._docking_private_output.completions, 'read_verified_artifact', forbidden)
            async with client_for(case, tmp_path, foreign=stage == 'foreign') as client:
                assert (await get(client, case)).status_code == 404
            assert case.root.publication is None or case.root.publication.outcome.status == 'SUCCEEDED'
    asyncio.run(run())


@pytest.mark.parametrize('clock', ['wall', 'mono', 'rollback', 'runtime_wall', 'runtime_mono'])
def test_grant_equality_expiry_is_fixed_and_sticky(tmp_path, target_case, monkeypatch, clock):
    def fresh_for_three_days(rows):
        rows[0]['target_expires_at'] = '2026-01-04T00:00:00+00:00'
        rows[0]['recommended_structures'][0]['expires_at'] = '2026-01-04T00:00:00+00:00'
    republish(target_case, fresh_for_three_days)
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            # State creation / CAS proposal are earlier than the actual latch.
            original = case.rig.tasks._record_docking_publication
            def record(**kwargs):
                case.rig.wall += 10_000
                case.rig.mono += 10
                return original(**kwargs)
            monkeypatch.setattr(case.rig.tasks, '_record_docking_publication', record)
            outcome = await publish(case)
            grant = case.root.publication.grant
            wall, mono = case.rig.wall, case.rig.mono
            assert grant.clocks == ((wall, mono), (wall, mono))
            with pytest.raises(FrozenInstanceError): grant.owner = 'foreign'
            async with client_for(case, tmp_path) as client:
                case.rig.wall += 86_399_000
                case.rig.mono += 86_399
                assert (await get(client, case)).status_code == 200
                assert case.root.publication.grant is grant
                if clock == 'wall': case.rig.wall = wall + 86_400_000
                elif clock == 'mono': case.rig.mono = mono + 86_400
                elif clock == 'rollback':
                    case.rig.wall = wall - 1000
                    case.rig.mono = mono + 86_400
                elif clock == 'runtime_wall':
                    monkeypatch.setattr(case.runtime, '_consent_wall_time_ms', lambda: wall + 86_400_000)
                elif clock == 'runtime_mono':
                    monkeypatch.setattr(case.runtime, '_consent_monotonic', lambda: mono + 86_400)
                assert (await get(client, case)).status_code == 404
                case.rig.wall, case.rig.mono = wall, mono
                assert (await get(client, case)).status_code == 404
            assert case.root.publication.outcome is outcome and outcome.status == 'SUCCEEDED'
    asyncio.run(run())


def asgi_scope(case, token):
    return dict(type='http', method='GET', scheme='https', path=path(case),
        raw_path=path(case).encode(), query_string=b'', server=('localhost', 443),
        http_version='1.1', client=('127.0.0.1', 1234),
        headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode()),
                 (b'cookie', (COOKIE_NAME + '=' + token).encode())])


async def drain(case):
    await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)


def test_queued_cancel_cannot_start_physical_reader(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            app, token, _ = app_for(case, tmp_path)
            reads, sends = [], []
            original = case.runtime._docking_private_output.completions.read_verified_artifact
            def read(*args, **kwargs):
                reads.append(True)
                return original(*args, **kwargs)
            monkeypatch.setattr(case.runtime._docking_private_output.completions, 'read_verified_artifact', read)
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message): sends.append(message)
            async with case.rig.gate.exclusive():
                call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
                await checkpoint(lambda: bool(case.root.publication.snapshots))
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
                assert case.rig.gate._readers == 0 and not reads
            await drain(case)
            assert not reads and not sends
            assert not case.root.publication.snapshots
            assert case.root.publication.outcome is outcome
    asyncio.run(run())


async def cleanup(case):
    return await asyncio.to_thread(case.stager.cleanup_expired, time.time() + 1000, [],
        status_check=lambda task_id: case.rig.tasks.get(task_id).status)


@pytest.mark.parametrize('ending', ['deadline', 'cancel', 'disconnect'])
def test_actual_send_lease_blocks_cleanup_until_physical_return(tmp_path, target_case, monkeypatch, ending):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            app, token, _ = app_for(case, tmp_path)
            entered, release, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()
            messages = []
            async def send(message):
                messages.append(message)
                if len(messages) == 1:
                    entered.set()
                    while not release.is_set():
                        try: await release.wait()
                        except asyncio.CancelledError: pass
            received = False
            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return dict(type='http.request', body=b'', more_body=False)
                await disconnected.wait()
                return dict(type='http.disconnect')
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                assert messages[0]['status'] == 200
                assert case.rig.gate._readers == 0
                snapshot, = case.root.publication.snapshots
                assert snapshot.lease.acquired and type(snapshot.content) is bytes
                assert await cleanup(case) == []
                assert (case.stager._root / case.root.task_id).is_dir()
                # A model writer is not held behind the slow network send.
                async with case.rig.gate.exclusive(): pass
                if ending == 'cancel': call.cancel()
                elif ending == 'disconnect': disconnected.set()
                done, _ = await asyncio.wait({call}, timeout=6 if ending == 'deadline' else 1)
                assert done, 'logical HTTP must not drain the physical send'
                for task in tuple(case.rig.gate._background):
                    if not task.done(): task.cancel()
                await asyncio.sleep(0)
                assert snapshot.lease.acquired and not snapshot.closed
                assert await cleanup(case) == []
                assert len(messages) == 1
            finally:
                release.set()
                disconnected.set()
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            assert snapshot.closed and not snapshot.lease.acquired
            assert not case.root.publication.snapshots and case.rig.gate._readers == 0
            assert case.root.publication.outcome is outcome
            assert len(messages) == 1, 'closed envelope sent a late body'
            # Same real API CAN delete once physical ownership has ended.
            assert await cleanup(case) == [case.root.task_id]
    asyncio.run(run())


@pytest.mark.parametrize('invalidated', [False, True])
def test_actual_read_keeps_lease_and_reader_under_repeated_cancellation(tmp_path, target_case, monkeypatch, invalidated):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            app, token, _ = app_for(case, tmp_path)
            entered, release = threading.Event(), threading.Event()
            store = case.runtime._docking_private_output.completions
            original = store.read_verified_artifact
            def held(*args, **kwargs):
                entered.set()
                assert release.wait(15)
                return original(*args, **kwargs)
            monkeypatch.setattr(store, 'read_verified_artifact', held)
            sends = []
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message): sends.append(message)
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
            model_generation = case.rig.context['model_generation']
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                snapshot, = case.root.publication.snapshots
                assert snapshot.lease.acquired and case.rig.gate._readers == 1
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
                for _ in range(3):
                    for task in tuple(case.rig.gate._background):
                        if not task.done(): task.cancel()
                    await asyncio.sleep(0)
                assert snapshot.lease.acquired and not snapshot.closed
                assert case.rig.gate._readers == 1
                assert await cleanup(case) == []
                if invalidated: case.rig.context['model_generation'] = 'invalid-during-cancelled-read'
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            case.rig.context['model_generation'] = model_generation
            assert case.root.publication.access_disabled is invalidated
            assert not sends and not case.root.publication.snapshots
            assert snapshot.closed and case.rig.gate._readers == 0
            assert case.root.publication.outcome is outcome
            assert await cleanup(case) == [case.root.task_id]
    asyncio.run(run())


def test_first_publication_never_borrows_grant_clock_at_latch(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            module = publication_api()
            original = module._publication_live
            calls = []
            def late(state):
                value = original(state)
                calls.append(True)
                if len(calls) == 3:
                    limits = case.context.claimed_execution_limits(case.context._execution)
                    case.rig.wall = limits['operation_deadline_ms']
                    case.rig.mono = limits['operation_monotonic_expires']
                return value
            monkeypatch.setattr(module, '_publication_live', late)
            outcome = await publish(case)
            assert len(calls) == 3
            assert outcome.status != 'SUCCEEDED'
            assert case.root.publication.grant is None
    asyncio.run(run())


def test_busy_reader_capacity_does_not_revoke_grant(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            app, token, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message):
                if message['type'] == 'http.response.start':
                    entered.set()
                    await release.wait()
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        base_url=ORIGIN, cookies=cookies(token)) as client:
                    assert (await get(client, case)).status_code == 503
                assert not case.root.publication.access_disabled
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url=ORIGIN, cookies=cookies(token)) as client:
                assert (await get(client, case)).status_code == 200
    asyncio.run(run())


def test_external_task_lease_contention_is_not_sticky(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            lease = case.stager._task_lease(case.root.task_id, timeout_seconds=0)
            await asyncio.to_thread(lease.__enter__)
            try:
                async with client_for(case, tmp_path) as client:
                    assert (await get(client, case)).status_code == 503
                assert not case.root.publication.access_disabled
            finally:
                await asyncio.to_thread(lease.__exit__, None, None, None)
            assert not case.root.publication.snapshots
    asyncio.run(run())


@pytest.mark.parametrize('database', ['task', 'agent'])
@pytest.mark.parametrize('observer', ['get', 'confirmed'])
def test_sqlite_writer_contention_preserves_published_grant(tmp_path, target_case, monkeypatch, database, observer):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            state = case.root.publication
            grant = state.grant
            before = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            async with client_for(case, tmp_path) as client:
                assert (await get(client, case)).status_code == 200
            app, token, _ = app_for(case, tmp_path)
            module = artifacts_api() if observer == 'get' else publication_api()
            original = module._capture_guarded
            entered, errors, messages = threading.Event(), [], []
            def capture(*args, **kwargs):
                entered.set()
                try:
                    return original(*args, **kwargs)
                except Exception as exc:
                    errors.append(exc)
                    raise
            monkeypatch.setattr(module, '_capture_guarded', capture)
            blocker = (sqlite3.connect(case.rig.tasks.db_path, timeout=0)
                       if database == 'task' else case.rig.state._connect())
            blocker.execute('BEGIN IMMEDIATE')  # No data changes in either DB.
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message): messages.append(message)
            started = time.monotonic()
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send)
                if observer == 'get' else publish(case))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                await checkpoint(lambda: case.rig.gate._readers == 1)
                if observer == 'get':
                    snapshot, = state.snapshots
                    assert snapshot.lease.acquired and not snapshot.closed
                    assert case.coordinator._http_control_tasks
                result, = await asyncio.wait_for(asyncio.gather(call, return_exceptions=True), 6.5)
                elapsed = time.monotonic() - started
                # Retain the real writer through physical read failure, even
                # when the original HTTP waiter has already hit its deadline.
                await asyncio.wait_for(drain(case), 3)
            finally:
                blocker.rollback()
                blocker.close()
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            assert len(errors) == 1 and type(errors[0]) is sqlite3.OperationalError
            assert str(errors[0]) == 'database is locked'
            assert elapsed < (5.8 if observer == 'get' else 6.5)
            if observer == 'confirmed':
                assert result is errors[0]  # Unavailable, never stale success.
            else:
                assert result is None
                assert not any(m.get('status') == 200 for m in messages)
                assert snapshot.closed and not snapshot.lease.acquired
            assert state.outcome is outcome and state.grant is grant
            assert not state.snapshots and case.rig.gate._readers == 0
            assert not case.coordinator._http_control_tasks
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id) == before
            assert not state.access_disabled, 'Transient SQLite contention permanently revoked the grant'
            assert await publish(case) is outcome
            async with client_for(case, tmp_path) as client:
                response = await get(client, case)
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == outcome.projection.pose_sha256
            assert state.grant is grant and case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('code,message,transient', [
    (5, 'busy', True), (6, 'locked', True), (261, 'recovery', True),
    (517, 'snapshot', True), (262, 'shared cache', True),
    (1, 'database is locked', False), (11, 'database is locked', False),
    (False, 'database is locked', False),
    (None, 'database is locked', True), (None, 'database table is locked', True),
    (None, 'database schema is locked', True),
    (None, 'no such table: database is locked', False),
    (None, 'database disk image is malformed', False), (None, 'database is busy', False),
])
def test_sqlite_contention_classification_is_exact(code, message, transient):
    error = sqlite3.OperationalError(message)
    if code is not None:
        error.sqlite_errorcode = code
    assert publication_api()._sqlite_contention(error) is transient
    assert not publication_api()._sqlite_contention(RuntimeError(message))
    assert not publication_api()._sqlite_contention(sqlite3.DatabaseError(message))


@pytest.mark.parametrize('observer', ['get', 'confirmed'])
@pytest.mark.parametrize('message', ['database is locked', 'database table is locked'])
def test_immediate_sqlite_contention_is_unavailable_not_sticky(tmp_path, target_case, monkeypatch, observer, message):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            module = artifacts_api() if observer == 'get' else publication_api()
            calls = []
            def fail(*args, **kwargs):
                calls.append(True)
                raise sqlite3.OperationalError(message)
            with monkeypatch.context() as patch:
                patch.setattr(module, '_capture_guarded', fail)
                if observer == 'get':
                    async with client_for(case, tmp_path) as client:
                        response = await get(client, case)
                        assert response.status_code == 503
                        assert message not in response.text
                else:
                    with pytest.raises(sqlite3.OperationalError, match=message):
                        await publish(case)
            await drain(case)
            assert calls == [True]  # No automatic retry/new read budget.
            assert not case.root.publication.access_disabled
            assert not case.root.publication.snapshots and case.rig.gate._readers == 0
            assert await publish(case) is outcome
            async with client_for(case, tmp_path) as client:
                assert (await get(client, case)).status_code == 200
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('observer', ['get', 'confirmed'])
@pytest.mark.parametrize('message', ['no such table: docking_consents', 'database disk image is malformed'])
def test_nonbusy_sqlite_failure_still_revokes_grant(tmp_path, target_case, monkeypatch, observer, message):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            module = artifacts_api() if observer == 'get' else publication_api()
            def fail(*args, **kwargs):
                raise sqlite3.OperationalError(message)
            with monkeypatch.context() as patch:
                patch.setattr(module, '_capture_guarded', fail)
                if observer == 'get':
                    async with client_for(case, tmp_path) as client:
                        assert (await get(client, case)).status_code == 404
                else:
                    with pytest.raises(sqlite3.OperationalError, match=message):
                        await publish(case)
            await drain(case)
            assert case.root.publication.access_disabled
            assert case.root.publication.outcome is outcome
            assert not case.root.publication.snapshots and case.rig.gate._readers == 0
            with pytest.raises(publication_api().DockingPublicationUnavailable):
                await publish(case)
            async with client_for(case, tmp_path) as client:
                assert (await get(client, case)).status_code == 404
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'root', 'model', 'runtime', 'runtime_replaced', 'policy', 'token',
    'generation', 'invalidation', 'pose', 'input', 'completion'])
def test_live_invalidation_is_sticky_without_rewriting_success(tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            store = case.runtime._docking_private_output.completions
            manifest_path = store.manifest_path(case.root.task_id)
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
            with monkeypatch.context() as patch:
                if mutation in {'source', 'root', 'model'}: invalidate_source(case.rig, mutation)
                elif mutation == 'runtime': patch.setattr(case.runtime, '_closed', True)
                elif mutation == 'runtime_replaced': patch.setattr(case.context, '_runtime', copy(case.runtime))
                elif mutation == 'policy': patch.setattr(case.runtime, '_docking_consent_policy', replace(case.rig.policy, policy_generation='changed'))
                elif mutation in {'token', 'generation'}:
                    column = 'execution_token' if mutation == 'token' else 'runtime_generation'
                    with sqlite3.connect(case.rig.tasks.db_path) as db:
                        db.execute(f"UPDATE docking_consents SET {column}='changed'")
                elif mutation == 'invalidation': (manifest_path.parent / 'completion_invalid.marker').write_bytes(b'invalid')
                elif mutation == 'pose': (manifest_path.parent / payload['pose']['path']).write_bytes(b'tampered')
                elif mutation == 'input':
                    staged = json.loads((manifest_path.parent / 'input_manifest.json').read_text(encoding='utf-8'))
                    (manifest_path.parent / staged['ligand']['path']).write_bytes(b'tampered')
                elif mutation == 'completion': manifest_path.write_text('{}', encoding='utf-8')
                async with client_for(case, tmp_path) as client:
                    assert (await get(client, case)).status_code == 404
            assert case.root.publication.access_disabled
            assert case.root.publication.outcome is outcome and outcome.status == 'SUCCEEDED'
            with pytest.raises(publication_api().DockingPublicationUnavailable): await publish(case)
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['range', 'query', 'blank_query', 'body', 'encoding', 'origin'])
def test_bad_http_shape_rejected_before_physical_read(tmp_path, target_case, monkeypatch, kind):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            def forbidden(*args, **kwargs): pytest.fail('rejected request read a pose')
            monkeypatch.setattr(case.runtime._docking_private_output.completions, 'read_verified_artifact', forbidden)
            headers, url, content = {'Origin': ORIGIN}, path(case), None
            if kind == 'range': headers['Range'] = 'bytes=0-3'
            elif kind == 'query': url += '?artifact=pose'
            elif kind == 'blank_query': url += '?&&'
            elif kind == 'body': content = b'not-empty'
            elif kind == 'encoding': headers['Content-Encoding'] = 'gzip'
            elif kind == 'origin': headers['Origin'] = 'https://foreign.invalid'
            async with client_for(case, tmp_path) as client:
                response = await client.request('GET', url, headers=headers, content=content)
                assert response.status_code == {'range': 416, 'query': 422, 'blank_query': 422, 'body': 422,
                    'encoding': 415, 'origin': 403}[kind]
            assert not case.root.publication.snapshots
            assert not case.root.publication.access_disabled
    asyncio.run(run())


@pytest.mark.parametrize('timing', ['before_read', 'during_read', 'after_verified_bytes'])
def test_pose_tamper_at_read_boundaries_denies_bytes(tmp_path, target_case, monkeypatch, timing):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            store = case.runtime._docking_private_output.completions
            manifest_path = store.manifest_path(case.root.task_id)
            record = json.loads(manifest_path.read_text(encoding='utf-8'))['pose']
            pose = manifest_path.parent / record['path']
            if timing == 'before_read':
                cls = artifacts_api()._OwnedSnapshot
                original = cls.acquire
                def acquire(self):
                    original(self)
                    pose.write_bytes(b'changed between precheck and physical read')
                monkeypatch.setattr(cls, 'acquire', acquire)
            else:
                original = store.read_verified_artifact
                def read(*args, **kwargs):
                    if timing == 'during_read': pose.write_bytes(b'changed before secure byte read')
                    result = original(*args, **kwargs)
                    if timing == 'after_verified_bytes': pose.write_bytes(b'changed after verified byte read')
                    return result
                monkeypatch.setattr(store, 'read_verified_artifact', read)
            async with client_for(case, tmp_path) as client:
                response = await get(client, case)
                assert response.status_code == 404
                assert b'MODEL 1' not in response.content
            assert case.root.publication.access_disabled
            assert case.root.publication.outcome is outcome and case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['hardlink', 'symlink'])
def test_linked_pose_cannot_be_served(tmp_path, target_case, monkeypatch, kind):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            manifest = case.runtime._docking_private_output.completions.manifest_path(case.root.task_id)
            record = json.loads(manifest.read_text(encoding='utf-8'))['pose']
            pose = manifest.parent / record['path']
            alias = tmp_path / 'pose-link-target.pdbqt'
            if kind == 'hardlink':
                try: os.link(pose, alias)
                except OSError as exc: pytest.skip(f'Platform hardlink unavailable: {exc.__class__.__name__}')
            else:
                alias.write_bytes(pose.read_bytes())
                probe = tmp_path / 'symlink-probe'
                try: probe.symlink_to(alias)
                except OSError as exc: pytest.skip(f'Platform symlink unavailable: {exc.__class__.__name__}')
                probe.unlink()
                pose.unlink()
                pose.symlink_to(alias)
            async with client_for(case, tmp_path) as client:
                assert (await get(client, case)).status_code == 404
            assert case.root.publication.access_disabled
    asyncio.run(run())


def test_release_failure_retains_unresolved_snapshot(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            app, token, _ = app_for(case, tmp_path)
            captured = []
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message):
                if not captured:
                    snapshot, = case.root.publication.snapshots
                    captured.append((snapshot, snapshot.lease.__exit__))
                    def fail(*args): raise OSError('synthetic release failure')
                    monkeypatch.setattr(snapshot.lease, '__exit__', fail)
            try:
                await app(asgi_scope(case, token), receive, send)
                snapshot, original_exit = captured[0]
                assert snapshot.unresolved and not snapshot.closed
                assert snapshot in case.root.publication.snapshots and snapshot.lease.acquired
                assert case.root.publication.access_disabled
                assert case.root.publication.outcome is outcome
                assert await cleanup(case) == []
            finally:
                if captured:
                    snapshot, original_exit = captured[0]
                    monkeypatch.setattr(snapshot.lease, '__exit__', original_exit)
                    await case.coordinator._sql(snapshot.release)
                    case.root.publication.snapshots.discard(snapshot)
                await drain(case)
    asyncio.run(run())


def test_auth_queue_read_and_send_share_original_five_second_envelope(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            app, token, _ = app_for(case, tmp_path)
            sessions = app.state.agent_session_store
            resolve = sessions.resolve_and_touch
            def auth(value):
                time.sleep(1.3)
                return resolve(value)
            monkeypatch.setattr(sessions, 'resolve_and_touch', auth)
            entered, release = threading.Event(), threading.Event()
            store = case.runtime._docking_private_output.completions
            original = store.read_verified_artifact
            def read(*args, **kwargs):
                entered.set()
                assert release.wait(15)
                return original(*args, **kwargs)
            monkeypatch.setattr(store, 'read_verified_artifact', read)
            messages = []
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message): messages.append(message)
            started = time.monotonic()
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                done, _ = await asyncio.wait({call}, timeout=max(0, 5.7 - (time.monotonic() - started)))
                assert done, 'read restarted the deadline after session authentication'
                call.result()
                assert time.monotonic() - started < 5.8
                snapshot, = case.root.publication.snapshots
                assert snapshot.lease.acquired and case.rig.gate._readers == 1
                assert not messages
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            assert not messages and not case.root.publication.snapshots
            assert case.rig.gate._readers == 0
    asyncio.run(run())


def test_response_revalidates_after_body_wait_and_latches_invalid_access(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            module = artifacts_api()
            original = module.docking_artifact_response
            constructed, release = asyncio.Event(), asyncio.Event()
            def response(*args, **kwargs):
                value = original(*args, **kwargs)
                constructed.set()
                return value
            monkeypatch.setattr(module, 'docking_artifact_response', response)
            app, token, _ = app_for(case, tmp_path)
            async def receive():
                await release.wait()
                return dict(type='http.request', body=b'', more_body=False)
            sends = []
            async def send(message): sends.append(message)
            model = case.root.current['model']
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
            try:
                await asyncio.wait_for(constructed.wait(), 2)
                assert not case.root.publication.snapshots
                case.root.current['model'] = object()
                release.set()
                await call
                assert case.root.publication.access_disabled
                assert sends[0]['status'] == 404
            finally:
                release.set()
                case.root.current['model'] = model
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            assert not case.root.publication.snapshots
            assert case.root.publication.outcome is outcome
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['acquire', 'release'])
def test_physical_lease_operations_drain_before_owner_release(tmp_path, target_case, monkeypatch, phase):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            cls = artifacts_api()._OwnedSnapshot
            original = getattr(cls, phase)
            entered, release = threading.Event(), threading.Event()
            def held(self):
                if phase == 'acquire': original(self)
                entered.set()
                assert release.wait(15)
                if phase == 'release': original(self)
            monkeypatch.setattr(cls, phase, held)
            app, token, _ = app_for(case, tmp_path)
            sends = []
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message): sends.append(message)
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                snapshot, = case.root.publication.snapshots
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
                for _ in range(3):
                    for task in tuple(case.rig.gate._background):
                        if not task.done(): task.cancel()
                    await asyncio.sleep(0)
                assert snapshot.lease.acquired and not snapshot.closed
                assert case.rig.gate._readers == (1 if phase == 'acquire' else 0)
                assert await cleanup(case) == []
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            assert snapshot.closed and not snapshot.lease.acquired
            assert not case.root.publication.snapshots
            assert case.root.publication.outcome is outcome
            assert case.rig.gate._readers == 0
            assert len(sends) == (0 if phase == 'acquire' else 2)
    asyncio.run(run())


def test_pending_publication_get_denies_without_poisoning_future_grant(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            entered, release = threading.Event(), threading.Event()
            original = case.rig.tasks._record_docking_publication
            def held(**kwargs):
                entered.set()
                assert release.wait(15)
                return original(**kwargs)
            monkeypatch.setattr(case.rig.tasks, '_record_docking_publication', held)
            publishing = asyncio.create_task(publish(case))
            app, token, _ = app_for(case, tmp_path)
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        base_url=ORIGIN, cookies=cookies(token)) as client:
                    assert (await get(client, case)).status_code == 404
                assert case.root.publication.grant is None
                assert not case.root.publication.access_disabled
            finally:
                release.set()
                await asyncio.gather(publishing, return_exceptions=True)
                await drain(case)
            assert publishing.result().status == 'SUCCEEDED'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url=ORIGIN, cookies=cookies(token)) as client:
                assert (await get(client, case)).status_code == 200
    asyncio.run(run())


def test_cancelled_waiter_cannot_hide_final_loop_model_invalidation(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            outcome = await publish(case)
            cls = artifacts_api()._OwnedSnapshot
            original = cls.capture
            entered, release = threading.Event(), threading.Event()
            loop = asyncio.get_running_loop()
            model_generation = case.rig.context['model_generation']
            def capture(self):
                content = original(self)
                entered.set()
                assert release.wait(15)
                loop.call_soon_threadsafe(case.rig.context.__setitem__, 'model_generation', 'final-loop-change')
                return content
            monkeypatch.setattr(cls, 'capture', capture)
            app, token, _ = app_for(case, tmp_path)
            sends = []
            async def receive(): return dict(type='http.request', body=b'', more_body=False)
            async def send(message): sends.append(message)
            call = asyncio.create_task(app(asgi_scope(case, token), receive, send))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await drain(case)
            case.rig.context['model_generation'] = model_generation
            assert not sends and not case.root.publication.snapshots
            assert case.root.publication.access_disabled
            assert case.root.publication.outcome is outcome
    asyncio.run(run())


def test_missing_malformed_and_extra_ids_do_not_reach_files(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            def forbidden(*args, **kwargs): pytest.fail('unsupported identifier reached a physical file')
            monkeypatch.setattr(case.runtime._docking_private_output.stager, '_task_lease', forbidden)
            urls = [path(case, value) for value in ('extra-00', 'POSE', 'pose.pdbqt', 'a' * 200)]
            urls += [f'/api/agent/docking/tasks/{value}/artifacts/pose'
                     for value in ('missing-task', 'a' * 200, '%2Fhidden', '%00')]
            async with client_for(case, tmp_path) as client:
                for url in urls:
                    response = await client.get(url, headers={'Origin': ORIGIN})
                    assert response.status_code == 404
            assert not case.root.publication.access_disabled
    asyncio.run(run())


def test_committed_candidate_without_ack_cannot_mint_or_recover_download(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            original = case.rig.tasks._record_docking_publication
            def lost_ack(**kwargs):
                original(**kwargs)
                raise OSError('synthetic lost CAS acknowledgement')
            monkeypatch.setattr(case.rig.tasks, '_record_docking_publication', lost_ack)
            assert (await publish(case)).status != 'SUCCEEDED'
            assert case.root.publication.grant is None
            with sqlite3.connect(case.rig.tasks.db_path) as db:
                assert db.execute('SELECT count(*) FROM docking_publications').fetchone() == (1,)
            async with client_for(case, tmp_path) as client:
                assert (await get(client, case)).status_code == 404
            assert case.raw == [True]
    asyncio.run(run())
