"""Closed C wire capture from actual owned fixtures, not live Vina acceptance."""
import asyncio
from copy import copy
import importlib
import json
import threading

import pytest

from tests.agent.test_web_docking_coordinator import Rig
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case, approve
from tests.agent.test_docking_publication_candidate import settle
from tests.agent.test_docking_publication_receipt import publish, checkpoint


def entry():
    name = 'src.web.docking_view'
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail('D6 closed DockingView capture is absent', pytrace=False)
    result = getattr(module, 'capture_docking_view', None)
    assert callable(result), 'D6 closed DockingView capture callable is absent'
    return result


async def capture(coordinator, root, **changes):
    args = dict(root_id=root.root_id, owner_session_id=root.owner,
                origin_turn_id=root.origin, sequence=1, send_generation=1)
    args.update(changes)
    return await entry()(coordinator, **args)


def test_waiting_event_is_exact_detached_and_not_scientific_success(tmp_path):
    entry()
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            event = await capture(coordinator, root)
            assert event == dict(type='docking_event', schema='DockingView@1',
                request_id=root.root_id, root_trace_id=root.root_id, task_id=root.task_id,
                preparation_id=root.preparation_id, origin_turn_id=root.origin,
                revision=0, sequence=1, send_generation=1, phase='awaiting_input',
                segment_complete=True, terminal=False, view_status='AWAITING_INPUT',
                task_status='unavailable', cleanup_status='settled',
                reason_code='source_preparation_required', persistence_pending=False,
                missing_fields=[], preview=None, result=None)
            event['missing_fields'].append('extension')
            assert (await capture(coordinator, root))['missing_fields'] == []
            assert len(rig.calls) == 1 and root.publication is None
            await coordinator.close_root(root_id=root.root_id, owner_session_id=root.owner)
    asyncio.run(run())


@pytest.mark.parametrize('changes', [dict(owner_session_id='foreign'),
    dict(origin_turn_id='other-turn'), dict(sequence=True), dict(sequence=-1),
    dict(sequence=2**53), dict(send_generation=False), dict(send_generation=0),
    dict(send_generation=2**53), dict(root_id='../root')])
def test_invalid_transport_identity_rejects_before_coordinator_work(tmp_path, monkeypatch, changes):
    entry()
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            def forbidden(**kwargs):
                pytest.fail('invalid identity reached coordinator status')
            with monkeypatch.context() as patch:
                patch.setattr(coordinator, 'status', forbidden)
                with pytest.raises(ValueError, match='^docking_view_unavailable$'):
                    await capture(coordinator, root, **changes)
            await coordinator.close_root(root_id=root.root_id, owner_session_id=root.owner)
    asyncio.run(run())


@pytest.mark.parametrize('change', ['root', 'turn', 'revision'])
def test_replacement_during_status_never_attaches_to_later_turn(tmp_path, monkeypatch, change):
    entry()
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            original = coordinator.status
            async def changed(**kwargs):
                view = await original(**kwargs)
                if change == 'root': coordinator._roots[root.root_id] = copy(root)
                elif change == 'turn': root.origin = 'other-turn'
                else: root.accepted_revision += 1
                return view
            with monkeypatch.context() as patch:
                patch.setattr(coordinator, 'status', changed)
                with pytest.raises(ValueError, match='^docking_view_unavailable$'):
                    await capture(coordinator, root)
            coordinator._roots[root.root_id] = root
            root.origin, root.accepted_revision = 'turn-1', 0
            await coordinator.close_root(root_id=root.root_id, owner_session_id=root.owner)
    asyncio.run(run())


