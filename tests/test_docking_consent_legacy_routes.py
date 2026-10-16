"""D5 legacy isolation: real HTTP sessions, history files and SQLite authorities."""
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.docking import history_index
from src.task_runtime import manager as manager_module
from src.task_runtime.manager import TaskManager
from src.task_runtime.store import TaskStore
from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
from src.web.routes.docking_routes import setup_docking_routes
from src.web.routes.docking_report_routes import register_docking_report_routes
from tests.task_runtime.test_docking_consent_generic_routes import consent, STATES
from tests.test_durable_docking_artifact_routes import _pdbqt_with_two_poses


@pytest.fixture
def api(tmp_path, monkeypatch, request):
    prefix = getattr(request, 'param', None)
    if prefix is not None:
        import itertools
        import secrets
        original = secrets.token_urlsafe
        sequence = itertools.count()
        monkeypatch.setattr(secrets, 'token_urlsafe', lambda size:
            prefix + str(next(sequence)).zfill(31) if size == 24 else original(size))
    manager = TaskManager(tmp_path / 'manager.sqlite')
    runtime = SimpleNamespace(store=TaskStore(tmp_path / 'runtime.sqlite'),
                              config=SimpleNamespace(staging_root=tmp_path / 'stage'))
    config = SimpleNamespace(runtime=runtime)
    monkeypatch.setattr(manager_module, 'get_task_manager', lambda: manager)
    work = tmp_path / 'history'
    service = SimpleNamespace(work_dir=str(work), parse_vina_results=lambda _: [])
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(tmp_path / 'sessions.sqlite'))

    @app.get('/who')
    def who(request: Request):
        return request.scope['agent_session_id']

    setup_docking_routes(app, service, task_runtime=lambda: config.runtime,
                         logger=logging.getLogger(__name__))
    register_docking_report_routes(app, service,
        validate_report_base64_payload=lambda a, b: (a, b))
    with TestClient(app, base_url='https://localhost', raise_server_exceptions=False) as client, \
            TestClient(app, base_url='https://localhost', raise_server_exceptions=False) as foreign:
        owner = client.get('/who').json()
        other = foreign.get('/who').json()
        yield SimpleNamespace(app=app, client=client, foreign=foreign, owner=owner,
            other=other, manager=manager, runtime=runtime, config=config, work=work, service=service)
    manager.executor.shutdown(wait=True)


def history(api, task_id, owner=None, timestamp=1):
    directory = api.work / ('docking_' + task_id)
    directory.mkdir(parents=True)
    (directory / 'result.pdbqt').write_bytes(_pdbqt_with_two_poses())
    history_index.upsert_history_record(api.work, dict(job_id=task_id,
        owner_session_id=api.owner if owner is None else owner, timestamp=timestamp))
    return directory


def request(api, action, task_id='ordinary'):
    path = '/api/docking/' + action + '/' + task_id
    if action == 'report':
        return api.client.post(path, json={})
    if action == 'history':
        return api.client.delete(path)
    return api.client.get(path)


def forbid_artifacts(api, monkeypatch):
    from src.docking import pose_export, interaction_analysis
    from src.task_runtime.completion import DockingCompletionStore
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('artifact/control')
        raise AssertionError('C artifact/control reached')

    monkeypatch.setattr(api.service, 'parse_vina_results', forbidden)
    monkeypatch.setattr(pose_export, 'pose_sdf_from_pdbqt', forbidden)
    monkeypatch.setattr(interaction_analysis, 'resolve_analysis_inputs', forbidden)
    monkeypatch.setattr(DockingCompletionStore, 'read_pose', forbidden)
    original = Path.read_bytes

    def read_bytes(path):
        if path.name == 'result.pdbqt':
            return forbidden()
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    return calls


@pytest.mark.parametrize('source', ['manager', 'runtime'])
@pytest.mark.parametrize('state', STATES)
def test_c_membership_blocks_every_legacy_surface(api, monkeypatch, source, state):
    directory = history(api, 'ordinary')
    store = getattr(api, source).store
    consent(store, 'ordinary', state, owner=api.other)
    calls = forbid_artifacts(api, monkeypatch)
    for action in ('result', 'pose_sdf', 'interactions', 'report', 'history'):
        assert request(api, action).status_code == 404, action
    response = api.client.get('/api/docking/history')
    assert response.status_code == 200
    assert response.json()['history'] == [] and response.json()['total'] == 0
    assert 'ordinary' not in response.text
    assert api.client.delete('/api/docking/history').json()['deleted'] == 0
    assert directory.is_dir() and calls == []


