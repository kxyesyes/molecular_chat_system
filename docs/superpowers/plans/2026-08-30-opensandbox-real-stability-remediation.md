# OpenSandbox Real Runtime Stability Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the project-controlled causes behind the preserved real OpenSandbox run's 5 provider/readiness failures, 5 scientific-command timeouts, 2 invalid outputs, probe-timing failures, and approximately 50-second image-inspection delay without weakening scientific truth, cleanup, provenance, release identity, or promotion gates.

**Architecture:** Keep the corrected soak evaluator as the evidence judge. Remediate sanitized telemetry, bounded control-plane retries, image-readiness caching, phase budgets, atomic scientific output, and deterministic probes first. Then a single tested root orchestrator acquires a whole-transaction non-blocking lock, stages the reviewed commit as a root-owned immutable runtime generation, and atomically activates and verifies both Broker and Temporal worker through `/opt/medchat/current`. A root-only SQLite journal is the sole authority for formal-gate state, settlement, resolution, and poison; the permanent O_EXCL full-commit claim remains an immutable audit anchor, while `gate-result.json` is only a reconstructable redacted export. The same orchestrator owns privileged validation, isolated non-root transient acceptance/soak, pre/post manifests, evidence hashing, journal transitions, and at most one attested rollback; operators never call activation primitives or formal workers directly.

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
| `src/sandbox_broker/formal_gate_state.py` | Sole authoritative SQLite journal schema, trusted-path checks, legal state graph, CAS transitions, settlement, resolution, poison, and redacted export projection. |
| `scripts/opensandbox_formal_runtime.py` | Canonical release manifest, release attestation, hygiene checks, and trusted external-output preparation shared by staging and the formal gate. |
| `scripts/run_opensandbox_formal_qemu_gate.py` | Sole root-only formal QEMU entry; holds the process-wide gate lock while it stages, journals, activates, verifies, validates, runs non-root acceptance/soak, settles the journal, exports the result, and owns at most one rollback. |
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
| `tests/sandbox_broker/test_formal_gate_state.py` | Journal path security, schema constraints, CAS/state graph, settlement, recovery, poison, and concurrency tests. |
| `tests/sandbox_broker/test_formal_qemu_gate.py` | Formal command/environment isolation, whole-gate concurrency, claim and DB crash gaps, failpoint recovery, manifest ordering, at-most-once rollback, and reconstructable export tests. |
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

### Task 7: Implement the authoritative SQLite formal-gate journal and run the one formal soak

**Files:**
- Create: `src/sandbox_broker/formal_gate_state.py`
- Create: `tests/sandbox_broker/test_formal_gate_state.py`
- Create: `scripts/run_opensandbox_formal_qemu_gate.py`
- Modify: `scripts/opensandbox_formal_runtime.py`
- Modify: `scripts/run_opensandbox_docking_acceptance.py`
- Modify: `scripts/run_opensandbox_stability_soak.py`
- Modify: `scripts/validate_opensandbox_deployment.py`
- Modify: `deployment/opensandbox/activate-runtime-generation.sh`
- Modify: `deployment/opensandbox/install.sh`
- Modify: `deployment/opensandbox/medchat-sandbox-broker.service`
- Modify: `deployment/medchat-temporal-worker.service`
- Modify: `deployment/opensandbox/README.md`
- Modify: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `tests/sandbox_broker/test_real_opensandbox_acceptance.py`
- Modify: `tests/sandbox_broker/test_stability_soak.py`
- Create: `tests/sandbox_broker/test_formal_qemu_gate.py`

The implementation in this task replaces every proposed JSON transaction,
settlement, resolution, and poison state file with one authoritative SQLite
journal. Do not implement compatibility readers for the superseded multi-JSON
design because it has never been deployed. The permanent O_EXCL claim remains a
separate immutable audit anchor; `gate-result.json` and an optional poison marker
are exports only and never drive recovery or admission.

- [ ] **Step 1: Write RED journal filesystem and schema tests**

Create `tests/sandbox_broker/test_formal_gate_state.py` first. The production
module is `src/sandbox_broker/formal_gate_state.py`, and its only production
database is:

```text
/var/lib/medchat-runtime-generation/formal-gate.sqlite3
```

Test all of the following before implementation:

- the process must be root and the fixed parent must be a root-owned, non-symlink
  directory with mode `0700`;
- the database must be a root-owned, non-symlink regular file with mode `0600`;
- every path component is opened/verified without following symlinks; a symlink,
  non-root owner, wrong type, group/other writable component, or alternate DB path
  fails closed before journal mutation;
- database creation uses a temporary trusted parent handle, commits the schema,
  closes the new file, fsyncs it, and fsyncs the parent directory so first-boot
  creation survives power loss;
