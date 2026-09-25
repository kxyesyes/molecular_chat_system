"""Normal application lifetime tests with in-memory HTTP protocol barriers."""
import asyncio
import json
import inspect
import threading
from dataclasses import replace
from functools import partial
from types import SimpleNamespace

import pytest
import httpx

from test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision, result_of


def route_executors(monkeypatch, allow_join, join_started):
    """Real executors; only the owning test's finally may release a join barrier.

    A slow test must not fabricate an unresolved WorkerCleanupError in-process.
    Permanent failed joins belong exclusively to the isolated child below.
    """
    from concurrent.futures import ThreadPoolExecutor
    records = []
    class Executor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.future_done, self.joined = threading.Event(), threading.Event()
            records.append(self)
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            future.add_done_callback(lambda _: self.future_done.set())
            return future
        def shutdown(self, wait=True, **kwargs):
            if wait:
                assert threading.current_thread() not in self._threads
                with pytest.raises(RuntimeError, match='no running event loop'):
                    asyncio.get_running_loop()
                join_started.set()
                allow_join.wait()
            super().shutdown(wait=wait, **kwargs)
            if wait:
                self.joined.set()
    monkeypatch.setattr('src.agent.orchestrators.workflow.ThreadPoolExecutor', Executor)
    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', Executor)
    return records


def _failed_join_child():
    """Test-only isolated process entry; intentionally never claims graceful cleanup."""
    import os
    import sys
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor
    from test_decision_loop import tool
    from src.web import decision_runtime

    async def run():
        mp = pytest.MonkeyPatch()
        build = actual_app.__wrapped__(Path.cwd(), mp)
        retained = asyncio.Event()
        executors, completed, closes = [], [], []
        class FailedJoin(ThreadPoolExecutor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.join_attempted = False
                executors.append(self)
            def submit(self, *args, **kwargs):
                future = super().submit(*args, **kwargs)
                completed.append(future)
                return future
            def shutdown(self, wait=True, **kwargs):
                if wait:
                    self.join_attempted = True
                    raise RuntimeError('synthetic permanent executor join failure')
                return super().shutdown(wait=wait, **kwargs)
        mp.setattr('src.agent.orchestrators.workflow.ThreadPoolExecutor', FailedJoin)
        mp.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', FailedJoin)
        original_retain = decision_runtime.WebDecisionRuntime._retain_unresolved
        async def observe_retain(self, turn):
            retained.set()
            await original_retain(self, turn)
        mp.setattr(decision_runtime.WebDecisionRuntime, '_retain_unresolved', observe_retain)
        async def respond(payload):
            return tool().model_dump()
        async with build(mode='decision_a2', respond=respond) as b:
            async def close():
                closes.append(True)
            mp.setattr(b.model, 'close', close)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send({'message': '计算性质；SMILES: CCO'})
                accepted = await socket.receive()
                assert accepted['type'] == 'request_accepted'
                await asyncio.wait_for(retained.wait(), 5)
                runtime, gate = b.app.decision_runtime, b.app.model_request_gate
                turn, = runtime.active_owners
                root, = turn.worker_owner._roots
                def snapshot(stage, shutdown=None):
                    facts = dict(stage=stage, pid=os.getpid(), owner_retained=turn in runtime.active_owners,
                        reader_count=gate._readers, owner_status=turn.worker_owner.status,
                        model_close_count=len(closes), turn_pending=not turn.task.done(),
                        shutdown_pending=shutdown is not None and not shutdown.done(), closing=runtime.closing,
                        terminal_count=sum(json.loads(f['text'])['type'] in {'agent_result', 'complete'}
                            for f in socket.outgoing._queue if f['type'] == 'websocket.send'),
                        adapter_calls=len(b.calls), executor_count=len(executors),
                        actual_attached_pairs=len(root.records) == 2 and all(
                            r.executor in executors and r.future in completed and r.future.done()
                            and r.failed and r.executor.join_attempted for r in root.records))
                    print('P7A2_CHILD ' + json.dumps(facts), flush=True)
                snapshot('retained')
                assert (await asyncio.to_thread(sys.stdin.readline)).strip() == 'shutdown'
                shutdown_entered = asyncio.Event()
                original_cancel = turn.cancel
                def observe_cancel():
                    original_cancel()
                    shutdown_entered.set()
                mp.setattr(turn, 'cancel', observe_cancel)
                shutdown = asyncio.create_task(b.app.shutdown())
                await asyncio.wait_for(shutdown_entered.wait(), 3)
                snapshot('shutdown', shutdown)
                assert (await asyncio.to_thread(sys.stdin.readline)).strip() == 'cancel-again'
                turn.task.cancel()
                turn.task.cancel()
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()
                snapshot('retained-after-cancel', shutdown)
                # Parent owns this test process and force-terminates it. There
                # is no production escape, false owner settlement or retry.
                await shutdown
    asyncio.run(run())


def test_permanent_failed_join_retains_route_owner_in_isolated_child(tmp_path, record_property):
    import hashlib
    import os
    from pathlib import Path
    import queue
    import shutil
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    root = tmp_path / 'isolated-failed-join'
    root.mkdir()
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC')
           if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
        PYTHONPATH=os.pathsep.join((str(repo), str(repo / 'tests/agent'))), AGENT_HARNESS_MODE='legacy',
        MOLECULAR_CHAT_CONFIG=str(root / 'missing.yaml'), MEDCHAT_ENV_FILE=str(root / 'not-loaded.env'),
        MEDCHAT_USER_CONFIG_DIR=str(root / 'user-config'), MEDCHAT_LLM_LOCK_DIR=str(root / 'locks'),
        MEDCHAT_AGENT_SESSION_DB=str(root / 'sessions.sqlite'), AGENT_STATE_DB=str(root / 'agent.sqlite'),
        MEDCHAT_TASK_DB_PATH=str(root / 'tasks.sqlite'), TARGET_DB_PATH=str(root / 'targets.sqlite'),
        TARGET_CACHE_DIR=str(root / 'target-cache'), MEDCHAT_FRAGMENT_DB_PATH=str(root),
        MEDCHAT_TASK_BACKEND='local', MEDCHAT_TEMPORAL_CANARY_PERCENT='0', AGENT_LANGGRAPH_CANARY_PERCENT='0',
        MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0', MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE='0',
        MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK='0', RUN_REAL_TARGET_SEARCH='0')
    fixtures = root / 'data/agent_evals'
    fixtures.mkdir(parents=True)
    for name in ('real_agent_cases.jsonl', 'golden_scientific_cases.jsonl', 'diverse_scientific_cases.jsonl'):
        source, destination = repo / 'data/agent_evals' / name, fixtures / name
        shutil.copy2(source, destination)
        assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(destination.read_bytes()).digest()
    command = [sys.executable, '-B', '-u', '-c',
               'from test_web_decision_runtime_lifecycle import _failed_join_child; _failed_join_child()']
    child = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding='utf-8',
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    owned_pid = child.pid
    snapshots = queue.Queue()
    def drain_output():
        try:
            for line in child.stdout:
                if line.startswith('P7A2_CHILD '):
                    snapshots.put(json.loads(line[len('P7A2_CHILD '):]))
        finally:
            snapshots.put({'stage': 'child-output-ended'})
    reader = threading.Thread(target=drain_output, name='owned-failed-join-output')
    reader.start()
    try:
        for stage, next_command in (('retained', 'shutdown'), ('shutdown', 'cancel-again'),
                                     ('retained-after-cancel', None)):
            facts = snapshots.get(timeout=20)
            assert facts['stage'] == stage, 'isolated child did not reach controlled ownership checkpoint'
            assert facts['pid'] == owned_pid and child.poll() is None
            assert facts['owner_retained'] and facts['reader_count'] == 1
            assert facts['owner_status'] == 'unresolved' and facts['turn_pending']
            assert facts['actual_attached_pairs'] and facts['executor_count'] == 2
            assert facts['adapter_calls'] == 1 and facts['model_close_count'] == facts['terminal_count'] == 0
            if stage != 'retained':
                assert facts['closing'] and facts['shutdown_pending']
            if next_command:
                child.stdin.write(next_command + '\n')
                child.stdin.flush()
    finally:
        # Popen handle + unchanged PID identify ONLY this test's child. Always
        # reap it and drain the pipe, including timeout/assertion failure paths.
        assert child.pid == owned_pid
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        child.stdin.close()
        reader.join(timeout=5)
        child.stdout.close()
        assert not reader.is_alive()
        record_property('failed_join_cleanup', 'forced isolated child termination; NOT graceful cleanup')


async def receive_complete(socket):
    frames = []
    for _ in range(150):
        frame = await socket.receive()
        frames.append(frame)
        if frame['type'] == 'complete':
            return frames
    pytest.fail('turn did not complete')


@pytest.mark.parametrize('kind', ['unknown-control', [], {}, None, True, False, 0, 1.5],
                         ids=['unknown-string', 'array', 'object', 'null', 'true', 'false', 'integer', 'float'])
