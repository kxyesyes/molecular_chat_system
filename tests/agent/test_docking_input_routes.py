"""D6 isolated HTTP input controls; synthetic source children, no C1/Vina/network."""
import asyncio
from contextlib import asynccontextmanager
from copy import copy, deepcopy
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.docking import chat_preparation, source_preparation
from src.task_runtime.backends.local import LocalTaskBackend
from src.task_runtime.config import TaskRuntimeConfig
from src.task_runtime.database import connection
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.staging import DockingInputStager
from src.web.agent_session import COOKIE_NAME
from src.web.routes import docking_consent_routes as routes
from tests.agent.test_web_docking_consent import target_case
from tests.agent.test_web_docking_http import app_for, cookies, ORIGIN
from tests.agent.test_web_docking_selection import _complete_source_child, choices
from tests.agent.test_web_docking_sources import source_rig


CONTROLS = ('source-page', 'selection', 'parameters')
RECEIPT = {'task_id', 'preparation_id', 'phase', 'reason_code', 'cleanup_pending',
           'input_ready', 'execution_authorized'}


@asynccontextmanager
async def input_case(tmp_path, target_case, monkeypatch, *, selected=False):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async with rig.client() as model_client:
        coordinator = rig.coordinator(model_client)
        initial = await rig.begin(coordinator, structure_context=pointer)
        assert initial['reason_code'] == 'scientific_selection_required', initial
        root = coordinator._roots['root-1']
        calls = []

        def forbidden(*args, **kwargs):
            calls.append('staging/execution')
            raise AssertionError('input controls cannot stage or execute')

        async def forbidden_backend(*args, **kwargs):
            forbidden()

        local = LocalTaskBackend(rig.tasks, {'docking': forbidden_backend})
        for name in ('submit', 'get', 'list', 'cancel', 'health'):
            monkeypatch.setattr(local, name, forbidden_backend)
        stager = DockingInputStager(tmp_path / 'stage')
        monkeypatch.setattr(stager, 'stage', forbidden)
        config = TaskRuntimeConfig(backend='local', canary_percent=0,
            temporal_address='unused.invalid:7233', temporal_namespace='test',
            docking_queue='test', docking_concurrency=1, staging_root=tmp_path / 'stage')
        runtime = TaskRuntime(config=config, store=rig.tasks, stager=stager,
            local_backend=local, docking_consent_policy=rig.policy)
        case = SimpleNamespace(rig=rig, coordinator=coordinator, root=root, runtime=runtime,
            source_sha256=initial['source_preview']['source_sha256'], calls=calls)
        if selected:
            result = await coordinator.select_source(root_id=root.root_id, owner_session_id=root.owner,
                source_sha256=case.source_sha256, selection=choices())
            assert result['reason_code'] == 'parameters_confirmation_required', result
        app, token, foreign = app_for(case, tmp_path)
        case.app, case.token, case.foreign = app, token, foreign
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                case.client = client
                yield case
        finally:
            await coordinator.close_root(root_id=root.root_id, owner_session_id=root.owner)
            await asyncio.gather(*tuple(rig.gate._background), return_exceptions=True)
            await runtime.close()
        assert not calls and root.input_context is None and not root.staging_started
        with connection(rig.tasks.db_path) as db:
            assert db.execute('SELECT COUNT(*) AS n FROM tasks').fetchone()['n'] == 0


def path(case, control):
    return '/api/agent/docking/tasks/' + case.root.task_id + '/' + control


def body(case, control):
    value = dict(revision=case.root.accepted_revision)
    if control == 'parameters':
        preview = json.loads(case.root.prepared_preview_json)
        value.update(receptor_ref=preview['receptor_ref'], ligand_ref=preview['ligand_ref'],
                     parameters=preview['parameters_suggestion'])
    else:
        value['source_sha256'] = case.source_sha256
        value.update(dict(section='residues', offset=0, limit=2) if control == 'source-page'
                     else dict(selection=choices()))
    return value


