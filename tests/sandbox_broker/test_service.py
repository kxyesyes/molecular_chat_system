from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.sandbox_broker.artifacts import ArtifactRegistry
from src.sandbox_broker.config import BrokerConfig
from src.sandbox_broker.models import (
    BrokerErrorCode,
    BrokerJobStatus,
    DockingManifest,
    DockingParameters,
)
from src.sandbox_broker.opensandbox_client import SandboxCommandResult, SandboxHandle
from src.sandbox_broker.service import (
    BrokerFailure,
    PreparedDockingSubmission,
    SandboxBrokerService,
    canonical_submission_sha256,
)
from src.sandbox_broker.store import BrokerStore
from src.sandbox_broker.validation import StagedInput


POSE = "REMARK VINA RESULT: -7.4 0.0 0.0\nMODEL 1\nENDMDL\n"


class FakeSandboxClient:
    def __init__(
        self,
        *,
        valid_result: bool = True,
        block: bool = False,
        result_extras: dict[str, object] | None = None,
    ) -> None:
        self.valid_result = valid_result
        self.block = block
        self.result_extras = result_extras or {}
        self.create_count = 0
        self.run_count = 0
        self.destroy_count = 0
        self.destroyed_ids: list[str] = []
        self.uploads: list[tuple[str, str]] = []
        self.running = asyncio.Event()
        self.release = asyncio.Event()
        self.active_runs = 0
        self.max_active_runs = 0

    async def create(self, job_id: str) -> SandboxHandle:
        self.create_count += 1
        return SandboxHandle(f"sandbox-{job_id}", object())

    async def upload_text(self, handle: SandboxHandle, path: str, data: str) -> None:
        del handle
        self.uploads.append((path, data))

    async def run(self, handle: SandboxHandle) -> SandboxCommandResult:
        del handle
        self.run_count += 1
        self.active_runs += 1
        self.max_active_runs = max(self.max_active_runs, self.active_runs)
        self.running.set()
        try:
            if self.block:
                await self.release.wait()
            return SandboxCommandResult(0, "", "")
        finally:
            self.active_runs -= 1

    async def list_files(
        self, handle: SandboxHandle, path: str, pattern: str
    ) -> list[str]:
        del handle, path, pattern
        return ["/workspace/output/poses/result.pdbqt"]

    def _request_hashes(self) -> tuple[str, str]:
        request = next(data for path, data in self.uploads if path.endswith("request.json"))
        payload = json.loads(request)
        return payload["receptor_sha256"], payload["ligand_sha256"]

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        del handle
        receptor_hash, ligand_hash = self._request_hashes()
        if path == "/workspace/output/result.json":
            payload = {
                    "schema_version": 1,
                    "status": "succeeded",
                    "receptor_sha256": receptor_hash,
                    "ligand_sha256": ligand_hash if self.valid_result else "f" * 64,
                    "pose_count": 1,
                    "best_energy": -7.4,
                    "pose_files": ["poses/result.pdbqt"],
                    "vina_version": "1.2.5",
                    "meeko_version": "0.6.1",
                    "warnings": [],
                }
            payload.update(self.result_extras)
            return json.dumps(payload, sort_keys=True)
        if path == "/workspace/output/poses/result.pdbqt":
            return POSE
        raise AssertionError("unexpected fixed output path")

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroy_count += 1
        self.destroyed_ids.append(handle.sandbox_id)

    async def destroy_by_id(self, sandbox_id: str) -> None:
        self.destroyed_ids.append(sandbox_id)


class FaultSandboxClient(FakeSandboxClient):
    def __init__(
        self,
        stage: str,
        *,
        failure: BaseException | None = None,
    ) -> None:
        super().__init__()
        self.stage = stage
        self.failure = failure or RuntimeError(r"C:\host-secret\token.txt")

    def _raise(self, stage: str) -> None:
        if self.stage == stage:
            raise self.failure

    async def create(self, job_id: str) -> SandboxHandle:
        self._raise("create")
        return await super().create(job_id)

    async def upload_text(self, handle: SandboxHandle, path: str, data: str) -> None:
        self._raise("upload")
        await super().upload_text(handle, path, data)

    async def run(self, handle: SandboxHandle) -> SandboxCommandResult:
        self._raise("run")
        result = await super().run(handle)
        if self.stage == "nonzero":
            return SandboxCommandResult(17, "", "")
        return result

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        self._raise("read")
        return await super().read_text(handle, path)

    async def list_files(
        self, handle: SandboxHandle, path: str, pattern: str
    ) -> list[str]:
        self._raise("list")
        return await super().list_files(handle, path, pattern)

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroy_count += 1
        self._raise("destroy")
        self.destroyed_ids.append(handle.sandbox_id)


