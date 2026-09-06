# OpenSandbox Stability Hardening Design

**Date:** 2026-08-27

**Status:** Approved for implementation planning

## 1. Objective

Make the existing MedChat OpenSandbox docking path observable, diagnosable and
stable enough for a production canary without changing its scientific contract.
The work must identify whether intermittent HTTP 500/502 responses and latency
spikes originate in Broker queueing, OpenSandbox provisioning/readiness, gVisor
container startup, remote command execution, result validation or cleanup.

The completed stage must preserve real Vina/Meeko provenance, fail closed, avoid
duplicate scientific execution, terminate every job, clean every known sandbox
and provide enough bounded telemetry to explain every failure.

## 2. Current evidence

The baseline at commit `c9eed10` has already established:

- a Broker reachable only over a protected Unix domain socket;
- a digest-pinned docking image running under gVisor;
- fixed resource, timeout, network, artifact and provenance policy;
- bounded cancellation, output validation and cleanup;
- a passing real `repeat=3` acceptance with 100% pass rate;
- a later real scientific run that succeeded while the idempotency probe hit the
  fixed 267-second execution timeout;
- separate late observations of OpenSandbox command/server-proxy 500/502 errors
  and variable execution latency;
- no evidence that the application fabricated a scientific result or leaked an
  orphan container during those failures.

This stage treats the infrastructure instability as unresolved. A successful
rerun must not erase or relabel an earlier failed observation.

## 3. Scope

### 3.1 In scope

- Structured, low-cardinality telemetry at every Broker lifecycle boundary.
- Sanitized OpenSandbox SDK/control-plane failure classification.
- A private UDS diagnostics contract for counters, gauges and histograms.
- A control-plane circuit breaker with one half-open probe.
- Existing reconciliation-based provisioning retry and bounded idempotent
  cleanup retry.
- A repeatable QEMU soak runner covering real docking, idempotency, queue
  pressure, cancellation, timeout and cleanup.
- Deployment validation, operator documentation and a structured report.

### 3.2 Out of scope

- No DeepSeek or other external language-model call.
- No Agent, frontend, chat, planner or molecular-generation change.
- No automatic retry of Vina, Meeko or any command that may have begun scientific
  execution.
- No Broker concurrency increase, warm pool, multi-node scheduler or Kubernetes.
- No replacement of OpenSandbox, gVisor, Temporal or the docking tool contract.
- No business SQLite schema migration and no high-frequency telemetry in the
  Broker job database.

## 4. Selected architecture

The execution path remains:

```text
Temporal Worker
      |
      | HTTP over protected UDS
      v
Sandbox Broker ---- BrokerTelemetry ---- structured journald events
      |                    |
      |                    +-----------> private UDS diagnostics
      v
OpenSandbox SDK / Server
      |
      v
Docker + gVisor + pinned Vina/Meeko image
```

Three focused units are added.

### 4.1 BrokerTelemetry

`src/sandbox_broker/telemetry.py` owns immutable event validation, monotonic
phase timers, bounded counters, gauges, histograms and Prometheus projection. It
must not import the OpenSandbox SDK or business persistence layer.

Allowed event fields are:

```text
schema_version
trace_id
job_id
phase
attempt
outcome
failure_class
duration_ms
queue_depth
active_job_count
cleanup_status
image_digest
vina_version
meeko_version
```

`trace_id` and `job_id` are allowed in structured diagnostic events for
correlation but are never Prometheus labels. Metric labels are fixed enums only.

### 4.2 OpenSandbox failure classifier

`src/sandbox_broker/opensandbox_client.py` maps SDK and HTTP failures to a
private, stable control-plane taxonomy before they reach service orchestration:

```text
connection_failed
server_500
proxy_502
readiness_timeout
create_timeout
command_transport_failed
command_timeout
resource_limit
destroy_failed
unknown_control_plane_failure
```

The classifier records status class and operation but never records response
bodies, request headers, credentials, environment variables, shell output or
exception text. Public `BrokerErrorCode` values remain backward compatible.

### 4.3 Stability soak runner

`scripts/run_opensandbox_stability_soak.py` calls the existing worker-side UDS
runner and private diagnostics endpoint. It produces one versioned JSON report
containing run outcomes, phase latency distributions, failure classes, queue
observations, cleanup results, artifact/provenance checks and orphan-container
observations. It never invokes Vina directly.