async def post(case, control, value=None, **kwargs):
    return await case.client.post(path(case, control), json=body(case, control) if value is None else value,
                                  headers={'Origin': ORIGIN}, **kwargs)


def receipt(response, *, pending=False):
    value = response.json()
    assert set(value) == RECEIPT, value
    assert value['input_ready'] is value['execution_authorized'] is False
    assert value['cleanup_pending'] is pending
    assert response.headers['cache-control'] == 'private, no-store'


def test_actual_source_selection_parameters_and_current_revision_duplicates(tmp_path, target_case, monkeypatch):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch) as case:
            expected = await case.coordinator.source_page(root_id=case.root.root_id,
                owner_session_id=case.root.owner, **{k: v for k, v in body(case, 'source-page').items()
                                                   if k != 'revision'})
            page = await post(case, 'source-page')
            assert page.status_code == 200, page.text
            assert page.json() == expected and expected['ok']
            for control in ('selection', 'parameters'):
                old = body(case, control)
                accepted = await post(case, control, old)
                assert accepted.status_code == 200, accepted.text
                receipt(accepted)
                assert case.root.accepted_revision == old['revision'] + 1
                duplicate = await post(case, control)
                assert duplicate.status_code == 200, duplicate.text
                receipt(duplicate)
                assert (await post(case, control, old)).status_code == 409
            assert case.root.reason == 'inputs_confirmed_staging_required'
            assert len(case.root.preparation_owners) == 4  # source, inspection, projection, ligand
    asyncio.run(run())


def test_authentication_concrete_services_and_disabled_registration(tmp_path, target_case, monkeypatch):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch, selected=True) as case:
            for control in CONTROLS:
                for headers in ({}, {'Origin': 'null'}, {'Origin': 'https://foreign.invalid'},
                                [('Origin', ORIGIN), ('Origin', ORIGIN)]):
                    response = await case.client.post(path(case, control), json=body(case, control), headers=headers)
                    assert response.status_code == 403
                foreign = await case.client.post(path(case, control), json=body(case, control),
                    headers={'Origin': ORIGIN, 'Cookie': COOKIE_NAME + '=' + case.foreign})
                missing = await case.client.post('/api/agent/docking/tasks/missing/' + control,
                    json=body(case, control), headers={'Origin': ORIGIN})
                assert foreign.status_code == missing.status_code == 404
                assert foreign.json() == missing.json()
                assert (await case.client.get(path(case, control))).status_code == 405
            for enabled in (False, None, 1):
                app = FastAPI()
                routes.setup_docking_consent_routes(app, coordinator=case.coordinator,
                    task_runtime=case.runtime, enabled=enabled)
                assert not app.user_middleware
                assert not any('/api/agent/docking/' in route.path for route in app.routes)
            app, _, _ = app_for(case, tmp_path, middleware=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                for control in CONTROLS:
                    assert (await client.post(path(case, control), json=body(case, control),
                                             headers={'Origin': ORIGIN})).status_code == 401
            real_runtime = case.runtime
            case.runtime = SimpleNamespace(store=case.rig.tasks)
            app, token, _ = app_for(case, tmp_path)
            case.runtime = real_runtime
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                    cookies=cookies(token)) as client:
                assert (await client.post(path(case, 'selection'), json=body(case, 'selection'),
                                         headers={'Origin': ORIGIN})).status_code == 503
    asyncio.run(run())


