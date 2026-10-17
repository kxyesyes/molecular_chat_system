"""Prepare request deadline covers its body; no scientific backend calls."""
import asyncio
import json
import threading

import pytest
from starlette.requests import Request

from tests.agent.test_web_docking_http import app_for, body, ORIGIN, PATH
from tests.task_runtime.test_chat_docking_input_context import authenticated_rig, target_case
from tests.task_runtime.test_chat_docking_execution_context import execution_case
from tests.agent.test_web_docking_approval_deadlines import direct_approval
from tests.agent.test_web_docking_approval_http import approval


def direct_prepare(app, case, receive):
    route = next(route for route in app.routes if route.path == PATH and 'POST' in route.methods)
    scope = dict(type='http', method='POST', scheme='https', path=PATH,
        query_string=b'', server=('localhost', 443),
        headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode()),
                 (b'content-type', b'application/json')], agent_session_id=case.root.owner)
    return route.endpoint(request=Request(scope, receive))


@pytest.mark.parametrize('disconnect', [False, True])
def test_prepare_late_body_cannot_stage_after_feedback_closes(tmp_path, target_case, monkeypatch, disconnect):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch, capture_context=False) as case:
            app, _, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []
            async def receive():
                calls.append(True)
                if len(calls) == 1:
                    # Empty streamed chunks cannot create a fresh body budget.
                    return dict(type='http.request', body=b'', more_body=True)
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
                return dict(type='http.request', body=json.dumps(body(case)).encode(), more_body=False)
            call = asyncio.create_task(direct_prepare(app, case, receive))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                if disconnect:
                    call.cancel()
                done, _ = await asyncio.wait({call}, timeout=1 if disconnect else 31.5)
                assert done, 'prepare HTTP body ignored the absolute deadline or drained disconnect'
                if disconnect:
                    assert call.cancelled()
                else:
                    try:
                        response = call.result()
                    except Exception as exc:
                        assert getattr(exc, 'status_code', None) in {408, 503}
                    else:
                        assert response.status_code in {408, 503}
                assert not case.stages and not case.calls and not case.root.staging_started
                assert any(not task.done() for task in case.rig.gate._background)
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert not case.stages and not case.calls and not case.root.staging_started
    asyncio.run(run())


@pytest.mark.parametrize('disconnect', [False, True])
def test_prepare_closes_approval_but_retains_physical_readback(tmp_path, target_case, monkeypatch, disconnect):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch, capture_context=False) as case:
            app, _, _ = app_for(case, tmp_path)
            entered, release = threading.Event(), threading.Event()
            original = case.stager.read_verified_input_bytes
            def held(*args):
                entered.set()
                assert release.wait(45), 'physical readback barrier was not released'
                return original(*args)
            monkeypatch.setattr(case.stager, 'read_verified_input_bytes', held)
            async def receive():
                return dict(type='http.request', body=json.dumps(body(case)).encode(), more_body=False)
            call = asyncio.create_task(direct_prepare(app, case, receive))
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                if disconnect:
                    call.cancel()
                done, _ = await asyncio.wait({call}, timeout=1 if disconnect else 31.5)
                assert done, 'prepare feedback drained a live physical read'
                if disconnect:
                    assert call.cancelled()
                else:
                    try:
                        response = call.result()
                    except Exception as exc:
                        assert getattr(exc, 'status_code', None) in {408, 503}
                    else:
                        assert response.status_code in {408, 503}
                assert case.root.closed and case.root.phase != 'READY'
                assert case.coordinator._roots.get(case.root.root_id) is case.root
                assert case.runtime._consent_preparations
                assert not case.calls
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
                await case.runtime.close()
            row = case.rig.tasks.get_docking_consent(case.root.preparation_id)
            assert row['state'] != 'READY' and row['approval_nonce_hash'] is None
            assert not case.calls and not case.runtime._consent_preparations
    asyncio.run(run())