## 5. Lifecycle telemetry

Every job emits the following ordered phase events when applicable:

```text
job_received
queue_entered
provisioning_started
provisioning_completed
upload_started
upload_completed
command_started
command_completed
validation_started
validation_completed
cleanup_started
cleanup_completed
job_terminal
```

Each started phase has at most one matching completed event per attempt. A
completed event contains `outcome=passed|failed|cancelled` and a stable
`failure_class` when it did not pass. Durations use `time.monotonic()` and are
finite non-negative integers. Wall-clock timestamps are produced by the logging
system and are not used to calculate latency.

The following metrics are exposed over the existing protected UDS:

```text
medchat_sandbox_jobs_total{terminal_status,failure_class}
medchat_sandbox_phase_duration_seconds{phase,outcome}
medchat_sandbox_control_plane_failures_total{operation,failure_class}
medchat_sandbox_retry_total{operation,outcome}
medchat_sandbox_queue_depth
medchat_sandbox_active_jobs
medchat_sandbox_cleanup_tasks
medchat_sandbox_isolated_tasks
medchat_sandbox_circuit_breaker_state
```

The diagnostics surface consists of:

- `GET /v1/diagnostics`: strict JSON snapshot used by tests and the soak runner;
- `GET /metrics`: Prometheus text projection using only the bounded labels above.

Both routes are available only on the current UDS and are read-only. They do not
create a TCP listener or weaken socket ownership/mode checks.

## 6. Sensitive-data contract

Telemetry, logs, diagnostics and reports must reject or omit:

- receptor or ligand contents;
- caller host paths and artifact absolute paths;
- SMILES, prompts and chat text;
- API keys, tokens, OpenSandbox credentials and environment values;
- HTTP request/response bodies and headers;
- raw exception messages, stdout and stderr;
- unbounded user-controlled label or event values.

The existing sensitive-text detector and report redaction utilities are reused.
Failure classification is allowlisted; unknown values become
`unknown_control_plane_failure` rather than raw text.

## 7. Retry policy

Retries are based on knowledge of whether scientific execution could have begun.

### 7.1 Allowed retries

- Provisioning may retry once only after reconciliation proves the previous
  sandbox was never created or has been destroyed.
- Read-only readiness and metadata observations may retry with two bounded
  attempts and short jittered backoff inside the existing absolute deadline.
- Cleanup may retry the same known sandbox ID once because destroy is treated as
  an idempotent control-plane operation.

### 7.2 Forbidden retries

- No retry after `command_started` unless independent evidence proves the command
  never entered the sandbox.
- No Vina/Meeko command retry.
- No retry for invalid input, scientific output validation failure, resource
  limit, user cancellation or artifact mismatch.
- No new sandbox while the previous sandbox identity or cleanup state is
  uncertain.

All retries emit an attempt number and outcome. Retry exhaustion returns the
existing fail-closed Broker error and does not manufacture a success.

## 8. Control-plane circuit breaker

The breaker observes only OpenSandbox infrastructure failure classes. Scientific
tool failures, invalid inputs, cancellations and artifact validation failures do
not contribute.

The fixed initial policy is:

- open after 3 consecutive qualifying failures within 60 seconds;
- remain open for 30 seconds;
- reject new provisioning with `opensandbox_unavailable` while open;
- permit exactly one half-open provisioning probe;
- close after a successful half-open provisioning;
- reopen for 30 seconds after a failed half-open probe.

State is process-local and intentionally not persisted. A Broker restart resets
the breaker to closed but leaves job/idempotency persistence unchanged. Breaker
state and transitions are observable through bounded telemetry.

## 9. Queue and concurrency policy

The Broker remains a single consumer with queue capacity 8. This stage measures
rather than increases concurrency. Queue depth is observed at submission,
dequeue and terminal transition.

When capacity is exhausted, submission returns the existing `queue_saturated`
result immediately. It does not wait without a bound and does not bypass the
circuit breaker. A repeated idempotency key returns the existing job and cannot
consume a second queue slot or create another sandbox.

## 10. Cleanup and terminal-state policy

Cleanup remains in the lifecycle `finally` path. Every known sandbox receives a
bounded destroy attempt even when provisioning, upload, command, validation,
cancellation or telemetry fails.

