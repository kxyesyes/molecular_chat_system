from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import src.sandbox_broker.service as service_module
from src.sandbox_broker.artifacts import ArtifactRegistry
from src.sandbox_broker.config import BrokerConfig
from src.sandbox_broker.models import (
    TERMINAL_STATUSES,
    BrokerErrorCode,
    BrokerJobStatus,
    DockingManifest,
    DockingParameters,
)
from src.sandbox_broker.opensandbox_client import (
    OpenSandboxClient,
    SandboxCommandResult,
    SandboxCreateError,
    SandboxDestroyError,
    SandboxHandle,
    SandboxProtocolError,
)
from src.sandbox_broker.resilience import ControlPlaneCircuitBreaker
from src.sandbox_broker.service import (
    BrokerFailure,
    PreparedDockingSubmission,
    SandboxBrokerService,
    StopIncomplete,
    canonical_submission_sha256,
)
from src.sandbox_broker.store import BrokerStore
from src.sandbox_broker.telemetry import BrokerTelemetry, FailureClass
from src.sandbox_broker.validation import StagedInput


ROOT = Path(__file__).parents[2]
RUNNER = ROOT / "deployment" / "opensandbox" / "run_docking.py"
POSE = "REMARK VINA RESULT: -7.4 0.0 0.0\nMODEL 1\nENDMDL\n"
MULTI_MODE_POSE = (
    "REMARK VINA RESULT: -8.1 0.0 0.0\n"
    "MODEL 1\nENDMDL\n"
    "REMARK VINA RESULT: -7.2 1.0 1.5\n"
    "MODEL 2\nENDMDL\n"
)
VALID_MOL = b"""ethanol
  MedChat        2D

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    2.5981    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""
VALID_SDF = VALID_MOL + b"$$$$\n"


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
        self.uploads: list[tuple[str, str | bytes]] = []
        self.running = asyncio.Event()
        self.release = asyncio.Event()
        self.active_runs = 0
        self.max_active_runs = 0

    async def create(self, job_id: str) -> SandboxHandle:
        self.create_count += 1
        return SandboxHandle(f"sandbox-{job_id}", object())

    async def upload_text(
        self, handle: SandboxHandle, path: str, data: str | bytes
    ) -> None:
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
        assert isinstance(request, str)
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

    async def drain_cleanup(self) -> None:
        return None


class UnknownIdentitySandboxClient(FakeSandboxClient):
    async def create(self, job_id: str) -> SandboxHandle:
        del job_id
        self.create_count += 1
        raise SandboxProtocolError(
            "the sandbox service returned an invalid identifier",
            operation="create",
            cleanup_confirmed=False,
        )


class MultiModeSandboxClient(FakeSandboxClient):
    def __init__(self) -> None:
        super().__init__(result_extras={"pose_count": 2, "best_energy": -8.1})

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        if path == "/workspace/output/poses/result.pdbqt":
            return MULTI_MODE_POSE
        return await super().read_text(handle, path)


class _UploadRecordingFactory:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str | bytes]] = []

    async def upload_text(self, raw: object, path: str, data: str | bytes) -> None:
        del raw
        self.uploads.append((path, data))


class AdapterUploadSandboxClient(FakeSandboxClient):
    """Run service uploads through the production adapter's fixed allowlist."""

    def __init__(self, config: BrokerConfig) -> None:
        super().__init__()
        self.factory = _UploadRecordingFactory()
        self.adapter = OpenSandboxClient(config, sandbox_factory=self.factory)
        self.uploads = self.factory.uploads

    async def upload_text(
        self, handle: SandboxHandle, path: str, data: str | bytes
    ) -> None:
        await self.adapter.upload_text(handle, path, data)


