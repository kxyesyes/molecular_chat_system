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
| `scripts/run_opensandbox_docking_acceptance.py` | Real container observer, process-local image-preflight cache, lifecycle probes, unique diagnostics-delta identity discovery, and external formal output root. |
| `scripts/run_opensandbox_stability_soak.py` | Corrected report gate, external formal output root, and future release/QEMU attestation. |
| `scripts/opensandbox_formal_runtime.py` | Canonical release manifest, release attestation, hygiene checks, and trusted external-output preparation shared by staging and both formal runners. |
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


def _broker_event_key(event: Mapping[str, object]) -> tuple[str, str, str, int] | None:
    trace_id = event.get("trace_id")
    job_id = event.get("job_id")
    phase = event.get("phase")
    attempt = event.get("attempt")
    if not all(type(value) is str and value for value in (trace_id, job_id, phase)):
        return None
    if type(attempt) is not int or attempt < 1:
        return None
    return trace_id, job_id, phase, attempt


def _broker_event_baseline(snapshot: object) -> frozenset[tuple[str, str, str, int]]:
    if not isinstance(snapshot, Mapping):
        raise _BrokerObservationError("diagnostics_invalid")
    telemetry = snapshot.get("telemetry", snapshot)
    if not isinstance(telemetry, Mapping):
        raise _BrokerObservationError("diagnostics_invalid")
    events = telemetry.get("recent_events")
    if not isinstance(events, list):
        raise _BrokerObservationError("diagnostics_invalid")
    keys: list[tuple[str, str, str, int]] = []
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
    baseline: frozenset[tuple[str, str, str, int]],
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
`recent_events`. The helper then polls only the diagnostics delta. Zero new
identities at the single monotonic deadline is
`broker_identity_timeout`; more than one is
`broker_identity_ambiguous`; a terminal event before `command_started` is
`broker_terminal_before_command`. All three fail closed and never trigger a
second scientific command.

Write a complete deterministic unit fixture whose fake diagnostics client
returns, in order: the baseline snapshot, a snapshot with one new
`provisioning_started`, and a snapshot with the same identity's
`command_started`. Start the synchronous fake runner in one `threading.Thread`.
Its `execute()` increments `run_count`, sets a `threading.Event` named
`run_entered`, and waits on the supplied cancellation event. The polling thread
calls `_wait_for_unique_new_broker_event(...)`; only after it returns the unique
identity does the test set cancellation. Join both threads with bounded waits
and assert `run_count == 1`.

Add negative fixtures for zero identity until deadline, two new identities in
one delta, and `job_terminal` before `command_started`; each raises the exact
typed code above, sets cancellation in `finally`, joins the runner thread, and
still asserts `run_count == 1`. Use a `FakeSandboxClient` plus
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
- failed restart or cwd verification restores the old symlink and old services;
- `--rollback` uses the recorded previous target and verifies old PID cwd;
- generation-to-generation activation requires a valid `current` symlink and
  uses only `previous`; a first migration without `current` instead requires a
  verified `legacy.json` captured by `--capture-legacy`;
- the historical `79860ec` unit fixtures retain
  `WorkingDirectory=/opt/medchat/molecular_chat_system`, are captured byte for
  byte with SHA-256, and are restored byte for byte by `--rollback-legacy`;
- first migration never stages or activates `79860ec` as a new generation and
  succeeds without a `previous` symlink;
- legacy rollback restores the prior `current` state, unit enabled/active state,
  unit hashes, and both active PID cwd values to the captured legacy root;
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

Define `attest_formal_runtime()` in the same shared module and import it from
both formal runners. Do not import the soak module from the acceptance module;
the soak already imports acceptance helpers, so that would create a circular
dependency. Test the shared helper with injected command/path readers:

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
        PurePosixPath("scripts/run_opensandbox_docking_acceptance.py"),
        PurePosixPath("scripts/run_opensandbox_stability_soak.py"),
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
- `script_path.resolve()` equals the allowlisted acceptance or soak script named
  by `expected_script_relative`; cross-script and arbitrary script paths fail;
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