@pytest.mark.parametrize('source', ['manager', 'runtime'])
@pytest.mark.parametrize('task_source', ['manager', 'runtime', 'neither'])
@pytest.mark.parametrize('owner_kind', ['own', 'foreign'])
def test_consent_only_collision_does_not_need_task_row_or_owner(api, source, task_source, owner_kind):
    history(api, 'ordinary')
    if task_source != 'neither':
        getattr(api, task_source).store.create('ordinary', 'docking', {}, owner_session_id=api.owner)
    consent(getattr(api, source).store, 'ordinary', owner=api.owner if owner_kind == 'own' else api.other)
    assert request(api, 'result').status_code == 404


@pytest.mark.parametrize('action', ['result', 'pose_sdf', 'interactions', 'report', 'history'])
@pytest.mark.parametrize('source', ['manager', 'runtime'])
def test_late_consent_during_owned_history_read(api, monkeypatch, action, source):
    directory = history(api, 'ordinary')
    original = history_index.get_history_record
    inserted = []

    def read(*args, **kwargs):
        record = original(*args, **kwargs)
        if not inserted:
            consent(getattr(api, source).store, 'ordinary')
            inserted.append(True)
        return record

    monkeypatch.setattr(history_index, 'get_history_record', read)
    calls = forbid_artifacts(api, monkeypatch)
    assert request(api, action).status_code == 404
    assert inserted == [True] and directory.is_dir() and calls == []


@pytest.mark.parametrize('action', ['result', 'pose_sdf', 'interactions'])
def test_late_consent_during_owned_durable_read(api, monkeypatch, action):
    task_id = '00000000-0000-4000-8000-000000000991'
    api.runtime.config.staging_root.mkdir()
    api.runtime.store.create(task_id, 'docking', {}, owner_session_id=api.owner,
                             input_manifest_path='fixture-manifest')
    with sqlite3.connect(api.runtime.store.db_path) as connection:
        connection.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?", (task_id,))
    original = api.runtime.store.get_owned

    def read(*args, **kwargs):
        record = original(*args, **kwargs)
        consent(api.manager.store, task_id)
        return record

    monkeypatch.setattr(api.runtime.store, 'get_owned', read)
    calls = forbid_artifacts(api, monkeypatch)
    assert request(api, action, task_id).status_code == 404
    assert api.manager.store.is_docking_consent_task(task_id) is True
    assert calls == []


@pytest.mark.parametrize('broken', ['runtime-none', 'runtime-store', 'manager-store',
    'runtime-db', 'manager-db', 'classifier-int', 'classifier-raise', 'runtime-binding-raise'])
@pytest.mark.parametrize('empty', [False, True])
def test_unavailable_authority_is_503_including_empty_history(api, monkeypatch, broken, empty):
    if not empty:
        history(api, 'ordinary')
    if broken == 'runtime-none':
        api.config.runtime = None
    elif broken == 'runtime-binding-raise':
        del api.config.runtime
    elif broken in ('runtime-store', 'manager-store'):
        getattr(api, broken.split('-')[0]).store = object()
    elif broken.endswith('-db'):
        with sqlite3.connect(getattr(api, broken.split('-')[0]).store.db_path) as connection:
            connection.execute('DROP TABLE docking_consents')
    elif broken == 'classifier-int':
        monkeypatch.setattr(api.runtime.store, 'is_docking_consent_task', lambda _: 0)
    else:
        def unavailable(_):
            raise RuntimeError('broken')
        monkeypatch.setattr(api.manager.store, 'is_docking_consent_task', unavailable)
    for action in ('result', 'pose_sdf', 'interactions', 'report', 'history'):
        assert request(api, action).status_code == 503, action
    assert api.client.get('/api/docking/history').status_code == 503
    assert api.client.delete('/api/docking/history').status_code == 503