def _run_uploaded_request_through_wrapper(
    tmp_path: Path, uploads: list[tuple[str, str | bytes]]
) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("service_wrapper_contract", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    input_root = tmp_path / "wrapper-input"
    output_root = tmp_path / "wrapper-output"
    input_root.mkdir(mode=0o700)
    output_root.mkdir(mode=0o700)
    module.INPUT_ROOT = input_root
    module.OUTPUT_ROOT = output_root
    for remote_path, data in uploads:
        destination = input_root / Path(remote_path).name
        destination.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        destination.chmod(0o600)

    class FakeProcess:
        next_pid = 30000

        def __init__(self, argv: list[str], **kwargs: object) -> None:
            del kwargs
            self.argv = list(argv)
            self.pid = FakeProcess.next_pid
            FakeProcess.next_pid += 1
            self.returncode: int | None = None
            if self.argv[0] == "mk_prepare_receptor.py":
                Path(self.argv[4]).write_text("RECEPTOR PDBQT\n", encoding="ascii")
            elif self.argv[0] == "obabel":
                Path(self.argv[self.argv.index("-O") + 1]).write_bytes(VALID_SDF)
            elif self.argv[0] == "mk_prepare_ligand.py":
                Path(self.argv[4]).write_text("LIGAND PDBQT\n", encoding="ascii")
            elif self.argv[0] == "vina":
                Path(self.argv[-1]).write_text(POSE, encoding="ascii")
            else:  # pragma: no cover - exact argv failures should expose the tool
                raise AssertionError(self.argv)

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.returncode = 0
            return 0

        def poll(self) -> int | None:
            return self.returncode

    original_popen = module.subprocess.Popen
    original_version = module.metadata.version
    try:
        module.subprocess.Popen = FakeProcess
        module.metadata.version = lambda name: {
            "vina": "1.2.5",
            "meeko": "0.7.1",
        }[name]
        assert module.main(
            [
                "--request",
                "/workspace/input/request.json",
                "--output",
                "/workspace/output",
            ]
        ) == 0
    finally:
        module.subprocess.Popen = original_popen
        module.metadata.version = original_version
    return json.loads((output_root / "result.json").read_text(encoding="ascii"))


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


class StructuredFailureSandboxClient(FaultSandboxClient):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__("nonzero")
        self.payload = payload
        self.read_paths: list[str] = []

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        del handle
        self.read_paths.append(path)
        return json.dumps(self.payload, sort_keys=True)


class BlockingFailureReadSandboxClient(FaultSandboxClient):
    def __init__(self) -> None:
        super().__init__("nonzero")
        self.read_started = asyncio.Event()
        self.read_release = asyncio.Event()
        self.read_cancellations = 0

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        del handle, path
        self.read_started.set()
        try:
            await self.read_release.wait()
        except asyncio.CancelledError:
            self.read_cancellations += 1
            raise
        return ""


class ResistantFailureReadSandboxClient(BlockingFailureReadSandboxClient):
    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        del handle, path
        self.read_started.set()
        while not self.read_release.is_set():
            try:
                await self.read_release.wait()
            except asyncio.CancelledError:
                self.read_cancellations += 1
        return ""


class BlockingValidationSandboxClient(FakeSandboxClient):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = asyncio.Event()
        self.read_release = asyncio.Event()
        self.read_cancellations = 0

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        self.read_started.set()
        try:
            await self.read_release.wait()
        except asyncio.CancelledError:
            self.read_cancellations += 1
            raise
        return await super().read_text(handle, path)


class ResistantValidationSandboxClient(BlockingValidationSandboxClient):
    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        self.read_started.set()
        while not self.read_release.is_set():
            try:
                await self.read_release.wait()
            except asyncio.CancelledError:
                self.read_cancellations += 1
        return await FakeSandboxClient.read_text(self, handle, path)


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
        self.destroy_cancelled = False

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroy_count += 1
        self.destroy_started.set()
        try:
            await self.destroy_release.wait()
        except asyncio.CancelledError:
            self.destroy_cancelled = True
            raise
        self.destroyed_ids.append(handle.sandbox_id)


class BlockingCreateSandboxClient(FakeSandboxClient):
    def __init__(self, *, failure: BaseException | None = None) -> None:
        super().__init__()
        self.failure = failure
        self.remote_live = asyncio.Event()
        self.create_release = asyncio.Event()
        self.live_ids: set[str] = set()
        self.create_cancelled = False

    async def create(self, job_id: str) -> SandboxHandle:
        self.create_count += 1
        sandbox_id = f"sandbox-{job_id}"
        self.live_ids.add(sandbox_id)
        self.remote_live.set()
        try:
            await self.create_release.wait()
        except asyncio.CancelledError:
            self.create_cancelled = True
            raise
        if self.failure is not None:
            self.live_ids.remove(sandbox_id)
            raise self.failure
        return SandboxHandle(sandbox_id, object())

    async def destroy(self, handle: SandboxHandle) -> None:
        await super().destroy(handle)
        self.live_ids.remove(handle.sandbox_id)


class _AdapterLifecycleRaw:
    def __init__(self, sandbox_id: str) -> None:
        self.id = sandbox_id


class AdapterLifecycleFactory:
    def __init__(self) -> None:
        self.remote_live = asyncio.Event()
        self.create_release = asyncio.Event()
        self.live_ids: set[str] = set()
        self.destroyed_ids: list[str] = []
        self.create_cancelled = False

    async def create(self, **policy: object) -> _AdapterLifecycleRaw:
        metadata = policy["metadata"]
        assert isinstance(metadata, dict)
        sandbox_id = f"sandbox-{metadata['medchat.job_id']}"
        raw = _AdapterLifecycleRaw(sandbox_id)
        self.live_ids.add(sandbox_id)
        self.remote_live.set()
        try:
            await self.create_release.wait()
        except asyncio.CancelledError:
            self.create_cancelled = True
            raise
        return raw

    async def destroy(self, raw: _AdapterLifecycleRaw) -> None:
        self.live_ids.remove(raw.id)
        self.destroyed_ids.append(raw.id)

    async def destroy_by_id(self, sandbox_id: str) -> None:
        self.live_ids.remove(sandbox_id)
        self.destroyed_ids.append(sandbox_id)


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
    ligand_bytes = VALID_SDF
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


def _prepared_with_suffixes(
    config: BrokerConfig,
    receptor_suffix: str,
    ligand_suffix: str,
) -> PreparedDockingSubmission:
    staged = config.state_root / "staged" / ("2" * 32)
    staged.mkdir(parents=True, exist_ok=True)
    receptor_bytes = b"ATOM\n"
    ligand_bytes = VALID_SDF if ligand_suffix == ".sdf" else VALID_MOL
    receptor_name = f"receptor{receptor_suffix}"
    ligand_name = f"ligand{ligand_suffix}"
    (staged / receptor_name).write_bytes(receptor_bytes)
    (staged / ligand_name).write_bytes(ligand_bytes)
    receptor = StagedInput(
        relative_path=f"staged/{'2' * 32}/{receptor_name}",
        size_bytes=len(receptor_bytes),
        sha256=hashlib.sha256(receptor_bytes).hexdigest(),
    )
    ligand = StagedInput(
        relative_path=f"staged/{'2' * 32}/{ligand_name}",
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
        trace_id="trace-suffixes",
    )


def _forged_prepared_with_suffixes(
    config: BrokerConfig,
    receptor_suffix: str,
    ligand_suffix: str,
) -> PreparedDockingSubmission:
    valid = _prepared(config)
    forged = object.__new__(PreparedDockingSubmission)
    object.__setattr__(forged, "parameters", valid.parameters)
    object.__setattr__(
        forged,
        "receptor",
        StagedInput(
            relative_path=f"staged/{'3' * 32}/receptor{receptor_suffix}",
            size_bytes=5,
            sha256="a" * 64,
        ),
    )
    object.__setattr__(
        forged,
        "ligand",
        StagedInput(
            relative_path=f"staged/{'3' * 32}/ligand{ligand_suffix}",
            size_bytes=7,
            sha256="b" * 64,
        ),
    )
    object.__setattr__(forged, "canonical_input_sha256", "c" * 64)
    object.__setattr__(forged, "trace_id", "trace-forged-suffix")
    return forged


def _service(
    tmp_path: Path,
    sdk: object,
    *,
    queue_capacity: int | None = None,
    telemetry: BrokerTelemetry | None = None,
    circuit_breaker: ControlPlaneCircuitBreaker | None = None,
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
        telemetry=telemetry,
        circuit_breaker=circuit_breaker,
    )
    return service, store, config


def test_unknown_remote_identity_never_records_cleanup_success(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        sdk = UnknownIdentitySandboxClient()
        service, _store, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-unknown-identity")
        terminal = await service.wait_terminal(job.job_id, timeout=2.0)
        await service.stop(timeout=2.0)

        assert sdk.create_count == 1
        assert terminal.status is BrokerJobStatus.FAILED
        assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
        assert terminal.cleanup_status == "failed"
        assert service_module._REMOTE_AUTO_EXPIRY_WARNING in terminal.warnings

    asyncio.run(scenario())


class RecordingTelemetry(BrokerTelemetry):
    def __init__(self) -> None:
        super().__init__()
        self.runtime_updates: list[dict[str, object]] = []

    def update_runtime_gauges(self, **values: object) -> None:
        self.runtime_updates.append(dict(values))
        super().update_runtime_gauges(**values)  # type: ignore[arg-type]


class BaseExceptionTelemetry(BrokerTelemetry):
    @staticmethod
    def _fail() -> None:
        raise KeyboardInterrupt("telemetry-sensitive-detail")

    def emit(self, event: object) -> None:
        del event
        self._fail()

    def start_phase(self, **values: object) -> object:
        del values
        self._fail()

    def finish_phase(self, token: object, **values: object) -> object:
        del token, values
        self._fail()

    def record_control_plane_failure(self, **values: object) -> None:
        del values
        self._fail()

    def record_retry(self, operation: str, outcome: str) -> None:
        del operation, outcome
        self._fail()

    def record_terminal(
        self,
        status: str,
        failure_class: FailureClass = FailureClass.NONE,
    ) -> None:
        del status, failure_class
        self._fail()

    def update_runtime_gauges(self, **values: object) -> None:
        del values
        self._fail()


class TrackingCircuitBreaker(ControlPlaneCircuitBreaker):
    def __init__(self) -> None:
        super().__init__()
        self.successes = 0
        self.failures: list[FailureClass] = []
        self.releases = 0
        self.on_acquire: object | None = None

    def acquire(self) -> object | None:
        permit = super().acquire()
        callback = self.on_acquire
        if permit is not None and callable(callback):
            callback()
        return permit

    def record_success(self, permit: object) -> None:
        self.successes += 1
        super().record_success(permit)  # type: ignore[arg-type]

    def record_failure(
        self,
        permit: object,
        failure_class: FailureClass,
    ) -> None:
        self.failures.append(failure_class)
        super().record_failure(permit, failure_class)  # type: ignore[arg-type]

    def release(self, permit: object) -> None:
        self.releases += 1
        super().release(permit)  # type: ignore[arg-type]


def _trip_breaker(breaker: ControlPlaneCircuitBreaker) -> None:
    for _ in range(3):
        permit = breaker.acquire()
        assert permit is not None
        breaker.record_failure(
            permit,
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        )


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


def test_single_pose_artifact_can_register_multiple_vina_modes(tmp_path: Path) -> None:
    async def scenario() -> tuple[DockingManifest, bytes]:
        service, _, config = _service(tmp_path, MultiModeSandboxClient())
        await service.start()
        job = await service.submit(_prepared(config), "idem-multi-mode")
        await service.wait_terminal(job.job_id)
        manifest = service.get_manifest(job.job_id)
        pose = service.read_artifact(job.job_id, manifest.artifacts[0]["artifact_id"])
        await service.stop()
        return manifest, pose

    manifest, pose = asyncio.run(scenario())

    assert manifest.pose_count == 2
    assert manifest.best_energy == -8.1
    assert len(manifest.artifacts) == 1
    assert pose == MULTI_MODE_POSE.encode("utf-8")


def test_service_suffix_allowlist_is_single_and_matches_wrapper_contract() -> None:
    assert service_module._UPLOAD_SUFFIXES == {
        "receptor": frozenset({".pdb"}),
        "ligand": frozenset({".sdf", ".mol"}),
    }


@pytest.mark.parametrize(
    ("role", "suffix"),
    [("receptor", ".pdb"), ("ligand", ".sdf"), ("ligand", ".mol")],
)
def test_service_upload_and_staged_suffix_guards_accept_exact_matrix(
    role: str, suffix: str
) -> None:
    upload = type(
        "Upload",
        (),
        {"filename": f"sample{suffix}", "file": io.BytesIO(b"payload")},
    )()
    assert service_module._upload_suffix(upload, role) == suffix
    staged = StagedInput(
        relative_path=f"staged/{'4' * 32}/{role}{suffix}",
        size_bytes=7,
        sha256="d" * 64,
    )
    assert service_module._safe_staged_input(staged, role) is staged


@pytest.mark.parametrize(
    ("role", "suffix"),
    [("receptor", ".pdbqt"), ("ligand", ".pdb"), ("ligand", ".pdbqt")],
)
def test_service_upload_and_staged_suffix_guards_reject_meeko_unsupported_inputs(
    role: str, suffix: str
) -> None:
    upload = type(
        "Upload",
        (),
        {"filename": f"sample{suffix}", "file": io.BytesIO(b"payload")},
    )()
    with pytest.raises(BrokerFailure) as upload_failure:
        service_module._upload_suffix(upload, role)
    assert upload_failure.value.code is BrokerErrorCode.INVALID_INPUT
    staged = StagedInput(
        relative_path=f"staged/{'4' * 32}/{role}{suffix}",
        size_bytes=7,
        sha256="d" * 64,
    )
    with pytest.raises(ValueError):
        service_module._safe_staged_input(staged, role)


@pytest.mark.parametrize(
    ("receptor_suffix", "ligand_suffix"),
    [(".pdbqt", ".sdf"), (".pdb", ".pdb"), (".pdb", ".pdbqt")],
)
def test_direct_submit_rejects_forged_unsupported_prepared_suffix_before_readiness(
    tmp_path: Path,
    receptor_suffix: str,
    ligand_suffix: str,
) -> None:
    service, store, config = _service(tmp_path, FakeSandboxClient())
    forged = _forged_prepared_with_suffixes(
        config, receptor_suffix, ligand_suffix
    )

    async def scenario() -> BrokerFailure:
        with pytest.raises(BrokerFailure) as raised:
            await service.submit(forged, "idem-forged-suffix")
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.INVALID_INPUT
    assert store.active_jobs() == []


@pytest.mark.parametrize("receptor_suffix", [".pdb"])
@pytest.mark.parametrize("ligand_suffix", [".sdf", ".mol"])
def test_every_accepted_suffix_combination_reaches_the_production_adapter_and_run(
    tmp_path: Path,
    receptor_suffix: str,
    ligand_suffix: str,
) -> None:
    async def scenario() -> tuple[object, AdapterUploadSandboxClient]:
        config = _config(tmp_path)
        sdk = AdapterUploadSandboxClient(config)
        store = BrokerStore(config.state_root / "broker.sqlite")
        service = SandboxBrokerService(
            config,
            store,
            sdk,
            ArtifactRegistry(config.state_root, store),
        )
        await service.start()
        prepared = _prepared_with_suffixes(config, receptor_suffix, ligand_suffix)
        job = await service.submit(prepared, "idem-suffix-combination")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert sdk.run_count == 1
    request_text = next(
        data for path, data in sdk.uploads if path == "/workspace/input/request.json"
    )
    request = json.loads(request_text)
    assert request["receptor_path"] == f"/workspace/input/receptor{receptor_suffix}"
    assert request["ligand_path"] == f"/workspace/input/ligand{ligand_suffix}"
    assert [path for path, _ in sdk.uploads] == [
        f"/workspace/input/receptor{receptor_suffix}",
        f"/workspace/input/ligand{ligand_suffix}",
        "/workspace/input/request.json",
    ]
    assert request["parameters"] == DockingParameters(
        center=[1, 2, 3], size=[20, 20, 20]
    ).model_dump(mode="json")
    wrapper_result = _run_uploaded_request_through_wrapper(tmp_path, sdk.uploads)
    assert wrapper_result["status"] == "succeeded"
    assert wrapper_result["vina_version"] == "1.2.5"
    assert wrapper_result["meeko_version"] == "0.7.1"


def test_non_utf8_receptor_bytes_reach_the_production_adapter_unchanged(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, AdapterUploadSandboxClient, bytes]:
        config = _config(tmp_path)
        base = _prepared(config)
        legacy_receptor = b"REMARK legacy path: " + bytes([0xC9, 0xA3, 0xC0, 0xCF]) + b"\nATOM\n"
        receptor_path = config.state_root / base.receptor.relative_path
        receptor_path.write_bytes(legacy_receptor)
        receptor = StagedInput(
            relative_path=base.receptor.relative_path,
            size_bytes=len(legacy_receptor),
            sha256=hashlib.sha256(legacy_receptor).hexdigest(),
        )
        prepared = PreparedDockingSubmission(
            parameters=base.parameters,
            receptor=receptor,
            ligand=base.ligand,
            canonical_input_sha256=canonical_submission_sha256(
                base.parameters,
                receptor,
                base.ligand,
            ),
            trace_id="trace-legacy-receptor",
        )
        sdk = AdapterUploadSandboxClient(config)
        store = BrokerStore(config.state_root / "broker.sqlite")
        service = SandboxBrokerService(
            config,
            store,
            sdk,
            ArtifactRegistry(config.state_root, store),
        )
        await service.start()
        job = await service.submit(prepared, "idem-legacy-receptor")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk, legacy_receptor

    terminal, sdk, legacy_receptor = asyncio.run(scenario())

    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert sdk.uploads[0] == (
        "/workspace/input/receptor.pdb",
        legacy_receptor,
    )


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


def test_cancel_before_job_coroutine_starts_keeps_consumer_alive(
    tmp_path: Path,
) -> None:
    checkpoint = ["scenario_not_started"]

    async def scenario() -> tuple[object, object, FakeSandboxClient]:
        loop = asyncio.get_running_loop()
        release_first_job = asyncio.Event()
        first_job_wrapped = asyncio.Event()
        intercepted = False

        def delaying_task_factory(
            task_loop: asyncio.AbstractEventLoop,
            coroutine: object,
        ) -> asyncio.Task[object]:
            nonlocal intercepted
            code = getattr(coroutine, "cr_code", None)
            if not intercepted and getattr(code, "co_name", None) == "_run_job":
                intercepted = True

                async def delayed() -> object:
                    first_job_wrapped.set()
                    try:
                        await release_first_job.wait()
                        return await coroutine  # type: ignore[misc]
                    finally:
                        close = getattr(coroutine, "close", None)
                        if callable(close):
                            close()

                return asyncio.Task(delayed(), loop=task_loop)
            return asyncio.Task(coroutine, loop=task_loop)  # type: ignore[arg-type]

        loop.set_task_factory(delaying_task_factory)
        sdk = FakeSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        try:
            checkpoint[0] = "starting_service"
            await service.start()
            checkpoint[0] = "submitting_first"
            first = await service.submit(_prepared(config), "idem-prestart-cancel")
            checkpoint[0] = "waiting_for_delayed_job_task"
            await asyncio.wait_for(first_job_wrapped.wait(), timeout=1.0)
            checkpoint[0] = "cancelling_first"
            await service.cancel(first.job_id)
            checkpoint[0] = "submitting_second"
            second = await service.submit(_prepared(config), "idem-after-cancel")
            release_first_job.set()
            checkpoint[0] = "waiting_for_first_terminal"
            first_terminal = await service.wait_terminal(first.job_id, timeout=1.0)
            checkpoint[0] = "waiting_for_second_terminal"
            second_terminal = await service.wait_terminal(second.job_id, timeout=3.0)
            checkpoint[0] = "stopping_service"
            await asyncio.wait_for(service.stop(timeout=3.0), timeout=5.0)
            checkpoint[0] = "completed"
            return first_terminal, second_terminal, sdk
        finally:
            release_first_job.set()
            loop.set_task_factory(None)

    try:
        first, second, sdk = asyncio.run(
            asyncio.wait_for(scenario(), timeout=10.0)
        )
    except asyncio.TimeoutError:
        pytest.fail(f"cancellation watchdog expired at {checkpoint[0]}")
    assert first.status is BrokerJobStatus.CANCELLED
    assert second.status is BrokerJobStatus.SUCCEEDED, second
    assert sdk.create_count == 1
    assert sdk.run_count == 1
    assert sdk.destroy_count == 1


def test_run_cancel_event_cancels_only_run_task_and_consumer_runs_next_job(
    tmp_path: Path,
) -> None:
    class RunCancellationClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__(block=True)
            self.run_cancellations = 0

        async def run(self, handle: SandboxHandle) -> SandboxCommandResult:
            try:
                return await super().run(handle)
            except asyncio.CancelledError:
                self.run_cancellations += 1
                raise

    async def scenario() -> tuple[object, object, RunCancellationClient]:
        sdk = RunCancellationClient()
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        first = await service.submit(_prepared(config), "idem-run-event-cancel")
        await sdk.running.wait()
        await service.cancel(first.job_id)
        second = await service.submit(_prepared(config), "idem-after-run-cancel")
        first_terminal = await service.wait_terminal(first.job_id, timeout=1.0)
        sdk.release.set()
        second_terminal = await service.wait_terminal(second.job_id, timeout=3.0)
        await service.stop()
        return first_terminal, second_terminal, sdk

    first, second, sdk = asyncio.run(scenario())
    assert first.status is BrokerJobStatus.CANCELLED
    assert first.cleanup_status == "succeeded"
    assert second.status is BrokerJobStatus.SUCCEEDED
    assert sdk.run_cancellations == 1
    assert sdk.run_count == 2
    assert sdk.destroy_count == 2


def test_cancel_during_provisioning_waits_for_create_then_attaches_and_destroys(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, BlockingCreateSandboxClient]:
        sdk = BlockingCreateSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-cancel-create")
        await sdk.remote_live.wait()

        await service.cancel(job.job_id)
        await asyncio.sleep(0)
        assert service.get_job(job.job_id).status is BrokerJobStatus.PROVISIONING
        assert sdk.live_ids == {f"sandbox-{job.job_id}"}

        sdk.create_release.set()
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.CANCELLED
    assert terminal.cleanup_status == "succeeded"
    assert terminal.sandbox_id is not None
    assert sdk.create_cancelled is False
    assert sdk.destroyed_ids == [terminal.sandbox_id]
    assert sdk.live_ids == set()


def test_transient_create_retries_once_after_confirmed_reconciliation(
    tmp_path: Path,
) -> None:
    class TransientCreateSandboxClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.reconciled: list[str] = []

        async def create(self, job_id: str) -> SandboxHandle:
            self.create_count += 1
            if self.create_count == 1:
                raise SandboxCreateError("sandbox provisioning failed")
            return SandboxHandle(f"sandbox-{job_id}-retry", object())

        async def destroy_by_job_id(self, job_id: str) -> int:
            self.reconciled.append(job_id)
            return 0

    async def scenario() -> tuple[object, TransientCreateSandboxClient]:
        sdk = TransientCreateSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-create-retry")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert sdk.create_count == 2
    assert sdk.reconciled == [terminal.job_id]
    assert sdk.run_count == sdk.destroy_count == 1


def test_transient_create_does_not_retry_without_confirmed_reconciliation(
    tmp_path: Path,
) -> None:
    class UnreconciledCreateSandboxClient(FakeSandboxClient):
        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            raise SandboxDestroyError("sandbox cleanup failed")

    async def scenario() -> tuple[object, UnreconciledCreateSandboxClient]:
        sdk = UnreconciledCreateSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-create-unreconciled")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert sdk.create_count == 1
    assert terminal.cleanup_status == "failed"
    assert "sandbox_auto_expires_within_configured_remote_lifetime" in terminal.warnings


@pytest.mark.parametrize("reconciliation", [True, -1, 1, 1.0, "0", None])
def test_transient_create_does_not_retry_after_invalid_reconciliation_result(
    tmp_path: Path,
    reconciliation: object,
) -> None:
    class InvalidReconciliationClient(FakeSandboxClient):
        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

        async def destroy_by_job_id(self, job_id: str) -> object:
            del job_id
            return reconciliation

    async def scenario() -> tuple[object, InvalidReconciliationClient]:
        sdk = InvalidReconciliationClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-invalid-reconcile")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.cleanup_status == "failed"
    assert sdk.create_count == 1


def test_transient_create_does_not_retry_without_reconciliation_callable(
    tmp_path: Path,
) -> None:
    class NoReconciliationClient(FakeSandboxClient):
        destroy_by_job_id = None

        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

    async def scenario() -> tuple[object, NoReconciliationClient]:
        sdk = NoReconciliationClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-no-reconcile-callable")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.cleanup_status == "failed"
    assert sdk.create_count == 1


def test_second_create_failure_never_makes_a_third_create_call(tmp_path: Path) -> None:
    class TwiceFailingCreateClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.reconcile_count = 0

        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.reconcile_count += 1
            return 0

    async def scenario() -> tuple[object, TwiceFailingCreateClient]:
        sdk = TwiceFailingCreateClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-create-fails-twice")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert sdk.create_count == 2
    assert sdk.reconcile_count == 2


def test_create_cancellation_after_reconciliation_blocks_second_create(
    tmp_path: Path,
) -> None:
    class CancelDuringReconciliationClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.reconcile_started = asyncio.Event()
            self.reconcile_release = asyncio.Event()

        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.reconcile_started.set()
            await self.reconcile_release.wait()
            return 0

    async def scenario() -> tuple[object, CancelDuringReconciliationClient]:
        sdk = CancelDuringReconciliationClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-cancel-reconcile")
        await sdk.reconcile_started.wait()
        await service.cancel(job.job_id)
        sdk.reconcile_release.set()
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.CANCELLED
    assert sdk.create_count == 1


def test_create_retry_preserves_cancellation_during_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResistantReconciliationClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.reconcile_started = asyncio.Event()
            self.reconcile_cancelled = asyncio.Event()
            self.reconcile_release = asyncio.Event()

        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.reconcile_started.set()
            try:
                await self.reconcile_release.wait()
            except asyncio.CancelledError:
                self.reconcile_cancelled.set()
                await self.reconcile_release.wait()
            return 0

    async def scenario() -> tuple[SandboxBrokerService, ResistantReconciliationClient]:
        sdk = ResistantReconciliationClient()
        service, _, _ = _service(tmp_path, sdk)
        service._create_retry_delay_seconds = 0.0
        service._run_cancel_grace_seconds = 0.01
        retry = asyncio.create_task(service._create_with_retry("job-cancel-reconcile"))
        original_sleep = asyncio.sleep
        handoff_started = asyncio.Event()
        handoff_release = asyncio.Event()

        async def controlled_sleep(delay: float) -> None:
            if (
                delay == 0
                and asyncio.current_task() is retry
                and not handoff_started.is_set()
            ):
                handoff_started.set()
                await handoff_release.wait()
                return
            await original_sleep(delay)

        monkeypatch.setattr(service_module.asyncio, "sleep", controlled_sleep)
        await sdk.reconcile_started.wait()
        retry.cancel()
        await handoff_started.wait()
        retry.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(retry), timeout=0.5)
            assert sdk.reconcile_cancelled.is_set()
            assert len(service._isolated_tasks) == 1
        finally:
            handoff_release.set()
            sdk.reconcile_release.set()
            for _ in range(20):
                if not service._isolated_tasks:
                    break
                await asyncio.sleep(0)
        return service, sdk

    service, sdk = asyncio.run(scenario())
    assert sdk.create_count == 1
    assert service._isolated_tasks == set()


def test_double_cancel_at_create_handoff_retains_and_cleans_late_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResistantCreateClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.create_started = asyncio.Event()
            self.create_cancelled = asyncio.Event()
            self.create_release = asyncio.Event()
            self.live_ids: set[str] = set()

        async def create(self, job_id: str) -> SandboxHandle:
            self.create_count += 1
            self.create_started.set()
            try:
                await self.create_release.wait()
            except asyncio.CancelledError:
                self.create_cancelled.set()
                await self.create_release.wait()
            sandbox_id = f"sandbox-{job_id}-late"
            self.live_ids.add(sandbox_id)
            return SandboxHandle(sandbox_id, object())

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroyed_ids.append(handle.sandbox_id)
            self.live_ids.discard(handle.sandbox_id)

    async def scenario() -> tuple[SandboxBrokerService, ResistantCreateClient, bool]:
        sdk = ResistantCreateClient()
        service, _, _ = _service(tmp_path, sdk)
        create = asyncio.create_task(service._create_with_retry("job-create-handoff"))
        original_sleep = asyncio.sleep
        handoff_started = asyncio.Event()
        handoff_release = asyncio.Event()

        async def controlled_sleep(delay: float) -> None:
            if (
                delay == 0
                and asyncio.current_task() is create
                and not handoff_started.is_set()
            ):
                handoff_started.set()
                await handoff_release.wait()
                return
            await original_sleep(delay)

        monkeypatch.setattr(service_module.asyncio, "sleep", controlled_sleep)
        await sdk.create_started.wait()
        create.cancel()
        await handoff_started.wait()
        create.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(create), timeout=0.5)
        handoff_release.set()
        assert sdk.create_cancelled.is_set()

        stop_incomplete = False
        try:
            await service.stop(timeout=0.05)
        except StopIncomplete:
            stop_incomplete = True
        tracked_before_release = bool(service._create_tasks)
        sdk.create_release.set()
        await service.stop(timeout=1.0)
        await asyncio.sleep(0)
        return service, sdk, stop_incomplete and tracked_before_release

    service, sdk, shutdown_waited = asyncio.run(scenario())
    assert shutdown_waited is True
    assert sdk.create_count == 1
    assert sdk.destroy_count == 1
    assert sdk.destroyed_ids == ["sandbox-job-create-handoff-late"]
    assert sdk.live_ids == set()
    assert service._create_tasks == {}
    assert service._cleanup_tasks == {}
    assert service._isolated_tasks == set()