Add parser tests requiring `--expected-release-commit`, `--require-virt qemu`,
and explicit `--output-root` for a formal enabled run in both scripts. Formal
mode also requires `sys.dont_write_bytecode is True`; invocation without `-B`
fails with `bytecode_writes_enabled`. The only accepted output shape is:

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
- formal invocation with effective UID zero fails with
  `formal_root_forbidden`; deployment-asset tests require the documented
  transient unit to use the current worker `User=medchat`, `Group=medchat`, a
  transient-only `SupplementaryGroups=docker`, release `ReadOnlyPaths`, and only
  the external commit parent as writable state;
- acceptance and soak recompute the release manifest before the first job and
  after the last probe but before the final atomic report; a mutation at either
  boundary makes the report failed with `release_manifest_mismatch`;
- a second invocation cannot reuse the run directory or replace its report.

- [ ] **Step 3: Run deployment/attestation RED tests**

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider -k "runtime_generation or legacy_migration or release_attestation or release_manifest or output_root or require_virt or report_non_clobber or bytecode"
```

Expected: tests fail because the activation script, `/opt/medchat/current`
units, legacy capture/rollback, canonical manifest, external output-root contract,
and formal attestation flags do not yet exist.

- [ ] **Step 4: Implement the root-only generation mechanism**

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

`--capture-legacy` is allowed only when `/opt/medchat/current` is absent and no
legacy transaction already exists. It resolves and validates
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
unsafe file metadata, or an existing partial transaction fail closed.
`transaction_state` is the closed enum `captured`, `activated`, or
`rolled_back`; update it only through an atomic fsync-and-rename after the
corresponding operation verifies. Activation requires `captured`, automatic or
manual legacy rollback requires `activated`, and a completed `rolled_back`
record is immutable evidence rather than a reusable transaction.

In generation-to-generation mode, `--activate` verifies the staged tree, records
the resolved old target in
`/var/lib/medchat-runtime-generation/previous`, atomically replaces
`/opt/medchat/current` through a temporary symlink plus `rename`, creates and
verifies the two `/var/lib/medchat-worker` runtime directories, atomically
installs the Broker and worker units from the staged release, runs daemon-reload,
restarts `medchat-sandbox-broker.service` then
`medchat-temporal-worker.service`, and verifies both service PID cwd values equal
the expected release. It also installs its already verified source as
`/usr/local/sbin/medchat-activate-runtime-generation`. On any failure it restores
the previous unit snapshots and symlink, daemon-reloads, restarts in dependency
order, verifies old PID cwd, and exits non-zero. For first migration it requires
the verified legacy transaction instead of creating `previous`; on any
activation or gate failure it invokes the same internal operation as
`--rollback-legacy`. `--rollback-legacy` stops the new worker then Broker,
atomically restores both captured unit snapshots, restores the captured
`current_state`, runs daemon-reload, restores each captured enabled/disabled and
active/inactive state in Broker-before-worker dependency order, verifies both
unit hashes, and for each previously active service verifies its MainPID cwd is
the captured `legacy_root`. A previously inactive service must remain inactive
with MainPID zero. Only after all checks pass may it mark the legacy rollback
complete. `--rollback` remains generation-only and transitions to `previous`.
Never edit an activated release.

If automatic legacy rollback cannot restore unit hashes, service states, and PID
cwd, stop and disable both services, retain `legacy.json` and unit snapshots,
and require the operator to restore the pre-activation QEMU snapshot whose ID
and evidence hash are recorded in `legacy.json`. Do not attempt a second
activation or a formal soak after that condition.

Update `install.sh` to install this script root-owned mode `0755` as
`/usr/local/sbin/medchat-activate-runtime-generation`, retain its existing
configuration-generation rollback, and make clear that immutable runtime
activation is authoritative for the live Broker and worker unit pair. The
existing Temporal bundle installer remains unchanged for legacy deployments and
must not overwrite units during a generation activation transaction.
Update `deployment/opensandbox/README.md` with the exact capture, first
activation, generation rollback, legacy rollback, transient non-root formal
runner, manifest verification, and QEMU snapshot disaster-recovery commands
from Steps 8-10. State explicitly that the preserved `79860ec` checkout is
evidence only and must never be fed to `--stage` during first migration.

- [ ] **Step 5: Implement release and virtualization attestation**

Add parser arguments without making disabled developer invocations fail during
argument parsing:

```python
parser.add_argument("--expected-release-commit")
parser.add_argument("--require-virt", choices=("qemu",))
parser.add_argument("--output-root", type=Path)
```

After confirming either formal environment gate is enabled, require all three
values, require `sys.dont_write_bytecode`, create the absent trusted output root,
and call `attest_formal_runtime()` before deployment validation or any job. Use
`systemd-detect-virt --vm` without a shell. Recompute the release manifest after
the last scientific/probe operation and before the final atomic report. If it
differs, add `release_manifest_mismatch`, fail the report, and retain the
external evidence; do not write anything into the release.

Map all failures to stable report codes; do not print command output, marker
content beyond the validated commit, Broker identity, or host paths. Add
`release_commit` and `release_manifest_sha256` to each strict report schema: they
are validated values for a formal run and `None` for a pre-attestation gate
report. Update report-field constants, gate/build/validation functions, fixture
dictionaries, and non-clobber tests together so report path, runtime identity,
manifest, and external artifact root are cross-checked without weakening
unknown-field rejection.

- [ ] **Step 6: Run deployment/attestation GREEN tests and the post-Task-7 full gate**

These commands are mandatory after the Task 7 implementation; the Task 6 gate
does not cover newly added deployment or attestation tests:

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
python -m pytest tests/sandbox_broker -q -p no:cacheprovider
python -m compileall -q src scripts deployment/opensandbox/run_docking.py
python scripts/validate_opensandbox_deployment.py --static
git diff --check
```