def test_filtered_pagination_total_and_clear_preserve_non_owned_and_c(api, monkeypatch):
    from src.web.routes import docking_routes
    monkeypatch.setattr(docking_routes, '_HISTORY_PAGE_SIZE', 2)
    for index in range(7):
        history(api, f'c-{index}', timestamp=20 + index)
        consent(api.runtime.store, f'c-{index}')
    for index in range(4):
        history(api, f'normal-{index}', timestamp=index)
    history(api, 'foreign', owner=api.other)
    history(api, 'unknown', owner='')
    response = api.client.get('/api/docking/history?page=2&limit=2')
    assert response.status_code == 200
    assert response.json()['total'] == 4
    assert [row['job_id'] for row in response.json()['history']] == ['normal-1', 'normal-0']
    response = api.client.delete('/api/docking/history')
    assert response.status_code == 200 and response.json()['deleted'] == 4
    assert all((api.work / ('docking_' + name)).is_dir()
               for name in ['foreign', 'unknown'] + [f'c-{i}' for i in range(7)])


def test_non_c_prefix_is_not_membership_and_owner_is_still_required(api):
    history(api, 'docking-consent-c-task')
    assert request(api, 'result', 'docking-consent-c-task').status_code == 200
    assert request(api, 'pose_sdf', 'docking-consent-c-task').status_code == 200
    assert api.foreign.get('/api/docking/result/docking-consent-c-task').status_code == 404
    assert request(api, 'history', 'docking-consent-c-task').status_code == 200


def test_late_consent_from_history_snapshot_is_filtered(api, monkeypatch):
    history(api, 'ordinary')
    original = history_index.read_owned_history_snapshot
    inserted = []

    def read(*args, **kwargs):
        rows = original(*args, **kwargs)
        if not inserted:
            consent(api.manager.store, 'ordinary')
            inserted.append(True)
        return rows

    monkeypatch.setattr(history_index, 'read_owned_history_snapshot', read)
    response = api.client.get('/api/docking/history')
    assert response.json()['history'] == [] and response.json()['total'] == 0
    assert api.client.delete('/api/docking/history').json()['deleted'] == 0


def test_history_scan_budget_is_fail_closed(api, monkeypatch):
    from src.web.routes import docking_routes
    monkeypatch.setattr(docking_routes, '_HISTORY_SCAN_LIMIT', 2, raising=False)
    for index in range(3):
        history(api, f'job-{index}')
    assert api.client.get('/api/docking/history?limit=1').status_code == 503
    assert api.client.delete('/api/docking/history').status_code == 503
    assert len(list(api.work.glob('docking_job-*'))) == 3


def test_global_clear_rechecks_owned_record_and_preserves_late_c(api, monkeypatch):
    directory = history(api, 'ordinary')
    original = history_index.get_history_record
    calls = []

    def read(*args, **kwargs):
        record = original(*args, **kwargs)
        if not calls:
            consent(api.runtime.store, 'ordinary')
            calls.append(True)
        return record

    monkeypatch.setattr(history_index, 'get_history_record', read)
    response = api.client.delete('/api/docking/history')
    assert response.status_code == 200 and response.json()['deleted'] == 0
    assert calls == [True] and directory.is_dir()


@pytest.mark.parametrize('collision', ['consent-only', 'foreign-task'])
@pytest.mark.parametrize('action', ['result', 'pose_sdf', 'interactions'])
def test_durable_opposite_store_collision_before_artifact(api, monkeypatch, collision, action):
    task_id = '00000000-0000-4000-8000-000000000991'
    api.runtime.config.staging_root.mkdir()
    api.runtime.store.create(task_id, 'docking', {}, owner_session_id=api.owner,
                             input_manifest_path='fixture-manifest')
    with sqlite3.connect(api.runtime.store.db_path) as connection:
        connection.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?", (task_id,))
    if collision == 'foreign-task':
        api.manager.store.create(task_id, 'docking', {}, owner_session_id=api.other)
    else:
        original = api.manager.store.get
        def read(key):
            consent(api.manager.store, key)
            return original(key)
        monkeypatch.setattr(api.manager.store, 'get', read)
    calls = forbid_artifacts(api, monkeypatch)
    assert request(api, action, task_id).status_code == 404
    assert calls == []
    if collision == 'consent-only':
        assert api.manager.store.is_docking_consent_task(task_id)


@pytest.mark.parametrize('action', ['result', 'report', 'history'])
def test_broken_classifier_after_owned_read_propagates_503(api, monkeypatch, action):
    directory = history(api, 'ordinary')
    original = history_index.get_history_record
    def read(*args, **kwargs):
        row = original(*args, **kwargs)
        monkeypatch.setattr(api.runtime.store, 'is_docking_consent_task', lambda _: None)
        return row
    monkeypatch.setattr(history_index, 'get_history_record', read)
    calls = forbid_artifacts(api, monkeypatch)
    assert request(api, action).status_code == 503
    assert calls == [] and directory.is_dir()


