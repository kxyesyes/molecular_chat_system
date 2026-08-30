# OpenSandbox Real Runtime Stability Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the project-controlled causes behind the preserved real OpenSandbox run's 5 provider/readiness failures, 5 scientific-command timeouts, 2 invalid outputs, probe-timing failures, and approximately 50-second image-inspection delay without weakening scientific truth, cleanup, provenance, release identity, or promotion gates.

**Architecture:** Keep the corrected soak evaluator as the evidence judge. Remediate sanitized telemetry, bounded control-plane retries, image-readiness caching, phase budgets, atomic scientific output, and deterministic probes first. Then a single tested root orchestrator acquires a whole-transaction non-blocking lock, stages the reviewed commit as a root-owned immutable runtime generation, and atomically activates and verifies both Broker and Temporal worker through `/opt/medchat/current`. The same orchestrator owns privileged validation, isolated non-root transient acceptance/soak, pre/post manifests, the permanent full-commit claim, evidence hashing, terminal-result fsync, and at most one rollback; operators never call activation primitives or formal workers directly.

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
| `scripts/run_opensandbox_docking_acceptance.py` | Real container observer, process-local image-preflight cache, lifecycle probes, unique diagnostics-delta identity discovery, and external formal output root. |
| `scripts/run_opensandbox_stability_soak.py` | Corrected scientific report gate and external formal output root; callable formally only by the root orchestrator. |
| `scripts/opensandbox_formal_runtime.py` | Canonical release manifest, release attestation, hygiene checks, and trusted external-output preparation shared by staging and the formal gate. |
| `scripts/run_opensandbox_formal_qemu_gate.py` | Sole root-only formal QEMU entry; holds the process-wide gate lock while it stages, activates, verifies, validates, runs non-root acceptance/soak, hashes evidence, publishes the terminal result, and owns at most one rollback. |
| `scripts/validate_opensandbox_deployment.py` | Static/runtime release, unit, image, socket, network, and container checks. |
| `deployment/opensandbox/activate-runtime-generation.sh` | Future root-only generation primitive invoked by the formal orchestrator for staging, activation, verification, and one mode-specific rollback; it is not an operator formal entry. |
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
| `tests/sandbox_broker/test_formal_qemu_gate.py` | Formal command construction and environment isolation, production telemetry keys, whole-gate locking/concurrency, commit claims, activation/verify failures, manifest ordering, rollback state machine, and atomic gate-result tests. |
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

The caller does not know the Broker `trace_id` or Broker `job_id` before
`SandboxDockingRunner.execute()` submits the request. Do not use the caller's
idempotency key as either identity. Add the following future helper and typed
failure in `run_opensandbox_docking_acceptance.py`; the soak script already
imports this module and reuses the same helper:

```python
@dataclass(frozen=True)
class _BrokerIdentity:
    trace_id: str
    job_id: str


class _BrokerObservationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_NO_ATTEMPT_PHASES = frozenset({"job_received", "queue_entered", "job_terminal"})
_ATTEMPT_PHASES = frozenset(
    {
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
    }
)


def _broker_event_key(
    event: Mapping[str, object],
) -> tuple[str, str, str, int | None] | None:
    trace_id = event.get("trace_id")
    job_id = event.get("job_id")
    phase = event.get("phase")
    attempt = event.get("attempt")
    if not all(type(value) is str and value for value in (trace_id, job_id, phase)):
        return None
    if phase in _NO_ATTEMPT_PHASES:
        if attempt is not None:
            return None
    elif phase not in _ATTEMPT_PHASES:
        return None
    elif type(attempt) is not int or attempt not in (1, 2):
        return None
    return trace_id, job_id, phase, attempt


def _broker_event_baseline(
    snapshot: object,
) -> frozenset[tuple[str, str, str, int | None]]:
    if not isinstance(snapshot, Mapping):
        raise _BrokerObservationError("diagnostics_invalid")
    telemetry = snapshot.get("telemetry", snapshot)
    if not isinstance(telemetry, Mapping):
        raise _BrokerObservationError("diagnostics_invalid")
    events = telemetry.get("recent_events")
    if not isinstance(events, list):
        raise _BrokerObservationError("diagnostics_invalid")
    keys: list[tuple[str, str, str, int | None]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise _BrokerObservationError("diagnostics_invalid")
        key = _broker_event_key(event)
        if key is None:
            raise _BrokerObservationError("diagnostics_invalid")
        keys.append(key)
    return frozenset(keys)


def _wait_for_unique_new_broker_event(
    diagnostics: object,
    *,
    baseline: frozenset[tuple[str, str, str, int | None]],
    phase: str,
    deadline: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> _BrokerIdentity:
    waiter = threading.Event()
    while True:
        snapshot = diagnostics.snapshot()
        if not isinstance(snapshot, Mapping):
            raise _BrokerObservationError("diagnostics_invalid")
        telemetry = snapshot.get("telemetry", snapshot)
        if not isinstance(telemetry, Mapping):
            raise _BrokerObservationError("diagnostics_invalid")
        events = telemetry.get("recent_events")
        if not isinstance(events, list):
            raise _BrokerObservationError("diagnostics_invalid")
        new_events = []
        for event in events:
            if not isinstance(event, Mapping):
                raise _BrokerObservationError("diagnostics_invalid")
            key = _broker_event_key(event)
            if key is None:
                raise _BrokerObservationError("diagnostics_invalid")
            if key not in baseline:
                new_events.append(event)
        identities = {
            _BrokerIdentity(str(event["trace_id"]), str(event["job_id"]))
            for event in new_events
        }
        if len(identities) > 1:
            raise _BrokerObservationError("broker_identity_ambiguous")
        if len(identities) == 1:
            identity = next(iter(identities))
            correlated = [
                event
                for event in new_events
                if event["trace_id"] == identity.trace_id
                and event["job_id"] == identity.job_id
            ]
            if any(event["phase"] == phase for event in correlated):
                return identity
            if any(event["phase"] == "job_terminal" for event in correlated):
                raise _BrokerObservationError("broker_terminal_before_command")
        now = monotonic()
        if type(now) not in (int, float) or not math.isfinite(float(now)):
            raise _BrokerObservationError("observation_clock_invalid")
        remaining = deadline - float(now)
        if remaining <= 0:
            raise _BrokerObservationError("broker_identity_timeout")
        waiter.wait(min(0.05, remaining))
```

Before starting the runner thread, validate one diagnostics snapshot and create
`baseline` from every valid `(trace_id, job_id, phase, attempt)` key in its
`recent_events`. Production phases `job_received`, `queue_entered`, and
`job_terminal` must carry `attempt=None`; any integer or Boolean attempt on those
phases is invalid. Only provisioning, upload, command, validation, and cleanup
`started`/`completed` phases are accepted, and each must carry an exact integer
attempt in `{1, 2}`. Suffix matching is forbidden: `forged_started`,
`forged_completed`, and every other unknown phase fail closed even when their
attempt looks valid. A missing, Boolean, zero, negative, or greater-than-two
attempt is invalid. The helper
then polls only the diagnostics delta. Zero new
identities at the single monotonic deadline is
`broker_identity_timeout`; more than one is
`broker_identity_ambiguous`; a terminal event before `command_started` is
`broker_terminal_before_command`. All three fail closed and never trigger a
second scientific command.

Write a complete deterministic unit fixture whose fake diagnostics client starts
with a real complete prior lifecycle containing `job_received(attempt=None)`,
  `queue_entered(attempt=None)`, all started/completed pairs with attempts in
  `{1, 2}`,
attempts, and `job_terminal(attempt=None)`. It then returns, in order: that
baseline, a delta containing one new identity's `job_received(attempt=None)`,
`queue_entered(attempt=None)`, and `provisioning_started(attempt=1)`, followed by
the same identity's completed provisioning and `command_started(attempt=1)`.
Start the synchronous fake runner in one `threading.Thread`.
Its `execute()` increments `run_count`, sets a `threading.Event` named
`run_entered`, and waits on the supplied cancellation event. The polling thread
calls `_wait_for_unique_new_broker_event(...)`; only after it returns the unique
identity does the test set cancellation. Join both threads with bounded waits
and assert `run_count == 1`.

Use this explicit RED/GREEN parameter matrix for `_broker_event_key`; RED must
fail against the suffix-based implementation, and GREEN must pass only after the
closed production phase sets and exact attempt bounds are implemented:

```python
@pytest.mark.parametrize(
    ("phase", "attempt", "accepted"),
    [
        ("job_received", None, True),
        ("queue_entered", None, True),
        ("job_terminal", None, True),
        ("provisioning_started", 1, True),
        ("provisioning_completed", 2, True),
        ("upload_started", 1, True),
        ("upload_completed", 2, True),
        ("command_started", 1, True),
        ("command_completed", 2, True),
        ("validation_started", 1, True),
        ("validation_completed", 2, True),
        ("cleanup_started", 1, True),
        ("cleanup_completed", 2, True),
        ("forged_started", 1, False),
        ("forged_completed", 2, False),
        ("command_started", None, False),
        ("command_started", True, False),
        ("command_started", 0, False),
        ("command_started", 3, False),
        ("job_terminal", 1, False),
        ("job_received", False, False),
    ],
)
def test_broker_event_key_matches_production_phase_schema(
    phase: str, attempt: object, accepted: bool
) -> None:
    event = {"trace_id": "trace-1", "job_id": "job-1", "phase": phase,
             "attempt": attempt}
    assert (_broker_event_key(event) is not None) is accepted
```