class RecoverySandboxClient(FakeSandboxClient):
    def __init__(self, *, destroy_fails: bool = False) -> None:
        super().__init__()
        self.destroy_fails = destroy_fails
        self.recovery_calls = 0

    async def destroy_by_id(self, sandbox_id: str) -> None:
        self.recovery_calls += 1
        await asyncio.sleep(0)
        if self.destroy_fails:
            raise RuntimeError("recovery-secret")
        self.destroyed_ids.append(sandbox_id)


class BlockingDestroySandboxClient(FakeSandboxClient):
    def __init__(self) -> None:
        super().__init__(block=True)
        self.destroy_started = asyncio.Event()
        self.destroy_release = asyncio.Event()

    async def destroy(self, handle: SandboxHandle) -> None:
        del handle
        self.destroy_count += 1
        self.destroy_started.set()
        await self.destroy_release.wait()


def _config(tmp_path: Path) -> BrokerConfig:
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True)
    return BrokerConfig(
        state_root=state_root,
        socket_path=tmp_path / "broker.sock",
        image_uri="medchat-docking",
        image_digest="a" * 64,
        opensandbox_domain="127.0.0.1:8080",
        opensandbox_api_key="runtime-secret-value",
    )


def _prepared(config: BrokerConfig) -> PreparedDockingSubmission:
    staged = config.state_root / "staged" / ("1" * 32)
    staged.mkdir(parents=True, exist_ok=True)
    receptor_bytes = b"ATOM\n"
    ligand_bytes = b"ligand\n"
    (staged / "receptor.pdb").write_bytes(receptor_bytes)
    (staged / "ligand.sdf").write_bytes(ligand_bytes)
    receptor = StagedInput(
        relative_path=f"staged/{'1' * 32}/receptor.pdb",
        size_bytes=len(receptor_bytes),
        sha256=hashlib.sha256(receptor_bytes).hexdigest(),
    )
    ligand = StagedInput(
        relative_path=f"staged/{'1' * 32}/ligand.sdf",
        size_bytes=len(ligand_bytes),
        sha256=hashlib.sha256(ligand_bytes).hexdigest(),
    )
    parameters = DockingParameters(center=[1, 2, 3], size=[20, 20, 20])
    return PreparedDockingSubmission(
        parameters=parameters,
        receptor=receptor,
        ligand=ligand,
        canonical_input_sha256=canonical_submission_sha256(
            parameters, receptor, ligand
        ),
        trace_id="trace-1",
    )


def _service(
    tmp_path: Path,
    sdk: object,
    *,
    queue_capacity: int | None = None,
) -> tuple[SandboxBrokerService, BrokerStore, BrokerConfig]:
    config = _config(tmp_path)
    if queue_capacity is not None:
        object.__setattr__(config, "queue_capacity", queue_capacity)
    store = BrokerStore(config.state_root / "broker.sqlite")
    service = SandboxBrokerService(
        config,
        store,
        sdk,
        ArtifactRegistry(config.state_root, store),
    )
    return service, store, config


def _seed_running(store: BrokerStore, key: str = "idem-recovery") -> object:
    job, _ = store.create_or_get(key, "d" * 64, "trace-recovery")
    store.transition(job.job_id, BrokerJobStatus.PROVISIONING)
    store.attach_sandbox(job.job_id, "sandbox-orphan")
    store.transition(job.job_id, BrokerJobStatus.UPLOADING)
    return store.transition(job.job_id, BrokerJobStatus.RUNNING)


def _set_updated_at(store: BrokerStore, job_id: str, value: float) -> None:
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("UPDATE jobs SET updated_at=? WHERE job_id=?", (value, job_id))