- connections use stdlib `sqlite3` only and set `PRAGMA journal_mode=DELETE`,
  `PRAGMA synchronous=FULL`, `PRAGMA foreign_keys=ON`, and a bounded busy timeout;
- every read-modify-write operation begins with `BEGIN IMMEDIATE`; no transition
  helper silently opens a deferred transaction;
- corrupt SQLite, unsupported schema version, failed `integrity_check`, unexpected
  table/trigger/index definitions, or an unknown state returns a stable fail-closed
  code and never recreates or repairs the database automatically.

The schema has one current row per full commit and an append-only transition
audit table. Use strict tables when supported by the pinned SQLite version and
explicit `CHECK`, `NOT NULL`, `UNIQUE`, and foreign-key constraints in all cases.
The current row includes at least:

```text
schema_version
release_commit                  # 40 lowercase hex, primary key
mode                            # legacy | generation
state
release_target                 # canonical in-root release identifier
previous_target                # canonical in-root target or explicit absent sentinel
legacy_snapshot_sha256
generation_snapshot_sha256
qemu_snapshot_evidence_sha256
pre_manifest_sha256
post_acceptance_manifest_sha256
post_soak_manifest_sha256
claim_sha256
claim_relative_name
report_sha256
report_status                  # passed | failed | absent
failure_envelope_sha256
physical_attestation_sha256
rollback_receipt_sha256
rollback_verified              # 0 | 1
resolution_evidence_sha256
resolution_record_sha256
stable_code
created_at_utc
updated_at_utc
row_version
```

Targets and claim names are canonical identifiers below fixed trusted roots, not
arbitrary host paths. Hashes are lowercase SHA-256 or NULL. `stable_code` is from
a closed allowlist; raw exception text, response bodies, credentials, sandbox
IDs, host-user paths, and out-of-root paths are forbidden. The audit table binds
`release_commit`, old/new states, old/new row versions, stable code, evidence
hashes, and timestamp to the current row through a foreign key. It contains no
second writable settlement truth.

`failure_envelope_sha256` and `resolution_record_sha256` are hashes of
versioned canonical, sanitized tuples whose bounded fields are stored in the same
current row and audit entry; they are not hashes of separate state JSON files.
`rollback_receipt_sha256`, report hashes, snapshot hashes, and operator-evidence
hashes bind immutable external evidence, but only the journal row decides state.

All transitions use one API that performs an expected-state and expected-version
compare-and-swap:

```python
def transition(
    conn: sqlite3.Connection,
    *,
    release_commit: str,
    expected_state: str,
    expected_version: int,
    next_state: str,
    updates: Mapping[str, object],
) -> FormalGateRecord:
    """Apply one legal CAS transition or fail closed without side effects."""
```

The implementation must execute one constrained `UPDATE ... WHERE
release_commit=? AND state=? AND row_version=?`, require exactly one changed row,
append the audit row in the same SQLite transaction, and commit once. Unknown,
illegal, stale, repeated, or zero/multi-row transitions fail closed. Tests must
use two independent connections and multiprocessing to prove stale writers cannot
both advance the same transaction.

- [ ] **Step 2: Lock the closed state machine and recovery table in RED tests**

Use exactly these authoritative states:

```text
STAGED
ACTIVATING
ACTIVE_PRECLAIM
CLAIMED
ROLLING_BACK
RESOLUTION_REQUIRED
RESOLVING
SETTLED_PASSED
SETTLED_FAILED
POISONED
```

The journal module owns this transition table. Any transition not listed is
illegal. 閳ユ发ay mutate閳?means live runtime mutation, not the SQLite CAS itself.