Expected: all local tests pass or explicitly skip only real Linux/runtime cases;
static validation exits zero.

- [ ] **Step 7: Obtain a second independent review and commit Task 7**

Reviewers must specifically verify immutable staging, canonical manifest before
and after formal commands, external non-clobber output roots, bytecode
suppression, current/previous atomic switches, first legacy capture/rollback,
historical-unit restoration, PID cwd checks, root/read-only ownership, writable
runtime bind mounts, rollback on restart failure, QEMU/KVM-only attestation, and
marker/path mismatch. No Critical or Important findings may remain.

```powershell
git add -- deployment/opensandbox/activate-runtime-generation.sh deployment/opensandbox/install.sh deployment/opensandbox/medchat-sandbox-broker.service deployment/medchat-temporal-worker.service scripts/opensandbox_formal_runtime.py scripts/run_opensandbox_docking_acceptance.py scripts/run_opensandbox_stability_soak.py scripts/validate_opensandbox_deployment.py deployment/opensandbox/README.md tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py
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
ACTIVATOR="$SOURCE_REPOSITORY/deployment/opensandbox/activate-runtime-generation.sh"
if [ -L /opt/medchat/current ]; then
    ROLLBACK_MODE=generation
else
    : "${QEMU_SNAPSHOT_ID:?QEMU_SNAPSHOT_ID must identify the pre-activation snapshot}"
    : "${QEMU_OPERATOR_EVIDENCE_FILE:?QEMU_OPERATOR_EVIDENCE_FILE must be a root-owned mode-0600 evidence file}"
    ROLLBACK_MODE=legacy
    sudo "$ACTIVATOR" --capture-legacy "$QEMU_SNAPSHOT_ID" "$QEMU_OPERATOR_EVIDENCE_FILE"
fi
sudo "$ACTIVATOR" --stage "$SOURCE_REPOSITORY" "$RELEASE_COMMIT"
sudo "$ACTIVATOR" --activate "$RELEASE_COMMIT"
sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$RELEASE_COMMIT"

rollback_runtime() {
    if [ "$ROLLBACK_MODE" = legacy ]; then
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback-legacy
    else
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
    fi
}

if ! sudo -u medchat env PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/medchat/bin/python -B /opt/medchat/current/scripts/validate_opensandbox_deployment.py --runtime; then
    rollback_runtime
    exit 1
fi
```