def test_ready_preview_contains_no_nonce_and_never_approves(tmp_path, target_case, monkeypatch):
    entry()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            event = await capture(case.coordinator, case.root)
            assert event['phase'] == 'awaiting_consent' and event['view_status'] == 'READY'
            assert event['segment_complete'] is True and event['terminal'] is False
            assert event['result'] is None
            preview = event['preview']
            assert set(preview) == {'source', 'prepared', 'consent'}
            assert preview['consent'] == json.loads(case.root.ready_preview_json)
            assert preview['prepared'] == json.loads(case.root.prepared_preview_json)
            assert preview['source'] == json.loads(case.root.source_preview_json)
            text = json.dumps(event, allow_nan=False)
            assert len(text.encode('utf-8')) <= 64 * 1024
            assert 'approval_nonce' not in text and case.proof['approval_nonce'] not in text
            assert case.raw == [] and case.context._execution is None
            preview['consent']['parameters']['center'].clear()
            assert (await capture(case.coordinator, case.root))['preview']['consent']['parameters']['center']
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['settled', 'success', 'tampered'])
def test_success_requires_existing_verified_publication(tmp_path, target_case, monkeypatch, stage):
    entry()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            if stage != 'settled':
                await publish(case)
            if stage == 'tampered':
                case.root.publication.access_disabled = True
                with pytest.raises(ValueError, match='^docking_view_unavailable$'):
                    await capture(case.coordinator, case.root)
                return
            event = await capture(case.coordinator, case.root)
            if stage == 'settled':
                assert event['phase'] == 'running' and event['result'] is None
                assert event['terminal'] is False and case.root.publication is None
            else:
                assert event['phase'] == 'terminal' and event['terminal'] is True
                assert event['view_status'] == 'SUCCEEDED'
                assert event['preview'] is None and event['missing_fields'] == []
                assert event['result']['best_energy_kcal_mol'] == case.root.publication.outcome.projection.energy_kcal_mol
                assert event['result']['energy_unit'] == 'kcal/mol'
                assert event['result']['pose']['download_path'].endswith('/artifacts/pose')
            assert case.raw == [True]
    asyncio.run(run())


def test_busy_segment_does_not_release_composer(tmp_path, monkeypatch):
    entry()
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            release = asyncio.Event()
            root.task = asyncio.create_task(release.wait())
            try:
                event = await capture(coordinator, root)
                assert event['segment_complete'] is False
                assert event['result'] is None and event['preview'] is None
            finally:
                release.set()
                await root.task
                await coordinator.close_root(root_id=root.root_id, owner_session_id=root.owner)
    asyncio.run(run())


@pytest.mark.parametrize('malformed', [{'approval_nonce': 'private-marker'}, ['private-marker']])
def test_scalar_preview_slot_cannot_smuggle_nested_data(tmp_path, target_case, monkeypatch, malformed):
    entry()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            source = json.loads(case.root.source_preview_json)
            source['pdb_id'] = malformed
            case.root.source_preview_json = json.dumps(source)
            with pytest.raises(ValueError, match='^docking_view_unavailable$'):
                await capture(case.coordinator, case.root)
            assert not case.raw
    asyncio.run(run())


def test_preview_extensions_do_not_become_authority_fields(tmp_path, target_case, monkeypatch):
    entry()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            source = json.loads(case.root.source_preview_json)
            source['private_path'] = 'private-marker'
            source['target']['private_path'] = 'private-marker'
            case.root.source_preview_json = json.dumps(source)
            ready = json.loads(case.root.ready_preview_json)
            ready['approval_nonce'] = 'private-marker'
            ready['receptor']['path'] = 'private-marker'
            case.root.ready_preview_json = json.dumps(ready)
            event = await capture(case.coordinator, case.root)
            assert 'private-marker' not in json.dumps(event)
            assert not case.raw
    asyncio.run(run())


def test_existing_closed_root_can_report_final_cleanup_without_restoring_authority(tmp_path):
    entry()
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            coordinator._interrupt(root, 'closed', 'CLOSED')
            event = await capture(coordinator, root)
            assert root.root_id not in coordinator._roots
            assert event['terminal'] is True and event['view_status'] == 'CANCELLED'
            assert event['segment_complete'] is True
            assert event['cleanup_status'] == 'settled'
            assert event['result'] is None and event['preview'] is None
            with pytest.raises(ValueError, match='^docking_view_unavailable$'):
                await capture(coordinator, root)
    asyncio.run(run())