def test_reconciliation_cannot_extend_absolute_create_retry_deadline(
    tmp_path: Path,
) -> None:
    class SlowReconciliationClient(FakeSandboxClient):
        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            await asyncio.sleep(0.03)
            return 0

    async def scenario() -> tuple[object, SlowReconciliationClient]:
        sdk = SlowReconciliationClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_hard_timeout_seconds = 0.02
        service._destroy_hard_timeout_seconds = 0.1
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-create-one-deadline")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert sdk.create_count == 1


def test_blocking_reconciliation_is_bounded_by_absolute_create_deadline(
    tmp_path: Path,
) -> None:
    class ResistantReconciliationClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.reconcile_started = asyncio.Event()
            self.reconcile_cancelled = asyncio.Event()
            self.reconcile_release = asyncio.Event()

        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError("sandbox provisioning failed")

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.reconcile_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.reconcile_cancelled.set()
                await self.reconcile_release.wait()
            return 0

    async def scenario() -> tuple[object, ResistantReconciliationClient, bool]:
        sdk = ResistantReconciliationClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_hard_timeout_seconds = 0.02
        service._destroy_hard_timeout_seconds = 0.2
        service._create_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-create-bounded-reconcile")
        await sdk.reconcile_cancelled.wait()
        terminal = await asyncio.wait_for(
            service.wait_terminal(job.job_id), timeout=0.5
        )
        completed_before_release = not sdk.reconcile_release.is_set()
        sdk.reconcile_release.set()
        await asyncio.sleep(0)
        await service.stop()
        return terminal, sdk, completed_before_release

    terminal, sdk, completed_before_release = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.cleanup_status == "failed"
    assert sdk.create_count == 1
    assert completed_before_release is True


def test_late_cancellation_resistant_create_is_owned_and_cleaned_before_stop(
    tmp_path: Path,
) -> None:
    class LateCreateClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.create_started = asyncio.Event()
            self.create_cancelled = asyncio.Event()
            self.create_release = asyncio.Event()
            self.live_ids: set[str] = set()

        async def create(self, job_id: str) -> SandboxHandle:
            self.create_count += 1
            self.create_started.set()
            try:
                await self.create_release.wait()
            except asyncio.CancelledError:
                self.create_cancelled.set()
                await self.create_release.wait()
            sandbox_id = f"sandbox-{job_id}-late"
            self.live_ids.add(sandbox_id)
            return SandboxHandle(sandbox_id, object())

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroyed_ids.append(handle.sandbox_id)
            self.live_ids.discard(handle.sandbox_id)

    async def scenario() -> tuple[object, LateCreateClient, bool, list[str]]:
        sdk = LateCreateClient()
        service, _, config = _service(tmp_path, sdk)
        service._create_hard_timeout_seconds = 0.02
        await service.start()
        job = await service.submit(_prepared(config), "idem-late-create-cleanup")
        await sdk.create_started.wait()
        terminal = await asyncio.wait_for(
            service.wait_terminal(job.job_id), timeout=0.5
        )
        assert sdk.create_cancelled.is_set()

        stop_incomplete = False
        try:
            await service.stop(timeout=0.05)
        except StopIncomplete:
            stop_incomplete = True
        tracked_before_release = bool(service._create_tasks)

        sdk.create_release.set()
        await service.stop(timeout=1.0)
        await asyncio.sleep(0)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-")
        ]
        assert service._create_tasks == {}
        assert service._cleanup_tasks == {}
        assert service._isolated_tasks == set()
        return terminal, sdk, stop_incomplete and tracked_before_release, leaked

    terminal, sdk, shutdown_waited, leaked = asyncio.run(scenario())
    assert shutdown_waited is True
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert terminal.cleanup_status == "failed"
    assert "sandbox_auto_expires_within_configured_remote_lifetime" in terminal.warnings
    assert sdk.create_count == 1
    assert sdk.destroy_count == 1
    assert sdk.destroyed_ids == [f"sandbox-{terminal.job_id}-late"]
    assert sdk.live_ids == set()
    assert leaked == []


def test_execution_timeout_and_command_failure_are_stable_and_cleanup(
    tmp_path: Path,
) -> None:
    timeout_telemetry = RecordingTelemetry()

    async def scenario() -> tuple[object, object, FakeSandboxClient, FaultSandboxClient]:
        timeout_sdk = FakeSandboxClient(block=True)
        timeout_service, _, timeout_config = _service(
            tmp_path / "timeout",
            timeout_sdk,
            telemetry=timeout_telemetry,
        )
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
    assert timeout_sdk.run_count == command_sdk.run_count == 1
    assert timeout_sdk.destroy_count == command_sdk.destroy_count == 1
    assert timeout_telemetry.snapshot()["counters"][
        "control_failure|command|command_timeout"
    ] == 1


def test_nonzero_wrapper_timeout_preserves_verified_failure_code(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, StructuredFailureSandboxClient]:
        sdk = StructuredFailureSandboxClient(
            {
                "schema_version": 1,
                "status": "failed",
                "phase": "docking",
                "error_code": "tool_timeout",
                "warnings": [],
            }
        )
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-wrapper-timeout")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())

    assert terminal.error_code == BrokerErrorCode.EXECUTION_TIMEOUT.value
    assert terminal.cleanup_status == "succeeded"
    assert sdk.read_paths == ["/workspace/output/result.json"]
    assert sdk.destroy_count == 1


@pytest.mark.parametrize(
    "phase",
    [
        "receptor_preparation",
        "ligand_geometry_preparation",
        "ligand_preparation",
        "docking",
    ],
)
def test_nonzero_wrapper_tool_failure_preserves_verified_failure_code(
    tmp_path: Path,
    phase: str,
) -> None:
    async def scenario() -> object:
        sdk = StructuredFailureSandboxClient(
            {
                "schema_version": 1,
                "status": "failed",
                "phase": phase,
                "error_code": "tool_failed",
                "warnings": [],
            }
        )
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-wrapper-tool-failed")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())

    assert terminal.error_code == BrokerErrorCode.COMMAND_FAILED.value
    assert terminal.warnings == ("sandbox_tool_failed",)
    assert terminal.cleanup_status == "succeeded"


def test_verified_failure_result_rejects_impossible_phase_code_pair() -> None:
    forged = json.dumps(
        {
            "schema_version": 1,
            "status": "failed",
            "phase": "docking",
            "error_code": "invalid_input",
            "warnings": [],
        }
    )

    assert service_module._verified_failure_result(forged) is BrokerErrorCode.COMMAND_FAILED


@pytest.mark.parametrize(
    "payload",
    [
        "[" * 1100 + "0" + "]" * 1100,
        '{"schema_version":1,"status":"failed","phase":"input_validation",'
        '"phase":"docking","error_code":"tool_timeout","warnings":[]}',
        "\ud800",
        "x" * (1024 * 1024 + 1),
    ],
    ids=("deep-json", "duplicate-key", "unpaired-surrogate", "oversized"),
)
def test_verified_failure_result_rejects_ambiguous_or_hostile_json(
    payload: str,
) -> None:
    assert service_module._verified_failure_result(payload) is BrokerErrorCode.COMMAND_FAILED


def test_cancel_during_failure_result_read_is_bounded_and_cleans(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, BlockingFailureReadSandboxClient]:
        sdk = BlockingFailureReadSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._run_cancel_grace_seconds = 0.02
        await service.start()
        job = await service.submit(_prepared(config), "idem-failure-read-cancel")
        await sdk.read_started.wait()
        await service.cancel(job.job_id)
        terminal = await service.wait_terminal(job.job_id, timeout=1.0)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())

    assert terminal.status is BrokerJobStatus.CANCELLED
    assert terminal.cleanup_status == "succeeded"
    assert sdk.read_cancellations == 1
    assert sdk.destroy_count == 1


def test_failure_result_read_timeout_is_bounded_and_cleans(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, BlockingFailureReadSandboxClient]:
        sdk = BlockingFailureReadSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._failure_result_read_timeout_seconds = 0.01
        service._run_cancel_grace_seconds = 0.02
        await service.start()
        job = await service.submit(_prepared(config), "idem-failure-read-timeout")
        terminal = await service.wait_terminal(job.job_id, timeout=1.0)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())

    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.COMMAND_FAILED.value
    assert terminal.cleanup_status == "succeeded"
    assert sdk.read_cancellations == 1
    assert sdk.destroy_count == 1


def test_resistant_failure_result_read_does_not_block_cleanup(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, ResistantFailureReadSandboxClient, list[str]]:
        sdk = ResistantFailureReadSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._failure_result_read_timeout_seconds = 0.01
        service._run_cancel_grace_seconds = 0.01
        await service.start()
        job = await service.submit(_prepared(config), "idem-resistant-failure-read")
        terminal = await service.wait_terminal(job.job_id, timeout=1.0)
        await service.stop()
        sdk.read_release.set()
        await asyncio.sleep(0.03)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-failure-")
            and not task.done()
        ]
        return terminal, sdk, leaked

    terminal, sdk, leaked = asyncio.run(scenario())

    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.COMMAND_FAILED.value
    assert terminal.cleanup_status == "succeeded"
    assert sdk.read_cancellations >= 1
    assert sdk.destroy_count == 1
    assert leaked == []


def test_cancel_during_output_validation_is_bounded_and_cleans(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, BlockingValidationSandboxClient]:
        sdk = BlockingValidationSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._run_cancel_grace_seconds = 0.02
        await service.start()
        job = await service.submit(_prepared(config), "idem-validation-cancel")
        await sdk.read_started.wait()
        await service.cancel(job.job_id)
        terminal = await service.wait_terminal(job.job_id, timeout=1.0)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())

    assert terminal.status is BrokerJobStatus.CANCELLED
    assert terminal.cleanup_status == "succeeded"
    assert sdk.read_cancellations == 1
    assert sdk.destroy_count == 1


def test_output_validation_timeout_is_bounded_and_cleans(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, BlockingValidationSandboxClient]:
        sdk = BlockingValidationSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._output_validation_timeout_seconds = 0.01
        service._run_cancel_grace_seconds = 0.02
        await service.start()
        job = await service.submit(_prepared(config), "idem-validation-timeout")
        terminal = await service.wait_terminal(job.job_id, timeout=1.0)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())

    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.ARTIFACT_FAILED.value
    assert terminal.cleanup_status == "succeeded"
    assert sdk.read_cancellations == 1
    assert sdk.destroy_count == 1