def test_global_clear_rechecks_between_filesystem_and_index_delete(api, monkeypatch):
    import shutil
    history(api, 'ordinary')
    original = shutil.rmtree
    def remove(path, *args, **kwargs):
        original(path, *args, **kwargs)
        if Path(path).name == 'docking_ordinary':
            consent(api.manager.store, 'ordinary')
    monkeypatch.setattr(shutil, 'rmtree', remove)
    response = api.client.delete('/api/docking/history')
    assert response.status_code == 200 and response.json()['deleted'] == 0
    assert history_index.get_history_record(api.work, 'ordinary', owner_session_id=api.owner)


def test_unconfigured_runtime_still_checks_manager_and_report_shared_binding(api):
    # Re-registering only on a separate app exercises the literal None contract.
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=AgentSessionStore(api.work.parent / 'extra-sessions.sqlite'))
    @app.get('/who')
    def who(request: Request):
        return request.scope['agent_session_id']
    setup_docking_routes(app, api.service, task_runtime=None, logger=logging.getLogger(__name__))
    register_docking_report_routes(app, api.service,
        validate_report_base64_payload=lambda a, b: (a, b))
    with TestClient(app, base_url='https://localhost') as client:
        owner = client.get('/who').json()
        history(api, 'plain', owner=owner)
        assert client.get('/api/docking/result/plain').status_code == 200
        assert client.post('/api/docking/report/plain', json={}).status_code == 200
        consent(api.manager.store, 'plain')
        assert client.get('/api/docking/result/plain').status_code == 404
        assert client.post('/api/docking/report/plain', json={}).status_code == 404


def test_same_database_membership_still_blocks(api):
    api.runtime.store = api.manager.store
    history(api, 'ordinary')
    consent(api.manager.store, 'ordinary')
    assert request(api, 'result').status_code == 404
    assert request(api, 'report').status_code == 404


def durable_artifact(api):
    import hashlib
    import json
    from src.task_runtime.staging import DockingInputStager
    task_id = '00000000-0000-4000-8000-000000000991'
    root = api.runtime.config.staging_root
    manifest = DockingInputStager(root).stage(task_id, 'receptor.pdb',
        b'ATOM      1  C   ALA A   1       0.000   0.000   0.000\n', None, None, 'C',
        {'center': [0.0, 0.0, 0.0], 'size': [20.0, 20.0, 20.0]})
    pose = _pdbqt_with_two_poses()
    artifact = root / task_id / 'artifacts' / 'docking_pose.pdbqt'
    artifact.parent.mkdir()
    artifact.write_bytes(pose)
    completion = dict(schema='DockingCompletionManifest@1', task_id=task_id,
        input_hash='0' * 64, config_hash='1' * 64, tool_version='fixture-tool',
        model_name='fixture', model_version='1', demo_mode=False, fallback_used=False,
        real_execution=True, pose=dict(path='artifacts/docking_pose.pdbqt', size=len(pose),
            sha256=hashlib.sha256(pose).hexdigest()), pose_count=2, best_energy=-7.0,
        best_pose_index=1, validator_status='succeeded', attempt=1,
        completed_at='2026-10-12T00:00:00Z', result_metadata=dict(elapsed_ms=1,
            formatted='Synthetic route fixture, not scientific execution', warnings=[], evidence=[],
            artifacts=[], quality=dict(real_execution=True, engine='fixture',
                                       execution_status='completed', validated=True)))
    (root / task_id / 'completion_manifest.json').write_text(json.dumps(completion), encoding='utf-8')
    api.runtime.store.create(task_id, 'docking', {}, owner_session_id=api.owner,
                             input_manifest_path=str(manifest))
    with sqlite3.connect(api.runtime.store.db_path) as connection:
        connection.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?", (task_id,))
    return task_id, pose


@pytest.mark.parametrize('same_database', [False, True])
def test_real_durable_non_c_result_and_pose_remain_available(api, monkeypatch, same_database):
    from src.docking import interaction_analysis
    if same_database:
        api.runtime.store = api.manager.store
    task_id, pose = durable_artifact(api)
    response = request(api, 'result', task_id)
    assert response.status_code == 200 and response.content == pose
    assert request(api, 'pose_sdf', task_id).status_code == 200
    monkeypatch.setattr(interaction_analysis, '_dependency_available', lambda _: False)
    response = request(api, 'interactions', task_id)
    assert response.status_code == 200
    assert response.json()['reason_code'] == 'dependency_missing'
    assert api.foreign.get('/api/docking/result/' + task_id).status_code == 404


