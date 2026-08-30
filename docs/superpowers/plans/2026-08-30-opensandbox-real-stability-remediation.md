# OpenSandbox Real Runtime Stability Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the project-controlled causes behind the preserved real OpenSandbox run's 5 provider/readiness failures, 5 scientific-command timeouts, 2 invalid outputs, probe-timing failures, and approximately 50-second image-inspection delay without weakening scientific truth, cleanup, provenance, release identity, or promotion gates.

**Architecture:** Keep the corrected soak evaluator as the evidence judge. Remediate sanitized telemetry, bounded control-plane retries, image-readiness caching, phase budgets, atomic scientific output, and deterministic probes first. Then install the reviewed commit as a root-owned immutable runtime generation, atomically activate both Broker and Temporal worker through `/opt/medchat/current`, attest the release and QEMU identity, and permit exactly one new formal 30-job QEMU soak.

**Tech Stack:** Python 3.10+, asyncio, FastAPI over Unix-domain socket, Pydantic 2, OpenSandbox SDK 0.1.15, Docker, gVisor/runsc, AutoDock Vina 1.2.5, Meeko 0.7.1, pytest, POSIX shell, systemd, Ubuntu 24.04 QEMU/KVM.

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

- Evaluator correction is complete but is not evidence that runtime stability is proven.
- Retry only bounded control-plane create/readiness/reconciliation/cleanup operations. Never retry upload, the scientific command, Vina, Meeko, output validation, or an entire acceptance run.
- A recovered control-plane retry remains visible as `broker_retry_observed` and fails the clean-stability gate.
- Do not enlarge the 270-second scientific execution limit, the 300-second sandbox limit, or the 300-second p95 gate.
- Missing dependencies, malformed output, provenance mismatch, cleanup uncertainty, probe failure, release mismatch, virtualization mismatch, or leaked containers remain failures.
- Keep credentials in root-managed runtime environments only. Reports and tests contain no credential values, response bodies, raw exception text, sandbox IDs, host-user paths, or service environment values.
- Preserve the old QEMU report. A future formal run writes one new full-commit-specific report and is attempted exactly once.
- Stage only files listed by the active task; never use broad staging.

## File map

| Path | Responsibility |
|---|---|
| `src/sandbox_broker/telemetry.py` | Existing diagnostics schema plus bounded classified control-plane evidence. |
| `src/sandbox_broker/opensandbox_client.py` | SDK boundary; every sandbox still performs create/readiness/metadata checks. |
| `src/sandbox_broker/resilience.py` | Circuit-breaker permits and final-attempt accounting. |
| `src/sandbox_broker/service.py` | Single-consumer lifecycle, create retry allowlist, budgets, validation, publication, cancellation, and cleanup. |
| `src/sandbox_broker/config.py` | Fixed safety limits and internal phase budgets. |
| `deployment/opensandbox/run_docking.py` | Scientific command, exact failure schema, provenance, and atomic result publication. |
| `scripts/run_opensandbox_docking_acceptance.py` | Real container observer, process-local image-preflight cache, and lifecycle probes. |
| `scripts/run_opensandbox_stability_soak.py` | Corrected report gate and future formal release/QEMU attestation. |
| `scripts/validate_opensandbox_deployment.py` | Static/runtime release, unit, image, socket, network, and container checks. |
| `deployment/opensandbox/activate-runtime-generation.sh` | Future root-only staging, activation, verification, and rollback entry point. |
| `deployment/opensandbox/medchat-sandbox-broker.service` | Future Broker execution from `/opt/medchat/current`. |
| `deployment/medchat-temporal-worker.service` | Future worker execution from the same `/opt/medchat/current` generation. |
| `deployment/opensandbox/install.sh` | Future installation of units and the root-owned activation command. |
| `deployment/opensandbox/README.md` | Exact immutable deployment, formal run, evidence retention, and rollback procedure. |
| `tests/sandbox_broker/test_telemetry.py` | Diagnostics schema, bounds, and sanitization. |
| `tests/sandbox_broker/test_opensandbox_client.py` | SDK classification and readiness-cache key/invalidation tests. |
| `tests/sandbox_broker/test_resilience.py` | Breaker and final-attempt concurrency. |
| `tests/sandbox_broker/test_service.py` | Real `FakeSandboxClient` and `RecordingTelemetry` lifecycle contracts. |
| `tests/sandbox_broker/test_docking_image_contract.py` | Wrapper timeout, failure schema, provenance, filesystem, and atomic writes. |
| `tests/sandbox_broker/test_real_opensandbox_acceptance.py` | Observer and probe timing contracts. |
| `tests/sandbox_broker/test_stability_soak.py` | Report, lifecycle, attestation, failure retention, and non-clobber gates. |
| `tests/sandbox_broker/test_deployment_assets.py` | Units, activation/rollback, validator, and immutable-generation tests. |
| `docs/handoff/latest.md` | Final evidence and promotion or rollback decision. |

### Task 1: Lock the sanitized failure-evidence contract

**Files:**
- Modify: `tests/sandbox_broker/test_telemetry.py`
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `tests/sandbox_broker/test_stability_soak.py`
- Modify: `src/sandbox_broker/telemetry.py`
- Modify only if a test proves duplicate accounting: `src/sandbox_broker/service.py`

- [ ] **Step 1: Write failing diagnostics tests**

Use the current `BrokerTelemetryEvent`, `FailureClass`, `Phase`, and
`BrokerTelemetry.snapshot()` objects. Add a test that first calls
`update_runtime_gauges()`, emits classified events, records a control-plane
failure, and then asserts that the existing schema remains intact:

```python
snapshot = telemetry.snapshot()
assert snapshot["schema_version"] == 1
assert set(snapshot) == {
    "schema_version",
    "counters",
    "phase_latency_ms",
    "runtime",
    "recent_control_plane_failures",
    "recent_events",
}
assert snapshot["runtime"] == {
    "queue_depth": 0,
    "active_job_count": 1,
    "cleanup_task_count": 0,
    "isolated_task_count": 0,
    "breaker_state": "closed",
}
assert snapshot["recent_control_plane_failures"] == [
    {
        "operation": "create",
        "failure_class": "server_500",
        "count": 1,
    }
]
```

