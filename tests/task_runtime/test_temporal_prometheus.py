from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from src.task_runtime import prometheus_metrics
from src.task_runtime.prometheus_metrics import (
    MetricsServerHandle,
    TemporalWorkerMetrics,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _MutableUtcClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _render(metrics: TemporalWorkerMetrics) -> str:
    return generate_latest(metrics.registry).decode("utf-8")


def _sample_value(metrics: TemporalWorkerMetrics, name: str, labels=None) -> float:
    labels = labels or {}
    for family in text_string_to_metric_families(_render(metrics)):
        for sample in family.samples:
            if sample.name == name and sample.labels == labels:
                return float(sample.value)
    raise AssertionError(f"missing sample {name}")


def test_metrics_expose_exact_fixed_names_labels_and_no_sensitive_values():
    clock = _MutableUtcClock(datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc))
    metrics = TemporalWorkerMetrics(CollectorRegistry(), utc_now=clock)
    metrics.set_ready(True)
    metrics.configure_release(canary_percent=5, baseline_p95_seconds=40.0)
    metrics.record_backup_verification(clock().timestamp())
    metrics.record_worker_heartbeat()
    metrics.docking_process_attempt("docking", 2)
    metrics.activity_started("docking")
    metrics.activity_finished("docking", 12.5)
    metrics.terminal_projected("docking", "succeeded")
    metrics.terminal_invariant_violation()
    metrics.artifact_validation_failure()
    metrics.provenance_validation_failure()

    rendered = _render(metrics)
    expected_names = {
        "medchat_temporal_worker_process_start_timestamp_seconds",
        "medchat_temporal_worker_ready",
        "medchat_temporal_worker_last_heartbeat_timestamp_seconds",
        "medchat_temporal_active_tasks",
        "medchat_temporal_docking_process_attempts_total",
        "medchat_temporal_task_terminal_total",
        "medchat_temporal_task_duration_seconds_bucket",
        "medchat_temporal_task_duration_seconds_count",
        "medchat_temporal_task_duration_seconds_sum",
        "medchat_temporal_canary_percent",
        "medchat_temporal_baseline_p95_seconds",
        "medchat_temporal_backup_verified_timestamp_seconds",
        "medchat_temporal_duplicate_vina_execution_total",
        "medchat_temporal_terminal_invariant_violation_total",
        "medchat_temporal_artifact_validation_failure_total",
        "medchat_temporal_provenance_validation_failure_total",
    }
    samples = [
        sample
        for family in text_string_to_metric_families(rendered)
        for sample in family.samples
    ]
    names = {sample.name for sample in samples}
    assert expected_names <= names
    assert all(set(sample.labels) <= {"task_type", "status", "le"} for sample in samples)
    assert all(
        sample.labels.get("task_type", "docking") == "docking"
        for sample in samples
    )
    assert all(
        sample.labels.get("status", "succeeded")
        in {"succeeded", "failed", "canceled", "timed_out"}
        for sample in samples
    )
    for forbidden in (
        "private-task",
        "trace_id",
        "workflow_id",
        "user_id",
        "smiles",
        "prompt",
        "c:/private",
        "sk-secret",
        "error_text",
    ):
        assert forbidden not in rendered.lower()


def test_metrics_instances_use_isolated_registries_by_default_and_when_injected():
    first = TemporalWorkerMetrics()
    second = TemporalWorkerMetrics()
    explicit_first = TemporalWorkerMetrics(CollectorRegistry())
    explicit_second = TemporalWorkerMetrics(CollectorRegistry())

    first.set_ready(True)

    assert first.registry is not second.registry
    assert explicit_first.registry is not explicit_second.registry
    assert _sample_value(first, "medchat_temporal_worker_ready") == 1.0
    assert _sample_value(second, "medchat_temporal_worker_ready") == 0.0


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("activity_started", ("private-task",)),
        ("activity_finished", ("private-task", 1.0)),
        ("docking_process_attempt", ("private-task", 1)),
        ("terminal_projected", ("private-task", "succeeded")),
        ("terminal_projected", ("docking", "invented")),
    ],
)
def test_metrics_reject_unknown_task_types_and_terminal_statuses(method, args):
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    with pytest.raises(ValueError):
        getattr(metrics, method)(*args)