def test_invalid_control_type_preserves_socket_history_and_wait(actual_app, kind):
    """Migrated QUALITY route probes plus JSON scalar neighbors."""
    from copy import deepcopy
    from test_decision_loop import clarify
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return chat_decision('safe retained explanation') if count == 1 else clarify().model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            socket = ActualSocket(b.app, await cookie_for(b.app))
            await socket.__aenter__()
            receiving = None
            try:
                await socket.ready()
                warm = result_of(await socket.turn({'message': 'Explain logP'}))
                frames = await socket.turn({'message': '计算 logP'})
                assert result_of(frames)['status'] == 'waiting_for_input'
                sender, = b.app.decision_runtime.sockets
                waiting = sender.waiting
                before = deepcopy(b.app.agent_state_store.get_run(frames[-1]['trace_id']))
                memory = [{'user': 'Explain logP', 'assistant': warm['final_answer']}]
                assert sender.memory == memory
                admission_count = len(b.admission_calls)
                await socket.send({'type': kind})
                receiving = asyncio.create_task(socket.receive())
                done, _ = await asyncio.wait({socket.task, receiving}, timeout=3,
                                            return_when=asyncio.FIRST_COMPLETED)
                error = socket.task.exception() if socket.task.done() and not socket.task.cancelled() else None
                facts = dict(receiver_done=socket.task.done(), receiver_error=type(error).__name__ if error else None,
                    history_pairs=len(sender.memory), handle_retained=sender.waiting is waiting,
                    model_calls=len(b.calls), cas_claims=len(b.claims))
                assert socket.task not in done, json.dumps(facts)
                assert receiving in done, json.dumps(facts)
                assert receiving.result() == {'type': 'error', 'code': 'invalid_control'}
                assert sender.waiting is waiting and sender.memory == memory
                assert len(b.calls) == 2 and not b.claims and len(b.admission_calls) == admission_count
                assert b.app.agent_state_store.get_run(frames[-1]['trace_id']) == before
                await socket.send({'type': 'ping', 'timestamp': 0})
                assert await socket.receive() == {'type': 'pong', 'timestamp': 0}
                resumed = result_of(await socket.turn({'type': 'resume', 'trace_id': frames[-1]['trace_id'],
                    'continuation_id': frames[-1]['continuation_id'], 'message': '计算 logP；SMILES: CCO'}))
                assert resumed['status'] == 'waiting_for_input' and len(b.claims) == 1 and len(b.calls) == 3
                assert sender.memory == memory and sender.waiting.context.memory == memory
            finally:
                if receiving is not None:
                    if not receiving.done():
                        receiving.cancel()
                    await asyncio.gather(receiving, return_exceptions=True)
                await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                await asyncio.wait_for(asyncio.gather(socket.task, return_exceptions=True), 5)
    asyncio.run(run())


