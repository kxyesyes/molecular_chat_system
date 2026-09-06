from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.task_runtime.backends.base import BackendSubmitResult, StartOutcome
from src.task_runtime.models import BackendHealth, TaskEvent, TaskRecord, TaskStatus
from src.task_runtime.routes import setup_task_routes
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.selector import BackendDecision
from src.task_runtime.staging import DockingInputStager
from src.task_runtime.store import TaskStore
from src.web.routes.api_routes import setup_api_routes


TASK_ID = "00000000-0000-4000-8000-000000000901"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeTaskRuntime:
    def __init__(self) -> None:
        self.submissions = []
        self.cancel_reasons = []
        self.record = TaskRecord(
            task_id=TASK_ID,
            task_type="docking",
            status=TaskStatus.QUEUED,
            input={},
            backend="temporal",
            phase="staging",
            warnings=[],
            provenance={"start_outcome": "accepted"},
            artifacts=[],
        )

    async def submit_docking(self, **request):
        self.submissions.append(request)
        return BackendSubmitResult(
            TASK_ID,
            "temporal",
            StartOutcome.ACCEPTED,
            "Temporal workflow accepted",
        )

    async def get(self, task_id):
        if task_id != TASK_ID:
            raise KeyError(task_id)
        return self.record

    async def list(self, limit=20, status=None, task_type=None):
        return [self.record]

    async def events(self, task_id):
        if task_id != TASK_ID:
            raise KeyError(task_id)
        return [
            TaskEvent("event-1", TASK_ID, 1, "task_created", {}, False, "t1"),
            TaskEvent(
                "event-2",
                TASK_ID,
                2,
                "task_started",
                {"phase": "running"},
                False,
                "t2",
            ),
        ]

    async def cancel(self, task_id, reason=None):
        if task_id != TASK_ID:
            raise KeyError(task_id)
        self.cancel_reasons.append(reason)
        return TaskRecord(
            **{
                **self.record.__dict__,
                "status": TaskStatus.CANCEL_REQUESTED,
            }
        )


class FakeDockingService:
    async def perform_docking(self, **kwargs):
        return {
            "success": True,
            "job_id": "sync-job",
            "total_poses": 1,
            "best_pose": {"binding_energy": -7.0},
            "warnings": [],
        }


def _build_client(monkeypatch):
    runtime = FakeTaskRuntime()
    app = FastAPI()
    setup_api_routes(
        app,
        docking_service=FakeDockingService(),
        task_runtime=runtime,
    )
    setup_task_routes(app, task_runtime=runtime)
    return TestClient(app), runtime


def _headers(**extra):
    return extra


def _files():
    return {
        "protein_file": ("MAGL.pdb", b"ATOM\n", "chemical/x-pdb"),
        "ligand_file": ("ligand.sdf", b"$$$$\n", "chemical/x-mdl-sdfile"),
    }


def _docking_form():
    return {
        "center_x": "5.99",
        "center_y": "3.01",
        "center_z": "17.345",
        "size_x": "20",
        "size_y": "20",
        "size_z": "20",
        "manual_center": "true",
    }


def test_async_submit_needs_no_admin_token_and_returns_202(monkeypatch):
    client, runtime = _build_client(monkeypatch)

    accepted = client.post(
        "/api/docking/tasks",
        headers=_headers(**{"Idempotency-Key": "request-1"}),
        files=_files(),
        data=_docking_form(),
    )

    assert accepted.status_code == 202
    payload = accepted.json()
    assert payload["success"] is True
    assert payload["data"]["task_id"] == TASK_ID
    assert payload["data"]["start_outcome"] == "accepted"
    assert runtime.submissions[0]["receptor_name"] == "MAGL.pdb"
    assert runtime.submissions[0]["receptor_bytes"] == b"ATOM\n"
    assert runtime.submissions[0]["ligand_name"] == "ligand.sdf"
    assert runtime.submissions[0]["ligand_bytes"] == b"$$$$\n"
    assert runtime.submissions[0]["idempotency_key"] == "request-1"
    assert runtime.submissions[0]["config"] == {
        "center": [5.99, 3.01, 17.345],
        "size": [20.0, 20.0, 20.0],
        "exhaustiveness": 8,
        "num_modes": 10,
    }


def test_async_submit_rejects_docking_options_not_in_durable_contract(monkeypatch):
    client, runtime = _build_client(monkeypatch)

    automatic_center = client.post(
        "/api/docking/tasks",
        headers=_headers(),
        files=_files(),
        data={**_docking_form(), "manual_center": "false"},
    )
    custom_energy_range = client.post(
        "/api/docking/tasks",
        headers=_headers(),
        files=_files(),
        data={**_docking_form(), "energy_range": "5"},
    )

    assert automatic_center.status_code == 422
    assert custom_energy_range.status_code == 422
    assert runtime.submissions == []


