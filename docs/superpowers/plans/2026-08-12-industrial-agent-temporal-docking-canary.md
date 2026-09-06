# Temporal Docking Canary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reversible Temporal backend for asynchronous real docking tasks, with durable lifecycle, heartbeat, cancellation, idempotency, scientific validation, and zero duplicate Vina execution.

**Architecture:** Keep FastAPI and the SQLite task runtime behind a new async `TaskRuntimeBackend` façade. Route only stable canary-selected docking submissions to a dedicated Temporal workflow and worker; local submissions use the same cancellable docking primitive. Temporal owns lifecycle history, while `MolecularDocking`, `execute_tool_compat`, `AgentResultValidator`, and real pose artifacts remain the scientific authority.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, Temporal Python SDK 1.30.0, pytest/pytest-asyncio, RDKit, AutoDock Vina, Meeko, ADFRsuite.

---

## File map

Create focused modules under `src/task_runtime/`: `config.py`, `selector.py`, `store.py`, `staging.py`, `completion.py`, `docking_execution.py`, `runtime.py`, `backends/{base,local,temporal}.py`, and `temporal/{activities,workflows,worker,reconcile}.py`. Add `scripts/run_temporal_dev_server.py`, `scripts/run_temporal_docking_worker.py`, `scripts/run_temporal_docking_acceptance.py`, and `requirements-agent-temporal.txt`.

`src/task_runtime/metrics.py` owns the fixed metric names and emits sanitized metric events through `TaskStore`; it does not introduce a monitoring vendor.

This plan intentionally excludes PostgreSQL, Redis, NATS, object storage, Kubernetes, multi-worker scheduling, full Agent workflow migration, and removal of the synchronous docking endpoint. Those remain Stage 3B or later.

Modify only the relevant task runtime, docking adapters/service/tool, docking/task routes, app wiring, health check, tests, standards, and handoff files. Do not refactor unrelated Agent, frontend, model, or scientific code.

## Task 1: Pin Temporal and add fail-closed configuration

**Files:**
- Create: `requirements-agent-temporal.txt`
- Create: `src/task_runtime/config.py`
- Create: `src/task_runtime/selector.py`
- Test: `tests/task_runtime/test_temporal_config.py`
- Test: `tests/task_runtime/test_backend_selector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/task_runtime/test_temporal_config.py
import pytest
from src.task_runtime.config import TaskRuntimeConfig


def test_defaults_are_local_and_zero(monkeypatch):
    monkeypatch.delenv("MEDCHAT_TASK_BACKEND", raising=False)
    monkeypatch.delenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", raising=False)
    config = TaskRuntimeConfig.from_env()
    assert (config.backend, config.canary_percent, config.docking_concurrency) == (
        "local", 0, 1
    )


@pytest.mark.parametrize("value", ["-1", "101", "1.5", "true", ""])
def test_invalid_percent_fails_closed(monkeypatch, value):
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", value)
    config = TaskRuntimeConfig.from_env()
    assert config.backend == "local"
    assert config.canary_percent == 0
    assert "invalid_temporal_canary_percent" in config.warnings
```

```python
# tests/task_runtime/test_backend_selector.py
from src.task_runtime.selector import TemporalDockingSelector


def test_only_healthy_docking_enters_temporal():
    selector = TemporalDockingSelector(100)
    selected = selector.select("docking", "task-1", True)
    blocked = selector.select("agent_workflow", "task-1", True)
    assert selected.backend == "temporal"
    assert (blocked.backend, blocked.reason) == (
        "local", "task_type_not_allowlisted"
    )


def test_selector_is_stable_and_does_not_serialize_key():
    selector = TemporalDockingSelector(37)
    first = selector.select("docking", "private-task", True)
    second = selector.select("docking", "private-task", True)
    assert first == second
    assert "private-task" not in repr(first)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_config.py tests\task_runtime\test_backend_selector.py -q -p no:cacheprovider
```

Expected: collection fails because `config` and `selector` do not exist.

- [ ] **Step 3: Implement strict configuration and selection**

Create the optional dependency:

```text
temporalio==1.30.0
```

Implement `TaskRuntimeConfig` as a frozen dataclass with `backend`, `canary_percent`, Temporal address/namespace/queue, concurrency, staging root, and warnings. `from_env()` accepts only `local|temporal_canary`, exact integer percentages from 0 through 100, and concurrency exactly 1. Any malformed value sets local/0 and appends a stable warning code.

Implement the selector with this public contract:

```python
@dataclass(frozen=True)
class BackendDecision:
    backend: str
    reason: str
    bucket: int | None
    percent: int


class TemporalDockingSelector:
    def select(
        self, task_type: str, task_id: str, temporal_available: bool
    ) -> BackendDecision:
        if task_type != "docking":
            return BackendDecision("local", "task_type_not_allowlisted", None, self.percent)
        if temporal_available is not True:
            return BackendDecision("local", "temporal_unavailable", None, self.percent)
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % 100
        if bucket < self.percent:
            return BackendDecision("temporal", "canary_selected", bucket, self.percent)
        return BackendDecision("local", "canary_not_selected", bucket, self.percent)
```