| State | Allowed next | Entry recovery | May mutate runtime? |
|---|---|---|---|
| `STAGED` | `ACTIVATING`, `SETTLED_FAILED`, `POISONED` | Re-attest the immutable release and captured legacy/generation snapshot. If exact, the same invocation may continue; mismatch poisons. | No |
| `ACTIVATING` | `ACTIVE_PRECLAIM`, `ROLLING_BACK`, `POISONED` | Activation may have started. Attest `current`, both service PID cwd values, unit hashes, and the transaction-local previous/snapshot. Exact active target may finish verify; exact previous target enters deterministic failure settlement; ambiguity poisons. | Activation/verify or one attested rollback |
| `ACTIVE_PRECLAIM` | `CLAIMED`, `ROLLING_BACK`, `RESOLUTION_REQUIRED`, `POISONED` | Re-attest active target and manifests. A matching permanent claim with no DB hash is the claim-file/DB gap and must CAS to `RESOLUTION_REQUIRED`; it must never rerun acceptance/soak. No claim may continue only through the original locked invocation; an inherited process treats it as pre-claim recovery. | Acceptance before claim only in the owning invocation; recovery may roll back once |
| `CLAIMED` | `SETTLED_PASSED`, `ROLLING_BACK`, `RESOLUTION_REQUIRED`, `POISONED` | A trusted report may settle; a trusted failure envelope may enter rollback; no report/envelope is a post-claim crash and enters `RESOLUTION_REQUIRED`. | No scientific rerun; rollback only through the declared path |
| `ROLLING_BACK` | `SETTLED_FAILED`, `POISONED` | Verify DB evidence, then attest physical runtime. If still exactly on target, dispatch the one idempotent attested rollback; if already exactly previous, write/verify the receipt and settle without dispatch; any mixed/unknown state poisons. | At most one logical rollback |
| `RESOLUTION_REQUIRED` | `RESOLVING`, `POISONED` | Ordinary gate refuses with zero runtime mutation. Only the explicit resolution CLI may proceed after evidence and stopped-process checks. | No |
| `RESOLVING` | `SETTLED_FAILED`, `POISONED` | If physical attestation proves exact previous target and rollback completion, verify/write the receipt and settle without another rollback. If completion cannot be proved, poison and require QEMU snapshot recovery; never dispatch rollback twice. | No repeat rollback |
| `SETTLED_PASSED` | none | Verify report, claim when present, release target, manifest, and journal hashes; then admit a different commit. | No |
| `SETTLED_FAILED` | none | Verify the applicable pre-live, normal-failure, or manual-resolution evidence branch and its physical attestation; then admit a different commit. | No |
| `POISONED` | none | Refuse every formal gate and resolution mutation with `qemu_snapshot_recovery_required`. | No |

Settlement is closed and singular:

- `SETTLED_PASSED` requires a trusted passed report hash/status and all required
  manifest/attestation hashes. The claim hash is required for the 30-job path.
- `SETTLED_FAILED` after ordinary failure requires the stable failure envelope,
  optional failed report hash/status, and a verified rollback receipt.
- `SETTLED_FAILED` before any live mutation requires the stable failure envelope
  and a physical no-mutation attestation hash; its rollback fields must remain
  NULL/0. This branch is valid only from `STAGED`.
- `SETTLED_FAILED` after manual resolution requires claim hash, operator evidence
  hash, resolution record hash, and a verified rollback receipt. It does not
  require or fabricate a formal scientific report.
- `POISONED` is itself the blocking authority. A marker may be exported for
  operator visibility but its absence, corruption, or publication crash cannot
  weaken or override the DB state.

The superseded multi-file state vocabulary and its transaction, resolution,
settlement, and poison JSON authorities do not exist in the future design.

- [ ] **Step 3: Write RED failpoint and concurrency tests for the complete gate**

Create `tests/sandbox_broker/test_formal_qemu_gate.py`. Import pure helpers from
the future formal gate and inject the clock, filesystem, SQLite connection factory,
subprocess runner, service observer, physical attestor, and failpoint controller.
Do not monkeypatch global SQLite state or invoke real systemd/Docker in unit tests.

Parameterize a hard process-kill failpoint immediately before and after:

- every authoritative SQLite commit;
- database creation and parent-directory fsync;
- release staging/capture and canonical-manifest fsync;
- activation dispatch and activation verification;
- acceptance dispatch and report/manifest hashing;
- permanent claim O_EXCL creation/fsync and the following DB CAS;
- the single 30-job scientific soak dispatch and report hashing;
- failure-envelope persistence;
- rollback dispatch and physical verification;
- resolution authorization CAS, rollback, and receipt verification;
- `SETTLED_PASSED`, `SETTLED_FAILED`, and `POISONED` commits;
- atomic `gate-result.json` export and parent fsync.

For every failpoint, restart through the public entry point and assert:

- scientific soak dispatch count is at most one for the full commit;
- successful logical rollback count is at most one;
- no crash window can move backward in the state graph;
- no crash window can turn failed/unknown evidence into passed;
- no ordinary retry can clear `RESOLUTION_REQUIRED` or `POISONED`;
- no JSON export, claim content, marker, or filesystem ordering can override DB;
- every refusal happens before random run-ID allocation and before live mutation.

The RED suite must explicitly cover these named scenarios:

1. A succeeds and reaches `SETTLED_PASSED`; corrected commit B may stage.
2. A fails normally, completes verified rollback, reaches `SETTLED_FAILED`; B may
   stage whether A failed before or after claim.
3. A crashes after claim with no report; B is refused; an unauthorized resolution
   is refused; one authorized resolution reaches `SETTLED_FAILED`; B may stage;
   A remains permanently unrerunnable because its claim remains.
4. Claim file creation succeeds but DB update does not. Entry verifies the claim
   content/hash, CASes to `RESOLUTION_REQUIRED`, and never reruns scientific work.
