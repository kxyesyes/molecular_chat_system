"""Real Session/submission lifetime contracts; no provider or detached-thread claim."""
import asyncio
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult
from src.agent.harness.decision_execution import SingleAttemptTool, settle_action
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.tooling.adapters import ToolAdapter
from src.agent.tooling.spec import RetryPolicy, ToolSpec


def owner_if_available():
    # Before implementation, exercise the actual legacy behavior, not an import error.
    if importlib.util.find_spec('src.agent.runtime.worker_ownership') is None:
        return None
    from src.agent.runtime.worker_ownership import WorkerOwner
    return WorkerOwner()


async def action(session, owner):
    if owner is None:
        return await settle_action(session)
    return await settle_action(session, worker_owner=owner)


async def signalled(event):
    assert await asyncio.to_thread(event.wait, 5), 'barrier was not reached'


async def pending(task):
    done, _ = await asyncio.wait({task}, timeout=0.03)
    assert not done, 'settled before actual worker exit / executor join'


class Adapter(ToolAdapter):
    def __init__(self, invoke, timeout=1, concurrency=1, retry=None):
        super().__init__(ToolSpec('owned_test', '1', 'offline lifecycle fixture',
            None, None, set(), timeout, retry or RetryPolicy(), 'none', True, set(),
            max_concurrency=concurrency))
        self.call = invoke
        self.timeout_returned = threading.Event()

    def invoke(self, payload):
        return self.call(payload)

    def _timeout_result(self, message):
        result = super()._timeout_result(message)
        self.timeout_returned.set()
        return result


def success():
    return ToolResult.success_result('owned_test', {'value': 1})


def make_session(adapter, timeout, outer_ended=None, timeout_returned=None):
    class Workflow(WorkflowOrchestrator):
        def _execute_step(self, *args):
            result = super()._execute_step(*args)
            if timeout_returned and result.error and result.error.code == AgentErrorCode.TOOL_TIMEOUT:
                timeout_returned.set()
            return result

    class Session(WorkflowRunSession):
        def execute_step(self, index):
            try:
                return super().execute_step(index)
            finally:
                if outer_ended:
                    outer_ended.set()

    session = Session(Workflow(), AgentContext('lifetime fixture', 'owned-test'),
        [], {'owned_test': SingleAttemptTool(adapter)}, dynamic=True)
    session.start()
    session.append_step(WorkflowStep('one', 'owned_test', 'fixture',
                                    required=False, timeout_seconds=timeout))
    return session


def instrument_executors(monkeypatch, allow_join):
    records = []

    class Executor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.future_done = threading.Event()
            self.join_entered = threading.Event()
            self.joined = threading.Event()
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
                self.join_entered.set()
                assert allow_join.wait(5), 'test must release join barrier'
            super().shutdown(wait=wait, **kwargs)
            if wait:
                self.joined.set()

    monkeypatch.setattr('src.agent.orchestrators.workflow.ThreadPoolExecutor', Executor)
    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', Executor)
    return records


@pytest.mark.parametrize('adapter_first', [True, False])
def test_actual_nested_deadlines_drain_worker_and_join(monkeypatch, adapter_first):
    entered, exited, release = threading.Event(), threading.Event(), threading.Event()
    outer_ended, timeout_returned, allow_join = (threading.Event() for _ in range(3))
    records = instrument_executors(monkeypatch, allow_join)

    def invoke(_):
        entered.set()
        try:
            assert release.wait(5)
            return success()
        finally:
            exited.set()

    adapter = Adapter(invoke, timeout=0.03 if adapter_first else 0.3)
    session = make_session(adapter, 0.3 if adapter_first else 0.03,
                           outer_ended, timeout_returned)
    owner = owner_if_available()

    async def exercise():
        task = asyncio.create_task(action(session, owner))
        try:
            await signalled(entered)
            await signalled(timeout_returned)
            await signalled(outer_ended)
            assert not exited.is_set()
            await pending(task)
            release.set()
            await signalled(exited)
            for executor in records:
                await signalled(executor.future_done)
            await pending(task)  # future.done is not executor-joined.
            allow_join.set()
            await task
            assert len(records) == 2 and all(e.joined.is_set() for e in records)
            assert session.results[0].error.code == AgentErrorCode.TOOL_TIMEOUT
            assert owner.pending_roots == 0
        finally:
            release.set()
            allow_join.set()
            await asyncio.gather(task, return_exceptions=True)
            for executor in records:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