@pytest.mark.parametrize('action', ['result', 'pose_sdf', 'interactions'])
@pytest.mark.parametrize('switch_binding', [False, True])
@pytest.mark.parametrize('change', ['consent', 'collision', 'classifier-int', 'storage-error'])
def test_durable_read_rechecks_original_authority_after_binding_switch(
        api, monkeypatch, action, switch_binding, change):
    from src.task_runtime.completion import DockingCompletionStore
    task_id, pose = durable_artifact(api)
    assert request(api, 'result', task_id).content == pose
    runtime_a = api.runtime
    runtime_b = SimpleNamespace(store=TaskStore(api.work.parent / 'clean-runtime.sqlite'),
                                config=runtime_a.config)
    original = runtime_a.store.get_owned
    reads, artifacts = [], []
    original_pose = DockingCompletionStore.read_pose

    def read(*args, **kwargs):
        record = original(*args, **kwargs)
        reads.append(True)
        if change == 'consent':
            consent(runtime_a.store, task_id, owner=api.other)
        elif change == 'collision':
            api.manager.store.create(task_id, 'docking', {}, owner_session_id=api.other)
        elif change == 'classifier-int':
            monkeypatch.setattr(runtime_a.store, 'is_docking_consent_task', lambda _: 0)
        else:
            with sqlite3.connect(runtime_a.store.db_path) as connection:
                connection.execute('DROP TABLE docking_consents')
        if switch_binding:
            api.config.runtime = runtime_b
        return record

    def read_pose(*args, **kwargs):
        artifacts.append(True)
        return original_pose(*args, **kwargs)

    monkeypatch.setattr(runtime_a.store, 'get_owned', read)
    monkeypatch.setattr(DockingCompletionStore, 'read_pose', read_pose)
    response = request(api, action, task_id)
    expected = (404, 503) if change in ('consent', 'collision') else (503,)
    assert response.status_code in expected
    assert reads == [True] and artifacts == []
    assert task_id not in response.text and api.other not in response.text


@pytest.mark.parametrize('binding', ['clean', 'missing'])
def test_durable_binding_change_alone_fails_closed_before_artifact(api, monkeypatch, binding):
    task_id, _ = durable_artifact(api)
    replacement = SimpleNamespace(store=TaskStore(api.work.parent / 'replacement.sqlite'),
                                  config=api.runtime.config) if binding == 'clean' else None
    original = api.runtime.store.get_owned

    def read(*args, **kwargs):
        record = original(*args, **kwargs)
        api.config.runtime = replacement
        return record

    monkeypatch.setattr(api.runtime.store, 'get_owned', read)
    calls = forbid_artifacts(api, monkeypatch)
    assert request(api, 'result', task_id).status_code == 503
    assert calls == []


@pytest.mark.parametrize('source', ['manager', 'runtime'])
def test_missing_database_is_not_recreated(api, source):
    database = getattr(api, source).store.db_path
    database.unlink()
    assert api.client.get('/api/docking/history').status_code == 503
    assert api.client.delete('/api/docking/history').status_code == 503
    assert not database.exists()


@pytest.mark.parametrize('source', ['manager', 'runtime'])
def test_membership_does_not_short_circuit_other_broken_authority(api, source):
    history(api, 'ordinary')
    consent(getattr(api, source).store, 'ordinary')
    other = api.runtime if source == 'manager' else api.manager
    with sqlite3.connect(other.store.db_path) as connection:
        connection.execute('DROP TABLE docking_consents')
    for action in ('result', 'pose_sdf', 'interactions', 'report', 'history'):
        assert request(api, action).status_code == 503


def test_delete_rejects_directory_link_outside_workdir(api):
    api.work.mkdir()
    outside = api.work.parent / 'outside'
    outside.mkdir()
    sentinel = outside / 'keep.txt'
    sentinel.write_text('must remain', encoding='utf-8')
    try:
        (api.work / 'docking_ordinary').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlink privilege unavailable')
    history_index.upsert_history_record(api.work,
        dict(job_id='ordinary', owner_session_id=api.owner, timestamp=1))
    assert request(api, 'history').status_code == 404
    assert api.client.delete('/api/docking/history').status_code == 404
    assert sentinel.read_text(encoding='utf-8') == 'must remain'