def test_async_submit_forwards_smiles_without_ligand_file(monkeypatch):
    client, runtime = _build_client(monkeypatch)

    response = client.post(
        "/api/docking/tasks",
        headers=_headers(),
        files={"protein_file": ("protein.pdb", b"ATOM\n")},
        data={**_docking_form(), "smiles": "CCO"},
    )

    assert response.status_code == 202, response.text
    assert runtime.submissions[0]["smiles"] == "CCO"
    assert runtime.submissions[0]["ligand_bytes"] is None


def test_repeated_idempotent_submit_returns_same_task(monkeypatch):
    client, runtime = _build_client(monkeypatch)

    responses = [
        client.post(
            "/api/docking/tasks",
            headers=_headers(**{"Idempotency-Key": "stable-request"}),
            files=_files(),
            data=_docking_form(),
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [202, 202]
    assert [response.json()["data"]["task_id"] for response in responses] == [
        TASK_ID,
        TASK_ID,
    ]
    assert [item["idempotency_key"] for item in runtime.submissions] == [
        "stable-request",
        "stable-request",
    ]


def test_task_events_are_ordered_and_missing_task_is_404(monkeypatch):
    client, _ = _build_client(monkeypatch)

    response = client.get(f"/api/tasks/{TASK_ID}/events", headers=_headers())
    missing = client.get("/api/tasks/missing/events", headers=_headers())

    assert response.status_code == 200
    events = response.json()["data"]
    assert [event["sequence"] for event in events] == [1, 2]
    assert [event["event_type"] for event in events] == [
        "task_created",
        "task_started",
    ]
    assert missing.status_code == 404
    assert missing.json()["code"] == "TASK_NOT_FOUND"


def test_cancel_reason_is_bounded_and_redacted(monkeypatch):
    client, runtime = _build_client(monkeypatch)
    unsafe_reason = (
        "C:/private/input.sdf sk-fakecredential123 CCO " + ("x" * 1000)
    )

    response = client.post(
        f"/api/tasks/{TASK_ID}/cancel",
        headers=_headers(),
        json={"reason": unsafe_reason},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancel_requested"
    reason = runtime.cancel_reasons[0]
    assert len(reason) <= 256
    assert "sk-fakecredential123" not in reason
    assert "C:/private" not in reason
    assert "CCO" not in reason


def test_cancel_missing_task_is_404(monkeypatch):
    client, _ = _build_client(monkeypatch)

    response = client.post(
        "/api/tasks/missing/cancel",
        headers=_headers(),
        json={"reason": "user request"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_cancel_terminal_task_returns_existing_record_without_new_cancel(monkeypatch):
    client, runtime = _build_client(monkeypatch)
    runtime.record = TaskRecord(
        **{**runtime.record.__dict__, "status": TaskStatus.SUCCEEDED}
    )

    response = client.post(
        f"/api/tasks/{TASK_ID}/cancel",
        headers=_headers(),
        json={"reason": "too late"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "succeeded"
    assert runtime.cancel_reasons == []


def test_synchronous_docking_contract_is_unchanged(monkeypatch):
    client, runtime = _build_client(monkeypatch)

    response = client.post(
        "/api/docking/submit",
        files={"protein_file": ("protein.pdb", b"ATOM\n")},
        data={"smiles": "CCO"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "job_id": "sync-job",
        "total_poses": 1,
        "best_pose": {"binding_energy": -7.0},
        "warnings": [],
    }
    assert runtime.submissions == []


class _LocalSelector:
    def select(self, task_type, task_id, temporal_available):
        return BackendDecision("local", "temporal_unavailable", None, 0)


class _RecordingLocalBackend:
    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self.submissions = []

    async def submit(self, submission):
        self.submissions.append(submission)
        self.store.create(
            submission.task_id,
            submission.task_type,
            submission.payload,
            backend="local",
            provenance=submission.payload["decision"],
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
        return self.store.list(limit=limit, status=status, task_type=task_type)

    async def cancel(self, task_id, reason=None):
        return self.store.request_cancel(task_id, reason=reason)

    async def health(self):
        return BackendHealth("local", True, "available", {"running": 0})

    async def close(self):
        return None


def test_async_submit_is_accepted_by_real_docking_stager(tmp_path: Path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.sqlite")
    stager = DockingInputStager(tmp_path / "staging")
    backend = _RecordingLocalBackend(store)
    runtime = TaskRuntime(
        config=SimpleNamespace(
            backend="local",
            canary_percent=0,
            staging_root=tmp_path / "staging",
        ),
        store=store,
        stager=stager,
        selector=_LocalSelector(),
        local_backend=backend,
        temporal_backend=None,
    )
    app = FastAPI()
    setup_api_routes(app, task_runtime=runtime)

    with TestClient(app) as client:
        response = client.post(
            "/api/docking/tasks",
            headers=_headers(),
            files=_files(),
            data=_docking_form(),
        )

    assert response.status_code == 202, response.text
    submission = backend.submissions[0]
    manifest = stager.load_verified(
        submission.task_id,
        submission.input_manifest_path,
    )
    assert manifest["config"] == {
        "center": [5.99, 3.01, 17.345],
        "size": [20.0, 20.0, 20.0],
        "exhaustiveness": 8,
        "num_modes": 10,
    }


def test_runtime_binding_owns_asgi_lifecycle_and_redacts_start_failure():
    from src.task_runtime.runtime import TaskRuntimeBinding

    warnings = []

    class _Logger:
        def warning(self, message):
            warnings.append(message)

    def broken_factory():
        raise RuntimeError("C:/private/runtime.sqlite sk-runtime-secret")

    app = FastAPI()
    async def shutdown(_runtime):
        return None

    binding = TaskRuntimeBinding(factory=broken_factory, shutdown=shutdown)
    binding.install(app, _Logger())

    with TestClient(app):
        pass

    assert warnings == ["Durable task runtime initialization failed"]


@pytest.mark.anyio
async def test_runtime_binding_recreates_singleton_after_shutdown():
    from src.task_runtime import runtime as runtime_module

    binding_type = getattr(runtime_module, "TaskRuntimeBinding", None)
    assert binding_type is not None, "TaskRuntimeBinding must own ASGI lifecycle"

    instances = [object(), object()]
    created = []
    closed = []

    def factory():
        value = instances[len(created)]
        created.append(value)
        return value

    async def shutdown(runtime):
        closed.append(runtime)

    binding = binding_type(factory=factory, shutdown=shutdown)
    assert binding() is instances[0]
    assert binding() is instances[0]

    await binding.close()

    assert closed == [instances[0]]
    assert binding() is instances[1]


@pytest.mark.anyio
async def test_runtime_binding_never_reuses_runtime_after_failed_shutdown():
    from src.task_runtime.runtime import TaskRuntimeBinding

    first = object()
    second = object()
    created = []
    shutdown_attempts = []

    def factory():
        runtime = first if not created else second
        created.append(runtime)
        return runtime

    async def shutdown(runtime):
        shutdown_attempts.append(runtime)
        if len(shutdown_attempts) == 1:
            raise RuntimeError("partial shutdown")

    binding = TaskRuntimeBinding(factory=factory, shutdown=shutdown)
    assert binding() is first

    with pytest.raises(RuntimeError, match="partial shutdown"):
        await binding.close()
    with pytest.raises(RuntimeError, match="unavailable"):
        binding()

    await binding.close()

    assert binding() is second
    assert shutdown_attempts == [first, first]


@pytest.mark.anyio
async def test_runtime_bindings_share_a_lease_without_stale_close():
    from src.task_runtime import runtime as runtime_module

    runtime = object()
    owners = 0
    closed = []

    def acquire():
        nonlocal owners
        owners += 1
        return runtime

    async def release(expected):
        nonlocal owners
        assert expected is runtime
        owners -= 1
        if owners == 0:
            closed.append(expected)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runtime_module, "acquire_task_runtime", acquire, raising=False)
    monkeypatch.setattr(runtime_module, "release_task_runtime", release, raising=False)
    try:
        first_binding = runtime_module.TaskRuntimeBinding()
        second_binding = runtime_module.TaskRuntimeBinding()
        assert first_binding() is runtime
        assert second_binding() is runtime

        await first_binding.close()
        assert closed == []
        assert first_binding() is runtime

        await second_binding.close()
        assert closed == []
        assert first_binding() is runtime

        await first_binding.close()
        assert closed == [runtime]
    finally:
        monkeypatch.undo()


@pytest.mark.anyio
async def test_failed_global_release_blocks_new_lease_until_retry(monkeypatch):
    from src.task_runtime import runtime as runtime_module

    created = []

    class _Runtime:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1
            if self is created[0] and self.close_calls == 1:
                raise RuntimeError("partial close")

    def factory():
        runtime = _Runtime()
        created.append(runtime)
        return runtime

    monkeypatch.setattr(runtime_module, "TaskRuntime", factory)
    monkeypatch.setattr(runtime_module, "_RUNTIME", None)
    monkeypatch.setattr(runtime_module, "_RUNTIME_BINDING_COUNT", 0)
    monkeypatch.setattr(runtime_module, "_RUNTIME_RESET_FUTURE", None)
    monkeypatch.setattr(runtime_module, "_RUNTIME_RESET_RUNNER", None)
    monkeypatch.setattr(runtime_module, "_RUNTIME_POISONED", False, raising=False)

    first = runtime_module.acquire_task_runtime()
    with pytest.raises(RuntimeError, match="partial close"):
        await runtime_module.release_task_runtime(first)

    with pytest.raises(RuntimeError, match="unavailable"):
        runtime_module.acquire_task_runtime()

    await runtime_module.release_task_runtime(first)
    replacement = runtime_module.acquire_task_runtime()

    assert replacement is not first
    await runtime_module.release_task_runtime(replacement)
