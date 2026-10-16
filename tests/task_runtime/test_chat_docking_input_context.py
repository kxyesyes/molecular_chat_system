"""Live-source C1 gate; synthetic source providers, never real docking evidence."""
import asyncio
from contextlib import asynccontextmanager, closing
from copy import deepcopy
import importlib
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from src.docking import chat_preparation, source_preparation
from src.task_runtime.backends.local import LocalTaskBackend
from src.task_runtime.config import TaskRuntimeConfig
from src.task_runtime.docking_consent import DockingConsentError
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.staging import DockingInputStager
from tests.agent.test_web_docking_consent import target_case
from tests.agent.test_web_docking_sources import source_rig, invalidate_source
from tests.agent.test_web_docking_selection import _complete_source_child
from tests.agent.test_web_docking_parameters import prepared, confirmation_args


def context_api():
    name = 'src.web.docking_input_context'
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail('live one-use chat input context is absent', pytrace=False)


@asynccontextmanager
async def authenticated_rig(tmp_path, target_case, monkeypatch, *, capture_context=True):
    api = context_api()
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _complete_source_child)
    async with rig.client() as client:
        coordinator = rig.coordinator(client)
        before = await prepared(rig, pointer, coordinator)
        view = await coordinator.confirm_parameters(**confirmation_args(before))
        assert view['reason_code'] == 'inputs_confirmed_staging_required', view
        root = coordinator._roots['root-1']
        calls, stages = [], []
        async def forbidden(*args, **kwargs):
            calls.append('backend')
            raise AssertionError('C1 cannot execute docking')
        local = LocalTaskBackend(rig.tasks, {'docking': forbidden})
        for name in ('submit', 'get', 'list', 'cancel', 'health'):
            monkeypatch.setattr(local, name, forbidden)
        stager = DockingInputStager(tmp_path / 'stage')
        actual_stage = stager.stage
        def stage(*args, **kwargs):
            stages.append(args[0])
            return actual_stage(*args, **kwargs)
        monkeypatch.setattr(stager, 'stage', stage)
        config = TaskRuntimeConfig(backend='local', canary_percent=0,
            temporal_address='unused.invalid:7233', temporal_namespace='test',
            docking_queue='test', docking_concurrency=1, staging_root=tmp_path / 'stage')
        runtime = TaskRuntime(config=config, store=rig.tasks, stager=stager,
            local_backend=local, docking_consent_policy=rig.policy,
            consent_wall_time_ms=lambda: rig.wall, consent_monotonic=lambda: rig.mono)
        context = (await api.capture_chat_input_context(coordinator,
            root_id='root-1', owner_session_id='owner-session')) if capture_context else None
        args = dict(preparation_id=root.preparation_id, owner_session_id=root.owner,
            revision=root.accepted_revision, receptor_name='receptor.pdb',
            receptor_bytes=root.projected['pdb_bytes'], ligand_name='ligand.sdf',
            ligand_bytes=root.ligand['sdf_bytes'],
            parameters=deepcopy(view['prepared_preview']['confirmed_parameters']),
            chat_input_context=context)
        result = SimpleNamespace(rig=rig, coordinator=coordinator, root=root, runtime=runtime,
            stager=stager, calls=calls, stages=stages, context=context, args=args, api=api,
            expected_unresolved=False)
        try:
            yield result
        finally:
            if result.expected_unresolved:
                with pytest.raises(DockingConsentError, match='consent_cleanup_unresolved'):
                    await runtime.close()
                assert runtime._consent_preparations
            else:
                await runtime.close()


def test_live_context_checks_actual_staged_projection_without_locks_or_execution(
        tmp_path, target_case, monkeypatch):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            original = chat_preparation.run_preparation
            verified = []
            async def observe(name, **kwargs):
                assert name == 'projection_verify'
                assert case.rig.gate._readers == 0
                operation, = case.runtime._consent_preparations
                assert operation.projection_owner is kwargs['owner']
                assert kwargs['owner'] in case.root.preparation_owners
                for path, sql in ((case.rig.state.db_path, 'UPDATE agent_runs SET metadata_json=metadata_json'),
                                  (case.rig.tasks.db_path, 'UPDATE docking_consents SET state=state')):
                    with closing(sqlite3.connect(path, timeout=0)) as db, db:
                        db.execute(sql)
                verified.append(kwargs['inputs']['derived_bytes'])
                return await original(name, **kwargs)
            monkeypatch.setattr(chat_preparation, 'run_preparation', observe)
            preview = (await case.runtime.prepare_docking_consent(**case.args)).to_dict()
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'READY' and row['approval_nonce_hash'] is not None
            assert preview['expires_at_ms'] == case.rig.wall + 900000
            assert verified == [case.args['receptor_bytes']]
            assert case.stages == [case.root.task_id] and not case.calls
            assert not case.runtime._consent_preparations
            assert await case.api.capture_chat_input_context(case.coordinator,
                root_id='root-1', owner_session_id='owner-session') is case.context
            with pytest.raises(DockingConsentError):
                await case.runtime.prepare_docking_consent(**case.args)
            assert case.stages == [case.root.task_id]
    asyncio.run(run())


