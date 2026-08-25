# OpenSandbox Docking Broker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route MedChat's verified Temporal docking execution through a dedicated, fail-closed Sandbox Broker that runs a pinned Vina/Meeko image in OpenSandbox with gVisor and returns only validated, traceable artifacts.

**Architecture:** Keep the existing Supervisor, Temporal workflow, staging lease, `DockingExecution`, `ToolResult`, validator, and completion store. Replace only the production raw docking executor when `MEDCHAT_DOCKING_EXECUTION_BACKEND=opensandbox`: a synchronous worker-side client submits bounded multipart inputs over an access-controlled Unix socket, while a separate async Broker owns OpenSandbox lifecycle, SQLite idempotency, scientific validation, artifact publication, cancellation, and cleanup. OpenSandbox Server remains loopback-only, authenticated, and is the only process with Docker socket access.

**Tech Stack:** Python 3.10+, FastAPI 0.104.1, Uvicorn 0.24.0, Pydantic 2.5.0, HTTPX 0.25.2, SQLite, Temporal SDK 1.30.0, OpenSandbox SDK 0.1.15, OpenSandbox Server 0.2.2, Docker, gVisor `runsc`, AutoDock Vina, Meeko, RDKit, pytest.

---

## File map

The implementation is intentionally split into focused units:

- `src/sandbox_broker/models.py`: public schemas, stable states, error codes, transitions, manifests, and provenance.
- `src/sandbox_broker/config.py`: bounded environment configuration with secret-safe representations.
- `src/sandbox_broker/store.py`: dedicated SQLite jobs, transitions, idempotency, artifacts, and recovery queries.
- `src/sandbox_broker/validation.py`: multipart staging, file limits, hashes, result JSON, pose, and provenance gates.
- `src/sandbox_broker/artifacts.py`: registered artifact publication, lookup, bounded reads, and retention.
- `src/sandbox_broker/opensandbox_client.py`: narrow protocol plus the only official SDK imports.
- `src/sandbox_broker/service.py`: queue, single-concurrency lifecycle, cancellation, timeout, recovery, and cleanup.
- `src/sandbox_broker/app.py`: Broker-only FastAPI routes and lifespan; no Web/Agent routes.
- `scripts/run_sandbox_broker.py`: Uvicorn Unix-socket entrypoint.
- `src/docking/sandbox_runner.py`: synchronous Unix-socket Broker client that maps Broker results to `ToolResult`.
- `src/task_runtime/docking_execution.py`: explicit `local|opensandbox` raw-executor selection while preserving existing verification and completion logic.
- `deployment/opensandbox/`: pinned image, fixed docking command, OpenSandbox/gVisor config, services, installer, and operator guide.
- `scripts/validate_opensandbox_deployment.py`: static/runtime production validation.
- `scripts/run_opensandbox_docking_acceptance.py`: opt-in QEMU real acceptance and structured report.
- `tests/sandbox_broker/`: unit, API, fake-SDK, worker-adapter, deployment, and real opt-in tests.

## Invariants used by every task

- The Agent, Web process, and Temporal worker cannot provide image, command, entrypoint, environment, mount, network, secret, or host output path.
- The Broker Unix socket is the only worker-to-Broker endpoint. OpenSandbox Server listens on `127.0.0.1:8080` with an API key read from its service environment.
- `MEDCHAT_DOCKING_EXECUTION_BACKEND=opensandbox` never falls back to `MolecularDocking` on any Broker/OpenSandbox failure.
- Success requires exit code zero, echoed input hashes, `pose_count > 0`, finite best energy, bounded parseable PDBQT, registered artifacts, `demo_mode=false`, and `fallback_used=false`.
- Logs, responses, reports, exceptions, and `repr()` values contain no API keys or host input paths.
- Real acceptance is opt-in and fails or skips honestly when OpenSandbox, gVisor, Vina, Meeko, RDKit, sample inputs, or required privileges are unavailable.

### Task 1: Broker contracts and bounded configuration

**Files:**
- Create: `src/sandbox_broker/__init__.py`
- Create: `src/sandbox_broker/models.py`
- Create: `src/sandbox_broker/config.py`
- Create: `tests/sandbox_broker/__init__.py`
- Create: `tests/sandbox_broker/test_models_config.py`

- [ ] **Step 1: Write failing contract tests**

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.sandbox_broker.config import BrokerConfig
from src.sandbox_broker.models import (
    BrokerJobStatus,
    DockingParameters,
    transition_allowed,
)


def test_docking_parameters_require_finite_positive_box() -> None:
    valid = DockingParameters(center=[5.99, 3.01, 17.345], size=[20, 20, 20])
    assert valid.center == (5.99, 3.01, 17.345)
    with pytest.raises(ValidationError):
        DockingParameters(center=[float("nan"), 0, 0], size=[20, 20, 20])
    with pytest.raises(ValidationError):
        DockingParameters(center=[0, 0, 0], size=[20, 0, 20])


def test_terminal_states_cannot_transition() -> None:
    assert transition_allowed(BrokerJobStatus.QUEUED, BrokerJobStatus.PROVISIONING)
    assert not transition_allowed(BrokerJobStatus.SUCCEEDED, BrokerJobStatus.RUNNING)
    assert not transition_allowed(BrokerJobStatus.FAILED, BrokerJobStatus.QUEUED)


def test_config_requires_digest_loopback_and_secret_without_repr_leak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_STATE", str(tmp_path.resolve()))
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_SOCKET", str((tmp_path / "broker.sock").resolve()))
    monkeypatch.setenv("MEDCHAT_SANDBOX_IMAGE", "medchat-docking@sha256:" + "a" * 64)
    monkeypatch.setenv("OPEN_SANDBOX_DOMAIN", "127.0.0.1:8080")
    monkeypatch.setenv("OPEN_SANDBOX_API_KEY", "runtime-secret-value")
    config = BrokerConfig.from_env()
    assert config.image_digest == "a" * 64
    assert "runtime-secret-value" not in repr(config)
    monkeypatch.setenv("OPEN_SANDBOX_DOMAIN", "sandbox.example.test:8080")
    with pytest.raises(ValueError, match="loopback"):
        BrokerConfig.from_env()
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_models_config.py -q -p no:cacheprovider`

Expected: FAIL during collection because `src.sandbox_broker` does not exist.

- [ ] **Step 3: Implement schemas and transition rules**

Use these exact public names and state rules in `models.py`:

```python
from __future__ import annotations

import math
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrokerJobStatus(str, Enum):
    QUEUED = "queued"
    PROVISIONING = "provisioning"
    UPLOADING = "uploading"
    RUNNING = "running"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATUSES = frozenset({
    BrokerJobStatus.SUCCEEDED,
    BrokerJobStatus.FAILED,
    BrokerJobStatus.CANCELLED,
    BrokerJobStatus.EXPIRED,
})
_TRANSITIONS = {
    BrokerJobStatus.QUEUED: {BrokerJobStatus.PROVISIONING, BrokerJobStatus.FAILED, BrokerJobStatus.CANCELLED, BrokerJobStatus.EXPIRED},
    BrokerJobStatus.PROVISIONING: {BrokerJobStatus.UPLOADING, BrokerJobStatus.FAILED, BrokerJobStatus.CANCELLED, BrokerJobStatus.EXPIRED},
    BrokerJobStatus.UPLOADING: {BrokerJobStatus.RUNNING, BrokerJobStatus.FAILED, BrokerJobStatus.CANCELLED, BrokerJobStatus.EXPIRED},
    BrokerJobStatus.RUNNING: {BrokerJobStatus.VALIDATING, BrokerJobStatus.FAILED, BrokerJobStatus.CANCELLED, BrokerJobStatus.EXPIRED},
    BrokerJobStatus.VALIDATING: {BrokerJobStatus.SUCCEEDED, BrokerJobStatus.FAILED, BrokerJobStatus.CANCELLED, BrokerJobStatus.EXPIRED},
}