Reject non-string, empty, oversized, and invalid-Unicode task IDs before hashing. Never store or serialize the original key.

- [ ] **Step 4: Install and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pip install -r requirements-agent-temporal.txt
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pip check
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_config.py tests\task_runtime\test_backend_selector.py -q -p no:cacheprovider
```

Expected: `pip check` is clean and all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- requirements-agent-temporal.txt src/task_runtime/config.py src/task_runtime/selector.py tests/task_runtime/test_temporal_config.py tests/task_runtime/test_backend_selector.py
git commit -m "feat(runtime): configure temporal docking canary"
```

## Task 2: Version task records and add atomic SQLite projection

**Files:**
- Modify: `src/task_runtime/models.py`
- Modify: `src/task_runtime/database.py`
- Create: `src/task_runtime/errors.py`
- Create: `src/task_runtime/store.py`
- Modify: `src/task_runtime/manager.py`
- Test: `tests/task_runtime/test_task_store.py`
- Test: `tests/test_task_runtime.py`

- [ ] **Step 1: Write failing migration and terminal tests**

```python
# tests/task_runtime/test_task_store.py
from src.task_runtime.models import TaskStatus
from src.task_runtime.store import TaskStore


def test_existing_database_is_migrated_without_row_loss(tmp_path):
    from src.task_runtime.database import connect
    path = tmp_path / "tasks.sqlite"
    with connect(path) as conn:
        conn.execute(
            "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, task_type TEXT, "
            "status TEXT, input_json TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
            ("old", "demo", "queued", "{}", "now", "now"),
        )
    record = TaskStore(path).get("old")
    assert (record.task_id, record.backend) == ("old", "local")


def test_terminal_transition_and_event_are_single_writer(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    store.create("task-1", "docking", {}, backend="temporal")
    assert store.finish("task-1", TaskStatus.SUCCEEDED, result={"ok": True})
    assert not store.finish("task-1", TaskStatus.FAILED, error="late")
    terminal = [event for event in store.events("task-1") if event.is_terminal]
    assert [event.event_type for event in terminal] == ["task_succeeded"]
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_task_store.py tests\test_task_runtime.py -q -p no:cacheprovider
```

Expected: failure because `TaskStore` and new contract fields do not exist.

- [ ] **Step 3: Extend models and append-only schema**

Add `CANCEL_REQUESTED` and `TIMED_OUT` to `TaskStatus`. Add `backend`, `external_workflow_id`, `phase`, `progress`, `attempt`, `heartbeat_at`, `error_code`, `warnings`, `input_manifest_path`, and `provenance` to `TaskRecord`, `from_row()`, and `to_dict()`.

Add:

```python
@dataclass(frozen=True)
class TaskEvent:
    event_id: str
    task_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    is_terminal: bool
    created_at: str
```

Create a string enum `TaskErrorCode` containing exactly:

```text
TASK_INPUT_INVALID
TASK_INPUT_HASH_MISMATCH
TASK_BACKEND_UNAVAILABLE
TEMPORAL_START_FAILED
TEMPORAL_WORKER_UNAVAILABLE
TASK_HEARTBEAT_TIMEOUT
TASK_CANCEL_TIMEOUT
DOCKING_ENVIRONMENT_UNAVAILABLE
DOCKING_PROCESS_FAILED
DOCKING_PROCESS_OWNERSHIP_UNCERTAIN
DOCKING_ARTIFACT_INVALID
SCIENTIFIC_VALIDATION_FAILED
TASK_PROJECTION_FAILED
```

All subsequent runtime code persists enum values and never classifies failures by matching human-readable messages.

After the existing `CREATE TABLE IF NOT EXISTS`, inspect `PRAGMA table_info(tasks)` and add only missing columns. Create:

```sql
CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    is_terminal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, sequence),
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_events_one_terminal
ON task_events(task_id) WHERE is_terminal = 1;

CREATE TABLE IF NOT EXISTS task_worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    task_queue TEXT NOT NULL,
    concurrency INTEGER NOT NULL,
    sdk_version TEXT,
    updated_at TEXT NOT NULL
);
```

- [ ] **Step 4: Implement transactional `TaskStore`**

Implement `create`, `get`, `list`, `heartbeat`, `request_cancel`, `events`, `finish`, `record_worker_heartbeat`, and `worker_health`. Allocate event sequence inside `BEGIN IMMEDIATE`. `finish()` uses a CAS update whose predicate excludes `succeeded`, `failed`, `canceled`, and `timed_out`, then inserts a terminal event only when `rowcount == 1`.