Retain assertions that no event or classified summary includes `exception`,
`response_body`, headers, endpoint values, or raw messages.

- [ ] **Step 2: Run the focused RED tests**

Run:

```powershell
python -m pytest tests/sandbox_broker/test_telemetry.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
```

Expected: the new `recent_control_plane_failures` assertion fails because the
current snapshot has only `schema_version`, `counters`, `phase_latency_ms`,
`runtime`, and `recent_events`. Existing tests remain green.

- [ ] **Step 3: Merge the bounded summary into the existing snapshot**

Keep the current lock, counters, latency, runtime, and recent-event copies.
Add the summary without replacing any existing field:

```python
def snapshot(self) -> dict[str, object]:
    with self._lock:
        counters = {
            "|".join(key): value
            for key, value in sorted(self._counters.items())
        }
        latency = {
            "|".join(key): list(values)
            for key, values in sorted(self._latencies.items())
        }
        runtime = dict(self._runtime)
        recent_events = [dict(item) for item in self._events]
        recent_control_plane_failures = [
            {
                "operation": operation,
                "failure_class": failure_class,
                "count": count,
            }
            for (kind, operation, failure_class), count
            in sorted(self._counters.items())
            if kind == "control_failure"
        ][-64:]
    return {
        "schema_version": 1,
        "counters": counters,
        "phase_latency_ms": latency,
        "runtime": runtime,
        "recent_control_plane_failures": recent_control_plane_failures,
        "recent_events": recent_events,
    }
```

Update the strict diagnostics validator and fixture dictionaries in
`test_stability_soak.py` for this additive field. Do not relax unknown-field,
number-bound, enum, or credential checks.

- [ ] **Step 4: Preserve the corrected 18/12 report truth**

Keep the existing corrected offline regression and assert:

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

```powershell
python -m pytest tests/sandbox_broker/test_telemetry.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
git add -- src/sandbox_broker/telemetry.py src/sandbox_broker/service.py tests/sandbox_broker/test_telemetry.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py
git commit -m "test: lock opensandbox runtime failure evidence"
```

Expected: PASS; retries and failures remain non-clean outcomes.

### Task 2: Restrict provider/readiness recovery to an explicit allowlist

**Files:**
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `tests/sandbox_broker/test_opensandbox_client.py`
- Modify: `tests/sandbox_broker/test_resilience.py`
- Modify: `src/sandbox_broker/service.py`
- Modify only if classification is proven wrong: `src/sandbox_broker/opensandbox_client.py`
- Modify only if final-attempt accounting is proven non-linearizable: `src/sandbox_broker/resilience.py`

- [ ] **Step 1: Add a genuinely RED retry-allowlist matrix**

Reuse `FakeSandboxClient`, `_service`, `_prepared`, `RecordingTelemetry`, and
`TrackingCircuitBreaker` from `test_service.py`. Parameterize retryable
`FailureClass.SERVER_500`, `PROXY_502`, `READINESS_TIMEOUT`, `CREATE_TIMEOUT`,
and `CONNECTION_FAILED`. Each fake raises `SandboxCreateError` on attempt 1,
returns zero from `destroy_by_job_id()`, and succeeds on attempt 2. Assert:

```python
assert terminal.status is BrokerJobStatus.SUCCEEDED
assert sdk.create_count == 2
assert sdk.run_count == 1
assert sdk.destroy_count == 1
```

Add a separate `RESOURCE_LIMIT` fake with the same reconciliation method and
assert the strict non-retry contract:

```python
assert terminal.status is BrokerJobStatus.FAILED
assert terminal.error_code == BrokerErrorCode.PROVISIONING_FAILED.value
assert sdk.create_count == 1
assert sdk.run_count == 0
assert terminal.cleanup_status == "succeeded"
assert "retry|create|attempted" not in telemetry.snapshot()["counters"]
```

Also cover `UNKNOWN_CONTROL_PLANE_FAILURE` as non-retryable unless a separate
reviewed contract explicitly classifies it. Across every case assert
`sdk.run_count <= 1`. This new `RESOURCE_LIMIT` test must fail against the
current `_create_with_retry()`, which retries every `SandboxCreateError` after a
successful reconciliation.

- [ ] **Step 2: Run RED and record whether existing retryable cases already pass**

```powershell
python -m pytest tests/sandbox_broker/test_service.py -q -p no:cacheprovider -k "create_retry or retryable_create or resource_limit"
```

Expected: at least the `RESOURCE_LIMIT` case fails with `create_count == 2`.
If any retryable case is already green, preserve it and implement only the
allowlist guard needed by the red cases.

- [ ] **Step 3: Implement the minimal allowlist guard**

Define beside `_BREAKER_QUALIFYING_PROVISIONING_FAILURES`:

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

In `_create_with_retry()`, record the first failure exactly once, perform
reconciliation when remote identity may exist, but enter attempt 2 only when
`_retryable_create_failure(first_class)` is true, reconciliation proves zero
live duplicates, cancellation is false, and the hard deadline has sufficient
remaining time. Do not change upload, command, validation, or whole-job retry
behavior.

- [ ] **Step 4: Verify breaker final-attempt accounting**

Run the existing recovered and terminal-failure tests plus new concurrency
tests. Only change `resilience.py` if a RED test proves one logical create
permit can be completed more than once or an older attempt can overwrite the
final classified result.

```powershell
python -m pytest tests/sandbox_broker/test_service.py::test_breaker_records_only_final_provisioning_result_and_create_retry tests/sandbox_broker/test_service.py::test_final_classified_create_failure_records_breaker_failure_once tests/sandbox_broker/test_resilience.py -q -p no:cacheprovider
```

- [ ] **Step 5: Run GREEN tests and commit**

```powershell
python -m pytest tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_resilience.py -q -p no:cacheprovider
git add -- src/sandbox_broker/opensandbox_client.py src/sandbox_broker/service.py src/sandbox_broker/resilience.py tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_resilience.py
git commit -m "fix: bound opensandbox readiness recovery"
```

Expected: control-plane create recovery is bounded; `RESOURCE_LIMIT` creates
once; every scientific command executes at most once.

### Task 3: Separate phase budgets and add a fail-closed image-readiness cache

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