@pytest.mark.parametrize(
    "duration",
    [True, False, -1, 10**400, float("nan"), float("inf"), -float("inf"), "1"],
)
def test_duration_requires_an_exact_finite_nonnegative_number(duration):
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    metrics.activity_started("docking")
    with pytest.raises(ValueError):
        metrics.activity_finished("docking", duration)


@pytest.mark.parametrize("attempt", [True, False, 0, -1, 1.0, float("nan"), "1"])
def test_attempt_requires_an_exact_positive_integer(attempt):
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    with pytest.raises(ValueError):
        metrics.docking_process_attempt("docking", attempt)


@pytest.mark.parametrize("ready", [0, 1, None, "true"])
def test_readiness_requires_an_exact_boolean(ready):
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    with pytest.raises(ValueError):
        metrics.set_ready(ready)


@pytest.mark.parametrize("percent", [True, -1, 1, 5.0, 20, 100, "5"])
def test_release_level_is_limited_to_exact_canary_steps(percent):
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    with pytest.raises(ValueError):
        metrics.configure_release(percent, 40.0)


@pytest.mark.parametrize(
    "baseline",
    [
        True,
        False,
        0,
        -1,
        60.1,
        10**400,
        float("nan"),
        float("inf"),
        -float("inf"),
        "40",
    ],
)
def test_release_baseline_is_finite_positive_and_bounded(baseline):
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    with pytest.raises(ValueError):
        metrics.configure_release(5, baseline)


@pytest.mark.parametrize(
    "timestamp",
    [True, False, -1, 10**400, float("nan"), float("inf"), "1"],
)
def test_backup_timestamp_requires_an_exact_finite_nonnegative_number(timestamp):
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    with pytest.raises(ValueError):
        metrics.record_backup_verification(timestamp)


def test_process_start_is_set_once_and_heartbeat_uses_injected_utc_clock():
    clock = _MutableUtcClock(datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc))
    metrics = TemporalWorkerMetrics(CollectorRegistry(), utc_now=clock)
    started = clock().timestamp()

    clock.value += timedelta(seconds=17)
    metrics.record_worker_heartbeat()

    assert _sample_value(
        metrics,
        "medchat_temporal_worker_process_start_timestamp_seconds",
    ) == started
    assert _sample_value(
        metrics,
        "medchat_temporal_worker_last_heartbeat_timestamp_seconds",
    ) == clock().timestamp()


def test_active_balance_never_drops_below_zero_on_duplicate_finish():
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    metrics.activity_started("docking")
    metrics.activity_finished("docking", 1.0)
    metrics.activity_finished("docking", 2.0)

    assert _sample_value(
        metrics,
        "medchat_temporal_active_tasks",
        {"task_type": "docking"},
    ) == 0.0
    assert _sample_value(
        metrics,
        "medchat_temporal_task_duration_seconds_count",
        {"task_type": "docking"},
    ) == 1.0


def test_active_balance_is_concurrency_safe_under_duplicate_finishes():
    import concurrent.futures

    metrics = TemporalWorkerMetrics(CollectorRegistry())
    starts = 50
    finishes = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda _: metrics.activity_started("docking"), range(starts)))
        list(
            executor.map(
                lambda _: metrics.activity_finished("docking", 0.01),
                range(finishes),
            )
        )

    assert _sample_value(
        metrics,
        "medchat_temporal_active_tasks",
        {"task_type": "docking"},
    ) == 0.0
    assert _sample_value(
        metrics,
        "medchat_temporal_task_duration_seconds_count",
        {"task_type": "docking"},
    ) == starts


def test_backup_refresh_uses_trusted_reader_and_resets_on_invalid_state(tmp_path):
    clock = _MutableUtcClock(datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc))
    metrics = TemporalWorkerMetrics(CollectorRegistry(), utc_now=clock)
    marker = (tmp_path / "latest-verified.json").resolve()
    verified_at = clock.value - timedelta(minutes=5)
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "verified_at": verified_at.isoformat(),
                "sha256": "a" * 64,
                "ignored_private_field": "C:/private/CCO sk-secret",
            }
        ),
        encoding="utf-8",
    )

    assert metrics.refresh_backup_verification(marker) is True
    assert _sample_value(
        metrics,
        "medchat_temporal_backup_verified_timestamp_seconds",
    ) == verified_at.timestamp()
    assert "ignored_private_field" not in _render(metrics)

    invalid_payloads = [
        {"status": "failed", "verified_at": verified_at.isoformat(), "sha256": "a" * 64},
        {"status": "passed", "verified_at": verified_at.isoformat(), "sha256": "A" * 64},
        {"status": "passed", "verified_at": verified_at.replace(tzinfo=None).isoformat(), "sha256": "a" * 64},
        {"status": "passed", "verified_at": (clock.value + timedelta(seconds=1)).isoformat(), "sha256": "a" * 64},
    ]
    for payload in invalid_payloads:
        marker.write_text(json.dumps(payload), encoding="utf-8")
        assert metrics.refresh_backup_verification(marker) is False
        assert _sample_value(
            metrics,
            "medchat_temporal_backup_verified_timestamp_seconds",
        ) == 0.0

    marker.unlink()
    assert metrics.refresh_backup_verification(marker) is False
    assert _sample_value(
        metrics,
        "medchat_temporal_backup_verified_timestamp_seconds",
    ) == 0.0