@pytest.mark.parametrize('kind', ['legacy', 'chat'])
@pytest.mark.parametrize('action', ['result', 'report', 'history'])
@pytest.mark.parametrize('api', ['A', '-', '_'], indirect=True)
def test_actual_reservation_during_owned_history_read(api, monkeypatch, kind, action):
    from src.task_runtime import docking_consent
    from tests.task_runtime.test_docking_consent import identity as legacy_identity, policy
    from tests.task_runtime.test_docking_consent_draft import identity as chat_identity, metadata
    directory = history(api, 'ordinary')
    original = history_index.get_history_record
    inserted = []
    def read(*args, **kwargs):
        row = original(*args, **kwargs)
        if not inserted:
            selected_policy = policy(docking_consent)
            identity = legacy_identity() if kind == 'legacy' else chat_identity()
            identity.update(task_id='ordinary', preparation_id='late-preparation', owner_session_id=api.other)
            if kind == 'legacy':
                api.runtime.store.reserve_docking_consent_draft(identity=identity,
                    policy=selected_policy, now_ms=1000000, monotonic_now=100.0)
            else:
                api.runtime.store.reserve_chat_docking_draft(identity=identity, draft=metadata(),
                    policy=selected_policy, wall_time_ms=lambda: 1000000, monotonic=lambda: 100.0,
                    current_context=lambda: dict(policy=selected_policy, model_generation='model-1',
                                                 capability_generation='capability-1'))
            inserted.append(True)
        return row
    monkeypatch.setattr(history_index, 'get_history_record', read)
    calls = forbid_artifacts(api, monkeypatch)
    assert request(api, action).status_code == 404
    assert inserted == [True] and directory.is_dir() and calls == []


def test_report_missing_shared_authority_does_not_fall_back(api):
    history(api, 'ordinary')
    consent(api.runtime.store, 'ordinary')
    api.app.state.legacy_docking_consent_guard = None
    assert request(api, 'report').status_code == 503


@pytest.mark.parametrize('action', ['result', 'pose_sdf', 'interactions', 'report', 'history', 'list', 'clear'])
def test_non_c_cross_database_collision_blocks_legacy_hit(api, monkeypatch, action):
    directory = history(api, 'ordinary')
    api.runtime.store.create('ordinary', 'docking', {}, owner_session_id=api.owner)
    api.manager.store.create('ordinary', 'docking', {}, owner_session_id=api.other)
    calls = forbid_artifacts(api, monkeypatch)
    if action == 'list':
        response = api.client.get('/api/docking/history')
        assert response.status_code == 200 and response.json()['history'] == []
        assert response.json()['total'] == 0
    elif action == 'clear':
        response = api.client.delete('/api/docking/history')
        assert response.status_code == 200 and response.json()['deleted'] == 0
    else:
        assert request(api, action).status_code == 404
    assert calls == [] and directory.is_dir()
    assert history_index.get_history_record(api.work, 'ordinary', owner_session_id=api.owner)


@pytest.mark.parametrize('source', ['manager', 'runtime', 'same-database'])
def test_single_database_non_c_legacy_hit_remains_compatible(api, source):
    history(api, 'ordinary')
    if source == 'same-database':
        api.runtime.store = TaskStore(api.manager.store.db_path)
        source = 'manager'
    getattr(api, source).store.create('ordinary', 'docking', {}, owner_session_id=api.owner)
    assert request(api, 'result').status_code == 200
    assert request(api, 'report').status_code == 200
    assert api.client.get('/api/docking/history').json()['total'] == 1
    assert request(api, 'history').status_code == 200


