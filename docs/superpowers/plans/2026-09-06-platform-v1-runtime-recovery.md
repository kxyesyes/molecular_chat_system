# Platform V1 Runtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve execution authority when new traffic switches to local mode and accurately clean up OpenSandbox jobs interrupted during provisioning.

**Architecture:** Persisted task/job ownership determines recovery actions. TaskRuntime lazily obtains a Temporal control backend for records already owned by Temporal; SandboxBroker compensates unknown provisioning outcomes through the existing stable job ID instead of treating missing sandbox IDs as proof of cleanup.

**Tech Stack:** Python 3.10, asyncio, SQLite TaskStore/BrokerStore, Temporal Python SDK boundary, OpenSandbox client boundary, pytest.

---

### Task 1: Route existing Temporal task control by persisted ownership

**Files:**
- Modify: `tests/task_runtime/test_local_backend.py`
- Modify: `src/task_runtime/runtime.py:52-115,350-359,570-575`

- [ ] **Step 1: Write the failing cancellation ownership test**

Create a task record with `backend="temporal"`, configure runtime for local new traffic, inject a Temporal backend factory/stub and a local backend spy, then call `cancel`. Assert Temporal receives the task ID and reason once and Local receives no cancellation.

- [ ] **Step 2: Write the failing unavailable-control test**

Make lazy Temporal control creation fail. Assert cancel raises a structured backend-unavailable error and does not return a record implying that cancellation reached Temporal.

- [ ] **Step 3: Verify RED**

Run the two tests. Expected: current runtime delegates to LocalTaskBackend when `temporal_backend` is absent.

- [ ] **Step 4: Add a lazy Temporal control factory**

Add an optional constructor dependency for a Temporal backend factory. The default factory constructs `TemporalTaskBackend` from the existing address, namespace, queue, and store. Protect lazy construction with an async lock and cache the backend. New-task selection remains unchanged.

- [ ] **Step 5: Route get and cancel by TaskRecord.backend**

For a Temporal record, call the existing or lazily created Temporal control backend. Convert import/connect/control failure to `TaskBackendStartError` or the repository's structured unavailable mapping. Never delegate a Temporal-owned record to LocalTaskBackend.

- [ ] **Step 6: Verify GREEN and close behavior**

Run the new tests plus TaskRuntime close/reconciliation tests. Confirm a lazily created Temporal backend is closed exactly once.

- [ ] **Step 7: Commit**

```powershell
git add -- src/task_runtime/runtime.py tests/task_runtime/test_local_backend.py
git commit -m "fix: preserve temporal task control ownership"
```

### Task 2: Compensate provisioning jobs without a persisted sandbox ID

**Files:**
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `src/sandbox_broker/service.py:2290-2413`

- [ ] **Step 1: Write the failing accepted-create crash recovery test**

Persist a job through `PROVISIONING` without attaching `sandbox_id`. Use a client whose `destroy_by_job_id` records calls and returns 1. Run `recover()` and assert one cleanup call, terminal failed status, and `cleanup_status="succeeded"` only after the call returns.

- [ ] **Step 2: Write the failing uncertain cleanup test**

Use the same persisted state but make `destroy_by_job_id` raise a bounded transport error. Assert recovery does not write `cleanup_status="succeeded"`; it records cleanup failure/pending and a retryable warning.

- [ ] **Step 3: Write the queued control case**

Persist a job that never left `QUEUED`. Assert recovery fails the interrupted local record without calling remote destroy-by-job-ID.

- [ ] **Step 4: Verify RED**

Run the three tests. Expected: the provisioning case is marked cleanup succeeded without a client call.

- [ ] **Step 5: Add job-ID recovery compensation**

Extract a bounded `_recover_destroy_by_job_id(job_id)` that calls the existing client method. For `PROVISIONING` with no sandbox ID, mark cleanup in progress, execute compensation, then record succeeded only when the client explicitly returns a non-negative integer confirming the completed lookup/delete operation. Exceptions record cleanup failed and preserve audit warnings.

- [ ] **Step 6: Preserve existing sandbox-ID recovery**

Keep `_recover_destroy(job_id, sandbox_id)` for records with a stored ID. Do not route queued records to remote cleanup. Ensure both paths emit one terminal observation.

- [ ] **Step 7: Verify GREEN and regression**

Run the new tests, existing recovery tests around `test_recovery_without_sandbox_fails_unavailable_without_sdk_cleanup`, and OpenSandbox client destroy-by-job-ID tests.

- [ ] **Step 8: Commit**

```powershell
git add -- src/sandbox_broker/service.py tests/sandbox_broker/test_service.py
git commit -m "fix: reconcile unknown sandbox provisioning outcomes"
```

### Task 3: Run durable recovery gates and publish the reviewed head

**Files:**
- Modify: `docs/handoff/platform-v1-baseline-pr.md`

- [ ] **Step 1: Run focused runtime tests**

Run TaskRuntime ownership tests, Temporal backend/reconciliation tests, SandboxBroker service tests, and OpenSandbox client cleanup tests. Expected: all deterministic tests pass.

- [ ] **Step 2: Run the full deterministic suite**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m pytest tests -q -p no:cacheprovider
```

Expected: no failure; skips remain explicit.

- [ ] **Step 3: Run compile, Node, contract, and safety gates**

Run compileall, every tracked Node test, contract acceptance, `git diff --check`, and the tracked credential scan. Do not write real credentials to reports.

- [ ] **Step 4: Update the PR verification record**

Record the new head SHA, exact local test results, strict acceptance rates, and GitHub CI status. Keep the existing real acceptance partials unchanged unless real acceptance is rerun.

- [ ] **Step 5: Commit and push explicitly**

```powershell
git add -- docs/handoff/platform-v1-baseline-pr.md
git commit -m "docs: update platform baseline merge gates"
git push origin codex/platform-v1-baseline
```

- [ ] **Step 6: Verify GitHub Actions**

Fetch workflow runs for the pushed head. If CI fails, inspect the failed job logs, add a new failing local contract when reproducible, and keep PR #2 in Draft until the new head passes.

- [ ] **Step 7: Update PR #2 description**

Replace stale validation totals with the observed totals and list any remaining partial/skipped dependencies. Do not mark the PR ready or merge it.
