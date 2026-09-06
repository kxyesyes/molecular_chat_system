from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.task_runtime.backends.base import BackendSubmitResult, StartOutcome
from src.task_runtime.backends.local import (
    LocalTaskBackend,
    TaskIdempotencyConflictError,
)
from src.task_runtime.backends.temporal import (
    TemporalStartOutcome,
    TemporalTaskBackend,
)
from src.task_runtime.models import (
    BackendHealth,
    TaskPhase,
    TaskStatus,
    TaskSubmission,
)
from src.task_runtime.config import TaskRuntimeConfig
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.selector import BackendDecision
from src.task_runtime.store import TaskStore
from src.task_runtime.temporal.workflows import (
    DOCKING_ACTIVITY_NAME,
    PROJECTION_ACTIVITY_NAME,
    VERIFY_MANIFEST_ACTIVITY_NAME,
    DockingWorkflow,
)


TASK_ID = "00000000-0000-4000-8000-000000000101"
WORKFLOW_ID = f"medchat-docking-{TASK_ID}"
DIGEST = "a" * 64


class WorkflowNotFoundError(RuntimeError):
    pass


class FakeHandle:
    def __init__(
        self,
        *,
        snapshot=None,
        describe_result=None,
        describe_error=None,
        cancel_started=None,
        cancel_release=None,
        cancel_error=None,
    ):
        self.snapshot = snapshot or {
            "task_id": TASK_ID,
            "status": "queued",
            "phase": "staging",
            "attempt": 0,
            "error_code": None,
            "projection_pending": False,
            "terminal_event_count": 0,
        }
        self.describe_result = describe_result or type(
            "Description",
            (),
            {"status": type("Status", (), {"name": "RUNNING"})()},
        )()
        self.describe_error = describe_error
        self.cancel_count = 0
        self.query_count = 0
        self.cancel_started = cancel_started
        self.cancel_release = cancel_release
        self.cancel_error = cancel_error
        self.cancel_completed = False

    async def describe(self):
        if self.describe_error is not None:
            raise self.describe_error
        return self.describe_result

    async def query(self, name):
        self.query_count += 1
        assert name == "task_snapshot"
        return dict(self.snapshot)

    async def cancel(self, **kwargs):
        self.cancel_count += 1
        if self.cancel_started is not None:
            self.cancel_started.set()
        if self.cancel_release is not None:
            await self.cancel_release.wait()
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancel_completed = True


class FakeServiceClient:
    def __init__(self, health_error=None, health_result=True):
        self.health_error = health_error
        self.health_result = health_result
        self.health_calls = 0

    async def check_health(self):
        self.health_calls += 1
        if self.health_error is not None:
            raise self.health_error
        return self.health_result


class FakeClient:
    def __init__(
        self,
        handle: FakeHandle,
        *,
        start_error=None,
        on_start=None,
        health_error=None,
        health_result=True,
    ):
        self.handle = handle
        self.start_error = start_error
        self.on_start = on_start
        self.start_calls = []
        self.handle_ids = []
        self.service_client = FakeServiceClient(health_error, health_result)

    async def start_workflow(self, workflow, payload, **kwargs):
        self.start_calls.append((workflow, payload, kwargs))
        if self.on_start is not None:
            self.on_start()
        if self.start_error is not None:
            raise self.start_error
        return self.handle

    def get_workflow_handle(self, workflow_id):
        self.handle_ids.append(workflow_id)
        return self.handle


def _submission(tmp_path: Path, task_id: str = TASK_ID) -> TaskSubmission:
    return TaskSubmission(
        task_id=task_id,
        task_type="docking",
        payload={
            "decision": {
                "backend": "temporal",
                "reason": "canary_selected",
                "bucket": 1,
                "percent": 10,
            },
            "mode": "file",
            "total_bytes": 10,
            "config_hash": "b" * 64,
            "request_digest": DIGEST,
        },
        input_manifest_path=str(tmp_path / task_id / "input_manifest.json"),
        request_digest=DIGEST,
    )