Also add observer-level negative fixtures for a started phase with
`attempt=None`, an unknown `forged_started` and `forged_completed` phase, a
terminal phase with an integer attempt, attempts `0` and `3`, zero identity until
deadline, two new identities in one delta, and `job_terminal(attempt=None)`
before `command_started`; malformed phases or attempts raise
`diagnostics_invalid`, while the observation failures raise the exact typed
codes above. Each fixture sets cancellation in `finally`, joins the runner
thread, and still asserts `run_count == 1`. Use a `FakeSandboxClient` plus
`RecordingTelemetry` service test for end-to-end cancellation evidence. Run the
synchronous worker-side runner only through
`await asyncio.to_thread(runner.execute, payload, job_id=caller_job_id,
cancel_event=cancel_event)` and discover the Broker identity from telemetry,
not from `caller_job_id`. Then assert from concrete data:

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

The real UDS acceptance probe uses the same helper against
`BrokerDiagnosticsClient`, but the returned identity remains process-local. It
is used only to correlate lifecycle evidence and is never emitted in the report,
logs, filenames, or error text.

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

Implement `_wait_for_unique_new_broker_event()` exactly as defined in Step 1.
Capture the baseline before starting `runner.execute()`, key observations to the
single newly discovered `(trace_id, job_id)`, use one monotonic deadline per
probe, and never retry the scientific command. Add
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
- Create: `scripts/opensandbox_formal_runtime.py`
- Create: `scripts/run_opensandbox_formal_qemu_gate.py`
- Modify: `deployment/opensandbox/install.sh`
- Modify: `deployment/opensandbox/medchat-sandbox-broker.service`
- Modify: `deployment/medchat-temporal-worker.service`
- Modify: `scripts/run_opensandbox_docking_acceptance.py`
- Modify: `scripts/run_opensandbox_stability_soak.py`
- Modify: `scripts/validate_opensandbox_deployment.py`
- Modify: `deployment/opensandbox/README.md`
- Modify: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `tests/sandbox_broker/test_real_opensandbox_acceptance.py`
- Modify: `tests/sandbox_broker/test_stability_soak.py`
- Create: `tests/sandbox_broker/test_formal_qemu_gate.py`

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
`--rollback`, plus first-migration tests for `--capture-legacy` and
`--rollback-legacy`. Use a temporary Git repository with two new-layout commits
for generation switching. Separately load the exact historical unit fixtures
with these commands so the test proves the old layout is migratable without
pretending the old commit is a generation:

```powershell
git show 79860ec175f0d3235dfd0597f248d7d52788fc76:deployment/opensandbox/medchat-sandbox-broker.service
git show 79860ec175f0d3235dfd0597f248d7d52788fc76:deployment/medchat-temporal-worker.service
```

Assert:

- releases are `/opt/medchat/releases/$RELEASE_COMMIT` using the full 40-character commit;
- `.medchat-release-commit` contains exactly that commit and is root-owned mode `0444` in live mode;
- all release paths are non-writable after staging;
- `current` is an atomic symlink to one complete release, never a partial tree;
- `/var/lib/medchat-runtime-generation/previous` records the prior resolved release target with root-only mode;
- activation restarts Broker before worker and verifies both `MainPID` values;
- activation atomically installs the Broker and worker units from the staged
  release while both services are stopped, then runs `systemctl daemon-reload`;
- `/proc/$PID/cwd` for both services resolves to the same expected release;
- an activation or cwd-verification failure is surfaced to the still-locked
  formal orchestrator, which invokes the one mode-correct `rollback_once()`;
  the activation primitive must not perform a hidden second rollback;
- `--stage` completes before any legacy capture or activation transaction and
  never mutates live runtime state;
- `--stage` is idempotent for an existing
  `/opt/medchat/releases/$RELEASE_COMMIT` only when a read-only inspection proves
  root owner/mode, the full commit marker, canonical manifest, source commit,
  and absence of symlinks, devices, sockets, FIFOs, caches, and bytecode all
  match exactly; it performs zero writes on this path;
- any difference in an existing release returns `release_stage_conflict` with
  zero filesystem, service, transaction, claim, result, or rollback mutation;
- `--rollback` uses only the current transaction's fsynced `pending_previous`,
  never a possibly stale long-term `previous`, and verifies restored PID cwd;
- generation-to-generation activation requires a valid `current` symlink and
  creates a recoverable generation transaction; a first migration without
  `current` instead requires a
  verified `legacy.json` captured by `--capture-legacy`;
- the historical `79860ec` unit fixtures retain
  `WorkingDirectory=/opt/medchat/molecular_chat_system`, are captured byte for
  byte with SHA-256, and are restored byte for byte by `--rollback-legacy`;
- first migration never stages or activates `79860ec` as a new generation and
  succeeds without a `previous` symlink;
- legacy rollback restores the prior `current` state, unit enabled/active state,
  unit hashes, and both active PID cwd values to the captured legacy root;
- legacy tests inject failures after the first unit replacement, after the
  symlink replacement, during restart, and during verification; every case has
  a durable `activating` record and restores successfully from snapshots;
- generation tests inject failure both before and after long-term `previous`
  publication; both restore this transaction's `pending_previous` and the prior
  long-term metadata;
- a process crash leaving either transaction in `activating` or `activated`,
  while the same commit has neither a claim nor a terminal result, is recovered
  before a new stage, acceptance, or claim; the recovery invocation writes an
  atomic fsynced recovery-only gate result and stops;
- crashes after installed verification, during the privileged validator, and
  during acceptance all leave `activated` transactions that are recovered once
  on the next invocation; that recovery invocation does not stage, validate,
  run acceptance, allocate a run ID, or create a claim;
- a failed rollback atomically marks the transaction `rollback_failed`, writes
  and fsyncs the root-only poison marker
  `/var/lib/medchat-runtime-generation/snapshot-required.json`, stops and
  disables both services, and makes every later gate return
  `qemu_snapshot_recovery_required` with zero mutation and zero rollback;
- malformed commit, symlinked source/release ancestors, dirty source, missing marker, owner/mode mismatch, or target outside `/opt/medchat/releases` fails closed.

- [ ] **Step 2: Write RED formal attestation, external-output, and non-clobber tests**

Define the canonical tree function in the new
`scripts/opensandbox_formal_runtime.py`. It walks with `lstat`, rejects every
symlink, device, socket, and FIFO, sorts relative POSIX paths by encoded bytes,
and hashes newline-delimited records containing entry type, mode, relative path,
size, and file SHA-256. Exclude only `.git` path components, cache directory
components `__pycache__`, `.pytest_cache`, and `.mypy_cache`, files ending in
`.pyc`, `.medchat-release-commit`, and
`.medchat-release-manifest.sha256`. The two excluded release metadata files are
validated separately. No runtime output directory is excluded because every
runtime output must be outside the release.

```python
class ReleaseManifestError(RuntimeError):
    pass


_CACHE_COMPONENTS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache"})
_RELEASE_METADATA = frozenset({
    ".medchat-release-commit",
    ".medchat-release-manifest.sha256",
})


def _is_excluded(relative: Path) -> bool:
    return (
        any(part in _CACHE_COMPONENTS for part in relative.parts)
        or relative.name.endswith(".pyc")
        or relative.as_posix() in _RELEASE_METADATA
    )


def canonical_release_manifest(root: Path) -> str:
    records: list[bytes] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode("utf-8")):
        relative = path.relative_to(root)
        if _is_excluded(relative):
            continue
        metadata = path.lstat()
        name = relative.as_posix()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            record = f"d {mode:04o} {name}\n".encode("utf-8")
        elif stat.S_ISREG(metadata.st_mode):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            record = f"f {mode:04o} {name} {metadata.st_size} {digest}\n".encode("utf-8")
        else:
            raise ReleaseManifestError("release_entry_invalid")
        records.append(record)
    return hashlib.sha256(b"".join(records)).hexdigest()
```

Because cache paths are intentionally outside the canonical digest, add
`validate_release_hygiene(root)` that walks with `lstat` and returns
`release_cache_present` if `.git`, a cache directory, or a `.pyc` exists, and
`release_entry_invalid` for a symlink or special file. Staging and every formal
pre/post attestation require both a matching digest and clean hygiene. Thus a
runtime-created `.pyc` cannot hide behind the manifest exclusion.

```python
def validate_release_hygiene(root: Path) -> str | None:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in _CACHE_COMPONENTS for part in relative.parts) or relative.name.endswith(".pyc"):
            return "release_cache_present"
        metadata = path.lstat()
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            return "release_entry_invalid"
    return None
```

Define `attest_formal_runtime()` in the same shared module and import it from the
formal gate. The acceptance and soak runners retain their strict report and
artifact contracts, while the one root-owned gate owns release/QEMU attestation
and pre/post manifest ordering. Do not import the soak module from the acceptance
module; the soak already imports acceptance helpers, so that would create a
circular dependency. Test the shared helper with injected command/path readers:

```python
def attest_formal_runtime(
    *,
    expected_release_commit: str,
    require_virt: str,
    project_root: Path,
    script_path: Path,
    expected_script_relative: PurePosixPath,
    marker_path: Path,
    manifest_path: Path,
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
    allowed_scripts = {
        PurePosixPath("scripts/run_opensandbox_formal_qemu_gate.py"),
    }
    if expected_script_relative not in allowed_scripts:
        return "release_script_invalid"
    expected_script = expected_root.joinpath(*expected_script_relative.parts)
    try:
        if resolver(project_root) != expected_root:
            return "release_path_mismatch"
        if resolver(script_path) != expected_script:
            return "release_script_mismatch"
        if resolver(marker_path) != expected_root / ".medchat-release-commit":
            return "release_marker_path_mismatch"
        if resolver(manifest_path) != expected_root / ".medchat-release-manifest.sha256":
            return "release_manifest_path_mismatch"
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
        manifest_metadata = lstat_reader(manifest_path)
        if (
            not stat.S_ISREG(manifest_metadata.st_mode)
            or stat.S_ISLNK(manifest_metadata.st_mode)
            or manifest_metadata.st_uid != 0
            or stat.S_IMODE(manifest_metadata.st_mode) & 0o222
        ):
            return "release_manifest_metadata_invalid"
        expected_manifest = text_reader(manifest_path).strip()
        if re.fullmatch(r"[0-9a-f]{64}", expected_manifest) is None:
            return "release_manifest_invalid"
        if canonical_release_manifest(expected_root) != expected_manifest:
            return "release_manifest_mismatch"
        hygiene_failure = validate_release_hygiene(expected_root)
        if hygiene_failure is not None:
            return hygiene_failure
    except ReleaseManifestError:
        return "release_entry_invalid"
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
- `script_path.resolve()` equals the allowlisted formal gate script named by
  `expected_script_relative`; acceptance, soak, and arbitrary direct entry paths
  fail formal attestation;
- marker is a non-symlink regular file, root-owned, non-writable, and contains
  the exact commit;
- manifest file is a non-symlink regular file, root-owned, non-writable, contains
  one lowercase SHA-256, and matches a fresh canonical tree calculation;
- mutation, creation, deletion, or mode change after staging produces
  `release_manifest_mismatch`; `.pyc`/cache creation produces
  `release_cache_present`; symlink insertion produces `release_entry_invalid`,
  all before any scientific job;
- `systemd-detect-virt --vm` returns `qemu` or `kvm` when
  `--require-virt qemu` is supplied;
- `wsl`, `none`, empty, unknown, command failure, marker mismatch, path mismatch,
  or script outside the release fails closed.

Keep parser tests requiring explicit `--output-root` whenever the acceptance or
soak environment gate is enabled. Those lower-level scripts reject effective
UID zero and require `sys.dont_write_bytecode is True`; invocation without `-B`
fails with `bytecode_writes_enabled`. They are internal formal workers and are
never operator entry points. The only accepted output shape is:

```text
/var/lib/medchat-opensandbox-runs/$FULL_COMMIT/$UNIQUE_RUN_ID
```

where the base is a root-owned non-symlink directory not writable by group or
world, the full-commit parent is a non-symlink directory owned by the selected
non-root run user with mode `0700`, and the final run directory does not yet
exist. `UNIQUE_RUN_ID` must match `[a-z0-9][a-z0-9-]{7,63}`. Resolve and `lstat`
every parent; reject a path under `PROJECT_ROOT`, `/opt/medchat/current`, or
`/opt/medchat/releases`, an untrusted owner or mode, a symlink, `..`, a wrong
commit component, and any existing final target. Create the final directory
once with mode `0700`; do not accept a caller-precreated final target.

Implement the shared boundary with this interface and stable
`FormalRuntimeError.code` values:

```python
class FormalRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def prepare_formal_output_root(
    output_root: Path,
    *,
    expected_release_commit: str,
    project_root: Path,
    current_uid: int,
    trusted_base: Path = Path("/var/lib/medchat-opensandbox-runs"),
    current_root: Path = Path("/opt/medchat/current"),
    releases_root: Path = Path("/opt/medchat/releases"),
) -> Path:
    if current_uid <= 0:
        raise FormalRuntimeError("formal_root_forbidden")
    if not output_root.is_absolute() or ".." in output_root.parts:
        raise FormalRuntimeError("output_root_invalid")
    commit_parent = output_root.parent
    if commit_parent.parent != trusted_base or commit_parent.name != expected_release_commit:
        raise FormalRuntimeError("output_root_commit_mismatch")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", output_root.name) is None:
        raise FormalRuntimeError("output_run_id_invalid")
    base_metadata = trusted_base.lstat()
    parent_metadata = commit_parent.lstat()
    if (
        not stat.S_ISDIR(base_metadata.st_mode)
        or base_metadata.st_uid != 0
        or stat.S_IMODE(base_metadata.st_mode) & 0o022
        or trusted_base.resolve(strict=True) != trusted_base
    ):
        raise FormalRuntimeError("output_base_untrusted")
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != current_uid
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or commit_parent.resolve(strict=True) != commit_parent
    ):
        raise FormalRuntimeError("output_parent_untrusted")
    candidate = commit_parent / output_root.name
    forbidden = (
        project_root.resolve(strict=True),
        current_root.resolve(strict=True),
        releases_root.resolve(strict=True),
    )
    if any(candidate == root or candidate.is_relative_to(root) for root in forbidden):
        raise FormalRuntimeError("output_root_forbidden")
    if os.path.lexists(candidate):
        raise FormalRuntimeError("output_root_exists")
    os.mkdir(candidate, mode=0o700)
    created = candidate.lstat()
    if not stat.S_ISDIR(created.st_mode) or created.st_uid != current_uid or stat.S_IMODE(created.st_mode) != 0o700:
        raise FormalRuntimeError("output_root_creation_invalid")
    return candidate
```

The implementation catches only expected filesystem exceptions and maps them
to `output_root_unavailable`; it never retries creation and never removes a
partially observed path. Tests inject `trusted_base`, `current_root`, and
`releases_root` backed by real temporary ownership metadata rather than
weakening live checks.

In formal mode, both scripts derive their report path inside that newly created
run directory (`acceptance-report.json` or `stability-report.json`) and pass the
same directory to `SandboxDockingRunner.from_env()`. All poses, temporary input
copies, observer output, and reports stay below it. Refactor artifact projection
to validate relative paths against this output root, not `PROJECT_ROOT`.
Retain the trusted `O_EXCL`/hard-link writer and reject an existing report or
artifact rather than replacing it.

Change the existing `--report` parser default to `None`. A disabled or local
developer invocation maps `None` to its existing project-local default; a
formal enabled invocation rejects any explicit `--report` and always derives
the fixed report filename from `--output-root`. This prevents a second output
escape hatch.

RED tests must prove:

- the current default `PROJECT_ROOT/outputs` fails formal mode;
- a trusted external parent and absent run target succeed with injected UID and
  path readers;
- existing target, symlink parent, wrong owner/mode, wrong commit component, and
  release/project descendants fail closed;
- a subprocess invocation with `python -B` and
  `PYTHONDONTWRITEBYTECODE=1` creates no `.pyc`; invocation without bytecode
  suppression fails before a job;
- formal worker invocation with effective UID zero fails with
  `formal_root_forbidden`; command-builder tests require every socket probe,
  acceptance, and soak transient unit to use `User=medchat`, `Group=medchat`,
  `SupplementaryGroups=medchat-sandbox docker`, release `ReadOnlyPaths`, and only
  the external commit parent plus `/run/medchat-sandbox` as writable state;
- the root gate computes the release manifest before validation, after
  acceptance regardless of its return code, and after soak regardless of its
  return code; a mutation at any boundary yields `release_manifest_mismatch`
  before rollback;
- a second invocation cannot reuse the run directory or replace its report.

- [ ] **Step 3: Write RED formal-QEMU orchestrator tests**

Create `tests/sandbox_broker/test_formal_qemu_gate.py`. Import only pure helpers
from the future `scripts/run_opensandbox_formal_qemu_gate.py`; inject command,
filesystem, clock, random-ID, and activation adapters so local tests never call
systemd, Docker, OpenSandbox, Vina, or QEMU.

The command-builder tests require these exact privilege and environment
boundaries. `FormalWorkerKind` is a closed enum with `SOCKET_PROBE`,
`ACCEPTANCE`, and `SOAK`; callers cannot supply arbitrary environment pairs:

```python
validator = build_runtime_validator_command(
    python=Path("/opt/conda/envs/medchat/bin/python"),
    release_root=Path("/opt/medchat/current"),
)
assert validator == [
    "/opt/conda/envs/medchat/bin/python", "-B",
    "/opt/medchat/current/scripts/validate_opensandbox_deployment.py",
    "--runtime",
]

common = dict(
    run_base=Path("/var/lib/medchat-opensandbox-runs/" + "a" * 40),
)
probe = build_transient_worker_command(
    kind=FormalWorkerKind.SOCKET_PROBE,
    unit_name="medchat-formal-probe-c0ffee12",
    executable=["/opt/conda/envs/medchat/bin/python", "-B", "probe.py"],
    **common,
)
acceptance = build_transient_worker_command(
    kind=FormalWorkerKind.ACCEPTANCE,
    unit_name="medchat-formal-acceptance-c0ffee12",
    executable=["/opt/conda/envs/medchat/bin/python", "-B", "acceptance.py"],
    **common,
)
soak = build_transient_worker_command(
    kind=FormalWorkerKind.SOAK,
    unit_name="medchat-formal-soak-c0ffee12",
    executable=["/opt/conda/envs/medchat/bin/python", "-B", "soak.py"],
    **common,
)

for worker in (probe, acceptance, soak):
    assert "--uid=medchat" in worker
    assert "--gid=medchat" in worker
    assert "--property=SupplementaryGroups=medchat-sandbox docker" in worker
    assert "--setenv=PYTHONDONTWRITEBYTECODE=1" in worker
    assert "--property=ReadOnlyPaths=/opt/medchat/current /opt/medchat/releases" in worker

