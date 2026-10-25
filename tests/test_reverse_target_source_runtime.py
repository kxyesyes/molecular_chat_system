"""Application-owned prewarm lifecycle over real compact scientific fixtures."""
import asyncio
import importlib.util
import threading
import time
from types import SimpleNamespace

import pytest
from rdkit import rdBase

from src.reverse_target.owned_source import ReverseSourceUnavailable
from tests.test_reverse_target_compact_source import write_source


def runtime_type():
    assert importlib.util.find_spec("src.reverse_target.source_runtime") is not None, (
        "owned reverse source runtime is not implemented")
    from src.reverse_target.source_runtime import SourceRuntime
    return SourceRuntime


def blocked_loader(monkeypatch):
    from src.reverse_target import compact_source
    original = compact_source.load_compact_source
    entered, release = threading.Event(), threading.Event()
    calls = []
    def held(*args, **kwargs):
        calls.append(threading.current_thread())
        entered.set()
        assert release.wait(10)
        with rdBase.BlockLogs():
            return original(*args, **kwargs)
    monkeypatch.setattr(compact_source, "load_compact_source", held)
    return entered, release, calls


def test_prewarm_waits_only_for_active_foreground_io_and_counts_overlaps(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    waits, observations = [], []
    monkeypatch.setattr(runtime._stop, "wait", waits.append)

    def initialize(*, policy, timeout_seconds, cancelled, progress):
        assert policy is runtime._policy and timeout_seconds == 1800
        observations.append(cancelled())
        with runtime.foreground_io():
            observations.append(cancelled())
            try:
                with runtime.foreground_io():
                    observations.append(cancelled())
                    raise OSError("synthetic I/O failure")
            except OSError:
                pass
            observations.append(cancelled())
        observations.append(cancelled())
        runtime._stop.set()
        with runtime.foreground_io():
            observations.append(cancelled())

    monkeypatch.setattr(runtime._producer, "initialize_compact_strict", initialize)
    try:
        runtime.start()
        runtime._thread.join(10)
        assert not runtime._thread.is_alive()
        assert observations == [False, False, False, False, False, True]
        assert waits == [.001, .001, .001]
        assert runtime._foreground_io_count == 0
    finally:
        runtime.close()


def test_tool_foreground_io_is_optional_and_never_calls_runtime_under_tool_lock():
    from contextlib import nullcontext
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    tool = ReverseTargetTool()
    calls = []

    def foreground():
        assert tool._state_lock.acquire(blocking=False)
        tool._state_lock.release()
        calls.append(True)
        return nullcontext()

    with tool.foreground_io():
        assert tool._source_runtime is None and not tool._prewarm_requested
    tool._source_runtime = SimpleNamespace(foreground_io=foreground)
    with tool.foreground_io():
        pass
    tool._closed = True
    with tool.foreground_io():
        pass
    assert calls == [True]


def test_foreground_wait_still_expires_original_acquisition_deadline(tmp_path, monkeypatch):
    from src.reverse_target import compact_source
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    clock, waits = [1.0], []

    def wait(delay):
        waits.append(delay)
        clock[0] += 1800  # Simulate suspension beyond the unchanged acquisition budget.

    monkeypatch.setattr(compact_source, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(runtime._stop, "wait", wait)
    try:
        with runtime.foreground_io():
            runtime.start()
            runtime._thread.join(10)
        assert not runtime._thread.is_alive()
        assert waits == [.001]
        assert runtime.snapshot()["state"] == "failed"
        assert runtime.snapshot()["reason"] == "acquisition_timeout"
        assert runtime.snapshot()["verified_rows"] == 0
        with pytest.raises(ReverseSourceUnavailable):
            runtime.ready_predictor()
    finally:
        runtime.close()


def test_close_during_prewarm_yield_cancels_before_source_acquisition(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    entered, release = threading.Event(), threading.Event()
    acquisitions = []

    def yield_thread(delay):
        assert delay == .001
        entered.set()
        assert release.wait(10)

    original = runtime._producer._resolved_source_paths

    def paths():
        acquisitions.append(True)
        return original()

    monkeypatch.setattr(runtime._stop, "wait", yield_thread)
    monkeypatch.setattr(runtime._producer, "_resolved_source_paths", paths)
    try:
        with runtime.foreground_io():
            runtime.start()
            assert entered.wait(2), 'prewarm never offered a scheduling yield'
        runtime.request_close()
        assert runtime.snapshot()["state"] == "closing"
        assert runtime.snapshot()["cleanup_pending"]
        with pytest.raises(ReverseSourceUnavailable):
            runtime.ready_predictor()
        release.set()
        runtime._thread.join(10)
        assert not runtime._thread.is_alive()
        assert acquisitions == [], 'stop during yield was not rechecked before acquisition'
        assert runtime.snapshot()["state"] == "closing"
    finally:
        release.set()
        runtime.close()
    assert runtime.snapshot()["state"] == "closed"
    assert not runtime.snapshot()["cleanup_pending"]


@pytest.mark.parametrize("corrupt_last_row", [False, True])
def test_cooperative_prewarm_still_validates_every_row_and_array_hash(tmp_path, monkeypatch, corrupt_last_row):
    import numpy as np
    from src.reverse_target.owned_source import _array_hash, operation_check
    producer, arrays = write_source(tmp_path / "source")
    if corrupt_last_row:
        damaged = arrays["maccs"].copy()
        damaged[-1, 7] ^= 1
        np.save(producer.maccs_fp_path, damaged)
    runtime = runtime_type()(producer.data_dir)
    yields, rows = [], []

    original = runtime._producer.compute_query_fingerprints

    def fingerprint(smiles):
        rows.append(smiles)
        return original(smiles)

    monkeypatch.setattr(runtime._stop, "wait", yields.append)
    monkeypatch.setattr(runtime._producer, "compute_query_fingerprints", fingerprint)
    try:
        with runtime.foreground_io():
            runtime.start()
            runtime._thread.join(10)
        assert not runtime._thread.is_alive()
        assert yields and set(yields) == {.001}, 'real acquisition did not cooperate with other threads'
        assert rows == ["CCO", "CCC", "CCO"]
        if corrupt_last_row:
            assert runtime.snapshot()["state"] == "failed"
            with pytest.raises(ReverseSourceUnavailable):
                runtime.ready_predictor()
        else:
            result = runtime.ready_predictor().predict_with_receipt("CCO")
            descriptor = result["receipt"]["source"]
            assert descriptor["row_count"] == 3 and descriptor["row_alignment_verified"]
            for name, array in arrays.items():
                assert descriptor[name + "_sha256"] == _array_hash(array, operation_check(30, None))
            assert runtime.snapshot()["verified_rows"] == runtime.snapshot()["total_rows"] == 3
    finally:
        runtime.close()


def test_runtime_loads_once_and_only_publishes_after_full_loader_return(tmp_path, monkeypatch, caplog):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    entered, release, calls = blocked_loader(monkeypatch)
    caplog.set_level("INFO", logger="src.reverse_target.source_runtime")
    assert runtime.snapshot()["state"] == "idle"
    try:
        runtime.start()
        assert entered.wait(10)
        runtime.start()
        state = runtime.snapshot()
        assert state["state"] == "loading" and state["cleanup_pending"]
        assert set(state) == {"state", "phase", "verified_rows", "total_rows",
                              "elapsed_seconds", "reason", "cleanup_pending"}
        with pytest.raises(ReverseSourceUnavailable):
            runtime.ready_predictor()
        assert len(calls) == 1 and not calls[0].daemon
        release.set()
        calls[0].join(10)
        assert not calls[0].is_alive()
        ready = runtime.ready_predictor()
        with rdBase.BlockLogs():
            result = ready.predict_with_receipt("CCO")
        assert result["receipt"]["source"]["row_count"] == 3
        state = runtime.snapshot()
        assert state["state"] == "ready" and not state["cleanup_pending"]
        assert state["verified_rows"] == state["total_rows"] == 3
        assert "Reverse-target source prewarm ready (3/3 rows)" in caplog.text
    finally:
        release.set()
        runtime.close()
    assert runtime.snapshot()["state"] == "closed"
    with pytest.raises(ReverseSourceUnavailable):
        runtime.ready_predictor()


def test_close_retains_physical_prewarm_worker_and_revokes_publication(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    entered, release, calls = blocked_loader(monkeypatch)
    try:
        runtime.start()
        assert entered.wait(10)
        runtime.request_close()
        state = runtime.snapshot()
        assert state["state"] == "closing" and state["cleanup_pending"]
        runtime.start()
        assert len(calls) == 1
        with pytest.raises(ReverseSourceUnavailable):
            runtime.ready_predictor()
    finally:
        release.set()
        runtime.close()
    assert runtime.snapshot()["state"] == "closed"
    assert not runtime.snapshot()["cleanup_pending"]
    assert not calls[0].is_alive()


def test_source_failure_is_sanitized_and_never_retried(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    from src.reverse_target import compact_source
    calls = []
    def broken(*args, **kwargs):
        calls.append(threading.current_thread())
        raise ValueError("private/path/secret-data-must-not-appear")
    monkeypatch.setattr(compact_source, "load_compact_source", broken)
    try:
        runtime.start()
        runtime._thread.join(10)
        state = runtime.snapshot()
        assert state["state"] == "failed" and state["reason"] == "source_validation_failed"
        assert "secret" not in str(state) and "private" not in str(state)
        runtime.start()
        assert len(calls) == 1
        with pytest.raises(ReverseSourceUnavailable):
            runtime.ready_predictor()
    finally:
        runtime.close()


def test_tool_uses_application_prewarm_without_lazy_reload_or_waiting(tmp_path, monkeypatch):
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    producer, _ = write_source(tmp_path / "source")
    monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(producer.data_dir))
    tool = ReverseTargetTool()
    assert callable(getattr(tool, "start_preload", None)), "tool prewarm seam is missing"
    entered, release, calls = blocked_loader(monkeypatch)
    try:
        assert tool.start_preload() is True
        assert entered.wait(10)
        begin = time.monotonic()
        result = tool.execute("CCO")
        assert time.monotonic() - begin < 2
        assert result["success"] is False and result["error"]["code"] == "tool_unavailable"
        assert "校验" in result["message"]
        assert tool.start_preload() is False and len(calls) == 1
        release.set()
        calls[0].join(10)
        with rdBase.BlockLogs():
            result = tool.execute("CCO")
        assert result["success"] is True
        projection = tool.validate_current_observation(result["data"], result["evidence"], input_data="CCO")
        assert projection["generation_id"] == result["evidence"][0]["prediction_receipt"]["source"]["generation_id"]
    finally:
        release.set()
        tool.close()
    assert tool._source_runtime.snapshot()["state"] == "closed"
    assert tool._predictor is None


def test_injected_predictor_remains_borrowed_and_cannot_start_prewarm(tmp_path):
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    producer, _ = write_source(tmp_path / "source")
    with rdBase.BlockLogs():
        producer.initialize_strict()
    snapshot = producer.capture_prediction_source()
    tool = ReverseTargetTool(producer)
    assert callable(getattr(tool, "start_preload", None)), "tool prewarm seam is missing"
    assert tool.start_preload() is False
    tool.close()
    assert producer.capture_prediction_source() == snapshot
    producer.close_strict()


@pytest.mark.parametrize("value", ["", "abc", "119", "3601", "true", "nan", "1800.5", " 1800 "])
def test_server_prewarm_timeout_rejects_unsafe_configuration(monkeypatch, value):
    from src.reverse_target import config
    resolve = getattr(config, "get_reverse_target_prewarm_timeout", None)
    assert callable(resolve), "server prewarm timeout configuration is missing"
    monkeypatch.setenv("REVERSE_TARGET_PREWARM_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        resolve()


def test_server_prewarm_timeout_defaults_and_valid_boundaries(monkeypatch):
    from src.reverse_target import config
    resolve = getattr(config, "get_reverse_target_prewarm_timeout", None)
    assert callable(resolve), "server prewarm timeout configuration is missing"
    monkeypatch.delenv("REVERSE_TARGET_PREWARM_TIMEOUT_SECONDS", raising=False)
    assert resolve() == 1800
    for value in ("120", "1800", "3600"):
        monkeypatch.setenv("REVERSE_TARGET_PREWARM_TIMEOUT_SECONDS", value)
        assert resolve() == int(value)


def test_application_starts_registered_owner_and_drains_shutdown_off_loop(tmp_path, monkeypatch):
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.agent.tooling import build_tool_registry
    from src.reverse_target import predictor as predictor_module
    from src.web.app import MolecularChatApp
    producer, _ = write_source(tmp_path / "source")
    monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(producer.data_dir))
    tool = ReverseTargetTool()
    application = MolecularChatApp.__new__(MolecularChatApp)
    application.config = {"rag": {"enabled": False}}
    application._llm_watch_task = None
    application.agent_system = SimpleNamespace(tools={tool.name: tool})
    application.agent_tool_registry = build_tool_registry([tool])
    async def watch():
        await asyncio.Event().wait()
    application._watch_llm_env_config = watch
    old_singleton_calls = []
    monkeypatch.setattr(predictor_module, "get_predictor", lambda: old_singleton_calls.append(True))
    entered, release, calls = blocked_loader(monkeypatch)
    original_close = tool.close
    close_entered = threading.Event()
    closes = []
    def close():
        closes.append(threading.current_thread())
        close_entered.set()
        original_close()
    monkeypatch.setattr(tool, "close", close)
    async def exercise():
        shutdown = None
        emergency_release = None
        try:
            await application.initialize()
            assert tool._source_runtime is not None, "startup did not preload the registered tool"
            assert await asyncio.to_thread(entered.wait, 5)
            await application.initialize()
            assert not old_singleton_calls and len(calls) == 1
            emergency_release = threading.Timer(3, release.set)
            emergency_release.start()
            shutdown = asyncio.create_task(application.shutdown())
            assert await asyncio.to_thread(close_entered.wait, 5)
            shutdown.cancel()
            shutdown.cancel()
            await asyncio.sleep(.02)
            assert not shutdown.done(), "shutdown abandoned the actual prewarm worker"
            assert application.agent_tool_registry is not None, "owner registry cleared before actual stop"
            assert tool._source_runtime.snapshot()["cleanup_pending"]
            assert closes[0] is not threading.current_thread(), "thread join blocked the event loop"
        finally:
            release.set()
            if emergency_release is not None:
                emergency_release.cancel()
                emergency_release.join()
            if shutdown is not None:
                await asyncio.gather(shutdown, return_exceptions=True)
            else:
                await application.shutdown()
        await application.shutdown()
        assert len(closes) == 1
        assert not calls[0].is_alive()
        assert application.agent_tool_registry is None
        assert application.agent_system.tools == {}
    asyncio.run(exercise())


def test_thread_construction_failure_does_not_leave_an_unjoinable_owner(tmp_path, monkeypatch):
    from src.reverse_target import source_runtime
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    def cannot_allocate(*args, **kwargs):
        raise RuntimeError("thread-allocation-private-diagnostic")
    monkeypatch.setattr(source_runtime.threading, "Thread", cannot_allocate)
    try:
        with pytest.raises(RuntimeError):
            runtime.start()
        assert runtime._start_done.is_set(), "thread constructor failure stranded startup ownership"
        assert runtime.snapshot()["state"] == "failed"
        assert not runtime.snapshot()["cleanup_pending"]
    finally:
        runtime._start_done.set()  # Release an old broken implementation during RED.
        runtime.close()


def test_close_waits_for_dispatch_in_progress_then_reaps_late_worker(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    original_start = threading.Thread.start
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    def held(thread):
        if thread.name == "reverse-source-prewarm":
            entered.set()
            assert release.wait(10)
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, "start", held)
    starter = threading.Thread(target=runtime.start)
    def close():
        runtime.close()
        closed.set()
    closer = threading.Thread(target=close)
    try:
        starter.start()
        assert entered.wait(5)
        closer.start()
        assert not closed.wait(.05)
        assert runtime.snapshot()["cleanup_pending"]
        assert runtime.snapshot()["state"] == "closing"
    finally:
        release.set()
        starter.join(10)
        if closer.ident is not None:
            closer.join(10)
        runtime.close()
    assert closed.is_set() and not runtime._thread.is_alive()
    assert runtime.snapshot()["state"] == "closed"


def test_start_raising_after_dispatch_keeps_physical_worker_owned(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    entered, release, calls = blocked_loader(monkeypatch)
    original_start = threading.Thread.start
    def uncertain(thread):
        result = original_start(thread)
        if thread.name == "reverse-source-prewarm":
            assert entered.wait(5)
            raise RuntimeError("start-return-lost")
        return result
    monkeypatch.setattr(threading.Thread, "start", uncertain)
    try:
        with pytest.raises(RuntimeError):
            runtime.start()
        assert runtime.snapshot()["state"] == "failed"
        assert runtime.snapshot()["cleanup_pending"]
        with pytest.raises(ReverseSourceUnavailable):
            runtime.ready_predictor()
        runtime.start()
        assert len(calls) == 1
    finally:
        release.set()
        runtime.close()
    assert not calls[0].is_alive()


def test_bad_prewarm_configuration_does_not_fall_back_to_request_loader(tmp_path, monkeypatch):
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.reverse_target.predictor import ReverseTargetPredictor
    producer, _ = write_source(tmp_path / "source")
    monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(producer.data_dir))
    monkeypatch.setenv("REVERSE_TARGET_PREWARM_TIMEOUT_SECONDS", "invalid")
    calls = []
    monkeypatch.setattr(ReverseTargetPredictor, "initialize_strict", lambda *a, **kw: calls.append(True))
    tool = ReverseTargetTool()
    try:
        with pytest.raises(ValueError):
            tool.start_preload()
        assert tool.execute("CCO")["success"] is False
        assert tool.start_preload() is False
        assert not calls
    finally:
        tool.close()


@pytest.mark.parametrize("phase,verified,total", [
    ("private-source-text", 1, 3), ("validating_rows", True, 3),
    ("validating_rows", 4, 3), ("validating_rows", 0, 1_000_001),
])
def test_progress_never_exports_arbitrary_phases_or_invalid_counts(tmp_path, phase, verified, total):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    try:
        with pytest.raises(ValueError):
            runtime._progress(phase, verified, total)
        assert runtime.snapshot()["phase"] == "idle"
        assert runtime.snapshot()["total_rows"] == 0
    finally:
        runtime.close()


def test_prewarm_does_not_log_raw_rdkit_source_diagnostics(tmp_path, monkeypatch, capfd):
    from src.reverse_target.predictor import ReverseTargetPredictor
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    def invalid_fingerprint(*args, **kwargs):
        rdBase.LogWarningMsg("private-smiles-record-from-owned-source")
        return None, None
    monkeypatch.setattr(ReverseTargetPredictor, "compute_query_fingerprints", invalid_fingerprint)
    try:
        runtime.start()
        runtime._thread.join(10)
        assert runtime.snapshot()["state"] == "failed"
        captured = capfd.readouterr()
        assert "private-smiles-record" not in captured.err + captured.out
    finally:
        runtime.close()


def test_start_failure_after_fast_worker_finishes_cannot_leave_ready_status(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    original_start = threading.Thread.start
    def lost_start_reply(thread):
        result = original_start(thread)
        if thread.name == "reverse-source-prewarm":
            thread.join(10)
            assert not thread.is_alive()
            raise RuntimeError("lost-start-reply-after-worker-return")
        return result
    monkeypatch.setattr(threading.Thread, "start", lost_start_reply)
    try:
        with pytest.raises(RuntimeError):
            runtime.start()
        assert runtime.snapshot()["state"] == "failed"
        assert runtime.snapshot()["reason"] == "prewarm_start_failed"
        with pytest.raises(ReverseSourceUnavailable):
            runtime.ready_predictor()
    finally:
        runtime.close()


def interrupt_start_before_ident(monkeypatch, *, after_ident=False):
    """Keep real OS dispatch, but interrupt the caller before bootstrap sets ident."""
    bootstrap_method = "_set_tstate_lock" if after_ident else "_bootstrap"
    original_start = threading.Thread.start
    original_bootstrap = getattr(threading.Thread, bootstrap_method)
    entered, release = threading.Event(), threading.Event()
    workers = []
    def held_bootstrap(thread):
        if thread.name == "reverse-source-prewarm":
            entered.set()
            assert release.wait(10)
        return original_bootstrap(thread)
    def interrupted_start(thread):
        if thread.name != "reverse-source-prewarm":
            return original_start(thread)
        workers.append(thread)
        original_wait = thread._started.wait
        def interrupted_wait(*args, **kwargs):
            assert entered.wait(5)
            raise KeyboardInterrupt("interrupted-start-handshake")
        thread._started.wait = interrupted_wait
        try:
            return original_start(thread)
        finally:
            thread._started.wait = original_wait
    monkeypatch.setattr(threading.Thread, bootstrap_method, held_bootstrap)
    monkeypatch.setattr(threading.Thread, "start", interrupted_start)
    return entered, release, workers


@pytest.mark.parametrize("after_ident", [False, True])
def test_interrupted_start_before_ident_retains_worker_until_physical_exit(tmp_path, monkeypatch, after_ident):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    entered, release, workers = interrupt_start_before_ident(monkeypatch, after_ident=after_ident)
    closed = threading.Event()
    def close():
        runtime.close()
        closed.set()
    closer = threading.Thread(target=close)
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.start()
        assert entered.is_set() and (workers[0].ident is not None) is after_ident
        assert runtime.snapshot()["cleanup_pending"], "dispatched bootstrap lost ownership before ident"
        closer.start()
        assert not closed.wait(.05), "close settled while bootstrap was physically active"
        assert runtime.snapshot()["state"] == "closing"
        assert closer.is_alive(), "close abandoned an incomplete startup handshake"
    finally:
        release.set()
        if workers:
            assert workers[0]._started.wait(5)
            workers[0].join(10)
        if closer.ident is not None:
            closer.join(10)
        runtime.close()
    assert not workers[0].is_alive()
    assert runtime.snapshot()["state"] == "closed"
    assert not runtime.snapshot()["cleanup_pending"]


def test_app_shutdown_retains_interrupted_pre_ident_bootstrap_owner(tmp_path, monkeypatch):
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.agent.tooling import build_tool_registry
    from src.web.app import MolecularChatApp
    producer, _ = write_source(tmp_path / "source")
    monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(producer.data_dir))
    tool = ReverseTargetTool()
    application = MolecularChatApp.__new__(MolecularChatApp)
    application._llm_watch_task = None
    application.agent_system = SimpleNamespace(tools={tool.name: tool})
    application.agent_tool_registry = build_tool_registry([tool])
    application._reverse_preload_tool = tool
    entered, release, workers = interrupt_start_before_ident(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        tool.start_preload()
    close_entered = threading.Event()
    original_close = tool.close
    def close():
        close_entered.set()
        original_close()
    monkeypatch.setattr(tool, "close", close)
    async def exercise():
        shutdown = asyncio.create_task(application.shutdown())
        try:
            assert await asyncio.to_thread(close_entered.wait, 5)
            shutdown.cancel()
            shutdown.cancel()
            await asyncio.sleep(.05)
            assert not shutdown.done(), "application discarded pending bootstrap ownership"
            assert application._reverse_preload_tool is tool
            assert application.agent_tool_registry is not None
            assert tool._source_runtime.snapshot()["cleanup_pending"]
        finally:
            release.set()
            await asyncio.gather(shutdown, return_exceptions=True)
            if workers:
                assert await asyncio.to_thread(workers[0]._started.wait, 5)
                await asyncio.to_thread(workers[0].join, 10)
            await application.shutdown()
        assert application._reverse_preload_tool is None
        assert application.agent_tool_registry is None
        assert not workers[0].is_alive()
    asyncio.run(exercise())


def test_native_dispatch_failure_can_close_without_waiting_for_nonexistent_worker(tmp_path, monkeypatch):
    producer, _ = write_source(tmp_path / "source")
    runtime = runtime_type()(producer.data_dir)
    def cannot_dispatch(*args, **kwargs):
        raise RuntimeError("native-thread-allocation-failed")
    with monkeypatch.context() as patch:
        patch.setattr(threading, "_start_new_thread", cannot_dispatch)
        with pytest.raises(RuntimeError):
            runtime.start()
    assert runtime.snapshot()["state"] == "failed"
    assert runtime._thread.ident is None
    assert runtime._thread not in threading.enumerate()
    assert not runtime.snapshot()["cleanup_pending"]
    closed = threading.Event()
    def close():
        runtime.close()
        closed.set()
    closer = threading.Thread(target=close, daemon=True)
    closer.start()
    closer.join(2)
    assert closed.is_set(), "known dispatch failure was mistaken for a pending worker"


def test_overlapping_rdkit_log_blocks_cannot_expose_source_smiles(tmp_path, monkeypatch, capfd):
    from src.reverse_target.predictor import ReverseTargetPredictor
    producer, _ = write_source(tmp_path / "source")
    source = producer.training_data_path
    source.write_text(source.read_text(encoding="utf-8").replace("\tCCO\t", "\tC1NCCCCC\t"), encoding="utf-8")
    runtime = runtime_type()(producer.data_dir)
    original = ReverseTargetPredictor.compute_query_fingerprints
    entered, release = threading.Event(), threading.Event()
    def held(self, smiles):
        entered.set()
        assert release.wait(10)
        return original(self, smiles)
    monkeypatch.setattr(ReverseTargetPredictor, "compute_query_fingerprints", held)
    was_enabled = "rdApp.error:enabled" in rdBase.LogStatus()
    rdBase.EnableLog("rdApp.error")
    try:
        with rdBase.BlockLogs():
            runtime.start()
            assert entered.wait(5)
        # Another thread's earlier BlockLogs now restores the process-wide logs.
        release.set()
        runtime._thread.join(10)
        assert runtime.snapshot()["state"] == "failed"
        captured = capfd.readouterr()
        assert "C1NCCCCC" not in captured.out + captured.err
    finally:
        release.set()
        runtime.close()
        if not was_enabled:
            rdBase.DisableLog("rdApp.error")


def test_app_shutdown_preserves_optional_adapter_close_contract(tmp_path, monkeypatch):
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.agent.tooling import build_tool_registry
    from src.agent.tooling.adapters import HTTPToolAdapter
    from src.web.app import MolecularChatApp
    from tests.agent.test_tool_adapters import make_spec
    producer, _ = write_source(tmp_path / "source")
    monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(producer.data_dir))
    tool = ReverseTargetTool()
    registry = build_tool_registry([tool])
    registry.register(HTTPToolAdapter(make_spec(name="aaa-http"), "https://example.invalid"))
    closes = []
    later = SimpleNamespace(close=lambda: closes.append("later"))
    application = MolecularChatApp.__new__(MolecularChatApp)
    application._llm_watch_task = None
    application._reverse_preload_tool = tool
    application.agent_tool_registry = registry
    application.agent_system = SimpleNamespace(tools={tool.name: tool, "later": later})
    try:
        assert tool.start_preload()
        asyncio.run(application.shutdown())
        assert closes == ["later"]
        assert tool._source_runtime.snapshot()["state"] == "closed"
    finally:
        tool.close()


def test_app_without_prewarm_closes_standalone_adapter():
    from src.agent.tooling.registry import ToolRegistry
    from src.agent.tooling.adapters import HTTPToolAdapter
    from src.web.app import MolecularChatApp
    from tests.agent.test_tool_adapters import make_spec
    closes = []
    adapter = HTTPToolAdapter(make_spec(name="standalone-http"), "https://example.invalid")
    adapter.close = lambda: closes.append("adapter")
    registry = ToolRegistry()
    registry.register(adapter)
    application = MolecularChatApp.__new__(MolecularChatApp)
    application._llm_watch_task = None
    application.agent_tool_registry = registry
    application.agent_system = SimpleNamespace(tools={})
    asyncio.run(application.shutdown())
    assert closes == ["adapter"]
