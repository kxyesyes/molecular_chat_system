"""Coordinator close must retain and confirm the real waiting-draft CAS."""
import asyncio
import threading

import pytest

from tests.agent.test_web_docking_coordinator import Rig
from tests.agent.test_web_docking_consent import target_case


def test_explicit_close_releases_matching_waiting_draft_owner(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            before = await rig.begin(coordinator)
            original = rig.rows()[0]
            closed = await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert closed['phase'] == 'CLOSED'
            assert not closed.get('cleanup_pending', False)
            row = rig.rows()[0]
            assert row['state'] == 'REVOKED', 'local close did not revoke the waiting draft'
            assert row['version'] == original['version'] + 1
            assert row['expires_at_ms'] == original['expires_at_ms']
            assert row['cleanup_state'] == 'settled'
            assert 'root-1' not in coordinator._roots
            rig.start(root='root-2')
            other = await coordinator.begin(**{**rig.args, 'root_id': 'root-2', 'origin_turn_id': 'turn-2'})
            assert other['phase'] == 'AWAITING_INPUT'
            assert other['task_id'] != before['task_id']
            await coordinator.close_root(root_id='root-2', owner_session_id='owner-1')
    asyncio.run(run())


def test_invalidation_finalizer_also_closes_the_current_waiting_draft(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            rig.context['model_generation'] = 'replacement-model'
            result = await coordinator.status(root_id='root-1', owner_session_id='owner-1')
            assert result['phase'] == 'UNAVAILABLE'
            # status may expose cleanup pending while the retained finalizer
            # owns SQL; explicit close observes its actual committed completion.
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert rig.rows()[0]['state'] == 'REVOKED', 'invalidation dropped the context but left owner busy'
    asyncio.run(run())


def test_close_repeated_cancellation_keeps_actual_sql_and_root_until_receipt(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    entered, release, finished = (threading.Event() for _ in range(3))
    actual = rig.tasks.close_chat_docking_draft
    def held(**kwargs):
        entered.set()
        try:
            assert release.wait(10)
            return actual(**kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(rig.tasks, 'close_chat_docking_draft', held)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            closer = asyncio.create_task(coordinator.close_root(root_id='root-1', owner_session_id='owner-1'))
            try:
                for _ in range(200):
                    if entered.is_set(): break
                    await asyncio.sleep(.005)
                assert entered.is_set(), 'close never acquired its real SQL owner'
                closer.cancel()
                await asyncio.sleep(.01)
                closer.cancel()
                await asyncio.sleep(.01)
                assert not closer.done() and not finished.is_set()
                assert 'root-1' in coordinator._roots
                assert rig.rows()[0]['state'] == 'AWAITING_INPUT'
            finally:
                release.set()
                await asyncio.gather(closer, return_exceptions=True)
            assert closer.cancelled()
            assert finished.is_set()
            assert rig.rows()[0]['state'] == 'REVOKED'
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert 'root-1' not in coordinator._roots
    asyncio.run(run())


def test_uncertain_close_is_not_recovered_by_readback_or_retried(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    actual = rig.tasks.close_chat_docking_draft
    calls = []
    def uncertain(**kwargs):
        actual(**kwargs)
        calls.append(1)
        raise OSError('synthetic lost commit receipt')
    monkeypatch.setattr(rig.tasks, 'close_chat_docking_draft', uncertain)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            first = await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert first.get('cleanup_pending') is True, 'uncertain close was reported as settled'
            assert 'root-1' in coordinator._roots
            second = await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert second.get('cleanup_pending') is True
            assert calls == [1]
            assert rig.rows()[0]['state'] == 'REVOKED'  # Test observation, never a recovery receipt.
    asyncio.run(run())


def test_concurrent_close_uses_one_retained_cas(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    actual = rig.tasks.close_chat_docking_draft
    entered, release = threading.Event(), threading.Event()
    calls = []
    def held(**kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return actual(**kwargs)
    monkeypatch.setattr(rig.tasks, 'close_chat_docking_draft', held)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            first = asyncio.create_task(coordinator.close_root(root_id='root-1', owner_session_id='owner-1'))
            second = None
            try:
                for _ in range(400):
                    if entered.is_set(): break
                    await asyncio.sleep(.005)
                assert entered.is_set(), 'no retained close operation'
                second = asyncio.create_task(coordinator.close_root(root_id='root-1', owner_session_id='owner-1'))
                await asyncio.sleep(.02)
                assert not second.done() and calls == [1]
            finally:
                release.set()
                results = await asyncio.gather(*(task for task in (first, second) if task is not None))
            assert all(not result.get('cleanup_pending', False) for result in results)
            assert rig.rows()[0]['state'] == 'REVOKED' and calls == [1]
    asyncio.run(run())


def test_uncertain_initial_draft_receipt_retains_context_without_close_readback(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    actual = rig.tasks.reserve_chat_docking_draft
    def uncertain(**kwargs):
        actual(**kwargs)
        raise OSError('synthetic lost initial receipt')
    monkeypatch.setattr(rig.tasks, 'reserve_chat_docking_draft', uncertain)
    def forbidden(**kwargs):
        pytest.fail('unconfirmed draft must not acquire close authority by readback')
    monkeypatch.setattr(rig.tasks, 'get_chat_docking_draft', forbidden)
    monkeypatch.setattr(rig.tasks, 'close_chat_docking_draft', forbidden)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            before = await rig.begin(coordinator)
            assert before['phase'] == 'UNAVAILABLE'
            assert before.get('cleanup_pending') is True
            assert 'root-1' in coordinator._roots
            result = await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
            assert result.get('cleanup_pending') is True
            assert rig.rows()[0]['state'] == 'AWAITING_INPUT'
    asyncio.run(run())


def test_no_waiting_draft_owner_release_until_actual_source_close_settles(tmp_path, target_case, monkeypatch):
    from src.docking import chat_preparation, source_preparation
    from tests.agent.test_web_docking_sources import source_rig
    from tests.test_docking_source_preparation import _fixture_pipeline_child
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, '_source_candidate', _fixture_pipeline_child)
    original = chat_preparation.IsolatedProcess
    entered, release, finished = (threading.Event() for _ in range(3))
    class HeldClose(original):
        def close(self):
            super().close()
            entered.set()
            try:
                assert release.wait(20)
            finally:
                finished.set()
    monkeypatch.setattr(chat_preparation, 'IsolatedProcess', HeldClose)
    monkeypatch.setattr(chat_preparation, 'CHILD_MAX_SECONDS', 5)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            first = asyncio.create_task(rig.begin(coordinator, structure_context=pointer))
            try:
                for _ in range(1200):
                    if entered.is_set(): break
                    await asyncio.sleep(.005)
                assert entered.is_set()
                result = await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
                assert result.get('cleanup_pending') is True
                assert not finished.is_set() and 'root-1' in coordinator._roots
                assert rig.rows()[0]['state'] == 'AWAITING_INPUT'
            finally:
                release.set()
                await asyncio.gather(first, return_exceptions=True)
            for _ in range(400):
                result = await coordinator.close_root(root_id='root-1', owner_session_id='owner-session')
                if not result.get('cleanup_pending', False): break
                await asyncio.sleep(.005)
            assert finished.is_set()
            assert rig.rows()[0]['state'] == 'REVOKED'
            assert 'root-1' not in coordinator._roots
    asyncio.run(run())


def test_closed_status_reports_actual_claim_sql_still_pending(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            actual = coordinator._claim_root
            def held(**kwargs):
                entered.set()
                assert release.wait(10)
                return actual(**kwargs)
            monkeypatch.setattr(coordinator, '_claim_root', held)
            first = asyncio.create_task(rig.begin(coordinator))
            close = None
            try:
                for _ in range(400):
                    if entered.is_set(): break
                    await asyncio.sleep(.005)
                assert entered.is_set()
                close = asyncio.create_task(coordinator.close_root(root_id='root-1', owner_session_id='owner-1'))
                await asyncio.sleep(.02)
                result = await coordinator.status(root_id='root-1', owner_session_id='owner-1')
                assert result.get('cleanup_pending') is True
                assert not first.done() and not close.done()
            finally:
                release.set()
                await asyncio.gather(*(task for task in (first, close) if task is not None), return_exceptions=True)
    asyncio.run(run())


def test_model_cancel_uses_confirmed_budget_receipt_for_draft_close(tmp_path):
    import httpx
    from tests.agent.test_web_docking_coordinator import wire
    rig = Rig(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            first = asyncio.create_task(rig.begin(coordinator))
            close = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert rig.rows()[0]['version'] == 2  # Actual acknowledged budget CAS precedes model dispatch.
                close = asyncio.create_task(coordinator.close_root(root_id='root-1', owner_session_id='owner-1'))
                await asyncio.sleep(.02)
                assert not close.done()
            finally:
                release.set()
                await asyncio.gather(*(task for task in (first, close) if task is not None), return_exceptions=True)
            assert rig.rows()[0]['state'] == 'REVOKED'
            assert not close.result().get('cleanup_pending', False)
            assert 'root-1' not in coordinator._roots
    asyncio.run(run())
