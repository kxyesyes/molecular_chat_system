"""Contract tests for the test-only, manually advanced asyncio clock."""

import asyncio
import math
import time

import pytest

from tests.sandbox_broker.controlled_clock import ControlledClock


def test_call_later_runs_at_deadline_not_before():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            start = loop.time()
            calls = []
            timer = loop.call_later(0.02, lambda: calls.append(loop.time()))
            await clock.advance(0.019)
            assert calls == []
            await clock.advance(timer.when() - loop.time())
            assert calls == [timer.when()]
            assert loop.time() == start + 0.02

    asyncio.run(scenario())


def test_wait_for_uses_real_timeout_and_cancels_blocked_coroutine():
    async def scenario():
        loop = asyncio.get_running_loop()
        cancelled = []

        async def blocked():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(loop.time())

        with ControlledClock(loop) as clock:
            start = loop.time()
            task = asyncio.create_task(asyncio.wait_for(blocked(), timeout=0.5))
            try:
                await clock.drain()
                await clock.advance(0.499)
                assert not task.done()
                assert cancelled == []
                await clock.advance(start + 0.5 - loop.time())
                assert task.done()
                with pytest.raises(asyncio.TimeoutError):
                    await task
                assert cancelled == [start + 0.5]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_advance_visits_intermediate_timers_and_newly_scheduled_timers():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            start = loop.time()
            calls = []

            def first():
                calls.append(("first", loop.time() - start))
                loop.call_later(0.125, lambda: calls.append(("new", loop.time() - start)))

            async def sleeper():
                await asyncio.sleep(0.375)
                calls.append(("sleep", loop.time() - start))

            loop.call_later(0.5, lambda: calls.append(("last", loop.time() - start)))
            loop.call_later(0.125, first)
            task = asyncio.create_task(sleeper())
            try:
                await clock.advance(1)
                assert calls == [("first", 0.125), ("new", 0.25), ("sleep", 0.375), ("last", 0.5)]
                assert task.done()
                assert loop.time() == start + 1
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_drain_exhausts_ready_chains_and_due_timers_without_advancing():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            start = loop.time()
            calls = []

            def ready():
                calls.append(("ready", loop.time()))
                loop.call_soon(lambda: calls.append(("child", loop.time())))

            loop.call_soon(ready)
            loop.call_later(0, lambda: calls.append(("due", loop.time())))
            future = loop.call_later(0.25, lambda: calls.append(("future", loop.time())))
            try:
                await clock.drain()
                assert {label for label, _ in calls} == {"ready", "child", "due"}
                assert all(when == start for _, when in calls)
                assert loop.time() == start
            finally:
                future.cancel()

    asyncio.run(scenario())


def test_advance_drains_ready_work_before_moving_time():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            start = loop.time()
            calls = []

            async def ready_work():
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                calls.append(("ready", loop.time() - start))
                loop.call_later(0.25, lambda: calls.append(("timer", loop.time() - start)))

            task = asyncio.create_task(ready_work())
            try:
                await clock.advance(1)
                assert task.done()
                assert calls == [("ready", 0), ("timer", 0.25)]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_timers_are_ignored_including_cancellation_by_callback():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            calls = []
            cancelled = loop.call_later(0.125, calls.append, "already cancelled")
            cancelled.cancel()
            later = loop.call_later(0.5, calls.append, "cancelled later")
            loop.call_later(0.25, later.cancel)
            loop.call_later(0.75, calls.append, "live")
            await clock.advance(1)
            assert calls == ["live"]

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_inside", [False, True])
def test_context_restores_clock_and_resolution_even_on_exception(fail_inside):
    async def scenario():
        loop = asyncio.get_running_loop()
        original_time = loop.time
        original_resolution = loop._clock_resolution
        original_attributes = {key: loop.__dict__.get(key) for key in ("time", "_clock_resolution")}
        original_keys = {key for key in original_attributes if key in loop.__dict__}
        try:
            with ControlledClock(loop) as clock:
                assert loop.time != original_time
                await clock.advance(0.5)
                if fail_inside:
                    raise LookupError("body failure")
        except LookupError as error:
            assert str(error) == "body failure"
        assert loop.time == original_time
        assert loop._clock_resolution == original_resolution
        assert {key for key in original_attributes if key in loop.__dict__} == original_keys
        assert {key: loop.__dict__.get(key) for key in original_attributes} == original_attributes

    asyncio.run(scenario())


def test_only_current_loop_is_patched_and_initial_timestamp_is_preserved():
    async def scenario():
        loop = asyncio.get_running_loop()
        other = asyncio.new_event_loop()
        monotonic = time.monotonic
        policy = asyncio.get_event_loop_policy()
        wait_for, wait = asyncio.wait_for, asyncio.wait
        other_time, other_resolution = other.time, other._clock_resolution
        before = loop.time()
        calls = []
        timer = loop.call_later(0.25, lambda: calls.append(loop.time()))
        try:
            with ControlledClock(loop) as clock:
                assert before <= loop.time() <= monotonic()
                await clock.advance(timer.when() - loop.time())
                assert calls == [timer.when()]
                assert time.monotonic is monotonic
                assert asyncio.get_event_loop_policy() is policy
                assert (asyncio.wait_for, asyncio.wait) == (wait_for, wait)
                assert (other.time, other._clock_resolution) == (other_time, other_resolution)
        finally:
            timer.cancel()
            other.close()

    asyncio.run(scenario())


