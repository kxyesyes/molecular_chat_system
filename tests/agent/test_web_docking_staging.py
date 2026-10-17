"""Next Web/C1 lifecycle seam; synthetic providers and no Vina approval."""
import asyncio
import json
import re
import sqlite3
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.web.docking_coordinator import DockingRequestCoordinator
from tests.task_runtime.test_chat_docking_input_context import authenticated_rig, target_case
from tests.agent.test_web_docking_sources import invalidate_source


def staging_method():
    method = getattr(DockingRequestCoordinator, 'prepare_inputs', None)
    assert callable(method), 'same-root Web/C1 staging lifecycle is absent'
    return method


async def prepare_inputs(case):
    return await case.coordinator.prepare_inputs(root_id='root-1', owner_session_id='owner-session',
        expected_revision=case.root.accepted_revision,
        receptor_ref=case.root.accepted_slots['receptor_ref']['value'],
        ligand_ref=case.root.accepted_slots['ligand_ref']['value'], runtime=case.runtime)


def test_same_root_prepare_discloses_nonce_once_and_ready_uses_consent_ttl(tmp_path, target_case, monkeypatch):
    staging_method()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            view = await prepare_inputs(case)
            assert view['phase'] == 'READY' and view['input_ready'] is True
            assert view['execution_authorized'] is False
            assert view['task_id'] == case.root.task_id
            assert view['preparation_id'] == case.root.preparation_id
            assert re.fullmatch('[0-9a-f]{64}', view['consent_preview']['approval_nonce'])
            duplicate = await prepare_inputs(case)
            assert duplicate['phase'] == 'READY' and 'approval_nonce' not in json.dumps(duplicate)
            case.rig.wall += 181000
            case.rig.mono += 181  # The upstream work budget is over, consent TTL is not.
            status = await case.coordinator.status(root_id='root-1', owner_session_id='owner-session')
            assert status['phase'] == 'READY' and 'approval_nonce' not in json.dumps(status)
            assert case.stages == [case.root.task_id] and not case.calls
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_prepare_captures_context_inside_its_retained_root_task(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch, capture_context=False) as case:
            view = await prepare_inputs(case)
            assert view['phase'] == 'READY', view
            assert case.root.input_context is not None
            assert case.root.task is None
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_final_ready_validation_must_finish_before_duplicate_exposes_ready(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            cls = type(case.context)
            check = cls._check_ready_guarded
            def held(self):
                entered.set()
                assert release.wait(15)
                return check(self)
            monkeypatch.setattr(cls, '_check_ready_guarded', held)
            caller = asyncio.create_task(prepare_inputs(case))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                duplicate = await prepare_inputs(case)
                assert duplicate['input_ready'] is False, duplicate
                assert duplicate['phase'] == 'PREPARING', duplicate
                assert 'approval_nonce' not in json.dumps(duplicate)
            finally:
                release.set()
                await caller
                await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


def test_prepare_rejects_nonconcrete_runtime_without_invoking_it(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            calls = []
            async def forbidden(**kwargs):
                calls.append(True)
                raise AssertionError('untrusted runtime called')
            case.runtime = SimpleNamespace(prepare_docking_consent=forbidden)
            view = await prepare_inputs(case)
            assert view['input_ready'] is False
            assert not calls
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'root', 'owner', 'model', 'wall_expiry', 'mono_expiry',
                                    'runtime_closed', 'runtime_closing', 'runtime_policy'])
def test_ready_refresh_revalidates_source_and_consent_expiry(tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            view = await prepare_inputs(case)
            assert view['phase'] == 'READY'
            if mutation == 'wall_expiry': case.rig.wall += 900001
            elif mutation == 'mono_expiry': case.rig.mono += 900.001
            elif mutation == 'runtime_closed': case.runtime._closed = True
            elif mutation == 'runtime_closing': case.runtime._closing = True
            elif mutation == 'runtime_policy':
                case.runtime._docking_consent_policy = replace(case.rig.policy, runtime_generation='changed-runtime')
            else: invalidate_source(case.rig, mutation)
            view = await case.coordinator.status(root_id='root-1', owner_session_id='owner-session')
            assert view['input_ready'] is False and view['execution_authorized'] is False
            assert 'approval_nonce' not in json.dumps(view)
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['cleanup_state'] == 'settled'
            assert not case.calls
    asyncio.run(run())


def test_close_ready_revokes_nonce_and_settles_exact_existing_stage(tmp_path, target_case, monkeypatch):
    staging_method()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            await prepare_inputs(case)
            result = await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
            assert result['input_ready'] is False and result['execution_authorized'] is False
            assert 'approval_nonce' not in json.dumps(result)
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['approval_nonce_hash'] is None
            assert row['cleanup_state'] == 'settled'
            assert 'root-1' not in case.coordinator._roots
            assert not case.calls
    asyncio.run(run())


def test_close_during_actual_staging_retains_root_until_worker_and_sql_settle(tmp_path, target_case, monkeypatch):
    staging_method()
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            stage = case.stager.stage
            def held(*args, **kwargs):
                result = stage(*args, **kwargs)
                entered.set()
                try:
                    assert release.wait(15), 'staging fixture was not released'
                    return result
                finally:
                    finished.set()
            monkeypatch.setattr(case.stager, 'stage', held)
            caller = asyncio.create_task(prepare_inputs(case))
            closer = None
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                closer = asyncio.create_task(case.coordinator.close_root(
                    root_id='root-1', owner_session_id='owner-session'))
                await asyncio.sleep(0)
                assert 'root-1' in case.coordinator._roots and not finished.is_set()
                assert case.runtime._consent_preparations
            finally:
                release.set()
                await asyncio.gather(caller, return_exceptions=True)
                if closer is not None:
                    await asyncio.wait_for(closer, 8)
            assert finished.is_set()
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
            assert 'root-1' not in case.coordinator._roots
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['cleanup_state'] == 'settled'
            assert not case.calls
    asyncio.run(run())


def test_lost_revocation_acknowledgement_retains_root_without_readback_retry(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            await prepare_inputs(case)
            revoke = case.rig.tasks._revoke_docking_preparation
            calls = []
            def uncertain(*args):
                calls.append(True)
                revoke(*args)
                raise RuntimeError('synthetic lost acknowledgement')
            monkeypatch.setattr(case.rig.tasks, '_revoke_docking_preparation', uncertain)
            case.expected_unresolved = True
            for _ in range(2):
                view = await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
                assert view['cleanup_pending'] is True and view['input_ready'] is False
                assert case.coordinator._roots['root-1'] is case.root
                assert case.root.close_state == 'unconfirmed'
            assert len(calls) == 1
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id)['state'] == 'REVOKED'
            assert case.context._operation.manifest_path.is_file()
    asyncio.run(run())


def test_cancel_during_final_ready_check_never_returns_nonce_or_detaches_sql(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            cls, check = type(case.context), type(case.context)._check_ready_guarded
            def held(self):
                entered.set()
                try:
                    assert release.wait(15)
                    return check(self)
                finally:
                    finished.set()
            monkeypatch.setattr(cls, '_check_ready_guarded', held)
            caller = asyncio.create_task(prepare_inputs(case))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                caller.cancel()
                await asyncio.sleep(0)
                assert 'root-1' in case.coordinator._roots and not finished.is_set()
                assert not caller.done()
                caller.cancel()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            assert finished.is_set()
            assert 'root-1' not in case.coordinator._roots
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['cleanup_state'] == 'settled'
            assert not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('expected_revision', True), ('expected_revision', 999),
    ('receptor_ref', 'sha256:' + '0' * 64), ('ligand_ref', None), ('owner_session_id', 'foreign')])
