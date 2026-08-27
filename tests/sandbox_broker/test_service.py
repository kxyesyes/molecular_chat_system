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
    BrokerErrorCode,
    BrokerJobStatus,
    DockingManifest,
    DockingParameters,
)
from src.sandbox_broker.opensandbox_client import (
    OpenSandboxClient,
    SandboxCommandResult,
    SandboxDestroyError,
    SandboxHandle,
)
from src.sandbox_broker.service import (
    BrokerFailure,
    PreparedDockingSubmission,
    SandboxBrokerService,
    StopIncomplete,
    canonical_submission_sha256,
)
from src.sandbox_broker.store import BrokerStore
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
            await service.start()
            first = await service.submit(_prepared(config), "idem-prestart-cancel")
            await first_job_wrapped.wait()
            await service.cancel(first.job_id)
            second = await service.submit(_prepared(config), "idem-after-cancel")
            release_first_job.set()
            first_terminal = await service.wait_terminal(first.job_id, timeout=1.0)
            second_terminal = await service.wait_terminal(second.job_id, timeout=1.0)
            await service.stop()
            return first_terminal, second_terminal, sdk
        finally:
            release_first_job.set()
            loop.set_task_factory(None)

    first, second, sdk = asyncio.run(scenario())
    assert first.status is BrokerJobStatus.CANCELLED
    assert second.status is BrokerJobStatus.SUCCEEDED
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
        second_terminal = await service.wait_terminal(second.job_id, timeout=1.0)
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
    async def scenario() -> tuple[object, object, RecoverySandboxClient, list[object]]:
        failing = FaultSandboxClient("destroy")
        service, store, config = _service(tmp_path, failing)
        await service.start()
        job = await service.submit(_prepared(config), "idem-terminal-cleanup-retry")
        before = await service.wait_terminal(job.job_id)
        await service.stop()
        assert store.cleanup_pending_jobs()

        recovery = RecoverySandboxClient()
        reopened = SandboxBrokerService(
            config,
            store,
            recovery,
            ArtifactRegistry(config.state_root, store),
        )
        await reopened.recover()
        after = reopened.get_job(job.job_id)
        return before, after, recovery, store.cleanup_pending_jobs()

    before, after, recovery, pending = asyncio.run(scenario())
    assert before.status is BrokerJobStatus.FAILED
    assert before.error_code == BrokerErrorCode.CLEANUP_FAILED.value
    assert before.cleanup_status == "failed"
    assert after.status is before.status
    assert after.error_code == before.error_code
    assert after.warnings == before.warnings
    assert after.cleanup_status == "succeeded"
    assert recovery.recovery_calls == 1
    assert pending == []


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

    async def scenario() -> tuple[object, float, list[str]]:
        sdk = CancellationResistantCreate()
        service, _, config = _service(tmp_path, sdk)
        service._create_hard_timeout_seconds = 0.03
        service._destroy_hard_timeout_seconds = 0.03
        await service.start()
        job = await service.submit(_prepared(config), "idem-hung-create")
        await sdk.started.wait()
        started = asyncio.get_running_loop().time()
        await service.stop(timeout=0.5)
        elapsed = asyncio.get_running_loop().time() - started
        terminal = service.get_job(job.job_id)
        sdk.release_create.set()
        await asyncio.sleep(0.02)
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
    assert terminal.cleanup_status == "failed"
    assert "sandbox_auto_expires_within_configured_remote_lifetime" in terminal.warnings
    assert leaked == []


def test_hung_run_and_destroy_are_detached_after_hard_deadlines(
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
        await service.stop(timeout=0.5)
        elapsed = asyncio.get_running_loop().time() - started
        terminal = service.get_job(job.job_id)
        sdk.release_run.set()
        sdk.release_destroy.set()
        await asyncio.sleep(0.03)
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
        second_terminal = await service.wait_terminal(second.job_id, timeout=1)
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
        assert client._cleanup_in_flight == {}
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
