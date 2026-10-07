"""Activity prediction resource limits and hard process cancellation."""

import asyncio
from pathlib import Path
import threading
import time

import pytest
from fastapi import HTTPException

from src.web.routes import activity_prediction_routes as routes, api_routes


def _success_activity_child(smiles: str, target: str | None):
    return {"kind": "result", "value": {"status": "succeeded", "smiles": smiles, "target": target}}


def _slow_activity_child(marker_path: str, _target: str):
    time.sleep(5.0)
    Path(marker_path).write_text("completed", encoding="utf-8")
    return {"kind": "result", "value": {"status": "succeeded"}}


@pytest.fixture
def admission(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(routes, "_ACTIVITY_ADMISSION", slots)
    monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", "5")
    return slots


def test_success_releases_capacity(admission):
    async def exercise():
        result = await routes._invoke_activity_with_budget(
            api_routes,
            operation="test",
            isolated_payload=("CC", "PDE5A"),
            isolated_target=_success_activity_child,
        )
        assert result == {"status": "succeeded", "smiles": "CC", "target": "PDE5A"}
        assert admission.acquire(blocking=False)
        admission.release()

    asyncio.run(exercise())


def test_capacity_is_reserved_until_process_is_stopped(admission, monkeypatch, tmp_path):
    marker = tmp_path / "marker.txt"
    monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", "0.5")

    async def exercise():
        task = asyncio.create_task(
            routes._invoke_activity_with_budget(
                api_routes,
                operation="isolated-test",
                isolated_payload=(str(marker), "PDE5A"),
                isolated_target=_slow_activity_child,
            )
        )
        with pytest.raises(HTTPException) as error:
            await task
        assert error.value.status_code == 504
        assert error.value.detail["compute_disposition"] == "terminated"
        assert admission.acquire(blocking=False)
        admission.release()

    asyncio.run(exercise())
    assert not marker.exists()


def test_cancel_terminates_child_and_releases_capacity(admission, monkeypatch, tmp_path):
    marker = tmp_path / "marker.txt"
    monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", "5")

    async def exercise():
        task = asyncio.create_task(
            routes._invoke_activity_with_budget(
                api_routes,
                operation="isolated-test",
                isolated_payload=(str(marker), "PDE5A"),
                isolated_target=_slow_activity_child,
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert admission.acquire(blocking=False)
        admission.release()

    asyncio.run(exercise())
    assert not marker.exists()


def test_hung_process_start_consumes_activity_budget(admission, monkeypatch):
    class HungStartProcess:
        def start(self):
            time.sleep(0.5)

        def terminate(self):
            pass

        def close(self):
            pass

        def is_alive(self):
            return False

    monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(routes, "IsolatedProcess", lambda *args, **kwargs: HungStartProcess())

    async def exercise():
        started = time.monotonic()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(
                routes._invoke_activity_with_budget(
                    api_routes,
                    operation="isolated-test",
                    isolated_payload=("CC", "PDE5A"),
                    isolated_target=_success_activity_child,
                ),
                timeout=0.25,
            )
        assert error.value.status_code == 504
        assert error.value.detail["code"] == "ACTIVITY_REQUEST_TIMEOUT"
        assert time.monotonic() - started < 0.35

    asyncio.run(exercise())


def test_activity_cleanup_cancels_waiter_that_outlives_cleanup_window(monkeypatch):
    class StoppedProcess:
        def terminate(self):
            pass

        def close(self):
            pass

    real_wait_for = asyncio.wait_for

    async def shortened_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(routes.asyncio, "wait_for", shortened_wait_for)

    async def exercise():
        errors = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))

        async def fail_late():
            await asyncio.sleep(0.05)
            raise RuntimeError("late activity pipe failure")

        waiter = asyncio.create_task(fail_late())
        await routes._stop_activity_process(StoppedProcess(), waiter)
        await asyncio.sleep(0.08)
        assert errors == []

    asyncio.run(exercise())


@pytest.mark.parametrize("value", ["inf", "nan", "-1", "0", "not-a-number"])
def test_invalid_timeout_cannot_disable_budget(monkeypatch, value):
    monkeypatch.setenv("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", value)
    assert routes._positive_float_env("MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", 60.0) == 60.0