def test_wrong_prepare_identity_never_consumes_or_stages(tmp_path, target_case, monkeypatch, field, value):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            args = dict(root_id='root-1', owner_session_id='owner-session',
                expected_revision=case.root.accepted_revision,
                receptor_ref=case.root.accepted_slots['receptor_ref']['value'],
                ligand_ref=case.root.accepted_slots['ligand_ref']['value'], runtime=case.runtime)
            args[field] = value
            view = await case.coordinator.prepare_inputs(**args)
            assert view['input_ready'] is False and not case.stages and not case.calls
            assert case.root.staging_started is False and case.context._claimed is False
            await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'model'])
def test_concurrent_refresh_cannot_expose_ready_while_real_sql_guard_waits(tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            await prepare_inputs(case)
            invalidate_source(case.rig, mutation)
            entered = threading.Event()
            cls, check = type(case.context), type(case.context)._check_ready_guarded
            def observed(self):
                entered.set()
                return check(self)
            monkeypatch.setattr(cls, '_check_ready_guarded', observed)
            writer = sqlite3.connect(case.rig.tasks.db_path)
            writer.execute('BEGIN IMMEDIATE')
            caller = asyncio.create_task(case.coordinator.status(root_id='root-1', owner_session_id='owner-session'))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                assert not caller.done()
                for reply in (await case.coordinator.status(root_id='root-1', owner_session_id='owner-session'),
                              await prepare_inputs(case)):
                    assert reply['input_ready'] is False, reply
                    assert reply['phase'] != 'READY', reply
                    assert 'consent_preview' not in reply
            finally:
                writer.rollback()
                writer.close()
                result = await caller
            assert result['input_ready'] is False
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['cleanup_state'] == 'settled'
            assert not case.calls
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['source', 'model'])
def test_first_prepare_cannot_reattach_nonce_during_a_concurrent_ready_refresh(tmp_path, target_case, monkeypatch, mutation):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch) as case:
            completed, resume = asyncio.Event(), asyncio.Event()
            entered = threading.Event()
            original_wait = asyncio.wait
            first, paused = None, False
            async def pause_first_wait(*args, **kwargs):
                nonlocal paused
                result = await original_wait(*args, **kwargs)
                if asyncio.current_task() is first and not paused:
                    paused = True
                    completed.set()
                    await resume.wait()
                return result
            monkeypatch.setattr(asyncio, 'wait', pause_first_wait)
            cls, check = type(case.context), type(case.context)._check_ready_guarded
            def observed(self):
                entered.set()
                return check(self)
            monkeypatch.setattr(cls, '_check_ready_guarded', observed)
            first = asyncio.create_task(prepare_inputs(case))
            writer = refresh = None
            try:
                await asyncio.wait_for(completed.wait(), 10)
                entered.clear()
                invalidate_source(case.rig, mutation)
                writer = sqlite3.connect(case.rig.tasks.db_path)
                writer.execute('BEGIN IMMEDIATE')
                refresh = asyncio.create_task(case.coordinator.status(root_id='root-1', owner_session_id='owner-session'))
                assert await asyncio.to_thread(entered.wait, 8)
                assert not refresh.done()
                resume.set()
                reply = await first
                assert reply['input_ready'] is False and reply['phase'] == 'BUSY'
                assert 'approval_nonce' not in json.dumps(reply), 'nonce restored during pending current validation'
            finally:
                resume.set()
                if writer is not None:
                    writer.rollback()
                    writer.close()
                await asyncio.gather(first, *([refresh] if refresh else []), return_exceptions=True)
                await case.coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] == 'REVOKED' and row['cleanup_state'] == 'settled'
            assert not case.calls
    asyncio.run(run())