This is the only supported first-migration path. It captures the installed
legacy layout and activates the current reviewed release directly. It never
stages or activates `79860ec175f0d3235dfd0597f248d7d52788fc76`; that commit is
used only by the RED/GREEN historical-unit fixture and preserved evidence.
Absence of `previous` during a verified first migration is expected.

Before any real job, independently verify both PID cwd values:

```bash
rollback_runtime() {
    if sudo test -f /var/lib/medchat-runtime-generation/legacy.json && ! sudo test -e /var/lib/medchat-runtime-generation/previous; then
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback-legacy
    else
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
    fi
}
trap 'rollback_runtime' ERR
BROKER_PID="$(sudo systemctl show --property=MainPID --value medchat-sandbox-broker.service)"
WORKER_PID="$(sudo systemctl show --property=MainPID --value medchat-temporal-worker.service)"
test "$(sudo systemctl show --property=User --value medchat-sandbox-broker.service)" = medchat-sandbox
test "$(sudo systemctl show --property=Group --value medchat-sandbox-broker.service)" = medchat-sandbox
test "$(sudo systemctl show --property=User --value medchat-temporal-worker.service)" = medchat
test "$(sudo systemctl show --property=Group --value medchat-temporal-worker.service)" = medchat
test "$(sudo readlink -f "/proc/$BROKER_PID/cwd")" = "/opt/medchat/releases/$RELEASE_COMMIT"
test "$(sudo readlink -f "/proc/$WORKER_PID/cwd")" = "/opt/medchat/releases/$RELEASE_COMMIT"
VIRT="$(systemd-detect-virt --vm)"
test "$VIRT" = qemu || test "$VIRT" = kvm
test "$(sudo docker ps -q --filter label=opensandbox.io/id | wc -l)" -eq 0
sudo -u medchat test -r /run/medchat-sandbox/broker.sock
sudo -u medchat test -w /run/medchat-sandbox/broker.sock
getent group docker >/dev/null
trap - ERR
```

Any failed identity, permission, deployment, or manifest check calls
`rollback_runtime` from the same root-controlled operator script and blocks all
remaining steps. There is no root execution fallback for acceptance or soak.
If `--rollback-legacy` itself fails, stop and disable both services and restore
the recorded QEMU snapshot; do not continue with a second activation.

The current worker unit runs as `medchat:medchat`, has
`SupplementaryGroups=medchat-sandbox`, and deliberately cannot access the Docker
socket. The real observers still need bounded Docker inspect/exec access, so the
two formal CLIs run in one-shot transient units as the same non-root worker user
with an additional transient `docker` supplementary group. Do not permanently
add `medchat` to that group. The transient units set `ProtectSystem=strict`,
`ReadOnlyPaths=/opt/medchat/current /opt/medchat/releases`, and allow writes only
to the external commit parent and `/run/medchat-sandbox`. Their source is the
attested immutable release; they receive no environment file or API credential.

- [ ] **Step 9: Run one real acceptance gate from the activated generation**