def test_active_execution_does_not_claim_physical_cleanup_is_settled(tmp_path, target_case, monkeypatch):
    entry()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            entered, release = threading.Event(), threading.Event()
            def blocked():
                entered.set()
                assert release.wait(20), 'test barrier was not released'
            case.raw_hook = blocked
            caller = asyncio.create_task(approve(case, chat_input_context=case.context))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                event = await capture(case.coordinator, case.root)
                assert event['phase'] == 'running' and event['view_status'] == 'ACTIVE'
                assert event['cleanup_status'] == 'pending'
                assert event['result'] is None and event['terminal'] is False
            finally:
                release.set()
                await caller
                operation = case.context._execution
                if operation.settlement is not None:
                    await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
    asyncio.run(run())


def test_final_read_cannot_attach_science_after_revision_change(tmp_path, target_case, monkeypatch):
    entry()
    module = importlib.import_module('src.web.docking_view')
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            await publish(case)
            actual = module.verified_docking_result
            revision = case.root.accepted_revision
            async def changed(*args, **kwargs):
                result = await actual(*args, **kwargs)
                case.root.accepted_revision += 1
                return result
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(module, 'verified_docking_result', changed)
                    with pytest.raises(ValueError, match='^docking_view_unavailable$'):
                        await capture(case.coordinator, case.root)
            finally:
                case.root.accepted_revision = revision
    asyncio.run(run())


def test_lost_suppression_ack_is_visible_as_pending_without_science(tmp_path, target_case, monkeypatch):
    entry()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual_commit = case.rig.tasks._commit_docking_publication
            def commit(conn):
                actual_commit(conn)
                raise OSError('synthetic lost publication acknowledgement')
            actual_suppress = case.rig.tasks._suppress_docking_publication
            calls = []
            def suppress(**kwargs):
                calls.append(True)
                actual_suppress(**kwargs)
                raise OSError('synthetic lost suppression acknowledgement')
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            monkeypatch.setattr(case.rig.tasks, '_suppress_docking_publication', suppress)
            result = await publish(case)
            assert result.status == 'UNKNOWN'
            event = await capture(case.coordinator, case.root)
            assert event['view_status'] == 'UNKNOWN' and event['terminal'] is True
            assert event['cleanup_status'] == 'pending'
            assert event['persistence_pending'] is True
            assert event['result'] is None and event['preview'] is None
            assert calls == [True]
    asyncio.run(run())


def test_canceled_publication_held_in_commit_is_persistence_pending(tmp_path, target_case, monkeypatch):
    entry()
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            await settle(case)
            actual = case.rig.tasks._commit_docking_publication
            entered, release = threading.Event(), threading.Event()
            def commit(conn):
                entered.set()
                assert release.wait(20), 'test publication barrier not released'
                actual(conn)
            monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', commit)
            caller = asyncio.create_task(publish(case))
            try:
                assert await asyncio.to_thread(entered.wait, 10)
                caller.cancel()
                await checkpoint(lambda: case.root.publication.outcome is not None)
                state = case.root.publication
                assert not state.task.done() and state.reconciliation == 'not_needed'
                event = await capture(case.coordinator, case.root)
                assert event['view_status'] == 'CANCELLED' and event['terminal'] is True
                assert event['persistence_pending'] is True
                assert event['cleanup_status'] == 'pending' and event['segment_complete'] is False
                assert event['result'] is None and event['preview'] is None
            finally:
                release.set()
                await asyncio.gather(caller, return_exceptions=True)
                await asyncio.wait_for(asyncio.shield(case.root.publication.task), 10)
            assert case.root.publication.outcome.status == 'CANCELLED'
    asyncio.run(run())


def test_lost_draft_close_ack_is_persistence_pending_without_retry(tmp_path, monkeypatch):
    entry()
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            actual = rig.tasks.close_chat_docking_draft
            calls = []
            def close(**kwargs):
                calls.append(actual(**kwargs))
                raise OSError('synthetic lost draft close acknowledgement')
            monkeypatch.setattr(rig.tasks, 'close_chat_docking_draft', close)
            coordinator._interrupt(root, 'closed', 'CLOSED')
            event = await capture(coordinator, root)
            assert calls == [True] and root.close_state == 'unconfirmed'
            assert event['view_status'] == 'CANCELLED' and event['terminal'] is True
            assert event['persistence_pending'] is True
            assert event['cleanup_status'] == 'pending'
            assert event['result'] is None and event['preview'] is None
            assert (await capture(coordinator, root))['persistence_pending'] is True
            assert calls == [True]
    asyncio.run(run())