@pytest.mark.parametrize('wire', ['native', 'json'])
@pytest.mark.parametrize('failure', ['cancel-before-lease', 'refresh-failed'])
def test_terminal_resume_before_loop_erases_local_handle(actual_app, monkeypatch, wire, failure):
    """Migrated SPEC route probe: terminal failure is not a bad-resume rejection."""
    from copy import deepcopy
    from test_decision_loop import clarify
    async def run():
        async def respond(payload):
            return clarify().model_dump()
        async with actual_app(mode='decision_a2', wire=wire, respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = await socket.turn({'message': '计算 logP'})
                assert result_of(first)['status'] == 'waiting_for_input'
                sender, = b.app.decision_runtime.sockets
                payload = dict(type='resume', trace_id=first[-1]['trace_id'],
                    continuation_id=first[-1]['continuation_id'], message='计算 logP; SMILES: CCO')
                before = deepcopy(b.app.agent_state_store.get_run(payload['trace_id']))
                if failure == 'cancel-before-lease':
                    async with b.app.model_request_gate.exclusive():
                        await socket.send(payload)
                        accepted = await socket.receive()
                        assert accepted['type'] == 'request_accepted'
                        await socket.send({'type': 'ping', 'timestamp': 0})
                        assert (await socket.receive())['type'] == 'pong'
                        turn, = b.app.decision_runtime.active_owners
                        assert turn.started and not turn.dispatching
                        await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                        terminal = result_of(await receive_complete(socket))
                    assert terminal['status'] == 'cancelled'
                else:
                    original = b.app._llm_env_file_signature
                    def fail():
                        raise RuntimeError('synthetic refresh unavailable')
                    monkeypatch.setattr(b.app, '_llm_env_file_signature', fail)
                    try:
                        terminal = result_of(await socket.turn(payload))
                        assert terminal['status'] == 'failed'
                    finally:
                        monkeypatch.setattr(b.app, '_llm_env_file_signature', original)
                assert not b.claims and len(b.calls) == 1
                assert b.app.agent_state_store.get_run(payload['trace_id']) == before
                retained = sender.waiting is not None
                await socket.send(payload)
                retried = await socket.receive()
                retry_status = None
                if retried['type'] == 'request_accepted':
                    retry_status = result_of(await receive_complete(socket))['status']
                facts = dict(terminal_status=terminal['status'], old_handle_retained=retained,
                    retry_type=retried['type'], retry_status=retry_status,
                    model_calls=len(b.calls), cas_claims=len(b.claims))
                assert retried == {'type': 'error', 'code': 'continuation_unavailable'}, json.dumps(facts)
                assert sender.waiting is None and not b.claims and len(b.calls) == 1
                assert b.app.agent_state_store.get_run(payload['trace_id']) == before
                assert not b.app.decision_runtime.active_owners and b.app.model_request_gate._readers == 0
    asyncio.run(run())


@pytest.mark.parametrize('send_failure', [False, True])
def test_history_commits_only_after_successful_complete_send(actual_app, monkeypatch, send_failure):
    """SPEC transport positive/negative controls; successful send is not UI ACK."""
    from starlette.websockets import WebSocket
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        original_send = WebSocket.send_text
        count = 0
        async def send(socket, text):
            nonlocal count
            if json.loads(text)['type'] == 'complete' and count == 0:
                count += 1
                entered.set()
                await release.wait()
                if send_failure:
                    raise ConnectionError('synthetic complete send failure')
            return await original_send(socket, text)
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                monkeypatch.setattr(WebSocket, 'send_text', send)
                try:
                    await socket.send({'message': 'Explain logP'})
                    await asyncio.wait_for(entered.wait(), 3)
                    sender, = b.app.decision_runtime.sockets
                    assert sender.memory == []
                    tasks = tuple(b.app.decision_runtime.tasks)
                    assert len(tasks) == 1
                    release.set()
                    await asyncio.wait_for(asyncio.gather(*tasks), 3)
                    if send_failure:
                        assert sender.memory == [] and not sender.writable
                    else:
                        displayed = result_of(await receive_complete(socket))['final_answer']
                        assert sender.memory == [{'user': 'Explain logP', 'assistant': displayed}]
                        result_of(await socket.turn({'message': '解释分子生成的概念'}))
                        assert b.calls[-1]['messages'][2:4] == [
                            {'role': 'user', 'content': 'Explain logP'},
                            {'role': 'assistant', 'content': displayed}]
                finally:
                    release.set()
    asyncio.run(run())


@pytest.mark.parametrize('failed_frame', ['agent_result', 'complete'])
def test_failed_resume_terminal_send_cannot_preserve_handle(actual_app, monkeypatch, failed_frame):
    from copy import deepcopy
    from starlette.websockets import WebSocket
    from test_decision_loop import clarify
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(payload):
            return clarify().model_dump()
        original_send = WebSocket.send_text
        async def fail_send(socket, text):
            if json.loads(text)['type'] == failed_frame:
                entered.set()
                await release.wait()
                raise ConnectionError('synthetic terminal send failure')
            return await original_send(socket, text)
        async with actual_app(mode='decision_a2', respond=respond) as b:
            socket = ActualSocket(b.app, await cookie_for(b.app))
            await socket.__aenter__()
            tasks = ()
            try:
                await socket.ready()
                first = await socket.turn({'message': '计算 logP'})
                sender, = b.app.decision_runtime.sockets
                trace = first[-1]['trace_id']
                before = deepcopy(b.app.agent_state_store.get_run(trace))
                def fail_refresh():
                    raise RuntimeError('synthetic refresh unavailable')
                monkeypatch.setattr(b.app, '_llm_env_file_signature', fail_refresh)
                monkeypatch.setattr(WebSocket, 'send_text', fail_send)
                await socket.send({'type': 'resume', 'trace_id': trace,
                    'continuation_id': first[-1]['continuation_id'], 'message': '计算 logP; SMILES: CCO'})
                await asyncio.wait_for(entered.wait(), 3)
                tasks = tuple(b.app.decision_runtime.tasks)
                assert tasks
                # Revocation must precede attempted delivery, not depend on
                # successful terminal send or eventual socket disconnection.
                assert sender.waiting is None
                release.set()
                settled = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
                assert all(value is None or isinstance(value, ConnectionError) for value in settled)
                assert sender.waiting is None and not sender.writable
                assert not b.claims and len(b.calls) == 1
                assert b.app.agent_state_store.get_run(trace) == before
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                settled = await asyncio.wait_for(asyncio.gather(socket.task, return_exceptions=True), 3)
                assert all(value is None or isinstance(value, ConnectionError) for value in settled)
            assert sender.waiting is None and not b.app.decision_runtime.active_owners
            assert b.app.model_request_gate._readers == 0
    asyncio.run(run())


def test_actual_route_two_wait_cycles_resumed_cancel_then_fresh_chat(actual_app):
    from copy import deepcopy
    from test_decision_loop import clarify
    async def run():
        entered, exited = asyncio.Event(), asyncio.Event()
        calls = 0
        async def respond(payload):
            nonlocal calls
            calls += 1
            if calls in {2, 3}:
                return clarify().model_dump()
            if calls == 4:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    exited.set()
            return chat_decision('safe concept response')
        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                warm = result_of(await socket.turn({'message': 'Explain logP'}))
                first_frames = await socket.turn({'message': '计算 logP'})
                first = result_of(first_frames)
                assert first['status'] == 'waiting_for_input' and not first['success']
                trace, nonce = first['trace_id'], first_frames[-1]['continuation_id']
                record = deepcopy(b.app.agent_state_store.get_run(trace))
                assert record['status'] == 'waiting_for_input'
                assert not b.app.decision_runtime.active_owners and b.app.model_request_gate._readers == 0
                await socket.send({'type': 'resume', 'trace_id': trace, 'continuation_id': nonce,
                                   'message': '计算 logP；SMILES: CCO'})
                accepted = await socket.receive()
                assert accepted['type'] == 'request_accepted', accepted
                assert accepted['trace_id'] == trace
                second_frames = [accepted] + await receive_complete(socket)
                second = result_of(second_frames)
                next_nonce = second_frames[-1]['continuation_id']
                assert second['status'] == 'waiting_for_input' and next_nonce != nonce
                assert second['trace_id'] == trace
                assert second['metadata']['task_requirements'] == first['metadata']['task_requirements']
                assert b.app.agent_state_store.get_run(trace)['metadata']['decision_continuation']['configuration'] == (
                    record['metadata']['decision_continuation']['configuration'])
                assert second['metadata']['model_requests'] == 2
                await socket.send({'type': 'resume', 'trace_id': trace, 'continuation_id': next_nonce,
                                   'message': '计算 logP；SMILES: CCO'})
                active = await socket.receive()
                assert active['type'] == 'request_accepted' and active['turn_id'] != accepted['turn_id']
                await asyncio.wait_for(entered.wait(), 3)
                await socket.send({'type': 'cancel', 'turn_id': active['turn_id']})
                assert result_of(await receive_complete(socket))['status'] == 'cancelled'
                assert exited.is_set() and not b.app.decision_runtime.active_owners
                assert len(b.claims) == 2
                assert b.app.agent_state_store.get_tool_executions(trace) == []
                final = result_of(await socket.turn({'message': '你好'}))
                assert final['status'] == 'completed'
                assert [m for m in b.calls[-1]['messages'] if m['role'] != 'system'] == [
                    {'role': 'user', 'content': 'Explain logP'},
                    {'role': 'assistant', 'content': warm['final_answer']},
                    {'role': 'user', 'content': '你好'}]
    asyncio.run(run())


@pytest.mark.parametrize('clarification', [
    '计算分子量；SMILES: CCO', '计算 logP；SMILES: CCN',
    '计算 logP 及类药性；SMILES: CCO', '计算 logP；SMILES: CCO; CCN',
    '解释 logP\n对接这个分子', '计算 CCO 的分子量和熔点',
    '计算性质（禁用 property_calculator）；SMILES: CCO',
    '分析两种化合物的性质；SMILES: CCO', '计算 logP；SMILES: CCOjunk',
])
def test_resume_cannot_change_original_obligations_before_cas(actual_app, clarification):
    from copy import deepcopy
    from test_decision_loop import clarify
    async def run():
        async def respond(payload):
            return clarify().model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message': '计算 logP；SMILES: CCO'})
                waiting = result_of(frames)
                assert waiting['status'] == 'waiting_for_input'
                trace = waiting['trace_id']
                before = deepcopy(b.app.agent_state_store.get_run(trace))
                await socket.send({'type': 'resume', 'trace_id': trace,
                    'continuation_id': frames[-1]['continuation_id'], 'message': clarification})
                accepted = await socket.receive()
                assert accepted['type'] == 'request_accepted', accepted
                result = result_of(await receive_complete(socket))
                assert result['status'] == 'rejected'
                assert len(b.calls) == 1
                assert b.app.agent_state_store.get_run(trace) == before
                assert b.app.agent_state_store.get_tool_executions(trace) == []
                assert not b.claims
    asyncio.run(run())


@pytest.mark.parametrize('original,clarified,valid', [
    ('计算 logP；SMILES: CCO', '计算 logP；SMILES: OCC', True),
    ('预测 PDE4D 活性；SMILES: CCO', '预测 PDE5A 活性；SMILES: CCO', False),
    ('预测 BuChE 活性；SMILES: CCO', '预测 BChE 活性；SMILES: CCO', True),
    ('预测活性；SMILES: CCO', '预测 BuChE 活性；SMILES: CCO', True),
    ('预测 PDE4D 活性；SMILES: CCO', '预测未知靶点活性；SMILES: CCO', False),
    ('查询 EGFR 的靶点结构', '查询 KRAS 的靶点结构', False),
    ('查询 EGFR 的靶点结构', '查询 EGFR 的靶点结构', True),
])
def test_resume_uses_public_whole_subject_and_target_boundaries(actual_app, original, clarified, valid):
    from copy import deepcopy
    from test_decision_loop import clarify
    async def run():
        async def respond(payload):
            return clarify().model_dump()
        async with actual_app(mode='decision_a2', respond=respond, optional_tools=True) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = await socket.turn({'message': original})
                assert result_of(first)['status'] == 'waiting_for_input'
                trace = first[-1]['trace_id']
                before = deepcopy(b.app.agent_state_store.get_run(trace))
                resumed = result_of(await socket.turn({'type': 'resume', 'trace_id': trace,
                    'continuation_id': first[-1]['continuation_id'], 'message': clarified}))
                assert resumed['status'] == ('waiting_for_input' if valid else 'rejected')
                assert len(b.claims) == int(valid) and len(b.calls) == 1 + int(valid)
                if not valid:
                    assert b.app.agent_state_store.get_run(trace) == before
                assert b.app.agent_state_store.get_tool_executions(trace) == []
                for name, attribute in [('activity_predictor', '_predictor'), ('target_database_search', '_service')]:
                    # No tool execution means no asset/service initialization.
                    adapter = b.app.decision_runtime.registry.resolve(name)
                    assert getattr(adapter.tool, attribute) is None
    asyncio.run(run())


@pytest.mark.parametrize('during_complete', [False, True])
def test_shutdown_cannot_restore_socket_waiting_or_history(actual_app, monkeypatch, during_complete):
    from starlette.websockets import WebSocket
    from test_decision_loop import clarify
    async def run():
        entered, release, shutdown_checked = (asyncio.Event() for _ in range(3))
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return chat_decision('retained warm pair') if count == 1 else clarify().model_dump()
        send_text = WebSocket.send_text
        async def block_complete(self, text):
            if during_complete and json.loads(text)['type'] == 'complete' and count == 2:
                entered.set()
                await release.wait()
            await send_text(self, text)
        async with actual_app(mode='decision_a2', respond=respond) as b:
            monkeypatch.setattr(WebSocket, 'send_text', block_complete)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result_of(await socket.turn({'message': 'Explain logP'}))
                sender, = b.app.decision_runtime.sockets
                assert sender.memory
                shutdown = None
                try:
                    if during_complete:
                        await socket.send({'message': '计算 logP'})
                        await asyncio.wait_for(entered.wait(), 3)
                    else:
                        assert result_of(await socket.turn({'message': '计算 logP'}))['status'] == 'waiting_for_input'
                        assert sender.waiting is not None
                    async def stop():
                        operation = asyncio.create_task(b.app.shutdown())
                        # FIFO scheduling checkpoint after shutdown starts;
                        # no elapsed-time assumption or provider stub.
                        asyncio.get_running_loop().call_soon(shutdown_checked.set)
                        await operation
                    shutdown = asyncio.create_task(stop())
                    await asyncio.wait_for(shutdown_checked.wait(), 3)
                    assert b.app.decision_runtime.closing
                    assert not sender.memory and sender.waiting is None
                    release.set()
                    await asyncio.wait_for(asyncio.shield(shutdown), 3)
                    assert not sender.memory and sender.waiting is None
                    assert not b.app.decision_runtime.active_owners
                    assert b.app.model_request_gate._readers == 0
                finally:
                    release.set()
                    if shutdown is not None:
                        await shutdown
    asyncio.run(run())


@pytest.mark.parametrize('change', ['epoch', 'schema', 'requirements', 'frozen-history', 'snapshot-history', 'revision5'])
def test_changed_waiting_fingerprint_rejects_before_actual_cas(actual_app, monkeypatch, change):
    from contextlib import AsyncExitStack
    from copy import deepcopy
    from dataclasses import replace
    import sqlite3
    from test_decision_loop import clarify
    from src.agent.evidence import EvidenceLedger
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            return chat_decision('{safe historical prose}') if count == 1 else clarify().model_dump()
        async with AsyncExitStack() as clients, actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result_of(await socket.turn({'message': 'Explain logP'}))
                scientific = change in {'schema', 'requirements'}
                query = '计算 logP' if scientific else 'Explain logP'
                frames = await socket.turn({'message': query})
                assert result_of(frames)['status'] == 'waiting_for_input'
                trace, nonce = frames[-1]['trace_id'], frames[-1]['continuation_id']
                sender, = b.app.decision_runtime.sockets
                store = b.app.agent_state_store
                if change == 'epoch':
                    async def forbidden_transport(request):
                        pytest.fail('stale continuation reached replacement provider')
                    client = await clients.enter_async_context(httpx.AsyncClient(transport=httpx.MockTransport(forbidden_transport)))
                    replacement = OpenAICompatibleModel('synthetic-credential-rotation', 'protocol-only',
                        'https://example.invalid/v1', client=client)
                    monkeypatch.setattr(b.app, '_create_model_from_llm_config', lambda config: replacement)
                    previous = b.app.model_generation
                    await b.app._replace_llm_config(dict(b.app.active_llm_config,
                        api_key='synthetic-credential-rotation'))
                    assert previous != b.app.model_generation
                elif change == 'schema':
                    adapter = b.app.decision_runtime.registry.resolve('property_calculator')
                    adapter.spec = replace(adapter.spec, version='changed-test-schema')
                elif change == 'requirements':
                    from src.agent.contracts.task_requirements import TaskRequirements, MolecularRequirement
                    sender.waiting = replace(sender.waiting, prepared=replace(sender.waiting.prepared,
                        requirements=TaskRequirements(molecular_results=(MolecularRequirement(
                            tool_name='property_calculator', required_metrics=('logp',), exact_molecule_count=1),))))
                elif change == 'frozen-history':
                    sender.waiting.context.memory[0]['assistant'] = 'substituted safe history'
                else:
                    # Deliberately corrupt a temporary persisted fixture, with
                    # a recomputed checksum. The real claim API is not replaced;
                    # semantic prefix/revision checks, not a checksum, must reject.
                    record = store.get_run(trace)
                    metadata = deepcopy(record['metadata'])
                    payload = metadata['decision_continuation']
                    if change == 'revision5':
                        payload['snapshot']['decision_protocol_revision'] = 5
                    else:
                        payload['snapshot']['messages'][2]['content'] = 'substituted safe history'
                    payload['checksum'] = EvidenceLedger.output_digest({k: v for k, v in payload.items() if k != 'checksum'})
                    with sqlite3.connect(store.db_path) as connection:
                        connection.execute('UPDATE agent_runs SET metadata_json=? WHERE trace_id=?',
                            (json.dumps(metadata), trace))
                before = deepcopy(store.get_run(trace))
                result = result_of(await socket.turn({'type': 'resume', 'trace_id': trace,
                    'continuation_id': nonce, 'message': '计算 logP；SMILES: CCO' if scientific else query}))
                assert result['status'] == 'rejected'
                assert not b.claims and len(b.calls) == 2
                assert store.get_run(trace) == before and store.get_tool_executions(trace) == []
                assert sender.waiting is not None and sender.waiting.continuation_id == nonce
    asyncio.run(run())


@pytest.mark.parametrize('change', ['revoked', 'replace-subject', 'invalid-explicit', 'same-subject'])
def test_confirmed_source_resume_is_sealed_before_cas(actual_app, change):
    from copy import deepcopy
    from test_decision_loop import clarify
    from test_scientific_reference_web import seed, pointer
    async def run():
        async def respond(payload):
            return clarify().model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                sender, = b.app.decision_runtime.sockets
                owner = sender.scope['agent_session_id']
                store = b.app.agent_state_store
                source = seed(store, status='partial', warnings=['synthetic historical source only'])
                # Bind the synthetic historical fixture to the real middleware
                # identity before it has a presentation. No generated molecule
                # or mounted browser ACK is claimed by this protocol test.
                store.start_run(dict(store.get_run('trace'), session_id=owner, user_id=owner))
                event, = b.app.decision_runtime.references.project(source, session_id=owner)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app.app),
                        base_url='http://127.0.0.1') as client:
                    client.cookies.set('medchat_agent_session', cookie, domain='127.0.0.1', path='/')
                    confirmed = await client.post('/api/agent/workflows/references/confirm', json=event['reference'])
                    assert confirmed.status_code == 200
                    restored = await client.post('/api/agent/workflows/references/restore', json=pointer(event))
                    assert restored.status_code == 200
                frames = await socket.turn({'message': '计算 logP', 'reference': pointer(event), 'selection': {'ordinal': 1}})
                assert result_of(frames)['status'] == 'waiting_for_input'
                trace = frames[-1]['trace_id']
                assert sender.waiting.context.resolved_molecule.canonical_smiles == 'CCO'
                if change == 'revoked':
                    store.update_run_status('trace', 'failed')
                query = {'replace-subject': '计算 logP；SMILES: CCN',
                         'invalid-explicit': '计算 logP；SMILES: CCOjunk'}.get(change, '计算 logP')
                before = deepcopy(store.get_run(trace))
                result = result_of(await socket.turn({'type': 'resume', 'trace_id': trace,
                    'continuation_id': frames[-1]['continuation_id'], 'message': query}))
                valid = change == 'same-subject'
                assert result['status'] == ('waiting_for_input' if valid else 'rejected')
                assert len(b.claims) == int(valid) and len(b.calls) == 1 + int(valid)
                if not valid:
                    assert store.get_run(trace) == before
                assert store.get_tool_executions(trace) == []
                assert store.get_run('trace')['status'] == ('failed' if change == 'revoked' else 'partial')
    asyncio.run(run())