def test_resistant_output_validation_does_not_publish_after_terminal(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[
        object,
        ResistantValidationSandboxClient,
        list[str],
        int,
        int,
    ]:
        sdk = ResistantValidationSandboxClient()
        service, store, config = _service(tmp_path, sdk)
        service._output_validation_timeout_seconds = 0.01
        service._run_cancel_grace_seconds = 0.01
        await service.start()
        job = await service.submit(_prepared(config), "idem-resistant-validation")
        terminal = await service.wait_terminal(job.job_id, timeout=1.0)
        artifacts_at_terminal = len(store.list_artifacts(job.job_id))
        await service.stop()
        sdk.read_release.set()
        await asyncio.sleep(0.03)
        artifacts_after_release = len(store.list_artifacts(job.job_id))
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-validation-")
            and not task.done()
        ]
        return (
            terminal,
            sdk,
            leaked,
            artifacts_at_terminal,
            artifacts_after_release,
        )

    terminal, sdk, leaked, artifacts_at_terminal, artifacts_after_release = (
        asyncio.run(scenario())
    )

    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.ARTIFACT_FAILED.value
    assert terminal.cleanup_status == "succeeded"
    assert sdk.read_cancellations >= 1
    assert sdk.destroy_count == 1
    assert artifacts_at_terminal == 0
    assert artifacts_after_release == 0
    assert leaked == []


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

    telemetry = RecordingTelemetry()

    async def scenario() -> tuple[object, FaultSandboxClient]:
        sdk = FaultSandboxClient(stage, failure=FatalProbe("secret-base-exception"))
        service, _, config = _service(tmp_path, sdk, telemetry=telemetry)
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
    operation = {
        "create": "create",
        "upload": "upload",
        "run": "command",
        "read": "metadata",
        "list": "metadata",
    }[stage]
    failure_class = (
        "command_transport_failed"
        if stage == "run"
        else "unknown_control_plane_failure"
    )
    assert telemetry.snapshot()["counters"][
        f"control_failure|{operation}|{failure_class}"
    ] == 1


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
    telemetry = RecordingTelemetry()

    async def scenario() -> tuple[object, RecoverySandboxClient]:
        sdk = RecoverySandboxClient(destroy_fails=destroy_fails)
        service, store, _ = _service(tmp_path, sdk, telemetry=telemetry)
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
    terminal_events = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["phase"] == "job_terminal"
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0]["job_id"] == terminal.job_id
    terminal_counters = {
        key: value
        for key, value in telemetry.snapshot()["counters"].items()
        if key.startswith("terminal|")
    }
    assert sum(terminal_counters.values()) == 1
    lifecycle = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["job_id"] == terminal.job_id
    ]
    assert [event["phase"] for event in lifecycle] == [
        "cleanup_started",
        "cleanup_completed",
        "job_terminal",
    ]
    assert lifecycle[0]["attempt"] == 1
    assert lifecycle[1]["attempt"] == 1
    assert lifecycle[1]["outcome"] == ("failed" if destroy_fails else "passed")
    assert lifecycle[1]["cleanup_status"] == (
        "failed" if destroy_fails else "succeeded"
    )
    assert lifecycle[1]["failure_class"] == (
        "destroy_failed" if destroy_fails else None
    )
    destroy_counter = telemetry.snapshot()["counters"].get(
        "control_failure|destroy|destroy_failed",
        0,
    )
    assert destroy_counter == (1 if destroy_fails else 0)
    assert telemetry.snapshot()["runtime"]["active_job_count"] == 0


def test_recovery_cancellation_pairs_cleanup_and_preserves_active_state(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()

    class ResistantRecoveryClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.destroy_started = asyncio.Event()
            self.destroy_release = asyncio.Event()
            self.destroy_finished = asyncio.Event()
            self.destroy_calls = 0
            self.destroy_cancellations = 0

        async def destroy_by_id(self, sandbox_id: str) -> None:
            self.destroy_calls += 1
            if self.destroy_calls == 1:
                self.destroy_started.set()
                while not self.destroy_release.is_set():
                    try:
                        await self.destroy_release.wait()
                    except asyncio.CancelledError:
                        self.destroy_cancellations += 1
                self.destroy_finished.set()
            self.destroyed_ids.append(sandbox_id)

    async def scenario() -> tuple[object, list[dict[str, object]], int, int]:
        sdk = ResistantRecoveryClient()
        service, store, _ = _service(tmp_path, sdk, telemetry=telemetry)
        await service.start()
        seeded = _seed_running(store, "idem-cancelled-recovery")
        recovery = asyncio.create_task(service.recover())
        await asyncio.wait_for(sdk.destroy_started.wait(), timeout=1.0)
        recovery.cancel()
        cancellation_propagated = False
        try:
            await asyncio.wait_for(recovery, timeout=1.0)
        except asyncio.CancelledError:
            cancellation_propagated = True

        after_cancel = store.get(seeded.job_id)
        assert after_cancel is not None
        events_after_cancel = [
            event
            for event in telemetry.snapshot()["recent_events"]
            if event["job_id"] == seeded.job_id
        ]
        isolated_after_cancel = len(service._isolated_tasks)

        sdk.destroy_release.set()
        await asyncio.wait_for(sdk.destroy_finished.wait(), timeout=1.0)
        for _ in range(20):
            if not service._isolated_tasks:
                break
            await asyncio.sleep(0)
        await service.recover()
        terminal = store.get(seeded.job_id)
        assert terminal is not None
        isolated_after_recovery = len(service._isolated_tasks)
        await service.stop()

        assert cancellation_propagated is True
        assert after_cancel.status not in TERMINAL_STATUSES
        assert after_cancel.cleanup_status == "in_progress"
        return (
            terminal,
            events_after_cancel,
            isolated_after_cancel,
            isolated_after_recovery,
        )

    terminal, events, isolated_during, isolated_after = asyncio.run(scenario())
    assert [event["phase"] for event in events] == [
        "cleanup_started",
        "cleanup_completed",
    ]
    assert events[1]["outcome"] == "cancelled"
    assert events[1]["failure_class"] is None
    assert isolated_during == 1
    assert isolated_after == 0
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    lifecycle = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["job_id"] == terminal.job_id
    ]
    assert [event["phase"] for event in lifecycle] == [
        "cleanup_started",
        "cleanup_completed",
        "cleanup_started",
        "cleanup_completed",
        "job_terminal",
    ]


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


def test_stop_shields_inflight_create_until_handle_is_attached_and_destroyed(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, BlockingCreateSandboxClient, list[str]]:
        sdk = BlockingCreateSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-stop-create")
        await sdk.remote_live.wait()

        stop_task = asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        assert not stop_task.done()
        assert service.get_job(job.job_id).cleanup_status == "not_started"

        sdk.create_release.set()
        await stop_task
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
    assert terminal.sandbox_id is not None
    assert sdk.destroyed_ids == [terminal.sandbox_id]
    assert sdk.live_ids == set()
    assert leaked == []


def test_cancelled_stop_waiter_does_not_cancel_known_cleanup(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, BlockingDestroySandboxClient, list[str]]:
        sdk = BlockingDestroySandboxClient()
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-stop-caller-cancel")
        await sdk.running.wait()
        stop_task = asyncio.create_task(service.stop())
        await sdk.destroy_started.wait()
        stop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop_task
        assert not sdk.destroy_release.is_set()
        assert sdk.destroy_cancelled is False

        sdk.destroy_release.set()
        await service.stop(timeout=1.0)
        await asyncio.sleep(0)
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
    assert sdk.destroy_cancelled is False
    assert sdk.destroyed_ids == [terminal.sandbox_id]
    assert leaked == []


def test_short_stop_keeps_adapter_create_and_shutdown_running_until_retry(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, AdapterLifecycleFactory, list[str]]:
        config = _config(tmp_path)
        factory = AdapterLifecycleFactory()
        sdk = OpenSandboxClient(config, sandbox_factory=factory)
        store = BrokerStore(config.state_root / "broker.sqlite")
        service = SandboxBrokerService(
            config,
            store,
            sdk,
            ArtifactRegistry(config.state_root, store),
        )
        await service.start()
        job = await service.submit(_prepared(config), "idem-stop-unknown-create")
        await factory.remote_live.wait()

        with pytest.raises(StopIncomplete, match="shutdown is incomplete"):
            await service.stop(timeout=0.01)
        pending = service.get_job(job.job_id)
        assert pending.status is BrokerJobStatus.PROVISIONING
        assert pending.cleanup_status == "not_started"
        assert factory.live_ids == {f"sandbox-{job.job_id}"}
        assert any(not task.done() for task in service._create_tasks.values())

        factory.create_release.set()
        for _ in range(100):
            if service._shutdown_task is not None and service._shutdown_task.done():
                break
            await asyncio.sleep(0.01)
        assert service._shutdown_task is not None
        assert service._shutdown_task.done()
        await service.stop(timeout=1.0)
        assert service._shutdown_task is None
        assert service._stopping is False
        await asyncio.sleep(0)
        terminal = service.get_job(job.job_id)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-")
            and not task.done()
        ]
        return terminal, factory, leaked

    terminal, factory, leaked = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.CANCELLED
    assert terminal.cleanup_status == "succeeded"
    assert terminal.sandbox_id is not None
    assert factory.create_cancelled is False
    assert factory.destroyed_ids == [terminal.sandbox_id]
    assert factory.live_ids == set()
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


def test_completed_destroy_failure_retries_same_identity_then_succeeds(
    tmp_path: Path,
) -> None:
    class TransientDestroyClient(FakeSandboxClient):
        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroyed_ids.append(handle.sandbox_id)
            if self.destroy_count == 1:
                raise SandboxDestroyError(
                    "sandbox cleanup failed",
                    operation="destroy",
                    failure_class=FailureClass.CONNECTION_FAILED,
                    completion_known=True,
                )

    telemetry = RecordingTelemetry()

    async def scenario() -> tuple[object, TransientDestroyClient, list[str]]:
        sdk = TransientDestroyClient()
        service, store, config = _service(tmp_path, sdk, telemetry=telemetry)
        service._destroy_retry_delay_seconds = 0.0
        statuses: list[str] = []
        original_record_cleanup = store.record_cleanup

        def record_cleanup(job_id: str, status: str) -> object:
            statuses.append(status)
            return original_record_cleanup(job_id, status)

        store.record_cleanup = record_cleanup  # type: ignore[method-assign]
        await service.start()
        job = await service.submit(_prepared(config), "idem-destroy-retry-success")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk, statuses

    terminal, sdk, statuses = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert terminal.cleanup_status == "succeeded"
    assert sdk.create_count == 1
    assert sdk.run_count == 1
    assert sdk.destroy_count == 2
    assert sdk.destroyed_ids == [terminal.sandbox_id, terminal.sandbox_id]
    assert statuses == ["in_progress", "succeeded"]
    counters = telemetry.snapshot()["counters"]
    assert counters["retry|destroy|attempted"] == 1
    assert counters["retry|destroy|succeeded"] == 1
    assert counters["control_failure|destroy|connection_failed"] == 1
    assert "retry|destroy|exhausted" not in counters
    cleanup_events = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["phase"] in {"cleanup_started", "cleanup_completed"}
    ]
    assert [event["attempt"] for event in cleanup_events] == [1, 1, 2, 2]
    assert [event.get("outcome") for event in cleanup_events] == [
        None,
        "failed",
        None,
        "passed",
    ]
    assert cleanup_events[1]["cleanup_status"] == "failed"
    assert cleanup_events[3]["cleanup_status"] == "succeeded"

    from scripts import run_opensandbox_stability_soak as soak

    lifecycle_events = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["job_id"] == terminal.job_id
    ]
    evidence = soak.evaluate_lifecycle(lifecycle_events, result_succeeded=True)

    assert sdk.run_count == 1
    assert evidence.events_complete is True
    assert evidence.failure_codes == ("broker_retry_observed",)
    assert evidence.failure_class == "none"


def test_failed_science_keeps_terminal_truth_after_cleanup_retry_succeeds(
    tmp_path: Path,
) -> None:
    class InvalidResultTransientDestroyClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__(valid_result=False)

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroyed_ids.append(handle.sandbox_id)
            if self.destroy_count == 1:
                raise SandboxDestroyError(
                    "sandbox cleanup failed",
                    operation="destroy",
                    failure_class=FailureClass.CONNECTION_FAILED,
                    completion_known=True,
                )

    telemetry = RecordingTelemetry()

    async def scenario() -> tuple[object, InvalidResultTransientDestroyClient]:
        sdk = InvalidResultTransientDestroyClient()
        service, _, config = _service(tmp_path, sdk, telemetry=telemetry)
        service._destroy_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-invalid-cleanup-recovered")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID.value
    assert terminal.cleanup_status == "succeeded"
    assert sdk.run_count == 1
    assert sdk.destroy_count == 2
    events = telemetry.snapshot()["recent_events"]
    cleanup_events = [
        event
        for event in events
        if event["phase"] in {"cleanup_started", "cleanup_completed"}
    ]
    assert [event["attempt"] for event in cleanup_events] == [1, 1, 2, 2]
    assert [event["outcome"] for event in cleanup_events] == [
        None,
        "failed",
        None,
        "passed",
    ]
    assert cleanup_events[1]["failure_class"] == "destroy_failed"
    assert cleanup_events[3]["failure_class"] is None
    terminal_event = events[-1]
    assert terminal_event["phase"] == "job_terminal"
    assert terminal_event["outcome"] == "failed"
    assert terminal_event["cleanup_status"] == "succeeded"
    assert terminal_event["failure_class"] == "none"

    from scripts import run_opensandbox_stability_soak as soak

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is True
    assert evidence.cleanup_status == "succeeded"
    assert evidence.terminal_outcome == "failed"
    assert evidence.failure_class == "none"
    assert evidence.failure_codes == ("broker_retry_observed",)


def test_two_completed_destroy_failures_mark_cleanup_failed(tmp_path: Path) -> None:
    class FailingDestroyClient(FakeSandboxClient):
        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroyed_ids.append(handle.sandbox_id)
            raise SandboxDestroyError(
                "sandbox cleanup failed",
                operation="destroy",
                failure_class=FailureClass.SERVER_500,
                completion_known=True,
            )

    telemetry = RecordingTelemetry()

    async def scenario() -> tuple[object, FailingDestroyClient, list[str]]:
        sdk = FailingDestroyClient()
        service, store, config = _service(tmp_path, sdk, telemetry=telemetry)
        service._destroy_retry_delay_seconds = 0.0
        statuses: list[str] = []
        original_record_cleanup = store.record_cleanup

        def record_cleanup(job_id: str, status: str) -> object:
            statuses.append(status)
            return original_record_cleanup(job_id, status)

        store.record_cleanup = record_cleanup  # type: ignore[method-assign]
        await service.start()
        job = await service.submit(_prepared(config), "idem-destroy-retry-fails")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk, statuses

    terminal, sdk, statuses = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert terminal.cleanup_status == "failed"
    assert sdk.create_count == 1
    assert sdk.run_count == 1
    assert sdk.destroy_count == 2
    assert sdk.destroyed_ids == [terminal.sandbox_id, terminal.sandbox_id]
    assert statuses == ["in_progress", "failed"]
    counters = telemetry.snapshot()["counters"]
    assert counters["retry|destroy|attempted"] == 1
    assert counters["retry|destroy|exhausted"] == 1
    assert counters["control_failure|destroy|server_500"] == 2
    assert "retry|destroy|succeeded" not in counters
    events = telemetry.snapshot()["recent_events"]
    cleanup_events = [
        event
        for event in events
        if event["phase"] in {"cleanup_started", "cleanup_completed"}
    ]
    assert [event["attempt"] for event in cleanup_events] == [1, 1, 2, 2]
    assert [event["outcome"] for event in cleanup_events] == [
        None,
        "failed",
        None,
        "failed",
    ]

    from scripts import run_opensandbox_stability_soak as soak

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is True
    assert evidence.cleanup_status == "failed"
    assert evidence.terminal_outcome == "failed"
    assert evidence.failure_class == "destroy_failed"
    assert "events_incomplete" not in evidence.failure_codes


