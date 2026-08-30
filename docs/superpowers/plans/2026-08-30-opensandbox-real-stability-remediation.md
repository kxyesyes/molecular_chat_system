# OpenSandbox Real Runtime Stability Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the project-controlled causes behind the preserved real OpenSandbox soak's 5 provider/readiness failures, 5 tool timeouts, 2 invalid outputs, probe-timing failures, and approximately 50-second image-inspection delay without weakening scientific truth, cleanup, provenance, or promotion gates.

**Architecture:** Keep the corrected evaluator unchanged as the evidence judge and remediate the runtime in four bounded layers: sanitized Broker evidence, control-plane create/readiness handling, explicit phase budgets, and fail-closed scientific output publication. Harden cancellation, timeout, and queue probes separately, validate a new immutable release generation on Linux, and permit exactly one new 30-job QEMU soak only after every local, static, runtime, cleanup, and independent-review gate passes.

**Tech Stack:** Python 3.10+, asyncio, FastAPI over Unix-domain socket, Pydantic 2, OpenSandbox SDK, Docker, gVisor/runsc, AutoDock Vina 1.2.5, Meeko 0.7.1, pytest, systemd, QEMU Ubuntu 24.04.

---

## Evidence baseline and non-negotiable boundaries

The input is the corrected interpretation of the preserved real run at runtime
commit `79860ec175f0d3235dfd0597f248d7d52788fc76`:

```text
18 scientific successes
5 provider_error / server_500
5 tool_timeout / command_timeout
2 invalid_output / none
Docker image inspection observed at approximately 50 seconds
cancellation and timeout probes did not satisfy their acceptance windows
overall status remains failed
```

The immutable source report remains:

```text
/opt/medchat/molecular_chat_system/outputs/agent_evaluation/opensandbox_stability_soak-79860ec.json
sha256=c66e07c3e9ad4669a8ab703fa57e887d16b8d71b938ba8a0755725692ae3c67f
```

Every task must preserve these boundaries:

- The evaluator correction is complete and is not evidence that the runtime is stable.
- Retry only bounded control-plane create/readiness/reconciliation/cleanup operations. Never retry upload, the scientific command, Vina, Meeko, output validation, or an entire acceptance run.
- A recovered control-plane retry remains visible as `broker_retry_observed` and fails the clean-stability gate.
- Do not enlarge the 270-second scientific execution limit, the 300-second sandbox limit, or the p95 gate to make a failure pass.
- Missing dependencies, timeouts, malformed output, provenance mismatch, cleanup uncertainty, or leaked containers remain failures.
- Keep the OpenSandbox credential in the root-managed runtime environment only. Reports and tests contain no credentials, response bodies, raw exception text, sandbox IDs, host-specific user paths, or service environment values.
- Preserve the old QEMU report. A later formal run writes a new commit-specific file and runs exactly once.
- Stage only files listed by the active task; never use broad staging.

## File map

| Path | Responsibility |
|---|---|
| `src/sandbox_broker/telemetry.py` | Bounded, sanitized phase/failure/retry evidence and diagnostics schema. |
| `src/sandbox_broker/opensandbox_client.py` | SDK exception classification, readiness/create metadata reconciliation, and bounded cleanup. |
| `src/sandbox_broker/resilience.py` | Control-plane circuit-breaker permits and final-attempt accounting. |
| `src/sandbox_broker/service.py` | One-consumer lifecycle, create retry boundary, phase deadlines, validation, atomic publication, cancellation, and cleanup. |
| `src/sandbox_broker/config.py` | Fixed safety budgets; no operator-controlled relaxation. |
| `deployment/opensandbox/run_docking.py` | Scientific subprocess deadline, structured failure output, provenance, and atomic result publication. |
| `scripts/run_opensandbox_docking_acceptance.py` | Docker/gVisor observation and cancellation/timeout/security probes. |
| `scripts/run_opensandbox_stability_soak.py` | Existing corrected report gate; only schema-compatible evidence projection may change. |
| `scripts/validate_opensandbox_deployment.py` | Static/runtime release, image, service, socket, network, and container checks. |
| `deployment/opensandbox/README.md` | Exact generation deployment, one-run acceptance, rollback, and evidence retention procedure. |
| `tests/sandbox_broker/test_telemetry.py` | Diagnostics schema and sanitization tests. |
| `tests/sandbox_broker/test_opensandbox_client.py` | SDK 500/502/readiness fault injection and cleanup tests. |
| `tests/sandbox_broker/test_resilience.py` | Breaker and final-attempt concurrency tests. |
| `tests/sandbox_broker/test_service.py` | End-to-end Broker lifecycle, retry, deadline, cancellation, cleanup, and publication tests. |
| `tests/sandbox_broker/test_docking_image_contract.py` | Wrapper timeout, invalid-output, provenance, filesystem, and atomic-write tests. |
| `tests/sandbox_broker/test_real_opensandbox_acceptance.py` | Observer and probe timing contract tests. |
| `tests/sandbox_broker/test_stability_soak.py` | Report schema, failure preservation, latency, cleanup, and promotion-gate tests. |
| `tests/sandbox_broker/test_deployment_assets.py` | Linux service, release-generation, validator, and rollback command tests. |
| `docs/handoff/latest.md` | Final runtime evidence, rollback decision, and promotion status. |