def test_coarse_resolution_cannot_fire_a_future_timer_early():
    async def scenario():
        loop = asyncio.get_running_loop()
        original_resolution = loop._clock_resolution
        loop._clock_resolution = 0.015625
        try:
            with ControlledClock(loop) as clock:
                calls = []
                now = loop.time()
                next_tick = math.nextafter(now, math.inf)
                timer = loop.call_at(next_tick, calls.append, "next tick")
                try:
                    await clock.drain()
                    assert calls == []
                    await clock.advance(next_tick - now)
                    assert calls == ["next tick"]
                finally:
                    timer.cancel()
            assert loop._clock_resolution == 0.015625
        finally:
            loop._clock_resolution = original_resolution

    asyncio.run(scenario())


@pytest.mark.parametrize("delta", [-0.001, -math.inf, math.inf, math.nan, "1", None, True])
def test_invalid_delta_is_rejected_without_running_work_or_changing_time(delta):
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            start = loop.time()
            calls = []
            handle = loop.call_soon(calls.append, "ready")
            try:
                with pytest.raises((TypeError, ValueError)):
                    await clock.advance(delta)
                assert loop.time() == start
                assert calls == []
            finally:
                handle.cancel()

    asyncio.run(scenario())


def test_zero_advance_drains_ready_work():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            start = loop.time()
            calls = []
            loop.call_soon(calls.append, "ready")
            await clock.advance(0)
            assert calls == ["ready"]
            assert loop.time() == start

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["drain", "advance"])
@pytest.mark.parametrize("reschedule", ["call_soon", "call_later"])
def test_runaway_ready_or_due_timer_chains_are_bounded(method, reschedule):
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop, max_turns=20) as clock:
            handle = None
            calls = []

            def repeat():
                nonlocal handle
                calls.append(loop.time())
                if reschedule == "call_soon":
                    handle = loop.call_soon(repeat)
                else:
                    handle = loop.call_later(0, repeat)

            handle = loop.call_soon(repeat)
            try:
                with pytest.raises(RuntimeError, match="quiesce|limit"):
                    if method == "drain":
                        await clock.drain()
                    else:
                        await clock.advance(1)
                assert 0 < len(calls) <= 20
            finally:
                handle.cancel()

    asyncio.run(scenario())


def test_enter_rejects_a_loop_other_than_the_running_loop():
    async def scenario():
        other = asyncio.new_event_loop()
        original_time = other.time
        original_resolution = other._clock_resolution
        try:
            with pytest.raises(RuntimeError, match="running loop"):
                with ControlledClock(other):
                    pass
            assert other.time == original_time
            assert other._clock_resolution == original_resolution
        finally:
            other.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["drain", "advance"])
def test_driving_outside_context_is_rejected(method):
    async def scenario():
        clock = ControlledClock(asyncio.get_running_loop())

        async def drive():
            if method == "drain":
                await clock.drain()
            else:
                await clock.advance(0)

        with pytest.raises(RuntimeError, match="active"):
            await drive()
        with clock:
            await drive()
        with pytest.raises(RuntimeError, match="active"):
            await drive()

    asyncio.run(scenario())


@pytest.mark.parametrize("same_clock", [False, True])
def test_nested_clock_is_rejected_without_corrupting_outer_clock(same_clock):
    async def scenario():
        loop = asyncio.get_running_loop()
        original_time = loop.time
        with ControlledClock(loop) as clock:
            nested = clock if same_clock else ControlledClock(loop)
            patched_time = loop.time
            with pytest.raises(RuntimeError, match="active|nested"):
                with nested:
                    pass
            assert loop.time == patched_time
            await clock.advance(0.25)
        assert loop.time == original_time

    asyncio.run(scenario())


@pytest.mark.parametrize("max_turns", [0, -1, True, 1.5])
def test_invalid_turn_limit_is_rejected(max_turns):
    async def scenario():
        with pytest.raises(ValueError, match="positive integer"):
            ControlledClock(asyncio.get_running_loop(), max_turns=max_turns)

    asyncio.run(scenario())


def test_advance_has_one_turn_budget_across_intermediate_timers():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop, max_turns=20) as clock:
            handle = None

            def repeat():
                nonlocal handle
                handle = loop.call_later(0.125, repeat)

            handle = loop.call_later(0.125, repeat)
            try:
                with pytest.raises(RuntimeError, match="limit"):
                    await clock.advance(100)
            finally:
                handle.cancel()

    asyncio.run(scenario())


def test_nonfinite_target_is_rejected_without_changing_time():
    async def scenario():
        loop = asyncio.get_running_loop()
        with ControlledClock(loop) as clock:
            await clock.advance(1e308)
            start = loop.time()
            with pytest.raises(ValueError, match="finite"):
                await clock.advance(1e308)
            assert loop.time() == start

    asyncio.run(scenario())