- [ ] **Step 1: Write RED phase-budget tests without real sleeps**

Use an injected monotonic callable and fake command runner. Assert image
inspection latency is recorded separately, the scientific budget remains
270,000 ms, total sandbox lifetime remains 300 seconds, and clock rollback or a
negative duration returns the stable `process_failed` failure. Provisioning and
setup time must not be subtracted twice from the command timeout.

- [ ] **Step 2: Define the exact readiness-cache interface and RED matrix**

Add these future private types in `run_opensandbox_docking_acceptance.py`; tests
load that script with the existing `_module()` helper rather than duplicating
their behavior. This root/operator observer can obtain Docker daemon identity;
the unprivileged Broker remains unable to access the Docker socket:

```python
@dataclass(frozen=True)
class _ReadinessKey:
    image_digest: str
    endpoint_identity: str
    daemon_identity: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.image_digest) is None:
            raise ValueError("invalid readiness digest")
        if self.endpoint_identity not in {"127.0.0.1:8080", "localhost:8080"}:
            raise ValueError("invalid readiness endpoint")
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.daemon_identity) is None:
            raise ValueError("invalid daemon identity")


@dataclass(frozen=True)
class _ReadinessEntry:
    observed_at: float


class _ImageReadinessCache:
    def __init__(self, *, monotonic: Callable[[], float], ttl_seconds: float) -> None:
        self._monotonic = monotonic
        self._ttl_seconds = ttl_seconds
        self._entries: dict[_ReadinessKey, _ReadinessEntry] = {}

    def contains(self, key: _ReadinessKey) -> bool:
        if type(key) is not _ReadinessKey:
            return False
        entry = self._entries.get(key)
        if entry is None:
            return False
        try:
            now = self._monotonic()
            age = float(now) - entry.observed_at
        except BaseException:
            self._entries.pop(key, None)
            return False
        if not math.isfinite(float(now)) or not math.isfinite(age):
            self._entries.pop(key, None)
            return False
        if age < 0 or age > self._ttl_seconds:
            self._entries.pop(key, None)
            return False
        return True

    def record_success(self, key: _ReadinessKey) -> None:
        if type(key) is not _ReadinessKey:
            raise ValueError("invalid readiness key")
        observed_at = float(self._monotonic())
        if not math.isfinite(observed_at) or observed_at < 0:
            self.clear()
            raise ValueError("invalid readiness clock")
        self._entries.clear()
        self._entries[key] = _ReadinessEntry(observed_at=observed_at)

    def invalidate(self, key: _ReadinessKey) -> None:
        if type(key) is _ReadinessKey:
            self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
```

The tests must cover all of these cases:

1. Same lowercase image digest, approved loopback endpoint identity, daemon
   identity, and monotonic TTL produces a cache hit.
2. Changing digest, endpoint identity, or daemon identity produces a miss.
3. A create/readiness `SERVER_500`, `PROXY_502`, or `CONNECTION_FAILED`
   invalidates the matching entry before retry or failure is returned.
4. Daemon restart observation and deployment-runtime validation failure clear
   the process cache.
5. Clock rollback, non-finite time, negative age, or expired TTL fails closed as
   a miss and removes the entry.
6. Tags, response bodies, credentials, and container identifiers are never
   accepted as key material.
7. Two jobs sharing a valid image-level hit still invoke per-sandbox create and
   readiness/metadata verification twice. Add a test-only counting
   `FakeSandboxClient` around the existing `create()` contract and assert
   `create_count == 2`; add `image_preflight_count` to the fake observer command
   runner and assert it equals 1.
8. A cache hit never skips the existing `DockerSandboxObserver` checks for
   gVisor, CPU, memory, PID limit, immutable image, network, tool versions,
   cleanup, or provenance.

The RED tests first import these names before the acceptance script contains
them. The implementation in Step 4 uses the complete bodies shown above.

- [ ] **Step 3: Run RED tests**

```powershell
python -m pytest tests/sandbox_broker/test_models_config.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
```

Expected: cache imports or assertions fail, while existing tests remain green.

- [ ] **Step 4: Implement bounded budgets and cache semantics**

Keep public fixed limits unchanged. Add exact internal constants validated as
integers:

```python
_OBSERVATION_TIMEOUT_SECONDS = 60
_SETUP_TIMEOUT_SECONDS = 30
_CLEANUP_RESERVE_SECONDS = 20
_IMAGE_READINESS_CACHE_TTL_SECONDS = 300
```

Implement `_ImageReadinessCache.contains()` by sampling the injected clock once,
requiring a finite non-negative age no greater than TTL, and evicting on every
invalid sample or identity mismatch. `record_success()` stores only a validated
`_ReadinessKey` after successful image-level preflight. `invalidate()` removes
one exact key; `clear()` removes all entries. Keep it process-local and bounded
to one current immutable image identity.

Wire the cache only around the acceptance/runtime validator's repeated
image-level preflight. `DockerSandboxObserver` receives a shared
`_ImageReadinessCache` from the acceptance executor, and `start()` calls one
`_preflight_image()` before launching its per-container observer thread.
`_preflight_image()` obtains the safe pinned reference through the existing
`validate_opensandbox_deployment._runtime_image_reference()`, builds
`daemon_identity` with the fixed command `docker info --format '{{.ID}}'`, uses
the approved loopback OpenSandbox endpoint as `endpoint_identity`, and verifies
the digest through `docker image inspect --format '{{json .RepoDigests}}'`.
`StabilityExecutor` and the acceptance fault probes reuse one cache instance for
all observer instances in that process. A command failure never creates a cache
entry; a projected create/readiness 500, 502, connection failure, deployment
validation failure, or changed daemon identity clears it before the next
observer starts. Every
`OpenSandboxClient.create()` still calls the official SDK, waits for the new
sandbox, validates metadata, and returns a fresh `SandboxHandle`; the acceptance
observer still inspects every created container. Continue passing exactly
`config.execution_timeout_seconds` to the scientific command.

- [ ] **Step 5: Extend runtime preflight evidence**