def transition_allowed(current: BrokerJobStatus, target: BrokerJobStatus) -> bool:
    return target in _TRANSITIONS.get(current, set())


class DockingParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    exhaustiveness: int = Field(default=8, ge=1, le=64)
    num_modes: int = Field(default=10, ge=1, le=20)
    energy_range: float = Field(default=3.0, ge=0, le=20)

    @field_validator("center", "size")
    @classmethod
    def finite_vector(cls, value: tuple[float, float, float], info):
        normalized = tuple(float(item) for item in value)
        if len(normalized) != 3 or not all(math.isfinite(item) for item in normalized):
            raise ValueError(f"{info.field_name} must contain three finite values")
        if info.field_name == "size" and not all(0 < item <= 80 for item in normalized):
            raise ValueError("size values must be in (0, 80]")
        return normalized


class BrokerErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    UNAUTHORIZED = "unauthorized"
    QUEUE_SATURATED = "queue_saturated"
    OPENSANDBOX_UNAVAILABLE = "opensandbox_unavailable"
    PROVISIONING_FAILED = "provisioning_failed"
    UPLOAD_FAILED = "upload_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    COMMAND_FAILED = "command_failed"
    SCIENTIFIC_OUTPUT_INVALID = "scientific_output_invalid"
    ARTIFACT_FAILED = "artifact_failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    CLEANUP_FAILED = "cleanup_failed"


class BrokerProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sandbox_id: str
    image_uri: str
    image_digest: str
    secure_runtime: str
    vina_version: str
    meeko_version: str
    receptor_sha256: str
    ligand_sha256: str
    cleanup_status: str
    demo_mode: bool = False
    fallback_used: bool = False


class DockingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1
    job_id: str
    trace_id: str
    pose_count: int = Field(gt=0)
    best_energy: float
    artifacts: list[dict[str, Any]]
    warnings: list[str] = []
    provenance: BrokerProvenance
```

- [ ] **Step 4: Implement bounded secret-safe configuration**

`BrokerConfig.from_env()` must read only operator environment variables and validate the approved limits:

```python
@dataclass(frozen=True)
class BrokerConfig:
    state_root: Path = field(repr=False)
    socket_path: Path = field(repr=False)
    image_uri: str
    image_digest: str
    opensandbox_domain: str
    opensandbox_api_key: str = field(repr=False)
    cpu: str = "2"
    memory: str = "4Gi"
    pids_limit: int = 128
    receptor_max_bytes: int = 50 * 1024 * 1024
    ligand_max_bytes: int = 10 * 1024 * 1024
    output_max_bytes: int = 100 * 1024 * 1024
    execution_timeout_seconds: int = 180
    sandbox_timeout_seconds: int = 300
    concurrency: int = 1
    queue_capacity: int = 8
    artifact_retention_seconds: int = 86400
    audit_retention_seconds: int = 30 * 86400

    @classmethod
    def from_env(cls) -> "BrokerConfig":
        state_root = Path(os.environ["MEDCHAT_SANDBOX_BROKER_STATE"]).resolve()
        socket_path = Path(os.environ["MEDCHAT_SANDBOX_BROKER_SOCKET"]).resolve()
        image = os.environ["MEDCHAT_SANDBOX_IMAGE"]
        match = re.fullmatch(r"([^\s@]+)@sha256:([0-9a-f]{64})", image)
        if match is None:
            raise ValueError("sandbox image must be pinned by sha256 digest")
        domain = os.environ["OPEN_SANDBOX_DOMAIN"]
        if domain not in {"127.0.0.1:8080", "localhost:8080"}:
            raise ValueError("OpenSandbox domain must be loopback")
        key = os.environ.get("OPEN_SANDBOX_API_KEY", "")
        if not key:
            raise ValueError("OpenSandbox API key is required")
        return cls(
            state_root=state_root,
            socket_path=socket_path,
            image_uri=match.group(1),
            image_digest=match.group(2),
            opensandbox_domain=domain,
            opensandbox_api_key=key,
        )
```

- [ ] **Step 5: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_models_config.py -q -p no:cacheprovider`

Expected: PASS.

```powershell
git add src/sandbox_broker/__init__.py src/sandbox_broker/models.py src/sandbox_broker/config.py tests/sandbox_broker/__init__.py tests/sandbox_broker/test_models_config.py
git commit -m "feat: add sandbox broker contracts"
```

### Task 2: SQLite state, idempotency, and recovery

**Files:**
- Create: `src/sandbox_broker/store.py`
- Create: `tests/sandbox_broker/test_store.py`

- [ ] **Step 1: Write failing store tests**

```python
from src.sandbox_broker.models import BrokerJobStatus
from src.sandbox_broker.store import BrokerStore, IdempotencyConflict


def test_same_idempotency_key_and_hash_reuses_job(tmp_path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    first, reused = store.create_or_get("idem-1", "a" * 64, "trace-1")
    second, reused_again = store.create_or_get("idem-1", "a" * 64, "trace-2")
    assert reused is False
    assert reused_again is True
    assert second.job_id == first.job_id
    assert second.trace_id == first.trace_id


def test_reused_key_with_different_hash_conflicts(tmp_path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    store.create_or_get("idem-1", "a" * 64, "trace-1")
    with pytest.raises(IdempotencyConflict):
        store.create_or_get("idem-1", "b" * 64, "trace-2")


def test_transition_is_atomic_and_terminal_is_immutable(tmp_path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    job, _ = store.create_or_get("idem-1", "a" * 64, "trace-1")
    store.transition(job.job_id, BrokerJobStatus.PROVISIONING)
    store.transition(job.job_id, BrokerJobStatus.FAILED, error_code="provisioning_failed")
    with pytest.raises(ValueError, match="illegal job transition"):
        store.transition(job.job_id, BrokerJobStatus.RUNNING)


def test_recovery_returns_active_jobs_with_sandbox_ids(tmp_path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    job, _ = store.create_or_get("idem-1", "a" * 64, "trace-1")
    store.transition(job.job_id, BrokerJobStatus.PROVISIONING, sandbox_id="sandbox-1")
    assert [(item.job_id, item.sandbox_id) for item in store.active_jobs()] == [
        (job.job_id, "sandbox-1")
    ]
```

- [ ] **Step 2: Verify the tests fail**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_store.py -q -p no:cacheprovider`

Expected: FAIL because `BrokerStore` is missing.

- [ ] **Step 3: Implement the dedicated store**

Use SQLite `BEGIN IMMEDIATE`, WAL, foreign keys, busy timeout, JSON only for sanitized warnings/provenance, and these tables:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    canonical_input_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    sandbox_id TEXT,
    error_code TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    cleanup_status TEXT NOT NULL DEFAULT 'not_started',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS transitions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    relative_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    UNIQUE(job_id, relative_path)
);
```

The transaction boundary must perform this check before every update:

```python
current = BrokerJobStatus(row["status"])
if not transition_allowed(current, target):
    raise ValueError("illegal job transition")
cursor = connection.execute(
    "UPDATE jobs SET status=?, phase=?, sandbox_id=COALESCE(?, sandbox_id), "
    "error_code=?, updated_at=? WHERE job_id=? AND status=?",
    (target.value, phase or target.value, sandbox_id, error_code, now, job_id, current.value),
)
if cursor.rowcount != 1:
    raise RuntimeError("concurrent job transition")
```

