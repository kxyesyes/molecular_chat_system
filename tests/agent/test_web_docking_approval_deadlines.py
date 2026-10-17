"""Approval HTTP lifetime is not a subscription; no real scientific services."""
import asyncio
import importlib
import json

import pytest
from starlette.requests import Request
from fastapi import HTTPException

from tests.agent.test_web_docking_http import app_for, ORIGIN
from tests.agent.test_web_docking_approval_http import approval, approve_path
from tests.task_runtime.test_chat_docking_execution_context import execution_case, target_case


def direct_approval(app, case, receive):
    route = next(route for route in app.routes
                 if route.path == '/api/agent/docking/preparations/{preparation_id}/approve')
    scope = dict(type='http', method='POST', scheme='https', path=approve_path(case),
        query_string=b'', server=('localhost', 443),
        headers=[(b'host', b'localhost'), (b'origin', ORIGIN.encode()),
                 (b'content-type', b'application/json')], agent_session_id=case.root.owner)
    return route.endpoint(preparation_id=case.root.preparation_id,
                          request=Request(scope, receive))


@pytest.mark.parametrize('disconnect', [False, True])
def test_late_approval_body_cannot_start_after_request_closes(tmp_path, target_case, monkeypatch, disconnect):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, _, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            async def receive():
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
                return dict(type='http.request', body=json.dumps(approval(case)).encode(), more_body=False)
            call = asyncio.create_task(direct_approval(app, case, receive))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if disconnect:
                    call.cancel()
                done, _ = await asyncio.wait({call}, timeout=6.5)
                assert done, 'approval HTTP waited for a non-cooperative request body'
                if disconnect:
                    assert call.cancelled()
                else:
                    try:
                        response = call.result()
                    except Exception as exc:
                        assert getattr(exc, 'status_code', None) in {408, 503}
                    else:
                        assert response.status_code in {408, 503}
                assert case.context._execution is None and not case.raw
                assert any(not task.done() for task in case.rig.gate._background)
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert case.context._execution is None and not case.raw
    asyncio.run(run())


def test_accepted_approval_does_not_require_http_caller_to_stay_alive(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, _, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            original = case.runtime._admit_consent_execution
            async def held(operation):
                entered.set()
                await release.wait()
                return await original(operation)
            monkeypatch.setattr(case.runtime, '_admit_consent_execution', held)
            async def receive():
                return dict(type='http.request', body=json.dumps(approval(case)).encode(), more_body=False)
            call = asyncio.create_task(direct_approval(app, case, receive))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                operation = case.context._execution
                assert operation is not None
                call.cancel()
                done, _ = await asyncio.wait({call}, timeout=1)
                assert done and call.cancelled(), 'HTTP disconnect waited for owned admission'
                assert not operation.signal.is_set() and not case.root.closed
                assert any(not task.done() for task in case.rig.gate._background)
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            if operation.settlement is not None:
                await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert case.raw == [True] and operation.status == 'SUCCEEDED' and operation.settled
    asyncio.run(run())


def test_wrong_proof_cannot_join_pending_approval(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            app, _, _ = app_for(case, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()
            original = case.runtime._admit_consent_execution
            admissions = []
            async def held(operation):
                admissions.append(operation)
                entered.set()
                await release.wait()
                return await original(operation)
            monkeypatch.setattr(case.runtime, '_admit_consent_execution', held)
            async def receive():
                return dict(type='http.request', body=json.dumps(approval(case)).encode(), more_body=False)
            async def bad_receive():
                value = dict(approval(case), approval_nonce='0' * 64)
                return dict(type='http.request', body=json.dumps(value).encode(), more_body=False)
            call = asyncio.create_task(direct_approval(app, case, receive))
            bad = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                bad = asyncio.create_task(direct_approval(app, case, bad_receive))
                done, _ = await asyncio.wait({bad}, timeout=1)
                assert done, 'mismatched proof joined a pending valid approval'
                try:
                    response = bad.result()
                except HTTPException as exc:
                    assert exc.status_code == 409
                else:
                    assert response.status_code == 409
                assert len(admissions) == 1 and not case.root.closed
            finally:
                release.set()
                await asyncio.gather(call, *([bad] if bad else []), return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            operation = case.context._execution
            await asyncio.wait_for(asyncio.shield(operation.settlement), 10)
            assert case.raw == [True] and operation.settled
    asyncio.run(run())


def test_approval_body_capacity_survives_disconnect_and_app_registration(tmp_path, target_case, monkeypatch):
    async def run():
        async with execution_case(tmp_path, target_case, monkeypatch) as case:
            module = importlib.import_module('src.web.routes.docking_consent_routes')
            monkeypatch.setattr(module, '_MAX_CONTROL_TASKS', 2)
            app, _, _ = app_for(case, tmp_path)
            release = asyncio.Event()
            entered = [asyncio.Event(), asyncio.Event()]
            calls = []
            def receive_for(event):
                async def receive():
                    event.set()
                    while not release.is_set():
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            pass
                    return dict(type='http.request', body=json.dumps(approval(case)).encode(), more_body=False)
                return receive
            overflow = None
            overflow_reads = []
            async def overflow_receive():
                overflow_reads.append(True)
                return dict(type='http.request', body=json.dumps(approval(case)).encode(), more_body=False)
            try:
                for event in entered:
                    calls.append(asyncio.create_task(direct_approval(app, case, receive_for(event))))
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 2)
                for call in calls:
                    call.cancel()
                done, _ = await asyncio.wait(calls, timeout=1)
                assert len(done) == 2 and all(call.cancelled() for call in calls)
                second_app, _, _ = app_for(case, tmp_path)
                overflow = asyncio.create_task(direct_approval(second_app, case, overflow_receive))
                done, _ = await asyncio.wait({overflow}, timeout=1)
                assert done, 'shared physical body capacity was not enforced'
                try:
                    response = overflow.result()
                except HTTPException as exc:
                    assert exc.status_code == 503
                else:
                    assert response.status_code == 503
                assert not overflow_reads and case.context._execution is None and not case.raw
            finally:
                release.set()
                await asyncio.gather(*calls, *([overflow] if overflow else []), return_exceptions=True)
                await asyncio.gather(*tuple(case.rig.gate._background), return_exceptions=True)
            assert case.context._execution is None and not case.raw
    asyncio.run(run())