`validate_opensandbox_deployment.py --runtime` must inspect the digest-pinned
image and daemon identity before service activation, return stable codes only,
and never print the environment. The operator documentation records bounded
image-preflight latency separately. A latency at or above 50 seconds blocks
promotion for investigation; it does not consume Vina's budget and is not
cached across daemon identity changes.

- [ ] **Step 6: Run GREEN tests and commit**

```powershell
python -m pytest tests/sandbox_broker/test_models_config.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
python scripts/validate_opensandbox_deployment.py --static
git add -- src/sandbox_broker/config.py src/sandbox_broker/service.py scripts/run_opensandbox_docking_acceptance.py scripts/validate_opensandbox_deployment.py deployment/opensandbox/README.md tests/sandbox_broker/test_models_config.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_deployment_assets.py
git commit -m "fix: isolate opensandbox runtime phase budgets"
```

Expected: all cache key, invalidation, clock, and per-container checks pass;
static validation exits zero without contacting real services.

### Task 4: Preserve invalid scientific output as a typed atomic failure

**Files:**
- Modify: `tests/sandbox_broker/test_docking_image_contract.py`
- Modify: `tests/sandbox_broker/test_service.py`
- Modify: `tests/sandbox_broker/test_validation_artifacts.py`
- Modify: `deployment/opensandbox/run_docking.py`
- Modify: `src/sandbox_broker/service.py`
- Modify: `src/sandbox_broker/validation.py`
- Modify: `src/sandbox_broker/artifacts.py`

- [ ] **Step 1: Write RED invalid-output and replacement tests**

Cover truncated JSON, duplicate keys, impossible phase/error pairs, absent pose,
pose/hash mismatch, non-finite energy, missing Vina/Meeko version, and a result
file replaced during validation. Every case must end with
`BrokerJobStatus.FAILED`, stable `SCIENTIFIC_OUTPUT_INVALID`, no manifest, and no
published pose.

Add a direct `_failure_result()` test requiring this exact strict schema:

```python
assert _failure_result("docking", "tool_timeout") == {
    "schema_version": 1,
    "status": "failed",
    "phase": "docking",
    "error_code": "tool_timeout",
    "warnings": [],
}
```

This matches `service.py`'s `_FAILURE_RESULT_FIELDS` exactly and must not include
stdout, stderr, exception text, energy, pose count, or provenance.

- [ ] **Step 2: Run RED tests**

```powershell
python -m pytest tests/sandbox_broker/test_docking_image_contract.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_validation_artifacts.py -q -p no:cacheprovider
```

Expected: any uncovered malformed/replacement case fails before implementation.

- [ ] **Step 3: Keep wrapper failure publication single-path and atomic**

Route every `DockingFailure` through the existing `_failure_result()` and
`_atomic_result()`. Preserve all five strict fields, fsync the temporary result,
replace `result.json` once, and fsync the output directory. If the deadline
prevents safe publication, exit non-zero; never synthesize energy, pose count,
or provenance.

- [ ] **Step 4: Keep Broker validation and artifact publication transactional**

In the existing validation/publication path, verify exact JSON, hashes, pose
bytes, energy/count consistency, Vina, Meeko, and immutable input identity before
publishing. A private temporary generation is renamed only after every file is
fsynced. Failure removes only that temporary generation and retains the typed
phase/error.

- [ ] **Step 5: Run GREEN tests and commit**

```powershell
python -m pytest tests/sandbox_broker/test_docking_image_contract.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_validation_artifacts.py -q -p no:cacheprovider
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

- [ ] **Step 1: Write RED tests using existing concrete test objects**

Do not introduce a fictional lifecycle object. Reuse:

- `threading.Event` for observer/cancellation barriers;
- synchronous `SandboxDockingRunner.execute()` or the fake runner's synchronous
  `execute()` method;
- `FakeSandboxClient.run_count` and `destroy_count` from `test_service.py` when
  asserting scientific and cleanup calls;
- `RecordingTelemetry.snapshot()["recent_events"]` for Broker lifecycle facts;
- `_ScriptedUnixServer.ready` and `peer_closed` only for existing UDS transport
  tests in `test_worker_runner.py`.

Create a small future helper in the acceptance script:

```python
def _wait_for_broker_event(
    diagnostics: object,
    *,
    trace_id: str,
    job_id: str,
    phase: str,
    outcome: str | None,
    deadline: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object] | None:
    waiter = threading.Event()
    while True:
        snapshot = diagnostics.snapshot()
        if not isinstance(snapshot, Mapping):
            return None
        telemetry = snapshot.get("telemetry", snapshot)
        if not isinstance(telemetry, Mapping):
            return None
        events = telemetry.get("recent_events")
        if not isinstance(events, list):
            return None
        for event in events:
            if (
                isinstance(event, Mapping)
                and event.get("trace_id") == trace_id
                and event.get("job_id") == job_id
                and event.get("phase") == phase
                and event.get("outcome") == outcome
            ):
                return dict(event)
        now = monotonic()
        if type(now) not in (int, float) or not math.isfinite(float(now)):
            return None
        remaining = deadline - float(now)
        if remaining <= 0:
            return None
        waiter.wait(min(0.05, remaining))