@pytest.mark.parametrize('change', ['owner', 'revision', 'preparation', 'receptor_bytes',
    'ligand_bytes', 'receptor_name', 'ligand_name', 'parameters', 'dict_context', 'fake_context',
    'preparation_subclass'])
def test_context_binds_exact_runtime_request_before_staging(tmp_path, target_case, monkeypatch, change):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            args = dict(case.args)
            if change == 'owner': args['owner_session_id'] = 'foreign'
            elif change == 'revision': args['revision'] += 1
            elif change == 'preparation': args['preparation_id'] = 'different'
            elif change == 'preparation_subclass':
                class Hook(str):
                    def __ne__(self, other): raise AssertionError('untrusted identifier hook ran')
                args['preparation_id'] = Hook(args['preparation_id'])
            elif change.endswith('_bytes'): args[change] += b'\n'
            elif change.endswith('_name'): args[change] = 'alternate.' + args[change].split('.')[-1]
            elif change == 'parameters':
                args['parameters'] = deepcopy(args['parameters'])
                args['parameters']['num_modes'] += 1
            elif change == 'dict_context': args['chat_input_context'] = {'valid': True}
            else: args['chat_input_context'] = SimpleNamespace(claim=lambda *a, **k: True)
            with pytest.raises(DockingConsentError):
                await case.runtime.prepare_docking_consent(**args)
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] != 'READY' and row['approval_nonce_hash'] is None
            assert not case.stages and not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'root', 'draft', 'model', 'deadline'])
def test_invalidation_after_staging_never_mints_ready(tmp_path, target_case, monkeypatch, mutation):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            original = case.stager.stage
            case.expected_unresolved = mutation == 'draft'
            def changed(*args, **kwargs):
                value = original(*args, **kwargs)
                invalidate_source(case.rig, mutation)
                return value
            monkeypatch.setattr(case.stager, 'stage', changed)
            with pytest.raises(DockingConsentError):
                await case.runtime.prepare_docking_consent(**case.args)
            if mutation != 'draft':
                row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
                assert row['state'] != 'READY' and row['approval_nonce_hash'] is None
            assert not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('field', ['receptor', 'ligand'])
def test_self_consistent_replacement_staging_is_not_source_authority(tmp_path, target_case, monkeypatch, field):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            original = case.stager.stage
            def changed(*args, **kwargs):
                args = list(args)
                args[2 if field == 'receptor' else 4] += b'\n'
                return original(*args, **kwargs)
            monkeypatch.setattr(case.stager, 'stage', changed)
            with pytest.raises(DockingConsentError):
                await case.runtime.prepare_docking_consent(**case.args)
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] != 'READY' and row['approval_nonce_hash'] is None
            assert case.stages == [case.root.task_id] and not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('hold_revocation', [False, True])
def test_cancel_during_actual_readback_never_dispatches_projection_child(tmp_path, target_case, monkeypatch, hold_revocation):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            revoke_entered, revoke_release = threading.Event(), threading.Event()
            if hold_revocation:
                revoke = case.rig.tasks._revoke_docking_preparation
                def held_revoke(*args):
                    revoke_entered.set()
                    assert revoke_release.wait(15), 'revocation fixture was not released'
                    return revoke(*args)
                monkeypatch.setattr(case.rig.tasks, '_revoke_docking_preparation', held_revoke)
            original = case.stager.read_verified_input_bytes
            def held(*args):
                entered.set()
                assert release.wait(15), 'readback fixture barrier was not released'
                return original(*args)
            monkeypatch.setattr(case.stager, 'read_verified_input_bytes', held)
            children = []
            actual_child = chat_preparation.run_preparation
            async def observed(name, **kwargs):
                children.append(name)
                return await actual_child(name, **kwargs)
            monkeypatch.setattr(chat_preparation, 'run_preparation', observed)
            call = asyncio.create_task(case.runtime.prepare_docking_consent(**case.args))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                call.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await call
                assert case.runtime._consent_preparations
                if hold_revocation:
                    assert await asyncio.to_thread(revoke_entered.wait, 8)
                    release.set()
                    operation, = case.runtime._consent_preparations
                    with pytest.raises(DockingConsentError):
                        await asyncio.wait_for(asyncio.shield(operation.worker), 8)
            finally:
                release.set()
                revoke_release.set()
                await case.runtime.close()
            assert children == []
            assert not case.runtime._consent_preparations and not case.calls
    asyncio.run(run())