def test_refined_subject_is_sealed_but_invalid_resume_does_not_consume_handle(actual_app):
    from copy import deepcopy
    from test_decision_loop import clarify, tool, finish
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            if count <= 2:
                return clarify().model_dump()
            observations = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
            return (finish([o['quality']['evidence_id'] for o in observations]) if observations else tool()).model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                first = await socket.turn({'message': '计算 logP'})
                trace = first[-1]['trace_id']
                second = await socket.turn({'type': 'resume', 'trace_id': trace,
                    'continuation_id': first[-1]['continuation_id'], 'message': '计算 logP；SMILES: CCO'})
                assert result_of(second)['status'] == 'waiting_for_input'
                sender, = b.app.decision_runtime.sockets
                frozen = deepcopy(sender.waiting.context)
                requirements = sender.waiting.prepared.requirements.model_dump(mode='json')
                record = deepcopy(b.app.agent_state_store.get_run(trace))
                # Invalid new chats and invalid refinements must not consume
                # the local handle, mutate original obligations or claim CAS.
                assert result_of(await socket.turn({'message': '检索知识库中的相关文献'}))['status'] == 'rejected'
                bad = await socket.turn({'type': 'resume', 'trace_id': trace,
                    'continuation_id': second[-1]['continuation_id'], 'message': '计算 logP；SMILES: CCN'})
                assert result_of(bad)['status'] == 'rejected'
                assert b.app.agent_state_store.get_run(trace) == record and len(b.claims) == 1
                good = result_of(await socket.turn({'type': 'resume', 'trace_id': trace,
                    'continuation_id': second[-1]['continuation_id'], 'message': '计算 logP；SMILES: OCC'}))
                assert good['status'] == 'completed' and good['success']
                assert len(b.claims) == 2 and len(b.calls) == 4
                assert len(b.app.agent_state_store.get_tool_executions(trace)) == 1
                assert frozen.query == '计算 logP' and frozen.resolved_molecule is None
                assert good['metadata']['task_requirements'] == requirements
                assert sender.waiting is None and not sender.memory
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['completed', 'partial', 'failed', 'rejected', 'waiting_for_input', 'cancelled'])
def test_history_excludes_nonchat_and_uncompleted_turns(actual_app, outcome):
    from test_decision_loop import clarify, tool, finish
    async def run():
        phase = 'warm'
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(payload):
            if phase != 'main':
                return chat_decision('safe admitted explanation')
            if outcome == 'cancelled':
                entered.set()
                await release.wait()
            if outcome == 'waiting_for_input':
                return clarify().model_dump()
            if outcome == 'failed':
                raise RuntimeError('synthetic protocol transport unavailable')
            observations = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
            if observations and outcome == 'partial':
                raise RuntimeError('synthetic protocol transport unavailable after real observation')
            return (finish([o['quality']['evidence_id'] for o in observations]) if observations else tool()).model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                warm = result_of(await socket.turn({'message': 'Explain logP'}))
                phase = 'main'
                query = ('计算 logP；SMILES: CCO' if outcome in {'completed', 'partial'} else
                         '检索知识库中的相关文献' if outcome == 'rejected' else '解释分子生成的概念')
                try:
                    if outcome == 'cancelled':
                        await socket.send({'message': query})
                        accepted = await socket.receive()
                        await asyncio.wait_for(entered.wait(), 3)
                        await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                        frames = await receive_complete(socket)
                    else:
                        frames = await socket.turn({'message': query})
                    result = result_of(frames)
                    assert result['status'] == outcome
                    sender, = b.app.decision_runtime.sockets
                    assert sender.memory == [{'user': 'Explain logP', 'assistant': warm['final_answer']}]
                    if outcome in {'completed', 'partial'}:
                        assert len(b.app.agent_state_store.get_tool_executions(result['trace_id'])) == 1
                    phase = 'next'
                    result_of(await socket.turn({'message': '你好'}))
                    assert [m for m in b.calls[-1]['messages'] if m['role'] != 'system'] == [
                        {'role': 'user', 'content': 'Explain logP'}, {'role': 'assistant', 'content': warm['final_answer']},
                        {'role': 'user', 'content': '你好'}]
                finally:
                    release.set()
    asyncio.run(run())