```

Its complete implementation repeatedly calls the existing diagnostics
`snapshot()`, validates `recent_events` as a list of mappings, filters exact
`trace_id`, `job_id`, `phase`, and `outcome`, sleeps with `Event.wait()` for at
most the remaining deadline, and returns `None` without extending the deadline.
It never changes the job result.

In an async service test, run the synchronous worker-side runner only through
`await asyncio.to_thread(runner.execute, payload, job_id=job_id,
cancel_event=cancel_event)`. The test owns a `threading.Event` named
`command_seen`; the diagnostics poller sets it only after observing
`command_started`. Then set cancellation and assert from real data:

```python
events = [
    event
    for event in telemetry.snapshot()["recent_events"]
    if event["job_id"] == terminal.job_id
]
assert sdk.run_count == 1
assert sdk.destroy_count == 1
assert any(event["phase"] == "command_started" for event in events)
assert any(event["phase"] == "cleanup_completed" for event in events)
assert events[-1]["phase"] == "job_terminal"
assert terminal.cleanup_status == "succeeded"
```

For the current observer-only cancellation regression, replace
`time.sleep(0.01)` with a test-owned `threading.Event` that proves the
cancellation thread is waiting before `_inspect_and_probe()` starts.

- [ ] **Step 2: Add timeout and queue RED contracts**

The timeout test observes exactly one failed `command_completed` event with
`failure_class == "command_timeout"`, one cleanup completion, one terminal
event, `FakeSandboxClient.run_count == 1`, and `destroy_count == 1`.

The queue test uses an `asyncio.Event` owned by the blocking
`FakeSandboxClient.run()` and polls `RecordingTelemetry.snapshot()["runtime"]`
until `active_job_count == 1` and `queue_depth == 8`. Only then submit the tenth
job and require exactly one `QUEUE_SATURATED` result. Release the fake in a
`finally` block and assert all accepted jobs terminate and clean up.

- [ ] **Step 3: Run RED tests**

```powershell
python -m pytest tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_worker_runner.py tests/sandbox_broker/test_service.py -q -p no:cacheprovider -k "cancellation or timeout or queue or broker_event"
```

Expected: new barrier/event assertions expose fixed-sleep or early-window
behavior. Existing synchronous runner calls remain outside the event loop unless
explicitly wrapped with `asyncio.to_thread`.

- [ ] **Step 4: Implement bounded event-driven windows**

Implement `_wait_for_broker_event()` exactly as defined in Step 1. Key every
observation to one `(trace_id, job_id)`, use one monotonic deadline per probe,
and never retry the scientific command. Add
`probe_observation_latency_ms` to probe evidence while retaining `latency_ms` as
Broker job latency; exclude observation latency from docking p50/p95. Missing,
negative, non-finite, or over-bound measurements fail report validation.

- [ ] **Step 5: Run GREEN tests and commit**

```powershell
python -m pytest tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_worker_runner.py tests/sandbox_broker/test_service.py -q -p no:cacheprovider
git add -- scripts/run_opensandbox_docking_acceptance.py scripts/run_opensandbox_stability_soak.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_worker_runner.py tests/sandbox_broker/test_service.py
git commit -m "test: harden opensandbox lifecycle probes"
```

Expected: probes prove complete cleanup, one scientific run, and zero leaked
containers without fixed sleeps.

### Task 6: Pass pre-deployment local, static, repetition, and review gates

**Files:**
- Modify only if a gate exposes an in-scope defect: files listed in Tasks 1-5

- [ ] **Step 1: Run the complete local gate**

```powershell
python -m pytest tests/sandbox_broker -q -p no:cacheprovider
python -m compileall -q src scripts deployment/opensandbox/run_docking.py
python scripts/validate_opensandbox_deployment.py --static
git diff --check
git status --short
```

Expected: all local tests pass or skip only explicit real Linux/runtime cases;
compileall and static validation exit zero.

- [ ] **Step 2: Repeat timing-sensitive groups**

```powershell
1..10 | ForEach-Object { python -m pytest tests/sandbox_broker/test_service.py -q -p no:cacheprovider --tb=short }
1..10 | ForEach-Object { python -m pytest tests/sandbox_broker/test_worker_runner.py tests/sandbox_broker/test_real_opensandbox_acceptance.py -q -p no:cacheprovider --tb=short }
```

Expected: 10/10 successful invocations for both commands. Any recurrence blocks
deployment and receives a new failing test in its owning task.

- [ ] **Step 3: Obtain independent spec and code-quality approval**

Reviewers receive the preserved 18/12 distribution, this plan, branch diff, and
test output. Approval requires no Critical or Important findings and explicit
confirmation that scientific commands are never retried and that retry,
timeout, invalid output, cleanup uncertainty, and probe failure remain
non-passing.

- [ ] **Step 4: Record the reviewed release commit**

```powershell
git status --short
git diff --cached --check
git rev-parse HEAD
```

Expected: the worktree is clean. The printed 40-character lowercase commit is
the only commit eligible for Task 7. Do not create an empty commit.

### Task 7: Implement immutable runtime generations, attest QEMU identity, and run the one formal soak

**Files:**
- Create: `deployment/opensandbox/activate-runtime-generation.sh`
- Modify: `deployment/opensandbox/install.sh`
- Modify: `deployment/opensandbox/medchat-sandbox-broker.service`
- Modify: `deployment/medchat-temporal-worker.service`
- Modify: `scripts/run_opensandbox_stability_soak.py`
- Modify: `scripts/validate_opensandbox_deployment.py`
- Modify: `deployment/opensandbox/README.md`
- Modify: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `tests/sandbox_broker/test_stability_soak.py`

- [ ] **Step 1: Write RED immutable-generation and unit tests**

Add constants in `test_deployment_assets.py` for the future activation script
and current units. Tests must require:

```python
assert "WorkingDirectory=/opt/medchat/current" in broker_unit
assert "WorkingDirectory=/opt/medchat/current" in worker_unit
assert "ExecStart=/opt/conda/envs/medchat/bin/python /opt/medchat/current/scripts/run_sandbox_broker.py" in broker_unit
assert "ExecStart=/opt/conda/envs/medchat/bin/python /opt/medchat/current/scripts/run_temporal_docking_worker.py" in worker_unit
assert "ReadOnlyPaths=/opt/medchat/current /opt/medchat/releases" in broker_unit
assert "ReadOnlyPaths=/opt/medchat/current /opt/medchat/releases" in worker_unit
```

Because the reviewed release must stay root-owned and non-writable, move worker
runtime state to `/var/lib/medchat-worker/scratch` and
`/var/lib/medchat-worker/temp_docking` through systemd bind mounts onto the
release's empty `scratch` and `temp_docking` directories. Tests require both
`BindPaths` entries and verify the activation script creates the backing
directories as `medchat:medchat` mode `0700`; no release file or directory may
be group/world writable. Remove the worker unit's dependency on
`medchat-temporal-worker-prepare.service`; generation activation now owns these
two runtime directories, while existing Temporal bundle preparation remains
available only to legacy deployments.

Add POSIX-only `--destdir` tests for
`activate-runtime-generation.sh --stage`, `--activate`, `--verify`, and
`--rollback`. Use a temporary Git repository with two commits. Assert:

- releases are `/opt/medchat/releases/$RELEASE_COMMIT` using the full 40-character commit;
- `.medchat-release-commit` contains exactly that commit and is root-owned mode `0444` in live mode;
- all release paths are non-writable after staging;
- `current` is an atomic symlink to one complete release, never a partial tree;
- `/var/lib/medchat-runtime-generation/previous` records the prior resolved release target with root-only mode;
- activation restarts Broker before worker and verifies both `MainPID` values;
- activation atomically installs the Broker and worker units from the staged
  release while both services are stopped, then runs `systemctl daemon-reload`;
- `/proc/$PID/cwd` for both services resolves to the same expected release;
- failed restart or cwd verification restores the old symlink and old services;
- `--rollback` uses the recorded previous target and verifies old PID cwd;
- bootstrap activation may start without a `current` symlink, but rollback is
  unavailable until a second verified generation records a previous target;
- malformed commit, symlinked source/release ancestors, dirty source, missing marker, owner/mode mismatch, or target outside `/opt/medchat/releases` fails closed.

- [ ] **Step 2: Write RED formal attestation and non-clobber tests**

Define these future helpers in `run_opensandbox_stability_soak.py` and test them
with injected command/path readers:

```python
def attest_formal_runtime(
    *,
    expected_release_commit: str,
    require_virt: str,
    project_root: Path,
    script_path: Path,
    marker_path: Path,
    command_runner: Callable[[Sequence[str]], object],
    resolver: Callable[[Path], Path],
    lstat_reader: Callable[[Path], os.stat_result],
    text_reader: Callable[[Path], str],
) -> str | None:
    if re.fullmatch(r"[0-9a-f]{40}", expected_release_commit) is None:
        return "release_commit_invalid"
    if require_virt != "qemu":
        return "virtualization_requirement_invalid"
    expected_root = Path("/opt/medchat/releases") / expected_release_commit
    expected_script = expected_root / "scripts/run_opensandbox_stability_soak.py"
    try:
        if resolver(project_root) != expected_root:
            return "release_path_mismatch"
        if resolver(script_path) != expected_script:
            return "release_script_mismatch"
        if resolver(marker_path) != expected_root / ".medchat-release-commit":
            return "release_marker_path_mismatch"
        metadata = lstat_reader(marker_path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o222
        ):
            return "release_marker_invalid"
        if text_reader(marker_path) != expected_release_commit + "\n":
            return "release_marker_mismatch"
    except (OSError, UnicodeError):
        return "release_attestation_unavailable"
    result = command_runner(("systemd-detect-virt", "--vm"))
    if not bool(getattr(result, "ok", False)):
        return "virtualization_unavailable"
    detected = getattr(result, "stdout", None)
    if type(detected) is not str or detected.strip() not in {"qemu", "kvm"}:
        return "virtualization_not_qemu"
    return None