### Task 1: Lock the sanitized failure-evidence contract

**Files:**
- Modify: `tests/sandbox_broker/test_telemetry.py`
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `tests/sandbox_broker/test_stability_soak.py`
- Modify: `src/sandbox_broker/telemetry.py`
- Modify: `src/sandbox_broker/service.py`

- [ ] **Step 1: Write failing diagnostics tests for phase, final failure class, and retry truth**

Add tests that create one Broker lifecycle for each preserved class and assert
that diagnostics contain only stable enums and bounded numbers:

```python
@pytest.mark.parametrize(
    ("failure_class", "phase"),
    [
        (FailureClass.SERVER_500, Phase.PROVISIONING_COMPLETED),
        (FailureClass.PROXY_502, Phase.PROVISIONING_COMPLETED),
        (FailureClass.READINESS_TIMEOUT, Phase.PROVISIONING_COMPLETED),
        (FailureClass.COMMAND_TIMEOUT, Phase.COMMAND_COMPLETED),
        (FailureClass.RESOURCE_LIMIT, Phase.VALIDATION_COMPLETED),
    ],
)
def test_diagnostics_preserve_sanitized_failure_phase(
    failure_class: FailureClass,
    phase: Phase,
) -> None:
    telemetry = BrokerTelemetry()
    telemetry.emit(
        BrokerTelemetryEvent(
            trace_id="trace-failed",
            job_id="f" * 32,
            phase=phase,
            attempt=1,
            outcome="failed",
            failure_class=failure_class,
            duration_ms=125,
            queue_depth=0,
            active_job_count=1,
            cleanup_status="not_started",
            image_digest="a" * 64,
        )
    )
    snapshot = telemetry.snapshot()
    event = snapshot["recent_events"][-1]
    assert event["phase"] == phase.value
    assert event["failure_class"] == failure_class.value
    assert "exception" not in event
    assert "response_body" not in event
```

Extend the existing
`test_breaker_records_only_final_provisioning_result_and_create_retry` regression
with the bounded diagnostics projection. It already creates a failed first
create and successful second create; add these exact assertions:

```python
snapshot = telemetry.snapshot()
assert sdk.create_count == 2
assert sdk.run_count == 1
assert snapshot["counters"]["retry|create|succeeded"] == 1
assert snapshot["counters"]["control_failure|create|connection_failed"] == 1
assert snapshot["recent_control_plane_failures"] == [
    {
        "operation": "create",
        "failure_class": "connection_failed",
        "count": 1,
    }
]
```

