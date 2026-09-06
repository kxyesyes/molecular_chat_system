# OpenSandbox Real Runtime Stability Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the project-controlled causes behind the preserved real OpenSandbox run's 5 provider/readiness failures, 5 scientific-command timeouts, 2 invalid outputs, probe-timing failures, and approximately 50-second image-inspection delay without weakening scientific truth, cleanup, provenance, release identity, or promotion gates.

**Architecture:** Keep the corrected soak evaluator as the evidence judge. Remediate sanitized telemetry, bounded control-plane retries, image-readiness caching, phase budgets, atomic scientific output, and deterministic probes first. Then a single tested root orchestrator acquires a whole-transaction non-blocking lock, stages the reviewed commit as a root-owned immutable runtime generation, and atomically activates and verifies both Broker and Temporal worker through `/opt/medchat/current`. A root-only SQLite journal on a separately attested formal-authority block volume is the sole authority for formal-gate state, settlement, resolution, and poison; the permanent O_EXCL full-commit claim remains an immutable audit anchor, while `gate-result.json` is only a reconstructable redacted export. That authority volume is excluded from QEMU OS/runtime snapshot rewind, so journal, claim, sealed report, and attempt-tombstone truth cannot disappear during recovery. The same orchestrator owns privileged validation, isolated non-root transient acceptance/soak, private output mount namespaces, daemon-resource observation, pre/post manifests, evidence hashing, journal transitions, and at most one attested rollback; operators never call activation primitives or formal workers directly.

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
| `scripts/attest_opensandbox_formal_authority_boot.py` | Read-only authority-volume/journal attestation and current-boot token creation/verification. |
| `scripts/medchat_formal_authority_exec_wrapper.py` | Root-owned protected-service start boundary; maps a closed systemd unit set to hash-attested executable/argv/cwd/environment/identity, revalidates authority under the shared lifecycle lock, drops privileges, and directly execs without a shell or caller argv. |
| `scripts/validate_opensandbox_deployment.py` | Static/runtime release, unit, image, socket, network, and container checks. |
| `deployment/opensandbox/activate-runtime-generation.sh` | Future root-only generation primitive invoked by the formal orchestrator for staging, activation, verification, and one mode-specific rollback; it is not an operator formal entry. |
| `deployment/opensandbox/medchat-formal-authority-*` | Pre-snapshot systemd attestation service/target, root ExecCondition, guarded effective ExecStart, diagnostics-only generator, and static fail-closed cold-boot interlock. |
| `deployment/opensandbox/systemd/*/10-formal-authority.conf` | Static fail-closed drop-ins for Broker, Temporal worker, OpenSandbox daemon, and both automatic formal-gate service/timer units. |
| `deployment/opensandbox/opensandbox-server-0.2.2-formal-label.patch` | Minimal reviewed daemon-side create-time mapping for the reserved formal-run label. |
| `deployment/opensandbox/opensandbox-server-formal-label-lock.json` | Pinned server source/version, reviewed artifact/wheel hash, and protected-transport deployment identity. |
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
- Modify: `src/sandbox_broker/models.py`
- Modify: `src/sandbox_broker/opensandbox_client.py`
- Modify: `src/sandbox_broker/service.py`
- Modify: `scripts/opensandbox_formal_runtime.py`
- Modify: `scripts/run_opensandbox_docking_acceptance.py`
- Modify: `scripts/run_opensandbox_stability_soak.py`
- Modify: `scripts/validate_opensandbox_deployment.py`
- Modify: `deployment/opensandbox/activate-runtime-generation.sh`
- Modify: `deployment/opensandbox/install.sh`
- Modify: `deployment/opensandbox/medchat-sandbox-broker.service`
- Modify: `deployment/medchat-temporal-worker.service`
- Create: `scripts/attest_opensandbox_formal_authority_boot.py`
- Create: `scripts/medchat_formal_authority_exec_wrapper.py`
- Create: `deployment/opensandbox/medchat-formal-authority-generator`
- Create: `deployment/opensandbox/medchat-formal-authority-attestation.service`
- Create: `deployment/opensandbox/medchat-formal-authority-attested.target`
- Create: `deployment/opensandbox/medchat-formal-authority-exec-condition`
- Create: `deployment/opensandbox/medchat-opensandbox-formal-gate.service`
- Create: `deployment/opensandbox/medchat-opensandbox-formal-gate.timer`
- Create: `deployment/opensandbox/systemd/medchat-sandbox-broker.service.d/10-formal-authority.conf`
- Create: `deployment/opensandbox/systemd/medchat-temporal-worker.service.d/10-formal-authority.conf`
- Create: `deployment/opensandbox/systemd/medchat-opensandbox.service.d/10-formal-authority.conf`
- Create: `deployment/opensandbox/systemd/medchat-opensandbox-formal-gate.service.d/10-formal-authority.conf`
- Create: `deployment/opensandbox/systemd/medchat-opensandbox-formal-gate.timer.d/10-formal-authority.conf`
- Create: `deployment/opensandbox/opensandbox-server-0.2.2-formal-label.patch`
- Create: `deployment/opensandbox/opensandbox-server-formal-label-lock.json`
- Modify: `deployment/opensandbox/README.md`
- Modify: `tests/sandbox_broker/test_deployment_assets.py`
- Modify: `tests/sandbox_broker/test_opensandbox_client.py`
- Modify: `tests/sandbox_broker/test_service.py`
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

That path is a fixed root-only bind mount into the separately provisioned formal
authority block volume, not storage on the QEMU OS/runtime disk. The same volume
is mounted only at `/mnt/medchat-formal-authority` as `root:root 0700`; its
descriptor-verified `journal/` and `runs/` subtrees are fixed root-only bind mounts
at `/var/lib/medchat-runtime-generation` and
`/var/lib/medchat-opensandbox-runs`, respectively. Claims and results are fixed
descendants of the latter. Before opening the journal, every
formal or resolution entry must attest the expected QEMU drive serial, filesystem
UUID, mount source `major:minor`, read/write and hardening mount options, fixed
bind-mount targets, and an explicit snapshot-exclusion policy from the canonical
QEMU snapshot evidence. Missing storage, an empty replacement filesystem, an
identity/options mismatch, a bind mount backed by the OS/runtime disk, or a volume
included in the rewind set fails closed before journal, output, service, or runtime
mutation. The authority mount root and host-visible bind parents remain
`root:root 0700`; no non-root actor traverses them.

Test all of the following before implementation:

- the process must be root and the fixed parent must be a root-owned, non-symlink
  directory with mode `0700`;
- the database must be a root-owned, non-symlink regular file with mode `0600`;
- every path component is opened/verified without following symlinks; a symlink,
  non-root owner, wrong type, group/other writable component, or alternate DB path
  fails closed before journal mutation;
- the fixed authority mount and both bind targets resolve to the attested drive
  serial/filesystem UUID/`major:minor`, required mount options, and QEMU block
  node; an OS-disk-backed bind, missing/empty/substituted volume, or evidence that
  includes that node in the snapshot device set fails before DB creation/open;
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

The schema has one current row per full commit, one singleton live-baseline row,
an append-only transition audit table, and an append-only live-baseline audit
table. Use strict tables when supported by the pinned SQLite version and explicit
`CHECK`, `NOT NULL`, `UNIQUE`, and foreign-key constraints in all cases. The
current row includes at least these real columns (none may exist only as prose or
inside an opaque JSON blob):

```text
schema_version
release_commit                  # 40 lowercase hex, primary key
mode                            # legacy | generation
state
release_target                 # canonical in-root release identifier
previous_target                # canonical in-root target or explicit absent sentinel
legacy_snapshot_sha256
generation_snapshot_sha256
qemu_snapshot_id               # normalized safe ASCII identifier, 1..128 chars
qemu_snapshot_evidence_sha256
authority_drive_serial         # normalized bounded device serial
authority_filesystem_uuid      # canonical lowercase filesystem UUID
authority_device_major_minor   # canonical decimal major:minor
authority_mount_options_sha256 # canonical required/forbidden option set
authority_exclusion_sha256     # snapshot-exclusion policy + QEMU device graph
pre_manifest_sha256
post_acceptance_manifest_sha256
acceptance_seal_sha256         # canonical root-only acceptance-tree seal receipt
post_soak_manifest_sha256
claim_sha256
claim_relative_name
claim_expected_row_version      # ACTIVE_PRECLAIM version frozen in claim payload
run_relpath                    # unique, safe, commit-bound path below output base
report_relpath                 # unique safe descendant of run_relpath
report_sha256
report_status                  # passed | failed | absent
report_seal_sha256             # canonical root ownership/mode/path seal receipt
failure_envelope_sha256
physical_attestation_sha256
actor_unit                     # exact transient .service unit or NULL
actor_kind                     # closed allowlist or NULL
actor_cgroup                   # exact expected systemd cgroup or NULL
actor_invocation_id            # unique 32-char lowercase hex process identity
actor_identity_sha256          # canonical command/release/environment identity
actor_mount_sha256             # exact source/destination/rw/private-mount tuple
actor_run_label                # bounded unique commit+run resource label
observer_baseline_sha256       # root observer inventory before dispatch
observer_post_sha256           # root observer zero-delta inventory after exit
actor_cleanup_receipt_sha256   # cgroup + daemon-resource zero-live receipt
actor_stop_intent              # 0 | 1
actor_stop_receipt_sha256
poison_shutdown_receipt_sha256 # same-state worker/Broker/actor shutdown evidence
rollback_receipt_sha256
rollback_verified              # 0 | 1
resolution_evidence_sha256
resolution_record_sha256
stable_code
created_at_utc
updated_at_utc
row_version
```

`qemu_snapshot_id` is normalized by rejecting Unicode, leading/trailing
whitespace, and alternate encodings, then requiring the exact ASCII expression
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. It is compared byte-for-byte after
normalization. Targets, claim names, and output locators are canonical identifiers
below fixed trusted roots, not arbitrary host paths. `run_relpath` has the exact
form `$FULL_COMMIT/$UNIQUE_RUN_ID`; `report_relpath` is the fixed descendant
`$RUN_RELPATH/soak/report.json`. Acceptance output is confined to the separate
`$RUN_RELPATH/acceptance/` subtree and cannot create or replace the soak report.
Both locators are bound to `release_commit`, reject absolute paths,
empty/dot/dot-dot components and symlinks, and are independently `UNIQUE` so a
different transaction can never adopt them. Hashes are lowercase SHA-256 or
NULL. `stable_code` is from a closed allowlist; raw exception text, response
bodies, credentials, sandbox IDs, host-user paths, and out-of-root paths are
forbidden.

`post_acceptance_manifest_sha256` and `acceptance_seal_sha256` are both NULL until
the legal `acceptance_sealed` mutation and both non-NULL afterward. Any row with a
non-NULL `claim_sha256`, including `CLAIMED` and every post-claim terminal/recovery
path, requires both fields and requires the permanent claim to bind the exact seal
hash. No separate file or process-local flag may satisfy these constraints.

The five authority-volume fields are immutable after the initial `STAGED` insert
and are bound into the QEMU snapshot evidence, first audit, claim, report seal,
resolution authorization, and every physical attestation. The canonical
snapshot-exclusion record proves that the authority device is a distinct QEMU
block node and is absent from the named OS/runtime snapshot's participating device
set. A path or UUID alone is insufficient identity. Fixed root-only bind mounts may
preserve the public paths above, but descriptor and mount-table verification must
prove their backing device is the attested authority volume on every entry.

The actor ownership tuple (`actor_unit`, `actor_kind`, `actor_cgroup`,
`actor_invocation_id`, `actor_identity_sha256`, and `actor_stop_intent`) is
all-NULL when no external actor is owned. While an actor is bound, all ownership
columns are non-NULL. Acceptance/soak additionally require
`actor_mount_sha256`, `actor_run_label`, and `observer_baseline_sha256` at bind;
their post-observation and cleanup receipt remain NULL until proved. Other actor
kinds require the mount/observer fields to be NULL. Actor clear NULLs only the
live ownership fields after persisting the immutable post-observation and cleanup
receipt; the audit retains the full prior tuple. `actor_unit` is a bounded safe systemd service name
derived from the full commit, row version, and actor kind; `actor_kind` is exactly
one of `activation`, `root_validator`, `socket_probe`, `acceptance`, `soak`,
`rollback`, or `resolution_rollback`; `actor_cgroup` is the deterministic
`/system.slice/$ACTOR_UNIT` expected before dispatch. `actor_invocation_id` is a
preallocated non-secret `[0-9a-f]{32}` value included in the unit's closed
environment and wrapper process; it avoids PID-reuse ambiguity and lets root
verify the exact process family before trusting `MainPID` or cgroup observations.
The identity hash binds that invocation ID, exact executable, immutable release,
sanitized argv, closed environment-key allowlist, run/report locators, user/group,
timeout, mount mapping, run label, observer baseline, and phase prerequisites. A
soak actor additionally binds the frozen non-NULL `acceptance_seal_sha256` as a
precondition but receives no acceptance file, directory, descriptor, or mount.
`actor_mount_sha256`
binds a closed canonical ordered list of every descriptor-resolved source locator,
fixed namespace destination, read-only or read/write mode, `PrivateMounts=yes`, and
the complete mount-namespace policy. The ordered list is actor-kind-specific:
acceptance receives only its acceptance subtree read/write; soak receives only its
soak subtree read/write. The soak workflow depends only on immutable release code,
fixed scientific inputs, its closed configuration, and the journaled acceptance
seal prerequisite; it never consumes acceptance contents. `actor_run_label` is a non-secret bounded label derived
as lowercase SHA-256 over the domain-separated canonical full-commit and unique
run-ID tuple. It is injected exactly as `medchat.formal_run=$ACTOR_RUN_LABEL` into
both OpenSandbox and Docker resource metadata and accepts only `[0-9a-f]{64}`;
raw sandbox/container IDs never enter the journal. No secret value enters any
tuple or the journal.

