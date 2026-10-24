"""Offline provider calls through real generation/adapter/owned-worker paths."""
import asyncio
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import httpx

import pytest

from src.agent.runtime.worker_ownership import WorkerOwner
from src.agent.scientific_session.decision_execution import SingleAttemptTool, settle_action, settle_owned_call
from src.agent.tooling.factory import build_tool_registry
from tests.agent.test_generation_transport_characterization import (
    isolated_production_context, mocked_client,
)


async def reached(event):
    assert await asyncio.to_thread(event.wait, 5), 'worker barrier was not reached'


@pytest.mark.parametrize('response', ['', 'CCO'])
@pytest.mark.parametrize('early_return', [False, True])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_cancel_stops_real_generation_retries_and_drains_worker(tmp_path, response, early_return, asynchronous):
    entered, release, exited = (threading.Event() for _ in range(3))
    owner, observed = WorkerOwner(), []
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, (('ok', response),) * 5) as (model, transport):
            binding = (SimpleNamespace(model_name=model.model_name, generate=model.generate_async)
                       if asynchronous else model)
            tool = production.generator(binding)
            adapter = build_tool_registry([tool]).resolve(tool.name)
            if early_return:
                adapter.spec = replace(adapter.spec, timeout_seconds=0.01)
            view = SingleAttemptTool(adapter)

            def provider_entry(event):
                if len(transport.calls) == 1:
                    entered.set()
                    assert release.wait(5)

            transport.on_request = provider_entry

            def invoke():
                try:
                    result = view.execute({'query': 'generate 2 molecules'})
                    observed.append(result)
                    return result
                finally:
                    exited.set()

            async def exercise():
                task = asyncio.create_task(settle_owned_call(invoke, worker_owner=owner))
                try:
                    await reached(entered)
                    if early_return:
                        await reached(exited)  # Cancellation now occurs inside root.settle().
                    for _ in range(2):
                        task.cancel()
                        await asyncio.sleep(0.02)
                        assert not task.done()
                    assert exited.is_set() is early_return
                finally:
                    release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert exited.is_set() and owner.pending_roots == 0
                await owner.settle()
                assert owner.status == 'settled'

            asyncio.run(exercise())
            assert len(transport.calls) == 1, 'cancelled root dispatched further provider requests'
            if response and not early_return:
                assert observed[0].data[0]['smiles'] == 'CCO'
                assert observed[0].quality['partial_generation'] is True
                assert observed[0].quality['generation_stop_reason'] == 'cancelled'
            elif not early_return:
                assert observed[0].status.value == 'cancelled'


def owned_with_deadline(call, owner, deadline):
    return settle_owned_call(call, worker_owner=owner, deadline=deadline)


@pytest.mark.parametrize('expired', [False, True])
def test_session_action_forwards_deadline_to_real_generation(tmp_path, expired):
    owner, attempts = WorkerOwner(), []
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, (('ok', 'CCO'),) * 5) as (model, transport):
            tool = production.generator(model)
            view = SingleAttemptTool(build_tool_registry([tool]).resolve(tool.name))

            def execute_step(index):
                attempts.append(index)
                return view.execute({'query': 'generate 1 molecule'})

            session = SimpleNamespace(next_index=0, execute_step=execute_step)

            async def exercise():
                result = await settle_action(session, worker_owner=owner,
                    deadline=time.monotonic() + (-1 if expired else 5))
                await owner.settle()
                assert owner.status == 'settled' and owner.pending_roots == 0
                return result

            result = asyncio.run(exercise())
            assert attempts == [0]
            assert len(transport.calls) == (0 if expired else 1)
            assert result.status.value == ('timeout' if expired else 'succeeded')


@pytest.mark.parametrize('expired', [False, True])
def test_provider_deadline_prevents_next_retry_and_preserves_partial(tmp_path, expired):
    owner = WorkerOwner()
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, (('ok', 'CCO'),) * 5) as (model, transport):
            tool = production.generator(model)
            view = SingleAttemptTool(build_tool_registry([tool]).resolve(tool.name))
            deadline = time.monotonic() + (-1 if expired else 0.1)

            def block_first(event):
                if len(transport.calls) == 1 and not expired:
                    time.sleep(max(0, deadline - time.monotonic()) + 0.02)

            transport.on_request = block_first

            async def exercise():
                result = await owned_with_deadline(lambda: view.execute({'query': 'generate 2 molecules'}), owner, deadline)
                await owner.settle()
                assert owner.status == 'settled'
                return result

            result = asyncio.run(exercise())
            assert len(transport.calls) == (0 if expired else 1)
            if not expired:
                assert result.data[0]['smiles'] == 'CCO'
                assert result.quality['partial_generation'] is True
                assert result.quality['generation_stop_reason'] == 'deadline_exceeded'
            else:
                assert result.status.value == 'timeout'