- [ ] **Step 2: Run the focused RED tests**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_telemetry.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
```

Expected: the new assertions fail because one or more classified attempt facts
are not retained across recovery; all pre-existing tests remain green.

- [ ] **Step 3: Add the minimal bounded evidence record**

Use the existing `BrokerTelemetryEvent`, `record_control_plane_failure()`, and
`record_retry()` paths. Do not add raw text fields. If final-attempt evidence is
missing, add only this fixed-schema projection to `snapshot()`:

```python
attempts = [
    {
        "operation": operation,
        "failure_class": failure_class,
        "count": count,
    }
    for (kind, operation, failure_class), count in sorted(self._counters.items())
    if kind == "control_failure"
]
return {
    "counters": counters,
    "phase_latency_ms": latency,
    "recent_control_plane_failures": attempts[-64:],
    "recent_events": recent_events,
}
```

Update `SandboxBrokerService._record_control_plane_failure()` only where needed
so each real SDK failure is recorded once before reconciliation. Keep retry
outcome counters separate; never replace a failure class with `none`.

- [ ] **Step 4: Lock report-schema truth without changing pass semantics**

Keep the existing
`test_corrected_report_shape_keeps_eighteen_successes_and_twelve_failures` and
add this exact assertion over its already constructed `report` and
`failed_runs` values:

```python
assert report["failure_type_distribution"] == {
    "command_timeout": 5,
    "none": 2,
    "server_500": 5,
}
assert Counter(
    code for run in failed_runs for code in run["failure_codes"]
) == {"provider_error": 5, "tool_timeout": 5, "invalid_output": 2}
assert report["status"] == "failed"
```

- [ ] **Step 5: Run GREEN tests and commit**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_telemetry.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
```

Expected: PASS; no test accepts a retry or failure as a clean success.

Commit:

```powershell
git add -- src/sandbox_broker/telemetry.py src/sandbox_broker/service.py tests/sandbox_broker/test_telemetry.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py
git commit -m "test: lock opensandbox runtime failure evidence"
```

### Task 2: Bound provider/readiness 500 and 502 recovery

**Files:**
- Modify: `tests/sandbox_broker/test_opensandbox_client.py`
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `tests/sandbox_broker/test_resilience.py`
- Modify: `src/sandbox_broker/opensandbox_client.py`
- Modify: `src/sandbox_broker/service.py`
- Modify: `src/sandbox_broker/resilience.py`

- [ ] **Step 1: Write fault-injection tests for the exact five-failure class**

Parametrize the existing
`test_final_classified_create_failure_records_breaker_failure_once` fake client
over create/readiness `server_500`, `proxy_502`, and `readiness_timeout`. Keep
the existing `_service`, `_prepared`, `RecordingTelemetry`, and
`TrackingCircuitBreaker` fixtures. Add this assertion matrix after each
scenario:

```python
assert sdk.create_count <= 2
assert sdk.run_count <= 1
assert len(sdk.live_ids) <= 1
assert terminal.cleanup_status == "succeeded"
assert breaker.failures == [failure_class]
```

Extend `test_each_stage_exception_and_base_exception_cleans_and_redacts`,
`test_execution_timeout_and_command_failure_are_stable_and_cleanup`, and
`test_transient_create_does_not_retry_without_confirmed_reconciliation` with
these exact assertions against their existing `sdk` and `terminal` values:

```python
assert terminal.status is BrokerJobStatus.FAILED
assert sdk.create_count == 1
assert sdk.run_count <= 1
assert sdk.destroy_count <= 1
```

- [ ] **Step 2: Run the provider/readiness RED tests**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_resilience.py -q -p no:cacheprovider
```

Expected: new fault-injection cases expose an unbounded, misclassified, or
unreconciled create/readiness path.

- [ ] **Step 3: Restrict retry eligibility to control-plane create/readiness**

Add one pure helper beside `_classified_failure()` in `service.py`:

```python
_RETRYABLE_CREATE_FAILURES = frozenset(
    {
        FailureClass.SERVER_500,
        FailureClass.PROXY_502,
        FailureClass.READINESS_TIMEOUT,
        FailureClass.CREATE_TIMEOUT,
        FailureClass.CONNECTION_FAILED,
    }
)


def _retryable_create_failure(value: FailureClass) -> bool:
    return type(value) is FailureClass and value in _RETRYABLE_CREATE_FAILURES