def test_backup_refresh_rejects_duplicate_json_keys(tmp_path):
    clock = _MutableUtcClock(datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc))
    metrics = TemporalWorkerMetrics(CollectorRegistry(), utc_now=clock)
    marker = (tmp_path / "latest-verified.json").resolve()
    marker.write_text(
        '{"status":"passed","status":"passed","sha256":"'
        + "a" * 64
        + '","verified_at":"2026-08-22T03:55:00+00:00"}',
        encoding="utf-8",
    )

    assert metrics.refresh_backup_verification(marker) is False
    assert _sample_value(
        metrics,
        "medchat_temporal_backup_verified_timestamp_seconds",
    ) == 0.0


def test_backup_refresh_rejects_trust_boundary_race(tmp_path, monkeypatch):
    clock = _MutableUtcClock(datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc))
    metrics = TemporalWorkerMetrics(CollectorRegistry(), utc_now=clock)
    marker = (tmp_path / "latest-verified.json").resolve()
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "sha256": "a" * 64,
                "verified_at": "2026-08-22T03:55:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    boundaries = iter((object(), object()))
    monkeypatch.setattr(
        prometheus_metrics,
        "capture_trusted_path_boundary",
        lambda _path: next(boundaries),
    )

    assert metrics.refresh_backup_verification(marker) is False
    assert _sample_value(
        metrics,
        "medchat_temporal_backup_verified_timestamp_seconds",
    ) == 0.0


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX trust boundary")
def test_backup_refresh_rejects_writable_file_or_parent(tmp_path):
    clock = _MutableUtcClock(datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc))
    metrics = TemporalWorkerMetrics(CollectorRegistry(), utc_now=clock)
    parent = tmp_path / "backup"
    parent.mkdir(mode=0o700)
    marker = (parent / "latest-verified.json").resolve()
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "sha256": "a" * 64,
                "verified_at": "2026-08-22T03:55:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    marker.chmod(0o666)
    assert metrics.refresh_backup_verification(marker) is False
    marker.chmod(0o600)
    parent.chmod(0o777)
    try:
        assert metrics.refresh_backup_verification(marker) is False
    finally:
        parent.chmod(0o700)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows ACL boundary")
def test_backup_refresh_accepts_live_trusted_windows_dacl(tmp_path):
    clock = _MutableUtcClock(datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc))
    metrics = TemporalWorkerMetrics(CollectorRegistry(), utc_now=clock)
    marker = (tmp_path / "latest-verified.json").resolve()
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "sha256": "a" * 64,
                "verified_at": "2026-08-22T03:55:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    assert metrics.refresh_backup_verification(marker) is True


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.parametrize(
    ("address", "port"),
    [
        ("0.0.0.0", 9465),
        ("localhost", 9465),
        ("127.0.0.1", True),
        ("127.0.0.1", 0),
        ("127.0.0.1", 65536),
        ("127.0.0.1", 9465.0),
    ],
)
def test_metrics_server_rejects_non_loopback_or_invalid_port(address, port):
    with pytest.raises(ValueError):
        TemporalWorkerMetrics(CollectorRegistry()).start_loopback_server(address, port)


