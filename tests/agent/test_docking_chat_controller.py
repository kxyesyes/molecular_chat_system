"""Shared chat C admission; real stores/coordinator, synthetic provider only."""
import asyncio
import importlib
import json

import pytest

from tests.agent.test_docking_control_feedback import runtime_sender
from tests.agent.test_docking_view_delivery import socket_for
from tests.agent.test_web_docking_coordinator import Rig, QUERY, proposal
from tests.agent.test_web_docking_refinement import response
from tests.agent.test_web_docking_consent import target_case


def controller(runtime, coordinator, **kwargs):
    try:
        module = importlib.import_module('src.web.docking_chat_controller')
    except ModuleNotFoundError as exc:
        if exc.name != 'src.web.docking_chat_controller':
            raise
        pytest.fail('shared C chat controller is absent', pytrace=False)
    runtime.store = coordinator._state
    return module.DockingChatController(runtime, coordinator, **kwargs)


async def settle(runtime):
    while runtime.tasks:
        await asyncio.wait_for(asyncio.gather(*tuple(runtime.tasks)), 10)


def frames(socket):
    return [json.loads(item) for item in socket.texts]


@pytest.mark.parametrize('enabled,message,tools', [
    (False, QUERY, True), (True, QUERY, False), (True, '你好', True),
    (True, '不要对接，只查结构。', True), (True, QUERY + ' 然后预测活性', True),
])
def test_non_c_requests_remain_unconsumed(tmp_path, enabled, message, tools):
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=enabled)
            assert await c.handle(sender, dict(type='chat', message=message, enable_tools=tools)) is False
            assert not rig.calls and not runtime.tasks and not sender.websocket.texts
    asyncio.run(run())


def test_original_chat_creates_one_owned_root_with_exact_source_and_no_execution(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            history = [dict(role='user', content='原始历史'), dict(role='assistant', content='原始答复')]
            assert await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True), history=history)
            history[0]['content'] = 'changed after admission'
            await settle(runtime)
            accepted = next(frame for frame in frames(socket) if frame['type'] == 'request_accepted')
            root = coordinator._owned(accepted['trace_id'], 'owner-1')
            assert root is not None and root.origin == accepted['turn_id']
            source = json.loads(next(message['content'] for message in
                json.loads(rig.calls[0].content)['messages'] if message['role'] == 'user'))
            assert source['query'] == QUERY and source['history'][0]['content'] == '原始历史'
            views = [frame for frame in frames(socket) if frame['type'] == 'docking_event']
            assert views and views[-1]['request_id'] == root.root_id
            assert views[-1]['segment_complete'] and views[-1]['view_status'] == 'AWAITING_INPUT'
            assert root.input_context is None and root.publication is None and sender.memory == []
            count = len(rig.calls)
            assert await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await settle(runtime)
            assert len(rig.calls) == count
            assert len(coordinator._roots) == 1
            rejection = frames(socket)[-1]
            assert rejection['code'] == 'docking_request_active'
            assert rejection['docking_request_rejected'] is True
            await c.disconnect(sender)
            await settle(runtime)
            assert not coordinator._roots
    asyncio.run(run())