```

Use it in `_create_with_retry()` only after
`_reconcile_unknown_create(job_id, deadline)` proves that duplicate live state
is absent or has been cleaned. Preserve the existing one-retry maximum, hard
deadline, breaker permit, and `broker_retry_observed` evidence.

- [ ] **Step 4: Make final-attempt breaker accounting linearizable**

In `resilience.py`, keep one permit per logical create operation and complete it
once with the final classified result. Add concurrent tests proving an older
attempt cannot overwrite the newer terminal observation and only one half-open
probe can enter.

- [ ] **Step 5: Run GREEN tests and commit**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_resilience.py -q -p no:cacheprovider
```

Expected: PASS; control-plane recovery is bounded and every scientific command
still executes at most once.

Commit:

```powershell
git add -- src/sandbox_broker/opensandbox_client.py src/sandbox_broker/service.py src/sandbox_broker/resilience.py tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_resilience.py
git commit -m "fix: bound opensandbox readiness recovery"
```

### Task 3: Separate image readiness, setup, observation, and scientific budgets

**Files:**
- Modify: `tests/sandbox_broker/test_models_config.py`
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `tests/sandbox_broker/test_real_opensandbox_acceptance.py`
- Modify: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `src/sandbox_broker/config.py`
- Modify: `src/sandbox_broker/service.py`
- Modify: `scripts/run_opensandbox_docking_acceptance.py`
- Modify: `scripts/validate_opensandbox_deployment.py`
- Modify: `deployment/opensandbox/README.md`

- [ ] **Step 1: Write RED tests for a 50-second image-inspection observation**

Add this fake monotonic clock and use it with the existing observer command
fixture; do not sleep in tests:

```python
class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_slow_image_inspection_does_not_consume_scientific_budget() -> None:
    clock = FakeMonotonic()
    calls: list[tuple[str, ...]] = []

    def command_runner(argv: tuple[str, ...], *, timeout: float) -> _CommandResult:
        del timeout
        calls.append(argv)
        return _CommandResult(True, "")

    observer = DockerSandboxObserver(
        command_runner=command_runner,
        monotonic=clock,
    )
    observer.begin_image_inspection()
    clock.advance(50.0)
    observer.finish_image_inspection(valid_digest="a" * 64)
    budget = observer.budget_evidence()
    assert budget["image_inspection_ms"] == 50_000
    assert budget["scientific_budget_ms"] == 270_000
    assert "image_inspection_slow" in observer.failure_codes
```

Add service tests proving provisioning/setup time is not subtracted twice from
the command timeout, while the total sandbox lifetime still cannot exceed 300
seconds. Add a mutation test where clock rollback or a negative duration fails
closed as `process_failed`.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_models_config.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
```

Expected: new phase-budget evidence is absent and the slow-inspection contract
fails.

- [ ] **Step 3: Implement fixed internal phase budgets**

Keep public safety limits unchanged. Add fixed internal constants, validated as
exact integers, for observation and cleanup reserves:

```python
_OBSERVATION_TIMEOUT_SECONDS = 60
_SETUP_TIMEOUT_SECONDS = 30
_CLEANUP_RESERVE_SECONDS = 20
```

Record provisioning, setup/upload, command, validation, observation, and cleanup
durations independently. Continue passing exactly
`config.execution_timeout_seconds` to `OpenSandboxClient.run()`; do not add 50
seconds to that value and do not raise the 300-second sandbox lifetime.

- [ ] **Step 4: Add a digest-keyed readiness cache with fail-closed invalidation**

Cache only a successful immutable image-digest readiness preflight for the
current process. Use this exact identity:

```python
ReadinessKey = tuple[str, str]  # image_uri, lowercase sha256 digest
```

The cache entry is valid only while the OpenSandbox endpoint identity and digest
match. Clear it after any create/readiness 500, 502, connection failure, daemon
restart observation, digest mismatch, or runtime-validator failure. Never cache
a tag, failed observation, container ID, credential, or response body. A cache
hit may skip duplicate image readiness work but must not skip per-container
gVisor, network, limit, cleanup, or provenance checks.

- [ ] **Step 5: Update deployment preflight and operator evidence**

Extend `validate_opensandbox_deployment.py --runtime` to verify the digest-pinned
image once before service activation and emit only `image_readiness=passed` plus
bounded latency. Document that latency at or above 50 seconds blocks promotion
until investigated; it does not reduce Vina's budget or become a passing cache
entry after daemon restart.

- [ ] **Step 6: Run GREEN tests and commit**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_models_config.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
python scripts/validate_opensandbox_deployment.py --static
```

