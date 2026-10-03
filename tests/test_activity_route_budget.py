"""Request lifetime is not physical worker lifetime; no model assets are used."""
import asyncio
import threading

import pytest
from fastapi import HTTPException

from src.web.routes import activity_prediction_routes as routes, api_routes


@pytest.fixture
def admission(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(routes, "_ACTIVITY_ADMISSION", slots)
    monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", "0.1")
    return slots


def test_success_and_worker_exceptions_release_capacity(admission):
    async def exercise():
        for error in (None, RuntimeError, TimeoutError, None):
            def compute():
                if error:
                    raise error("synthetic worker error")
                return {"status": "failed", "results": []}

            call = routes._invoke_activity_with_budget(api_routes, compute, operation="test")
            if error:
                with pytest.raises(error):
                    await call
            else:
                assert await call == {"status": "failed", "results": []}
            assert admission.acquire(blocking=False), "completed call leaked a slot"
            admission.release()

    asyncio.run(exercise())


@pytest.mark.parametrize("end_request", ["timeout", "cancel"])
@pytest.mark.parametrize("late_error", [False, True])
def test_running_thread_retains_slot_until_physical_exit(admission, monkeypatch, end_request, late_error):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def compute():
        entered.set()
        try:
            assert release.wait(5)
            if late_error:
                raise RuntimeError("private synthetic failure")
            return {"unexpected_late_result": True}
        finally:
            exited.set()

    async def exercise():
        # Real offload, with a signal confirming the slot-owning wrapper returned.
        settled = asyncio.Event()
        original = api_routes._invoke_in_threadpool

        async def dispatch(func):
            try:
                return await original(func)
            finally:
                settled.set()

        monkeypatch.setattr(api_routes, "_invoke_in_threadpool", dispatch)
        if end_request == "cancel":
            monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", "5")
        task = asyncio.create_task(routes._invoke_activity_with_budget(api_routes, compute, operation="test"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if end_request == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(HTTPException) as error:
                    await task
                assert error.value.status_code == 504
                assert error.value.detail["code"] == "ACTIVITY_REQUEST_TIMEOUT"
                assert error.value.detail["compute_disposition"] == "draining"
            assert not exited.is_set()
            with pytest.raises(HTTPException) as busy:
                await routes._invoke_activity_with_budget(api_routes, lambda: pytest.fail("over capacity"), operation="test")
            assert busy.value.status_code == 429
        finally:
            release.set()
            await asyncio.wait_for(settled.wait(), 3)
            await asyncio.gather(task, return_exceptions=True)
        assert exited.is_set()
        assert admission.acquire(blocking=False)
        admission.release()

    asyncio.run(exercise())


def test_loop_shutdown_does_not_release_a_still_running_thread(admission):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def compute():
        entered.set()
        try:
            assert release.wait(5)
        finally:
            exited.set()

    async def exercise():
        with pytest.raises(HTTPException) as error:
            await routes._invoke_activity_with_budget(api_routes, compute, operation="test")
        assert error.value.status_code == 504
        assert entered.is_set()

    try:
        asyncio.run(exercise())  # cancels all pending async wrappers on shutdown
        assert not exited.is_set()
        assert not admission.acquire(blocking=False), "async cancellation released a live thread"
    finally:
        release.set()
        assert exited.wait(3)
    assert admission.acquire(timeout=3)
    admission.release()


def test_timeout_before_dispatch_prevents_late_computation(admission):
    async def exercise():
        release, settled = asyncio.Event(), asyncio.Event()
        calls = []

        class DelayedSupport:
            async def _invoke_in_threadpool(self, function):
                try:
                    await release.wait()
                    return function()
                finally:
                    settled.set()

        try:
            with pytest.raises(HTTPException) as error:
                await routes._invoke_activity_with_budget(DelayedSupport(), lambda: calls.append(1), operation="test")
            assert error.value.status_code == 504
            assert admission.acquire(blocking=False), "queued work retained a slot after cancellation"
            admission.release()
        finally:
            release.set()
            await asyncio.wait_for(settled.wait(), 3)
        assert calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("value", ["inf", "nan", "-1", "0", "not-a-number"])
def test_invalid_timeout_cannot_disable_budget(monkeypatch, value):
    monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", value)
    assert routes._positive_float_env("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", 60.) == 60.