The transition audit binds `release_commit`, mutation kind, old/new states,
old/new row versions, stable code, actor tuple, evidence hashes, and timestamp to
the current row through a foreign key. It contains no second writable settlement
truth. Same-state evidence mutations are legal only for the closed mutation kinds
`actor_bound`, `actor_stop_intent`, `actor_zero_live_attested`, `actor_cleared`,
`acceptance_sealed`, `report_sealed`, `poison_actor_stop_intent`,
`poison_actor_zero_live_attested`,
`poison_actor_cleared`, and `poison_shutdown_receipt`; they use the same
expected-state/version CAS and audit rules as state changes. The four
`poison_*` mutations require both expected and next state `POISONED`, are strictly
monotonic safety cleanup, and cannot change scientific, activation, rollback,
settlement, claim, report, baseline, or resolution fields. All other same-state
updates are forbidden. `acceptance_sealed` is legal only as
`ACTIVE_PRECLAIM -> ACTIVE_PRECLAIM`, requires the acceptance actor tuple already
cleared with a verified cgroup-plus-daemon zero-live receipt, derives the exact
post-acceptance manifest and seal in one canonical receipt, and changes only
`post_acceptance_manifest_sha256`, `acceptance_seal_sha256`, `updated_at_utc`, and
`row_version`. A second different
seal, a seal recorded in any other state, or a seal before zero-live proof is
illegal; exact already-recorded re-entry is read-only rather than another audit.

The singleton table is a real schema object, not a derived query:

```text
live_baseline
  singleton_id                 # INTEGER PRIMARY KEY CHECK(singleton_id = 1)
  release_commit               # nullable FK until first passed generation
  release_target               # nullable canonical generation identifier
  physical_attestation_sha256  # nullable only when no baseline exists
  source_gate_row_version
  row_version
  updated_at_utc
```

At most one row exists. Its fields are either all baseline-NULL or identify one
`SETTLED_PASSED` generation. The append-only baseline audit records every pointer
change and its source gate row/version. Historical settled rows are immutable
evidence history; they do not become a second live pointer.

`failure_envelope_sha256` and `resolution_record_sha256` are hashes of
versioned canonical, sanitized tuples whose bounded fields are stored in the same
current row and audit entry; they are not hashes of separate state JSON files.
`rollback_receipt_sha256`, report hashes, snapshot hashes, and operator-evidence
hashes bind immutable external evidence, but only the journal row decides state.

The permanent claim payload is a strict closed canonical JSON object with exactly
these keys and no others:

```text
schema_version                       # integer 1
release_commit                       # exact journal primary key
claim_expected_row_version           # ACTIVE_PRECLAIM version at O_EXCL creation
release_target                       # exact frozen journal target
run_relpath                          # exact frozen journal locator
report_relpath                       # exact frozen journal locator
acceptance_seal_sha256               # exact frozen ACTIVE_PRECLAIM seal receipt
qemu_snapshot_id                     # exact normalized journal value
qemu_snapshot_evidence_sha256        # exact frozen journal hash
authority_drive_serial               # exact frozen journal value
authority_filesystem_uuid            # exact frozen journal value
authority_device_major_minor         # exact frozen journal value
authority_mount_options_sha256       # exact frozen journal hash
authority_exclusion_sha256           # exact frozen journal hash
```

Serialization is UTF-8 without BOM, keys sorted by their UTF-8 bytes, compact
separators `,` and `:`, JSON integer syntax for the two numeric values, JSON string
escaping with ASCII-only `\uXXXX` escapes where required, and no trailing newline
or insignificant whitespace. The parser rejects unknown, missing, duplicate, or
out-of-order keys, non-canonical escapes/numbers, invalid UTF-8, BOMs, partial
writes, trailing bytes, and any bytes that do not equal a reserialization of the
validated object. Creation is descriptor-confined with
`O_CREAT|O_EXCL|O_NOFOLLOW`, root ownership and mode `0600`, then file fsync and
verified parent fsync.

For the `ACTIVE_PRECLAIM` claim-file/DB gap, recovery does not trust fields read
from the claim. It freezes the current journal row, requires
`claim_expected_row_version == row_version`, requires a non-NULL
`acceptance_seal_sha256`, reconstructs the one canonical byte sequence above from
that row, including that seal and all five frozen authority identity fields,
and compares both bytes and SHA-256 with the existing file. An exact match CASes
to `RESOLUTION_REQUIRED` while recording the hash and
expected version; it never dispatches science. A stale version, locator/target,
snapshot/evidence or acceptance-seal mismatch, any one-field authority serial/filesystem UUID/
`major:minor`/mount-options/exclusion mismatch, unknown field,
partial/non-canonical file,
or hash mismatch CASes to `POISONED` when the trusted journal is writable and then
refuses. If even that poison CAS cannot be made safely, it returns a stable
fail-closed refusal. It never deletes, repairs, replaces, or reruns around the
claim.

The first row is not a transition and must never be synthesized through an UPDATE.
It is created only by this dedicated API:

```python
def insert_staged_if_absent(
    conn: sqlite3.Connection,
    *,
    staged: NewStagedRecord,
) -> FormalGateRecord:
    """Atomically create STAGED row_version=1 and its first audit, or verify identity."""
```

It starts `BEGIN IMMEDIATE`, validates all immutable staged fields (including
snapshot ID/evidence, output locators, release/previous target and snapshots), and
uses one `INSERT ... SELECT ... WHERE NOT EXISTS` keyed by the full commit. If one
row is inserted, it appends the initial `created -> STAGED`, `0 -> 1` audit entry
in the same transaction and commits once. If a row already exists, the helper
re-reads it in that transaction: a byte-for-byte identical complete staged record
is an idempotent read that performs no UPDATE, increments neither `row_version`
nor audit count, and returns the frozen existing row; any differing field is
`formal_gate_stage_conflict` with no mutation. Independent-connection and
multiprocess tests race identical and conflicting inserts and prove exactly one
row/initial audit and stable conflict refusal.

All later mutations use one API that performs an expected-state and
expected-version compare-and-swap:

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
both advance the same transaction. The only multi-table settlement is successful
promotion: after current physical attestation, the same `BEGIN IMMEDIATE`
transaction performs the `CLAIMED -> SETTLED_PASSED` CAS, updates or creates
`live_baseline(singleton_id=1)` with its own version check, and appends both audits
before one commit. A baseline conflict rolls back the entire settlement.

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
illegal. “May mutate” means live runtime mutation, not the SQLite CAS itself.

| State | Allowed next | Entry recovery | May mutate runtime? |
|---|---|---|---|
| `STAGED` | `ACTIVATING`, `SETTLED_FAILED`, `POISONED` | Re-attest the immutable release and captured legacy/generation snapshot. If exact, the same invocation may continue; mismatch poisons. | No |
| `ACTIVATING` | `ACTIVE_PRECLAIM`, `ROLLING_BACK`, `POISONED` | Activation may have started. Attest `current`, both service PID cwd values, unit hashes, and the transaction-local previous/snapshot. Exact active target may finish verify; exact previous target enters deterministic failure settlement; ambiguity poisons. | Activation/verify or one attested rollback |
| `ACTIVE_PRECLAIM` | `CLAIMED`, `ROLLING_BACK`, `RESOLUTION_REQUIRED`, `POISONED` | Re-attest active target and manifests. If root sealing completed but `acceptance_sealed` did not commit, recovery may descriptor-revalidate that exact tree and perform only the same-state seal CAS; a writable, ambiguous, or mismatched tree cannot be accepted. A matching permanent claim with no DB hash is the claim-file/DB gap and must CAS to `RESOLUTION_REQUIRED`; it must never rerun acceptance/soak. With no claim, only the original locked invocation may run acceptance; an inherited process otherwise follows pre-claim rollback/recovery and never repeats an actor whose execution is not proved absent. | Acceptance before claim only in the owning invocation; seal reconciliation or one attested rollback during recovery |
| `CLAIMED` | `SETTLED_PASSED`, `ROLLING_BACK`, `RESOLUTION_REQUIRED`, `POISONED` | A trusted report may settle; a trusted failure envelope may enter rollback; no report/envelope is a post-claim crash and enters `RESOLUTION_REQUIRED`. | No scientific rerun; rollback only through the declared path |
| `ROLLING_BACK` | `SETTLED_FAILED`, `POISONED` | Verify DB evidence, then attest physical runtime. If still exactly on target, dispatch the one idempotent attested rollback; if already exactly previous, write/verify the receipt and settle without dispatch; any mixed/unknown state poisons. | At most one logical rollback |
| `RESOLUTION_REQUIRED` | `RESOLVING`, `POISONED` | Ordinary gate refuses with zero runtime mutation. Only the explicit resolution CLI may proceed after evidence and stopped-process checks. | No |
| `RESOLVING` | `SETTLED_FAILED`, `POISONED` | If physical attestation proves exact previous target and rollback completion, verify/write the receipt and settle without another rollback. If completion cannot be proved, poison and require QEMU snapshot recovery; never dispatch rollback twice. | No repeat rollback |
| `SETTLED_PASSED` | none | Verify the historical row/audit and frozen claim, sealed report, manifest, attestation, and evidence hashes. Check current symlink/PIDs only if `live_baseline` points to this row; otherwise never compare this historical row with current physical runtime. | No |
| `SETTLED_FAILED` | none | Verify the applicable historical row/audit and frozen pre-live, normal-failure, or manual-resolution evidence hashes. Never compare a historical failed row with current symlink/PIDs; current physical truth comes only from the active transaction or `live_baseline`. | No |
| `POISONED` | none | Permanently refuse science, activation, rollback, settlement, resolution, and new gates. The only legal same-state work is the audited monotonic shutdown/zero-live cleanup below; afterward return `qemu_snapshot_recovery_required`. | Shutdown only; never business/runtime progression |

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

`POISONED` therefore has one narrow safety exception to “no mutation.” A recovery
entry may only: persist `poison_actor_stop_intent`; perform the journal-bound
actor's single stop/wait; independently attest cgroup, process, sandbox, and
container zero-live; clear that actor with a cleanup receipt; stop/disable worker
then Broker; and persist `poison_shutdown_receipt_sha256`. Every operation is a
same-state expected-version CAS with one of the closed `poison_*` mutation kinds.
It cannot alter the claim, report, scientific status, live baseline, rollback,
resolution, or authority identity, and it never leaves `POISONED`. Success still
returns `qemu_snapshot_recovery_required`. Failure, ambiguity, or another crash
retains the actor and poison state, records only monotonic evidence that was proved,
and refuses without a new actor, rollback, settlement, or gate.
`poison_shutdown_receipt_sha256` hashes a closed canonical receipt containing the
poison row/version, actor cleanup receipt when applicable, exact worker/Broker unit
identities, inactive/disabled/MainPID/cgroup observations, independent daemon
observer post hash, and authority-volume identity. It is not a generic “command
returned zero” flag.

Before applying any entry-recovery row, inspect its actor tuple. A non-NULL actor
always takes precedence over state-specific activation, settlement, rollback, or
new-gate logic. The recovering orchestrator must independently verify that the
persisted unit name, actor identity, and systemd `ControlGroup` match the expected
cgroup, and every live member matches the persisted invocation ID/identity. If the
actor is active, it first CASes `actor_stop_intent=1`, then issues
exactly one idempotent `systemctl stop $ACTOR_UNIT`, waits once for `inactive`, and
independently requires `MainPID=0`, an empty/absent `ControlGroup`, and an empty or
absent descriptor-opened `cgroup.procs`. It also verifies that no process with the
persisted actor identity remains. For acceptance and soak it must additionally
verify the independent daemon-resource contract below. Only then may a CAS store
the zero-live/cleanup receipt, clear the actor tuple, and continue state recovery.
If stop fails, times out, the cgroup is non-empty, the unit/cgroup/identity differs,
a relevant daemon resource remains, or any observation is unavailable or
contradictory, CAS to `POISONED`. The POISONED safety exception may subsequently
resume only an unperformed stop/proof step under the existing durable intent; it
never issues a second stop after an attempted stop and may not continue the
original state recovery. Never roll back, settle, dispatch another actor, or admit
a new gate while an actor or daemon resource may still be alive.

There is no stop loop. A recovery entry dispatches stop/wait at most once. If it
crashes after durable stop intent but before a durable zero-live receipt, the next
entry may clear without another stop only when all independent zero-live checks
pass; if the actor remains live or completion is ambiguous, it poisons instead of
issuing a second stop. A cleanly exited actor is cleared only after the same
zero-live checks and its result/evidence is reconciled from the exact journal-bound
paths. The whole-gate flock serializes all of this, but actor safety never assumes
that the parent process still holds the lock.

Stopping a client transient unit is not proof that OpenSandbox/Docker daemon-side
resources are gone. Before binding an acceptance or soak actor, an independent
root observer records a canonical sanitized baseline of both OpenSandbox and
Docker inventories and stores `observer_baseline_sha256`. The orchestrator injects
the unique `actor_run_label` through the Broker so every sandbox/container created
for that actor carries the same commit+run label; the label and observer contract
are part of `actor_identity_sha256`. Raw sandbox/container IDs are used only in
ephemeral root memory for cleanup and never stored in the journal, report, unit
description, or logs.