- [ ] **Step 4: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_store.py -q -p no:cacheprovider`

Expected: PASS.

```powershell
git add src/sandbox_broker/store.py tests/sandbox_broker/test_store.py
git commit -m "feat: persist sandbox broker jobs"
```

### Task 3: Input staging, scientific validation, and registered artifacts

**Files:**
- Create: `src/sandbox_broker/validation.py`
- Create: `src/sandbox_broker/artifacts.py`
- Create: `tests/sandbox_broker/test_validation_artifacts.py`

- [ ] **Step 1: Write failing input and result-gate tests**

```python
import json
from pathlib import Path

import pytest

from src.sandbox_broker.artifacts import ArtifactRegistry
from src.sandbox_broker.validation import ScientificOutputError, validate_scientific_output


POSE = b"REMARK VINA RESULT: -7.4 0.0 0.0\nMODEL 1\nENDMDL\n"


def build_valid_output(tmp_path: Path) -> Path:
    output = tmp_path / "output"
    poses = output / "poses"
    poses.mkdir(parents=True)
    (poses / "result.pdbqt").write_bytes(POSE)
    payload = {
        "schema_version": 1,
        "status": "succeeded",
        "receptor_sha256": "a" * 64,
        "ligand_sha256": "b" * 64,
        "pose_count": 1,
        "best_energy": -7.4,
        "pose_files": ["poses/result.pdbqt"],
        "vina_version": "1.2.5",
        "meeko_version": "0.6.1",
        "warnings": [],
    }
    (output / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    return output


def test_scientific_output_requires_echoed_hash_energy_and_pose(tmp_path: Path) -> None:
    output = build_valid_output(tmp_path)
    validated = validate_scientific_output(
        output,
        receptor_sha256="a" * 64,
        ligand_sha256="b" * 64,
        max_output_bytes=1024,
    )
    assert validated.pose_count == 1
    assert validated.best_energy == -7.4


@pytest.mark.parametrize("mutation", ["hash", "energy", "pose_count", "missing_pose", "traversal"])
def test_scientific_output_fails_closed(tmp_path: Path, mutation: str) -> None:
    # Build the valid fixture above, then mutate exactly one field/file.
    output = build_valid_output(tmp_path)
    data = json.loads((output / "result.json").read_text(encoding="utf-8"))
    if mutation == "hash": data["ligand_sha256"] = "c" * 64
    if mutation == "energy": data["best_energy"] = "NaN"
    if mutation == "pose_count": data["pose_count"] = 0
    if mutation == "missing_pose": (output / "poses" / "result.pdbqt").unlink()
    if mutation == "traversal": data["pose_files"] = ["../result.pdbqt"]
    (output / "result.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, receptor_sha256="a" * 64, ligand_sha256="b" * 64, max_output_bytes=1024)


def test_artifact_lookup_uses_identifier_not_caller_path(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.sqlite")
    job, _ = store.create_or_get("idem-1", "a" * 64, "trace-1")
    registry = ArtifactRegistry(tmp_path, store)
    record = registry.publish(job.job_id, "pose", POSE, "chemical/x-pdbqt")
    assert registry.read_registered(job.job_id, record.artifact_id, 1024) == POSE
    with pytest.raises(KeyError):
        registry.read_registered(job.job_id, "../pose", 1024)
```

Each parametrized case receives its own `tmp_path`, so the helper does not share mutable state.

- [ ] **Step 2: Verify failures**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_validation_artifacts.py -q -p no:cacheprovider`

Expected: FAIL because validation and artifact modules are missing.

- [ ] **Step 3: Implement bounded staging and output validation**

`validation.py` must:

1. Stream each upload to `state_root/jobs/<job_id>/input/.<name>.part` in 1 MiB chunks.
2. Reject size overflow before publication.
3. `fsync()` the file, atomically replace the final server-generated name, and hash during streaming.
4. Accept receptor suffixes `.pdb`/`.pdbqt` and ligand suffixes `.sdf`/`.mol`/`.pdb`/`.pdbqt`; never use the client filename as a path.
5. Validate PDBQT using `REMARK VINA RESULT` lines and require the first parsed energy to equal `best_energy` within `1e-6`.

Core path projection and finite gate:

```python
relative = PurePosixPath(item)
if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
    raise ScientificOutputError("pose path is invalid")
pose = (output_root / Path(*relative.parts)).resolve(strict=True)
if output_root.resolve(strict=True) not in pose.parents:
    raise ScientificOutputError("pose path escapes output root")
energy = float(payload["best_energy"])
if not math.isfinite(energy):
    raise ScientificOutputError("best energy is not finite")
```

- [ ] **Step 4: Implement identifier-only artifact publication**

`ArtifactRegistry.publish()` must generate `artifact_id = uuid.uuid4().hex`, write under `jobs/<job_id>/published/<artifact_id>`, preserve only an allowlisted media type, and return size/hash metadata. `read_registered()` must query the store record, open only that recorded relative path under the state root, call `read_file_snapshot(recorded_path, max_bytes=limit)`, and reject symlinks, non-regular files, hash drift, or size drift.

- [ ] **Step 5: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_validation_artifacts.py -q -p no:cacheprovider`

Expected: PASS.

```powershell
git add src/sandbox_broker/validation.py src/sandbox_broker/artifacts.py tests/sandbox_broker/test_validation_artifacts.py
git commit -m "feat: validate sandbox docking artifacts"
```

### Task 4: Narrow OpenSandbox SDK adapter

**Files:**
- Create: `requirements-opensandbox-broker.txt`
- Create: `requirements-opensandbox-server.txt`
- Create: `src/sandbox_broker/opensandbox_client.py`
- Create: `tests/sandbox_broker/test_opensandbox_client.py`

- [ ] **Step 1: Add pinned dependency profiles**

`requirements-opensandbox-broker.txt`:

```text
fastapi==0.104.1
httpx==0.25.2
opensandbox==0.1.15
pydantic==2.5.0
python-multipart==0.0.6
uvicorn[standard]==0.24.0
```

`requirements-opensandbox-server.txt`:

```text
opensandbox-server==0.2.2
```

- [ ] **Step 2: Write a failing fake-SDK contract test**

```python
@pytest.mark.asyncio
async def test_adapter_uses_only_pinned_policy_and_fixed_command() -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(), sandbox_factory=fake)
    handle = await client.create("job-1")
    await client.upload_text(handle, "/workspace/input/receptor.pdb", "ATOM\n")
    result = await client.run(handle)
    await client.destroy(handle)
    assert fake.create_calls == [{
        "image": "medchat-docking@sha256:" + "a" * 64,
        "timeout_seconds": 300,
        "resource": {"cpu": "2", "memory": "4Gi"},
        "network_default": "deny",
        "entrypoint": ["sleep", "infinity"],
        "metadata": {"medchat.operation": "molecular_docking", "medchat.job_id": "job-1"},
    }]
    assert fake.commands == ["python /opt/medchat/run_docking.py --request /workspace/input/request.json --output /workspace/output"]
    assert result.exit_code == 0
    assert fake.destroyed == [handle.sandbox_id]
```

Also assert the adapter has no method accepting mount, image, command, environment, or network arguments.

- [ ] **Step 3: Verify failure**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_opensandbox_client.py -q -p no:cacheprovider`

Expected: FAIL because `OpenSandboxClient` is missing.

- [ ] **Step 4: Implement a protocol and delayed official imports**

Keep the Broker service testable without importing the SDK on ordinary Web startup:

```python
@dataclass(frozen=True)
class SandboxHandle:
    sandbox_id: str
    raw: Any = field(repr=False)


@dataclass(frozen=True)
class SandboxCommandResult:
    exit_code: int
    stdout: str
    stderr: str


class SandboxClient(Protocol):
    async def create(self, job_id: str) -> SandboxHandle:
        raise NotImplementedError

    async def upload_text(self, handle: SandboxHandle, path: str, data: str) -> None:
        raise NotImplementedError

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        raise NotImplementedError

    async def list_files(self, handle: SandboxHandle, path: str, pattern: str) -> list[str]:
        raise NotImplementedError

    async def run(self, handle: SandboxHandle) -> SandboxCommandResult:
        raise NotImplementedError

    async def destroy(self, handle: SandboxHandle) -> None:
        raise NotImplementedError

    async def destroy_by_id(self, sandbox_id: str) -> None:
        raise NotImplementedError
```

The production constructor imports `ConnectionConfig`, `Sandbox`, `SandboxManager`, `WriteEntry`, `SearchEntry`, and `NetworkPolicy` inside methods. `Sandbox.create()` must receive the digest-pinned image, `timeout=timedelta(seconds=300)`, fixed resource limits, fixed entrypoint, fixed metadata, and `NetworkPolicy(defaultAction="deny", egress=[])`. `run()` must execute exactly:

```text
python /opt/medchat/run_docking.py --request /workspace/input/request.json --output /workspace/output
```

Capture at most 16 KiB each of stdout/stderr and sanitize before returning. `destroy()` must call the SDK's `destroy()` in `finally`; `destroy_by_id()` must use `SandboxManager.kill_sandbox()`.

- [ ] **Step 5: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_opensandbox_client.py -q -p no:cacheprovider`

Expected: PASS without requiring a running OpenSandbox Server.

```powershell
git add requirements-opensandbox-broker.txt requirements-opensandbox-server.txt src/sandbox_broker/opensandbox_client.py tests/sandbox_broker/test_opensandbox_client.py
git commit -m "feat: add OpenSandbox SDK boundary"
```

### Task 5: Async Broker lifecycle and fail-closed cleanup

**Files:**
- Create: `src/sandbox_broker/service.py`
- Create: `tests/sandbox_broker/test_service.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
@pytest.mark.asyncio
async def test_job_runs_once_and_publishes_only_after_validation(tmp_path) -> None:
    sdk = FakeSandboxClient(valid_result=True)
    service = build_service(tmp_path, sdk)
    job = await service.submit(valid_submission(), "idem-1")
    await service.wait_terminal(job.job_id)
    terminal = service.get_job(job.job_id)
    assert terminal.status is BrokerJobStatus.SUCCEEDED
    assert sdk.create_count == 1
    assert terminal.provenance.secure_runtime == "gvisor"
    assert terminal.provenance.cleanup_status == "succeeded"


@pytest.mark.asyncio
async def test_invalid_output_fails_without_manifest(tmp_path) -> None:
    sdk = FakeSandboxClient(valid_result=False)
    service = build_service(tmp_path, sdk)
    job = await service.submit(valid_submission(), "idem-1")
    await service.wait_terminal(job.job_id)
    assert service.get_job(job.job_id).error_code == "scientific_output_invalid"
    with pytest.raises(KeyError):
        service.get_manifest(job.job_id)


@pytest.mark.asyncio
async def test_cancel_and_timeout_destroy_sandbox(tmp_path) -> None:
    sdk = BlockingSandboxClient()
    service = build_service(tmp_path, sdk, execution_timeout_seconds=1)
    job = await service.submit(valid_submission(), "idem-1")
    await sdk.running.wait()
    await service.cancel(job.job_id)
    await service.wait_terminal(job.job_id)
    assert service.get_job(job.job_id).status is BrokerJobStatus.CANCELLED
    assert sdk.destroy_count == 1


@pytest.mark.asyncio
async def test_restart_recovery_kills_orphan_and_fails_job(tmp_path) -> None:
    store = seeded_running_store(tmp_path, sandbox_id="sandbox-orphan")
    sdk = FakeSandboxClient()
    service = build_service(tmp_path, sdk, store=store)
    await service.recover()
    assert sdk.destroyed_ids == ["sandbox-orphan"]
    assert store.get_by_idempotency("idem-1").status is BrokerJobStatus.FAILED
```

- [ ] **Step 2: Verify failure**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_service.py -q -p no:cacheprovider`

Expected: FAIL because `SandboxBrokerService` is missing.

- [ ] **Step 3: Implement one-consumer lifecycle**

The service owns `asyncio.Queue(maxsize=config.queue_capacity)`, one consumer task, a per-job cancellation event, and these phases:

```python
async def _run_job(self, job_id: str) -> None:
    handle = None
    prepared = None
    failure = None
    try:
        self.store.transition(job_id, BrokerJobStatus.PROVISIONING)
        handle = await self.client.create(job_id)
        self.store.attach_sandbox(job_id, handle.sandbox_id)
        self.store.transition(job_id, BrokerJobStatus.UPLOADING)
        await self._upload_fixed_inputs(job_id, handle)
        self.store.transition(job_id, BrokerJobStatus.RUNNING)
        command = await asyncio.wait_for(
            self.client.run(handle),
            timeout=self.config.execution_timeout_seconds,
        )
        if command.exit_code != 0:
            raise BrokerFailure(BrokerErrorCode.COMMAND_FAILED)
        self.store.transition(job_id, BrokerJobStatus.VALIDATING)
        prepared = await self._download_validate_publish(job_id, handle)
    except asyncio.TimeoutError:
        failure = BrokerErrorCode.EXECUTION_TIMEOUT
    except asyncio.CancelledError:
        failure = BrokerErrorCode.CANCELLED
    except BrokerFailure as exc:
        failure = exc.code
    except Exception:
        failure = BrokerErrorCode.OPENSANDBOX_UNAVAILABLE
    finally:
        cleanup = await self._destroy_best_effort(handle)
        self.store.record_cleanup(job_id, cleanup)
    if failure is BrokerErrorCode.CANCELLED:
        self.store.cancel(job_id)
    elif failure is not None:
        self.store.fail(job_id, failure)
    elif cleanup != "succeeded":
        self.store.fail(job_id, BrokerErrorCode.CLEANUP_FAILED)
    else:
        provenance = prepared.provenance.model_copy(update={"cleanup_status": cleanup})
        self.store.complete(
            job_id,
            prepared.model_copy(update={"provenance": provenance}),
        )
```

Before `complete()`, build provenance from configured digest, SDK sandbox ID, fixed `secure_runtime="gvisor"`, result tool versions, input hashes, `demo_mode=False`, and `fallback_used=False`. If cleanup fails after otherwise valid science, retain the scientific result but add `cleanup_failed`, set cleanup status `failed`, and make the overall Broker job `failed`; do not publish the manifest route.

- [ ] **Step 4: Implement recovery and retention**

`recover()` must iterate active rows, call `destroy_by_id()` for every persisted sandbox ID, then mark the job failed with `opensandbox_unavailable` or `cleanup_failed`. `expire(now)` removes job files older than 24 hours only after resolving them beneath `state_root/jobs`, marks retained metadata `expired`, and deletes audit rows older than 30 days.

- [ ] **Step 5: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_service.py -q -p no:cacheprovider`

Expected: PASS.

```powershell
git add src/sandbox_broker/service.py tests/sandbox_broker/test_service.py
git commit -m "feat: run sandbox docking jobs"
```

### Task 6: Broker FastAPI over a Unix socket

**Files:**
- Create: `src/sandbox_broker/app.py`
- Create: `scripts/run_sandbox_broker.py`
- Create: `tests/sandbox_broker/test_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_submit_observe_manifest_artifact_and_idempotency(client, valid_files) -> None:
    response = client.post(
        "/v1/docking/jobs",
        headers={"Idempotency-Key": "task-0001"},
        data={"request_json": json.dumps(valid_parameters())},
        files=valid_files,
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert client.post(
        "/v1/docking/jobs",
        headers={"Idempotency-Key": "task-0001"},
        data={"request_json": json.dumps(valid_parameters())},
        files=valid_files,
    ).json()["job_id"] == job_id
    wait_for_success(client, job_id)
    manifest = client.get(f"/v1/docking/jobs/{job_id}/manifest")
    assert manifest.status_code == 200
    artifact_id = manifest.json()["artifacts"][0]["artifact_id"]
    assert client.get(f"/v1/docking/jobs/{job_id}/artifacts/{artifact_id}").status_code == 200


def test_api_rejects_missing_key_extra_policy_and_path_artifact(client, valid_files) -> None:
    assert client.post("/v1/docking/jobs", files=valid_files).status_code == 422
    request = valid_parameters() | {"image": "ubuntu", "command": "id", "mounts": ["/"]}
    assert submit(client, request, valid_files).status_code == 422
    assert client.get("/v1/docking/jobs/job-1/artifacts/../secret").status_code in {404, 422}


def test_idempotency_conflict_and_cancel_are_stable(client, valid_files) -> None:
    first = submit(client, valid_parameters(), valid_files, key="same")
    changed = valid_parameters() | {"center": [0, 0, 0]}
    conflict = submit(client, changed, valid_files, key="same")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    cancelled = client.post(f"/v1/docking/jobs/{first.json()['job_id']}/cancel")
    assert cancelled.status_code == 202
```

- [ ] **Step 2: Verify failure**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_api.py -q -p no:cacheprovider`

Expected: FAIL because the Broker app is missing.

- [ ] **Step 3: Implement the narrow routes and lifespan**

Use exactly these routes:

```python
@router.post("/v1/docking/jobs", status_code=202)
async def submit_job(
    request_json: Annotated[str, Form()],
    receptor: Annotated[UploadFile, File()],
    ligand: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    service: Annotated[SandboxBrokerService, Depends(get_service)],
):
    parameters = DockingParameters.model_validate_json(request_json)
    return await service.submit_uploads(parameters, receptor, ligand, idempotency_key)

@router.get("/v1/docking/jobs/{job_id}")
async def get_job(job_id: str, service: Annotated[SandboxBrokerService, Depends(get_service)]):
    return service.get_job(job_id)

@router.get("/v1/docking/jobs/{job_id}/manifest")
async def get_manifest(job_id: str, service: Annotated[SandboxBrokerService, Depends(get_service)]):
    return service.get_manifest(job_id)

@router.get("/v1/docking/jobs/{job_id}/artifacts/{artifact_id}")
async def get_artifact(job_id: str, artifact_id: str, service: Annotated[SandboxBrokerService, Depends(get_service)]):
    record, stream = service.open_artifact(job_id, artifact_id)
    return StreamingResponse(stream, media_type=record.media_type)

@router.post("/v1/docking/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str, service: Annotated[SandboxBrokerService, Depends(get_service)]):
    return await service.cancel(job_id)

@router.get("/healthz")
async def health():
    return {"status": "ok", "operation": "molecular_docking"}
```

Map only stable error codes to responses. Do not return exception text. The lifespan must initialize the database, call `recover()`, start the one consumer, and stop it with bounded cleanup.

- [ ] **Step 4: Add the Uvicorn entrypoint**

`scripts/run_sandbox_broker.py` must delete only a stale socket proven to be a socket under the configured runtime directory, set `umask(0o077)`, and run:

```python
uvicorn.run(
    create_app(config),
    uds=str(config.socket_path),
    workers=1,
    access_log=False,
    log_level="info",
)
```

After bind, deployment owns socket group/mode through `RuntimeDirectoryMode=0750` and service `UMask=0007`; the worker and Broker share group `medchat-sandbox`.

- [ ] **Step 5: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_api.py -q -p no:cacheprovider`

Expected: PASS.

```powershell
git add src/sandbox_broker/app.py scripts/run_sandbox_broker.py tests/sandbox_broker/test_api.py
git commit -m "feat: expose sandbox broker API"
```

### Task 7: Worker-side Broker runner and explicit backend selection

**Files:**
- Create: `src/docking/sandbox_runner.py`
- Modify: `src/task_runtime/docking_execution.py`
- Modify: `src/task_runtime/production_worker.py`
- Modify: `deployment/temporal-worker.env.example`
- Create: `tests/sandbox_broker/test_worker_runner.py`
- Modify: `tests/task_runtime/test_docking_execution.py`
- Modify: `tests/test_temporal_worker_trust_boundary.py`

- [ ] **Step 1: Write failing runner and no-fallback tests**

```python
def test_runner_downloads_registered_pose_and_returns_tool_result(tmp_path) -> None:
    transport = broker_transport(success_manifest())
    runner = SandboxDockingRunner(
        socket_path=tmp_path / "broker.sock",
        output_root=tmp_path / "outputs",
        transport=transport,
    )
    result = runner.execute(valid_payload(tmp_path), job_id="task-1")
    assert result.success is True
    assert result.data["total_poses"] == 2
    assert result.data["best_pose"]["binding_energy"] == -7.4
    assert Path(result.data["pose_file"]).is_file()
    assert result.provenance.demo_mode is False
    assert result.provenance.fallback_used is False
    assert result.quality["execution_backend"] == "opensandbox"


def test_selected_opensandbox_failure_never_calls_local_tool(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "opensandbox")
    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_SOCKET", str(tmp_path / "missing.sock"))
    monkeypatch.setattr(MolecularDocking, "execute", lambda *args, **kwargs: pytest.fail("local fallback"))
    execution = DockingExecution(tmp_path / "staging", allowed_output_root=tmp_path / "outputs")
    result = execution.run_verified("task-1", build_manifest(tmp_path))
    assert result["success"] is False
    assert result["error"]["code"] in {"tool_unavailable", "external_tool_unavailable"}


def test_production_validator_requires_opensandbox_backend(monkeypatch, production_config) -> None:
    monkeypatch.setenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "local")
    with pytest.raises(ProductionWorkerValidationError) as exc:
        validate_production_worker_config(production_config)
    assert "docking_backend_not_opensandbox" in exc.value.codes
```

- [ ] **Step 2: Verify failure**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_worker_runner.py tests\task_runtime\test_docking_execution.py tests\test_temporal_worker_trust_boundary.py -q -p no:cacheprovider`

Expected: FAIL because the runner and backend validation do not exist.

- [ ] **Step 3: Implement the synchronous UDS runner**

Use `httpx.Client(transport=httpx.HTTPTransport(uds=str(socket_path)))`. `execute()` must:

1. bounded-read receptor/ligand files from the already verified payload;
2. submit multipart with `Idempotency-Key=job_id`;
3. poll at 500 ms while calling the existing progress callback;
4. send cancel if `cancel_event.is_set()`;
5. accept only `succeeded` plus a valid manifest;
6. download only the registered pose artifact;
7. atomically publish it to `<allowed_output_root>/docking_<job_id>/result.pdbqt`;
8. return a standard `ToolResult` compatible with the existing `AgentResultValidator`.

The success result must include:

```python
ToolResult(
    success=True,
    status=ObservationStatus.SUCCEEDED,
    message="Real AutoDock Vina docking completed in OpenSandbox.",
    data={
        "job_id": job_id,
        "total_poses": manifest.pose_count,
        "best_pose": {"binding_energy": manifest.best_energy, "pose_file": str(pose_path)},
        "pose_file": str(pose_path),
        "warnings": manifest.warnings,
    },
    warnings=manifest.warnings,
    artifacts=[WorkflowArtifact(artifact_type="docking_pose", path=str(pose_path))],
    provenance=ToolProvenance(
        tool_name="molecular_docking",
        tool_version=manifest.provenance.vina_version,
        model_name="AutoDock Vina",
        model_version=manifest.provenance.vina_version,
        demo_mode=False,
        fallback_used=False,
    ),
    quality={
        "real_execution": True,
        "execution_backend": "opensandbox",
        "secure_runtime": "gvisor",
        "sandbox_image_digest": manifest.provenance.image_digest,
        "docking_inputs": {"receptor_provided": True, "ligand_provided": True},
    },
)
```

- [ ] **Step 4: Integrate explicit backend selection at the existing raw-executor seam**

In `DockingExecution.__init__`, keep injected executors unchanged. Otherwise select:

```python
backend = os.getenv("MEDCHAT_DOCKING_EXECUTION_BACKEND", "local")
if backend == "local":
    self.raw_executor = self._execute_local_tool
elif backend == "opensandbox":
    self.raw_executor = SandboxDockingRunner.from_env(self._allowed_output_root).execute
else:
    raise ValueError("invalid docking execution backend")
```

Rename the current `_execute_production_tool` to `_execute_local_tool`. Do not change `_run_under_lease`, validator, pose stream validation, or completion commit semantics.

Add `docking_backend_not_opensandbox`, `sandbox_socket_not_configured`, and `sandbox_socket_outside_runtime` to the production worker's stable validation codes. The production validator must require backend `opensandbox` and an explicitly configured absolute socket under `/run/medchat-sandbox/`.

- [ ] **Step 5: Update the worker environment contract**

Append only non-secret settings:

```text
MEDCHAT_DOCKING_EXECUTION_BACKEND=opensandbox
MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock
```

The OpenSandbox API key remains exclusive to Broker/OpenSandbox environment files and must not appear in `temporal-worker.env.example`.

- [ ] **Step 6: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_worker_runner.py tests\task_runtime\test_docking_execution.py tests\test_temporal_worker_trust_boundary.py -q -p no:cacheprovider`

Expected: PASS.

```powershell
git add src/docking/sandbox_runner.py src/task_runtime/docking_execution.py src/task_runtime/production_worker.py deployment/temporal-worker.env.example tests/sandbox_broker/test_worker_runner.py tests/task_runtime/test_docking_execution.py tests/test_temporal_worker_trust_boundary.py
git commit -m "feat: route Temporal docking through sandbox broker"
```

### Task 8: Reproducible non-root docking image and fixed command

**Files:**
- Create: `deployment/opensandbox/Dockerfile.docking`
- Create: `deployment/opensandbox/run_docking.py`
- Create: `deployment/opensandbox/environment-lock.yml`
- Create: `tests/sandbox_broker/test_docking_image_contract.py`

- [ ] **Step 1: Write static contract tests**

```python
def test_dockerfile_is_non_root_pinned_and_has_no_project_copy() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.startswith("FROM mambaorg/micromamba:2.3.2")
    assert "USER 65532:65532" in text
    assert "COPY deployment/opensandbox/run_docking.py /opt/medchat/run_docking.py" in text
    assert "COPY . " not in text
    assert "ADD " not in text
    assert "curl " not in text


def test_wrapper_uses_argv_without_shell_and_fixed_paths() -> None:
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"system", "popen"}
        for node in calls
    )
    source = RUNNER.read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert 'Path("/workspace/input")' in source
    assert 'Path("/workspace/output")' in source
    assert "allow_nan=False" in source