```

The helper returns `None` only when all conditions pass; otherwise it returns
one stable code included in the report. Tests require:

- full lowercase 40-character commit only;
- `project_root.resolve()` equals `/opt/medchat/releases/$RELEASE_COMMIT`;
- `script_path.resolve()` equals that release's
  `scripts/run_opensandbox_stability_soak.py`;
- marker is a non-symlink regular file, root-owned, non-writable, and contains
  the exact commit;
- `systemd-detect-virt --vm` returns `qemu` or `kvm` when
  `--require-virt qemu` is supplied;
- `wsl`, `none`, empty, unknown, command failure, marker mismatch, path mismatch,
  or script outside the release fails closed.

Add parser tests requiring both `--expected-release-commit` and
`--require-virt qemu` for a formal enabled run. Retain the existing atomic
`O_EXCL`/hard-link report writer tests and add a CLI test proving an existing
full-commit report is not replaced.

- [ ] **Step 3: Run deployment/attestation RED tests**

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider -k "runtime_generation or release_attestation or require_virt or report_non_clobber"
```

Expected: tests fail because the activation script, `/opt/medchat/current`
units, and formal attestation flags do not yet exist.

- [ ] **Step 4: Implement the root-only generation mechanism**

`activate-runtime-generation.sh` supports exactly these commands:

```text
--stage SOURCE_REPOSITORY FULL_COMMIT
--activate FULL_COMMIT
--verify FULL_COMMIT
--rollback
--destdir ABSOLUTE_TEST_ROOT followed by one of the four commands above
```

In live mode require UID 0, root-owned non-writable script and source ancestors,
a clean source repository, and `git rev-parse` equality with the supplied full
commit. `--stage` archives exactly that commit into a private sibling under
`/opt/medchat/releases`, writes/fsyncs the marker, creates empty runtime mount
targets, recursively assigns root ownership and removes all write bits, fsyncs
parents, then renames once to `/opt/medchat/releases/$RELEASE_COMMIT`.

`--activate` verifies the staged tree, records the resolved old target in
`/var/lib/medchat-runtime-generation/previous`, atomically replaces
`/opt/medchat/current` through a temporary symlink plus `rename`, creates and
verifies the two `/var/lib/medchat-worker` runtime directories, atomically
installs the Broker and worker units from the staged release, runs daemon-reload,
restarts `medchat-sandbox-broker.service` then
`medchat-temporal-worker.service`, and verifies both service PID cwd values equal
the expected release. It also installs its already verified source as
`/usr/local/sbin/medchat-activate-runtime-generation`. On any failure it restores
the previous unit snapshots and symlink, daemon-reloads, restarts in dependency
order, verifies old PID cwd, and exits non-zero. `--rollback` performs the same
verified transition to the recorded previous target. Never edit an activated
release.

Update `install.sh` to install this script root-owned mode `0755` as
`/usr/local/sbin/medchat-activate-runtime-generation`, retain its existing
configuration-generation rollback, and make clear that immutable runtime
activation is authoritative for the live Broker and worker unit pair. The
existing Temporal bundle installer remains unchanged for legacy deployments and
must not overwrite units during a generation activation transaction.

- [ ] **Step 5: Implement release and virtualization attestation**

Add parser arguments without making disabled developer invocations fail during
argument parsing:

```python
parser.add_argument("--expected-release-commit")
parser.add_argument("--require-virt", choices=("qemu",))
```