def test_late_descendant_registers_after_action_root_ends(monkeypatch):
    allow_child, child_entered, child_release, child_exited = (threading.Event() for _ in range(4))
    outer_ended, timeout_returned, allow_join = (threading.Event() for _ in range(3))
    allow_join.set()
    records = instrument_executors(monkeypatch, allow_join)

    def child(_):
        child_entered.set()
        try:
            assert child_release.wait(5)
            return success()
        finally:
            child_exited.set()

    descendant = Adapter(child, timeout=0.03)

    def parent(_):
        assert allow_child.wait(5)
        return descendant.execute('child')

    session = make_session(Adapter(parent, timeout=0.3), 0.03, outer_ended, timeout_returned)
    owner = owner_if_available()

    async def exercise():
        task = asyncio.create_task(action(session, owner))
        try:
            await signalled(outer_ended)
            turn = asyncio.create_task(owner.settle())
            assert owner.pending_roots == 1
            allow_child.set()  # New adapter submit from an already-owned worker.
            await signalled(child_entered)
            await signalled(descendant.timeout_returned)
            await pending(task)
            await pending(turn)
            assert not child_exited.is_set() and len(records) == 3
            child_release.set()
            await task
            await turn
            assert all(e.joined.is_set() for e in records)
            assert owner.status == 'settled'
            with pytest.raises(RuntimeError, match='sealed'):
                owner.start_action()
        finally:
            allow_child.set()
            child_release.set()
            await asyncio.gather(task, return_exceptions=True)
            if 'turn' in locals():
                await asyncio.gather(turn, return_exceptions=True)
            for executor in records:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