def test_metrics_server_lifecycle_shuts_down_and_propagates_port_conflicts():
    port = _unused_loopback_port()
    first = TemporalWorkerMetrics(CollectorRegistry())
    second = TemporalWorkerMetrics(CollectorRegistry())
    handle = first.start_loopback_server("127.0.0.1", port)
    server_thread = handle._thread
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
            assert response.status == 200
            assert b"medchat_temporal_worker_ready" in response.read()
        with pytest.raises(OSError):
            second.start_loopback_server("127.0.0.1", port)
    finally:
        handle.shutdown()
        handle.shutdown()

    assert not server_thread.is_alive()

    replacement = second.start_loopback_server("127.0.0.1", port)
    replacement_thread = replacement._thread
    replacement.shutdown()
    assert not replacement_thread.is_alive()


class _InjectedMetricsServer:
    def __init__(self, *, fail_shutdown=False, fail_close_once=False) -> None:
        self.fail_shutdown = fail_shutdown
        self.fail_close_once = fail_close_once
        self.shutdown_calls = 0
        self.close_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self.fail_shutdown:
            raise RuntimeError("PRIVATE_SHUTDOWN_CANARY")

    def server_close(self) -> None:
        self.close_calls += 1
        if self.fail_close_once and self.close_calls == 1:
            raise RuntimeError("PRIVATE_CLOSE_CANARY")


class _InjectedMetricsThread:
    def __init__(self, *, stuck=False) -> None:
        self.stuck = stuck
        self.alive = True
        self.join_calls = 0

    def join(self, timeout=None) -> None:
        self.join_calls += 1
        if not self.stuck:
            self.alive = False

    def is_alive(self) -> bool:
        return self.alive


def _owned_metrics_handle(server, thread, endpoint):
    with prometheus_metrics._ENDPOINT_LOCK:
        prometheus_metrics._BOUND_ENDPOINTS.add(endpoint)
    return MetricsServerHandle(server, thread, endpoint)


def test_metrics_handle_always_closes_and_joins_after_shutdown_failure():
    endpoint = ("127.0.0.1", _unused_loopback_port())
    server = _InjectedMetricsServer(fail_shutdown=True)
    thread = _InjectedMetricsThread()
    handle = _owned_metrics_handle(server, thread, endpoint)

    handle.shutdown()

    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    assert thread.join_calls == 1
    assert not thread.is_alive()
    assert endpoint not in prometheus_metrics._BOUND_ENDPOINTS


def test_metrics_handle_close_failure_is_generic_and_retryable():
    endpoint = ("127.0.0.1", _unused_loopback_port())
    server = _InjectedMetricsServer(fail_close_once=True)
    thread = _InjectedMetricsThread()
    handle = _owned_metrics_handle(server, thread, endpoint)

    with pytest.raises(RuntimeError, match="^Temporal metrics server shutdown failed$") as failure:
        handle.shutdown()
    assert failure.value.__cause__ is None
    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    assert thread.join_calls == 1
    assert endpoint in prometheus_metrics._BOUND_ENDPOINTS

    handle.shutdown()
    assert server.close_calls == 2
    assert endpoint not in prometheus_metrics._BOUND_ENDPOINTS


def test_metrics_handle_stuck_thread_retains_endpoint_until_successful_retry():
    endpoint = ("127.0.0.1", _unused_loopback_port())
    server = _InjectedMetricsServer()
    thread = _InjectedMetricsThread(stuck=True)
    handle = _owned_metrics_handle(server, thread, endpoint)

    with pytest.raises(RuntimeError, match="^Temporal metrics server shutdown failed$"):
        handle.shutdown()
    assert endpoint in prometheus_metrics._BOUND_ENDPOINTS
    assert thread.is_alive()

    thread.stuck = False
    handle.shutdown()
    assert not thread.is_alive()
    assert endpoint not in prometheus_metrics._BOUND_ENDPOINTS


def _load_worker_script():
    path = PROJECT_ROOT / "scripts" / "run_temporal_docking_worker.py"
    spec = importlib.util.spec_from_file_location("test_metrics_worker_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _script_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        temporal_address="private-temporal.example:7233",
        temporal_namespace="default",
        docking_queue="medchat-docking",
        staging_root=tmp_path / "staging",
        worker_metrics_address="127.0.0.1",
        worker_metrics_port=9465,
        canary_percent=5,
        baseline_p95_seconds=40.0,
        backup_state_path=tmp_path / "latest-verified.json",
    )