Expected: PASS; static validation returns exit code 0 without contacting Docker
or OpenSandbox.

Commit:

```powershell
git add -- src/sandbox_broker/config.py src/sandbox_broker/service.py scripts/run_opensandbox_docking_acceptance.py scripts/validate_opensandbox_deployment.py deployment/opensandbox/README.md tests/sandbox_broker/test_models_config.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_deployment_assets.py
git commit -m "fix: isolate opensandbox runtime phase budgets"
```

### Task 4: Preserve invalid scientific output as a typed atomic failure

**Files:**
- Modify: `tests/sandbox_broker/test_docking_image_contract.py`
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `tests/sandbox_broker/test_validation_artifacts.py`
- Modify: `deployment/opensandbox/run_docking.py`
- Modify: `src/sandbox_broker/service.py`
- Modify: `src/sandbox_broker/validation.py`
- Modify: `src/sandbox_broker/artifacts.py`

- [ ] **Step 1: Write RED tests for both preserved invalid-output runs**

Cover truncated JSON, duplicate keys, impossible phase/error pairs, absent pose,
pose/hash mismatch, non-finite energy, missing Vina/Meeko version, and a result
file replaced during validation:

```python
@pytest.mark.parametrize(
    "payload",
    [
        b'{"status":"passed"',
        b'{"status":"passed","status":"failed"}',
        b'{"status":"passed","best_energy":NaN}',
    ],
)
def test_invalid_result_never_publishes_scientific_artifact(
    tmp_path: Path,
    payload: bytes,
) -> None:
    class RawResultClient(FakeSandboxClient):
        async def read_text(self, handle: SandboxHandle, path: str) -> str:
            if path == "/workspace/output/result.json":
                return payload.decode("ascii")
            return await super().read_text(handle, path)

    async def scenario() -> tuple[BrokerJobView, SandboxBrokerService]:
        client = RawResultClient()
        service, _, config = _service(tmp_path, client)
        await service.start()
        submitted = await service.submit(_prepared(config), "idem-invalid-raw")
        terminal = await service.wait_terminal(submitted.job_id)
        await service.stop()
        return terminal, service

    terminal, service = asyncio.run(scenario())
    assert terminal.status is BrokerJobStatus.FAILED
    assert terminal.error_code == BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID.value
    with pytest.raises(KeyError):
        service.get_manifest(terminal.job_id)
```

Add a wrapper test asserting `_failure_result(phase, error_code)` keeps the
allowlisted phase/error code and never includes command output or exception
text. Add a service test asserting atomic publication leaves either the old
complete generation or the new complete generation, never a mixed manifest and
pose.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_docking_image_contract.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_validation_artifacts.py -q -p no:cacheprovider
```

Expected: one or more malformed/replacement cases expose missing typed phase
evidence or partial publication.

- [ ] **Step 3: Make wrapper failure publication single-path and atomic**

Route every `DockingFailure` through `_failure_result()` and `_atomic_result()`.
The payload remains an exact allowlist:

```python
{
    "schema_version": 1,
    "status": "failed",
    "phase": failure.phase,
    "error_code": failure.error_code,
}
```

Fsync the temporary result, replace `result.json` once, and fsync the output
directory. If the deadline prevents safe publication, exit non-zero and let the
Broker classify the missing result as `invalid_output`; never synthesize energy,
pose count, or provenance.

- [ ] **Step 4: Keep Broker validation and artifact publication transactional**

In `_validate_publish_downloaded()`, validate exact JSON, hashes, pose bytes,
energy/count consistency, Vina, Meeko, and immutable input identity before
calling `_publish_validated()`. Publish pose, result, and manifest into a private
temporary generation and rename only after every file is fsynced. On any error,
delete only that temporary generation and persist the typed phase/failure.

- [ ] **Step 5: Run GREEN tests and commit**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_docking_image_contract.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_validation_artifacts.py -q -p no:cacheprovider
```

Expected: PASS; every genuinely invalid output remains failed and no artifact is
partially published.

Commit:

```powershell
git add -- deployment/opensandbox/run_docking.py src/sandbox_broker/service.py src/sandbox_broker/validation.py src/sandbox_broker/artifacts.py tests/sandbox_broker/test_docking_image_contract.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_validation_artifacts.py
git commit -m "fix: preserve typed docking output failures"
```

### Task 5: Make cancellation, timeout, and queue probes deterministic observers

**Files:**
- Modify: `tests/sandbox_broker/test_real_opensandbox_acceptance.py`
- Modify: `tests/sandbox_broker/test_stability_soak.py`
- Modify: `tests/sandbox_broker/test_worker_runner.py`
- Modify: `scripts/run_opensandbox_docking_acceptance.py`
- Modify: `scripts/run_opensandbox_stability_soak.py`

- [ ] **Step 1: Write RED tests for explicit probe barriers**

Replace sleep-based assumptions in
`test_cancellation_waits_for_inspection_before_immediate_removal` and the
existing sacrificial fault-contract tests with Broker lifecycle barriers. Add
these exact terminal assertions after their existing observer and runner threads
join:

```python
assert lifecycle.command_started.wait(2.0)
cancel_event.set()
assert lifecycle.cleanup_completed.wait(5.0)
assert lifecycle.job_terminal.wait(1.0)
assert runner.scientific_run_count == 1
assert observer.assert_no_running()
```

Add corresponding timeout and queue tests. The timeout probe waits for
`command_started`, observes exactly one `command_completed=failed` with
`command_timeout`, then waits for cleanup and terminal. The queue probe waits
until one active plus eight queued jobs are visible before submitting the tenth
job and requires exactly one `queue_saturated` rejection.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_worker_runner.py -q -p no:cacheprovider
```

Expected: tests reveal dependence on wall-clock sleeps or an observation window
that can close before cleanup/terminal evidence arrives.

- [ ] **Step 3: Implement bounded event-driven probe windows**

Use Broker diagnostics deltas keyed by the probe's `(trace_id, job_id)`. Each
probe gets one monotonic deadline and waits for named events; polling does not
extend the deadline. The terminal checks are:

```python
assert scientific_run_count == 1
assert cleanup_status == "succeeded"
assert terminal_outcome in {"failed", "cancelled"}
assert running_labelled_container_count() == 0
```

An observation timeout returns its own stable probe failure code and does not
change the underlying job result. Do not issue a second cancellation, second
scientific command, or whole-suite retry.

- [ ] **Step 4: Prove observer latency is not scientific latency**

Add report assertions that `latency_ms` remains the Broker job latency while
`probe_observation_latency_ms` is separate, bounded, and excluded from docking
p50/p95. Missing either measurement fails report validation.

- [ ] **Step 5: Run GREEN tests and commit**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_worker_runner.py -q -p no:cacheprovider
```

Expected: PASS; probes prove cleanup, exactly one scientific run, and zero
leaked containers without relying on fixed sleeps.

Commit:

```powershell
git add -- scripts/run_opensandbox_docking_acceptance.py scripts/run_opensandbox_stability_soak.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_worker_runner.py
git commit -m "test: harden opensandbox lifecycle probes"
```

### Task 6: Pass local, static, Linux-runtime, and independent-review gates

**Files:**
- Modify only if a gate exposes an in-scope defect: files already listed in Tasks 1-5

- [ ] **Step 1: Run the complete local test gate**

Run:

```powershell
python -m pytest tests/sandbox_broker -q -p no:cacheprovider
python -m compileall -q src scripts deployment/opensandbox/run_docking.py
python scripts/validate_opensandbox_deployment.py --static
git diff --check
git status --short
```

Expected: all tests pass or explicitly skip only real Linux/runtime cases;
compileall and static validation exit 0; diff check is empty; status contains
only reviewed task files.

- [ ] **Step 2: Run repeated timing-sensitive local tests**

Run:

```powershell
1..10 | ForEach-Object { python -m pytest tests/sandbox_broker/test_service.py -q -p no:cacheprovider --tb=short }
1..10 | ForEach-Object { python -m pytest tests/sandbox_broker/test_worker_runner.py tests/sandbox_broker/test_real_opensandbox_acceptance.py -q -p no:cacheprovider --tb=short }
```