def test_refinement_uses_same_original_root_and_issuer(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        socket = socket_for()
        runtime, sender = runtime_sender(socket)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            rig.output = proposal('run docking')
            await c.handle(sender, dict(type='chat', message='run docking', enable_tools=True))
            await settle(runtime)
            root = next(iter(coordinator._roots.values()))
            issuer, task_id, snapshot = root.issuer, root.task_id, root.snapshot
            rig.output = response('num_modes=5', {'num_modes': 5})
            await c.handle(sender, dict(type='docking_refine', request_id=root.root_id,
                revision=0, message='num_modes=5'))
            await settle(runtime)
            assert root.accepted_revision == 1 and root.accepted_slots['num_modes']['value'] == 5
            assert root.issuer is issuer and root.snapshot is snapshot and root.task_id == task_id
            assert len(rig.calls) == 2
            await c.disconnect(sender)
            await settle(runtime)
    asyncio.run(run())


@pytest.mark.parametrize('foreign', [False, True])
def test_watch_is_read_only_and_never_admits_refinement_from_new_attachment(tmp_path, foreign):
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        other_socket = socket_for('foreign' if foreign else 'owner-1')
        _, watcher = runtime_sender(other_socket)
        watcher._runtime = runtime
        runtime.sockets.add(watcher)
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await settle(runtime)
            root = next(iter(coordinator._roots.values()))
            calls = len(rig.calls)
            await c.handle(watcher, dict(type='docking_watch', task_id=root.task_id))
            await settle(runtime)
            events = [frame for frame in frames(other_socket) if frame['type'] == 'docking_event']
            assert bool(events) is not foreign
            await c.handle(watcher, dict(type='docking_refine', request_id=root.root_id,
                revision=0, message='num_modes=5'))
            await settle(runtime)
            assert len(rig.calls) == calls and not root.closed
            await c.disconnect(watcher)
            await settle(runtime)
            assert not root.closed
            await c.disconnect(sender)
            await settle(runtime)
    asyncio.run(run())


def test_lost_original_run_receipt_never_admits_model_or_retries_root(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            calls = []
            original = rig.state.start_run
            def lost(run, **kwargs):
                calls.append(run['trace_id'])
                original(run, **kwargs)
                raise OSError('synthetic lost original run receipt')
            monkeypatch.setattr(rig.state, 'start_run', lost)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await settle(runtime)
            assert len(calls) == 1 and not rig.calls and not coordinator._roots
            assert not sender.memory
            error = next(frame for frame in frames(sender.websocket) if frame.get('code') == 'docking_unavailable')
            attachment = sender._chat_docking
            assert error['trace_id'] == attachment.root_id and error['turn_id'] == attachment.origin
            assert error['docking_segment_complete'] is True and 'docking_request_rejected' not in error
            accepted = next(frame for frame in frames(sender.websocket) if frame['type'] == 'request_accepted')
            failure = frames(sender.websocket)[-1]
            assert failure['docking_segment_complete'] is True
            assert failure['turn_id'] == accepted['turn_id']
            assert failure['trace_id'] == accepted['trace_id']
            assert 'docking_request_rejected' not in failure
    asyncio.run(run())


def test_confirmed_failed_begin_closes_original_card_without_reconstructing_root(tmp_path):
    import httpx
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async def unavailable(request):
            return httpx.Response(503, json={'error': 'synthetic unavailable'})
        async with rig.client(unavailable) as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await settle(runtime)
            events = [frame for frame in frames(sender.websocket) if frame['type'] == 'docking_event']
            assert events[-1]['terminal'] is True
            assert events[-1]['view_status'] == 'UNKNOWN'
            assert events[-1]['segment_complete'] and events[-1]['cleanup_status'] == 'settled'
            assert events[-1]['preview'] is None and events[-1]['result'] is None
            assert not coordinator._roots and not sender.memory
            assert events[0]['request_id'] == events[-1]['request_id']
            assert events[-1]['send_generation'] == events[-1]['sequence'] > events[0]['sequence']
    asyncio.run(run())


def test_disconnect_during_provider_call_retains_worker_and_no_late_delivery(tmp_path):
    import httpx
    from tests.agent.test_web_docking_coordinator import wire
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(request):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(held) as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                before = list(sender.websocket.texts)
                await c.disconnect(sender)
                await asyncio.sleep(.02)
                assert runtime.tasks and rig.gate._readers == 1
                assert sender.websocket.texts == before
            finally:
                release.set()
                await settle(runtime)
            assert rig.gate._readers == 0 and not coordinator._roots
            assert sender.websocket.texts == before and sender.memory == []
    asyncio.run(run())


def test_changed_actual_socket_owner_cannot_refine_or_watch_original_attachment(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await settle(runtime)
            root = next(iter(coordinator._roots.values()))
            before = len(rig.calls)
            sender.websocket.scope = {'agent_session_id': 'foreign'}
            await c.handle(sender, dict(type='docking_refine', request_id=root.root_id,
                revision=0, message='num_modes=5'))
            await c.handle(sender, dict(type='docking_watch', task_id=root.task_id))
            await settle(runtime)
            assert len(rig.calls) == before and not root.closed
            sender.websocket.scope = {'agent_session_id': 'owner-1'}
            await c.disconnect(sender)
            await settle(runtime)
    asyncio.run(run())


@pytest.mark.parametrize('owner,accepted', [('owner-session', True), ('foreign', False)])
def test_source_pointer_comes_only_from_actual_owned_accepted_result(tmp_path, target_case, owner, accepted):
    rig = Rig(tmp_path / 'rig')
    rig.state = target_case.store
    async def run():
        runtime, sender = runtime_sender(socket_for(owner))
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            method = getattr(c, 'accept_result', None)
            assert callable(method), 'accepted S03 result hook is absent'
            assert await method(sender, target_case.agent) is accepted
            pointer = getattr(sender, '_docking_structure', None)
            if accepted:
                assert pointer['trace_id'] == 'target-trace'
                assert pointer['session_id'] == owner and pointer['observation_id'] == 'target-observation'
                assert not {'data', 'score', 'download_url'} & set(pointer)
            else:
                assert pointer is None
            assert sender.memory == [] and not coordinator._roots and not rig.calls
    asyncio.run(run())


def test_busy_state_tracks_only_actual_retained_attachment_work(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            busy = getattr(c, 'busy', None)
            assert callable(busy), 'shared receiver busy guard is absent'
            assert busy(sender) is False
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            assert busy(sender) is True
            await settle(runtime)
            assert busy(sender) is False
            await c.disconnect(sender)
            await settle(runtime)
    asyncio.run(run())


@pytest.mark.parametrize('read_only', [False, True])
def test_only_original_live_attachment_publishes_settled_result_and_records_history_once(
        tmp_path, target_case, monkeypatch, read_only):
    from tests.task_runtime.test_chat_docking_execution_context import execution_case
    from tests.agent.test_docking_publication_candidate import settle as settle_execution
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            runtime, sender = runtime_sender(socket_for('owner-session'))
            c = controller(runtime, case.coordinator, enabled=True)
            module = importlib.import_module('src.web.docking_chat_controller')
            root = case.root
            sender._chat_docking = module._Attachment(root.owner, root.root_id, root.origin,
                read_only=read_only, root=root, identity=(root.task_id, root.preparation_id))
            await settle_execution(case)  # Explicit synthetic test approval, NOT real Vina.
            assert root.publication is None
            await c.handle(sender, dict(type='docking_watch', task_id=root.task_id))
            await settle(runtime)
            events = [frame for frame in frames(sender.websocket) if frame['type'] == 'docking_event']
            assert events
            if read_only:
                assert root.publication is None and events[-1]['result'] is None
                assert not getattr(sender, '_docking_history', ())
            else:
                assert events[-1]['view_status'] == 'SUCCEEDED'
                assert events[-1]['result']['best_energy_kcal_mol'] == -7.2
                history = sender._docking_history
                assert len(history) == 1 and history[0] == dict(outcome='SUCCEEDED',
                    root_id=root.root_id, task_id=root.task_id,
                    completion_digest=root.publication.candidate.completion_digest)
                await c.handle(sender, dict(type='docking_watch', task_id=root.task_id))
                await settle(runtime)
                assert sender._docking_history == history
                case.rig.output = proposal('run docking')
                await c.handle(sender, dict(type='chat', message='run docking', enable_tools=True))
                await settle(runtime)
                assert sender._chat_docking.root_id != root.root_id
                assert sender._docking_history == history
                await c.disconnect(sender)
                await settle(runtime)
                assert not sender._docking_history
            assert sender.memory == [] and len(case.raw) == 1
    asyncio.run(run())


def test_released_failure_rechecks_socket_owner_after_waiting_for_send_lock(tmp_path):
    import httpx
    from tests.agent.test_docking_control_feedback import checkpoint
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async def unavailable(request):
            return httpx.Response(503, json={'error': 'synthetic unavailable'})
        async with rig.client(unavailable) as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await settle(runtime)
            before = list(sender.websocket.texts)
            await sender.lock.acquire()
            pending = asyncio.create_task(c._released_failure(sender, sender._chat_docking))
            try:
                await checkpoint(lambda: bool(runtime.docking_operations))
                sender.websocket.scope = {'agent_session_id': 'foreign'}
                sender.lock.release()
                assert await pending is False
                assert sender.websocket.texts == before
            finally:
                if sender.lock.locked():
                    sender.lock.release()
                await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('change', ['none', 'scope', 'owner', 'attachment', 'owner_restored', 'attachment_restored'])
def test_queued_acceptance_rechecks_actual_attachment(tmp_path, change):
    from tests.agent.test_docking_control_feedback import checkpoint
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            await sender.lock.acquire()
            pending = asyncio.create_task(c.handle(sender,
                dict(type='chat', message=QUERY, enable_tools=True)))
            try:
                await checkpoint(lambda: bool(sender.lock._waiters))
                if change == 'scope':
                    sender.websocket.scope = {'agent_session_id': 'foreign'}
                elif change in {'owner', 'owner_restored'}:
                    sender.websocket.scope['agent_session_id'] = 'foreign'
                elif change in {'attachment', 'attachment_restored'}:
                    attachment = sender._chat_docking
                    sender._chat_docking = None
            finally:
                sender.lock.release()
                if change == 'owner_restored':
                    asyncio.get_running_loop().call_soon(
                        sender.websocket.scope.__setitem__, 'agent_session_id', 'owner-1')
                elif change == 'attachment_restored':
                    asyncio.get_running_loop().call_soon(setattr, sender, '_chat_docking', attachment)
                await pending
                await settle(runtime)
            if change == 'none':
                assert frames(sender.websocket)[0]['type'] == 'request_accepted'
                assert len(rig.calls) == 1
                await c.disconnect(sender)
                await settle(runtime)
            else:
                assert not sender.websocket.texts and not rig.calls
            assert not c.coordinator._roots
    asyncio.run(run())


@pytest.mark.parametrize('change', ['none', 'scope', 'owner', 'attachment'])
def test_queued_correlated_failure_rechecks_actual_attachment(tmp_path, change):
    from tests.agent.test_docking_control_feedback import checkpoint
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        entered, release = asyncio.Event(), asyncio.Event()
        async def failed_refresh():
            entered.set()
            await release.wait()
            raise RuntimeError('synthetic refresh failure')
        runtime.application._refresh_llm_config_from_env = failed_refresh
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await asyncio.wait_for(entered.wait(), 2)
            attachment = sender._chat_docking
            before = list(sender.websocket.texts)
            await sender.lock.acquire()
            try:
                release.set()
                await checkpoint(lambda: bool(sender.lock._waiters))
                if change == 'scope':
                    sender.websocket.scope = {'agent_session_id': 'foreign'}
                elif change == 'owner':
                    sender.websocket.scope['agent_session_id'] = 'foreign'
                elif change == 'attachment':
                    sender._chat_docking = None
            finally:
                sender.lock.release()
                await settle(runtime)
            if change == 'none':
                error = frames(sender.websocket)[-1]
                assert error['type'] == 'error' and error['docking_segment_complete'] is True
                assert error['trace_id'] == attachment.root_id and error['turn_id'] == attachment.origin
            else:
                assert sender.websocket.texts == before
            assert not rig.calls and not c.coordinator._roots
    asyncio.run(run())


def test_conflicting_fixed_control_during_busy_refinement_closes_original_root(tmp_path):
    import httpx
    from tests.agent.test_web_docking_coordinator import wire
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(request):
            rig.calls.append(request)
            if len(rig.calls) == 2:
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(held) as client:
            coordinator = rig.coordinator(client)
            c = controller(runtime, coordinator, enabled=True)
            rig.output = proposal('run docking; exhaustiveness=8')
            await c.handle(sender, dict(type='chat', message='run docking; exhaustiveness=8', enable_tools=True))
            await settle(runtime)
            root = sender._chat_docking.root
            rig.output = response('num_modes=5', {'num_modes': 5})
            await c.handle(sender, dict(type='docking_refine', request_id=root.root_id, revision=0, message='num_modes=5'))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await c.handle(sender, dict(type='docking_refine', request_id=root.root_id,
                    revision=0, message='exhaustiveness=9'))
                assert root.closed and c.busy(sender) and rig.gate._readers == 1
            finally:
                release.set()
                await settle(runtime)
            assert root.accepted_revision == 0 and len(rig.calls) == 2
            events = [frame for frame in frames(sender.websocket) if frame['type'] == 'docking_event']
            assert events[-1]['terminal'] and events[-1]['segment_complete']
    asyncio.run(run())


@pytest.mark.parametrize('failure_kind,reconciled', [('commit', True), ('commit', False), ('deadline', True)])
def test_new_explicit_request_after_publication_failure_requires_confirmed_cleanup(
        tmp_path, target_case, monkeypatch, failure_kind, reconciled):
    from tests.task_runtime.test_chat_docking_execution_context import execution_case
    from tests.agent.test_docking_publication_candidate import settle as settle_execution
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            runtime, sender = runtime_sender(socket_for('owner-session'))
            c = controller(runtime, case.coordinator, enabled=True)
            module = importlib.import_module('src.web.docking_chat_controller')
            root = case.root
            sender._chat_docking = module._Attachment(root.owner, root.root_id, root.origin,
                root=root, identity=(root.task_id, root.preparation_id))
            await settle_execution(case)  # Synthetic executor, never real science.
            actual = case.rig.tasks._commit_docking_publication
            def lost_receipt(conn):
                actual(conn)
                raise OSError('synthetic lost acknowledgement')
            if failure_kind == 'commit':
                monkeypatch.setattr(case.rig.tasks, '_commit_docking_publication', lost_receipt)
            else:
                row = case.context._execution.row
                case.rig.wall = row['operation_deadline_ms']
                case.rig.mono = row['operation_monotonic_expires']
            if not reconciled:
                def unavailable(**kwargs):
                    raise OSError('synthetic suppression unavailable')
                monkeypatch.setattr(case.rig.tasks, '_suppress_docking_publication', unavailable)
            await c.handle(sender, dict(type='docking_watch', task_id=root.task_id))
            await settle(runtime)
            if failure_kind == 'commit':
                assert root.publication.outcome.status == 'UNKNOWN' and root.publication.task.done()
            else:
                assert root.publication.outcome.status == 'TIMED_OUT' and root.publication.task is None
                assert root.publication.reconciliation == 'not_needed'
            assert root.input_context._execution.settled
            assert root.close_state == ('confirmed' if reconciled else 'unconfirmed')
            assert not getattr(sender, '_docking_history', ())
            await c._error(sender, 'docking_unavailable', settled_attachment=sender._chat_docking)
            error = frames(sender.websocket)[-1]
            assert error['docking_segment_complete'] is reconciled
            assert error['trace_id'] == root.root_id and error['turn_id'] == root.origin
            case.rig.output = proposal('run docking')
            await c.handle(sender, dict(type='chat', message='run docking', enable_tools=True))
            await settle(runtime)
            assert (sender._chat_docking.root_id != root.root_id) is reconciled
            assert len(case.raw) == 1
            await c.disconnect(sender)
            await settle(runtime)
    asyncio.run(run())


def test_pre_admission_chat_errors_are_distinct_from_uncorrelated_watch_errors(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        runtime, sender = runtime_sender(socket_for())
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True), history='invalid')
            assert frames(sender.websocket)[-1]['docking_request_rejected'] is True
            assert not rig.calls and getattr(sender, '_chat_docking', None) is None
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            await settle(runtime)
            await c.handle(sender, dict(type='chat', message=QUERY, enable_tools=True))
            error = frames(sender.websocket)[-1]
            assert error['code'] == 'docking_request_active' and error['docking_request_rejected'] is True
            await c.handle(sender, dict(type='docking_watch', task_id='missing-task'))
            error = frames(sender.websocket)[-1]
            assert error['code'] == 'docking_unavailable'
            assert not {'docking_request_rejected', 'docking_segment_complete', 'turn_id', 'trace_id'} & set(error)
            await c.disconnect(sender)
            await settle(runtime)
    asyncio.run(run())


def test_concurrent_read_only_watch_does_not_close_result_pending_root(
        tmp_path, target_case, monkeypatch):
    from tests.task_runtime.test_chat_docking_execution_context import execution_case
    from tests.agent.test_docking_publication_candidate import settle as settle_execution
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            runtime, sender = runtime_sender(socket_for('owner-session'))
            _, watcher = runtime_sender(socket_for('owner-session'))
            watcher._runtime = runtime
            runtime.sockets.add(watcher)
            c = controller(runtime, case.coordinator, enabled=True)
            module = importlib.import_module('src.web.docking_chat_controller')
            root = case.root
            sender._chat_docking = module._Attachment(root.owner, root.root_id, root.origin,
                root=root, identity=(root.task_id, root.preparation_id))
            await settle_execution(case)  # Synthetic execution only.
            await case.coordinator.status(root_id=root.root_id, owner_session_id=root.owner)
            assert root.phase == 'RESULT_PENDING' and root.publication is None
            entered, release, shown = asyncio.Event(), asyncio.Event(), asyncio.Event()
            actual_view = case.context._runtime.get_docking_consent_view
            async def held_view(**kwargs):
                if not entered.is_set():
                    entered.set()
                    await release.wait()
                return await actual_view(**kwargs)
            actual_show = c._show
            async def observed_show(current_sender, attachment):
                try:
                    return await actual_show(current_sender, attachment)
                finally:
                    if current_sender is sender:
                        shown.set()
            monkeypatch.setattr(case.context._runtime, 'get_docking_consent_view', held_view)
            monkeypatch.setattr(c, '_show', observed_show)
            try:
                await c.handle(watcher, dict(type='docking_watch', task_id=root.task_id))
                await asyncio.wait_for(entered.wait(), 3)
                assert root.task is not None and not root.task.done()
                await c.handle(sender, dict(type='docking_watch', task_id=root.task_id))
                await asyncio.wait_for(shown.wait(), 3)
            finally:
                release.set()
                await settle(runtime)
            assert not root.closed and root.phase == 'RESULT_PENDING'
            assert root.publication is None and watcher._chat_docking.read_only
            assert not getattr(sender, '_docking_history', ())
            await c.handle(sender, dict(type='docking_watch', task_id=root.task_id))
            await settle(runtime)
            events = [frame for frame in frames(sender.websocket) if frame['type'] == 'docking_event']
            assert events[-1]['view_status'] == 'SUCCEEDED'
            assert len(sender._docking_history) == 1 and len(case.raw) == 1
    asyncio.run(run())