5. `SETTLED_PASSED` commits but `gate-result.json` export crashes. Entry regenerates
   the same atomic redacted export from DB without validation, activation, claim,
   acceptance, soak, or rollback.
6. `ROLLING_BACK` commits and the process dies before rollback. Exact target
   attestation permits one rollback; exact previous attestation completes the
   receipt/settlement without rollback; mixed attestation poisons.
7. Rollback physically completes but DB receipt/settlement does not. Entry proves
   exact previous state and completes `SETTLED_FAILED` without repeating rollback.
8. Resolution crashes before rollback, during rollback, after physical rollback,
   and after settled commit. Only provable physical completion may settle; an
   indeterminate inherited `RESOLVING` becomes `POISONED` and never double-rolls
   back.
9. `POISONED` blocks even when the optional marker was never created; marker-only
   state without matching DB poison is an untrusted-export error, not authority.
10. Two concurrent gates contend on the whole-flow flock; exactly one enters.
    Two stale SQLite writers cannot both transition the same row.
11. Legacy-first and generation-to-generation rollback each use only the current
    row's previous target and snapshot hashes.
12. Existing release reuse succeeds only after exact immutable attestation;
    conflict is a stable zero-mutation failure.

- [ ] **Step 4: Run the journal and gate RED tests before implementation**

Run these before creating the production module or gate implementation and retain
the expected assertion/import failures in the task notes:

```powershell
python -m pytest tests/sandbox_broker/test_formal_gate_state.py -q -p no:cacheprovider
python -m pytest tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider
```

Do not run Docker, OpenSandbox, systemd, acceptance, soak, or QEMU during RED.

- [ ] **Step 5: Implement the authoritative journal module**

Create `src/sandbox_broker/formal_gate_state.py` with no FastAPI or broker-service
imports. It owns path verification, schema creation/migration refusal, connection
PRAGMAs, integrity validation, record parsing, legal-transition validation, CAS,
append-only transition audit, and read-only export projections.

Every public mutation method:

1. verifies root identity and securely creates the missing fixed parent as
   root-owned mode `0700`, or verifies the existing trusted parent and DB path;
2. opens the DB without following symlinks;
3. applies the required PRAGMAs;
4. starts `BEGIN IMMEDIATE`;
5. re-reads and validates the complete row;
6. checks expected state/version and evidence-field invariants;
7. performs exactly one legal CAS plus audit insert;
8. commits and fsyncs the DB; on first parent creation it fsyncs the verified
   `/var/lib` directory after `mkdir`, and on DB creation it fsyncs the DB and
   `/var/lib/medchat-runtime-generation`;
9. returns a frozen typed projection without raw SQLite rows or errors.

Rollback on Python/SQLite exceptions is best effort, but the public error is a
stable sanitized code. No helper deletes, truncates, VACUUMs, auto-recovers,
archives, or replaces this DB. Journal backup/archival belongs only to the
independent operator process after QEMU snapshot restoration.

- [ ] **Step 6: Integrate immutable staging, capture, activation, and rollback with the journal**

Keep the already reviewed immutable-generation design and make the journal row
its transaction-local source:

- stage the reviewed full commit under `/opt/medchat/releases/$FULL_COMMIT` from a
  root-owned clean source, reject dirty/short/mismatched commits, and attest an
  existing release read-only before reuse;
- build the canonical manifest before any live mutation and require exact equality
  after activation, acceptance, and soak;
- include only the allowlisted source/config/deployment files; exclude outputs,
  caches, bytecode, credentials, DBs, models, and mutable runtime data;
- preserve the root-owned release and unit permissions already specified in this
  plan; both services execute through `/opt/medchat/current` and must attest the
  same release commit and PID cwd;
- capture first-migration legacy unit bytes/hashes, enabled/active states, current
  link state, and both PID cwd values as bounded root-owned snapshots whose hashes
  are stored in the `STAGED` row;
- for later generations, store the exact active release identifier as
  `previous_target`; never recover from a long-term global `previous` pointer;
- commit `ACTIVATING` before calling the activation primitive; after current link,
  daemon-reload, service restart, health, unit hash, PID cwd, release marker, and
  canonical manifest all verify, CAS to `ACTIVE_PRECLAIM`;
- `--activate`, `--verify`, `--rollback`, and `--rollback-legacy` remain root-only
  primitives called only by the formal orchestrator. They never update the DB,
  select transaction state, create claims, run scientific work, or choose a
  fallback target;
- generation and legacy rollback receive the journal row's exact target/snapshot
  inputs, are idempotent and attested, and never edit an immutable release.

Stage or capture failure before `ACTIVATING` settles as a stable failed row with
no rollback receipt only if no live mutation occurred; otherwise it poisons. Once
`ACTIVATING` is durable, every failure follows the state table and physical
attestation rules.