def test_unsettled_real_projection_child_keeps_stage_and_capacity_until_physical_close(
        tmp_path, target_case, monkeypatch):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            cleanup = chat_preparation.PreparationOwner._cleanup
            def held(owner):
                entered.set()
                try:
                    assert release.wait(20), 'physical close fixture was not released'
                    return cleanup(owner)
                finally:
                    finished.set()
            monkeypatch.setattr(chat_preparation.PreparationOwner, '_cleanup', held)
            actual_child = chat_preparation.run_preparation
            async def bounded(name, **kwargs):
                kwargs['deadline'] = min(kwargs['deadline'], time.monotonic() + 3)
                return await actual_child(name, **kwargs)
            monkeypatch.setattr(chat_preparation, 'run_preparation', bounded)
            try:
                with pytest.raises(DockingConsentError):
                    await case.runtime.prepare_docking_consent(**case.args)
                assert entered.is_set() and not finished.is_set()
                operation, = case.runtime._consent_preparations
                await operation.settlement
                assert operation.physical_pending and not operation.settled
                assert not operation.projection_owner.snapshot()['settled']
                assert operation.manifest_path.exists()
                with pytest.raises(DockingConsentError, match='consent_cleanup_unresolved'):
                    await case.runtime.close()
                row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
                assert row['state'] == 'REVOKED' and row['cleanup_state'] == 'pending'
                assert row['approval_nonce_hash'] is None
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 8)
                for _ in range(100):
                    if operation.projection_owner.snapshot()['settled']:
                        break
                    await asyncio.sleep(0.01)
                await case.runtime.close()
            assert not case.runtime._consent_preparations
            assert not operation.manifest_path.exists()
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['cleanup_state'] == 'settled' and not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source_bytes', 'derived_bytes', 'ligand_bytes', 'projection'])
def test_context_capture_rejects_changed_private_producer_outputs(tmp_path, target_case, monkeypatch, mutation):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch, capture_context=False) as case:
            if mutation == 'source_bytes': case.root.source_result['pdb_bytes'] += b'\n'
            elif mutation == 'derived_bytes': case.root.projected['pdb_bytes'] += b'\n'
            elif mutation == 'ligand_bytes': case.root.ligand['sdf_bytes'] += b'\n'
            else: case.root.projected['projection']['rule_version'] = 'replacement'
            with pytest.raises(DockingConsentError):
                await case.api.capture_chat_input_context(case.coordinator,
                    root_id='root-1', owner_session_id='owner-session')
            assert not case.calls and not case.stages
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['begin', 'parameters', 'seal'])
def test_root_close_after_source_check_is_rechecked_under_task_store_lock(
        tmp_path, target_case, monkeypatch, phase):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            name = dict(begin='_begin_docking_preparation',
                        parameters='_validate_docking_preparation_parameters',
                        seal='_seal_docking_preparation')[phase]
            original = getattr(case.rig.tasks, name)
            def close_before_lock(*args, **kwargs):
                case.coordinator._stop(case.root, 'cancelled')
                return original(*args, **kwargs)
            monkeypatch.setattr(case.rig.tasks, name, close_before_lock)
            with pytest.raises(DockingConsentError):
                await case.runtime.prepare_docking_consent(**case.args)
            await case.runtime.close()
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] != 'READY' and row['approval_nonce_hash'] is None
            if phase != 'seal': assert not case.stages
            assert not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['begin', 'seal'])
def test_lost_sql_acknowledgement_cannot_return_ready_or_reuse_context(tmp_path, target_case, monkeypatch, phase):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            name = '_begin_docking_preparation' if phase == 'begin' else '_seal_docking_preparation'
            original = getattr(case.rig.tasks, name)
            def lost_ack(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('synthetic lost commit acknowledgement')
            monkeypatch.setattr(case.rig.tasks, name, lost_ack)
            with pytest.raises(DockingConsentError):
                await case.runtime.prepare_docking_consent(**case.args)
            with pytest.raises(DockingConsentError):
                await case.runtime.prepare_docking_consent(**case.args)
            await case.runtime.close()
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['approval_nonce_hash'] is None
            assert row['cleanup_state'] == 'settled' and not case.calls
    asyncio.run(run())


def test_cancel_after_final_source_guard_before_child_dispatch(tmp_path, target_case, monkeypatch):
    context_api()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            guarded = type(case.context)._check_guarded
            checks, children = [], []
            def delayed_return(context):
                result = guarded(context)  # Real source/row check; all locks already released.
                checks.append(1)
                if len(checks) == 2:
                    entered.set()
                    assert release.wait(15), 'source return fixture was not released'
                return result
            monkeypatch.setattr(type(case.context), '_check_guarded', delayed_return)
            child = chat_preparation.run_preparation
            async def observed(name, **kwargs):
                result = await child(name, **kwargs)
                children.append((name, kwargs['owner'].process is not None, result.get('ok')))
                return result
            monkeypatch.setattr(chat_preparation, 'run_preparation', observed)
            caller = asyncio.create_task(case.runtime.prepare_docking_consent(**case.args))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                caller.cancel()
                with pytest.raises(asyncio.CancelledError): await caller
                operation, = case.runtime._consent_preparations
                assert operation.aborted.is_set() and operation.projection_task is None
            finally:
                release.set()
                await case.runtime.close()
            assert children == []
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['cleanup_state'] == 'settled'
            assert not case.calls
    asyncio.run(run())