def _job_file(config: BrokerConfig, job_id: str, name: str = "payload") -> Path:
    root = config.state_root / "jobs" / job_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"payload")
    return path


def test_prepared_submission_is_strict_immutable_and_host_path_free(
    tmp_path: Path,
) -> None:
    prepared = _prepared(_config(tmp_path))

    with pytest.raises(FrozenInstanceError):
        prepared.trace_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="prepared submission is invalid") as raised:
        PreparedDockingSubmission(
            parameters=prepared.parameters,
            receptor=StagedInput(
                relative_path=str((tmp_path / "secret-receptor.pdb").resolve()),
                size_bytes=1,
                sha256="b" * 64,
            ),
            ligand=prepared.ligand,
            canonical_input_sha256="c" * 64,
            trace_id="trace-2",
        )
    assert str(tmp_path) not in str(raised.value)


def test_submit_before_start_fails_without_creating_a_job(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = BrokerStore(config.state_root / "broker.sqlite")
    service = SandboxBrokerService(
        config,
        store,
        object(),
        ArtifactRegistry(config.state_root, store),
    )

    async def scenario() -> BrokerFailure:
        with pytest.raises(BrokerFailure) as raised:
            await service.submit(_prepared(config), "idem-1")
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
    assert store.active_jobs() == []


def test_job_runs_once_and_manifest_is_visible_only_after_cleanup(tmp_path: Path) -> None:
    async def scenario() -> tuple[FakeSandboxClient, object, DockingManifest, bytes]:
        sdk = FakeSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-success")
        await service.wait_terminal(job.job_id)
        terminal = service.get_job(job.job_id)
        manifest = service.get_manifest(job.job_id)
        pose = service.read_artifact(job.job_id, manifest.artifacts[0]["artifact_id"])
        await service.stop()
        return sdk, terminal, manifest, pose

    sdk, terminal, manifest, pose = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert terminal.provenance is not None
    assert terminal.provenance.secure_runtime == "gvisor"
    assert terminal.provenance.cleanup_status == "succeeded"
    with pytest.raises(FrozenInstanceError):
        terminal.status = BrokerJobStatus.FAILED  # type: ignore[misc]
    assert isinstance(terminal.warnings, tuple)
    assert sdk.create_count == sdk.run_count == sdk.destroy_count == 1
    assert manifest.job_id == terminal.job_id
    assert manifest.trace_id == "trace-1"
    assert len(manifest.artifacts) == 1
    assert pose == POSE.encode("utf-8")


def test_invalid_scientific_output_fails_without_manifest(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, SandboxBrokerService]:
        sdk = FakeSandboxClient(valid_result=False)
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-invalid")
        await service.wait_terminal(job.job_id)
        terminal = service.get_job(job.job_id)
        await service.stop()
        return terminal, service

    terminal, service = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID.value
    with pytest.raises(KeyError):
        service.get_manifest(terminal.job_id)


def test_twenty_concurrent_same_idempotency_submissions_run_once(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], FakeSandboxClient]:
        sdk = FakeSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        prepared = _prepared(config)
        await service.start()
        jobs = await asyncio.gather(
            *(service.submit(prepared, "idem-concurrent") for _ in range(20))
        )
        await service.wait_terminal(jobs[0].job_id)
        await service.stop()
        return [job.job_id for job in jobs], sdk

    job_ids, sdk = asyncio.run(scenario())
    assert len(set(job_ids)) == 1
    assert sdk.create_count == sdk.run_count == sdk.destroy_count == 1


def test_queue_saturation_fails_new_job_and_keeps_one_consumer(tmp_path: Path) -> None:
    async def scenario() -> tuple[FakeSandboxClient, object]:
        sdk = FakeSandboxClient(block=True)
        service, store, config = _service(tmp_path, sdk, queue_capacity=1)
        await service.start()
        first = await service.submit(_prepared(config), "idem-first")
        await sdk.running.wait()
        second = await service.submit(_prepared(config), "idem-second")
        with pytest.raises(BrokerFailure) as raised:
            await service.submit(_prepared(config), "idem-saturated")
        saturated = store.get_by_idempotency("idem-saturated")
        assert saturated is not None
        assert saturated.status is BrokerJobStatus.FAILED
        assert saturated.error_code == BrokerErrorCode.QUEUE_SATURATED.value
        sdk.release.set()
        await service.wait_terminal(first.job_id)
        await service.wait_terminal(second.job_id)
        await service.stop()
        return sdk, raised.value

    sdk, failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.QUEUE_SATURATED
    assert sdk.max_active_runs == 1