After confirming the formal environment gate is enabled, require both values
and call `attest_formal_runtime()` before deployment validation or any job. Use
`systemd-detect-virt --vm` without a shell. Map failures to stable report codes;
do not print command output, marker content beyond the validated commit, or host
paths. Add `release_commit` to the strict report schema: it is the validated
commit for a formal run and `None` for a pre-attestation gate report. Update
`REPORT_FIELDS`, `gate_report()`, `build_report()`, report validation, fixture
dictionaries, and non-clobber tests together so filename and runtime identity
can be cross-checked without weakening unknown-field rejection.

- [ ] **Step 6: Run deployment/attestation GREEN tests and the post-Task-7 full gate**

These commands are mandatory after the Task 7 implementation; the Task 6 gate
does not cover newly added deployment or attestation tests:

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
python -m pytest tests/sandbox_broker -q -p no:cacheprovider
python -m compileall -q src scripts deployment/opensandbox/run_docking.py
python scripts/validate_opensandbox_deployment.py --static
git diff --check
```

Expected: all local tests pass or explicitly skip only real Linux/runtime cases;
static validation exits zero.

- [ ] **Step 7: Obtain a second independent review and commit Task 7**

Reviewers must specifically verify immutable staging, current/previous atomic
switches, PID cwd checks, root/read-only ownership, writable runtime bind mounts,
rollback on restart failure, QEMU/KVM-only attestation, marker/path mismatch,
and report non-clobber. No Critical or Important findings may remain.

```powershell
git add -- deployment/opensandbox/activate-runtime-generation.sh deployment/opensandbox/install.sh deployment/opensandbox/medchat-sandbox-broker.service deployment/medchat-temporal-worker.service scripts/run_opensandbox_stability_soak.py scripts/validate_opensandbox_deployment.py deployment/opensandbox/README.md tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_stability_soak.py
git diff --cached --check
git commit -m "feat: activate immutable opensandbox runtime generation"
git rev-parse HEAD
```

The resulting full commit becomes `RELEASE_COMMIT`; do not amend it after
staging on QEMU.

- [ ] **Step 8: Stage, activate, and verify one QEMU release**

Run on the snapshotted Ubuntu QEMU host from a root-owned clean checkout:

```bash
set -euo pipefail
SOURCE_REPOSITORY=/usr/local/src/medchat-release
RELEASE_COMMIT="$(git -C "$SOURCE_REPOSITORY" rev-parse HEAD)"
test "${#RELEASE_COMMIT}" -eq 40
BASELINE_COMMIT=79860ec175f0d3235dfd0597f248d7d52788fc76
if [ ! -L /opt/medchat/current ]; then
    sudo "$SOURCE_REPOSITORY/deployment/opensandbox/activate-runtime-generation.sh" --stage "$SOURCE_REPOSITORY" "$BASELINE_COMMIT"
    sudo "$SOURCE_REPOSITORY/deployment/opensandbox/activate-runtime-generation.sh" --activate "$BASELINE_COMMIT"
    sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$BASELINE_COMMIT"
fi
PREVIOUS_TARGET="$(sudo readlink -f /opt/medchat/current)"
PREVIOUS_COMMIT="$(sudo cat "$PREVIOUS_TARGET/.medchat-release-commit")"
sudo /usr/local/sbin/medchat-activate-runtime-generation --stage "$SOURCE_REPOSITORY" "$RELEASE_COMMIT"
sudo /usr/local/sbin/medchat-activate-runtime-generation --activate "$RELEASE_COMMIT"
sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$RELEASE_COMMIT"
cd /opt/medchat/current
sudo /opt/conda/envs/medchat/bin/python /opt/medchat/current/scripts/validate_opensandbox_deployment.py --runtime
```

Before any real job, independently verify both PID cwd values:

```bash
BROKER_PID="$(sudo systemctl show --property=MainPID --value medchat-sandbox-broker.service)"
WORKER_PID="$(sudo systemctl show --property=MainPID --value medchat-temporal-worker.service)"
test "$(sudo readlink -f "/proc/$BROKER_PID/cwd")" = "/opt/medchat/releases/$RELEASE_COMMIT"
test "$(sudo readlink -f "/proc/$WORKER_PID/cwd")" = "/opt/medchat/releases/$RELEASE_COMMIT"
VIRT="$(systemd-detect-virt --vm)"
test "$VIRT" = qemu || test "$VIRT" = kvm
test "$(sudo docker ps -q --filter label=opensandbox.io/id | wc -l)" -eq 0
```

Any failure runs the new rollback command and blocks all remaining steps:

```bash
sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$PREVIOUS_COMMIT"
```

- [ ] **Step 9: Run one real acceptance gate from the activated generation**

```bash
cd /opt/medchat/current
sudo env MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1 MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock /opt/conda/envs/medchat/bin/python -m pytest /opt/medchat/current/tests/sandbox_broker/test_real_opensandbox_acceptance.py -q -p no:cacheprovider
```

Expected: all enabled checks pass with gVisor, pinned digest, provenance,
cleanup, cancellation, timeout, and zero leaks. A skip or failure triggers the
new rollback command and blocks the formal soak.

- [ ] **Step 10: Run the only permitted formal 30-job QEMU soak and always hash its report**

The report directory is mutable runtime state outside the read-only release.
Use the already verified full commit in the filename. Run exactly once:

```bash
set -euo pipefail
cd /opt/medchat/current
RELEASE_COMMIT="$(cat /opt/medchat/current/.medchat-release-commit)"
REPORT="/var/lib/medchat-agent-evaluation/opensandbox_stability_soak-$RELEASE_COMMIT.json"
PREVIOUS_TARGET="$(sudo cat /var/lib/medchat-runtime-generation/previous)"
PREVIOUS_COMMIT="$(sudo cat "$PREVIOUS_TARGET/.medchat-release-commit")"
sudo install -d -o root -g root -m 0700 /var/lib/medchat-agent-evaluation
sudo test ! -e "$REPORT"
set +e
sudo env MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1 MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock /opt/conda/envs/medchat/bin/python /opt/medchat/current/scripts/run_opensandbox_stability_soak.py --repeat 30 --expected-release-commit "$RELEASE_COMMIT" --require-virt qemu --report "$REPORT"
SOAK_RC=$?
set -e
if ! sudo test -f "$REPORT"; then
    sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
    sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$PREVIOUS_COMMIT"
    exit 1
