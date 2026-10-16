"""Coordinator execution lifecycle over the isolated synthetic C2 fixture."""
import asyncio
import sqlite3
import threading

import pytest

from src.web.docking_coordinator import DockingRequestCoordinator
from tests.task_runtime.test_chat_docking_execution_context import execution_case, approve, target_case


def test_web_approval_lifecycle_seam_is_present():
    assert callable(getattr(DockingRequestCoordinator, 'approve_inputs', None)), 'owned approval lifecycle is absent'


async def web_approve(case, **overrides):
    values = dict(root_id=case.root.root_id, owner_session_id=case.root.owner,
        approval_nonce=case.proof['approval_nonce'], binding_digest=case.proof['binding_digest'],
        confirm=True)
    values.update(overrides)
    return await case.coordinator.approve_inputs(**values)


def test_owned_duplicate_approval_executes_once_and_waits_for_publication(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await asyncio.gather(web_approve(case), web_approve(case))
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            view = await case.coordinator.status(root_id=case.root.root_id, owner_session_id=case.root.owner)
            assert case.raw == [True] and operation.status == 'SUCCEEDED'
            assert view['phase'] == 'RESULT_PENDING'
            assert view['execution_authorized'] is True and view['input_ready'] is False
            assert 'binding_energy' not in str(view) and case.proof['approval_nonce'] not in str(view)
            await web_approve(case)
            assert case.raw == [True]
    asyncio.run(run())


@pytest.mark.parametrize('overrides', [dict(owner_session_id='foreign'),
    dict(root_id='absent'), dict(confirm=False), dict(approval_nonce='0' * 64)])
def test_invalid_web_approval_cannot_acquire_execution(tmp_path, target_case, monkeypatch, overrides):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await web_approve(case, **overrides)
            assert case.context._execution is None and not case.raw
            assert case.rig.tasks.get_docking_consent(case.root.preparation_id)['state'] == 'READY'
    asyncio.run(run())


def test_status_of_running_execution_does_not_revalidate_consumed_ready(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            def held_raw():
                entered.set()
                assert release.wait(15)
            case.raw_hook = held_raw
            try:
                await web_approve(case)
                assert await asyncio.to_thread(entered.wait, 8)
                view = await case.coordinator.status(root_id=case.root.root_id, owner_session_id=case.root.owner)
                assert view['phase'] == 'EXECUTING' and view['execution_authorized'] is True
                assert not case.root.closed and not case.context._execution.signal.is_set()
                assert 'consent_preview' not in view
            finally:
                release.set()
    asyncio.run(run())


def test_close_claimed_root_cancels_exact_c2_owner_before_draining(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            def held_raw():
                entered.set()
                assert release.wait(15), 'synthetic raw release was not signaled'
            case.raw_hook = held_raw
            closing = None
            try:
                await approve(case, chat_input_context=case.context)
                assert await asyncio.to_thread(entered.wait, 8)
                operation = case.context._execution
                closing = asyncio.create_task(case.coordinator.close_root(
                    root_id=case.root.root_id, owner_session_id=case.root.owner))
                await asyncio.wait({closing}, timeout=0.2)
                assert operation.signal.is_set(), 'root close did not signal the actual C2 execution'
                assert not operation.settled and not closing.done()
                assert case.coordinator._roots[case.root.root_id] is case.root
            finally:
                release.set()
                if closing is not None:
                    await closing
                case.unresolved = bool(case.runtime._consent_preparations)
            assert operation.settled and operation.status == 'CANCELLED'
            assert case.root.root_id not in case.coordinator._roots
    asyncio.run(run())


def test_close_during_claim_keeps_actual_sql_and_model_owner(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered = threading.Event()
            original = case.rig.tasks._claim_docking_consent
            def claim(*args, **kwargs):
                entered.set()
                return original(*args, **kwargs)
            monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            blocker = sqlite3.connect(case.rig.tasks.db_path, timeout=0)
            blocker.execute('BEGIN IMMEDIATE')
            pending = asyncio.create_task(web_approve(case))
            closing = None
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                operation = case.context._execution
                closing = asyncio.create_task(case.coordinator.close_root(
                    root_id=case.root.root_id, owner_session_id=case.root.owner))
                await asyncio.wait({closing}, timeout=0.2)
                assert operation.signal.is_set() and not closing.done()
                assert case.rig.gate._readers == 1 and not operation.admission.done()
                assert case.coordinator._roots[case.root.root_id] is case.root
            finally:
                blocker.rollback()
                blocker.close()
                await asyncio.gather(pending, return_exceptions=True)
                if closing is not None:
                    await closing
            assert case.rig.gate._readers == 0 and not case.raw
            assert operation.settled and case.root.close_state == 'confirmed'
    asyncio.run(run())


def test_post_claim_unknown_cannot_release_the_root_owner(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            original = case.rig.tasks._claim_docking_consent
            def claim(*args, **kwargs):
                acquired = original(*args, **kwargs)
                case.rig.context['model_generation'] = 'after-claim-invalidated'
                return acquired
            monkeypatch.setattr(case.rig.tasks, '_claim_docking_consent', claim)
            case.unresolved = True
            view = await web_approve(case)
            operation = case.context._execution
            assert view['cleanup_pending'] is True
            assert operation.acquired and operation.status == 'UNKNOWN' and not operation.settled
            assert case.coordinator._roots[case.root.root_id] is case.root
            assert not case.runtime._consent_preparations and not case.raw
            assert case.root.close_state == 'unconfirmed'
    asyncio.run(run())


def test_execution_status_rechecks_live_source_and_signals_before_drain(tmp_path, target_case, monkeypatch):
    from tests.agent.test_web_docking_sources import invalidate_source
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            def held_raw():
                entered.set()
                assert release.wait(15)
            case.raw_hook = held_raw
            pending = None
            try:
                await web_approve(case)
                assert await asyncio.to_thread(entered.wait, 8)
                operation = case.context._execution
                invalidate_source(case.rig, 'source')
                pending = asyncio.create_task(case.coordinator.status(
                    root_id=case.root.root_id, owner_session_id=case.root.owner))
                await asyncio.wait({pending}, timeout=0.2)
                assert operation.signal.is_set(), 'status did not invalidate the live execution'
                assert not operation.settled
            finally:
                release.set()
                if pending is not None:
                    await pending
            assert operation.status != 'SUCCEEDED'
    asyncio.run(run())


def test_slow_status_keeps_physical_read_owned_after_feedback_timeout(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await web_approve(case)
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            entered, release = threading.Event(), threading.Event()
            original = case.rig.tasks._owned_docking_consent
            def read(**kwargs):
                entered.set()
                assert release.wait(20), 'status read barrier was not released'
                return original(**kwargs)
            monkeypatch.setattr(case.rig.tasks, '_owned_docking_consent', read)
            closing = None
            pending = asyncio.create_task(case.coordinator.status(
                root_id=case.root.root_id, owner_session_id=case.root.owner))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                await asyncio.wait_for(asyncio.shield(pending), 7)
                assert not case.root.task.done(), 'feedback timeout detached the physical read'
                closing = asyncio.create_task(case.coordinator.close_root(
                    root_id=case.root.root_id, owner_session_id=case.root.owner))
                await asyncio.wait({closing}, timeout=0.2)
                assert not closing.done() and case.coordinator._roots[case.root.root_id] is case.root
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                if closing is not None:
                    await closing
            assert case.root.root_id not in case.coordinator._roots
    asyncio.run(run())