def test_chat_resume_replays_brace_prefix_and_retains_current_clarified_input(actual_app):
    from test_decision_loop import clarify
    async def run():
        count = 0
        async def respond(payload):
            nonlocal count
            count += 1
            if count == 2:
                return clarify().model_dump()
            return chat_decision('{ordinary safe answer}' if count == 1 else 'resumed safe explanation')
        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                warm = result_of(await socket.turn({'message': 'Explain logP'}))
                waiting = await socket.turn({'message': 'Explain logP'})
                assert result_of(waiting)['status'] == 'waiting_for_input'
                resumed = result_of(await socket.turn({'type': 'resume', 'trace_id': waiting[-1]['trace_id'],
                    'continuation_id': waiting[-1]['continuation_id'], 'message': '解释分子生成的概念'}))
                assert resumed['status'] == 'completed' and len(b.claims) == 1
                assert resumed['metadata']['model_requests'] == 2
                result_of(await socket.turn({'message': '你好'}))
                assert [m for m in b.calls[-1]['messages'] if m['role'] != 'system'] == [
                    {'role': 'user', 'content': 'Explain logP'}, {'role': 'assistant', 'content': warm['final_answer']},
                    {'role': 'user', 'content': '解释分子生成的概念'}, {'role': 'assistant', 'content': resumed['final_answer']},
                    {'role': 'user', 'content': '你好'}]
    asyncio.run(run())


@pytest.mark.parametrize('case', ['foreign-cookie', 'other-socket', 'disconnect', 'stale', 'trace',
                                 'duplicate', 'expired', 'abandoned', 'new-chat', 'extra-options'])
def test_waiting_handle_is_socket_local_single_use_and_bounded(actual_app, case):
    from copy import deepcopy
    from test_decision_loop import clarify
    async def run():
        async def respond(payload):
            return clarify().model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                frames = await socket.turn({'message': '计算 logP'})
                result_of(frames)
                trace, nonce = frames[-1]['trace_id'], frames[-1]['continuation_id']
                payload = {'type': 'resume', 'trace_id': trace, 'continuation_id': nonce,
                           'message': '计算 logP；SMILES: CCO'}
                sender, = b.app.decision_runtime.sockets
                if case == 'expired':
                    sender.waiting = replace(sender.waiting, expires_at=0)
                if case == 'stale':
                    payload['continuation_id'] = '0' * 32
                if case == 'trace':
                    payload['trace_id'] = '0' * 32
                if case == 'extra-options':
                    payload['enable_tools'] = False
                if case == 'duplicate':
                    second = await socket.turn(payload)
                    assert result_of(second)['status'] == 'waiting_for_input'
                    assert second[-1]['continuation_id'] != nonce
                if case == 'abandoned':
                    await socket.send({'type': 'abandon', 'trace_id': trace, 'continuation_id': nonce})
                    assert await socket.receive() == {'type': 'continuation_abandoned', 'trace_id': trace}
                if case == 'new-chat':
                    result_of(await socket.turn({'message': 'Explain logP'}))
                before = deepcopy(b.app.agent_state_store.get_run(trace))
                before_calls, before_claims = len(b.calls), len(b.claims)
                if case in {'foreign-cookie', 'other-socket', 'disconnect'}:
                    if case == 'disconnect':
                        await socket.__aexit__(None, None, None)
                    other_cookie = await cookie_for(b.app) if case == 'foreign-cookie' else cookie
                    async with ActualSocket(b.app, other_cookie) as other:
                        await other.ready()
                        await other.send(payload)
                        rejected = await other.receive()
                else:
                    await socket.send(payload)
                    rejected = await socket.receive()
                assert rejected == {'type': 'error', 'code': 'continuation_unavailable'}
                assert b.app.agent_state_store.get_run(trace) == before
                assert (len(b.calls), len(b.claims)) == (before_calls, before_claims)
                assert b.app.agent_state_store.get_tool_executions(trace) == []
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['overflow', 'event-serialization', 'result-serialization'])
def test_actual_route_display_failures_settle_before_one_terminal(actual_app, monkeypatch, failure):
    from src.web import decision_chat
    from test_decision_loop import tool, finish
    async def run():
        overflow = asyncio.Event()
        async def respond(payload):
            observations = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
            return (finish([o['quality']['evidence_id'] for o in observations]) if observations else tool()).model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            hit = []
            if failure == 'overflow':
                # Tighten the existing queue budget; the real loop emits real
                # Session events. Exact default-128 stress remains in bridge tests.
                monkeypatch.setattr(decision_chat, 'MAX_PENDING_EVENTS', 1)
                publish = decision_chat._EventBuffer.publish
                def observe_publish(self, prepare):
                    publish(self, prepare)
                    if self.error == 'event_buffer_overflow':
                        hit.append(True)
                        self.loop.call_soon_threadsafe(overflow.set)
                monkeypatch.setattr(decision_chat._EventBuffer, 'publish', observe_publish)
                from starlette.websockets import WebSocket
                send_text = WebSocket.send_text
                async def slow_send(self, text):
                    if json.loads(text)['type'] == 'agent_event':
                        await overflow.wait()
                    return await send_text(self, text)
                monkeypatch.setattr(WebSocket, 'send_text', slow_send)
            else:
                name = '_event_frame' if failure == 'event-serialization' else '_result_frame'
                original = getattr(decision_chat, name)
                def fail_once(*args):
                    if not hit:
                        hit.append(True)
                        raise ValueError('synthetic serialization failure')
                    return original(*args)
                monkeypatch.setattr(decision_chat, name, fail_once)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                try:
                    result = result_of(await socket.turn({'message': '计算性质；SMILES: CCO'}))
                finally:
                    overflow.set()
                assert hit
                assert result['status'] == 'failed' and not result['success']
                reason = {'overflow': 'event_buffer_overflow', 'event-serialization': 'event_display_invalid',
                          'result-serialization': 'result_display_invalid'}[failure]
                assert result['metadata']['stop_reason'] == reason
                assert not b.app.decision_runtime.active_owners and b.app.model_request_gate._readers == 0
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['send-timeout', 'receiver-failure'])
def test_actual_route_transport_failure_drains_provider(actual_app, monkeypatch, failure):
    from starlette.websockets import WebSocket
    from src.web import decision_chat, decision_runtime
    async def run():
        entered, exited, send_entered, send_exited = (asyncio.Event() for _ in range(4))
        async def respond(payload):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            send_text = WebSocket.send_text
            async def block_send(self, text):
                if json.loads(text)['type'] == 'agent_event':
                    send_entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        send_exited.set()
                await send_text(self, text)
            if failure == 'send-timeout':
                monkeypatch.setattr(WebSocket, 'send_text', block_send)
                monkeypatch.setattr(decision_chat, 'SEND_TIMEOUT_SECONDS', 0.1)
                monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.1)
            socket = ActualSocket(b.app, await cookie_for(b.app))
            await socket.__aenter__()
            try:
                await socket.ready()
                await socket.send({'message': '你好'})
                await asyncio.wait_for(entered.wait(), 3)
                turn, = b.app.decision_runtime.active_owners
                if failure == 'receiver-failure':
                    await socket.incoming.put({'type': 'websocket.receive'})
                    with pytest.raises(KeyError):
                        await asyncio.wait_for(socket.task, 3)
                else:
                    await asyncio.wait_for(send_entered.wait(), 3)
                    await asyncio.wait_for(asyncio.shield(turn.task), 3)
                    assert send_exited.is_set()
                assert exited.is_set() and turn.worker_owner.status == 'settled'
                assert not b.app.decision_runtime.active_owners and b.app.model_request_gate._readers == 0
                assert not any(json.loads(f['text'])['type'] in {'agent_result', 'complete'}
                               for f in socket.outgoing._queue if f['type'] == 'websocket.send')
            finally:
                await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                await asyncio.wait_for(asyncio.gather(socket.task, return_exceptions=True), 4)
    asyncio.run(run())


def test_stale_cancel_concurrent_chat_and_finish_latch_on_actual_route(actual_app, monkeypatch):
    from starlette.websockets import WebSocket
    async def run():
        entered, release, finishing, finish_release = (asyncio.Event() for _ in range(4))
        async def respond(payload):
            entered.set()
            await release.wait()
            return chat_decision('committed answer')
        send_text = WebSocket.send_text
        async def pause_result(self, text):
            if json.loads(text)['type'] == 'agent_result':
                finishing.set()
                await finish_release.wait()
            return await send_text(self, text)
        async with actual_app(mode='decision_a2', respond=respond) as b:
            monkeypatch.setattr(WebSocket, 'send_text', pause_result)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                try:
                    await socket.send({'message': '你好'})
                    accepted = await socket.receive()
                    await asyncio.wait_for(entered.wait(), 3)
                    turn, = b.app.decision_runtime.active_owners
                    await socket.send({'type': 'cancel', 'turn_id': 'foreign-turn'})
                    await socket.send({'message': 'Explain logP'})
                    errors = []
                    while len(errors) < 2:
                        frame = await socket.receive()
                        if frame['type'] == 'error':
                            errors.append(frame['code'])
                    assert errors == ['invalid_control', 'turn_in_progress']
                    assert not turn.cancel_event.is_set() and len(b.calls) == 1
                    assert b.app.decision_runtime.active_owners == {turn}
                    release.set()
                    await asyncio.wait_for(finishing.wait(), 3)
                    await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                    await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                    # No sender is needed for matching cancel; receiving a later
                    # ping proves both were processed, after result send resumes.
                    await socket.send({'type': 'ping', 'timestamp': 0})
                    finish_release.set()
                    frames = await receive_complete(socket)
                    assert result_of(frames)['final_answer'] == 'committed answer'
                    assert not turn.cancel_event.is_set()
                    assert len(b.calls) == 1 and not b.app.decision_runtime.active_owners
                finally:
                    release.set()
                    finish_release.set()
    asyncio.run(run())