acceptance_gate = "--setenv=MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1"
soak_gate = "--setenv=MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1"
assert acceptance_gate not in probe and soak_gate not in probe
assert acceptance_gate in acceptance and soak_gate not in acceptance
assert soak_gate in soak and acceptance_gate not in soak
```

The validator command is run directly by the root gate with `python -B`; it is
never wrapped in `sudo -u medchat` or a non-root transient unit because runtime
validation requires Docker, root-only iptables state, and the root-managed
environment file. The gate first runs a transient socket probe under the same
`medchat:medchat` identity and both supplementary groups used by acceptance and
soak. The probe must prove read/write access to
`/run/medchat-sandbox/broker.sock`; an ordinary `sudo -u medchat test` command is
forbidden by a negative command-builder assertion.

The socket probe receives only `PYTHONDONTWRITEBYTECODE=1` and the fixed Broker
socket environment required by its closed probe implementation. Acceptance adds
exactly `MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1`; soak adds exactly
`MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1`. Neither scientific gate variable may
leak into the probe or the other scientific worker, and no builder accepts an
arbitrary environment mapping.

Add a whole-gate lock helper with this exact live path and ownership boundary:

```python
FORMAL_GATE_LOCK = Path("/run/lock/medchat-opensandbox-formal.lock")


@contextmanager
def acquire_formal_gate_lock(
    path: Path = FORMAL_GATE_LOCK,
) -> Iterator[BinaryIO]:
    """Hold one root-only non-blocking formal-gate lock for the full transaction."""
```

After parsing and the effective-UID-zero check, this is the first gate operation.
Open the non-symlink regular file using `O_CREAT | O_RDWR | O_CLOEXEC |
O_NOFOLLOW`, mode `0600`, verify root ownership and exact mode with `fstat`, then
call `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. A held lock returns the stable code
`formal_gate_locked`; unsafe metadata returns `formal_lock_untrusted`. Both are
pre-ownership refusals: they must not stage, activate, verify, run validation or
acceptance, publish/replace a terminal result, or call rollback. Keep the open
file description alive until the terminal `gate-result.json` and its parent are
fsynced on owned runs and any required rollback plus restored-PID verification
has completed. Release it only in the outermost `finally`.

Import `fcntl` only in the POSIX live adapter. The real temporary-file flock and
two whole-gate concurrency tests are POSIX-only with an explicit Windows skip;
all pure ordering, refusal, command-builder, and rollback tests remain
cross-platform through the injected `GateLockAdapter`.

Add a root-only claim helper with this exact public contract:

```python
def create_formal_soak_claim(
    *,
    claims_root: Path,
    release_commit: str,
    payload: Mapping[str, object],
) -> Path:
    """Create one permanent commit claim or raise FormalGateError with a stable code."""
```

The implementation must open the full-commit claim path with
`os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW`, mode `0600`, write one
canonical JSON object with no raw error text, fsync the file, close it, and fsync
the trusted root-owned parent. The claim path is
`/var/lib/medchat-opensandbox-runs/claims/$FULL_COMMIT.json`; the claims directory
must be a non-symlink root-owned directory mode `0700`, and every ancestor must
resolve beneath the trusted base without group/world write bits. Never unlink a
claim. Tests use multiprocessing or two synchronized threads and a real temporary
directory to prove exactly one concurrent claimant succeeds and the other gets
`formal_soak_already_claimed`. Also test a pre-existing regular claim, symlinked
claim, symlinked/untrusted parent, invalid commit, short write/fsync failure, and
an injected exception immediately after successful claim: every case is
fail-closed and a successfully created claim remains present.

Existing trusted regular commit claims and existing trusted terminal
`gate-result.json` files are checked while the whole-gate lock is held and before
legacy capture, staging, activation, or random-ID allocation. Check the terminal
result first: it returns `formal_gate_already_terminal`; only an existing claim
without a terminal result returns `formal_soak_already_claimed`.
Unsafe claim/result entries return their existing untrusted-path codes. These are
also pre-ownership refusals and always leave `rollback_count == 0`.

Define the rollback state exactly as:

```python
class RollbackState(str, Enum):
    NOT_REQUESTED = "not_requested"
    LEGACY = "legacy"
    GENERATION = "generation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
```

`rollback_once()` starts at `not_requested`, transitions to `legacy` immediately
before one `--rollback-legacy` call or to `generation` immediately before one
`--rollback` call, then ends at `succeeded` only after the expected Broker and
worker PID cwd values are verified. A failed call or cwd mismatch ends at
`failed`, atomically transitions the selected transaction to `rollback_failed`,
then atomically publishes and fsyncs the root-only poison marker
`/var/lib/medchat-runtime-generation/snapshot-required.json`. The marker schema
contains exactly `schema_version`, stable `failure_code`, `transaction_mode`,
and full `release_commit`; it contains no raw exception, command output,
credential, path supplied by a user, or environment value. After marker
publication, stop and disable worker then Broker. Any second call raises
`rollback_already_requested` without executing a command. It selects mode and
rollback target from the validated active
transaction and its state, not from a gate-local boolean or the long-term
`previous` file. It accepts legacy and generation transactions in `activating`
or `activated`; `captured`, staging failures, capture conflicts, and all other
states, including `rollback_failed`, never request rollback. Tests cover first
migration without
`previous`, later generation with transaction-local `pending_previous`, success,
command failure, PID-cwd mismatch, inherited `activating`, and an attempted
second rollback. Tests also inject rollback failure, verify the transaction and
poison marker are each fsynced, then invoke a second gate and assert
`qemu_snapshot_recovery_required`, `rollback_count == 0`, and no stage,
activation, validator, acceptance, claim, or result mutation.

```json
{
  "schema_version": 1,
  "failure_code": "rollback_failed",
  "transaction_mode": "legacy-or-generation",
  "release_commit": "40-lowercase-hex"
}
```

The values above are schema examples, not defaults. The `rollback_failed`
transaction replacement preserves every immutable snapshot, unit hash,
rollback source, `pending_previous`, and prior-metadata field from the active
transaction; only its state changes and no raw failure text is added.

Use an injected event recorder to test this exact gate order:

```text
gate_lock_acquired
preownership_claim_result_check
snapshot_recovery_poison_check
incomplete_activation_recovery_check
release_stage
rollback_state_capture_or_reuse
activation_transaction_fsync
release_activate
release_verify
pre_manifest
runtime_validator_root
socket_probe_transient
acceptance_transient
post_acceptance_manifest
acceptance_report_hash
formal_soak_claim
soak_transient_repeat_30
post_soak_manifest
soak_report_hash
rollback_once_if_failed
gate_result_write
gate_result_parent_fsync
gate_lock_release
```

On acceptance non-zero, missing report, or manifest mutation, the sequence still
contains `post_acceptance_manifest` before one rollback and never contains
`formal_soak_claim`. On soak non-zero, crash, missing report, or mutation, the
permanent claim remains, `post_soak_manifest` and report-presence/hash collection
are attempted before one rollback, and no second soak command is possible. The
only scientific soak command builder accepts `repeat == 30`; every other repeat
returns `formal_repeat_invalid`. A second same-commit gate fails with
`formal_soak_already_claimed` before creating a random artifact directory.

Add two full `run_formal_qemu_gate()` concurrency regressions, not merely helper
tests. In the first, G1 reaches an injected barrier immediately after acquiring
the real temporary-file `flock`; G2 enters with the same commit and returns
`formal_gate_locked`, with `activation_count == 0`, `acceptance_count == 0`, and
`rollback_count == 0`. Release G1, join both boundedly, and verify G1 alone owns
the transaction. In the second, let G1 complete successfully and fsync both the
permanent claim and terminal result; G2 then obtains the lock but refuses before
staging with `formal_gate_already_terminal` because terminal-result precedence
applies when claim and result both exist, with `activation_count == 0`,
`acceptance_count == 0`, `soak_count == 0`, and `rollback_count == 0`. Run each
test repeatedly to prove the refusal is deterministic.

Add a parameterized ownership-boundary test for stage failure, capture failure,
activation failure, and post-activation `--verify` failure. The first two assert
`rollback_count == 0`; the latter two assert `rollback_count == 1`, the selected
legacy/generation rollback command, restored PID-cwd verification before lock
release, and no scientific worker invocation. RED must expose the old external
activation/verify split; GREEN must pass through the one locked orchestrator.

Add RED/GREEN crash-recovery and state-transition matrices using the historical
legacy unit fixtures and two temporary generation commits:

- stage failure occurs before capture, leaves no legacy/generation transaction,
  performs zero live mutations and zero rollback, and a later gate can retry;
- stage success followed by a capture conflict or a crash leaves the immutable
  target reusable for the same commit: the next `--stage` performs a read-only
  full attestation and records no writes. A crash after capture proceeds directly
  to captured-state reuse; after the test fixture restores all live prerequisites
  that caused a capture conflict, the same-commit retry also reaches that reuse.
  Changing any marker, manifest, owner/mode, source commit, hygiene entry, or
  special-file/symlink property returns `release_stage_conflict` before
  captured-state reuse and with zero mutation;
- a process crash immediately after a valid legacy `captured` record is fsynced
  is resumed by the next gate without a second capture only when snapshot hashes,
  resolved legacy path, unit hashes, service enabled/active values, absent
  `current`, live unit bytes, live service states, and active PID cwd all still
  match; any mismatch returns `legacy_capture_conflict` with zero mutation and
  zero rollback;
- failure after the first unit replacement, after the `current` symlink switch,
  during restart, and during installed `--verify` each starts from a fsynced
  legacy `activating` record and restores snapshots, service states, unit hashes,
  absent `current`, and legacy PID cwd exactly once;
- an inherited legacy or generation `activating` or `activated` record for a
  commit with neither claim nor terminal result is rolled back and verified
  before any new stage, validator, acceptance, claim, or soak; the gate writes a
  separate atomic recovery-only gate result, creates no claim, and stops the
  current invocation;