def test_worker_script_binds_metrics_before_temporal_connect_and_shuts_down(tmp_path, monkeypatch):
    module = _load_worker_script()
    events: list[str] = []

    class Handle:
        def shutdown(self):
            events.append("metrics_shutdown")

    class Metrics:
        def configure_release(self, *_args):
            events.append("configure")

        def refresh_backup_verification(self, *_args):
            events.append("backup")

        def start_loopback_server(self, *_args):
            events.append("bind")
            return Handle()

    async def connect(*_args, **_kwargs):
        events.append("connect")
        return object()

    async def run_worker(*_args, **kwargs):
        events.append("poll")
        assert kwargs["metrics"] is metrics

    metrics = Metrics()
    config = _script_config(tmp_path)
    monkeypatch.setattr(
        module,
        "validate_production_worker_config",
        lambda *_args, **_kwargs: events.append("validate"),
    )
    monkeypatch.setattr(module, "TemporalWorkerMetrics", lambda: metrics)
    monkeypatch.setattr(module.TaskRuntimeConfig, "from_env", lambda: config)
    monkeypatch.setattr(module, "Client", SimpleNamespace(connect=connect))
    monkeypatch.setattr(module, "TaskStore", lambda: object())
    monkeypatch.setattr(module, "DockingExecution", lambda *_args: object())
    monkeypatch.setattr(module, "DockingInputStager", lambda *_args: object())
    monkeypatch.setattr(module, "ManagedDockingProcessRunner", lambda *_args: object())
    monkeypatch.setattr(module, "DockingProcessConfig", lambda **_kwargs: object())
    monkeypatch.setattr(module, "TemporalDockingActivities", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "run_temporal_worker", run_worker)
    monkeypatch.setattr(module, "_install_signal_handlers", lambda *_args: None)
    monkeypatch.setattr(module.importlib.metadata, "version", lambda *_args: "1.30.0")

    asyncio.run(module.main())

    assert events == [
        "validate", "configure", "backup", "bind", "connect", "poll",
        "metrics_shutdown",
    ]


def test_worker_script_bind_failure_prevents_connect_polling_and_science(tmp_path, monkeypatch, capsys):
    module = _load_worker_script()
    calls = {"connect": 0, "poll": 0, "science": 0}

    class Metrics:
        def configure_release(self, *_args):
            pass

        def refresh_backup_verification(self, *_args):
            return False

        def start_loopback_server(self, *_args):
            raise OSError("C:/private/CCO sk-secret bind detail")

    async def connect(*_args, **_kwargs):
        calls["connect"] += 1

    async def run_worker(*_args, **_kwargs):
        calls["poll"] += 1

    def science(*_args, **_kwargs):
        calls["science"] += 1

    config = _script_config(tmp_path)
    monkeypatch.setattr(
        module,
        "validate_production_worker_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(module, "TemporalWorkerMetrics", Metrics)
    monkeypatch.setattr(module.TaskRuntimeConfig, "from_env", lambda: config)
    monkeypatch.setattr(module, "Client", SimpleNamespace(connect=connect))
    monkeypatch.setattr(module, "DockingExecution", science)
    monkeypatch.setattr(module, "run_temporal_worker", run_worker)

    with pytest.raises(OSError):
        asyncio.run(module.main())

    assert calls == {"connect": 0, "poll": 0, "science": 0}
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
    assert "CCO" not in output.out + output.err
    assert "sk-secret" not in output.out + output.err


@pytest.mark.parametrize(
    "failure",
    [
        OSError("C:/private/CCO bind detail"),
        ConnectionError("PRIVATE_CONNECT_CANARY"),
        RuntimeError("PRIVATE_POLL_OR_SHUTDOWN_CANARY"),
    ],
)
def test_worker_script_cli_redacts_expected_runtime_failures(
    monkeypatch,
    capsys,
    failure,
):
    module = _load_worker_script()

    def fail_runtime(_coroutine):
        _coroutine.close()
        raise failure

    monkeypatch.setattr(module.asyncio, "run", fail_runtime)

    assert module._run_cli() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "temporal_worker_failed code=WORKER_RUNTIME_FAILED\n"
    assert "private" not in output.err.lower()
    assert "cco" not in output.err.lower()


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(7)])
def test_worker_script_cli_preserves_process_control_exceptions(
    monkeypatch,
    capsys,
    failure,
):
    module = _load_worker_script()

    def interrupt(_coroutine):
        _coroutine.close()
        raise failure

    monkeypatch.setattr(module.asyncio, "run", interrupt)

    with pytest.raises(type(failure)) as raised:
        module._run_cli()
    if isinstance(failure, SystemExit):
        assert raised.value.code == 7
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