def test_queued_and_running_cancellation_are_idempotent_and_cleanup(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[FakeSandboxClient, object, object]:
        sdk = FakeSandboxClient(block=True)
        service, _, config = _service(tmp_path, sdk, queue_capacity=1)
        await service.start()
        running = await service.submit(_prepared(config), "idem-running")
        await sdk.running.wait()
        queued = await service.submit(_prepared(config), "idem-queued")
        await service.cancel(queued.job_id)
        await service.cancel(queued.job_id)
        await service.cancel(running.job_id)
        await service.cancel(running.job_id)
        running_terminal = await service.wait_terminal(running.job_id)
        queued_terminal = await service.wait_terminal(queued.job_id)
        await service.stop()
        return sdk, running_terminal, queued_terminal

    sdk, running, queued = asyncio.run(scenario())
    assert running.status is BrokerJobStatus.CANCELLED
    assert queued.status is BrokerJobStatus.CANCELLED
    assert sdk.create_count == 1
    assert sdk.destroy_count == 1


def test_execution_timeout_and_command_failure_are_stable_and_cleanup(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, object, FakeSandboxClient, FaultSandboxClient]:
        timeout_sdk = FakeSandboxClient(block=True)
        timeout_service, _, timeout_config = _service(tmp_path / "timeout", timeout_sdk)
        object.__setattr__(timeout_config, "execution_timeout_seconds", 0.01)
        await timeout_service.start()
        timeout_job = await timeout_service.submit(_prepared(timeout_config), "idem-timeout")
        timeout_terminal = await timeout_service.wait_terminal(timeout_job.job_id)
        await timeout_service.stop()

        command_sdk = FaultSandboxClient("nonzero")
        command_service, _, command_config = _service(tmp_path / "command", command_sdk)
        await command_service.start()
        command_job = await command_service.submit(_prepared(command_config), "idem-command")
        command_terminal = await command_service.wait_terminal(command_job.job_id)
        await command_service.stop()
        return timeout_terminal, command_terminal, timeout_sdk, command_sdk

    timeout, command, timeout_sdk, command_sdk = asyncio.run(scenario())
    assert timeout.error_code == BrokerErrorCode.EXECUTION_TIMEOUT.value
    assert command.error_code == BrokerErrorCode.COMMAND_FAILED.value
    assert timeout_sdk.destroy_count == command_sdk.destroy_count == 1


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("create", BrokerErrorCode.PROVISIONING_FAILED),
        ("upload", BrokerErrorCode.UPLOAD_FAILED),
        ("run", BrokerErrorCode.OPENSANDBOX_UNAVAILABLE),
        ("read", BrokerErrorCode.ARTIFACT_FAILED),
        ("list", BrokerErrorCode.ARTIFACT_FAILED),
    ],
)
def test_each_stage_exception_and_base_exception_cleans_and_redacts(
    tmp_path: Path,
    stage: str,
    expected: BrokerErrorCode,
) -> None:
    class FatalProbe(BaseException):
        pass

    async def scenario() -> tuple[object, FaultSandboxClient]:
        sdk = FaultSandboxClient(stage, failure=FatalProbe("secret-base-exception"))
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), f"idem-{stage}")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == expected.value
    assert "secret" not in repr(terminal)
    assert sdk.destroy_count == (0 if stage == "create" else 1)


def test_cleanup_failure_keeps_partial_artifacts_but_exposes_no_manifest(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, SandboxBrokerService, BrokerStore]:
        sdk = FaultSandboxClient("destroy")
        service, store, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-cleanup")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, service, store

    terminal, service, store = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert terminal.cleanup_status == "failed"
    assert "cleanup_failed" in terminal.warnings
    assert len(store.list_artifacts(terminal.job_id)) == 1
    with pytest.raises(KeyError):
        service.get_manifest(terminal.job_id)
    for artifact in store.list_artifacts(terminal.job_id):
        with pytest.raises(KeyError):
            service.read_artifact(terminal.job_id, artifact.artifact_id)