Preserve the old `TaskManager.submit/get/list` signatures by delegating persistence to one store instance. Existing returned `success=False` and `status=failed` normalization must remain unchanged.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_task_store.py tests\test_task_runtime.py tests\test_phase2_phase3_routes.py -q -p no:cacheprovider
git add -- src/task_runtime/models.py src/task_runtime/database.py src/task_runtime/errors.py src/task_runtime/store.py src/task_runtime/manager.py tests/task_runtime/test_task_store.py tests/test_task_runtime.py
git commit -m "feat(runtime): add atomic task projection"
```

Expected: old rows remain readable and terminal state/event is unique.

## Task 3: Stage task-owned inputs and verify input manifests

**Files:**
- Create: `src/task_runtime/staging.py`
- Modify: `.gitignore`
- Test: `tests/task_runtime/test_staging.py`

- [ ] **Step 1: Write failing security tests**

```python
# tests/task_runtime/test_staging.py
import json
import pytest
from src.task_runtime.staging import DockingInputStager, ManifestError


def test_staged_files_are_hashed_and_survive_request_scope(tmp_path):
    stager = DockingInputStager(tmp_path)
    path = stager.stage(
        "task-1", "receptor.pdb", b"ATOM\n", "ligand.sdf", b"$$$$\n",
        None, {"center": [1, 2, 3], "size": [20, 20, 20]},
    )
    manifest = stager.load_verified("task-1", path)
    assert manifest["receptor"]["sha256"]
    assert (tmp_path / "task-1" / "inputs" / "receptor.pdb").is_file()


def test_manifest_rejects_path_escape(tmp_path):
    stager = DockingInputStager(tmp_path)
    path = stager.stage(
        "task-1", "r.pdb", b"ATOM", "l.sdf", b"$$$$", None,
        {"center": [1, 2, 3], "size": [20, 20, 20]},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["receptor"]["path"] = "../../secret"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError):
        stager.load_verified("task-1", path)


def test_cleanup_preserves_protected_task_directories(tmp_path):
    stager = DockingInputStager(tmp_path)
    stager.stage(
        "running", "r.pdb", b"ATOM", "l.sdf", b"$$$$", None,
        {"center": [1, 2, 3], "size": [20, 20, 20]},
    )
    removed = stager.cleanup_expired(cutoff_epoch=10**12, protected={"running"})
    assert removed == []
    assert (tmp_path / "running").is_dir()
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_staging.py -q -p no:cacheprovider
```

Expected: missing-module failure.

- [ ] **Step 3: Implement atomic staging**

Use `Path.resolve()`, `os.path.commonpath`, a temporary file in the destination directory, `os.replace`, and SHA-256. Write schema `DockingInputManifest@1` with task ID/type, receptor and ligand relative paths/sizes/hashes, ligand mode, docking config, config hash, and creation time.

Reject absolute paths, symlinks, task IDs containing separators, changed size/hash, missing receptor, multiple ligand modes, and paths outside `<staging_root>/<task_id>`. Never include SMILES or absolute paths in `repr`, warnings, or heartbeat payloads. `cleanup_expired()` accepts an explicit protected task-ID set and refuses to remove queued, running, or cancel-requested task directories.

Add:

```gitignore
# Durable task input staging
scratch/task_inputs/
```

- [ ] **Step 4: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_staging.py -q -p no:cacheprovider
git add -- .gitignore src/task_runtime/staging.py tests/task_runtime/test_staging.py
git commit -m "feat(runtime): stage durable docking inputs"
```

## Task 4: Make docking commands cooperatively cancellable

**Files:**
- Modify: `src/docking/adapters/base.py`
- Modify: `src/docking/adapters/adfr_adapter.py`
- Modify: `src/docking/adapters/meeko_adapter.py`
- Modify: `src/docking/adapters/openbabel_adapter.py`
- Modify: `src/docking/adapters/vina_adapter.py`
- Modify: `src/docking/molecular_docking_service.py`
- Modify: `src/agent/tools/molecular_docking.py`
- Test: `tests/test_docking_command_cancellation.py`
- Test: `tests/test_docking_configuration.py`

- [ ] **Step 1: Write the failing process cancellation test**

```python
# tests/test_docking_command_cancellation.py
import sys
import threading
import time
from src.docking.adapters.base import CommandAdapter, CommandCancelledError


def test_cancel_event_terminates_controlled_command(tmp_path):
    adapter = CommandAdapter(sys.executable)
    cancel = threading.Event()
    outcome = {}

    def invoke():
        try:
            adapter.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=str(tmp_path), timeout=60, cancel_event=cancel,
            )
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=invoke)
    thread.start()
    time.sleep(0.3)
    cancel.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert isinstance(outcome["error"], CommandCancelledError)
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_docking_command_cancellation.py -q -p no:cacheprovider
```

Expected: failure because `CommandCancelledError` and `cancel_event` are unsupported.

- [ ] **Step 3: Extend the existing Job Object/process-group runner**

Add `CommandCancelledError(RuntimeError)` and a keyword-only `cancel_event: threading.Event | None` to `CommandAdapter.run()`. Replace the single long `communicate()` wait with a 200 ms loop. When canceled, call the existing `_cleanup_windows_process()` or `_terminate_posix_process_group()`, drain output, and raise `CommandCancelledError("Docking command was cancelled")`.