@pytest.mark.parametrize('site', ['workflow', 'adapter'])
def test_reservation_survives_fast_completion_before_submit_attachment(monkeypatch, site):
    from src.agent.runtime.worker_ownership import WorkerOwner, reserve_worker
    submitted, return_submit, invoked = (threading.Event() for _ in range(3))
    created = []

    class DelayedSubmit(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            # Reservation must already exist before executor construction.
            assert sum(r.reserved for r in root.records) == 1
            super().__init__(*args, **kwargs)
            created.append(self)

        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            assert invoked.wait(5)
            future.result(timeout=5)
            submitted.set()
            assert return_submit.wait(5)
            return future

    monkeypatch.setattr('src.agent.' + ('orchestrators.workflow' if site == 'workflow' else
                                      'tooling.adapters') + '.ThreadPoolExecutor', DelayedSubmit)
    owner = WorkerOwner()
    root = owner.start_action()

    def invoke(_):
        invoked.set()
        return success()

    adapter = Adapter(invoke)

    class Tool:
        execute = staticmethod(invoke)

    def call():
        if site == 'adapter':
            return adapter.execute('input')
        return WorkflowOrchestrator()._execute_step(Tool(), 'input',
                    WorkflowStep('test', 'owned_test', timeout_seconds=1))

    async def exercise():
        outer = asyncio.create_task(asyncio.to_thread(root.run, call))
        drain = asyncio.create_task(owner.settle())
        try:
            await signalled(submitted)
            assert len(root.records) == 1
            assert all(r.reserved and r.future is None for r in root.records)
            await pending(drain)
            return_submit.set()
            assert (await outer).success
            await drain
            assert owner.status == 'settled'
            assert reserve_worker() is None  # No binding leaks into the event loop.
            assert await asyncio.to_thread(reserve_worker) is None
        finally:
            return_submit.set()
            await asyncio.gather(outer, drain, return_exceptions=True)
            for executor in created:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


@pytest.mark.parametrize('site', ['workflow', 'adapter'])
@pytest.mark.parametrize('failure', ['creation', 'submit'])
@pytest.mark.parametrize('owned', [False, True])
def test_actual_submit_failure_rolls_back_and_preserves_slots(monkeypatch, site, failure, owned):
    from src.agent.runtime.worker_ownership import WorkerOwner
    owner = WorkerOwner() if owned else None
    root = owner.start_action() if owned else None
    invokes, created = [], []

    class Broken(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            if owned:
                assert len(root.records) == 1
            if failure == 'creation':
                raise RuntimeError('test creation failure')
            super().__init__(*args, **kwargs)
            self.joined = False
            created.append(self)

        def submit(self, *args, **kwargs):
            raise RuntimeError('test submit failure')

        def shutdown(self, wait=True, **kwargs):
            super().shutdown(wait=wait, **kwargs)
            if wait:
                self.joined = True

    monkeypatch.setattr('src.agent.' + ('orchestrators.workflow' if site == 'workflow' else
                                      'tooling.adapters') + '.ThreadPoolExecutor', Broken)
    adapter = Adapter(lambda _: invokes.append(1) or success())

    def call():
        if site == 'adapter':
            return adapter.execute('input')
        return WorkflowOrchestrator()._execute_step(SingleAttemptTool(adapter), 'input',
                    WorkflowStep('test', 'owned_test', timeout_seconds=1))

    async def exercise():
        try:
            if site == 'adapter' and failure == 'creation':
                result = await asyncio.to_thread(root.run, call) if owned else await asyncio.to_thread(call)
                assert result.error.code == AgentErrorCode.INTERNAL_ERROR
                assert result.quality['retryable'] is False
            else:
                with pytest.raises(RuntimeError, match='test .* failure'):
                    if owned:
                        await asyncio.to_thread(root.run, call)
                    else:
                        await asyncio.to_thread(call)
            if owned:
                await owner.settle()
                assert owner.pending_roots == 0
                assert all(e.joined for e in created)
            assert not invokes
            assert adapter._invocation_slots.acquire(blocking=False)
            adapter._invocation_slots.release()
        finally:
            for executor in created:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


def test_two_concurrent_turns_sharing_adapter_do_not_drain_each_other(monkeypatch):
    from src.agent.runtime.worker_ownership import WorkerOwner
    releases = [threading.Event(), threading.Event()]
    entered = [threading.Event(), threading.Event()]
    allow_join = threading.Event()
    allow_join.set()
    records = instrument_executors(monkeypatch, allow_join)

    def invoke(index):
        entered[index].set()
        assert releases[index].wait(5)
        return success()

    adapter = Adapter(invoke, timeout=0.03, concurrency=2)
    owners = [WorkerOwner(), WorkerOwner()]
    roots = [owner.start_action() for owner in owners]

    async def exercise():
        calls = [asyncio.create_task(asyncio.to_thread(root.run, adapter.execute, i))
                 for i, root in enumerate(roots)]
        drains = []
        try:
            for event in entered:
                await signalled(event)
            results = await asyncio.gather(*calls)
            assert all(r.error.code == AgentErrorCode.TOOL_TIMEOUT for r in results)
            drains = [asyncio.create_task(owner.settle()) for owner in owners]
            releases[0].set()
            await asyncio.wait_for(asyncio.shield(drains[0]), 3)
            assert owners[0].status == 'settled'
            assert owners[1].pending_roots == 1
            await pending(drains[1])
            assert not any('owner' in name for name in vars(adapter))
        finally:
            for release in releases:
                release.set()
            await asyncio.gather(*calls, *drains, return_exceptions=True)
            for executor in records:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


def test_repeated_cancellation_retains_one_shared_join_helper(monkeypatch):
    from src.agent.runtime.worker_ownership import WorkerOwner
    allow_join = threading.Event()
    records = instrument_executors(monkeypatch, allow_join)
    owner = WorkerOwner()
    root = owner.start_action()

    async def exercise():
        await asyncio.to_thread(root.run, Adapter(lambda _: success()).execute, 'input')
        first = asyncio.create_task(owner.settle())
        second = asyncio.create_task(owner.settle())
        try:
            await signalled(records[0].join_entered)
            helper, turn_helper = root._settler, owner._settler
            for _ in range(3):
                first.cancel()
                second.cancel()
                await pending(first)
                await pending(second)
                assert root._settler is helper and owner._settler is turn_helper
                assert not helper.done() and owner.pending_roots == 1
            allow_join.set()
            outcomes = await asyncio.gather(first, second, return_exceptions=True)
            assert all(isinstance(r, asyncio.CancelledError) for r in outcomes)
            assert helper.done() and records[0].joined.is_set()
            await owner.settle()
            assert owner.status == 'settled'
        finally:
            allow_join.set()
            await asyncio.gather(first, second, return_exceptions=True)
            for executor in records:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


def test_failed_join_retains_unresolved_owner_and_fixed_diagnostic(monkeypatch):
    from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError
    created = []

    class FailedJoin(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.join_calls = 0
            created.append(self)

        def shutdown(self, wait=True, **kwargs):
            if wait:
                self.join_calls += 1
                raise RuntimeError('private worker diagnostic must not escape')
            super().shutdown(wait=wait, **kwargs)

    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', FailedJoin)
    owner = WorkerOwner()
    root = owner.start_action()

    async def exercise():
        try:
            await asyncio.to_thread(root.run, Adapter(lambda _: success()).execute, 'input')
            for _ in range(2):
                with pytest.raises(WorkerCleanupError, match='^Owned worker cleanup remains unresolved$'):
                    await owner.settle()
                assert owner.status == 'unresolved' and owner.pending_roots == 1
                assert len(root.records) == 1
            assert created[0].join_calls == 1  # Do not restart failed join or tool work.
        finally:
            for executor in created:
                await asyncio.to_thread(ThreadPoolExecutor.shutdown, executor, wait=True)

    asyncio.run(exercise())


def test_turn_drains_other_roots_even_when_one_join_fails(monkeypatch):
    from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError
    owner = WorkerOwner()
    roots = [owner.start_action(), owner.start_action()]
    first = next(iter(owner._roots))
    created = []
    failing = False

    class Executor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fail = failing
            self.joined = False
            created.append(self)

        def shutdown(self, wait=True, **kwargs):
            if wait and self.fail:
                raise RuntimeError('private join failure')
            super().shutdown(wait=wait, **kwargs)
            if wait:
                self.joined = True

    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', Executor)

    async def exercise():
        nonlocal failing
        try:
            for root in roots:
                failing = root is first
                await asyncio.to_thread(root.run, Adapter(lambda _: success()).execute, 'input')
            with pytest.raises(WorkerCleanupError):
                await owner.settle()
            assert owner.pending_roots == 1
            assert owner.status == 'unresolved'
            assert all(e.joined for e in created if not e.fail)
        finally:
            for executor in created:
                await asyncio.to_thread(ThreadPoolExecutor.shutdown, executor, wait=True)

    asyncio.run(exercise())


@pytest.mark.parametrize('site', ['workflow', 'adapter'])
def test_child_registration_before_parent_submit_returns(monkeypatch, site):
    from src.agent.runtime.worker_ownership import WorkerOwner
    child_entered, release_child, parent_submit, attach_parent = (threading.Event() for _ in range(4))
    owner = WorkerOwner()
    root = owner.start_action()
    created = []

    class Executor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.parent = not created
            self.joined = False
            created.append(self)

        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            if self.parent:
                assert child_entered.wait(5)
                parent_submit.set()
                assert attach_parent.wait(5)
            return future

        def shutdown(self, wait=True, **kwargs):
            super().shutdown(wait=wait, **kwargs)
            if wait:
                self.joined = True

    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', Executor)
    monkeypatch.setattr('src.agent.orchestrators.workflow.ThreadPoolExecutor', Executor)

    def child(_):
        child_entered.set()
        assert release_child.wait(5)
        return success()

    inner = Adapter(child, timeout=0.03)
    parent = Adapter(lambda _: inner.execute('child'), timeout=0.03)

    def call():
        if site == 'adapter':
            return parent.execute('parent')
        return WorkflowOrchestrator()._execute_step(SingleAttemptTool(inner), 'parent',
                WorkflowStep('parent', 'owned_test', timeout_seconds=0.03))

    async def exercise():
        outer = asyncio.create_task(asyncio.to_thread(root.run, call))
        drain = asyncio.create_task(owner.settle())
        try:
            await signalled(parent_submit)
            with root.condition:
                assert len(root.records) == 2
                assert any(r.reserved for r in root.records)
            await pending(drain)
            attach_parent.set()
            await outer
            await pending(drain)
            release_child.set()
            await drain
            assert all(e.joined for e in created)
            assert owner.status == 'settled'
        finally:
            attach_parent.set()
            release_child.set()
            await asyncio.gather(outer, drain, return_exceptions=True)
            for executor in created:
                await asyncio.to_thread(executor.shutdown, wait=True)

    asyncio.run(exercise())


@pytest.mark.parametrize('already_started', [False, True])
def test_outer_abort_is_atomic_and_cannot_close_running_work(already_started):
    from src.agent.runtime.worker_ownership import WorkerOwner
    owner = WorkerOwner()
    root = owner.start_action()
    entered, release, exited = (threading.Event() for _ in range(3))
    calls = []

    def invoke():
        calls.append(1)
        entered.set()
        try:
            assert release.wait(5)
        finally:
            exited.set()

    async def exercise():
        worker = drain = None
        try:
            if already_started:
                worker = asyncio.create_task(asyncio.to_thread(root.run, invoke))
                await signalled(entered)
                assert root.abort_unstarted() is False
                assert not root.finished and not root.records
                drain = asyncio.create_task(owner.settle())
                await pending(drain)
                assert not exited.is_set() and owner.pending_roots == 1
                release.set()
                await worker
                await drain
                assert calls == [1]
            else:
                assert root.abort_unstarted() is True
                # Simulate a late queued callable after failed submission:
                # the closed entry gate must prevent any Session invocation.
                with pytest.raises(RuntimeError, match='scope is closed'):
                    await asyncio.to_thread(root.run, invoke)
                assert not calls and not entered.is_set()
                await owner.settle()
            assert owner.status == 'settled' and owner.pending_roots == 0
        finally:
            release.set()
            await asyncio.gather(*(t for t in (worker, drain) if t is not None),
                                 return_exceptions=True)

    asyncio.run(exercise())


def test_failed_parent_join_does_not_abandon_late_child_cleanup(monkeypatch):
    """A failed record stays retained; independent later children still drain."""
    from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError
    parent_entered, allow_child, child_entered, release_child = (
        threading.Event() for _ in range(4))
    failed_join, child_joined = threading.Event(), threading.Event()
    created = []

    class Executor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.parent = not created
            created.append(self)

        def shutdown(self, wait=True, **kwargs):
            if wait and self.parent:
                failed_join.set()
                raise RuntimeError('private synthetic parent join failure')
            if wait:
                assert threading.current_thread() not in self._threads
                with pytest.raises(RuntimeError):
                    asyncio.get_running_loop()
            result = super().shutdown(wait=wait, **kwargs)
            if wait:
                child_joined.set()
            return result

    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', Executor)
    owner = WorkerOwner()
    root = owner.start_action()

    def child(_):
        child_entered.set()
        assert release_child.wait(5)
        return success()

    descendant = Adapter(child, timeout=0.03)

    def parent(_):
        parent_entered.set()
        assert allow_child.wait(5)
        return descendant.execute('late child')

    producer = Adapter(parent, timeout=0.03)

    async def exercise():
        outer = asyncio.create_task(asyncio.to_thread(root.run, producer.execute, 'parent'))
        drain = None
        try:
            await signalled(parent_entered)
            result = await outer
            assert not result.success and root.finished
            drain = asyncio.create_task(owner.settle())
            await signalled(failed_join)
            # Give a prematurely terminating drain a chance to cache failure;
            # correct draining may remain pending until the parent is released.
            await asyncio.wait({drain}, timeout=0.05)
            allow_child.set()
            await signalled(child_entered)
            assert len(created) == 2
            release_child.set()
            with pytest.raises(WorkerCleanupError, match='^Owned worker cleanup remains unresolved$'):
                await drain
            # Querying the same retained owner must not strand a late child.
            with pytest.raises(WorkerCleanupError):
                await owner.settle()
            assert owner.status == 'unresolved' and owner.pending_roots == 1
            assert child_joined.is_set(), {
                'records': len(root.records),
                'failed_records': sum(r.failed for r in root.records),
                'settler_done': root._settler.done(),
                'child_wait_joined': child_joined.is_set(),
            }
        finally:
            allow_child.set()
            release_child.set()
            await asyncio.gather(outer, *([drain] if drain else []), return_exceptions=True)
            for executor in created:
                await asyncio.to_thread(ThreadPoolExecutor.shutdown, executor, wait=True)
            assert all(not thread.is_alive() for executor in created for thread in executor._threads)

    asyncio.run(exercise())


@pytest.mark.parametrize('queued', [False, True])
def test_failed_parent_pending_future_and_completion_notification(monkeypatch, queued):
    """Neither not-started nor post-run/pre-Future-completion is quiescence."""
    from src.agent.runtime.worker_ownership import WorkerOwner, WorkerCleanupError
    owner = WorkerOwner()
    root = owner.start_action()
    start_parent, allow_child, parent_entered, returned = (threading.Event() for _ in range(4))
    complete_future, failed_join, child_joined, waiting = (threading.Event() for _ in range(4))
    created = []
    original_wait = root.condition.wait

    def wait(*args, **kwargs):
        if returned.is_set():
            waiting.set()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(root.condition, 'wait', wait)

    class Executor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.parent = not created
            created.append(self)
            if self.parent and queued:
                # Occupy this real single-worker executor before the owned
                # callable is queued. This barrier is always released below.
                super().submit(lambda: start_parent.wait(5))

        def submit(self, call, *args, **kwargs):
            if not self.parent:
                return super().submit(call, *args, **kwargs)

            def invoke():
                result = call(*args, **kwargs)
                returned.set()  # reservation.run finally has already notified.
                assert complete_future.wait(5)
                return result

            future = super().submit(invoke)
            if queued:
                # Failure injection: a not-yet-started submission cannot be
                # cancelled. The real Future remains pending and later runs.
                future.cancel = lambda: False
            self.real_future = future
            return future

        def shutdown(self, wait=True, **kwargs):
            if wait and self.parent:
                failed_join.set()
                raise RuntimeError('private synthetic join failure')
            if self.parent and queued and not wait:
                # Preserve the injected uncancellable queue item as well as
                # its Future; do not fabricate a discarded, never-done Future.
                kwargs['cancel_futures'] = False
            result = super().shutdown(wait=wait, **kwargs)
            if wait:
                child_joined.set()
            return result

    monkeypatch.setattr('src.agent.tooling.adapters.ThreadPoolExecutor', Executor)
    descendant = Adapter(lambda _: success())

    def parent(_):
        parent_entered.set()
        assert allow_child.wait(5)
        return descendant.execute('child')

    async def exercise():
        outer = asyncio.create_task(asyncio.to_thread(root.run, Adapter(parent, timeout=0.03).execute, 'input'))
        drain = None
        try:
            result = await outer
            assert not result.success and root.finished
            if queued:
                assert not parent_entered.is_set()
                assert not created[0].real_future.running()
            drain = asyncio.create_task(owner.settle())
            await signalled(failed_join)
            await pending(drain)
            start_parent.set()
            allow_child.set()
            await signalled(returned)
            await signalled(child_joined)
            assert not created[0].real_future.done()
            # Put the drain deterministically into its wait after run.finally
            # but before Future.set_result: only the done callback can wake it.
            with root.condition:
                root.condition.notify_all()
            await signalled(waiting)
            await pending(drain)
            complete_future.set()
            done, _ = await asyncio.wait({drain}, timeout=2)
            assert done, 'Future completion did not notify the waiting ownership drain'
            with pytest.raises(WorkerCleanupError):
                await drain
            assert owner.status == 'unresolved' and owner.pending_roots == 1
            assert len(root.records) == 1  # Failed parent retained; child released.
        finally:
            start_parent.set()
            allow_child.set()
            complete_future.set()
            await asyncio.gather(outer, return_exceptions=True)
            for executor in created:
                await asyncio.to_thread(ThreadPoolExecutor.shutdown, executor, wait=True)
            with root.condition:
                root.condition.notify_all()  # RED teardown for a missed callback.
            if drain is not None:
                await asyncio.gather(drain, return_exceptions=True)

    asyncio.run(exercise())
