"""Actual C view/physical-send binding; all scientific fixtures are synthetic."""
import asyncio
from copy import copy
import json

import pytest

from src.web import docking_consent
from tests.agent.test_docking_control_feedback import runtime_sender, ResistantSocket, checkpoint
from tests.agent.test_web_docking_coordinator import Rig


def entry(sender):
    method = getattr(sender, 'send_docking_view', None)
    assert callable(method), 'C view capture is not bound to its physical sender'
    return method


def socket_for(owner='owner-1', *, resistant=False):
    socket = ResistantSocket()
    socket.scope = {'agent_session_id': owner}
    if not resistant:
        socket.release_send.set()
    socket.release_close.set()
    return socket


def test_event_and_receipt_use_the_same_actual_send_identity_and_generation(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        send = entry(sender)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            for sequence in (1, 2):
                receipt = await send(coordinator, root_id='root-1')
                assert receipt.delivery == 'delivered'
                event = json.loads(socket.texts[-1])
                assert event['schema'] == 'DockingView@1'
                assert event['request_id'] == receipt.identity.root_id == root.root_id
                assert event['task_id'] == receipt.identity.task_id == root.task_id
                assert event['origin_turn_id'] == receipt.identity.turn_id == root.origin
                assert event['revision'] == receipt.identity.revision == root.accepted_revision
                assert event['sequence'] == receipt.identity.sequence == sequence
                assert event['send_generation'] == receipt.send_generation == sequence
                assert event['segment_complete'] is True and event['result'] is None
            assert root.publication is None and not runtime.docking_operations
            assert not runtime.tasks and not runtime.active_owners and sender.memory == []
            await coordinator.close_root(root_id=root.root_id, owner_session_id=root.owner)
    asyncio.run(run())


@pytest.mark.parametrize('owner,root_id', [('foreign', 'root-1'), ('owner-1', 'missing')])
def test_foreign_and_absent_roots_do_not_capture_or_send(tmp_path, monkeypatch, owner, root_id):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for(owner)
        runtime, sender = runtime_sender(socket)
        send = entry(sender)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            def forbidden(**kwargs):
                pytest.fail('unauthorized view capture')
            with monkeypatch.context() as patch:
                patch.setattr(coordinator, 'status', forbidden)
                result = await send(coordinator, root_id=root_id)
            assert result.delivery == 'undeliverable'
            assert not socket.texts and not runtime.docking_operations and sender.writable
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['revision', 'replacement', 'session', 'scope'])
def test_waiting_send_cannot_adopt_a_changed_root_or_session(tmp_path, mutation):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        send = entry(sender)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            root = coordinator._roots['root-1']
            await sender.lock.acquire()
            pending = asyncio.create_task(send(coordinator, root_id='root-1'))
            try:
                await checkpoint(lambda: bool(runtime.docking_operations))
                if mutation == 'revision': root.accepted_revision += 1
                elif mutation == 'replacement': coordinator._roots['root-1'] = copy(root)
                elif mutation == 'scope': socket.scope = {'agent_session_id': 'foreign'}
                else: socket.scope['agent_session_id'] = 'foreign'
                sender.lock.release()
                assert (await pending).delivery == 'undeliverable'
                assert socket.texts == []
                await checkpoint(lambda: not runtime.docking_operations)
            finally:
                if sender.lock.locked():
                    sender.lock.release()
                coordinator._roots['root-1'] = root
                root.accepted_revision = 0
                socket.scope['agent_session_id'] = 'owner-1'
                await asyncio.gather(pending, return_exceptions=True)
                await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


def test_replaced_socket_scope_is_checked_before_initial_capture(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            socket.scope = {'agent_session_id': 'foreign'}
            result = await entry(sender)(coordinator, root_id='root-1')
            assert result.delivery == 'undeliverable'
            assert not socket.texts and not runtime.docking_operations
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


def test_capture_timeout_cannot_renew_budget_or_send_a_late_event(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        send = entry(sender)
        entered, release = asyncio.Event(), asyncio.Event()
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            original = coordinator.status
            async def held(**kwargs):
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
                return await original(**kwargs)
            monkeypatch.setattr(coordinator, 'status', held)
            monkeypatch.setattr(docking_consent, 'FEEDBACK_SECONDS', .05)
            pending = asyncio.create_task(send(coordinator, root_id='root-1'))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                result = await asyncio.wait_for(pending, .5)
                assert result.delivery == 'undeliverable'
                assert runtime.docking_operations and sender.lock.locked()
                assert socket.events == []
                release.set()
                # Actual status SQL runs after release; event-loop ticks alone
                # cannot establish that its worker has settled.
                await asyncio.wait_for(socket.close_started.wait(), 5)
                await checkpoint(lambda: not runtime.docking_operations)
                assert socket.texts == [] and result.delivery == 'undeliverable'
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


def test_noncooperative_physical_send_keeps_matching_original_event_binding(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for(resistant=True)
        runtime, sender = runtime_sender(socket)
        send = entry(sender)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            monkeypatch.setattr(docking_consent, 'FEEDBACK_SECONDS', .5)
            pending = asyncio.create_task(send(coordinator, root_id='root-1'))
            try:
                await asyncio.wait_for(socket.send_started.wait(), 2)
                pending.cancel()
                receipt = await asyncio.wait_for(pending, .5)
                event = json.loads(socket.texts[0])
                assert receipt.delivery == 'undeliverable'
                assert receipt.send_generation == event['send_generation']
                assert receipt.identity.sequence == event['sequence']
                assert sender.lock.locked() and runtime.docking_operations
                socket.release_send.set()
                await checkpoint(socket.close_started.is_set)
                await checkpoint(lambda: not runtime.docking_operations)
                assert receipt.delivery == 'undeliverable'
            finally:
                socket.release_send.set()
                await asyncio.gather(pending, return_exceptions=True)
                await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


def test_view_send_does_not_exceed_javascript_safe_sequence(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        send = entry(sender)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            sender._docking_generation = 2**53 - 1
            assert (await send(coordinator, root_id='root-1')).delivery == 'undeliverable'
            assert not socket.texts and not runtime.docking_operations
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())


def test_feedback_budget_starts_before_view_identity_capture(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        send = entry(sender)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            now = [100.0]
            monkeypatch.setattr(docking_consent, '_now', lambda: now[0])
            original = coordinator._owned
            def expensive_capture(*args, **kwargs):
                result = original(*args, **kwargs)
                now[0] = 106.0
                return result
            monkeypatch.setattr(coordinator, '_owned', expensive_capture)
            result = await send(coordinator, root_id='root-1')
            assert result.delivery == 'undeliverable'
            assert socket.texts == []
            await coordinator.close_root(root_id='root-1', owner_session_id='owner-1')
    asyncio.run(run())