@pytest.mark.parametrize("flag", ["demo_mode", "fallback_used"])
def test_demo_or_fallback_markers_can_never_reach_success(
    tmp_path: Path, flag: str
) -> None:
    async def scenario() -> object:
        sdk = FakeSandboxClient(result_extras={flag: True})
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), f"idem-{flag}")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID.value


@pytest.mark.parametrize("destroy_fails", [False, True])
def test_concurrent_recovery_destroys_orphan_once_and_fails_closed(
    tmp_path: Path, destroy_fails: bool
) -> None:
    async def scenario() -> tuple[object, RecoverySandboxClient]:
        sdk = RecoverySandboxClient(destroy_fails=destroy_fails)
        service, store, _ = _service(tmp_path, sdk)
        seeded = _seed_running(store)
        await asyncio.gather(service.recover(), service.recover(), service.recover())
        terminal = store.get(seeded.job_id)
        assert terminal is not None
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert sdk.recovery_calls == 1
    assert terminal.status is BrokerJobStatus.FAILED
    if destroy_fails:
        assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
        assert terminal.cleanup_status == "failed"
    else:
        assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
        assert terminal.cleanup_status == "succeeded"


def test_manifest_survives_service_reopen_and_tamper_and_cross_job_fail_closed(
    tmp_path: Path,
) -> None:
    async def create_success() -> tuple[BrokerStore, BrokerConfig, object]:
        sdk = FakeSandboxClient()
        service, store, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-persisted")
        await service.wait_terminal(job.job_id)
        await service.stop()
        return store, config, job

    store, config, job = asyncio.run(create_success())
    reopened = SandboxBrokerService(
        config,
        store,
        FakeSandboxClient(),
        ArtifactRegistry(config.state_root, store),
    )
    manifest = reopened.get_manifest(job.job_id)
    assert manifest.job_id == job.job_id
    other, _ = store.create_or_get("idem-other", "e" * 64, "trace-other")
    with pytest.raises(KeyError):
        reopened.read_artifact(other.job_id, manifest.artifacts[0]["artifact_id"])

    manifest_record = next(
        item for item in store.list_artifacts(job.job_id) if item.media_type == "application/json"
    )
    manifest_path = config.state_root.joinpath(*manifest_record.relative_path.split("/"))
    manifest_path.write_bytes(b"{}")
    with pytest.raises(KeyError):
        reopened.get_manifest(job.job_id)