@pytest.mark.parametrize('mutation', ['same-total', 'count-change'])
@pytest.mark.parametrize('action', ['list', 'clear'])
def test_real_history_mutation_requires_atomic_snapshot_or_503(api, monkeypatch, mutation, action):
    from src.web.routes import docking_routes
    monkeypatch.setattr(docking_routes, '_HISTORY_PAGE_SIZE', 2)
    for name, timestamp in zip('abcd', [4, 3, 2, 1]):
        history(api, name, timestamp=timestamp)
    original_page = history_index.read_history_page
    original_snapshot = getattr(history_index, 'read_owned_history_snapshot', None)
    reads = []

    def mutate():
        if mutation == 'same-total':
            history_index.remove_history_record(api.work, 'a')
        history(api, 'x', timestamp=5)

    def page(*args, **kwargs):
        result = original_page(*args, **kwargs)
        if not reads:
            reads.append('page')
            mutate()
        return result

    def snapshot(*args, **kwargs):
        result = original_snapshot(*args, **kwargs)
        reads.append('snapshot')
        mutate()
        return result

    monkeypatch.setattr(history_index, 'read_history_page', page)
    monkeypatch.setattr(history_index, 'read_owned_history_snapshot', snapshot, raising=False)
    response = (api.client.get if action == 'list' else api.client.delete)('/api/docking/history')
    assert reads
    if reads == ['page']:
        # Independent pages have no version token; even unchanged totals are unsafe.
        assert response.status_code == 503
    else:
        assert reads == ['snapshot'] and response.status_code == 200
        if action == 'list':
            assert [row['job_id'] for row in response.json()['history']] == list('abcd')
        else:
            assert response.json()['deleted'] == (3 if mutation == 'same-total' else 4)
    assert (api.work / 'docking_x').is_dir()
    assert history_index.get_history_record(api.work, 'x', owner_session_id=api.owner)


@pytest.mark.parametrize('payload', ['{"records": [', '{"records": {}}',
    '{"records": [null]}', '{"records": [{"job_id": "ordinary", "owner_session_id": "OWNER"},'
    '{"job_id": "ordinary", "owner_session_id": "OWNER"}]}'])
def test_snapshot_corrupt_or_duplicate_metadata_fails_closed(api, payload):
    directory = history(api, 'ordinary')
    history_index.history_index_path(api.work).write_text(
        payload.replace('OWNER', api.owner), encoding='utf-8')
    assert api.client.get('/api/docking/history').status_code == 503
    assert api.client.delete('/api/docking/history').status_code == 503
    assert directory.is_dir()


@pytest.mark.parametrize('action', ['list', 'clear'])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('other_owner', ['foreign', 'own', 'missing', 'foreign-coerced'])
def test_snapshot_duplicate_owned_candidate_is_rejected_without_mutation(
        api, action, reverse, other_owner):
    import json
    task_id = '7' if other_owner == 'foreign-coerced' else 'ordinary'
    directory = history(api, task_id)
    untouched = history(api, 'unambiguous', timestamp=10)
    owned = dict(job_id=task_id, owner_session_id=api.owner, timestamp=1)
    duplicate = dict(job_id=7 if other_owner == 'foreign-coerced' else task_id, timestamp=2)
    if other_owner != 'missing':
        duplicate['owner_session_id'] = api.owner if other_owner == 'own' else api.other
    rows = [owned, duplicate]
    if reverse:
        rows.reverse()
    rows.insert(0, dict(job_id='unambiguous', owner_session_id=api.owner, timestamp=10))
    index = history_index.history_index_path(api.work)
    index.write_text(json.dumps({'records': rows}), encoding='utf-8')
    before = index.read_bytes()
    files = {path: path.read_bytes() for folder in (directory, untouched) for path in folder.iterdir()}
    response = (api.client.get if action == 'list' else api.client.delete)('/api/docking/history')
    assert response.status_code == 503
    assert response.json() == {'detail': 'Docking history snapshot unavailable'}
    assert index.read_bytes() == before
    assert directory.is_dir() and untouched.is_dir()
    assert all(path.read_bytes() == content for path, content in files.items())


@pytest.mark.parametrize('reverse', [False, True])
def test_snapshot_duplicate_detection_holds_both_locks(api, monkeypatch, reverse):
    import json
    from contextlib import contextmanager
    history(api, 'ordinary')
    rows = [dict(job_id='ordinary', owner_session_id=api.owner),
            dict(job_id='ordinary', owner_session_id=api.other)]
    index = history_index.history_index_path(api.work)
    index.write_text(json.dumps(rows[::-1] if reverse else rows), encoding='utf-8')
    original = history_index._interprocess_history_lock
    rejected_under_lock = []

    @contextmanager
    def lock(work_dir):
        with original(work_dir):
            try:
                yield
            except ValueError:
                assert history_index._HISTORY_INDEX_LOCK._is_owned()
                rejected_under_lock.append(True)
                raise

    monkeypatch.setattr(history_index, '_interprocess_history_lock', lock)
    with pytest.raises(ValueError, match='Invalid history identity'):
        history_index.read_owned_history_snapshot(api.work, api.owner)
    assert rejected_under_lock == [True]


