"""Reverse-target 3D queue, startup and computation share a finite deadline."""

import asyncio
import gc
import time
from pathlib import Path

import pytest

from src.web.routes import api_routes as support


def _echo(value):
    return value


def _slow_child(started_path, completed_path):
    Path(started_path).write_text("started", encoding="utf-8")
    time.sleep(30)
    Path(completed_path).write_text("completed", encoding="utf-8")


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "0", "-1", "invalid"])
def test_invalid_environment_timeout_cannot_disable_budget(monkeypatch, value):
    monkeypatch.setenv("REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS", value)
    assert support._get_pharm3d_timeout(25.0) == 25.0


def test_cleanup_consumes_already_finished_waiter_failure():
    class StoppedProcess:
        def terminate(self):
            pass

        def close(self):
            pass

    async def exercise():
        errors = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))

        async def fail():
            raise RuntimeError("expected pipe closure during termination")

        waiter = asyncio.create_task(fail())
        await asyncio.sleep(0)
        assert waiter.done()
        await support._stop_pharm3d_process(StoppedProcess(), waiter)
        del waiter
        gc.collect()
        await asyncio.sleep(0)
        assert errors == [], "cleanup left an unobserved waiter exception"

    asyncio.run(exercise())


def test_cleanup_cancels_waiter_that_outlives_cleanup_window(monkeypatch):
    class StoppedProcess:
        def terminate(self):
            pass

        def close(self):
            pass

    real_wait_for = asyncio.wait_for

    async def shortened_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(support.asyncio, "wait_for", shortened_wait_for)

    async def exercise():
        errors = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))

        async def fail_late():
            await asyncio.sleep(0.05)
            raise RuntimeError("late pipe failure")

        waiter = asyncio.create_task(fail_late())
        await support._stop_pharm3d_process(StoppedProcess(), waiter)
        await asyncio.sleep(0.08)
        assert errors == []

    asyncio.run(exercise())


def test_queue_timeout_never_constructs_worker(monkeypatch):
    async def exercise():
        semaphore = asyncio.Semaphore(1)
        await semaphore.acquire()
        monkeypatch.setattr(support, "_PHARM3D_SEMAPHORE", semaphore)
        created = []

        def forbidden_worker(*args, **kwargs):
            created.append(True)
            raise AssertionError("expired queue entry must not start a worker")

        monkeypatch.setattr(support, "IsolatedProcess", forbidden_worker)
        task = asyncio.create_task(support._run_pharm3d_job(_echo, "ok", timeout_seconds=0.03))
        try:
            # The outer guard only prevents a broken implementation hanging the test.
            done, _ = await asyncio.wait({task}, timeout=1.0)
            assert task in done, "queue wait escaped the request deadline"
            with pytest.raises(asyncio.TimeoutError):
                await task
            assert created == []
            assert semaphore.locked()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            semaphore.release()

    asyncio.run(exercise())


def test_startup_consumes_budget_and_reaps_worker(monkeypatch):
    class DelayedProcess:
        alive = False
        closed = False
        waited = False

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            time.sleep(0.15)
            self.alive = True

        def is_alive(self):
            return self.alive

        def wait(self):
            self.waited = True
            return "must not return late success"

        def terminate(self):
            self.alive = False

        def close(self):
            self.closed = True

    process = DelayedProcess()
    monkeypatch.setattr(support, "IsolatedProcess", lambda *a, **kw: process)

    async def exercise():
        semaphore = asyncio.Semaphore(1)
        monkeypatch.setattr(support, "_PHARM3D_SEMAPHORE", semaphore)
        with pytest.raises(asyncio.TimeoutError):
            await support._run_pharm3d_job(_echo, "ok", timeout_seconds=0.03)
        assert not process.alive
        assert process.closed
        assert not process.waited
        assert not semaphore.locked()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_running_child_is_reaped_before_capacity_returns(monkeypatch, tmp_path, cancel):
    started, completed = tmp_path / "started", tmp_path / "completed"
    real_process = support.IsolatedProcess
    processes = []

    def recording_process(*args, **kwargs):
        process = real_process(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(support, "IsolatedProcess", recording_process)

    async def exercise():
        semaphore = asyncio.Semaphore(1)
        monkeypatch.setattr(support, "_PHARM3D_SEMAPHORE", semaphore)
        task = asyncio.create_task(support._run_pharm3d_job(
            _slow_child, str(started), str(completed), timeout_seconds=5.0,
        ))
        try:
            deadline = asyncio.get_running_loop().time() + 4.0
            while not started.exists() and not task.done():
                assert asyncio.get_running_loop().time() < deadline, "child did not start"
                await asyncio.sleep(0.01)
            assert started.exists(), "must exercise a live calculation, not just startup"
            assert semaphore.locked()
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
                await task
            assert len(processes) == 1
            assert not processes[0].is_alive()
            assert not completed.exists()
            assert not semaphore.locked()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for process in processes:
                process.terminate()
                process.close()

    asyncio.run(exercise())