def test_timed_out_cancellation_resistant_destroy_is_never_overlapped(
    tmp_path: Path,
) -> None:
    class ResistantDestroyClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.destroy_started = asyncio.Event()
            self.destroy_release = asyncio.Event()
            self.active_destroy = 0
            self.max_active_destroy = 0

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.active_destroy += 1
            self.max_active_destroy = max(
                self.max_active_destroy, self.active_destroy
            )
            self.destroy_started.set()
            try:
                while not self.destroy_release.is_set():
                    try:
                        await self.destroy_release.wait()
                    except asyncio.CancelledError:
                        continue
                self.destroyed_ids.append(handle.sandbox_id)
            finally:
                self.active_destroy -= 1

    async def scenario() -> tuple[object, ResistantDestroyClient]:
        sdk = ResistantDestroyClient()
        service, _, config = _service(tmp_path, sdk)
        service._destroy_hard_timeout_seconds = 0.02
        service._destroy_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-destroy-timeout-no-retry")
        await sdk.destroy_started.wait()
        terminal = await service.wait_terminal(job.job_id)
        sdk.destroy_release.set()
        await asyncio.sleep(0.02)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.cleanup_status == "failed"
    assert sdk.create_count == 1
    assert sdk.destroy_count == 1
    assert sdk.max_active_destroy == 1


def test_stop_tracks_cancellation_resistant_cleanup_until_it_terminates(
    tmp_path: Path,
) -> None:
    class ResistantDestroyClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.destroy_started = asyncio.Event()
            self.destroy_cancelled = asyncio.Event()
            self.destroy_release = asyncio.Event()

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroy_started.set()
            try:
                await self.destroy_release.wait()
            except asyncio.CancelledError:
                self.destroy_cancelled.set()
                await self.destroy_release.wait()
            self.destroyed_ids.append(handle.sandbox_id)

    async def scenario() -> tuple[object, ResistantDestroyClient, list[str], bool]:
        sdk = ResistantDestroyClient()
        service, store, config = _service(tmp_path, sdk)
        service._destroy_hard_timeout_seconds = 0.02
        await service.start()
        job = await service.submit(_prepared(config), "idem-stop-drains-cleanup")
        await sdk.destroy_started.wait()
        terminal = await service.wait_terminal(job.job_id)
        assert sdk.destroy_cancelled.is_set()

        stop_incomplete = False
        try:
            await service.stop(timeout=0.05)
        except StopIncomplete:
            stop_incomplete = True
        tracked_before_release = list(service._cleanup_tasks)
        shutdown_pending = (
            service._shutdown_task is not None
            and not service._shutdown_task.done()
        )

        sdk.destroy_release.set()
        await service.stop(timeout=1.0)
        await asyncio.sleep(0)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and (
                task.get_name().startswith("sandbox-broker-")
                or task.get_name().startswith("sandbox-cleanup-")
            )
        ]
        persisted = store.get(job.job_id)
        assert persisted is not None
        assert persisted.cleanup_status == "failed"
        assert service._cleanup_tasks == {}
        assert service._isolated_tasks == set()
        return terminal, sdk, leaked, stop_incomplete and shutdown_pending and bool(
            tracked_before_release
        )

    terminal, sdk, leaked, shutdown_waited = asyncio.run(scenario())
    assert shutdown_waited is True
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.cleanup_status == "failed"
    assert sdk.create_count == 1
    assert sdk.destroy_count == 1
    assert leaked == []


def test_successful_destroy_is_invoked_once_without_replacement(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, FakeSandboxClient]:
        sdk = FakeSandboxClient()
        service, _, config = _service(tmp_path, sdk)
        service._destroy_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-destroy-first-success")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert sdk.create_count == 1
    assert sdk.destroy_count == 1


def test_adapter_unknown_destroy_completion_is_not_retried_and_is_drained(
    tmp_path: Path,
) -> None:
    class CancellationSuppressingFactory:
        def __init__(self) -> None:
            self.destroy_count = 0
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()

        async def destroy(self, raw: object) -> None:
            del raw
            self.destroy_count += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release.wait()
            raise ConnectionError("private destroy detail")

    async def scenario() -> tuple[str, int, int, bool]:
        config = _config(tmp_path)
        factory = CancellationSuppressingFactory()
        adapter = OpenSandboxClient(config, sandbox_factory=factory)
        adapter._cleanup_hard_timeout_seconds = 0.02
        adapter._cleanup_drain_margin_seconds = 0.05
        store = BrokerStore(config.state_root / "broker.sqlite")
        service = SandboxBrokerService(
            config,
            store,
            adapter,
            ArtifactRegistry(config.state_root, store),
        )
        service._destroy_hard_timeout_seconds = 0.1
        service._destroy_retry_delay_seconds = 0.0
        record, _ = store.create_or_get(
            "idem-adapter-unknown-destroy",
            "a" * 64,
            "trace-adapter-unknown-destroy",
        )
        store.transition(record.job_id, BrokerJobStatus.PROVISIONING)
        store.attach_sandbox(record.job_id, "sandbox-unknown-destroy")
        cleanup = await service._destroy_best_effort(
            record.job_id,
            SandboxHandle("sandbox-unknown-destroy", object()),
        )
        await factory.cancelled.wait()
        quarantined = len(adapter._cleanup_quarantine)
        factory.release.set()
        with pytest.raises(SandboxDestroyError):
            await adapter.drain_cleanup()
        await adapter.drain_cleanup()
        return cleanup, factory.destroy_count, quarantined, not adapter._cleanup_quarantine

    cleanup, destroy_count, quarantined, drained = asyncio.run(scenario())
    assert cleanup == "failed"
    assert destroy_count == 1
    assert quarantined == 1
    assert drained is True