def _backend(tmp_path, client, *, store=None):
    async def client_factory(address, namespace):
        assert address == "127.0.0.1:7233"
        assert namespace == "default"
        return client

    return TemporalTaskBackend(
        store or TaskStore(tmp_path / "tasks.sqlite"),
        address="127.0.0.1:7233",
        namespace="default",
        task_queue="medchat-docking",
        client_factory=client_factory,
    )


def test_temporal_start_outcome_contract_is_tiny_and_explicit():
    outcome = TemporalStartOutcome(
        accepted=True,
        workflow_id=WORKFLOW_ID,
        error_code=None,
    )
    assert outcome.accepted is True
    assert outcome.workflow_id == WORKFLOW_ID
    assert outcome.error_code is None


def test_runtime_idempotency_never_reports_ambiguous_temporal_as_accepted(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    record = store.create(
        TASK_ID,
        "docking",
        {},
        backend="temporal",
        external_workflow_id=WORKFLOW_ID,
        provenance={"start_outcome": "ambiguous"},
        submission_digest=DIGEST,
    )

    result = TaskRuntime._authority_result(
        record,
        DIGEST,
        request_digest=DIGEST,
        task_type="docking",
        allowed_backends={"temporal"},
    )

    assert result.outcome is StartOutcome.AMBIGUOUS


def test_submit_starts_safe_workflow_and_projects_authority_once(tmp_path):
    async def scenario():
        handle = FakeHandle()
        client = FakeClient(handle)
        backend = _backend(tmp_path, client)
        submission = _submission(tmp_path)

        first = await backend.submit(submission)
        second = await backend.submit(submission)

        assert first.outcome is StartOutcome.ACCEPTED
        assert second.outcome is StartOutcome.ACCEPTED
        assert len(client.start_calls) == 1
        workflow, payload, kwargs = client.start_calls[0]
        assert payload == {
            "task_id": TASK_ID,
            "manifest_locator": "input_manifest.json",
        }
        assert kwargs["id"] == WORKFLOW_ID
        assert kwargs["task_queue"] == "medchat-docking"
        assert "id_reuse_policy" in kwargs
        assert kwargs["id_conflict_policy"].name == "FAIL"
        assert kwargs["request_eager_start"] is False
        assert isinstance(kwargs["request_id"], str)
        assert kwargs["request_id"] == backend._start_request_id(TASK_ID)
        record = await backend.get(TASK_ID)
        assert record.backend == "temporal"
        assert record.external_workflow_id == WORKFLOW_ID
        assert record.input_manifest_path == submission.input_manifest_path

    asyncio.run(scenario())


def test_submit_reuses_idempotency_winner_with_different_loser_task_id(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        client = FakeClient(FakeHandle())
        backend = _backend(tmp_path, client, store=store)
        first = _submission(tmp_path)
        first = TaskSubmission(
            **{**first.__dict__, "idempotency_key": "same-request"}
        )
        loser_id = "00000000-0000-4000-8000-000000000102"
        loser = _submission(tmp_path, loser_id)
        loser = TaskSubmission(
            **{**loser.__dict__, "idempotency_key": "same-request"}
        )

        accepted = await backend.submit(first)
        reused = await backend.submit(loser)

        assert accepted.task_id == TASK_ID
        assert reused.task_id == TASK_ID
        assert reused.outcome is StartOutcome.ACCEPTED
        assert len(client.start_calls) == 1
        with pytest.raises(KeyError):
            store.get(loser_id)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("describe_error", "expected"),
    [
        (None, StartOutcome.ACCEPTED),
        (WorkflowNotFoundError("not found"), StartOutcome.AMBIGUOUS),
        (RuntimeError("unknown transport error"), StartOutcome.AMBIGUOUS),
    ],
)
def test_ambiguous_start_is_resolved_before_local_fallback(
    tmp_path,
    describe_error,
    expected,
):
    async def scenario():
        handle = FakeHandle(describe_error=describe_error)
        client = FakeClient(handle, start_error=RuntimeError("start failed"))
        backend = _backend(tmp_path, client)

        result = await backend.submit(_submission(tmp_path))

        assert result.outcome is expected
        record = backend.store.get(TASK_ID)
        assert record.backend == "temporal"
        assert record.external_workflow_id == WORKFLOW_ID
        if expected is StartOutcome.ACCEPTED:
            assert record.provenance["start_outcome"] == "accepted"
        else:
            assert record.provenance["start_outcome"] == "ambiguous"

    asyncio.run(scenario())


def test_start_authority_exists_before_temporal_rpc(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        observed = []

        def observe_reservation():
            try:
                record = store.get(TASK_ID)
            except KeyError:
                observed.append(None)
            else:
                observed.append(
                    (
                        record.backend,
                        record.external_workflow_id,
                        record.provenance["start_outcome"],
                    )
                )

        client = FakeClient(FakeHandle(), on_start=observe_reservation)
        backend = _backend(tmp_path, client, store=store)

        result = await backend.submit(_submission(tmp_path))

        assert result.outcome is StartOutcome.ACCEPTED
        assert observed == [("temporal", WORKFLOW_ID, "pending")]
        assert store.get(TASK_ID).provenance["start_outcome"] == "accepted"

    asyncio.run(scenario())


def test_temporal_start_outcome_is_monotonic_after_acceptance(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = _backend(tmp_path, FakeClient(FakeHandle()), store=store)
    backend._reserve_authority(_submission(tmp_path), WORKFLOW_ID)

    backend._mark_start_outcome(TASK_ID, "accepted")
    backend._mark_start_outcome(TASK_ID, "ambiguous")

    record = store.get(TASK_ID)
    assert record.provenance["start_outcome"] == "accepted"
    assert {warning["code"] for warning in record.warnings} == set()


def test_temporal_cancel_rejects_non_temporal_authority(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        store.create(TASK_ID, "docking", {}, backend="local")
        backend = _backend(tmp_path, FakeClient(FakeHandle()), store=store)

        with pytest.raises(ValueError, match="authority"):
            await backend.cancel(TASK_ID)

        assert store.get(TASK_ID).status is TaskStatus.QUEUED

    asyncio.run(scenario())


def test_cancel_requests_projection_then_temporal_cancel_without_terminal(tmp_path):
    async def scenario():
        handle = FakeHandle()
        backend = _backend(tmp_path, FakeClient(handle))
        await backend.submit(_submission(tmp_path))

        record = await backend.cancel(TASK_ID, "user request")

        assert record.status is TaskStatus.CANCEL_REQUESTED
        assert handle.cancel_count == 1
        events = backend.store.events(TASK_ID)
        assert [event.event_type for event in events] == [
            "task_created",
            "task_projection_updated",
            "task_cancel_requested",
        ]
        assert not any(event.is_terminal for event in events)

    asyncio.run(scenario())


def test_cancel_rpc_completes_before_caller_cancellation_is_propagated(tmp_path):
    async def scenario():
        cancel_started = asyncio.Event()
        cancel_release = asyncio.Event()
        handle = FakeHandle(
            cancel_started=cancel_started,
            cancel_release=cancel_release,
        )
        backend = _backend(tmp_path, FakeClient(handle))
        await backend.submit(_submission(tmp_path))

        operation = asyncio.create_task(backend.cancel(TASK_ID))
        await cancel_started.wait()
        operation.cancel()
        await asyncio.sleep(0)
        assert handle.cancel_completed is False
        cancel_release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation

        assert handle.cancel_completed is True
        assert backend.store.get(TASK_ID).status is TaskStatus.CANCEL_REQUESTED

    asyncio.run(scenario())


def test_reconcile_replays_durable_cancel_intent(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        handle = FakeHandle(cancel_error=RuntimeError("transport unavailable"))
        backend = _backend(tmp_path, FakeClient(handle), store=store)
        await backend.submit(_submission(tmp_path))
        store.claim_running(TASK_ID)

        await backend.cancel(TASK_ID)
        assert handle.cancel_count == 1
        handle.cancel_error = None

        record = await backend.reconcile(TASK_ID)

        assert record.status is TaskStatus.CANCEL_REQUESTED
        assert handle.cancel_count == 2
        assert handle.cancel_completed is True

    asyncio.run(scenario())


def test_get_repairs_running_snapshot_and_marks_query_failure_stale(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        handle = FakeHandle(
            snapshot={
                "task_id": TASK_ID,
                "status": "running",
                "phase": "vina_running",
                "attempt": 2,
                "error_code": None,
                "projection_pending": False,
                "terminal_event_count": 0,
            }
        )
        backend = _backend(tmp_path, FakeClient(handle), store=store)
        await backend.submit(_submission(tmp_path))

        running = await backend.get(TASK_ID)
        assert running.status is TaskStatus.RUNNING
        assert running.phase == TaskPhase.VINA_RUNNING.value
        assert running.attempt == 2

        handle.describe_error = RuntimeError("query unavailable CCO sk-secret")
        stale = await backend.get(TASK_ID)
        stale = await backend.get(TASK_ID)
        assert stale.status is TaskStatus.RUNNING
        assert {item["code"] for item in stale.warnings} == {"projection_stale"}
        assert "secret" not in repr(stale.warnings)
        stale_events = [
            event
            for event in store.events(TASK_ID)
            if event.event_type == "task_projection_updated"
            and event.payload.get("warning_codes") == ["projection_stale"]
        ]
        assert len(stale_events) == 1

    asyncio.run(scenario())


def test_connect_caches_only_successful_client(tmp_path):
    async def scenario():
        client = FakeClient(FakeHandle())
        calls = 0

        async def factory(address, namespace):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("unavailable")
            return client

        backend = TemporalTaskBackend(
            TaskStore(tmp_path / "tasks.sqlite"),
            address="127.0.0.1:7233",
            namespace="default",
            task_queue="medchat-docking",
            client_factory=factory,
        )
        with pytest.raises(RuntimeError):
            await backend.connect()
        assert await backend.connect() is client
        assert await backend.connect() is client
        assert calls == 2

    asyncio.run(scenario())


def test_submit_rejects_before_reservation_when_temporal_cannot_connect(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")

        async def unavailable(address, namespace):
            raise RuntimeError("connection unavailable")

        backend = TemporalTaskBackend(
            store,
            address="127.0.0.1:7233",
            namespace="default",
            task_queue="medchat-docking",
            client_factory=unavailable,
        )

        result = await backend.submit(_submission(tmp_path))

        assert result.outcome is StartOutcome.REJECTED
        with pytest.raises(KeyError):
            store.get(TASK_ID)

    asyncio.run(scenario())


def test_temporal_backend_contract_is_async_and_imports_sdk_lazily():
    for name in ("submit", "get", "list", "cancel", "health"):
        assert inspect.iscoroutinefunction(getattr(TemporalTaskBackend, name))
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "task_runtime"
        / "backends"
        / "temporal.py"
    ).read_text(encoding="utf-8")
    assert "from temporalio" not in source.split("async def connect", 1)[0]


def test_runtime_installs_temporal_backend_only_for_enabled_canary(tmp_path):
    async def scenario():
        async def unused_handler(submission, cancel_event, progress_callback):
            raise AssertionError("scientific handler must not run")

        store = TaskStore(tmp_path / "tasks.sqlite")
        local = LocalTaskBackend(store, {"docking": unused_handler})
        config = TaskRuntimeConfig(
            backend="temporal_canary",
            canary_percent=100,
            temporal_address="127.0.0.1:7233",
            temporal_namespace="default",
            docking_queue="medchat-docking",
            docking_concurrency=1,
            staging_root=(tmp_path / "staging").resolve(),
        )

        runtime = TaskRuntime(
            config=config,
            store=store,
            stager=object(),
            local_backend=local,
        )
        assert isinstance(runtime.temporal_backend, TemporalTaskBackend)
        await runtime.close()

    asyncio.run(scenario())


def test_runtime_get_delegates_temporal_projection_repair(tmp_path):
    class TrackingTemporalBackend:
        def __init__(self, store):
            self.store = store
            self.get_calls = []

        async def submit(self, submission):
            raise AssertionError("submit not expected")

        async def get(self, task_id):
            self.get_calls.append(task_id)
            return self.store.get(task_id)

        async def list(self, limit=20, status=None, task_type=None):
            return []

        async def cancel(self, task_id, reason=None):
            return self.store.get(task_id)

        async def health(self):
            raise AssertionError("health not expected")

    async def scenario():
        async def unused_handler(submission, cancel_event, progress_callback):
            raise AssertionError("scientific handler must not run")

        store = TaskStore(tmp_path / "tasks.sqlite")
        store.create(
            TASK_ID,
            "docking",
            {},
            backend="temporal",
            external_workflow_id=WORKFLOW_ID,
        )
        temporal = TrackingTemporalBackend(store)
        runtime = TaskRuntime(
            store=store,
            stager=object(),
            local_backend=LocalTaskBackend(store, {"docking": unused_handler}),
            temporal_backend=temporal,
        )

        record = await runtime.get(TASK_ID)

        assert record.backend == "temporal"
        assert temporal.get_calls == [TASK_ID]
        await runtime.close()

    asyncio.run(scenario())


def test_temporal_health_requires_live_worker_heartbeat(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        backend = _backend(tmp_path, FakeClient(FakeHandle()), store=store)
        missing = await backend.health()
        assert missing.available is False

        store.record_worker_heartbeat(
            "worker-1",
            backend="temporal",
            task_queue="medchat-docking",
            concurrency=1,
            sdk_version="1.30.0",
        )
        healthy = await backend.health()
        assert healthy.available is True
        assert healthy.details["durable_execution"] is True
        assert healthy.details["process_restart_recovery"] is True

    asyncio.run(scenario())


def test_temporal_health_checks_current_service_connection(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        client = FakeClient(
            FakeHandle(),
            health_error=RuntimeError("connection unavailable"),
        )
        backend = _backend(tmp_path, client, store=store)
        store.record_worker_heartbeat(
            "worker-1",
            backend="temporal",
            task_queue="medchat-docking",
            concurrency=1,
            sdk_version="1.30.0",
        )

        health = await backend.health()

        assert health.available is False
        assert client.service_client.health_calls == 1

    asyncio.run(scenario())


def test_temporal_health_fails_closed_on_false_or_missing_service_check(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        store.record_worker_heartbeat(
            "worker-1",
            backend="temporal",
            task_queue="medchat-docking",
            concurrency=1,
            sdk_version="1.30.0",
        )
        false_backend = _backend(
            tmp_path,
            FakeClient(FakeHandle(), health_result=False),
            store=store,
        )
        missing_client = FakeClient(FakeHandle())
        del missing_client.service_client
        missing_backend = _backend(tmp_path, missing_client, store=store)

        assert (await false_backend.health()).available is False
        assert (await missing_backend.health()).available is False

    asyncio.run(scenario())


def test_real_temporal_backend_start_duplicate_and_projection_repair(tmp_path):
    @activity.defn(name=VERIFY_MANIFEST_ACTIVITY_NAME)
    async def verify_manifest(payload):
        return {"verified": True}

    @activity.defn(name=DOCKING_ACTIVITY_NAME)
    async def run_docking(payload):
        return {
            "task_id": payload["task_id"],
            "attempt": 1,
            "status": "succeeded",
            "result": {"pose_count": 1, "best_energy": -7.0},
            "artifacts": [],
            "warnings": [],
            "provenance": {"tool": "molecular_docking"},
        }

    @activity.defn(name=PROJECTION_ACTIVITY_NAME)
    async def project(payload):
        return {"applied": True, "operation": payload["operation"]}

    async def scenario():
        environment = await WorkflowEnvironment.start_time_skipping()
        async with environment:
            async def client_factory(address, namespace):
                return environment.client

            store = TaskStore(tmp_path / "tasks.sqlite")
            backend = TemporalTaskBackend(
                store,
                address="127.0.0.1:7233",
                namespace="default",
                task_queue="real-temporal-backend",
                client_factory=client_factory,
            )
            worker = Worker(
                environment.client,
                task_queue="real-temporal-backend",
                workflows=[DockingWorkflow],
                activities=[verify_manifest, run_docking, project],
            )
            async with worker:
                submission = _submission(tmp_path)
                first = await backend.submit(submission)
                handle = environment.client.get_workflow_handle(WORKFLOW_ID)
                receipt = await handle.result()
                second = await backend.submit(submission)
                repaired = await backend.get(TASK_ID)

        assert first.outcome is StartOutcome.ACCEPTED
        assert second.outcome is StartOutcome.ACCEPTED
        assert receipt["status"] == TaskStatus.SUCCEEDED.value
        assert repaired.status is TaskStatus.SUCCEEDED
        assert repaired.result == {
            "best_energy": -7.0,
            "pose_count": 1,
            "warnings": [],
        }
        assert repaired.provenance == {"tool": "molecular_docking"}
        assert repaired.external_workflow_id == WORKFLOW_ID
        assert len(
            [event for event in store.events(TASK_ID) if event.is_terminal]
        ) == 1

    asyncio.run(scenario())


def test_runtime_falls_back_only_after_definitive_temporal_rejection(tmp_path):
    class FakeStager:
        def stage(self, task_id, *args, **kwargs):
            path = tmp_path / task_id / "input_manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

        def load_verified(self, task_id, manifest_path):
            return {
                "config_hash": "b" * 64,
                "ligand_mode": "file",
                "ligand": {"sha256": "c" * 64},
                "receptor": {"sha256": "d" * 64},
            }

        def cleanup_unprojected_expired(self, *args, **kwargs):
            return []

    class RecordingLocal:
        def __init__(self, store):
            self.store = store
            self.submit_count = 0

        async def submit(self, submission):
            self.submit_count += 1
            self.store.create(
                submission.task_id,
                submission.task_type,
                submission.payload,
                backend="local",
                input_manifest_path=submission.input_manifest_path,
                submission_digest=submission.request_digest,
            )
            return BackendSubmitResult(
                submission.task_id,
                "local",
                StartOutcome.ACCEPTED,
                "local task accepted",
            )

        async def get(self, task_id):
            return self.store.get(task_id)

        async def list(self, limit=20, status=None, task_type=None):
            return self.store.list(limit, status, task_type)

        async def cancel(self, task_id, reason=None):
            return self.store.request_cancel(task_id, reason=reason)

        async def health(self):
            return BackendHealth("local", True, "available", {"running": 0})

    class ControlledTemporal:
        def __init__(self, store):
            self.store = store
            self.outcome = StartOutcome.REJECTED

        async def submit(self, submission):
            if self.outcome is StartOutcome.ACCEPTED:
                self.store.create(
                    submission.task_id,
                    submission.task_type,
                    submission.payload,
                    backend="temporal",
                    external_workflow_id=(
                        f"medchat-docking-{submission.task_id}"
                    ),
                    input_manifest_path=submission.input_manifest_path,
                    submission_digest=submission.request_digest,
                )
            return BackendSubmitResult(
                submission.task_id,
                "temporal",
                self.outcome,
                "controlled Temporal outcome",
            )

        async def get(self, task_id):
            return self.store.get(task_id)

        async def list(self, limit=20, status=None, task_type=None):
            return []

        async def cancel(self, task_id, reason=None):
            return self.store.request_cancel(task_id, reason=reason)

        async def health(self):
            return BackendHealth(
                "temporal",
                True,
                "available",
                {"running": 0},
            )

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        local = RecordingLocal(store)
        temporal = ControlledTemporal(store)
        ids = iter(
            [
                "00000000-0000-4000-8000-000000000111",
                "00000000-0000-4000-8000-000000000112",
            ]
        )
        runtime = TaskRuntime(
            store=store,
            stager=FakeStager(),
            selector=type(
                "TemporalSelector",
                (),
                {
                    "select": lambda self, *args: BackendDecision(
                        backend="temporal",
                        reason="canary_selected",
                        bucket=1,
                        percent=100,
                    )
                },
            )(),
            local_backend=local,
            temporal_backend=temporal,
            uuid_factory=lambda: next(ids),
        )

        rejected = await runtime.submit_docking(
            receptor_name="receptor.pdb",
            receptor_bytes=b"ATOM\n",
            ligand_name="ligand.sdf",
            ligand_bytes=b"$$$$\n",
            smiles=None,
            config={},
        )
        assert rejected.backend == "local"
        assert local.submit_count == 1

        temporal.outcome = StartOutcome.ACCEPTED
        accepted = await runtime.submit_docking(
            receptor_name="receptor.pdb",
            receptor_bytes=b"ATOM\n",
            ligand_name="ligand.sdf",
            ligand_bytes=b"$$$$\n",
            smiles=None,
            config={},
        )
        assert accepted.backend == "temporal"
        assert local.submit_count == 1

    asyncio.run(scenario())


def test_runtime_accepts_idempotency_winner_and_discards_loser_staging(tmp_path):
    class FakeStager:
        def __init__(self):
            self.discarded = []

        def stage(self, task_id, *args, **kwargs):
            path = tmp_path / task_id / "input_manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

        def load_verified(self, task_id, manifest_path):
            return {
                "config_hash": "b" * 64,
                "ligand_mode": "file",
                "ligand": {"sha256": "c" * 64},
                "receptor": {"sha256": "d" * 64},
            }

        def discard_unprojected(self, task_id, manifest_path, projection_check):
            self.discarded.append(task_id)
            return True

        def cleanup_unprojected_expired(self, *args, **kwargs):
            return []

    class WinnerTemporal:
        def __init__(self, store):
            self.store = store

        async def submit(self, submission):
            winner_id = "00000000-0000-4000-8000-000000000120"
            self.store.create(
                winner_id,
                submission.task_type,
                submission.payload,
                backend="temporal",
                external_workflow_id=f"medchat-docking-{winner_id}",
                input_manifest_path=submission.input_manifest_path,
                provenance={"start_outcome": "accepted"},
                idempotency_digest=LocalTaskBackend.idempotency_digest(
                    submission.idempotency_key
                ),
                submission_digest=submission.request_digest,
            )
            return BackendSubmitResult(
                winner_id,
                "temporal",
                StartOutcome.ACCEPTED,
                "concurrent winner accepted",
            )

        async def get(self, task_id):
            return self.store.get(task_id)

        async def list(self, limit=20, status=None, task_type=None):
            return []

        async def cancel(self, task_id, reason=None):
            return self.store.request_cancel(task_id, reason=reason)

        async def health(self):
            return BackendHealth("temporal", True, "available", {"running": 0})

    async def unused_handler(submission, cancel_event, progress_callback):
        raise AssertionError("local scientific handler must not run")

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        stager = FakeStager()
        loser_id = "00000000-0000-4000-8000-000000000121"
        runtime = TaskRuntime(
            store=store,
            stager=stager,
            selector=type(
                "TemporalSelector",
                (),
                {
                    "select": lambda self, *args: BackendDecision(
                        backend="temporal",
                        reason="canary_selected",
                        bucket=1,
                        percent=100,
                    )
                },
            )(),
            local_backend=LocalTaskBackend(store, {"docking": unused_handler}),
            temporal_backend=WinnerTemporal(store),
            uuid_factory=lambda: loser_id,
        )

        result = await runtime.submit_docking(
            receptor_name="receptor.pdb",
            receptor_bytes=b"ATOM\n",
            ligand_name="ligand.sdf",
            ligand_bytes=b"$$$$\n",
            smiles=None,
            config={},
            idempotency_key="same-request",
        )

        assert result.task_id.endswith("120")
        assert result.outcome is StartOutcome.ACCEPTED
        assert stager.discarded == [loser_id]
        with pytest.raises(KeyError):
            store.get(loser_id)
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("selected_backend", "winner_backend"),
    [("local", "temporal"), ("temporal", "local")],
)
def test_runtime_reuses_cross_backend_idempotency_race_winner(
    tmp_path,
    selected_backend,
    winner_backend,
):
    class FakeStager:
        def __init__(self):
            self.discarded = []

        def stage(self, task_id, *args, **kwargs):
            path = tmp_path / task_id / "input_manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

        def load_verified(self, task_id, manifest_path):
            return {
                "config_hash": "b" * 64,
                "ligand_mode": "file",
                "ligand": {"sha256": "c" * 64},
                "receptor": {"sha256": "d" * 64},
            }

        def discard_unprojected(self, task_id, manifest_path, projection_check):
            self.discarded.append(task_id)
            return True

        def cleanup_unprojected_expired(self, *args, **kwargs):
            return []

    class RacingBackend:
        def __init__(self, store, backend, *, selected=False):
            self.store = store
            self.backend = backend
            self.selected = selected

        async def submit(self, submission):
            if not self.selected:
                raise AssertionError("unselected backend must not submit")
            winner_id = "00000000-0000-4000-8000-000000000130"
            self.store.create(
                winner_id,
                submission.task_type,
                submission.payload,
                backend=winner_backend,
                external_workflow_id=(
                    f"medchat-docking-{winner_id}"
                    if winner_backend == "temporal"
                    else None
                ),
                provenance=(
                    {"start_outcome": "accepted"}
                    if winner_backend == "temporal"
                    else {}
                ),
                idempotency_digest=LocalTaskBackend.idempotency_digest(
                    submission.idempotency_key
                ),
                submission_digest=submission.request_digest,
            )
            raise TaskIdempotencyConflictError("idempotency authority conflict")

        async def get(self, task_id):
            return self.store.get(task_id)

        async def list(self, limit=20, status=None, task_type=None):
            return []

        async def cancel(self, task_id, reason=None):
            return self.store.request_cancel(task_id, reason=reason)

        async def health(self):
            return BackendHealth(self.backend, True, "available", {"running": 0})

    async def scenario():
        store = TaskStore(tmp_path / "tasks.sqlite")
        stager = FakeStager()
        loser_id = "00000000-0000-4000-8000-000000000131"
        local = RacingBackend(
            store,
            "local",
            selected=selected_backend == "local",
        )
        temporal = RacingBackend(
            store,
            "temporal",
            selected=selected_backend == "temporal",
        )
        runtime = TaskRuntime(
            store=store,
            stager=stager,
            selector=type(
                "FixedSelector",
                (),
                {
                    "select": lambda self, *args: BackendDecision(
                        backend=selected_backend,
                        reason=(
                            "canary_selected"
                            if selected_backend == "temporal"
                            else "canary_not_selected"
                        ),
                        bucket=1,
                        percent=100,
                    )
                },
            )(),
            local_backend=local,
            temporal_backend=temporal,
            uuid_factory=lambda: loser_id,
        )

        result = await runtime.submit_docking(
            receptor_name="receptor.pdb",
            receptor_bytes=b"ATOM\n",
            ligand_name="ligand.sdf",
            ligand_bytes=b"$$$$\n",
            smiles=None,
            config={},
            idempotency_key="same-request",
        )

        assert result.task_id.endswith("130")
        assert result.backend == winner_backend
        assert result.outcome is StartOutcome.ACCEPTED
        assert stager.discarded == [loser_id]
        with pytest.raises(KeyError):
            store.get(loser_id)
        await runtime.close()

    asyncio.run(scenario())


def test_idempotency_race_recovery_cancellation_cleans_loser_staging(tmp_path):
    async def scenario():
        runtime = object.__new__(TaskRuntime)
        lookup_started = asyncio.Event()
        release_lookup = asyncio.Event()
        cleaned = []

        async def blocked_lookup(*args, **kwargs):
            lookup_started.set()
            await release_lookup.wait()

        async def cleanup(task_id, manifest_path):
            cleaned.append((task_id, manifest_path))

        async def ordinary_cleanup(task_id, manifest_path):
            raise AssertionError("cancellation-safe cleanup must be used")

        runtime._global_idempotency_result = blocked_lookup
        runtime._discard_staging_after_cancellation = cleanup
        runtime._discard_staging = ordinary_cleanup
        submission = _submission(tmp_path)
        submission = TaskSubmission(
            **{**submission.__dict__, "idempotency_key": "same-request"}
        )
        manifest_path = tmp_path / TASK_ID / "input_manifest.json"

        operation = asyncio.create_task(
            runtime._recover_idempotency_race(
                submission,
                TASK_ID,
                manifest_path,
            )
        )
        await lookup_started.wait()
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation

        assert cleaned == [(TASK_ID, manifest_path)]

    asyncio.run(scenario())