- SIGKILL immediately after installed verification, during the privileged
  validator, and during acceptance leaves `activated`; each next invocation
  performs exactly one recovery rollback and stops with `stage_count == 0`,
  `validator_count == 0`, `acceptance_count == 0`, `claim_count == 0`, and no
  run-ID allocation. If the same commit already has a trusted claim, claim
  refusal takes precedence and performs zero rollback pending human review;
- generation failure before long-term `previous` publication and after its
  publication both restore `current` from this transaction's
  `pending_previous`, restore the pre-transaction long-term `previous` state,
  and never consult a stale `previous` value as the rollback source;
- a generation crash after `activating` fsync follows the same entry recovery
  path and stops; only a fully verified activation may reach `activated`;
- rollback failure in either mode atomically sets `rollback_failed`, creates the
  strict root-only poison marker, and stops/disables both services. A second
  gate, including one for a different commit, observes the poison marker before
  stage or recovery rollback and returns `qemu_snapshot_recovery_required` with
  `rollback_count == 0`. Only an operator may restore the recorded QEMU snapshot
  and run a separately documented, independently verified cleanup procedure to
  remove the poison marker; the formal gate has no cleanup path.

The atomic root-only gate result path is
`/var/lib/medchat-opensandbox-runs/results/$FULL_COMMIT/gate-result.json`. Tests
require a root-owned non-symlink `0700` parent, `O_EXCL` publication, file and
parent fsync, and this strict no-raw-error schema:

```json
{
  "schema_version": 1,
  "release_commit": "40-lowercase-hex",
  "status": "passed-or-failed",
  "failure_codes": ["stable_code_only"],
  "acceptance_rc": 0,
  "soak_rc": 0,
  "claim_path": "/var/lib/medchat-opensandbox-runs/claims/40-lowercase-hex.json-or-null",
  "acceptance_report_path": "/var/lib/medchat-opensandbox-runs/40-lowercase-hex/acceptance-run/acceptance-report.json-or-null",
  "soak_report_path": "/var/lib/medchat-opensandbox-runs/40-lowercase-hex/soak-run/stability-report.json-or-null",
  "acceptance_report_sha256": "64-lowercase-hex-or-null",
  "soak_report_sha256": "64-lowercase-hex-or-null",
  "rollback_mode": "none-or-legacy-or-generation",
  "rollback_state": "not_requested-or-succeeded-or-failed",
  "rollback_verified": false
}
```

Passing gates use `rollback_mode="none"`, state `not_requested`, and retain the
activated generation. A failure requests exactly one mode-correct rollback only
when its validated transaction state is `activating` or `activated`. An inherited
`activating` or unclaimed/unresolved `activated` transaction is recovered once
at entry even though the current gate did not create it; that recovery invocation
stops afterward. Lock contention,
existing claim/result refusal, unsafe pre-ownership paths, stage failure, capture
failure, and `legacy_capture_conflict` never request rollback; an owned
pre-mutation refusal returns its stable code without a formal soak claim or
terminal `gate-result.json`, so a corrected later invocation can retry. No random
scientific run directory is created on those paths.
If rollback fails, the orchestrator atomically records transaction state
`rollback_failed`, publishes/fsyncs the strict poison marker, stops and disables
both services, writes the result if its trusted result path remains usable, and
requires QEMU snapshot recovery; it never attempts another activation or
rollback. Every later gate checks the poison marker after lock and trusted
claim/result precedence but before transaction recovery, stage, or rollback and
returns `qemu_snapshot_recovery_required` with zero mutation. The gate never
removes or replaces the poison marker.

Entry recovery does not publish the commit's terminal formal `gate-result.json`,
because the current invocation is recovery-only and a corrected later gate must
remain possible. Instead write once with `O_EXCL` under
`/var/lib/medchat-opensandbox-runs/recoveries/$FULL_COMMIT/` using the canonical
pre-recovery transaction SHA-256 as the filename. The strict fsynced object
contains only `schema_version`, `release_commit`, `transaction_mode`,
`transaction_sha256`, `status` (`recovered` or `recovery_failed`), stable
`failure_codes`, `rollback_state`, and `rollback_verified`; it contains no raw
errors or secrets. This object is the atomic recovery-only gate result. Tests
prove it cannot be replaced, creates no formal soak claim, and does not make the
corrected later gate appear terminal.