The Broker request model has one internal-only field,
`formal_run_label: str | None`. It accepts only the exact non-secret
`[0-9a-f]{64}` value already bound in the journal actor tuple. Public HTTP, job,
workflow, and user payload schemas do not expose it; if an unknown-field-tolerant
compatibility path receives `formal_run_label` or `medchat.formal_run`, it rejects
the request instead of stripping or forwarding it. Only the root formal gate may
inject the field through a protected internal invocation context bound to the
journal actor identity. The Broker passes it at create time with the pinned
OpenSandbox 0.1.15 call:

```python
Sandbox.create(
    ...,
    metadata={**trusted_metadata, "medchat.formal_run": formal_run_label},
)
```

There is no post-create metadata inspect/patch/update path. Task 7 must first prove
that pinned `opensandbox-server==0.2.2` maps this trusted metadata key during the
same backend `docker.containers.create(..., labels=...)` operation to the immutable
Docker label `medchat.formal_run`, while preserving the native
`opensandbox.io/id` label. The server accepts that reserved key only on a dedicated
loopback mTLS listener. Its root-provisioned Broker client-certificate fingerprint,
server certificate, listener address, and expected Broker systemd identity are
pinned in the deployment lock; the private key is readable only by the installed
Broker service identity and never enters a request, journal, report, unit
description, or log. The server accepts the key only after that client identity is
authenticated; its ordinary listener and public API return a stable rejection if
they receive it. If the current daemon cannot prove origin or atomic propagation,
Task 7 implements the minimal reviewed server-side
adapter in `deployment/opensandbox/opensandbox-server-0.2.2-formal-label.patch`,
builds and pins its wheel, records source/version/wheel SHA-256 plus deployment
settings in `opensandbox-server-formal-label-lock.json`, and installs only that
locked artifact. Formal mode is unavailable on an unpatched, hash-mismatched, or
origin-unprotected daemon.

`Sandbox.create()` may return only after both the OpenSandbox record metadata and
the Docker container labels independently expose the exact same persisted label.
Missing support, a mismatched value, partial visibility, or create-time ambiguity
fails closed and cannot enter a formal soak. The real non-scientific observer
preflight creates one short-lived labelled sandbox, proves both views agree before
create returns, cleans it through the exact label, and then proves both views have
zero matching resources and zero unexplained delta. In this contract
“zero-resource” means resources were created, observed, and returned to zero; it
never means the creation path was skipped.

Before actor clear, the root observer queries both systems independently and
requires: zero resources carrying the run label; zero unexplained baseline delta;
no unknown or unassignable resource in the formal-run scope; agreement between
OpenSandbox and Docker observations; and the cgroup/process checks above. It stores
the sanitized post inventory hash and one canonical
`actor_cleanup_receipt_sha256`. An empty cgroup with a labelled residual container
or sandbox, disagreement, inability to observe either daemon, a non-unique label,
or an unexplained resource poisons and blocks rollback, replacement actor, and new
gate. Cleanup may target only resources proven to carry the exact persisted label;
it must never sweep by name prefix or accept a baseline-only count as identity.

`live_baseline` is validated separately from historical rows. During an in-flight
generation, the active transaction row describes temporary physical mutation and
the baseline continues to identify the last passed generation. Failed rollback
must restore and attest that baseline (or the captured legacy state when no
baseline exists) without changing the pointer. A successful gate changes the
pointer only in the atomic passed-settlement transaction described above. A test
sequence A -> B -> C must prove that C alone receives current symlink/PID
attestation after C passes, while A and B remain independently verifiable from
their frozen rows/audits/evidence hashes even though neither matches current
runtime.

The superseded multi-file state vocabulary and its transaction, resolution,
settlement, and poison JSON authorities do not exist in the future design.

- [ ] **Step 3: Write RED failpoint and concurrency tests for the complete gate**

Create `tests/sandbox_broker/test_formal_qemu_gate.py`. Import pure helpers from
the future formal gate and inject the clock, filesystem, SQLite connection factory,
subprocess runner, service observer, physical attestor, and failpoint controller.
Do not monkeypatch global SQLite state or invoke real systemd/Docker in unit tests.

Parameterize a hard process-kill failpoint immediately before and after:

- every authoritative SQLite commit;
- the first `insert_staged_if_absent` INSERT and initial audit insert;
- database creation and parent-directory fsync;
- authority-volume, mount-table, fixed-bind, and snapshot-exclusion attestation;
- release staging/capture and canonical-manifest fsync;
- every actor-binding CAS, transient-unit dispatch, stop-intent CAS, stop/wait,
  cgroup zero-live verification, daemon-observer baseline/post observation,
  atomic create-time OpenSandbox/Docker label observation, actor-clear CAS,
  actor-kind-specific private-mount creation, and activation verification;
- acceptance dispatch, acceptance-subtree validation/fsync/root sealing,
  `acceptance_sealed` CAS, exact report-path publication, soak-subtree
  validation/fsync/root sealing, final run parent sealing, and report-sealed CAS;
- token-lifecycle flock acquisition/release, automatic-gate receipt and runtime-token
  revocation, each authorization CAS, post-CAS formal-token publication, settled/
  rollback runtime-token plus eligibility-receipt publication, formal-token
  revocation, every no-replace publication boundary, every token-directory fsync,
  protected-service `ExecCondition` success, guard-wrapper shared-lock acquisition,
  authority revalidation, identity reduction, and the final fixed `execve` boundary;
- the deliberately long first-mutation lifecycle-lock interval: settled/initial proof
  revocation, release stage/capture, output-locator binding, first `STAGED` INSERT,
  `STAGED -> ACTIVATING` actor CAS, formal-token publication/fsync, and lock release;
- permanent claim O_EXCL creation/fsync and the following DB CAS;
- the single 30-job scientific soak dispatch, report hashing/sealing, and report
  settlement;
- failure-envelope persistence;
- rollback dispatch and physical verification;
- resolution authorization CAS, rollback, and receipt verification;
- `SETTLED_PASSED`, `SETTLED_FAILED`, and `POISONED` commits;
- every POISONED same-state shutdown intent, actor cleanup, worker/Broker stop, and
  shutdown-receipt CAS;
- pre-restore full shutdown, authority flush/clean unmount, host-side evidence
  hash/backup when selected, disk-only OS/runtime rewind, cold boot, authority
  read-only remount/reattest, ephemeral boot-token publication, and gated service
  ExecCondition boundary;
- atomic `gate-result.json` export and parent fsync.

For every failpoint, restart through the public entry point and assert:

- scientific soak dispatch count is at most one for the full commit;
- successful logical rollback count is at most one;
- no crash window can move backward in the state graph;
- no crash window can turn failed/unknown evidence into passed;
- no ordinary retry can clear `RESOLUTION_REQUIRED` or `POISONED`;
- no JSON export, claim content, marker, or filesystem ordering can override DB;
- every refusal happens before live mutation; a locator already durably bound by
  `insert_staged_if_absent` is reused exactly and never reallocated or guessed;
- no rollback or new gate overlaps a persisted or possibly live external actor;
- a sealed report can settle after parent death without redispatching science.
- an OS/runtime rewind cannot remove or weaken the authority journal, claim,
  sealed report, poison, or permanent attempt tombstone.
- no protected service can start from a token published before its authorizing CAS,
  from a token whose state/row version/action/unit/generation is stale, or while a
  token/receipt publication or revocation is only partially durable;
- passing `ExecCondition` never reserves admission: the root guard wrapper must
  reject a queued start if a gate rotates the journal/capability before its shared-
  lock revalidation and fixed-command `execve`;
- while a live gate holds the first-mutation exclusive lifecycle lock, no settled
  publisher, initial-proof publisher, condition, or guard wrapper can republish or
  consume the old capability between revocation and durable `ACTIVATING` authority;
- concurrent token publishers never overwrite one another, and an unexpected
  destination is either proved byte-identical under the lifecycle lock or blocks.

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
    Two stale SQLite writers cannot both transition the same row. Concurrent
    identical first-stage inserts produce one row and one initial audit with
    `row_version=1`; conflicting first-stage inserts fail without mutation.
11. Legacy-first and generation-to-generation rollback each use only the current
    row's previous target and snapshot hashes.
12. Existing release reuse succeeds only after exact immutable attestation;
    conflict is a stable zero-mutation failure.
13. Kill the parent while each actor kind remains live. Recovery uses the exact
    persisted unit/cgroup/identity, performs one stop/wait sequence, proves
    `inactive`, `MainPID=0`, and empty `cgroup.procs`, clears the actor once, and
    only then continues. Stop failure, non-empty cgroup, identity mismatch, or an
    unobservable unit poisons; no rollback/new actor/new gate is dispatched.
14. A report is fully written and root-sealed but the process dies before its DB
    settlement. Recovery opens only `report_relpath` from the journal, validates
    its seal/hash/schema/artifacts, and settles without globbing, guessing, or a
    second soak. An unsealed, writable, symlinked, relocated, or hash-mismatched
    report cannot settle.
15. A passes, B passes, then C passes. `live_baseline` changes atomically with each
    passed settlement and current physical checks apply only to C. Historical A
    and B still validate from row/audit/frozen evidence without matching C's
    current symlink or PID cwd.
16. Manual resolution accepts only the exact normalized journal
    `qemu_snapshot_id`, QEMU identity, and evidence hash. Missing, malformed,
    alternate-case, wrong-ID, wrong-evidence, and evidence-for-a-different-VM
    inputs fail before resolution CAS or rollback.
17. Root creates host base/commit/run parents as `root:root 0700` and pre-creates
    distinct `acceptance/` and `soak/` actor subtrees. A real acceptance transient
    unit with `PrivateMounts=yes` receives only `acceptance/` read/write at the fixed
    output path; `soak/` is absent. After zero-live, root seals acceptance. A real
    soak unit receives only `soak/` read/write; the acceptance master and every
    acceptance input path are absent. Running both as the actual `medchat` UID proves
    they cannot cross-read/write actor-private subtrees, pre-create the soak report,
    see or alter sealed acceptance, traverse host parents, inspect another commit/run,
    create a sibling, or replace a parent. Wrong ordered source/destination,
    rw/ro flag, namespace policy, or mount hash refuses before dispatch.
18. Kill between claim O_EXCL/fsync and the claim CAS. Recovery reconstructs exact
    canonical bytes from `ACTIVE_PRECLAIM` and enters `RESOLUTION_REQUIRED` once.
    Partial JSON, duplicate/unknown/missing/out-of-order keys, stale expected row
    version, locator/target mismatch, snapshot/evidence or acceptance-seal mismatch, an independent
    mismatch in each of authority drive serial/filesystem UUID/`major:minor`/
    mount-options hash/exclusion hash, bad encoding, and non-canonical bytes
    poison/refuse before actor stop, rollback, or science and never rerun science.
19. Kill immediately before and after the POISONED commit while an actor, worker,
    or Broker is still live. Recovery performs only the same-state audited shutdown
    protocol, remains POISONED, and returns snapshot-required. Stop failure,
    contradictory identity, or an unproved receipt retains ownership and never
    permits rollback, settlement, a new actor, or a new gate.
20. Kill acceptance/soak after its client cgroup becomes empty while a labelled
    OpenSandbox sandbox or Docker container remains. The independent observers
    detect the residual resource, CAS POISONED, and do not clear the actor. Also
    reject missing labels, label collisions, observer disagreement, unavailable
    daemons, unexplained baseline delta, and unknown/unassignable resources.
21. Pass A, capture an OS/runtime-only snapshot, advance/poison B on the authority
    volume, then rewind the OS/runtime disks. After reattesting the same authority
    drive serial, filesystem UUID, `major:minor`, mount options, and exclusion
    policy, B's journal/claim/report/tombstone still exist and B is refused without
    a second attempt. An absent, empty, substituted, rewind-participating, or
    incorrectly mounted authority volume fails before Broker/worker/gate enable.
22. Historical A -> B -> C report and baseline behavior remains unchanged when all
    journal, claim, and sealed run evidence is reached through fixed root-only bind
    mounts backed by the authority volume.
23. The real pinned server creates one short-lived non-scientific sandbox through
    the protected Broker path. Before create returns, OpenSandbox metadata and the
    Docker immutable labels both contain the exact `medchat.formal_run` value and
    retain `opensandbox.io/id`; cleanup makes both observer views zero. Public API
    injection is rejected. Kill during create, missing server support, wrong origin,
    one-sided/mismatched labels, unknown delta, and post-create-only propagation all
    fail closed and keep formal soak disabled.
24. Build the snapshot baseline with Broker, Temporal worker, OpenSandbox daemon,
    and any automatic gate unit enabled, then perform the reviewed powered-off
    disk-only OS/runtime rewind. Before authority attestation there is no current-
    boot token and every relevant `ExecStart` is blocked. POISONED, in-flight,
    unsettled, wrong-volume, wrong-exclusion, and stale-token cases remain blocked;
    only read-only validation of the surviving settled baseline creates a token
    bound to the current kernel `boot_id`, authority identity, and journal
    state/version hash. RAM, vmstate, and device-state restore are rejected.
25. Kill before acceptance sealing, after the root-only seal but before
    `acceptance_sealed`, and immediately after that CAS. Recovery never trusts a
    writable tree, but may descriptor-reopen the exact journal-bound root-only tree,
    recompute its manifest and canonical ownership/mode/path receipt, and commit the
    one legal `ACTIVE_PRECLAIM` same-state mutation. Wrong seal, changed content,
    retained actor/resource, wrong manifest, or seal in another state refuses; an
    exact committed seal is idempotent and creates no second audit. The subsequent
    claim canonical bytes bind that exact seal hash.