```

- [ ] **Step 2: Verify failure**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_docking_image_contract.py -q -p no:cacheprovider`

Expected: FAIL because image assets are missing.

- [ ] **Step 3: Add the locked image**

`environment-lock.yml` must pin Python 3.10, AutoDock Vina, Meeko, RDKit, ProDy, NumPy, and Open Babel to versions resolved by `micromamba create --file`. Generate the lock with an explicit linux-64 solve and commit the resolved build strings; do not use wildcard or floating versions.

The Dockerfile must:

```dockerfile
FROM mambaorg/micromamba:2.3.2
COPY --chown=$MAMBA_USER:$MAMBA_USER deployment/opensandbox/environment-lock.yml /tmp/environment-lock.yml
RUN micromamba install -y -n base -f /tmp/environment-lock.yml && micromamba clean --all --yes
USER root
RUN install -d -o 65532 -g 65532 -m 0700 /workspace/input /workspace/output \
    && install -d -o root -g root -m 0555 /opt/medchat
COPY deployment/opensandbox/run_docking.py /opt/medchat/run_docking.py
RUN chown root:root /opt/medchat/run_docking.py && chmod 0555 /opt/medchat/run_docking.py
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["sleep", "infinity"]
```

- [ ] **Step 4: Implement the fixed scientific command**