- [ ] **Step 4: Run deployment, attestation, and formal-gate RED tests**

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider -k "runtime_generation or legacy_migration or activation_transaction or crash_recovery or activated_recovery or legacy_capture_conflict or stage_idempotent or release_stage_conflict or rollback_failed or snapshot_required or pending_previous or release_attestation or release_manifest or output_root or report_non_clobber or bytecode or formal_qemu_gate or formal_gate_lock or formal_gate_concurrent or formal_soak_claim or activation_verify_failure or rollback_once or command_builder or worker_gate_environment"
```

Expected: tests fail because the activation script, `/opt/medchat/current`
units, legacy capture/rollback, canonical manifest, external output-root contract,
formal gate, whole-transaction lock, permanent claim, isolated worker-gate
environments, command builders, recoverable activation transactions, and
rollback state machine do not yet exist.

- [ ] **Step 5: Implement the root-only generation mechanism**

`activate-runtime-generation.sh` supports exactly these commands:

```text
--stage SOURCE_REPOSITORY FULL_COMMIT
--capture-legacy QEMU_SNAPSHOT_ID OPERATOR_EVIDENCE_FILE
--activate FULL_COMMIT
--verify FULL_COMMIT
--rollback
--rollback-legacy
--destdir ABSOLUTE_TEST_ROOT followed by one command above
```

In live mode require UID 0, root-owned non-writable script and source ancestors,
a clean source repository, and `git rev-parse` equality with the supplied full
commit. `--stage` archives exactly that commit into a private sibling under
`/opt/medchat/releases`, writes/fsyncs the commit marker, creates empty runtime
mount targets, recursively assigns root ownership, applies the final directory
and file modes, and removes all write bits. Only after modes are final does it
compute the canonical manifest with `scripts/opensandbox_formal_runtime.py`,
write/fsync `.medchat-release-manifest.sha256` as root-owned mode `0444`, and
recompute once to prove equality. It then fsyncs parents and renames once to
`/opt/medchat/releases/$RELEASE_COMMIT`. `--verify` recomputes and compares the
manifest as well as checking the commit marker, owner, modes, units, and PID cwd.
Every activation-script invocation of the Python helper uses
`PYTHONDONTWRITEBYTECODE=1 python -B`; staging fails if hygiene detects any
cache or bytecode before the atomic rename.

Before creating a private sibling, `--stage` uses `lstat` on the fixed target.
If it is absent, perform the atomic staging path above. If it already exists,
do not open it for writing and do not rename, chmod, chown, clean, or repair it.
Instead require the source repository to resolve to the supplied full commit and
read-only attest every target owner/mode, full commit marker, canonical manifest,
and source commit, while rejecting every symlink, device, socket, FIFO, cache
component, and `.pyc` file. Only an exact match returns idempotent success with
zero writes. Any metadata, content, ancestry, commit, manifest, or hygiene
difference returns `release_stage_conflict` with zero mutation. Tests snapshot
inode/mtime/content/ownership before and after both idempotent success and
conflict, exercise stage-success followed by capture conflict and crash, and
prove the same-commit retry reaches captured-state reuse after all captured
prerequisites are again exact. A tampered existing generation is never deleted
or repaired by the gate.

On every non-recovery invocation, the locked formal gate runs `--stage` before
`--capture-legacy` or creation of a new generation transaction. An inherited
`activating` or unclaimed/unresolved `activated` transaction is recovered and
that invocation stops before this rule is reached. Staging is immutable
preparation only: failure cannot
create or update `legacy.json`, a generation transaction, `current`, units,
service state, or rollback state, and a later invocation may retry.

After staging, `--capture-legacy` is allowed only when
`/opt/medchat/current` is absent. It resolves and validates
`/opt/medchat/molecular_chat_system`, atomically snapshots the complete Broker
and worker unit bytes from
`/etc/systemd/system/medchat-sandbox-broker.service` and
`/etc/systemd/system/medchat-temporal-worker.service` under
`/var/lib/medchat-runtime-generation/legacy-units`, and writes an fsynced,
root-owned mode `0600` `/var/lib/medchat-runtime-generation/legacy.json` with
this exact schema:

```json
{
  "schema_version": 1,
  "transaction_state": "captured",
  "legacy_root": "/opt/medchat/molecular_chat_system",
  "current_state": "absent",
  "qemu_snapshot_id": "pre-runtime-generation-20260831",
  "operator_evidence_sha256": "64-lowercase-hex-digest",
  "services": {
    "medchat-sandbox-broker.service": {
      "unit_snapshot": "/var/lib/medchat-runtime-generation/legacy-units/medchat-sandbox-broker.service",
      "unit_sha256": "64-lowercase-hex-digest",
      "enabled": true,
      "active": true
    },
    "medchat-temporal-worker.service": {
      "unit_snapshot": "/var/lib/medchat-runtime-generation/legacy-units/medchat-temporal-worker.service",
      "unit_sha256": "64-lowercase-hex-digest",
      "enabled": true,
      "active": true
    }
  }
}
```

The values shown are schema examples, not defaults. Validate snapshot ID with
`[A-Za-z0-9][A-Za-z0-9._-]{2,127}`. `OPERATOR_EVIDENCE_FILE` must be a
root-owned, non-symlink regular file, mode `0600`, outside the source/release
trees; store only its SHA-256, never its contents. Unit snapshots are root-owned
mode `0600`, and their stored hashes must match before activation or rollback.
Unknown keys, missing keys, a pre-existing `current`, a different legacy root,
or unsafe file metadata fail closed. `transaction_state` is the closed enum
`captured`, `activating`, `activated`, `rolled_back`, or `rollback_failed`; every transition writes
a complete replacement file, fsyncs it, atomically renames it, and fsyncs the
trusted parent before the next operation.

An existing `captured` record is not recaptured. The gate may reuse it only after
recomputing and matching every immutable snapshot hash, the current gate's QEMU
snapshot ID and operator-evidence SHA-256, the resolved legacy path, both live
unit hashes, both recorded service enabled/active states, the absent `current`
state, and each active service's current PID cwd. Any mismatch returns
`legacy_capture_conflict` before a live mutation and performs no rollback. A
matching record left by a crash after capture is safe to reuse. An existing
`rolled_back` record remains immutable evidence: before a later corrected gate
captures a new transaction, archive that record and its unit snapshots under a
new root-owned O_EXCL history entry, fsync the history and parent, and then create
a fresh `legacy.json`; never overwrite or delete rollback evidence.

Immediately before the first live mutation (including replacing `current`,
replacing either unit, stopping, enabling/disabling, daemon-reload, or restart),
legacy activation atomically transitions `captured` to `activating`, adds the
validated full `release_commit` and `target_release` to that state-dependent
record, then fsyncs the record and parent. Only after the staged release,
installed units,
service states, marker, manifest, and both PID cwd values verify may it transition
to `activated`. `--rollback-legacy` accepts both `activating` and `activated` and
always restores from the immutable snapshots, never from partially changed live
files.

In generation-to-generation mode, use a separate root-owned mode `0600`
transaction record at
`/var/lib/medchat-runtime-generation/transactions/$FULL_COMMIT.json`.
Before any live mutation, validate the staged tree and current generation, then
atomically write and fsync this minimum transaction state:

```json
{
  "schema_version": 1,
  "transaction_mode": "generation",
  "transaction_state": "activating",
  "release_commit": "40-lowercase-hex",
  "target_release": "/opt/medchat/releases/40-hex-target",
  "pending_previous": "/opt/medchat/releases/40-hex-current",
  "prior_previous_state": "absent",
  "prior_previous": null
}
```

The values shown are schema examples. `pending_previous` is the resolved
`current` captured for this activation. `prior_previous_state` is the closed enum
`absent` or `present`; `prior_previous` must be `null` for `absent` and the
validated resolved release path for `present`, preserving the long-term metadata
that existed before this transaction. Generation `transaction_state` is the
closed enum `activating`, `activated`, `rolled_back`, or `rollback_failed`, with
every replacement and parent directory fsynced. Unknown fields, unsafe metadata,
invalid release ancestry, or a non-matching live current fail closed before
mutation. The activation then atomically replaces `/opt/medchat/current`, creates
and verifies the two
`/var/lib/medchat-worker` runtime directories, atomically installs the Broker and
worker units from the staged release, runs daemon-reload, restarts
`medchat-sandbox-broker.service` then `medchat-temporal-worker.service`, and
verifies both service PID cwd values equal the expected release. Only after all
activation verification passes may it atomically publish long-term `previous`
from `pending_previous` and transition the transaction to `activated`, with both
writes and parent directories fsynced. It also installs its already verified
source as `/usr/local/sbin/medchat-activate-runtime-generation`.

Once either mode reaches its fsynced `activating` boundary, every non-zero exit
or subsequent `--verify` failure is returned to the whole-gate-lock owner;
`--activate` and `--verify` never invoke rollback implicitly. The formal gate is
the sole caller of `rollback_once()`. Generation `--rollback` accepts
`activating` and `activated`, restores `current` only from that transaction's
`pending_previous`, restores the recorded pre-transaction long-term `previous`
state even if publication already occurred, verifies both restored PID cwd
values, and then atomically marks the transaction `rolled_back`. It must never
choose a target by reading the long-term `previous` file.

`--rollback-legacy` stops the new worker then Broker,
atomically restores both captured unit snapshots, restores the captured
`current_state`, runs daemon-reload, restores each captured enabled/disabled and
active/inactive state in Broker-before-worker dependency order, verifies both
unit hashes, and for each previously active service verifies its MainPID cwd is
the captured `legacy_root`. A previously inactive service must remain inactive
with MainPID zero. Only after all checks pass may it mark the legacy rollback
`rolled_back`. Never edit an activated release.

At gate entry, after the whole-gate lock and trusted claim/result checks but
before any new stage, validation, acceptance, or claim, first validate the
root-only poison marker. If present and strict, return
`qemu_snapshot_recovery_required` with zero mutation and zero rollback; unsafe
marker metadata fails closed. Then inspect both transaction stores. If either
contains `activating` or `activated` for a commit with no trusted claim or
terminal result, call `rollback_once()` for that exact mode, verify
snapshots/unit hashes/service state/PID cwd, atomically write a separate
root-owned recovery-only gate result under the trusted external evidence root,
fsync it and its parent, and stop the current invocation without creating a
formal soak claim. It must not stage or activate the requested commit. The
operator must correct the cause and start a later gate. A trusted claim takes
precedence over transaction recovery and returns claimed refusal for human
review with zero rollback. A recovery failure atomically changes the transaction
to `rollback_failed`, writes/fsyncs the poison marker, stops and disables worker
then Broker, and requires the recorded QEMU snapshot; no second rollback is
attempted.

If automatic legacy rollback cannot restore unit hashes, service states, and PID
cwd, stop and disable both services, retain `legacy.json` and unit snapshots,
atomically mark `legacy.json` as `rollback_failed`, publish the strict poison
marker, and require the operator to restore the pre-activation QEMU snapshot
whose ID and evidence hash are recorded in `legacy.json`. Generation rollback
failure follows the same state and poison protocol. Do not attempt a second
activation or a formal soak after that condition. The formal gate contains no
operation that removes the marker; only an operator who has restored the QEMU
snapshot may run a separate cleanup/verification procedure and remove it.

Update `install.sh` to install this script root-owned mode `0755` as
`/usr/local/sbin/medchat-activate-runtime-generation`, retain its existing
configuration-generation rollback, and make clear that immutable runtime
activation is authoritative for the live Broker and worker unit pair. The
existing Temporal bundle installer remains unchanged for legacy deployments and
must not overwrite units during a generation activation transaction.
Update `deployment/opensandbox/README.md` with the exact capture, first
activation, generation rollback, legacy rollback, single root formal-gate
invocation, whole-gate lock, manifest verification, and QEMU snapshot
disaster-recovery commands from Steps 10-11. The README must not expose direct
operator `--stage`, `--activate`, or `--verify` commands as a formal workflow;
those are tested primitives called only by the locked orchestrator. State
explicitly that the preserved `79860ec` checkout is
evidence only and must never be fed to `--stage` during first migration.

- [ ] **Step 6: Implement release, output, and virtualization attestation**

Add parser arguments without making disabled developer invocations fail during
argument parsing:

```python
parser.add_argument("--expected-release-commit")
parser.add_argument("--require-virt", choices=("qemu",))
parser.add_argument("--output-root", type=Path)
```

After confirming either internal formal worker environment gate is enabled,
require all three values, require `sys.dont_write_bytecode`, and create the
absent trusted output root. The root orchestrator calls
`attest_formal_runtime()` before runtime validation or any real job and owns all
pre/post manifest comparisons. The lower-level scripts cross-check the supplied
commit/output identity but cannot create claims, run privileged validation, or
perform rollback. Use `systemd-detect-virt --vm` without a shell. If the gate
detects a manifest mismatch, it adds `release_manifest_mismatch`, retains the
external evidence, and invokes the single rollback state machine; no component
writes into the release.

Map all failures to stable report codes; do not print command output, marker
content beyond the validated commit, Broker identity, or host paths. Add
`release_commit` and `release_manifest_sha256` to each strict report schema: they
are validated values for a formal run and `None` for a pre-attestation gate
report. Update report-field constants, gate/build/validation functions, fixture
dictionaries, and non-clobber tests together so report path, runtime identity,
manifest, and external artifact root are cross-checked without weakening
unknown-field rejection.

- [ ] **Step 7: Implement the single root-owned formal QEMU gate**

Create `scripts/run_opensandbox_formal_qemu_gate.py` with a `main()` that accepts
only these formal arguments:

```text
--source-repository /usr/local/src/medchat-release
--expected-release-commit FULL_40_HEX
--output-base /var/lib/medchat-opensandbox-runs
--repeat 30
--require-virt qemu
--qemu-snapshot-id VALIDATED_SNAPSHOT_ID
--qemu-operator-evidence-file /root/medchat-qemu-snapshot-evidence.json
```

Require effective UID zero, `sys.dont_write_bytecode`, a clean root-owned source
checkout whose `HEAD` exactly matches the full commit, a trusted QEMU evidence
file, a clean root-owned output/claims/results hierarchy, and `repeat == 30`.
The operator must not call the activation primitive or either lower-level formal
worker directly. Implement the gate as one explicit state machine with injected
lock, command, filesystem, activation, and clock adapters and no `shell=True`:

```python
def run_formal_qemu_gate(
    config: FormalGateConfig,
    *,
    commands: CommandExecutor,
    filesystem: FormalFilesystem,
    activation: ActivationAdapter,
    gate_lock: GateLockAdapter,
    clock: Callable[[], float],
    run_id_factory: Callable[[str], str],
) -> FormalGateResult:
    ...