Preserve the previously reviewed release and unit contracts verbatim in tests:

- Broker and worker use `WorkingDirectory=/opt/medchat/current`, execute the
  scripts below that link, and declare
  `ReadOnlyPaths=/opt/medchat/current /opt/medchat/releases`;
- mutable worker `scratch` and `temp_docking` live under root-created
  `/var/lib/medchat-worker` backing directories owned by `medchat:medchat`
  mode `0700` and are exposed only through the reviewed `BindPaths`;
- staged release directories/files are root-owned and non-writable, the full
  commit marker is mode `0444`, and no release entry is group/world writable;
- first migration captures the historical unit bytes, hashes, enabled/active
  states, absent/present current link, and legacy PID cwd without staging the
  historical commit as a generation.

`canonical_release_manifest()` continues to walk with `lstat`, sort relative
POSIX paths by encoded bytes, reject symlinks/devices/sockets/FIFOs, and hash
entry type, mode, relative path, size, and file SHA-256. Only `.git`, known cache
directories, `.pyc`, and the two separately attested release metadata files are
excluded from the digest. `validate_release_hygiene()` nevertheless rejects
`.git`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, every `.pyc`, and
every special/symlink entry, so an excluded cache can never hide a release write.
The root gate recomputes both hygiene and digest before validation, after
acceptance regardless of return code, and after soak regardless of return code.

- [ ] **Step 7: Implement the single root-owned formal QEMU gate**

Create `scripts/run_opensandbox_formal_qemu_gate.py`. The operator-visible entry
keeps the whole-flow root-only non-blocking flock from preflight through final DB
settlement and best-effort export. No other command may invoke a formal worker.
The lock is `/run/lock/medchat-opensandbox-formal.lock`, opened without following
symlinks as a root-owned regular file with mode `0600`; its file descriptor
remains open for the entire flow. An untrusted path/type/owner/mode fails before
journal or runtime mutation.

The main gate order is fixed:

1. Validate root, `repeat == 30`, clean full commit, QEMU/KVM evidence, trusted
   root-managed environment, fixed DB/output roots, and exact script allowlist.
2. Acquire the whole-flow flock. Lock contention returns `formal_gate_locked`
   before DB/runtime/output mutation.
3. Open and validate the SQLite journal. Any `POISONED` row blocks immediately.
   Verify all settled rows and reconcile only the explicit recovery states in the
   transition table. Do not let `gate-result.json` or a marker select a branch.
4. For the requested commit, inspect the journal row and permanent claim together.
   A matching claim-file/DB gap enters `RESOLUTION_REQUIRED`; a mismatch poisons.
   A trusted settled row returns its stable already-terminal code only after row,
   claim, report/evidence, receipt, current/previous target, and physical state
   agree. An unsettled row is recovered before any return.
5. Stage and attest the release, capture/reuse the exact legacy or generation
   snapshot, and create/reuse the `STAGED` row by legal CAS.
6. CAS to `ACTIVATING`, activate, verify, and CAS to `ACTIVE_PRECLAIM`.
7. Run privileged deployment/runtime validation, then a transient non-root socket
   probe and real acceptance. Preserve the already reviewed phase allowlist,
   timeout budgets, clean diagnostics delta, zero-container check, artifact truth,
   and post-acceptance manifest/report hashes.
8. Create `/var/lib/medchat-opensandbox-runs/claims/$FULL_COMMIT.json` with
   `O_CREAT|O_EXCL|O_NOFOLLOW`, root `0600`, file fsync, and parent fsync. It is
   permanent. Hash its canonical sanitized content, then immediately CAS
   `ACTIVE_PRECLAIM -> CLAIMED` with that hash. Never delete, replace, or reuse it.
9. Allocate the formal run directory only after claim settlement, then dispatch
   exactly one non-root 30-job soak. Never retry the scientific command, Vina,
   Meeko, upload, validation, or the complete run.
10. Hash and validate the report, post-soak manifest, provenance, artifacts,
    cleanup, probes, retries, release identity, and zero-leak state. Any retry,
    timeout, invalid output, fallback, mismatch, cleanup uncertainty, or missing
    evidence prevents pass.
11. On success, within one `BEGIN IMMEDIATE` CAS `CLAIMED -> SETTLED_PASSED` while
    storing report/status and final manifest hashes. After DB fsync, atomically
    regenerate the redacted `gate-result.json`; export failure does not change
    settlement and is repaired from DB on next read-only entry.
12. On normal failure after possible live mutation, first CAS to `ROLLING_BACK`
    while storing the stable failure-envelope hash and any trusted failed-report
    hash/status. Then perform the row-selected idempotent attested rollback. After
    exact physical verification, use one DB transaction to store the receipt hash,
    set `rollback_verified=1`, and reach `SETTLED_FAILED`. Export afterward.