def test_async_provider_wait_is_capped_by_remaining_root_deadline(tmp_path, monkeypatch):
    from src.agent.runtime import worker_ownership

    # Keep cold thread/adapter startup out of the provider's 70ms budget.
    # Windows timers may wake before GetTickCount64 advances to the deadline;
    # make expiry explicit when the bounded provider coroutine is cancelled.
    now, deadline = [1000.0], 1000.07
    monkeypatch.setattr(worker_ownership, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    owner, entered, completed, cancelled = WorkerOwner(), [], [], []
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, (('ok', ''),) * 5) as (model, transport):
            asyncio.run(model.client.aclose())

            async def handle(request):
                entered.append(time.monotonic())
                try:
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    now[0] = deadline
                    cancelled.append(True)
                    raise
                completed.append(True)
                return transport.handle(request, 'async')

            model.client = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
            tool = production.generator(SimpleNamespace(model_name=model.model_name, generate=model.generate_async))
            view = SingleAttemptTool(build_tool_registry([tool]).resolve(tool.name))

            async def exercise():
                await owned_with_deadline(lambda: view.execute({'query': 'generate 2 molecules'}), owner,
                                          deadline)
                await owner.settle()

            asyncio.run(exercise())
            assert entered and len(entered) == 1
            assert cancelled == [True] and completed == []


def test_cancel_is_request_local_on_shared_tool_and_adapter(tmp_path):
    entered, release = threading.Event(), threading.Event()
    first_owner, second_owner = WorkerOwner(), WorkerOwner()
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, (('ok', 'CCO'),) * 3) as (model, transport):
            tool = production.generator(model)
            original_state = dict(vars(tool))
            adapter = build_tool_registry([tool]).resolve(tool.name)
            adapter = type(adapter)(replace(adapter.spec, max_concurrency=2), tool)
            view = SingleAttemptTool(adapter)

            def provider_entry(event):
                if len(transport.calls) == 1:
                    entered.set()
                    assert release.wait(5)

            transport.on_request = provider_entry

            async def exercise():
                first = asyncio.create_task(settle_owned_call(
                    lambda: view.execute({'query': 'generate 2 molecules'}), worker_owner=first_owner))
                try:
                    await reached(entered)
                    first.cancel()
                    await asyncio.sleep(0.02)
                    second = await settle_owned_call(
                        lambda: view.execute({'query': 'generate 1 molecule'}), worker_owner=second_owner)
                    assert second.success and len(second.data) == 1
                finally:
                    release.set()
                with pytest.raises(asyncio.CancelledError):
                    await first
                # Same worker ownership machinery is reusable without the old signal.
                third = await settle_owned_call(
                    lambda: view.execute({'query': 'generate 1 molecule'}), worker_owner=first_owner)
                assert third.success
                await first_owner.settle()
                await second_owner.settle()

            asyncio.run(exercise())
            assert len(transport.calls) == 3
            assert vars(tool) == original_state


def test_owned_running_loop_bridge_inherits_signal_and_joins(tmp_path):
    owner, entered, release = WorkerOwner(), threading.Event(), threading.Event()
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, (('ok', 'CCO'),) * 5) as (model, transport):
            tool = production.generator(SimpleNamespace(model_name=model.model_name, generate=model.generate_async))

            def provider_entry(event):
                entered.set()
                assert release.wait(5)

            transport.on_request = provider_entry

            async def inner():
                return tool.execute('generate 2 molecules')

            async def exercise():
                task = asyncio.create_task(settle_owned_call(lambda: asyncio.run(inner()), worker_owner=owner))
                try:
                    await reached(entered)
                    task.cancel()
                    await asyncio.sleep(0.02)
                    assert not task.done()
                finally:
                    release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert owner.pending_roots == 0
                await owner.settle()

            asyncio.run(exercise())
            assert len(transport.calls) == 1


@pytest.mark.parametrize('deadline', [True, float('nan'), float('inf'), '1'])
def test_invalid_deadline_rejects_before_worker_submission(deadline):
    owner, calls = WorkerOwner(), []

    async def exercise():
        with pytest.raises(ValueError, match='Invalid worker deadline'):
            await settle_owned_call(lambda: calls.append(True), worker_owner=owner, deadline=deadline)
        assert calls == [] and owner.pending_roots == 0
        await owner.settle()

    asyncio.run(exercise())
