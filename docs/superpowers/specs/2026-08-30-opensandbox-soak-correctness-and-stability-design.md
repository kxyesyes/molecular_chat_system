# OpenSandbox Soak Correctness and Stability Design

Date: 2026-08-30

Branch: `codex/opensandbox-stability-hardening`

## 1. Objective

Correct the false-negative behavior exposed by the preserved `79860ec` QEMU
report, then address the real OpenSandbox control-plane failures before creating
one new reviewed QEMU generation. The work must preserve scientific truth,
bounded failure semantics, cleanup guarantees and the existing failed evidence.

This design does not reinterpret the previous run as successful. The
`79860ec` report remains failed because twelve of thirty measured submissions
did not produce accepted scientific output, the queue gate failed, and the
cancellation and timeout probes failed.

## 2. Scope

### In scope

- Correct measured-run event correlation without changing Broker trace IDs.
- Obtain Vina and Meeko provenance from Broker validation events when the
  measured Docker observer intentionally disables intrusive probes.
- Validate complete successful lifecycle events and truthful failed lifecycle
  events.
- Re-evaluate preserved report-shaped fixtures offline without invoking QEMU,
  OpenSandbox, Docker or Vina.
- Diagnose and fix project-controlled causes of `provider_error`,
  `tool_timeout`, `invalid_output`, queue, cancellation and timeout failures.
- Preserve bounded retries, circuit breaking, cancellation and cleanup.
- Deploy a new reviewed commit and execute one new fixed 30-job QEMU suite only
  after local tests, failure injection and independent review pass.

### Out of scope

- Rewriting OpenSandbox or replacing it with another sandbox.
- Increasing retry or timeout limits solely to make the report green.
- Modifying Vina scientific parameters or acceptance thresholds.
- Re-running or overwriting either preserved `79860ec`-generation report.
- Treating pose-producing runs as passed when provenance, events or cleanup are
  incomplete.

## 3. Evidence Baseline

The immutable QEMU report has SHA-256
`c66e07c3e9ad4669a8ab703fa57e887d16b8d71b938ba8a0755725692ae3c67f`.
It records:

- thirty measured submissions;
- eighteen verified gVisor/Vina pose artifacts;
- five `provider_error`, five `tool_timeout`, and two `invalid_output` results;
- zero report-level passes because event correlation and Meeko extraction also
  failed every run;
- queue accepted seven with one saturation;
- security probe passed, cancellation and timeout probes failed;
- zero running labelled containers after the suite.

This report is evidence, not a golden expected output. Offline tests will use
minimal sanitized fixtures that reproduce its contracts rather than commit the
runtime report.

## 4. Architecture

### 4.1 Per-run diagnostics window

`StabilityExecutor.measured_run()` already obtains a Broker diagnostics snapshot
immediately before and after one measured submission. The delta is therefore
the authoritative correlation window. The evaluator will group delta events by
the pair `(trace_id, job_id)` and require exactly one lifecycle identity for a
successful measured submission.

The caller-provided soak key remains the worker idempotency key. It will not be
compared with the Broker-generated trace ID or Broker job ID.

### 4.2 Lifecycle validation

For a successful ToolResult, the single event group must contain the exact
ordered phases:

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

The terminal event must report a passed outcome and successful cleanup. The
validation completion event must report safe Vina and Meeko versions.

For a failed ToolResult, the evaluator must not invent missing success phases.
It will require one identity, ordered events, one terminal event and a cleanup
terminal state consistent with the ToolResult. Failure events remain failed and
cannot contribute to scientific success.

### 4.3 Provenance selection

The existing Docker observer remains authoritative for runtime, immutable image
digest, resource limits and network isolation. Intrusive version probing stays
disabled during measured runs to avoid perturbing latency and stability.

Vina and Meeko versions for measured runs come from the validated Broker
`validation_completed` event. Vina must agree with the ToolResult provenance.
Meeko must be present and satisfy the existing safe-version contract. Missing,
ambiguous or conflicting versions fail closed.

### 4.4 Failure classification

The evaluator will preserve the structured ToolResult error code and correlate
it with Broker terminal telemetry. Known control-plane classes remain the
existing allowlist. Scientific and worker errors such as `tool_timeout` and
`invalid_output` remain explicit failure codes rather than being flattened into
an unrelated control-plane class.

No raw exception body, server response, environment value, sandbox ID, host
path or credential may enter the report.

## 5. Offline Replay Tests

Tests will construct sanitized diagnostics snapshots with Broker-generated
identities that differ from the caller idempotency key. They will prove:

- a complete successful event group is accepted;
- Vina and Meeko versions are sourced from validation telemetry;
- missing or conflicting versions fail closed;
- two event identities in one measured window fail closed;
- reordered, duplicated or missing phases fail closed;
- a truthful failed lifecycle is retained as failed without synthetic phases;
- cleanup and terminal-state disagreement fails closed;
- report redaction and non-clobber publication remain unchanged.

These tests must fail against the current implementation before production code
changes are made.

## 6. Real Stability Diagnosis

After evaluator correctness is restored, diagnosis proceeds by boundary:

1. Broker admission and queue counters.
2. OpenSandbox create/readiness and metadata calls.
3. Upload and command execution.
4. Result and pose download/validation.
5. Destroy and cleanup reconciliation.

The preserved diagnostics counters and safe failure classes will determine the
failing boundary. Any additional instrumentation must emit only allowlisted
codes and bounded numeric data.

Fixes may improve state reconciliation, readiness checks, bounded retry
eligibility or output transport validation. They may not add unbounded retries,
retry Vina scientific commands, weaken circuit breaking, extend deadlines above
the reviewed limits, or turn missing output into success.

## 7. Probe Semantics

- Idempotency must create one sandbox and return two consistent successful
  results for one key.
- Queue pressure must accept nine submissions and reject exactly one as
  saturated; other provider or timeout failures fail the gate.
- Cancellation and timeout must produce their exact structured error codes,
  complete cleanup and leave no labelled container.
- Security probes retain the current isolation and immutable-image checks.

The probe implementation may be corrected where it misreads a valid Broker
state, but acceptance criteria will not be relaxed.

## 8. Deployment and Evidence Preservation

Implementation uses the existing isolated worktree and branch. Every behavior
change receives a focused commit and independent review. The prior QEMU runtime
directories and both failed reports remain untouched.

A new reviewed commit is deployed as a separate release generation. Before the
formal suite, static/runtime validators, UDS diagnostics, service state and zero
labelled containers must pass. The new report path includes the new release
identity and must not already exist.

Exactly one formal 30-job suite is allowed for that release generation. A
failed report is preserved and reported honestly. No same-generation rerun may
replace it.

## 9. Acceptance Criteria

Local completion requires:

- focused evaluator tests pass;
- full `tests/sandbox_broker` passes;
- `python -m compileall -q src scripts` passes;
- static deployment validation passes;
- independent review has no open Critical or Important finding.

Production promotion requires the new QEMU report to satisfy all existing
gates: 30/30 scientific success, complete lifecycle and provenance, successful
cleanup, correct idempotency and queue behavior, passing cancellation/timeout/
security probes, bounded p95 latency and zero independently observed labelled
containers.

## 10. Rollback

If deployment validation or the formal suite fails, stop promotion, preserve
the report, retain the failing generation for investigation and reactivate the
previous reviewed service generation. Do not delete runtime evidence until an
operator confirms it is no longer required.