13. If rollback fails or physical state is ambiguous, CAS to `POISONED`, stop and
    disable worker then Broker, optionally export the strict poison marker, and
    require the recorded QEMU snapshot. The DB state blocks even if marker export
    fails.
14. Release the flock only after DB fsync and export attempt. The outer `finally`
    must not infer rollback from a process-local boolean; it consults and CASes the
    authoritative row and can dispatch only the state-table-authorized rollback.

Each transient unit is created from a closed environment builder. Socket probe
receives neither scientific gate variable; acceptance receives only
`MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1`; soak receives only
`MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1`. Acceptance and soak run as
`medchat:medchat` with `SupplementaryGroups=medchat-sandbox docker`, `python -B`,
the activated immutable release as working directory, and a trusted external
output root. Credentials remain only in root-managed runtime environment files
and are never copied to reports, DB fields, commands, unit descriptions, or logs.

The external output contract remains:

```text
/var/lib/medchat-opensandbox-runs/$FULL_COMMIT/$UNIQUE_RUN_ID
```

The base is a root-owned non-symlink directory not writable by group/world. The
full-commit parent is a non-symlink directory owned by the selected non-root run
user with mode `0700`. `UNIQUE_RUN_ID` matches
`[a-z0-9][a-z0-9-]{7,63}`, and the final directory must not exist before its one
mode-`0700` creation. Resolve and `lstat` every component; reject `..`, wrong
commit components, untrusted owner/mode/type, symlinks, existing final targets,
and any candidate under the source, current link, or releases root. Acceptance
and soak derive their reports below that root and pass the same root to the
scientific runner; poses, copied inputs, temporary files, observer output, and
reports stay below it. Artifact projection validates relative paths against this
root, and no cleanup path may cross it.

Retain the existing exact phase model and attempt cardinality: non-attempt phases
have NULL attempt; attempt phases accept only attempts 1 and 2; the 30-job soak
is exactly one scientific attempt per job. Preserve the fixed 270-second command,
300-second sandbox, and 300-second p95 gates. Keep image readiness caching,
deterministic probe timing, canonical pre/post manifests, no release writes,
external artifacts, and the immutable historical report unchanged.

- [ ] **Step 8: Implement the explicit post-claim resolution path**

The same script exposes `--resolve-incomplete-claim`, but this mode is not a
formal run. It acquires the same flock, opens the same journal, and accepts only a
row in `RESOLUTION_REQUIRED` for the supplied old full commit.

Inside the lock it must verify:

- exact claim file and DB/hash binding;
- exact operator-evidence file hash, root ownership, mode `0600`, non-symlink
  components, and bounded location;
- journal row version/state and release/previous/snapshot hashes;
- stopped formal transient units/processes and zero relevant sandbox/container
  state;
- no formal report that can be independently settled instead;
- QEMU identity and the recorded snapshot identifier.

After validation, CAS `RESOLUTION_REQUIRED -> RESOLVING` while storing the
operator-evidence and canonical resolution-record hashes. Commit before rollback.
Perform the one row-selected attested rollback. On exact previous-state proof,
store the verified receipt and CAS to `SETTLED_FAILED` in one transaction. Do not
create a scientific result, recreate/delete the claim, run validator, acceptance,
or soak. Wrong hashes, active work, repeated authorization, or a settled row
refuse with zero rollback.

If the process inherits `RESOLVING`, it may settle without rollback only when
physical attestation proves the exact previous target and the stored evidence can
produce a trusted receipt. If that proof is unavailable or mixed, CAS to
`POISONED`; do not attempt a second rollback. A rollback error follows the same
poison path.

The operator syntax is hash- and version-bound and contains no transaction-file
argument:

```bash
sudo env PYTHONDONTWRITEBYTECODE=1 \
  /opt/conda/envs/medchat/bin/python -B \
  /usr/local/src/medchat-release/scripts/run_opensandbox_formal_qemu_gate.py \
  --resolve-incomplete-claim \
  --old-release-commit "$OLD_RELEASE_COMMIT" \
  --claim-sha256 "$OLD_CLAIM_SHA256" \
  --expected-row-version "$EXPECTED_ROW_VERSION" \
  --operator-evidence-file "$RESOLUTION_EVIDENCE_FILE" \
  --operator-evidence-sha256 "$RESOLUTION_EVIDENCE_SHA256" \
  --output-base /var/lib/medchat-opensandbox-runs
```

- [ ] **Step 9: Run GREEN, full, static, and repetition gates before any formal run**

No Docker, OpenSandbox, service, acceptance, soak, or QEMU formal command may run
until all commands below are green:

```powershell
python -m pytest tests/sandbox_broker/test_formal_gate_state.py -q -p no:cacheprovider
python -m pytest tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_formal_gate_state.py tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider
1..10 | ForEach-Object { python -m pytest tests/sandbox_broker/test_formal_gate_state.py tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider; if ($LASTEXITCODE -ne 0) { throw "formal gate repetition failed at iteration $_" } }
python -m pytest tests/sandbox_broker -q -p no:cacheprovider
python -m compileall -q src scripts
python scripts/validate_opensandbox_deployment.py --static
git diff --check
```

Run the full failpoint matrix in both legacy-first and generation-to-generation
modes. The test report must show scientific soak dispatch `<= 1`, logical rollback
`<= 1`, no duplicate claim, no stale CAS success, and no export-driven branch.

- [ ] **Step 10: Obtain independent review and commit Task 7 before formal execution**

Request independent specification and implementation-quality reviews over the
complete Task 7 diff. Both reviewers must explicitly check journal filesystem
security, schema constraints, CAS transitions, the transition/recovery table,
claim-file/DB gap, result-export gap, rollback physical/DB gap, resolution crash,
poison persistence, concurrent gates, immutable releases, environment isolation,
manifest equality, external outputs, phase contracts, and old-report retention.
No Critical or Important finding may remain.

```powershell
git add -- src/sandbox_broker/formal_gate_state.py scripts/opensandbox_formal_runtime.py scripts/run_opensandbox_formal_qemu_gate.py scripts/run_opensandbox_docking_acceptance.py scripts/run_opensandbox_stability_soak.py scripts/validate_opensandbox_deployment.py deployment/opensandbox/activate-runtime-generation.sh deployment/opensandbox/install.sh deployment/opensandbox/medchat-sandbox-broker.service deployment/medchat-temporal-worker.service deployment/opensandbox/README.md tests/sandbox_broker/test_formal_gate_state.py tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_formal_qemu_gate.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py
git diff --cached --check
git commit -m "feat: add opensandbox formal gate journal"
git rev-parse HEAD
```

The resulting immutable full commit becomes `RELEASE_COMMIT`. Do not amend it.

- [ ] **Step 11: Prepare and invoke one reviewed QEMU formal gate**

On the snapshotted Ubuntu QEMU host, prepare a root-owned clean source checkout,
the pre-gate QEMU snapshot identifier, and a root-owned mode-0600 operator evidence
file. Preparation may not stage, capture, activate, validate, claim, run workers,
or roll back. Then invoke only:

```bash
set -euo pipefail
SOURCE_REPOSITORY=/usr/local/src/medchat-release
RELEASE_COMMIT="$(git -C "$SOURCE_REPOSITORY" rev-parse HEAD)"
test "${#RELEASE_COMMIT}" -eq 40
test -z "$(git -C "$SOURCE_REPOSITORY" status --porcelain)"
: "${QEMU_SNAPSHOT_ID:?required}"
: "${QEMU_OPERATOR_EVIDENCE_FILE:?required}"
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

The operator is the only caller of the formal gate. It never calls activation
primitives, acceptance, soak, or resolution commands directly. A second call for
the same commit can only verify the journal/claim/export and return the stable
already-terminal, resolution-required, or poison code; it never reruns science.

After an independently restored QEMU snapshot, a separate reviewed operator
archival procedure may close services, hash the old DB and its filesystem metadata,
copy it into immutable incident evidence, create a fresh secured DB, and record
the old/new DB hashes in an external operator log. The gate itself has no archive,
delete, truncate, replace, clear-poison, or snapshot-restore command.

### Task 8: Record authoritative evidence and promotion status

**Files:**
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Read the journal as the authority**

Task 8 opens `/var/lib/medchat-runtime-generation/formal-gate.sqlite3` read-only,
verifies the trusted root-owned path, SQLite schema/integrity, row constraints,
transition audit continuity, and the requested full-commit row. It records:

- release commit, mode, authoritative state, stable code, row version, and update
  timestamp;
- canonical release/previous target identifiers and snapshot/manifest hashes;
- claim hash, report hash/status, rollback receipt hash/verified flag, and any
  resolution/evidence hashes;
- external formal report path/hash, total status, pass/partial/fail counts, pass
  rate, p50/p95 and phase p95, failure distributions, event completeness, retry
  evidence, cleanup, Vina/Meeko versions, image digest, gVisor, artifact checks,
  probes, zero-leak count, and Broker/worker PID cwd attestation.

Promotion is allowed only for `SETTLED_PASSED` with all linked evidence verified.
`SETTLED_FAILED`, `RESOLUTION_REQUIRED`, `RESOLVING`, and `POISONED` block
promotion. Do not fabricate a scientific status for a manually resolved crash.

State exactly:

```text
Evaluator correction does not imply runtime stability.
Promotion is allowed only when the one formal report passed every gate.
A failed formal report is retained and blocks promotion.
```

- [ ] **Step 2: Verify the JSON export without treating it as state**

`/var/lib/medchat-opensandbox-runs/results/$RELEASE_COMMIT/gate-result.json` is a
root-owned mode-0600, atomic, redacted projection that can be rebuilt from the DB.
Task 8 compares it with the authoritative row and linked external report. Missing
or stale export is reported as an export defect; it never changes settlement,
authorizes a rerun, selects rollback, or blocks DB-based incident recovery.

Task 8 is read-only. It must not activate, verify, restart, stop, roll back,
resolve, recreate a claim, rerun acceptance/soak, clear poison, archive/replace the
DB, or repair the export. For a failed settlement, record only the DB-verified
rollback mode, receipt and physical attestation. For manual resolution, record
claim/evidence/resolution/receipt hashes and that no scientific result was
created. For poison, record the required QEMU snapshot and independent operator
archival procedure; do not attempt it from Task 8.

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
- One root-only non-blocking `flock` covers source/state checks, SQLite journal
  creation/validation and recovery, explicit incomplete-claim resolution,
  stage/capture, activation/verify, validation, acceptance, claim, soak, journal
  settlement, export, and any rollback verification. Lock contention and trusted
  settled-row refusals do not mutate runtime and never invoke rollback.
- Linux runtime validation and real acceptance pass on the activated immutable generation.
- Exactly one new formal 30-job QEMU soak is attempted per full commit, enforced
  by a permanent root-only O_EXCL claim created immediately before the command;
  its report is retained and hashed when produced even if the command exits
  non-zero, and a post-claim crash never authorizes a rerun.
- `/var/lib/medchat-runtime-generation/formal-gate.sqlite3` is the only
  authoritative transaction, settlement, resolution, and poison store. Its
  root-only path, DELETE journal mode, FULL synchronous mode, foreign keys,
  `BEGIN IMMEDIATE`, schema constraints, row-version CAS, and parent fsync are
  tested. Claim files are permanent audit anchors; result/poison JSON files are
  reconstructable exports and never select a recovery branch.
- The closed states are `STAGED`, `ACTIVATING`, `ACTIVE_PRECLAIM`, `CLAIMED`,
  `ROLLING_BACK`, `RESOLUTION_REQUIRED`, `RESOLVING`,
  `SETTLED_PASSED`, `SETTLED_FAILED`, and `POISONED`. Every state/evidence
  change is one legal expected-state/version CAS. Settled A permits corrected B
  in both legacy-first and generation-to-generation paths.
- A claim without a trusted report/failure envelope reaches
  `RESOLUTION_REQUIRED`; ordinary gates make zero runtime mutations. The
  explicit root-only hash-bound resolution command retains the claim, proves
  stopped work and preserved evidence, commits `RESOLVING` before rollback,
  and settles `SETTLED_FAILED` only with a verified receipt. An inherited
  `RESOLVING` that cannot prove physical completion becomes `POISONED`; no
  second rollback is attempted.
- The report truthfully records latency, failures, events, cleanup, provenance, artifacts, probes, and zero-leak state.
- Any retry, scientific timeout, invalid output, cleanup uncertainty, dependency failure, probe failure, release mismatch, virtualization mismatch, or residual container prevents promotion.
- First migration stages before capture, safely reuses only an unchanged
  `STAGED` journal snapshot, and can atomically restore the exact legacy units, service
  states, absent `current`, unit hashes, and legacy PID cwd without staging the
  old commit. Later rollback uses the active journal row's transaction-local
  `previous_target`, not long-term `previous`. Both modes persist `ACTIVATING`
  before live mutation, reconcile inherited `ACTIVATING`/`ACTIVE_PRECLAIM`
  through physical attestation, refuse `RESOLUTION_REQUIRED` pending the
  explicit resolution path, invoke at most one logical rollback,
  and verify restored PID cwd. Existing immutable releases are reused only after
  exact read-only attestation; any mismatch is `release_stage_conflict` with zero
  mutation.
  Stage/capture/conflict failures before live mutation do not roll back. QEMU
  snapshot restoration is required when rollback fails or physical completion
  cannot be proven; the journal persists `POISONED` as the authority and a
  marker is optional. Only independent QEMU snapshot recovery and separately
  reviewed operator archival may hash/archive the old DB and initialize a new
  secured DB; the gate never deletes, rewrites, or clears historical journal,
  claim, report, or export evidence.
- `docs/handoff/latest.md` distinguishes evaluator correctness from runtime stability and records either promotion evidence or rollback evidence.