def test_retention_expires_active_keeps_terminal_metadata_and_purges_old_audit(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[BrokerStore, BrokerConfig, tuple[str, str, str, str]]:
        service, store, config = _service(tmp_path, FakeSandboxClient())
        active, _ = store.create_or_get("idem-active-old", "1" * 64, "trace-active")
        terminal, _ = store.create_or_get("idem-terminal-old", "2" * 64, "trace-terminal")
        terminal = store.fail(
            terminal.job_id,
            BrokerErrorCode.INVALID_INPUT,
            cleanup_status="succeeded",
        )
        audit, _ = store.create_or_get("idem-audit-old", "3" * 64, "trace-audit")
        audit = store.fail(
            audit.job_id,
            BrokerErrorCode.INVALID_INPUT,
            cleanup_status="succeeded",
        )
        fresh, _ = store.create_or_get("idem-fresh", "4" * 64, "trace-fresh")
        for record in (active, terminal, audit, fresh):
            _job_file(config, record.job_id)
        now = 2_000_000_000.0
        store._now = lambda: now  # type: ignore[method-assign]
        _set_updated_at(store, active.job_id, now - 2 * 86_400)
        _set_updated_at(store, terminal.job_id, now - 2 * 86_400)
        _set_updated_at(store, audit.job_id, now - 31 * 86_400)
        _set_updated_at(store, fresh.job_id, now - 60)

        await service.expire(now)
        await service.expire(now)
        return store, config, (active.job_id, terminal.job_id, audit.job_id, fresh.job_id)

    store, config, ids = asyncio.run(scenario())
    active_id, terminal_id, audit_id, fresh_id = ids
    active = store.get(active_id)
    terminal = store.get(terminal_id)
    assert active is not None and active.status is BrokerJobStatus.EXPIRED
    assert terminal is not None and terminal.status is BrokerJobStatus.FAILED
    assert store.get(audit_id) is None
    assert store.get(fresh_id) is not None
    assert not (config.state_root / "jobs" / active_id).exists()
    assert not (config.state_root / "jobs" / terminal_id).exists()
    assert not (config.state_root / "jobs" / audit_id).exists()
    assert (config.state_root / "jobs" / fresh_id / "payload").read_bytes() == b"payload"


def test_retention_rejects_symlink_hardlink_and_concurrent_sibling_escape(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[BrokerStore, BrokerConfig, list[str], Path, Path]:
        service, store, config = _service(tmp_path, FakeSandboxClient())
        now = 2_000_000_000.0
        jobs = []
        for index, kind in enumerate(("symlink", "hardlink", "root-link"), start=5):
            record, _ = store.create_or_get(
                f"idem-{kind}", str(index) * 64, f"trace-{kind}"
            )
            jobs.append(record)
            _set_updated_at(store, record.job_id, now - 2 * 86_400)
        outside = tmp_path / "outside-secret"
        outside.mkdir()
        outside_file = outside / "keep.txt"
        outside_file.write_bytes(b"keep")
        sibling = config.state_root / "jobs" / ("f" * 32)
        sibling.mkdir(parents=True)
        (sibling / "keep.txt").write_bytes(b"sibling")

        symlink_root = config.state_root / "jobs" / jobs[0].job_id
        symlink_root.mkdir()
        try:
            (symlink_root / "escape").symlink_to(outside_file)
        except (NotImplementedError, OSError):
            os.link(outside_file, symlink_root / "escape-hardlink")

        hardlink_root = config.state_root / "jobs" / jobs[1].job_id
        hardlink_root.mkdir()
        os.link(outside_file, hardlink_root / "escape-hardlink")

        root_link = config.state_root / "jobs" / jobs[2].job_id
        try:
            root_link.symlink_to(sibling, target_is_directory=True)
        except (NotImplementedError, OSError):
            root_link.mkdir()
            os.link(outside_file, root_link / "escape-hardlink")

        await asyncio.gather(service.expire(now), service.expire(now), service.expire(now))
        return store, config, [item.job_id for item in jobs], outside_file, sibling

    store, config, job_ids, outside_file, sibling = asyncio.run(scenario())
    assert outside_file.read_bytes() == b"keep"
    assert (sibling / "keep.txt").read_bytes() == b"sibling"
    for job_id in job_ids:
        record = store.get(job_id)
        assert record is not None and record.status is BrokerJobStatus.QUEUED
        assert (config.state_root / "jobs" / job_id).exists()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1.0, True, "1"])
def test_retention_requires_a_finite_epoch(tmp_path: Path, invalid: object) -> None:
    async def scenario() -> None:
        service, _, _ = _service(tmp_path, FakeSandboxClient())
        with pytest.raises(ValueError, match="finite epoch"):
            await service.expire(invalid)  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_start_stop_recover_are_idempotent_and_leave_no_broker_tasks(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, list[str]]:
        service, _, _ = _service(tmp_path, FakeSandboxClient())
        await asyncio.gather(service.start(), service.start(), service.start())
        consumers = [
            task
            for task in asyncio.all_tasks()
            if task.get_name() == "sandbox-broker-consumer" and not task.done()
        ]
        await asyncio.gather(service.recover(), service.recover(), service.recover())
        await asyncio.gather(service.stop(), service.stop(), service.stop())
        await asyncio.sleep(0)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-")
            and not task.done()
        ]
        return len(consumers), leaked

    consumers, leaked = asyncio.run(scenario())
    assert consumers == 1
    assert leaked == []


def test_stop_cancels_running_job_only_after_successful_cleanup(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, FakeSandboxClient, list[str]]:
        sdk = FakeSandboxClient(block=True)
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-stop")
        await sdk.running.wait()
        await service.stop()
        terminal = service.get_job(job.job_id)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-")
            and not task.done()
        ]
        return terminal, sdk, leaked

    terminal, sdk, leaked = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.CANCELLED
    assert terminal.cleanup_status == "succeeded"
    assert sdk.destroy_count == 1
    assert leaked == []