@pytest.mark.parametrize('boundary', ['refresh-lock', 'reader-lease'])
def test_cancel_before_admission_drains_queued_turn(actual_app, monkeypatch, boundary):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            gate = b.app.model_request_gate
            if boundary == 'refresh-lock':
                monkeypatch.setattr(b.app, '_llm_env_file_signature', lambda: 'synthetic-change')
                held = b.app._llm_config_lock
            else:
                held = gate.exclusive()
            async with held:
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    await socket.ready()
                    await socket.send({'message': '你好'})
                    accepted = await socket.receive()
                    await socket.send({'type': 'ping', 'timestamp': 0})
                    assert (await socket.receive())['type'] == 'pong'
                    turn, = b.app.decision_runtime.active_owners
                    assert turn.started and not turn.dispatching
                    await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                    result = result_of(await receive_complete(socket))
                    assert result['status'] == 'cancelled'
                    await asyncio.wait_for(asyncio.shield(turn.task), 3)
                    assert turn.task.done() and turn.worker_owner.status == 'settled'
                    assert not b.app.decision_runtime.active_owners and gate._readers == 0
                    assert not b.calls and not b.admission_calls
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['config-read', 'model-construction'])
def test_failed_refresh_has_no_stale_inference_on_actual_route(actual_app, monkeypatch, failure):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            snapshot = (b.app.model, b.app.active_llm_config, b.app.model_generation)
            monkeypatch.setattr(b.app, '_llm_env_file_signature', lambda: 'synthetic-change')
            def unavailable(*args):
                assert b.app.model_request_gate._readers == 0
                raise RuntimeError('synthetic unavailable config boundary')
            if failure == 'config-read':
                monkeypatch.setattr(b.app, '_load_active_llm_config', unavailable)
            else:
                monkeypatch.setattr(b.app, '_load_active_llm_config', lambda: dict(snapshot[1]))
                monkeypatch.setattr(b.app, '_create_model_from_llm_config', unavailable)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': '你好'}))
                assert result['status'] == 'failed'
            assert not b.calls and not b.admission_calls
            assert b.app.model is snapshot[0] and b.app.active_llm_config is snapshot[1]
            assert b.app.model_generation == snapshot[2]
            assert b.app.chat_handler.model is snapshot[0] and b.app.agent_system.llm is snapshot[0]
            assert not b.app.decision_runtime.active_owners and b.app.model_request_gate._readers == 0
    asyncio.run(run())


def test_receiver_task_creation_cancellation_closes_unscheduled_coroutine(actual_app, monkeypatch):
    from src.web import decision_runtime
    async def run():
        async with actual_app(mode='decision_a2') as b:
            socket = ActualSocket(b.app, await cookie_for(b.app))
            await socket.__aenter__()
            await socket.ready()
            create_task, unscheduled = asyncio.create_task, []
            def inject(coro, **kwargs):
                if getattr(coro, 'cr_code', None) is decision_runtime.WebDecisionRuntime._execute.__code__:
                    unscheduled.append(coro)
                    raise asyncio.CancelledError()
                return create_task(coro, **kwargs)
            monkeypatch.setattr(asyncio, 'create_task', inject)
            try:
                await socket.send({'message': '你好'})
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(socket.task, 3)
                assert len(unscheduled) == 1
                assert inspect.getcoroutinestate(unscheduled[0]) == inspect.CORO_CLOSED
                assert not b.app.decision_runtime.active_owners and not b.calls
            finally:
                for coro in unscheduled:
                    coro.close()
                await asyncio.gather(socket.task, return_exceptions=True)
    asyncio.run(run())


def test_shutdown_during_accept_send_cannot_start_late_turn(actual_app, monkeypatch):
    from starlette.websockets import WebSocket
    from src.web import decision_runtime
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        started_after_shutdown = []
        send_text, create_task = WebSocket.send_text, asyncio.create_task
        async def blocked_send(self, text):
            if json.loads(text)['type'] == 'request_accepted':
                entered.set()
                await release.wait()
            await send_text(self, text)
        async with actual_app(mode='decision_a2') as b:
            def observe_create(coro, **kwargs):
                if getattr(coro, 'cr_code', None) is decision_runtime.WebDecisionRuntime._execute.__code__:
                    started_after_shutdown.append(b.app.decision_runtime.closing)
                return create_task(coro, **kwargs)
            monkeypatch.setattr(WebSocket, 'send_text', blocked_send)
            monkeypatch.setattr(asyncio, 'create_task', observe_create)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send({'message': '你好'})
                await asyncio.wait_for(entered.wait(), 3)
                try:
                    await asyncio.wait_for(b.app.shutdown(), 3)
                finally:
                    release.set()
                await asyncio.wait_for(socket.task, 3)
                assert not started_after_shutdown, 'shutdown must seal owner admission across send await'
                assert not b.calls and not b.admission_calls
                assert not b.app.decision_runtime.active_owners
    asyncio.run(run())


@pytest.mark.parametrize('adapter_first,delayed_child', [(True, False), (False, False), (False, True)])
@pytest.mark.parametrize('ending', ['timeout', 'cancel', 'disconnect', 'shutdown', 'cancel-cleanup'])
def test_actual_route_nested_timeout_retains_physical_workers(
        actual_app, monkeypatch, adapter_first, delayed_child, ending):
    from test_decision_loop import tool
    from test_worker_ownership import signalled
    from src.agent.orchestrators.workflow import WorkflowOrchestrator
    from src.agent.contracts import AgentErrorCode
    from src.web import decision_runtime

    async def run():
        entered, exited, release, timed_out, allow_join = (threading.Event() for _ in range(5))
        allow_child = threading.Event()
        join_started = threading.Event()
        records = route_executors(monkeypatch, allow_join, join_started)
        original_step = WorkflowOrchestrator._execute_step
        def observe_timeout(self, *args, **kwargs):
            result = original_step(self, *args, **kwargs)
            if result.error and result.error.code == AgentErrorCode.TOOL_TIMEOUT:
                timed_out.set()
            return result
        monkeypatch.setattr(WorkflowOrchestrator, '_execute_step', observe_timeout)
        if not adapter_first:
            # Real loop, with only its existing run deadline shortened. No run,
            # Session, admission, result or executor future is substituted.
            monkeypatch.setattr(decision_runtime, 'ModelDecisionLoop',
                                partial(decision_runtime.ModelDecisionLoop, timeout_seconds=2))
        async def respond(payload):
            return tool().model_dump()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            runtime, gate = b.app.decision_runtime, b.app.model_request_gate
            adapter = runtime.registry.resolve('property_calculator')
            monkeypatch.setattr(adapter, 'spec', replace(adapter.spec, timeout_seconds=0.03 if adapter_first else 4))
            if delayed_child:
                execute = adapter.execute
                def delayed_execute(*args, **kwargs):
                    assert allow_child.wait(5), 'test must release child submission'
                    return execute(*args, **kwargs)
                monkeypatch.setattr(adapter, 'execute', delayed_execute)
            invoke = adapter.tool.execute
            def blocked(payload):
                entered.set()
                try:
                    assert release.wait(5), 'test must release real property worker'
                    return invoke(payload)
                finally:
                    exited.set()
            monkeypatch.setattr(adapter.tool, 'execute', blocked)
            closed = []
            async def close_model():
                closed.append('model')
            monkeypatch.setattr(b.model, 'close', close_model)
            socket = ActualSocket(b.app, await cookie_for(b.app))
            await socket.__aenter__()
            shutdown = switch = None
            try:
                await socket.ready()
                await socket.send({'message': '计算性质；SMILES: CCO'})
                accepted = await socket.receive()
                assert accepted['type'] == 'request_accepted'
                if delayed_child:
                    await signalled(timed_out)
                    assert len(records) == 1 and gate._readers == 1
                    allow_child.set()  # Child registration AFTER workflow timeout.
                if not await asyncio.to_thread(entered.wait, 3):
                    result = result_of(await receive_complete(socket))
                    pytest.fail('worker not entered: ' + str({
                        'status': result['status'], 'reason': result['metadata'].get('stop_reason'),
                        'calls': len(b.calls), 'executors': len(records)}))
                await signalled(timed_out)
                turn, = runtime.active_owners
                assert turn.worker_owner.pending_roots == 1
                epoch = b.app.model_generation
                if ending == 'cancel':
                    for _ in range(3):
                        await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                    await socket.send({'type': 'ping', 'timestamp': 0})
                    for _ in range(150):
                        frame = await socket.receive()
                        assert frame['type'] not in {'agent_result', 'complete'}
                        if frame['type'] == 'pong':
                            break
                    else:
                        pytest.fail('receiver did not answer ping during owned cleanup')
                elif ending == 'disconnect':
                    await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                elif ending == 'shutdown':
                    shutdown = asyncio.create_task(b.app.shutdown())
                elif ending == 'cancel-cleanup':
                    socket.task.cancel()
                else:
                    switch = asyncio.create_task(b.app._replace_llm_config(dict(b.app.active_llm_config)))
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()
                assert not exited.is_set() and not turn.task.done()
                assert turn in runtime.active_owners and gate._readers == 1
                assert not closed and b.app.model_generation == epoch
                assert not any(json.loads(f['text'])['type'] in {'agent_result', 'complete'}
                               for f in socket.outgoing._queue if f['type'] == 'websocket.send')
                release.set()
                await signalled(exited)
                for executor in records:
                    await signalled(executor.future_done)
                # The ownership ledger may join either nested executor first.
                await signalled(join_started)
                assert not turn.task.done() and gate._readers == 1
                assert turn in runtime.active_owners and not closed
                assert b.app.model_generation == epoch and not any(e.joined.is_set() for e in records)
                if ending == 'cancel-cleanup':
                    socket.task.cancel()
                allow_join.set()
                await asyncio.wait_for(asyncio.shield(turn.task), 4)
                assert turn.worker_owner.status == 'settled' and not runtime.active_owners
                assert len(records) == 2 and all(e.joined.is_set() for e in records)
                assert len(b.calls) == 1, 'timeout/late success must never replay the provider or tool'
                if ending in {'timeout', 'cancel'}:
                    result = result_of(await receive_complete(socket))
                    assert result['status'] in {'failed', 'cancelled', 'partial'} and not result['success']
                    if ending == 'cancel':
                        assert result['status'] == 'cancelled'
                    else:
                        assert any(row.get('error', {}).get('code') == 'tool_timeout'
                                   for row in result['tool_result_sequence'])
                else:
                    assert not any(json.loads(f['text'])['type'] in {'agent_result', 'complete'}
                                   for f in socket.outgoing._queue if f['type'] == 'websocket.send')
                if switch:
                    await asyncio.wait_for(switch, 3)
                if shutdown:
                    await asyncio.wait_for(shutdown, 3)
                    assert closed == ['model']
                assert gate._readers == 0
            finally:
                allow_child.set()
                release.set()
                allow_join.set()
                if ending == 'cancel-cleanup':
                    await socket.incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                    await asyncio.wait_for(asyncio.gather(socket.task, return_exceptions=True), 5)
                else:
                    await socket.__aexit__(None, None, None)
                await asyncio.gather(*(t for t in (switch, shutdown) if t), return_exceptions=True)
                for executor in records:
                    await asyncio.to_thread(executor.shutdown, wait=True)
            await b.app.shutdown()
            assert closed == ['model']
    asyncio.run(run())