- Cleanup success is required for an overall successful job.
- A scientific artifact produced before cleanup failure is not returned as a
  successful result.
- Terminal jobs never transition back to an active state.
- Cancellation wins any simultaneous timeout/failure race.
- Side-effect-free SDK tasks that resist cancellation remain tracked and cannot
  publish artifacts after terminal.
- An unknown remote identity retains the existing auto-expiry warning and cannot
  be reported as confirmed cleanup.

Telemetry failures are isolated: they may emit a bounded internal diagnostic but
must not prevent sandbox cleanup or alter a scientifically validated result.

## 11. Test strategy

All behavior changes follow test-driven development.

### 11.1 Unit tests

- Event schema rejects extra fields, invalid enums, non-finite durations and
  sensitive/unbounded text.
- Prometheus labels are bounded and never include trace/job/user values.
- Phase timers produce exactly one terminal observation.
- Failure classifier covers 500, 502, connection failure, readiness timeout,
  command timeout, resource limit and unknown failure without raw text.
- Circuit breaker covers closed, open, half-open, recovery and excluded scientific
  failures using an injected monotonic clock.
- Retry tests prove each allowed operation is bounded and every forbidden
  scientific retry count remains zero.

### 11.2 Fault-injection integration tests

Inject failures at create, readiness, upload, command transport, validation and
destroy boundaries. Tests assert ordered telemetry, stable Broker result,
terminal state, cleanup attempt, no second scientific command and no secret in
events or metrics.

Queue tests submit duplicate and distinct keys to prove idempotency reuse, queue
capacity enforcement, one consumer and deterministic saturation behavior.

### 11.3 QEMU real tests

Use the pinned Ubuntu 24.04 pre-production environment, gVisor, Vina, Meeko,
`data/samples/MAGL_5zun.pdb`, `data/samples/5.sdf`, center
`[5.99,3.01,17.345]` and size `[20,20,20]`.

The final soak contains:

- 30 consecutive real docking submissions using production parameters;
- same-key idempotency reuse with exactly one sandbox;
- a bounded queue-pressure batch up to configured capacity plus one expected
  `queue_saturated` request;
- cancellation and timeout probes;
- existing network, filesystem, PID, gVisor, digest and provenance checks;
- an independent post-run query proving zero running labelled containers.

## 12. Acceptance gates

The stage passes only when all of the following are true:

- all 30 real jobs reach a terminal state;
- all 30 real jobs succeed scientifically; any infrastructure or tool failure
  prevents the soak from passing even when it terminates and cleans correctly;
- every reported scientific success has a finite energy, positive pose count,
  present pose artifact, matching hash and real provenance;
- cleanup succeeds for 30/30 real jobs;
- running labelled sandbox count is zero after the suite;
- telemetry event completeness is 100%;
- each failure, if any, has an explicit phase and failure class;
- same-key reuse creates exactly one sandbox;
- queue pressure produces only accepted/reused jobs or `queue_saturated`;
- phase and total p95 latency remain below their existing hard deadlines;
- no security probe fails;
- no report, diagnostic event or log contains a forbidden sensitive pattern.

The overall result is `passed` only if all gates pass. A real OpenSandbox 500,
502, timeout, cleanup failure or unexplained missing event produces `failed` or
`partial` according to the existing report contract. The runner never retries an
entire suite to replace failed evidence with a passing report.

## 13. Deployment and rollback

Implementation is developed on `codex/opensandbox-stability-hardening` and
is stacked on the completed `codex/opensandbox-docking-broker` baseline until
that prerequisite PR is merged. It is deployed first to the existing QEMU
environment. Runtime and release source must have matching hashes before a real
soak. Static/runtime deployment validators, service state and UDS permissions
must pass before scientific execution.

Production promotion remains blocked until the soak passes and an independent
review reports no Critical or Important issue. Rollback restores the previous
Broker and worker generation. Because public error enums, API schema and business
SQLite schema remain unchanged, rollback can read all jobs created by this stage.

## 14. Relationship to external LLM testing

The DeepSeek credential supplied for later Agent verification is not needed by
this stage. It remains runtime-only and must not appear in code, configuration,
logs, telemetry, reports or Git. Agent/DeepSeek full-chain testing starts in a
separate branch and PR only after the OpenSandbox stability gate is resolved.