Do not add `taskkill`, shell command strings, or a second Windows process implementation. Reuse the existing Windows Job Object and POSIX process-group code.

- [ ] **Step 4: Thread control through adapters, service, and tool**

Each adapter operation accepts `cancel_event=None` and forwards it to `run()`. Extend the service boundary exactly as follows:

```python
async def perform_docking(
    self,
    receptor_file: str,
    ligand_input: str,
    config: DockingConfig,
    input_type: str = "smiles",
    *,
    job_id: str | None = None,
    progress_callback=None,
    cancel_event=None,
) -> Dict[str, Any]:
```

Generate a UUID only when `job_id is None`. Emit the approved fixed phases and pass cancellation into receptor preparation, ligand preparation, and Vina. Convert cancellation into `success=False`, `error_code="cancelled"`, with no energy or pose claim.

Extend `MolecularDocking.execute()` with the same keyword-only control parameters and pass them into the service. Calls without controls remain backward compatible.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_docking_command_cancellation.py tests\test_docking_configuration.py tests\test_docking_agent_architecture.py tests\agent\test_prompt_acceptance.py -q -p no:cacheprovider
git add -- src/docking/adapters/base.py src/docking/adapters/adfr_adapter.py src/docking/adapters/meeko_adapter.py src/docking/adapters/openbabel_adapter.py src/docking/adapters/vina_adapter.py src/docking/molecular_docking_service.py src/agent/tools/molecular_docking.py tests/test_docking_command_cancellation.py tests/test_docking_configuration.py
git commit -m "feat(docking): support cooperative process cancellation"
```

## Task 5: Add completion manifests and a shared scientific execution primitive

**Files:**
- Create: `src/task_runtime/completion.py`
- Create: `src/task_runtime/docking_execution.py`
- Test: `tests/task_runtime/test_docking_execution.py`

- [ ] **Step 1: Write failing authority and reuse tests**

```python
# tests/task_runtime/test_docking_execution.py
from src.agent.contracts import ToolResult
from src.task_runtime.docking_execution import DockingExecution


def test_verified_completion_prevents_second_tool_call(tmp_path):
    calls = []

    def execute_tool(payload, **control):
        calls.append(payload)
        pose = tmp_path / "pose.pdbqt"
        pose.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return ToolResult.success_result(
            tool_name="molecular_docking",
            message="done",
            data={
                "total_poses": 1,
                "pose_file": str(pose),
                "best_pose": {"binding_energy": -7.2, "pose_file": str(pose)},
            },
            quality={
                "docking_inputs": {
                    "receptor_path": "r.pdb",
                    "ligand_input": "l.sdf",
                    "center": [1, 2, 3],
                    "size": [20, 20, 20],
                }
            },
        )

    runner = DockingExecution(tmp_path, raw_executor=execute_tool)
    first = runner.run_verified("task-1", {"config_hash": "abc"})
    second = runner.run_verified("task-1", {"config_hash": "abc"})
    assert first["success"] is True
    assert second["reused_completion"] is True
    assert len(calls) == 1
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_docking_execution.py -q -p no:cacheprovider
```

Expected: missing-module failure.

- [ ] **Step 3: Implement atomic completion manifests**

Write schema `DockingCompletionManifest@1` with task/input/config hashes, tool version, pose relative path/hash, pose count, finite best energy, Validator status, attempt, and completion time. Reject missing or changed pose, non-finite energy, zero poses, config mismatch, and failed validation.

- [ ] **Step 4: Implement the one scientific execution path**

`DockingExecution.__init__()` accepts an optional `raw_executor` test seam whose signature is `(payload, **control) -> ToolResult`; the production default calls `execute_tool_compat(MolecularDocking(), payload, **control)`. `run_verified()` acquires an exclusive task lock, reuses only a fully verified completion, then executes this boundary:

```python
tool_result = self.raw_executor(
    payload, job_id=task_id,
    progress_callback=progress_callback, cancel_event=cancel_event,
)
validated = AgentResultValidator().validate_tool_result(tool_result)
if not validated.success:
    return validated.to_legacy_dict()
completion = manifests.commit(task_id, input_manifest, validated)
return {
    **validated.to_legacy_dict(),
    "completion": completion,
    "reused_completion": False,
}
```

Always release the lock. Never create completion metadata before artifact and domain validation succeeds.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_docking_execution.py tests\agent\test_domain_result_validators.py -q -p no:cacheprovider
git add -- src/task_runtime/completion.py src/task_runtime/docking_execution.py tests/task_runtime/test_docking_execution.py
git commit -m "feat(runtime): validate durable docking completion"
```

## Task 6: Introduce the async backend façade and local backend

**Files:**
- Create: `src/task_runtime/backends/__init__.py`
- Create: `src/task_runtime/backends/base.py`
- Create: `src/task_runtime/backends/local.py`
- Create: `src/task_runtime/runtime.py`
- Modify: `src/task_runtime/models.py`
- Modify: `src/task_runtime/manager.py`
- Modify: `src/task_runtime/__init__.py`
- Test: `tests/task_runtime/test_local_backend.py`