@pytest.mark.parametrize('writer', ['replace', 'persist'])
@pytest.mark.parametrize('consumer_failure', [False, True])
@pytest.mark.parametrize('credential_only', [False, True])
def test_common_model_publisher_is_coherent_at_consumer_boundary(
        actual_app, monkeypatch, writer, consumer_failure, credential_only):
    """Real app/consumer and adapter; only config I/O and failure boundary injected."""
    import httpx
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    from src.web import app as app_module
    from test_web_decision_runtime import protocol_response

    async def run():
        async with actual_app(mode='decision_a2') as b:
            old_model, old_config, old_epoch = b.app.model, b.app.active_llm_config, b.app.model_generation
            old_stream = b.app.config['inference']['stream']
            generator = b.app.molecular_generator_model
            generator_tool = b.app.agent_system.tools['llm_molecular_generator']
            generator_binding = generator_tool.llm
            closes, boundaries = [], []
            async def close_old():
                closes.append('old')
            monkeypatch.setattr(old_model, 'close', close_old)
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: protocol_response(chat_decision('rotated response')))) as client:
                replacement = OpenAICompatibleModel('synthetic-rotation-only', 'protocol-only',
                                                     'https://example.invalid/v1', client=client)
                async def close_new():
                    closes.append('new')
                monkeypatch.setattr(replacement, 'close', close_new)
                monkeypatch.setattr(b.app, '_create_model_from_llm_config', lambda config: replacement)
                config = dict(old_config, api_key='synthetic-rotation-only', stream=not old_stream)
                if credential_only:
                    config['stream'] = old_stream
                monkeypatch.setattr(app_module, 'save_user_llm_config',
                                    lambda *args, **kwargs: (config, 'synthetic-signature'))
                original_set = b.app.agent_system.set_llm
                def set_llm(model):
                    if model is replacement:
                        boundaries.append((b.app.model is old_model,
                            b.app.active_llm_config is old_config, b.app.model_generation == old_epoch,
                            b.app.config['inference']['stream'] == old_stream,
                            b.app.model_request_gate._writing, b.app.model_request_gate._readers))
                    original_set(model)
                    if model is replacement and consumer_failure:
                        raise RuntimeError('synthetic consumer publication failure')
                monkeypatch.setattr(b.app.agent_system, 'set_llm', set_llm)
                operation = b.app._replace_llm_config if writer == 'replace' else b.app._persist_user_llm_config
                if consumer_failure:
                    with pytest.raises(RuntimeError, match='synthetic consumer publication failure'):
                        await operation(config)
                    assert b.app.model is old_model
                    assert b.app.active_llm_config is old_config and b.app.model_generation == old_epoch
                    assert b.app.config['inference']['stream'] == old_stream
                    assert b.app.chat_handler.model is old_model and b.app.agent_system.llm is old_model
                    assert closes == ['new']
                else:
                    await operation(config)
                    assert b.app.model is replacement and b.app.chat_handler.model is replacement
                    assert b.app.agent_system.llm is replacement
                    assert b.app.active_llm_config == config
                    assert len(b.app.model_generation) == 32 and b.app.model_generation != old_epoch
                    assert closes == ['old']
                assert boundaries == [(True, True, True, True, True, 0)], boundaries
                assert b.app.molecular_generator_model is generator
                assert generator_tool.llm is generator_binding
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    await socket.ready()
                    frames = await socket.turn({'message': '你好'})
                    answer = 'Protocol-only explanation' if consumer_failure else 'rotated response'
                    assert result_of(frames)['final_answer'] == answer
                    assert 'synthetic-rotation-only' not in json.dumps(frames)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['run', 'watch_cancel'])
def test_watcher_creation_failure_cannot_release_running_provider(actual_app, monkeypatch, failure):
    """SPEC actual-route probe, including safe rejection before provider start."""
    async def run():
        entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        children, unscheduled, snapshots = [], [], []
        create_task = asyncio.create_task

        async def respond(payload):
            entered.set()
            try:
                await release.wait()
                return chat_decision('released provider')
            finally:
                exited.set()

        def inject(coro, *args, **kwargs):
            is_run = kwargs.get('name') == 'isolated-decision-chat'
            is_watch = getattr(getattr(coro, 'cr_code', None), 'co_name', None) == 'watch_cancel'
            if (failure == 'run' and is_run) or (failure == 'watch_cancel' and is_watch):
                unscheduled.append(coro)
                raise RuntimeError('synthetic scheduling failure')
            task = create_task(coro, *args, **kwargs)
            if is_run:
                children.append(task)
            return task

        async with actual_app(mode='decision_a2', respond=respond) as b:
            from src.web import decision_runtime
            send = decision_runtime._Sender.send_text
            async def observe(self, text, **kwargs):
                if json.loads(text)['type'] in {'agent_result', 'complete'}:
                    snapshots.append(dict(provider_entered=entered.is_set(), provider_exited=exited.is_set(),
                        child_done=all(t.done() for t in children)))
                return await send(self, text, **kwargs)
            monkeypatch.setattr(decision_runtime._Sender, 'send_text', observe)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                monkeypatch.setattr(asyncio, 'create_task', inject)
                try:
                    frames = await socket.turn({'message': '你好'})
                    assert result_of(frames)['status'] == 'failed'
                    if failure == 'watch_cancel' and any(not t.done() for t in children):
                        # Preserve the old SPEC failure as a positive diagnostic:
                        # the leaked real provider starts AFTER premature terminal.
                        # Correct rollback may cancel before dispatch, so it never
                        # needs this wait. No sleep or production scheduling hook.
                        await asyncio.wait_for(entered.wait(), 3)
                    snapshots.append(dict(provider_entered=entered.is_set(), provider_exited=exited.is_set(),
                        child_done=all(t.done() for t in children),
                        readers=b.app.model_request_gate._readers,
                        active_owners=len(b.app.decision_runtime.active_owners)))
                    assert snapshots and all(s['child_done'] and (
                        not s['provider_entered'] or s['provider_exited']) for s in snapshots), snapshots
                    assert unscheduled and all(inspect.getcoroutinestate(c) == inspect.CORO_CLOSED
                                               for c in unscheduled)
                    assert b.app.model_request_gate._readers == 0
                    assert not b.app.decision_runtime.active_owners
                finally:
                    # Even RED exits retain all created tasks and close the
                    # unscheduled coroutines, with no test/production escape.
                    release.set()
                    for coro in unscheduled:
                        coro.close()
                    await asyncio.wait_for(asyncio.gather(*children, return_exceptions=True), 5)
    asyncio.run(run())


def test_cancel_blocked_provider_then_next_turn_on_same_socket(actual_app):
    async def run():
        entered, released = asyncio.Event(), asyncio.Event()
        count = 0

        async def respond(payload):
            nonlocal count
            count += 1
            if count == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    released.set()
            return chat_decision('next turn')

        async with actual_app(mode='decision_a2', respond=respond) as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                await socket.send({'type': 'chat', 'message': '你好'})
                accepted = await socket.receive()
                assert accepted['type'] == 'request_accepted'
                await asyncio.wait_for(entered.wait(), 3)
                await socket.send({'type': 'ping', 'timestamp': 0})
                for _ in range(15):
                    frame = await socket.receive()
                    if frame['type'] == 'pong':
                        break
                else:
                    assert False, 'receiver stopped during model dispatch'
                assert frame['timestamp'] == 0
                await socket.send({'type': 'cancel', 'turn_id': accepted['turn_id']})
                frames = []
                for _ in range(20):
                    frame = await socket.receive()
                    frames.append(frame)
                    if frame['type'] == 'complete':
                        break
                assert result_of(frames)['status'] == 'cancelled'
                assert released.is_set()
                assert b.app.model_request_gate._readers == 0
                assert not b.app.decision_runtime.active_owners
                assert result_of(await socket.turn({'message': '你好'}))['final_answer'] == 'next turn'
    asyncio.run(run())