`run_docking.py` must parse only `--request /workspace/input/request.json --output /workspace/output`, verify input hashes, call tools with argv lists and 180-second cumulative timeout, and atomically write result JSON. The exact external command sequence is:

```python
run(["mk_prepare_receptor.py", "-i", str(receptor), "--write_pdbqt", str(receptor_pdbqt)])
run(["mk_prepare_ligand.py", "-i", str(ligand), "-o", str(ligand_pdbqt)])
run([
    "vina", "--receptor", str(receptor_pdbqt), "--ligand", str(ligand_pdbqt),
    "--center_x", str(center[0]), "--center_y", str(center[1]), "--center_z", str(center[2]),
    "--size_x", str(size[0]), "--size_y", str(size[1]), "--size_z", str(size[2]),
    "--exhaustiveness", str(exhaustiveness), "--num_modes", str(num_modes),
    "--energy_range", str(energy_range), "--out", str(pose_file),
])
```

Write `result.json` only after parsing all `REMARK VINA RESULT` lines and validating finite energies. On failure, write a result with `status="failed"`, stable phase/error code, no `best_energy`, and exit nonzero.

- [ ] **Step 5: Run static tests and a local image smoke test**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_docking_image_contract.py -q -p no:cacheprovider`

Expected: PASS.

Run in QEMU during Task 11: `docker build --pull --no-cache -f deployment/opensandbox/Dockerfile.docking -t medchat-docking:opensandbox .`

Expected: image builds successfully and `docker run --rm medchat-docking:opensandbox id -u` prints `65532`.

For QEMU's digest-pinned reference, push the image to a loopback-only disposable registry and capture the manifest digest:

```bash
docker run -d --restart=unless-stopped --name medchat-local-registry -p 127.0.0.1:5000:5000 registry:2
docker tag medchat-docking:opensandbox 127.0.0.1:5000/medchat-docking:opensandbox
docker push 127.0.0.1:5000/medchat-docking:opensandbox
docker image inspect 127.0.0.1:5000/medchat-docking:opensandbox --format '{{index .RepoDigests 0}}'
```

Expected: one reference shaped as `127.0.0.1:5000/medchat-docking@sha256:` followed by 64 lowercase hexadecimal characters. Production must use its approved private registry instead of this QEMU-only registry.

```powershell
git add deployment/opensandbox/Dockerfile.docking deployment/opensandbox/run_docking.py deployment/opensandbox/environment-lock.yml tests/sandbox_broker/test_docking_image_contract.py
git commit -m "feat: add sandboxed Vina image"
```

### Task 9: Hardened OpenSandbox and Broker deployment assets

**Files:**
- Create: `deployment/opensandbox/sandbox.toml`
- Create: `deployment/opensandbox/medchat-opensandbox.service`
- Create: `deployment/opensandbox/medchat-sandbox-broker.service`
- Create: `deployment/opensandbox/opensandbox.env.example`
- Create: `deployment/opensandbox/install.sh`
- Create: `scripts/validate_opensandbox_deployment.py`
- Create: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `deployment/medchat-temporal-worker.service`

- [ ] **Step 1: Write failing deployment tests**

```python
def test_server_config_is_authenticated_loopback_gvisor_and_mount_safe() -> None:
    config = tomllib.loads(SANDBOX_TOML.read_text(encoding="utf-8"))
    assert config["server"]["host"] == "127.0.0.1"
    assert config["runtime"] == {"type": "docker", "execd_image": "opensandbox/execd:v1.0.21"}
    assert config["secure_runtime"] == {"type": "gvisor", "docker_runtime": "runsc"}
    assert config["docker"]["pids_limit"] == 128
    assert config["docker"]["network_mode"] == "bridge"
    assert config["storage"]["allowed_host_paths"] == ["/var/lib/opensandbox/approved-empty"]
    assert config["server"]["api_key"] == "${OPEN_SANDBOX_API_KEY}"