Expected: 10/10 successful invocations for both commands. Any recurrence blocks
Linux deployment and must be fixed in the owning task with a new failing test.

- [ ] **Step 3: Perform independent spec and code-quality review**

Give reviewers the preserved failure distribution, this plan, the branch diff,
and test output. Approval requires no Critical or Important findings, explicit
confirmation that scientific commands are not retried, and explicit confirmation
that retry, timeout, invalid output, cleanup uncertainty, and probe failure all
remain non-passing.

- [ ] **Step 4: Create the reviewed release commit**

Run:

```powershell
git status --short
git diff --cached --check
git commit -m "fix: harden opensandbox runtime stability"
git rev-parse HEAD
```

Expected: the release commit is immutable and the worktree is clean. If Tasks
1-5 already produced complete commits, this step records the existing reviewed
HEAD and does not create an empty commit.

### Task 7: Deploy one immutable Linux generation and run one formal QEMU soak

**Files:**
- Modify: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `deployment/opensandbox/README.md`
- Modify only if validation exposes an in-scope defect: `scripts/validate_opensandbox_deployment.py`

- [ ] **Step 1: Write deployment-generation and non-clobber tests**

Add static tests requiring a release directory derived from the full Git commit
and a report derived from its short commit:

```python
def test_runtime_remediation_uses_commit_specific_generation_and_report() -> None:
    readme = README.read_text(encoding="utf-8")
    assert 'RELEASE_COMMIT="$(git rev-parse HEAD)"' in readme
    assert 'medchat-release-${RELEASE_COMMIT:0:12}' in readme
    assert 'opensandbox_stability_soak-${RELEASE_COMMIT:0:8}.json' in readme
    assert "test -e \"$REPORT\" && exit 1" in readme
```

- [ ] **Step 2: Document the exact generation deployment**

Add these commands to `deployment/opensandbox/README.md`:

```bash
set -euo pipefail
cd /usr/local/src/medchat-release
RELEASE_COMMIT="$(git rev-parse HEAD)"
RELEASE_DIR="/usr/local/src/medchat-release-${RELEASE_COMMIT:0:12}"
sudo test ! -e "$RELEASE_DIR"
sudo install -d -o root -g medchat-sandbox -m 0750 "$RELEASE_DIR"
sudo git archive "$RELEASE_COMMIT" | sudo tar -x -C "$RELEASE_DIR"
sudo /opt/conda/envs/medchat/bin/python "$RELEASE_DIR/scripts/validate_opensandbox_deployment.py" --static
```

Activate the Broker and worker from that immutable directory through the
existing systemd generation mechanism. Do not edit a live release tree.

- [ ] **Step 3: Run Linux runtime validation before any real job**

Run on QEMU:

```bash
sudo /opt/conda/envs/medchat/bin/python scripts/validate_opensandbox_deployment.py --runtime
sudo -u medchat-sandbox curl --fail --silent --show-error --unix-socket /run/medchat-sandbox/broker.sock http://localhost/v1/diagnostics
test "$(sudo docker ps -q --filter label=opensandbox.io/id | wc -l)" -eq 0
```

Expected: runtime validation exits 0; diagnostics schema is valid and contains
no credentials; zero labelled containers exist. Any failure blocks the formal
soak and triggers rollback.

- [ ] **Step 4: Run one non-destructive real acceptance gate**

Run on QEMU:

```bash
sudo env MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1 MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock /opt/conda/envs/medchat/bin/python -m pytest tests/sandbox_broker/test_real_opensandbox_acceptance.py -q -p no:cacheprovider
```

Expected: all enabled real acceptance checks pass, including gVisor, digest,
network, limits, provenance, cleanup, cancellation, timeout, and zero-leak
checks. A skip or failure blocks the formal soak.

- [ ] **Step 5: Run the only permitted formal 30-job soak**

Run once on QEMU after Steps 1-4 and independent review are green:

```bash
set -euo pipefail
RELEASE_COMMIT="$(git rev-parse HEAD)"
REPORT="outputs/agent_evaluation/opensandbox_stability_soak-${RELEASE_COMMIT:0:8}.json"
test -e "$REPORT" && exit 1
sudo env MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1 MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock /opt/conda/envs/medchat/bin/python scripts/run_opensandbox_stability_soak.py --repeat 30 --report "$REPORT"
sudo sha256sum "$REPORT"
```