def test_closed_native_schema_bounds_and_no_invalid_dispatch(tmp_path, target_case, monkeypatch):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch, selected=True) as case:
            dispatched = []
            async def forbidden(**kwargs):
                dispatched.append(kwargs)
                raise AssertionError('invalid body dispatched')
            for method in ('source_page', 'select_source', 'confirm_parameters'):
                monkeypatch.setattr(case.coordinator, method, forbidden)
            for control in CONTROLS:
                valid = body(case, control)
                invalid = [{}, [], dict(valid, policy='local'), dict(valid, root='root-1')]
                invalid += [dict(valid, revision=v) for v in (True, 1.0, -1, 2**53, '0', None)]
                if control != 'parameters':
                    invalid += [dict(valid, source_sha256=v) for v in ('A'*64, 'a'*63, 'a'*64+'\n', 3)]
                if control == 'source-page':
                    invalid += [dict(valid, **{key: v}) for key, values in (
                        ('section', ['unknown', [], None]), ('offset', [-1, 100000, True, 1.0]),
                        ('limit', [0, 33, True, 2.0])) for v in values]
                elif control == 'selection':
                    invalid += [dict(valid, selection=v) for v in (None, [], {},
                        dict(choices(), files=[]), dict(choices(), reviewed_limitations=[True]))]
                    nested = choices()
                    nested['protein_selection']['policy'] = 'automatic'
                    invalid.append(dict(valid, selection=nested))
                else:
                    invalid += [dict(valid, receptor_ref=v) for v in ('sha256:'+'A'*64, '../x', None)]
                    invalid += [dict(valid, parameters=v) for v in ({}, [],
                        dict(valid['parameters'], file='x'), dict(valid['parameters'], center=[True, 0, 0]),
                        dict(valid['parameters'], size=[0, 1, 1]), dict(valid['parameters'], num_modes=1.0),
                        dict(valid['parameters'], exhaustiveness=65))]
                raws = [json.dumps(v) for v in invalid] + ['{', 'NaN',
                    json.dumps(valid)[:-1] + ',"revision":0}', '{"revision":'+'['*1100+'0'+']'*1100+'}']
                for raw in raws:
                    response = await case.client.post(path(case, control), content=raw,
                        headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
                    assert response.status_code == 422, (control, raw[:150], response.status_code)
                assert (await post(case, control, dict(valid, revision=2**53-1))).status_code == 409
                if control != 'parameters':
                    assert (await post(case, control, dict(valid, source_sha256='0'*64))).status_code == 409
                else:
                    assert (await post(case, control, dict(valid, receptor_ref='sha256:'+'0'*64))).status_code == 409
                for suffix, extra, status in [('?owner=x', {}, 422), ('', {'Content-Encoding': 'gzip'}, 415),
                                             ('', {'Content-Type': 'text/plain'}, 415)]:
                    response = await case.client.post(path(case, control)+suffix, content=json.dumps(valid),
                        headers={'Origin': ORIGIN, 'Content-Type': 'application/json', **extra})
                    assert response.status_code == status
            assert not dispatched
    asyncio.run(run())


def test_selection_only_has_64k_body_budget_and_streaming_stops_at_limit(tmp_path, target_case, monkeypatch):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch, selected=True) as case:
            # Whitespace counts too; valid >4 KiB selection preserves idempotence.
            raw = json.dumps(body(case, 'selection')).encode()
            response = await case.client.post(path(case, 'selection'), content=raw+b' '*(65536-len(raw)),
                headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
            assert response.status_code == 200, response.text
            for url, limit in [(path(case, 'selection'), 65536), (path(case, 'source-page'), 4096),
                    (path(case, 'parameters'), 4096), ('/api/agent/docking/preparations', 4096),
                    ('/api/agent/docking/preparations/' + case.root.preparation_id + '/approve', 4096)]:
                response = await case.client.post(url, content=b' '*(limit+1),
                    headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
                assert response.status_code == 413
                consumed = []
                async def chunks():
                    consumed.append(1)
                    yield b' '*(limit+1)
                    consumed.append(2)
                    yield b'never read'
                response = await case.client.post(url, content=chunks(),
                    headers={'Origin': ORIGIN, 'Content-Type': 'application/json'})
                assert response.status_code == 413 and consumed == [1]
    asyncio.run(run())


@pytest.mark.parametrize('control', ['selection', 'parameters'])
@pytest.mark.parametrize('disconnect', [False, True])
def test_admitted_work_keeps_physical_owner_and_rejects_busy_requests(
        tmp_path, target_case, monkeypatch, control, disconnect):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch, selected=control=='parameters') as case:
            entered, release = asyncio.Event(), asyncio.Event()
            method = '_run_selection' if control == 'selection' else '_run_confirmation'
            actual = getattr(case.coordinator, method)
            async def held(*args):
                entered.set()
                await release.wait()
                return await actual(*args)
            monkeypatch.setattr(case.coordinator, method, held)
            value = body(case, control)
            call = asyncio.create_task(post(case, control, value))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                physical = case.root.task
                changed = deepcopy(value)
                if control == 'selection':
                    changed['selection']['hetero_choices'][0]['action'] = 'retain'
                else:
                    changed['parameters']['num_modes'] += 1
                busy = await post(case, control, changed)
                assert busy.status_code in {409, 503}, busy.text
                assert (await post(case, control, value)).status_code in {409, 503}
                if control == 'parameters':
                    assert (await post(case, 'selection')).status_code in {409, 503}
                if disconnect:
                    call.cancel()
                    await asyncio.gather(call, return_exceptions=True)
                    assert call.cancelled()
                else:
                    response = await asyncio.wait_for(call, 6)
                    assert response.status_code == 202, response.text
                    receipt(response, pending=True)
                assert not physical.done() and physical in case.rig.gate._background
                assert case.coordinator._roots[case.root.root_id] is case.root and not case.root.closed
                assert case.root.task is physical
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert case.root.accepted_revision == value['revision'] + 1
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['late', 'revision', 'replacement', 'owner'])
def test_body_is_parse_only_and_rechecks_captured_identity(tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = asyncio.Event(), asyncio.Event()
            value = json.dumps(body(case, 'selection')).encode()
            dispatched = []
            actual = case.coordinator.select_source
            async def observed(**kwargs):
                dispatched.append(kwargs)
                return await actual(**kwargs)
            monkeypatch.setattr(case.coordinator, 'select_source', observed)
            async def chunks():
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
                yield value
            call = asyncio.create_task(case.client.post(path(case, 'selection'), content=chunks(),
                headers={'Origin': ORIGIN, 'Content-Type': 'application/json'}))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                # Wait until the actual route captured the root and awaits its body.
                for _ in range(200):
                    if any(task.get_coro().__name__ == '_input_body'
                           for task in case.coordinator._http_control_tasks.values()):
                        break
                    await asyncio.sleep(.005)
                assert any(task.get_coro().__name__ == '_input_body'
                           for task in case.coordinator._http_control_tasks.values())
                if mutation == 'late':
                    response = await asyncio.wait_for(call, 6)
                    assert response.status_code in {408, 503}
                else:
                    if mutation == 'revision': case.root.accepted_revision += 1
                    elif mutation == 'replacement': case.coordinator._roots[case.root.root_id] = copy(case.root)
                    else: case.root.owner = 'foreign'
                    release.set()
                    assert (await call).status_code in {404, 409}
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                case.coordinator._roots[case.root.root_id] = case.root
                case.root.owner = 'owner-session'
            assert not dispatched and case.root.selection_key is None
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['deadline', 'revision', 'replacement', 'capacity'])
def test_queued_operation_rechecks_before_dispatch_and_cannot_borrow_pending_receipt(
        tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = asyncio.Event(), asyncio.Event()
            actual_retain = routes._ControlTransport.retain
            dispatched = []
            async def forbidden(**kwargs):
                dispatched.append(kwargs)
                raise AssertionError('queued stale request dispatched')
            monkeypatch.setattr(case.coordinator, 'select_source', forbidden)
            def retain(transport, key, operation):
                if isinstance(key, tuple) and key[0] == 'input':
                    if mutation == 'capacity':
                        from fastapi import HTTPException
                        raise HTTPException(503, 'Control capacity unavailable')
                    async def queued():
                        entered.set()
                        await release.wait()
                        return await operation()
                    return actual_retain(transport, key, queued)
                return actual_retain(transport, key, operation)
            monkeypatch.setattr(routes._ControlTransport, 'retain', retain)
            call = asyncio.create_task(post(case, 'selection'))
            try:
                if mutation != 'capacity':
                    await asyncio.wait_for(entered.wait(), 3)
                if mutation == 'revision':
                    case.root.accepted_revision += 1
                    release.set()
                elif mutation == 'replacement':
                    case.coordinator._roots[case.root.root_id] = copy(case.root)
                    release.set()
                response = await asyncio.wait_for(call, 6)
                assert response.status_code == (409 if mutation in {'revision', 'replacement'} else 503)
                assert 'cleanup_pending' not in response.json()
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                case.coordinator._roots[case.root.root_id] = case.root
            assert not dispatched and case.root.selection_key is None
    asyncio.run(run())


def test_selection_timeout_keeps_actual_child_close_owner(tmp_path, target_case, monkeypatch):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch) as case:
            entered, release, finished = (threading.Event() for _ in range(3))
            original = chat_preparation.IsolatedProcess
            class HeldClose(original):
                def close(self):
                    super().close()
                    entered.set()
                    try:
                        assert release.wait(20), 'synthetic child close barrier unreleased'
                    finally:
                        finished.set()
            monkeypatch.setattr(chat_preparation, 'IsolatedProcess', HeldClose)
            call = asyncio.create_task(post(case, 'selection'))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                response = await asyncio.wait_for(call, 6)
                assert response.status_code == 202, response.text
                receipt(response, pending=True)
                assert not finished.is_set() and not case.root.closed
                assert not case.root.task.done() and case.root.task in case.rig.gate._background
                assert any(not owner.snapshot()['settled'] for owner in case.root.preparation_owners)
                assert case.coordinator._roots[case.root.root_id] is case.root
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert finished.is_set()
            assert all(owner.snapshot()['settled'] for owner in case.root.preparation_owners)
            assert case.root.reason == 'parameters_confirmation_required'
    asyncio.run(run())