26. With generator absent, non-zero, empty, or emitting only a subset of diagnostic
    coverage, boot an image whose Broker, worker, OpenSandbox daemon, automatic gate
    service, and timer are enabled. Static drop-ins alone block every service
    `ExecStart`; the timer cannot queue an unguarded service. Missing any static
    drop-in or changing its target/condition fails installation and validation.
27. Exercise token lifecycle failpoints for A -> B pass, B rollback -> A, and an
    inherited in-flight recovery. Mutation begins only after the automatic-gate
    receipt and A runtime token are durably removed. While the gate remains alive,
    it retains the exclusive lifecycle lock across stage/capture and the first INSERT,
    so no read-only publisher or protected start can restore/use A. Kill the gate
    after revocation but before the first INSERT: only after kernel lock release may
    settled read-only recovery restore the reattested original A capability pair,
    because no authoritative formal row exists. `STAGED` alone authorizes no
    protected service. Only after `STAGED -> ACTIVATING` commits may the gate publish
    `formal-new-run(action=activate_target)` for that exact new row version,
    generation, and unit allowlist. Kill after CAS/before publication and after
    publication/before dispatch: the former leaves services blocked and recovery
    reconstructs only the journal-justified token; neither window repeats the CAS.
    Every later row-version/state/action change rejects the old token. On failure,
    commit `ROLLING_BACK` with the rollback actor identity, durably revoke
    `formal-new-run`, and only then publish `formal-recovery` bound to that current
    `ROLLING_BACK` version, exact previous generation, and rollback-only unit/action
    allowlist. Normal rollback succeeds through this path and no scientific actor or
    new target can start. After physical attestation and settlement, read-only
    recovery publishes only the runtime token and automatic-gate receipt justified
    by the new/current baseline; it never changes settlement or reruns work.
28. Exercise `clean_initial_legacy` from a cold boot with correct authority identity,
    empty gate/audit history, no claim/tombstone/run, NULL `live_baseline`, exact
    reviewed legacy files/state, absent `current`, and zero Broker/worker/
    OpenSandbox/formal PIDs and cgroups. The boot proof alone starts nothing. The
    locked manual gate inserts the first `STAGED` row/audit, which still starts
    nothing; it then commits `STAGED -> ACTIVATING` with the activation actor before
    issuing one invocation-bound `formal-new-run(action=activate_target)` token for
    the resulting row version. Kill after proof, after `STAGED`, after `ACTIVATING`,
    and before/after token publication; recovery remains deterministic. Any residual
    row/audit/claim/tombstone/run, non-NULL baseline, changed legacy state, live
    process/cgroup, or reused proof refuses, and history can never become empty again.
29. Run concurrent cold-boot/read-only-recovery publishers and gate mutation under
    the fixed token-lifecycle flock. Publish runtime token first and automatic-gate
    receipt last from one frozen settled-baseline snapshot. A partial pair keeps the
    automatic service blocked and is completed idempotently only from identical
    canonical evidence. `renameat2(RENAME_NOREPLACE)` (or the tested `linkat`
    no-replace fallback) returns `EEXIST` for a competitor; exact canonical bytes are
    accepted as idempotent, while wrong owner/mode/boot ID/payload/hash, a symlink,
    partial temp, or a conflicting destination blocks without overwrite. Crash
    before/after publish and revoke fsyncs leaves every static `ExecCondition`
    fail-closed until read-only reconstruction completes.
30. Let the static root `ExecCondition` pass, pause the protected service before its
    guard wrapper obtains the shared lifecycle lock, and have the gate acquire the
    exclusive lock, revoke the old capability, CAS/rotate to a new state/version,
    and release. The resumed wrapper rereads the authority and refuses the queued
    stale start. Also verify the closed unit mapping, exact executable descriptor and
    hash, fixed argv/cwd/environment, target UID/GID/supplementary groups,
    `no_new_privs`, CLOEXEC lock release at successful `execve`, refusal on exec
    failure, and fail-closed behavior when the wrapper is missing, writable,
    replaced, or hash-mismatched. No shell, caller argv, or PATH lookup is accepted.
31. With a gate process alive, pause after settled capability or initial-legacy
    proof revocation but before the first `STAGED` INSERT. Concurrent settled/proof
    publishers, `ExecCondition`, and guard wrappers block on the exclusive lifecycle
    lock and cannot restore or use A. Killing the gate before `STAGED` releases the
    kernel lock and permits only the fully reattested settled-A pair or a newly
    attested empty-history initial proof. Killing it after `STAGED` or `ACTIVATING`
    never restores settled capabilities or initial eligibility; journal recovery is
    the only path. Repeat with pauses during stage, capture, locator preparation,
    INSERT/CAS fsync, token no-clobber publication, and the final pre-dispatch release.

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

Every public mutation method, including `insert_staged_if_absent()`, actor evidence
CASes, `acceptance_sealed`, passed settlement plus baseline update, and ordinary
state transitions:

1. verifies root identity and securely creates the missing fixed parent as
   root-owned mode `0700`, or verifies the existing trusted parent and DB path;
2. opens the DB without following symlinks;
3. applies the required PRAGMAs;
4. starts `BEGIN IMMEDIATE`;
5. re-reads and validates the complete row, or verifies expected absence for the
   first `STAGED` insert;
6. checks expected state/version (or absence) and evidence-field invariants;
7. performs exactly one legal CAS plus audit insert, except that first staging
   performs the atomic conditional INSERT plus its first audit and passed
   settlement atomically updates both the gate row and `live_baseline` plus their
   audits;
8. commits and fsyncs the DB; on first parent creation it fsyncs the verified
   `/var/lib` directory after `mkdir`, and on DB creation it fsyncs the DB and
   `/var/lib/medchat-runtime-generation`;
9. returns a frozen typed projection without raw SQLite rows or errors.

Rollback on Python/SQLite exceptions is best effort, but the public error is a
stable sanitized code. No helper deletes, truncates, VACUUMs, auto-recovers,
archives, or replaces this DB. Journal backup/archival belongs only to the
independent recovery procedure and, when requested, is completed and hashed from
the frozen authority volume before any OS/runtime snapshot restoration. Recovery
never initializes a fresh DB merely because the OS/runtime disks were rewound.

- [ ] **Step 6: Integrate immutable staging, capture, activation, and rollback with the journal**

Keep the already reviewed immutable-generation design and make the journal row
its transaction-local source:

- require the pre-provisioned formal-authority block volume and fixed root-only
  bind mounts for journal, claims, and runs; installation/validation may verify
  but never format or silently substitute this volume, and Broker/worker/gate stay
  disabled until its drive serial, filesystem UUID, `major:minor`, mount options,
  and snapshot exclusion match the reviewed evidence;
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
- bind every activation/verification/rollback primitive as an external actor in
  the journal before `systemd-run` dispatch, using the same zero-live recovery
  contract as validator, probes, acceptance, and soak; the activation actor is
  persisted in the `STAGED -> ACTIVATING` CAS and is cleared only after unit and
  cgroup termination plus physical verification;
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

The pre-snapshot OS baseline must also install an unavoidable cold-boot authority
interlock. Protection does not depend on a generator. Root-owned, non-writable
static drop-ins are installed for `medchat-sandbox-broker.service`,
`medchat-temporal-worker.service`, `medchat-opensandbox.service`,
`medchat-opensandbox-formal-gate.service`, and
`medchat-opensandbox-formal-gate.timer`. Every service drop-in permanently has
`Requires=medchat-formal-authority-attested.target`, orders after both the
attestation service and target, contains the explicit root-prefixed condition, and
clears the vendor command before replacing the effective start command with the
root-owned guard wrapper:

```ini
ExecCondition=+/usr/local/sbin/medchat-formal-authority-exec-condition --unit %n
ExecStart=
ExecStart=+/usr/local/sbin/medchat-formal-authority-exec-wrapper --unit %n
```

The wrapper accepts only `--unit %n`; it rejects extra arguments, environment-based
command overrides, path lookup, and unit aliases. Its closed mapping has two explicit
classes. The static class covers exactly the Broker, Temporal worker, OpenSandbox
daemon, and automatic formal-gate service. The journal-bound actor class accepts only
the exact persisted unique transient-unit name and closed actor kind for activation,
root validation, socket probe, acceptance, soak, ordinary rollback, or resolution
rollback; each kind maps to one reviewed command shape and must match the actor
identity hash already committed by CAS. For every entry it selects the one reviewed
executable descriptor and SHA-256, fixed argv, working directory, minimal environment
hash, target UID, GID, supplementary-group vector, and whether the target remains
root. The automatic formal-gate mapping names the root-owned gate executable; the
Broker, worker, daemon, and non-root actor mappings restore only their reviewed
identities. No shell or caller-controlled argv is ever involved. Installation and
validation inspect the
effective `systemctl cat/show` result, prove the original `ExecStart` list was reset,
and require the sole effective command to be this immutable wrapper with the exact
unit argument. A missing, writable, replaced, or hash-mismatched wrapper or mapped
executable fails before service execution.

The timer drop-in permanently requires/orders after the same target, uses a
fail-closed current-boot `ConditionPathExists` for
`/run/medchat-formal-authority/automatic-gate-eligibility.json`, and may name only
the statically guarded `medchat-opensandbox-formal-gate.service` as its `Unit=`.
`ExecCondition=` is not a valid timer directive; the timer's static condition may
queue only while the receipt exists, while the target service's root
`ExecCondition` performs an early shared-lock validation and the target service's
guard wrapper repeats the complete validation immediately at the real start boundary.
Thus a stale queued start is still blocked, both timer and service are guarded, and
no alternate service name is accepted. The timer itself has no `ExecStart`; its only
target is the statically mapped service whose effective `ExecStart` is the wrapper.
For the automatic gate service only, the root condition may admit a service job to
the guarded start boundary from a complete current-boot
runtime-token/eligibility-receipt pair after independently proving the same clean
settled baseline. Admission authorizes preflight and journal entry only. Before the
first formal mutation, the gate must exclusively lock the token lifecycle and
durably revoke the receipt and runtime token; it cannot admit an in-flight,
poisoned, initial-legacy, resolution, or unknown state. The manual root CLI does not
rely on this receipt.

`deployment/opensandbox/medchat-formal-authority-generator` remains root-owned at
`/usr/lib/systemd/system-generators/medchat-formal-authority-generator`, but it may
only emit coverage diagnostics and fail-closed warnings; it never creates the sole
dependency or condition. Installation and static/runtime validation enumerate the
closed five-unit set and fail if any static drop-in, directive, target, condition
binary, timer target, owner, mode, or hash is missing or different. Generator
absence, non-zero exit, empty output, or partial output therefore cannot remove the
static interlock and is explicitly tested with all restored units enabled. Static
validation also hashes the installed wrapper and closed command mapping and proves
that each service's effective `ExecStart` is exactly the wrapper after an empty reset;
the diagnostic generator cannot add an alternate unwrapped service command.

`medchat-formal-authority-attestation.service` runs before the target. It first
mounts the excluded authority volume read-only and descriptor-verifies its drive
serial, filesystem UUID, `major:minor`, canonical mount options, fixed binds,
snapshot exclusion, SQLite integrity/schema, audit chain, claims, sealed evidence,
and current journal state. `POISONED`, identity/exclusion mismatch,
blank/substituted volume, or unreadable evidence fails without starting Broker,
worker, daemon, timer, or gate. The cold-boot attestation service and every
post-`SETTLED_PASSED` or verified-rollback settled-baseline read-only recovery are
the only publishers of the runtime token and automatic-gate eligibility receipt.
Under one frozen SQLite read snapshot they read the authoritative settled row,
audit head, singleton `live_baseline`, and all five authority fields; while holding
the token-lifecycle lock they publish
`/run/medchat-formal-authority/runtime-token.json` first and
`automatic-gate-eligibility.json` last. Both are tmpfs, root:root `0400` files. The
runtime payload binds the current kernel `boot_id`, authority identity, frozen
journal snapshot hash, and either the `live_baseline` commit/target or the exact
captured legacy snapshot selected by a verified `SETTLED_FAILED` rollback, plus the
allowed immutable-generation or captured-legacy unit/executable identity.

The receipt binds the same `boot_id`, all five authority fields, baseline commit or
captured-legacy sentinel, baseline target, settled state and row version,
`live_baseline` singleton version (including the constrained NULL-baseline sentinel
for a verified legacy rollback), frozen snapshot hash, and exactly
`medchat-opensandbox-formal-gate.service` plus
`medchat-opensandbox-formal-gate.timer`. Only a clean settled baseline may produce
this pair. `clean_initial_legacy`, `STAGED` or any other in-flight state,
`RESOLUTION_REQUIRED`/`RESOLVING`, `POISONED`, unknown history, or mismatched
physical state produces neither receipt nor runtime token. A partial publication in
which the runtime token is durable but the receipt is absent is safe: normal runtime
may remain available, but the timer cannot queue and the automatic service condition
still rejects. A later read-only recovery using the identical frozen authoritative
snapshot may idempotently complete only the missing receipt. A receipt without a
valid matching runtime token never authorizes the gate.