def test_only_opensandbox_service_has_docker_socket_access() -> None:
    server = OPENSANDBOX_SERVICE.read_text(encoding="utf-8")
    broker = BROKER_SERVICE.read_text(encoding="utf-8")
    worker = TEMPORAL_WORKER_SERVICE.read_text(encoding="utf-8")
    assert "/var/run/docker.sock" in server
    assert "/var/run/docker.sock" not in broker
    assert "/var/run/docker.sock" not in worker
    assert "ProtectSystem=strict" in broker
    assert "NoNewPrivileges=true" in broker
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in broker
    assert "/run/medchat-sandbox" in worker
```

- [ ] **Step 2: Verify failure**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_deployment_assets.py -q -p no:cacheprovider`

Expected: FAIL because deployment assets are missing.

- [ ] **Step 3: Add OpenSandbox Server configuration**

Use `execd_image="opensandbox/execd:v1.0.21"`, loopback, API-key auth, Docker bridge, gVisor `runsc`, `pids_limit=128`, dangerous capability drops, `no_new_privileges=true`, and a non-empty host-mount allowlist containing only `/var/lib/opensandbox/approved-empty`. The installer must create that root-owned empty directory mode `0000`; neither Broker nor worker may write it.

The committed TOML uses `${OPEN_SANDBOX_API_KEY}` as an install-time token. `install.sh` must render it from `/etc/medchat/opensandbox.env` into `/etc/medchat/opensandbox.toml` mode `0600` without printing the value. The rendered file is never committed.