```

The implementation performs, in order, under one outer `try/finally`:

1. After argument parsing and the effective-UID-zero check, acquire
   `/run/lock/medchat-opensandbox-formal.lock` non-blockingly. Hold the same open
   file description through every following step, terminal-result fsync, and any
   rollback verification. Lock refusal exits immediately without rollback.
2. While locked and before capture, staging, activation, or `run_id_factory`,
   validate the full commit, QEMU identity/evidence, source checkout and trusted
   output hierarchy. `lstat` the deterministic full-commit result first and claim
   second. A trusted result returns `formal_gate_already_terminal`; absent a
   result, a trusted claim returns `formal_soak_already_claimed`. Either condition
   or any unsafe entry is a pre-ownership refusal: do not activate, run workers,
   publish/replace a result, or rollback.
3. Validate `/var/lib/medchat-runtime-generation/snapshot-required.json`. A
   strict existing marker returns `qemu_snapshot_recovery_required` before any
   stage or rollback with zero mutation; unsafe marker metadata fails closed.
   The gate never deletes or repairs this marker.
4. Inspect the trusted legacy and generation transaction records. For any
   `activating` or `activated` record, derive and validate its full commit, then
   `lstat` that transaction commit's deterministic claim and result paths using
   the same trusted-path rules from step 2. A trusted claim/result refuses with
   zero rollback for human review; only when both are absent recover it with the
   one mode-correct `rollback_once()`,
   verify the restored state, atomically write/fsync the separate recovery-only
   gate result, and stop this invocation. Do not stage, validate, run acceptance,
   allocate a run ID, or create a claim. A recovery failure marks the transaction
   `rollback_failed`, writes/fsyncs the poison marker, stops/disables both
   services, and never attempts a second rollback. Claim/result precedence in
   this step means a claimed `activated` transaction is refused for human review,
   not automatically rolled back.
5. Invoke `--stage SOURCE_REPOSITORY FULL_COMMIT` before capture or any live
   activation transaction. Stage failure returns a stable pre-mutation code but
   writes no terminal gate result: it performs no capture, live mutation, claim,
   or rollback and remains safe to retry with a later invocation. If the fixed
   release already exists, only exact read-only attestation may return
   idempotent success; any difference returns `release_stage_conflict` with zero
   writes and zero live mutation.
6. Capture the rollback state. With no `current`, either safely reuse a matching
   `captured` legacy record or invoke `--capture-legacy` using the supplied
   snapshot ID/evidence file and set mode `legacy`; mismatch returns
   `legacy_capture_conflict` with zero mutation and rollback. With a verified
   current generation, validate it and set mode `generation`; the activation
   primitive will persist this invocation's `pending_previous` before mutation.
7. Immediately before the first live mutation inside `--activate FULL_COMMIT`,
   require the activation primitive to persist and fsync mode-specific state
   `activating`. Only that durable transition crosses the rollback boundary;
   gate-local `activation_started` alone is not sufficient. Invoke `--activate`,
   then the installed
   `--verify FULL_COMMIT`, and require marker, symlink target, service identity,
   and both PID cwd values to match the staged release. Any non-zero return,
   exception, or verification mismatch from this point onward is handled by the
   same outer failure path and calls the already selected `rollback_once()`
   exactly once. Add a RED/GREEN test where activation succeeds but `--verify`
   fails; assert the correct legacy/generation rollback runs once, restored PID
   cwd is verified, no validator/acceptance/claim runs, and the lock is released
   only afterward.
8. Compute and retain the pre-command canonical manifest; require zero labelled
   containers and release hygiene on the now activated generation.
9. Run the privileged deployment validator directly as root with
   `/opt/conda/envs/medchat/bin/python -B ... --runtime`; capture only return code
   and stable classifications, never stdout/stderr or environment values.
10. Run the Broker-socket read/write probe in a collected transient unit built
   with `User=medchat`, `Group=medchat`,
   `SupplementaryGroups=medchat-sandbox docker`, `NoNewPrivileges=true`,
   `ProtectSystem=strict`, read-only release paths, and only the external commit
   parent plus `/run/medchat-sandbox` writable.
11. Create an absent acceptance run directory below the external commit parent
   and run acceptance once in the same transient identity with `python -B` and
   `PYTHONDONTWRITEBYTECODE=1` plus only
   `MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1`.
12. Regardless of acceptance return code, recompute the manifest before any
   rollback, require the acceptance report, and hash it when present. Any return
   code, report, or mutation failure records stable codes and calls
   `rollback_once()` exactly once; it never creates a soak claim.
13. Only after every prior gate passes, create and fsync the permanent full-commit
   claim. Create the random soak output directory only after the claim succeeds.
14. Run the lower-level soak exactly once with `--repeat 30` in the same
   transient identity and only
   `MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1`. A process crash after claim is
   evidence requiring human review and never authorizes another run for that
   commit.
15. Regardless of soak return code, recompute the manifest before rollback,
   require the soak report, hash it when present, and collect only stable codes.
16. On any failure after a transaction has durably entered `activating`, call the
     mode-correct rollback once and verify its target PID
    cwd. On rollback failure stop and disable worker then Broker and require the
    recorded QEMU snapshot. Before stopping, atomically set the transaction to
    `rollback_failed` and atomically publish/fsync the strict poison marker.
    Failures before activation, lock contention, and
    existing claim/result refusals never call rollback. On success retain the
    activated generation.
17. For an owned transaction, atomically publish one root-only strict
    `gate-result.json` using an absent target, fsync the file and parent, and
    return non-zero for every failed gate. Release the whole-gate lock only after
    result fsync and rollback verification finish. Pre-ownership refusals return
    their stable code without creating or replacing a terminal result.

`build_transient_worker_command()` is the only constructor for socket probe,
acceptance, and soak units. It includes both supplementary groups on every path,
sets `PYTHONDONTWRITEBYTECODE=1`, uses `python -B`, rejects arbitrary writable
paths, and assigns a unique bounded unit name. The probe adds only the fixed
socket environment; acceptance adds only its acceptance gate; soak adds only its
soak gate. Tests assert both positive presence and cross-kind absence. The
acceptance and soak argument builders always include the expected commit, QEMU
requirement, and external output root. The gate never receives API credentials
and never copies the root-managed service environment into a transient unit.

`acquire_formal_gate_lock()`, activation/verification,
`create_formal_soak_claim()`, `rollback_once()`, manifest checks, report hashing,
and atomic gate-result publication are coordinated by
`run_formal_qemu_gate()` only.
Do not distribute rollback or exactly-once logic across shell traps, README
snippets, acceptance, or soak. Map expected operating-system errors to stable
codes; unexpected exceptions become `formal_gate_internal_error` without raw
text. The outer `finally` invokes rollback only when no poison marker exists, a
validated transaction is `activating` or `activated`, and rollback has not
already been requested. It uses
that transaction's mode and immutable rollback source, verifies the restored
state, fsyncs an owned terminal or recovery result when possible, and finally
releases the lock. It must never infer rollback ownership from a gate-local
boolean, delete a claim, replace an existing result, remove a poison marker, or
invoke a second rollback. A transaction in `rollback_failed` is never
rollback-eligible.

- [ ] **Step 8: Run GREEN, full, static, and repetition gates**

These commands are mandatory after the Task 7 implementation; the Task 6 gate
does not cover newly added deployment or attestation tests:

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider
1..10 | ForEach-Object { python -m pytest tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider; if ($LASTEXITCODE -ne 0) { throw "formal gate repetition failed at iteration $_" } }
python -m pytest tests/sandbox_broker -q -p no:cacheprovider
python -m compileall -q src scripts deployment/opensandbox/run_docking.py
python scripts/validate_opensandbox_deployment.py --static
git diff --check
```

Expected: all local tests pass or explicitly skip only real Linux/runtime cases;
all ten formal-gate repetitions pass; static validation exits zero.

- [ ] **Step 9: Obtain a second independent review and commit Task 7**

Reviewers must specifically verify immutable staging, canonical manifest before
and after each formal worker regardless of return code, external non-clobber
output roots, bytecode
suppression, current/previous atomic switches, first legacy capture/rollback,
historical-unit restoration, PID cwd checks, root/read-only ownership, writable
runtime bind mounts, root-only validator, transient worker supplementary groups,
closed per-worker environment gates, the whole-transaction non-blocking lock,
pre-ownership refusal without rollback, permanent O_EXCL claims, exact repeat
30, activation/verify inside the locked state machine, single rollback state,
strict existing-release idempotency/conflict handling, recovery-only handling of
unclaimed/unresolved `activating` and `activated` transactions, persistent
`rollback_failed` poison behavior, QEMU/KVM-only attestation, marker/path
mismatch, atomic result schema, and the
prohibition on direct activation or formal-worker invocation. No Critical or
Important findings may remain.

```powershell
git add -- deployment/opensandbox/activate-runtime-generation.sh deployment/opensandbox/install.sh deployment/opensandbox/medchat-sandbox-broker.service deployment/medchat-temporal-worker.service scripts/opensandbox_formal_runtime.py scripts/run_opensandbox_formal_qemu_gate.py scripts/run_opensandbox_docking_acceptance.py scripts/run_opensandbox_stability_soak.py scripts/validate_opensandbox_deployment.py deployment/opensandbox/README.md tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_formal_qemu_gate.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py
git diff --cached --check
git commit -m "feat: activate immutable opensandbox runtime generation"
git rev-parse HEAD
```

The resulting full commit becomes `RELEASE_COMMIT`; do not amend it after this
review or while preparing the QEMU source checkout.

- [ ] **Step 10: Prepare one reviewed QEMU source and recovery record**

Run on the snapshotted Ubuntu QEMU host from a root-owned clean checkout. This
step prepares immutable operator inputs only. It must not capture legacy state,
stage, activate, verify, restart, validate, run acceptance/soak, or invoke any
rollback primitive; all runtime mutation belongs to the locked gate in Step 11:

```bash
set -euo pipefail
SOURCE_REPOSITORY=/usr/local/src/medchat-release
RELEASE_COMMIT="$(git -C "$SOURCE_REPOSITORY" rev-parse HEAD)"
test "${#RELEASE_COMMIT}" -eq 40
test -z "$(git -C "$SOURCE_REPOSITORY" status --porcelain)"
test "$(git -C "$SOURCE_REPOSITORY" rev-parse "$RELEASE_COMMIT^{commit}")" = "$RELEASE_COMMIT"
: "${QEMU_SNAPSHOT_ID:?QEMU_SNAPSHOT_ID must identify the pre-gate snapshot}"
: "${QEMU_OPERATOR_EVIDENCE_FILE:?QEMU_OPERATOR_EVIDENCE_FILE must be a root-owned mode-0600 evidence file}"
sudo test -f "$QEMU_OPERATOR_EVIDENCE_FILE"
sudo test ! -L "$QEMU_OPERATOR_EVIDENCE_FILE"
sudo test "$(sudo stat -c '%u:%a' "$QEMU_OPERATOR_EVIDENCE_FILE")" = "0:600"
```

The operator may create the QEMU snapshot and its root-owned evidence file, and
may prepare this clean source checkout. There is no supported external formal
`--capture-legacy`, `--stage`, `--activate`, or `--verify` command. The gate
checks the poison marker, then recovers any inherited `activating` or
unclaimed/unresolved `activated` transaction and stops, or else stages the
reviewed commit before capturing/reusing the installed legacy layout, then
activates and verifies the generation while holding its one lock. It never
stages or activates `79860ec175f0d3235dfd0597f248d7d52788fc76`; that commit is
used only by the RED/GREEN historical-unit fixture and preserved evidence.

- [ ] **Step 11: Invoke the formal QEMU gate exactly once**

```bash
set -euo pipefail
SOURCE_REPOSITORY=/usr/local/src/medchat-release
RELEASE_COMMIT="$(git -C "$SOURCE_REPOSITORY" rev-parse HEAD)"
: "${QEMU_SNAPSHOT_ID:?QEMU_SNAPSHOT_ID must identify the pre-gate snapshot}"
: "${QEMU_OPERATOR_EVIDENCE_FILE:?QEMU_OPERATOR_EVIDENCE_FILE must identify its root-owned evidence file}"
sudo env PYTHONDONTWRITEBYTECODE=1 \
  /opt/conda/envs/medchat/bin/python -B \
  "$SOURCE_REPOSITORY/scripts/run_opensandbox_formal_qemu_gate.py" \
  --source-repository "$SOURCE_REPOSITORY" \
  --expected-release-commit "$RELEASE_COMMIT" \
  --output-base /var/lib/medchat-opensandbox-runs \
  --repeat 30 \
  --require-virt qemu \
  --qemu-snapshot-id "$QEMU_SNAPSHOT_ID" \
  --qemu-operator-evidence-file "$QEMU_OPERATOR_EVIDENCE_FILE"
```

This is the only operator command allowed to produce formal evidence. The CLI
acquires the root-only non-blocking lock before inspecting transaction state and
holds it through result fsync and rollback verification. Under that lock it
refuses an existing claim/result before mutation, checks the snapshot-required
poison marker, recovers and stops on an inherited `activating` or
unclaimed/unresolved `activated` transaction, stages, captures/reuses legacy
state or
persists generation `pending_previous`, activates, verifies, performs
release/QEMU/PID attestation,
privileged root runtime validation, the transient socket-permission probe,
non-root real acceptance, post-acceptance manifest/report hashing, the permanent
full-commit claim, the non-root 30-job soak, post-soak manifest/report hashing,
atomic `gate-result.json`, and at most one mode-correct rollback. The operator
must not invoke the activation primitive or either lower-level runner directly.

Lock contention returns `formal_gate_locked`. A trusted existing claim or
terminal result returns `formal_soak_already_claimed` or
`formal_gate_already_terminal`. These pre-ownership refusals occur before source
staging or runtime activation and always have `rollback_count == 0`. A second
invocation after a successful gate therefore cannot interrupt or roll back the
accepted generation.

The claim is created only after every preflight, runtime validator, release
attestation, real acceptance, acceptance-report, and post-acceptance-manifest
gate passes, immediately before the one 30-job command. It is permanent across
success, failure, process crash, rollback, and reboot. A second invocation for
the same full commit fails with `formal_soak_already_claimed` before creating a
random run directory. A crash after claim requires human evidence review and a
new corrected commit; deleting or reusing the claim is forbidden.

For a non-zero acceptance or soak return code, missing report, report-hash
failure, or release mutation, the gate completes the applicable post-command
manifest check before rollback. It writes stable result codes and invokes
`rollback_once()` no more than once. First migration uses `--rollback-legacy`;
later generations use `--rollback`; both verify restored Broker and worker PID
cwd. If rollback fails, the gate atomically marks the transaction
`rollback_failed`, publishes/fsyncs the strict snapshot-required poison marker,
stops and disables worker then Broker, and requires restoration of the recorded
QEMU snapshot. A later gate returns `qemu_snapshot_recovery_required` before any
stage or rollback; the gate cannot clear the marker.
An activation return-code failure, an exception after activation starts, or an
installed `--verify` failure follows the same one-rollback path. Stage failure,
capture failure, and `legacy_capture_conflict` occur before live mutation and do
not roll back. Task 8 only reads the fsynced gate result and never invokes
rollback.

### Task 8: Record evidence, rollback decision, and promotion status

**Files:**
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Record the immutable result**

Read the root-owned
`/var/lib/medchat-opensandbox-runs/results/$RELEASE_COMMIT/gate-result.json` and
cross-check its commit, strict schema, file owner/mode, claim path, report paths,
hashes, return codes, rollback mode/state, and rollback verification. Record the
release commit, absolute host report path, SHA-256, total status,
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

- [ ] **Step 2: Record the gate-owned rollback result without mutating runtime**

Task 8 is read-only with respect to runtime. It must not invoke `--rollback`,
`--rollback-legacy`, restart, stop, activate, or assume that a
`PREVIOUS_COMMIT` exists. For a failed gate, read and verify only the immutable
gate result:

```python
assert result["status"] == "failed"
assert result["rollback_mode"] in {"legacy", "generation"}
assert result["rollback_state"] in {"succeeded", "failed"}
assert type(result["rollback_verified"]) is bool
```

When `rollback_state == "succeeded"`, record the gate-verified rollback mode and
target PID cwd evidence. When it is `failed`, record that the orchestrator
already stopped and disabled both services and that the operator must restore
the QEMU snapshot identified by the root-owned legacy evidence. Do not attempt a
second rollback, remove a claim, rerun the soak, or fabricate a previous
generation. Promotion is blocked for either rollback outcome.

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
- Task 7's new deployment, attestation, closed worker-environment builder,
  whole-gate lock/concurrency, claim, activation/verify failure,
  manifest-ordering, gate-result, and rollback-state tests run RED before
  implementation and GREEN afterward.
- The post-Task-7 full Broker, compileall, static, and independent-review gates pass.
- One full reviewed commit is installed under `/opt/medchat/releases/$RELEASE_COMMIT`; both services run from it through `/opt/medchat/current`, and its canonical manifest is identical before validation, after acceptance, and after soak.
- Formal attestation accepts only matching marker/path/commit and QEMU/KVM virtualization, rejecting WSL, bare metal, unknown virtualization, and all identity mismatches.
- The root gate runs privileged runtime validation; socket probe, formal acceptance,
  and soak run as `medchat:medchat` with transient
  `SupplementaryGroups=medchat-sandbox docker`, `python -B`, and a trusted
  external run root. No report, pose, temporary file, cache, or bytecode enters
  the release.
- The socket probe receives neither scientific gate variable; acceptance receives
  only `MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1`; soak receives only
  `MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1`.
- One root-only non-blocking `flock` covers source/state checks, inherited
  transaction recovery, stage/capture,
  activation/verify, validation, acceptance, claim, soak, result fsync, and any
  rollback verification. Lock contention and existing claim/result refusals do
  not mutate runtime and never invoke rollback.
- Linux runtime validation and real acceptance pass on the activated immutable generation.
- Exactly one new formal 30-job QEMU soak is attempted per full commit, enforced
  by a permanent root-only O_EXCL claim created immediately before the command;
  its report is retained and hashed when produced even if the command exits
  non-zero, and a post-claim crash never authorizes a rerun.
- The report truthfully records latency, failures, events, cleanup, provenance, artifacts, probes, and zero-leak state.
- Any retry, scientific timeout, invalid output, cleanup uncertainty, dependency failure, probe failure, release mismatch, virtualization mismatch, or residual container prevents promotion.
- First migration stages before capture, safely reuses only an unchanged
  `captured` snapshot, and can atomically restore the exact legacy units, service
  states, absent `current`, unit hashes, and legacy PID cwd without staging the
  old commit. Later rollback uses the active transaction's fsynced
  `pending_previous`, not long-term `previous`. Both modes persist `activating`
  before live mutation, recover inherited `activating` or unclaimed/unresolved
  `activated` state before a new stage and then stop, invoke at most one rollback,
  and verify restored PID cwd. Existing immutable releases are reused only after
  exact read-only attestation; any mismatch is `release_stage_conflict` with zero
  mutation.
  Stage/capture/conflict failures before live mutation do not roll back. QEMU
  snapshot restoration is required only when that gate-owned rollback fails;
  failure persists `rollback_failed` and a root-only poison marker that all later
  gates honor without another rollback.
- `docs/handoff/latest.md` distinguishes evaluator correctness from runtime stability and records either promotion evidence or rollback evidence.