def test_physical_http_disconnect_cannot_cancel_admitted_selection(tmp_path, target_case, monkeypatch):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = asyncio.Event(), asyncio.Event()
            actual = case.coordinator._run_selection
            async def held(root):
                entered.set()
                await release.wait()
                return await actual(root)
            monkeypatch.setattr(case.coordinator, '_run_selection', held)
            messages, sent = asyncio.Queue(), []
            messages.put_nowait(dict(type='http.request', body=json.dumps(body(case, 'selection')).encode(),
                                     more_body=False))
            url = path(case, 'selection')
            scope = dict(type='http', method='POST', scheme='https', path=url, raw_path=url.encode(),
                query_string=b'', server=('localhost', 443), http_version='1.1', client=('127.0.0.1', 1234),
                headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode()),
                    (b'content-type', b'application/json'),
                    (b'cookie', (COOKIE_NAME + '=' + case.token).encode())])
            async def send(message):
                sent.append(message)
            call = asyncio.create_task(case.app(scope, messages.get, send))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                physical = case.root.task
                messages.put_nowait(dict(type='http.disconnect'))
                await asyncio.wait_for(call, 1)
                assert not physical.done() and physical in case.rig.gate._background
                assert case.coordinator._roots[case.root.root_id] is case.root and not case.root.closed
                assert not sent
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert not sent and case.root.reason == 'parameters_confirmation_required'
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['timeout', 'revision', 'replacement'])
def test_source_page_never_exposes_a_late_or_replaced_result(tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with input_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = asyncio.Event(), asyncio.Event()
            actual = case.coordinator.source_page
            async def held(**kwargs):
                result = await actual(**kwargs)
                entered.set()
                await release.wait()
                return result
            monkeypatch.setattr(case.coordinator, 'source_page', held)
            call = asyncio.create_task(post(case, 'source-page'))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if mutation == 'timeout':
                    response = await asyncio.wait_for(call, 6)
                    assert response.status_code == 503
                else:
                    if mutation == 'revision': case.root.accepted_revision += 1
                    else: case.coordinator._roots[case.root.root_id] = copy(case.root)
                    release.set()
                    response = await call
                    assert response.status_code == 409
                assert 'items' not in response.json()
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                case.coordinator._roots[case.root.root_id] = case.root
    asyncio.run(run())