- [ ] **Step 4: Add hardened systemd units and installer**

Service ownership:

- `medchat-opensandbox`: user/group with Docker socket membership; loopback only; reads `/etc/medchat/opensandbox.env`.
- `medchat-sandbox-broker`: user `medchat-sandbox`, group `medchat-sandbox`; writes only `/var/lib/medchat-sandbox` and `/run/medchat-sandbox`; no Docker socket.
- Temporal worker: add supplementary group `medchat-sandbox` and read/write access only to the Unix socket runtime directory; no OpenSandbox key.

Both new services must set `UMask=0077`, `PrivateTmp=true`, `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateDevices=true`, `ProtectKernelTunables=true`, `ProtectKernelModules=true`, `ProtectControlGroups=true`, and explicit `ReadWritePaths`.

- [ ] **Step 5: Implement production validation**

`scripts/validate_opensandbox_deployment.py --static` must parse committed assets and emit only:

```text
opensandbox_deployment_validation=passed
```

or:

```text
opensandbox_deployment_validation=failed code=runtime_not_gvisor
```

Other failures substitute another code from the validator's committed `_STABLE_CODES` set; raw exception text is never printed.

`--runtime` additionally checks `runsc --version`, Docker runtime registration, OpenSandbox `/health`, socket type/owner/mode, image repo digest, and systemd active states. Never serialize command stderr, environment values, absolute user paths, or API keys.

- [ ] **Step 6: Run tests and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_deployment_assets.py -q -p no:cacheprovider`

Expected: PASS.

```powershell
git add deployment/opensandbox/sandbox.toml deployment/opensandbox/medchat-opensandbox.service deployment/opensandbox/medchat-sandbox-broker.service deployment/opensandbox/opensandbox.env.example deployment/opensandbox/install.sh deployment/medchat-temporal-worker.service scripts/validate_opensandbox_deployment.py tests/sandbox_broker/test_deployment_assets.py
git commit -m "feat: deploy hardened OpenSandbox services"
```

### Task 10: Security and failure-mode integration suite

**Files:**
- Create: `tests/sandbox_broker/test_security_contract.py`
- Modify: `tests/agent/test_domain_result_validators.py`
- Modify: `tests/task_runtime/test_temporal_acceptance.py`
- Modify: `scripts/run_temporal_docking_acceptance.py`

- [ ] **Step 1: Add adversarial Broker API and worker tests**

Cover these exact cases with fake SDK/Broker transports:

```python
@pytest.mark.parametrize("field", ["image", "command", "entrypoint", "env", "mounts", "network_policy"])
def test_caller_cannot_control_sandbox_policy(field, client, valid_request, valid_files) -> None:
    response = submit(client, valid_request | {field: "attacker-controlled"}, valid_files)
    assert response.status_code == 422


@pytest.mark.parametrize("failure", [
    "opensandbox_unavailable", "provisioning_failed", "upload_failed",
    "execution_timeout", "command_failed", "scientific_output_invalid",
    "artifact_failed", "cleanup_failed",
])
def test_every_broker_failure_returns_no_energy_or_pose(failure, runner, broker_failure_transport) -> None:
    result = runner.with_transport(broker_failure_transport(failure)).execute(valid_payload(), job_id="task-1")
    assert result.success is False
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "binding_energy" not in encoded
    assert "kcal/mol" not in encoded
    assert "pose_file" not in encoded


def test_secret_and_host_path_redaction_across_all_surfaces(client, runner, tmp_path) -> None:
    secret = "sk-" + "sensitive-value-123456789"
    injected = failing_sdk(stderr=secret + " C:/Users/private/input.pdb")
    response = run_through_api_and_runner(client, runner, injected)
    encoded = json.dumps(response, ensure_ascii=False)
    assert secret not in encoded
    assert "C:/Users/private" not in encoded
```

- [ ] **Step 2: Add scientific validator regressions**

Extend existing domain validator tests so `execution_backend="opensandbox"` is accepted only with `secure_runtime="gvisor"`, a 64-character image digest, finite energy, real-execution provenance, and a verified pose artifact. Add cases rejecting `runc`, missing digest, `demo_mode=True`, `fallback_used=True`, and cleanup failure.

- [ ] **Step 3: Add Temporal acceptance projection fields**

Extend `summarize_runs()` input and normalized output with allowlisted `execution_backend`, `secure_runtime`, `sandbox_image_digest`, and `cleanup_status`. When `expected_execution_backend="opensandbox"`, any missing/mismatched value is a hard failure. Add assertions that reports contain no sandbox API key, sandbox raw endpoint, container ID, or host path.

- [ ] **Step 4: Run focused and full regressions**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker tests\task_runtime\test_docking_execution.py tests\task_runtime\test_temporal_acceptance.py tests\agent\test_domain_result_validators.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

Expected: all tests pass; compileall exits 0.

- [ ] **Step 5: Commit**

```powershell
git add tests/sandbox_broker/test_security_contract.py tests/agent/test_domain_result_validators.py tests/task_runtime/test_temporal_acceptance.py scripts/run_temporal_docking_acceptance.py
git commit -m "test: enforce sandbox scientific trust gates"
```

### Task 11: QEMU bootstrap and real gVisor acceptance

**Files:**
- Create: `deployment/opensandbox/README.md`
- Create: `scripts/run_opensandbox_docking_acceptance.py`
- Create: `tests/sandbox_broker/test_real_opensandbox_acceptance.py`

- [ ] **Step 1: Write the opt-in real acceptance test**

```python
RUN_REAL = os.environ.get("MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE") == "1"