def test_snapshot_unrelated_foreign_duplicates_remain_ignored(api):
    import json
    history(api, 'ordinary')
    index = history_index.history_index_path(api.work)
    rows = [dict(job_id='ordinary', owner_session_id=api.owner),
            dict(job_id='foreign-only', owner_session_id=api.other),
            dict(job_id='foreign-only', owner_session_id=api.other),
            dict(job_id=['malformed', 'foreign'], owner_session_id=api.other)]
    index.write_text(json.dumps({'records': rows}), encoding='utf-8')
    response = api.client.get('/api/docking/history')
    assert response.status_code == 200 and response.json()['total'] == 1
    assert response.json()['history'][0]['job_id'] == 'ordinary'
    response = api.client.delete('/api/docking/history')
    assert response.status_code == 200 and response.json()['deleted'] == 1
    assert json.loads(index.read_text(encoding='utf-8'))['records'] == rows[1:]


def test_snapshot_is_bounded_detached_sorted_and_uses_both_locks(api, monkeypatch):
    from contextlib import contextmanager
    history(api, 'older', timestamp=1)
    history(api, 'newer', timestamp=2)
    history(api, 'foreign', owner=api.other, timestamp=3)
    original_lock = history_index._interprocess_history_lock
    original_load = history_index._load_records_unlocked
    held = []
    observed = []

    @contextmanager
    def lock(work_dir):
        assert history_index._HISTORY_INDEX_LOCK._is_owned()
        with original_lock(work_dir):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    def load(*args, **kwargs):
        assert held == [True]
        records = original_load(*args, **kwargs)
        observed.extend(records)
        return records

    monkeypatch.setattr(history_index, '_interprocess_history_lock', lock)
    monkeypatch.setattr(history_index, '_load_records_unlocked', load)
    calls = forbid_artifacts(api, monkeypatch)
    rows = history_index.read_owned_history_snapshot(api.work, api.owner, max_records=2)
    assert [row['job_id'] for row in rows] == ['newer', 'older']
    rows[0]['timestamp'] = 100
    assert next(row for row in observed if row['job_id'] == 'newer')['timestamp'] == 2
    with pytest.raises(ValueError):
        history_index.read_owned_history_snapshot(api.work, api.owner, max_records=1)
    assert calls == [] and held == []


@pytest.mark.parametrize('source', ['manager', 'runtime'])
@pytest.mark.parametrize('change', ['consent-only', 'poisoned-binding'])
@pytest.mark.parametrize('action', ['result', 'report', 'history', 'list', 'clear'])
def test_collision_metadata_read_rechecks_late_authority(api, monkeypatch, source, change, action):
    directory = history(api, 'ordinary')
    store = getattr(api, source).store
    original = store.get
    changed = []

    def read(task_id):
        try:
            return original(task_id)
        finally:
            if task_id == 'ordinary' and not changed:
                changed.append(True)
                if change == 'consent-only':
                    consent(store, task_id, owner=api.other)
                else:
                    api.config.runtime = None

    monkeypatch.setattr(store, 'get', read)
    calls = forbid_artifacts(api, monkeypatch)
    if action in ('list', 'clear'):
        response = (api.client.get if action == 'list' else api.client.delete)('/api/docking/history')
        if change == 'consent-only':
            assert response.status_code == 200
            assert response.json()['total' if action == 'list' else 'deleted'] == 0
        else:
            assert response.status_code == 503
    else:
        assert request(api, action).status_code == (404 if change == 'consent-only' else 503)
    assert changed == [True] and calls == [] and directory.is_dir()


@pytest.mark.parametrize('action', ['result', 'report', 'history', 'clear'])
def test_late_non_c_collision_during_owned_history_read(api, monkeypatch, action):
    directory = history(api, 'ordinary')
    api.runtime.store.create('ordinary', 'docking', {}, owner_session_id=api.owner)
    original = history_index.get_history_record
    inserted = []

    def read(*args, **kwargs):
        row = original(*args, **kwargs)
        if not inserted:
            api.manager.store.create('ordinary', 'docking', {}, owner_session_id=api.other)
            inserted.append(True)
        return row

    monkeypatch.setattr(history_index, 'get_history_record', read)
    calls = forbid_artifacts(api, monkeypatch)
    if action == 'clear':
        response = api.client.delete('/api/docking/history')
        assert response.status_code == 200 and response.json()['deleted'] == 0
    else:
        assert request(api, action).status_code == 404
    assert inserted == [True] and calls == [] and directory.is_dir()