- [ ] **Step 1: Write the failing backend contract test**

```python
# tests/task_runtime/test_local_backend.py
import asyncio
import pytest
from src.task_runtime.backends.local import LocalTaskBackend
from src.task_runtime.models import TaskSubmission, TaskStatus
from src.task_runtime.store import TaskStore


@pytest.mark.asyncio
async def test_local_backend_submits_and_requests_cancel(tmp_path):
    started = asyncio.Event()

    async def handler(submission, cancel_event, progress):
        started.set()
        while not cancel_event.is_set():
            await asyncio.sleep(0.01)
        return {"success": False, "error_code": "cancelled"}

    store = TaskStore(tmp_path / "tasks.sqlite")
    backend = LocalTaskBackend(store, {"docking": handler})
    submission = TaskSubmission("task-1", "docking", {}, "manifest.json")
    await backend.submit(submission)
    await started.wait()
    record = await backend.cancel("task-1", "test")
    assert record.status is TaskStatus.CANCEL_REQUESTED
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_local_backend.py -q -p no:cacheprovider
```

Expected: backend and submission contracts are missing.

- [ ] **Step 3: Add contracts and local implementation**

Add frozen `TaskSubmission(task_id, task_type, payload, input_manifest_path, idempotency_key=None)` and `BackendHealth(backend, available, message, details)` dataclasses. Define an async `TaskRuntimeBackend` Protocol with `submit/get/list/cancel/health`.

`LocalTaskBackend` maintains one `threading.Event` per running task under a lock. `cancel()` first writes `cancel_requested`, then sets the token. A canceled handler must finish as `CANCELED`, never succeeded. The handler registry maps only known task types; unknown types fail before task creation.

- [ ] **Step 4: Add the runtime façade and startup-only fallback contract**

`TaskRuntime.submit_docking()` generates task ID before staging/selection, stages inputs, writes the sanitized decision, and invokes exactly one backend. A Temporal result may fall back to local only when it explicitly reports `accepted=False`; ambiguous outcomes are not eligible for local execution.

Expose `get_task_runtime()` and `reset_task_runtime_for_tests()` without importing `temporalio` at package import time.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_local_backend.py tests\test_task_runtime.py tests\test_phase2_phase3_routes.py -q -p no:cacheprovider
git add -- src/task_runtime/backends src/task_runtime/runtime.py src/task_runtime/models.py src/task_runtime/manager.py src/task_runtime/__init__.py tests/task_runtime/test_local_backend.py
git commit -m "feat(runtime): add async task backend facade"
```

## Task 7: Implement Temporal Workflow, Activities, and worker

**Files:**
- Create: `src/task_runtime/temporal/__init__.py`
- Create: `src/task_runtime/temporal/activities.py`
- Create: `src/task_runtime/temporal/workflows.py`
- Create: `src/task_runtime/temporal/worker.py`
- Create: `scripts/run_temporal_dev_server.py`
- Create: `scripts/run_temporal_docking_worker.py`
- Test: `tests/task_runtime/test_temporal_workflow.py`

- [ ] **Step 1: Write failing workflow success and cancellation tests**

```python
# tests/task_runtime/test_temporal_workflow.py
import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from src.task_runtime.temporal.workflows import DockingWorkflow


@activity.defn(name="run_docking_activity")
async def fake_docking(payload: dict) -> dict:
    return {"success": True, "task_id": payload["task_id"], "attempt": 1}


@activity.defn(name="project_task_activity")
async def fake_projection(payload: dict) -> dict:
    return payload


@pytest.mark.asyncio
async def test_workflow_has_one_terminal_result():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        worker = Worker(
            env.client,
            task_queue="test-docking",
            workflows=[DockingWorkflow],
            activities=[fake_docking, fake_projection],
        )
        async with worker:
            result = await env.client.execute_workflow(
                DockingWorkflow.run,
                {"task_id": "task-1", "manifest_path": "manifest.json"},
                id="medchat-docking-task-1",
                task_queue="test-docking",
            )
    assert result["status"] == "succeeded"
    assert result["terminal_event_count"] == 1
```

Add a second fake Activity that blocks until canceled; cancel its workflow handle and assert the Activity `finally` block sets a local confirmation event before the workflow reaches the canceled terminal.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_workflow.py -q -p no:cacheprovider
```

Expected: Temporal workflow modules do not exist.

- [ ] **Step 3: Implement deterministic `DockingWorkflow`**

The Workflow stores a serializable snapshot and exposes `@workflow.query def task_snapshot(self) -> dict`. It invokes the docking Activity with a 30-minute start-to-close timeout, 15-second heartbeat timeout, `RetryPolicy(maximum_attempts=1)`, and `WAIT_CANCELLATION_COMPLETED`. It maps success, tool failure, cancellation, and timeout to exactly one terminal status/code.

The Workflow must not read files, SQLite, environment variables, RDKit, or docking libraries.