def test_shutdown_waits_for_safe_completed_destroy_retry(tmp_path: Path) -> None:
    class ShutdownDestroyClient(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.first_release = asyncio.Event()
            self.second_started = asyncio.Event()
            self.second_release = asyncio.Event()

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroyed_ids.append(handle.sandbox_id)
            if self.destroy_count == 1:
                self.first_started.set()
                await self.first_release.wait()
                raise SandboxDestroyError(
                    "sandbox cleanup failed",
                    operation="destroy",
                    failure_class=FailureClass.CONNECTION_FAILED,
                    completion_known=True,
                )
            self.second_started.set()
            await self.second_release.wait()

    async def scenario() -> tuple[object, ShutdownDestroyClient, bool]:
        sdk = ShutdownDestroyClient()
        service, _, config = _service(tmp_path, sdk)
        service._destroy_retry_delay_seconds = 0.0
        await service.start()
        job = await service.submit(_prepared(config), "idem-shutdown-destroy-retry")
        await sdk.first_started.wait()
        stopping = asyncio.create_task(service.stop(timeout=1.0))
        await asyncio.sleep(0)
        sdk.first_release.set()
        try:
            await asyncio.wait_for(sdk.second_started.wait(), timeout=0.1)
            second_started = True
        except asyncio.TimeoutError:
            second_started = False
        stop_waited = second_started and not stopping.done()
        if second_started:
            sdk.second_release.set()
        await stopping
        return service.get_job(job.job_id), sdk, stop_waited

    terminal, sdk, stop_waited = asyncio.run(scenario())
    assert stop_waited is True
    assert terminal.cleanup_status == "succeeded"
    assert sdk.create_count == 1
    assert sdk.destroy_count == 2
    assert sdk.destroyed_ids == [terminal.sandbox_id, terminal.sandbox_id]


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


def test_recovery_retries_terminal_cleanup_pending_without_rewriting_audit(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()

    async def scenario() -> tuple[
        object,
        object,
        RecoverySandboxClient,
        list[object],
        list[dict[str, object]],
    ]:
        failing = FaultSandboxClient("destroy")
        service, store, config = _service(
            tmp_path,
            failing,
            telemetry=telemetry,
        )
        await service.start()
        job = await service.submit(_prepared(config), "idem-terminal-cleanup-retry")
        before = await service.wait_terminal(job.job_id)
        await service.stop()
        assert store.cleanup_pending_jobs()
        event_count = len(telemetry.snapshot()["recent_events"])

        recovery = RecoverySandboxClient()
        reopened = SandboxBrokerService(
            config,
            store,
            recovery,
            ArtifactRegistry(config.state_root, store),
            telemetry=telemetry,
        )
        await reopened.recover()
        after = reopened.get_job(job.job_id)
        recovery_events = telemetry.snapshot()["recent_events"][event_count:]
        return before, after, recovery, store.cleanup_pending_jobs(), recovery_events

    before, after, recovery, pending, recovery_events = asyncio.run(scenario())
    assert before.status is BrokerJobStatus.FAILED
    assert before.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert before.cleanup_status == "failed"
    assert after.status is before.status
    assert after.error_code == before.error_code
    assert after.warnings == before.warnings
    assert after.cleanup_status == "succeeded"
    assert recovery.recovery_calls == 1
    assert pending == []
    terminal_events = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["phase"] == "job_terminal" and event["job_id"] == before.job_id
    ]
    assert len(terminal_events) == 1
    terminal_counters = {
        key: value
        for key, value in telemetry.snapshot()["counters"].items()
        if key.startswith("terminal|")
    }
    assert sum(terminal_counters.values()) == 1
    assert [event["phase"] for event in recovery_events] == [
        "cleanup_started",
        "cleanup_completed",
    ]
    assert recovery_events[0]["attempt"] == 1
    assert recovery_events[1]["attempt"] == 1
    assert recovery_events[1]["outcome"] == "passed"
    assert recovery_events[1]["cleanup_status"] == "succeeded"
    assert recovery_events[1]["failure_class"] is None


@pytest.mark.parametrize(
    "warning",
    [
        "path=/home/runner/pose.pdbqt",
        "file:/tmp/pose.pdbqt",
        'source="/home/runner/pose.pdbqt"',
        "source=[/home/runner/pose.pdbqt]",
        r"source=C:\Users\runner\pose.pdbqt",
        r"source=\\server\share\pose.pdbqt",
        "\x1b[31mpath=/home/runner/pose.pdbqt\x1b[0m",
        "path=\x00/home/runner/pose.pdbqt",
        "prefix/home/runner/pose.pdbqt",
        r"prefixC:\Users\runner\pose.pdbqt",
        r"prefix\\server\share\pose.pdbqt",
    ],
)
def test_untrusted_warning_with_host_path_fails_scientific_gate_without_leak(
    tmp_path: Path,
    warning: str,
) -> None:
    async def scenario() -> tuple[object, SandboxBrokerService]:
        sdk = FakeSandboxClient(result_extras={"warnings": [warning]})
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
    assert warning not in repr(terminal)
    with pytest.raises(KeyError):
        service.get_manifest(terminal.job_id)


def test_scientific_fraction_warning_is_not_mistaken_for_an_absolute_path(
    tmp_path: Path,
) -> None:
    async def scenario() -> object:
        sdk = FakeSandboxClient(result_extras={"warnings": ["score ratio 1/2"]})
        service, _, config = _service(tmp_path, sdk)
        await service.start()
        job = await service.submit(_prepared(config), "idem-warning-fraction")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert terminal.warnings == ("score ratio 1/2",)


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


def test_complete_failure_terminalizes_without_reusing_rejected_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> object:
        service, store, config = _service(tmp_path, FakeSandboxClient())
        real_fail = store.fail

        def fail_complete(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise ValueError("rejected provenance")

        def fail_without_rejected_provenance(
            *args: object, **kwargs: object
        ) -> object:
            if kwargs.get("provenance") is not None:
                raise ValueError("rejected provenance")
            return real_fail(*args, **kwargs)

        monkeypatch.setattr(store, "complete", fail_complete)
        monkeypatch.setattr(store, "fail", fail_without_rejected_provenance)
        await service.start()
        job = await service.submit(_prepared(config), "idem-rejected-provenance")
        terminal = await service.wait_terminal(job.job_id, timeout=3)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.ARTIFACT_FAILED.value
    assert terminal.provenance is None


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


def test_recovery_provisioning_without_sandbox_reconciles_by_job_id(
    tmp_path: Path,
) -> None:
    class ReconcileByJobClient(RecoverySandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.reconciled_jobs: list[str] = []

        async def destroy_by_job_id(self, job_id: str) -> int:
            self.reconciled_jobs.append(job_id)
            return 1

    async def scenario() -> tuple[object, ReconcileByJobClient]:
        sdk = ReconcileByJobClient()
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
    assert sdk.reconciled_jobs == [terminal.job_id]


def test_recovery_provisioning_without_identity_fails_when_reconciliation_missing(
    tmp_path: Path,
) -> None:
    class NoReconciliationClient(RecoverySandboxClient):
        destroy_by_job_id = None

    async def scenario() -> object:
        sdk = NoReconciliationClient()
        service, store, _ = _service(tmp_path, sdk)
        job, _ = store.create_or_get(
            "idem-no-identity", "8" * 64, "trace-no-identity"
        )
        store.transition(job.job_id, BrokerJobStatus.PROVISIONING)
        await service.recover()
        terminal = store.get(job.job_id)
        assert terminal is not None
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert terminal.cleanup_status == "failed"
    assert "cleanup_failed" in terminal.warnings


def test_recovery_queued_without_sandbox_needs_no_remote_reconciliation(
    tmp_path: Path,
) -> None:
    class TrackingReconciliationClient(RecoverySandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.reconciled_jobs: list[str] = []

        async def destroy_by_job_id(self, job_id: str) -> int:
            self.reconciled_jobs.append(job_id)
            return 0

    async def scenario() -> tuple[object, TrackingReconciliationClient]:
        sdk = TrackingReconciliationClient()
        service, store, _ = _service(tmp_path, sdk)
        job, _ = store.create_or_get("idem-queued", "7" * 64, "trace-queued")
        await service.recover()
        terminal = store.get(job.job_id)
        assert terminal is not None
        return terminal, sdk

    terminal, sdk = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.cleanup_status == "succeeded"
    assert sdk.reconciled_jobs == []


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


def test_hung_create_hits_hard_deadline_and_never_claims_cleanup_success(
    tmp_path: Path,
) -> None:
    class CancellationResistantCreate(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release_create = asyncio.Event()

        async def create(self, job_id: str) -> SandboxHandle:
            self.create_count += 1
            self.started.set()
            while not self.release_create.is_set():
                try:
                    await self.release_create.wait()
                except asyncio.CancelledError:
                    continue
            return SandboxHandle(f"sandbox-{job_id}", object())

    async def scenario() -> tuple[object, CancellationResistantCreate, list[str]]:
        sdk = CancellationResistantCreate()
        service, _, config = _service(tmp_path, sdk)
        service._create_hard_timeout_seconds = 0.03
        service._destroy_hard_timeout_seconds = 0.03
        await service.start()
        job = await service.submit(_prepared(config), "idem-hung-create")
        await sdk.started.wait()
        terminal = await asyncio.wait_for(
            service.wait_terminal(job.job_id), timeout=0.5
        )
        with pytest.raises(StopIncomplete, match="shutdown is incomplete"):
            await service.stop(timeout=0.05)
        assert service._create_tasks
        sdk.release_create.set()
        await service.stop(timeout=0.5)
        await asyncio.sleep(0)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-")
            and not task.done()
        ]
        return terminal, sdk, leaked

    terminal, sdk, leaked = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.cleanup_status == "failed"
    assert "sandbox_auto_expires_within_configured_remote_lifetime" in terminal.warnings
    assert sdk.create_count == 1
    assert sdk.destroy_count == 1
    assert sdk.destroyed_ids == [f"sandbox-{terminal.job_id}"]
    assert leaked == []


def test_hung_run_and_destroy_keep_shutdown_pending_after_hard_deadlines(
    tmp_path: Path,
) -> None:
    class ResistantRunAndDestroy(FakeSandboxClient):
        def __init__(self) -> None:
            super().__init__()
            self.release_run = asyncio.Event()
            self.release_destroy = asyncio.Event()
            self.destroy_started = asyncio.Event()

        async def run(self, handle: SandboxHandle) -> SandboxCommandResult:
            del handle
            self.run_count += 1
            self.running.set()
            while not self.release_run.is_set():
                try:
                    await self.release_run.wait()
                except asyncio.CancelledError:
                    continue
            return SandboxCommandResult(0, "", "")

        async def destroy(self, handle: SandboxHandle) -> None:
            self.destroy_count += 1
            self.destroy_started.set()
            while not self.release_destroy.is_set():
                try:
                    await self.release_destroy.wait()
                except asyncio.CancelledError:
                    continue
            self.destroyed_ids.append(handle.sandbox_id)

    async def scenario() -> tuple[object, float, list[str]]:
        sdk = ResistantRunAndDestroy()
        service, _, config = _service(tmp_path, sdk)
        service._run_cancel_grace_seconds = 0.02
        service._destroy_hard_timeout_seconds = 0.03
        await service.start()
        job = await service.submit(_prepared(config), "idem-hung-run-destroy")
        await sdk.running.wait()
        started = asyncio.get_running_loop().time()
        await service.cancel(job.job_id)
        await sdk.destroy_started.wait()
        terminal = await service.wait_terminal(job.job_id)
        with pytest.raises(StopIncomplete, match="shutdown is incomplete"):
            await service.stop(timeout=0.05)
        elapsed = asyncio.get_running_loop().time() - started
        sdk.release_run.set()
        sdk.release_destroy.set()
        await service.stop(timeout=0.5)
        await asyncio.sleep(0)
        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("sandbox-broker-")
            and not task.done()
        ]
        return terminal, elapsed, leaked

    terminal, elapsed, leaked = asyncio.run(scenario())
    assert elapsed < 0.5
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert terminal.cleanup_status == "failed"
    assert leaked == []


def test_consumer_survives_store_get_baseexception_and_processes_next_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> tuple[object, object, FakeSandboxClient]:
        sdk = FakeSandboxClient()
        service, store, config = _service(tmp_path, sdk)
        await service.start()
        first = await service.submit(_prepared(config), "idem-flaky-get-first")
        original_get = store.get
        failed = False

        def flaky_get(job_id: str) -> object:
            nonlocal failed
            task = asyncio.current_task()
            if (
                job_id == first.job_id
                and not failed
                and task is not None
                and task.get_name() == "sandbox-broker-consumer"
            ):
                failed = True
                raise KeyboardInterrupt("private-marker /private/path")
            return original_get(job_id)

        monkeypatch.setattr(store, "get", flaky_get)
        second = await service.submit(_prepared(config), "idem-flaky-get-second")
        first_terminal = await service.wait_terminal(first.job_id, timeout=1)
        second_terminal = await service.wait_terminal(second.job_id, timeout=3)
        await service.stop(timeout=1)
        return first_terminal, second_terminal, sdk

    first, second, sdk = asyncio.run(scenario())
    assert first.status is BrokerJobStatus.FAILED
    assert first.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert second.status is BrokerJobStatus.SUCCEEDED
    assert sdk.run_count == 1


def test_stop_sanitizes_internal_baseexception_without_context_or_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> StopIncomplete:
        service, store, _ = _service(tmp_path, FakeSandboxClient())
        await service.start()

        def fail_active_jobs() -> object:
            raise RuntimeError("private-marker C:\\private\\secret.txt")

        monkeypatch.setattr(store, "active_jobs", fail_active_jobs)
        try:
            await service.stop(timeout=0.2)
        except StopIncomplete as raised:
            return raised
        raise AssertionError("stop did not fail closed")

    failure = asyncio.run(scenario())
    assert "private-marker" not in str(failure)
    assert "private-marker" not in repr(failure)
    assert failure.__cause__ is None
    assert failure.__context__ is None


def test_stop_stays_incomplete_until_adapter_quarantine_is_empty(tmp_path: Path) -> None:
    class SlowAdapterFactory(_UploadRecordingFactory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = False
            self.release = asyncio.Event()

        async def destroy_by_id(self, sandbox_id: str) -> None:
            del sandbox_id
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                await self.release.wait()
            raise RuntimeError("private-marker /private/zombie")

    async def scenario() -> tuple[OpenSandboxClient, SlowAdapterFactory, SandboxBrokerService]:
        config = _config(tmp_path)
        factory = SlowAdapterFactory()
        client = OpenSandboxClient(config, sandbox_factory=factory)
        client._cleanup_hard_timeout_seconds = 0.02
        client._cleanup_drain_margin_seconds = 0.05
        store = BrokerStore(config.state_root / "broker.sqlite")
        service = SandboxBrokerService(
            config,
            store,
            client,
            ArtifactRegistry(config.state_root, store),
        )
        cleanup = asyncio.create_task(client.destroy_by_id("sandbox-stop-drain"))
        await factory.started.wait()
        with pytest.raises(StopIncomplete, match="shutdown is incomplete"):
            await service.stop(timeout=0.5)
        with pytest.raises(SandboxDestroyError):
            await cleanup
        assert len(client._cleanup_in_flight) == 1
        assert len(client._cleanup_quarantine) == 1
        assert service._cleanup_tasks == {}
        with pytest.raises(StopIncomplete, match="shutdown is incomplete"):
            await service.stop(timeout=0.5)
        factory.release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        await service.stop(timeout=0.5)
        return client, factory, service

    client, factory, service = asyncio.run(scenario())
    assert factory.cancelled is True
    assert client._cleanup_in_flight == {}
    assert client._cleanup_quarantine == set()
    assert service._cleanup_tasks == {}


def test_terminal_wait_index_is_constant_after_one_thousand_jobs(
    tmp_path: Path,
) -> None:
    async def scenario() -> SandboxBrokerService:
        service, store, _ = _service(tmp_path, FakeSandboxClient())
        job_ids: list[str] = []
        for index in range(1000):
            job, _ = store.create_or_get(
                f"idem-bounded-{index}",
                hashlib.sha256(str(index).encode("ascii")).hexdigest(),
                f"trace-bounded-{index}",
            )
            store.fail(
                job.job_id,
                BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
                cleanup_status="succeeded",
            )
            job_ids.append(job.job_id)
        for job_id in job_ids:
            await service.wait_terminal(job_id, timeout=2)
        return service

    service = asyncio.run(scenario())
    assert not hasattr(service, "_terminal_events")
    assert service._job_tasks == {}
    assert service._create_tasks == {}
    assert service._cleanup_tasks == {}


@pytest.mark.parametrize(
    "warning",
    [
        "prefix/data/model.bin",
        "prefix/srv/worker/result.json",
        "prefix/proc/self/status",
        "prefix/dev/null",
        "prefix/\u200bdata/model.bin",
        "to\u200bken=private-marker",
        "/数据/结果",
        "prefix/é/ß",
        "prefix/１２/结果",
        "C:\\数据\\结果",
        r"\\服务器\共享\结果",
        "1\u29f52",
        "1\u29f92",
        "1\u20442",
        "1\u22152",
        "1\u29f82",
        "1\uff0f2",
        "1\ufe682",
        "1\uff3c2",
        "prefix\uff1a数据",
        "prefix\ufe13数据",
        "１/２",
    ],
)
def test_unicode_reassembled_warning_fails_end_to_end_without_manifest(
    tmp_path: Path,
    warning: str,
) -> None:
    async def scenario() -> tuple[object, SandboxBrokerService]:
        service, _, config = _service(
            tmp_path,
            FakeSandboxClient(result_extras={"warnings": [warning]}),
        )
        await service.start()
        job = await service.submit(_prepared(config), "idem-unicode-warning")
        terminal = await service.wait_terminal(job.job_id)
        await service.stop()
        return terminal, service

    terminal, service = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.warnings == ()
    assert warning not in repr(terminal)
    with pytest.raises(KeyError):
        service.get_manifest(terminal.job_id)


def test_successful_job_emits_exact_ordered_logical_lifecycle(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()

    async def scenario() -> tuple[object, dict[str, object]]:
        service, _, config = _service(
            tmp_path,
            FakeSandboxClient(),
            telemetry=telemetry,
        )
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-telemetry-success")
        terminal = await service.wait_terminal(submitted.job_id)
        snapshot = telemetry.snapshot()
        await service.stop()
        return terminal, snapshot

    terminal, snapshot = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    events = snapshot["recent_events"]
    assert isinstance(events, list)
    assert [event["phase"] for event in events] == [
        "job_received",
        "queue_entered",
        "provisioning_started",
        "provisioning_completed",
        "upload_started",
        "upload_completed",
        "command_started",
        "command_completed",
        "validation_started",
        "validation_completed",
        "cleanup_started",
        "cleanup_completed",
        "job_terminal",
    ]
    assert events[3]["outcome"] == "passed"
    assert events[9]["outcome"] == "passed"
    assert events[9]["vina_version"] == "1.2.5"
    assert events[9]["meeko_version"] == "0.6.1"
    assert events[-1]["outcome"] == "passed"
    assert events[-1]["cleanup_status"] == "succeeded"
    assert snapshot["counters"]["terminal|succeeded|none"] == 1


def test_command_failure_is_single_shot_then_cleans_and_terminalizes(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()
    sdk = FaultSandboxClient("nonzero")

    async def scenario() -> object:
        service, _, config = _service(tmp_path, sdk, telemetry=telemetry)
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-command-failure")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.COMMAND_FAILED.value
    assert sdk.run_count == 1
    events = telemetry.snapshot()["recent_events"]
    phases = [event["phase"] for event in events]
    command_completed = phases.index("command_completed")
    assert events[command_completed]["outcome"] == "failed"
    assert phases[command_completed:] == [
        "command_completed",
        "cleanup_started",
        "cleanup_completed",
        "job_terminal",
    ]


def test_open_breaker_reuses_existing_job_but_rejects_new_submission(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()
    breaker = ControlPlaneCircuitBreaker()

    async def scenario() -> tuple[object, object, BrokerStore, BrokerFailure]:
        service, store, config = _service(
            tmp_path,
            FakeSandboxClient(),
            telemetry=telemetry,
            circuit_breaker=breaker,
        )
        await service.start()
        first = await service.submit(_prepared(config), "idem-breaker-reuse")
        terminal = await service.wait_terminal(first.job_id)
        _trip_breaker(breaker)
        reused = await service.submit(_prepared(config), "idem-breaker-reuse")
        with pytest.raises(BrokerFailure) as raised:
            await service.submit(_prepared(config), "idem-breaker-new")
        await service.stop()
        return terminal, reused, store, raised.value

    terminal, reused, store, failure = asyncio.run(scenario())
    assert reused.job_id == terminal.job_id
    assert failure.code is BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
    rejected = store.get_by_idempotency("idem-breaker-new")
    assert rejected is None
    assert store.active_jobs() == []
    assert sum(
        event["phase"] == "job_received"
        for event in telemetry.snapshot()["recent_events"]
    ) == 1


def test_telemetry_baseexceptions_cannot_change_success_or_skip_cleanup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = FakeSandboxClient()

    async def scenario() -> object:
        service, _, config = _service(
            tmp_path,
            sdk,
            telemetry=BaseExceptionTelemetry(),
        )
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-telemetry-errors")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert terminal.cleanup_status == "succeeded"
    assert sdk.destroy_count == 1
    assert "telemetry-sensitive-detail" not in caplog.text
    assert "sandbox broker telemetry observation failed" in caplog.text


def test_breaker_rejection_uses_atomic_discard_not_terminal_delete_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = ControlPlaneCircuitBreaker()
    _trip_breaker(breaker)

    async def scenario() -> tuple[BrokerStore, BrokerFailure]:
        service, store, config = _service(
            tmp_path,
            FakeSandboxClient(),
            circuit_breaker=breaker,
        )
        monkeypatch.setattr(
            store,
            "fail",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
        )
        monkeypatch.setattr(
            store,
            "delete_terminal_audit",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
        )
        await service.start()
        with pytest.raises(BrokerFailure) as raised:
            await service.submit(_prepared(config), "idem-atomic-reject")
        await service.stop()
        return store, raised.value

    store, failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
    assert store.get_by_idempotency("idem-atomic-reject") is None
    assert store.active_jobs() == []


def test_atomic_discard_failure_falls_back_to_owned_authoritative_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = ControlPlaneCircuitBreaker()
    _trip_breaker(breaker)
    sdk = FakeSandboxClient()

    async def scenario() -> object:
        service, store, config = _service(
            tmp_path,
            sdk,
            circuit_breaker=breaker,
        )
        monkeypatch.setattr(
            store,
            "discard_pristine_queued",
            lambda expected: (_ for _ in ()).throw(RuntimeError("blocked")),
            raising=False,
        )
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-discard-fallback")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        assert store.active_jobs() == []
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert sdk.create_count == 0


def test_upload_discard_failure_transfers_ownership_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = ControlPlaneCircuitBreaker()
    _trip_breaker(breaker)
    sdk = FakeSandboxClient()

    async def scenario() -> tuple[object, BrokerConfig]:
        service, store, config = _service(
            tmp_path,
            sdk,
            circuit_breaker=breaker,
        )

        def fail_discard(record: object) -> bool:
            del record
            raise sqlite3.OperationalError("injected discard failure")

        def staging_must_not_run(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("staging ran before ownership transfer")

        monkeypatch.setattr(store, "discard_pristine_queued", fail_discard)
        monkeypatch.setattr(service_module, "stage_input", staging_must_not_run)
        await service.start()
        submitted = await service.submit_uploads(
            DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
            type("Upload", (), {"filename": "receptor.pdb", "file": io.BytesIO(b"ATOM\n")})(),
            type("Upload", (), {"filename": "ligand.sdf", "file": io.BytesIO(VALID_SDF)})(),
            "idem-upload-discard-fallback",
        )
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal, config

    terminal, config = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert sdk.create_count == 0
    assert not (config.state_root / "jobs" / terminal.job_id).exists()


def test_upload_rejection_fallback_never_provisions_after_breaker_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    breaker = ControlPlaneCircuitBreaker(monotonic=lambda: clock[0])
    sdk = FakeSandboxClient(block=True)
    removed_job_ids: list[str] = []

    async def scenario() -> tuple[object, BrokerConfig]:
        service, store, config = _service(
            tmp_path,
            sdk,
            circuit_breaker=breaker,
        )
        original_remove = service._remove_job_tree

        def record_remove(job_id: str) -> bool:
            removed_job_ids.append(job_id)
            return original_remove(job_id)

        def fail_discard(record: object) -> bool:
            del record
            raise sqlite3.OperationalError("injected discard failure")

        monkeypatch.setattr(service, "_remove_job_tree", record_remove)
        monkeypatch.setattr(store, "discard_pristine_queued", fail_discard)
        await service.start()
        first = await service.submit(_prepared(config), "idem-cooldown-owner")
        await asyncio.wait_for(sdk.running.wait(), timeout=1.0)
        _trip_breaker(breaker)

        rejected = await service.submit_uploads(
            DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
            type(
                "Upload",
                (),
                {"filename": "receptor.pdb", "file": io.BytesIO(b"ATOM\n")},
            )(),
            type(
                "Upload",
                (),
                {"filename": "ligand.sdf", "file": io.BytesIO(VALID_SDF)},
            )(),
            "idem-upload-cooldown-fallback",
        )
        clock[0] = 31.0
        sdk.release.set()
        await service.wait_terminal(first.job_id, timeout=3.0)
        terminal = await service.wait_terminal(rejected.job_id, timeout=3.0)
        await service.stop()
        return terminal, config

    terminal, config = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert sdk.create_count == 1
    assert sdk.run_count == 1
    assert removed_job_ids.count(terminal.job_id) == 1
    assert not (config.state_root / "jobs" / terminal.job_id).exists()


def test_queue_full_rejection_persistence_failure_remains_owned_until_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = ControlPlaneCircuitBreaker()
    sdk = FakeSandboxClient(block=True)
    allow_persistence = [False]
    rejected_job_ids: list[str] = []

    async def scenario() -> tuple[object, object, int]:
        service, store, config = _service(
            tmp_path,
            sdk,
            queue_capacity=1,
            circuit_breaker=breaker,
        )
        await service.start()
        running = await service.submit(_prepared(config), "idem-pending-running")
        await asyncio.wait_for(sdk.running.wait(), timeout=1.0)
        queued = await service.submit(_prepared(config), "idem-pending-queued")
        del queued
        _trip_breaker(breaker)

        original_fail = store.fail

        def fail_discard(record: object) -> bool:
            del record
            raise sqlite3.OperationalError("injected discard failure")

        def controlled_fail(job_id: str, *args: object, **kwargs: object) -> object:
            if job_id not in {running.job_id} and not allow_persistence[0]:
                rejected_job_ids.append(job_id)
                raise sqlite3.OperationalError("injected terminal failure")
            return original_fail(job_id, *args, **kwargs)

        monkeypatch.setattr(store, "discard_pristine_queued", fail_discard)
        monkeypatch.setattr(store, "fail", controlled_fail)
        submitted = await service.submit_uploads(
            DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
            type(
                "Upload",
                (),
                {"filename": "receptor.pdb", "file": io.BytesIO(b"ATOM\n")},
            )(),
            type(
                "Upload",
                (),
                {"filename": "ligand.sdf", "file": io.BytesIO(VALID_SDF)},
            )(),
            "idem-pending-rejection",
        )
        assert submitted.job_id in service._pending_rejections
        assert submitted.job_id in service._rejected_work

        reused = await service.submit_uploads(
            DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
            type(
                "Upload",
                (),
                {"filename": "receptor.pdb", "file": io.BytesIO(b"ATOM\n")},
            )(),
            type(
                "Upload",
                (),
                {"filename": "ligand.sdf", "file": io.BytesIO(VALID_SDF)},
            )(),
            "idem-pending-rejection",
        )
        assert reused.job_id == submitted.job_id
        assert list(service._pending_rejections) == [submitted.job_id]

        allow_persistence[0] = True
        await service._drain_pending_rejections()
        terminal = await service.wait_terminal(submitted.job_id, timeout=3.0)
        assert submitted.job_id not in service._pending_rejections
        assert submitted.job_id not in service._rejected_work
        sdk.release.set()
        await service.wait_terminal(running.job_id, timeout=3.0)
        await service.stop()
        return terminal, reused, len(set(rejected_job_ids))

    terminal, reused, rejected_count = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.QUEUE_SATURATED.value
    assert reused.job_id == terminal.job_id
    assert rejected_count == 1
    assert sdk.create_count == 1
    assert sdk.run_count == 1


def test_persistent_pending_rejection_blocks_stop_and_restart_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = ControlPlaneCircuitBreaker()
    sdk = FakeSandboxClient(block=True)
    rejected_job_id = [""]

    async def scenario() -> tuple[object, int, int]:
        service, store, config = _service(
            tmp_path,
            sdk,
            queue_capacity=1,
            circuit_breaker=breaker,
        )
        await service.start()
        running = await service.submit(_prepared(config), "idem-persistent-running")
        await asyncio.wait_for(sdk.running.wait(), timeout=1.0)
        queued = await service.submit(_prepared(config), "idem-persistent-queued")
        _trip_breaker(breaker)
        original_fail = store.fail

        def fail_discard(record: object) -> bool:
            del record
            raise sqlite3.OperationalError("injected discard failure")

        def persistent_fail(
            job_id: str,
            error_code: BrokerErrorCode,
            *args: object,
            **kwargs: object,
        ) -> object:
            if (
                error_code is BrokerErrorCode.QUEUE_SATURATED
                or job_id == rejected_job_id[0]
            ):
                rejected_job_id[0] = job_id
                raise sqlite3.OperationalError("injected terminal failure")
            return original_fail(job_id, error_code, *args, **kwargs)

        monkeypatch.setattr(store, "discard_pristine_queued", fail_discard)
        monkeypatch.setattr(store, "fail", persistent_fail)
        submitted = await service.submit_uploads(
            DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
            type(
                "Upload",
                (),
                {"filename": "receptor.pdb", "file": io.BytesIO(b"ATOM\n")},
            )(),
            type(
                "Upload",
                (),
                {"filename": "ligand.sdf", "file": io.BytesIO(VALID_SDF)},
            )(),
            "idem-persistent-rejection",
        )
        assert rejected_job_id == [submitted.job_id]
        pending = service._pending_rejections[submitted.job_id]
        await asyncio.wait_for(asyncio.shield(pending), timeout=1.0)
        assert pending.done()
        assert submitted.job_id in service._pending_rejections
        active = store.get(submitted.job_id)
        assert active is not None and active.status not in TERMINAL_STATUSES

        sdk.release.set()
        await service.wait_terminal(running.job_id, timeout=3.0)
        await service.wait_terminal(queued.job_id, timeout=3.0)
        with pytest.raises(StopIncomplete):
            await service.stop(timeout=1.0)
        assert submitted.job_id in service._pending_rejections

        monkeypatch.setattr(store, "fail", original_fail)
        reopened_store = BrokerStore(config.state_root / "broker.sqlite")
        restarted_sdk = FakeSandboxClient()
        restarted = SandboxBrokerService(
            config,
            reopened_store,
            restarted_sdk,
            ArtifactRegistry(config.state_root, reopened_store),
        )
        await restarted.recover()
        terminal = restarted.get_job(submitted.job_id)
        await restarted.stop()
        return terminal, sdk.create_count, restarted_sdk.create_count

    terminal, original_creates, restarted_creates = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert original_creates == 1
    assert restarted_creates == 0


def test_pending_rejection_capacity_is_reserved_before_durable_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = ControlPlaneCircuitBreaker()
    sdk = FakeSandboxClient(block=True)
    allow_persistence = [False]

    def upload(name: str, payload: bytes) -> object:
        return type(
            "Upload",
            (),
            {"filename": name, "file": io.BytesIO(payload)},
        )()

    async def scenario() -> tuple[int, int, int]:
        service, store, config = _service(
            tmp_path,
            sdk,
            queue_capacity=2,
            circuit_breaker=breaker,
        )
        await service.start()
        running = await service.submit(_prepared(config), "idem-capacity-running")
        await asyncio.wait_for(sdk.running.wait(), timeout=1.0)
        queued = [
            await service.submit(_prepared(config), f"idem-capacity-queued-{index}")
            for index in range(2)
        ]
        _trip_breaker(breaker)
        original_fail = store.fail

        def fail_discard(record: object) -> bool:
            del record
            raise sqlite3.OperationalError("injected discard failure")

        def persistent_fail(
            job_id: str,
            error_code: BrokerErrorCode,
            *args: object,
            **kwargs: object,
        ) -> object:
            if (
                error_code is BrokerErrorCode.QUEUE_SATURATED
                and not allow_persistence[0]
            ):
                raise sqlite3.OperationalError("injected terminal failure")
            return original_fail(job_id, error_code, *args, **kwargs)

        monkeypatch.setattr(store, "discard_pristine_queued", fail_discard)
        monkeypatch.setattr(store, "fail", persistent_fail)

        outcomes = await asyncio.gather(
            *(
                service.submit_uploads(
                    DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
                    upload("receptor.pdb", b"ATOM\n"),
                    upload("ligand.sdf", VALID_SDF),
                    f"idem-capacity-rejected-{index}",
                )
                for index in range(5)
            ),
            return_exceptions=True,
        )
        accepted = [
            (index, item)
            for index, item in enumerate(outcomes)
            if not isinstance(item, BaseException)
        ]
        rejected = [
            (index, item)
            for index, item in enumerate(outcomes)
            if isinstance(item, BrokerFailure)
        ]

        assert len(accepted) == 2
        assert len(rejected) == 3
        assert {
            item.code for _, item in rejected
        } == {BrokerErrorCode.OPENSANDBOX_UNAVAILABLE}
        assert len(service._pending_rejections) <= config.queue_capacity
        assert service._pending_rejection_reservations == 0
        assert len(store.active_jobs()) == 3 + config.queue_capacity
        for index, _ in rejected:
            assert store.get_by_idempotency(
                f"idem-capacity-rejected-{index}"
            ) is None

        first_index, first = accepted[0]
        reused = await service.submit_uploads(
            DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
            upload("receptor.pdb", b"ATOM\n"),
            upload("ligand.sdf", VALID_SDF),
            f"idem-capacity-rejected-{first_index}",
        )
        assert reused.job_id == first.job_id
        with pytest.raises(BrokerFailure) as conflict:
            await service.submit_uploads(
                DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
                upload("receptor.pdb", b"ATOM\n"),
                upload("ligand.sdf", VALID_SDF + b"\n"),
                f"idem-capacity-rejected-{first_index}",
            )
        assert conflict.value.code is BrokerErrorCode.IDEMPOTENCY_CONFLICT
        assert len(service._pending_rejections) <= config.queue_capacity
        for _, item in accepted:
            assert not (config.state_root / "jobs" / item.job_id).exists()

        allow_persistence[0] = True
        await service._drain_pending_rejections()
        terminals = [
            await service.wait_terminal(item.job_id, timeout=2.0)
            for _, item in accepted
        ]
        assert all(
            item.error_code == BrokerErrorCode.QUEUE_SATURATED.value
            for item in terminals
        )
        assert service._pending_rejections == {}

        sdk.release.set()
        await service.wait_terminal(running.job_id, timeout=3.0)
        for item in queued:
            await service.wait_terminal(item.job_id, timeout=3.0)
        await service.stop()
        return len(accepted), len(store.active_jobs()), sdk.create_count

    accepted_count, active_count, create_count = asyncio.run(scenario())
    assert accepted_count == 2
    assert active_count == 0
    assert create_count == 1


def test_breaker_rejected_upload_cleanup_runs_once_and_reused_row_is_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    breaker = ControlPlaneCircuitBreaker()
    _trip_breaker(breaker)

    async def scenario() -> tuple[int, BrokerStore]:
        service, store, _ = _service(
            tmp_path,
            FakeSandboxClient(),
            circuit_breaker=breaker,
        )
        removals = 0
        original_remove = service._remove_job_tree

        def counted_remove(job_id: str) -> bool:
            nonlocal removals
            removals += 1
            return original_remove(job_id)

        monkeypatch.setattr(service, "_remove_job_tree", counted_remove)
        await service.start()
        receptor = type(
            "Upload",
            (),
            {"filename": "receptor.pdb", "file": io.BytesIO(b"ATOM\n")},
        )()
        ligand = type(
            "Upload",
            (),
            {"filename": "ligand.sdf", "file": io.BytesIO(VALID_SDF)},
        )()
        with pytest.raises(BrokerFailure) as raised:
            await service.submit_uploads(
                DockingParameters(center=[1, 2, 3], size=[20, 20, 20]),
                receptor,
                ligand,
                "idem-upload-atomic-reject",
            )
        await service.stop()
        assert raised.value.code is BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
        return removals, store

    removals, store = asyncio.run(scenario())
    assert removals == 1
    assert store.get_by_idempotency("idem-upload-atomic-reject") is None


def test_queued_job_obeys_breaker_opened_before_authoritative_create(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()
    breaker = ControlPlaneCircuitBreaker()
    sdk = FakeSandboxClient(block=True)

    async def scenario() -> tuple[object, object]:
        service, _, config = _service(
            tmp_path,
            sdk,
            telemetry=telemetry,
            circuit_breaker=breaker,
        )
        await service.start()
        first = await service.submit(_prepared(config), "idem-breaker-first")
        await sdk.running.wait()
        second = await service.submit(_prepared(config), "idem-breaker-queued")
        _trip_breaker(breaker)
        sdk.release.set()
        first_terminal = await service.wait_terminal(first.job_id)
        second_terminal = await service.wait_terminal(second.job_id)
        await service.stop()
        return first_terminal, second_terminal

    first_terminal, second_terminal = asyncio.run(scenario())
    assert first_terminal.status is BrokerJobStatus.SUCCEEDED
    assert second_terminal.status is BrokerJobStatus.FAILED
    assert second_terminal.error_code == BrokerErrorCode.OPENSANDBOX_UNAVAILABLE.value
    assert second_terminal.cleanup_status == "succeeded"
    assert sdk.create_count == 1
    second_events = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["job_id"] == second_terminal.job_id
    ]
    assert [event["phase"] for event in second_events] == [
        "job_received",
        "queue_entered",
        "provisioning_started",
        "provisioning_completed",
        "cleanup_started",
        "cleanup_completed",
        "job_terminal",
    ]
    assert second_events[3]["failure_class"] == "unknown_control_plane_failure"


def test_breaker_records_only_final_provisioning_result_and_create_retry(
    tmp_path: Path,
) -> None:
    class RetryCreateClient(FakeSandboxClient):
        async def create(self, job_id: str) -> SandboxHandle:
            self.create_count += 1
            if self.create_count == 1:
                raise SandboxCreateError(
                    "classified create failure",
                    operation="create",
                    failure_class=FailureClass.CONNECTION_FAILED,
                )
            return SandboxHandle(f"sandbox-{job_id}", object())

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            return 0

    telemetry = RecordingTelemetry()
    breaker = TrackingCircuitBreaker()
    sdk = RetryCreateClient()

    async def scenario() -> object:
        service, _, config = _service(
            tmp_path,
            sdk,
            telemetry=telemetry,
            circuit_breaker=breaker,
        )
        service._create_retry_delay_seconds = 0.0
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-create-retry-metric")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert sdk.create_count == 2
    assert sdk.run_count == 1
    assert breaker.successes == 1
    assert breaker.failures == []
    counters = telemetry.snapshot()["counters"]
    assert counters["retry|create|attempted"] == 1
    assert counters["retry|create|succeeded"] == 1
    assert counters["control_failure|create|connection_failed"] == 1
    assert "retry|create|exhausted" not in counters

    from scripts import run_opensandbox_stability_soak as soak

    lifecycle_events = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["job_id"] == terminal.job_id
    ]
    evidence = soak.evaluate_lifecycle(lifecycle_events, result_succeeded=True)

    assert evidence.events_complete is True
    assert evidence.failure_codes == ("broker_retry_observed",)
    assert evidence.failure_class == "none"


def test_final_classified_create_failure_records_breaker_failure_once(
    tmp_path: Path,
) -> None:
    class FinalCreateFailureClient(FakeSandboxClient):
        async def create(self, job_id: str) -> SandboxHandle:
            del job_id
            self.create_count += 1
            raise SandboxCreateError(
                "classified create failure",
                operation="create",
                failure_class=FailureClass.PROXY_502,
            )

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            return 0

    telemetry = RecordingTelemetry()
    breaker = TrackingCircuitBreaker()
    sdk = FinalCreateFailureClient()

    async def scenario() -> object:
        service, _, config = _service(
            tmp_path,
            sdk,
            telemetry=telemetry,
            circuit_breaker=breaker,
        )
        service._create_retry_delay_seconds = 0.0
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-create-final-failure")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.error_code == BrokerErrorCode.PROVISIONING_FAILED.value
    assert sdk.create_count == 2
    assert breaker.successes == 0
    assert breaker.failures == [FailureClass.PROXY_502]
    assert telemetry.snapshot()["counters"]["retry|create|exhausted"] == 1
    assert telemetry.snapshot()["counters"][
        "control_failure|create|proxy_502"
    ] == 2
    events = telemetry.snapshot()["recent_events"]
    provisioning_events = [
        event
        for event in events
        if event["phase"] in {"provisioning_started", "provisioning_completed"}
    ]
    assert [event["attempt"] for event in provisioning_events] == [1, 1, 2, 2]
    assert [event["outcome"] for event in provisioning_events] == [
        None,
        "failed",
        None,
        "failed",
    ]

    from scripts import run_opensandbox_stability_soak as soak

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.events_complete is True
    assert evidence.cleanup_status == "succeeded"
    assert evidence.terminal_outcome == "failed"
    assert evidence.failure_class == "proxy_502"
    assert "events_incomplete" not in evidence.failure_codes


def test_cancellation_before_create_releases_authoritative_permit(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()
    breaker = TrackingCircuitBreaker()
    sdk = FakeSandboxClient()

    async def scenario() -> object:
        service, _, config = _service(
            tmp_path,
            sdk,
            telemetry=telemetry,
            circuit_breaker=breaker,
        )
        await service.start()
        prepared = _prepared(config)

        def cancel_at_acquire() -> None:
            for event in service._cancel_events.values():
                event.set()

        breaker.on_acquire = cancel_at_acquire
        submitted = await service.submit(prepared, "idem-cancel-before-create")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.CANCELLED
    assert sdk.create_count == 0
    assert breaker.releases == 1
    assert breaker.successes == 0
    assert breaker.failures == []


def test_local_attach_failures_do_not_count_as_remote_create_failures(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()
    breaker = TrackingCircuitBreaker()
    sdk = FakeSandboxClient()

    async def scenario() -> list[object]:
        service, store, config = _service(
            tmp_path,
            sdk,
            telemetry=telemetry,
            circuit_breaker=breaker,
        )

        def fail_attach(job_id: str, sandbox_id: str) -> object:
            del job_id, sandbox_id
            raise RuntimeError("local sqlite failure")

        store.attach_sandbox = fail_attach  # type: ignore[method-assign]
        await service.start()
        terminals: list[object] = []
        for index in range(3):
            submitted = await service.submit(
                _prepared(config),
                f"idem-local-attach-{index}",
            )
            terminals.append(await service.wait_terminal(submitted.job_id))
        await service.stop()
        return terminals

    terminals = asyncio.run(scenario())
    assert [item.error_code for item in terminals] == [
        BrokerErrorCode.PROVISIONING_FAILED.value,
    ] * 3
    assert breaker.successes == 3
    assert breaker.failures == []
    assert breaker.state.value == "closed"
    assert sdk.create_count == 3
    assert sdk.destroy_count == 3
    assert len(sdk.destroyed_ids) == len(set(sdk.destroyed_ids)) == 3
    assert not any(
        key.startswith("control_failure|create|")
        for key in telemetry.snapshot()["counters"]
    )


@pytest.mark.parametrize("raise_after_mutation", [False, True])
def test_half_open_success_completion_failure_cannot_wedge_probe(
    tmp_path: Path,
    raise_after_mutation: bool,
) -> None:
    clock = [0.0]

    class CompletionFailureBreaker(ControlPlaneCircuitBreaker):
        def __init__(self) -> None:
            super().__init__(monotonic=lambda: clock[0])
            self.fail_once = True

        def record_success(self, permit: object) -> None:
            if self.fail_once:
                self.fail_once = False
                if raise_after_mutation:
                    super().record_success(permit)  # type: ignore[arg-type]
                raise KeyboardInterrupt
            super().record_success(permit)  # type: ignore[arg-type]

    breaker = CompletionFailureBreaker()
    _trip_breaker(breaker)
    clock[0] = 31.0
    sdk = FakeSandboxClient()

    async def scenario() -> object:
        service, _, config = _service(
            tmp_path,
            sdk,
            telemetry=RecordingTelemetry(),
            circuit_breaker=breaker,
        )
        await service.start()
        submitted = await service.submit(
            _prepared(config),
            "idem-half-open-completion",
        )
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert sdk.destroy_count == 1
    assert breaker.state.value == "closed"
    next_permit = breaker.acquire()
    assert next_permit is not None
    breaker.release(next_permit)


@pytest.mark.parametrize(
    ("sdk", "expected_error"),
    [
        (FakeSandboxClient(valid_result=False), BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID),
        (
            FaultSandboxClient(
                "create",
                failure=SandboxCreateError(
                    "resource limited",
                    operation="create",
                    failure_class=FailureClass.RESOURCE_LIMIT,
                ),
            ),
            BrokerErrorCode.CLEANUP_FAILED,
        ),
    ],
)
def test_scientific_and_resource_failures_do_not_trip_breaker(
    tmp_path: Path,
    sdk: FakeSandboxClient,
    expected_error: BrokerErrorCode,
) -> None:
    breaker = TrackingCircuitBreaker()
    telemetry = RecordingTelemetry()

    async def scenario() -> object:
        service, _, config = _service(
            tmp_path,
            sdk,
            telemetry=telemetry,
            circuit_breaker=breaker,
        )
        service._create_retry_delay_seconds = 0.0
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-nonbreaker-failure")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.error_code == expected_error.value
    assert breaker.failures == []
    assert breaker.state.value == "closed"
    if isinstance(sdk, FaultSandboxClient):
        assert telemetry.snapshot()["counters"][
            "control_failure|create|resource_limit"
        ] == 1


def test_invalid_input_never_acquires_or_updates_breaker(tmp_path: Path) -> None:
    breaker = TrackingCircuitBreaker()

    async def scenario() -> BrokerFailure:
        service, _, _ = _service(
            tmp_path,
            FakeSandboxClient(),
            telemetry=RecordingTelemetry(),
            circuit_breaker=breaker,
        )
        await service.start()
        with pytest.raises(BrokerFailure) as raised:
            await service.submit(object(), "idem-invalid-no-breaker")  # type: ignore[arg-type]
        await service.stop()
        return raised.value

    failure = asyncio.run(scenario())
    assert failure.code is BrokerErrorCode.INVALID_INPUT
    assert breaker.successes == 0
    assert breaker.failures == []
    assert breaker.releases == 0


def test_runtime_gauges_cover_queue_job_cleanup_isolation_and_terminal(
    tmp_path: Path,
) -> None:
    telemetry = RecordingTelemetry()

    async def scenario() -> object:
        service, _, config = _service(
            tmp_path,
            FakeSandboxClient(),
            telemetry=telemetry,
        )
        service._run_cancel_grace_seconds = 0.01
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-runtime-gauges")
        terminal = await service.wait_terminal(submitted.job_id)

        release = asyncio.Event()

        async def resistant() -> None:
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue

        task = asyncio.create_task(resistant())
        await asyncio.sleep(0)
        await service._cancel_with_grace(task)
        release.set()
        await task
        await asyncio.sleep(0)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    updates = telemetry.runtime_updates
    assert any(item["queue_depth"] == 1 for item in updates)
    assert any(item["queue_depth"] == 0 for item in updates)
    assert any(item["active_job_count"] == 1 for item in updates)
    assert any(item["active_job_count"] == 0 for item in updates)
    assert any(item["cleanup_task_count"] == 1 for item in updates)
    assert any(item["cleanup_task_count"] == 0 for item in updates)
    assert any(item["isolated_task_count"] == 1 for item in updates)
    assert updates[-1] == {
        "queue_depth": 0,
        "active_job_count": 0,
        "cleanup_task_count": 0,
        "isolated_task_count": 0,
        "breaker_state": "closed",
    }


def test_diagnostics_and_prometheus_share_injected_runtime_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.sandbox_broker.app as app_module

    telemetry = RecordingTelemetry()
    breaker = ControlPlaneCircuitBreaker()
    service, _, _ = _service(
        tmp_path / "service",
        FakeSandboxClient(),
        telemetry=telemetry,
        circuit_breaker=breaker,
    )
    diagnostics = service.diagnostics()
    assert diagnostics == {
        "schema_version": 1,
        "telemetry": telemetry.snapshot(),
        "circuit_breaker": breaker.snapshot(),
    }
    assert service.prometheus_text() == telemetry.prometheus_text()

    captured: dict[str, object] = {}

    def fake_client(config: BrokerConfig, *, telemetry: object) -> object:
        del config
        captured["client_telemetry"] = telemetry
        return object()

    def fake_service(
        config: BrokerConfig,
        store: BrokerStore,
        client: object,
        artifacts: ArtifactRegistry,
        *,
        telemetry: object,
        circuit_breaker: object,
    ) -> object:
        del config, store, client, artifacts
        captured["service_telemetry"] = telemetry
        captured["breaker"] = circuit_breaker
        return object()

    monkeypatch.setattr(app_module, "OpenSandboxClient", fake_client)
    monkeypatch.setattr(app_module, "SandboxBrokerService", fake_service)
    config = _config(tmp_path / "app")
    app_module._default_service(config)
    assert isinstance(captured["client_telemetry"], BrokerTelemetry)
    assert captured["client_telemetry"] is captured["service_telemetry"]
    assert isinstance(captured["breaker"], ControlPlaneCircuitBreaker)


@pytest.mark.parametrize("scrape", ["diagnostics", "prometheus"])
def test_diagnostics_and_scrape_refresh_expired_breaker_state_coherently(
    tmp_path: Path,
    scrape: str,
) -> None:
    clock = [0.0]
    telemetry = RecordingTelemetry()
    breaker = ControlPlaneCircuitBreaker(monotonic=lambda: clock[0])
    _trip_breaker(breaker)
    service, _, _ = _service(
        tmp_path,
        FakeSandboxClient(),
        telemetry=telemetry,
        circuit_breaker=breaker,
    )
    assert breaker.snapshot()["state"] == "open"
    clock[0] = 31.0

    if scrape == "diagnostics":
        result = service.diagnostics()
        breaker_snapshot = result["circuit_breaker"]
        telemetry_snapshot = result["telemetry"]
    else:
        assert service.prometheus_text().startswith(b"# HELP")
        breaker_snapshot = breaker.snapshot()
        telemetry_snapshot = telemetry.snapshot()

    assert breaker_snapshot["state"] == "half_open"
    assert telemetry_snapshot["runtime"]["breaker_state"] == "half_open"


def test_diagnostic_telemetry_errors_return_safe_fallback_without_job_mutation(
    tmp_path: Path,
) -> None:
    class DiagnosticFailureTelemetry(BaseExceptionTelemetry):
        def snapshot(self) -> dict[str, object]:
            self._fail()

        def prometheus_text(self) -> bytes:
            self._fail()

    telemetry = DiagnosticFailureTelemetry()
    sdk = FakeSandboxClient()

    async def scenario() -> tuple[object, dict[str, object], bytes]:
        service, _, config = _service(tmp_path, sdk, telemetry=telemetry)
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-diagnostic-errors")
        terminal = await service.wait_terminal(submitted.job_id)
        diagnostics = service.diagnostics()
        prometheus = service.prometheus_text()
        after = service.get_job(submitted.job_id)
        await service.stop()
        assert after == terminal
        return terminal, diagnostics, prometheus

    terminal, diagnostics, prometheus = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert diagnostics["schema_version"] == 1
    assert diagnostics["telemetry"] == {"schema_version": 1, "unavailable": True}
    assert prometheus == b""


def test_partial_telemetry_sink_failures_preserve_lifecycle_and_job_result(
    tmp_path: Path,
) -> None:
    class FailingLogger:
        def info(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise KeyboardInterrupt

    class FailingMetric:
        def labels(self, **values: object) -> "FailingMetric":
            del values
            return self

        def observe(self, value: object) -> None:
            del value
            raise SystemExit

        def inc(self) -> None:
            raise SystemExit

        def set(self, value: object) -> None:
            del value
            raise SystemExit

    telemetry = BrokerTelemetry(logger=FailingLogger())  # type: ignore[arg-type]
    sink = FailingMetric()
    telemetry._phase_duration = sink  # type: ignore[assignment]
    telemetry._control_failures = sink  # type: ignore[assignment]
    telemetry._retries = sink  # type: ignore[assignment]
    telemetry._jobs = sink  # type: ignore[assignment]
    telemetry._queue_depth = sink  # type: ignore[assignment]
    telemetry._active_jobs = sink  # type: ignore[assignment]
    telemetry._cleanup_tasks = sink  # type: ignore[assignment]
    telemetry._isolated_tasks = sink  # type: ignore[assignment]
    telemetry._breaker_state = sink  # type: ignore[assignment]
    sdk = FakeSandboxClient()

    async def scenario() -> object:
        service, _, config = _service(tmp_path, sdk, telemetry=telemetry)
        await service.start()
        submitted = await service.submit(
            _prepared(config),
            "idem-partial-sink-failures",
        )
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal

    terminal = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert terminal.cleanup_status == "succeeded"
    assert sdk.create_count == sdk.run_count == sdk.destroy_count == 1
    lifecycle = [
        event
        for event in telemetry.snapshot()["recent_events"]
        if event["job_id"] == terminal.job_id
    ]
    assert [event["phase"] for event in lifecycle] == [
        "job_received",
        "queue_entered",
        "provisioning_started",
        "provisioning_completed",
        "upload_started",
        "upload_completed",
        "command_started",
        "command_completed",
        "validation_started",
        "validation_completed",
        "cleanup_started",
        "cleanup_completed",
        "job_terminal",
    ]


def test_plain_unicode_warning_persists_in_success_manifest(tmp_path: Path) -> None:
    async def scenario() -> tuple[object, DockingManifest]:
        service, _, config = _service(
            tmp_path,
            FakeSandboxClient(result_extras={"warnings": ["科学计算结果正常"]}),
        )
        await service.start()
        job = await service.submit(_prepared(config), "idem-plain-unicode-warning")
        terminal = await service.wait_terminal(job.job_id)
        manifest = service.get_manifest(job.job_id)
        await service.stop()
        return terminal, manifest

    terminal, manifest = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert terminal.warnings == ("科学计算结果正常",)
    assert manifest.warnings == ["科学计算结果正常"]