All runtime, formal, automatic-gate, and initial-legacy proofs use distinct fixed
filenames, closed canonical schemas, and one fixed root-only
`/run/medchat-formal-authority/token-lifecycle.lock` (`root:root 0600`) on trusted
tmpfs. The whole-gate orchestrator always acquires the whole-gate flock first. For
the first formal mutation it then takes the token-lifecycle flock exclusively before
verifying/revoking the settled capability pair or initial-legacy proof and deliberately
holds that one lock continuously through release stage/capture, output-locator
preparation, `insert_staged_if_absent()`, `STAGED -> ACTIVATING` actor binding,
current-version `formal-new-run` no-clobber publication, every DB/file/parent fsync,
and the final pre-dispatch boundary. Only after the formal token is durable and the
gate is immediately ready to dispatch does it release the lifecycle lock. This long
critical section is an intentional safety boundary: stage/capture code must not
start, call, or wait for any protected service or any other operation requiring the
shared lifecycle lock. For later authorization-state CAS and capability rotations,
the orchestrator reacquires the locks in the same whole-gate-then-lifecycle order,
holds the exclusive lifecycle lock across revoke -> CAS -> publish plus all fsyncs,
and releases it only immediately before the corresponding protected dispatch. Cold-
boot and settled-baseline read-only publishers acquire only the token-lifecycle
flock. While
they hold it, no gate can change eligibility; after they freeze the journal snapshot,
any in-flight/non-settled row causes publication refusal. Conversely, before a gate
changes eligibility it holds the lifecycle lock, revokes settled capabilities, and
commits the new state, so a read-only publisher cannot race a mutation. Root
`ExecCondition` readers and root guard wrappers take the lifecycle lock shared;
publishers and revokers take it exclusively. A condition observes either the old
complete set or the new complete set, but condition success is only an early check
and never reserves a future start. The wrapper's second check at the executable
boundary is authoritative. Process death may leave a durable partial set, which all
conditions and wrappers reject until journal-driven read-only recovery repairs it.

Publication creates an unpredictable same-directory temporary file with
`O_CREAT|O_EXCL|O_NOFOLLOW`, writes the exact canonical bytes at root-only mode
`0600`, fsyncs, changes to final mode `0400`, re-fstats, and publishes with
`renameat2(RENAME_NOREPLACE)` before parent-directory fsync. If the pinned platform
lacks `renameat2`, it uses the separately tested same-filesystem `linkat` no-replace
plus temporary-unlink protocol; ordinary `rename` is forbidden. On `EEXIST`, the
publisher opens the destination with `O_NOFOLLOW`, verifies regular-file type,
root owner, exact mode, current `boot_id`, canonical bytes and hash, and accepts only
a byte-identical payload as idempotent. Any other destination, symlink, owner/mode,
payload, or hash blocks without overwrite. Partial temporary files confer no
authority and are descriptor-verified and removed only under the same lock after
authority verification. Revocation likewise occurs under the lock: descriptor-open
and verify the exact expected canonical token/receipt, unlink it, then fsync the
parent. Unexpected absence is accepted only in a documented crash-recovery branch
whose journal state requires that capability to be absent; every other absence or
mismatch fails closed.

The `+` prefix makes every service `ExecCondition` run as root even when the service
uses `User=medchat`. On every start it takes the shared lifecycle lock, descriptor-
opens and independently revalidates the authority volume, SQLite journal/integrity/
audit, current `boot_id`, target unit, token kind, action, state and row version,
`live_baseline` or settled captured-legacy receipt, automatic-gate receipt when
applicable, and exact executable generation. The condition always rereads journal
and token; a token is a bounded current-boot capability, never a substitute for
journal truth. Failure to reopen either capability or authority evidence blocks.

`scripts/medchat_formal_authority_exec_wrapper.py`, installed root-owned and
non-writable as `/usr/local/sbin/medchat-formal-authority-exec-wrapper`, is the actual
protected-service admission boundary. It opens the lifecycle lock without following
symlinks and holds a shared flock while it descriptor-reopens and validates the
authority volume, journal integrity/audit, token and receipt, `boot_id`, canonical
static unit or exact journal-bound transient actor unit, action, state/version,
actor, generation, and the closed command mapping. It
opens the mapped executable by trusted descriptor, verifies owner/mode/hash and the
fixed release identity, constructs only the reviewed argv and minimal environment,
and validates the mapped cwd and exact UID/GID/supplementary groups. Still holding
the same shared flock, it clears ambient capabilities, applies the fixed
`setgroups`/`setgid`/`setuid` identity (or the mapped root identity), sets
`no_new_privs`, changes to the descriptor-verified cwd, marks authority/token/lock
descriptors `CLOEXEC`, and uses `execveat(AT_EMPTY_PATH)` or an equivalently pinned
descriptor-based `fexecve` to enter the exact executable. The shared flock is
released only by CLOEXEC at the successful exec boundary; if any preparation or exec
fails, the wrapper starts nothing, closes descriptors, releases the lock, and exits
non-zero. It never invokes a shell, accepts caller argv beyond canonical `--unit`,
uses PATH lookup, or passes an unreviewed environment variable.

Consequently, a service job that already passed `ExecCondition` but waits while the
gate obtains the exclusive lifecycle lock cannot cross into the real command. After
the gate completes revoke -> CAS -> token rotation and releases its lock, the wrapper
acquires shared access, sees the new authority, and rejects the stale queued start.
The condition remains defense in depth and an early failure path; it is never treated
as proof that the later `ExecStart` is authorized.

The manual formal-gate entry uses a separate root-only attestation mode. Under the
whole-gate and exclusive token-lifecycle locks, and before the first formal
mutation, it descriptor-verifies and durably revokes the automatic-gate eligibility
receipt and old runtime token. `STAGED` alone never receives a service-start token.
For every protected service start, the gate first commits the state/evidence/actor
CAS that authorizes that exact action, then publishes a formal token bound to the
resulting latest state and `row_version`, invocation, commit, exact generation,
exact unit/action allowlist, and actor identity. Token publication before the CAS is
forbidden. A crash after CAS but before publication leaves every service blocked;
recovery may reconstruct only the token derivable from the current journal row and
physical attestation. A crash after publication but before dispatch reuses that
exact authorization and does not repeat the CAS. Any subsequent state, same-state
evidence, or actor-binding CAS increments `row_version`; before another protected
start the old token is durably revoked and a new current-version/action token is
published. Every static condition rejects the stale token.

For initial activation, the single `STAGED -> ACTIVATING` CAS binds the exact
activation actor. Only after that commit does the gate publish
`formal-new-run(action=activate_target)` for the reviewed target generation and
activation transient unit plus only the exact guarded Broker/worker/daemon units
that action may start; it then releases the lifecycle lock and dispatches the actor.
Other new-run phases follow the same CAS-before-token rule with closed non-scientific/
scientific action and unit lists.
On any normal failure requiring rollback, the gate first CASes to `ROLLING_BACK`
with the stable failure envelope and exact rollback actor tuple, then durably
revokes `formal-new-run`, and only then publishes `formal-recovery` bound to the
current `ROLLING_BACK` row version, exact journal-selected previous generation or
captured legacy runtime, and rollback/PID-cwd/health-verification-only unit/action
allowlist. An inherited `ROLLING_BACK` row with no dispatchable actor first performs
its legal actor-binding CAS and publishes against that resulting version. Recovery
cannot start the new target or any science, validation, acceptance, soak, design,
or arbitrary service.

After `SETTLED_PASSED`, root reopens the journal and performs a new settled-baseline
read-only physical attestation of the new `live_baseline`; after verified rollback
and `SETTLED_FAILED`, it does the same for the restored previous baseline or exact
captured legacy runtime. From that one frozen snapshot and under the exclusive
lifecycle lock, it verifies and revokes the now-stale formal token, publishes the
new/current baseline runtime token, and publishes the automatic-gate eligibility
receipt last. To shared-lock `ExecCondition` readers this closure is atomic. If the
process dies at any internal boundary, the durable partial set authorizes no
automatic gate and any mismatched service start is blocked; a later read-only
recovery reconstructs only the pair justified by the already settled row and
current physical attestation. It does not alter settlement, advance a row, dispatch
an actor, or rerun science. An existing destination is never overwritten: only an
exact canonical payload is idempotent. A crash-created ambiguous mix of runtime,
formal, or receipt files remains fail-closed until the same recovery verifies and
removes only capabilities proven stale by the settled journal.

A zero-live-mutation `STAGED -> SETTLED_FAILED` follows the same token closure: if
the invocation already revoked the runtime token/receipt, settled-baseline recovery
reattests the unchanged baseline and republishes the matching runtime token followed
by its automatic-gate receipt. If revocation never occurred, it verifies the
existing pair rather than rotating it. This branch cannot publish a target-
generation or formal service-start token.

First migration has one separate `clean_initial_legacy` branch. It requires the
correct authority/QEMU identity and initialized schema; completely empty gate and
audit history; no claim, tombstone, run, or result; NULL `live_baseline`; exact
reviewed legacy unit hashes, enabled/disabled states and PID-cwd expectations;
absent `/opt/medchat/current`; and zero Broker, worker, OpenSandbox, automatic-gate
PIDs and cgroups under the static interlock. Boot attestation may create only a
root-only current-boot `initial-legacy-eligible` proof binding those observations;
it creates no runtime token or automatic-gate receipt and starts no service. The
manual gate acquires the whole-flow then token-lifecycle flocks, revalidates the
proof and physical state, consumes the eligibility proof by verified unlink plus
directory fsync, and retains the exclusive lifecycle lock throughout staging,
legacy capture, locator preparation, `insert_staged_if_absent()` creation of the
first `STAGED` row/audit, and `STAGED -> ACTIVATING` with the exact activation actor.
`STAGED` still publishes no service-start token. Only after the activation CAS may it
publish
`formal-new-run(action=activate_target)` bound to the new row version, commit,
invocation, target generation, activation actor, and exact guarded service/action
allowlist, then fsync and release immediately before dispatch. Stage/capture in this
interval is filesystem/journal-only and must not call any protected service or wait
for a shared lifecycle reader. Once the
first row/audit exists, `clean_initial_legacy` can never be true again. A crash after
proof revocation but before the insert releases the kernel lock and may only
reattest the same still-empty branch and publish a new invocation-bound proof; it
never reuses the prior nonce. While the original gate remains alive, no proof
publisher can enter that interval. A
crash after `STAGED` but before `ACTIVATING` publishes no token and resumes only the
journal-authorized transition; a crash after `ACTIVATING` but before publication
reconstructs only the matching current-version activation token. Neither path
recreates the initial row or uses rollback recovery before live mutation. Any
residual or mismatched
history, claim, tombstone, run, baseline, legacy state, process, cgroup, proof, or
authority identity refuses without a token or service start. A stale eligibility
proof found after `STAGED` is removed only after journal verification and can never
authorize a second initial branch.

`POISONED` can obtain only the same-state safety-cleanup mode; it never authorizes
activation, rollback, resolution, science, or service start. The root gate reattests
before any controlled read/write remount and removes any invocation token on a
verified terminal/abort path. No token mode can bypass poison, create a replacement
journal, or make history empty.

- [ ] **Step 7: Implement the single root-owned formal QEMU gate**

Create `scripts/run_opensandbox_formal_qemu_gate.py`. The operator-visible entry
keeps the whole-flow root-only non-blocking flock from preflight through final DB
settlement and best-effort export. Every time it needs the separate token-lifecycle
flock, the orchestrator acquires it only after the whole-flow flock, retains it
through the relevant revoke/CAS/capability fsync boundary, and releases it only
immediately before protected-service dispatch. The first such interval is longer by
design: it begins before settled/initial capability revocation and continues without
release through stage/capture, locator binding, first `STAGED`, `ACTIVATING`, and
activation-token durability. Nothing in that interval may invoke a protected service
or wait for a shared lifecycle lock. No other command may invoke a formal worker.
The lock is `/run/lock/medchat-opensandbox-formal.lock`, opened without following
symlinks as a root-owned regular file with mode `0600`; its file descriptor
remains open for the entire flow. An untrusted path/type/owner/mode fails before
journal or runtime mutation.

The main gate order is fixed:

1. Validate root, `repeat == 30`, clean full commit, the normalized
   `qemu_snapshot_id`, exact snapshot-evidence file/hash and current QEMU/KVM VM
   identity, trusted root-managed environment, fixed DB/output roots, exact script
   allowlist, and the separately mounted formal-authority volume. Require the
   attested drive serial, filesystem UUID, `major:minor`, mount options, fixed
   root-only bind mounts, and proof that the named QEMU snapshot excludes that
   block node. Repeat authority attestation after acquiring the lock and before
   every recovery or dispatch decision.
2. Acquire the whole-flow flock. Lock contention returns `formal_gate_locked`
   before DB/runtime/output/capability mutation. Every recovery branch that must
   change journal eligibility or capabilities next acquires the exclusive token-
   lifecycle flock, commits/revokes/publishes under it, and releases it before any
   protected service dispatch; cold-boot/read-only publishers use only that lock and
   therefore cannot race the eligibility CAS or invert the order.
3. Open and validate the SQLite journal. A `POISONED` row permits only the closed
   same-state actor/service shutdown and zero-live receipt protocol, then blocks.
   Verify historical settled rows only against their rows, audits, and frozen
   evidence hashes; verify current symlink/PIDs only against `live_baseline` or the
   active transaction. Reconcile only the explicit recovery states in the table,
   beginning with persisted actor ownership. Do not let `gate-result.json` or a
   marker select a branch.
4. For the requested commit, inspect the journal row and permanent claim together.
   A matching claim-file/DB gap enters `RESOLUTION_REQUIRED`; a mismatch poisons.
   A trusted settled row returns its stable already-terminal code only after its
   row, audit and frozen claim/report/evidence/receipt hashes agree; it requires
   current physical agreement only when selected by `live_baseline`. An unsettled
   row is recovered before any return.
5. Read-only validate the source/snapshot inputs and derive one unique safe commit-
   bound `run_relpath` plus fixed descendant `report_relpath`, but do not stage,
   capture, create output, or insert a row yet. Re-entry may reuse only a completely
   identical locator already durably bound by a `STAGED` row; it never allocates a
   replacement. The initial-legacy path must satisfy the one-time proof above; every
   non-initial path must begin from a clean settled baseline.