def test_concurrent_prepare_does_not_share_one_time_nonce(tmp_path, target_case, monkeypatch):
    async def run():
        async with authenticated_rig(tmp_path, target_case, monkeypatch, capture_context=False) as case:
            app, _, _ = app_for(case, tmp_path)
            entered, release = threading.Event(), threading.Event()
            original = case.stager.read_verified_input_bytes
            def held(*args):
                entered.set()
                assert release.wait(15), 'physical readback barrier was not released'
                return original(*args)
            monkeypatch.setattr(case.stager, 'read_verified_input_bytes', held)
            async def receive():
                return dict(type='http.request', body=json.dumps(body(case)).encode(), more_body=False)
            first = asyncio.create_task(direct_prepare(app, case, receive))
            second = None
            try:
                assert await asyncio.to_thread(entered.wait, 8)
                second = asyncio.create_task(direct_prepare(app, case, receive))
                # The repeat is a read-only status observation, not another
                # waiter entitled to the initiating call's one-time nonce.
                done, _ = await asyncio.wait({second}, timeout=2)
                assert done, 'repeat preparation joined nonce-bearing initial response'
                repeated = second.result()
                assert repeated.status_code in {200, 202}
                assert 'approval_nonce' not in repeated.body.decode()
            finally:
                release.set()
                results = await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert not isinstance(results[0], BaseException)
            assert results[0].status_code == 201
            assert json.loads(results[0].body)['consent_preview']['approval_nonce']
            assert len(case.stages) == 1 and not case.calls
    asyncio.run(run())


def test_repeat_prepare_timeout_cannot_cancel_consumed_c2(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, _, _ = app_for(case, tmp_path)
            raw_entered, raw_release = threading.Event(), threading.Event()
            read_entered, read_release = threading.Event(), threading.Event()
            def hold_raw():
                raw_entered.set()
                assert raw_release.wait(60), 'synthetic executor not released'
            case.raw_hook = hold_raw
            call, operation = None, None
            try:
                async def approval_receive():
                    return dict(type='http.request', body=json.dumps(approval(case)).encode(), more_body=False)
                accepted = await direct_approval(app, case, approval_receive)
                assert accepted.status_code in {200, 202}
                assert await asyncio.to_thread(raw_entered.wait, 4)
                operation = case.context._execution
                assert operation.acquired and not operation.signal.is_set() and not case.root.closed
                original = case.coordinator._check_execution_source
                def held_status_read(**kwargs):
                    read_entered.set()
                    assert read_release.wait(15), 'status read not released'
                    return original(**kwargs)
                monkeypatch.setattr(case.coordinator, '_check_execution_source', held_status_read)
                async def prepare_receive():
                    # Consume the real route budget, leaving less than a second
                    # for a read-only status observation of an approved job.
                    await asyncio.sleep(29.6)
                    return dict(type='http.request', body=json.dumps(body(case)).encode(), more_body=False)
                call = asyncio.create_task(direct_prepare(app, case, prepare_receive))
                done, _ = await asyncio.wait({call}, timeout=31.5)
                assert done, 'prepare feedback exceeded its bounded deadline'
                outcome, = await asyncio.gather(call, return_exceptions=True)
                status = getattr(outcome, 'status_code', None)
                assert status in {409, 503}, repr(outcome)
                if status == 503:
                    assert read_entered.is_set()
                assert not operation.signal.is_set(), 'repeat prepare cancelled an independently approved C2'
                assert not case.root.closed
            finally:
                read_release.set()
                raw_release.set()
                if call is not None:
                    await asyncio.gather(call, return_exceptions=True)
                await asyncio.wait_for(asyncio.gather(*tuple(case.rig.gate._background),
                    return_exceptions=True), 12)
                if operation is not None and operation.settlement is not None:
                    await asyncio.wait_for(asyncio.shield(operation.settlement), 12)
            assert operation.settled and operation.status == 'SUCCEEDED'
    asyncio.run(run())