fi
if ! REPORT_SHA256="$(sudo sha256sum "$REPORT" | awk '{print $1}')"; then
    sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
    sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$PREVIOUS_COMMIT"
    exit 1
fi
test "${#REPORT_SHA256}" -eq 64
printf '%s\n' "report_sha256=$REPORT_SHA256"
if [ "$SOAK_RC" -ne 0 ]; then
    sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
    sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$PREVIOUS_COMMIT"
    exit "$SOAK_RC"
fi
```

The bounded `set +e` applies only to the soak process. A non-zero result still
requires the report to exist and be hashed before rollback and exit. Never rerun
or overwrite that report.

Expected passing gate: 30 measured runs, `pass_rate=1.0`, complete events,
successful cleanup, Vina/Meeko provenance, verified relative artifact/hash,
positive pose count, finite energy, gVisor, pinned image digest, all probes
passing, zero labelled containers, and p95 below 300 seconds.

### Task 8: Record evidence, rollback decision, and promotion status

**Files:**
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Record the immutable result**

Record the release commit, absolute host report path, SHA-256, total status,
pass/partial/fail counts, pass rate, p50/p95, phase p95 values, failure-code and
failure-class distributions, event completeness, retry evidence, cleanup,
Vina/Meeko versions, image digest, gVisor, artifact verification, probe outcomes,
final labelled-container count, and verified Broker/worker PID cwd generation.

State exactly:

```text
Evaluator correction does not imply runtime stability.
Promotion is allowed only when the one formal report passed every gate.
A failed formal report is retained and blocks promotion.
```

- [ ] **Step 2: Record and verify rollback after any failure**

Use only the new activation command; do not claim the prior README deletion
procedure can reactivate a generation:

```bash
sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$PREVIOUS_COMMIT"
BROKER_PID="$(sudo systemctl show --property=MainPID --value medchat-sandbox-broker.service)"
WORKER_PID="$(sudo systemctl show --property=MainPID --value medchat-temporal-worker.service)"
test "$(sudo readlink -f "/proc/$BROKER_PID/cwd")" = "$PREVIOUS_TARGET"
test "$(sudo readlink -f "/proc/$WORKER_PID/cwd")" = "$PREVIOUS_TARGET"
test "$(sudo docker ps -q --filter label=opensandbox.io/id | wc -l)" -eq 0
```

The pre-install QEMU snapshot is a heavier disaster-recovery option only when
the tested activation rollback itself cannot restore the previous generation.

- [ ] **Step 3: Run documentation and diff-added-line safety checks**

Scan the complete remediation plan and only newly added handoff lines, so
historical handoff paths do not create false positives:

```powershell
$planPath = 'docs/superpowers/plans/2026-08-30-opensandbox-real-stability-remediation.md'
$unsafePattern = @(
  ('s' + 'k-[A-Za-z0-9_-]{8,}'),
  ('C:\\' + 'Users\\[^\\\s]+'),
  ('/' + 'Users/' + '[^/\s]+'),
  ('OPEN_SANDBOX_' + 'API_KEY\s*=\s*[A-Za-z0-9_-]{16,}'),
  ('(?:API_' + 'KEY|TOKEN|SECRET)\s*=\s*["''][^$"'']{8,}')
) -join '|'
$unfinishedPattern = @(
  ('T' + 'BD'),
  ('T' + 'ODO'),
  ('implement' + ' later'),
  ('fill in' + ' details')
) -join '|'
$planText = Get-Content -Raw -LiteralPath $planPath
if ($planText -match $unsafePattern) { throw 'unsafe plan content' }
if ($planText -cmatch $unfinishedPattern) { throw 'unfinished plan content' }
$addedHandoff = git diff --unified=0 -- docs/handoff/latest.md |
  Where-Object { $_ -match '^\+(?!\+\+)' }
if (($addedHandoff -join "`n") -match $unsafePattern) {
  throw 'unsafe handoff addition'
}
git diff --check
git status --short
```

Also verify Markdown structure:

```powershell
$fences = (Select-String -Path $planPath -Pattern '^```').Count
if ($fences % 2 -ne 0) { throw 'unbalanced Markdown fences' }
$tasks = (Select-String -Path $planPath -Pattern '^### Task [1-8]:').Count
if ($tasks -ne 8) { throw 'task count mismatch' }
$steps = (Select-String -Path $planPath -Pattern '^- \[ \] \*\*Step ').Count
if ($steps -lt 32) { throw 'step count too small' }
```

- [ ] **Step 4: Commit the final handoff**

```powershell
git add -- docs/handoff/latest.md
git diff --cached --check
git commit -m "docs: hand off opensandbox runtime evidence"
git status --short
```

## Final completion criteria

Implementation is complete only when all statements are true:

- Tasks 1-5 local tests, compileall, and static validation pass.
- Timing-sensitive local groups pass 10 consecutive invocations.
- Task 7's new deployment and attestation tests run RED before implementation and GREEN afterward.
- The post-Task-7 full Broker, compileall, static, and independent-review gates pass.
- One full reviewed commit is installed under `/opt/medchat/releases/$RELEASE_COMMIT`; both services run from it through `/opt/medchat/current`.
- Formal attestation accepts only matching marker/path/commit and QEMU/KVM virtualization, rejecting WSL, bare metal, unknown virtualization, and all identity mismatches.
- Linux runtime validation and real acceptance pass on the activated immutable generation.
- Exactly one new formal 30-job QEMU soak is attempted; its report is always retained and hashed, even when the command exits non-zero.
- The report truthfully records latency, failures, events, cleanup, provenance, artifacts, probes, and zero-leak state.
- Any retry, scientific timeout, invalid output, cleanup uncertainty, dependency failure, probe failure, release mismatch, virtualization mismatch, or residual container prevents promotion.
- Rollback uses the newly tested generation activation command and verifies both previous-generation PID cwd values; QEMU snapshot restoration remains disaster recovery.
- `docs/handoff/latest.md` distinguishes evaluator correctness from runtime stability and records either promotion evidence or rollback evidence.