6. While holding the whole-gate flock, acquire the exclusive token-lifecycle flock.
   Before any formal filesystem, journal, output, or runtime mutation, descriptor-
   verify and durably revoke the automatic-gate eligibility receipt and old runtime
   token, or consume the one-time initial-legacy proof, and fsync. Do **not** release
   the lifecycle lock. While continuously holding it, stage and attest the release,
   capture/reuse the exact legacy or generation snapshot, finalize the unique safe
   output locators, call `insert_staged_if_absent()`, and keep `STAGED` without a
   protected-service token. Commit `STAGED -> ACTIVATING`
   while binding the exact activation actor; only after that CAS and DB fsync publish
   `formal-new-run(action=activate_target)` bound to the resulting state/row version,
   invocation, target generation, activation transient unit, exact guarded Broker/
   worker/daemon units required by that action, and actor identity. Fsync the
   capability directory. Release the lifecycle lock only when the token is durable
   and the gate is immediately ready; dispatch the unique transient unit only after
   that release. Verify zero-live termination and
   activation, clear the actor, and CAS to `ACTIVE_PRECLAIM`; that version change
   invalidates the activation token. Before every later protected-service dispatch,
   perform its authorizing state/evidence/actor CAS first, then revoke any stale
   formal token and publish a current-version token for only that exact action/unit.
   A CAS-before-publication crash leaves services blocked and is reconstructed from
   the journal; token-before-CAS is forbidden.
   Holding the lifecycle lock during potentially slow stage/capture is deliberate:
   concurrent settled/proof publishers, conditions, and start wrappers must block
   rather than recreate A's authority. The stage/capture implementation is therefore
   restricted to local descriptor-confined filesystem/journal work and may not call
   or wait for any protected service/shared-lock consumer. If the gate dies before
   `STAGED`, kernel lock release leaves the journal settled and read-only recovery may
   republish only the reattested prior baseline pair (or, for truly empty history, a
   new initial-legacy proof). If `STAGED` or `ACTIVATING` committed, every publisher
   sees in-flight authority and refuses settled/initial capability restoration.
7. Descriptor-confined below the trusted output-base handle, create exactly the
    journal-bound run directory plus `acceptance/` and `soak/` subdirectories once,
    with no symlink traversal or pre-existing target. The report file itself must
    remain absent until the soak atomically publishes it with no-replace semantics.
    Keep the run parent root-owned mode `0700`; make only each pre-created actor
    subtree `medchat:medchat 0700` while that phase owns it. Run privileged
    deployment/runtime validation and a transient non-root socket probe first.
    Before acceptance, capture the independent OpenSandbox/Docker baseline and
    durable unique run label, persist the complete actor identity including an
    ordered mount policy, and give its `PrivateMounts=yes` unit only
    `acceptance/` read/write at `/run/medchat-formal-output`; `soak/` is not mounted
    or visible. Clear ownership only after cgroup and daemon-resource zero-live
    proof. Then descriptor-validate/fsync the acceptance evidence, recursively
    seal it root:root with files `0400` and directories `0500`, fsync its parent,
    reopen and verify the seal, calculate its canonical receipt, and perform the
    one `acceptance_sealed` expected-state/version CAS to persist
    `post_acceptance_manifest_sha256` and `acceptance_seal_sha256` before claim
    creation. A crash after physical sealing but before that CAS is recovered only
    by descriptor-revalidating the same journal-bound tree; a writable or mismatched
    tree is never promoted. Preserve the reviewed phase allowlist, timeout
    budgets, clean diagnostics delta, zero-container check, and artifact truth.
8. Re-read the frozen `ACTIVE_PRECLAIM` row and construct the exact closed
   canonical claim payload, including expected row version, target, locators,
   `acceptance_seal_sha256`, snapshot/evidence, and authority identity. Create
   `/var/lib/medchat-opensandbox-runs/claims/$FULL_COMMIT.json` with
   `O_CREAT|O_EXCL|O_NOFOLLOW`, root `0600`, file fsync, and parent fsync. It is
   permanent. Re-open and byte-verify it, hash its canonical content, then
   immediately CAS `ACTIVE_PRECLAIM -> CLAIMED` with that hash and
   `claim_expected_row_version`. An inherited O_EXCL-to-CAS gap uses only the
   reconstruction algorithm in Step 1. Never delete, replace, repair, or reuse it.
9. Bind the exact soak actor to the existing journal locators and frozen
    `acceptance_seal_sha256` prerequisite. Give its private namespace exactly one
    mapping: `soak/` read/write at `/run/medchat-formal-output`. Acceptance paths,
    files, descriptors, and mounts are absent; the soak does not read acceptance
    contents. Include the one ordered RW mapping and complete namespace policy in
    `actor_mount_sha256`. Then dispatch exactly one non-root 30-job soak. Never
    retry the scientific command, Vina, Meeko, upload, validation, or complete run.
    After the actor exits, prove cgroup and daemon-resource zero-live before
    trusting or sealing output.
10. Open the soak tree and exact report through retained trusted directory
    descriptors with `O_NOFOLLOW`; reject path substitution, hard links outside
    policy, unexpected owners/types/modes, and every unbound file. Validate report
    schema, post-soak manifest, provenance, artifacts, cleanup, probes, retries,
    release identity, and zero-leak state; hash and fsync every accepted file and
    directory. Recursively seal `soak/` root:root with regular files mode `0400`
    and directories mode `0500`, fsync/reopen/verify it, then seal the run parent
    root:root mode `0500` only after both phase trees are independently sealed.
    Reopen the complete tree descriptor-confined and verify content hashes plus
    canonical ownership/mode/path seal. Store `report_sha256`, `report_status`, and
    `report_seal_sha256` through the audited `report_sealed` CAS. Any retry,
    timeout, invalid output, fallback, mismatch, cleanup uncertainty, mutable
    evidence, or missing evidence prevents pass.
11. On success and after a fresh current physical attestation, use one
    `BEGIN IMMEDIATE` transaction to CAS `CLAIMED -> SETTLED_PASSED`, store final
    manifest/attestation hashes, update `live_baseline` to this commit, and append
    both audits before one commit. After DB and parent fsync, settled-baseline
    read-only recovery takes one frozen journal/live-baseline snapshot, reattests the
    new baseline, verifies and revokes the stale formal token, publishes the current
    generation runtime token, and publishes its automatic-gate eligibility receipt
    last under the lifecycle lock. Atomically regenerate the redacted
    `gate-result.json`; export or capability-publication failure does not change
    settlement and is repaired idempotently from DB on the next read-only entry.
    Until the matching runtime token exists services stay blocked; until both it and
    the receipt exist the automatic gate stays blocked.
12. On normal failure after possible live mutation, first CAS to `ROLLING_BACK`
    while storing the stable failure-envelope hash, any trusted failed-report
    hash/status, and the exact rollback actor tuple. After that CAS/fsync, durably
    revoke `formal-new-run`, then publish `formal-recovery` bound to the current
    `ROLLING_BACK` state/version, row-selected previous generation or captured legacy
    runtime, and rollback-only unit/action allowlist. Only then perform the
    idempotent attested rollback. An inherited row that must newly bind an actor
    publishes only after that same-state CAS and against its resulting version.
    After exact physical verification, use one DB transaction to store the receipt
    hash, set `rollback_verified=1`, and reach `SETTLED_FAILED`. Settled-baseline
    read-only recovery reattests the restored baseline, revokes the stale formal
    token, publishes its runtime token and automatic-gate receipt in that order, and
    exports afterward. A crash in any CAS/token window leaves starts blocked and is
    repaired only from the authoritative current row; it never repeats rollback.
13. If rollback fails or physical state is ambiguous, CAS to `POISONED`. Through
    only the closed same-state poison mutations, reconcile/stop the journal-bound
    actor, independently prove cgroup and daemon-resource zero-live, stop and
    disable worker then Broker, and persist `poison_shutdown_receipt_sha256` when
    proved. Whether cleanup succeeds, fails, or crashes, remain POISONED,
    optionally export the strict marker, and require the recorded QEMU snapshot.
    The DB state blocks even if cleanup or marker export fails.
14. Assert no token-lifecycle flock remains held, then release the whole-gate flock
    only after DB fsync and export attempt. The outer `finally`
    must not infer rollback from a process-local boolean; it consults and CASes the
    authoritative row and can dispatch only the state-table-authorized rollback.

Every external command that can mutate runtime or outlive an orchestrator call is
executed in a uniquely named systemd transient service: activation, privileged
root validator, socket probe, acceptance, soak, ordinary rollback, and resolution
rollback. Before `systemd-run`, an expected-state/version CAS stores the exact
unit, closed actor kind, deterministic expected cgroup, invocation ID, and actor
identity hash. For acceptance and soak it also stores the private-mount hash,
unique run label, and independently observed OpenSandbox/Docker baseline hash.
After that CAS and before `systemd-run`, the gate publishes the exact formal token
for the resulting row version, actor unit, action, and generation; any token from
the prior version/action is first durably revoked. After parent fsync it releases
the lifecycle lock. The transient unit's generated `ExecStart` is not the actor
command: it is the same root-owned wrapper with only `--unit %n`; the wrapper resolves
the exact journal-bound actor kind/identity to its fixed command. Its root
`ExecCondition` and, authoritatively, wrapper each acquire the shared lock and
independently revalidate current authority. Condition success alone never reserves
the dispatch.
No actor dispatch may rely on a token created before its actor-binding CAS.
The gate verifies the created unit/cgroup against that tuple and never identifies
an actor by process-name search. Normal completion and inherited recovery both
use the cgroup-plus-daemon-resource zero-live protocol in Step 2 before clearing
ownership. The parent-held
flock does not constitute actor ownership and its loss never authorizes rollback
or a replacement unit.

Each transient unit is created from a closed environment builder. Root validation
receives no scientific gate variable. Socket probe receives neither scientific
gate variable; acceptance receives only
`MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1`; soak receives only
`MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1`. Acceptance and soak run as
`medchat:medchat` with `SupplementaryGroups=medchat-sandbox docker`, `python -B`,
the activated immutable release as working directory, `PrivateMounts=yes`, and
only the fixed namespace output path. Credentials remain only in root-managed
runtime environment files and are never copied to reports, DB fields, commands,
unit descriptions, or logs.

The external output contract remains:

```text
/var/lib/medchat-opensandbox-runs/$FULL_COMMIT/$UNIQUE_RUN_ID
```

The base and full-commit parent are root-owned non-symlink directories with mode
`0700`, not writable by group/world. `UNIQUE_RUN_ID` matches
`[a-z0-9][a-z0-9-]{7,63}`. The privileged gate creates the final directory once
through verified parent descriptors only after its relative locator is durable in
the journal. It creates the run parent root:root `0700` and exactly two independent
children, `acceptance/` and `soak/`; only the child currently assigned to an actor
is `medchat:medchat 0700`. The host base, commit parent, and run parent stay
root:root `0700` and are never group-traversable. Resolve and `lstat` every
component; reject `..`, wrong commit components, untrusted owner/mode/type,
symlinks, existing final targets, and any candidate under the source, current link,
or releases root. Neither actor receives a host path or the run parent.

For acceptance, the root systemd manager creates a private mount namespace with
`PrivateMounts=yes` and exactly one writable mapping:
`BindPaths=$VERIFIED_ACCEPTANCE_SOURCE:/run/medchat-formal-output`. The soak source
is not visible. After acceptance cgroup and daemon resources are proved zero, root
descriptor-validates and fsyncs that subtree, changes all files to root:root `0400`
and directories to root:root `0500`, fsyncs/reopens/verifies the seal, and only then
may commit the audited `acceptance_sealed` mutation and prepare soak. For soak, the
manager creates a new private namespace with exactly
`BindPaths=$VERIFIED_SOAK_SOURCE:/run/medchat-formal-output`. It has no second
mapping and no acceptance source. Scientific soak is self-contained in
the immutable release, fixed scientific inputs, closed configuration, and its own
writable subtree; `acceptance_seal_sha256` is a journal precondition only, not an
input artifact. Acceptance never receives a soak bind, and soak cannot resolve the
acceptance master through its namespace.

For each actor, the ordered mapping list, exact source descriptor/locator, fixed
destination, RO/RW mode, `PrivateMounts=yes`, and all namespace properties must
exactly match `actor_mount_sha256` persisted before dispatch. The root manager
creates and verifies destinations inside the unit namespace before exec. A wrong
source/destination/order, missing private namespace, extra bind, RO/RW mismatch,
or mount-table discrepancy fails closed and leaves the actor journal-bound for
safe recovery. Actual-UID integration tests prove acceptance cannot read or write
soak, pre-create the soak report, inspect siblings/other runs, or replace parents;
soak cannot see or modify sealed acceptance and still completes its fixed-input
workflow using only the soak subtree.

Poses, copied inputs, temporary files, observer output, and reports stay below the
phase-specific writable subtree. Artifact projection validates relative paths
against its retained phase-root descriptor, and no cleanup path may cross it. Root
seals soak independently after zero-live proof, then seals the run parent root:root
`0500`. No `medchat` process may retain a writable descriptor when either phase
seal begins.

If the report is root-sealed and the process dies before `report_sealed` or final
settlement commits, recovery uses only `run_relpath`/`report_relpath` from the
journal. It descriptor-confined revalidates the root seal, content hash, schema,
artifact closure, actor zero-live receipt, claim and release identity, then records
the seal or settles without redispatching soak. It never globs the output root,
sorts candidate reports, scans random run IDs, or accepts a path discovered from
report contents. A report left writable/unsealed at a crash is not formal evidence;
it cannot settle passed and follows the closed failure/resolution/poison rules.

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
- the exact normalized CLI `qemu_snapshot_id` equals the real schema column, the
  supplied snapshot-evidence SHA-256 equals both the file and journal field, and
  the canonical evidence identifies the current QEMU/KVM VM identity (including
  its stable VM UUID) plus that exact snapshot ID; it must also attest the same
  formal-authority drive serial, filesystem UUID, `major:minor`, mount options,
  fixed binds, and exclusion from the OS/runtime rewind set;