@pytest.mark.skipif(not RUN_REAL, reason="real OpenSandbox acceptance is opt-in")
def test_magL_sample_runs_three_times_through_gvisor(tmp_path) -> None:
    report = run_real_acceptance(
        receptor=Path("data/samples/MAGL_5zun.pdb"),
        ligand=Path("data/samples/5.sdf"),
        center=(5.99, 3.01, 17.345),
        size=(20.0, 20.0, 20.0),
        repeat=3,
        report_path=tmp_path / "report.json",
    )
    assert report["status"] == "passed"
    assert report["pass_rate"] == 1.0
    assert all(run["secure_runtime"] == "gvisor" for run in report["runs"])
    assert all(run["pose_count"] > 0 for run in report["runs"])
    assert all(math.isfinite(run["best_energy"]) for run in report["runs"])
```

- [ ] **Step 2: Implement the structured acceptance runner**

The script accepts `--repeat 3 --report outputs/agent_evaluation/opensandbox_docking_acceptance.json`. It must:

1. run static/runtime deployment validation;
2. inspect the created sandbox container and require Docker `Runtime=runsc`;
3. submit the MAGL sample through the worker-side runner, not directly through OpenSandbox;
4. verify pose bytes/hash/count/energy/tool versions/image digest/cleanup per run;
5. test same-key reuse creates one sandbox;
6. test cancellation and timeout leave no running sandbox;
7. run in-sandbox probes that cannot read `/opt/medchat/molecular_chat_system`, `.env`, task SQLite, or `/var/run/docker.sock`;
8. require outbound network probe failure;
9. require CPU/memory/PID limits from Docker inspection and a PID exhaustion probe that is terminated;
10. require write attempts under `/etc` and `/opt/medchat` to fail while `/workspace/output` remains writable;
11. calculate pass rate and p50/p95 latency.

The report schema is:

```python
report = {
    "schema_version": 1,
    "status": "passed" if all(run["status"] == "passed" for run in runs) and not security_failures else "failed",
    "backend": "opensandbox",
    "secure_runtime": "gvisor",
    "repeat": repeat,
    "pass_rate": passed / repeat,
    "p50_latency_ms": percentile(latencies, 0.50),
    "p95_latency_ms": percentile(latencies, 0.95),
    "security_failures": sorted(set(security_failures)),
    "failure_type_distribution": failure_distribution,
    "runs": runs,
}
```

Each run contains only trace-safe IDs, pose count, finite energy, repo-relative artifact path, artifact hash, tool versions, image digest, secure runtime, cleanup status, latency, warning codes, and failures. It must not contain prompts, receptor/ligand contents, host absolute paths, raw Docker inspect output, or credentials.

- [ ] **Step 3: Document exact QEMU deployment sequence**

The operator guide must use the existing Ubuntu 24.04 QEMU environment and these commands:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg python3-venv
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main' | sudo tee /etc/apt/sources.list.d/gvisor.list
sudo apt-get update
sudo apt-get install -y runsc
sudo runsc install
sudo systemctl restart docker
sudo deployment/opensandbox/install.sh
sudo systemctl enable --now medchat-opensandbox medchat-sandbox-broker medchat-temporal-worker
python scripts/validate_opensandbox_deployment.py --runtime
MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1 python -m pytest tests/sandbox_broker/test_real_opensandbox_acceptance.py -q -p no:cacheprovider
python scripts/run_opensandbox_docking_acceptance.py --repeat 3 --report outputs/agent_evaluation/opensandbox_docking_acceptance.json
```

The guide must instruct the operator to generate the OpenSandbox API key with `openssl rand -hex 32`, write it directly to `/etc/medchat/opensandbox.env` mode `0600`, and never paste it into Git, shell history, reports, or chat.

- [ ] **Step 4: Run non-real test and commit**

Run: `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker\test_real_opensandbox_acceptance.py -q -p no:cacheprovider`

Expected: one honest SKIP unless `MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1` is explicitly set on Linux.

```powershell
git add deployment/opensandbox/README.md scripts/run_opensandbox_docking_acceptance.py tests/sandbox_broker/test_real_opensandbox_acceptance.py
git commit -m "test: add real OpenSandbox docking acceptance"
```

### Task 12: Final verification and implementation handoff

**Files:**
- Modify: `docs/handoff/latest.md`
- Create during execution only, never stage: `outputs/agent_evaluation/opensandbox_docking_acceptance.json`

- [ ] **Step 1: Run Windows/Conda contract regression**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\sandbox_broker tests\task_runtime\test_docking_execution.py tests\task_runtime\test_temporal_acceptance.py tests\agent\test_domain_result_validators.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\validate_opensandbox_deployment.py --static
```

Expected: all tests pass, compileall exits 0, static deployment validation passes.

- [ ] **Step 2: Run QEMU real acceptance**

Use SSH with the existing dedicated QEMU key and known-hosts file, check out this branch in `/home/medchat/molecular_chat_system`, install the approved services, and run the Task 11 commands. Do not use WSL. Do not copy the host `.env` or API credentials into the VM repository.

Expected release gate:

- 3/3 MAGL runs passed;
- p50/p95 latency present;
- positive pose count and finite energy for every run;
- artifact files exist and hashes match;
- image is digest-pinned and Docker reports `runsc`;
- network/project/credential/Docker-socket probes are denied;
- no duplicate sandbox for one idempotency key;
- cancellation/timeout cleanup leaves no sandbox;
- no local Vina execution observed;
- no secret or host path in report.

If any dependency or gate is unavailable, record `failed` or `skipped` with an allowlisted reason and do not mark the phase complete.

- [ ] **Step 3: Update the handoff with evidence**

Record branch, commits, exact test counts, QEMU OS/runtime versions, image digest, OpenSandbox SDK/server versions, gVisor version, Vina/Meeko versions, report relative path, failed/skipped reasons, and rollback command. Do not include any API key, absolute user path, raw environment, receptor/ligand content, or Docker inspect dump.

- [ ] **Step 4: Check scope and secret safety**

Run:

```powershell
git diff --check
git status --short
git diff --name-only (git merge-base HEAD effb447)..HEAD
rg -n "sk-[A-Za-z0-9_-]{8,}|OPEN_SANDBOX_API_KEY=.*[A-Za-z0-9]" src tests scripts deployment docs requirements-opensandbox-*.txt
```

Expected: no whitespace errors; changed paths are only those listed in this plan; secret scan has no committed value assignment.

- [ ] **Step 5: Commit the handoff only**

```powershell
git add docs/handoff/latest.md
git commit -m "docs: hand off OpenSandbox docking broker"
```

Do not stage `outputs/`, QEMU keys, rendered `/etc/medchat` files, SQLite databases, image archives, Docker inspect output, or any runtime credential.

## Rollback contract

Before production default changes, rollback is configuration-only: stop the Broker/OpenSandbox services and deploy the preceding worker generation with its preceding environment file. Never switch a running production worker to `local`; production validation must reject that state. For developer-only local use, `MEDCHAT_DOCKING_EXECUTION_BACKEND=local` remains available outside production validation.

## Primary references

- OpenSandbox Python SDK lifecycle, files, commands, resources, and deny-by-default network policy: <https://github.com/opensandbox-group/OpenSandbox/blob/main/sdks/sandbox/python/README.md>
- OpenSandbox secure runtime startup validation and gVisor configuration: <https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/secure-container.md>
- OpenSandbox server configuration, API authentication, Docker PID limits, and host-mount allowlist: <https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md>
- gVisor installation: <https://gvisor.dev/docs/user_guide/install/>
- Meeko receptor and ligand CLI contracts: <https://github.com/forlilab/Meeko/blob/develop/docs/source/cli_rec_prep.rst>