- [ ] **Step 4: Implement heartbeat and cancellation-confirming Activities**

Use this control shape in `run_docking_activity`:

```python
cancel_event = threading.Event()
work = asyncio.create_task(asyncio.to_thread(run_scientific_docking))
try:
    while not work.done():
        activity.heartbeat({
            "phase": progress.phase,
            "attempt": activity.info().attempt,
        })
        await asyncio.sleep(1)
    return await work
except asyncio.CancelledError:
    cancel_event.set()
    await asyncio.shield(work)
    raise
```

Projection Activities use `TaskStore` and may retry independently; they never invoke docking.

- [ ] **Step 5: Add the dedicated worker CLI**

Register only `DockingWorkflow`, projection Activities, and docking Activity. Set `max_concurrent_activities=1`. Start a bounded background heartbeat that calls `TaskStore.record_worker_heartbeat()` at a fixed interval and stops with the worker. Log queue, namespace, and SDK version only; never log input manifests or credentials.

Add a repository-owned development server script using `await WorkflowEnvironment.start_local(ip="127.0.0.1", port=7233, namespace="default")`. Hold it open until SIGINT/SIGTERM, then await `environment.shutdown()`. The SDK downloads its compatible Temporal CLI binary on first use when necessary; do not require a separately installed `temporal` executable. See <https://python.temporal.io/temporalio.testing.WorkflowEnvironment.html>.

- [ ] **Step 6: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_workflow.py -q -p no:cacheprovider
git add -- src/task_runtime/temporal scripts/run_temporal_dev_server.py scripts/run_temporal_docking_worker.py tests/task_runtime/test_temporal_workflow.py
git commit -m "feat(runtime): execute docking with temporal"
```

## Task 8: Add Temporal backend, duplicate-safe start, and reconciliation

**Files:**
- Create: `src/task_runtime/backends/temporal.py`
- Create: `src/task_runtime/temporal/reconcile.py`
- Modify: `src/task_runtime/runtime.py`
- Test: `tests/task_runtime/test_temporal_backend.py`
- Test: `tests/task_runtime/test_temporal_reconciliation.py`

- [ ] **Step 1: Write failing fallback and duplicate tests**

```python
# tests/task_runtime/test_temporal_backend.py
import pytest
from src.task_runtime.backends.temporal import TemporalStartOutcome


@pytest.mark.asyncio
async def test_fallback_only_when_temporal_did_not_accept(fake_runtime):
    fake_runtime.temporal.start_outcome = TemporalStartOutcome(
        accepted=False, workflow_id=None, error_code="TEMPORAL_START_FAILED"
    )
    await fake_runtime.submit_prepared_docking("task-1", "manifest.json")
    assert fake_runtime.local.submit_count == 1

    fake_runtime.temporal.start_outcome = TemporalStartOutcome(
        accepted=True,
        workflow_id="medchat-docking-task-2",
        error_code=None,
    )
    await fake_runtime.submit_prepared_docking("task-2", "manifest.json")
    assert fake_runtime.local.submit_count == 1
```

Also test that repeated task IDs return the existing workflow, cancellation writes `cancel_requested` but not `canceled`, and reconciliation never calls a scientific handler.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_backend.py tests\task_runtime\test_temporal_reconciliation.py -q -p no:cacheprovider
```

Expected: backend and reconciliation modules are missing.

- [ ] **Step 3: Implement lazy client and duplicate-safe workflow start**

Import `temporalio` only inside `connect()`. Cache only successful clients. Start `medchat-docking-{task_id}` with reject-duplicate semantics. If the start RPC is ambiguous, query that workflow ID before returning an outcome. Return `accepted=False` only after proving no workflow exists; otherwise mark accepted and prohibit local fallback.

Use:

```python
@dataclass(frozen=True)
class TemporalStartOutcome:
    accepted: bool
    workflow_id: str | None
    error_code: str | None
```

- [ ] **Step 4: Implement get, cancel, health, and projection repair**

`cancel()` requests `TaskStore.request_cancel()` and then cancels the workflow handle. It never writes the canceled terminal itself. `get()` reads SQLite first; stale or missing temporal projections query `task_snapshot` and `describe()`, then repair through CAS. A failed query returns the latest projection with one `projection_stale` warning.

`python -m src.task_runtime.temporal.reconcile` scans only nonterminal `backend='temporal'` rows. It may repair state/events but cannot start workflows, run Activities, or execute Vina.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_backend.py tests\task_runtime\test_temporal_reconciliation.py tests\task_runtime\test_backend_selector.py -q -p no:cacheprovider
git add -- src/task_runtime/backends/temporal.py src/task_runtime/runtime.py src/task_runtime/temporal/reconcile.py tests/task_runtime/test_temporal_backend.py tests/task_runtime/test_temporal_reconciliation.py
git commit -m "feat(runtime): route docking through temporal canary"
```

## Task 9: Add authenticated asynchronous docking APIs

**Files:**
- Modify: `src/web/routes/api_routes.py`
- Modify: `src/task_runtime/routes.py`
- Modify: `src/web/app.py`
- Test: `tests/test_temporal_docking_routes.py`
- Test: `tests/test_admin_auth_routes.py`
- Test: `tests/test_docking_agent_architecture.py`

- [ ] **Step 1: Write failing route tests**

```python
# tests/test_temporal_docking_routes.py
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.task_runtime.models import TaskRecord, TaskStatus
from src.web.routes.api_routes import setup_api_routes