- any persisted actor is stopped/reconciled through the one-stop
  cgroup-plus-daemon-resource zero-live protocol, with matching baseline/post
  observers and zero labelled/unexplained sandbox/container state;
- the journal-bound report locator directly, including whether a root-sealed report
  can be independently settled instead; globbing for a report is forbidden.

After validation, CAS `RESOLUTION_REQUIRED -> RESOLVING` while storing the
operator-evidence and canonical resolution-record hashes. Commit before rollback.
Perform the one row-selected attested rollback. On exact previous-state proof,
store the verified receipt and CAS to `SETTLED_FAILED` in one transaction. Do not
create a scientific result, recreate/delete the claim, run validator, acceptance,
or soak. Wrong hashes, active work, repeated authorization, or a settled row
refuse with zero rollback. Resolution rollback itself is a journal-bound
`resolution_rollback` transient actor; its unit/cgroup/identity is persisted before
dispatch and must be independently zero-live before receipt settlement.

If the process inherits `RESOLVING`, it may settle without rollback only when
physical attestation proves the exact previous target and the stored evidence can
produce a trusted receipt. If that proof is unavailable or mixed, CAS to
`POISONED`; do not attempt a second rollback. A rollback error follows the same
poison path.

The operator syntax is hash- and version-bound and contains no transaction-file
argument. The snapshot evidence is a root-owned mode-`0600`, non-symlink,
descriptor-confined canonical record created outside the gate by the reviewed
QEMU snapshot procedure. It contains only bounded snapshot ID, QEMU/KVM type,
stable VM UUID, capture timestamp, evidence version, authority drive serial,
filesystem UUID, observed `major:minor`, canonical mount options and bind targets,
QEMU block-node identity, and the snapshot-exclusion policy/hash. Missing arguments,
malformed/non-normalized IDs, evidence hash mismatch, snapshot-ID mismatch, VM
identity mismatch, authority identity/exclusion mismatch, or an evidence file for
another VM fails before actor stop, resolution CAS, or rollback:

```bash
sudo env PYTHONDONTWRITEBYTECODE=1 \
  /opt/conda/envs/medchat/bin/python -B \
  /usr/local/src/medchat-release/scripts/run_opensandbox_formal_qemu_gate.py \
  --resolve-incomplete-claim \
  --old-release-commit "$OLD_RELEASE_COMMIT" \
  --claim-sha256 "$OLD_CLAIM_SHA256" \
  --expected-row-version "$EXPECTED_ROW_VERSION" \
  --qemu-snapshot-id "$QEMU_SNAPSHOT_ID" \
  --qemu-snapshot-evidence-file "$QEMU_SNAPSHOT_EVIDENCE_FILE" \
  --qemu-snapshot-evidence-sha256 "$QEMU_SNAPSHOT_EVIDENCE_SHA256" \
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
python -m pytest tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_stability_soak.py tests/sandbox_broker/test_formal_gate_state.py tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider
1..10 | ForEach-Object { python -m pytest tests/sandbox_broker/test_formal_gate_state.py tests/sandbox_broker/test_formal_qemu_gate.py -q -p no:cacheprovider; if ($LASTEXITCODE -ne 0) { throw "formal gate repetition failed at iteration $_" } }
python -m pytest tests/sandbox_broker -q -p no:cacheprovider
python -m compileall -q src scripts
python scripts/validate_opensandbox_deployment.py --static
git diff --check
```

Run the full failpoint matrix in both legacy-first and generation-to-generation
modes. The test report must show scientific soak dispatch `<= 1`, logical rollback
`<= 1`, actor stop/wait `<= 1` per recovery entry, no live-actor overlap, no
duplicate claim, no stale CAS success, one initial `STAGED` audit, exact sealed
report-locator reuse, correct A -> B -> C baseline history, persistent authority
truth across OS/runtime rewind, no export-driven branch, no pre-CAS or stale-version
formal-token start, correct `ROLLING_BACK` recovery-token rotation, and no-clobber
runtime/receipt publication under concurrent publishers. It must also show that a
condition-passed queued service is rejected by the guard wrapper after a concurrent
CAS/token rotation; that the wrapper executes only the hash-bound fixed command as
the mapped UID/GID/groups/cwd/environment with `no_new_privs`; and that missing,
tampered, wrong-hash, exec-failed, caller-argv, shell, and PATH cases fail closed.
Before a formal run,
the QEMU host integration preflight must additionally execute the real transient
mount-namespace/DAC test as the actual `medchat` UID and the real independent
OpenSandbox/Docker create-time label/observer test. That non-scientific preflight
creates one short-lived labelled sandbox, proves OpenSandbox and Docker metadata
agreement before create returns, cleans it, and proves both observers return to
zero; it creates no claim and cannot satisfy the formal soak. The host test also
boots an OS snapshot whose relevant units are enabled and proves the static
authority drop-ins block every service `ExecStart` and prevent the timer from
queuing an unguarded service without valid current-boot evidence, including when
the generator is missing, exits non-zero, emits nothing, or emits only partial
diagnostics. Poison/in-flight/wrong-volume states and any RAM/vmstate/device-state
restore attempt remain blocked. Only a surviving read-only-verified settled
generation baseline or a verified settled rollback to the exact captured legacy
state may produce its corresponding runtime token; the exact empty-history
legacy branch may produce only `initial-legacy-eligible`, never a runtime token.
Only the same frozen clean-settled snapshot may produce the matching automatic-gate
receipt, and the receipt is published last. Any failure or partial/conflicting
capability set keeps the automatic gate disabled; absent required runtime capability
also keeps Broker, worker, and daemon disabled. The host preflight inspects every
effective service command and proves it is the root guard wrapper, not the vendor
`ExecStart`. A deterministic concurrency probe pauses a live gate after capability/
proof revocation but before `STAGED`: publishers, conditions, and wrappers remain
blocked until either the gate completes durable `ACTIVATING` plus token publication
or is killed. Only a pre-`STAGED` death can restore the reattested settled pair or
empty-history proof; post-`STAGED` death cannot.

- [ ] **Step 10: Obtain independent review and commit Task 7 before formal execution**

Request independent specification and implementation-quality reviews over the
complete Task 7 diff. Both reviewers must explicitly check journal filesystem
security, schema constraints, CAS transitions, the transition/recovery table,
atomic first-STAGED insertion, actor unit/cgroup ownership and orphan recovery,
claim-file/DB gap, acceptance-seal/DB gap, report sealing/DB gap, result-export gap,
live-baseline history,
rollback physical/DB gap, snapshot-ID/evidence binding, resolution crash, poison
persistence and same-state shutdown, phase-specific single-RW output mount/DAC
isolation and sealing, OpenSandbox/Docker atomic create-time labelled-resource
observation, authority-volume identity and rewind exclusion, static cold-boot
drop-ins, diagnostics-only generator failure, root ExecCondition, guarded effective
ExecStart and closed command/identity mapping, condition-to-exec race closure,
complete token
CAS-before-publication sequencing, lifecycle-lock ordering, true no-clobber
revocation/publication/recovery, automatic-gate receipt creation/rotation, one-time
clean-initial-legacy authorization,
concurrent gates, immutable releases, environment isolation,
manifest equality, external outputs, phase contracts, and old-report retention.
No Critical or Important finding may remain.

```powershell
git add -- src/sandbox_broker/formal_gate_state.py src/sandbox_broker/models.py src/sandbox_broker/opensandbox_client.py src/sandbox_broker/service.py scripts/attest_opensandbox_formal_authority_boot.py scripts/medchat_formal_authority_exec_wrapper.py scripts/opensandbox_formal_runtime.py scripts/run_opensandbox_formal_qemu_gate.py scripts/run_opensandbox_docking_acceptance.py scripts/run_opensandbox_stability_soak.py scripts/validate_opensandbox_deployment.py deployment/opensandbox/activate-runtime-generation.sh deployment/opensandbox/install.sh deployment/opensandbox/medchat-formal-authority-generator deployment/opensandbox/medchat-formal-authority-attestation.service deployment/opensandbox/medchat-formal-authority-attested.target deployment/opensandbox/medchat-formal-authority-exec-condition deployment/opensandbox/medchat-opensandbox-formal-gate.service deployment/opensandbox/medchat-opensandbox-formal-gate.timer deployment/opensandbox/systemd/medchat-sandbox-broker.service.d/10-formal-authority.conf deployment/opensandbox/systemd/medchat-temporal-worker.service.d/10-formal-authority.conf deployment/opensandbox/systemd/medchat-opensandbox.service.d/10-formal-authority.conf deployment/opensandbox/systemd/medchat-opensandbox-formal-gate.service.d/10-formal-authority.conf deployment/opensandbox/systemd/medchat-opensandbox-formal-gate.timer.d/10-formal-authority.conf deployment/opensandbox/opensandbox-server-0.2.2-formal-label.patch deployment/opensandbox/opensandbox-server-formal-label-lock.json deployment/opensandbox/medchat-sandbox-broker.service deployment/medchat-temporal-worker.service deployment/opensandbox/README.md tests/sandbox_broker/test_formal_gate_state.py tests/sandbox_broker/test_deployment_assets.py tests/sandbox_broker/test_formal_qemu_gate.py tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_real_opensandbox_acceptance.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py
git diff --cached --check
git commit -m "feat: add opensandbox formal gate journal"
git rev-parse HEAD
```

The resulting immutable full commit becomes `RELEASE_COMMIT`. Do not amend it.

- [ ] **Step 11: Prepare and invoke one reviewed QEMU formal gate**

On the snapshotted Ubuntu QEMU host, prepare a root-owned clean source checkout,
the pre-gate normalized QEMU snapshot identifier, and its root-owned mode-0600
canonical snapshot-evidence file plus independently recorded SHA-256. Provision
and mount the distinct formal-authority block volume first; attest its drive
serial, filesystem UUID, `major:minor`, hardening options, fixed bind mounts, and
absence from the OS/runtime snapshot device set. The OS snapshot baseline must
already contain the reviewed static drop-ins for all five units, attestation
service/target, root ExecCondition, root guard wrapper plus closed unit-command
mapping, timer condition, and diagnostics-only generator.
Validation must prove the static layer remains effective with generator failure.
Run the real
non-scientific phase-mount/DAC, atomic-label observer, and cold-boot authority
interlock preflights described in Step 9. Preparation may
not stage, capture, activate, validate, claim, run workers, or roll back. Then
invoke only:

```bash
set -euo pipefail
SOURCE_REPOSITORY=/usr/local/src/medchat-release
RELEASE_COMMIT="$(git -C "$SOURCE_REPOSITORY" rev-parse HEAD)"
test "${#RELEASE_COMMIT}" -eq 40
test -z "$(git -C "$SOURCE_REPOSITORY" status --porcelain)"
: "${QEMU_SNAPSHOT_ID:?required}"
: "${QEMU_SNAPSHOT_EVIDENCE_FILE:?required}"
: "${QEMU_SNAPSHOT_EVIDENCE_SHA256:?required}"
sudo env PYTHONDONTWRITEBYTECODE=1 \
  /opt/conda/envs/medchat/bin/python -B \
  "$SOURCE_REPOSITORY/scripts/run_opensandbox_formal_qemu_gate.py" \
  --source-repository "$SOURCE_REPOSITORY" \
  --expected-release-commit "$RELEASE_COMMIT" \
  --output-base /var/lib/medchat-opensandbox-runs \
  --repeat 30 \
  --require-virt qemu \
  --qemu-snapshot-id "$QEMU_SNAPSHOT_ID" \
  --qemu-snapshot-evidence-file "$QEMU_SNAPSHOT_EVIDENCE_FILE" \
  --qemu-snapshot-evidence-sha256 "$QEMU_SNAPSHOT_EVIDENCE_SHA256"
```

The operator is the only caller of the formal gate. It never calls activation
primitives, acceptance, soak, or resolution commands directly. A second call for
the same commit can only verify the journal/claim/export and return the stable
already-terminal, resolution-required, or poison code; it never reruns science.

QEMU recovery never restores the formal-authority volume. Before rewind, the
independent operator stops the gate, worker, Broker, OpenSandbox daemon and all
formal resources, flushes every filesystem, cleanly unmounts the authority volume,
and fully powers off the guest. `fsfreeze` is not a substitute for shutdown. The
host verifies authority device identity and exclusion after power-off. If an
additional immutable incident backup is required, it copies and hashes the frozen
journal, claims, sealed runs/reports, audits, and filesystem metadata to
snapshot-external host evidence before restore, using a reviewed offline mount.
Recovery rewinds only the named OS/runtime block nodes. Restoring RAM, QEMU vmstate,
device state, a suspended guest, or any snapshot that includes the authority block
node is forbidden and fails the recovery checklist.