def test_bounded_stop_marks_unfinished_cleanup_failed_and_leaks_no_tasks(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[bool, object, list[str]]:
        sdk = BlockingDestroySandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._stop_timeout_seconds = 0.02
        await service.start()
        job = await service.submit(_prepared(config), "idem-stop-timeout")
        await sdk.running.wait()
        stop_task = asyncio.create_task(service.stop())
        await sdk.destroy_started.wait()
        bounded = True
        try:
            await asyncio.wait_for(asyncio.shield(stop_task), timeout=0.2)
        except asyncio.TimeoutError:
            bounded = False
        finally:
            sdk.destroy_release.set()
            await stop_task
        await asyncio.sleep(0)
        terminal = service.get_job(job.job_id)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-")
            and not task.done()
        ]
        return bounded, terminal, leaked

    bounded, terminal, leaked = asyncio.run(scenario())
    assert bounded is True
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert terminal.cleanup_status == "failed"
    assert leaked == []


def test_persisted_manifest_contains_final_cleanup_and_internal_id_is_hidden(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[SandboxBrokerService, BrokerStore, object]:
        service, store, config = _service(tmp_path, FakeSandboxClient())
        await service.start()
        job = await service.submit(_prepared(config), "idem-final-manifest")
        await service.wait_terminal(job.job_id)
        await service.stop()
        return service, store, job

    service, store, job = asyncio.run(scenario())
    manifest_record = next(
        item for item in store.list_artifacts(job.job_id) if item.media_type == "application/json"
    )
    payload = json.loads(
        service.artifact_registry.read_registered(
            job.job_id, manifest_record.artifact_id, 1024 * 1024
        )
    )
    assert payload["provenance"]["cleanup_status"] == "succeeded"
    with pytest.raises(KeyError):
        service.read_artifact(job.job_id, manifest_record.artifact_id)


def test_cleanup_failure_overrides_invalid_science_with_cleanup_failed(
    tmp_path: Path,
) -> None:
    class InvalidAndDestroyFail(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__(valid_result=False)

        async def destroy(self, handle: SandboxHandle) -> None:
            del handle
            self.destroy_count += 1
            raise RuntimeError("cleanup-secret")

    async def scenario() -> object:
        service, _, config = _service(tmp_path, InvalidAndDestroyFail())
        await service.start()
        job = await service.submit(_prepared(config), "idem-invalid-cleanup")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert terminal.cleanup_status == "failed"
    assert "cleanup_failed" in terminal.warnings


def test_recovery_retries_a_persisted_failed_cleanup_before_terminalizing(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, RecoverySandboxClient]:
        sdk = RecoverySandboxClient()
        service, store, _ = _service(tmp_path, sdk)
        running = _seed_running(store, key="idem-retry-cleanup")
        store.record_cleanup(running.job_id, "failed")
        await service.recover()
        terminal = store.get(running.job_id)
        assert terminal is not None
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert sdk.recovery_calls == 1
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert terminal.cleanup_status == "succeeded"


def test_untrusted_warning_with_host_path_fails_scientific_gate_without_leak(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, SandboxBrokerService]:
        sdk = FakeSandboxClient(result_extras={"warnings": [r"C:\secret\pose.pdbqt"]})
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-warning-path")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, service

    terminal, service = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID.value
    assert terminal.warnings == ()
    assert "secret" not in repr(terminal)
    with pytest.raises(KeyError):
        service.get_manifest(terminal.job_id)


def test_complete_failure_falls_back_to_terminal_artifact_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> object:
        service, store, config = _service(tmp_path, FakeSandboxClient())

        def fail_complete(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise RuntimeError("database-secret")

        monkeypatch.setattr(store, "complete", fail_complete)
        await service.start()
        job = await service.submit(_prepared(config), "idem-complete-failure")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.ARTIFACT_FAILED.value
    assert "secret" not in repr(terminal)


def test_get_manifest_maps_corrupt_job_json_to_key_error(
    tmp_path: Path,
) -> None:
    async def create_success() -> tuple[SandboxBrokerService, BrokerStore, str]:
        service, store, config = _service(tmp_path, FakeSandboxClient())
        await service.start()
        job = await service.submit(_prepared(config), "idem-corrupt-manifest")
        await service.wait_terminal(job.job_id)
        await service.stop()
        return service, store, job.job_id

    service, store, job_id = asyncio.run(create_success())
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE jobs SET warnings_json=? WHERE job_id=?",
            ("{broken", job_id),
        )
    with pytest.raises(KeyError, match="manifest not found"):
        service.get_manifest(job_id)


def test_retention_does_not_repeat_successful_cleanup_when_files_are_unsafe(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, RecoverySandboxClient, Path]:
        sdk = RecoverySandboxClient()
        service, store, config = _service(tmp_path, sdk)
        running = _seed_running(store, key="idem-retention-cleanup")
        now = 2_000_000_000.0
        _set_updated_at(store, running.job_id, now - 2 * 86_400)
        outside = tmp_path / "outside-retention"
        outside.write_bytes(b"keep")
        job_root = config.state_root / "jobs" / running.job_id
        job_root.mkdir(parents=True)
        os.link(outside, job_root / "unsafe-hardlink")

        await service.expire(now)
        await service.expire(now)
        current = store.get(running.job_id)
        assert current is not None
        return current, sdk, outside

    current, sdk, outside = asyncio.run(scenario())
    assert sdk.recovery_calls == 1
    assert current.status is BrokerJobStatus.RUNNING
    assert current.cleanup_status == "succeeded"
    assert outside.read_bytes() == b"keep"


def test_submit_maps_different_hash_idempotency_conflict_without_second_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[BrokerFailure, FakeSandboxClient]:
        sdk = FakeSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        prepared = _prepared(config)
        conflicting_ligand = StagedInput(
            relative_path=prepared.ligand.relative_path,
            size_bytes=prepared.ligand.size_bytes,
            sha256="f" * 64,
        )
        conflicting = PreparedDockingSubmission(
            parameters=prepared.parameters,
            receptor=prepared.receptor,
            ligand=conflicting_ligand,
            canonical_input_sha256=canonical_submission_sha256(
                prepared.parameters, prepared.receptor, conflicting_ligand
            ),
            trace_id="trace-conflict",
        )
        await service.start()
        first = await service.submit(prepared, "idem-conflict")
        with pytest.raises(BrokerFailure) as raised:
            await service.submit(conflicting, "idem-conflict")
        await service.wait_terminal(first.job_id)
        await service.stop()
        return raised.value, sdk

    failure, sdk = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.IDEMPOTENCY_CONFLICT
    assert sdk.create_count == sdk.run_count == 1


def test_recovery_without_sandbox_fails_unavailable_without_sdk_cleanup(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, RecoverySandboxClient]:
        sdk = RecoverySandboxClient()
        service, store, _ = _service(tmp_path, sdk)
        job, _ = store.create_or_get("idem-no-sandbox", "9" * 64, "trace-no-sandbox")
        store.transition(job.job_id, BrokerJobStatus.PROVISIONING)
        await service.recover()
        terminal = store.get(job.job_id)
        assert terminal is not None
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert terminal.cleanup_status == "succeeded"
    assert sdk.recovery_calls == 0


def test_cancel_completion_race_has_exactly_one_legal_terminal_transition(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, BrokerStore]:
        sdk = FakeSandboxClient(block=True)
        service, store, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-race")
        await sdk.running.wait()

        async def release() -> None:
            await asyncio.sleep(0)
            sdk.release.set()

        await asyncio.gather(release(), service.cancel(job.job_id))
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, store

    terminal, store = asyncio.run(scenario())
    assert terminal.status in {BrokerJobStatus.SUCCEEDED, BrokerJobStatus.CANCELLED}
    with sqlite3.connect(store.db_path) as connection:
        terminal_count = connection.execute(
            "SELECT COUNT(*) FROM transitions WHERE job_id=? AND status IN (?, ?, ?, ?)",
            (
                terminal.job_id,
                BrokerJobStatus.SUCCEEDED.value,
                BrokerJobStatus.FAILED.value,
                BrokerJobStatus.CANCELLED.value,
                BrokerJobStatus.EXPIRED.value,
            ),
        ).fetchone()[0]
    assert terminal_count == 1
