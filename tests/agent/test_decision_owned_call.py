"""Generic owned-call lifecycle tests extracted from the reviewed Session suite."""
import asyncio
import inspect
import threading

import pytest

from src.agent.harness import decision_execution
from src.agent.runtime.worker_ownership import WorkerOwner
from test_worker_ownership import signalled, pending


def owned_helper():
    helper = getattr(decision_execution, 'settle_owned_call', None)
    assert callable(helper), 'generic owned settlement helper is missing'
    return helper


@pytest.mark.parametrize('cancelled', [False, True])
def test_generic_owned_call_drains_nested_reservation_after_callback_return(cancelled):
    from concurrent.futures import ThreadPoolExecutor
    from src.agent.runtime.worker_ownership import reserve_worker
    helper, owner = owned_helper(), WorkerOwner()
    entered, release, exited = (threading.Event() for _ in range(3))
    def work():
        entered.set()
        try:
            assert release.wait(5)
        finally:
            exited.set()
    def dispatch():
        reservation = reserve_worker()
        executor = ThreadPoolExecutor(max_workers=1)
        reservation.attach(executor, executor.submit(reservation.run, work))
        return 'callback returned'
    async def exercise():
        task = asyncio.create_task(helper(dispatch, worker_owner=owner))
        try:
            await signalled(entered)
            await pending(task)
            if cancelled:
                for _ in range(3):
                    task.cancel()
                    await pending(task)
            assert not exited.is_set()
        finally:
            release.set()
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == 'callback returned'
        assert exited.is_set() and owner.pending_roots == 0
        await owner.settle()
    asyncio.run(exercise())


@pytest.mark.parametrize('owned', [False, True])
def test_generic_owned_call_returns_result_never_retries_callback(owned):
    helper, calls = owned_helper(), []
    owner = WorkerOwner() if owned else None
    def fail():
        calls.append(1)
        raise RuntimeError('callback failed')
    async def exercise():
        marker = object()
        assert await helper(lambda: marker, worker_owner=owner) is marker
        with pytest.raises(RuntimeError, match='callback failed'):
            await helper(fail, worker_owner=owner)
        if owner:
            assert owner.pending_roots == 0
            await owner.settle()
    asyncio.run(exercise())
    assert calls == [1]


@pytest.mark.parametrize('owned', [False, True])
def test_generic_owned_call_repeated_cancel_drains_before_return(owned):
    helper = owned_helper()
    owner = WorkerOwner() if owned else None
    entered, release, exited = (threading.Event() for _ in range(3))
    def work():
        entered.set()
        try:
            assert release.wait(5)
        finally:
            exited.set()
    async def exercise():
        task = asyncio.create_task(helper(work, worker_owner=owner))
        try:
            await signalled(entered)
            for _ in range(3):
                task.cancel()
                await pending(task)
            assert not exited.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
        if owner:
            assert owner.pending_roots == 0
            await owner.settle()
    asyncio.run(exercise())


@pytest.mark.parametrize('owned', [False, True])
def test_generic_owned_call_task_creation_failure_closes_coroutine(monkeypatch, owned):
    helper = owned_helper()
    owner = WorkerOwner() if owned else None
    attempted, coroutines = [], []
    original = asyncio.create_task
    def fail_first(coro, **kwargs):
        if not coroutines:
            coroutines.append(coro)
            raise RuntimeError('create failed')
        return original(coro, **kwargs)
    async def exercise():
        monkeypatch.setattr(asyncio, 'create_task', fail_first)
        with pytest.raises(RuntimeError, match='create failed'):
            await helper(lambda: attempted.append(1), worker_owner=owner)
        assert inspect.getcoroutinestate(coroutines[0]) == inspect.CORO_CLOSED
        assert not attempted
        if owner:
            assert owner.pending_roots == 0
            await owner.settle()
    asyncio.run(exercise())