The next guest start is a cold boot. Snapshot-restored enable/mask state is never
trusted: preinstalled static drop-ins already bind every relevant service and timer
to authority attestation before units are queued; the generator only checks and
reports coverage. The attestation service mounts the
same authority volume read-only and validates serial, filesystem UUID,
`major:minor`, options, fixed root-only binds, exclusion evidence, journal/audits,
claims and seals before it can produce a current-`boot_id` tmpfs token. With no
token, a prior-boot token, `POISONED`, any in-flight/unsettled row, wrong volume, or
identity/exclusion mismatch, Broker, worker, OpenSandbox daemon and automatic gate
units cannot reach `ExecStart` even if the restored snapshot marks them enabled.
Only a verified settled `live_baseline`, or a settled failed row whose receipt
proves exact captured-legacy restoration, authorizes the corresponding runtime
token; a manual gate or recovery requires its narrower state-bound token and
cannot bypass poison. An exact empty-history legacy state can create only the
current-boot eligibility proof described above and starts nothing; inherited
recovery may authorize only the exact previous generation selected by its row.
Journal rows, claims, sealed reports, POISONED state, and
permanent attempt tombstones therefore survive unchanged and the same full commit
remains unrerunnable. An absent, blank, substituted, reformatted,
rewind-participating, or incorrectly mounted authority volume is a terminal
recovery refusal, never a reason to create a new DB. The gate has no archive,
delete, truncate, replace, clear-poison, format-volume, or snapshot-restore command.

### Task 8: Record authoritative evidence and promotion status

**Files:**
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Read the journal as the authority**

Task 8 opens `/var/lib/medchat-runtime-generation/formal-gate.sqlite3` read-only,
verifies the trusted root-owned path, SQLite schema/integrity, row constraints,
transition audit continuity, and the requested full-commit row. It records:

- release commit, mode, authoritative state, stable code, row version, and update
  timestamp;
- canonical release/previous target identifiers, normalized QEMU snapshot ID,
  snapshot evidence hash, authority drive/filesystem/mount/exclusion identity, and
  manifest hashes;
- claim hash, journal-bound run/report locators, report hash/status/seal hash,
  rollback receipt hash/verified flag, and any resolution/evidence hashes;
- `live_baseline` pointer/version/audit binding and whether this row is current;
- external formal report locator/hash, total status, pass/partial/fail counts, pass
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
OS/runtime-only recovery procedure plus surviving authority-volume evidence; do
not attempt it from Task 8.

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
- Task 7's new deployment, static boot interlock, attestation, closed worker-environment builder,
  whole-gate lock/concurrency, first-STAGED insertion, actor orphan recovery,
  acceptance sealing/CAS recovery, claim, report sealing/recovery, token lifecycle,
  CAS-before-token authorization, condition-to-exec guard-wrapper admission,
  first-mutation long-lock exclusion, no-clobber capability publication, automatic-gate
  receipt creation/rotation, clean-initial-legacy authorization, live-baseline history, activation/verify failure,
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
- Activation, root validation, socket probe, acceptance, soak, and both rollback
  forms run only as uniquely named transient systemd actors whose exact unit,
  kind, expected cgroup, identity, private-mount mapping, run label, and observer
  baseline are journaled before dispatch as applicable. Their transient effective
  `ExecStart` is only the root guard wrapper with canonical
  `--unit %n`; the journal-bound actor kind/identity selects the fixed command and
  target identity after shared-lock revalidation. Acceptance and soak use
  separate `PrivateMounts=yes` namespaces: each receives only its own phase subtree
  read/write at `/run/medchat-formal-output`; soak has no acceptance path, mount,
  descriptor, or content dependency and binds only the journaled seal hash as a
  prerequisite. The ordered mappings and RO/RW
  attributes are identity-bound; host output parents remain `root:root 0700`.
  Acceptance and soak are independently zero-live-verified and root-sealed before
  the run parent is sealed. Recovery proves inactive/MainPID=0/empty cgroup
  and zero labelled/unexplained OpenSandbox/Docker resource delta before clearing
  ownership; ambiguity or a surviving actor/resource poisons before rollback or a
  new gate. The actual `medchat` UID DAC/mount-namespace integration test passes.
- `formal_run_label` is internal-only and cannot be supplied through public HTTP,
  job, workflow, or ordinary OpenSandbox create payloads. OpenSandbox 0.1.15 passes
  `medchat.formal_run` in create metadata, and the locked reviewed
  `opensandbox-server==0.2.2` artifact propagates it atomically in the same Docker
  create call. Before create returns, OpenSandbox and Docker expose the identical
  label; ordinary-client injection, unsupported propagation, mismatches, create
  crashes, unknown deltas, and post-create patching fail closed.
- The socket probe receives neither scientific gate variable; acceptance receives
  only `MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE=1`; soak receives only
  `MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK=1`.
- One root-only non-blocking `flock` covers source/state checks, SQLite journal
  creation/validation and recovery, explicit incomplete-claim resolution,
  stage/capture, activation/verify, validation, acceptance, claim, soak, journal
  settlement, export, and any rollback verification. Lock contention and trusted
  settled-row refusals do not mutate runtime and never invoke rollback. For each
  eligibility CAS/capability rotation the gate next acquires the fixed root-only
  token-lifecycle flock and always preserves whole-gate-then-lifecycle order. The
  first interval starts before settled/initial capability revocation and remains
  exclusive through stage/capture, locator binding, first `STAGED`, `ACTIVATING`,
  current formal-token publication, and all fsyncs; only then is it released for
  immediate dispatch. Later rotations hold it across revoke -> CAS -> publish and
  release only before their protected dispatch. Stage/capture invokes no protected
  service or shared-lock consumer.
  Cold-boot and settled-baseline publishers use only that lifecycle flock, root
  `ExecCondition` and guard-wrapper readers take it shared, and journal state plus
  the uninterrupted pre-`STAGED` exclusive interval prevents read-only publication
  during a live mutation. A pre-`STAGED` gate death may restore only reattested prior
  authority after kernel lock release; a post-`STAGED` death may not.
- Linux runtime validation and real acceptance pass on the activated immutable generation.
- Exactly one new formal 30-job QEMU soak is attempted per full commit, enforced
  by a permanent root-only O_EXCL claim created immediately before the command;
  its report is retained and hashed when produced even if the command exits
  non-zero, and a post-claim crash never authorizes a rerun. The claim uses the
  closed canonical payload, exact expected row version, byte/hash reconstruction,
  exact `acceptance_seal_sha256`, all five frozen authority identity fields, and
  negative tests for partial/
  non-canonical/stale content and each independently mismatched authority field.
- `/var/lib/medchat-runtime-generation/formal-gate.sqlite3` is the only
  authoritative transaction, settlement, resolution, and poison store. Its
  root-only path, DELETE journal mode, FULL synchronous mode, foreign keys,
  `BEGIN IMMEDIATE`, schema constraints, row-version CAS, and parent fsync are
  tested. Claim files are permanent audit anchors; result/poison JSON files are
  reconstructable exports and never select a recovery branch.
- First staging uses one atomic expected-absence INSERT and initial audit at
  `row_version=1`; identical re-entry is read-only and conflicts fail closed.
  `qemu_snapshot_id`, authority-volume identity/exclusion, actor ownership/mount/
  observer receipts, safe unique run/report locators, claim expected version,
  acceptance seal, poison shutdown receipt, report seal, and snapshot evidence are
  constrained real columns. No opaque side file fills a missing schema field.
- The closed states are `STAGED`, `ACTIVATING`, `ACTIVE_PRECLAIM`, `CLAIMED`,
  `ROLLING_BACK`, `RESOLUTION_REQUIRED`, `RESOLVING`,
  `SETTLED_PASSED`, `SETTLED_FAILED`, and `POISONED`. Every state/evidence
  change is one legal expected-state/version CAS. Settled A permits corrected B
  in both legacy-first and generation-to-generation paths.
- The singleton `live_baseline` points only to the current passed generation and
  changes atomically with `SETTLED_PASSED`. Historical settled rows validate only
  frozen journal/audit/evidence truth; only the baseline row is checked against
  current symlink/PIDs. A -> B -> C retains verifiable A/B history while physically
  attesting only C as current.
- Acceptance output becomes formal evidence only through the single audited
  `ACTIVE_PRECLAIM` `acceptance_sealed` same-state CAS after actor/resource
  zero-live proof and descriptor-confined manifest/hash/fsync/root sealing. The
  claim binds that seal. Seal-before-CAS recovery revalidates the exact tree and
  never reruns acceptance; wrong or mutable evidence fails closed.
- Every relevant service has a pre-snapshot static authority drop-in with root
  `ExecCondition=+...` and an emptied/replaced `ExecStart` that invokes only the
  root-owned hash-attested guard wrapper; the automatic timer is separately static-
  gated and can
  target only the guarded service. Generator absence, failure, empty output, or
  partial output cannot remove protection. Each condition reopens the authority
  journal and validates boot, volume, row, state/version/action, capability set,
  target unit, and generation while holding the shared lifecycle lock, but condition
  success never reserves a later start. The wrapper accepts only canonical `--unit`,
  repeats that descriptor-confined validation under the shared lock, selects a closed
  executable/argv/cwd/environment/UID/GID/groups mapping, drops privileges with
  `no_new_privs`, and executes the pinned descriptor directly. CLOEXEC releases the
  shared flock only at successful `execve`; shell, PATH, caller argv, missing/tampered
  wrapper, stale authority, and exec failure start nothing. A condition-passed queued
  job therefore cannot cross a concurrent CAS/token rotation.
- The gate durably revokes the old automatic-gate receipt and runtime token before
  formal mutation. Every protected start is authorized by a CAS first and a formal
  token second, bound to the resulting state, row version, action, exact generation,
  unit allowlist, and actor; `STAGED` alone authorizes no start and stale/pre-CAS
  tokens are rejected. `ROLLING_BACK` is committed before `formal-new-run` is
  revoked and rollback-only `formal-recovery` is published. It may start only the
  row-selected previous generation for rollback verification and no science.
- Cold-boot attestation and settled-baseline read-only recovery are the only runtime
  token/automatic-gate receipt publishers. From one frozen clean-settled journal and
  `live_baseline` snapshot they publish the runtime token first and receipt last,
  binding current `boot_id`, all authority fields, baseline state/version, singleton
  version, snapshot hash, and exact gate service/timer names. In-flight, poison,
  resolution, and clean-initial-legacy states produce no pair. A partial pair blocks
  the automatic gate and is completed only from identical authoritative evidence.
- Every capability publisher/revoker uses the lifecycle flock, O_EXCL canonical
  temp, fsync, `renameat2(RENAME_NOREPLACE)` or the tested `linkat` no-replace
  fallback, and parent fsync. `EEXIST` is idempotent only for a descriptor-verified
  root-owned exact canonical payload; ordinary rename overwrite is forbidden.
  Revocation verifies the expected capability before unlink/fsync. Concurrent,
  anomalous-existing, partial-temp, and publish/revoke crash tests pass.
- `clean_initial_legacy` is possible exactly once with correct authority/QEMU
  identity, empty gate/audit and filesystem attempt history, NULL baseline, exact
  reviewed legacy state, absent current link, and zero relevant PIDs/cgroups. Boot
  creates only a current-boot eligibility proof; the locked manual gate inserts the
  first `STAGED` row/audit without a service token, then commits
  `STAGED -> ACTIVATING` with the exact actor before issuing
  `formal-new-run(action=activate_target)` for that resulting version. Any residue
  or first history entry permanently disables this branch. Proof consumption through
  token publication is one uninterrupted exclusive lifecycle interval; only death
  before first `STAGED` permits a newly reattested empty-history proof.
- A claim without a trusted report/failure envelope reaches
  `RESOLUTION_REQUIRED`; ordinary gates make zero runtime mutations. The
  explicit root-only hash-bound resolution command retains the claim, proves
  stopped work and preserved evidence, commits `RESOLVING` before rollback,
  and settles `SETTLED_FAILED` only with a verified receipt. An inherited
  `RESOLVING` that cannot prove physical completion becomes `POISONED`; no
  second rollback is attempted.
- `POISONED` permanently forbids science, activation, rollback, settlement,
  resolution, and new gates. Its only permitted same-state mutations are the
  audited journal-bound actor/service stop, cgroup-plus-daemon zero-live proof,
  actor clear, and worker/Broker shutdown receipt. Success or failure remains
  POISONED and returns `qemu_snapshot_recovery_required`.
- The report truthfully records latency, failures, events, cleanup, provenance,
  artifacts, probes, and zero-leak state. Its exact locator is commit-bound before
  dispatch; successful evidence is descriptor-validated, hashed/fsynced, sealed
  root:root and read-only, then journaled. Recovery after sealing uses that exact
  locator and never globs or guesses.
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
  OS/runtime snapshot restoration is required when rollback fails or physical
  completion cannot be proven; the journal persists `POISONED` as the authority
  and a marker is optional. Journal, claims, sealed runs/reports, and audits reside
  on one separately attested authority block volume excluded from that rewind.
  Recovery fully shuts down, flushes and cleanly unmounts authority storage,
  optionally backs up/hash-seals evidence, and rewinds only OS/runtime disks;
  RAM/vmstate/device-state restore is forbidden. On the mandatory cold boot,
  preinstalled static drop-ins gate Broker, worker, OpenSandbox daemon and both the
  automatic formal-gate service and timer behind read-only authority attestation;
  the generator is diagnostics-only. Authorization requires a root-only
  current-`boot_id` tmpfs token bound to authority identity, journal state/version,
  and service generation. Snapshot-restored enabled units cannot reach `ExecStart`
  before this check. POISONED/in-flight/wrong-volume states fail closed, and manual
  gate/recovery uses a narrower state-bound mode that cannot bypass poison.
  Missing/blank/substituted/included storage fails closed, and
  the same commit remains permanently unrerunnable. The gate never deletes,
  rewrites, clears, reformats, or replaces historical authority evidence.
- `docs/handoff/latest.md` distinguishes evaluator correctness from runtime stability and records either promotion evidence or rollback evidence.
