"""Contract tests for the standalone sandbox broker HTTP API."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import io
import json
import logging
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData, UploadFile
from starlette.requests import ClientDisconnect

from src.sandbox_broker.artifacts import ArtifactRegistry
from src.sandbox_broker.config import BrokerConfig
from src.sandbox_broker.models import (
    BrokerErrorCode,
    BrokerJobStatus,
    BrokerProvenance,
    DockingManifest,
    DockingParameters,
)
from src.sandbox_broker.opensandbox_client import SandboxHandle
from src.sandbox_broker.service import (
    BrokerFailure,
    BrokerJobView,
    SandboxBrokerService,
    StopIncomplete,
)
from src.sandbox_broker.store import ArtifactRecord, BrokerStore


def test_broker_entrypoint_imports_from_an_isolated_working_directory(
    tmp_path: Path,
) -> None:
    entrypoint = Path(__file__).resolve().parents[2] / "scripts" / "run_sandbox_broker.py"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import runpy,sys; "
                "runpy.run_path(sys.argv[1], run_name='broker_entrypoint_import_test')"
            ),
            str(entrypoint),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr


class _HoldingSandboxClient:
    def __init__(self) -> None:
        self.create_count = 0
        self.run_count = 0
        self.running = asyncio.Event()
        self.calls = {
            operation: 0
            for operation in (
                "create",
                "upload_text",
                "read_text",
                "list_files",
                "run",
                "destroy",
                "destroy_by_id",
                "destroy_by_job_id",
                "drain_cleanup",
            )
        }

    async def create(self, job_id: str) -> SandboxHandle:
        self.calls["create"] += 1
        self.create_count += 1
        return SandboxHandle(f"sandbox-{job_id}", object())

    async def upload_text(self, handle: SandboxHandle, path: str, data: str) -> None:
        self.calls["upload_text"] += 1
        del handle, path, data

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        self.calls["read_text"] += 1
        del handle, path
        return ""

    async def list_files(
        self,
        handle: SandboxHandle,
        root: str,
        pattern: str,
    ) -> list[str]:
        self.calls["list_files"] += 1
        del handle, root, pattern
        return []

    async def run(self, handle: SandboxHandle) -> object:
        del handle
        self.calls["run"] += 1
        self.run_count += 1
        self.running.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def destroy(self, handle: SandboxHandle) -> None:
        self.calls["destroy"] += 1
        del handle

    async def destroy_by_id(self, sandbox_id: str) -> None:
        self.calls["destroy_by_id"] += 1
        del sandbox_id

    async def destroy_by_job_id(self, job_id: str) -> int:
        self.calls["destroy_by_job_id"] += 1
        del job_id
        return 0

    async def drain_cleanup(self) -> None:
        self.calls["drain_cleanup"] += 1
        return None


class _GeneratedFile:
    def __init__(self, size: int, *, fail_after: int | None = None) -> None:
        self.remaining = size
        self.fail_after = fail_after
        self.total_read = 0
        self.largest_request = 0

    def read(self, size: int = -1) -> bytes:
        self.largest_request = max(self.largest_request, size)
        if self.fail_after is not None and self.total_read >= self.fail_after:
            raise OSError("sensitive upload failure")
        if self.remaining <= 0:
            return b""
        amount = min(size, self.remaining)
        self.remaining -= amount
        self.total_read += amount
        return b"x" * amount


class _BlockingFile:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.reads = 0

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        self.entered.set()
        self.release.wait(timeout=5)
        return b"x" * min(size, 1024)


class _ApiService:
    job_id = "1" * 32
    artifact_id = "2" * 32
    diagnostics_payload = {
        "schema_version": 1,
        "telemetry": {
            "schema_version": 1,
            "counters": {"job_terminal|passed|none": 1},
            "phase_latency_ms": {},
            "runtime": {
                "queue_depth": 0,
                "active_job_count": 0,
            },
            "recent_events": [
                {
                    "trace_id": "bounded-diagnostic-trace",
                    "job_id": "bounded-diagnostic-job",
                    "phase": "job_terminal",
                    "outcome": "passed",
                }
            ],
        },
        "circuit_breaker": {"state": "closed"},
    }
    prometheus_payload = (
        b"# HELP medchat_sandbox_broker_queue_depth Current queue depth.\n"
        b"# TYPE medchat_sandbox_broker_queue_depth gauge\n"
        b"medchat_sandbox_broker_queue_depth 0.0\n"
    )

    def __init__(self, *, stop_failure: BaseException | None = None) -> None:
        self.events: list[str] = []
        self.stop_failure = stop_failure
        self.submitted_keys: list[str] = []
        self.diagnostics_calls = 0
        self.prometheus_calls = 0

    async def recover(self) -> None:
        self.events.append("recover")

    async def start(self) -> None:
        self.events.append("start")

    async def stop(self, *, timeout: float | None = None) -> None:
        assert timeout is not None and timeout > 0
        self.events.append("stop")
        if self.stop_failure is not None:
            raise self.stop_failure

    def _view(self, status: BrokerJobStatus = BrokerJobStatus.QUEUED) -> BrokerJobView:
        return BrokerJobView(
            job_id=self.job_id,
            trace_id="trace-public",
            status=status,
            phase=status.value,
            sandbox_id="sandbox-private",
            error_code=None,
            warnings=(),
            provenance=None,
            cancel_requested=False,
            cleanup_status="not_started",
            created_at=1.0,
            updated_at=1.0,
        )

    async def submit_uploads(
        self,
        parameters: DockingParameters,
        receptor: object,
        ligand: object,
        idempotency_key: str,
    ) -> BrokerJobView:
        del parameters, receptor, ligand
        self.submitted_keys.append(idempotency_key)
        if idempotency_key == "conflict":
            raise BrokerFailure(BrokerErrorCode.IDEMPOTENCY_CONFLICT)
        if idempotency_key == "queue-full":
            raise BrokerFailure(BrokerErrorCode.QUEUE_SATURATED)
        if idempotency_key == "dependency-down":
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE)
        if idempotency_key == "unexpected-secret":
            raise RuntimeError(r"OPEN_SANDBOX_API_KEY=secret C:\private\state")
        return self._view()

    def get_job(self, job_id: str) -> BrokerJobView:
        if job_id != self.job_id:
            raise KeyError(r"missing C:\private\state")
        return self._view()

    def get_manifest(self, job_id: str) -> DockingManifest:
        if job_id != self.job_id:
            raise KeyError("manifest not found")
        provenance = BrokerProvenance(
            sandbox_id="sandbox-public-provenance",
            image_uri="medchat-docking",
            image_digest="a" * 64,
            secure_runtime="gvisor",
            vina_version="1.2.5",
            meeko_version="0.6.1",
            receptor_sha256="b" * 64,
            ligand_sha256="c" * 64,
            cleanup_status="succeeded",
            demo_mode=False,
            fallback_used=False,
        )
        return DockingManifest(
            job_id=self.job_id,
            trace_id="trace-public",
            pose_count=1,
            best_energy=-7.4,
            artifacts=[
                {
                    "artifact_id": self.artifact_id,
                    "media_type": "chemical/x-pdbqt",
                    "sha256": "d" * 64,
                    "size_bytes": 5,
                }
            ],
            warnings=[],
            provenance=provenance,
        )

    def open_artifact(
        self, job_id: str, artifact_id: str
    ) -> tuple[ArtifactRecord, object]:
        if job_id != self.job_id or artifact_id != self.artifact_id:
            raise KeyError("artifact not found")
        record = ArtifactRecord(
            artifact_id=self.artifact_id,
            job_id=self.job_id,
            relative_path=f"jobs/{self.job_id}/published/{self.artifact_id}",
            media_type="chemical/x-pdbqt",
            size_bytes=5,
            sha256="d" * 64,
        )
        return record, iter([b"POSE\n"])

    async def cancel(self, job_id: str) -> BrokerJobView:
        if job_id != self.job_id:
            raise KeyError("job not found")
        return self._view(BrokerJobStatus.CANCELLED)

    def diagnostics(self) -> dict[str, object]:
        self.diagnostics_calls += 1
        return self.diagnostics_payload

    def prometheus_text(self) -> bytes:
        self.prometheus_calls += 1
        return self.prometheus_payload


def _config(tmp_path: Path) -> BrokerConfig:
    state_root = tmp_path / "state"
    state_root.mkdir()
    return BrokerConfig(
        state_root=state_root,
        socket_path=tmp_path / "broker.sock",
        image_uri="medchat-docking",
        image_digest="a" * 64,
        opensandbox_domain="127.0.0.1:8080",
        opensandbox_api_key="runtime-secret-value",
    )


def _service(
    tmp_path: Path,
) -> tuple[SandboxBrokerService, BrokerStore, BrokerConfig, _HoldingSandboxClient]:
    config = _config(tmp_path)
    store = BrokerStore(config.state_root / "broker.sqlite")
    client = _HoldingSandboxClient()
    service = SandboxBrokerService(
        config,
        store,
        client,
        ArtifactRegistry(config.state_root, store),
    )
    return service, store, config, client


def _parameters(**updates: object) -> DockingParameters:
    values: dict[str, object] = {"center": [1, 2, 3], "size": [20, 20, 20]}
    values.update(updates)
    return DockingParameters.model_validate(values)


def _upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(content), filename=filename)


def _open_fd_count() -> int | None:
    descriptor_root = Path("/proc/self/fd")
    if os.name != "posix" or not descriptor_root.is_dir():
        return None
    return len(list(descriptor_root.iterdir()))


def _successful_service(
    tmp_path: Path,
    pose: bytes,
    *,
    key: str = "artifact-job",
) -> tuple[SandboxBrokerService, BrokerStore, ArtifactRegistry, object, object]:
    config = _config(tmp_path)
    store = BrokerStore(config.state_root / "broker.sqlite")
    registry = ArtifactRegistry(config.state_root, store)
    job, _ = store.create_or_get(key, "b" * 64, "trace-artifact")
    store.transition(job.job_id, BrokerJobStatus.PROVISIONING)
    store.transition(job.job_id, BrokerJobStatus.UPLOADING)
    store.transition(job.job_id, BrokerJobStatus.RUNNING)
    store.transition(job.job_id, BrokerJobStatus.VALIDATING)
    pose_record = registry.publish(
        job.job_id,
        "pose-1",
        pose,
        "chemical/x-pdbqt",
    )
    provenance = BrokerProvenance(
        sandbox_id="sandbox-artifact",
        image_uri=config.image_uri,
        image_digest=config.image_digest,
        secure_runtime="gvisor",
        vina_version="1.2.5",
        meeko_version="0.6.1",
        receptor_sha256="c" * 64,
        ligand_sha256="d" * 64,
        cleanup_status="succeeded",
        demo_mode=False,
        fallback_used=False,
    )
    manifest = DockingManifest(
        job_id=job.job_id,
        trace_id=job.trace_id,
        pose_count=1,
        best_energy=-7.4,
        artifacts=[
            {
                "artifact_id": pose_record.artifact_id,
                "media_type": pose_record.media_type,
                "sha256": pose_record.sha256,
                "size_bytes": pose_record.size_bytes,
            }
        ],
        warnings=[],
        provenance=provenance,
    )
    manifest_record = registry.publish(
        job.job_id,
        "manifest",
        json.dumps(manifest.model_dump(mode="json"), sort_keys=True).encode("ascii"),
        "application/json",
    )
    store.complete(
        job.job_id,
        warnings=(),
        provenance=provenance.model_dump(mode="json"),
    )
    service = SandboxBrokerService(config, store, object(), registry)
    return service, store, registry, pose_record, manifest_record


def test_broker_app_factory_is_importable() -> None:
    from src.sandbox_broker.app import create_app

    assert callable(create_app)


def test_submit_uploads_reuses_exact_request_and_conflicts_on_change(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, object, BrokerFailure, SandboxBrokerService]:
        service, _, _, _ = _service(tmp_path)
        await service.start()
        first = await service.submit_uploads(
            _parameters(),
            _upload("target.PDB", b"ATOM\n"),
            _upload("compound.SDF", b"ligand\n"),
            "task-0001",
        )
        reused = await service.submit_uploads(
            _parameters(),
            _upload("renamed.pdb", b"ATOM\n"),
            _upload("renamed.sdf", b"ligand\n"),
            "task-0001",
        )
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                _parameters(center=[0, 0, 0]),
                _upload("target.pdb", b"ATOM\n"),
                _upload("compound.sdf", b"ligand\n"),
                "task-0001",
            )
        await service.stop()
        return first, reused, raised.value, service

    first, reused, conflict, service = asyncio.run(scenario())
    assert reused.job_id == first.job_id
    assert conflict.code is BrokerErrorCode.IDEMPOTENCY_CONFLICT
    assert (service.config.state_root / "jobs" / first.job_id / "input" / "receptor.pdb").read_bytes() == b"ATOM\n"
    assert (service.config.state_root / "jobs" / first.job_id / "input" / "ligand.sdf").read_bytes() == b"ligand\n"


@pytest.mark.parametrize(
    ("role", "filename"),
    [
        ("receptor", "../target.pdb"),
        ("receptor", "folder/target.pdb"),
        ("receptor", "folder\\target.pdb"),
        ("receptor", "target.pdb.exe"),
        ("receptor", "target..pdb"),
        ("receptor", "target.pdb\x00"),
        ("receptor", ".pdb"),
        ("receptor", "target.pdbqt"),
        ("ligand", "compound.xyz"),
        ("ligand", "compound.pdb"),
        ("ligand", "compound.pdbqt"),
        ("ligand", "compound.sdf.zip"),
    ],
)
def test_submit_uploads_rejects_untrusted_filename_without_creating_job(
    tmp_path: Path,
    role: str,
    filename: str,
) -> None:
    service, store, _, _ = _service(tmp_path)

    async def scenario() -> BrokerFailure:
        await service.start()
        receptor = _upload(filename if role == "receptor" else "target.pdb", b"ATOM\n")
        ligand = _upload(filename if role == "ligand" else "compound.sdf", b"ligand\n")
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                _parameters(), receptor, ligand, "filename-check"
            )
        await service.stop()
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.INVALID_INPUT
    assert store.active_jobs() == []


@pytest.mark.parametrize(
    "key",
    ["", " " * 3, "x" * 257, "line\nbreak", "tab\tkey", "nul\x00key", "snowman-\u2603"],
)
def test_submit_uploads_rejects_non_printable_or_non_ascii_idempotency_key_before_read(
    tmp_path: Path,
    key: str,
) -> None:
    service, store, _, _ = _service(tmp_path)
    generated = _GeneratedFile(16)

    async def scenario() -> BrokerFailure:
        await service.start()
        receptor = type("Upload", (), {"filename": "target.pdb", "file": generated})()
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                _parameters(), receptor, _upload("compound.sdf", b"ligand\n"), key
            )
        await service.stop()
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.INVALID_INPUT
    assert generated.total_read == 0
    assert store.active_jobs() == []


def test_submit_uploads_streams_in_one_mib_chunks_and_rejects_oversize(
    tmp_path: Path,
) -> None:
    service, store, config, _ = _service(tmp_path)
    generated = _GeneratedFile(config.ligand_max_bytes + 1)
    ligand = type("Upload", (), {"filename": "compound.sdf", "file": generated})()

    async def scenario() -> BrokerFailure:
        await service.start()
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                _parameters(), _upload("target.pdb", b"ATOM\n"), ligand, "large-upload"
            )
        await service.stop()
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.INVALID_INPUT
    assert generated.largest_request == 1024 * 1024
    assert generated.total_read <= config.ligand_max_bytes + 1
    assert store.active_jobs() == []


def test_submit_uploads_cleans_temporary_files_on_stream_error(tmp_path: Path) -> None:
    service, store, config, _ = _service(tmp_path)
    generated = _GeneratedFile(2 * 1024 * 1024, fail_after=1024 * 1024)
    ligand = type("Upload", (), {"filename": "compound.sdf", "file": generated})()

    async def scenario() -> BrokerFailure:
        await service.start()
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                _parameters(), _upload("target.pdb", b"ATOM\n"), ligand, "stream-error"
            )
        await service.stop()
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.INVALID_INPUT
    assert store.active_jobs() == []
    assert not list(config.state_root.glob("*.tmp"))


def test_submit_uploads_cancellation_waits_for_copy_and_cleans_temporary(
    tmp_path: Path,
) -> None:
    service, store, config, _ = _service(tmp_path)
    blocking = _BlockingFile()
    ligand = type("Upload", (), {"filename": "compound.sdf", "file": blocking})()

    async def scenario() -> None:
        await service.start()
        submission = asyncio.create_task(
            service.submit_uploads(
                _parameters(),
                _upload("target.pdb", b"ATOM\n"),
                ligand,
                "cancel-upload",
            )
        )
        await asyncio.wait_for(asyncio.to_thread(blocking.entered.wait), timeout=2)
        submission.cancel()
        blocking.release.set()
        with pytest.raises(asyncio.CancelledError):
            await submission
        await service.stop()

    asyncio.run(scenario())
    assert store.active_jobs() == []
    assert not list(config.state_root.glob("*.tmp"))


def test_twenty_concurrent_same_submissions_create_and_run_one_job(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[list[object], _HoldingSandboxClient, BrokerStore]:
        service, store, _, client = _service(tmp_path)
        await service.start()
        jobs = await asyncio.gather(
            *(
                service.submit_uploads(
                    _parameters(),
                    _upload("target.pdb", b"ATOM\n"),
                    _upload("compound.sdf", b"ligand\n"),
                    "concurrent-key",
                )
                for _ in range(20)
            )
        )
        await asyncio.wait_for(client.running.wait(), timeout=2)
        await service.stop()
        return jobs, client, store

    jobs, client, store = asyncio.run(scenario())
    assert len({job.job_id for job in jobs}) == 1
    assert client.create_count == 1
    assert client.run_count == 1
    assert len(store.jobs_updated_before(time.time() + 1)) == 1


def test_staging_failure_marks_new_job_failed_and_never_queues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.sandbox_broker.service as service_module

    service, store, _, _ = _service(tmp_path)
    original = service_module.stage_input
    calls = 0

    def fail_second_stage(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("sensitive stage failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service_module, "stage_input", fail_second_stage)

    async def scenario() -> None:
        await service.start()
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                _parameters(),
                _upload("target.pdb", b"ATOM\n"),
                _upload("compound.sdf", b"ligand\n"),
                "stage-failure",
            )
        assert raised.value.code is BrokerErrorCode.INVALID_INPUT
        await service.stop()

    asyncio.run(scenario())
    jobs = store.jobs_updated_before(time.time() + 1)
    assert len(jobs) == 1
    assert jobs[0].status is BrokerJobStatus.FAILED
    assert jobs[0].error_code == BrokerErrorCode.INVALID_INPUT.value
    assert service._queue.qsize() == 0
    assert not (service.config.state_root / "jobs" / jobs[0].job_id).exists()


def test_artifact_registry_verifies_then_streams_large_file_in_bounded_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.sandbox_broker.artifacts as artifacts_module

    pose = b"x" * (3 * 1024 * 1024 + 17)
    _, _, registry, pose_record, _ = _successful_service(tmp_path, pose)
    monkeypatch.setattr(
        artifacts_module,
        "read_file_snapshot",
        lambda *args, **kwargs: pytest.fail("stream path buffered the full artifact"),
    )

    record, stream = registry.open_registered(
        pose_record.job_id,
        pose_record.artifact_id,
        100 * 1024 * 1024,
    )
    chunks = list(stream)

    assert record == pose_record
    assert b"".join(chunks) == pose
    assert chunks
    assert max(map(len, chunks)) == 1024 * 1024


def test_service_open_artifact_allows_only_succeeded_manifest_pose(
    tmp_path: Path,
) -> None:
    service, _, _, pose_record, manifest_record = _successful_service(
        tmp_path, b"REMARK VINA RESULT: -7.4 0 0\n"
    )

    record, stream = service.open_artifact(
        pose_record.job_id,
        pose_record.artifact_id,
    )
    assert record == pose_record
    assert b"".join(stream).startswith(b"REMARK VINA RESULT")
    with pytest.raises(KeyError, match="artifact not found"):
        service.open_artifact(manifest_record.job_id, manifest_record.artifact_id)


def test_open_artifact_rejects_cross_job_and_preheader_tamper(tmp_path: Path) -> None:
    service, _, _, pose_record, _ = _successful_service(tmp_path, b"POSE-A\n")
    second_root = tmp_path / "second"
    second_root.mkdir()
    second, _, _, _, _ = _successful_service(second_root, b"POSE-B\n", key="second")

    with pytest.raises(KeyError, match="artifact not found"):
        second.open_artifact(second.get_job(next(iter(second.store.jobs_updated_before(time.time() + 1))).job_id).job_id, pose_record.artifact_id)

    artifact_path = service.config.state_root.joinpath(*Path(pose_record.relative_path).parts)
    artifact_path.write_bytes(b"TAMPER\n")
    with pytest.raises(KeyError, match="artifact not found"):
        service.open_artifact(pose_record.job_id, pose_record.artifact_id)


def test_artifact_second_pass_detects_drift_and_closes_on_interruption(
    tmp_path: Path,
) -> None:
    pose = b"A" * (2 * 1024 * 1024)
    _, _, registry, pose_record, _ = _successful_service(tmp_path, pose)
    record, stream = registry.open_registered(
        pose_record.job_id,
        pose_record.artifact_id,
        100 * 1024 * 1024,
    )
    artifact_path = registry._state_root.joinpath(*Path(record.relative_path).parts)
    artifact_path.write_bytes(b"B" * len(pose))

    with pytest.raises(ValueError, match="artifact stream failed"):
        list(stream)
    stream.close()


def test_artifact_stream_close_before_first_chunk_releases_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.sandbox_broker.artifacts as artifacts_module

    _, _, registry, pose_record, _ = _successful_service(tmp_path, b"POSE\n")
    _, stream = registry.open_registered(
        pose_record.job_id,
        pose_record.artifact_id,
        100 * 1024 * 1024,
    )
    real_close = artifacts_module.os.close
    closed: list[int] = []

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(artifacts_module.os, "close", close)
    stream.close()
    assert len(closed) == 1


def _submit_api(
    client: TestClient,
    *,
    key: str = "api-key",
    parameters: dict[str, object] | None = None,
    extra_data: dict[str, str] | None = None,
    extra_files: dict[str, tuple[str, bytes, str]] | None = None,
) -> object:
    data = {
        "request_json": json.dumps(
            parameters or {"center": [1, 2, 3], "size": [20, 20, 20]}
        )
    }
    if extra_data:
        data.update(extra_data)
    files = {
        "receptor": ("target.pdb", b"ATOM\n", "chemical/x-pdb"),
        "ligand": ("compound.sdf", b"ligand\n", "chemical/x-mdl-sdfile"),
    }
    if extra_files:
        files.update(extra_files)
    return client.post(
        "/v1/docking/jobs",
        headers={"Idempotency-Key": key},
        data=data,
        files=files,
    )


def test_api_happy_path_public_fields_stream_headers_and_lifecycle(tmp_path: Path) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app) as client:
        submitted = _submit_api(client)
        assert submitted.status_code == 202
        payload = submitted.json()
        assert payload["job_id"] == service.job_id
        serialized = json.dumps(payload, sort_keys=True)
        for forbidden in (
            "idempotency",
            "canonical",
            "runtime-secret-value",
            "sandbox-private",
            str(tmp_path),
        ):
            assert forbidden not in serialized

        observed = client.get(f"/v1/docking/jobs/{service.job_id}")
        assert observed.status_code == 200
        manifest = client.get(f"/v1/docking/jobs/{service.job_id}/manifest")
        assert manifest.status_code == 200
        artifact = client.get(
            f"/v1/docking/jobs/{service.job_id}/artifacts/{service.artifact_id}",
            headers={"Range": "bytes=0-1"},
        )
        assert artifact.status_code == 200
        assert artifact.content == b"POSE\n"
        assert artifact.headers["content-length"] == "5"
        assert artifact.headers["content-disposition"] == (
            f'attachment; filename="{service.artifact_id}.pdbqt"'
        )
        assert artifact.headers["x-content-type-options"] == "nosniff"
        cancelled = client.post(f"/v1/docking/jobs/{service.job_id}/cancel")
        assert cancelled.status_code == 202
        assert client.get("/healthz").json() == {
            "status": "ok",
            "operation": "molecular_docking",
        }
    assert service.events == ["recover", "start", "stop"]


def test_private_diagnostics_and_metrics_are_exact_uds_service_views(
    tmp_path: Path,
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService()
    config = _config(tmp_path)
    app = create_app(config, service=service)
    supplied_trace = "caller-supplied-trace-must-not-reflect"
    supplied_job = "caller-supplied-job-must-not-reflect"
    supplied_secret = "caller-supplied-secret-must-not-reflect"
    with TestClient(app) as client:
        before = (list(service.events), list(service.submitted_keys))
        diagnostics = client.get(
            f"/v1/diagnostics?trace_id={supplied_trace}&job_id={supplied_job}"
            f"&secret={supplied_secret}",
            headers={
                "Authorization": f"Bearer {supplied_secret}",
                "X-Trace-ID": supplied_trace,
                "X-Job-ID": supplied_job,
            },
        )
        metrics = client.get(
            f"/metrics?trace_id={supplied_trace}&job_id={supplied_job}"
            f"&secret={supplied_secret}",
            headers={
                "Authorization": f"Bearer {supplied_secret}",
                "X-Trace-ID": supplied_trace,
                "X-Job-ID": supplied_job,
            },
        )
        after = (list(service.events), list(service.submitted_keys))

        assert diagnostics.status_code == 200
        assert diagnostics.json() == service.diagnostics_payload
        assert metrics.status_code == 200
        assert metrics.content == service.prometheus_payload
        content_type = {
            part.strip().lower()
            for part in metrics.headers["content-type"].split(";")
        }
        assert content_type == {"text/plain", "version=0.0.4", "charset=utf-8"}
        assert "bounded-diagnostic-trace" in diagnostics.text
        assert "bounded-diagnostic-job" in diagnostics.text
        for forbidden in (supplied_trace, supplied_job, supplied_secret):
            assert forbidden not in diagnostics.text
            assert forbidden.encode() not in metrics.content
        for forbidden in (
            service.job_id.encode(),
            b"bounded-diagnostic-trace",
            b"bounded-diagnostic-job",
            b"secret",
        ):
            assert forbidden not in metrics.content.lower()
        assert before == after == (["recover", "start"], [])
        assert list(config.state_root.iterdir()) == []

    assert service.diagnostics_calls == 1
    assert service.prometheus_calls == 1


def test_real_service_observability_gets_refresh_telemetry_only_without_mutating_jobs_queue_tasks_staged_files_or_tools(
    tmp_path: Path,
) -> None:
    from src.sandbox_broker.app import create_app

    service, store, config, sandbox_client = _service(tmp_path)
    record, _ = store.create_or_get(
        "observability-read-only",
        "b" * 64,
        "trace-observability-read-only",
    )
    store.fail(
        record.job_id,
        BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
        cleanup_status="succeeded",
    )
    jobs_root = config.state_root / "jobs"
    job_root = jobs_root / record.job_id
    fixture_files = {
        "staged/receptor.pdb": b"ATOM\n",
        "staged/ligand.sdf": b"ligand\n",
        "output/result.json": b'{"status":"fixture"}\n',
        "published/pose.pdbqt": b"MODEL 1\nENDMDL\n",
        "scientific/audit.txt": b"immutable scientific fixture\n",
    }
    for relative_path, content in fixture_files.items():
        path = job_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def snapshot_store() -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
        with store._connect() as connection:
            return tuple(
                (
                    table,
                    tuple(
                        tuple(row)
                        for row in connection.execute(
                            f"SELECT * FROM {table} ORDER BY rowid"
                        ).fetchall()
                    ),
                )
                for table in ("jobs", "transitions", "artifacts")
            )

    def snapshot_task(task: object) -> object:
        if task is None:
            return None
        return (
            id(task),
            task.get_name(),
            task.done(),
            task.cancelled(),
        )

    def snapshot_task_map(tasks: dict[str, object]) -> tuple[object, ...]:
        return tuple(
            sorted((key, snapshot_task(task)) for key, task in tasks.items())
        )

    def snapshot_job_tree() -> tuple[tuple[object, ...], ...]:
        entries: list[tuple[object, ...]] = []
        for path in sorted((jobs_root, *jobs_root.rglob("*"))):
            relative = (
                "."
                if path == jobs_root
                else path.relative_to(jobs_root).as_posix()
            )
            if path.is_symlink():
                entries.append((relative, "symlink", os.readlink(path)))
            elif path.is_dir():
                entries.append((relative, "directory"))
            else:
                content = path.read_bytes()
                entries.append(
                    (
                        relative,
                        "file",
                        len(content),
                        hashlib.sha256(content).hexdigest(),
                    )
                )
        return tuple(entries)

    def snapshot_runtime() -> dict[str, object]:
        condition_waiters = tuple(
            (id(waiter), waiter.done(), waiter.cancelled())
            for waiter in service._terminal_condition._waiters
        )
        return {
            "store": snapshot_store(),
            "job_tree": snapshot_job_tree(),
            "queue": (
                service._queue.qsize(),
                service._queue._unfinished_tasks,
                tuple(service._queue._queue),
            ),
            "prepared": tuple(
                sorted(
                    (key, id(value), repr(value))
                    for key, value in service._prepared.items()
                )
            ),
            "rejected_work": tuple(sorted(service._rejected_work)),
            "pending_rejections": snapshot_task_map(service._pending_rejections),
            "rejection_capacity": (
                service._pending_rejection_reservations,
                service._pending_rejection_capacity,
                tuple(sorted(service._rejection_ownership)),
            ),
            "job_tasks": snapshot_task_map(service._job_tasks),
            "create_tasks": snapshot_task_map(service._create_tasks),
            "cleanup_tasks": snapshot_task_map(service._cleanup_tasks),
            "isolated_tasks": tuple(
                sorted(snapshot_task(task) for task in service._isolated_tasks)
            ),
            "lifecycle_tasks": (
                snapshot_task(service._consumer_task),
                snapshot_task(service._shutdown_task),
                service._started,
                service._stopping,
            ),
            "cancel_events": tuple(
                sorted(
                    (key, id(event), event.is_set())
                    for key, event in service._cancel_events.items()
                )
            ),
            "terminal": (
                id(service._terminal_condition),
                condition_waiters,
                tuple(sorted(service._terminal_observed)),
                service._recovery_generation,
            ),
            "client_calls": tuple(sorted(sandbox_client.calls.items())),
        }

    app = create_app(config, service=service)
    with TestClient(app) as client:
        before = snapshot_runtime()

        diagnostics = client.get("/v1/diagnostics")
        metrics = client.get("/metrics")

        after = snapshot_runtime()
        assert diagnostics.status_code == 200
        assert diagnostics.json()["schema_version"] == 1
        assert metrics.status_code == 200
        assert before == after


def test_actual_entrypoint_startup_prebinds_one_af_unix_socket_without_tcp_or_second_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    events: list[tuple[object, ...]] = []
    target_reads = 0
    socket_metadata = SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o660,
        st_dev=7,
        st_ino=11,
        st_uid=getattr(os, "geteuid", lambda: 0)(),
        st_gid=getattr(os, "getegid", lambda: 0)(),
        st_file_attributes=0,
    )
    parent_metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o770,
        st_dev=7,
        st_ino=3,
        st_uid=getattr(os, "geteuid", lambda: 0)(),
        st_gid=getattr(os, "getegid", lambda: 0)(),
        st_file_attributes=0,
    )

    class InterceptedSocket:
        def bind(self, address: str) -> None:
            events.append(("bind", address))

        def listen(self, backlog: int) -> None:
            pytest.fail(f"entrypoint called listen({backlog})")

        def close(self) -> None:
            events.append(("close",))

    intercepted = InterceptedSocket()

    def socket_factory(family: int, socket_type: int) -> InterceptedSocket:
        events.append(("socket", family, socket_type))
        return intercepted

    def lstat(path: object) -> object:
        nonlocal target_reads
        observed = Path(path)
        if observed == target:
            target_reads += 1
            if target_reads == 1:
                raise FileNotFoundError
            return socket_metadata
        if observed == target.parent:
            return parent_metadata
        raise AssertionError(f"unexpected lstat path: {observed}")

    class InterceptedConfig:
        def __init__(self, application: object, **kwargs: object) -> None:
            events.append(("uvicorn-config", application, kwargs))

    class InterceptedServer:
        def __init__(
            self,
            configuration: object,
            *,
            readiness_callback: object,
        ) -> None:
            events.append(("uvicorn-server", configuration, readiness_callback))

        def run(self, *, sockets: list[object]) -> None:
            events.append(("uvicorn-run", tuple(sockets)))

    monkeypatch.setattr(
        entrypoint,
        "_trusted_socket_parent",
        lambda path: (target, entrypoint._identity(parent_metadata)),
    )
    monkeypatch.setattr(entrypoint.os, "lstat", lstat)
    monkeypatch.setattr(
        entrypoint.os,
        "chmod",
        lambda path, mode: events.append(("chmod", Path(path), mode)),
    )
    monkeypatch.setattr(entrypoint, "_bound_socket_is_ready", lambda path: True)
    monkeypatch.setattr(
        entrypoint,
        "_remove_owned_bound_socket",
        lambda path, identity: events.append(("cleanup", path, identity)),
    )
    monkeypatch.setattr(entrypoint.socket, "socket", socket_factory)
    monkeypatch.setattr(
        entrypoint.socket,
        "create_server",
        lambda *args, **kwargs: pytest.fail("entrypoint called create_server"),
        raising=False,
    )
    monkeypatch.setattr(
        entrypoint.uvicorn,
        "run",
        lambda *args, **kwargs: pytest.fail("entrypoint called uvicorn.run"),
    )
    monkeypatch.setattr(entrypoint.uvicorn, "Config", InterceptedConfig)
    monkeypatch.setattr(entrypoint, "_ReadyServer", InterceptedServer)

    application = object()
    entrypoint.serve_application(application, target)

    socket_events = [event for event in events if event[0] == "socket"]
    assert socket_events == [
        ("socket", entrypoint._AF_UNIX, entrypoint.socket.SOCK_STREAM)
    ]
    assert socket_events[0][1] != entrypoint.socket.AF_INET
    assert ("bind", str(target)) in events
    assert ("uvicorn-run", (intercepted,)) in events


@pytest.mark.parametrize("path", ["/v1/diagnostics", "/metrics"])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_private_observability_endpoints_reject_mutating_methods_without_service_calls(
    tmp_path: Path,
    path: str,
    method: str,
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app) as client:
        before = (list(service.events), list(service.submitted_keys))
        response = client.request(method, path, json={"job_id": service.job_id})
        after = (list(service.events), list(service.submitted_keys))

    assert response.status_code in {404, 405}
    assert before == after == (["recover", "start"], [])
    assert service.diagnostics_calls == 0
    assert service.prometheus_calls == 0


class _DiagnosticsDictSubclass(dict[str, object]):
    pass


class _DiagnosticsIntSubclass(int):
    pass


class _MetricsBytesSubclass(bytes):
    pass


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        _DiagnosticsDictSubclass(schema_version=1),
        {},
        {"schema_version": True},
        {"schema_version": _DiagnosticsIntSubclass(1)},
        {"schema_version": 0},
        {"schema_version": 1.0},
    ],
)
def test_diagnostics_rejects_non_exact_builtin_schema(
    tmp_path: Path,
    result: object,
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService()
    service.diagnostics = lambda: result  # type: ignore[method-assign]
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v1/diagnostics")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error"}}


@pytest.mark.parametrize(
    "result",
    [
        None,
        "metric 1\n",
        bytearray(b"metric 1\n"),
        memoryview(b"metric 1\n"),
        _MetricsBytesSubclass(b"metric 1\n"),
        SimpleNamespace(content=b"metric 1\n"),
        (chunk for chunk in (b"metric 1\n",)),
    ],
)
def test_metrics_rejects_non_exact_builtin_bytes(
    tmp_path: Path,
    result: object,
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService()
    service.prometheus_text = lambda: result  # type: ignore[method-assign]
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/metrics")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error"}}


class _SecretDiagnosticBaseFailure(BaseException):
    pass


@pytest.mark.parametrize("path", ["/v1/diagnostics", "/metrics"])
@pytest.mark.parametrize("failure_type", [RuntimeError, _SecretDiagnosticBaseFailure])
def test_private_observability_failures_are_sanitized_without_exception_context(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    path: str,
    failure_type: type[BaseException],
) -> None:
    from src.sandbox_broker.app import create_app

    secret = "DIAGNOSTIC_SECRET_MUST_NOT_ESCAPE"
    service = _ApiService()

    def fail() -> object:
        try:
            raise ValueError(f"context-{secret}")
        except ValueError as cause:
            raise failure_type(f"cause-{secret}") from cause

    if path == "/v1/diagnostics":
        service.diagnostics = fail  # type: ignore[method-assign]
    else:
        service.prometheus_text = fail  # type: ignore[method-assign]
    app = create_app(_config(tmp_path), service=service)
    caplog.set_level(logging.DEBUG)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(path)

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error"}}
    rendered = response.text.lower()
    assert secret.lower() not in rendered
    for forbidden in ("traceback", "context", "cause"):
        assert forbidden not in rendered
    assert secret not in caplog.text


def test_api_validation_and_multipart_shape_are_stable(tmp_path: Path) -> None:
    from src.sandbox_broker.app import create_app

    app = create_app(_config(tmp_path), service=_ApiService())
    with TestClient(app) as client:
        missing_key = client.post(
            "/v1/docking/jobs",
            data={"request_json": json.dumps({"center": [1, 2, 3], "size": [20, 20, 20]})},
            files={
                "receptor": ("target.pdb", b"ATOM\n"),
                "ligand": ("compound.sdf", b"ligand\n"),
            },
        )
        extra_parameter = _submit_api(
            client,
            parameters={
                "center": [1, 2, 3],
                "size": [20, 20, 20],
                "command": "id",
            },
        )
        extra_form = _submit_api(client, extra_data={"policy": "unsafe"})
        extra_file = _submit_api(
            client,
            extra_files={"mount": ("mount.txt", b"/", "text/plain")},
        )
        malformed = client.post(
            "/v1/docking/jobs",
            headers={"Idempotency-Key": "malformed"},
            data={"request_json": "{"},
            files={
                "receptor": ("target.pdb", b"ATOM\n"),
                "ligand": ("compound.sdf", b"ligand\n"),
            },
        )
        oversized_json = _submit_api(
            client,
            parameters={
                "center": [1, 2, 3],
                "size": [20, 20, 20],
                "padding": "x" * (64 * 1024),
            },
        )

    for response in (
        missing_key,
        extra_parameter,
        extra_form,
        extra_file,
        malformed,
        oversized_json,
    ):
        assert response.status_code == 422
        assert response.json() == {"error": {"code": "invalid_input"}}


@pytest.mark.parametrize(
    "headers",
    [
        [("Idempotency-Key", "same"), ("Idempotency-Key", "same")],
        [("Idempotency-Key", "first"), ("Idempotency-Key", "second")],
    ],
)
def test_api_rejects_every_duplicate_raw_idempotency_header(
    tmp_path: Path,
    headers: list[tuple[str, str]],
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app) as client:
        response = client.post(
            "/v1/docking/jobs",
            headers=headers,
            data={
                "request_json": json.dumps(
                    {"center": [1, 2, 3], "size": [20, 20, 20]}
                )
            },
            files={
                "receptor": ("target.pdb", b"ATOM\n"),
                "ligand": ("compound.sdf", b"ligand\n"),
            },
        )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_input"}}
    assert service.submitted_keys == []


@pytest.mark.parametrize("key", [" leading", "trailing ", "comma,key"])
def test_api_rejects_ows_or_comma_in_raw_idempotency_header(
    tmp_path: Path,
    key: str,
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    with TestClient(app) as client:
        response = _submit_api(client, key=key)
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_input"}}
    assert service.submitted_keys == []


@pytest.mark.parametrize(
    "raw_value",
    [b"", b"x" * 257, b"bad\x00key", b"bad\x7fkey", b"\xff", "snowman-\u2603".encode()],
)
def test_raw_idempotency_parser_strictly_rejects_non_ascii_or_control_bytes(
    raw_value: bytes,
) -> None:
    from src.sandbox_broker.app import _raw_idempotency_key

    with pytest.raises(BrokerFailure) as raised:
        _raw_idempotency_key(
            {"headers": [(b"idempotency-key", raw_value)]}
        )
    assert raised.value.code is BrokerErrorCode.INVALID_INPUT


@pytest.mark.parametrize("key", [" leading", "trailing "])
def test_service_rejects_idempotency_ows_before_upload_or_lifecycle_check(
    tmp_path: Path,
    key: str,
) -> None:
    service, store, _, _ = _service(tmp_path)
    generated = _GeneratedFile(16)
    receptor = type("Upload", (), {"filename": "target.pdb", "file": generated})()

    async def scenario() -> BrokerFailure:
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                _parameters(),
                receptor,
                _upload("compound.sdf", b"ligand\n"),
                key,
            )
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.INVALID_INPUT
    assert generated.total_read == 0
    assert store.active_jobs() == []


@pytest.mark.parametrize(
    ("key", "status", "code"),
    [
        ("conflict", 409, "idempotency_conflict"),
        ("queue-full", 429, "queue_saturated"),
        ("dependency-down", 503, "opensandbox_unavailable"),
        ("unexpected-secret", 500, "internal_error"),
    ],
)
def test_api_error_mapping_never_returns_exception_context(
    tmp_path: Path,
    key: str,
    status: int,
    code: str,
) -> None:
    from src.sandbox_broker.app import create_app

    app = create_app(_config(tmp_path), service=_ApiService())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = _submit_api(client, key=key)
        missing = client.get(f"/v1/docking/jobs/{'f' * 32}")
    assert response.status_code == status
    assert response.json() == {"error": {"code": code}}
    assert "secret" not in response.text.lower()
    assert str(tmp_path) not in response.text
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "not_found"}}


def test_openapi_contains_only_narrow_routes_and_no_dangerous_schema_fields(
    tmp_path: Path,
) -> None:
    from src.sandbox_broker.app import create_app

    app = create_app(_config(tmp_path), service=_ApiService())
    assert app.openapi_url is None
    schema = app.openapi()
    assert set(schema["paths"]) == {
        "/v1/docking/jobs",
        "/v1/docking/jobs/{job_id}",
        "/v1/docking/jobs/{job_id}/manifest",
        "/v1/docking/jobs/{job_id}/artifacts/{artifact_id}",
        "/v1/docking/jobs/{job_id}/cancel",
        "/v1/diagnostics",
        "/metrics",
        "/healthz",
    }
    serialized = json.dumps(schema, sort_keys=True).lower()
    for forbidden in ('"command"', '"image"', '"mounts"', '"policy"', '"debug"', '"admin"'):
        assert forbidden not in serialized


def test_app_registers_exact_private_http_paths_without_docs_or_redirects(
    tmp_path: Path,
) -> None:
    from src.sandbox_broker.app import create_app

    app = create_app(_config(tmp_path), service=_ApiService())
    assert [route.path for route in app.routes] == [
        "/v1/docking/jobs",
        "/v1/docking/jobs/{job_id}",
        "/v1/docking/jobs/{job_id}/manifest",
        "/v1/docking/jobs/{job_id}/artifacts/{artifact_id}",
        "/v1/docking/jobs/{job_id}/cancel",
        "/v1/diagnostics",
        "/metrics",
        "/healthz",
    ]
    with TestClient(app, follow_redirects=False) as client:
        for path in (
            "/docs",
            "/openapi.json",
            "/redoc",
            "/healthz/",
            "/v1/diagnostics/",
            "/metrics/",
        ):
            response = client.get(path)
            assert response.status_code == 404
            assert "location" not in response.headers
            assert response.json() == {"error": {"code": "not_found"}}


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/docking/jobs",
        "raw_path": b"/v1/docking/jobs",
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 1234),
        "server": ("broker", 80),
    }


def test_transport_gate_counts_actual_chunked_bytes_and_stops_after_overflow() -> None:
    from src.sandbox_broker.app import _MultipartBodyLimitMiddleware

    mebibyte = 1024 * 1024
    receive_calls = 0
    service_called = False
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls > 63:
            pytest.fail("middleware read beyond the first overflowing chunk")
        return {
            "type": "http.request",
            "body": b"x" * mebibyte,
            "more_body": True,
        }

    async def downstream(
        scope: dict[str, object],
        downstream_receive: object,
        send: object,
    ) -> None:
        nonlocal service_called
        del scope, send
        while True:
            message = await downstream_receive()
            if not message.get("more_body", False):
                break
        service_called = True

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = _MultipartBodyLimitMiddleware(
        downstream,
        maximum_bytes=62 * mebibyte,
    )
    asyncio.run(middleware(_http_scope(), receive, send))

    assert receive_calls == 63
    assert service_called is False
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {
        "error": {"code": "payload_too_large"}
    }


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"1"), (b"content-length", b"1")],
        [(b"content-length", b"1"), (b"content-length", b"2")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"+1")],
        [(b"content-length", b" 1")],
        [(b"content-length", b"1, 1")],
        [(b"content-length", b"not-a-number")],
    ],
)
def test_transport_gate_rejects_duplicate_or_invalid_content_length_before_receive(
    headers: list[tuple[bytes, bytes]],
) -> None:
    from src.sandbox_broker.app import _MultipartBodyLimitMiddleware

    downstream_called = False
    receive_called = False
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def downstream(*args: object) -> None:
        nonlocal downstream_called
        del args
        downstream_called = True

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(
        _MultipartBodyLimitMiddleware(downstream, maximum_bytes=8)(
            _http_scope(headers), receive, send
        )
    )
    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 422
    assert json.loads(sent[1]["body"]) == {"error": {"code": "invalid_input"}}


def test_transport_gate_preflights_declared_oversize_without_reading_body() -> None:
    from src.sandbox_broker.app import _MultipartBodyLimitMiddleware

    calls: list[str] = []
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        calls.append("receive")
        return {"type": "http.request", "body": b"secret", "more_body": False}

    async def downstream(*args: object) -> None:
        del args
        calls.append("downstream")

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(
        _MultipartBodyLimitMiddleware(downstream, maximum_bytes=8)(
            _http_scope([(b"content-length", b"9")]), receive, send
        )
    )
    assert calls == []
    assert sent[0]["status"] == 413


def test_transport_gate_allows_missing_length_but_rejects_actual_overflow() -> None:
    from src.sandbox_broker.app import _MultipartBodyLimitMiddleware

    messages = iter(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": True},
            {"type": "http.request", "body": b"9", "more_body": False},
        ]
    )
    service_called = False
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return next(messages)

    async def downstream(scope: object, downstream_receive: object, send: object) -> None:
        nonlocal service_called
        del scope, send
        while True:
            message = await downstream_receive()
            if not message.get("more_body", False):
                break
        service_called = True

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(
        _MultipartBodyLimitMiddleware(downstream, maximum_bytes=8)(
            _http_scope(), receive, send
        )
    )
    assert service_called is False
    assert sent[0]["status"] == 413


def test_full_stack_chunked_upload_without_length_stays_413_and_skips_service(
    tmp_path: Path,
) -> None:
    from src.sandbox_broker.app import (
        _MultipartBodyLimitMiddleware,
        create_app,
    )

    boundary = b"task6-limit-probe"
    payload = b"".join(
        [
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="request_json"\r\n\r\n',
            b'{"center":[1,2,3],"size":[20,20,20]}\r\n',
            b"--" + boundary + b"\r\n",
            (
                b'Content-Disposition: form-data; name="receptor"; '
                b'filename="receptor.pdb"\r\n'
            ),
            b"Content-Type: chemical/x-pdb\r\n\r\n",
            b"R" * 256 + b"\r\n",
            b"--" + boundary + b"\r\n",
            (
                b'Content-Disposition: form-data; name="ligand"; '
                b'filename="ligand.sdf"\r\n'
            ),
            b"Content-Type: chemical/x-mdl-sdfile\r\n\r\n",
            b"LIGAND\r\n",
            b"--" + boundary + b"--\r\n",
        ]
    )
    chunks = [payload[index : index + 31] for index in range(0, len(payload), 31)]
    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    limited = _MultipartBodyLimitMiddleware(app, maximum_bytes=127)
    sent: list[dict[str, object]] = []
    receive_count = 0

    async def receive() -> dict[str, object]:
        nonlocal receive_count
        chunk = chunks[receive_count]
        receive_count += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": receive_count < len(chunks),
        }

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _http_scope(
        [
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
            (b"idempotency-key", b"chunked-probe"),
        ]
    )

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            await limited(scope, receive, send)

    asyncio.run(exercise())

    assert all(name != b"content-length" for name, _ in scope["headers"])
    assert receive_count == 5
    assert service.submitted_keys == []
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {
        "error": {"code": "payload_too_large"}
    }


def test_rolled_multipart_spool_is_explicitly_closed_after_full_stack_413(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import starlette.formparsers as formparsers
    from src.sandbox_broker.app import (
        _MultipartBodyLimitMiddleware,
        create_app,
    )

    real_spooled_temporary_file = tempfile.SpooledTemporaryFile
    created: list[tuple[object, int]] = []

    def tracked_spool(*args: object, **kwargs: object) -> object:
        kwargs["max_size"] = 1
        spool = real_spooled_temporary_file(*args, **kwargs)
        descriptor = spool.fileno()
        created.append((spool, descriptor))
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_spool)
    boundary = b"owned-limit-probe"
    payload = b"".join(
        [
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="request_json"\r\n\r\n',
            b'{"center":[1,2,3],"size":[20,20,20]}\r\n',
            b"--" + boundary + b"\r\n",
            (
                b'Content-Disposition: form-data; name="receptor"; '
                b'filename="receptor.pdb"\r\n'
            ),
            b"Content-Type: chemical/x-pdb\r\n\r\n",
            b"R" * 1024 + b"\r\n",
            b"--" + boundary + b"--\r\n",
        ]
    )
    chunks = [payload[index : index + 64] for index in range(0, len(payload), 64)]
    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    limited = _MultipartBodyLimitMiddleware(app, maximum_bytes=448)
    sent: list[dict[str, object]] = []
    receive_count = 0
    fd_baseline = _open_fd_count()

    async def receive() -> dict[str, object]:
        nonlocal receive_count
        chunk = chunks[receive_count]
        receive_count += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": receive_count < len(chunks),
        }

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = _http_scope(
        [
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
            (b"idempotency-key", b"rolled-limit"),
        ]
    )

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            await limited(scope, receive, send)

    asyncio.run(exercise())

    assert sent[0]["status"] == 413
    assert service.submitted_keys == []
    assert created
    assert receive_count * 64 <= 448 + 64
    for spool, descriptor in created:
        assert spool._rolled is True
        assert spool.closed is True
        with pytest.raises(OSError):
            os.fstat(descriptor)
    if fd_baseline is not None:
        assert _open_fd_count() == fd_baseline


def test_rolled_multipart_spool_closes_on_cancel_and_retains_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import starlette.formparsers as formparsers
    from src.sandbox_broker.app import create_app

    real_spooled_temporary_file = tempfile.SpooledTemporaryFile
    created: list[tuple[object, int]] = []

    def tracked_spool(*args: object, **kwargs: object) -> object:
        kwargs["max_size"] = 1
        spool = real_spooled_temporary_file(*args, **kwargs)
        descriptor = spool.fileno()
        created.append((spool, descriptor))
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_spool)
    boundary = b"owned-cancel-probe"
    prefix = b"".join(
        [
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="request_json"\r\n\r\n',
            b'{"center":[1,2,3],"size":[20,20,20]}\r\n',
            b"--" + boundary + b"\r\n",
            (
                b'Content-Disposition: form-data; name="receptor"; '
                b'filename="receptor.pdb"\r\n'
            ),
            b"Content-Type: chemical/x-pdb\r\n\r\n",
            b"R" * 512,
        ]
    )
    chunks = [prefix[index : index + 64] for index in range(0, len(prefix), 64)]
    service = _ApiService()
    app = create_app(_config(tmp_path), service=service)
    receive_count = 0

    async def receive() -> dict[str, object]:
        nonlocal receive_count
        if receive_count == len(chunks):
            raise asyncio.CancelledError
        chunk = chunks[receive_count]
        receive_count += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message: dict[str, object]) -> None:
        pytest.fail(f"response sent after cancellation: {message}")

    scope = _http_scope(
        [
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
            (b"idempotency-key", b"rolled-cancel"),
        ]
    )

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)

    with pytest.raises(asyncio.CancelledError) as raised:
        asyncio.run(exercise())

    assert raised.value.__traceback__ is not None
    assert service.submitted_keys == []
    assert created
    for spool, descriptor in created:
        assert spool._rolled is True
        assert spool.closed is True
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    ("key", "expected_status"),
    [("owned-success", 202), ("unexpected-secret", 500)],
)
def test_route_closes_every_upload_after_service_success_or_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    expected_status: int,
) -> None:
    import starlette.formparsers as formparsers
    from src.sandbox_broker.app import create_app

    real_spooled_temporary_file = tempfile.SpooledTemporaryFile
    created: list[object] = []

    def tracked_spool(*args: object, **kwargs: object) -> object:
        spool = real_spooled_temporary_file(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_spool)
    app = create_app(_config(tmp_path), service=_ApiService())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = _submit_api(client, key=key)
    assert response.status_code == expected_status
    assert len(created) == 2
    assert all(spool.closed for spool in created)


def test_route_closes_every_upload_when_service_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import starlette.formparsers as formparsers
    from src.sandbox_broker.app import create_app

    class CancelledSubmissionService(_ApiService):
        async def submit_uploads(self, *args: object) -> BrokerJobView:
            self.submitted_keys.append(str(args[-1]))
            raise asyncio.CancelledError(r"service-canary C:\private\upload")

    real_spooled_temporary_file = tempfile.SpooledTemporaryFile
    created: list[object] = []

    def tracked_spool(*args: object, **kwargs: object) -> object:
        spool = real_spooled_temporary_file(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_spool)
    boundary = b"service-cancel-probe"
    payload = b"".join(
        [
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="request_json"\r\n\r\n',
            b'{"center":[1,2,3],"size":[20,20,20]}\r\n',
            b"--" + boundary + b"\r\n",
            (
                b'Content-Disposition: form-data; name="receptor"; '
                b'filename="receptor.pdb"\r\n\r\nRECEPTOR\r\n'
            ),
            b"--" + boundary + b"\r\n",
            (
                b'Content-Disposition: form-data; name="ligand"; '
                b'filename="ligand.sdf"\r\n\r\nLIGAND\r\n'
            ),
            b"--" + boundary + b"--\r\n",
        ]
    )
    service = CancelledSubmissionService()
    app = create_app(_config(tmp_path), service=service)
    received = False

    async def receive() -> dict[str, object]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        pytest.fail(f"response sent after service cancellation: {message}")

    scope = _http_scope(
        [
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
            (b"idempotency-key", b"service-cancel"),
        ]
    )

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(exercise())
    assert service.submitted_keys == ["service-cancel"]
    assert len(created) == 2
    assert all(spool.closed for spool in created)


def test_form_cleanup_swallows_close_failure_and_continues() -> None:
    from src.sandbox_broker.app import _close_form_uploads

    calls: list[str] = []

    class FailingUpload(UploadFile):
        async def close(self) -> None:
            calls.append("failing")
            raise RuntimeError(r"close-canary C:\private\spool")

    class FollowingUpload(UploadFile):
        async def close(self) -> None:
            calls.append("following")

    form = FormData(
        [
            ("receptor", FailingUpload(io.BytesIO(b"r"), filename="r.pdb")),
            ("ligand", FollowingUpload(io.BytesIO(b"l"), filename="l.sdf")),
        ]
    )
    asyncio.run(_close_form_uploads(form))
    asyncio.run(_close_form_uploads(form))
    assert calls == ["failing", "following", "failing", "following"]


def test_parser_cleanup_swallows_close_failure_and_is_repeat_safe() -> None:
    from src.sandbox_broker.app import _close_parser_created_files

    calls: list[str] = []

    class FailingFile:
        def close(self) -> None:
            calls.append("failing")
            raise RuntimeError(r"close-canary C:\private\parser")

    class FollowingFile:
        def close(self) -> None:
            calls.append("following")

    parser = SimpleNamespace(
        _files_to_close_on_error=[FailingFile(), FollowingFile()]
    )
    _close_parser_created_files(parser)
    _close_parser_created_files(parser)
    assert calls == ["failing", "following"]
    assert parser._files_to_close_on_error == []


def test_owned_multipart_parser_supports_legacy_signature_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.sandbox_broker.app as app_module

    observed: list[dict[str, object]] = []

    class LegacyParser:
        def __init__(
            self,
            headers: object,
            stream: object,
            *,
            max_files: int,
            max_fields: int,
        ) -> None:
            observed.append(
                {
                    "headers": headers,
                    "stream": stream,
                    "max_files": max_files,
                    "max_fields": max_fields,
                }
            )
            self._files_to_close_on_error: list[object] = []

        async def parse(self) -> FormData:
            return FormData()

    async def stream() -> object:
        if False:
            yield b""

    stream_value = stream()
    request = SimpleNamespace(headers=object(), stream=lambda: stream_value)
    monkeypatch.setattr(app_module, "MultiPartParser", LegacyParser)

    result = asyncio.run(app_module._parse_owned_multipart(request))
    assert isinstance(result, FormData)
    assert len(observed) == 1
    assert observed[0]["headers"] is request.headers
    assert observed[0]["stream"] is stream_value
    assert observed[0]["max_files"] == 2
    assert observed[0]["max_fields"] == 1
    assert "max_part_size" not in observed[0]


def test_owned_multipart_parser_passes_supported_limit_and_never_retries_typeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.sandbox_broker.app as app_module

    calls: list[dict[str, object]] = []

    class ModernParser:
        def __init__(
            self,
            headers: object,
            stream: object,
            *,
            max_files: int,
            max_fields: int,
            max_part_size: int,
        ) -> None:
            calls.append(
                {
                    "headers": headers,
                    "stream": stream,
                    "max_files": max_files,
                    "max_fields": max_fields,
                    "max_part_size": max_part_size,
                }
            )
            raise TypeError("parser-internal-typeerror")

    async def stream() -> object:
        if False:
            yield b""

    request = SimpleNamespace(headers=object(), stream=lambda: stream())
    monkeypatch.setattr(app_module, "MultiPartParser", ModernParser)

    with pytest.raises(TypeError, match="parser-internal-typeerror"):
        asyncio.run(app_module._parse_owned_multipart(request))
    assert len(calls) == 1
    assert calls[0]["max_files"] == 2
    assert calls[0]["max_fields"] == 1
    assert calls[0]["max_part_size"] == 64 * 1024


def test_full_api_rejects_one_file_over_input_max_below_transport_total_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import starlette.formparsers as formparsers
    from src.sandbox_broker.app import create_app

    service, store, config, _ = _service(tmp_path)
    real_spooled_temporary_file = tempfile.SpooledTemporaryFile
    created: list[object] = []

    def tracked_spool(*args: object, **kwargs: object) -> object:
        spool = real_spooled_temporary_file(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_spool)
    ligand = b"L" * (config.ligand_max_bytes + 1)
    declared_payload = len(ligand) + len(b"ATOM\n")
    transport_limit = (
        config.receptor_max_bytes
        + config.ligand_max_bytes
        + 64 * 1024
        + 2 * 1024 * 1024
    )
    assert declared_payload < transport_limit
    app = create_app(config, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/v1/docking/jobs",
            headers={"Idempotency-Key": "single-file-over-input-max"},
            data={
                "request_json": json.dumps(
                    {"center": [1, 2, 3], "size": [20, 20, 20]}
                )
            },
            files={
                "receptor": ("target.pdb", b"ATOM\n"),
                "ligand": ("compound.sdf", ligand),
            },
        )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_input"}}
    assert len(created) == 2
    assert all(spool.closed for spool in created)
    assert store.active_jobs() == []


@pytest.mark.parametrize("disconnect", [False, True])
def test_transport_gate_propagates_cancellation_and_client_disconnect_without_state(
    disconnect: bool,
) -> None:
    from src.sandbox_broker.app import _MultipartBodyLimitMiddleware

    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        if disconnect:
            return {"type": "http.disconnect"}
        raise asyncio.CancelledError

    async def downstream(scope: object, downstream_receive: object, send: object) -> None:
        del scope, send
        message = await downstream_receive()
        if message["type"] == "http.disconnect":
            raise ClientDisconnect

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = _MultipartBodyLimitMiddleware(downstream, maximum_bytes=8)
    expected = ClientDisconnect if disconnect else asyncio.CancelledError
    with pytest.raises(expected):
        asyncio.run(middleware(_http_scope(), receive, send))
    assert sent == []
    assert vars(middleware) == {"app": downstream, "maximum_bytes": 8}


def test_lifespan_stop_incomplete_is_sanitized_after_cleanup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService(stop_failure=StopIncomplete())
    app = create_app(_config(tmp_path), service=service)
    with pytest.raises(RuntimeError, match="^sandbox broker shutdown failed$") as raised:
        asyncio.run(_enter_lifespan_once(app))
    assert service.events == ["recover", "start", "stop"]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "sandbox broker shutdown failed" in caplog.text
    assert "runtime-secret-value" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_production_service_initialization_is_deferred_to_lifespan(
    tmp_path: Path,
) -> None:
    from src.sandbox_broker.app import create_app

    config = _config(tmp_path)
    database = config.state_root / "broker.sqlite"
    app = create_app(config)
    assert not database.exists()
    with TestClient(app) as client:
        assert database.is_file()
        assert client.get("/healthz").status_code == 200
    assert "opensandbox" not in sys.modules


@pytest.mark.parametrize("failure_stage", ["recover", "start"])
def test_lifespan_startup_failure_attempts_bounded_stop_and_redacts_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failure_stage: str,
) -> None:
    from src.sandbox_broker.app import create_app

    class FailingService(_ApiService):
        async def recover(self) -> None:
            self.events.append("recover")
            if failure_stage == "recover":
                raise RuntimeError(r"OPEN_SANDBOX_API_KEY=secret C:\private\state")

        async def start(self) -> None:
            self.events.append("start")
            if failure_stage == "start":
                raise RuntimeError(r"OPEN_SANDBOX_API_KEY=secret C:\private\state")

    service = FailingService()
    app = create_app(_config(tmp_path), service=service)
    with pytest.raises(RuntimeError, match="^sandbox broker startup failed$") as raised:
        asyncio.run(_enter_lifespan_once(app))
    expected = ["recover", "stop"] if failure_stage == "recover" else [
        "recover",
        "start",
        "stop",
    ]
    assert service.events == expected
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert "OPEN_SANDBOX_API_KEY" not in rendered
    assert str(tmp_path) not in rendered
    assert "sandbox broker startup failed" in caplog.text
    assert "OPEN_SANDBOX_API_KEY" not in caplog.text
    assert str(tmp_path) not in caplog.text


async def _enter_lifespan_once(app: object) -> None:
    async with app.router.lifespan_context(app):
        return None


def test_startup_failure_cleanup_failure_does_not_replace_sanitized_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.sandbox_broker.app import create_app

    class DoubleFailureService(_ApiService):
        async def recover(self) -> None:
            self.events.append("recover")
            raise RuntimeError(r"recover-canary C:\private\recover")

        async def stop(self, *, timeout: float | None = None) -> None:
            assert timeout is not None
            self.events.append("stop")
            raise RuntimeError(r"cleanup-canary C:\private\cleanup")

    service = DoubleFailureService()
    app = create_app(_config(tmp_path), service=service)
    with pytest.raises(RuntimeError, match="^sandbox broker startup failed$") as raised:
        asyncio.run(_enter_lifespan_once(app))
    assert service.events == ["recover", "stop"]
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    for forbidden in ("recover-canary", "cleanup-canary", "private"):
        assert forbidden not in caplog.text


@pytest.mark.parametrize("stage", ["startup", "shutdown"])
def test_lifespan_cancellation_is_message_free_and_still_cleans_up(
    tmp_path: Path,
    stage: str,
) -> None:
    from src.sandbox_broker.app import create_app

    class CancelledService(_ApiService):
        async def recover(self) -> None:
            self.events.append("recover")
            if stage == "startup":
                raise asyncio.CancelledError(r"cancel-canary C:\private\startup")

        async def stop(self, *, timeout: float | None = None) -> None:
            assert timeout is not None
            self.events.append("stop")
            if stage == "shutdown":
                raise asyncio.CancelledError(r"cancel-canary C:\private\shutdown")

    service = CancelledService()
    app = create_app(_config(tmp_path), service=service)
    with pytest.raises(asyncio.CancelledError) as raised:
        asyncio.run(_enter_lifespan_once(app))
    expected = ["recover", "stop"] if stage == "startup" else [
        "recover",
        "start",
        "stop",
    ]
    assert service.events == expected
    assert str(raised.value) == ""
    assert raised.value.__cause__ is None
    chained: BaseException | None = raised.value
    while chained is not None:
        assert "cancel-canary" not in str(chained)
        assert "private" not in str(chained).lower()
        chained = chained.__cause__ or chained.__context__
    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert "cancel-canary" not in rendered
    assert "private" not in rendered.lower()


@pytest.mark.parametrize("stage", ["startup", "shutdown"])
@pytest.mark.filterwarnings(
    "ignore:websockets.legacy is deprecated:DeprecationWarning"
)
@pytest.mark.filterwarnings(
    "ignore:websockets.server.WebSocketServerProtocol is deprecated:DeprecationWarning"
)
def test_uvicorn_lifespan_logs_never_include_service_canary_or_path(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    stage: str,
) -> None:
    import uvicorn
    from uvicorn.lifespan.on import LifespanOn

    from src.sandbox_broker.app import create_app

    canary = "LIFESPAN-SECRET-CANARY"
    private_path = str(tmp_path / "private" / "broker.sqlite")

    class FailingService(_ApiService):
        async def recover(self) -> None:
            self.events.append("recover")
            if stage == "startup":
                raise RuntimeError(f"{canary} {private_path}")

        async def stop(self, *, timeout: float | None = None) -> None:
            assert timeout is not None
            self.events.append("stop")
            if stage == "shutdown":
                raise RuntimeError(f"{canary} {private_path}")

    caplog.set_level(logging.ERROR)
    app = create_app(_config(tmp_path), service=FailingService())
    configuration = uvicorn.Config(app, lifespan="on", log_config=None)
    configuration.load()
    manager = LifespanOn(configuration)

    async def exercise() -> None:
        await manager.startup()
        if stage == "shutdown":
            await manager.shutdown()
        await asyncio.sleep(0)

    asyncio.run(exercise())

    assert manager.should_exit is True
    assert canary not in caplog.text
    assert private_path not in caplog.text
    assert "sandbox broker startup failed" in caplog.text or (
        "sandbox broker shutdown failed" in caplog.text
    )


def test_uds_main_sets_umask_before_config_and_uses_hardened_server_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    config = _config(tmp_path)
    events: list[object] = []
    application = object()

    monkeypatch.setattr(
        entrypoint.os,
        "umask",
        lambda mask: events.append(("umask", mask)),
    )
    monkeypatch.setattr(
        entrypoint.BrokerConfig,
        "from_env",
        classmethod(lambda cls: events.append("config") or config),
    )
    monkeypatch.setattr(
        entrypoint,
        "acquire_runtime_lock",
        lambda path: events.append(("lock", path)) or 91,
    )
    monkeypatch.setattr(
        entrypoint,
        "release_runtime_lock",
        lambda descriptor: events.append(("release", descriptor)),
    )
    monkeypatch.setattr(
        entrypoint,
        "remove_stale_socket",
        lambda path: events.append(("stale", path)),
    )
    monkeypatch.setattr(
        entrypoint,
        "create_app",
        lambda observed: events.append(("app", observed)) or application,
    )

    monkeypatch.setattr(
        entrypoint,
        "serve_application",
        lambda app, path: events.append(("serve", app, path)),
    )
    entrypoint.main()

    assert events == [
        ("umask", 0o007),
        "config",
        ("lock", config.socket_path),
        ("stale", config.socket_path),
        ("app", config),
        (
            "serve",
            application,
            config.socket_path,
        ),
        ("release", 91),
    ]


@pytest.mark.skipif(os.name != "posix", reason="real Unix sockets require POSIX")
def test_prebound_broker_socket_is_real_owned_and_mode_0660(tmp_path: Path) -> None:
    from scripts.run_sandbox_broker import bind_runtime_socket

    target = tmp_path / "broker.sock"
    listener, bound_identity = bind_runtime_socket(target)
    try:
        metadata = os.lstat(target)
        assert stat.S_ISSOCK(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == 0o660
        assert metadata.st_uid == os.geteuid()
        assert metadata.st_gid == os.getegid()
        assert listener.family == socket.AF_UNIX
        assert listener.type & socket.SOCK_STREAM
        assert bound_identity == (metadata.st_dev, metadata.st_ino)
    finally:
        listener.close()


@pytest.mark.parametrize("failure_point", ["initial_lstat", "chmod", "final_lstat"])
@pytest.mark.skipif(os.name != "posix", reason="real Unix sockets require POSIX")
def test_bind_failure_closes_and_unlinks_only_created_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    real_lstat = entrypoint.os.lstat
    real_socket = entrypoint.socket.socket
    created: list[socket.socket] = []
    target_lstats = 0

    def socket_factory(*args: object, **kwargs: object) -> socket.socket:
        listener = real_socket(*args, **kwargs)
        created.append(listener)
        return listener

    def lstat(path: object) -> os.stat_result:
        nonlocal target_lstats
        if Path(path) == target:
            target_lstats += 1
            if failure_point == "initial_lstat" and target_lstats == 2:
                raise OSError("injected initial lstat failure")
            if failure_point == "final_lstat" and target_lstats == 3:
                raise OSError("injected final lstat failure")
        return real_lstat(path)

    monkeypatch.setattr(entrypoint.socket, "socket", socket_factory)
    monkeypatch.setattr(entrypoint.os, "lstat", lstat)
    if failure_point == "chmod":
        monkeypatch.setattr(
            entrypoint.os,
            "chmod",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("injected chmod failure")
            ),
        )

    with pytest.raises(RuntimeError, match="^sandbox broker socket binding failed$"):
        entrypoint.bind_runtime_socket(target)

    assert len(created) == 1
    assert created[0].fileno() == -1
    assert not target.exists()


@pytest.mark.skipif(os.name != "posix", reason="real Unix sockets require POSIX")
def test_post_bind_lstat_failure_still_closes_and_unlinks_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(target))
    metadata = os.lstat(target)
    identity = (metadata.st_dev, metadata.st_ino)
    real_lstat = entrypoint.os.lstat
    failed_once = False

    def flaky_lstat(path: object) -> os.stat_result:
        nonlocal failed_once
        if Path(path) == target and not failed_once:
            failed_once = True
            raise OSError("injected post-bind lstat failure")
        return real_lstat(path)

    monkeypatch.setattr(
        entrypoint,
        "bind_runtime_socket",
        lambda _path: (listener, identity),
    )
    monkeypatch.setattr(entrypoint.os, "lstat", flaky_lstat)

    with pytest.raises(RuntimeError):
        entrypoint.serve_application(object(), target)

    assert listener.fileno() == -1
    assert not target.exists()


def test_ready_server_notifies_only_after_uvicorn_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    events: list[str] = []

    async def startup(server: object, sockets: object = None) -> None:
        del sockets
        events.append("uvicorn_started")
        server.started = True
        server.should_exit = False

    monkeypatch.setattr(entrypoint.uvicorn.Server, "startup", startup)
    configuration = entrypoint.uvicorn.Config(object(), log_config=None)
    server = entrypoint._ReadyServer(
        configuration,
        readiness_callback=lambda: events.append("systemd_ready"),
    )

    asyncio.run(server.startup(sockets=[]))

    assert events == ["uvicorn_started", "systemd_ready"]


def test_ready_notification_failure_shuts_down_lifespan_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    events: list[str] = []

    async def startup(server: object, sockets: object = None) -> None:
        del sockets
        events.append("uvicorn_started")
        server.started = True
        server.should_exit = False

    async def shutdown(server: object, sockets: object = None) -> None:
        del server, sockets
        events.append("lifespan_shutdown")

    def notify() -> None:
        events.append("notify_failed")
        raise RuntimeError("secret notify failure")

    monkeypatch.setattr(entrypoint.uvicorn.Server, "startup", startup)
    monkeypatch.setattr(entrypoint.uvicorn.Server, "shutdown", shutdown)
    configuration = entrypoint.uvicorn.Config(object(), log_config=None)
    server = entrypoint._ReadyServer(
        configuration,
        readiness_callback=notify,
    )

    with pytest.raises(
        RuntimeError,
        match="^sandbox broker readiness notification failed$",
    ):
        asyncio.run(server.startup(sockets=[]))

    assert events == ["uvicorn_started", "notify_failed", "lifespan_shutdown"]
    assert server.started is False


@pytest.mark.skipif(os.name != "posix", reason="real Unix sockets require POSIX")
def test_server_failure_removes_owned_prebound_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"

    def fail_after_start(_server: object, sockets: object = None) -> None:
        del sockets
        assert target.exists()
        raise RuntimeError("sandbox broker readiness notification failed")

    monkeypatch.setattr(entrypoint._ReadyServer, "run", fail_after_start)

    with pytest.raises(
        RuntimeError,
        match="^sandbox broker readiness notification failed$",
    ):
        entrypoint.serve_application(object(), target)

    assert not target.exists()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_stale_socket_cleanup_refuses_non_socket_targets(
    tmp_path: Path,
    kind: str,
) -> None:
    from scripts.run_sandbox_broker import remove_stale_socket

    target = tmp_path / "broker.sock"
    if kind == "file":
        target.write_text("do not delete", encoding="utf-8")
    elif kind == "directory":
        target.mkdir()
    else:
        source = tmp_path / "real.sock"
        source.write_text("do not delete", encoding="utf-8")
        try:
            target.symlink_to(source)
        except OSError:
            pytest.skip("symlinks are unavailable on this Windows host")

    with pytest.raises(RuntimeError, match="unsafe stale socket target"):
        remove_stale_socket(target)
    assert target.exists() or target.is_symlink()


def test_stale_socket_cleanup_quarantines_matching_stale_identity_before_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    socket_stat = SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o600,
        st_dev=7,
        st_ino=11,
        st_file_attributes=0,
    )
    parent_stat = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700,
        st_dev=7,
        st_ino=3,
        st_file_attributes=0,
    )
    parent_identity = entrypoint._identity(parent_stat)
    quarantine: Path | None = None
    replaced: list[tuple[Path, Path]] = []
    removed: list[Path] = []

    def lstat(path: object) -> object:
        nonlocal quarantine
        if Path(path) == target:
            return socket_stat
        if quarantine is not None and Path(path) == quarantine:
            return socket_stat
        if Path(path) == target.parent:
            return parent_stat
        raise AssertionError(f"unexpected lstat target: {path}")

    def replace(source: object, destination: object) -> None:
        nonlocal quarantine
        quarantine = Path(destination)
        replaced.append((Path(source), quarantine))

    monkeypatch.setattr(
        entrypoint,
        "_trusted_socket_parent",
        lambda path: (target, parent_identity),
    )
    monkeypatch.setattr(entrypoint, "_socket_stale_candidate", lambda path: True)
    monkeypatch.setattr(entrypoint.os, "lstat", lstat)
    monkeypatch.setattr(entrypoint.os, "replace", replace)
    monkeypatch.setattr(
        entrypoint.os,
        "unlink",
        lambda path: removed.append(Path(path)),
    )
    entrypoint.remove_stale_socket(target)
    assert len(replaced) == 1
    assert replaced[0][0] == target
    assert replaced[0][1].parent == target.parent
    assert replaced[0][1] != target
    assert removed == [replaced[0][1]]


def test_stale_socket_cleanup_rejects_identity_swap_without_unlinking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    real_lstat = entrypoint.os.lstat
    observations = iter(
        [
            SimpleNamespace(
                st_mode=stat.S_IFSOCK | 0o600,
                st_dev=7,
                st_ino=11,
                st_file_attributes=0,
            ),
            SimpleNamespace(
                st_mode=stat.S_IFSOCK | 0o600,
                st_dev=7,
                st_ino=12,
                st_file_attributes=0,
            ),
        ]
    )

    def lstat(path: object) -> object:
        if Path(path) == target:
            return next(observations)
        return real_lstat(path)

    monkeypatch.setattr(entrypoint.os, "lstat", lstat)
    monkeypatch.setattr(entrypoint, "_socket_stale_candidate", lambda path: True)
    monkeypatch.setattr(
        entrypoint.os,
        "replace",
        lambda source, destination: pytest.fail(
            f"replaced swapped target: {source} -> {destination}"
        ),
    )
    with pytest.raises(RuntimeError, match="stale socket identity changed"):
        entrypoint.remove_stale_socket(target)


@pytest.mark.skipif(os.name != "posix", reason="real Unix sockets require POSIX")
def test_stale_socket_cleanup_removes_real_unix_socket(tmp_path: Path) -> None:
    from scripts.run_sandbox_broker import remove_stale_socket

    target = tmp_path / "broker.sock"
    uds = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        uds.bind(str(target))
    finally:
        uds.close()
    remove_stale_socket(target)
    assert not target.exists()


@pytest.mark.parametrize("mode", [0o700, 0o750])
def test_runtime_parent_accepts_single_user_or_shared_group_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR | mode,
        st_dev=9,
        st_ino=2,
        st_uid=101,
        st_gid=202,
        st_file_attributes=0,
    )
    fake_os = SimpleNamespace(
        name="posix",
        path=entrypoint.os.path,
        lstat=lambda path: metadata,
        geteuid=lambda: 101,
        getegid=lambda: 202,
    )
    monkeypatch.setattr(entrypoint, "os", fake_os)

    observed, identity = entrypoint._trusted_socket_parent(target)
    assert observed == target
    assert identity == entrypoint._identity(metadata)


@pytest.mark.parametrize(
    ("mode", "uid", "gid"),
    [
        (0o770, 101, 202),
        (0o751, 101, 202),
        (0o650, 101, 202),
        (0o750, 999, 202),
        (0o750, 101, 999),
    ],
)
def test_runtime_parent_rejects_unsafe_mode_owner_or_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR | mode,
        st_dev=9,
        st_ino=2,
        st_uid=uid,
        st_gid=gid,
        st_file_attributes=0,
    )
    fake_os = SimpleNamespace(
        name="posix",
        path=entrypoint.os.path,
        lstat=lambda path: metadata,
        geteuid=lambda: 101,
        getegid=lambda: 202,
    )
    monkeypatch.setattr(entrypoint, "os", fake_os)

    with pytest.raises(RuntimeError, match="unsafe sandbox broker runtime parent"):
        entrypoint._trusted_socket_parent(target)


def test_runtime_lock_uses_secure_open_identity_checks_and_nonblocking_flock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    socket_path = tmp_path / "broker.sock"
    lock_path = tmp_path / ".sandbox-broker.lock"
    parent_stat = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o750,
        st_dev=9,
        st_ino=2,
        st_file_attributes=0,
    )
    lock_stat = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=9,
        st_ino=4,
        st_nlink=1,
        st_file_attributes=0,
    )
    opened: list[tuple[Path, int, int]] = []
    flocked: list[int] = []

    monkeypatch.setattr(
        entrypoint,
        "_trusted_socket_parent",
        lambda path: (socket_path, entrypoint._identity(parent_stat)),
    )
    monkeypatch.setattr(
        entrypoint.os,
        "open",
        lambda path, flags, mode: opened.append((Path(path), flags, mode)) or 41,
    )
    monkeypatch.setattr(entrypoint.os, "fstat", lambda descriptor: lock_stat)
    monkeypatch.setattr(
        entrypoint.os,
        "lstat",
        lambda path: parent_stat if Path(path) == tmp_path else lock_stat,
    )
    monkeypatch.setattr(
        entrypoint,
        "_flock_exclusive_nonblocking",
        lambda descriptor: flocked.append(descriptor),
    )

    descriptor = entrypoint.acquire_runtime_lock(socket_path)
    assert descriptor == 41
    assert opened == [
        (
            lock_path,
            entrypoint.os.O_CREAT
            | entrypoint.os.O_RDWR
            | getattr(entrypoint.os, "O_NOFOLLOW", 0)
            | getattr(entrypoint.os, "O_CLOEXEC", 0),
            0o600,
        )
    ]
    assert flocked == [41]


def test_runtime_lock_rejects_nonprivate_parent_before_flock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    socket_path = tmp_path / "broker.sock"
    parent_stat = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o755,
        st_dev=9,
        st_ino=2,
        st_file_attributes=0,
    )
    lock_stat = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=9,
        st_ino=4,
        st_nlink=1,
        st_file_attributes=0,
    )
    closed: list[int] = []

    monkeypatch.setattr(
        entrypoint,
        "_trusted_socket_parent",
        lambda path: (socket_path, entrypoint._identity(parent_stat)),
    )
    monkeypatch.setattr(entrypoint.os, "open", lambda *args: 43)
    monkeypatch.setattr(entrypoint.os, "fstat", lambda descriptor: lock_stat)
    monkeypatch.setattr(
        entrypoint.os,
        "lstat",
        lambda path: parent_stat if Path(path) == tmp_path else lock_stat,
    )
    monkeypatch.setattr(
        entrypoint,
        "_flock_exclusive_nonblocking",
        lambda descriptor: pytest.fail("flocked in a nonprivate parent"),
    )
    monkeypatch.setattr(entrypoint.os, "close", lambda descriptor: closed.append(descriptor))

    with pytest.raises(RuntimeError, match="sandbox broker lock unavailable"):
        entrypoint.acquire_runtime_lock(socket_path)
    assert closed == [43]


@pytest.mark.parametrize("lock_mode", [0o640, 0o700])
def test_runtime_lock_rejects_existing_lock_unless_mode_is_exactly_0600(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lock_mode: int,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    socket_path = tmp_path / "broker.sock"
    parent_stat = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700,
        st_dev=9,
        st_ino=2,
        st_file_attributes=0,
    )
    lock_stat = SimpleNamespace(
        st_mode=stat.S_IFREG | lock_mode,
        st_dev=9,
        st_ino=4,
        st_nlink=1,
        st_file_attributes=0,
    )
    monkeypatch.setattr(
        entrypoint,
        "_trusted_socket_parent",
        lambda path: (socket_path, entrypoint._identity(parent_stat)),
    )
    monkeypatch.setattr(entrypoint.os, "open", lambda *args: 44)
    monkeypatch.setattr(entrypoint.os, "fstat", lambda descriptor: lock_stat)
    monkeypatch.setattr(
        entrypoint.os,
        "lstat",
        lambda path: parent_stat if Path(path) == tmp_path else lock_stat,
    )
    monkeypatch.setattr(
        entrypoint,
        "_flock_exclusive_nonblocking",
        lambda descriptor: pytest.fail("flocked an incorrectly-mode lock file"),
    )
    monkeypatch.setattr(entrypoint.os, "close", lambda descriptor: None)

    with pytest.raises(RuntimeError, match="sandbox broker lock unavailable"):
        entrypoint.acquire_runtime_lock(socket_path)


def test_lock_failure_never_touches_stale_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    config = _config(tmp_path)
    monkeypatch.setattr(entrypoint.os, "umask", lambda mask: None)
    monkeypatch.setattr(
        entrypoint.BrokerConfig,
        "from_env",
        classmethod(lambda cls: config),
    )
    monkeypatch.setattr(
        entrypoint,
        "acquire_runtime_lock",
        lambda path: (_ for _ in ()).throw(
            RuntimeError("sandbox broker lock unavailable")
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "remove_stale_socket",
        lambda path: pytest.fail("socket touched without owning the lock"),
    )
    with pytest.raises(RuntimeError, match="sandbox broker lock unavailable"):
        entrypoint.main()


def test_main_redacts_configuration_failure_before_filesystem_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    events: list[object] = []
    monkeypatch.setattr(
        entrypoint.os,
        "umask",
        lambda mask: events.append(("umask", mask)),
    )
    monkeypatch.setattr(
        entrypoint.BrokerConfig,
        "from_env",
        classmethod(
            lambda cls: (_ for _ in ()).throw(
                RuntimeError(r"OPEN_SANDBOX_API_KEY=secret C:\runtime\broker.sock")
            )
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "acquire_runtime_lock",
        lambda path: pytest.fail("filesystem touched after invalid configuration"),
    )

    with pytest.raises(
        RuntimeError,
        match="sandbox broker configuration unavailable",
    ) as raised:
        entrypoint.main()
    assert events == [("umask", 0o007)]
    assert "secret" not in str(raised.value).lower()
    assert "runtime" not in str(raised.value).lower()


@pytest.mark.parametrize(
    ("connect_errno", "stale"),
    [
        (None, False),
        (errno.ECONNREFUSED, True),
        (errno.ENOENT, True),
        (errno.EACCES, False),
        (errno.EPERM, False),
        (errno.ETIMEDOUT, False),
    ],
)
def test_unix_socket_probe_deletes_only_explicit_stale_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    connect_errno: int | None,
    stale: bool,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    class FakeSocket:
        def settimeout(self, timeout: float) -> None:
            assert 0 < timeout <= 1

        def connect(self, path: str) -> None:
            del path
            if connect_errno is not None:
                raise OSError(connect_errno, r"sensitive C:\runtime\broker.sock")

        def close(self) -> None:
            return None

    monkeypatch.setattr(entrypoint.socket, "socket", lambda *args: FakeSocket())
    assert entrypoint._socket_stale_candidate(Path("/run/broker.sock")) is stale


def test_quarantine_identity_mismatch_is_never_unlinked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    original = SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o600,
        st_dev=4,
        st_ino=8,
        st_file_attributes=0,
    )
    swapped = SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o600,
        st_dev=4,
        st_ino=9,
        st_file_attributes=0,
    )
    parent = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700,
        st_dev=4,
        st_ino=2,
        st_file_attributes=0,
    )
    quarantine: Path | None = None
    target_reads = 0

    def lstat(path: object) -> object:
        nonlocal target_reads
        if Path(path) == target:
            target_reads += 1
            return original
        if quarantine is not None and Path(path) == quarantine:
            return swapped
        if Path(path) == target.parent:
            return parent
        raise AssertionError

    def replace(source: object, destination: object) -> None:
        nonlocal quarantine
        assert Path(source) == target
        quarantine = Path(destination)

    monkeypatch.setattr(
        entrypoint,
        "_trusted_socket_parent",
        lambda path: (target, entrypoint._identity(parent)),
    )
    monkeypatch.setattr(entrypoint, "_socket_stale_candidate", lambda path: True)
    monkeypatch.setattr(entrypoint.os, "lstat", lstat)
    monkeypatch.setattr(entrypoint.os, "replace", replace)
    monkeypatch.setattr(
        entrypoint.os,
        "unlink",
        lambda path: pytest.fail(f"unlinked mismatched quarantine: {path}"),
    )
    with pytest.raises(RuntimeError, match="stale socket identity changed"):
        entrypoint.remove_stale_socket(target)
    assert target_reads == 2


def test_main_releases_lock_when_uvicorn_fails_and_redacts_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    config = _config(tmp_path)
    released: list[int] = []
    monkeypatch.setattr(entrypoint.os, "umask", lambda mask: None)
    monkeypatch.setattr(
        entrypoint.BrokerConfig,
        "from_env",
        classmethod(lambda cls: config),
    )
    monkeypatch.setattr(entrypoint, "acquire_runtime_lock", lambda path: 51)
    monkeypatch.setattr(entrypoint, "remove_stale_socket", lambda path: None)
    monkeypatch.setattr(entrypoint, "create_app", lambda observed: object())
    monkeypatch.setattr(
        entrypoint,
        "serve_application",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(r"OPEN_SANDBOX_API_KEY=secret C:\runtime\broker.sock")
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "release_runtime_lock",
        lambda descriptor: released.append(descriptor),
    )
    with pytest.raises(RuntimeError, match="sandbox broker server failed") as raised:
        entrypoint.main()
    assert "secret" not in str(raised.value).lower()
    assert str(tmp_path) not in str(raised.value)
    assert released == [51]


@pytest.mark.skipif(os.name != "posix", reason="fcntl requires POSIX")
def test_runtime_lock_excludes_second_real_listener(tmp_path: Path) -> None:
    from scripts.run_sandbox_broker import acquire_runtime_lock, release_runtime_lock

    socket_path = tmp_path / "broker.sock"
    first = acquire_runtime_lock(socket_path)
    try:
        with pytest.raises(RuntimeError, match="sandbox broker lock unavailable"):
            acquire_runtime_lock(socket_path)
    finally:
        release_runtime_lock(first)
    second = acquire_runtime_lock(socket_path)
    release_runtime_lock(second)


@pytest.mark.skipif(os.name != "posix", reason="real Unix sockets require POSIX")
def test_live_unix_listener_is_never_removed(tmp_path: Path) -> None:
    from scripts.run_sandbox_broker import remove_stale_socket

    target = tmp_path / "broker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(target))
    listener.listen(1)
    try:
        with pytest.raises(RuntimeError, match="active broker socket"):
            remove_stale_socket(target)
        assert target.exists()
    finally:
        listener.close()
    remove_stale_socket(target)