class FakeTaskRuntime:
    async def submit_docking(self, **request):
        return TaskRecord(
            task_id="task-1",
            task_type="docking",
            status=TaskStatus.QUEUED,
            input={"input_manifest_path": "manifest.json"},
        )


def build_client(monkeypatch):
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", "unit-admin-token")
    app = FastAPI()
    setup_api_routes(app, docking_service=object(), task_runtime=FakeTaskRuntime())
    return TestClient(app), {"X-MedChat-Admin-Token": "unit-admin-token"}


def sample_files():
    root = Path(__file__).resolve().parents[1]
    return {
        "protein_file": ("MAGL.pdb", (root / "data/samples/MAGL_5zun.pdb").read_bytes()),
        "ligand_file": ("ligand.sdf", (root / "data/samples/5.sdf").read_bytes()),
    }


def test_async_submit_returns_202(monkeypatch):
    client, admin_headers = build_client(monkeypatch)
    response = client.post(
        "/api/docking/tasks",
        headers=admin_headers,
        files=sample_files(),
        data={
            "center_x": "5.99", "center_y": "3.01", "center_z": "17.345",
            "size_x": "20", "size_y": "20", "size_z": "20",
            "manual_center": "true",
        },
    )
    assert response.status_code == 202
    assert response.json()["data"]["task_id"]


def test_submit_requires_admin(monkeypatch):
    client, _ = build_client(monkeypatch)
    response = client.post(
        "/api/docking/tasks",
        files=sample_files(),
    )
    assert response.status_code in {401, 403}
```

Add tests for ordered events, bounded/redacted cancel reason, idempotent repeated submit, missing task 404, and unchanged synchronous `/api/docking/submit` output.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_docking_routes.py tests\test_admin_auth_routes.py -q -p no:cacheprovider
```

Expected: new routes are missing.

- [ ] **Step 3: Add async submission without changing sync behavior**

Extend `setup_api_routes(app, docking_service=None, task_runtime=None)`. Add admin-protected `POST /api/docking/tasks`, reuse `_read_upload_limited()` and `_validate_docking_limits()`, call `await runtime.submit_docking(protein_file=protein_file, ligand_file=ligand_file, smiles=smiles, docking_config=docking_config, idempotency_key=idempotency_key)`, and return HTTP 202 with the standard API envelope.

Do not route `/api/docking/submit` through Temporal and do not change its response schema.

- [ ] **Step 4: Add query, events, and cancellation**

Keep existing task list/get shape and add:

```text
GET  /api/tasks/{task_id}/events
POST /api/tasks/{task_id}/cancel
```

Both require `require_admin`. Terminal tasks return the existing record without a new event. Redact and length-limit cancellation reasons before persistence.

- [ ] **Step 5: Wire one lazy runtime instance**

Create one `get_task_runtime()` instance during route registration and inject it into docking and task routes. Temporal connection remains lazy; importing `src.web.app` in local mode must not require a running Temporal server.

- [ ] **Step 6: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_docking_routes.py tests\test_admin_auth_routes.py tests\test_docking_agent_architecture.py tests\test_phase2_phase3_routes.py -q -p no:cacheprovider
git add -- src/web/routes/api_routes.py src/task_runtime/routes.py src/web/app.py tests/test_temporal_docking_routes.py tests/test_admin_auth_routes.py tests/test_docking_agent_architecture.py
git commit -m "feat(web): expose durable docking task API"
```

## Task 10: Add health and acceptance release gates

**Files:**
- Create: `scripts/run_temporal_docking_acceptance.py`
- Create: `src/task_runtime/metrics.py`
- Modify: `scripts/health_check.py`
- Test: `tests/task_runtime/test_temporal_acceptance.py`
- Test: `tests/test_agent_platform_health_check.py`

- [ ] **Step 1: Write the failing report contract**

```python
# tests/task_runtime/test_temporal_acceptance.py
from scripts.run_temporal_docking_acceptance import summarize_runs


def test_report_requires_unique_execution_and_terminal_event():
    report = summarize_runs([{
        "status": "succeeded",
        "vina_attempts": 1,
        "terminal_event_count": 1,
        "pose_exists": True,
        "binding_energy": -7.0,
        "provenance_complete": True,
    }])
    assert report["status"] == "passed"
    assert report["duplicate_vina_count"] == 0


