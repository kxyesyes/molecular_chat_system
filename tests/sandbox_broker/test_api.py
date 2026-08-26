"""Contract tests for the standalone sandbox broker HTTP API."""

from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import stat
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

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


class _HoldingSandboxClient:
    def __init__(self) -> None:
        self.create_count = 0
        self.run_count = 0
        self.running = asyncio.Event()

    async def create(self, job_id: str) -> SandboxHandle:
        self.create_count += 1
        return SandboxHandle(f"sandbox-{job_id}", object())

    async def upload_text(self, handle: SandboxHandle, path: str, data: str) -> None:
        del handle, path, data

    async def run(self, handle: SandboxHandle) -> object:
        del handle
        self.run_count += 1
        self.running.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def destroy(self, handle: SandboxHandle) -> None:
        del handle

    async def destroy_by_id(self, sandbox_id: str) -> None:
        del sandbox_id

    async def drain_cleanup(self) -> None:
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

    def __init__(self, *, stop_failure: BaseException | None = None) -> None:
        self.events: list[str] = []
        self.stop_failure = stop_failure
        self.submitted_keys: list[str] = []

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
        ("ligand", "compound.xyz"),
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

    schema = create_app(_config(tmp_path), service=_ApiService()).openapi()
    assert set(schema["paths"]) == {
        "/v1/docking/jobs",
        "/v1/docking/jobs/{job_id}",
        "/v1/docking/jobs/{job_id}/manifest",
        "/v1/docking/jobs/{job_id}/artifacts/{artifact_id}",
        "/v1/docking/jobs/{job_id}/cancel",
        "/healthz",
    }
    serialized = json.dumps(schema, sort_keys=True).lower()
    for forbidden in ('"command"', '"image"', '"mounts"', '"policy"', '"debug"', '"admin"'):
        assert forbidden not in serialized


def test_lifespan_stop_incomplete_is_logged_without_context_and_propagated(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.sandbox_broker.app import create_app

    service = _ApiService(stop_failure=StopIncomplete())
    app = create_app(_config(tmp_path), service=service)
    with pytest.raises(StopIncomplete):
        with TestClient(app):
            pass
    assert service.events == ["recover", "start", "stop"]
    assert "sandbox broker shutdown incomplete" in caplog.text
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
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
    expected = ["recover", "stop"] if failure_stage == "recover" else [
        "recover",
        "start",
        "stop",
    ]
    assert service.events == expected
    assert "sandbox broker startup failed" in caplog.text
    assert "OPEN_SANDBOX_API_KEY" not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_uds_main_sets_umask_before_config_and_uses_only_exact_uds_args(
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
        "remove_stale_socket",
        lambda path: events.append(("stale", path)),
    )
    monkeypatch.setattr(
        entrypoint,
        "create_app",
        lambda observed: events.append(("app", observed)) or application,
    )

    def run(app: object, **kwargs: object) -> None:
        events.append(("uvicorn", app, kwargs))

    monkeypatch.setattr(entrypoint.uvicorn, "run", run)
    entrypoint.main()

    assert events == [
        ("umask", 0o077),
        "config",
        ("stale", config.socket_path),
        ("app", config),
        (
            "uvicorn",
            application,
            {
                "uds": str(config.socket_path),
                "workers": 1,
                "access_log": False,
                "log_level": "info",
                "reload": False,
            },
        ),
    ]


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


def test_stale_socket_cleanup_uses_two_matching_identity_checks_when_mocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_sandbox_broker as entrypoint

    target = tmp_path / "broker.sock"
    real_lstat = entrypoint.os.lstat
    socket_stat = SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o600,
        st_dev=7,
        st_ino=11,
        st_file_attributes=0,
    )
    target_calls = 0
    removed: list[Path] = []

    def lstat(path: object) -> object:
        nonlocal target_calls
        if Path(path) == target:
            target_calls += 1
            return socket_stat
        return real_lstat(path)

    monkeypatch.setattr(entrypoint.os, "lstat", lstat)
    monkeypatch.setattr(
        entrypoint.os,
        "unlink",
        lambda path: removed.append(Path(path)),
    )
    entrypoint.remove_stale_socket(target)
    assert target_calls == 2
    assert removed == [target]


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
    monkeypatch.setattr(
        entrypoint.os,
        "unlink",
        lambda path: pytest.fail(f"unlinked swapped target: {path}"),
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