```bash
set -euo pipefail
rollback_runtime() {
    if sudo test -f /var/lib/medchat-runtime-generation/legacy.json && ! sudo test -e /var/lib/medchat-runtime-generation/previous; then
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback-legacy
    else
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
    fi
}
trap 'rollback_runtime' ERR
RELEASE_COMMIT="$(sudo cat /opt/medchat/current/.medchat-release-commit)"
RUN_USER="$(sudo systemctl show --property=User --value medchat-temporal-worker.service)"
RUN_GROUP="$(sudo systemctl show --property=Group --value medchat-temporal-worker.service)"
test "$RUN_USER" = medchat
test "$RUN_GROUP" = medchat
RUN_BASE="/var/lib/medchat-opensandbox-runs/$RELEASE_COMMIT"
ACCEPTANCE_RUN_ID="acceptance-$(date -u +%Y%m%d%H%M%S)-$(openssl rand -hex 8)"
ACCEPTANCE_OUTPUT_ROOT="$RUN_BASE/$ACCEPTANCE_RUN_ID"
sudo install -d -o root -g root -m 0755 /var/lib/medchat-opensandbox-runs
sudo install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0700 "$RUN_BASE"
sudo test ! -e "$ACCEPTANCE_OUTPUT_ROOT"
trap - ERR
set +e
sudo systemd-run --quiet --wait --pipe --collect --uid="$RUN_USER" --gid="$RUN_GROUP" --property=SupplementaryGroups=docker --property=NoNewPrivileges=true --property=PrivateTmp=true --property=ProtectSystem=strict --property='ReadOnlyPaths=/opt/medchat/current /opt/medchat/releases' --property="ReadWritePaths=$RUN_BASE /run/medchat-sandbox" --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1 --setenv=MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock /opt/conda/envs/medchat/bin/python -B /opt/medchat/current/scripts/run_opensandbox_docking_acceptance.py --repeat 1 --expected-release-commit "$RELEASE_COMMIT" --require-virt qemu --output-root "$ACCEPTANCE_OUTPUT_ROOT"
ACCEPTANCE_RC=$?
set -e
ACCEPTANCE_REPORT="$ACCEPTANCE_OUTPUT_ROOT/acceptance-report.json"
if ! sudo test -f "$ACCEPTANCE_REPORT"; then
    rollback_runtime
    exit 1
fi
if ! ACCEPTANCE_SHA256="$(sudo sha256sum "$ACCEPTANCE_REPORT" | awk '{print $1}')"; then
    rollback_runtime
    exit 1
fi
if [ "${#ACCEPTANCE_SHA256}" -ne 64 ]; then
    rollback_runtime
    exit 1
fi
printf '%s\n' "acceptance_report_sha256=$ACCEPTANCE_SHA256"
if [ "$ACCEPTANCE_RC" -ne 0 ]; then
    rollback_runtime
    exit "$ACCEPTANCE_RC"
fi
trap 'rollback_runtime' ERR
sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$RELEASE_COMMIT"
trap - ERR
```

Expected: all enabled checks pass with gVisor, pinned digest, provenance,
cleanup, cancellation, timeout, and zero leaks. A skip or failure triggers the
mode-correct rollback and blocks the formal soak. The acceptance report and all
its artifacts remain under its unique external run directory.

- [ ] **Step 10: Run the only permitted formal 30-job QEMU soak and always hash its report**

The report directory is mutable runtime state outside the read-only release.
Use a second unique, absent run directory under the same commit. Run exactly
once:

```bash
set -euo pipefail
rollback_runtime() {
    if sudo test -f /var/lib/medchat-runtime-generation/legacy.json && ! sudo test -e /var/lib/medchat-runtime-generation/previous; then
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback-legacy
    else
        sudo /usr/local/sbin/medchat-activate-runtime-generation --rollback
    fi
}
trap 'rollback_runtime' ERR
RELEASE_COMMIT="$(cat /opt/medchat/current/.medchat-release-commit)"
RUN_USER="$(sudo systemctl show --property=User --value medchat-temporal-worker.service)"
RUN_GROUP="$(sudo systemctl show --property=Group --value medchat-temporal-worker.service)"
RUN_BASE="/var/lib/medchat-opensandbox-runs/$RELEASE_COMMIT"
SOAK_RUN_ID="soak-$(date -u +%Y%m%d%H%M%S)-$(openssl rand -hex 8)"
SOAK_OUTPUT_ROOT="$RUN_BASE/$SOAK_RUN_ID"
sudo install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0700 "$RUN_BASE"
sudo test ! -e "$SOAK_OUTPUT_ROOT"
trap - ERR
set +e
sudo systemd-run --quiet --wait --pipe --collect --uid="$RUN_USER" --gid="$RUN_GROUP" --property=SupplementaryGroups=docker --property=NoNewPrivileges=true --property=PrivateTmp=true --property=ProtectSystem=strict --property='ReadOnlyPaths=/opt/medchat/current /opt/medchat/releases' --property="ReadWritePaths=$RUN_BASE /run/medchat-sandbox" --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1 --setenv=MEDCHAT_SANDBOX_BROKER_SOCKET=/run/medchat-sandbox/broker.sock /opt/conda/envs/medchat/bin/python -B /opt/medchat/current/scripts/run_opensandbox_stability_soak.py --repeat 30 --expected-release-commit "$RELEASE_COMMIT" --require-virt qemu --output-root "$SOAK_OUTPUT_ROOT"
SOAK_RC=$?
set -e
REPORT="$SOAK_OUTPUT_ROOT/stability-report.json"
if ! sudo test -f "$REPORT"; then
    rollback_runtime
    exit 1
fi
if ! REPORT_SHA256="$(sudo sha256sum "$REPORT" | awk '{print $1}')"; then
    rollback_runtime
    exit 1
fi
if [ "${#REPORT_SHA256}" -ne 64 ]; then
    rollback_runtime
    exit 1
fi
printf '%s\n' "report_sha256=$REPORT_SHA256"
if [ "$SOAK_RC" -ne 0 ]; then
    rollback_runtime
    exit "$SOAK_RC"
fi
trap 'rollback_runtime' ERR
sudo /usr/local/sbin/medchat-activate-runtime-generation --verify "$RELEASE_COMMIT"
trap - ERR
```

The bounded `set +e` applies only to the soak process. A non-zero result still
requires the report to exist and be hashed before rollback and exit. Never rerun
or overwrite that report. Both formal commands run as the actual non-root worker
user in collected transient units with `PYTHONDONTWRITEBYTECODE=1` and
`python -B`; root only prepares the trusted parent, starts the constrained unit,
reads hashes, and performs activation/rollback. The target run directory itself
remains absent until the non-root script atomically creates it. Neither command
may write the release tree, and systemd read-only mounts plus pre/post canonical
manifest checks enforce that boundary.

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
- One full reviewed commit is installed under `/opt/medchat/releases/$RELEASE_COMMIT`; both services run from it through `/opt/medchat/current`, and its canonical manifest is identical before and after both formal commands.
- Formal attestation accepts only matching marker/path/commit and QEMU/KVM virtualization, rejecting WSL, bare metal, unknown virtualization, and all identity mismatches.
- Formal acceptance and soak run as the verified non-root worker user with `python -B`, require a new trusted `/var/lib/medchat-opensandbox-runs/$RELEASE_COMMIT/$RUN_ID`, and leave no report, pose, temporary file, cache, or bytecode in the release.
- Linux runtime validation and real acceptance pass on the activated immutable generation.
- Exactly one new formal 30-job QEMU soak is attempted; its report is always retained and hashed, even when the command exits non-zero.
- The report truthfully records latency, failures, events, cleanup, provenance, artifacts, probes, and zero-leak state.
- Any retry, scientific timeout, invalid output, cleanup uncertainty, dependency failure, probe failure, release mismatch, virtualization mismatch, or residual container prevents promotion.
- First migration captures and can atomically restore the exact legacy units, service states, absent `current`, unit hashes, and legacy PID cwd without staging the old commit. Later rollback uses `previous`; QEMU snapshot restoration remains disaster recovery only when automatic legacy rollback cannot be verified.
- `docs/handoff/latest.md` distinguishes evaluator correctness from runtime stability and records either promotion evidence or rollback evidence.