def test_metric_payload_contains_only_fixed_safe_dimensions():
    from src.task_runtime.metrics import TaskMetrics
    event = TaskMetrics().duration("task-1", "temporal", "docking", 125)
    assert event["name"] == "task_duration_ms"
    assert set(event["dimensions"]) == {"backend", "task_type"}
    assert "task-1" not in str(event)
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_acceptance.py tests\test_agent_platform_health_check.py -q -p no:cacheprovider
```

Expected: acceptance script and Temporal health checks are missing.

- [ ] **Step 3: Implement contract and real modes**

Support `--mode contract|real`, `--repeat`, and `--output`. Contract mode uses a controlled fake command to verify heartbeat, cancellation, startup-only fallback, completion reuse, one process attempt, and one terminal event. Real mode uses the repository MAGL receptor, ligand, and fixed box.

Fail the report on duplicate Vina, multiple terminal events, missing pose, nonnumeric energy, incomplete provenance, demo/fallback science, or credential-shaped data. Save only provider, versions, latency, hashes, relative artifact paths, warnings, and status.

- [ ] **Step 4: Extend health checks safely**

Implement these fixed names in `TaskMetrics`: `task_submit_latency_ms`, `task_backend_selected_total`, `temporal_workflow_start_failed_total`, `task_heartbeat_age_seconds`, `task_cancel_latency_ms`, `docking_process_attempt_total`, `docking_duplicate_execution_prevented_total`, `task_terminal_event_total`, `docking_artifact_validation_total`, and `task_duration_ms`. Emit values through structured task events using only backend and task type as dimensions. Check SDK import, strict configuration, Temporal reachability, namespace, logical queue, and freshness from `task_worker_heartbeats`. Report the address as `configured: true`, never the raw value. Never print environment-variable values or task input manifests.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_acceptance.py tests\test_agent_platform_health_check.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_docking_acceptance.py --mode contract --output outputs\agent_evaluation\temporal_docking_contract.json
git add -- scripts/run_temporal_docking_acceptance.py src/task_runtime/metrics.py scripts/health_check.py tests/task_runtime/test_temporal_acceptance.py tests/test_agent_platform_health_check.py
git commit -m "test(runtime): gate temporal docking canary"
```

Expected: contract status is passed and duplicate execution count is zero.

## Task 11: Run real Temporal/Vina validation and hand off

**Files:**
- Modify: `docs/PROJECT_STANDARDS.md`
- Modify: `docs/handoff/latest.md`
- Local-only: `outputs/agent_evaluation/temporal_docking_acceptance.json`

- [ ] **Step 1: Start a local Temporal development server**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_dev_server.py
```

Expected: namespace `default` is available on `127.0.0.1:7233`. The first run may require network access to download the SDK-compatible dev-server binary. If startup fails, record the dependency failure honestly and do not claim real Temporal validation.

- [ ] **Step 2: Start the dedicated worker**

```powershell
$env:MEDCHAT_TASK_BACKEND='temporal_canary'
$env:MEDCHAT_TEMPORAL_CANARY_PERCENT='100'
$env:MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY='1'
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_docking_worker.py
```

Expected: one worker polls `medchat-docking` without logging payloads or credentials.

- [ ] **Step 3: Run the real sample three times**

```powershell
$env:MEDCHAT_TASK_BACKEND='temporal_canary'
$env:MEDCHAT_TEMPORAL_CANARY_PERCENT='100'
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_docking_acceptance.py --mode real --repeat 3 --output outputs\agent_evaluation\temporal_docking_acceptance.json
```

Expected: 3/3 succeed; each has one Vina attempt, one terminal event, numeric energy, existing pose, artifact hash, and complete provenance. Missing dependencies remain failed or partial.

- [ ] **Step 4: Run the complete local-default regression**

```powershell
$env:MEDCHAT_TASK_BACKEND='local'
$env:MEDCHAT_TEMPORAL_CANARY_PERCENT='0'
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_task_runtime.py tests\task_runtime tests\test_docking_configuration.py tests\test_docking_agent_architecture.py tests\test_temporal_docking_routes.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract --output outputs\agent_evaluation\agent_acceptance_temporal_contract.json
```

Expected: all tests and compileall pass; Agent contract remains passed; local remains default.

- [ ] **Step 5: Run security and repository checks**

```powershell
git diff --check
git status --short
rg -n 'sk-[A-Za-z0-9_-]{20,}' src scripts tests docs requirements-agent-temporal.txt
```

Expected: no whitespace errors or secret values. Do not stage `outputs/`, `scratch/`, `temp_docking/`, SQLite files, or pose artifacts.

- [ ] **Step 6: Document exact evidence and commit**

Update standards with optional install/server/worker commands, local default, canary controls, rollback, and credential rules. Document the fixed production progression `0% -> 5% -> 10% -> 25%`, with at least 20 accepted tasks or one release observation window at each gate. Prepend handoff with branch, commits, exact test counts, real run results, honest failures/partials, and Stage 3B exclusions.

```powershell
git add -- docs/PROJECT_STANDARDS.md docs/handoff/latest.md
git commit -m "docs(agent): hand off temporal docking canary"
git status --short
```

Expected: clean worktree. Do not merge `main`; PR publication is a separate user-authorized action.