Expected passing gate: 30 measured runs; `pass_rate=1.0`; p50/p95 present and
p95 below 300 seconds; no runtime failure class; every run has complete events,
successful cleanup, Vina/Meeko provenance, verified relative artifact and hash,
positive pose count, finite energy, gVisor, and the pinned digest; all queue,
cancellation, timeout, idempotency, and security probes pass; zero labelled
containers remain.

If any condition fails, the report remains `failed`, is preserved under its
commit-specific path, and is not replaced by another formal run.

- [ ] **Step 6: Commit deployment documentation if it changed during review**

Run locally:

```powershell
git add -- deployment/opensandbox/README.md tests/sandbox_broker/test_deployment_assets.py scripts/validate_opensandbox_deployment.py
git diff --cached --check
git commit -m "docs: gate opensandbox runtime promotion"
```

Expected: only reviewed deployment files are committed.

### Task 8: Record evidence, rollback decision, and promotion gate

**Files:**
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Write the handoff from the immutable report**

Record the release commit, report repository-relative path, SHA-256, total
status, pass/partial/fail counts, pass rate, p50/p95, failure-code and
failure-class distributions, phase p95 values, event completeness, retry
evidence, cleanup distribution, Vina/Meeko versions, image digest, gVisor,
artifact verification, probe outcomes, and final labelled-container count.

State explicitly:

```text
Evaluator correction does not imply runtime stability.
Promotion is allowed only when the one formal report passed every gate.
A failed formal report is retained and blocks promotion.
```

- [ ] **Step 2: Record rollback when any gate failed**

Use the existing rollback procedure in `deployment/opensandbox/README.md` to
reactivate the previous reviewed Broker and worker generation. Verify:

```bash
sudo systemctl is-active medchat-opensandbox.service medchat-sandbox-broker.service medchat-temporal-worker.service
sudo stat -c '%U %G %a' /run/medchat-sandbox/broker.sock
test "$(sudo docker ps -q --filter label=opensandbox.io/id | wc -l)" -eq 0
```

Expected: all required services are active on the previous generation, the UDS
owner/mode matches policy, and no labelled container remains.

- [ ] **Step 3: Run documentation and repository safety checks**

Run:

```powershell
$secretPatterns = @('sk-' + '[a-z0-9]{12,}', 'OPEN_SANDBOX_' + 'API_KEY=', 'Authoriza' + 'tion:') -join '|'
$unfinishedMarkers = @('T' + 'BD', 'T' + 'ODO', 'implement' + ' later', 'fill in' + ' details') -join '|'
rg -n -i $secretPatterns docs/handoff/latest.md deployment/opensandbox/README.md
rg -n $unfinishedMarkers docs/handoff/latest.md deployment/opensandbox/README.md
git diff --check
git status --short
```

Expected: both searches return no matches; diff check succeeds; status contains
only the handoff file before commit.

- [ ] **Step 4: Commit the final handoff**

Run:

```powershell
git add -- docs/handoff/latest.md
git diff --cached --check
git commit -m "docs: hand off opensandbox runtime evidence"
git status --short
```

Expected: commit succeeds and the worktree is clean.

## Final completion criteria

Implementation is complete only when all of these statements are true:

- Local Broker tests, compileall, and static validation pass.
- Timing-sensitive local groups pass 10 consecutive invocations.
- Independent spec and code-quality reviews report no Critical or Important findings.
- Linux runtime validation and real acceptance pass on the immutable generation.
- Exactly one new formal 30-job QEMU soak was attempted and its report was never overwritten.
- The report truthfully records pass rate, p50/p95, failure distributions, event completeness, cleanup, provenance, artifacts, probe results, and zero-leak state.
- Any retry, scientific timeout, invalid output, cleanup uncertainty, probe failure, dependency failure, or residual container prevents promotion.
- `docs/handoff/latest.md` explicitly distinguishes evaluator correctness from runtime stability and records either the promotion evidence or the rollback evidence.