def test_sender_deadline_includes_serialization_lock(monkeypatch):
    from src.web import decision_runtime
    async def run():
        monkeypatch.setattr(decision_runtime, 'SEND_TIMEOUT_SECONDS', 0.03)
        sent = []
        async def send_text(text):
            sent.append(text)
        sender = decision_runtime._Sender(SimpleNamespace(scope={}, send_text=send_text))
        await sender.lock.acquire()
        task = asyncio.create_task(sender.send_text('{}'))
        try:
            done, _ = await asyncio.wait({task}, timeout=0.2)
            assert done, 'waiting for send lock escaped the total deadline'
            with pytest.raises(asyncio.TimeoutError):
                task.result()
            assert not sender.writable and not sent
        finally:
            sender.lock.release()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['accept-send', 'task-create'])
def test_failed_turn_admission_leaves_no_orphan_owner(actual_app, monkeypatch, failure):
    from src.web import decision_runtime
    async def run():
        async with actual_app(mode='decision_a2') as b:
            sender = decision_runtime._Sender.send_text
            create_task = asyncio.create_task
            hit = asyncio.Event()
            async def fail_send(self, text, **kwargs):
                if json.loads(text)['type'] == 'request_accepted':
                    hit.set()
                    raise ConnectionError('synthetic transport failure')
                return await sender(self, text, **kwargs)
            def fail_create(coro, **kwargs):
                if getattr(coro, 'cr_code', None) is decision_runtime.WebDecisionRuntime._execute.__code__:
                    hit.set()
                    raise RuntimeError('synthetic scheduling failure')
                return create_task(coro, **kwargs)
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                if failure == 'accept-send':
                    monkeypatch.setattr(decision_runtime._Sender, 'send_text', fail_send)
                else:
                    monkeypatch.setattr(asyncio, 'create_task', fail_create)
                await socket.send({'message': '你好'})
                await asyncio.wait_for(hit.wait(), 3)
            assert not b.app.decision_runtime.active_owners
            assert not b.app.decision_runtime.tasks
            assert b.app.model_request_gate._readers == 0
            assert b.calls == []
    asyncio.run(run())


def test_ws_switch_waits_for_captured_model_and_uses_new_epoch_next_turn(actual_app, monkeypatch):
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    import httpx
    from test_web_decision_runtime import protocol_response

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(payload):
            entered.set()
            await release.wait()
            return chat_decision('old epoch')
        async with actual_app(mode='decision_a2', respond=respond) as b:
            old_epoch = b.app.model_generation
            closed = []
            async def close():
                closed.append(True)
            monkeypatch.setattr(b.model, 'close', close)
            new_calls = []
            def reply(request):
                new_calls.append(True)
                return protocol_response(chat_decision('new epoch'))
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                new_model = OpenAICompatibleModel('synthetic-rotated-key', 'protocol-only',
                                                  'https://example.invalid/v1', client=client)
                monkeypatch.setattr(b.app, '_create_model_from_llm_config', lambda config: new_model)
                async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                    await socket.ready()
                    await socket.send({'message': '你好'})
                    await asyncio.wait_for(entered.wait(), 3)
                    switch = asyncio.create_task(b.app._replace_llm_config(dict(b.app.active_llm_config)))
                    try:
                        scheduled = asyncio.Event()
                        asyncio.get_running_loop().call_soon(scheduled.set)
                        await scheduled.wait()
                        assert b.app.model_request_gate._writers == 1
                        assert not switch.done() and not closed
                        assert b.app.model_generation == old_epoch
                        assert b.app.model_request_gate._readers == 1
                    finally:
                        release.set()
                    frames = []
                    for _ in range(20):
                        frame = await socket.receive()
                        frames.append(frame)
                        if frame['type'] == 'complete':
                            break
                    assert result_of(frames)['final_answer'] == 'old epoch'
                    await asyncio.wait_for(switch, 3)
                    assert len(closed) == 1 and b.app.model_generation != old_epoch
                    assert result_of(await socket.turn({'message': '你好'}))['final_answer'] == 'new epoch'
                    assert len(new_calls) == 1
    asyncio.run(run())


def test_pending_writer_blocks_second_socket_without_nested_reader_deadlock(actual_app, monkeypatch):
    import httpx
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    from test_web_decision_runtime import protocol_response
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(payload):
            entered.set()
            await release.wait()
            return chat_decision('captured old model')
        async with actual_app(mode='decision_a2', respond=respond) as b:
            gate = b.app.model_request_gate
            epoch, closed, new_requests = b.app.model_generation, [], []
            async def close_old():
                closed.append(True)
            monkeypatch.setattr(b.model, 'close', close_old)
            def transport(request):
                new_requests.append(True)
                assert gate._readers == 1 and b.app.model_generation != epoch
                return protocol_response(chat_decision('captured new model'))
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                model = OpenAICompatibleModel('synthetic-replacement', 'protocol-only',
                                              'https://example.invalid/v1', client=client)
                monkeypatch.setattr(b.app, '_create_model_from_llm_config', lambda config: model)
                cookie = await cookie_for(b.app)
                async with ActualSocket(b.app, cookie) as first, ActualSocket(b.app, cookie) as second:
                    await first.ready()
                    await second.ready()
                    await first.send({'message': '你好'})
                    await asyncio.wait_for(entered.wait(), 3)
                    switch = None
                    try:
                        async with gate.request():
                            assert gate._readers == 2, 'unrelated pre-writer reader must be concurrent'
                            switch = asyncio.create_task(b.app._replace_llm_config(dict(b.app.active_llm_config)))
                            scheduled = asyncio.Event()
                            asyncio.get_running_loop().call_soon(scheduled.set)
                            await scheduled.wait()
                            assert gate._writers == 1
                            await second.send({'message': '你好'})
                            assert (await second.receive())['type'] == 'request_accepted'
                            await second.send({'type': 'ping', 'timestamp': 0})
                            assert (await second.receive())['type'] == 'pong'
                            assert gate._readers == 2 and len(b.calls) == len(b.admission_calls) == 1
                            assert not closed and not new_requests and not switch.done()
                        assert gate._readers == 1 and b.app.model_generation == epoch
                    finally:
                        release.set()
                    assert result_of(await receive_complete(first))['final_answer'] == 'captured old model'
                    await asyncio.wait_for(switch, 3)
                    assert result_of(await receive_complete(second))['final_answer'] == 'captured new model'
                    assert closed == [True] and new_requests == [True]
                    assert gate._readers == 0
    asyncio.run(run())


def test_client_without_decide_has_no_fallback_on_actual_route(actual_app, monkeypatch):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            def forbidden(*args, **kwargs):
                pytest.fail('unsupported main client must not fall back')
            unsupported = SimpleNamespace(generate=forbidden, generate_for_chat=forbidden)
            monkeypatch.setattr(b.app, '_create_model_from_llm_config', lambda config: unsupported)
            await b.app._replace_llm_config(dict(b.app.active_llm_config))
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': '你好'}))
                assert result['status'] == 'failed' and not result['success']
                assert not b.calls and not b.app.decision_runtime.active_owners
    asyncio.run(run())


def test_shutdown_with_active_and_idle_socket_stops_admission_before_close(actual_app, monkeypatch):
    async def run():
        entered, cleanup_entered, release, exited = (asyncio.Event() for _ in range(4))
        async def respond(payload):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await release.wait()
                exited.set()
        async with actual_app(mode='decision_a2', respond=respond) as b:
            closes = []
            async def close():
                closes.append(True)
            monkeypatch.setattr(b.model, 'close', close)
            cookie = await cookie_for(b.app)
            async with ActualSocket(b.app, cookie) as active, ActualSocket(b.app, cookie) as idle:
                await active.ready()
                await idle.ready()
                await active.send({'message': '你好'})
                await asyncio.wait_for(entered.wait(), 3)
                turn, = b.app.decision_runtime.active_owners
                shutdown = asyncio.create_task(b.app.shutdown())
                try:
                    await asyncio.wait_for(cleanup_entered.wait(), 3)
                    await idle.send({'message': 'Explain logP'})
                    await asyncio.wait_for(idle.task, 3)
                    assert not shutdown.done() and not exited.is_set() and not closes
                    assert b.app.decision_runtime.active_owners == {turn}
                    assert b.app.model_request_gate._readers == 1
                    assert len(b.calls) == len(b.admission_calls) == 1
                    assert idle.outgoing.empty(), 'idle shutdown cannot acknowledge another turn'
                finally:
                    release.set()
                    await asyncio.wait_for(shutdown, 4)
                assert exited.is_set() and closes == [True]
                assert b.app.model_request_gate._readers == 0
                assert not b.app.decision_runtime.active_owners
                assert not any(json.loads(f['text'])['type'] in {'agent_result', 'complete'}
                               for f in active.outgoing._queue if f['type'] == 'websocket.send')
    asyncio.run(run())
